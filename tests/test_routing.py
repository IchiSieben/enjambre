"""Tests for the router: every branch of routing.route with simulated nodes, plus the wiring in
`enjambre run` (dry run, queue, ledger fields, watch children). No network, no model.

    python -m unittest discover -s tests -v
"""
import json
import re
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import enjambre as E  # noqa: E402
import routing as R  # noqa: E402

CFG = R.load(E.ROUTING)  # the real table: the tests also guard what the owner reads
SENS = re.compile(r"confidencial|reservado", re.I)


def nodes(**over):
    n = {
        "win": {"host": "local", "tags": ["headless", "display", "keychain", "smb"], "ram_gb": 48,
                "max_jobs": 2, "owned": True, "workers": ["claude"]},
        "mac": {"host": "mac", "tags": ["headless", "keychain", "metal", "mlx", "smb"], "ram_gb": 16,
                "max_jobs": 2, "owned": True, "workers": ["claude"]},
    }
    for name, patch in over.items():
        n[name] = dict(n.get(name, {}), **patch)
    return n


def route(kind="tests", worker="claude", repo="/p/proj", ns=None, levels=None, running=None,
          mem=None, hist=None, cfg=CFG):
    ns = ns or nodes()
    levels = levels or {n: ("local" if c["host"] == "local" else "ready") for n, c in ns.items()}
    return R.route(kind, worker, repo, ns, cfg, levels, running or {}, mem or {}, hist or {}, SENS)


def runs(sec, n=5):
    return [float(sec)] * n


class HardFilters(unittest.TestCase):
    def test_capability_missing_excludes_the_node(self):
        d = route("render")
        self.assertEqual((d["action"], d["node"]), ("run", "mac"))
        self.assertIn("win: lacks metal", d["trace"])

    def test_gui_goes_to_the_only_node_with_a_display(self):
        self.assertEqual(route("gui")["node"], "win")

    def test_numeric_need_uses_ram_gb_and_unmeasured_ram_fails_it(self):
        self.assertEqual(route("video-edit")["node"], "win")
        d = route("video-edit", ns=nodes(win={"ram_gb": 0}))
        self.assertEqual(d["action"], "refuse")
        self.assertIn("ram>=32", d["reason"])

    def test_worker_needs_are_added_to_the_kind(self):
        d = route("tests", ns=nodes(mac={"tags": ["headless"]}))
        self.assertEqual(d["node"], "win")
        self.assertIn("mac: lacks keychain", d["trace"])
        self.assertEqual(route("tests", worker="stub-ok", ns=nodes(mac={"tags": ["headless"]}))["node"], "mac")

    def test_not_runnable_is_never_chosen(self):
        d = route("gui", ns=nodes(win={"runnable": False}))
        self.assertEqual(d["action"], "refuse")
        self.assertIn("no executor", d["reason"])

    def test_unknown_kind_is_refused(self):
        d = route("cooking")
        self.assertEqual(d["action"], "refuse")
        self.assertIn("tests", d["reason"])


class SensitiveRule(unittest.TestCase):
    def test_confidencial_repo_skips_a_node_that_is_not_owned(self):
        ns = nodes(cloud={"host": "cloud", "tags": ["headless", "keychain"], "ram_gb": 64,
                          "max_jobs": 4, "owned": False})
        cfg = dict(CFG, kinds=dict(CFG["kinds"], tests={"needs": ["headless"], "prefer": ["cloud", "mac"]}))
        self.assertEqual(route("tests", ns=ns, cfg=cfg)["node"], "cloud")
        d = route("tests", repo="/p/datos-reservado", ns=ns, cfg=cfg)
        self.assertEqual(d["node"], "mac")
        self.assertTrue(any(t.startswith("cloud: confidencial/reservado") for t in d["trace"]))

    def test_confidencial_repo_with_an_external_worker_is_refused_everywhere(self):
        d = route("tests", worker="agy", repo="/p/Plataforma-confidencial")
        self.assertEqual(d["action"], "refuse")


class LiveState(unittest.TestCase):
    def test_no_history_means_rule_and_first_of_prefer(self):
        d = route("tests")
        self.assertEqual((d["node"], d["routed_by"]), ("mac", "rule"))
        self.assertIn("first free node of prefer", d["reason"])

    def test_node_down_falls_to_the_next_apt_node(self):
        d = route("tests", levels={"win": "local", "mac": "down"})
        self.assertEqual((d["action"], d["node"]), ("run", "win"))
        self.assertEqual(d["diverted_from"], "mac")
        self.assertIn("mac skipped", d["reason"])

    def test_node_down_and_no_other_apt_node_queues_and_says_so(self):
        d = route("tests", ns=nodes(win={"runnable": False}), levels={"win": "local", "mac": "down"})
        self.assertEqual((d["action"], d["node"]), ("queue", "mac"))
        self.assertIn("mac is down", d["reason"])
        self.assertIn("no executor", d["reason"])

    def test_ssh_without_tmux_is_not_up(self):
        d = route("render", levels={"win": "local", "mac": "ssh"})
        self.assertEqual((d["action"], d["node"]), ("queue", "mac"))

    def test_full_slots_divert_to_the_other_node(self):
        d = route("tests", running={"mac": 2})
        self.assertEqual((d["node"], d["diverted_from"]), ("win", "mac"))
        self.assertIn("2/2 slots busy", d["reason"])

    def test_every_apt_node_busy_queues(self):
        d = route("render", running={"mac": 2})
        self.assertEqual((d["action"], d["node"]), ("queue", "mac"))

    def test_memory_pressure_counts_as_busy(self):
        d = route("tests", mem={"mac": (True, "memory free 9%")})
        self.assertEqual(d["node"], "win")
        self.assertIn("memory free 9%", d["reason"])


class History(unittest.TestCase):
    def test_history_decides_when_two_free_nodes_have_enough_runs(self):
        d = route("tests", hist={("tests", "mac"): runs(40), ("tests", "win"): runs(30)})
        self.assertEqual((d["node"], d["routed_by"]), ("win", "history"))
        self.assertIn("30.0 s", d["reason"])

    def test_one_measured_node_is_not_a_comparison(self):
        d = route("tests", hist={("tests", "win"): runs(30)})
        self.assertEqual((d["node"], d["routed_by"]), ("mac", "rule"))

    def test_fewer_than_five_runs_is_no_data(self):
        d = route("tests", hist={("tests", "mac"): runs(40), ("tests", "win"): runs(30, 4)})
        self.assertEqual(d["routed_by"], "rule")

    def test_history_only_among_free_nodes(self):
        d = route("tests", running={"win": 2}, hist={("tests", "mac"): runs(40), ("tests", "win"): runs(30)})
        self.assertEqual((d["node"], d["routed_by"]), ("mac", "rule"))

    def test_ledger_kind_is_derived_for_old_lines_and_stubs_are_left_out(self):
        lines = [json.dumps(x) for x in (
            {"node": "mac", "worker": "blender", "status": "ok", "duration_s": 8},
            {"node": "mac", "worker": "blender", "status": "failed", "duration_s": None},
            {"node": "mac", "worker": "local", "engine": "local", "status": "ok", "duration_s": 16},
            {"node": "mac", "worker": "claude", "exit": 0, "duration_s": 24},
            {"node": "mac", "worker": "stub-ok", "exit": 0, "duration_s": 1},
            {"node": "mac", "worker": "claude", "kind": "tests", "status": "ok", "duration_s": 5},
        )]
        h = R.history(lines)
        self.assertEqual(h, {("render", "mac"): [8.0], ("local-model", "mac"): [16.0],
                             ("claude", "mac"): [24.0], ("tests", "mac"): [5.0]})
        rows = R.history_table({("render", "mac"): runs(7.9)}, ["render"], ["win", "mac"], 5)
        self.assertEqual(rows, [("render", "win", 0, "sin datos"), ("render", "mac", 5, "7.9 s")])


class Memory(unittest.TestCase):
    T = 1_000_000.0

    def snap(self, pct, age):
        return {"built": self.T - age, "nodes": {"mac": {"memory": {"free_pct": pct}}}}

    def test_fresh_visor_reading_decides(self):
        self.assertTrue(R.mem_busy(self.snap(12, 60), "mac", self.T, CFG)[0])
        self.assertFalse(R.mem_busy(self.snap(27, 60), "mac", self.T, CFG)[0])

    def test_stale_reading_falls_back_to_the_model_manager(self):
        busy = R.mem_busy(self.snap(5, 3600), "mac", self.T, CFG, lambda n: {"running": "job-1"})
        idle = R.mem_busy(self.snap(5, 3600), "mac", self.T, CFG, lambda n: {"running": None})
        gone = R.mem_busy({}, "mac", self.T, CFG, lambda n: None)
        self.assertEqual((busy[0], idle[0], gone[0]), (True, False, False))

    def test_a_live_reading_from_the_probe_beats_the_model_flag(self):
        live = R.mem_busy({}, "mac", self.T, CFG, lambda n: {"running": None, "free_pct": 8})
        self.assertEqual(live, (True, "memory free 8% (live, floor 20%)"))


class KindAndClassify(unittest.TestCase):
    def test_frontmatter_kind(self):
        self.assertEqual(R.kind_from_task("---\nkind: render\n---\n# TASK\n"), "render")
        self.assertIsNone(R.kind_from_task("# TASK\nkind: render\n"))

    def test_classify_accepts_only_known_kinds_and_reports_cost(self):
        self.assertEqual(R.classify("x", ["tests", "gui"], lambda p: ("Tests.\n", 0.0004)), ("tests", 0.0004))
        self.assertEqual(R.classify("x", ["tests"], lambda p: ("I think tests", 0.0004)), (None, 0.0004))

    def test_resolve_order_flag_frontmatter_classify_default(self):
        fm = "---\nkind: git\n---\n"
        a = Namespace(kind=None, classify=False, engine="claude")
        self.assertEqual(E.resolve_kind(Namespace(kind="gui"), fm, CFG)[:2], ("gui", "flag"))
        self.assertEqual(E.resolve_kind(a, fm, CFG)[:2], ("git", "frontmatter"))
        self.assertEqual(E.resolve_kind(a, "# T", CFG)[:2], ("claude", "default"))
        a.classify = True
        self.assertEqual(E.resolve_kind(a, "# T", CFG, lambda p: ("scrape", 0.001)), ("scrape", "classify", 0.001))


TASK = "---\nkind: tests\n---\n# TASK\n\n## Files you may touch\n- x\n\n## Done criteria\n- [ ] y\n"


class RunWiring(unittest.TestCase):
    """`enjambre run` without --node: what the owner types in the 'done when' of prompt 12."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.saved = (E.STATE, E.NODES_JSON, E.PORTFOLIO, E.PROBE, E.load_nodes, E.sh, E.LEDGER,
                      E.VISOR_SNAPSHOT, E.model_health)
        E.STATE, E.NODES_JSON = self.root / "state", self.root / "state/nodes.json"
        E.PORTFOLIO, E.LEDGER = self.root, self.root / "jobs.jsonl"
        E.VISOR_SNAPSHOT = self.root / "nope.json"
        (self.root / "proj").mkdir()
        # kind tests needs a declared tests-cmd (largos.allowed_tools), or run refuses it
        (self.root / "proj/AGENTS.md").write_text("# rules\n- tests-cmd: `python3 -m unittest`\n",
                                                  encoding="utf-8")
        self.task = self.root / "TASK-r1.md"
        self.task.write_text(TASK, encoding="utf-8")
        E.load_nodes = lambda: nodes(win={"runnable": False})
        E.model_health = lambda host: {"running": None}

        def no_network(*a, **k):
            raise AssertionError("routing must not touch the node beyond PROBE")
        E.sh = no_network

    def tearDown(self):
        (E.STATE, E.NODES_JSON, E.PORTFOLIO, E.PROBE, E.load_nodes, E.sh, E.LEDGER,
         E.VISOR_SNAPSHOT, E.model_health) = self.saved
        self.tmp.cleanup()

    def run_cmd(self, **kw):
        a = dict(node=None, repo="proj", task=str(self.task), worker="claude", no_queue=False,
                 kind=None, classify=False, dry_run=False, engine="claude")
        a.update(kw)
        return E.cmd_run(Namespace(**a))

    def test_tests_kind_picks_the_mac_and_says_why(self):
        E.PROBE = lambda host: "ready"
        out = self._capture(lambda: self.run_cmd(kind="tests", dry_run=True))
        d = json.loads(out)
        self.assertEqual((d["action"], d["node"], d["routed_by"]), ("run", "mac", "rule"))
        self.assertIn("tests -> mac", d["reason"])
        self.assertFalse(E.LEDGER.exists(), "a dry run writes no ledger line")

    def test_mac_down_queues_with_the_routing_in_job_json(self):
        E.PROBE = lambda host: "down"
        self.assertEqual(self.run_cmd(), 0)  # kind from the TASK frontmatter
        meta = json.loads((E.STATE / "queue/r1/job.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["node"], meta["kind"], meta["kind_source"], meta["routed_by"]),
                         ("mac", "tests", "frontmatter", "rule"))
        self.assertIn("mac is down", meta["route_reason"])

    def test_refused_route_returns_2_and_queues_nothing(self):
        E.PROBE = lambda host: "ready"
        self.assertEqual(self.run_cmd(kind="gui"), 2)
        self.assertFalse((E.STATE / "queue").exists())

    def test_local_engine_is_never_queued_as_a_claude_job(self):
        E.PROBE = lambda host: "down"
        self.assertEqual(self.run_cmd(engine="local"), 2)
        self.assertFalse((E.STATE / "queue").exists())

    def test_explicit_node_is_explicit(self):
        E.PROBE = lambda host: "down"
        self.run_cmd(node="mac")
        meta = json.loads((E.STATE / "queue/r1/job.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["routed_by"], meta["route_reason"]), ("explicit", "--node mac"))

    def test_watch_child_keeps_the_original_routing(self):
        job = E.STATE / "running/r1"
        job.mkdir(parents=True)
        seen = {}

        class FakePopen:
            def __init__(self, cmd, **kw):
                seen["cmd"] = cmd
                for k in ("stdout", "stderr"):
                    kw[k].close()
        saved, E.subprocess.Popen = E.subprocess.Popen, FakePopen
        try:
            E.spawn_job({"id": "r1", "node": "mac", "repo": "proj", "worker": "claude",
                         "kind": "tests", "routed_by": "rule", "route_reason": "tests -> mac"})
        finally:
            E.subprocess.Popen = saved
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--routed-by") + 1], "rule")
        self.assertEqual(cmd[cmd.index("--kind") + 1], "tests")

    def _capture(self, fn):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(fn(), 0)
        return buf.getvalue().strip().splitlines()[-1]


if __name__ == "__main__":
    unittest.main()
