"""Telegram Bot API로 채널에 발행. HTML parse mode + blockquote로 스크린샷 포맷 재현."""
import html

import httpx

import topics
from config import settings

API = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"


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
    tags = " ".join(data.get("hashtags", []))

    def field(key: str, emoji: str) -> str:
        """값 앞에 이모지가 이미 붙어 오는 경우가 있어(스키마 설명을 따라함) 중복을 제거한다."""
        return (data.get(key) or "").strip().removeprefix(emoji).strip()

    lede = field("lede", "☑️")
    comment = field("comment", "🐧")

    parts = [
        f"{data.get('header_emoji', '📰')} <b>{e(data['headline'])}</b>",
        "",
        f"☑️ {e(lede)}",
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

    parts += ["", f"🐧 {e(comment)}", ""]

    # 실시간이 아닌 글(백필 등)은 언제 올라온 글인지 밝혀준다.
    posted = data.get("_posted_label")
    if posted:
        parts += [f"🕒 {e(posted)} 게시", ""]

    parts += [_source_line(data, url), "", e(tags)]
    return "\n".join(parts)


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
        return f'<a href="{e(url)}">기사 원문</a>'

    label, origin_url = origin_of(data)
    if origin_url:
        return f'📎 출처: <a href="{e(origin_url)}">{e(label)}</a>'
    return f"📎 출처: {e(label)}"


async def publish(client: httpx.AsyncClient, data: dict, url: str,
                  image_url: str = "") -> int | None:
    """발행하고 message_id 를 돌려준다. 실패하면 None.

    캡처 이미지가 있으면 링크 미리보기로 본문 위에 띄운다. sendPhoto 는 캡션이
    1024자로 제한돼 인사이트 본문이 잘리므로 쓰지 않는다.
    """
    text = render(data, url)
    payload = {
        "chat_id": settings.telegram_channel_id,
        "text": text,
        "parse_mode": "HTML",
    }

    if image_url:
        payload["link_preview_options"] = {
            "url": image_url,
            "prefer_large_media": True,
            "show_above_text": True,
        }
    elif data.get("_repost"):
        # 이미지가 없으면 텔레그램이 본문의 링크(=퍼온 채널)로 미리보기 카드를 만든다.
        # 카드에 채널 이름이 박혀 출처가 그쪽으로 보이므로 아예 끈다.
        payload["link_preview_options"] = {"is_disabled": True}
    else:
        payload["disable_web_page_preview"] = False

    # 카테고리에 해당하는 탭(토픽)으로 라우팅. 토픽 미사용이면 그대로 본문에 발행.
    category = data.get("category")
    thread_id = topics.thread_id_for(category) if category else None
    if thread_id:
        payload["message_thread_id"] = thread_id

    r = await client.post(API, json=payload, timeout=20)
    if r.status_code != 200:
        print(f"[publisher] 발행 실패: {r.status_code} {r.text[:200]}")
        return None

    where = f"[{category}]" if thread_id else ""
    pic = " +캡처" if image_url else ""
    print(f"[publisher] 발행 완료{where}{pic}: {data['headline'][:50]}")
    return r.json().get("result", {}).get("message_id")
