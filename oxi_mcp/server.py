"""OXI ONE MKII MCP server.

What is solid: plain MIDI the manual documents (clock/transport when Sync = USB, Program Change
selecting arranger pattern columns, notes/CC), the MKII remote-control SysEx from OXI's Remote
Control Reference sheet (pads, button LEDs, OLED, events), and offline .oxipro work through
rack-library. What is experimental: SYSTEM version / PROJECT requests, which come from the MK1
desktop app and are undocumented for MKII.
"""
import time

from mcp.server.fastmcp import FastMCP

from . import card, device, library, screen
from . import sysex as S

mcp = FastMCP("oxi-one", instructions=(
    "Control an OXI ONE MKII sequencer over USB MIDI (and, if RACK_LIBRARY is set, read/edit projects stored there). "
    "Call oxi_status first. For pads/LEDs/OLED call oxi_remote(on=True) first; the device then shows only "
    "what you draw until oxi_remote(on=False). The OXI's SD card wins over the library: check oxi_card and "
    "pull before editing. Gear facts come from rack-library's manual cards, not memory."))
dev = device.Device()


def _ok(**kw):
    return {"ok": True, **kw}


# --- connection --------------------------------------------------------------------------------

@mcp.tool()
def oxi_status(probe: bool = True) -> dict:
    """MIDI ports, whether the OXI is connected, remote-mode state, clock, and (probe=True) a firmware
    version request (experimental on MKII: MK1 SYSTEM message, tried with the MKII then the MK1 header)."""
    out = {"ports": device.ports(), "connected": dev.connected, "output": dev.out_name, "input": dev.in_name,
           "remote_mode": dev.remote, "clock_bpm": dev.clock_bpm}
    if not dev.connected:
        try:
            out.update(connected=True, **dev.connect())
        except RuntimeError as e:
            out["error"] = str(e)
            return out
    if probe and dev.inp is not None:
        for name, hdr in (("mkii", S.HEADER_MKII), ("mk1", S.HEADER_MK1)):
            ev = dev.request(S.sw_version_request(hdr), lambda e: e["type"] == "version", 0.8)
            if ev:
                out["firmware"] = ev["value"]
                out["version_header"] = name
                break
        else:
            out["firmware"] = "no reply to the MK1-style version request (expected if MKII dropped it)"
    return out


@mcp.tool()
def oxi_connect(port_hint: str = "") -> dict:
    """Open the OXI's USB MIDI ports (first ports whose name contains port_hint, default 'OXI').
    Any other MIDI output can be used too, e.g. port_hint='loopMIDI' to test without the device."""
    return _ok(**dev.connect(port_hint or None))


@mcp.tool()
def oxi_disconnect() -> dict:
    """Leave remote mode if on, stop the clock, close the ports."""
    if dev.remote:
        dev.send(S.exit_remote())
    dev.close()
    return _ok()


# --- plain MIDI ----------------------------------------------------------------------------------

@mcp.tool()
def oxi_transport(action: str) -> dict:
    """Send MIDI start / stop / continue. The OXI follows only when its Sync source is USB (Config)."""
    b = {"start": 0xFA, "stop": 0xFC, "continue": 0xFB}[action]
    if action == "stop":
        dev.clock_stop()
    dev.send([b])
    return _ok(sent=action)


@mcp.tool()
def oxi_clock(bpm: float = 0, send_start: bool = True) -> dict:
    """Run MIDI clock (24 ppqn) to the OXI at bpm, from a background thread; bpm=0 stops it (and sends stop).
    The OXI must sync to USB for this to drive it."""
    if bpm <= 0:
        dev.clock_stop(send_stop=True)
        return _ok(clock="stopped")
    if not 20 <= bpm <= 300:
        raise ValueError("bpm 20-300")
    dev.clock_start(bpm, send_start)
    return _ok(clock_bpm=bpm)


@mcp.tool()
def oxi_select_column(column: int, channel: int = 1) -> dict:
    """Program Change column-1 on the OXI's 'PC receive' global channel (Config > MIDI): selects that
    arranger pattern column (manual p.240). channel must match the device setting."""
    if not 1 <= column <= 128 or not 1 <= channel <= 16:
        raise ValueError("column 1-128, channel 1-16")
    dev.send([0xC0 | (channel - 1), column - 1])
    return _ok(program=column - 1, channel=channel)


@mcp.tool()
def oxi_send_note(note: int, velocity: int = 100, duration_ms: int = 200, channel: int = 1) -> dict:
    """Note on, wait, note off. Into the OXI this is e.g. sequencer transpose ('Trnsp' input, manual p.68)
    or a note to record with MIDI capture (p.93), depending on its settings."""
    ch = (channel - 1) & 0x0F
    dev.send([0x90 | ch, note & 0x7F, velocity & 0x7F])
    time.sleep(max(0, min(duration_ms, 10000)) / 1000)
    dev.send([0x80 | ch, note & 0x7F, 0])
    return _ok()


@mcp.tool()
def oxi_send_cc(cc: int, value: int, channel: int = 1) -> dict:
    """One control change."""
    dev.send([0xB0 | ((channel - 1) & 0x0F), cc & 0x7F, value & 0x7F])
    return _ok()


# --- remote mode ---------------------------------------------------------------------------------

def _need_remote():
    if not dev.remote:
        raise RuntimeError("not in remote mode: call oxi_remote(on=True) first")


@mcp.tool()
def oxi_remote(on: bool = True) -> dict:
    """Enter/leave the MKII SysEx remote mode (Remote Control Reference sheet). In it the OXI stops
    drawing its own UI: pads, function-button LEDs and the OLED show what the oxi_grid/oxi_led/oxi_oled
    tools send, and pads/buttons/encoders arrive as events (oxi_events)."""
    if not on:
        dev.send(S.exit_remote())
        dev.remote = False
        return _ok(remote_mode=False)
    ack = dev.request(S.enter_remote(), lambda e: e["type"] == "remote_ack", 1.5) if dev.inp else None
    if dev.inp is None:
        dev.send(S.enter_remote())
    dev.remote = True
    return _ok(remote_mode=True, ack=bool(ack),
               note=None if ack else "no ACK (F0 00 21 5B 01 01 06 53 F7) seen; check firmware supports remote mode")


@mcp.tool()
def oxi_grid(cells: list[dict] | None = None, fill: list[int] | None = None, clear: bool = False) -> dict:
    """Light pads. cells = [{x 0-15, y 0-7 (0 = top row), r, g, b 0-255}]. clear=True first blanks all 128,
    fill=[r,g,b] first paints all 128."""
    _need_remote()
    msgs = []
    base = [0, 0, 0] if clear else fill
    if base:
        msgs += [S.grid_led(x, y, *base) for y in range(S.GRID_H) for x in range(S.GRID_W)]
    for c in cells or []:
        msgs.append(S.grid_led(int(c["x"]), int(c["y"]), int(c.get("r", 0)), int(c.get("g", 0)), int(c.get("b", 0))))
    dev.send_many(msgs, gap=0.0005)
    return _ok(sent=len(msgs))


@mcp.tool()
def oxi_show_pattern(project: str, seq: int, pat: int, page: int = 0) -> dict:
    """Draw a rack-library project's pattern on the pads: steps page*16.. across, multitrack rows = tracks,
    mono/chord rows = the 8 pitches around the pattern's middle (highest on top), brightness = velocity."""
    _need_remote()
    p = library.pattern(project, seq, pat)
    cells = screen.pattern_cells(p, page)
    msgs = [S.grid_led(x, y, *cells.get((x, y), (0, 0, 0))) for y in range(S.GRID_H) for x in range(S.GRID_W)]
    dev.send_many(msgs, gap=0.0005)
    return _ok(pattern=f"{p['name']} ({p['type']}, {p['length']} steps)", lit=len(cells))


@mcp.tool()
def oxi_led(led: str, value: str = "on") -> dict:
    """Function-button LED. led = a name from LEDS_functions_e (back, play, rec, stop, shift, copy, seq_1..
    seq_8 (RGB), seq_1_sel.., ...) or its id 0-73; value = off|on|blink|blink_fast|short.
    White LEDs show only when their blue secondary LED is off (sheet note)."""
    _need_remote()
    dev.send(S.button_led(int(led) if led.isdigit() else led, value))
    return _ok()


@mcp.tool()
def oxi_led_names() -> dict:
    """LED names (button LEDs, for oxi_led) and button names (as reported by oxi_events)."""
    return {"leds": S.LEDS, "buttons": S.BUTTONS, "values": list(S.LED_VALUES)}


@mcp.tool()
def oxi_oled(lines: list[str], size: int = 0, invert: bool = False, layout: str = "pages",
             preview: bool = False) -> dict:
    """Write text to the 128x64 OLED (full-screen dump). size=0 uses the small bitmap font (~5 lines of 21
    chars); size>0 a scalable font at that pixel size. layout = pixel packing, 'pages' (SSD1306 style) or
    'rows': the sheet doesn't say which the MKII uses; if the text looks scrambled, try the other.
    preview=True also writes a PNG and returns its path (works without the device)."""
    img = screen.text_image(lines, size or None, invert)
    out = {}
    if preview:
        out["preview"] = screen.preview_png(img)
    if dev.remote or not preview:
        _need_remote()
        dev.send(S.oled(S.pack_oled(screen.pixels(img), layout)))
        out["sent"] = True
    return _ok(**out)


@mcp.tool()
def oxi_events(wait_s: float = 0, clear: bool = True, types: list[str] | None = None) -> dict:
    """Events the OXI sent: pad {x,y,pressed}, button {name,pressed}, encoder {encoder,delta}, plus notes/CC/
    program/transport/SysEx outside remote mode. wait_s > 0 waits (max 60 s) for at least one first."""
    if wait_s > 0:
        end = time.time() + min(wait_s, 60)
        with dev.cond:
            while not any(not types or e["type"] in types for e in dev.events) and time.time() < end:
                dev.cond.wait(end - time.time())
    return {"events": dev.take_events(clear, set(types) if types else None)}


# --- raw / experimental ----------------------------------------------------------------------------

@mcp.tool()
def oxi_sysex(body_hex: str, header: str = "mkii", wait_reply_s: float = 0.5,
              allow_destructive: bool = False) -> dict:
    """Send OXI SysEx: body_hex is everything between the 6-byte header and F7 (e.g. '03 00' = MK1 SW version
    request). Firmware update, memory reset and project/pattern send/delete bodies are refused unless
    allow_destructive=True. Returns SysEx replies seen within wait_reply_s."""
    body = bytes.fromhex(body_hex)
    why = S.destructive(body)
    if why and not allow_destructive:
        raise ValueError(f"refused: this message would {why} on the device; pass allow_destructive=True if intended")
    hdr = S.HEADER_MKII if header == "mkii" else S.HEADER_MK1
    msg = S.frame(body, hdr)
    replies = []
    if wait_reply_s > 0 and dev.inp is not None:
        with dev.cond:
            start = dev.n
        dev.send(msg)
        time.sleep(min(wait_reply_s, 10))
        with dev.cond:
            replies = [e for e in dev.events if e["n"] > start and e["type"] in ("sysex", "version", "remote_ack")]
    else:
        dev.send(msg)
    return _ok(sent=msg.hex(" "), replies=replies)


@mcp.tool()
def oxi_probe_project(project: int = 1, header: str = "mkii", wait_s: float = 2.0) -> dict:
    """EXPERIMENTAL, read-only: ask for a project header with the MK1 desktop request (05 00 <project-1> 00).
    Reports whatever SysEx comes back; MKII may ignore it."""
    return oxi_sysex(f"05 00 {(project - 1) & 0x7F:02x} 00", header, wait_s)


# --- rack-library projects (offline, optional: RACK_LIBRARY) ----------------------------------------------------------------

@mcp.tool()
def oxi_projects() -> dict:
    """OXI projects stored in the rack-library checkout at RACK_LIBRARY (projects/<slug>/)."""
    projects = library.projects()
    return {"root": str(library.ROOT), "projects": projects}


@mcp.tool()
def oxi_pattern(project: str, seq: int = 0, pat: int = 0) -> dict:
    """Decoded patterns of a stored project (seq/pat 1-based; 0 = all): type, channel, scale, and steps as
    MIDI note/velocity/gate (pitch mapping INFERRED, see the 'inferred' field)."""
    data = library.patterns(project)
    if seq:
        data["patterns"] = [p for p in data["patterns"] if p["seq"] == seq and (not pat or p["pat"] == pat)]
    return data


@mcp.tool()
def oxi_edit_pattern(project: str, seq: int, pat: int, steps: list[dict] | None = None,
                     tracks: list[dict] | None = None, length: int = 0, base: int = -1, root: str = "",
                     scale: str = "", channel: int = 0, push: bool = False, on_card: bool = True,
                     card_path: str = "") -> dict:
    """Replace one stored pattern's steps and commit it in rack-library (the same save path as the web
    editor). Mono/chord: steps = [{i, note, vel, gate, tie?}] (the whole pattern; omitted steps become
    empty), length optional. Multitrack: tracks = [{track 1-8, hits: [{i, vel, gate?}]}].
    base (MIDI note of grid row 0, C3 = 48), root (C..B), scale (major, minor, phrygian, ...) and channel (1-16)
    can be set with or without steps; every note keeps its pitch. push=True also git-pushes.
    The card wins: if the OXI's SD card is mounted (or card_path given) and holds this project, the card's
    version is pulled first and the edited file is written back to the card. on_card=False edits only
    the library."""
    edit = {"seq": seq, "pat": pat}
    if steps is not None:
        edit["steps"] = steps
    if tracks is not None:
        edit["tracks"] = tracks
    if length:
        edit["length"] = length
    for k, v in (("base", base if base >= 0 else None), ("root", root), ("scale", scale), ("channel", channel)):
        if v:
            edit[k] = v
    sd = None
    if on_card:
        try:
            sd = card.find(card_path)
        except ValueError:
            if card_path:
                raise
    if sd and (sd / "Projects" / library.oxipro_path(project).name).is_file():
        return _ok(**card.edit(sd, project, edit, push))
    out = library.edit(project, edit, push)
    out["card"] = None
    out["note"] = ("no OXI card mounted; library only" if not sd else
                   f"{library.oxipro_path(project).name} is not on the card; oxi_export copies it there")
    return _ok(**out)


# --- the OXI's microSD card (needs RACK_LIBRARY to decode and sync) -----------------------------------------------

@mcp.tool()
def oxi_card(card_path: str = "") -> dict:
    """The mounted OXI microSD card (found by its folders, or card_path): each project file with its name, BPM,
    CRC check and how it compares to rack-library (same / card differs / new). The card wins: pull with
    oxi_card_pull before trusting the library's copy."""
    sd = card.find(card_path)
    try:
        return card.listing(sd)
    except RuntimeError as e:  # no RACK_LIBRARY: list files only
        return {**card.listing(sd, lib=False), "note": str(e)}


@mcp.tool()
def oxi_card_pull(file: str = "", card_path: str = "", push: bool = False) -> dict:
    """Take the card's projects into rack-library (the card wins): a changed file replaces the stored one,
    keeping project.md notes; a new one is added as projects/<slug>/. file = one card file name, else all.
    Each is committed; push=True also git-pushes."""
    return _ok(**card.pull(card.find(card_path), file, push))


@mcp.tool()
def oxi_export(project: str, card_path: str = "") -> dict:
    """Copy a stored project's .oxipro onto the card's Projects folder (card found automatically, or card_path).
    Never overwrites a card file that differs; pull it first. Load it on the OXI: Project menu > Load from SD Card."""
    return _ok(**card.export(card.find(card_path), project))


def main():
    mcp.run()


if __name__ == "__main__":
    main()
