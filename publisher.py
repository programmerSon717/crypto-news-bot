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

    parts = [
        f"{data.get('header_emoji', '📰')} <b>{e(data['headline'])}</b>",
        "",
        f"☑️ {e(data['lede'])}",
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

    link_label = "원문 트윗" if data.get("_insight") else "기사 원문"
    parts += [
        "",
        f"🐧 {e(data['comment'])}",
        "",
        f'<a href="{e(url)}">{link_label}</a>',
        "",
        e(tags),
    ]
    return "\n".join(parts)


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
