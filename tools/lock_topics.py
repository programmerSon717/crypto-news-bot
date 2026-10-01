"""뉴스 탭을 '닫아' 일반 멤버가 글을 못 쓰게 한다. '자료검색' 탭만 열어 둔다.

    python tools/lock_topics.py --dry-run     # 무엇을 바꿀지만 본다
    python tools/lock_topics.py               # 실제로 적용
    python tools/lock_topics.py --unlock      # 전부 되돌린다(다시 열기)

## 왜 '닫기'인가

텔레그램의 글쓰기 권한은 **그룹 단위**다. 그룹 기본 권한에서 메시지 전송을 끄면
자료검색 탭까지 막힌다. 토픽별로 권한을 따로 줄 수는 없다.

대신 포럼 토픽에는 **열림/닫힘** 상태가 있다. 닫힌 토픽에는 관리자와
`can_manage_topics` 권한을 가진 봇만 쓸 수 있고, 일반 멤버는 읽기만 된다.
그래서 뉴스 탭을 전부 닫고 자료검색만 열어 두면 원하는 모양이 된다.

## 반드시 먼저 확인하는 것

**봇이 `can_manage_topics` 관리자가 아니면 토픽을 닫는 순간 발행이 멈춘다.**
그래서 이 스크립트는 권한을 먼저 확인하고, 조건이 안 맞으면 아무것도 바꾸지
않고 끝낸다. 되돌리기(`--unlock`)는 권한 확인 없이도 동작한다 — 막힌 상태를
푸는 것은 늦으면 안 되기 때문이다.

## 되돌리기

`--unlock` 이 전부 다시 열어 준다. 토픽을 닫는 것은 메시지를 지우지 않으므로
내용이 사라질 위험은 없다.
"""

import argparse
import asyncio
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import topics  # noqa: E402
from config import settings  # noqa: E402

API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

# 닫지 않고 열어 둘 탭.
#
# **비어 있다 — 전부 닫는다.** 자료검색도 1:1 대화로 옮겼기 때문이다(assistant.py).
# 그룹 탭에 질문을 쓰면 그 순간 누가 무엇을 찾아봤는지 모두에게 보이고 ALL 탭에도
# 쌓인다. 봇이 "1:1로 물어보세요"라고 답해 줘도 **질문 자체는 이미 노출된 뒤**라
# 늦다. 그래서 쓸 수 없게 닫고, 안내문만 고정해 둔다.
KEEP_OPEN: set[str] = set()

# 자료검색 탭에 고정해 둘 안내문. 닫힌 탭에도 봇(관리자)은 쓸 수 있다.
GUIDE = (
    "🔍 <b>자료검색 쓰는 법</b>\n\n"
    "검색은 <b>봇과 1:1 대화</b>에서만 됩니다.\n"
    "여기서 주고받으면 누가 무엇을 찾아봤는지 모두에게 보이기 때문입니다.\n\n"
    "① 위 봇 이름을 눌러 대화창을 엽니다 (처음 한 번 '시작')\n"
    "② 그냥 물어보세요 — 예: <code>체인링크 요번에 업뎃된거 뭐임?</code>\n\n"
    "발행된 기사에서 찾아 요약과 원문 링크를 보내 드립니다."
)

# 이미 원하는 상태일 때 텔레그램이 돌려주는 설명들. 실패로 보지 않는다.
_ALREADY = ("TOPIC_CLOSED", "TOPIC_NOT_MODIFIED", "already", "TOPIC_ID_INVALID")


async def _call(client: httpx.AsyncClient, method: str, **payload) -> tuple[bool, str]:
    r = await client.post(f"{API}/{method}", json=payload, timeout=20)
    body = r.json()
    if body.get("ok"):
        return True, ""
    return False, str(body.get("description", r.status_code))


async def _check_rights(client: httpx.AsyncClient) -> bool:
    """봇이 닫힌 토픽에도 쓸 수 있는 상태인지. 아니면 False."""
    me = (await client.get(f"{API}/getMe", timeout=15)).json()
    bot_id = me.get("result", {}).get("id")
    if not bot_id:
        print("[에러] getMe 실패 — 토큰을 확인하세요.")
        return False

    chat = (await client.get(f"{API}/getChat",
                             params={"chat_id": settings.telegram_channel_id},
                             timeout=15)).json().get("result", {})
    if not chat.get("is_forum"):
        print("[에러] 이 그룹은 주제(Topics)가 꺼져 있습니다.")
        return False

    m = (await client.get(f"{API}/getChatMember",
                          params={"chat_id": settings.telegram_channel_id,
                                  "user_id": bot_id}, timeout=15)
         ).json().get("result", {})
    status = m.get("status")
    manage = m.get("can_manage_topics")
    print(f"대상: {chat.get('title')}")
    print(f"봇: @{me['result'].get('username')}  상태={status}  "
          f"can_manage_topics={manage}")

    if status != "administrator" or not manage:
        print("\n[중단] 봇이 '주제 관리' 권한을 가진 관리자가 아닙니다.\n"
              "       이대로 탭을 닫으면 **봇의 발행도 막힙니다.**\n"
              "       그룹 설정 > 관리자 > 봇 > '주제 관리'를 켜고 다시 실행하세요.")
        return False
    return True


async def run(unlock: bool, dry_run: bool) -> None:
    async with httpx.AsyncClient() as client:
        if not unlock and not await _check_rights(client):
            return

        mapping = topics.load()
        if not mapping:
            print(f"[에러] {settings.topics_file} 가 비어 있습니다. "
                  f"먼저 python setup_topics.py 를 실행하세요.")
            return

        # 닫을 것 / 열어 둘 것을 가른다.
        to_close, to_open = [], []
        for name, tid in sorted(mapping.items()):
            if unlock or name in KEEP_OPEN:
                to_open.append((name, tid))
            else:
                to_close.append((name, tid))

        verb = "되돌리기(전부 열기)" if unlock else "뉴스 탭 닫기"
        print(f"\n== {verb} ==")
        print(f"  닫을 탭 {len(to_close)}개 / 열어 둘 탭 {len(to_open)}개"
              f"{'  (dry-run: 바꾸지 않음)' if dry_run else ''}\n")

        for name, tid in to_close:
            print(f"  🔒 닫기  {name} (thread {tid})")
            if dry_run:
                continue
            ok, why = await _call(client, "closeForumTopic",
                                  chat_id=settings.telegram_channel_id,
                                  message_thread_id=tid)
            if not ok and not any(a in why for a in _ALREADY):
                print(f"      실패: {why}")

        for name, tid in to_open:
            print(f"  🔓 열기  {name} (thread {tid})")
            if dry_run:
                continue
            ok, why = await _call(client, "reopenForumTopic",
                                  chat_id=settings.telegram_channel_id,
                                  message_thread_id=tid)
            if not ok and not any(a in why for a in _ALREADY):
                print(f"      실패: {why}")

        # 'General' 탭은 topics.json 에 없다(텔레그램이 자동으로 두는 탭).
        # 여기에도 글을 쓸 수 있으므로 같이 닫는다. 전용 메서드를 쓴다.
        method = "reopenGeneralForumTopic" if unlock else "closeGeneralForumTopic"
        print(f"  {'🔓 열기' if unlock else '🔒 닫기'}  General (기본 탭)")
        if not dry_run:
            ok, why = await _call(client, method,
                                  chat_id=settings.telegram_channel_id)
            if not ok and not any(a in why for a in _ALREADY):
                print(f"      실패: {why}")

        # 자료검색 탭에 안내문을 올리고 고정한다. 탭이 닫혀 있어도 봇은 쓸 수 있다.
        guide_tid = mapping.get("자료검색")
        if guide_tid and not unlock and not dry_run:
            r = await client.post(f"{API}/sendMessage", json={
                "chat_id": settings.telegram_channel_id,
                "message_thread_id": guide_tid, "text": GUIDE,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True}}, timeout=20)
            body = r.json()
            if body.get("ok"):
                mid = body["result"]["message_id"]
                await _call(client, "pinChatMessage",
                            chat_id=settings.telegram_channel_id,
                            message_id=mid, disable_notification=True)
                print("  📌 자료검색 탭에 사용법 안내문 고정")
            else:
                print(f"  안내문 실패: {body.get('description')}")

        if dry_run:
            print("\ndry-run 이었습니다. --dry-run 을 빼면 실제로 적용합니다.")
        elif unlock:
            print("\n완료. 모든 탭을 다시 열었습니다.")
        else:
            print(f"\n완료. 일반 멤버는 어느 탭에도 글을 쓸 수 없습니다"
                  f"{' (열어 둔 탭: ' + ', '.join(sorted(KEEP_OPEN)) + ')' if KEEP_OPEN else ''}.\n"
                  f"검색은 봇과의 1:1 대화로만 합니다 — 서로의 검색 내역이 보이지 않습니다.\n"
                  f"봇은 닫힌 탭에도 계속 발행합니다(관리자 + 주제 관리 권한).\n"
                  f"되돌리려면: python tools/lock_topics.py --unlock")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--unlock", action="store_true", help="전부 다시 열기")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(run(args.unlock, args.dry_run))


if __name__ == "__main__":
    main()
