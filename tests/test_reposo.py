"""Reposo (prompt 19 step 4): the rules over synthetic ring series, and routing that respects them.
Series are synthetic (no real pressure event was recorded on 2026-10-10); the thresholds are the
ones in ~/.claude/rules/thresholds.json, passed in here so the test does not depend on that file."""
import json
import sys
import tempfile
import unittest
import unittest.mock as um
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import enjambre as E  # noqa: E402
import live as LV  # noqa: E402
import routing as R  # noqa: E402

NOW = 1_800_000_000.0
THR = {"visor-reposo-pressure-s": 300, "visor-reposo-thermal-s": 120, "visor-reposo-swap-gb": 1,
       "visor-reposo-swap-window-s": 600}


def series(seconds: int, step: int = 10, **fields):
    """Ring lines every `step` s for the last `seconds` s; a field may be a value or f(age_s)."""
    out = []
    for age in range(seconds, -1, -step):
        ln = {"at": LV.iso(NOW - age)}
        for k, v in fields.items():
            ln[k] = v(age) if callable(v) else v
        out.append(ln)
    return out


def ids(rules):
    return [r["id"] for r in rules]


class Rules(unittest.TestCase):
    def test_pressure_needs_five_sustained_minutes(self):
        self.assertEqual(LV.reposo_rules(series(240, pr="warn"), THR, NOW), [])     # 4 min: not yet
        r = LV.reposo_rules(series(360, pr="warn"), THR, NOW)
        self.assertEqual(ids(r), ["presion"])
        self.assertTrue(r[0]["no_dispatch"])
        self.assertGreaterEqual(r[0]["held_s"], 300)
        self.assertIn("no despachar", r[0]["msg"])

    def test_one_normal_line_or_a_gap_restarts_the_clock(self):
        s = series(600, pr="warn")
        s[-10]["pr"] = "normal"                       # 90 s ago it was fine
        self.assertEqual(LV.reposo_rules(s, THR, NOW), [])
        s = series(600, pr="warn")
        del s[-20:-8]                                 # live loop off for 2 min (> RING_GAP_S)
        self.assertEqual(LV.reposo_rules(s, THR, NOW), [])

    def test_a_slow_node_read_every_minute_still_trips_it(self):
        self.assertEqual(ids(LV.reposo_rules(series(650, step=65, pr="warn"), THR, NOW)), ["presion"])

    def test_unknown_pressure_is_not_pressure(self):
        self.assertEqual(LV.reposo_rules(series(600, pr=None), THR, NOW), [])

    def test_an_old_ring_says_nothing_about_now(self):
        s = series(600, pr="critical")
        self.assertEqual(LV.reposo_rules(s, THR, NOW + 600), [])

    def test_thermal_after_two_minutes_and_null_is_not_throttled(self):
        self.assertEqual(ids(LV.reposo_rules(series(150, csl=70), THR, NOW)), ["termico"])
        self.assertEqual(LV.reposo_rules(series(90, csl=70), THR, NOW), [])
        self.assertEqual(LV.reposo_rules(series(600, csl=None), THR, NOW), [])
        self.assertEqual(LV.reposo_rules(series(600, csl=100), THR, NOW), [])

    def test_swap_growing_one_gb_in_ten_minutes(self):
        r = LV.reposo_rules(series(600, swap=lambda age: 4.0 + (600 - age) / 400), THR, NOW)
        self.assertEqual(ids(r), ["swap"])
        self.assertFalse(r[0]["no_dispatch"])
        self.assertEqual(LV.reposo_rules(series(600, swap=4.4), THR, NOW), [])        # flat
        self.assertEqual(LV.reposo_rules(series(600, swap=lambda age: 6.0 - (600 - age) / 400),
                                         THR, NOW), [])                               # shrinking

    def test_no_thresholds_no_verdict(self):
        self.assertIsNone(LV.reposo_rules(series(600, pr="warn"), None, NOW))
        self.assertIsNone(LV.reposo_rules(series(600, pr="warn"), {"visor-reposo-pressure-s": 300}, NOW))


THREE = {"mac": {"host": "mac", "max_jobs": 2, "tags": ["headless"], "owned": True},
         "laptop": {"host": "laptop", "max_jobs": 1, "tags": ["headless"], "owned": True},
         "win": {"host": "local", "max_jobs": 2, "tags": ["headless"], "owned": True, "runnable": False}}
CFG = {"defaults": {}, "kinds": {"claude": {"prefer": ["mac", "laptop", "win"]}}}
UP = {"mac": "ready", "laptop": "ready", "win": "local"}


def live_with(node, rules, age=5):
    return {"updated_ts": NOW - age, "reposo": {node: rules}}


class Routing(unittest.TestCase):
    PRESSURE = [{"id": "presion", "no_dispatch": True, "since": "t", "msg": "sin memoria: presión warn"}]

    def mem_for(self, live, nodes):
        return {n: R.reposo_busy(live, n, NOW) or (False, "") for n in nodes}

    def test_pressure_sends_the_job_to_the_other_node_and_says_why(self):
        d = R.route("claude", "claude", "/r", THREE, CFG, UP, {}, self.mem_for(live_with("mac", self.PRESSURE), THREE), {})
        self.assertEqual((d["action"], d["node"], d["diverted_from"]), ("run", "laptop", "mac"))
        self.assertIn("reposo: sin memoria", d["reason"])

    def test_with_only_the_mac_runnable_it_queues_and_says_why(self):
        two = {k: v for k, v in THREE.items() if k != "laptop"}
        d = R.route("claude", "claude", "/r", two, CFG, UP, {}, self.mem_for(live_with("mac", self.PRESSURE), two), {})
        self.assertEqual((d["action"], d["node"]), ("queue", "mac"))
        self.assertIn("reposo: sin memoria", d["reason"])

    def test_stale_live_json_never_blocks(self):
        self.assertIsNone(R.reposo_busy(live_with("mac", self.PRESSURE, age=600), "mac", NOW))
        self.assertIsNone(R.reposo_busy({}, "mac", NOW))
        self.assertIsNone(R.reposo_busy(None, "mac", NOW))

    def test_warning_rules_do_not_block(self):
        hot = [{"id": "termico", "no_dispatch": False, "msg": "estrangulando"}]
        self.assertIsNone(R.reposo_busy(live_with("mac", hot), "mac", NOW))


class Decide(unittest.TestCase):
    """enjambre.decide reads state/visor/live.json: point it at a fixture instead."""

    def test_decide_reads_live_json(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "live.json").write_text(json.dumps(
                {"updated_ts": NOW - 5, "reposo": {"mac": Routing.PRESSURE}}), encoding="utf-8")
            with um.patch.object(LV, "OUT", Path(d)), \
                    um.patch.object(E.P, "running_count", return_value=0), \
                    um.patch.object(E, "LEDGER", Path(d, "none.jsonl")):
                dec, _ = E.decide("claude", "claude", Path(d), THREE, CFG, NOW,
                                  probe_fn=lambda host: "ready", health_fn=lambda host: {})
        self.assertEqual(dec["node"], "laptop")
        self.assertIn("reposo", dec["reason"])


if __name__ == "__main__":
    unittest.main()
