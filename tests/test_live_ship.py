"""live ships collect.py and jobrun.py to each node without ever holding its loop.

2026-10-15: the Mac went down right after `live` restarted; ship_collector's scp had no timeout
and live sat 3.5 min before its first cycle. Now one copy is bounded (ConnectTimeout, timeout),
what did not ship is retried from the loop with growing waits, and a node that does not answer
is marked `down` in nodes.json at once; watch takes that mark as one more lower observation.
"""
import json
import logging
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import live as LV  # noqa: E402
import presence as P  # noqa: E402

UNREACHABLE = ["enjambre-unreachable.invalid", "192.0.2.1"]   # no DNS / TEST-NET-1 (RFC 5737)


class Clock:
    def __init__(self):
        self.lines = []

    def log(self, s):
        self.lines.append(s)


class UnreachableHost(unittest.TestCase):
    def test_scp_to_an_unreachable_host_fails_fast_and_reads_as_down(self):
        for host in UNREACHABLE:
            t = time.time()
            err = LV.ship_collector(host, timeout=4)
            took = time.time() - t
            self.assertIsNotNone(err, host)
            self.assertLess(took, 8, f"{host}: {took:.1f} s")
            self.assertRegex(err, LV.DOWN_ERR, host)

    def test_the_loop_is_not_held_and_the_node_is_marked_down(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nodes.json"
            P.write_json_atomic(path, {"updated": P.now_iso(0), "nodes": {"mac": {
                "level": "ready", "since": "2026-10-15T10:00:00+00:00", "checked": P.now_iso(0),
                "observed": "ready", "lower_count": 0}}})
            c = Clock()
            ship = LV.Shipper({"mac": {"host": UNREACHABLE[1]}, "win": {"host": "local"}},
                              [("collector", lambda h: LV.ship_collector(h, timeout=4), lambda n, h: "collector shipped"),
                               ("jobrun", lambda h: self.fail("tried after the node was down"), lambda n, h: "")],
                              log=c.log, mark=lambda n, r, t: LV.mark_down(n, r, t, path))
            t0 = time.time()
            ship.tick(t0)
            self.assertLess(time.time() - t0, 8)
            node = P.read_state(path)["nodes"]["mac"]
            self.assertEqual((node["level"], node["down_by"], node["prior_level"]), ("down", "live", "ready"))
            self.assertIn("collector:", node["down_reason"])
            self.assertEqual(ship.pending(), {"mac": ["collector", "jobrun"]})
            self.assertIn("retry in 5 s, marked down", c.lines[-1])


class Backoff(unittest.TestCase):
    def test_waits_grow_and_a_node_back_gets_everything(self):
        up = {"v": False}
        sent = []

        def fn(name):
            def ship(host):
                if not up["v"]:
                    return "ssh: connect to host mac port 22: Connection timed out"
                sent.append(name)
                return None
            return ship
        marks, c = [], Clock()
        ship = LV.Shipper({"mac": {"host": "mac"}},
                          [("collector", fn("collector"), lambda n, h: "collector shipped, ring backfill 0 lines"),
                           ("jobrun", fn("jobrun"), lambda n, h: "jobrun shipped")],
                          log=c.log, mark=lambda n, r, t: marks.append((n, t)))
        ship.tick(0)
        ship.tick(4)                       # before the 5 s wait: nothing
        ship.tick(5)
        ship.tick(15)
        self.assertEqual([t for _, t in marks], [0, 5, 15])
        self.assertEqual(ship.next_t["mac"], 15 + 20)
        ship.tick(40, reachable=lambda n: False)   # the loop's own ssh says the node is still down
        self.assertEqual(len(marks), 3)
        up["v"] = True
        ship.tick(40)
        self.assertEqual(sent, ["collector", "jobrun"])
        self.assertEqual(ship.pending(), {})
        self.assertEqual(c.lines[-1], "node mac: jobrun shipped")
        ship.tick(1000)
        self.assertEqual(sent, ["collector", "jobrun"])

    def test_a_file_error_is_not_a_node_down(self):
        marks, sent = [], []
        ship = LV.Shipper({"mac": {"host": "mac"}},
                          [("collector", lambda h: "scp: .enjambre/visor/collect.py: Permission denied", lambda n, h: ""),
                           ("jobrun", lambda h: sent.append(h), lambda n, h: "jobrun shipped")],
                          log=lambda s: None, mark=lambda n, r, t: marks.append(n))
        ship.tick(0)
        self.assertEqual((marks, sent, ship.pending()), ([], ["mac"], {"mac": ["collector"]}))


class WatchReadsTheMark(unittest.TestCase):
    def run_tick(self, observed):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nodes.json"
            P.write_json_atomic(path, {"nodes": {"mac": {
                "level": "ready", "since": "2026-10-15T10:00:00+00:00", "checked": "x",
                "observed": "ready", "lower_count": 0}}})
            LV.mark_down("mac", "collector: Connection timed out", 100, path)
            LV.mark_down("mac", "jobrun: Connection timed out", 110, path)   # a second mark keeps the prior
            prev = P.read_state(path)
        notes = []
        log = logging.getLogger("test-live-ship")
        out = P.tick({"mac": {"host": "mac"}}, prev, lambda h: observed, 130,
                     lambda *m: notes.append(m), log)
        return out["nodes"]["mac"], notes

    def test_node_up_for_watch_undoes_the_mark_quietly(self):
        node, notes = self.run_tick("ready")
        self.assertEqual((node["level"], node["since"], notes), ("ready", "2026-10-15T10:00:00+00:00", []))
        self.assertFalse({"down_by", "down_reason", "prior_level", "prior_since"} & set(node))

    def test_node_down_for_watch_too_drops_now_and_notifies(self):
        node, notes = self.run_tick("down")
        self.assertEqual(node["level"], "down")
        self.assertEqual(len(notes), 1)
        self.assertNotIn("down_by", node)


if __name__ == "__main__":
    unittest.main()
