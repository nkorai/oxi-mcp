"""Text and pictures for the 128x64 OLED and the 16x8 pad grid."""
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import sysex as S


def text_image(lines, size=None, invert=False):
    img = Image.new("1", (S.OLED_W, S.OLED_H), 0)
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size) if size else ImageFont.load_default_imagefont()
    y = 0
    for line in lines:
        d.text((1, y), line, fill=1, font=font)
        box = d.textbbox((0, 0), line or "Ag", font=font)
        y += max(box[3], 8) + 1
    return Image.eval(img, lambda v: 255 - v) if invert else img


def pixels(img):
    img = img.convert("1")
    return [[img.getpixel((x, y)) != 0 for x in range(S.OLED_W)] for y in range(S.OLED_H)]


def preview_png(img, name="oled.png"):
    p = Path(tempfile.gettempdir()) / "oxi-mcp" / name
    p.parent.mkdir(exist_ok=True)
    img.convert("L").resize((S.OLED_W * 4, S.OLED_H * 4), Image.NEAREST).save(p)
    return str(p)


TRACK_COLORS = [(255, 40, 40), (255, 140, 0), (255, 230, 0), (60, 255, 60),
                (0, 220, 255), (40, 80, 255), (180, 60, 255), (255, 60, 180)]


def _dim(rgb, vel):
    k = 0.15 + 0.85 * min(max(vel, 0), 127) / 127
    return tuple(int(c * k) for c in rgb)


def pattern_cells(pat, page=0):
    """Pads for one decoded pattern (rack-library patterns.json shape), steps page*16..page*16+15.
    Multitrack: row = track. Mono/chord: rows are the pattern's scale rows, highest at the top
    (only the 8 rows around the most used ones fit)."""
    lo = page * S.GRID_W
    cells = {}
    if pat["type"] == "multi":
        for tr in pat["tracks"][: S.GRID_H]:
            y = tr["track"] - 1
            for h in tr["hits"]:
                if lo <= h["i"] < lo + S.GRID_W:
                    cells[(h["i"] - lo, y)] = _dim(TRACK_COLORS[y], h["vel"])
        return cells
    steps = [s for s in pat.get("steps", []) if lo <= s["i"] < lo + S.GRID_W]
    if not steps:
        return cells
    key = lambda s: s["note"]
    vals = sorted(key(s) for s in steps)
    mid = vals[len(vals) // 2]
    uniq = sorted(set(vals))
    i = uniq.index(mid)
    window = uniq[max(0, min(i - 4, len(uniq) - S.GRID_H)):][: S.GRID_H]
    for s in steps:
        k = key(s)
        if k in window:
            y = S.GRID_H - 1 - window.index(k)
            tie = s.get("tie") or s.get("gate", 0) > 100
            cells[(s["i"] - lo, y)] = _dim((0, 255, 160) if not tie else (0, 160, 255), s.get("vel", 100))
    return cells
