"""봇 운영 상태를 한 덩어리 JSON 으로 모은다. 웹 어드민 페이지의 원료.

네트워크가 막히거나 형식이 바뀌어도 **부분 실패로 끝나야** 한다 —
한 항목이 죽어도 나머지는 채운다. 상태판이 통째로 안 뜨는 게 최악이다.
"""
import collections
import datetime
import json
import os
import re
import subprocess
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KST = datetime.timezone(datetime.timedelta(hours=9))
REPO_API = "https://api.github.com/repos/programmerSon717/crypto-news-bot"
BACKUPS = os.path.expanduser("~/Desktop/HanwhaDAPnews/backups")


def sh(*args, cwd=ROOT):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def kst(ts):
    return datetime.datetime.fromtimestamp(ts, KST).isoformat()


def get_json(url):
    try:
        import httpx
        r = httpx.get(url, headers={"Accept": "application/vnd.github+json"}, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"[:160]}


def collect_git():
    sh("git", "fetch", "-q", "origin")
    log = sh("git", "log", "origin/main", "--format=%H%x1f%ct%x1f%s", "-40")
    commits = []
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            commits.append({"sha": parts[0][:7], "ts": int(parts[1]),
                            "when": kst(int(parts[1])), "subject": parts[2]})
    return {
        "head": sh("git", "rev-parse", "--short", "HEAD"),
        "remote_head": sh("git", "rev-parse", "--short", "origin/main"),
        "dirty": bool(sh("git", "status", "--porcelain")),
        "commits": commits,
    }


def collect_runs():
    data = get_json(f"{REPO_API}/actions/runs?per_page=15")
    if "_error" in data:
        return {"error": data["_error"], "runs": []}
    out = []
    for r in data.get("workflow_runs", []):
        s = datetime.datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        e = datetime.datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00"))
        out.append({
            "number": r["run_number"], "status": r["status"],
            "conclusion": r["conclusion"], "event": r["event"],
            "sha": r["head_sha"][:7], "started": s.timestamp(),
            "when": kst(s.timestamp()),
            "minutes": round((e - s).total_seconds() / 60, 1),
        })
    return {"error": None, "runs": out}


def collect_db(path):
    if not os.path.exists(path):
        return {"error": "botstate.sqlite3 없음"}
    c = sqlite3.connect(path)
    try:
        pub = list(c.execute(
            "select message_id, category, headline, published_at, source_url, mirror_ids "
            "from published order by published_at desc limit 60"))
        cats = dict(c.execute(
            "select category, count(*) from published group by category"))
        per_day = collections.Counter()
        for (ts,) in c.execute("select published_at from published"):
            per_day[datetime.datetime.fromtimestamp(ts, KST).strftime("%m-%d")] += 1
        srcs = dict(c.execute(
            "select source, count(*) from seen group by source order by count(*) desc limit 15"))
        return {
            "error": None,
            "seen": c.execute("select count(*) from seen").fetchone()[0],
            "published": c.execute("select count(*) from published").fetchone()[0],
            "urls_indexed": c.execute("select count(*) from seen_urls").fetchone()[0]
                if c.execute("select name from sqlite_master where name='seen_urls'").fetchone() else 0,
            "by_category": cats,
            "per_day": dict(sorted(per_day.items())),
            "top_sources": srcs,
            "recent": [{"id": r[0], "category": r[1], "headline": r[2],
                        "ts": r[3], "when": kst(r[3]),
                        "url": r[4] or "", "mirrored": bool(r[5])} for r in pub],
        }
    finally:
        c.close()


def collect_backups():
    out = []
    if os.path.isdir(BACKUPS):
        for name in sorted(os.listdir(BACKUPS), reverse=True):
            p = os.path.join(BACKUPS, name)
            if not os.path.isdir(p):
                continue
            size = int(sh("du", "-sk", p).split("\t")[0] or 0)
            out.append({
                "name": name, "path": p, "mb": round(size / 1024, 1),
                "has_restore": os.path.exists(os.path.join(p, "RESTORE.md")),
                "has_archive": os.path.exists(os.path.join(p, "project.tar.gz")),
                "when": kst(os.path.getmtime(p)),
            })
    tags = [t for t in sh("git", "tag", "-l").splitlines() if t]
    return {"dirs": out, "tags": tags, "root": BACKUPS}


def poll_rhythm(commits):
    """발행 이력 커밋 간격 = 실제 폴링 리듬."""
    marks = [c for c in commits if "발행 이력" in c["subject"]]
    gaps = []
    for a, b in zip(marks, marks[1:]):
        gaps.append(round((a["ts"] - b["ts"]) / 60, 1))
    return {"count": len(marks), "gaps": gaps[:20],
            "last": marks[0]["when"] if marks else None}


def main():
    sys.path.insert(0, ROOT)
    snap = {
        "generated_at": kst(datetime.datetime.now(KST).timestamp()),
        "git": collect_git(),
        "actions": collect_runs(),
        "db": collect_db(os.path.join(ROOT, "botstate.sqlite3")),
        "backups": collect_backups(),
    }
    snap["rhythm"] = poll_rhythm(snap["git"]["commits"])
    try:
        import topics
        snap["tabs"] = {k: {"name": v[0], "thread": None} for k, v in topics.CATEGORIES.items()}
        tj = os.path.join(ROOT, "topics.json")
        if os.path.exists(tj):
            for k, v in json.load(open(tj)).items():
                if k in snap["tabs"]:
                    snap["tabs"][k]["thread"] = v
    except Exception as e:
        snap["tabs"] = {"_error": str(e)[:120]}
    print(json.dumps(snap, ensure_ascii=False))


if __name__ == "__main__":
    main()
