"""발행 이력에서 '같은 이름을 다르게 음차한 것' 후보를 뽑는다.

    python tools/find_variants.py                  # 상위 60쌍
    python tools/find_variants.py --top 150 --sim 0.4

`glossary.TERMS` 를 늘릴 때 쓴다. **출력은 후보일 뿐이고, 사람이 확인해서
직접 적어야 한다.** 자동으로 합치면 안 된다 — 실제로 이 스크립트는 아래처럼
서로 다른 대상을 유사쌍으로 올린다.

    스트라이프(Stripe) / 스트라이브(Strive) / 스트라이드(Stride)
    통화감독청(미국 OCC) / 통화청(싱가포르 MAS)
    익스플로잇(exploit) / 익스플로러(explorer)
    리스테이킹 / 언스테이킹 / 재스테이킹
    하드월렛 / 콜드월렛

이미 `glossary` 에 등록된 쌍은 숨긴다 — 남은 것만 봐야 일이 줄어든다.

## 방법

헤드라인에서 4글자 이상 한글 낱말을 모아 조사를 떼고, 글자 2-gram 자카드가
비슷한 쌍을 찾는다. 외래어성 음절(르·스·트·드·캄·댐 …)이 든 낱말만 본다 —
순한글 낱말끼리는 음차 변형일 수 없기 때문이다.

포함 관계인 쌍(`스테이블코인` / `스테이블코인법`)은 복합어일 뿐이라 뺀다.
"""

import argparse
import os
import re
import sqlite3
import sys
from collections import defaultdict
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import glossary  # noqa: E402

_HANGUL = re.compile(r"[가-힣]{4,}")
_TAIL = re.compile(
    r"(으로서|에서는|으로는|이라는|에게서|으로|에서|에게|과의|와의|이라|라는|까지|부터"
    r"|에도|이나|만을|들을|들이|들은|의|를|을|이|가|은|는|도|로|와|과|에|만|나|서)$"
)
# 외래어 음차에 자주 쓰이는 음절. 하나라도 들어야 후보로 본다.
_FOREIGNISH = re.compile(r"[랩럽롭립랜런론룬르러라로루리스트티드프브크큰컨캄캠댐덤담탐텀펙팩]")


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _sim(a: str, b: str) -> float:
    A, B = _bigrams(a), _bigrams(b)
    return len(A & B) / len(A | B) if A | B else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="botstate.sqlite3")
    ap.add_argument("--top", type=int, default=60)
    ap.add_argument("--sim", type=float, default=0.45)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        sys.exit(f"DB 없음: {args.db}")

    freq: dict[str, int] = defaultdict(int)
    for (head,) in sqlite3.connect(args.db).execute(
            "SELECT headline FROM published WHERE headline IS NOT NULL"):
        for word in _HANGUL.findall(head):
            stem = _TAIL.sub("", word)
            if len(stem) >= 4:
                freq[stem] += 1

    words = [w for w in freq if _FOREIGNISH.search(w)]
    print(f"헤드라인에서 모은 외래어성 낱말 {len(words):,}개"
          f" (사전 등록 {len(glossary.VARIANTS)}개는 숨김)\n")

    known = set(glossary.VARIANTS) | set(glossary.VARIANTS.values())
    pairs = []
    for a, b in combinations(words, 2):
        if abs(len(a) - len(b)) > 2:
            continue
        if a in b or b in a:            # 복합어지 변형이 아니다
            continue
        if a in known and b in known:   # 이미 처리한 쌍
            continue
        s = _sim(a, b)
        if s >= args.sim:
            pairs.append((s, a, b))

    pairs.sort(reverse=True)
    print(f"유사쌍 {len(pairs):,}개 — 상위 {min(args.top, len(pairs))}개\n")
    print("  (왼쪽이 더 자주 쓰인 표기다. 같은 대상이면 glossary.TERMS 에"
          " '표준=왼쪽, 변형=오른쪽' 으로 적는다)\n")
    for s, a, b in pairs[:args.top]:
        if freq[b] > freq[a]:
            a, b = b, a
        print(f"  {s:.2f}  {a}({freq[a]})   ←?   {b}({freq[b]})")


if __name__ == "__main__":
    main()
