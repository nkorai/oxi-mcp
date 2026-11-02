"""Optional: OXI projects kept in a rack-library checkout (projects/<slug>/): read decoded patterns, apply edits.
Enabled by setting RACK_LIBRARY to the checkout's path; without it the project tools say so.

Edits go through rack-library's own save path (tools/serve.py save_pattern): the .oxipro is patched,
project.md/patterns.json/the index are regenerated and the change is committed (pushed only if asked).
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ["RACK_LIBRARY"]) if os.environ.get("RACK_LIBRARY") else None
_serve = None
_path = None  # the tools/ folder put on sys.path


def use(root):
    """Point the project tools at another checkout: forgets every module loaded from the old one's tools/."""
    global ROOT, _serve, _path
    if ROOT is not None:
        old = (ROOT / "tools").resolve()
        for name, m in list(sys.modules.items()):
            f = getattr(m, "__file__", None)
            if f and Path(f).resolve().parent == old:
                del sys.modules[name]
        sys.path[:] = [x for x in sys.path if not x or Path(x).resolve() != old]
    ROOT = Path(root) if root else None
    _serve = _path = None


def tools():
    """rack-library's tools/serve.py (and through it .oxi and .project), imported from ROOT."""
    global _serve, _path
    if ROOT is None or not (ROOT / "tools" / "serve.py").is_file():
        raise RuntimeError("project tools need RACK_LIBRARY set to a rack-library checkout (tools/serve.py, projects/)")
    if _serve is None:
        _path = str(ROOT / "tools")
        sys.path.insert(0, _path)
        import serve  # noqa: E402  rack-library/tools/serve.py
        _serve = serve
    return _serve


def projects():
    tools()
    out = []
    for md in sorted((ROOT / "projects").glob("*/project.md")):
        f = tools().front_matter(md.read_text(encoding="utf-8").replace("\r\n", "\n"))
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
    s = tools()
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
    pf = tools().project_file(slug)
    if not pf:
        raise ValueError(f"no project {slug!r}")
    return pf[2]
