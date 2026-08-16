# crypto-news-bot

크립토/블록체인 뉴스(국내 규제, 거래소 공지, 해외 이슈)를 수집해 텔레그램 채널 스타일로 큐레이션하는 봇.

## 파이프라인

```
[수집] Binance/Upbit 공지 API + RSS(블록미디어, 금융위, CoinDesk 등)
   ↓
[중복제거] SQLite (소스+URL 해시)
   ↓
[요약] Claude API → JSON (헤드라인/요약 bullet/펭귄 코멘트/중요도/국내·해외 분류)
   ↓
[필터] importance < MIN_IMPORTANCE 이면 스킵
   ↓
[발행] Telegram Bot API sendMessage (HTML + blockquote)
```

## 셋업

1. 텔레그램 채널 생성 → @BotFather 로 봇 생성 → 봇을 채널 **관리자**로 추가
2. `.env.example` → `.env` 복사 후 토큰 입력
3. 설치 및 실행:

```bash
pip install -r requirements.txt
python main.py --warm   # 최초 1회: 기존 뉴스를 발행 없이 등록 (과거 뉴스 폭탄 방지)
python main.py          # 상시 실행
```

## 커스터마이징 포인트

- **스타일/페르소나**: `prompts.py` 의 SYSTEM_PROMPT 하나로 전부 제어. 이모지 규칙, 코멘트 톤, bullet 개수 등.
- **소스 추가**: RSS는 `config.py` 의 `rss_sources` 에 한 줄 추가. API형 소스는 `collectors/` 에 모듈 추가 후 `main.py` 루프에 연결.
- **발행 임계값**: `.env` 의 `MIN_IMPORTANCE` (기본 3). 낮추면 공지까지 다 나가고, 높이면 대형 뉴스만.
- **메시지 포맷**: `publisher.py` 의 `render()`.

## 상시 구동 (VPS)

```ini
# /etc/systemd/system/crypto-news-bot.service
[Unit]
Description=Crypto News Telegram Bot
After=network.target

[Service]
WorkingDirectory=/opt/crypto-news-bot
ExecStart=/usr/bin/python3 main.py
Restart=always
EnvironmentFile=/opt/crypto-news-bot/.env

[Install]
WantedBy=multi-user.target
```

## 확장 아이디어

- **X(트위터) 모니터링**: 공식 API Basic tier 필요(유료). `collectors/x.py` 로 특정 계정(프로젝트 공식 계정, 온체인 분석가) 폴링.
- **타 텔레그램 채널 리포스트**: Telethon(유저 계정 API)으로 다른 채널을 구독해 소스로 사용.
- **본문 크롤링**: RSS summary가 빈약한 소스는 URL fetch 후 본문 추출(trafilatura)해서 `body` 에 넣으면 요약 품질 상승.
- **이미지/표 첨부**: 원문 og:image를 sendPhoto로 함께 발행.
- **일간 브리핑**: 하루치 발행 내역을 모아 아침에 요약 1건 발행.

## 주의사항

- 뉴스 저작권: 전문 복제 대신 **요약 + 원문 링크** 방식 유지 (현재 구조가 그렇게 설계됨).
- Binance/Upbit 공지 API는 비공식 엔드포인트라 스키마 변경·차단 가능성 있음. 실패 시 스킵하도록 되어 있음.
- 코멘트가 투자 권유로 읽히지 않도록 프롬프트에 가드 포함. 채널 공개 운영 시 면책 문구를 채널 소개에 넣는 것 권장.

## 24시간 무인 운영 (GitHub Actions)

맥을 꺼도 10분마다 자동 실행된다. `.github/workflows/bot.yml` 참고.

### 셋업

1. GitHub에서 **빈 리포지토리 생성** (public 권장 — private는 무료 2,000분/월이라 10분 주기면 초과)
2. 이 폴더를 푸시 (`.gitignore`가 `.env`·세션파일을 제외하므로 비밀정보는 올라가지 않음)
3. 리포 **Settings → Secrets and variables → Actions** 에서 등록:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHANNEL_ID`  (그룹이면 `-100`으로 시작하는 숫자 id)
   - `GEMINI_API_KEY`
4. **Actions 탭 → crypto-news-bot → Run workflow** 로 수동 1회 실행해 동작 확인

### 동작 방식

- `main.py --once` 로 1회 수집·발행 후 종료
- 발행 이력(`botstate.sqlite3`)을 리포에 커밋해 다음 실행이 중복 발행하지 않음
- `concurrency` 로 동시 실행을 막아 상태 경합 방지

### 주의

- 텔레그램 채널 수집(Telethon)은 클라우드에서 자동 비활성화된다.
  세션 파일이 개인 계정 접근 권한이라 리포에 올릴 수 없기 때문. 로컬에서만 동작.
- GitHub cron은 부하에 따라 수 분 지연될 수 있다(무료 티어 특성).

## 과거 뉴스 백필

```bash
python main.py --since 2026-08-16 --dry-run   # 발행 없이 대상 확인
python main.py --since 2026-08-16             # 실제 발행
```

- 지정 날짜 00:00 KST 이후 기사만 처리하며, 시간순으로 발행한다.
- 발행일이 없는 소스(거래소 공지 등)는 백필에서 제외된다 — 과거 공지 무한 유입 방지.
- 블록미디어는 RSS가 최신 10건만 주므로 공개 사이트맵으로 당일 전체를 보강한다.
