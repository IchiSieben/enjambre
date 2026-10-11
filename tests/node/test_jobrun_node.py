"""jobrun.py tests that need a real POSIX node (process groups, detached supervisors, git
worktrees). Run ON the Mac with the system Python the node uses:

    /usr/bin/python3 -m unittest discover -s tests/node -v

Everything happens under a throwaway HOME: the real ~/.enjambre/busy.json, jobs and repos are
never touched. `claude` is a fake script in $HOME/.local/bin (first on jobrun's PATH) that
prints a stream-json transcript, so no test spends quota. Python 3.9-safe.
No __init__.py here on purpose: `discover -s tests` on Windows never imports this file.
"""
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)

FAKE_CLAUDE = r"""#!/bin/bash
# fake claude -p: records argv and stdin, prints a stream-json transcript
echo "$*" >> "$HOME/claude-argv.txt"
cat > "$HOME/claude-stdin.txt"
mode=$(cat "$HOME/claude-mode" 2>/dev/null || echo success)
cost=$(cat "$HOME/claude-cost" 2>/dev/null || echo 0.5)
echo '{"type":"system","subtype":"init","session_id":"sid-1"}'
for m in m1 m1 m2; do
  echo '{"type":"assistant","session_id":"sid-1","message":{"id":"'$m'","usage":{"input_tokens":1000000,"output_tokens":0},"content":[{"type":"tool_use","name":"Read","input":{"file_path":"a"}}]}}'
done
echo "linea del agente" > nota.txt
[ "$mode" = hang ] && sleep 300
err=true; [ "$mode" = success ] && err=false
echo '{"type":"result","subtype":"'$mode'","is_error":'$err',"total_cost_usd":'$cost',"num_turns":31,"session_id":"sid-1","result":"hecho","permission_denials":[]}'
"""


class Node(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="jobrun-test-")
        self.env = dict(os.environ, HOME=self.home)
        os.makedirs(os.path.join(self.home, ".enjambre", "jobs"))
        self.jobrun = os.path.join(self.home, ".enjambre", "jobrun.py")
        shutil.copy(os.path.join(REPO, "jobrun.py"), self.jobrun)
        self.proj = os.path.join(self.home, "Documents", "Portfolio", "proj")
        os.makedirs(self.proj)
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q"], cwd=self.proj, check=True)
        with open(os.path.join(self.proj, "README.md"), "w") as f:
            f.write("x\n")
        subprocess.run(["git", "add", "-A"], cwd=self.proj, check=True)
        subprocess.run(git + ["commit", "-q", "-m", "init"], cwd=self.proj, check=True)
        bindir = os.path.join(self.home, ".local", "bin")
        os.makedirs(bindir)
        fake = os.path.join(bindir, "claude")
        with open(fake, "w") as f:
            f.write(FAKE_CLAUDE)
        os.chmod(fake, 0o755)
        self.started = []

    def tearDown(self):
        for jid in self.started:  # never leave a test process behind
            st = self.status(jid)
            for pid in (st.get("pgid"),):
                try:
                    os.killpg(int(pid), signal.SIGKILL)
                except (OSError, TypeError, ValueError):
                    pass
            try:
                os.kill(int(st.get("supervisor_pid")), signal.SIGKILL)
            except (OSError, TypeError, ValueError):
                pass
        shutil.rmtree(self.home, ignore_errors=True)

    # -- helpers --
    def verb(self, *args):
        r = subprocess.run(["/usr/bin/python3", self.jobrun] + list(args), env=self.env,
                           capture_output=True, text=True, timeout=60)
        lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
        return json.loads(lines[-1]) if lines else {"raw": r.stdout + r.stderr}

    def status(self, jid):
        return self.verb("status", jid)

    def launch(self, jid, **spec):
        d = os.path.join(self.home, ".enjambre", "jobs", jid)
        os.makedirs(d, exist_ok=True)
        spec = dict({"id": jid, "repo": "proj", "via": "ssh", "beat_s": 0.5, "beat_log_s": 0.5}, **spec)
        with open(os.path.join(d, "spec.json"), "w") as f:
            json.dump(spec, f)
        self.started.append(jid)
        return self.verb("launch", jid)

    def wait(self, jid, states=("ok", "failed", "incomplete", "killed", "dead"), t=30):
        end = time.time() + t
        while time.time() < end:
            st = self.status(jid)
            if st.get("state") in states:
                return st
            time.sleep(0.3)
        self.fail(f"{jid} never reached {states}: {self.status(jid)}")

    def busy(self):
        try:
            with open(os.path.join(self.home, ".enjambre", "busy.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def group_alive(self, pgid):
        try:
            os.killpg(int(pgid), 0)
            return True
        except OSError:
            return False

    # -- scripts --
    def test_script_ok_beats_pct_and_releases_busy(self):
        res = self.launch("s-ok", kind="script", cmd="echo 'avance 10%'; sleep 2; echo 'avance 100%'")
        self.assertTrue(res["launched"], res)
        time.sleep(1)
        self.assertEqual((self.busy() or {}).get("quien"), "s-ok")  # marked while it runs
        st = self.wait("s-ok", ("ok", "failed"))
        self.assertEqual((st["state"], st["rc"], st["pct"]), ("ok", 0, 100.0))
        self.assertGreaterEqual(st["latidos_n"], 2)
        self.assertIsNone(self.busy())  # released at the end

    def test_script_failure_is_failed(self):
        self.launch("s-fail", kind="script", cmd="exit 3")
        st = self.wait("s-fail", ("ok", "failed"))
        self.assertEqual((st["state"], st["rc"]), ("failed", 3))

    def test_a_live_marker_of_another_job_is_never_stolen(self):
        other = {"quien": "bench", "desde": "x", "tipo": "bench", "pid": os.getpid()}
        with open(os.path.join(self.home, ".enjambre", "busy.json"), "w") as f:
            json.dump(other, f)
        self.launch("s-busy", kind="script", cmd="sleep 1")
        st = self.wait("s-busy", ("ok",))
        self.assertEqual(self.busy(), other)
        self.assertFalse(st.get("busy_marker"))

    def test_kill_takes_the_whole_group_down(self):
        self.launch("s-kill", kind="script", cmd="sleep 300 & sleep 300; wait")
        st = self.wait("s-kill", ("running",))
        time.sleep(1)
        self.assertTrue(self.group_alive(st["pgid"]))
        self.verb("kill", "s-kill")
        st = self.wait("s-kill", ("killed", "failed"))
        self.assertEqual(st["state"], "killed")
        self.assertFalse(self.group_alive(st["pgid"]))
        self.assertIsNone(self.busy())

    def crash(self, jid, group=True):
        st = self.status(jid)
        if group:
            os.killpg(int(st["pgid"]), signal.SIGKILL)
        os.kill(int(st["supervisor_pid"]), signal.SIGKILL)
        return st

    def test_dead_job_is_detected_and_resumed_with_the_same_command(self):
        cmd = "n=$(cat c 2>/dev/null || echo 0); n=$((n+1)); echo $n > c; echo intento $n; sleep 300"
        self.launch("s-dead", kind="script", cmd=cmd)
        self.wait("s-dead", ("running",))
        time.sleep(1)
        self.crash("s-dead")
        st = self.wait("s-dead", ("dead",))
        self.assertFalse(st["alive"])
        res = self.verb("resume", "s-dead")
        self.assertEqual((res["launched"], res["attempt"]), (True, 2))
        time.sleep(1.5)
        with open(os.path.join(self.home, ".enjambre", "jobs", "s-dead", "log")) as f:
            log = f.read()
        self.assertIn("intento 1", log)
        self.assertIn("=== enjambre resume: attempt 2", log)
        self.assertIn("intento 2", log)

    def test_resume_of_an_orphan_kills_the_old_group_first(self):
        self.launch("s-orphan", kind="script", cmd="sleep 300")
        self.wait("s-orphan", ("running",))
        time.sleep(1)
        old = self.crash("s-orphan", group=False)  # supervisor dies, the job keeps running
        st = self.wait("s-orphan", ("dead",))
        self.assertTrue(st["orphan_group"])
        self.verb("resume", "s-orphan")
        time.sleep(1)
        self.assertFalse(self.group_alive(old["pgid"]))  # never two copies of one job

    def test_finished_ok_is_not_resumed(self):
        self.launch("s-done", kind="script", cmd="true")
        self.wait("s-done", ("ok",))
        self.assertFalse(self.verb("resume", "s-done")["launched"])

    def test_a_second_launch_of_the_same_id_is_refused(self):
        self.launch("s-twice", kind="script", cmd="sleep 2")
        self.assertFalse(self.verb("launch", "s-twice")["launched"])

    # -- agents --
    def read(self, name):
        with open(os.path.join(self.home, name)) as f:
            return f.read()

    def write(self, name, text):
        with open(os.path.join(self.home, name), "w") as f:
            f.write(text)

    def test_agent_budget_stop_is_incomplete_budget_with_cli_numbers(self):
        os.makedirs(os.path.join(self.proj, ".venv"))
        self.write("claude-mode", "error_max_budget_usd")
        self.write("claude-cost", "2.01")
        task = "---\nkind: claude\n---\n# TASK\n"
        self.launch("a-budget", kind="claude", task=task, budget_usd=2, max_turns=300,
                    prices={"input": 3.0, "output": 15.0})
        st = self.wait("a-budget", ("ok", "failed", "incomplete"))
        self.assertEqual((st["state"], st["reason"], st["turns"], st["cost_usd"]),
                         ("incomplete", "budget", 31, 2.01))
        with open(os.path.join(self.home, "claude-stdin.txt")) as f:
            self.assertEqual(f.read(), task)  # TASK on stdin, `---` and all (0j)
        argv = self.read("claude-argv.txt")
        self.assertIn("--max-budget-usd 2.00", argv)
        wt = st["worktree"]
        self.assertTrue(os.path.islink(os.path.join(wt, ".venv")))
        files = subprocess.run(["git", "show", "--name-only", "--format=", "HEAD"], cwd=wt,
                               capture_output=True, text=True).stdout.split()
        self.assertIn("nota.txt", files)
        self.assertIn("result.json", files)
        self.assertNotIn(".venv", files)  # the link is excluded, never committed

    def test_heartbeat_estimate_counts_each_message_once(self):
        self.write("claude-mode", "hang")
        self.launch("a-beat", kind="claude", task="x", budget_usd=100,
                    prices={"input": 1.0, "output": 0})
        end = time.time() + 20
        b = {}
        while time.time() < end:
            b = self.status("a-beat").get("latido") or {}
            if b.get("turns"):
                break
            time.sleep(0.3)
        # m1 twice + m2 = 2 messages x 1M input tokens x US$1/M
        self.assertEqual((b.get("turns"), b.get("cost_est_usd"), b.get("last_tool")), (2, 2.0, "Read"))
        self.verb("kill", "a-beat")

    def test_resume_continues_the_session_with_the_remaining_budget(self):
        self.write("claude-mode", "error_max_turns")
        self.write("claude-cost", "0.5")
        self.launch("a-resume", kind="claude", task="t", budget_usd=2)
        st = self.wait("a-resume", ("incomplete",))
        self.assertEqual(st["reason"], "max_turns")
        self.write("claude-mode", "success")
        self.write("claude-cost", "0.25")
        self.assertTrue(self.verb("resume", "a-resume")["launched"])
        st = self.wait("a-resume", ("ok",), t=40)
        argv = self.read("claude-argv.txt").splitlines()
        self.assertIn("--resume sid-1", argv[1])
        self.assertIn("--max-budget-usd 1.50", argv[1])
        self.assertEqual((st["cost_usd"], st["turns"], st["attempt"]), (0.75, 62, 2))

    def test_spent_budget_is_not_resumed_into_a_new_segment(self):
        self.write("claude-mode", "error_max_budget_usd")
        self.write("claude-cost", "2.0")
        self.launch("a-spent", kind="claude", task="t", budget_usd=2)
        self.wait("a-spent", ("incomplete",))
        self.verb("resume", "a-spent")
        st = self.wait("a-spent", ("incomplete",))
        argv = self.read("claude-argv.txt").splitlines()
        self.assertEqual(len(argv), 1)  # no second claude call: nothing left to spend
        self.assertEqual(st["reason"], "budget")


class Units(unittest.TestCase):
    def test_classify(self):
        import jobrun as J
        self.assertEqual(J.classify_agent({"subtype": "error_max_budget_usd"}, 1, 2, 2.0), ("incomplete", "budget"))
        self.assertEqual(J.classify_agent({"subtype": "error_max_turns"}, 1, None, 0), ("incomplete", "max_turns"))
        self.assertEqual(J.classify_agent({"subtype": "success", "permission_denials": [{}]}, 0, None, 0),
                         ("incomplete", "permission_denials"))
        self.assertEqual(J.classify_agent({"subtype": "success"}, 0, None, 0), ("ok", None))
        self.assertEqual(J.classify_agent(None, 143, 2, 0)[0], "failed")

    def test_last_pct_reads_only_the_tail(self):
        import jobrun as J
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            f.write("hash  99.0% viejo\n" + "x" * 10000 + "\nhash  12,5% nuevo\n")
        try:
            self.assertEqual(J.last_pct(f.name), 12.5)
        finally:
            os.remove(f.name)


if __name__ == "__main__":
    unittest.main()
