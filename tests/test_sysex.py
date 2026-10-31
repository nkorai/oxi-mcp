"""Encoding checked byte for byte against the examples in OXI's MKII Remote Control Reference sheet."""
import unittest

from oxi_mcp import sysex as S


def h(s):
    return bytes.fromhex(s)


class SheetExamples(unittest.TestCase):
    def test_enter_ack_exit(self):
        self.assertEqual(S.enter_remote(), h("f0 00 21 5b 01 01 06 55 f7"))
        self.assertEqual(S.exit_remote(), h("f0 00 21 5b 01 01 00 f7"))
        self.assertEqual(S.decode_event(list(h("F0 00 21 5B 01 01 06 53 F7"))), {"type": "remote_ack"})

    def test_grid_led_row2_col4(self):
        self.assertEqual(S.grid_led(3, 1, 0x01, 0x01, 0x01),
                         h("f0 00 21 5b 01 01 01 01 03 00 01 00 01 00 01 f7"))

    def test_grid_led_first_example_bytes(self):
        # id 0x7d, r 0xf0, g 0x03, b 0xf0: x 13, y 7 on a 16-wide grid
        self.assertEqual(S.grid_led(13, 7, 0xF0, 0x03, 0xF0),
                         h("f0 00 21 5b 01 01 01 07 0d 0f 00 00 03 0f 00 f7"))

    def test_button_leds(self):
        self.assertEqual(S.button_led(4, "on"), h("f0 00 21 5b 01 01 02 00 04 00 01 f7"))
        self.assertEqual(S.button_led("seq_1", "on"), h("f0 00 21 5b 01 01 02 03 0A 00 01 f7"))
        self.assertEqual(S.button_led("seq_1_sel", "on"), h("f0 00 21 5b 01 01 02 04 02 00 01 f7"))
        self.assertEqual(len(S.LEDS), 74)

    def test_oled_white(self):
        msg = S.oled(b"\xff" * 1024)
        self.assertEqual(msg[:7], h("f0 00 21 5b 01 01 03"))
        self.assertEqual(msg[7:-1], b"\x0f" * 2048)
        self.assertEqual(msg[-1], 0xF7)


class Codec(unittest.TestCase):
    def test_nibble_roundtrip(self):
        data = bytes(range(256))
        self.assertEqual(S.denibble(S.nibble(data)), data)

    def test_frame_rejects_8bit(self):
        with self.assertRaises(ValueError):
            S.frame([0x80])

    def test_grid_bounds(self):
        with self.assertRaises(ValueError):
            S.grid_led(16, 0, 0, 0, 0)
        with self.assertRaises(ValueError):
            S.grid_led(0, 8, 0, 0, 0)

    def test_version_reply(self):
        reply = S.HEADER_MKII + bytes([3, 0]) + S.nibble(b"4.1.2 beta\0") + b"\xf7"
        self.assertEqual(S.parse_version(reply), "4.1.2")
        self.assertEqual(S.decode_event(list(reply))["value"], "4.1.2")

    def test_destructive(self):
        self.assertTrue(S.destructive(bytes([0, 1])))
        self.assertTrue(S.destructive(bytes([3, 0x61])))
        self.assertTrue(S.destructive(bytes([5, 3])))
        self.assertIsNone(S.destructive(bytes([3, 0])))
        self.assertIsNone(S.destructive(bytes([5, 0, 0, 0])))
        self.assertIsNone(S.destructive(bytes([1])))

    def test_pack_oled_layouts(self):
        px = [[0] * 128 for _ in range(64)]
        px[9][2] = 1
        self.assertEqual(S.pack_oled(px)[128 + 2], 0x02)
        self.assertEqual(S.pack_oled(px, "rows")[9 * 16], 0x20)


class Events(unittest.TestCase):
    def test_pad(self):
        self.assertEqual(S.decode_event([0x90, 0x13, 100]), {"type": "pad", "x": 3, "y": 1, "pressed": True, "note": 0x13})
        self.assertFalse(S.decode_event([0x80, 0x13, 0])["pressed"])
        self.assertFalse(S.decode_event([0x90, 0x13, 0])["pressed"])

    def test_button(self):
        self.assertEqual(S.decode_event([0x91, 25, 127])["name"], "play")
        self.assertEqual(S.decode_event([0x91, 33, 127])["name"], "seq_1")

    def test_encoder(self):
        self.assertEqual(S.decode_event([0xB0, 2, 0x03]), {"type": "encoder", "encoder": 2, "delta": 3})
        self.assertEqual(S.decode_event([0xB0, 2, 0x12])["delta"], -2)


if __name__ == "__main__":
    unittest.main()
