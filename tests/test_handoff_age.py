"""Prompt 21 §5: the visor shows each session's real HANDOFF age (the statusline only redraws on
session events, so its age froze). collect.py finds the same HANDOFF.md the bar judges."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "visor"))
import collect as C  # noqa: E402


class HandoffMtime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "src" / "deep").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_found_from_a_subfolder_at_the_git_root(self):
        h = self.repo / "HANDOFF.md"
        h.write_text("# h")
        os.utime(h, (1_700_000_000, 1_700_000_000))
        self.assertEqual(C.handoff_mtime(str(self.repo / "src" / "deep")), 1_700_000_000)

    def test_cwd_own_handoff_wins(self):
        (self.repo / "HANDOFF.md").write_text("# root")
        own = self.repo / "src" / "HANDOFF.md"
        own.write_text("# own")
        os.utime(own, (1_600_000_000, 1_600_000_000))
        self.assertEqual(C.handoff_mtime(str(self.repo / "src")), 1_600_000_000)

    def test_none_without_handoff_and_stops_at_the_git_root(self):
        # a HANDOFF.md ABOVE the repo belongs to another project: never borrowed
        (Path(self.tmp.name) / "HANDOFF.md").write_text("# outside")
        self.assertIsNone(C.handoff_mtime(str(self.repo / "src")))
        self.assertIsNone(C.handoff_mtime(None))


if __name__ == "__main__":
    unittest.main()
