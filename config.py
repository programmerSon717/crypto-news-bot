"""환경변수 기반 설정. .env 파일 또는 시스템 환경변수 사용."""
import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class Settings:
    # Telegram
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_channel_id: str = os.getenv("TELEGRAM_CHANNEL_ID", "")  # 예: @my_crypto_brief 또는 -100xxxxxxxxxx

    # Gemini (무료 티어) — 요약 엔진
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

    # (구) Anthropic — 더 이상 사용하지 않음. 되돌리고 싶을 때 참고용으로만 남겨둠.
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    # 폴링 주기(초)
    poll_exchange_sec: int = int(os.getenv("POLL_EXCHANGE_SEC", "120"))   # 거래소 공지
    poll_rss_sec: int = int(os.getenv("POLL_RSS_SEC", "420"))             # 뉴스 RSS

    # 발행 필터: 중요도(1~5)가 이 값 미만이면 발행하지 않음
    min_importance: int = int(os.getenv("MIN_IMPORTANCE", "3"))

    # 1회 실행(--once)에서 발행에 쓸 시간 상한(초). 0이면 무제한.
    # CI 에서 job 타임아웃에 걸려 통째로 취소되면 발행 이력이 저장되지 않아
    # 다음 실행이 같은 뉴스를 다시 발행한다. 그 전에 스스로 멈추기 위한 장치.
    run_budget_sec: int = int(os.getenv("RUN_BUDGET_SEC", "0"))

    db_path: str = os.getenv("DB_PATH", "botstate.sqlite3")

    # 포럼 토픽(탭) 사용 여부. 그룹(슈퍼그룹)에서만 동작.
    use_topics: bool = os.getenv("USE_TOPICS", "false").lower() == "true"
    topics_file: str = os.getenv("TOPICS_FILE", "topics.json")

    # 공개 텔레그램 채널은 웹 미리보기(t.me/s/...)로 읽는다 — 로그인 불필요, 클라우드에서도 동작.
    # 비공개 채널일 때만 아래 Telethon 경로가 필요하다.
    tg_web_channels: list = field(default_factory=lambda: [
        c.strip() for c in os.getenv(
            "TG_WEB_CHANNELS", os.getenv("TG_SOURCE_CHANNELS", "")
        ).split(",") if c.strip()
    ])

    # 텔레그램 채널 수집(Telethon). my.telegram.org 에서 발급.
    tg_api_id: str = os.getenv("TG_API_ID", "")
    tg_api_hash: str = os.getenv("TG_API_HASH", "")
    tg_source_channels: list = field(default_factory=lambda: [
        c.strip() for c in os.getenv("TG_SOURCE_CHANNELS", "").split(",") if c.strip()
    ])

    # 국가별 규제(Regulation) 전용 소스.
    # 대부분의 크립토 매체는 Regulation 섹션 RSS 를 따로 제공하지 않는다(대부분 404).
    # 그래서 구글뉴스 검색 피드로 "그 나라 + 규제" 를 직접 겨냥한다.
    # 힌트에 '규제'가 들어가면 프롬프트가 정책 탭 후보로 우선 판단한다.
    regulation_sources: list = field(default_factory=lambda: [
        ("규제:미국", "crypto regulation SEC OR CFTC OR stablecoin bill when:7d", "en-US", "US", "US:en", "미국/규제"),
        ("규제:한국", "가상자산 규제 금융위 OR 금감원 OR 디지털자산기본법 when:7d", "ko", "KR", "KR:ko", "한국/규제"),
        ("규제:일본", "暗号資産 規制 金融庁 OR ステーブルコイン when:7d", "ja", "JP", "JP:ja", "일본/규제"),
        ("규제:홍콩", "Hong Kong crypto regulation SFC OR stablecoin when:7d", "en-US", "US", "US:en", "홍콩/규제"),
        ("규제:싱가포르", "Singapore crypto regulation MAS OR digital token when:7d", "en-US", "US", "US:en", "싱가포르/규제"),
        ("규제:UAE", "UAE OR Dubai crypto regulation VARA OR ADGM when:7d", "en-US", "US", "US:en", "UAE/규제"),
        ("규제:베트남", "quy định tài sản số OR crypto Việt Nam when:7d", "vi", "VN", "VN:vi", "베트남/규제"),
        ("규제:중국", "China crypto regulation PBOC OR digital yuan when:7d", "en-US", "US", "US:en", "중국/규제"),
    ])

    # RSS 소스: (이름, URL, 기본 분류 힌트) — 2026-08 기준 수신 확인된 피드만 등록
    rss_sources: list = field(default_factory=lambda: [
        # 크립토 매체의 규제 섹션 (실제로 규제만 걸러 나오는 것만 등록)
        ("CryptoSlate 규제", "https://cryptoslate.com/regulation/feed/", "해외/규제"),
        ("CryptoBriefing 규제", "https://cryptobriefing.com/category/regulation/feed/", "해외/규제"),
        ("Blockworks 정책", "https://blockworks.co/feed/category/policy", "해외/규제"),
        # 국내
        ("블록미디어", "https://www.blockmedia.co.kr/feed", "국내"),
        ("토큰포스트", "https://www.tokenpost.kr/rss", "국내"),
        ("블록체인투데이", "https://www.blockchaintoday.co.kr/rss/allArticle.xml", "국내"),
        ("금융위원회 보도자료", "http://www.fsc.go.kr/about/fsc_bbs_rss/?fid=0111", "국내"),
        ("블록미디어 정책", "https://www.blockmedia.co.kr/feed?cat=policy", "국내"),
        # 일본 — 현지 매체라야 금융청(FSA) 움직임이 제때 잡힌다
        ("CoinPost(일본)", "https://coinpost.jp/?feed=rss2", "일본"),
        ("あたらしい経済(일본)", "https://www.neweconomy.jp/feed", "일본"),
        # 홍콩·아시아
        ("Forkast(아시아)", "https://forkast.news/feed/", "아시아"),
        ("SCMP(홍콩)", "https://www.scmp.com/rss/36/feed", "홍콩"),
        # 싱가포르 — MAS·금융권 소식
        ("Business Times(싱가포르)", "https://www.businesstimes.com.sg/rss/banking-finance", "싱가포르"),
        ("Straits Times(싱가포르)", "https://www.straitstimes.com/news/business/rss.xml", "싱가포르"),
        # 미국 규제기관 원문
        ("CFTC 보도자료", "https://www.cftc.gov/RSS/RSSGP/rssgp.xml", "미국"),
        ("연준 보도자료", "https://www.federalreserve.gov/feeds/press_all.xml", "미국"),
        # 해외
        # 로이터는 2020년 자체 RSS 를 폐지했다. 구글뉴스 검색 피드로 우회한다.
        ("로이터(크립토)",
         "https://news.google.com/rss/search?q=site%3Areuters.com%20crypto%20OR%20bitcoin%20OR%20cryptocurrency&hl=en-US&gl=US&ceid=US%3Aen",
         "해외"),
        ("로이터(규제)",
         "https://news.google.com/rss/search?q=site%3Areuters.com%20crypto%20regulation%20OR%20SEC%20OR%20stablecoin&hl=en-US&gl=US&ceid=US%3Aen",
         "해외"),
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "해외"),
        ("Bitcoin.com", "https://news.bitcoin.com/feed/", "해외"),
        ("CryptoSlate", "https://cryptoslate.com/feed/", "해외"),
        ("Cointelegraph", "https://cointelegraph.com/rss", "해외"),
        ("Cointelegraph 규제", "https://cointelegraph.com/rss/tag/regulation", "해외"),
        ("The Block", "https://www.theblock.co/rss.xml", "해외"),
        ("Decrypt", "https://decrypt.co/feed", "해외"),
        ("The Defiant", "https://thedefiant.io/api/feed", "해외"),
        ("Protos", "https://protos.com/feed/", "해외"),
        ("SEC 보도자료", "https://www.sec.gov/news/pressreleases.rss", "해외"),
    ])


settings = Settings()
