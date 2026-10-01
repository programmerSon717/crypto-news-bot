"""발행 이력을 새 중복 판정으로 다시 돌려 '얼마나 막혔을지' 센다.

    python tools/replay_dedup.py --db botstate.sqlite3
    python tools/replay_dedup.py --days 7 --show 30

`eval_dedup.py` 가 '문턱이 맞는가'를 보는 것이라면, 이쪽은 **효과가 얼마인가**를
본다. 실제로 나간 글을 시간순으로 다시 흘려보내면서, `main.process_items` 가
하는 것과 같은 방식으로 판정한다 — 이미 통과한 글만 비교 대상에 쌓고,
탭 제외(`SKIP_CATEGORIES`)도 그대로 적용한다.

**주의: 이것은 상한이 아니라 하한이다.** 재생은 '이미 발행된 글'만 보므로,
실제 운영에서는 여기에 더해 모델 쪽 판정(이력 창 10 → 120 확대)도 함께 걸린다.

희귀도(IDF)는 전체 이력으로 미리 학습한다. 재생 구간만으로 세면 그 구간에서
자주 나온 사건의 이름이 흔한 말로 취급돼 판정이 둔해진다.
"""

import argparse
import os
import sqlite3
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import eventdup  # noqa: E402

# main.process_items 와 같은 값을 쓴다. 다르면 측정이 의미 없다.
WINDOW_HOURS = int(os.getenv("DEDUP_CODE_HOURS", "36"))
WINDOW_LIMIT = int(os.getenv("DEDUP_CODE_LIMIT", "500"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="botstate.sqlite3")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--show", type=int, default=20, help="막힌 글 예시 개수")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    eventdup.set_corpus(
        h for (h,) in con.execute(
            "SELECT headline FROM published WHERE headline IS NOT NULL")
    )
    rows = con.execute(
        """SELECT published_at, category, headline FROM published
           WHERE headline IS NOT NULL
             AND published_at > strftime('%s','now',?)
           ORDER BY published_at ASC""",
        (f"-{args.days} days",),
    ).fetchall()
    if not rows:
        print("재생할 발행 이력이 없다.")
        return

    # (발행시각, 헤드라인) 롤링 창. 실제 코드가 보는 것과 같게 유지한다.
    window: list[tuple[float, str]] = []
    blocked: list[tuple[str, str, str, float]] = []
    kept_by_tab: Counter = Counter()
    blocked_by_tab: Counter = Counter()

    for ts, cat, head in rows:
        cat = cat or ""
        if eventdup.skip(cat):
            kept_by_tab[cat] += 1
            continue
        # 창에서 오래된 것을 버린다.
        cutoff = ts - WINDOW_HOURS * 3600
        window = [(t, h) for t, h in window if t >= cutoff][-WINDOW_LIMIT:]

        hit = eventdup.find_covered(head, [h for _t, h in window])
        if hit:
            blocked.append((cat, head, hit[0], hit[1]))
            blocked_by_tab[cat] += 1
            # 막힌 글은 발행되지 않으므로 창에 쌓지 않는다.
            continue
        kept_by_tab[cat] += 1
        window.append((ts, head))

    total = len(rows)
    nb = len(blocked)
    judged = sum(v for k, v in kept_by_tab.items() if not eventdup.skip(k)) + nb
    print(f"재생 구간 {args.days}일 · 발행 {total:,}건")
    print(f"  코드 판정 대상(서술형 탭) {judged:,}건")
    print(f"  같은 사건으로 막힘        {nb:,}건  "
          f"({nb / judged * 100:.1f}% of 판정 대상, {nb / total * 100:.1f}% of 전체)")
    print(f"  창: 최근 {WINDOW_HOURS}시간 / 최대 {WINDOW_LIMIT}건  문턱 {eventdup.SIM_FLOOR}")

    print("\n── 탭별 ──")
    for cat in sorted(set(kept_by_tab) | set(blocked_by_tab)):
        b, k = blocked_by_tab[cat], kept_by_tab[cat]
        tag = "  (판정 제외)" if eventdup.skip(cat) else ""
        rate = f"{b / (b + k) * 100:5.1f}%" if (b + k) else "    - "
        print(f"  {cat:<16} 발행 {k:>5}  막힘 {b:>4}  {rate}{tag}")

    if args.show:
        print(f"\n── 막힌 글 예시 (점수 높은 순 {args.show}건) ──")
        for cat, head, prev, sim in sorted(blocked, key=lambda r: -r[3])[:args.show]:
            print(f"  {sim:.2f} [{cat}]")
            print(f"       막힘 {head}")
            print(f"       기존 {prev}")


if __name__ == "__main__":
    main()
