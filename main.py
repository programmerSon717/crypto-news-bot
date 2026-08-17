"""메인 루프: 수집 → 중복제거 → 요약/분류 → 카테고리 탭으로 발행.

첫 실행 시에는 기존 뉴스가 전부 새 뉴스로 잡히므로,
--warm 옵션으로 현재 뉴스를 발행 없이 '이미 본 것'으로 등록하고 시작하는 걸 권장.

    python main.py --warm                    # 최초 1회
    python main.py                           # 상시 실행
    python main.py --once                    # 1회만 (GitHub Actions 등 스케줄러용)
    python main.py --since 2026-08-16        # 해당 날짜 00시(KST) 이후 뉴스 백필
    python main.py --since 2026-08-16 --dry-run   # 발행 없이 대상만 출력
    python main.py --tg-since 2026-08-10     # 텔레그램 소스 채널만 백필(트위터 캡처 인사이트)
    python main.py --digest                  # 직전 1시간 카테고리별 요약 + 전체 브리핑
    python main.py --digest --hours 3        # 3시간 구간
    python main.py --digest --dry-run        # 발행 없이 확인
"""
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

from collectors import binance, upbit, rss, telegram_channels, tg_web, blockmedia_archive
from config import settings
from models import NewsItem
import publisher
from publisher import publish
from store import Store
from summarizer import summarize, summarize_insight

store = Store(settings.db_path)

KST = timezone(timedelta(hours=9))


def _arg_value(flag: str) -> str | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def filter_since(items: list[NewsItem], date_str: str) -> list[NewsItem]:
    """date_str(YYYY-MM-DD) 00:00 KST 이후 항목만. 날짜 불명(None)은 제외한다.

    백필에서 날짜 불명을 통과시키면 오래된 공지까지 무제한 유입되므로 의도적으로 버린다.
    """
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=KST).timestamp()
    kept, undated = [], 0
    for it in items:
        if it.published_at is None:
            undated += 1
            continue
        if it.published_at >= start:
            kept.append(it)
    if undated:
        print(f"[since] 날짜 불명 {undated}건 제외")
    return kept


def normalize_category(cat: str | None) -> str:
    """모델이 뱉은 카테고리를 실제 탭이 존재하는 값으로 보정한다.

    표기가 흔들리면(예: 'US policy', '미국정책') 탭 라우팅이 조용히 실패해
    본문에 섞여 발행되므로, 여기서 한 번 걸러준다.
    """
    import topics as _topics

    if not cat:
        return "이슈"
    if cat in _topics.CATEGORIES:
        return cat
    lowered = cat.strip().lower()
    for known in _topics.CATEGORIES:
        if known.lower() == lowered:
            return known
    aliases = {
        "미국정책": "US Policy", "일본정책": "Japan Policy",
        "홍콩정책": "Hong Kong Policy", "싱가포르정책": "Singapore Policy",
        "싱가폴정책": "Singapore Policy", "uae정책": "UAE Policy",
        "베트남정책": "Vietnam Policy", "국내": "국내정책", "해외": "해외정책",
        "한국금리": "Korea Rates", "미국금리": "US Rates",
        "한국증시": "Korea Equities", "미국증시": "US Equities",
        "korea rate": "Korea Rates", "us rate": "US Rates",
        "korea equity": "Korea Equities", "us equity": "US Equities",
    }
    if lowered in aliases:
        return aliases[lowered]
    print(f"[분류] 알 수 없는 카테고리 '{cat}' → 이슈로 처리")
    return "이슈"


# 이보다 오래된 글은 '실시간'이 아니라고 보고 게시 시각을 함께 표기한다.
FRESH_SEC = 3 * 3600


def is_repost(item: NewsItem) -> bool:
    """다른 텔레그램 채널에서 퍼온 글인가. 이 글들은 수집처가 출처가 아니다."""
    return item.source.startswith("TG:")


def topics_thread_id(category: str) -> int | None:
    import topics as _topics
    return _topics.thread_id_for(category)


def annotate_origin(data: dict, item: NewsItem):
    """발행 직전에 출처·게시시각 정보를 보강한다.

    - 채널 캡션에 원문 링크가 붙어 있으면 그게 가장 확실하므로 모델 판단보다 우선한다.
    - 실시간이 아닌 글이면 게시 시각을 표기하도록 표시를 남긴다.
    """
    if is_repost(item):
        # 렌더러가 수집처 링크를 출처로 쓰지 않게 하는 표시
        data["_repost"] = True
    if item.origin_url:
        data["origin_url"] = item.origin_url

    if item.published_at is None:
        return
    age = datetime.now(tz=KST).timestamp() - item.published_at
    if age > FRESH_SEC:
        dt = datetime.fromtimestamp(item.published_at, KST)
        data["_posted_label"] = dt.strftime("%Y-%m-%d %H:%M KST")


async def process_items(client: httpx.AsyncClient, items: list[NewsItem], warm: bool,
                        dry_run: bool = False):
    stats: dict[str, int] = {}
    budget = settings.run_budget_sec
    started = time.monotonic()
    deferred = 0

    for item in items:
        # 시간 상한을 넘으면 남은 항목은 손대지 않고 다음 실행으로 넘긴다.
        # '본 것으로 표시'를 하기 전에 끊어야 발행 없이 유실되는 항목이 생기지 않는다.
        if budget and time.monotonic() - started > budget:
            deferred += 1
            continue

        key = Store.make_key(item.source, item.unique_id)
        if store.is_seen(key):
            continue
        if not dry_run:
            store.mark_seen(key, item.source, item.title)
        if warm:
            continue

        # 다른 채널에서 퍼온 글은 이미지 유무와 무관하게 인사이트 경로로 처리한다.
        # 일반 뉴스 경로로 흘리면 출처가 '퍼온 채널'로 찍히므로 폴백하지 않는다.
        data = None
        if is_repost(item):
            posted = (
                datetime.fromtimestamp(item.published_at, KST).strftime("%Y-%m-%d %H:%M")
                if item.published_at else "불명"
            )
            data = await summarize_insight(item, posted)
        else:
            data = await summarize(item)
        if data is None:
            print(f"[skip] 무관/실패: {item.title[:60]}")
            continue
        if data.get("importance", 0) < settings.min_importance:
            print(f"[skip] 중요도 {data.get('importance')}: {item.title[:60]}")
            continue

        cat = normalize_category(data.get("category"))
        data["category"] = cat
        stats[cat] = stats.get(cat, 0) + 1
        annotate_origin(data, item)

        if dry_run:
            print(f"[dry-run][{cat}] 중요도{data.get('importance')} {data['headline'][:55]}")
            continue

        msg_id = await publish(client, data, item.url, image_url=item.image_url)
        if msg_id:
            _, origin = publisher.origin_of(data)
            store.record_published(key, msg_id, topics_thread_id(cat),
                                   origin or item.url, data["headline"],
                                   category=cat, lede=data.get("lede", ""),
                                   text=data.get("_rendered", ""))
        await asyncio.sleep(3)  # 텔레그램 rate limit 여유

    total = sum(stats.values())
    if total:
        dist = "  ".join(f"{k}:{v}" for k, v in stats.items())
        print(f"\n[집계] 발행대상 {total}건 — {dist}")
    if deferred:
        print(f"[집계] 시간 상한({budget}초) 도달 — {deferred}건은 다음 실행으로 미룸")


async def recent_tg_web(client: httpx.AsyncClient, hours: int = 6) -> list[NewsItem]:
    """공개 채널의 최근 글. 상시 수집용이라 짧은 구간만 본다."""
    if not settings.tg_web_channels:
        return []
    since = datetime.now(tz=KST).timestamp() - hours * 3600
    items: list[NewsItem] = []
    for ch in settings.tg_web_channels:
        items += await tg_web.fetch_since(client, ch, since, max_pages=2)
    return items


async def collect_all(client: httpx.AsyncClient) -> list[NewsItem]:
    items: list[NewsItem] = []
    items += await binance.fetch(client)
    items += await upbit.fetch(client)
    items += await rss.fetch_all(client, settings.rss_sources)
    items += await recent_tg_web(client)
    items += await telegram_channels.fetch()
    return items


async def exchange_loop(client: httpx.AsyncClient):
    while True:
        items = []
        items += await binance.fetch(client)
        items += await upbit.fetch(client)
        await process_items(client, items, warm=False)
        await asyncio.sleep(settings.poll_exchange_sec)


async def rss_loop(client: httpx.AsyncClient):
    while True:
        items = await rss.fetch_all(client, settings.rss_sources)
        items += await telegram_channels.fetch()
        await process_items(client, items, warm=False)
        await asyncio.sleep(settings.poll_rss_sec)


async def main():
    warm_only = "--warm" in sys.argv
    once = "--once" in sys.argv
    since = _arg_value("--since")
    dry_run = "--dry-run" in sys.argv

    tg_since = _arg_value("--tg-since")
    do_digest = "--digest" in sys.argv
    digest_hours = int(_arg_value("--hours") or 1)

    async with httpx.AsyncClient() as client:
        if do_digest:
            import digest
            await digest.run(client, store, hours=digest_hours, dry_run=dry_run)
            return

        if "--reroute" in sys.argv:
            import reroute
            only = _arg_value("--only")
            await reroute.run(client, store, dry_run=dry_run,
                              only={c.strip() for c in only.split(",")} if only else None)
            return

        if tg_since:
            # 텔레그램 소스 채널만 백필. 트위터 캡처를 비전 모델로 읽어 인사이트로 발행한다.
            start = datetime.strptime(tg_since, "%Y-%m-%d").replace(tzinfo=KST).timestamp()
            print(f"[TG백필] {tg_since} 00:00 KST 이후 채널 글 수집")
            items = []
            for ch in settings.tg_web_channels:
                items += await tg_web.fetch_since(client, ch, start)
            if not items:
                # 공개 채널이 아니면 웹 미리보기가 비어 나온다 → 개인 계정 경로로 재시도
                items = await telegram_channels.fetch_since(start)
            items.sort(key=lambda i: i.published_at or 0)  # 시간순 발행
            withpic = sum(1 for i in items if i.image)
            print(f"[TG백필] 대상 {len(items)}건 (이미지 포함 {withpic}건)"
                  f"{' — dry-run: 발행하지 않음' if dry_run else ''}")
            await process_items(client, items, warm=False, dry_run=dry_run)
            return

        if since:
            print(f"[백필] {since} 00:00 KST 이후 뉴스 수집")
            items = await collect_all(client)
            # RSS는 최신 몇 건만 주므로 블록미디어는 사이트맵으로 당일 전체를 보강
            items += await blockmedia_archive.fetch_date(client, since)
            items = filter_since(items, since)
            items.sort(key=lambda i: i.published_at or 0)  # 시간순 발행
            print(f"[백필] 대상 {len(items)}건"
                  f"{' (dry-run: 발행하지 않음)' if dry_run else ''}")
            await process_items(client, items, warm=False, dry_run=dry_run)
            return

        if warm_only:
            items = await collect_all(client)
            await process_items(client, items, warm=True)
            print(f"[warm] {len(items)}건 등록 완료. 이제 python main.py 로 실행하세요.")
            return

        if once:
            # GitHub Actions 등 스케줄러에서 주기적으로 호출하는 모드: 1회 수집 후 종료
            items = await collect_all(client)
            print(f"[once] {len(items)}건 수집")
            await process_items(client, items, warm=False)
            print("[once] 완료")
            return

        if settings.use_topics:
            import topics

            mapping = topics._load()
            if not mapping:
                print("[경고] USE_TOPICS=true 인데 topics.json 이 없습니다. "
                      "먼저 python setup_topics.py 를 실행하세요.")
            else:
                print(f"[topics] 탭 라우팅 활성: {mapping}")

        await asyncio.gather(
            exchange_loop(client),
            rss_loop(client),
        )


if __name__ == "__main__":
    asyncio.run(main())
