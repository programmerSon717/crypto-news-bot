# 대기 중인 작업

> 새 세션은 이 파일을 먼저 확인해라. 끝난 항목은 지우면 된다.
> 최종 갱신: 2026-09-04

---

## 1. 발행량·한도 재측정 — 2026-09-05 15:27 KST 이후

**왜:** 2026-09-04 에 두 가지를 바꿨다.
- 발행 범위를 가격·수급 제외 / 규제·제도 중심으로 (프롬프트, 양쪽 리포)
- 가격 기사를 모델 호출 전에 거르는 사전 필터 (코드, 양쪽 리포)

그 효과를 하루 뒤에 재기로 했다.

**어떻게:** 명령 한 줄이면 된다. 읽기만 하며 봇을 건드리지 않는다.

```bash
cd ~/Desktop/HanwhaDAPnews && python3 tools/measure_daily.py --stdout
```

`--stdout` 을 빼면 `reports/YYYY-MM-DD.md` 로 저장된다.

**무엇을 볼 것인가**

| | 기준선 (09-03, 변경 전) | 예측치 |
|---|---|---|
| 한국어판 발행 | 357건 | 약 214건 |
| 영문판 발행 | 273건 | 약 156건 |
| 합계 | 630건 | 약 370건 |

- 규제·정책 탭 비중이 늘었는지가 핵심이다 (목적이 각국 규제 모니터링이다)
- 한도는 리셋(16:00 KST) 직전에 봐야 하루치 소모량이 잡힌다
- 너무 줄었다면 원인을 구분해라 — 수집이 준 것인지 · 필터가 과한 것인지 · 한도에 걸린 것인지

**주의:** 표본 검증에 모델을 많이 부르면 한도를 또 먹는다. 2026-09-04 에 50건씩
100번을 불러 주 모델 일일 한도를 소진시킨 적이 있다. 필요하면 10건 이하로.

---

## 2. 영문판 `WORKFLOW_PAT` — 아직 없음

영문 리포에 이 시크릿이 없어 자체 체인이 끊기고, cron 공백이 그대로 정지가 된다.
2026-09-03 에 실제로 3시간 넘게 멈췄다. 로그: `다음 루프 예약 실패 (HTTP ...)`

사용자가 만들어 줘야 한다 — fine-grained PAT, `crypto-news-bot-en` 만,
**Actions: Read and write**, 만료 없음. 등록 방법은
`crypto-news-bot-en/HANDOFF.local.md`.

---

## 3. launchd 자동 측정 — 권한 때문에 막혀 있음

`~/Library/LaunchAgents/com.woojin.cryptonews.measure.plist` 를 등록해 뒀지만
macOS 가 막는다. launchd 로 도는 프로세스는 `~/Desktop` 을 못 읽는다(TCC 보호).

```
/usr/bin/python3: can't open file '.../measure_daily.py': [Errno 1] Operation not permitted
```

**풀려면 둘 중 하나다.**

1. 시스템 설정 → 개인정보 보호 및 보안 → **전체 디스크 접근 권한** 에
   `/usr/bin/python3` 를 추가한다. 그 뒤:
   ```bash
   launchctl kickstart -k gui/$(id -u)/com.woojin.cryptonews.measure
   cat ~/Desktop/HanwhaDAPnews/reports/_launchd.err   # 비어 있으면 성공
   ```
2. 그냥 손으로 돌린다 — 위 1번 항목의 명령 한 줄.

권한을 안 줄 거면 등록을 지워도 된다.
```bash
launchctl unload ~/Library/LaunchAgents/com.woojin.cryptonews.measure.plist
rm ~/Library/LaunchAgents/com.woojin.cryptonews.measure.plist
```

---

## 4. 영문판 중복 발행 8건 — 지울지 미정

2026-09-04 새벽, 영문 봇의 루프(#26)가 02:21 에 멎었는데 GitHub 은 그걸
`in_progress` 로 계속 표시했다. 그 사이 텔레그램에는 87건이 나갔지만 DB 커밋이
안 올라와, 다음 실행이 그걸 "안 본 것"으로 알고 다시 발행했다.

**헤드라인이 글자까지 같은 중복 8건**이 남아 있다.

```
Tornado Cash developer Roman Storm's trial postponed
US SEC crypto custody rule revision enters White House review
Security vulnerability discovered in Bitcoin Core
IMF calls for international cooperation on stablecoin
Japan FSA requests budget increase to establish new …
… 외 3건
```

찾는 법:

```bash
cd ~/Desktop/HanwhaDAPnews/crypto-news-bot-en
git fetch -q origin && git show origin/main:botstate.sqlite3 > /tmp/en.db
venv/bin/python -c "
import sqlite3, collections
c=sqlite3.connect('/tmp/en.db')
cnt=collections.Counter(h for (h,) in c.execute('select headline from published'))
for h,k in cnt.items():
    if k>1:
        ids=[r[0] for r in c.execute('select message_id from published where headline=?',(h,))]
        print(k, ids, h[:60])"
```

지울 거면 나중에 나간 message_id 를 텔레그램에서 삭제하고 `published` 에서도 지운다.
`seen` 은 남겨야 다시 수집되지 않는다.

---

## 5. 루프 워치독 — 미조치

**증상:** 루프가 멎어도 GitHub 은 `in_progress` 로 둔다. 그동안 다음 실행이
대기열에서 자리를 못 잡아 봇이 통째로 선다. 실제로 두 번 겪었다.

```
09-03  #23  324분째 in_progress (LOOP_SECONDS 는 330분)
09-04  #26  02:21 에 멎었는데 계속 in_progress → 05:49 에 손으로 취소
```

**해법 후보:** 폴링 루프에 진전 감시를 넣는다. 일정 시간(예: 40분) 동안 폴링이
한 번도 성공하지 않으면 job 을 스스로 끝낸다. 그러면 대기 중인 실행이 즉시 이어받는다.
`LOOP_SECONDS` 와 별개로 도는 안전장치다.

**주의:** 양쪽 리포에 각각 넣어야 한다.

---

## 6. 커밋 실패가 이어질 때 폴링을 멈추기 — 미조치

**증상:** 발행은 되는데 DB 커밋이 밀리면, 그 사이 발행분이 다음 실행에서 통째로
다시 나간다. 4번 항목의 중복 8건이 그렇게 생겼다.

`mark_seen` 은 발행 **전에** 하지만 그건 로컬 DB 얘기다. **커밋·푸시가 되어야**
다음 실행이 안다. 지금은 푸시가 실패해도 로그만 남기고 폴링을 계속한다.

**해법 후보:** `save_state` 가 연속 N 회(예: 3회) 실패하면 폴링을 멈추고 job 을
끝낸다. 발행을 잠시 쉬는 손해가, 중복이 쌓이는 손해보다 작다.

**참고:** 이번 실패의 방아쇠는 내가 30분에 네 번 푸시한 것이었다. 푸시할 때마다
새 실행이 대기열에 들어가 원격이 앞서고, `save_state` 의 rebase 가 계속 밀렸다.
`START_HERE.md` 4-5 의 "자주 푸시하지 마라" 가 정확히 이 얘기다.
