"""종합지 기사를 요약 전에 걸러낸다 — 무료 한도를 알짜에만 쓰기 위해.

왜 필요한가: 소스에는 크립토 전문지 말고 **종합지**도 있다(Gulf News, ZDNet Korea,
Vietnam News 등). 그 나라 규제·시장 소식을 놓치지 않으려고 넣은 것인데, 향수·비자·
유모차·풍력발전 기사까지 같이 딸려온다. 그것들이 요약 한도를 먹고 `relevant=false`
로 버려진다. 실측(2026-08-29): 18건 요약 중 10건이 이런 기사였다.

모델을 부르기 **전에** 제목·본문에서 크립토 낱말을 찾아 없으면 넘긴다.
비용이 0이고, 걸러진 건 모델 호출을 아예 안 한다.

**전문지에는 적용하지 않는다.** 블록미디어·CoinDesk 같은 곳은 애초에 크립토만 싣고,
제목에 낱말이 안 보여도 본문은 크립토인 경우가 많다. 모르는 소스도 전문지로 취급한다
— 실수로 진짜 크립토 뉴스를 버리는 쪽이 한도를 낭비하는 것보다 나쁘다.
"""
import json
import os
import re
import time

# 크립토가 주제가 아닌 매체. 여기서 온 기사만 낱말 검사를 받는다.
GENERAL_SOURCES = {
    # 종합 일간·경제지
    "Gulf News", "Arabian Business", "Economy Middle East",
    "Vietnam News", "VnExpress", "SCMP(홍콩)",
    "Business Times(싱가포르)", "Straits Times(싱가포르)",
    "ZDNet Korea", "Tech in Asia",
    # 핀테크 일반 — 크립토 비중이 낮다
    "FinTech News ME", "FinTech News SG",
    # 규제기관 피드 — 대부분 크립토와 무관한 은행·증권 공지다
    "금융위원회 보도자료", "SEC 보도자료", "CFTC 보도자료", "연준 보도자료",
}

# 크립토 전문지지만 **일반 테크·경제 기사를 많이 싣는** 매체.
#
# 실측(14일): 요약 호출 9,805건 중 토큰포스트 1,802건·PANews 1,064건이 요약만 하고
# 버려졌다(전체 낭비의 41%). 제목에 크립토 낱말이 없는 것만 세면 2,149건이다 —
# 마이크론 DDR5, 인도 심해탐사, Databricks CEO, 할리우드 AI 영화 같은 것들이다.
#
# **그런데 바로 막지 않는다.** 위 2,149건 중 541건(25%)은 실제로 발행됐던 기사다.
# 다만 그 수치는 **제목만으로 잰 것**이고, 실제 `is_offtopic` 은 본문 600자까지
# 본다(`seen` 테이블에 본문이 없어 오프라인으로는 재현할 수 없다). 본문을 넣으면
# 그 541건 중 상당수가 살아날 것이다 — 얼마나인지는 **돌려봐야 안다.**
#
# 그래서 기본값은 '기록만 하고 통과시키기'다. 하루 돌린 뒤 로그를 보고
# PREFILTER_AUDIT_ONLY=0 으로 실제 차단을 켠다. 추측으로 뉴스를 버리지 않는다.
CANDIDATE_SOURCES = {"토큰포스트", "PANews"}
AUDIT_ONLY = os.getenv("PREFILTER_AUDIT_ONLY", "1") == "1"

# 감사 기록을 남길 파일. docs/ 는 워크플로가 매 회차 커밋하므로 기록이 보존된다
# (Actions 로그는 90일 뒤 사라지고 내려받아 분석하기도 번거롭다).
AUDIT_FILE = os.getenv("PREFILTER_AUDIT_FILE", "docs/prefilter_audit.jsonl")
AUDIT_KEEP = 4000          # 파일이 무한히 자라지 않게 최근 N줄만 남긴다
_audit: list[dict] = []

# 크립토·디지털자산 낱말. 소스가 여러 언어라 한국어·영어·중국어·일본어를 함께 본다.
CRYPTO_PAT = re.compile(
    r"암호화폐|가상자산|디지털\s?자산|크립토|블록체인|스테이블\s?코인|"
    r"비트코인|이더리움|리플|솔라나|알트코인|코인|토큰|지갑|채굴|디파이|"
    r"거래소\s?(상장|공지|해킹)|"
    r"crypto|bitcoin|\bbtc\b|ethereum|\beth\b|blockchain|stablecoin|"
    r"digital\s?asset|token|defi|\bnft\b|web3|altcoin|\bxrp\b|solana|"
    r"binance|coinbase|tether|\busdt\b|\busdc\b|mining|wallet|"
    r"加密|区块链|稳定币|比特币|以太坊|代币|"
    r"暗号資産|仮想通貨|ブロックチェーン|ステーブルコイン|"
    r"tiền\s?điện\s?tử|tiền\s?mã\s?hóa|blockchain",
    re.I)

# 제목이 짧아 낱말이 안 걸릴 수 있어 본문 앞부분까지 본다.
BODY_SCAN = 600



# ── 가격·시황 기사를 모델 호출 전에 걸러낸다 ──
#
# 이 채널의 목적은 각국 규제·제도 흐름을 놓치지 않는 것이다. 시세 중계가 아니다.
# 그런데 수집물의 상당수가 가격 기사라, 요약을 해보고 나서야 버리게 된다.
# 실측(2026-09-03): 발행 78건에 요약 호출 500건이 나가 주 모델 일일 한도를 소진했다.
# 제목에서 미리 잡으면 그 호출을 통째로 아낀다.
#
# **오탐이 가장 위험하다.** "SEC, ETF 자금 유입 급증에 승인 경로 재검토" 같은 글은
# 가격 낱말이 있어도 규제 기사다. 그래서 규제·제도 신호가 하나라도 있으면 무조건
# 살린다(KEEP_PAT 이 PRICE_PAT 을 이긴다). 놓치는 쪽이 낭비보다 나쁘다.

# 반복 칼럼·온체인 수급 글. 실측(발행 이력 14일, 요약까지 간 것 기준)으로
# **발행률이 0%** 인 것만 올렸다. 이름이 고정돼 있어 오탐이 날 구조가 아니다.
#
#   [오후/저녁/모닝/자정 시세브리핑] 0/49    [트렌딩 나우] 0/13
#   [선물 고수 PICK] 0/11                   [코인 국장] 0/10
#   [월가 유동성 레이더] 0/12               [뉴욕 코인시황] 0/11
#   [온체인 주식선물] 0/14                  爆仓 0/39
#   浮盈·浮亏 0/37                          巨鲸·某地址 0/75
#
# **[속보] 는 넣지 않았다** — 발행률 8% 지만 속보는 놓치면 안 되는 쪽이다.
# 指数(지수)도 뺐다 — 29% 가 발행된다(거시지표 기사가 섞여 있다).
COLUMN_PAT = (
    r"\[[^\]]*시세\s?브리핑[^\]]*\]|\[트렌딩 나우\]|\[선물 고수[^\]]*\]|"
    r"\[코인 국장\]|\[코인 매집[^\]]*\]|\[월가 유동성[^\]]*\]|"
    r"\[뉴욕 코인시황\]|\[온체인 주식선물\]|\[자금 흐름 24시[^\]]*\]|"
    r"爆仓|浮盈|浮亏|巨鲸|某地址|某交易员|某巨|清算了|"
    r"레버리지 포지션 .*청산|청산 규모|全网合约爆仓"
)

PRICE_PAT = re.compile(
    COLUMN_PAT + r"|"
    r"급등|급락|폭등|폭락|치솟|고꾸라|반등|조정 국면|"
    r"사상 최고가|신고가|최고치 경신|저점|고점|"
    r"목표가|목표주가|가격 전망|시세 전망|전망치 상향|전망치 하향|"
    r"지지선|저항선|기술적 분석|차트|이평선|"
    r"프리미엄 지표|김치 프리미엄|도미넌스|시총 순위|시가총액 비교|"
    r"순유입|순유출|순매수|순매도|자금 유입|자금 유출|입출금 규모|"
    r"ETF[^|]{0,12}(유입|유출)액?|市值(排名|超|突破)|排名第|"
    r"고래 지갑|대규모 출금|대규모 이체|온체인 수급|"
    r"\d+% ?(상승|하락|급등|급락|오르|내리)|\d+배 (상승|급등)|"
    r"surge[ds]?|plunge[ds]?|soar[s]?|slump|rally|rebound|"
    r"all-time high|price target|price forecast|technical analysis|"
    r"support level|resistance level|dominance|net inflow|net outflow|"
    r"whale wallet|large withdrawal",
    re.I)

# 이 신호가 있으면 가격 낱말이 있어도 살린다. 규제·제도·사건이 주어인 글이다.
KEEP_PAT = re.compile(
    r"규제|정책|법안|입법|시행령|가이드라인|당국|감독|인가|허가|라이선스|승인|반려|"
    r"제재|과징금|기소|소송|판결|과세|세제|"
    r"금융위|금감원|FIU|기재부|한국은행|국회|"
    r"토큰증권|증권형 토큰|STO|RWA|실물자산|토큰화|"
    r"해킹|탈취|유출|취약점|익스플로잇|"
    r"상장 폐지|거래지원 종료|유의종목|"
    r"SEC|CFTC|FSA|MAS|VARA|SFC|HKMA|BIS|FATF|ESMA|MiCA|"
    r"regulat|polic|legislat|licen[cs]|approv|denie|sanction|lawsuit|ruling|tax|"
    r"security token|tokeni[sz]|real.world asset|"
    r"hack|exploit|breach|delist",
    re.I)

# 거시지표는 사용자가 유지하라고 한 것이다(2026-09-04). 지표 이름이 보이면 살린다.
# "미국 7월 공장 주문 0.9% 증가하며 반등" 처럼 '반등' 때문에 잘리던 것을 막는다.
MACRO_PAT = re.compile(
    r"\bCPI\b|\bPCE\b|\bPPI\b|\bPMI\b|\bGDP\b|\bFOMC\b|"
    r"소비자물가|생산자물가|물가상승률|인플레이션|고용지표|실업률|비농업|"
    r"기준금리|금리 결정|금리 인상|금리 인하|점도표|국채 금리|"
    r"공장 주문|산업생산|소매판매|무역수지|경상수지|구매관리자|"
    r"연준|연방준비|중앙은행|한국은행|인민은행|일본은행|유럽중앙은행|"
    r"consumer price|producer price|inflation|unemploy|payroll|"
    r"interest rate|rate (decision|hike|cut)|treasury yield|"
    r"factory order|industrial production|retail sales|trade balance|"
    r"federal reserve|central bank",
    re.I)


def is_price_story(item) -> bool:
    """가격·시황이 주제라 모델을 부를 값어치가 없는가.

    제목만 본다. 본문까지 보면 스치듯 언급된 가격 얘기에도 걸려 규제 기사를 버린다.
    """
    title = item.title or ""
    if KEEP_PAT.search(title) or MACRO_PAT.search(title):
        return False
    return bool(PRICE_PAT.search(title))

def is_offtopic(item) -> bool:
    """이 기사를 요약 없이 버려도 되는가."""
    # 지표·정책 이벤트(긴급 레인)는 크립토 낱말이 없는 게 정상이다 — 건드리지 않는다.
    if getattr(item, "force_category", ""):
        return False

    candidate = item.source in CANDIDATE_SOURCES
    if not candidate and item.source not in GENERAL_SOURCES:
        return False

    text = f"{item.title}\n{(item.body or '')[:BODY_SCAN]}"
    offtopic = not CRYPTO_PAT.search(text)

    if candidate and offtopic and AUDIT_ONLY:
        # 아직 막지 않는다. 하루치를 모아 보고 켤지 정한다(CANDIDATE_SOURCES 주석).
        _audit.append({"t": int(time.time()), "source": item.source,
                       "title": (item.title or "")[:160],
                       "url": (item.url or "")[:200]})
        print(f"[프리필터감사] 막혔을 것: [{item.source}] {(item.title or '')[:62]}")
        return False
    return offtopic


def flush_audit() -> int:
    """모아둔 감사 기록을 파일에 덧붙인다. 폴링 1회가 끝날 때 부른다.

    실패해도 조용히 넘어간다 — 감사 기록 때문에 발행이 멈추면 안 된다.
    """
    global _audit
    if not _audit:
        return 0
    n = len(_audit)
    try:
        os.makedirs(os.path.dirname(AUDIT_FILE) or ".", exist_ok=True)
        lines = []
        if os.path.exists(AUDIT_FILE):
            with open(AUDIT_FILE, encoding="utf-8") as f:
                lines = f.read().splitlines()
        lines += [json.dumps(r, ensure_ascii=False) for r in _audit]
        with open(AUDIT_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines[-AUDIT_KEEP:]) + "\n")
    except OSError as exc:
        print(f"[프리필터감사] 기록 실패(무시): {exc}")
    finally:
        _audit = []
    return n
