"""국가 판정과 카테고리 강제 보정.

프롬프트로만 "중국 기사를 US Rates 에 넣지 마라"라고 해도 모델은 종종 어긴다.
실제로 인민은행 위안화 기사가 US Rates 로 발행된 적이 있다.
그래서 **발행 직전에 코드로 한 번 더 걸러낸다.** 프롬프트는 권고, 이쪽이 강제다.

판정은 키워드 기반이다. 완벽하진 않지만 나라 이름·중앙은행·통화·지수처럼
오탐이 적은 단어만 쓰기 때문에 실무에서는 충분히 걸러진다.
"""
import re

# 나라별 식별 키워드. 오탐을 줄이려고 일반명사(달러, 금리)는 넣지 않는다.
SIGNALS: dict[str, tuple[str, ...]] = {
    "CN": ("중국", "인민은행", "위안화", "위안 ", "PBOC", "상하이종합", "항셍", "홍콩H",
           "China", "Chinese", "yuan", "renminbi", "Shanghai", "Beijing"),
    "JP": ("일본", "일본은행", "BOJ", "엔화", "엔 캐리", "니케이", "금융청", "FSA",
           "Japan", "Japanese", "yen", "Nikkei"),
    "KR": ("한국", "코스피", "코스닥", "원화", "원·달러", "한국은행", "금통위", "금융위",
           "금감원", "FIU", "Korea", "Korean", "KOSPI", "won"),
    "US": ("미국", "연준", "FOMC", "Fed", "SEC", "CFTC", "재무부", "나스닥", "S&P",
           "다우", "미 국채", "美", "United States", "Nasdaq", "Treasury", "Powell"),
    "EU": ("유럽", "ECB", "유로존", "유로화", "MiCA", "Europe", "European", "euro"),
    "HK": ("홍콩", "SFC", "HKMA", "Hong Kong"),
    "SG": ("싱가포르", "MAS", "Singapore"),
    "AE": ("UAE", "두바이", "아부다비", "VARA", "Dubai", "Abu Dhabi", "Emirates"),
    "VN": ("베트남", "SBV", "Vietnam", "Vietnamese"),
}

# 나라 전용 탭. 이 탭에 다른 나라 기사가 들어가면 안 된다.
COUNTRY_TABS: dict[str, str] = {
    "US Rates": "US", "US Equities": "US",
    "Korea Rates": "KR", "Korea Equities": "KR",
    "국내정책": "KR", "US Policy": "US", "Japan Policy": "JP",
    "Hong Kong Policy": "HK", "Singapore Policy": "SG",
    "UAE Policy": "UAE", "Vietnam Policy": "VN", "China": "CN",
}
# UAE 키 표기 통일
COUNTRY_TABS["UAE Policy"] = "AE"

# 시장·거시 성격의 탭 (정책 탭과 구분해서 대체 탭을 고를 때 쓴다)
MARKET_TABS = {"Korea Rates", "US Rates", "Korea Equities", "US Equities",
               "Global Macro", "China"}

_WORD = re.compile(r"[A-Za-z]+")


def detect(text: str) -> str | None:
    """본문에서 가장 많이 언급된 나라 코드. 판단 근거가 없으면 None."""
    if not text:
        return None
    scores: dict[str, int] = {}
    for code, words in SIGNALS.items():
        n = sum(text.count(w) for w in words)
        if n:
            scores[code] = n
    if not scores:
        return None
    top = max(scores.values())
    winners = [c for c, v in scores.items() if v == top]
    # 동점이면 판정을 포기한다(억지로 고르면 오분류가 된다)
    return winners[0] if len(winners) == 1 else None


# 증시 탭 전용 판정 신호 — **지수 이름**.
#
# 일반 나라 단어로만 세면 증시 기사가 엉뚱한 탭에 간다. 실제 사고:
# "코스피 1.79% 하락, 금리 경계 반도체로 번졌다" 가 US Equities 로 갔다.
# 본문에 코스피가 3번 나오는데 미국·연준·Fed 가 5번 나와 미국이 이긴 것이다.
# 하지만 그 기사의 주어는 코스피다. 증시 기사에서는 **지수가 곧 그 시장**이므로
# 지수 이름만으로 먼저 판정한다.
INDEX_SIGNALS: dict[str, tuple[str, ...]] = {
    "KR": ("코스피", "코스닥", "KOSPI", "KOSDAQ"),
    "US": ("나스닥", "S&P", "다우", "Nasdaq", "Dow", "러셀"),
    "JP": ("니케이", "Nikkei"),
    "CN": ("상하이종합", "항셍", "Hang Seng", "Shanghai Composite"),
}
EQUITY_TABS = {"Korea Equities", "US Equities"}


def detect_index(text: str) -> str | None:
    """지수 이름으로 본 시장. 동점이거나 없으면 None(일반 판정에 맡긴다)."""
    scores = {c: sum(text.count(w) for w in ws) for c, ws in INDEX_SIGNALS.items()}
    scores = {c: n for c, n in scores.items() if n}
    if not scores:
        return None
    top = max(scores.values())
    winners = [c for c, v in scores.items() if v == top]
    return winners[0] if len(winners) == 1 else None


def text_of(data: dict) -> str:
    parts = [data.get("headline", ""), data.get("lede", ""),
             *(data.get("bullets") or []), data.get("context", "")]
    return " ".join(p for p in parts if p)


# 카테고리 성격. 옮길 때 금리↔증시↔정책이 섞이지 않게 한다.
KIND = {
    "US Rates": "rates", "Korea Rates": "rates",
    "US Equities": "equities", "Korea Equities": "equities",
    "국내정책": "policy", "US Policy": "policy", "Japan Policy": "policy",
    "Hong Kong Policy": "policy", "Singapore Policy": "policy",
    "UAE Policy": "policy", "Vietnam Policy": "policy",
}

# 나라 × 성격 → 갈 탭. 없는 조합은 catch-all 로 보낸다.
# 중국은 단일 탭이 정책·거시·증시를 다 받는다.
DEST: dict[str, dict[str, str]] = {
    "US": {"rates": "US Rates", "equities": "US Equities", "policy": "US Policy"},
    "KR": {"rates": "Korea Rates", "equities": "Korea Equities", "policy": "국내정책"},
    "CN": {"rates": "China", "equities": "China", "policy": "China"},
    "JP": {"policy": "Japan Policy"},
    "HK": {"policy": "Hong Kong Policy"},
    "SG": {"policy": "Singapore Policy"},
    "AE": {"policy": "UAE Policy"},
    "VN": {"policy": "Vietnam Policy"},
    "EU": {"policy": "해외정책"},
}


# catch-all 탭. 전용 탭이 있는 나라의 기사가 여기 머물러선 안 된다.
# (해외정책 탭에 미국 SEC 기사가 남아 있던 사고가 있었다)
CATCH_ALL = {"해외정책": "policy", "Global Macro": "rates"}


def enforce(category: str, data: dict) -> tuple[str, str | None]:
    """(보정된 카테고리, 사유). 문제가 없으면 사유는 None.

    - 나라 전용 탭인데 본문이 다른 나라를 말하면 옮긴다.
    - catch-all 탭(해외정책·Global Macro)인데 전용 탭이 있는 나라면 그 탭으로 옮긴다.
    """
    if category in CATCH_ALL:
        found = detect(text_of(data))
        if found is None:
            return category, None
        dest = DEST.get(found, {}).get(CATCH_ALL[category])
        if dest and dest != category:
            return dest, f"{category} → {dest} ({found} 전용 탭 있음)"
        return category, None

    want = COUNTRY_TABS.get(category)
    if not want:
        return category, None       # 이슈는 나라 제약이 없다

    text = text_of(data)

    # 증시 탭은 지수 이름이 우선이다(위 INDEX_SIGNALS 설명 참고).
    if category in EQUITY_TABS:
        idx = detect_index(text)
        if idx and idx != want:
            dest = DEST.get(idx, {}).get("equities") or DEST.get(idx, {}).get("rates")
            if dest and dest != category:
                return dest, f"{category} → {dest} (지수 기준 {idx})"
        if idx == want:
            return category, None   # 지수가 맞으면 나라 단어 빈도로 뒤집지 않는다

    found = detect(text)
    if found is None or found == want:
        return category, None

    kind = KIND.get(category, "policy")
    dest = DEST.get(found, {}).get(kind)
    if not dest:
        # 그 나라의 해당 성격 탭이 없으면 성격에 맞는 catch-all 로
        dest = "Global Macro" if kind in ("rates", "equities") else "해외정책"

    if dest == category:
        return category, None
    return dest, f"{category} → {dest} ({found} 기사)"
