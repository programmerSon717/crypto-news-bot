from dataclasses import dataclass


@dataclass
class NewsItem:
    source: str          # 수집처 이름 (예: "Binance 공지", "블록미디어")
    unique_id: str       # 소스 내 고유값 (URL, article id 등) — 중복제거 키
    title: str
    url: str
    body: str = ""       # 본문 또는 요약문 (있으면 요약 품질이 좋아짐)
    region_hint: str = ""  # "국내" / "해외" 힌트 (LLM이 최종 판단)
    published_at: float | None = None  # 발행시각(epoch). 백필 날짜 필터용. 불명이면 None

    # 트위터 캡처처럼 본문이 이미지 안에 있는 경우: 이미지 바이트를 실어 보내 비전 모델이 읽게 한다.
    # 값이 있으면 summarizer 가 이미지 경로로 처리한다.
    image: bytes | None = None
    image_mime: str = "image/jpeg"
    image_url: str = ""  # 아직 안 받았을 때의 원본 주소(있으면 나중에 지연 다운로드 가능)
