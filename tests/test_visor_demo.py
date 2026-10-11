"""Demo mode of the visor (19 §3): a synthetic swarm through the real pipeline, clean by audit."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools" / "visor"))
sys.path.insert(0, str(REPO / "tools" / "publicar"))

import demo  # noqa: E402
import auditar  # noqa: E402

NOW = 1_791_000_000.0      # fixed clock: the demo must be reproducible


class DemoBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name) / "demo"
        cls.res = demo.make(cls.out, now=NOW)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_writes_the_page_and_its_feeds(self):
        for f in ("visor.html", "live.js", "ring.js", "demo-fixture.json"):
            self.assertTrue((self.out / f).exists(), f)

    def test_topology_is_the_real_one_with_generic_names(self):
        self.assertEqual(self.res["nodes"], ["pc", "mac", "laptop", "hosting"])

    def test_projects_are_invented(self):
        self.assertIn("proyecto-a", self.res["projects"])
        self.assertIn("tienda-demo", self.res["projects"])
        self.assertFalse(any(p.startswith("inicio-") for p in self.res["projects"]))

    def test_page_is_flagged_demo_and_carries_the_clock_shim(self):
        html = (self.out / "visor.html").read_text(encoding="utf-8")
        self.assertIn('"demo": true', html)
        self.assertIn("demo · datos sintéticos", html)
        self.assertLess(html.index("Date.now"), html.index("<body"))

    def test_same_seed_same_page(self):
        other = Path(self.tmp.name) / "again"
        demo.make(other, now=NOW)
        a = json.loads((self.out / "demo-fixture.json").read_text(encoding="utf-8"))
        b = json.loads((other / "demo-fixture.json").read_text(encoding="utf-8"))
        self.assertEqual(a, b)

    def test_no_paths_users_or_addresses(self):
        for f in self.out.iterdir():
            text = f.read_text(encoding="utf-8")
            self.assertNotIn("/Users/", text, f.name)
            self.assertNotIn("C:\\\\Users\\\\", text, f.name)
            self.assertNotIn("C:\\Users\\", text, f.name)

    def test_generated_output_passes_the_hard_audit(self):
        found = auditar.audit_tree(self.out)
        self.assertEqual(found, [], found[:10])


class StaticExport(unittest.TestCase):
    """`visor --demo --export`: the public demo for a shared host, nothing that runs on the server."""

    def test_writes_exactly_the_static_files(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "demo"
            self.assertEqual(demo.export(out, now=NOW), 0)
            self.assertEqual(sorted(p.name for p in out.iterdir()), sorted(demo.WEB_FILES))
            self.assertTrue((Path(d) / "demo.zip").is_file())
            html = (out / "index.html").read_text(encoding="utf-8")
            self.assertIn("demo · datos sintéticos", html)
            self.assertNotIn("<?php", html)
            self.assertIn("Options -Indexes", (out / ".htaccess").read_text(encoding="utf-8"))
            self.assertEqual(demo.export(out, now=NOW), 0)          # rebuilding its own folder is fine

    def test_refuses_a_folder_it_did_not_build(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "demo"
            out.mkdir()
            (out / "notas.txt").write_text("x", encoding="utf-8")
            self.assertEqual(demo.export(out, now=NOW), 2)
            self.assertTrue((out / "notas.txt").exists())


class NoLandingNextDoor(unittest.TestCase):
    def test_os_marks_come_from_the_vendored_copy(self):
        """A public clone (or the Mac) has no Landing/node_modules: the Apple chip was a blank square."""
        import build
        with tempfile.TemporaryDirectory() as d:
            icons = build.os_icons(Path(d))
        self.assertIn("<path d=", icons["os-darwin"]["svg"])
        self.assertIn("<path d=", icons["os-linux"]["svg"])


if __name__ == "__main__":
    unittest.main()
