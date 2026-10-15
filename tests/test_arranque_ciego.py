"""Blind launch (2026-10-15): cert-media, kit-media and eco-gates-2 were `launched` on the Mac and
never wrote a status. Two causes: the repo was not cloned on the node, and `--repo` was a Windows
path. These tests keep both from coming back.

    python -m unittest tests.test_arranque_ciego -v
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import enjambre as E  # noqa: E402
import jobrun as J  # noqa: E402

NL = chr(10)
TASK = ("---\nkind: tests\n---\n# TASK\n\n## Files you may touch\n- x\n\n## Done criteria\n- [ ] y\n")


class FakeSh:
    """Stands in for enjambre.sh: answers the preflight `test -d`, records everything."""

    def __init__(self, repo_rc=1):
        self.repo_rc, self.calls = repo_rc, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        rc = self.repo_rc if any("test -d" in str(c) for c in cmd) else 0
        return subprocess.CompletedProcess(cmd, rc, "", "")


class Env(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.saved = (E.STATE, E.NODES_JSON, E.PORTFOLIO, E.PROBE, E.load_nodes, E.sh, E.LEDGER,
                      E.LV.OUT, E.L.JOBS, E.L.launch)
        E.STATE, E.NODES_JSON = self.root / "state", self.root / "state/nodes.json"
        E.L.JOBS = E.STATE / "jobs"
        E.LEDGER = self.root / "jobs.jsonl"
        E.LV.OUT = self.root / "visor"
        E.PORTFOLIO = self.root
        (self.root / "proj").mkdir()
        (self.root / "proj/AGENTS.md").write_text("- tests-cmd: `bash tools/verify.sh`\n", encoding="utf-8")
        self.task = self.root / "TASK-blind1.md"
        self.task.write_text(TASK, encoding="utf-8")
        E.load_nodes = lambda route=True: {"mac": {"host": "mac", "workers": ["claude"], "max_jobs": 2,
                                                   "os": "macos"}}
        E.PROBE = lambda host: "ready"
        self.launched = []
        E.L.launch = lambda *a, **k: self.launched.append(a) or {"launched": True}
        self.sh = FakeSh()
        E.sh = self.sh

        self.real_ledger = self.saved[6]
        self.real_rows = self.proj_rows()

    def tearDown(self):
        (E.STATE, E.NODES_JSON, E.PORTFOLIO, E.PROBE, E.load_nodes, E.sh, E.LEDGER,
         E.LV.OUT, E.L.JOBS, E.L.launch) = self.saved
        self.tmp.cleanup()
        # watch may append real rows meanwhile; a row for the fixture repo is ours
        self.assertEqual(self.proj_rows(), self.real_rows, "a test wrote into the real jobs.jsonl")

    def proj_rows(self):
        if not self.real_ledger.exists():
            return 0
        return self.real_ledger.read_text(encoding="utf-8").count('"repo": "proj"')

    def run_main(self, argv):
        err = io.StringIO()
        sys.stderr, old = err, sys.stderr
        out = io.StringIO()
        sys.stdout, old_out = out, sys.stdout
        try:
            rc = E.main(argv)
        finally:
            sys.stderr, sys.stdout = old, old_out
        return rc, err.getvalue()

    def assert_nothing_left(self):
        self.assertFalse(E.LEDGER.exists(), "no ledger line for a job that never ran")
        self.assertEqual(list(E.L.JOBS.glob("*")) if E.L.JOBS.exists() else [], [],
                         "nothing in state/jobs/")
        self.assertEqual(self.launched, [])


class RepoName(Env):
    def test_paths_are_refused_names_pass(self):
        for bad in (r"C:\Users\u\Documents\Portfolio\wt\landing-ecosistema",
                    "C:/Users/u/Documents/Portfolio/Metallum", "/Users/u/x", "wt/landing", "..",
                    "../Landing", "", "-rf"):
            with self.assertRaises(E.ContractError, msg=bad):
                E.check_repo_name(bad)
        for good in ("claude-cert-studio", "Landing", "enjambre-sandbox", "metal.globe_2"):
            E.check_repo_name(good)

    def test_refs(self):
        E.check_ref(None)
        E.check_ref("landing-ecosistema")
        E.check_ref("feat/v31-21")
        for bad in ("../x", "-x", "a b", "a;b", "x.lock", "x/", "$(id)"):
            with self.assertRaises(E.ContractError, msg=bad):
                E.check_ref(bad)

    def test_windows_path_repo_never_reaches_the_node(self):
        (self.root / "wt/landing-ecosistema").mkdir(parents=True)
        (self.root / "wt/landing-ecosistema/AGENTS.md").write_text("x", encoding="utf-8")
        rc, err = self.run_main(["run", "--node", "mac", "--detach", "--repo",
                                 str(self.root / "wt" / "landing-ecosistema"), "--task", str(self.task)])
        self.assertEqual(rc, 3)
        self.assertIn("--ref", err)
        self.assertEqual(self.sh.calls, [])
        self.assert_nothing_left()

    def test_script_refuses_a_path_too(self):
        rc, err = self.run_main(["script", "--repo", "C:/Users/u/Documents/Portfolio/Metallum",
                                 "--cmd", "x", "--dry-run"])
        self.assertEqual(rc, 3)
        self.assertIn("not a path", err)


class Preflight(Env):
    def test_detached_run_against_a_repo_missing_on_the_node(self):
        rc, err = self.run_main(["run", "--node", "mac", "--detach", "--repo", "proj",
                                 "--task", str(self.task)])
        self.assertNotEqual(rc, 0)
        self.assertIn("python enjambre.py clone --node mac proj", err)
        self.assert_nothing_left()

    def test_attached_run_against_a_repo_missing_on_the_node(self):
        rc, err = self.run_main(["run", "--node", "mac", "--repo", "proj", "--task", str(self.task)])
        self.assertEqual(rc, 3)
        self.assertIn("clone --node mac proj", err)
        self.assertFalse(any("scp" in c[0] for c in self.sh.calls), "worker never shipped")
        self.assert_nothing_left()

    def test_repo_present_goes_on_to_launch_with_the_ref(self):
        self.sh.repo_rc = 0
        rc, _ = self.run_main(["run", "--node", "mac", "--detach", "--repo", "proj", "--ref",
                               "landing-ecosistema", "--task", str(self.task)])
        self.assertEqual(rc, 0)
        spec = self.launched[0][2]
        self.assertEqual((spec["repo"], spec["ref"]), ("proj", "landing-ecosistema"))

    def test_unreachable_node_does_not_block_here(self):
        self.assertIsNone(E.repo_on_node("mac", "proj", lambda cmd: subprocess.CompletedProcess(cmd, 255)))


class NodeSide(unittest.TestCase):
    """jobrun.py, run here on its pure parts: the launch guard and the --ref rule."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.saved = (J.JOBS, J.PORTFOLIO)
        J.JOBS = str(self.home / ".enjambre" / "jobs")
        J.PORTFOLIO = str(self.home / "Documents" / "Portfolio")
        Path(J.PORTFOLIO).mkdir(parents=True)

    def tearDown(self):
        J.JOBS, J.PORTFOLIO = self.saved
        self.tmp.cleanup()

    def spec(self, jid, **kw):
        d = Path(J.JOBS) / jid
        d.mkdir(parents=True)
        (d / "spec.json").write_text(json.dumps(dict({"id": jid, "kind": "tests"}, **kw)), encoding="utf-8")

    def test_launch_refuses_a_repo_that_is_not_cloned(self):
        self.spec("cert-media", repo="claude-cert-studio")
        res = J.launch("cert-media")
        self.assertFalse(res["launched"])
        self.assertIn("clone --node", res["error"])
        self.assertFalse((Path(J.JOBS) / "cert-media" / "status.json").exists())

    def test_launch_refuses_a_windows_path(self):
        self.spec("eco-gates-2", repo=r"C:\Users\u\Documents\Portfolio\wt\landing-ecosistema")
        res = J.launch("eco-gates-2")
        self.assertFalse(res["launched"])
        self.assertIn("must be a name", res["error"])

    def test_ref_without_fetch_fails_instead_of_using_head(self):
        src = Path(J.PORTFOLIO) / "proj"
        src.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=src, check=True)   # no origin: fetch fails
        st, logs = {}, []
        wt = J.ensure_worktree({"id": "r1", "repo": "proj", "ref": "landing-ecosistema"}, st, logs.append)
        self.assertIsNone(wt)
        self.assertIn("--ref landing-ecosistema", st["reason"])
        self.assertFalse((Path(J.PORTFOLIO) / "wt").exists())


class Clone(Env):
    """`enjambre clone`: the replacement for the three `tests` jobs that tried to clone."""

    def fake(self, answer="cloned", rc=0):
        def run(cmd, **kw):
            self.sh.calls.append(cmd)
            if cmd[0] == "git":
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/IchiSieben/proj.git" + NL, "")
            if "tmux-run.sh" in cmd[-1]:
                return subprocess.CompletedProcess(cmd, rc, answer + NL if rc == 0 else "", "" if rc == 0 else answer)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        E.sh = run

    def ledger(self):
        return [json.loads(x) for x in E.LEDGER.read_text(encoding="utf-8").splitlines()]

    def test_clones_with_the_local_origin_and_writes_one_ledger_line(self):
        self.fake("cloned")
        rc, _ = self.run_main(["clone", "--node", "mac", "proj"])
        self.assertEqual(rc, 0)
        remote = self.sh.calls[-1][-1]
        self.assertIn("tmux-run.sh", remote, "keychain: through the GUI-born tmux")
        self.assertIn("https://github.com/IchiSieben/proj.git", remote)
        (e,) = self.ledger()
        self.assertEqual((e["worker"], e["status"], e["result"], e["node"], e["exit"], e["pushed"]),
                         ("clone", "ok", "cloned", "mac", 0, False))
        self.assertTrue(e["start"] and e["id"].startswith("clone-proj-"))

    def test_second_clone_says_exists(self):
        self.fake("exists")
        rc, _ = self.run_main(["clone", "--node", "mac", "proj"])
        self.assertEqual((rc, self.ledger()[0]["result"]), (0, "exists"))

    def test_failure_is_a_failed_line_with_the_reason(self):
        self.fake("fatal: repository not found", rc=128)
        rc, _ = self.run_main(["clone", "--node", "mac", "proj"])
        e = self.ledger()[0]
        self.assertEqual((rc, e["status"]), (128, "failed"))
        self.assertIn("repository not found", e["reason"])

    def test_refuses_paths_and_odd_urls_before_any_ssh(self):
        self.fake()
        self.assertEqual(self.run_main(["clone", "--node", "mac", "C:/x/proj"])[0], 3)
        self.assertEqual(self.run_main(["clone", "--node", "mac", "proj", "--url", "https://x/y;rm -rf ~"])[0], 3)
        self.assertEqual(self.sh.calls, [])
        self.assertFalse(E.LEDGER.exists())

    def test_a_hung_clone_is_bounded_and_ledgered(self):
        def run(cmd, **kw):
            self.sh.calls.append((cmd, kw.get("timeout")))
            if cmd[0] == "git":
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/IchiSieben/proj.git" + NL, "")
            if "tmux-run.sh" in cmd[-1]:
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            return subprocess.CompletedProcess(cmd, 0, "", "")
        E.sh = run
        rc, _ = self.run_main(["clone", "--node", "mac", "proj"])
        self.assertEqual(rc, 124)
        self.assertTrue(all(t for c, t in self.sh.calls if c[0] in ("ssh", "scp")), "every ssh/scp has a timeout")
        e = self.ledger()[0]
        self.assertEqual(e["status"], "failed")
        self.assertIn("timed out", e["reason"])

    def test_node_script_is_idempotent_shell(self):
        line = E.clone_script("claude-kit", "https://github.com/IchiSieben/claude-kit.git")
        self.assertTrue(line.startswith('if [ -d "$HOME/Documents/Portfolio/claude-kit"/.git ]; then echo exists'))
        self.assertIn("git clone --quiet https://github.com/IchiSieben/claude-kit.git", line)
        r = subprocess.run(["bash", "-n", "-c", line], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_update_fast_forwards_an_existing_clone_and_refuses_a_diverged_one(self):
        """`clone --update` (2026-10-15, playwright-unlock): a `script` job runs in the node's clone
        as it is, so a new script has to get there first. Real git, a temp HOME."""
        with tempfile.TemporaryDirectory() as d:
            home = Path(d)
            env = dict(os.environ, HOME=str(home), GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")

            def git(*args, cwd):
                return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
            origin, work = home / "origin.git", home / "work"
            git("init", "-q", "--bare", "-b", "main", str(origin), cwd=d)
            git("clone", "-q", str(origin), str(work), cwd=d)
            git("commit", "-q", "--allow-empty", "-m", "one", cwd=work)
            git("push", "-q", "origin", "HEAD:main", cwd=work)
            (home / "Documents" / "Portfolio").mkdir(parents=True)
            node = home / "Documents" / "Portfolio" / "r"
            git("clone", "-q", str(origin), str(node), cwd=d)
            git("commit", "-q", "--allow-empty", "-m", "two", cwd=work)
            git("push", "-q", "origin", "HEAD:main", cwd=work)
            want = git("rev-parse", "--short", "HEAD", cwd=work).stdout.strip()
            line = E.clone_script("r", origin.as_uri(), update=True)
            r = subprocess.run(["bash", "-c", line], env=env, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout.strip()), (0, f"updated {want}"), r.stderr)
            git("commit", "-q", "--allow-empty", "-m", "local only", cwd=node)
            git("commit", "-q", "--allow-empty", "-m", "three", cwd=work)
            git("push", "-q", "origin", "HEAD:main", cwd=work)
            r = subprocess.run(["bash", "-c", line], env=env, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)                       # diverged: not touched
            self.assertIn("exists", subprocess.run(["bash", "-c", E.clone_script("r", "x")], env=env,
                                                   capture_output=True, text=True).stdout)


class NeverUnknown(unittest.TestCase):
    """Item 3: a worker that dies before it starts leaves `failed` + a reason, and the PC turns
    a launch with no status into a terminal `dead-at-launch`."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.saved = (J.JOBS, J.PORTFOLIO, J.BUSY, J.ensure_worktree, E.L.JOBS, J.HOME)
        J.HOME = str(self.home)   # the reason folds HOME to ~: fold the test's home, on every OS
        J.JOBS = str(self.home / ".enjambre" / "jobs")
        J.BUSY = str(self.home / ".enjambre" / "busy.json")
        J.PORTFOLIO = str(self.home / "Documents" / "Portfolio")
        E.L.JOBS = self.home / "state" / "jobs"

    def tearDown(self):
        J.JOBS, J.PORTFOLIO, J.BUSY, J.ensure_worktree, E.L.JOBS, J.HOME = self.saved
        self.tmp.cleanup()

    def node_status(self, jid):
        return json.loads((Path(J.JOBS) / jid / "status.json").read_text(encoding="utf-8"))

    def test_supervisor_crash_before_the_job_is_failed_with_its_reason(self):
        d = Path(J.JOBS) / "cert-media"
        d.mkdir(parents=True)
        (d / "spec.json").write_text(json.dumps({"id": "cert-media", "kind": "tests",
                                                 "repo": "claude-cert-studio"}), encoding="utf-8")
        seen = {}

        def boom(spec, st, log):   # what 2026-10-15 did: git with cwd = a missing repo
            seen["state"] = self.node_status("cert-media")["state"]
            raise FileNotFoundError(2, "No such file or directory", J.PORTFOLIO + "/claude-cert-studio")
        J.ensure_worktree = boom
        self.assertEqual(J.supervise("cert-media"), 2)
        self.assertEqual(seen["state"], "starting", "a status exists before anything can fail")
        st = J.status("cert-media")
        self.assertEqual((st["state"], st["rc"], st["alive"]), ("failed", 2, False))
        self.assertIn("FileNotFoundError", st["reason"])
        self.assertNotIn(str(self.home), st["reason"])
        if sys.platform != "win32":   # jobrun runs on POSIX nodes; Windows str(e) escapes backslashes
            self.assertIn("~/Documents/Portfolio/claude-cert-studio", st["reason"])
        log = (d / "supervisor.log").read_text(encoding="utf-8").splitlines()
        self.assertIn("starting supervisor", log[0])
        self.assertIn("crashed", log[1])

    def test_missing_spec_is_failed_too_not_starting(self):
        self.assertEqual(J.supervise("no-spec"), 2)
        self.assertEqual(J.status("no-spec")["state"], "failed")

    def test_a_starting_supervisor_that_vanished_reads_dead(self):
        d = Path(J.JOBS) / "s1"
        d.mkdir(parents=True)
        (d / "status.json").write_text(json.dumps({"id": "s1", "state": "starting",
                                                   "supervisor_pid": 999999}), encoding="utf-8")
        saved = J.pid_alive, J.group_alive
        J.pid_alive = J.group_alive = lambda pid: False   # never os.kill a pid on Windows
        try:
            st = J.status("s1")
        finally:
            J.pid_alive, J.group_alive = saved
        self.assertEqual((st["state"], st["alive"]), ("dead", False))

    def test_worker_sh_that_dies_before_its_job_writes_failed(self):
        r = subprocess.run(["bash", str(Path(E.__file__).parent / "worker.sh"), "nope-repo", "w1", "claude", "mac"],
                           input=b"task", capture_output=True,
                           env=dict(__import__("os").environ, HOME=str(self.home), ENJAMBRE_NO_TMUX="1"))
        self.assertEqual(r.returncode, 2)
        logdir = self.home / ".enjambre" / "jobs" / "w1"
        st = json.loads((logdir / "status.json").read_text(encoding="utf-8"))
        self.assertEqual((st["state"], st["rc"], st["reason"]), ("failed", 2, "no repo nope-repo"))
        log = (logdir / "worker.log").read_text(encoding="utf-8").splitlines()
        self.assertIn("starting worker w1", log[0])
        self.assertTrue(any("No such file" in l for l in log), "stderr is copied into the log")

    # ---- PC side ----
    META = {"id": "kit-media", "node": "mac", "host": "mac", "kind": "tests", "repo": "claude-kit",
            "launched": "2026-10-15T10:06:46+00:00", "collected": False}
    T = __import__("datetime").datetime.fromisoformat("2026-10-15T10:06:46+00:00").timestamp()

    def test_dead_at_launch_rule(self):
        m = self.META
        unknown = {"state": "unknown", "alive": False, "launched": True}
        self.assertFalse(E.L.dead_at_launch(m, unknown, self.T + 60))
        self.assertTrue(E.L.dead_at_launch(m, unknown, self.T + 91))
        self.assertFalse(E.L.dead_at_launch(m, {"state": "starting", "alive": True}, self.T + 900),
                         "a live supervisor in a long git fetch is never dead")
        self.assertTrue(E.L.dead_at_launch(m, {"state": "dead", "alive": False}, self.T + 900))
        self.assertFalse(E.L.dead_at_launch(m, {"state": "dead", "alive": False, "child_pid": 5},
                                            self.T + 900), "a job that ran is `dead`, resumable")
        self.assertFalse(E.L.dead_at_launch(m, {"state": "unknown", "launched": False}, self.T + 900))

    def test_collect_closes_it_once_with_the_node_reason(self):
        E.L.write_meta(dict(self.META, launched="2020-01-01T00:00:00+00:00"))
        trace = ("== supervisor.out\nTraceback (most recent call last):\n  File \"x\"\n"
                 "FileNotFoundError: [Errno 2] No such file or directory: "
                 "'/Users/someone/Documents/Portfolio/claude-kit'\n")

        def node(host, cmd, stdin=None, timeout=60):
            if "jobrun.py status" in cmd:
                out = json.dumps({"id": "kit-media", "state": "unknown", "alive": False, "launched": True})
            else:
                out = trace
            return subprocess.CompletedProcess([], 0, out.encode(), b"")
        rows = []
        done = E.L.collect(rows.append, ssh_fn=node)
        self.assertEqual(done, [{"id": "kit-media", "state": "dead-at-launch", "ledger": True}])
        (e,) = rows
        self.assertEqual((e["status"], e["reason_code"], e["exit"], e["node"]), ("failed", "dead-at-launch", 2, "mac"))
        self.assertIn("FileNotFoundError", e["reason"])
        self.assertIn("~/Documents/Portfolio/claude-kit", e["reason"])
        self.assertNotIn("someone", e["reason"])
        self.assertTrue(E.L.read_meta("kit-media")["collected"])
        self.assertTrue((E.L.JOBS / "kit-media" / "node.log").exists())
        self.assertEqual(E.L.collect(rows.append, ssh_fn=node), [], "terminal: never again")

    def test_first_error_picks_the_exception_line(self):
        self.assertEqual(E.L.first_error("== a\nTraceback\n  File x\nValueError: boom\n== b\nok\n"),
                         "ValueError: boom")
        self.assertEqual(E.L.first_error("== a\nnothing special\n"), "nothing special")
        self.assertIsNone(E.L.first_error(""))

    def test_close_by_hand_is_terminal_and_refuses_a_running_job(self):
        jid = "objcap-build-20261008-033233"
        E.L.write_meta({"id": jid, "node": "mac", "host": "mac", "kind": "script", "repo": "autoescaneo",
                        "launched": "2026-10-08T08:32:35+00:00", "collected": False})
        st_path = E.L.JOBS / jid / "status.json"
        st_path.write_text(json.dumps({"id": jid, "state": "running", "alive": True}), encoding="utf-8")
        rows = []
        self.assertFalse(E.L.close(jid, "x", rows.append)["closed"])
        st_path.write_text(json.dumps({"id": jid, "state": "dead", "alive": False,
                                       "start": "2026-10-08T03:32:35-05:00"}), encoding="utf-8")
        res = E.L.close(jid, "cerrado manualmente 2026-10-15", rows.append)
        self.assertTrue(res["closed"])
        (e,) = rows
        self.assertEqual((e["status"], e["reason"], e["reason_code"], e["worker"]),
                         ("failed", "cerrado manualmente 2026-10-15", "closed", "script"))
        for k in ("start", "node", "worker", "exit", "pushed"):   # what cmd_status indexes
            self.assertIn(k, e)
        self.assertTrue(E.L.read_meta(jid)["collected"])
        self.assertFalse(E.L.close(jid, "again", rows.append)["closed"], "once")
        self.assertEqual(E.L.collect(rows.append, ssh_fn=lambda *a, **k: self.fail("ssh")), [])

    def test_a_job_closed_while_collect_waits_on_ssh_is_left_alone(self):
        jid = "sync-branch-20261007-135456"
        E.L.write_meta({"id": jid, "node": "mac", "host": "mac", "kind": "script", "repo": "x",
                        "launched": "2026-10-07T18:54:57+00:00", "collected": False})
        rows = []

        def node(host, cmd, stdin=None, timeout=60):
            # the owner runs `cerrar` while this ssh is in flight
            (E.L.JOBS / jid / "status.json").write_text(json.dumps({"state": "dead"}), encoding="utf-8")
            E.L.close(jid, "cerrado manualmente 2026-10-15", rows.append)
            return subprocess.CompletedProcess([], 0, json.dumps({"id": jid, "state": "dead", "alive": False}).encode(), b"")
        self.assertEqual(E.L.collect(rows.append, ssh_fn=node), [])
        self.assertEqual(len(rows), 1, "one ledger line, the hand-closed one")
        self.assertTrue(E.L.read_meta(jid)["collected"])

    def test_node_logs_refuses_odd_ids_without_ssh(self):
        rc, text = E.L.node_logs("mac", "../x", ssh_fn=lambda *a, **k: self.fail("ssh called"))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
