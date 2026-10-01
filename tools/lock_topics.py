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

# 닫지 않고 열어 둘 탭. 사람이 글을 쓰는 곳이다.
KEEP_OPEN = set(topics.EXTRA_TOPICS)

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

        # **열어 둘 탭이 하나도 없으면 막는다.** 이대로 닫으면 사람이 글을 쓸
        # 곳이 사라진다. 실제로 자료검색 토픽을 만들기 전에 이걸 돌리면
        # 16개 탭 + General 이 전부 닫히고 질문할 자리가 없어진다.
        if not unlock and not to_open:
            print(f"[중단] 열어 둘 탭이 없습니다 — {', '.join(sorted(KEEP_OPEN))} 이"
                  f" {settings.topics_file} 에 없습니다.\n"
                  f"       먼저 python setup_topics.py 로 탭을 만드세요.\n"
                  f"       (지금 닫으면 사람이 글 쓸 곳이 하나도 남지 않습니다)")
            return

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

        if dry_run:
            print("\ndry-run 이었습니다. --dry-run 을 빼면 실제로 적용합니다.")
        elif unlock:
            print("\n완료. 모든 탭을 다시 열었습니다.")
        else:
            print(f"\n완료. 이제 일반 멤버는 {', '.join(sorted(KEEP_OPEN))} 탭에만"
                  f" 글을 쓸 수 있습니다.\n"
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
