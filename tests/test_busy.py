"""Tests for the busy marker: busy.py parsing, the router's filter (b) with it, and tools/busy.sh
run under the local bash (Git Bash on Windows, bash 3.2+ on the Mac).

    python -m unittest discover -s tests -v
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import busy as B  # noqa: E402
import routing as R  # noqa: E402

CFG = R.load(HERE / "routing.toml")


def nodes(**over):
    n = {"win": {"host": "local", "tags": ["headless", "display", "keychain"], "ram_gb": 48,
                 "max_jobs": 2, "owned": True},
         "mac": {"host": "mac", "tags": ["headless", "keychain", "metal", "mlx"], "ram_gb": 16,
                 "max_jobs": 2, "owned": True}}
    for name, patch in over.items():
        n[name] = dict(n[name], **patch)
    return n


def route(kind, ns=None, busy=None, cfg=CFG):
    ns = ns or nodes()
    levels = {n: ("local" if c["host"] == "local" else "ready") for n, c in ns.items()}
    return R.route(kind, "claude", "/p/proj", ns, cfg, levels, {}, {}, {}, None, busy)

BENCH = {"quien": "sesion-g", "desde": "2026-10-07T01:40:00-0500", "tipo": "bench", "pid": 46464}


class Parse(unittest.TestCase):
    def test_no_marker(self):
        self.assertIsNone(B.parse(""))

    def test_alive_and_stale(self):
        self.assertTrue(B.parse(json.dumps(BENCH) + "\nALIVE\n")["alive"])
        stale = B.parse(json.dumps(BENCH) + "\n")
        self.assertEqual((stale["alive"], stale["tipo"]), (False, "bench"))

    def test_garbage_is_not_busy(self):
        self.assertFalse(B.parse("{half a json\nALIVE")["alive"])

    def test_section_after_mark(self):
        out = '{"running": null}\n' + B.MARK + "\n" + json.dumps(BENCH) + "\nALIVE\n"
        self.assertTrue(B.parse(B.section(out))["alive"])
        self.assertEqual(B.section("no mark here"), "")


class RouterFilterB(unittest.TestCase):
    live = dict(BENCH, alive=True)

    def test_render_on_a_busy_mac_goes_to_cpu_and_says_why(self):
        d = route("render", busy={"mac": self.live})
        self.assertEqual((d["action"], d["node"], d.get("device")), ("run", "mac", "CPU"))
        self.assertIn("on CPU, not Metal: busy marker, bench by sesion-g", d["reason"])

    def test_render_without_fallback_queues_and_says_why(self):
        cfg = dict(CFG, kinds=dict(CFG["kinds"], render={"needs": ["metal"], "prefer": ["mac"]}))
        d = route("render", busy={"mac": self.live}, cfg=cfg)
        self.assertEqual((d["action"], d["node"]), ("queue", "mac"))
        self.assertIn("busy marker, bench by sesion-g", d["reason"])

    def test_transcribe_waits_for_the_bench(self):
        d = route("transcribe", busy={"mac": self.live})
        self.assertEqual(d["action"], "queue")

    def test_light_kinds_ignore_the_marker(self):
        d = route("tests", busy={"mac": self.live})
        self.assertEqual((d["node"], d.get("device")), ("mac", None))

    def test_stale_marker_is_ignored(self):
        d = route("render", busy={"mac": dict(BENCH, alive=False)})
        self.assertEqual((d["node"], d.get("device")), ("mac", None))

    def test_a_fully_free_node_beats_a_cpu_only_one(self):
        ns = nodes(win={"tags": ["headless", "display", "keychain", "metal"]})
        d = route("render", ns=ns, busy={"mac": self.live})
        self.assertEqual((d["node"], d.get("device")), ("win", None))


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class BusySh(unittest.TestCase):
    """The real script, with HOME pointed at a temp dir."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = dict(os.environ, HOME=str(self.home).replace("\\", "/"))
        self.f = self.home / ".enjambre/busy.json"

    def tearDown(self):
        self.tmp.cleanup()

    def sh(self, *args, timeout=30):
        script = str(HERE / "tools/busy.sh").replace("\\", "/")
        return subprocess.run(["bash", script, *args], env=self.env, capture_output=True,
                              text=True, timeout=timeout)

    def test_run_writes_the_marker_during_the_job_and_removes_it_after(self):
        r = self.sh("run", "bench", "t1", "--", "bash", "-c", "cat \"$HOME/.enjambre/busy.json\"")
        self.assertEqual(r.returncode, 0, r.stderr)
        seen = json.loads(r.stdout)
        self.assertEqual((seen["tipo"], seen["quien"]), ("bench", "t1"))
        self.assertFalse(self.f.exists(), "trap removed it")

    def test_failing_job_still_removes_it_and_keeps_its_exit_code(self):
        r = self.sh("run", "ltx", "t2", "--", "bash", "-c", "exit 7")
        self.assertEqual(r.returncode, 7)
        self.assertFalse(self.f.exists())

    def test_nested_job_leaves_the_outer_marker(self):
        inner = f"bash '{str(HERE / 'tools/busy.sh').replace(chr(92), '/')}' run render inner -- true; " \
                "cat \"$HOME/.enjambre/busy.json\""
        r = self.sh("run", "bench", "outer", "--", "bash", "-c", inner)
        self.assertEqual(json.loads(r.stdout)["quien"], "outer")
        self.assertFalse(self.f.exists())

    def test_clear_refuses_a_live_marker_and_removes_a_stale_one(self):
        self.f.parent.mkdir(parents=True)
        self.f.write_text(json.dumps(dict(BENCH, pid=999999)), encoding="utf-8")
        self.assertEqual(self.sh("clear").returncode, 0)
        self.assertFalse(self.f.exists())

    def test_read_cmd_reports_alive_only_for_a_running_pid(self):
        self.f.parent.mkdir(parents=True)
        self.f.write_text(json.dumps(dict(BENCH, pid=999999)), encoding="utf-8")
        r = subprocess.run(["bash", "-c", B.READ_CMD], env=self.env, capture_output=True, text=True)
        self.assertFalse(B.parse(r.stdout)["alive"])
        r = subprocess.run(["bash", "-c", B.READ_CMD], env=dict(self.env, HOME=str(self.home / "none")),
                           capture_output=True, text=True)
        self.assertIsNone(B.parse(r.stdout))


import enjambre as E  # noqa: E402
import presence as P  # noqa: E402


class Wiring(unittest.TestCase):
    live = dict(BENCH, alive=True)

    def test_render_device_auto_falls_back_to_cpu_only_when_held(self):
        held = lambda h: {"busy": self.live}  # noqa: E731
        self.assertEqual(E.render_device("auto", "mac", held)[0], "CPU")
        self.assertIn("bench by sesion-g", E.render_device("auto", "mac", held)[1])
        self.assertEqual(E.render_device("auto", "mac", lambda h: {"busy": None})[0], "METAL")
        self.assertEqual(E.render_device("auto", "mac", lambda h: None)[0], "METAL")
        dev, why = E.render_device("METAL", "mac", held)
        self.assertEqual(dev, "METAL")
        self.assertIn("forced", why)
        own = lambda h: {"busy": dict(self.live, tipo="render")}  # noqa: E731
        self.assertEqual(E.render_device("auto", "mac", own)[0], "METAL", "a render does not block a render")

    def test_host_override_is_per_process_and_validated(self):
        saved = dict(os.environ)
        try:
            os.environ["ENJAMBRE_HOST_MAC"] = "mac6"
            self.assertEqual(E.load_nodes()["mac"]["host"], "mac6")
            self.assertTrue(E.load_nodes()["mac"]["host_override"], "decide must not trust nodes.json")
            self.assertEqual(E.load_nodes()["win"]["host"], "local")
            os.environ["ENJAMBRE_HOST_MAC"] = "mac; rm -rf ~"
            self.assertEqual(E.load_nodes()["mac"]["host"], "mac")
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_decide_reads_the_marker_in_the_same_live_call(self):
        calls = []

        def health(host):
            calls.append(host)
            return {"running": None, "free_pct": 50, "busy": self.live}
        saved = (E.NODES_JSON, E.VISOR_SNAPSHOT, E.LEDGER, E.STATE)
        with tempfile.TemporaryDirectory() as d:
            E.NODES_JSON = E.VISOR_SNAPSHOT = E.LEDGER = Path(d) / "none"
            E.STATE = Path(d)
            try:
                dec, _ = E.decide("transcribe", "claude", HERE, nodes(), CFG, time.time(),
                                  probe_fn=lambda h: "ready", health_fn=health)
            finally:
                E.NODES_JSON, E.VISOR_SNAPSHOT, E.LEDGER, E.STATE = saved
        self.assertEqual(calls, ["mac"], "one ssh per node, memory and busy together")
        self.assertEqual(dec["action"], "queue")
        self.assertIn("busy marker", dec["reason"])

    def test_tick_stores_the_marker_and_keeps_it_between_full_rounds(self):
        import logging
        ns = {"mac": {"host": "mac"}}
        log = logging.getLogger("t")
        st = P.tick(ns, {}, lambda h: "ready", 0.0, lambda *m: None, log, None, lambda h: self.live)
        self.assertEqual(st["nodes"]["mac"]["busy"]["tipo"], "bench")
        st = P.tick(ns, st, lambda h: "ready", 30.0, lambda *m: None, log, None, lambda h: P.KEEP)
        self.assertEqual(st["nodes"]["mac"]["busy"]["tipo"], "bench")
        st = P.tick(ns, st, lambda h: "ready", 60.0, lambda *m: None, log, None, lambda h: None)
        self.assertNotIn("busy", st["nodes"]["mac"])


if __name__ == "__main__":
    unittest.main()
