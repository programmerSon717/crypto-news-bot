"""영문판이 한국어판을 건드리지 않는다는 것을 '증명'한다.

사용자 지시: 영문판을 만들되 한국어판은 100% 격리한다.
조심하는 것으로는 부족해서, 깨지면 알 수 있게 검사로 고정한다.

    venv/bin/python tests/en_isolation.py
"""
import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

KO_STATE = ["botstate.sqlite3", "topics.json",
            "docs/status.json", "docs/history.jsonl"]
fails: list[str] = []


def check(cond, msg):
    print(f"  {'✅' if cond else '❌'} {msg}")
    if not cond:
        fails.append(msg)


def digest(path):
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return None
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def read(path):
    return open(os.path.join(ROOT, path), encoding="utf-8").read()


print("── 1. 워크플로 격리 ──")
ko = read(".github/workflows/bot.yml")
en = read(".github/workflows/bot-en.yml")

for f in KO_STATE + ["topics.json"]:
    name = f.split("/")[-1]
    # 영문 워크플로가 한국어판 상태 파일을 언급하면 안 된다
    hit = re.search(rf"(?<![\w_]){re.escape(name)}", en)
    check(hit is None, f"bot-en.yml 이 {name} 을 건드리지 않는다")

check("LOCALE: en" in en, "bot-en.yml 이 LOCALE=en 을 준다")
check("botstate_en.sqlite3" in en and "topics_en.json" in en,
      "bot-en.yml 이 자기 DB·탭 파일을 쓴다")
check("LOCALE" not in ko, "bot.yml 은 LOCALE 을 건드리지 않는다(기본 ko)")

def group(y):
    m = re.search(r"concurrency:\s*\n\s*group:\s*(\S+)", y)
    return m.group(1) if m else None
check(group(ko) != group(en),
      f"동시성 그룹이 다르다 ({group(ko)} vs {group(en)})")

ko_sec = set(re.findall(r"secrets\.([A-Z_]+)", ko))
en_sec = set(re.findall(r"secrets\.([A-Z_]+)", en))
shared = (ko_sec & en_sec) - {"WORKFLOW_PAT"}
check(not shared, f"봇·모델 시크릿을 공유하지 않는다 (공유: {shared or '없음'})")

print("\n── 2. 되돌리기가 양쪽 상태를 모두 보존한다 ──")
rb = read(".github/workflows/rollback.yml")
for f in ("botstate.sqlite3", "topics.json", "botstate_en.sqlite3", "topics_en.json"):
    check(f in rb, f"rollback.yml 이 {f} 을 보존 목록에 둔다")
import admin.snapshot as snap
for f in ("botstate.sqlite3", "topics.json", "botstate_en.sqlite3", "topics_en.json"):
    check(f in snap.PRESERVED, f"snapshot.PRESERVED 에 {f} 이 있다")

print("\n── 3. 언어 분기 ──")
def probe(locale):
    code = ("import prompts, i18n, topics, publisher;"
            "print(int('English-language' in prompts.SYSTEM_PROMPT),"
            "topics.CATEGORIES['이슈'][0], publisher._tab_tag('이슈'), sep='|')")
    env = dict(os.environ, LOCALE=locale)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True)
    return out.stdout.strip().split("|") if out.returncode == 0 else ["?", out.stderr[-200:], ""]

k = probe("ko"); e = probe("en")
check(k[0] == "0" and k[1] == "🚨주요이슈", f"LOCALE=ko → 한국어 프롬프트·탭 ({k[1]})")
check(e[0] == "1" and e[1] == "🚨Top Stories", f"LOCALE=en → 영문 프롬프트·탭 ({e[1]})")
check(k[2] != e[2], f"해시태그도 갈린다 ({k[2]} vs {e[2]})")

print("\n── 4. 영문 실행이 한국어 상태 파일을 못 만진다 ──")
before = {f: digest(f) for f in KO_STATE}
env = dict(os.environ, LOCALE="en",
           DB_PATH=os.path.join(ROOT, "tests", "_en_probe.sqlite3"),
           TOPICS_FILE=os.path.join(ROOT, "tests", "_en_probe.json"),
           TELEGRAM_BOT_TOKEN="0:probe", TELEGRAM_CHANNEL_ID="-100000")
subprocess.run([sys.executable, "-c",
                "import main, store, topics; "
                "from config import settings; "
                "s=store.Store(settings.db_path); s.mark_seen('k','s','t'); "
                "print(settings.db_path)"],
               cwd=ROOT, env=env, capture_output=True, text=True)
after = {f: digest(f) for f in KO_STATE}
for f in KO_STATE:
    check(before[f] == after[f], f"{f} 이 그대로다")
for junk in ("tests/_en_probe.sqlite3", "tests/_en_probe.json"):
    p = os.path.join(ROOT, junk)
    if os.path.exists(p):
        os.remove(p)

print("\n── 5. 규칙이 양쪽에 다 들어갔나 ──")
ko_p = read("prompts_ko.py"); en_p = read("prompts_en.py")
RULES = [
    ("같은 사건 중복 판정", "먼저 '같은 사건인가'", "First decide whether it is the same event"),
    ("예측시장 제외",       "예측 시장(prediction market)이 주제", "subject is a prediction market"),
    ("AI 제외",            "AI 가 주제인 글", "subject is AI"),
    ("도구로 등장하면 발행", "도구로 등장하는 것은 버리지 않는다", "merely appears as the instrument"),
    ("3점 채점 기준",       "제3자가 집계한 수치", "third-party figures"),
    ("인명 보존",           "네가 아는 이름으로 고쳐 쓰면 안 된다", 'a name you recognise'),
    ("거래소이슈 기준",      "거래소가 주체이거나", "an exchange is the actor"),
    ("지표/긴급 예외",      "`지표:` 로 시작하거나", "`지표:` or carrying"),
]
for name, kneedle, eneedle in RULES:
    check(kneedle in ko_p, f"[한국어] {name}")
    check(eneedle in en_p, f"[영문]   {name}")

print()
if fails:
    print(f"실패 {len(fails)}건:")
    for f in fails:
        print("  · " + f)
    sys.exit(1)
print("격리·동기화 모두 통과")
