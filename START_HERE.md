# 여기부터 읽어라 — 크립토 뉴스 봇 인수인계

> 이 파일은 **클로드 계정을 바꾼 뒤 새 세션이 가장 먼저 읽어야 할 문서**다.
> 대화 기록은 계정 사이에 넘어가지 않는다. 디스크와 git 에 남은 것이 전부다.
> 최종 갱신: 2026-09-03

---

## 1. 무엇이 돌고 있나

크립토 뉴스를 수집해 AI 로 요약·분류하고 텔레그램 그룹의 카테고리 탭에 발행하는 봇이
**두 벌** 돌고 있다. 한국어판과 영문판이며, **리포가 물리적으로 갈라져 있다.**

| | 한국어판 | 영문판 |
|---|---|---|
| 로컬 | `~/Desktop/HanwhaDAPnews/crypto-news-bot` | `~/Desktop/HanwhaDAPnews/crypto-news-bot-en` |
| GitHub | `programmerSon717/crypto-news-bot` | `programmerSon717/crypto-news-bot-en` |
| 텔레그램 봇 | `@CryptoNewsKing_bot` | `@CryptoNewsKingEN_bot` |
| 그룹 | 손우진 크립토뉴스창고 | Woojin-Son's Crypto Stash |
| 콘솔 | https://programmerson717.github.io/crypto-news-bot/ | https://programmerson717.github.io/crypto-news-bot-en/ |
| 누적 발행 | 약 1,960건 | 약 1,170건 |

**두 리포는 코드를 공유하지 않는다.** 발행 로직·수집기·요약기 수정은 언어와 무관해
대부분 양쪽에 다 필요하다. 한쪽만 고치면 두 판이 조용히 달라진다.
자세한 배경은 각 리포의 `HANDOFF.md` 0절.

---

## 1-1. 대기 중인 작업

`PENDING.md` 를 먼저 봐라. 지금 넘어가는 작업과 그 배경이 적혀 있다.
끝낸 항목은 그 파일에서 지우면 된다.

## 2. 새 세션이 읽어야 할 순서

```
0  PENDING.md                          지금 넘어가는 작업
1  이 파일 (START_HERE.md)              전체 지형
2  crypto-news-bot/RULES.md             사용자가 반복 지시한 절대 규칙
3  crypto-news-bot/HANDOFF.md           한국어판 전체 (구조·운영·함정·남은 일)
4  crypto-news-bot-en/HANDOFF.md        영문판에서 다른 점만
5  crypto-news-bot/HANDOFF.local.md     연결 정보(토큰·키). git 에 없다. 로컬 전용
```

`RULES.md` 와 `HANDOFF.md` 는 두 리포에 각각 있고 내용이 다르다. 둘 다 봐야 한다.

---

## 3. 지금 당장 손봐야 할 것 — 딱 하나

**영문판에 `WORKFLOW_PAT` 시크릿이 없다.** 이것 때문에 영문 봇이 하루에 몇 시간씩 멈춘다.

봇은 5시간 30분짜리 루프를 돌고 끝날 때 스스로 다음 루프를 예약한다. 그 예약에
PAT 이 필요한데(기본 `GITHUB_TOKEN` 으로는 워크플로를 못 띄운다 — GitHub 이 무한
재귀 방지로 막아둠) 영문 리포에는 없다. 실제 로그:

```
⚠️ 다음 루프 예약 실패 (HTTP ...) — cron 이 대신 깨우기를 기다린다
```

cron 은 못 믿는다(한국어판 실측: 하루 6~32회, 간격 중앙값 42분, 최대 11시간 공백).
그래서 실제로 2026-09-03 에 영문판이 3시간 넘게 발행하지 않았다.

**사용자가 만들어 줘야 한다.**

```
github.com/settings/personal-access-tokens/new
  Token name        crypto-news-bot-en chain
  Expiration        No expiration          ← 만료되면 봇이 조용히 멈춘다
  Repository access → Only select repositories → crypto-news-bot-en
  Permissions       → Repository permissions → Actions → Read and write
```

받으면 영문 리포 시크릿에 `WORKFLOW_PAT` 으로 넣는다(등록 방법은 `HANDOFF.local.md`).

---

## 4. 사용자가 반복해서 못박은 것

이 지시들은 **매번 지켜야 한다.** 어긴 적이 있어 사용자가 여러 번 다시 말했다.

1. **잘 돌고 있는 것을 건드리지 마라.** 요청받은 범위만 고친다. 개선점이 보이면
   코드를 고치지 말고 **말로 보고해 승인을 받아라.**
2. **작업 중에 발행이 멈추면 안 된다.** 배포할 때 도는 실행을 함부로 취소하지 마라.
   코드 변경이 급하지 않으면 푸시만 하고 다음 루프에서 자연히 갈아타게 둔다.
3. **한국어판과 영문판은 서로 영향이 없어야 한다.** 리포가 갈라져 있으니 각각 작업하고,
   한쪽 작업이 다른 쪽에 닿지 않는지 확인해라.
4. **텔레그램 발행 포맷은 잘 만들었으니 건드리지 마라.**
5. **자주 푸시하지 마라.** 푸시마다 새 실행이 생겨 서로 취소한다. 여러 개를 고칠 때는
   모아서 한 번에 푸시한다. (30분에 7번 푸시해 폴링을 밀리게 한 적이 있다)
6. 질문에 `이건 질문이야` 가 붙으면 **답만 하고 코드를 고치지 마라.**

한국어판에는 `tests/ko_snapshot.py` 라는 회귀 검사가 있다. 발행문 렌더·탭 이름·
해시태그·매체명 정리를 지문으로 떠서 대조한다. **한국어판을 건드렸다면 반드시 돌려라.**

```bash
cd ~/Desktop/HanwhaDAPnews/crypto-news-bot && venv/bin/python tests/ko_snapshot.py
```

---

## 5. 계정을 바꿀 때 실제로 해야 할 일

**클로드 쪽**

1. 터미널에서 `/logout` → `/login` 으로 새 계정 로그인.
2. 새 세션에서 이 파일을 읽게 한다:
   `~/Desktop/HanwhaDAPnews/START_HERE.md 부터 읽고 이어받아라`

**넘어가는 것 / 안 넘어가는 것**

| | |
|---|---|
| ✅ 넘어감 | 리포 코드·문서 전부(git), 로컬 `.env`, 백업 폴더, GitHub 시크릿, 봇·그룹·API 키 |
| ❌ 안 넘어감 | 이 대화 기록, 클로드 메모리(`~/.claude/projects/…/memory/`)의 계정 귀속분 |

**계정 자체는 봇과 무관하다.** 봇은 GitHub Actions 에서 도는 것이고 클로드 계정과
아무 연결이 없다. 계정을 바꿔도 발행은 1초도 안 멈춘다.

**다만 GitHub 계정은 그대로여야 한다.** `programmerSon717` 로 로그인된 git 자격증명이
맥 키체인에 있고, 배포·시크릿 등록·실행 취소가 전부 그걸로 돈다.

---

## 6. 상태를 확인하는 가장 빠른 방법

```bash
# 두 봇이 살아 있는지 (마지막 발행 시각)
cd ~/Desktop/HanwhaDAPnews
for R in crypto-news-bot crypto-news-bot-en; do
  (cd $R && git fetch -q origin && git show origin/main:botstate.sqlite3 > /tmp/s.db)
  crypto-news-bot/venv/bin/python -c "
import sqlite3,datetime
KST=datetime.timezone(datetime.timedelta(hours=9))
c=sqlite3.connect('/tmp/s.db')
n=c.execute('select count(*) from published').fetchone()[0]
h,ts=c.execute('select headline,published_at from published order by published_at desc limit 1').fetchone()
d=datetime.datetime.fromtimestamp(ts,KST)
print(f'$R {n}건 · 마지막 {d:%m-%d %H:%M}')"
done
```

웹으로도 볼 수 있다 — 위 표의 콘솔 주소 두 개. 발행 추이·폴링 리듬·탭별 분포·
버전 되돌리기 버튼·기여 그래프가 다 들어 있다.

**"멈춘 것 같다" 는 신고를 받으면** 각 리포 `HANDOFF.md` 9절의 진단 순서를 따르라.
대부분 고장이 아니라 새벽이라 처리할 뉴스가 없는 것이다.

---

## 7. 백업

```
~/Desktop/HanwhaDAPnews/backups/2026-08-27_pre-bot-swap/
    봇 교체 직전 전체 사본(.env·세션·DB·.git 포함, RESTORE.md 와 체크섬 동봉)
```

git 태그 `verified-2026-08-28-newbot` 도 있다. **로컬 폴더만 되돌리면 절반이다** —
GitHub Actions 시크릿도 함께 되돌려야 클라우드가 옛 봇으로 돌아간다.

그리고 두 콘솔에 **버전 되돌리기** 기능이 있다. 목록에서 버튼 한 번이면 그 버전으로
배포되고 봇이 새로 뜬다. 되돌려도 발행 이력 DB·탭 캐시는 보존된다.
(각 리포 `HANDOFF.md` 9-1절)
