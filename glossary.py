"""고유명사 한국어 표기 통일.

`lang.py` 는 **가나·한자가 안 옮겨진** 것을 고친다. 이 파일은 그 다음 문제를 본다 —
한국어로는 옮겨졌는데 **매번 다르게 음차되는** 것.

실측(발행 이력 8,632건): 이더리움 업그레이드 'Glamsterdam' 한 건이
9월 18~30일 사이 **여덟 가지 표기**로 나갔다.

    그램스타덤 / 글램스테르담 / 글램스터담 / 글램스터댐
    글래스담스테르담 / 글래머스테담 / 그램 스타덤 / 그램스테르담

같은 사건인데 이름이 달라 생기는 피해가 셋이다.
 1. 읽는 사람이 같은 사건인지 모른다.
 2. 중복 판정이 깨진다 — `eventdup` 은 이름으로 같은 사건을 알아본다.
 3. 나중에 자료를 검색할 때 '글램스터담'으로는 '그램스타덤' 기사가 안 나온다.

처리 순서는 `lang` 과 같다 — **프롬프트는 권고, 치환이 강제다.**
 1. 요약 호출에 표기 사전을 얹는다(`prompt_block`). 단, **원문에 걸리는 항목만**
    보낸다. 전부 보내면 호출마다 사전 전체를 실어 한도를 낭비한다.
 2. 그래도 모델이 새 표기를 만들어 오면 발행 전에 치환한다(`apply`).

## 두 가지 정규화를 구분해 둔 이유

`apply` (발행문에 적용)      한글 변형 → 한글 표준. 영문은 건드리지 않는다.
`canonicalize` (판정에만)    여기에 **영문 이름 → 한글 표준**까지 더한다.

발행문에서 영문을 함부로 바꾸면 안 된다 — 'CLARITY Act'·'Fund/SERV' 처럼
원어로 두는 게 맞는 것이 많고, 괄호 병기도 규칙이 허용하는 형식이다(RULES 9).
반면 중복 판정에서는 영문/한글이 갈리면 같은 사건을 놓친다. 실측:

    A 카카오페이, Ondo Finance 및 Dinari와 손잡고 한국 주식 토큰화 모색
    B 카카오페이증권, 온도·디나리와 제휴해 한국 상장주식 토큰화 검토
    → 같은 사건인데 겹치는 낱말이 없어 판정 점수 0.12 (문턱 0.40)

그래서 판정용 정규화만 영문을 한글로 모은다.

## 사전을 늘리는 방법

자동 수집은 하지 않는다. `tools/find_variants.py` 가 발행 이력에서 **후보**를
뽑아 주지만, 사람이 확인하고 여기에 적어야 한다. 기계가 판단하면 위험하다 —
실제로 후보 목록에는 아래처럼 **서로 다른 대상**이 유사쌍으로 올라온다.

    스트라이프(Stripe) / 스트라이브(Strive) / 스트라이드(Stride)
    통화감독청(미국 OCC) / 통화청(싱가포르 MAS)
    익스플로잇(exploit) / 익스플로러(explorer)

이것들을 합치면 뉴스가 틀린다. 그래서 수동이다.
"""

import re

import lang

# (표준 표기, [원어...], [한글 변형...], 비고)
#
# **표준은 발행 이력에서 가장 많이 쓰인 표기를 따른다** — 사전이 바뀌면 과거 글과
# 새 글의 표기가 갈리기 때문이다. 다수 표기가 분명히 틀린 음차일 때만 바로잡는다
# (예: 플래그먼트 → 프래그먼트, Fragment).
#
# 한글 변형이 없고 원어만 있는 항목도 쓸모가 있다 — 중복 판정에서 영문/한글을
# 한곳으로 모으고, 프롬프트에 "이 이름은 이렇게 적어라"로 들어간다.
TERMS: list[tuple[str, list[str], list[str], str]] = [
    # ── 이더리움 업그레이드 ──
    ("글램스터담", ["Glamsterdam"],
     ["글램스테르담", "그램스테르담", "그램스타덤", "그램 스타덤", "글램스터댐",
      "글래머스테담", "글래스담스테르담", "글램스테담", "글램스테르댐"],
     "이더리움 업그레이드. 2026-10-06 세폴리아 적용"),
    ("헤고타", ["Hegota", "Hegotá"], ["르고타", "헤고따"],
     "글램스터담 다음 이더리움 업그레이드"),
    ("세폴리아", ["Sepolia"], [], "이더리움 테스트넷"),
    # ── 프로토콜·기업 ──
    ("하이퍼리퀴드", ["Hyperliquid"],
     ["하퍼리퀴드", "하이브리퀴드", "하이브리디퀴드", "하이퍼리퀴트",
      "하이퍼리키드", "하이퍼리쿼드"], ""),
    ("시큐리타이즈", ["Securitize"], ["세큐리타이즈", "시큐리타이스"], "토큰화 플랫폼"),
    ("마이크로스트래티지", ["MicroStrategy"],
     ["마이크로스트레티지", "마이크로스트라테지"], ""),
    ("페이워드", ["Payward"], ["파이워드"], "크라켄 모회사"),
    ("비트트레이드", ["bitTrade"], ["비트레이드"], "일본 거래소"),
    ("프래그먼트", ["Fragment"], ["플래그먼트"], "스테이블코인 프로토콜"),
    ("유니크레디트", ["UniCredit"], ["유니크레딧"], ""),
    ("온도 파이낸스", ["Ondo Finance", "Ondo"], ["온도파이낸스"], "띄어쓰기 통일"),
    ("디나리", ["Dinari"], [], ""),
    ("파이어블록스", ["Fireblocks"], [], ""),
    ("체인링크", ["Chainlink"], [], ""),
    ("블랙록", ["BlackRock"], [], ""),
    ("카카오페이", ["KakaoPay", "Kakao Pay"], [], ""),
    ("카카오뱅크", ["KakaoBank", "Kakao Bank"], [], ""),
    ("잭엑스비티", ["ZachXBT"], ["잭스엑스비티", "잭스엑스티", "재크엑스비티"],
     "온체인 분석가"),
    # ── 기관 ──
    ("유럽증권시장감독청", ["ESMA"], ["유럽증권시장청", "유럽증권시장감독국"], ""),
    # ── 일반 용어 ──
    ("밸리데이터", ["validator"], ["발리데이터", "벨리데이터"], ""),
    ("테스트넷", ["testnet"], ["테스트망"], ""),
    ("에어드랍", ["airdrop"], ["에어드롭"], ""),
    ("시큐리티즈", ["Securities"], ["시큐리티스", "서큐리티스"], "사명 일부일 때"),
    ("인더스트리스", ["Industries"], ["인더스트리즈"], "사명 일부일 때"),
    ("컨퍼런스", ["conference"], ["콘퍼런스"], ""),
]

# 한글 변형 → 표준. 발행문에 적용하는 것은 이것뿐이다.
VARIANTS: dict[str, str] = {
    v: canon for canon, _en, variants, _note in TERMS for v in variants
}

# 같은 글자로 적힌 변형이 두 표준에 걸리면 사전이 모순이다. 조용히 틀리는 것보다
# 임포트 때 터지는 쪽이 낫다.
_dups = [v for canon, _e, vs, _n in TERMS for v in vs if VARIANTS[v] != canon]
if _dups:                                            # pragma: no cover
    raise ValueError(f"glossary: 변형이 두 표준에 걸려 있다 — {_dups}")

# 판정용. 한글 변형 + 원어(영문)를 모두 표준 한글로 모은다.
_FOLD: dict[str, str] = dict(VARIANTS)
for _canon, _ens, _vs, _n in TERMS:
    for _en in _ens:
        _FOLD[_en.lower()] = _canon

# 긴 것부터 바꿔야 'Ondo Finance' 가 'Ondo' 로 먼저 잘리지 않는다.
_FOLD_ORDER = sorted(_FOLD, key=len, reverse=True)
_FOLD_RE = re.compile("|".join(re.escape(k) for k in _FOLD_ORDER), re.IGNORECASE)


def apply(data: dict) -> None:
    """요약 JSON 의 모든 텍스트 필드에서 한글 변형 표기를 표준으로 바꾼다.

    `lang.apply_glossary` 와 같은 치환기를 쓴다 — 검사 대상 필드 목록
    (`lang.FIELDS`·`lang.LIST_FIELDS`)을 한 곳에만 두기 위해서다.
    영문은 건드리지 않는다(위 '두 가지 정규화' 참고).
    """
    lang._replace_all(data, VARIANTS)


def canonicalize(text: str) -> str:
    """**중복 판정 전용** 정규화. 영문 이름까지 한글 표준으로 모은다.

    발행문에 쓰면 안 된다 — 원어로 둬야 하는 표기를 뭉개기 때문이다.
    """
    if not text:
        return text
    return _FOLD_RE.sub(lambda m: _FOLD[m.group(0).lower()], text)


# 원문에서 항목을 찾을 때 쓰는 패턴. 원어와 표준·변형 표기를 모두 본다.
_LOOKUP: list[tuple[re.Pattern, int]] = []
for _i, (_canon, _ens, _vars, _note) in enumerate(TERMS):
    _alts = [re.escape(_canon)] + [re.escape(v) for v in _vars] \
        + [re.escape(e) for e in _ens]
    _LOOKUP.append((re.compile("|".join(_alts), re.IGNORECASE), _i))


def prompt_block(*texts: str) -> str:
    """요약 프롬프트에 얹을 표기 사전. **원문에 걸리는 항목만** 담는다.

    전체를 매번 보내면 호출당 토큰이 그만큼 늘고, 사전이 커질수록 무료 한도를
    그대로 깎는다. 원문(제목·본문)에 이름이 보이는 항목만 실으면 보통 0~3개다.
    """
    haystack = " ".join(t for t in texts if t)
    if not haystack:
        return ""
    hits = [TERMS[i] for pat, i in _LOOKUP if pat.search(haystack)]
    if not hits:
        return ""
    lines = []
    for canon, ens, _vars, note in hits:
        tail = f" — {note}" if note else ""
        lines.append(f"- {ens[0]}: **{canon}**{tail}" if ens
                     else f"- **{canon}**{tail}")
    return ("\n\n## 고유명사 표기 (이 표기를 글자 그대로 쓸 것)\n"
            + "\n".join(lines)
            + "\n다르게 음차하지 말 것. 같은 사건이 매번 다른 이름으로 나가면"
              " 중복 판정과 검색이 깨진다.")
