"""OXI ONE SysEx: framing, nibbling, remote-mode messages and incoming event decoding.

Sources:
  MKII remote mode: OXI's "Remote Control Reference" sheet (linked from the MKII manual p.273),
    https://docs.google.com/spreadsheets/d/1Yccnrluv10QL_PauMmtCt64EtYjfSeZEXKrlrw8P24w (tab "MKII").
  SYSTEM / PROJECT categories: MK1 OXI-Desktop sources (faqteur/OXI-Desktop, OXI_SYSEX_MSG.h).
    Not documented for MKII; tools that use them say "experimental".
Every multi-bit value after the category byte is sent as two 7-bit-safe bytes: high nibble, low nibble.
"""

MANUFACTURER = bytes([0x00, 0x21, 0x5B])
HEADER_MKII = bytes([0xF0, 0x00, 0x21, 0x5B, 0x01, 0x01])
HEADER_MK1 = bytes([0xF0, 0x00, 0x21, 0x5B, 0x00, 0x01])

# categories (MK1 header file; 0x06 is the MKII sheet's remote category)
CAT_FW_UPDATE, CAT_BLE, CAT_MIDI, CAT_SYSTEM, CAT_ANALOG, CAT_PROJECT, CAT_REMOTE = range(7)
SYSTEM_SW_VERSION, SYSTEM_HW_VERSION = 0x00, 0x01
SYSTEM_MEM_RESET = 0x61
PROJECT_GET_HEADER, PROJECT_SEND_HEADER, PROJECT_GET_PATTERN, PROJECT_SEND_PATTERN = 0, 1, 2, 3
PROJECT_DELETE_PROJECT, PROJECT_DELETE_PATTERN = 4, 5
ACK, NACK = 0x53, 0x54

# remote mode (MKII sheet)
REMOTE_ENTER = bytes([CAT_REMOTE, 0x55])
REMOTE_ACK = bytes([CAT_REMOTE, ACK])
REMOTE_EXIT, REMOTE_GRID_LED, REMOTE_BUTTON_LED, REMOTE_OLED = 0x00, 0x01, 0x02, 0x03

GRID_W, GRID_H = 16, 8
OLED_W, OLED_H = 128, 64
OLED_BYTES = OLED_W * OLED_H // 8

LED_VALUES = {"off": 0, "on": 1, "blink": 2, "blink_fast": 4, "short": 6}

# LEDS_functions_e, in order (ids 0..73)
LEDS = ("back config arranger_show arranger_state keyboard preview arp arp_hold shift_2 shift stop sync rec "
        "rec_2 play tap mute nudge fill groove gen cvout mod ext_mod perf proj undo redo copy duplicate load "
        "load_2 save save_2 paste clear random random2 lfo condense page expand init x2 end 2 track follow step "
        "chord 64 right 48 down 32 up 16 left").split()
LEDS += [f"seq_{i}" for i in range(1, 9)] + [f"seq_{i}_sel" for i in range(1, 9)]
LED_ID = {n: i for i, n in enumerate(LEDS)}

# FunctionButtons_e, in order: the note number (MIDI ch 2) each button sends in remote mode
BUTTONS = ("arp fill left_16 end_2 keyboard_preview gen up_32 init_x2 arranger mod down_48 save back perf "
           "right_64 load encoder1 shift page copy_duplicate encoder2 stop track paste_clear encoder3 play lfo "
           "undo encoder4 rec step random mute").split() + [f"seq_{i}" for i in range(1, 9)]

# messages a raw-SysEx caller must opt into: they erase or overwrite device memory
DESTRUCTIVE = {
    (CAT_FW_UPDATE, None): "firmware update",
    (CAT_SYSTEM, SYSTEM_MEM_RESET): "memory reset",
    (CAT_PROJECT, PROJECT_SEND_HEADER): "overwrite a project",
    (CAT_PROJECT, PROJECT_SEND_PATTERN): "overwrite a pattern",
    (CAT_PROJECT, PROJECT_DELETE_PROJECT): "delete a project",
    (CAT_PROJECT, PROJECT_DELETE_PATTERN): "delete a pattern",
}


def nibble(data):
    out = bytearray()
    for b in data:
        out += bytes([(b >> 4) & 0x0F, b & 0x0F])
    return bytes(out)


def denibble(data):
    if len(data) % 2:
        raise ValueError("odd nibble count")
    return bytes((data[i] << 4 | data[i + 1]) & 0xFF for i in range(0, len(data), 2))


def frame(body, header=HEADER_MKII):
    body = bytes(body)
    if any(b > 0x7F for b in body):
        raise ValueError("SysEx body bytes must be 0x00-0x7F (nibble values first)")
    return header + body + b"\xF7"


def body_of(msg):
    """(header, body) of an OXI SysEx message, or None if it is not one."""
    msg = bytes(msg)
    if len(msg) < 7 or msg[0] != 0xF0 or msg[-1] != 0xF7 or msg[1:4] != MANUFACTURER:
        return None
    return msg[:6], msg[6:-1]


# --- remote mode -------------------------------------------------------------------------------

def enter_remote():
    return frame(REMOTE_ENTER)


def exit_remote():
    return frame([REMOTE_EXIT])


def grid_led(x, y, r, g, b):
    """One pad's RGB. x = column 0-15, y = row 0-7, from the top left; id = x + 16*y.
    (The sheet's text says x 0-7 / y 0-15, but its second example, id 0x13 = "row 2 col 4",
    only fits 16 columns of 8 rows, the grid's shape.)"""
    if not (0 <= x < GRID_W and 0 <= y < GRID_H):
        raise ValueError(f"pad ({x},{y}) is off the 16x8 grid")
    return frame([REMOTE_GRID_LED, *nibble(bytes([x + GRID_W * y, r & 0xFF, g & 0xFF, b & 0xFF]))])


def button_led(led, value="on"):
    i = LED_ID[led] if isinstance(led, str) else int(led)
    if not 0 <= i < len(LEDS):
        raise ValueError(f"LED id {i} out of range 0-{len(LEDS) - 1}")
    v = LED_VALUES[value] if isinstance(value, str) else int(value)
    return frame([REMOTE_BUTTON_LED, *nibble(bytes([i, v]))])


def oled(buf):
    """Full-screen dump: 1024 bytes (128x64, 1 bit per pixel) sent as 2048 nibbles."""
    if len(buf) != OLED_BYTES:
        raise ValueError(f"OLED buffer must be {OLED_BYTES} bytes")
    return frame([REMOTE_OLED, *nibble(buf)])


def pack_oled(pixels, layout="pages"):
    """pixels[y][x] truthy -> 1024-byte buffer.
    "pages": SSD1306-style, 8 pages of 128 column bytes, bit 0 = top row of the page.
    "rows": row-major, 16 bytes per row, bit 7 = leftmost pixel.
    The sheet does not say which the MKII uses (only that 0xFF everywhere is white); verify on the device."""
    buf = bytearray(OLED_BYTES)
    for y in range(OLED_H):
        for x in range(OLED_W):
            if pixels[y][x]:
                if layout == "pages":
                    buf[(y // 8) * OLED_W + x] |= 1 << (y % 8)
                else:
                    buf[y * (OLED_W // 8) + x // 8] |= 0x80 >> (x % 8)
    return bytes(buf)


# --- SYSTEM / PROJECT (MK1 protocol, experimental on MKII) ---------------------------------------

def sw_version_request(header=HEADER_MKII):
    return frame([CAT_SYSTEM, SYSTEM_SW_VERSION], header)


def hw_version_request(header=HEADER_MKII):
    return frame([CAT_SYSTEM, SYSTEM_HW_VERSION], header)


def project_header_request(project, header=HEADER_MKII):
    """MK1: hdr 05 00 <project index> 00 (oxi-one-web sends a trailing 0)."""
    return frame([CAT_PROJECT, PROJECT_GET_HEADER, project & 0x7F, 0], header)


def parse_version(msg):
    """SW/HW version reply -> text (MK1: de-nibbled from byte 8, ends at space or NUL), else None."""
    hb = body_of(msg)
    if not hb or len(hb[1]) < 2 or hb[1][0] != CAT_SYSTEM or hb[1][1] not in (SYSTEM_SW_VERSION, SYSTEM_HW_VERSION):
        return None
    raw = hb[1][2:]
    try:
        data = denibble(raw[: len(raw) // 2 * 2])
    except ValueError:
        return None
    return data.split(b"\0")[0].split(b" ")[0].decode("ascii", "replace")


def destructive(body):
    """Why a raw message body is destructive, or None."""
    if not body:
        return None
    cat, sub = body[0], body[1] if len(body) > 1 else None
    return DESTRUCTIVE.get((cat, None)) or DESTRUCTIVE.get((cat, sub))


# --- incoming events in remote mode --------------------------------------------------------------

def decode_event(msg):
    """Decode one incoming MIDI message (list of ints) into a dict."""
    if not msg:
        return None
    st = msg[0]
    if st == 0xF0:
        hb = body_of(msg)
        if hb and hb[1] == REMOTE_ACK:
            return {"type": "remote_ack"}
        v = parse_version(msg)
        if v is not None:
            return {"type": "version", "kind": "hw" if hb[1][1] == SYSTEM_HW_VERSION else "sw", "value": v,
                    "header": hb[0].hex(" ")}
        return {"type": "sysex", "hex": bytes(msg).hex(" ")} if len(msg) <= 64 else \
            {"type": "sysex", "bytes": len(msg), "head": bytes(msg[:24]).hex(" ")}
    if st >= 0xF8:
        return {"type": {0xF8: "clock", 0xFA: "start", 0xFB: "continue", 0xFC: "stop"}.get(st, hex(st))}
    kind, ch = st & 0xF0, st & 0x0F
    if kind in (0x80, 0x90) and len(msg) >= 3:
        on = kind == 0x90 and msg[2] > 0
        n = msg[1]
        if ch == 0:
            return {"type": "pad", "x": n % GRID_W, "y": n // GRID_W, "pressed": on, "note": n}
        if ch == 1:
            return {"type": "button", "name": BUTTONS[n] if n < len(BUTTONS) else f"#{n}", "pressed": on, "note": n}
        return {"type": "note", "channel": ch + 1, "note": n, "velocity": msg[2] if on else 0}
    if kind == 0xB0 and len(msg) >= 3:
        if ch == 0 and (1 <= msg[2] <= 0x08 or 0x11 <= msg[2] <= 0x18):
            return {"type": "encoder", "encoder": msg[1], "delta": msg[2] if msg[2] < 0x10 else -(msg[2] - 0x10)}
        return {"type": "cc", "channel": ch + 1, "cc": msg[1], "value": msg[2]}
    if kind == 0xC0 and len(msg) >= 2:
        return {"type": "program", "channel": ch + 1, "program": msg[1]}
    return {"type": "midi", "hex": bytes(msg).hex(" ")}
