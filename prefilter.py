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
import re

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

PRICE_PAT = re.compile(
    r"급등|급락|폭등|폭락|치솟|고꾸라|반등|조정 국면|"
    r"사상 최고가|신고가|최고치 경신|저점|고점|"
    r"목표가|목표주가|가격 전망|시세 전망|전망치 상향|전망치 하향|"
    r"지지선|저항선|기술적 분석|차트|이평선|"
    r"프리미엄 지표|김치 프리미엄|도미넌스|시총 순위|시가총액 비교|"
    r"순유입|순유출|순매수|순매도|자금 유입|자금 유출|입출금 규모|"
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
    if item.source not in GENERAL_SOURCES:
        return False
    text = f"{item.title}\n{(item.body or '')[:BODY_SCAN]}"
    return not CRYPTO_PAT.search(text)
