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
    python main.py --resort                  # 탭 안 글을 최신순으로 재정렬
    python main.py --resort --only 이슈       # 특정 탭만
"""
import asyncio
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

from collectors import (binance, upbit, rss, telegram_channels, tg_web,
                        blockmedia_archive, blockmedia_research, coin68,
                        regulation)
import country
from config import settings
from models import NewsItem
import prefilter
import publisher
from publisher import publish
from store import Store, normalize_url
import summarizer
from summarizer import summarize, summarize_briefing, summarize_insight

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
        "global macro": "Global Macro", "글로벌거시": "Global Macro",
        "중국": "China", "china": "China", "china policy": "China",
    }
    if lowered in aliases:
        return aliases[lowered]
    print(f"[분류] 알 수 없는 카테고리 '{cat}' → 이슈로 처리")
    return "이슈"


# 이보다 오래된 글은 '실시간'이 아니라고 보고 게시 시각을 함께 표기한다.
FRESH_SEC = 3 * 3600

# 시장 지표 탭. 근거를 확인할 수 있는 언론 보도만 싣는다(커뮤니티 시황 코멘트 제외).
MARKET_TABS = country.MARKET_TABS


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

    # 발행 하단 '기사 원문' 옆에 매체명을 적기 위해 수집처를 넘긴다.
    data["_source_name"] = item.source

    # 기사·문서의 발행 시각은 **항상** 표기한다(사용자 요청).
    # 실시간이든 백필이든 언제 나온 뉴스인지 알 수 있어야 한다.
    if item.published_at is None:
        return
    dt = datetime.fromtimestamp(item.published_at, KST)
    data["_posted_label"] = dt.strftime("%Y-%m-%d %H:%M KST")


def is_stale(item: NewsItem) -> bool:
    """설정한 시간보다 오래된 기사인가. 날짜 불명은 최신으로 본다(공지 등)."""
    limit = settings.max_age_hours
    if not limit or item.published_at is None:
        return False
    return datetime.now(tz=KST).timestamp() - item.published_at > limit * 3600


def summarize_priority(item: NewsItem) -> tuple:
    """요약 대기열의 순번. 작을수록 먼저 처리한다.

    1) 국가별 규제·정책이 맨 앞. 물량이 많은 일반 기사에 밀리면 국가 탭이
       계속 비어 보인다. (기존 동작을 그대로 유지한다)
    2) 그다음 일반 뉴스.
    3) 리서치·분석은 맨 뒤. 시의성이 없어 몇 시간 늦어도 값이 안 떨어진다.

    같은 단계 안에서는 **최신순**이다. 예전에는 오래된 것부터 처리했는데,
    회당 상한이 7건으로 줄면서 5분 전 속보가 11시간 된 기사 뒤에 밀려
    12시간 제한에 걸려 버려지는 일이 생긴다. 뉴스 채널에는 최신이 먼저다.
    """
    hint = item.region_hint or ""
    if "리서치" in hint:
        tier = 2
    elif "/규제" in hint or "정책" in hint:
        tier = 0
    else:
        tier = 1
    return (tier, -(item.published_at or 0))


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    """같은 중복제거 키를 가진 항목이 여러 수집기에서 들어오면 첫 번째만 남긴다.

    블록미디어 리서치 글은 일반 피드에도 실린다. 키를 공유시켜 두 번 발행되는 것은
    막았지만, 어느 쪽이 먼저 처리되느냐에 따라 탭이 달라진다.
    수집 순서(리서치 먼저)를 그대로 존중해 여기서 잘라낸다.
    """
    seen_keys, seen_urls, out = set(), set(), []
    for it in items:
        key = Store.make_key(it.source, it.unique_id)
        if key in seen_keys:
            continue
        # 같은 매체를 여러 피드로 등록하면(일반 + 규제 등) 같은 기사가 두 번 들어온다.
        # 키는 소스이름이 섞여 있어 못 잡으므로 URL 로 한 번 더 거른다.
        # 여기서 안 거르면 두 건 다 요약돼 무료 한도를 두 배로 태운다.
        url_key = normalize_url(it.url)
        if url_key and url_key in seen_urls:
            continue
        seen_keys.add(key)
        if url_key:
            seen_urls.add(url_key)
        out.append(it)
    dropped = len(items) - len(out)
    if dropped:
        print(f"[중복] 수집 단계에서 같은 글 {dropped}건 정리")
    return out


def publish_order(items: list[NewsItem]) -> list[NewsItem]:
    """발행 순서 = 과거 → 최신.

    텔레그램은 탭을 열면 맨 아래로 이동한다. 그러므로 **최신이 맨 아래**에
    있어야 열자마자 최신 기사가 보인다. 그래서 오래된 것부터 보낸다.

    실시간으로 들어오는 새 글도 자동으로 맨 아래에 붙으므로 이 순서가 유지된다.
    날짜 불명은 맨 뒤(최신 자리)로 보낸다.
    """
    return sorted(items, key=lambda i: i.published_at or float("inf"))


# 무료 티어는 분당 15건이다. summarizer 가 12건/분으로 스로틀하므로
# 동시 실행을 늘려도 처리량은 안 늘고 429만 늘어난다.
SUMMARY_CONCURRENCY = 2

# 실행 예산 중 요약 단계에 쓸 비율. 나머지는 발행에 남긴다.
# 요약이 예산을 전부 먹으면 발행 루프가 통째로 밀려, 애써 요약한 결과를 버리게 된다.
SUMMARIZE_BUDGET_RATIO = 0.6

# 예산 초과·모델 소진으로 **손도 대지 않은** 항목. '요약 실패'(None)와 구분해야 한다.
# 실패는 '본 것'으로 찍고 넘어가지만, 이건 다음 실행이 다시 잡아야 한다.
SKIPPED = object()


async def _summarize_one(item: NewsItem) -> dict | None:
    """항목 1건을 알맞은 경로로 요약한다."""
    if item.deep:
        # 최상위 정책 이벤트(FOMC·연준 연설)는 bullet 10~16개짜리 심층 요약으로.
        return await summarize_briefing(item)
    if is_repost(item):
        posted = (
            datetime.fromtimestamp(item.published_at, KST).strftime("%Y-%m-%d %H:%M")
            if item.published_at else "불명"
        )
        return await summarize_insight(item, posted)
    return await summarize(item)


async def _summarize_ahead(items: list[NewsItem],
                           deadline: float | None = None) -> list:
    """요약을 미리 병렬로 돌린다. 발행은 순서대로 해야 하므로 결과 순서는 유지한다.

    한 건씩 처리하면 건당 15~20초(이미지 읽기)가 그대로 쌓여 수십 건에 수십 분이 걸린다.
    발행 자체는 텔레그램 레이트리밋 때문에 순차로 남겨둔다.
    """
    sem = asyncio.Semaphore(SUMMARY_CONCURRENCY)
    skipped = 0

    async def one(it: NewsItem):
        nonlocal skipped
        async with sem:
            # 예산을 넘겼거나 오늘 쓸 모델이 없으면 손대지 않고 다음 실행으로 넘긴다.
            # 이 검사가 없어서 소진된 날 요약 단계가 40분씩 돌다 job 타임아웃(20분)에
            # 통째로 잘렸고, 그 바람에 발행도 이력 커밋도 못 한 채 9일을 헛돌았다.
            if (deadline and time.monotonic() > deadline) or summarizer.all_exhausted():
                skipped += 1
                return SKIPPED
            try:
                return await _summarize_one(it)
            except Exception as e:
                print(f"[요약] 실패({it.title[:40]}): {e}")
                return None

    out = await asyncio.gather(*[one(i) for i in items])
    if skipped:
        print(f"[요약] {skipped}건은 시간/한도 부족 — 손대지 않고 다음 실행으로 넘김")
    return out


async def process_items(client: httpx.AsyncClient, items: list[NewsItem], warm: bool,
                        dry_run: bool = False):
    stats: dict[str, int] = {}
    budget = settings.run_budget_sec
    started = time.monotonic()
    deferred = 0
    dup = 0
    dropped = 0

    ordered = publish_order(dedupe_items(items))
    stale = 0

    # 아직 안 본 것만 추려 미리 요약해둔다(순서 유지). 오래된 건 요약도 하지 않는다.
    # URL 중복도 여기서 걸러야 한다. 발행 루프에서만 막으면 이미 요약이 끝난 뒤라
    # 무료 한도를 그냥 버리게 된다.
    pending = [i for i in ordered
               if not store.is_seen(Store.make_key(i.source, i.unique_id))
               and not store.is_url_seen(i.url)
               and not is_stale(i)
               and not prefilter.is_offtopic(i)]
    # ── 요약 순번(우선순위) ──
    # 무료 한도가 빡빡해 한 번에 몇 건밖에 못 돌린다. 그래서 **이 순서가 곧
    # 채널에 나가는 내용**이 된다. 발행 순서(과거→최신, RULES 1)와는 다른 얘기다.
    # 여기서 정하는 건 '무엇을 요약할 것인가'이고, 발행 순서는 아래 루프가 따로 지킨다.
    pending.sort(key=summarize_priority)
    if pending:
        head = pending[0]
        print(f"[우선순위] {len(pending)}건 대기 — 선두: {head.title[:40]}")
    if budget:
        # 시간 상한이 걸린 실행(CI)에서는 어차피 처리 못 할 분량까지 요약하면 낭비다.
        # 병목은 발행이 아니라 요약이다. summarizer 의 전역 스로틀(RATE_LIMIT_RPM)이
        # 호출 하나당 60/RPM 초를 강제하므로, 그 처리량으로 상한을 계산한다.
        per_item = 60.0 / summarizer.RATE_LIMIT_RPM
        cap = max(1, int(budget * SUMMARIZE_BUDGET_RATIO / per_item))
        if len(pending) > cap:
            print(f"[요약] 시간 상한 고려해 {len(pending)}건 중 {cap}건만 처리")
            pending = pending[:cap]

    summaries: dict[str, dict | None] = {}
    if pending and not warm:
        print(f"[요약] {len(pending)}건 병렬 처리 시작 (동시 {SUMMARY_CONCURRENCY})")
        deadline = (started + budget * SUMMARIZE_BUDGET_RATIO) if budget else None
        results = await _summarize_ahead(pending, deadline)
        # 손대지 않은 항목은 아예 담지 않는다 → 아래에서 '다음 실행으로' 처리된다.
        summaries = {Store.make_key(i.source, i.unique_id): d
                     for i, d in zip(pending, results) if d is not SKIPPED}
        print(f"[요약] 완료 — 유효 {sum(1 for d in results if d and d is not SKIPPED)}건")

    for item in ordered:
        # 시간 상한을 넘으면 남은 항목은 손대지 않고 다음 실행으로 넘긴다.
        # '본 것으로 표시'를 하기 전에 끊어야 발행 없이 유실되는 항목이 생기지 않는다.
        if budget and time.monotonic() - started > budget:
            deferred += 1
            continue

        key = Store.make_key(item.source, item.unique_id)
        if store.is_seen(key):
            continue

        # 소스 이름이 달라 키는 새것이지만 이미 처리한 기사일 수 있다
        # (같은 매체의 일반 피드 / 규제 피드에 같은 글이 실리는 경우).
        # '본 것'으로만 찍고 넘어가 다시 수집되지 않게 한다.
        if store.is_url_seen(item.url):
            if not dry_run:
                store.mark_seen(key, item.source, item.title)
            dup += 1
            continue

        # 이번 실행에서 요약하지 못한 항목(시간 예산 초과·모델 소진·처리량 상한)은
        # 손대지 않는다. '본 것'으로 찍어버리면 발행되지 않은 채 영영 사라진다.
        # 오래된 기사는 해당 없다 — 그건 아래에서 기록하고 버리는 게 맞다.
        outdated = is_stale(item)
        # 종합지에서 온 크립토 무관 기사. 요약하지 않고 '본 것'으로만 기록한다
        # (아래에서 mark_seen 되므로 다음 실행에 다시 잡히지 않는다).
        offtopic = prefilter.is_offtopic(item)
        if budget and not warm and not outdated and not offtopic and key not in summaries:
            deferred += 1
            continue

        if not dry_run:
            store.mark_seen(key, item.source, item.title)
            store.mark_url_seen(item.url, item.source, item.title)
        if warm:
            continue

        # 오래된 기사는 발행하지 않는다(위에서 '본 것'으로 기록했으니 다시 잡히지 않는다).
        if outdated:
            stale += 1
            continue
        if offtopic:
            dropped += 1
            continue

        # 미리 병렬로 돌려둔 결과를 쓴다. 없으면(단건 경로) 그 자리에서 처리.
        data = summaries[key] if key in summaries else await _summarize_one(item)
        if data is None:
            print(f"[skip] 무관/실패: {item.title[:60]}")
            continue
        # 탭이 지정된 소스(블록미디어 리서치 등)는 사용자가 직접 고른 것이므로
        # 중요도 문턱을 적용하지 않는다. 적용하면 분석·리서치 글이 거의 다 잘린다.
        if (not item.force_category
                and data.get("importance", 0) < settings.min_importance):
            print(f"[skip] 중요도 {data.get('importance')}: {item.title[:60]}")
            continue

        cat = normalize_category(data.get("category"))
        # 프롬프트가 나라를 틀리는 경우가 있어 코드로 한 번 더 강제한다
        cat, why = country.enforce(cat, data)
        if why:
            print(f"[분류보정] {why}: {data.get('headline','')[:45]}")
        # 수집기가 탭을 지정했으면 그게 최종이다(모델·country 판단보다 우선).
        if item.force_category and cat != item.force_category:
            print(f"[탭지정] {cat} → {item.force_category}: {item.title[:45]}")
            cat = item.force_category
        if cat in MARKET_TABS and is_repost(item):
            # 금리·증시 탭은 객관성이 중요해 언론 보도만 싣는다.
            # 개인 커뮤니티에서 퍼온 시황 코멘트는 근거를 확인할 수 없어 제외.
            print(f"[skip] 커뮤니티 출처는 {cat} 탭에 넣지 않음: {item.title[:50]}")
            continue
        data["category"] = cat
        stats[cat] = stats.get(cat, 0) + 1
        annotate_origin(data, item)

        if dry_run:
            print(f"[dry-run][{cat}] 중요도{data.get('importance')} {data['headline'][:55]}")
            continue

        msg_id = await publish(client, data, item.url,
                               image_url=item.image_url, image=item.image)
        if msg_id:
            _, origin = publisher.origin_of(data)
            ids = [i for i in data.get("_message_ids", []) if i != msg_id]

            # 지정 탭 외에 한 부 더 보내야 하는 글(FOMC·미국 거시·잭슨홀 → 이슈 탭).
            # 이미 렌더된 본문을 그대로 재사용하므로 모델을 다시 부르지 않는다.
            mirror_ids = []
            if item.mirror_to and item.mirror_to != cat:
                mtid = topics_thread_id(item.mirror_to)
                body = data.get("_rendered", "")
                if mtid and body:
                    mid2 = await publisher.send_raw(client, body, mtid)
                    if mid2:
                        mirror_ids.append(mid2)
                        print(f"[미러] {cat} → {item.mirror_to} 탭에도 발행 "
                              f"({data['headline'][:34]})")

            store.record_published(key, msg_id, topics_thread_id(cat),
                                   origin or item.url, data["headline"],
                                   category=cat, lede=data.get("lede", ""),
                                   text=data.get("_rendered", ""), extra_ids=ids,
                                   photo_file_id=data.get("_photo_file_id", ""),
                                   origin_at=item.published_at,
                                   mirror_ids=mirror_ids)
        await asyncio.sleep(5)  # 항목당 사진+본문 2건이 나가므로 여유를 둔다

    total = sum(stats.values())
    if total:
        dist = "  ".join(f"{k}:{v}" for k, v in stats.items())
        print(f"\n[집계] 발행대상 {total}건 — {dist}")
    if deferred:
        print(f"[집계] 시간 상한({budget}초) 도달 — {deferred}건은 다음 실행으로 미룸")
    if stale:
        print(f"[집계] {settings.max_age_hours}시간 초과된 과거 기사 {stale}건 제외")
    if dup:
        print(f"[집계] 다른 피드로 이미 처리한 같은 기사 {dup}건 제외")
    if dropped:
        print(f"[집계] 종합지에서 온 크립토 무관 기사 {dropped}건 제외 (요약 안 함)")


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
    # 리서치를 일반 RSS 보다 **먼저** 넣는다. 같은 글이 양쪽에 걸리면
    # dedupe_items() 가 먼저 온 쪽을 남기므로, 이래야 이슈 탭 강제가 유지된다.
    items += await blockmedia_research.fetch(client)
    items += await rss.fetch_all(client, settings.rss_sources)
    items += await coin68.fetch(client)      # 베트남 정책 (RSS 없음)
    # 국가별 규제 뉴스 — 각국 탭을 채우는 주 공급원
    items += await regulation.fetch_all(client, settings.regulation_sources)
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
        items = await blockmedia_research.fetch(client)
        items += await rss.fetch_all(client, settings.rss_sources)
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

        if "--purge" in sys.argv:
            import purge
            await purge.run(client, store, only=_arg_value("--only"), dry_run=dry_run)
            return

        if "--resort" in sys.argv:
            import resort
            await resort.run(client, store, only=_arg_value("--only"), dry_run=dry_run)
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
            items = publish_order(items)   # 과거 → 최신 순 발행
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
            items = publish_order(items)   # 과거 → 최신 순 발행
            print(f"[백필] 대상 {len(items)}건"
                  f"{' (dry-run: 발행하지 않음)' if dry_run else ''}")
            await process_items(client, items, warm=False, dry_run=dry_run)
            return

        if warm_only:
            items = await collect_all(client)
            await process_items(client, items, warm=True)
            print(f"[warm] {len(items)}건 등록 완료. 이제 python main.py 로 실행하세요.")
            return

        if "--urgent" in sys.argv:
            # 긴급 레인: 지표 발표·FOMC·잭슨홀 등 늦으면 가치가 없어지는 건만
            # 짧은 주기로 잡는다. 전체 스윕(--once)과 분리돼 있다.
            import urgent
            items = await urgent.collect(client)
            if items:
                for i in items:
                    print(f"  · {i.title[:58]}  [{i.region_hint}]")
            await process_items(client, items, warm=False, dry_run=dry_run)
            return

        if once:
            # GitHub Actions 등 스케줄러에서 주기적으로 호출하는 모드: 1회 수집 후 종료
            items = await collect_all(client)
            print(f"[once] {len(items)}건 수집"
                  f"{' — dry-run: 발행하지 않음' if dry_run else ''}")
            # dry_run 을 넘기지 않아 `--once --dry-run` 이 실제로 발행해버렸다.
            # 진단하려고 돌린 명령이 채널에 글을 올리면 안 된다.
            await process_items(client, items, warm=False, dry_run=dry_run)
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
