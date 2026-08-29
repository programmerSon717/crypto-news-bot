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


def is_offtopic(item) -> bool:
    """이 기사를 요약 없이 버려도 되는가."""
    # 지표·정책 이벤트(긴급 레인)는 크립토 낱말이 없는 게 정상이다 — 건드리지 않는다.
    if getattr(item, "force_category", ""):
        return False
    if item.source not in GENERAL_SOURCES:
        return False
    text = f"{item.title}\n{(item.body or '')[:BODY_SCAN]}"
    return not CRYPTO_PAT.search(text)
