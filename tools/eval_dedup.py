"""중복 판정(`eventdup`)의 재현율·오탐을 실제 발행 이력으로 측정한다.

    python tools/eval_dedup.py                  # 요약만
    python tools/eval_dedup.py --db other.sqlite3
    python tools/eval_dedup.py --fp 40          # 오탐 후보를 40건까지 보여준다

## 측정 방법

**재현(잡아야 하는 것)** — 아래 `CLUSTERS` 는 실제로 같은 사건이 여러 번 발행된
묶음이다(2026-09 발행 이력에서 손으로 확인). 각 묶음의 첫 글을 '이미 발행된 것'
으로 두고, 나머지가 중복으로 걸리는지 본다.

**오탐(막으면 안 되는 것)** — 두 가지로 본다.

 1. `NEGATIVES` — 발행 이력에서 **손으로 확인한 '다른 사건' 쌍**이다. 여기서
    하나라도 걸리면 실패로 본다. 처음 문턱을 잡을 때 이 목록이 전부 지표 탭
    (나라만 다른 PMI·GDP 발표)에서 나왔고, 그래서 `eventdup.SKIP_CATEGORIES`
    가 생겼다.
 2. 같은 날 발행된 글끼리 전수 비교 — 탭별로 몇 쌍이 걸리는지 센다.
    서술형 탭에서 걸리는 쌍은 표본 확인상 거의 다 **진짜 중복**이었다
    (이슈 8/8, US Policy 8/8). 숫자가 갑자기 뛰면 문턱을 의심한다.

문턱을 만지면 **반드시 이 스크립트를 다시 돌려** 재현이 떨어지지 않는지,
오탐 목록에 엉뚱한 쌍이 늘지 않는지 확인한다.
"""

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eventdup  # noqa: E402

# 손으로 확인한 중복 묶음. 첫 줄이 먼저 나간 글, 나머지는 걸려야 하는 글.
CLUSTERS: list[tuple[str, list[str]]] = [
    ("체인링크 CCIP 2.0 출시 (09-28)", [
        "체인링크, CCIP 2.0 출시로 기업용 맞춤형 보안 검사 기능 도입",
        "체인링크, 브리지 기술 보안 강화 업데이트",
        "체인링크, 보안 통제 강화한 CCIP 2.0 출시",
        "체인링크, 기업 맞춤형 보안 검증 지원하는 CCIP 2 출시",
        "체인링크, 금융기관용 브릿지 보안 검증 기능 CCIP 2.0 도입",
        "체인링크, 커스텀 검증기 도입한 CCIP 2.0 출시",
    ]),
    ("이더리움 글램스터담 세폴리아 적용일 확정 (09-18~30)", [
        "이더리움 그램스타덤 업그레이드 테스트넷 적용일 10월 6일 확정",
        "이더리움 글램스테르담 업그레이드 10월 6일 세폴리아 테스트넷 적용",
        "이더리움 차기 업그레이드 '글램스터담' 10월 6일 세폴리아 시험망 적용 확정",
        "이더리움 글램스터댐 업그레이드, 10월 6일 세폴리아 테스트넷 포크 예정",
        "이더리움 글래스담스테르담 업그레이드, 10월 6일 Sepolia 테스트망 적용 예정",
        "이더리움, 세폴리아 테스트넷서 10월 6일 글래머스테담 업그레이드 예정",
        "이더리움 차기 업그레이드 '그램스테르담' 10월 6일 세폴리아 테스트넷 적용",
    ]),
    ("카카오페이증권·온도·디나리 주식 토큰화 (09-29~30)", [
        "카카오페이, Ondo Finance 및 Dinari와 손잡고 한국 주식 토큰화 모색",
        "카카오페이증권, 디나리·온도 파이낸스와 한국 주식 토큰화 협력",
        "카카오페이증권, 온도·디나리와 제휴해 한국 상장주식 토큰화 검토",
        "카카오페이증권, 온도·디나리와 손잡고 주식 토큰 해외 유통 추진",
    ]),
    ("카카오·파이어블록스 스테이블코인 인프라 (09-22)", [
        "카카오, 파이어블록스와 스테이블코인 유통망 구축 나선다",
        "카카오페이·카카오뱅크, 파이어블록스와 스테이블코인 인프라 협력 모색",
        "카카오페이·카카오뱅크, 파이어블록스와 손잡고 스테이블코인 유통망 구축",
        "카카오페이·카카오뱅크, 파이어블록스와 기관급 디지털 자산 인프라 구축 추진",
    ]),
    ("카카오·그랩 원화 스테이블코인 (09-23~24)", [
        "카카오, 그랩과 원화 스테이블코인 동맹 추진",
        "카카오 그라운드X, 그랩과 원화 스테이블코인 활용 동남아 사업 협력",
    ]),
    ("온도·블랙록 토큰화 포트폴리오 (09-24~26)", [
        "온도 파이낸스, 블랙록과 협력해 토큰화 스마트 포트폴리오 출시",
        "온도파이낸스, 블랙록 모델 전략 기반 온체인 포트폴리오 토큰 3종 출시",
        "온도파이낸스, 블랙록 전략 담은 온체인 토큰 3종 출시",
    ]),
    ("비탈릭 '헤고타가 마지막 정규 포크' (09-27~28)", [
        "비탈릭 부테린, 2027년 마지막 '일반' 이더리움 하드포크 예정",
        "비탈릭 부테린 \"Hegota 업그레이드는 마지막 '정규' 업그레이드가 될 것\"",
        "비탈릭 부테린 \"르고타가 이더리움의 마지막 일반적인 하드포크 될 수도\"",
    ]),
    ("와이오밍 스테이블토큰 체인링크 도입 (09-02~03)", [
        "와이오밍주, 체인링크 협력 확대… 스테이블 토큰 FRNT 온체인 검증 도입",
        "미국 와이오밍주, 스테이블코인 FRNT 준비금 검증에 체인링크 도입",
        "와이오밍주, 프론티어 스테이블 토큰 준비금 증명에 체인링크 도입",
    ]),
    ("온도 자회사 DTCC Fund/SERV 합류 (09-16)", [
        "온도 파이낸스, DTCC 펀드 처리 네트워크 합류",
        "온도 파이낸스, DTCC Fund/SERV 플랫폼에 토큰화 펀드 연결",
    ]),
]


# 손으로 확인한 '다른 사건' 쌍. (탭, 글A, 글B).
#
# 대부분은 지표·거래소 탭이라 `eventdup.SKIP_CATEGORIES` 가 막아 준다. 마지막
# 둘은 **서술형 탭**이라 문턱만이 방어선이다 — 여기서 하나라도 걸리면 실패다.
NEGATIVES: list[tuple[str, str, str]] = [
    ("Global Macro", "몬테네그로 2026년 8월 연간 인플레이션율 4.5% 기록",
     "러시아 8월 연간 인플레이션율 6.3% 기록… 시장 예상치 부합"),
    ("Global Macro", "한국 8월 제조업 PMI 52.3, 예상치 하회",
     "말레이시아 8월 제조업 PMI 50.2 기록"),
    ("Global Macro", "프랑스 9월 S&P 글로벌 종합 PMI 속보치 51.2… 예상치 상회",
     "미국 9월 S&P 글로벌 종합 PMI 58.4… 5년래 최고치 기록"),
    ("Global Macro", "아일랜드 8월 제조업 PMI 55.4로 3개월 만에 최고치",
     "태국 8월 제조업 PMI 53.8 기록, 16개월 연속 확장세"),
    ("Global Macro", "앙골라 2분기 GDP 전년 대비 8.74% 성장... 4년 만에 최고치 기록",
     "세르비아 2026년 2분기 GDP 성장률 3.8% 기록… 2년 내 최고치"),
    ("US Rates", "미국 2년물 국채 수익률 2025년 1월 이후 최고치 기록",
     "일본 10년물 국채 금리 30년 만에 최고치 경신"),
    ("이슈", "미국 XRP 현물 ETF 일간 2619만 달러 순유입",
     "미국 비트코인 현물 ETF, 9일간의 순유입세 마감하고 순유출 기록"),
    # 아래 둘은 tools/replay_dedup.py 로 발행 이력을 재생하다 찾은 것이다.
    # 둘 다 공유 낱말이 2개뿐이라 MIN_SHARED 가 막는다.
    ("이슈", "Pyth Network, 나스닥 실시간 시세 데이터 외부분파사 선정",
     "X, 실시간 암호화폐 시세 확인 및 거래 연동 기능 도입"),
    ("US Policy", "비트와이즈, 클래리티법 무산에도 강세장 지속 전망",
     "미 상원, 가상자산 법안 클래리티법 통과 무산"),
    ("이슈", "마이크로스트래티지 주가 장중 5.03% 상승",
     "비트디어(Bitdeer) 주가 장중 10%대 상승 기록"),
    ("US Rates", "바클레이즈, 연준 9월·12월 각 25bp 금리 인상 전망",
     "연준 9월 25bp 인상 확률 57% 속 비트코인 7만8000달러대 기록"),
    ("거래소이슈", "게이트아이오, 밈 코인 ALLINU 무기한 선물 상장", "OKX, AKE 무기한 선물 상장"),
    ("거래소이슈", "빗썸, 솔라나 기반 USELESS 원화 마켓 상장",
     "업비트, BTC·USDT 마켓에 HEMI 및 USELESS 토큰 상장"),
    ("거래소이슈", "빗썸, 모나드(MON) 입출금 일시 중지",
     "빗썸, 온톨로지 네트워크 계열 가상자산 3종 입출금 일시 중지 안내"),
    ("거래소이슈", "후오비 HTX, CT(Concrete) 현물 상장 및 거래 지원 공지",
     "Gate, Concrete(CT) 현물 상장 및 100만 개 런치풀 공지"),
]


def negatives() -> int:
    """막혀야 할 쌍이 통과했는지 본다. 돌려주는 값은 **진짜 오탐**의 수다.

    탭 제외(`SKIP_CATEGORIES`)로 막히는 것과 문턱으로 막히는 것을 갈라 센다.
    앞의 것은 문턱이 느슨해도 안전하지만, 뒤의 것은 문턱이 유일한 방어선이다.
    """
    bad = 0
    by_tab = 0
    print("\n── 오탐 (막으면 안 되는 쌍) " + "─" * 34)
    for cat, a, b in NEGATIVES:
        hit, sim = eventdup.same_event(a, b)
        if not hit:
            continue
        if eventdup.skip(cat):
            by_tab += 1
            continue
        bad += 1
        print(f"    [오탐 {sim:.2f}] ({cat}) {a}\n                  ↔ {b}")
    mark = "✅" if not bad else "❌"
    print(f"  {mark} 진짜 오탐 {bad}건"
          f"  (탭 제외로 막힌 것 {by_tab}건 — 문턱과 무관하게 안전)")
    return bad


def recall() -> tuple[int, int]:
    hit = miss = 0
    print("── 재현 (같은 사건을 잡아내는가) " + "─" * 30)
    for name, heads in CLUSTERS:
        first, rest = heads[0], heads[1:]
        seen = [first]
        ok = 0
        for h in rest:
            found = eventdup.find_covered(h, seen)
            if found:
                ok += 1
            else:
                print(f"    [놓침] {h}")
                print(f"           ↔ {first}")
            # 실제 흐름과 같게, 통과한 글은 발행된 것으로 쌓는다.
            if not found:
                seen.append(h)
        hit += ok
        miss += len(rest) - ok
        mark = "✅" if ok == len(rest) else "⚠️ "
        print(f"  {mark} {name}: {ok}/{len(rest)}")
    return hit, miss


def false_positives(db: str, limit: int) -> None:
    con = sqlite3.connect(db)
    rows = con.execute(
        """SELECT date(published_at,'unixepoch'), category, headline
           FROM published WHERE headline IS NOT NULL
             AND published_at > strftime('%s','now','-45 days')"""
    ).fetchall()
    # 실제 동작과 같게, 코드 판정을 걸지 않는 탭은 제외한다.
    byday: dict[str, list[str]] = defaultdict(list)
    skipped = 0
    for day, cat, head in rows:
        if eventdup.skip(cat):
            skipped += 1
            continue
        byday[day].append(head)
    print(f"\n  (코드 판정 제외 탭 {skipped:,}건은 비교에서 뺐다: "
          f"{', '.join(sorted(eventdup.SKIP_CATEGORIES))})")

    pairs = 0
    flagged = []
    for day, heads in byday.items():
        heads = list(dict.fromkeys(heads))          # 완전 동일 제목은 제외
        for a, b in combinations(heads, 2):
            pairs += 1
            hit, sim = eventdup.same_event(a, b)
            if hit:
                flagged.append((sim, day, a, b))

    print(f"\n── 같은 날 발행된 글 전수 비교 {pairs:,}쌍 " + "─" * 22)
    print(f"  같은 사건으로 걸린 쌍: {len(flagged):,}"
          f"  ({len(flagged) / pairs * 100:.3f}%)")
    print("  ※ 이 중 상당수는 '지금 봇이 놓치고 있던 실제 중복'이다."
          "\n    사람이 눈으로 갈라야 한다 — 엉뚱한 쌍이 보이면 문턱을 올린다.\n")
    flagged.sort(key=lambda t: -t[0])
    if limit <= 0:
        return
    step = max(1, len(flagged) // limit) if len(flagged) > limit else 1
    for sim, day, a, b in flagged[::step][:limit]:
        print(f"  {sim:.2f} [{day}]")
        print(f"       A {a}")
        print(f"       B {b}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="botstate.sqlite3")
    ap.add_argument("--fp", type=int, default=25, help="오탐 후보 출력 개수")
    args = ap.parse_args()

    # 희귀도(IDF)를 발행 이력에서 학습한다. 실제 봇과 같은 조건으로 재야 한다 —
    # 코퍼스가 없으면 모든 낱말이 같은 무게가 돼 점수 자체가 달라진다.
    corpus = 0
    if os.path.exists(args.db):
        corpus = eventdup.set_corpus(
            h for (h,) in sqlite3.connect(args.db).execute(
                "SELECT headline FROM published WHERE headline IS NOT NULL")
        )
    else:
        print(f"[경고] DB 없음({args.db}) — 희귀도 가중 없이 측정한다. "
              "점수가 실제와 다르다.")

    print(f"문턱 {eventdup.SIM_FLOOR}  최소 낱말 {eventdup.MIN_TOKENS}  "
          f"최소 공유 {eventdup.MIN_SHARED}  코퍼스 {corpus:,}건\n")
    hit, miss = recall()
    total = hit + miss
    print(f"\n  재현 {hit}/{total} ({hit / total * 100:.0f}%)")
    bad = negatives()
    if bad:
        print("\n  ⚠️  오탐이 있다 — 문턱을 올리거나 해당 탭을"
              " SKIP_CATEGORIES 에 넣어야 한다.")

    if os.path.exists(args.db):
        false_positives(args.db, args.fp)
    else:
        print(f"\n[건너뜀] DB 없음: {args.db}")


if __name__ == "__main__":
    main()
