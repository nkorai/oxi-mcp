"""Projects to and from the OXI ONE MKII over USB SysEx, no SD card.

Measured on the device 2026-11-02 (MK1 desktop-app messages, which the MKII answers):
  05 00 <slot> 00     get header  -> 05 01 <slot> 00 <header, 7-bit packed>; nothing after the 4 bytes = empty slot
  05 02 <slot> <k>    get pattern -> 05 03 <slot> <k> <block[:n + 8], packed>   (k = seq * 16 + pattern, 0-127)
  05 01 <slot> 00 ... send header, 05 03 <slot> <k> ... send pattern: each answered 05 53 (ACK)
Slots 0-19 (the device has 20 projects). Packing: each 7 data bytes follow one byte holding their high bits,
bit k for byte k. The device converts a received v10 header to its own v12 layout (10820 bytes), so a
read-back header is checked by name, not bytes; pattern blocks come back exactly as sent.
"""
import struct
from pathlib import Path

from . import sysex as S

SLOTS = 20
PATTERNS = 128


def default_block(k):
    """An empty mono pattern for sequencer k // 16 on its own MIDI channel (oxipro's device-style default,
    which the MKII accepted on 2026-11-02): 8 equal-length wire blocks in default_blocks.bin."""
    d = (Path(__file__).parent / "default_blocks.bin").read_bytes()
    n = len(d) // 8
    return d[k // 16 * n:(k // 16 + 1) * n]


def pack7(data):
    out = bytearray()
    for g in range(0, len(data), 7):
        chunk = data[g:g + 7]
        out.append(sum(((b >> 7) & 1) << k for k, b in enumerate(chunk)))
        out += bytes(b & 0x7F for b in chunk)
    return bytes(out)


def unpack7(data):
    out = bytearray()
    for g in range(0, len(data), 8):
        hi = data[g]
        out += bytes(b | (((hi >> k) & 1) << 7) for k, b in enumerate(data[g + 1:g + 8]))
    return bytes(out)


def _ask(dev, body, op, timeout=5.0):
    ops = (S.ACK, S.NACK) if op == S.ACK else (op,)
    ev = dev.request(S.frame(body), lambda e: e["type"] == "project" and e["op"] in ops, timeout)
    if ev is None:
        raise RuntimeError(f"the OXI did not answer {bytes(body[:4]).hex(' ')} within {timeout:g}s")
    return ev


def _slot(slot):
    if not 1 <= slot <= SLOTS:
        raise ValueError(f"slot {slot}: the OXI has project slots 1-{SLOTS}")
    return slot - 1


def header(dev, slot):
    """The raw header stored in slot (1-based), or b'' when the slot is empty."""
    s = _slot(slot)
    return unpack7(_ask(dev, [S.CAT_PROJECT, S.PROJECT_GET_HEADER, s, 0], S.PROJECT_SEND_HEADER)["data"])


def pattern(dev, slot, k):
    s = _slot(slot)
    return unpack7(_ask(dev, [S.CAT_PROJECT, S.PROJECT_GET_PATTERN, s, k], S.PROJECT_SEND_PATTERN)["data"])


def describe(hdr):
    return {"name": hdr[4:16].split(b"\0")[0].decode("ascii", "replace"), "version": hdr[0],
            "bpm": struct.unpack_from("<H", hdr, 16)[0] / 10}


def device_projects(dev):
    out = []
    for slot in range(1, SLOTS + 1):
        hdr = header(dev, slot)
        out.append({"slot": slot, **describe(hdr), "empty": False} if hdr else {"slot": slot, "empty": True})
    return out


def _wire(blk):
    return blk[:struct.unpack_from("<H", blk, 2)[0] + 8]


def push(dev, hdr, blocks, slot, overwrite=False, default=default_block):
    """Write a project (v10 header bytes + 128 blocks, None = empty) into slot (1-based). A used slot is kept
    unless overwrite=True. Empty patterns get default(k), so nothing of an older project survives."""
    s = _slot(slot)
    old = header(dev, slot)
    if old and not overwrite:
        raise ValueError(f"slot {slot} holds {describe(old)['name']!r}; push to an empty slot, or pass "
                         "overwrite=True only if the user says to replace it")
    _need_ack(_ask(dev, [S.CAT_PROJECT, S.PROJECT_SEND_HEADER, s, 0, *pack7(hdr)], S.ACK, 10), "header")
    for k in range(PATTERNS):
        blk = blocks[k] if blocks[k] is not None else default(k)
        _need_ack(_ask(dev, [S.CAT_PROJECT, S.PROJECT_SEND_PATTERN, s, k, *pack7(_wire(blk))], S.ACK), f"pattern {k}")
    name = describe(hdr)["name"]
    back = describe(header(dev, slot))["name"]
    if back != name:
        raise RuntimeError(f"slot {slot} reads back as {back!r}, not {name!r}")
    bad = [k for k in range(PATTERNS) if blocks[k] is not None and pattern(dev, slot, k) != _wire(blocks[k])]
    if bad:
        raise RuntimeError(f"slot {slot}: patterns {bad} read back different")
    return {"slot": slot, "name": name, "replaced": describe(old)["name"] if old else None,
            "patterns": PATTERNS, "verified": sum(b is not None for b in blocks)}


def _need_ack(ev, what):
    if ev["op"] != S.ACK:
        raise RuntimeError(f"the OXI refused the {what}")
