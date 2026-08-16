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


def _source_line(data: dict, url: str) -> str:
    """출처 줄.

    퍼온 글은 **캡처를 올린 채널이 아니라 원 게시물이 출처**다.
    원문 주소를 알면 그리로 걸고, 모르면 최소한 게시자만이라도 밝힌 뒤
    실제로 가져온 위치(채널 글)를 함께 남긴다.
    """
    e = html.escape
    if not data.get("_insight"):
        return f'<a href="{e(url)}">기사 원문</a>'

    origin_url = (data.get("origin_url") or "").strip()
    author = (data.get("origin_author") or "").strip()
    platform = (data.get("origin_platform") or "").strip()

    # 핸들만 읽혔고 주소가 없으면 계정 페이지로라도 연결한다(트윗 주소는 추측 불가).
    if not origin_url and author.startswith("@") and platform == "X":
        origin_url = f"https://x.com/{author[1:]}"

    label = author or platform or "원문"
    if origin_url:
        return (f'📎 출처: <a href="{e(origin_url)}">{e(label)}</a>'
                f' · <a href="{e(url)}">퍼온 곳</a>')
    return f'📎 출처: {e(label)} · <a href="{e(url)}">퍼온 곳</a>'


async def publish(client: httpx.AsyncClient, data: dict, url: str):
    text = render(data, url)
    payload = {
        "chat_id": settings.telegram_channel_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    # 카테고리에 해당하는 탭(토픽)으로 라우팅. 토픽 미사용이면 그대로 본문에 발행.
    category = data.get("category")
    thread_id = topics.thread_id_for(category) if category else None
    if thread_id:
        payload["message_thread_id"] = thread_id

    r = await client.post(API, json=payload, timeout=15)
    if r.status_code != 200:
        print(f"[publisher] 발행 실패: {r.status_code} {r.text[:200]}")
    else:
        where = f"[{category}]" if thread_id else ""
        print(f"[publisher] 발행 완료{where}: {data['headline'][:50]}")
