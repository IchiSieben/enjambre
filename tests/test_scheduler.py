"""Prompt 20: the scheduler's rules over recorded telemetry (tests/fixtures/scheduler: the Mac and
the PC as live.json read them on 2026-10-10; the ring tail of the Mac). No system command runs."""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import presence as P  # noqa: E402
import scheduler as S  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "scheduler"
CFG = S.load()
LIMA = dt.timezone(dt.timedelta(hours=-5))
NINE_PM = dt.datetime(2026, 10, 10, 21, 0, tzinfo=LIMA).timestamp()
ONE_AM = dt.datetime(2026, 10, 11, 1, 0, tzinfo=LIMA).timestamp()
MAC = json.loads((FIX / "tele-mac-render.json").read_text(encoding="utf-8"))
WIN = json.loads((FIX / "tele-win.json").read_text(encoding="utf-8"))


def ring(now, n=20, step=10, **f):
    return [dict({"at": dt.datetime.fromtimestamp(now - a, LIMA).isoformat(), "pr": "normal", "swap": 3.4}, **f)
            for a in range(n * step, -1, -step)]


def admit(job, running=(), tele=MAC, age=5, rng=None, now=NINE_PM, os_name="macos", max_jobs=2):
    return S.admit(job, os_name, list(running), max_jobs, tele, age, rng if rng is not None else ring(now),
                   CFG, now)


class Window(unittest.TestCase):
    def test_closed_at_nine_says_when_it_opens(self):
        ok, why = S.window(CFG, NINE_PM)
        self.assertFalse(ok)
        self.assertIn("abre mañana a las 00:00", why)

    def test_open_at_one_says_when_it_closes(self):
        self.assertEqual(S.window(CFG, ONE_AM), (True, "ventana nocturna abierta hasta las 07:00"))

    def test_edges(self):
        seven = dt.datetime(2026, 10, 11, 7, 0, tzinfo=LIMA).timestamp()
        self.assertFalse(S.window(CFG, seven)[0])
        self.assertTrue(S.window(CFG, seven - 60)[0])
        self.assertIn("abre mañana a las 00:00", S.window(CFG, seven)[1])   # just closed: tomorrow

    def test_unknown_zone_is_closed_not_guessed(self):
        cfg = dict(CFG, nocturna=dict(CFG["nocturna"], tz="Nowhere/Atlantis"))
        ok, why = S.window(cfg, ONE_AM)
        self.assertFalse(ok)
        self.assertIn("ilegible", why)


class Admit(unittest.TestCase):
    def test_nocturna_at_nine_waits_for_the_window(self):
        ok, state, why = admit({"id": "n", "priority": "nocturna"})
        self.assertEqual((ok, state), (False, "waiting_window"))
        self.assertIn("00:00", why)
        self.assertTrue(admit({"id": "n", "priority": "nocturna"}, now=ONE_AM)[0])

    def test_baja_waits_while_alta_or_normal_runs(self):
        ok, state, why = admit({"id": "b", "priority": "baja"},
                               running=[{"id": "landing-media", "priority": "alta", "state": "running"}])
        self.assertEqual((ok, state), (False, "waiting_resources"))
        self.assertIn("landing-media (alta)", why)
        # a dead or finished job holds nothing
        self.assertTrue(admit({"id": "b", "priority": "baja"},
                              running=[{"id": "x", "priority": "normal", "state": "dead"}])[0])

    def test_baja_on_a_quiet_mac_is_admitted(self):
        ok, state, why = admit({"id": "b", "priority": "baja"})
        self.assertTrue(ok, why)

    def test_memory_pressure_and_growing_swap_hold_baja(self):
        hot = dict(MAC, mem=dict(MAC["mem"], pressure="warn"))
        ok, _, why = admit({"id": "b", "priority": "baja"}, tele=hot, rng=ring(NINE_PM, pr="warn"))
        self.assertFalse(ok)
        self.assertIn("presión de memoria warn desde 20:56", why)
        grow = ring(NINE_PM, swap=3.4)
        grow[-1]["swap"] = 4.2
        ok, _, why = admit({"id": "b", "priority": "baja"}, rng=grow)
        self.assertFalse(ok)
        self.assertIn("swap creciendo", why)

    def test_no_fresh_telemetry_no_baja(self):
        ok, _, why = admit({"id": "b", "priority": "baja"}, age=600)
        self.assertFalse(ok)
        self.assertIn("sin telemetría fresca", why)
        self.assertTrue(admit({"id": "a", "priority": "normal"}, age=600)[0])   # only low classes wait

    def test_windows_uses_available_percent(self):
        self.assertTrue(admit({"id": "b", "priority": "baja"}, tele=WIN, os_name="windows")[0])   # 40 % free
        full = dict(WIN, mem=dict(WIN["mem"], load_pct=85))
        ok, _, why = admit({"id": "b", "priority": "baja"}, tele=full, os_name="windows")
        self.assertFalse(ok)
        self.assertIn("15 % < 30 %", why)

    def test_slots_and_alta_over_max_jobs(self):
        two_low = [{"id": "b1", "priority": "baja", "state": "running"},
                   {"id": "b2", "priority": "baja", "state": "paused_by_priority"}]
        ok, state, why = admit({"id": "n", "priority": "normal"}, running=two_low)
        self.assertEqual((ok, state), (False, "waiting_resources"))
        self.assertIn("2/2", why)
        ok, _, why = admit({"id": "a", "priority": "alta"}, running=two_low)
        self.assertTrue(ok)
        self.assertIn("por encima de max_jobs", why)
        three = two_low + [{"id": "a0", "priority": "alta", "state": "running"}]
        self.assertFalse(admit({"id": "a", "priority": "alta"}, running=three)[0])


class Pick(unittest.TestCase):
    def test_pick_orders_by_priority_and_writes_the_reason(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            P.enqueue(root, "b1", "x", {"node": "mac", "priority": "baja"})
            P.enqueue(root, "n1", "x", {"node": "mac", "priority": "nocturna"})
            P.enqueue(root, "a1", "x", {"node": "mac", "priority": "alta"})
            seen = []

            def fn(meta, node, taken, cap):
                seen.append(meta["id"])
                return admit(meta, running=[dict(t, state="running") for t in taken])
            got = P.pick(root, {"mac": "ready"}, {"mac": 2}, fn)
            self.assertEqual(seen, ["a1", "b1", "n1"])
            self.assertEqual([m["id"] for m in got], ["a1"])     # b1 waits: a1 (alta) was just admitted
            b1 = P.read_state(root / "queue" / "b1" / "job.json")
            self.assertEqual(b1["state"], "waiting_resources")
            self.assertIn("a1 (alta)", b1["reason"])
            n1 = P.read_state(root / "queue" / "n1" / "job.json")
            self.assertEqual(n1["state"], "waiting_window")

    def test_without_admit_fn_it_is_the_old_pick(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            P.enqueue(root, "j1", "x", {"node": "mac"})
            self.assertEqual([m["id"] for m in P.pick(root, {"mac": "ready"}, {"mac": 1})], ["j1"])


if __name__ == "__main__":
    unittest.main()
