"""The microSD card tools: finding the card, and keeping a rack-library checkout in step with it (the card wins)."""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from oxi_mcp import card, library


def make_card(root):
    for d in card.LAYOUT:
        (Path(root) / d).mkdir(parents=True, exist_ok=True)
    return Path(root)


class Find(unittest.TestCase):
    def test_finds_the_oxi_layout(self):
        with tempfile.TemporaryDirectory() as t:
            make_card(t)
            self.assertEqual(card.find(t), Path(t))
            self.assertIn(Path(t), card.candidates({"OXI_CARD": t}))

    def test_refuses_a_folder_without_the_layout(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "Projects").mkdir()
            with self.assertRaises(ValueError):
                card.find(t)

    def test_card_with_no_projects_says_how_to_get_them(self):
        with tempfile.TemporaryDirectory() as t:
            r = card.listing(make_card(t), lib=False)
            self.assertEqual(r["projects"], [])
            self.assertIn("Copy to SD Card", r["hint"])


SRC = library.ROOT


@unittest.skipUnless(SRC and (SRC / "projects" / "hardfast").is_dir(), "RACK_LIBRARY not set")
class Sync(unittest.TestCase):
    """Runs against a throwaway clone of RACK_LIBRARY (its working tools/ copied over) and a temp card."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.lib = self.tmp / "lib"
        subprocess.run(["git", "clone", "-q", "--depth", "1", SRC.as_uri(), str(self.lib)], check=True)
        for f in (SRC / "tools").glob("*.py"):
            shutil.copy2(f, self.lib / "tools" / f.name)
        subprocess.run(["git", "-C", str(self.lib), "commit", "-qam", "working tools", "--allow-empty"], check=True,
                       env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@t"})
        library.use(self.lib)
        self.card = make_card(self.tmp / "card")
        self.orig = (self.lib / "projects/hardfast/HARDFAST.oxipro").read_bytes()
        (self.card / "Projects" / "HARDFAST.oxipro").write_bytes(self.orig)

    def tearDown(self):
        library.use(SRC)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def state(self, name):
        return {p["file"]: p for p in card.listing(self.card)["projects"]}[name]

    def log(self):
        return subprocess.run(["git", "-C", str(self.lib), "log", "--format=%s", "-3"], capture_output=True,
                              text=True).stdout

    def device_save(self, edit):
        """What saving on the OXI does, as far as the library can tell: the card file changes."""
        oxi = library.tools().oxi
        f = self.card / "Projects" / "HARDFAST.oxipro"
        f.write_bytes(oxi.apply(f.read_bytes(), edit))
        return f.read_bytes()

    def test_same_then_card_differs(self):
        self.assertEqual(self.state("HARDFAST.oxipro")["state"], "same")
        self.assertEqual(self.state("HARDFAST.oxipro")["slug"], "hardfast")
        self.device_save({"seq": 1, "pat": 1, "channel": 12})
        self.assertEqual(self.state("HARDFAST.oxipro")["state"], "card differs")

    def test_pull_takes_the_card_and_keeps_notes(self):
        notes = (self.lib / "projects/hardfast/project.md").read_text(encoding="utf-8").split("## Patterns")[0]
        new = self.device_save({"seq": 1, "pat": 1, "channel": 12})
        r = card.pull(self.card)
        self.assertEqual([(p["slug"], p["action"]) for p in r["pulled"]], [("hardfast", "updated")])
        self.assertEqual((self.lib / "projects/hardfast/HARDFAST.oxipro").read_bytes(), new)
        md = (self.lib / "projects/hardfast/project.md").read_text(encoding="utf-8")
        self.assertTrue(md.startswith(notes.split("updated:")[0]))
        self.assertIn("from the SD card", self.log())
        self.assertEqual(card.pull(self.card)["pulled"], [])  # nothing left to take

    def test_pull_adds_a_new_card_project(self):
        (self.card / "Projects" / "Vox Chop.oxipro").write_bytes(self.orig)
        r = card.pull(self.card)
        self.assertEqual([(p["slug"], p["action"]) for p in r["pulled"]], [("vox-chop", "added")])
        self.assertTrue((self.lib / "projects/vox-chop/Vox Chop.oxipro").is_file())
        self.assertEqual(self.state("Vox Chop.oxipro")["state"], "same")

    def test_edit_pulls_first_and_writes_back(self):
        self.device_save({"seq": 2, "pat": 1, "channel": 11})  # changed on the device, not yet pulled
        r = card.edit(self.card, "hardfast", {"seq": 1, "pat": 1, "channel": 6})
        on_card = (self.card / "Projects" / "HARDFAST.oxipro").read_bytes()
        self.assertEqual(on_card, (self.lib / "projects/hardfast/HARDFAST.oxipro").read_bytes())
        pats = {(p["seq"], p["pat"]): p for p in library.tools().oxi.decode(on_card)["patterns"]}
        self.assertEqual((pats[(1, 1)]["channel"], pats[(2, 1)]["channel"]), (6, 11))  # the device's change survived
        self.assertEqual(r["card"], str(self.card / "Projects" / "HARDFAST.oxipro"))
        self.assertEqual(self.state("HARDFAST.oxipro")["state"], "same")

    def test_tools_edit_on_the_card(self):
        from oxi_mcp import server
        self.assertEqual(server.oxi_card(str(self.card))["projects"][0]["state"], "same")
        r = server.oxi_edit_pattern("hardfast", 1, 1, channel=7, card_path=str(self.card))
        self.assertTrue(r["card"])
        on_card = (self.card / "Projects" / "HARDFAST.oxipro").read_bytes()
        self.assertEqual(library.tools().oxi.decode(on_card)["patterns"][0]["channel"], 7)
        r = server.oxi_edit_pattern("hardfast", 1, 1, channel=8, on_card=False)
        self.assertIsNone(r["card"])
        self.assertEqual(server.oxi_card(str(self.card))["projects"][0]["state"], "card differs")

    def test_write_back_refuses_a_card_that_changed(self):
        with self.assertRaises(ValueError):
            card.write_back(self.card, "hardfast", expect_sha="0" * 64)

    def test_export_adds_but_never_overwrites_a_different_card_file(self):
        (self.card / "Projects" / "HARDFAST.oxipro").unlink()
        self.assertTrue(card.export(self.card, "hardfast")["copied"])
        self.device_save({"seq": 1, "pat": 1, "channel": 12})
        with self.assertRaises(ValueError):
            card.export(self.card, "hardfast")

    def test_export_overwrites_only_when_told(self):
        from oxi_mcp import server
        self.device_save({"seq": 1, "pat": 1, "channel": 12})
        r = server.oxi_export("hardfast", str(self.card), overwrite=True)
        self.assertEqual((self.card / "Projects" / "HARDFAST.oxipro").read_bytes(), self.orig)
        self.assertTrue(r["copied"] and r["overwrote"])


if __name__ == "__main__":
    unittest.main()
