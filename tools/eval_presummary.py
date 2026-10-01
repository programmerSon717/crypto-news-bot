"""요약 '전' 중복 차단이 호출을 얼마나 아끼고 뉴스를 얼마나 잃는지 잰다.

    python tools/eval_presummary.py --db botstate.sqlite3 --days 14

## 왜 따로 재야 하는가

`eventdup` 의 판정(RULES 10-6)은 **요약이 끝난 뒤** 한국어 헤드라인끼리 비교한다.
그래서 호출은 이미 쓴 뒤다. 같은 사건 기사 6건이 들어오면 6번 요약하고 5번 버린다.

요약 전에 자르려면 **원문 제목**으로 판정해야 하는데, 이건 영어·중국어·일본어다.
한국어 헤드라인과 겹치는 낱말이 구조적으로 적다. 대신 고유명사는 언어를 건너
살아남는다 — `glossary.canonicalize` 가 영문 이름을 한글 표준으로 접어 주기 때문이다.

    Chainlink launches CCIP 2.0 to give apps more control   (CoinDesk, 영어)
    Chainlink 发布 CCIP 2.0，允许企业添加自定义安全检查       (PANews, 중국어)
    チェーンリンク「CCIP 2.0」公開                           (CoinPost, 일본어)
    → 전부 {체인링크, CCIP} 를 공유한다

## 위험이 다르다

요약 후 판정은 틀려도 '중복으로 안 내보냄'이다. 요약 전 판정은 **기사를 아예
안 읽고 버린다.** 그래서 이 스크립트가 재는 핵심 수치는 절감률이 아니라

    유실: 실제로 발행됐던 기사를 '중복'으로 잘못 잘라낸 비율

이다. 이 값이 0 에 가깝지 않으면 쓰면 안 된다.

## 방법

`seen` 에는 원문 제목이, `published` 에는 발행된 한국어 헤드라인이 있고 둘은
같은 키를 쓴다. 시간순으로 재생하면서, 각 수집 항목이 **그 시점까지 이미 발행된**
글과 같은 사건인지 본다. 같다고 판정되면 '요약 안 함'으로 세고, 그 항목이 실제로
발행됐던 것이면 '유실'로 센다.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3  # noqa: E402

import eventdup  # noqa: E402

# 원문 제목 ↔ 한국어 헤드라인은 겹치는 낱말이 적다. 같은 언어끼리 비교할 때
# 쓰는 MIN_SHARED(3)를 그대로 요구하면 아무것도 안 걸린다.
MIN_SHARED_CROSS = 2
# 공유 낱말 중 적어도 하나는 이만큼 드물어야 한다. 흔한 말 둘로 자르면 안 된다.
# (weight 는 log((N+1)/(df+1))+1 이라, 6.0 은 발행 이력의 약 0.7% 이하에 나온 말)
MIN_RARE_WEIGHT = 6.0
# 이미 발행된 글을 거슬러 볼 시간(시간).
WINDOW_HOURS = 36


def crosslang_same(title: str, published_title: str) -> tuple[bool, float]:
    """원문 제목과 발행된 헤드라인이 같은 사건인가."""
    ta = eventdup.tokens(title)
    tb = eventdup.tokens(published_title)
    shared = (ta & tb) - eventdup.TEMPLATE_WORDS
    if len(shared) < MIN_SHARED_CROSS:
        return False, 0.0
    rare = max(eventdup.weight(t) for t in shared)
    if rare < MIN_RARE_WEIGHT:
        return False, rare
    return True, rare


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="botstate.sqlite3")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--show", type=int, default=12)
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    eventdup.set_corpus(
        h for (h,) in con.execute(
            "SELECT headline FROM published WHERE headline IS NOT NULL"))

    # 수집된 것 전부(원문 제목) + 그 중 발행된 것의 한국어 헤드라인·탭
    rows = con.execute(
        """SELECT s.published_at, s.title, p.headline, p.category
           FROM seen s LEFT JOIN published p ON p.key = s.key
           WHERE s.published_at > strftime('%s','now',?)
           ORDER BY s.published_at ASC""",
        (f"-{args.days} days",),
    ).fetchall()
    if not rows:
        print("재생할 수집 이력이 없다."); return

    window: list[tuple[float, str]] = []     # (발행시각, 한국어 헤드라인)
    saved = 0          # 요약을 건너뛰게 되는 수집 항목
    lost: list[tuple[str, str, float]] = []  # 진짜 유실(중복이 아닌데 잘릴 것)
    dup_ok = 0         # 잘려도 되는 것(요약 후 판정도 중복이라 했을 것)
    total = 0

    for ts, title, headline, category in rows:
        total += 1
        cutoff = ts - WINDOW_HOURS * 3600
        window = [(t, h) for t, h in window if t >= cutoff][-400:]

        hit = None
        for _t, prev in window:
            ok, _w = crosslang_same(title, prev)
            if ok:
                hit = prev
                break

        if hit:
            saved += 1
            if headline:
                # 발행됐던 기사라고 전부 유실은 아니다. 요약 후 판정(eventdup)이
                # **어차피 중복이라고 잘랐을 것**이면, 요약 전에 자르는 게 이득이다.
                # 그 경우는 호출만 아끼는 것이지 뉴스를 잃는 게 아니다.
                already_dup = eventdup.find_covered(headline, [h for _t, h in window])
                if already_dup:
                    dup_ok += 1
                else:
                    lost.append((title, hit, ts))
        if headline and not eventdup.skip(category or ""):
            window.append((ts, headline))

    print(f"재생 {args.days}일 · 수집 {total:,}건 (발행 {sum(1 for r in rows if r[2]):,}건)")
    print(f"  요약 전에 잘릴 항목   {saved:,}건  ({saved / total * 100:.1f}%)  ← 절감")
    print(f"    · 애초에 중복이라 잘렸을 것 {dup_ok:,}건  (순이득)")
    print(f"    · **진짜 유실** {len(lost):,}건  "
          f"({len(lost) / saved * 100 if saved else 0:.1f}% of 잘릴 것)")
    print(f"  설정: 공유 {MIN_SHARED_CROSS}개 이상 · 희귀도 {MIN_RARE_WEIGHT} 이상 "
          f"· 창 {WINDOW_HOURS}시간")

    if lost and args.show:
        print(f"\n── 진짜 유실 예시 {min(args.show, len(lost))}건 ──")
        step = max(1, len(lost) // args.show)
        for title, prev, ts in lost[::step][:args.show]:
            when = time.strftime("%m-%d %H:%M", time.localtime(ts))
            print(f"  [{when}] 잘릴 원문: {title[:66]}")
            print(f"            이미 발행: {prev[:66]}")


if __name__ == "__main__":
    main()
