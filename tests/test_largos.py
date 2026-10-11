"""Tests for long detached jobs, Windows side (largos.py + the enjambre.py verbs): the AGENTS.md
allowlist, the tests allowlist, the push policy, launch/collect/follow with a simulated node.
The node side (jobrun.py) is tested ON the Mac: tests/node/test_jobrun_node.py.

    python -m unittest tests.test_largos -v
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import enjambre as E  # noqa: E402
import largos as L  # noqa: E402

AGENTS = """# AGENTS.md — proj
Verify: `py -m pytest`

## enjambre

- tests-cmd: `.venv/bin/python -m pytest -q`
- script-cmd catalogar: `/usr/bin/python3 tools/cat.py "/Volumes/Disco X" --etiqueta x`
- script-cmd bench: `bash scripts/bench.sh`
<!-- - script-cmd comentado: `rm -rf /` -->
"""


def proc(out: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out.encode(), b"")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.saved = (L.JOBS, E.LEDGER)
        L.JOBS = self.root / "state" / "jobs"
        E.LEDGER = self.root / "jobs.jsonl"
        self.repo = self.root / "proj"
        self.repo.mkdir()
        (self.repo / "AGENTS.md").write_text(AGENTS, encoding="utf-8")

    def tearDown(self):
        L.JOBS, E.LEDGER = self.saved
        self.tmp.cleanup()


class AgentsAllowlist(Base):
    def test_reads_tests_and_named_scripts(self):
        c = L.agents_cmds(self.repo)
        self.assertEqual(c["tests"], ".venv/bin/python -m pytest -q")
        self.assertEqual(sorted(c["scripts"]), ["bench", "catalogar"])
        self.assertIn('"/Volumes/Disco X"', c["scripts"]["catalogar"])

    def test_commented_line_is_not_declared(self):
        self.assertNotIn("comentado", L.agents_cmds(self.repo)["scripts"])

    def test_no_agents_md_declares_nothing(self):
        self.assertEqual(L.agents_cmds(self.root / "nada"), {"tests": None, "scripts": {}})

    def test_script_refuses_an_undeclared_name(self):
        saved = E.PORTFOLIO
        E.PORTFOLIO = self.root
        err = io.StringIO()
        try:
            sys.stderr, old = err, sys.stderr
            rc = E.main(["script", "--repo", "proj", "--cmd", "rm-todo", "--dry-run"])
        finally:
            sys.stderr = old
            E.PORTFOLIO = saved
        self.assertEqual(rc, 3)
        self.assertIn("declared: bench, catalogar", err.getvalue())

    def test_script_dry_run_resolves_the_command_and_quotes_nothing_itself(self):
        saved, out = E.PORTFOLIO, io.StringIO()
        E.PORTFOLIO = self.root
        try:
            sys.stdout, old = out, sys.stdout
            rc = E.main(["script", "--repo", "proj", "--cmd", "bench", "--dry-run", "--", "a b", "c;d"])
        finally:
            sys.stdout = old
            E.PORTFOLIO = saved
        spec = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(rc, 0)
        self.assertEqual((spec["cmd"], spec["args"]), ("bash scripts/bench.sh", ["a b", "c;d"]))


class TestsAllowlist(unittest.TestCase):
    def test_tests_kind_adds_exactly_that_command(self):
        a = L.allowed_tools("tests", ".venv/bin/python -m pytest -q")
        self.assertTrue(a.endswith(",Bash(.venv/bin/python -m pytest -q)"))
        self.assertNotIn("Bash(*", a)

    def test_other_kinds_get_no_command(self):
        self.assertEqual(L.allowed_tools("claude", "x"), L.BASE_TOOLS)

    def test_tests_without_a_declared_command_is_refused(self):
        with self.assertRaises(ValueError):
            L.allowed_tools("tests", None)

    def test_a_comma_would_split_the_list(self):
        with self.assertRaises(ValueError):
            L.allowed_tools("tests", "pytest a,b")

    def test_preamble_names_the_exact_command(self):
        self.assertIn("`make test`", L.tests_preamble("make test"))


class PushPolicy(Base):
    def test_explicit_false_wins(self):
        self.assertFalse(E.push_policy("---\npush: false\n---\n# T", self.repo, lambda _: "private")[0])

    def test_explicit_true_on_a_public_repo(self):
        self.assertTrue(E.push_policy("---\npush: true\n---\n# T", self.repo, lambda _: "public")[0])

    def test_public_does_not_push_by_default(self):
        ok, why = E.push_policy("# T", self.repo, lambda _: "public")
        self.assertFalse(ok)
        self.assertIn("public", why)

    def test_unknown_visibility_counts_as_public(self):
        self.assertFalse(E.push_policy("# T", self.repo, lambda _: "unknown")[0])

    def test_private_pushes(self):
        self.assertTrue(E.push_policy("# T", self.repo, lambda _: "private")[0])

    def test_kind_and_push_share_the_frontmatter(self):
        text = "---\nkind: tests\npush: no\n---\n# T"
        self.assertEqual((E.R.kind_from_task(text), E.frontmatter_flag(text, "push")), ("tests", False))

    def bring(self, push, leaks_clean):
        calls = []

        def fake_sh(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        saved = (E.sh, E.gitleaks_clean, E.PORTFOLIO)
        E.sh, E.gitleaks_clean, E.PORTFOLIO = fake_sh, (lambda *_: leaks_clean), self.root
        try:
            res = E.bring_branch_home({"host": "mac"}, "proj", "mac/x", push, "why")
        finally:
            E.sh, E.gitleaks_clean, E.PORTFOLIO = saved
        return res, calls

    def test_no_push_still_fetches_the_branch_home(self):
        (ok, note), calls = self.bring(False, True)
        self.assertFalse(ok)
        self.assertIn("fetch", calls[0])
        self.assertFalse(any("push" in c for c in calls))
        self.assertIn("not pushed", note)

    def test_gitleaks_overrides_push_true(self):
        (ok, note), calls = self.bring(True, False)
        self.assertFalse(ok)
        self.assertFalse(any("push" in c for c in calls))
        self.assertIn("gitleaks", note)

    def test_clean_and_allowed_pushes(self):
        (ok, _), calls = self.bring(True, True)
        self.assertTrue(ok)
        self.assertIn("push", calls[-1])


class Node:
    """A simulated node: answers jobrun.py verbs from a dict of statuses."""

    def __init__(self, statuses, beats=None):
        self.statuses, self.beats, self.cmds = statuses, beats or {}, []

    def __call__(self, host, cmd, stdin=None, timeout=60):
        self.cmds.append(cmd)
        if "jobrun.py launch" in cmd:
            return proc(json.dumps({"id": json.loads(stdin)["id"], "launched": True, "attempt": 1}))
        if "jobrun.py status" in cmd:
            ids = cmd.split("status ", 1)[1].split()
            return proc("\n".join(json.dumps(self.statuses[i]) for i in ids if i in self.statuses))
        if cmd.startswith("tail -n +"):
            jid = cmd.split("/")[2]
            start = int(cmd.split("+", 1)[1].split()[0])
            return proc("\n".join(self.beats.get(jid, [])[start - 1:]))
        if "jobrun.py resume" in cmd:
            return proc(json.dumps({"id": cmd.split()[-1], "launched": True, "attempt": 2}))
        if cmd.startswith("tail -c"):
            return proc("avance 50%\n")
        return proc("")


class LaunchCollect(Base):
    def launch(self, node, kind="script"):
        spec = {"id": "j1", "kind": kind, "repo": "proj", "cmd": "bash x.sh"}
        return L.launch("mac", "mac", spec, {"cmd_name": "x", "push": False}, ssh_fn=node,
                        ship_fn=lambda h: None)

    def test_launch_writes_job_json_only_when_the_node_confirms(self):
        res = self.launch(Node({}))
        self.assertTrue(res["launched"])
        meta = L.read_meta("j1")
        self.assertEqual((meta["host"], meta["collected"]), ("mac", False))

    def test_failed_launch_leaves_nothing_to_collect(self):
        def refuse(host, cmd, stdin=None, timeout=60):
            return proc(json.dumps({"id": "j1", "launched": False, "error": "no tmux session"}))
        res = L.launch("mac", "mac", {"id": "j1", "kind": "script", "repo": "proj"}, {},
                       ssh_fn=refuse, ship_fn=lambda h: None)
        self.assertFalse(res["launched"])
        self.assertIsNone(L.read_meta("j1"))

    def test_unreachable_node_is_reported(self):
        res = L.launch("mac", "mac", {"id": "j1", "kind": "script", "repo": "proj"}, {},
                       ssh_fn=Node({}), ship_fn=lambda h: "timeout")
        self.assertFalse(res["launched"])
        self.assertIn("cannot reach", res["error"])

    def collect(self, node, notes=None):
        lines = []
        out = L.collect(lines.append, lambda m, st: (False, "kept"),
                        (lambda t, m: notes.append(m)) if notes is not None else None,
                        ssh_fn=node, fetch_fn=lambda h, r, l: True)
        return out, lines

    def test_running_job_copies_beats_and_writes_no_ledger(self):
        self.launch(Node({}))
        node = Node({"j1": {"id": "j1", "state": "running", "latidos_n": 2}},
                    {"j1": ['{"ts": "a", "pct": 10}', '{"ts": "b", "pct": 20}']})
        out, lines = self.collect(node)
        self.assertEqual((out[0]["state"], out[0]["beats"], lines), ("running", 2, []))
        out, _ = self.collect(node)  # idempotent: the same beats are not copied twice
        self.assertEqual(out[0]["beats"], 0)
        self.assertEqual(len((L.JOBS / "j1" / "latidos.jsonl").read_text().splitlines()), 2)

    def test_finished_job_gets_exactly_one_ledger_line(self):
        self.launch(Node({}))
        st = {"id": "j1", "state": "ok", "rc": 0, "start": "2026-10-07T05:00:00-05:00",
              "end": "2026-10-07T07:00:00-05:00", "attempt": 2, "latidos_n": 0,
              "latido": {"pct": 100.0}}
        notes = []
        _, lines = self.collect(Node({"j1": st}), notes)
        _, again = self.collect(Node({"j1": st}), notes)
        self.assertEqual((len(lines), again), (1, []))
        e = lines[0]
        for k in ("id", "node", "worker", "exit", "pushed", "start", "status"):  # cmd_status reads these
            self.assertIn(k, e)
        self.assertEqual((e["status"], e["duration_s"], e["attempts"], e["worker"]), ("ok", 7200, 2, "script"))
        self.assertEqual(e["start"], "2026-10-07T10:00:00+00:00")  # UTC like every ledger line
        self.assertEqual(len(notes), 1)

    def test_budget_stop_is_incomplete_with_a_reason(self):
        self.launch(Node({}), kind="claude")
        st = {"id": "j1", "state": "incomplete", "reason": "budget", "rc": 1, "branch": "mac/j1",
              "start": "2026-10-07T05:00:00-05:00", "end": "2026-10-07T05:30:00-05:00",
              "cost_usd": 2.01, "turns": 64, "latidos_n": 0}
        _, lines = self.collect(Node({"j1": st}))
        e = lines[0]
        self.assertEqual((e["status"], e["reason"], e["worker"], e["cost_usd"]),
                         ("incomplete", "budget", "claude", 2.01))
        self.assertEqual(e["note"], "kept")

    def test_killed_counts_as_failed(self):
        self.launch(Node({}))
        _, lines = self.collect(Node({"j1": {"id": "j1", "state": "killed", "rc": -15, "latidos_n": 0}}))
        self.assertEqual((lines[0]["status"], lines[0]["reason"]), ("failed", "killed"))

    def test_dead_job_is_notified_once_and_never_ledgered(self):
        self.launch(Node({}))
        notes = []
        node = Node({"j1": {"id": "j1", "state": "dead", "latidos_n": 0}})
        _, lines = self.collect(node, notes)
        self.collect(node, notes)
        self.assertEqual((lines, len(notes)), ([], 1))
        self.assertIn("enjambre resume j1", notes[0])

    def test_resume_reopens_collection(self):
        self.launch(Node({}))
        meta = L.read_meta("j1")
        meta.update(collected=True)
        L.write_meta(meta)
        res = L.resume("j1", ssh_fn=Node({}), ship_fn=lambda h: None)
        meta = L.read_meta("j1")
        self.assertEqual((res["launched"], meta["collected"], meta["attempts"]), (True, False, 2))


class Follow(Base):
    def test_follow_prints_log_and_beat_until_the_end(self):
        L.launch("mac", "mac", {"id": "j1", "kind": "script", "repo": "proj"}, {}, ssh_fn=Node({}),
                 ship_fn=lambda h: None)
        st = {"id": "j1", "state": "ok", "log_size": 11, "log_name": "log",
              "latido": {"ts": "2026-10-07T05:00:00-05:00", "pct": 50.0, "elapsed_s": 120}, "attempt": 1}
        out = io.StringIO()
        res = L.follow("j1", ssh_fn=Node({"j1": st}), out=out, sleep=lambda s: None)
        self.assertEqual(res["state"], "ok")
        self.assertIn("avance 50%", out.getvalue())
        self.assertIn("50.0%", out.getvalue())

    def test_agent_stream_is_rendered_readable(self):
        ev = {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "docs/a.md"}}]}}
        self.assertIn("-> Read docs/a.md", L.render_chunk(json.dumps(ev) + "\n", "claude"))


class Prices(unittest.TestCase):
    def test_family_alias_picks_the_current_model(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "models.json"
            p.write_text(json.dumps({"models": [
                {"id": "claude-sonnet-old", "family": "sonnet", "current": False, "price_in": 9, "price_out": 9},
                {"id": "claude-sonnet-new", "family": "sonnet", "current": True, "price_in": 2.0,
                 "price_out": 10.0, "price_cache_hit": 0.2, "price_cache_write_1h": 4.0}]}), encoding="utf-8")
            pr = L.prices_for("sonnet", p)
        self.assertEqual((pr["model"], pr["input"], pr["cache_write"]), ("claude-sonnet-new", 2.0, 4.0))

    def test_missing_file_means_no_estimate(self):
        self.assertEqual(L.prices_for("sonnet", Path("/no/existe.json")), {})


if __name__ == "__main__":
    unittest.main()
