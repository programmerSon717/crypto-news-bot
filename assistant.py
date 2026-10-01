"""자료검색 — 봇에게 1:1로 물으면 발행 이력에서 찾아 답한다.

    python main.py --answer                # 기본 240초 동안 질문을 받는다
    python main.py --answer --seconds 60
    python main.py --ask "체인링크 요번에 업뎃된거 뭐임?"   # 콘솔에서 시험

## 구조

    질문  →  검색(코드, 공짜)  →  판단(모델 1회)  →  요약 + 원본 글 링크

**검색은 코드가, 판단은 모델이** 한다. `search` 가 BM25 로 후보 6건을 뽑고,
모델은 그 중에서 질문에 맞는 것을 고르고 한두 문장으로 답한다. 기사 본문을
다시 쓰지 않는다 — 원문은 이미 채널에 발행돼 있으므로 **링크로 보낸다**.
(발행 때 저장한 `message_id`·`thread_id` 로 링크를 만든다. `search.channel_link`)

모델이 하는 일은 '고르기'다. 기사를 새로 쓰거나 사실을 더하지 않는다.
후보 밖의 이야기를 하면 답이 틀려도 확인할 길이 없기 때문이다.

## 한도

**발행이 쓰는 Gemini 무료 한도를 이 기능이 빼앗으면 안 된다.** 발행이 멈추는
것보다 검색이 투박한 게 낫다. 그래서
 · 질문 1건당 모델 호출은 **1회**로 묶는다(고르기와 답을 한 번에 받는다).
 · 하루 `DAILY_BUDGET` 회를 넘으면 모델을 부르지 않고 **검색 결과만** 보낸다.
 · 한도가 소진된 날(`summarizer.all_exhausted`)도 검색 결과만 보낸다.
즉 모델이 없어도 기능은 돌아간다. 답변 문장이 없어질 뿐이다.

## 왜 그룹 탭이 아니라 1:1 대화인가

처음엔 '자료검색' 토픽에서 주고받게 만들었다. 그런데 **텔레그램 그룹 토픽은
구조적으로 전부 공개**다 — A가 무엇을 검색했는지 B·C가 그대로 보고, ALL 탭에도
쌓인다. 토픽별로 사람마다 다른 화면을 보여줄 방법은 없다.

그래서 질문은 **봇과의 1:1 대화에서만** 받고 거기로만 답한다. 그룹에는 아무것도
남지 않는다. 그룹 탭에 질문을 쓰면 답하지 않고 "1:1로 물어보라"고만 알린다 —
거기서 답하면 그 내용이 모두에게 보이기 때문이다.

대가가 하나 있다. **텔레그램은 봇이 먼저 말을 걸 수 없다.** 쓰는 사람이 한 번
봇과의 대화를 열고 '시작'을 눌러야 한다(1회). 그 전에는 봇이 답을 보낼 수 없다.

## 누가 쓸 수 있나

**그룹 멤버만.** 1:1 대화는 누구나 열 수 있으므로, 질문이 오면 그 사람이 뉴스
그룹의 멤버인지 확인하고(`getChatMember`) 아니면 답하지 않는다. 회사 뉴스
아카이브가 아무에게나 열리면 안 된다. 확인 결과는 잠깐 캐시한다.

## 설정

 1. 쓸 사람마다 봇과의 1:1 대화를 열고 '시작'을 누른다(1회). 그게 전부다.
 2. (선택) `python setup_topics.py` — '자료검색' 탭을 만들어 두면 거기 질문을
    쓴 사람에게 "1:1로 물어보라"고 안내한다. 탭이 없어도 1:1 은 그냥 된다.
 3. (권장) `GEMINI_SEARCH_API_KEY` 를 따로 넣는다. 안 넣으면 답변 생성이
    **발행 몫 한도를 깎는다**(config 주석 참고).

## webhook 과 같이 쓸 수 없다

`getUpdates` 는 webhook 이 설정돼 있으면 409 로 실패한다. 이 봇은 webhook 을
쓰지 않으므로 문제가 없지만, 나중에 webhook 으로 옮길 때는 이 모듈의 수신부만
바꾸면 된다 — `compose()` 는 그대로 쓸 수 있다.
"""

import asyncio
import os
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

import publisher
import search
import summarizer
from config import settings
from prompts import ANSWER_SYSTEM_PROMPT, build_answer_prompt

TOPIC_NAME = "자료검색"


def _bot_token() -> str:
    """질문을 받고 답할 봇. 따로 지정이 없으면 발행 봇을 그대로 쓴다."""
    return settings.assistant_bot_token or settings.telegram_bot_token


def _api(method: str, token: str | None = None) -> str:
    return f"https://api.telegram.org/bot{token or _bot_token()}/{method}"


def _bot_id(token: str) -> str:
    """토큰 앞부분이 봇 id 다. 수신 위치(offset)를 봇별로 따로 기억하기 위해 쓴다 —
    봇을 바꾸면 update_id 체계가 달라서 이전 offset 을 쓰면 안 된다."""
    return (token or "").split(":", 1)[0] or "0"

# 모델에게 보여 줄 후보 수. 늘려도 정확도가 오르지 않고 토큰만 든다.
CANDIDATES = int(os.getenv("ANSWER_CANDIDATES", "6"))
# 답에 함께 붙일 원본 글 최대 개수.
MAX_LINKS = int(os.getenv("ANSWER_MAX_LINKS", "3"))
# 하루 모델 호출 상한. 발행 한도를 지키기 위한 것이다(위 '한도' 참고).
DAILY_BUDGET = int(os.getenv("ANSWER_DAILY_BUDGET", "60"))
# getUpdates 롱폴링 대기(초). 텔레그램이 권하는 범위.
POLL_TIMEOUT = int(os.getenv("ANSWER_POLL_TIMEOUT", "25"))

_CMD = re.compile(r"^/(찾아|검색|ask|find)(@\S+)?\s*")


# ── 한도 계산 ────────────────────────────────────────────────

KST = timezone(timedelta(hours=9))


def _today() -> str:
    """한도는 KST 기준으로 센다 — 발행 한도 관리와 같은 기준이어야 한다."""
    return datetime.now(tz=KST).strftime("%Y-%m-%d")


def _budget_left(store) -> int:
    day, _, used = store.kv_get("answer_budget", "::0").partition(":")
    used = used.lstrip(":")
    if day != _today():
        return DAILY_BUDGET
    try:
        return max(0, DAILY_BUDGET - int(used or 0))
    except ValueError:
        return DAILY_BUDGET


def _budget_spend(store) -> None:
    left = _budget_left(store)
    used = DAILY_BUDGET - left + 1
    store.kv_set("answer_budget", f"{_today()}::{used}")


# ── 답 만들기 ────────────────────────────────────────────────

async def compose(store, question: str, now: float | None = None) -> str:
    """질문 → 보낼 메시지(HTML). 모델을 못 쓰면 검색 결과만 담는다."""
    idx = search.index(store)
    hits = idx.search(question, limit=CANDIDATES, now=now)
    if not hits:
        return ("🔍 발행 이력에서 관련 기사를 못 찾았습니다.\n"
                "고유명사(프로젝트·기관 이름)를 넣어 다시 물어보시면 더 잘 찾습니다.")

    answer = ""
    picked = list(range(min(MAX_LINKS, len(hits))))
    # 모델이 "맞는 게 없다"고 한 경우. 그때는 후보를 **관련 글로 내세우지 않는다** —
    # 없다고 말하면서 기사 세 건을 붙이면 읽는 사람이 그게 답인 줄 안다.
    nothing_fits = False

    may_call = _budget_left(store) > 0 and not summarizer.all_exhausted()
    if may_call:
        data = await summarizer.generate_json(
            ANSWER_SYSTEM_PROMPT, build_answer_prompt(question, hits),
            max_tokens=900, use_search_key=True,
        )
        _budget_spend(store)
        if data:
            answer = (data.get("answer") or "").strip()
            raw = data.get("picks") or []
            # 모델이 범위를 벗어난 번호를 주는 일이 있다. 조용히 걸러낸다.
            idxs = [int(i) for i in raw
                    if isinstance(i, (int, str)) and str(i).isdigit()
                    and 0 <= int(i) < len(hits)]
            if idxs:
                picked = list(dict.fromkeys(idxs))[:MAX_LINKS]
            elif answer:
                # 답은 받았는데 고른 게 없다 = 질문에 맞는 기사가 없다는 뜻이다.
                nothing_fits = True
                picked = picked[:2]

    return render(question, hits, picked, answer, nothing_fits)


def render(question: str, hits: list[dict], picked: list[int], answer: str,
           nothing_fits: bool = False) -> str:
    import html as _h
    e = _h.escape

    parts = [f"🔍 <b>{e(question[:120])}</b>", ""]
    if answer:
        parts += [e(answer), ""]
    else:
        # 모델을 못 쓴 경우. 왜 답 문장이 없는지 알려 주는 게 낫다.
        parts += ["<i>검색 결과만 보여드립니다 (요약 한도 소진)</i>", ""]

    parts.append("🤔 <b>혹시 이건가요</b>" if nothing_fits
                 else "📎 <b>관련 발행글</b>")
    for n in picked:
        h = hits[n]
        when = time.strftime("%m-%d %H:%M", time.localtime(h["published_at"]))
        link = search.channel_link(settings.telegram_channel_id,
                                   h["message_id"], h["thread_id"])
        title = e(h["headline"])
        head = f'<a href="{link}">{title}</a>' if link else title
        tail = f" <i>(같은 사건 {h['dupes'] + 1}건)</i>" if h["dupes"] else ""
        parts.append(f"• {head}\n   <i>{when} · {e(h['category'])}</i>{tail}")
    return "\n".join(parts)


# ── 텔레그램 수신 ─────────────────────────────────────────────

def thread_id() -> int | None:
    """'자료검색' 토픽의 thread id. topics.json 에 없으면 None."""
    import topics
    return topics.load().get(TOPIC_NAME)


# 멤버 확인 결과 캐시. {user_id: (확인시각, 멤버인가)}
_member_cache: dict[int, tuple[float, bool]] = {}
MEMBER_TTL = 3600.0

OK_STATUS = {"creator", "administrator", "member", "restricted"}


async def _is_member(client: httpx.AsyncClient, user_id: int) -> bool:
    """이 사람이 뉴스 그룹의 멤버인가. 1:1 대화를 아무에게나 열어 주지 않기 위한 것."""
    hit = _member_cache.get(user_id)
    if hit and time.time() - hit[0] < MEMBER_TTL:
        return hit[1]
    # 검색 봇은 그룹에 없을 수 있으므로 **발행 봇** 토큰으로 묻는다.
    url = _api("getChatMember", settings.telegram_bot_token)
    try:
        r = await client.get(url, params={"chat_id": settings.telegram_channel_id,
                                          "user_id": user_id}, timeout=20)
        ok = (r.json().get("result") or {}).get("status") in OK_STATUS
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[자료검색] 멤버 확인 실패({user_id}): {exc}")
        return False
    _member_cache[user_id] = (time.time(), ok)
    return ok


def guide_line() -> str:
    """사람에게 '어디로 물어보라'고 알려줄 한 줄."""
    name = settings.assistant_bot_username.lstrip("@")
    return (f"👉 @{name} 대화창을 열고 물어보세요 (처음 한 번 '시작')"
            if name else "저와의 대화창을 열고 같은 질문을 보내 주세요.")


async def _send(client: httpx.AsyncClient, chat_id, text: str,
                thread_id: int | None = None, reply_to: int | None = None,
                token: str | None = None):
    """임의의 대화로 보낸다. publisher.send_raw 는 뉴스 그룹 전용이라 따로 둔다."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "link_preview_options": {"is_disabled": True}}
    if thread_id:
        payload["message_thread_id"] = thread_id
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to}
    url = _api("sendMessage", token)
    try:
        r = await client.post(url, json=payload, timeout=30)
        if r.status_code != 200:
            print(f"[자료검색] 전송 실패 {r.status_code}: {r.text[:140]}")
    except httpx.HTTPError as exc:
        print(f"[자료검색] 전송 오류: {exc}")


async def _get_updates(client: httpx.AsyncClient, offset: int) -> list[dict]:
    url = _api("getUpdates")
    try:
        r = await client.post(url, json={
            "offset": offset, "timeout": POLL_TIMEOUT, "limit": 20,
            "allowed_updates": ["message"],
        }, timeout=POLL_TIMEOUT + 15)
    except httpx.HTTPError as exc:
        print(f"[자료검색] getUpdates 통신 실패: {exc}")
        return []
    if r.status_code != 200:
        print(f"[자료검색] getUpdates {r.status_code}: {r.text[:160]}")
        return []
    return r.json().get("result", [])


def _classify(msg: dict, tid: int | None) -> tuple[str, str] | None:
    """(어디서 온 질문인가, 질문) 또는 None.

    "dm"    1:1 대화 — 여기서만 실제로 답한다.
    "group" 자료검색 탭 — 답하지 않고 1:1 로 안내만 한다(내용이 모두에게 보인다).
    """
    chat = msg.get("chat") or {}
    if (msg.get("from") or {}).get("is_bot"):
        return None

    text = (msg.get("text") or msg.get("caption") or "").strip()
    text = _CMD.sub("", text).strip()
    if not text or text.startswith("/"):
        return None
    text = text[:300]

    if chat.get("type") == "private":
        return "dm", text
    if str(chat.get("id")) == str(settings.telegram_channel_id):
        if tid and msg.get("message_thread_id") == tid:
            return "group", text
    return None


async def run(client: httpx.AsyncClient, store, seconds: int = 240,
              dry_run: bool = False) -> None:
    """질문을 받아 답한다. `seconds` 동안만 돌고 끝낸다.

    발행 루프(bot.yml)가 전체 스윕 사이에 이걸 불러 주므로, 한 번에 길게 돌지
    않고 짧게 여러 번 도는 편이 응답이 빠르다.

    **답은 1:1 대화로만 나간다.** 그룹 탭에 쓴 질문에는 안내만 보낸다 —
    거기서 답하면 누가 무엇을 찾아봤는지 모두에게 보인다(모듈 설명 참고).
    """
    tid = thread_id()
    # 수신 위치는 **봇별로** 기억한다. 봇을 바꾸면 update_id 체계가 달라
    # 이전 봇의 offset 을 쓰면 새 질문을 통째로 건너뛴다.
    okey = f"answer_offset:{_bot_id(_bot_token())}"
    offset = int(store.kv_get(okey, "0") or 0)
    deadline = time.monotonic() + seconds
    answered = 0
    nudged = 0

    while time.monotonic() < deadline:
        updates = await _get_updates(client, offset)
        if not updates:
            continue
        for up in updates:
            offset = max(offset, up.get("update_id", 0) + 1)
            msg = up.get("message") or {}
            got = _classify(msg, tid)
            if not got:
                continue
            where, question = got
            user = (msg.get("from") or {}).get("id")

            if where == "group":
                # 답을 여기 쓰면 모두에게 보인다. 안내만 하고 끝낸다.
                nudged += 1
                if not dry_run:
                    await _send(client, msg["chat"]["id"],
                                "🔒 검색 결과는 <b>1:1 대화</b>로만 보내 드립니다.\n"
                                "여기서 답하면 다른 분들께도 보이기 때문입니다.\n\n"
                                + guide_line(),
                                thread_id=tid, reply_to=msg.get("message_id"),
                                token=settings.telegram_bot_token)
                continue

            # ── 1:1 ──
            if not await _is_member(client, user):
                print(f"[자료검색] 그룹 멤버가 아니라 거절: {user}")
                if not dry_run:
                    await _send(client, msg["chat"]["id"],
                                "이 봇은 뉴스 그룹 멤버만 쓸 수 있습니다.")
                continue

            print(f"[자료검색] 질문(1:1 {user}): {question[:50]}")
            text = await compose(store, question)
            if dry_run:
                print(text)
            else:
                await _send(client, msg["chat"]["id"], text,
                            reply_to=msg.get("message_id"))
            answered += 1
        # offset 은 **답한 뒤** 저장한다. 중간에 죽으면 같은 질문을 다시 받는다 —
        # 답을 빼먹는 것보다 두 번 답하는 쪽이 낫다.
        store.kv_set(okey, str(offset))

    if answered or nudged:
        print(f"[자료검색] 1:1 답변 {answered}건 · 그룹 안내 {nudged}건 "
              f"(남은 모델 한도 {_budget_left(store)}회)")


async def ask_once(store, question: str) -> None:
    """콘솔에서 한 건만 시험한다. 텔레그램을 거치지 않는다."""
    print(await compose(store, question))


def main() -> None:                      # pragma: no cover
    """`python assistant.py "질문"` 으로도 쓸 수 있게 둔다."""
    import sys
    from store import Store
    q = " ".join(sys.argv[1:])
    if not q:
        sys.exit('사용법: python assistant.py "질문"')
    asyncio.run(ask_once(Store(settings.db_path), q))


if __name__ == "__main__":               # pragma: no cover
    main()
