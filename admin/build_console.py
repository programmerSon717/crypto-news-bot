"""스냅샷을 템플릿에 심어 콘솔 HTML 을 만든다.

    venv/bin/python admin/snapshot.py > admin/snapshot.json
    venv/bin/python admin/build_console.py
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

snap_path = os.path.join(HERE, "snapshot.json")
if not os.path.exists(snap_path) or "--refresh" in sys.argv:
    out = subprocess.run([sys.executable, os.path.join(HERE, "snapshot.py")],
                         cwd=ROOT, capture_output=True, text=True,
                         env={**os.environ, "PYTHONPATH": ROOT})
    if out.returncode != 0:
        sys.exit(f"스냅샷 실패: {out.stderr[:400]}")
    open(snap_path, "w", encoding="utf-8").write(out.stdout)

data = open(snap_path, encoding="utf-8").read().strip()
json.loads(data)                      # 깨진 JSON 을 페이지에 심지 않는다
tpl = open(os.path.join(HERE, "console_template.html"), encoding="utf-8").read()
# </script> 가 데이터에 들어가면 스크립트 태그가 조기 종료된다
safe = data.replace("</", "<\\/")
html = tpl.replace("__SNAPSHOT__", safe)
dest = os.path.join(HERE, "console.html")
open(dest, "w", encoding="utf-8").write(html)
print(f"생성: {dest}  ({len(html):,} bytes)")
