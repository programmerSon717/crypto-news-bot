"""공개 웹사이트(docs/)를 만든다. GitHub Pages 로 서비스한다.

Claude 아티팩트를 쓰지 않는 이유: 그 링크는 만든 사람 계정에 묶여 다른 사람이 못 연다.
GitHub Pages 는 리포가 public 이므로 주소만 알면 누구나 본다.

여기서 만드는 것
  index.html    상태판 — status.json / history.jsonl 을 같은 출처에서 읽는다
  handoff.html  인수인계 문서를 역할별로 갈라 놓은 것
  handoff.json  위 문서의 원본 데이터(내가 읽기 쉬우라고)
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DOCS = os.path.join(ROOT, "docs")

# 문서를 '역할'로 가른다. 파일 순서가 아니라 **찾는 목적** 순서다.
ROLES = [
    ("지금 상태",   "무슨 일이 있었고 지금 어떤 상태인가",       ["## 0.", "## 2. 현재 상태"]),
    ("운영",       "어떻게 돌고 있나 · 멈췄을 때 뭘 보나",      ["## 5. 클라우드 운영", "## 4. 실행 방법"]),
    ("절대 규칙",   "고치기 전에 반드시 읽을 것",                ["RULES"]),
    ("구조",       "무엇이 어디에 있나",                       ["## 3. 파일 구조", "## 1. 이 프로젝트"]),
    ("판단 근거",   "왜 이렇게 했나 · 되돌리지 말 것",          ["## 6. 중요한 의사결정", "## 7. 함정"]),
    ("손보는 곳",   "톤·소스·탭을 바꾸려면",                    ["## 8. 커스터마이징", "## 9. 검증된 RSS"]),
]


def split_sections(md: str, source: str) -> list[dict]:
    out, cur = [], None
    for line in md.splitlines():
        m = re.match(r"^(#{2,3})\s+(.*)", line)
        if m:
            if cur:
                out.append(cur)
            cur = {"level": len(m.group(1)), "title": m.group(2).strip(),
                   "body": [], "source": source}
        elif cur:
            cur["body"].append(line)
    if cur:
        out.append(cur)
    for s in out:
        s["body"] = "\n".join(s["body"]).strip()
    return out


def assign_role(sec: dict) -> str:
    key = f'{"RULES" if sec["source"] == "RULES.md" else ""}## {sec["title"]}'
    for role, _, prefixes in ROLES:
        for p in prefixes:
            if p == "RULES" and sec["source"] == "RULES.md":
                return role
            if key.startswith(p) or sec["title"].startswith(p.removeprefix("## ")):
                return role
    return "그 밖에"


def build_handoff() -> dict:
    secs = []
    for name in ("HANDOFF.md", "RULES.md"):
        path = os.path.join(ROOT, name)
        if os.path.exists(path):
            secs += split_sections(open(path, encoding="utf-8").read(), name)
    grouped = {}
    for s in secs:
        grouped.setdefault(assign_role(s), []).append(
            {"title": s["title"], "body": s["body"], "source": s["source"]})
    order = [r[0] for r in ROLES] + ["그 밖에"]
    return {
        "roles": [{"name": r, "hint": next((h for n, h, _ in ROLES if n == r), ""),
                   "sections": grouped[r]}
                  for r in order if grouped.get(r)],
    }


def main():
    os.makedirs(DOCS, exist_ok=True)
    handoff = build_handoff()
    with open(os.path.join(DOCS, "handoff.json"), "w", encoding="utf-8") as f:
        json.dump(handoff, f, ensure_ascii=False, indent=1)
    total = sum(len(r["sections"]) for r in handoff["roles"])
    print(f"handoff.json: 역할 {len(handoff['roles'])}개 / 항목 {total}개")


if __name__ == "__main__":
    main()
