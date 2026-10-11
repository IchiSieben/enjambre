"""Presence, queue and watch with simulated nodes: no ssh, no ping, no toast.

    python -m unittest discover -s tests -v
"""
import json
import logging
import subprocess
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import enjambre as E  # noqa: E402
import presence as P  # noqa: E402

LOG = logging.getLogger("test.watch")
LOG.addHandler(logging.NullHandler())
T0 = 1_800_000_000.0
TASK = "# TASK\n\n## Files you may touch\n- a.md\n\n## Done criteria\n- [ ] done\n"


class Debounce(unittest.TestCase):
    def test_first_observation_is_taken_as_is(self):
        s, changed = P.step(None, "ssh", T0)
        self.assertEqual((s["level"], changed), ("ssh", True))

    def test_going_up_is_immediate(self):
        s, _ = P.step(None, "down", T0)
        s, changed = P.step(s, "ready", T0 + 30)
        self.assertEqual((s["level"], changed), ("ready", True))

    def test_one_miss_does_not_drop_a_node(self):
        s, _ = P.step(None, "ready", T0)
        s, changed = P.step(s, "down", T0 + 30)
        self.assertEqual((s["level"], changed, s["observed"]), ("ready", False, "down"))

    def test_two_misses_in_a_row_drop_it(self):
        s, _ = P.step(None, "ready", T0)
        s, _ = P.step(s, "down", T0 + 30)
        s, changed = P.step(s, "down", T0 + 60)
        self.assertEqual((s["level"], changed), ("down", True))

    def test_a_good_answer_resets_the_count(self):
        s, _ = P.step(None, "ready", T0)
        s, _ = P.step(s, "down", T0 + 30)
        s, _ = P.step(s, "ready", T0 + 60)
        s, changed = P.step(s, "down", T0 + 90)
        self.assertEqual((s["level"], changed), ("ready", False))


class Probes(unittest.TestCase):
    def setUp(self):
        self.real = P._run

    def tearDown(self):
        P._run = self.real

    def fake(self, rc, out=""):
        P._run = lambda cmd, timeout: subprocess.CompletedProcess(cmd, rc, out, "")

    def test_windows_unreachable_with_rc0_is_not_a_ping(self):
        self.fake(0, "Respuesta desde 192.0.2.9: Host de destino inaccesible.")
        self.assertFalse(P.ping_ok("x"))

    def test_reply_with_ttl_is_a_ping(self):
        self.fake(0, "Respuesta desde 192.0.2.9: bytes=32 tiempo<1m TTL=64")
        self.assertTrue(P.ping_ok("x"))

    def test_ssh_rc0_means_ready_rc1_means_no_tmux(self):
        self.fake(0)
        self.assertEqual(P.probe("mac"), "ready")
        self.fake(1)
        self.assertEqual(P.probe("mac"), "ssh")

    def test_claude_logged_out_is_ssh_not_ready(self):
        # prompt 21 §2: after a reboot ssh answers before the GUI login unlocks the keychain;
        # the node must not be `ready` until `claude auth status` says loggedIn from the tmux session
        self.fake(3)
        self.assertEqual(P.probe("mac"), "ssh")
        self.assertIn("claude auth status", P.READY_CMD)
        self.assertIn("tmux -L enjambre run-shell", P.READY_CMD)
        self.assertIn('grep -cE "\\"loggedIn\\": *true"', P.READY_CMD)   # tolerant of compact JSON

    def test_logon_instance_waits_for_the_boot_one_to_hand_over(self):
        # prompt 21 §2: the --boot loop hands over between rounds; the logon task retries the lock
        tries = iter([False, False, True])
        lock = type("L", (), {"acquire": lambda self: next(tries)})()
        slept = []
        self.assertTrue(P.acquire_or_wait(lock, 60, every=5, sleep=slept.append))
        self.assertEqual(slept, [5, 5])
        never = type("L", (), {"acquire": lambda self: False})()
        self.assertFalse(P.acquire_or_wait(never, 0, sleep=slept.append))


class Notifications(unittest.TestCase):
    def test_only_changes_notify_and_not_the_first_round(self):
        sent, seq = [], iter(["ready", "ready", "down", "down", "ready"])
        nodes = {"mac": {"host": "mac"}}
        st = {}
        for i in range(5):
            st = P.tick(nodes, st, lambda h: next(seq), T0 + 30 * i, lambda *m: sent.append(m), LOG)
        self.assertEqual([m[0] for m in sent], ["mac caída", "mac disponible"])


def report(t: float, ok: bool = True, state: str = "mounted") -> dict:
    """What smb-mount.sh writes to ~/.enjambre/mounts.json."""
    return {"checked_at": P.dt.datetime.fromtimestamp(t, P.dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reach_445": True, "keychain": True,
            "mounts": [{"name": "Compartido-Mac", "ok": ok, "state": state, "path": "~/Compartidos/Compartido-Mac"}]}


class Mounts(unittest.TestCase):
    def test_absent_file_adds_nothing(self):
        st = P.tick({"mac": {"host": "mac"}}, {}, lambda h: "ready", T0, lambda *m: None, LOG,
                    lambda h: None)
        self.assertNotIn("mounts", st["nodes"]["mac"])

    def test_fresh_report_is_kept(self):
        mv = P.mounts_view(report(T0 - 30), T0)
        self.assertEqual((mv["stale"], mv["mounts"][0]["ok"]), (False, True))

    def test_stale_report_is_not_alive(self):
        mv = P.mounts_view(report(T0 - P.MOUNTS_STALE_S - 1), T0)
        self.assertEqual((mv["stale"], mv["mounts"][0]["ok"], mv["mounts"][0]["state"]),
                         (True, False, "stale"))

    def test_garbage_timestamp_counts_as_stale(self):
        self.assertTrue(P.mounts_view({"checked_at": "nope", "mounts": []}, T0)["stale"])

    def test_not_read_when_node_is_down_or_local(self):
        calls = []
        nodes = {"mac": {"host": "mac"}, "win": {"host": "local"}}
        P.tick(nodes, {}, lambda h: "down" if h == "mac" else "ready", T0, lambda *m: None, LOG,
               lambda h: calls.append(h) or report(T0))
        self.assertEqual(calls, [])

    def test_flip_notifies_but_first_sight_does_not(self):
        sent, nodes, st = [], {"mac": {"host": "mac"}}, {}
        for i, ok in enumerate([True, True, False, True]):
            t = T0 + 60 * i
            st = P.tick(nodes, st, lambda h: "ready", t, lambda *m: sent.append(m), LOG,
                        lambda h, t=t, ok=ok: report(t, ok, "mounted" if ok else "hung"))
        self.assertEqual([m[1] for m in sent], [
            "Desmontado o colgado: la Mac no ve esa carpeta de Windows.", "Montado de nuevo."])

    def test_level_logic_unchanged_by_mounts(self):
        st = P.tick({"mac": {"host": "mac"}}, {}, lambda h: "ready", T0, lambda *m: None, LOG,
                    lambda h: report(T0, False, "unreachable"))
        self.assertEqual(st["nodes"]["mac"]["level"], "ready")


class Fs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()


class Queue(Fs):
    def test_snapshot_and_duplicate_rejected(self):
        P.enqueue(self.root, "a1", TASK, {"node": "mac"})
        self.assertEqual((self.root / "queue/a1/TASK-a1.md").read_text(encoding="utf-8"), TASK)
        with self.assertRaises(P.QueueError):
            P.enqueue(self.root, "a1", TASK, {"node": "mac"})

    def test_pick_respects_ready_and_max_jobs(self):
        for i in range(3):
            P.enqueue(self.root, f"j{i}", TASK, {"node": "mac"})
            time.sleep(0.01)
        self.assertEqual(P.pick(self.root, {"mac": "ssh"}, {"mac": 2}), [])
        P.move(self.root, "j0", "queue", "running")
        picked = P.pick(self.root, {"mac": "ready"}, {"mac": 2})
        self.assertEqual([m["id"] for m in picked], ["j1"])

    def test_fresh_level_ignores_stale_state(self):
        st = {"nodes": {"mac": {"level": "ready", "checked": P.now_iso(T0)}}}
        self.assertEqual(P.fresh_level(st, "mac", T0 + 30), "ready")
        self.assertIsNone(P.fresh_level(st, "mac", T0 + 600))

    def test_second_instance_cannot_take_the_lock(self):
        a, b = P.InstanceLock(self.root / "w.lock"), P.InstanceLock(self.root / "w.lock")
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())


class FakeProc:
    def __init__(self, rc):
        self.returncode = rc

    def poll(self):
        return self.returncode


class RunAndWatch(Fs):
    """`run` against a down node queues; `watch` dispatches when it comes back."""

    def setUp(self):
        super().setUp()
        self.saved = (E.STATE, E.NODES_JSON, E.PORTFOLIO, E.PROBE, E.load_nodes, E.sh, E.LV.OUT, E.L.JOBS)
        E.L.JOBS = self.root / "state" / "jobs"     # never the real detached jobs (they hold slots)
        E.STATE, E.NODES_JSON = self.root / "state", self.root / "state/nodes.json"
        E.LV.OUT = self.root / "visor"          # no live.json: reposo says nothing
        E.PORTFOLIO = self.root
        (self.root / "proj").mkdir()
        (self.root / "proj/AGENTS.md").write_text("# rules\n", encoding="utf-8")
        self.task = self.root / "TASK-q1.md"
        self.task.write_text(TASK, encoding="utf-8")
        E.load_nodes = lambda: {"mac": {"host": "mac", "workers": ["claude"], "max_jobs": 1}}

        def no_network(*a, **k):
            raise AssertionError("a queued run must not touch the node")
        E.sh = no_network

    def tearDown(self):
        E.STATE, E.NODES_JSON, E.PORTFOLIO, E.PROBE, E.load_nodes, E.sh, E.LV.OUT, E.L.JOBS = self.saved
        super().tearDown()

    def run_cmd(self):
        return E.cmd_run(Namespace(node="mac", repo="proj", task=str(self.task), worker="claude",
                                   no_queue=False))

    def test_down_node_queues_instead_of_failing(self):
        E.PROBE = lambda host: "down"
        self.assertEqual(self.run_cmd(), 0)
        meta = json.loads((E.STATE / "queue/q1/job.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["node"], meta["level_at_enqueue"]), ("mac", "down"))

    def test_contract_still_checked_before_queueing(self):
        E.PROBE = lambda host: "down"
        self.task.write_text("# no sections\n", encoding="utf-8")
        self.assertEqual(self.run_cmd(), 3)
        self.assertFalse((E.STATE / "queue").exists())

    def test_watch_dispatches_when_the_node_returns_and_reaps_after(self):
        E.PROBE = lambda host: "down"
        self.run_cmd()
        nodes, procs, spawned = E.load_nodes(), {}, []
        spawn = lambda meta: spawned.append(meta["id"]) or FakeProc(0)  # noqa: E731
        E.watch_round(nodes, procs, T0, lambda h: "down", lambda *m: None, spawn, LOG)
        self.assertEqual(spawned, [])
        E.watch_round(nodes, procs, T0 + 30, lambda h: "ready", lambda *m: None, spawn, LOG)
        self.assertEqual(spawned, ["q1"])
        self.assertTrue((E.STATE / "running/q1").is_dir())
        E.watch_round(nodes, procs, T0 + 60, lambda h: "ready", lambda *m: None, spawn, LOG)
        self.assertEqual((E.STATE / "done/q1/rc").read_text(), "0")
        self.assertEqual(spawned, ["q1"], "a finished job is never dispatched twice")

    def test_watch_holds_the_queue_while_the_node_rests(self):
        E.PROBE = lambda host: "down"
        self.run_cmd()
        nodes, procs, spawned = E.load_nodes(), {}, []
        spawn = lambda meta: spawned.append(meta["id"]) or FakeProc(0)  # noqa: E731
        E.LV.OUT.mkdir(parents=True)
        rest = {"id": "presion", "no_dispatch": True, "since": "t", "msg": "sin memoria"}
        (E.LV.OUT / "live.json").write_text(json.dumps({"updated_ts": T0, "reposo": {"mac": [rest]}}),
                                            encoding="utf-8")
        E.watch_round(nodes, procs, T0 + 30, lambda h: "ready", lambda *m: None, spawn, LOG)
        self.assertEqual(spawned, [], "max_jobs is 0 while a no-dispatch reposo rule holds")
        (E.LV.OUT / "live.json").write_text(json.dumps({"updated_ts": T0 + 60, "reposo": {"mac": []}}),
                                            encoding="utf-8")
        E.watch_round(nodes, procs, T0 + 60, lambda h: "ready", lambda *m: None, spawn, LOG)
        self.assertEqual(spawned, ["q1"])


class Gate(unittest.TestCase):
    """watch: ping every round, ssh + tmux only on a ping flip or every FULL_PROBE_S."""

    def make(self, pings):
        self.t, self.full, self.targets = T0, [], []
        seq = iter(pings)
        g = P.GatedProbe(full_fn=lambda h: self.full.append(self.t) or "ready",
                         ping_fn=lambda ip: next(seq),
                         target_fn=lambda h: self.targets.append(h) or "192.0.2.2",
                         clock=lambda: self.t)
        return g

    def rounds(self, g, n, step=30):
        out = []
        for _ in range(n):
            out.append(g("mac"))
            self.t += step
        return out

    def test_steady_ping_means_one_ssh_per_five_minutes(self):
        g = self.make([True] * 20)
        self.assertEqual(self.rounds(g, 20), ["ready"] * 20)      # 10 min of 30 s rounds
        self.assertEqual(self.full, [T0, T0 + 300])                # first round + one at 5 min

    def test_a_ping_flip_probes_at_once(self):
        g = self.make([True, True, False, False, True])
        self.rounds(g, 5)
        self.assertEqual(self.full, [T0, T0 + 60, T0 + 120])       # first, flip down, flip up

    def test_half_up_node_is_probed_every_round_until_ready(self):
        # prompt 21 §2: after a reboot the ping comes back before sshd and the GUI login;
        # waiting FULL_PROBE_S for the next full probe would eat the 5 min budget
        levels = iter(["ping", "ssh", "ssh", "ready", "ready", "ready"])
        g = self.make([True] * 6)
        g.full_fn = lambda h: self.full.append(self.t) or next(levels)
        self.assertEqual(self.rounds(g, 6), ["ping", "ssh", "ssh", "ready", "ready", "ready"])
        self.assertEqual(self.full, [T0, T0 + 30, T0 + 60, T0 + 90])   # then back to every 5 min

    def test_ssh_g_is_resolved_once(self):
        g = self.make([True] * 5)
        self.rounds(g, 5)
        self.assertEqual(self.targets, ["mac"])

    def test_mounts_read_only_on_full_rounds(self):
        g = self.make([True] * 3)
        reads = []
        for _ in range(3):
            g("mac")
            r = g.mounts("mac", read_fn=lambda h: reads.append(h) or report(self.t))
            self.t += 30
        self.assertEqual(len(reads), 1)
        self.assertIs(r, P.KEEP)

    def test_keep_preserves_the_last_mounts_view_without_notifying(self):
        sent, nodes = [], {"mac": {"host": "mac"}}
        st = P.tick(nodes, {}, lambda h: "ready", T0, lambda *m: sent.append(m), LOG,
                    lambda h: report(T0))
        st2 = P.tick(nodes, st, lambda h: "ready", T0 + 30, lambda *m: sent.append(m), LOG,
                     lambda h: P.KEEP)
        self.assertEqual(st2["nodes"]["mac"]["mounts"], st["nodes"]["mac"]["mounts"])
        self.assertEqual(sent, [])


class NoConsole(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_hidden_on_windows(self):
        kw = P.no_console()
        self.assertEqual(kw["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertEqual(kw["startupinfo"].wShowWindow, subprocess.SW_HIDE)
        self.assertTrue(kw["startupinfo"].dwFlags & subprocess.STARTF_USESHOWWINDOW)

    @unittest.skipIf(sys.platform == "win32", "non-Windows only")
    def test_nothing_elsewhere(self):
        self.assertEqual(P.no_console(), {})

    # run on the Mac: respaldo (xattr, mdls, /usr/bin/trash), jobrun (the long-job supervisor),
    # mcp_probe (drives the Playwright MCP from the Mac tmux), agent/puente.py (no subprocess today)
    MAC_ONLY = {"tools/respaldo.py", "jobrun.py", "tools/catalogar_disco.py", "tools/mcp_probe.py",
                "agent/puente.py"}

    def test_every_windows_ssh_call_site_hides_its_console(self):
        """Grep guard over EVERY .py outside tests/ (a fixed file list missed visor/web.py, added
        after the fix): each subprocess call must pass no_console() inside its own parentheses."""
        root = Path(__file__).resolve().parent.parent
        checked = 0
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel.startswith(("tests/", ".git/", "state/")) or rel in self.MAC_ONLY:
                continue
            src = path.read_text(encoding="utf-8", errors="replace")
            for m in P.re.finditer(r"subprocess\.(?:run|Popen|call|check_output|check_call)\(", src):
                depth, i = 0, m.end() - 1
                while i < len(src):          # the call's own (...) only, nested parens included
                    depth += {"(": 1, ")": -1}.get(src[i], 0)
                    if depth == 0:
                        break
                    i += 1
                call = src[m.start():i + 1]
                self.assertIn("no_console()", call, f"{rel}: {call[:80]}")
                checked += 1
        self.assertGreaterEqual(checked, 7)  # the guard must actually see the call sites


if __name__ == "__main__":
    unittest.main()
