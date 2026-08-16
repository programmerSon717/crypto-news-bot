"""RSS 피드 수집기. 국내/해외 뉴스, 금융당국 보도자료 등."""
import asyncio
import calendar

import feedparser
import httpx

from models import NewsItem


def _entry_epoch(entry) -> float | None:
    """RSS 엔트리의 발행시각을 epoch(UTC)로. 없거나 파싱 실패면 None."""
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return calendar.timegm(t)  # feedparser 는 UTC 기준 struct_time 을 준다
            except (TypeError, ValueError):
                continue
    return None


async def fetch_feed(client: httpx.AsyncClient, name: str, url: str, region_hint: str) -> list[NewsItem]:
    try:
        r = await client.get(url, timeout=20, follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        parsed = await asyncio.to_thread(feedparser.parse, r.content)
        items = []
        for e in parsed.entries[:15]:
            link = getattr(e, "link", "")
            if not link:
                continue
            summary = getattr(e, "summary", "") or ""
            items.append(
                NewsItem(
                    source=name,
                    unique_id=link,
                    title=getattr(e, "title", ""),
                    url=link,
                    body=summary[:2000],
                    region_hint=region_hint,
                    published_at=_entry_epoch(e),
                )
            )
        return items
    except Exception as e:
        print(f"[rss:{name}] fetch 실패: {e}")
        return []


async def fetch_all(client: httpx.AsyncClient, sources: list) -> list[NewsItem]:
    results = await asyncio.gather(
        *[fetch_feed(client, name, url, hint) for name, url, hint in sources]
    )
    return [item for sub in results for item in sub]
