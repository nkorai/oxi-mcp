"""Project transfer over USB SysEx, against a fake OXI that stores projects the way the MKII did on 2026-11-02
(firmware answering 05 00 / 05 02 with 7-bit packed data, ACK 05 53 for 05 01 / 05 03, 20 project slots)."""
import struct
import unittest

from oxi_mcp import server, transfer as T, sysex as S


def h(s):
    return bytes.fromhex(s)


def block(n, fill):
    """A pattern block: type 0, version 0x10, body length n, then fill; on the wire it is n + 8 bytes."""
    b = bytearray([fill]) * 0x3000
    b[0], b[1] = 0, 0x10
    struct.pack_into("<H", b, 2, n)
    return bytes(b)


class FakeOxi:
    def __init__(self, dev):
        self.dev, self.slots, self.sent = dev, {}, []

    def reply(self, body):
        msg = S.frame(body)  # Windows MIDI hands long SysEx over in 1024-byte pieces
        for i in range(0, len(msg), 1024):
            self.dev._on_msg((list(msg[i:i + 1024]), 0))

    def send_message(self, m):
        m = bytes(m)
        self.sent.append(m)
        body = m[6:-1]
        cat, op, slot, idx = body[0], body[1], body[2], body[3]
        if cat != S.CAT_PROJECT or slot >= 20:
            return
        if op == S.PROJECT_GET_HEADER:
            hdr = self.slots.get(slot, {}).get("header")
            self.reply(bytes([5, 1, slot, 0]) + (T.pack7(hdr) if hdr else b""))
        elif op == S.PROJECT_GET_PATTERN:
            pat = self.slots.get(slot, {}).get(idx, b"")
            self.reply(bytes([5, 3, slot, idx]) + T.pack7(pat))
        elif op == S.PROJECT_SEND_HEADER:
            self.slots.setdefault(slot, {})["header"] = T.unpack7(body[4:])
            self.reply(bytes([5, S.ACK]))
        elif op == S.PROJECT_SEND_PATTERN:
            self.slots.setdefault(slot, {})[idx] = T.unpack7(body[4:])
            self.reply(bytes([5, S.ACK]))

    def close_port(self):
        pass


class Pack7(unittest.TestCase):
    def test_matches_the_device_reply(self):
        # the start of slot 1's header reply: version 12, header length 0x2a44, "HARDFAST"
        self.assertEqual(T.pack7(h("0c 00 44 2a 48 41 52 44 46 41 53 54")),
                         h("00 0c 00 44 2a 48 41 52 00 44 46 41 53 54"))

    def test_high_bits_lsb_first_and_partial_group(self):
        self.assertEqual(T.pack7(b"\xff" * 8), h("7f 7f 7f 7f 7f 7f 7f 7f 01 7f"))
        self.assertEqual(T.pack7(b"\x80\x01"), h("01 00 01"))
        for d in (b"", b"\x00", bytes(range(256)) * 3):
            self.assertEqual(T.unpack7(T.pack7(d)), d)


class Device(unittest.TestCase):
    def setUp(self):
        d = server.dev
        d.close()
        self.oxi = FakeOxi(d)
        d.out, d.inp, d.out_name, d.in_name = self.oxi, object(), "FAKE OXI", "FAKE OXI"
        self.oxi.slots[0] = {"header": b"\x0c\x00\x44\x2aHARDFAST\0\0\0\0\x40\x06" + b"\0" * 3000}

    def tearDown(self):
        server.dev.out = server.dev.inp = None

    def test_lists_the_slots(self):
        slots = T.device_projects(server.dev)
        self.assertEqual(len(slots), 20)
        self.assertEqual(slots[0], {"slot": 1, "name": "HARDFAST", "version": 12, "bpm": 160.0, "empty": False})
        self.assertTrue(slots[1]["empty"])

    def test_push_refuses_a_used_slot_unless_told(self):
        hdr = b"\x0a\x00\xfc\x2bCHOP DROP\0\0\0" + b"\x40\x06" + b"\x11" * 3000
        with self.assertRaises(ValueError):
            T.push(server.dev, hdr, [None] * 128, slot=1)
        self.assertEqual(self.oxi.slots[0]["header"][4:12], b"HARDFAST")

    def test_push_sends_header_and_every_pattern_then_verifies(self):
        hdr = b"\x0a\x00\xfc\x2bCHOP DROP\0\0\0" + b"\x40\x06" + b"\x11" * 3000
        blocks = [None] * 128
        blocks[0], blocks[17] = block(2000, 0x22), block(3797, 0x33)
        r = T.push(server.dev, hdr, blocks, slot=2, default=lambda k: block(12, k))
        self.assertEqual(r["slot"], 2)
        self.assertEqual(r["patterns"], 128)
        self.assertEqual(r["verified"], 2)
        self.assertEqual(self.oxi.slots[1]["header"], hdr)
        self.assertEqual(self.oxi.slots[1][17], blocks[17][:3805])
        self.assertEqual(self.oxi.slots[1][5], block(12, 5)[:20])  # empty slots get the default block

    def test_push_tool_reads_the_library_project(self):
        from oxi_mcp import library
        if not (library.ROOT and (library.ROOT / "projects" / "hardfast").is_dir()):
            self.skipTest("RACK_LIBRARY not set")
        r = server.oxi_device_push("hardfast", 3)
        self.assertTrue(r["ok"])
        self.assertEqual(self.oxi.slots[2]["header"][4:12], b"HARDFAST")


if __name__ == "__main__":
    unittest.main()
