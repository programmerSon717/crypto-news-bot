"""발행 이력에서 자료를 찾는다. '자료검색' 탭의 뒷단.

## 왜 벡터DB를 쓰지 않는가

코퍼스가 작다. 발행글 8,632건, 헤드라인+리드 합쳐 2MB 안쪽이다. 이 규모에서는
BM25 가 임베딩보다 **빠르고, 공짜고, 디버깅이 된다.** 임베딩을 쓰면 색인 때마다
API 를 불러야 하고(무료 한도는 이미 발행이 다 쓰고 있다), 왜 이 글이 1위인지
설명할 수 없다.

의미 검색이 필요한 자리는 **질문 → 검색어 변환** 한 곳뿐이다. 그건 BM25 가
못 하는 게 맞다 — "업뎃됐다는데"로는 "업그레이드·출시·도입"이 안 걸린다.
거기만 `SYNONYMS` 로 메우고, 그래도 애매한 것은 모델이 후보 중에서 고른다
(`answer.py`). 즉 **검색은 코드가, 판단은 모델이** 한다.

## 같은 사건은 한 번만 보여준다

지금 발행 이력에는 같은 사건이 여러 번 들어 있다(2026-10-01 이전 발행분).
그대로 내놓으면 "체인링크 업뎃" 질문에 거의 똑같은 CCIP 2.0 기사 6건이 나온다.
그래서 결과를 `eventdup` 으로 묶어 **사건당 한 건**만 대표로 보여주고,
나머지는 개수로만 알린다. 중복 제거(RULES 10-6)와 같은 판정기를 재사용한다.

## 점수

BM25 에 최신성을 곱한다. 뉴스 검색에서 최신성은 선택이 아니다 —
"요번에 업뎃된 거"는 말 그대로 가장 최근 것을 묻는다. 질문에 '요번·최근·방금'
같은 말이 있으면 최신성 가중을 더 올린다(`RECENCY_WORDS`).
"""

import math
import os
import re
import time

import eventdup
import glossary

# BM25 상수. 기본값(k1=1.5, b=0.75)을 그대로 쓴다 — 짧은 문서라 조정 이득이 적다.
K1 = 1.5
B = 0.75

# 최신성 가중. 점수 × exp(-나이/HALFLIFE·ln2) 꼴로 반감기를 준다.
# 14일이면 2주 전 기사가 절반 무게가 된다. 뉴스 질문 대부분이 최근 일을 묻는다.
HALFLIFE_DAYS = float(os.getenv("SEARCH_HALFLIFE_DAYS", "14"))
# 질문에 '요번·최근' 같은 말이 있으면 반감기를 이만큼 짧게 본다(= 최신 쪽으로 더 쏠린다).
RECENT_HALFLIFE_DAYS = float(os.getenv("SEARCH_RECENT_HALFLIFE_DAYS", "4"))

RECENCY_WORDS = ("요번", "이번", "최근", "방금", "지금", "오늘", "어제", "며칠",
                 "새로", "신규", "최신", "근데 뭐", "나왔")

# 구어·약어를 발행글에 실제로 쓰인 말로 넓힌다.
#
# BM25 는 글자가 같아야 걸린다. 사람이 묻는 말과 기사에 쓰인 말이 다르면
# 아무리 좋은 랭커도 못 찾는다. 여기 있는 것은 **발행 이력에서 확인한 표현**이다.
# 늘릴 때도 같은 원칙 — 기사에 실제로 그 낱말이 있는지 보고 넣는다.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "업뎃": ("업그레이드", "업데이트", "출시", "도입", "버전"),
    "업데이트": ("업그레이드", "출시", "도입", "버전"),
    "업그레이드": ("업데이트", "출시", "하드포크", "포크"),
    "규제": ("법안", "당국", "정책", "감독"),
    "상장": ("거래지원", "마켓"),
    "해킹": ("탈취", "익스플로잇", "유출", "피해"),
    "제휴": ("협력", "파트너십", "협약", "맞손"),
    "투자": ("유치", "펀딩", "라운드"),
    "출금": ("입출금", "인출"),
    "소송": ("제소", "기소", "고발"),
    "승인": ("허가", "인가"),
    "금리": ("기준금리", "인하", "인상"),
    "가격": ("시세", "급등", "급락"),
    "스테이블": ("스테이블코인",),
    "인수": ("지분", "합병"),
    "출시": ("공개", "가동", "개시", "론칭"),
}
# '코인'→'가상자산·암호화폐' 같은 확장은 넣지 않는다. 너무 넓어서 질문과 무관한
# 기사를 끌어온다(실측: "카카오 스테이블코인" 질문에 엔화 연동 코인 기사가 2위).

# 확장어는 원래 질문에 없던 말이라 무게를 깎는다. 1.0 으로 두면 확장어만 걸린
# 엉뚱한 기사가 핵심어까지 맞은 기사를 밀어낸다(실측: "체인링크 업뎃" 질문에
# '업데이트'만 맞은 지캐시 기사가 1위, CCIP 2.0 이 3위).
EXPAND_WEIGHT = 0.4

# 용언 활용 어미. **질문에서만** 뗀다.
#
# 사람은 "진출한다는 얘기"라고 묻고 기사는 "미국 시장 진출"이라고 쓴다.
# `eventdup` 의 토크나이저는 체언 조사만 떼므로 '진출한다' 와 '진출' 이 갈린다.
# 여기서 어미를 떼어 **둘 다** 검색어에 넣는다(원형을 버리지 않는 이유는
# '업뎃됐다는데' 처럼 떼고 나서도 기사에 없는 말이 될 수 있어서다).
#
# 문서 쪽 토크나이저는 건드리지 않는다 — 중복 판정이 그 토크나이저에 맞춰
# 문턱을 재 놨고(RULES 10-6), 질문만 넓히면 같은 효과를 공짜로 얻는다.
_VERB_TAIL = re.compile(
    r"(한다는|한다고|한다|했다는|했다고|했다|하는|하기로|하려|했던|할|해서|해라"
    r"|된다는|된다고|된다|됐다는|됐다고|됐다|되는|되기로|당했|당한"
    r"|이라는|이라고|라는|라고|려고|려는|인가|인지|이냐|냐고)$"
)

# 질문에만 나오고 뜻이 없는 말. 검색어에서 뺀다.
QUESTION_WORDS = frozenset("""
뭐임 뭐야 뭔데 뭐지 뭔가 무엇 어떻게 어떤 어디 언제 누가 왜 얼마 알려줘 알려 궁금
궁금해 궁금한데 찾아줘 찾아 보여줘 보여 정리 정리해줘 설명 설명해줘 해줘 해줭
있나 있어 있음 됐다는데 했다는데 한다는데 라는데 다는데 그거 그게 이거 이게 저거
관련 내용 기사 자료 소식 얘기 이야기 말이야 말인데 좀 요번 이번 저번 그때 아까
""".split())


def expand(question: str) -> tuple[list[str], list[str]]:
    """질문 → (핵심어, 확장어).

    **핵심어는 질문에 실제로 있던 말**이고, 확장어는 동의어 표가 덧붙인 말이다.
    이걸 갈라야 하는 이유는 랭킹에서 둘을 다르게 다뤄야 하기 때문이다 —
    확장어만 맞은 기사는 질문과 무관할 수 있다(`EXPAND_WEIGHT` 주석 참고).
    """
    text = glossary.canonicalize(question or "")
    core: list[str] = []
    for tok in eventdup.tokens(text):
        if tok in QUESTION_WORDS:
            continue
        core.append(tok)
        stem = _VERB_TAIL.sub("", tok)
        if len(stem) >= 2 and stem != tok and stem not in QUESTION_WORDS:
            core.append(stem)

    extra: list[str] = []
    for tok in core:
        extra.extend(SYNONYMS.get(tok, ()))
    # 어간이 잘려 동의어 표에 안 걸리는 경우를 메운다('업뎃됐다는데' → '업뎃').
    # 이때 열쇠말 자체도 핵심어로 넣는다 — 질문에 들어 있던 말이기 때문이다.
    for key, syns in SYNONYMS.items():
        if key in text:
            if key not in core:
                core.append(key)
            extra.extend(syns)

    core = list(dict.fromkeys(core))
    extra = [t for t in dict.fromkeys(extra) if t not in core]
    return core, extra


def wants_recent(question: str) -> bool:
    return any(w in (question or "") for w in RECENCY_WORDS)


class Index:
    """발행글 BM25 색인. 메모리에 둔다 — 8,600건이면 20MB 안쪽이다."""

    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.df: dict[str, int] = {}
        self.postings: dict[str, list[int]] = {}
        self.avgdl = 1.0

    def build(self, rows) -> int:
        """rows: (key, category, headline, lede, message_id, thread_id,
        source_url, published_at) 튜플들."""
        self.docs.clear()
        self.df.clear()
        self.postings.clear()
        total_len = 0
        for key, cat, headline, lede, msg_id, thread_id, url, ts in rows:
            # 헤드라인과 리드만 색인한다. 본문(`text`)은 HTML·이모지·해시태그가
            # 섞여 있어 잡음이 크고, 리드가 이미 사건을 한 문장으로 담고 있다.
            toks = eventdup.tokens(f"{headline} {lede or ''}")
            if not toks:
                continue
            i = len(self.docs)
            self.docs.append({
                "key": key, "category": cat or "", "headline": headline,
                "lede": lede or "", "message_id": msg_id, "thread_id": thread_id,
                "url": url or "", "published_at": ts or 0.0, "len": len(toks),
            })
            total_len += len(toks)
            for t in toks:
                self.df[t] = self.df.get(t, 0) + 1
                self.postings.setdefault(t, []).append(i)
        self.avgdl = (total_len / len(self.docs)) if self.docs else 1.0
        return len(self.docs)

    def _idf(self, term: str) -> float:
        n = len(self.docs)
        df = self.df.get(term, 0)
        if not df:
            return 0.0
        # BM25 의 표준 IDF. 음수가 되지 않게 아래를 자른다.
        return max(0.0, math.log((n - df + 0.5) / (df + 0.5) + 1.0))

    def search(self, question: str, limit: int = 8,
               now: float | None = None) -> list[dict]:
        """사건 단위로 묶은 검색 결과. 점수 높은 순."""
        core, extra = expand(question)
        if not core or not self.docs:
            return []
        now = now or time.time()
        halflife = (RECENT_HALFLIFE_DAYS if wants_recent(question)
                    else HALFLIFE_DAYS) * 86400

        scores: dict[int, float] = {}
        hits: dict[int, list[str]] = {}
        core_hit: set[int] = set()
        for term, mult in [(t, 1.0) for t in core] + \
                          [(t, EXPAND_WEIGHT) for t in extra]:
            idf = self._idf(term)
            if idf <= 0:
                continue
            for i in self.postings.get(term, ()):
                d = self.docs[i]
                # 집합 기반이라 단어 빈도는 1 이다. 길이 정규화만 남는다.
                norm = K1 * (1 - B + B * d["len"] / self.avgdl)
                scores[i] = scores.get(i, 0.0) + mult * idf * (K1 + 1) / (1 + norm)
                hits.setdefault(i, []).append(term)
                if mult == 1.0:
                    core_hit.add(i)

        # **핵심어가 하나도 안 맞은 기사는 버린다.** 확장어만으로 걸린 것은
        # 질문의 주제와 무관하다. 이 한 줄이 "체인링크 업뎃" 질문에서
        # '업데이트'만 맞은 지캐시·Taiko 기사를 걷어낸다.
        scores = {i: s for i, s in scores.items() if i in core_hit}
        if not scores:
            return []

        ranked = []
        for i, base in scores.items():
            age = max(0.0, now - self.docs[i]["published_at"])
            recency = 0.5 ** (age / halflife) if halflife > 0 else 1.0
            # 최신성은 곱하되 바닥을 둔다 — 오래된 글이라도 질문에 딱 맞으면
            # 보여줘야 한다. 바닥이 없으면 1년 전 기사는 점수가 0 이 된다.
            ranked.append((base * (0.15 + 0.85 * recency), base, i))
        ranked.sort(reverse=True)

        # ── 같은 사건끼리 묶는다 ──
        out: list[dict] = []
        for score, base, i in ranked:
            d = self.docs[i]
            for prev in out:
                if eventdup.same_event(d["headline"], prev["headline"])[0]:
                    prev["dupes"] += 1
                    # 같은 사건이면 더 이른 보도를 '처음 나온 시각'으로 남긴다.
                    prev["first_at"] = min(prev["first_at"], d["published_at"])
                    break
            else:
                out.append({**d, "score": score, "bm25": base,
                            "matched": sorted(set(hits.get(i, []))),
                            "dupes": 0, "first_at": d["published_at"]})
                if len(out) >= limit:
                    break
        return out


_index: Index | None = None


def index(store, rebuild: bool = False) -> Index:
    """색인을 한 번만 만들어 재사용한다."""
    global _index
    if _index is not None and not rebuild:
        return _index
    eventdup.set_corpus(store.all_headlines())
    idx = Index()
    n = idx.build(store.searchable())
    _index = idx
    print(f"[검색] 발행글 {n:,}건 색인 완료")
    return idx


# ── 결과를 사람이 읽는 꼴로 ──────────────────────────────────

_TAG = re.compile(r"<[^>]+>")


def channel_link(chat_id: str, message_id: int, thread_id: int | None) -> str:
    """발행된 원본 글로 가는 링크.

    비공개 그룹은 `t.me/c/<내부 id>/<토픽>/<글>` 꼴이다. 설정의 채널 id 는
    `-100` 이 붙은 형태(-1001234567890)인데, 링크에는 그 접두사를 뺀 숫자를 쓴다.
    공개 채널(@이름)이면 사용자명을 그대로 쓴다.
    """
    chat_id = (chat_id or "").strip()
    if not message_id:
        return ""
    if chat_id.startswith("@"):
        return f"https://t.me/{chat_id[1:]}/{message_id}"
    digits = chat_id.lstrip("-")
    if digits.startswith("100"):
        digits = digits[3:]
    if not digits.isdigit():
        return ""
    if thread_id:
        return f"https://t.me/c/{digits}/{thread_id}/{message_id}"
    return f"https://t.me/c/{digits}/{message_id}"
