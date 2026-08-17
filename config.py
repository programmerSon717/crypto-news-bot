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

    # RSS 소스: (이름, URL, 기본 분류 힌트) — 2026-08 기준 수신 확인된 피드만 등록
    rss_sources: list = field(default_factory=lambda: [
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
