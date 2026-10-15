"""state/nodes.json has two writers: watch (every key, every 30 s) and live (only `live_down`, a
node it could not ship to). Each re-reads under a lock, replaces only its own keys and writes
temp + rename; neither drops the other's write, even when both write at the same moment."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import presence as P  # noqa: E402

WATCH_VIEW = {"updated": "2026-10-15T18:00:00+00:00", "nodes": {"mac": {
    "level": "ready", "since": "2026-10-15T16:00:00+00:00", "checked": "2026-10-15T18:00:00+00:00",
    "observed": "ready", "lower_count": 0, "busy": {"quien": "x"}}}}
MARK = {"at": "2026-10-15T18:00:05+00:00", "reason": "jobrun: Connection timed out"}

CONCURRENT = textwrap.dedent("""
    import json, sys
    from pathlib import Path
    sys.path.insert(0, sys.argv[4])
    import presence as P
    path, who, view = Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
    for i in range(60):
        if who == "watch":
            view["nodes"]["mac"]["lower_count"] = i
            P.write_nodes_watch(path, view)
        else:
            P.write_live_mark(path, "mac", None if i % 2 else {"at": "2026-10-15T18:00:05+00:00", "reason": str(i)})
        json.loads(path.read_text(encoding="utf-8"))
""")


class Ownership(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.path = Path(self.d.name) / "nodes.json"

    def tearDown(self):
        self.d.cleanup()

    def test_watch_keeps_lives_mark_and_live_keeps_watchs_keys(self):
        P.write_nodes_watch(self.path, WATCH_VIEW)
        P.write_live_mark(self.path, "mac", MARK)
        self.assertEqual(P.read_state(self.path)["nodes"]["mac"], dict(WATCH_VIEW["nodes"]["mac"], live_down=MARK))
        later = json.loads(json.dumps(WATCH_VIEW))          # watch read BEFORE live's mark: no live_down
        later["nodes"]["mac"].update(checked="2026-10-15T18:00:30+00:00", busy=None)
        P.write_nodes_watch(self.path, later)
        node = P.read_state(self.path)["nodes"]["mac"]
        self.assertEqual((node["live_down"], node["busy"], node["checked"]),
                         (MARK, None, "2026-10-15T18:00:30+00:00"))

    def test_neither_writes_the_others_keys(self):
        P.write_nodes_watch(self.path, WATCH_VIEW)
        P.write_nodes_watch(self.path, {"updated": "u", "nodes": {"mac": dict(WATCH_VIEW["nodes"]["mac"], live_down=MARK)}})
        self.assertNotIn("live_down", P.read_state(self.path)["nodes"]["mac"])
        P.write_live_mark(self.path, "mac", MARK)
        cur = P.read_state(self.path)
        self.assertEqual(cur["updated"], "u")                  # the top-level stamp is watch's
        self.assertEqual(cur["nodes"]["mac"]["level"], "ready")

    def test_watch_owns_the_node_list(self):
        P.write_nodes_watch(self.path, {"updated": "u", "nodes": {"mac": {"level": "ready"}, "old": {"level": "down"}}})
        P.write_live_mark(self.path, "mac", MARK)
        P.write_nodes_watch(self.path, {"updated": "u2", "nodes": {"mac": {"level": "ready"}}})
        self.assertEqual(list(P.read_state(self.path)["nodes"]), ["mac"])
        P.write_live_mark(self.path, "mac", None)
        self.assertEqual(P.read_state(self.path)["nodes"]["mac"], {"level": "ready"})

    def test_temp_plus_rename_leaves_nothing_behind(self):
        P.write_nodes_watch(self.path, WATCH_VIEW)
        P.write_live_mark(self.path, "mac", MARK)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["nodes.json"])

    def test_a_dead_writers_lock_is_broken(self):
        lock = self.path.with_suffix(".lock")
        lock.write_text("999999")
        os.utime(lock, (0, 0))
        P.write_live_mark(self.path, "mac", MARK)
        self.assertFalse(lock.exists())
        self.assertEqual(P.read_state(self.path)["nodes"]["mac"]["live_down"], MARK)

    def test_level_of(self):
        n = dict(WATCH_VIEW["nodes"]["mac"])
        self.assertEqual(P.level_of(n), "ready")
        self.assertEqual(P.level_of(dict(n, live_down=MARK)), "down")       # newer than watch's probe
        self.assertEqual(P.level_of(dict(n, live_down=dict(MARK, at="2026-10-15T17:59:00+00:00"))), "ready")

    def test_two_processes_writing_at_once_lose_nothing(self):
        """watch rewrites its view 60 times while live sets and clears its mark 60 times, in two
        real processes. The file always parses and both last writes are in it."""
        P.write_nodes_watch(self.path, WATCH_VIEW)
        procs = [subprocess.Popen([sys.executable, "-c", CONCURRENT, str(self.path), who,
                                   json.dumps(WATCH_VIEW), str(ROOT)]) for who in ("watch", "live")]
        self.assertEqual([p.wait(timeout=120) for p in procs], [0, 0])
        node = P.read_state(self.path)["nodes"]["mac"]
        self.assertEqual(node["lower_count"], 59)       # watch's last write
        self.assertNotIn("live_down", node)             # live's last write (a clear)
        self.assertEqual(node["busy"], {"quien": "x"})
        self.assertFalse(self.path.with_suffix(".lock").exists())


if __name__ == "__main__":
    unittest.main()
