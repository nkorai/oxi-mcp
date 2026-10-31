"""My OXI projects in rack-library (projects/<slug>/): read decoded patterns, apply edits.

Edits go through rack-library's own save path (tools/serve.py save_pattern): the .oxipro is patched,
project.md/patterns.json/the index are regenerated and the change is committed (pushed only if asked).
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("RACK_LIBRARY", Path.home() / "Projects" / "rack-library"))
_serve = None


def _tools():
    global _serve
    if _serve is None:
        t = str(ROOT / "tools")
        if t not in sys.path:
            sys.path.insert(0, t)
        import serve  # noqa: E402  rack-library/tools/serve.py
        _serve = serve
    return _serve


def projects():
    out = []
    for md in sorted((ROOT / "projects").glob("*/project.md")):
        f = _tools().front_matter(md.read_text(encoding="utf-8").replace("\r\n", "\n"))
        out.append({k: f.get(k) for k in ("slug", "name", "bpm", "status", "updated", "file")})
    return out


def patterns(slug):
    p = ROOT / "projects" / slug / "patterns.json"
    if not p.is_file():
        raise ValueError(f"no project {slug!r}; have {[x['slug'] for x in projects()]}")
    return json.loads(p.read_text(encoding="utf-8"))


def pattern(slug, seq, pat):
    for x in patterns(slug)["patterns"]:
        if x["seq"] == seq and x["pat"] == pat:
            return x
    raise ValueError(f"{slug}: no pattern at seq {seq} pat {pat}")


def edit(slug, edit, push=False):
    s = _tools()
    pf = s.project_file(slug)
    if not pf:
        raise ValueError(f"no project {slug!r}")
    s.EDIT["push"] = push
    code, out = s.save_pattern({"project": slug, "sha": s.oxi.sha(pf[2].read_bytes()), "edit": edit})
    if code != 200:
        raise ValueError(out.get("error"))
    out.pop("patterns", None)
    return out


def oxipro_path(slug):
    pf = _tools().project_file(slug)
    if not pf:
        raise ValueError(f"no project {slug!r}")
    return pf[2]
