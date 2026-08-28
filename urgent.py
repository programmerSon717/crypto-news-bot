"""긴급 레인 — 지표 발표·FOMC·잭슨홀처럼 늦으면 가치가 없어지는 건을 즉시 잡는다.

일반 폴링은 소스 60여 곳을 20분 주기로 훑는다. 그 주기로는 경제지표 발표가 최대
20분 늦고, 잭슨홀 연설처럼 시장이 실시간 반응하는 건은 이미 늦은 뒤에 나간다.
실제로 2026-08-28 워시 의장 잭슨홀 연설(23:00 KST)이 그날 폴링에 잡히지 않았다.

그래서 **소스를 몇 곳으로 좁혀 짧은 주기로 따로 돌린다.** 전체 스윕과 분리돼 있어
일반 파이프라인의 동작·비용에 영향을 주지 않는다.
"""
import re

import httpx

from collectors import rss
from config import settings
from models import NewsItem

# 사용자가 지정한 탭. 지표·정책 이벤트는 나라와 무관하게 여기로 모은다.
TARGET_TAB = "Global Macro"

# 제목에 하나라도 걸리면 긴급으로 본다. **제목만 본다** — 본문까지 보면
# 크립토 기사에 스치듯 언급된 것까지 걸려 오탐이 급증한다.
#
# 단순 단어 매칭으로는 새는 게 많아 정규식을 쓴다. 실제로 놓쳤던 것들:
#   "연준 선호 인플레이션 지표, 7월에도 목표치 상회"  ← PCE 발표인데 'PCE'가 없다
#   "체코 경제, 2분기 0.4% 성장"                  ← GDP 발표인데 '성장률'이 아니다
# 반대로 '인플레이션'만 넣으면 논평 기사까지 다 걸리므로, 발표를 뜻하는 말
# (상승·둔화·지표·기록 등)과 함께 있을 때만 잡는다.
KEYWORDS: dict[str, str] = {
    "CPI":   r"소비자\s?물가|\bcpi\b|인플레이션.{0,25}(상승|하락|둔화|가속|지표|기록|예상|%)",
    "PCE":   r"\bpce\b|개인\s?소비\s?지출",
    "PPI":   r"생산자\s?물가|\bppi\b",
    "고용":   r"비농업|고용\s?(지표|보고서|동향)|실업률|실업수당|nonfarm|non-farm|unemployment|payroll",
    "PMI":   r"\bism\b|\bpmi\b|구매관리자",
    "GDP":   r"\bgdp\b|국내총생산|성장률|\d\s?분기[^,]{0,30}성장",
    "FOMC":  r"\bfomc\b|연방공개시장위원회|금리\s?(결정|인상|인하|동결)|기준금리",
    "잭슨홀": r"잭슨홀|jackson\s?hole",
}
_COMPILED = {k: re.compile(v, re.I) for k, v in KEYWORDS.items()}


def urgency_of(title: str) -> str | None:
    """제목이 어떤 긴급 항목에 해당하는가. 아니면 None."""
    t = title or ""
    for label, pat in _COMPILED.items():
        if pat.search(t):
            return label
    return None


async def collect(client: httpx.AsyncClient) -> list[NewsItem]:
    """긴급 소스만 훑어 지정 키워드에 걸린 항목을 돌려준다."""
    items = await rss.fetch_all(client, settings.urgent_sources)
    hits: list[NewsItem] = []
    for it in items:
        label = urgency_of(it.title)
        if not label:
            continue
        # 지표·정책 이벤트는 지정 탭으로 강제한다(중요도 문턱도 적용되지 않는다).
        it.force_category = TARGET_TAB
        it.region_hint = f"{it.region_hint}/긴급:{label}".lstrip("/")
        hits.append(it)
    if items:
        print(f"[긴급] 소스 {len(settings.urgent_sources)}곳에서 {len(items)}건 중 "
              f"{len(hits)}건이 지표·정책 이벤트")
    return hits
