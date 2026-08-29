"""Telegram Bot API로 채널에 발행. HTML parse mode + blockquote로 스크린샷 포맷 재현."""
import asyncio
import html
import re

import httpx

import topics
from config import settings

API = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"


async def _post(client: httpx.AsyncClient, method: str, *, json=None, data=None,
                files=None, tries: int = 5) -> dict | None:
    """텔레그램 API 호출. 429(레이트리밋)를 만나면 지시된 시간만큼 쉬고 재시도한다.

    이걸 빼먹으면 연속 발행 시 조용히 실패한다. 실제로 재정렬 중 73건이
    429로 사라진 적이 있다.
    """
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    for attempt in range(tries):
        r = await client.post(url, json=json, data=data, files=files, timeout=60)
        if r.status_code == 200:
            return r.json().get("result", {})

        body = {}
        try:
            body = r.json()
        except Exception:
            pass

        if r.status_code == 429:
            wait = body.get("parameters", {}).get("retry_after", 5) + 1
            print(f"[publisher] 레이트리밋 — {wait}초 대기 후 재시도")
            await asyncio.sleep(wait)
            continue

        # 5xx 는 일시적일 수 있으므로 한 번 더 시도한다
        if 500 <= r.status_code < 600 and attempt < tries - 1:
            await asyncio.sleep(2 * (attempt + 1))
            continue

        print(f"[publisher] {method} 실패: {r.status_code} {str(body)[:180]}")
        return None

    print(f"[publisher] {method} 재시도 소진")
    return None


def render(data: dict, url: str) -> str:
    """스크린샷 스타일:

    📕 헤드라인

    ☑️ 상황 요약

    📁 소제목
    ┃ • bullet
    ┃ • bullet

    🐧 코멘트

    기사 원문

    #국내
    """
    e = html.escape
    bullets = "\n".join(f"• {e(b)}" for b in data.get("bullets", []))
    tags = _build_tags(data)

    def field(key: str, emoji: str) -> str:
        """값 앞에 이모지가 이미 붙어 오는 경우가 있어(스키마 설명을 따라함) 중복을 제거한다."""
        return (data.get(key) or "").strip().removeprefix(emoji).strip()

    lede = field("lede", "☑️")
    comment = field("comment", "🐧")

    # 사진 캡션에 이미 제목이 들어간 경우엔 본문에서 제목을 뺀다(중복 방지).
    parts = [] if data.get("_headline_in_caption") else [
        f"{data.get('header_emoji', '📰')} <b>{e(data['headline'])}</b>", ""
    ]
    parts += [f"☑️ {e(lede)}"]

    # 퍼온 글은 원 게시자가 실제로 뭐라고 썼는지를 그대로 보여준다.
    origin_text = (data.get("origin_text") or "").strip()
    if origin_text:
        author, _ = origin_of(data)
        parts += ["", f"🗣 <b>{e(author)} 원문</b>",
                  f"<blockquote expandable>{e(origin_text)}</blockquote>"]
        ko = (data.get("origin_text_ko") or "").strip()
        if ko:
            parts += [f"<blockquote expandable>{e(ko)}</blockquote>"]

    parts += [
        "",
        f"📁 <b>{e(data.get('section_title', '주요내용'))}</b>",
        f"<blockquote>{bullets}</blockquote>",
    ]

    # 트위터 캡처 인사이트 경로에서만 채워지는 필드들. 일반 뉴스에는 없으므로 있을 때만 붙인다.
    for emoji, key, label in (
        ("📌", "context", "배경"),
        ("📈", "impact", "영향"),
        ("🔍", "watch", "지켜볼 포인트"),
    ):
        val = (data.get(key) or "").strip()
        # 모델이 스키마 설명을 따라 앞에 이모지를 붙여 오는 경우가 있어 중복을 제거한다.
        val = val.removeprefix(emoji).strip()
        if val:
            parts += ["", f"{emoji} <b>{label}</b>", e(val)]

    # 모델이 코멘트 앞에 🐧 를 직접 붙여 오는 경우가 있어(스키마 설명을 따라 하다가)
    # 그대로 두면 "🐧 🐧 ..." 이 된다. 아래 배경/영향 필드와 같은 방식으로 정리한다.
    comment = comment.removeprefix("🐧").strip()
    parts += ["", f"🐧 {e(comment)}", ""]

    # 이미 나간 글과 겹치지만 새 내용이 있어 올리는 경우, 무엇이 새로운지 밝힌다.
    # 독자가 "아까 본 것 같은데" 하고 넘기지 않도록.
    note = (data.get("update_note") or "").strip().removeprefix("🔁").strip()
    if note:
        parts += ["", f"🔁 <b>업데이트</b>", e(note), ""]

    # 실시간이 아닌 글(백필 등)은 언제 올라온 글인지 밝혀준다.
    posted = data.get("_posted_label")
    if posted and not data.get("_headline_in_caption"):   # 캡션에 이미 넣었으면 생략
        parts += [f"🕒 {e(posted)} 게시", ""]

    parts += [_source_line(data, url), "", e(tags)]
    return "\n".join(parts)


# 내부 소스 이름에는 수집 경로가 섞여 있다.
#   "규제:미국(Bloomberg.com)"  ← 구글뉴스 규제 검색으로 들어온 것
#   "CoinPost(일본)" "SCMP(홍콩)"  ← 지역 표시
#   "CryptoSlate 규제" "로이터(크립토)"  ← 같은 매체의 어느 피드인지
# 독자에게는 매체 이름만 보이는 게 맞으므로 이런 꼬리표를 떼어낸다.
_GNEWS = re.compile(r"^규제:[^(]*\((.+)\)$")
_TAIL_PAREN = re.compile(r"\s*\((.+)\)\s*$")
_TAIL_WORD = re.compile(r"\s+(규제|정책|크립토)$")
_DROPPABLE = {"일본", "홍콩", "아시아", "싱가포르", "베트남", "중국", "한국",
              "미국", "영국", "UAE", "규제", "정책", "크립토"}


# 해시태그에서 빼는 것들.
#   - 내부 분류 키(US Rates → #US_Rates). 실제로 발행돼서 걸러내게 됐다.
#   - 막연한 #국내/#해외. 탭 이름 태그가 그 역할을 더 정확히 하므로 겹치면 헷갈린다.
_VAGUE_TAGS = {"국내", "해외", "국내외"}


def _tab_tag(category: str) -> str:
    """분류 키 → 탭 이름 해시태그. 'US Rates' → '#미국매크로'

    이모지·공백을 떼고 글자만 남긴다. **코드가 직접 만든다** — 모델에 맡기면
    탭과 다른 값을 뱉어 태그와 실제 탭이 따로 논다.
    """
    import topics as _topics
    entry = _topics.CATEGORIES.get(category)
    name = entry[0] if entry else category
    clean = re.sub(r"[^0-9A-Za-z가-힣]", "", name)
    return f"#{clean}" if clean else ""


def _build_tags(data: dict) -> str:
    """탭 이름 태그를 맨 앞에 두고, 모델이 준 주제 태그를 뒤에 붙인다."""
    import topics as _topics
    banned = set()
    for key in _topics.CATEGORIES:
        if " " in key:                       # 여러 단어짜리 내부 키만 막는다
            banned |= {key.replace(" ", "").lower(), key.replace(" ", "_").lower()}

    out: list[str] = []
    first = _tab_tag(data.get("category", ""))
    if first:
        out.append(first)
    for t in data.get("hashtags", []):
        t = (t or "").strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t
        body = t[1:].replace(" ", "")
        if body.lower() in banned or body in _VAGUE_TAGS:
            continue
        if t not in out:
            out.append(t)
    return " ".join(out[:4])          # 너무 많으면 지저분하다


def source_label(raw: str) -> str:
    """수집처 내부 이름 → 독자에게 보여줄 매체명."""
    s = (raw or "").strip()
    if not s:
        return ""
    m = _GNEWS.match(s)          # 규제:미국(Bloomberg.com) → Bloomberg.com
    if m:
        s = m.group(1).strip()
    m = _TAIL_PAREN.search(s)
    if m:
        inner = m.group(1).strip()
        head = s[:m.start()].strip()
        # 지역·피드 종류 표시이거나, 앞부분을 되풀이한 것(도메인 형태 포함)이면 버린다
        norm_head = head.replace(" ", "").lower()
        norm_inner = re.sub(r"\.(com|net|org|kr|jp|co\.kr|news|pro|io|info)$", "",
                            inner.replace(" ", "").lower())
        if inner in _DROPPABLE or norm_inner == norm_head:
            s = head
    return _TAIL_WORD.sub("", s).strip()


def origin_of(data: dict) -> tuple[str, str]:
    """(표시 이름, 주소). 주소를 모르면 주소는 빈 문자열."""
    origin_url = (data.get("origin_url") or "").strip()
    author = (data.get("origin_author") or "").strip()
    platform = (data.get("origin_platform") or "").strip()

    # 핸들만 읽혔고 주소가 없으면 계정 페이지로라도 연결한다(트윗 주소는 추측 불가).
    if not origin_url and author.startswith("@") and platform == "X":
        origin_url = f"https://x.com/{author[1:]}"

    return (author or platform or "원문"), origin_url


def _source_line(data: dict, url: str) -> str:
    """출처 줄.

    퍼온 글은 **캡처를 올린 채널이 아니라 원 게시물이 출처**다.
    채널은 중간 경로일 뿐이라 출처로 적지 않는다. 원문을 못 찾으면
    게시자 이름만 밝히고 링크는 생략한다 — 없는 주소를 지어내지 않는다.
    """
    e = html.escape
    # 퍼온 글이 아니면 수집처가 곧 원문이다(RSS 기사 등).
    if not (data.get("_repost") or data.get("_insight")):
        link = f'<a href="{e(url)}">기사 원문</a>'
        media = source_label(data.get("_source_name", ""))
        return f"{link} - {e(media)}" if media else link

    label, origin_url = origin_of(data)
    if origin_url:
        return f'📎 출처: <a href="{e(origin_url)}">{e(label)}</a>'
    return f"📎 출처: {e(label)}"


async def send_raw(client: httpx.AsyncClient, text: str, thread_id: int | None,
                   reply_to: int | None = None) -> int | None:
    """이미 만들어진 본문을 그대로 발행한다(탭 이동·재정렬용)."""
    payload = {
        "chat_id": settings.telegram_channel_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    if reply_to:
        payload["reply_parameters"] = {"message_id": reply_to}
    result = await _post(client, "sendMessage", json=payload)
    return result.get("message_id") if result else None


async def delete(client: httpx.AsyncClient, message_id: int) -> bool:
    result = await _post(client, "deleteMessage",
                         json={"chat_id": settings.telegram_channel_id,
                               "message_id": message_id}, tries=2)
    return result is not None


CAPTION_LIMIT = 1024   # 텔레그램 사진 캡션 상한


def render_caption(data: dict) -> str:
    """사진에 붙일 짧은 캡션. 제목 + 게시시각 + 출처만."""
    e = html.escape
    parts = [f"{data.get('header_emoji', '📰')} <b>{e(data['headline'])}</b>"]
    posted = data.get("_posted_label")
    if posted:
        parts += ["", f"🕒 {e(posted)} 게시"]
    return "\n".join(parts)


async def publish(client: httpx.AsyncClient, data: dict, url: str,
                  image_url: str = "", image: bytes | None = None) -> int | None:
    """발행하고 message_id 를 돌려준다. 실패하면 None.

    캡처가 있으면 **사진을 실제로 업로드**한다. 링크 미리보기로 띄우면 텔레그램이
    렌더링을 건너뛰는 경우가 있어 이미지가 아예 안 보인다.
    사진 캡션은 1024자 제한이라 본문을 담을 수 없으므로, 사진(제목만) → 본문(답글)
    두 개로 나눠 보낸다. 답글로 묶여 화면에서는 한 덩어리로 보인다.
    """
    category = data.get("category")
    thread_id = topics.thread_id_for(category) if category else None
    where = f"[{category}]" if thread_id else ""

    photo_id = None
    if image:
        _last_file_id.clear()
        photo_id = await _send_photo(client, image, render_caption(data), thread_id)
        if photo_id is None:
            print("[publisher] 사진 업로드 실패 — 본문만 발행합니다")
        elif _last_file_id:
            data["_photo_file_id"] = _last_file_id[0]

    data["_headline_in_caption"] = photo_id is not None
    text = render(data, url)
    data["_rendered"] = text   # 나중에 다른 탭으로 옮길 때 그대로 재사용

    payload = {
        "chat_id": settings.telegram_channel_id,
        "text": text,
        "parse_mode": "HTML",
        # 본문에 남은 링크(출처 등)로 미리보기 카드가 붙으면 사진과 겹쳐 지저분해진다.
        "link_preview_options": {"is_disabled": True},
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    if photo_id:
        payload["reply_parameters"] = {"message_id": photo_id}

    result = await _post(client, "sendMessage", json=payload)
    if result is None:
        return photo_id

    body_id = result.get("message_id")
    # 사진과 본문 두 개로 나갔으면 둘 다 지울 수 있어야 한다
    data["_message_ids"] = [i for i in (photo_id, body_id) if i]

    pic = " +캡처" if photo_id else ""
    print(f"[publisher] 발행 완료{where}{pic}: {data['headline'][:50]}")
    # 사진이 먼저 올라갔으면 그게 이 글의 시작점이다
    return photo_id or body_id


async def _send_photo(client: httpx.AsyncClient, image: bytes, caption: str,
                      thread_id: int | None) -> int | None:
    data = {
        "chat_id": str(settings.telegram_channel_id),
        "caption": caption[:CAPTION_LIMIT],
        "parse_mode": "HTML",
    }
    if thread_id:
        data["message_thread_id"] = str(thread_id)

    result = await _post(client, "sendPhoto", data=data,
                         files={"photo": ("capture.jpg", image, "image/jpeg")})
    if result is None:
        return None
    # file_id 를 남겨두면 재정렬 때 이미지를 다시 올리지 않고 그대로 재사용할 수 있다
    sizes = result.get("photo") or []
    if sizes:
        _last_file_id.append(sizes[-1].get("file_id", ""))
    return result.get("message_id")


# 직전 sendPhoto 의 file_id 를 publish() 가 꺼내 쓰기 위한 임시 보관
_last_file_id: list[str] = []


async def send_photo_by_id(client: httpx.AsyncClient, file_id: str, caption: str,
                           thread_id: int | None) -> int | None:
    """이미 올린 사진을 file_id 로 다시 보낸다(재업로드 없음)."""
    payload = {
        "chat_id": settings.telegram_channel_id,
        "photo": file_id,
        "caption": caption[:CAPTION_LIMIT],
        "parse_mode": "HTML",
    }
    if thread_id:
        payload["message_thread_id"] = thread_id
    result = await _post(client, "sendPhoto", json=payload)
    return result.get("message_id") if result else None
