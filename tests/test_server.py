"""Tools driven through a fake MIDI port that answers like the sheet says the device does."""
import os
import unittest

from unittest import mock

import rtmidi

from oxi_mcp import device, library, server, sysex as S


class FakeOut:
    def __init__(self, dev, replies):
        self.dev, self.replies, self.sent = dev, replies, []

    def send_message(self, m):
        self.sent.append(bytes(m))
        r = self.replies.get(bytes(m))
        if r:
            self.dev._on_msg((list(r), 0))

    def close_port(self):
        pass


class Tools(unittest.TestCase):
    def setUp(self):
        d = server.dev
        d.close()
        replies = {S.enter_remote(): S.frame(S.REMOTE_ACK),
                   S.sw_version_request(): S.HEADER_MKII + bytes([3, 0]) + S.nibble(b"9.9.9\0") + b"\xf7"}
        d.out = FakeOut(d, replies)
        d.inp = object()
        d.out_name = d.in_name = "FAKE OXI"
        self.out = d.out

    def tearDown(self):
        server.dev.out = server.dev.inp = None
        server.dev.remote = False

    def test_status_probe(self):
        r = server.oxi_status()
        self.assertEqual(r["firmware"], "9.9.9")
        self.assertEqual(r["version_header"], "mkii")

    def test_status_without_midi_backend(self):
        def boom():
            raise rtmidi.SystemError("MidiInAlsa::initialize: error creating ALSA sequencer client object.")
        with mock.patch.object(rtmidi, "MidiIn", boom):
            p = device.ports()
        self.assertEqual((p["inputs"], p["outputs"]), ([], []))
        self.assertIn("ALSA", p["error"])

    def test_remote_needs_enter(self):
        with self.assertRaises(RuntimeError):
            server.oxi_led("play")
        self.assertTrue(server.oxi_remote(True)["ack"])
        server.oxi_led("play", "blink")
        self.assertEqual(self.out.sent[-1], S.button_led(S.LED_ID["play"], 2))
        server.oxi_remote(False)
        self.assertEqual(self.out.sent[-1], S.exit_remote())

    def test_grid_clear_and_cells(self):
        server.oxi_remote(True)
        n = len(self.out.sent)
        r = server.oxi_grid(cells=[{"x": 15, "y": 7, "r": 255}], clear=True)
        self.assertEqual(r["sent"], 129)
        self.assertEqual(self.out.sent[n], S.grid_led(0, 0, 0, 0, 0))
        self.assertEqual(self.out.sent[-1], S.grid_led(15, 7, 255, 0, 0))

    def test_events(self):
        server.oxi_events()
        server.dev._on_msg(([0x90, 0x13, 90], 0))
        server.dev._on_msg(([0x91, 25, 127], 0))
        server.dev._on_msg(([0xB0, 1, 0x11], 0))
        evs = server.oxi_events(wait_s=0.1)["events"]
        self.assertEqual([(e["type"]) for e in evs], ["pad", "button", "encoder"])
        self.assertEqual((evs[0]["x"], evs[0]["y"]), (3, 1))
        self.assertEqual(evs[1]["name"], "play")
        self.assertEqual(evs[2]["delta"], -1)
        self.assertEqual(server.oxi_events()["events"], [])

    def test_raw_refuses_destructive(self):
        for body in ("03 61", "00 01", "05 03 00 00", "05 04 00"):
            with self.assertRaises(ValueError):
                server.oxi_sysex(body, wait_reply_s=0)
        r = server.oxi_sysex("03 00", wait_reply_s=0.05)
        self.assertEqual(r["replies"][0]["value"], "9.9.9")

    def test_oled_sends_full_dump(self):
        server.oxi_remote(True)
        r = server.oxi_oled(["OXI MCP", "160 BPM"], preview=True)
        self.assertTrue(os.path.isfile(r["preview"]))
        msg = self.out.sent[-1]
        self.assertEqual(len(msg), 6 + 1 + 2048 + 1)
        self.assertTrue(any(msg[7:-1]))

    def test_transport_and_pc(self):
        server.oxi_transport("start")
        server.oxi_select_column(3, channel=16)
        self.assertEqual(self.out.sent[-2:], [b"\xfa", bytes([0xCF, 2])])


@unittest.skipUnless(library.ROOT and (library.ROOT / "projects").is_dir(), "RACK_LIBRARY not set")
class Library(unittest.TestCase):
    def test_projects_and_pattern(self):
        ps = server.oxi_projects()["projects"]
        self.assertTrue(ps)
        slug = ps[0]["slug"]
        pats = server.oxi_pattern(slug)["patterns"]
        p = pats[0]
        one = server.oxi_pattern(slug, p["seq"], p["pat"])["patterns"]
        self.assertEqual(one, [p])

    def test_show_pattern_draws_all_pads(self):
        d = server.dev
        out = FakeOut(d, {})
        d.out, d.inp, d.remote = out, None, True
        try:
            slug = server.oxi_projects()["projects"][0]["slug"]
            for p in server.oxi_pattern(slug)["patterns"]:
                n = len(out.sent)
                first = min([x["i"] for x in p.get("steps", [])] + [h["i"] for t in p.get("tracks", []) for h in t["hits"]],
                            default=0)
                r = server.oxi_show_pattern(slug, p["seq"], p["pat"], page=first // 16)  # a riser can start late
                self.assertEqual(len(out.sent) - n, 128)
                if p.get("steps") or any(t["hits"] for t in p.get("tracks", [])):  # seq 2 pads to 16 with empties
                    self.assertGreater(r["lit"], 0, p["name"])
        finally:
            d.out, d.remote = None, False


# 2026-11-05: a push kept timing out because the server process predated the commit that fixed it; status names
# that state instead of it being found by reading process start times
class Stale(unittest.TestCase):
    def test_status_flags_code_newer_than_the_server(self):
        with mock.patch.object(server, "STARTED", 0.0):
            self.assertIn("restart", server.stale() or "")

    def test_fresh_server_is_not_stale(self):
        import time
        with mock.patch.object(server, "STARTED", time.time() + 60):
            self.assertIsNone(server.stale())


if __name__ == "__main__":
    unittest.main()
