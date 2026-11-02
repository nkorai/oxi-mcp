"""The OXI's microSD card: find it, list its projects, and keep a rack-library checkout in step with it.

The card wins. Projects are played and saved on the device, so a card file that differs from the
library's copy is pulled into the library (git keeps the old version) before anything edits it, and an
edit is written back to the card only if the card still holds what was pulled.

The OXI creates the card's folders at power-up and keeps projects in its 20 internal slots; a project
reaches the card only through Project menu > Copy to SD Card (MKII manual p.23, p.277).
"""
import os
import string
import sys
from pathlib import Path
from types import SimpleNamespace

from . import library

LAYOUT = ("Projects", "Settings", "Instruments", "Grooves", "Scales")
HINT = ("No projects on the card. On the OXI: hold Shift + press Perf (Project menu), turn knob 1 to "
        "'Copy to SD Card', press knob 1 and confirm Yes; repeat per project (MKII manual p.23).")


def is_card(path):
    return all((Path(path) / d).is_dir() for d in LAYOUT)


def candidates(env=os.environ):
    """Places a mounted card may be: OXI_CARD, then drive letters / /Volumes / /media mounts."""
    out = [Path(env["OXI_CARD"])] if env.get("OXI_CARD") else []
    if sys.platform == "win32":
        out += [Path(f"{c}:/") for c in string.ascii_uppercase[2:]]
    else:
        for base in ("/Volumes", "/media", f"/media/{env.get('USER', '')}", f"/run/media/{env.get('USER', '')}"):
            if os.path.isdir(base):
                out += sorted(Path(base).iterdir())
    return out


def find(path=""):
    """The card's root: path if given (must have the OXI folders), else the first mounted card found."""
    if path:
        if not is_card(path):
            raise ValueError(f"{path} has no OXI card folders ({', '.join(LAYOUT)})")
        return Path(path)
    for c in candidates():
        try:
            if is_card(c):
                return c
        except OSError:
            continue
    raise ValueError("no OXI card mounted (looked for folders " + ", ".join(LAYOUT) + "); pass its path or set OXI_CARD")


def _library_files():
    """{card file name: slug} for every stored project."""
    s = library.tools()
    out = {}
    for md in (library.ROOT / "projects").glob("*/project.md"):
        f = s.front_matter(md.read_text(encoding="utf-8").replace("\r\n", "\n"))
        if f.get("file"):
            out[f["file"]] = md.parent.name
    return out


def _card_files(root):
    return sorted(p for p in (Path(root) / "Projects").glob("*.oxipro") if p.is_file())


def listing(root, lib=True):
    """The card's projects; with the library, each one's slug and state: same, card differs, or new."""
    root = Path(root)
    out = []
    oxi = library.tools().oxi if lib else None
    known = _library_files() if lib else {}
    for f in _card_files(root):
        p = {"file": f.name, "bytes": f.stat().st_size}
        if oxi:
            data = f.read_bytes()
            try:
                bad = oxi.verify(data)
                p.update(name=oxi.name(data).strip(), bpm=oxi.bpm(data), crc_ok=not bad)
            except ValueError as e:
                p.update(crc_ok=False, error=str(e))
            slug = known.get(f.name)
            p["slug"] = slug
            if slug:
                mine = library.oxipro_path(slug).read_bytes()
                p["state"] = "same" if mine == data else "card differs"
            else:
                p["state"] = "new"
        out.append(p)
    other = {d: sum(1 for x in (root / d).rglob("*") if x.is_file()) for d in LAYOUT if d != "Projects"}
    r = {"card": str(root), "projects": out, "other_files": other}
    if not out:
        r["hint"] = HINT
    return r


def _quiet(fn, a):
    """Run a tools/project.py command, turning its sys.exit(message) into ValueError."""
    try:
        fn(a)
    except SystemExit as e:
        raise ValueError(str(e)) from None


def pull(root, file="", push=False):
    """Take card projects into the library: changed ones replace the stored file (notes kept), new ones are
    added. file = one card file name, else every project on the card. Commits each project."""
    s = library.tools()
    pr = s.project
    root = Path(root)
    known = _library_files()
    files = [root / "Projects" / file] if file else _card_files(root)
    s.EDIT["push"] = push
    pulled = []
    for f in files:
        if not f.is_file():
            raise ValueError(f"{f} is not on the card")
        slug = known.get(f.name)
        if slug:
            if library.oxipro_path(slug).read_bytes() == f.read_bytes():
                continue
            # project.update copies the card file over the stored one and regenerates the pattern table
            _quiet(pr.update, SimpleNamespace(slug=slug, file=str(f), status=None))
            action = "updated"
        else:
            slug = pr.slugify(s.oxi.name(f.read_bytes()).strip() or f.stem)
            if (pr.PROJECTS / slug).exists():
                slug = pr.slugify(f.stem)
            _quiet(pr.add, SimpleNamespace(file=str(f), name=None, slug=slug, build=None,
                                           status="saved on the OXI (from the SD card)"))
            action = "added"
        d = pr.PROJECTS / slug
        item = {"slug": slug, "file": f.name, "action": action}
        try:
            item["commit"] = s.commit_project(d, f"projects/{slug}: {action} from the SD card ({f.name})")
        except Exception as e:  # the file is in place; only the commit failed
            item["warning"] = f"pulled, but {e}"
        pulled.append(item)
    return {"card": str(root), "pulled": pulled}


def write_back(root, slug, expect_sha):
    """Replace the card's copy of a project with the library's, if the card still holds expect_sha."""
    oxi = library.tools().oxi
    src = library.oxipro_path(slug)
    dest = Path(root) / "Projects" / src.name
    if oxi.sha(dest.read_bytes()) != expect_sha:
        raise ValueError(f"{dest} changed on the card since it was pulled; run oxi_card_pull and redo the edit")
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(src.read_bytes())
    os.replace(tmp, dest)
    return dest


def edit(root, slug, change, push=False):
    """One pattern edit to a project that is on the card: pull the card's version, edit it in the
    library (committed), then write it back to the card."""
    oxi = library.tools().oxi
    name = library.oxipro_path(slug).name
    f = Path(root) / "Projects" / name
    if not f.is_file():
        raise ValueError(f"{name} is not on the card; edit it with oxi_edit_pattern(on_card=False) and copy it with oxi_export")
    pulled = pull(root, name, push)["pulled"]
    before = oxi.sha(f.read_bytes())
    out = library.edit(slug, change, push)
    out["pulled_first"] = bool(pulled)
    out["card"] = str(write_back(root, slug, before))
    return out


def export(root, slug, overwrite=False):
    """Copy a stored project onto the card. A card file that differs is kept (pull it first) unless
    overwrite, which only the user asks for: it replaces the card's version with the library's."""
    src = library.oxipro_path(slug)
    dest = Path(root) / "Projects" / src.name
    replaced = False
    if dest.exists():
        if dest.read_bytes() == src.read_bytes():
            return {"copied": None, "card": str(dest), "note": "already on the card, identical"}
        if not overwrite:
            raise ValueError(f"{dest} differs from the library's copy; the card wins: run oxi_card_pull first, "
                             "or export with overwrite=True if the user says to replace the card's version")
        replaced = True
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(src.read_bytes())
    os.replace(tmp, dest)
    return {"copied": str(dest), "overwrote": replaced}
