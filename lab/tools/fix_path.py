from pathlib import Path
p = Path("README.md")
t = p.read_text(encoding="utf-8")
t = t.replace("`server/moondream_server.py`", "`remote/moondream_server.py`")
p.write_text(t, encoding="utf-8")
print("ok")
