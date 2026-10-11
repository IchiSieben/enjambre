"""Prompt 20 step 1: the priority model and the per-OS prefixes. Nothing here runs a command:
worker.sh is read as text, jobrun's command is built, Windows calls are only parsed."""
import base64
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "visor"))
import collect as C  # noqa: E402
import jobrun as J  # noqa: E402
import scheduler as S  # noqa: E402

CFG = S.load()


class Model(unittest.TestCase):
    def test_task_frontmatter(self):
        f = S.task_flags("---\nkind: claude\npriority: baja\nresumable: true\npreempt_between_turns: yes\n---\n# x")
        self.assertEqual(f, {"priority": "baja", "resumable": True, "preempt_between_turns": True})
        self.assertEqual(S.task_flags("# no frontmatter")["priority"], None)
        self.assertIsNone(S.task_flags("---\npriority: urgentisimo\n---\n")["priority"])   # unknown class: unsaid

    def test_defaults_scripts_preempt_agents_do_not(self):
        self.assertEqual(S.job_model("script", None), {"priority": "normal", "preemptable": True,
                                                       "resumable": False, "between_turns": False})
        a = S.job_model("claude", "baja")
        self.assertFalse(a["preemptable"])
        self.assertTrue(S.job_model("claude", "baja", between_turns=True)["preemptable"])

    def test_queue_order_priority_then_fifo(self):
        q = [{"id": "n1", "priority": "normal", "enqueued": "1"}, {"id": "b1", "priority": "baja", "enqueued": "0"},
             {"id": "a1", "priority": "alta", "enqueued": "2"}, {"id": "n0", "enqueued": "0"}]
        self.assertEqual([m["id"] for m in S.order(q)], ["a1", "n0", "n1", "b1"])


class Prefixes(unittest.TestCase):
    def test_macos_prefix_from_config(self):
        self.assertEqual(S.os_prefix(CFG, "baja", "macos"), ["taskpolicy", "-b", "nice", "-n", "15"])
        self.assertEqual(S.os_prefix(CFG, "nocturna", "macos"), ["taskpolicy", "-b", "nice", "-n", "19"])
        self.assertEqual(S.os_prefix(CFG, "alta", "macos"), [])        # alta runs at the OS default
        self.assertEqual(S.os_prefix(CFG, "baja", "windows"), [])      # Windows sets the class after start

    def test_jobrun_script_runs_inside_caffeinate_with_the_prefix(self):
        cmd = J.script_cmd({"prio_prefix": S.os_prefix(CFG, "baja", "macos")}, "yes > /dev/null")
        self.assertEqual(cmd[:7], ["caffeinate", "-is", "taskpolicy", "-b", "nice", "-n", "15"])
        self.assertEqual(cmd[-3:], ["/bin/bash", "-c", "yes > /dev/null"])
        self.assertEqual(J.script_cmd({}, "x"), ["caffeinate", "-is", "/bin/bash", "-c", "x"])

    def test_worker_sh_setsid_helper_and_prefix_guard(self):
        w = (ROOT / "worker.sh").read_text(encoding="utf-8")
        self.assertIn("os.setsid()", w)                         # macOS has no setsid binary
        self.assertIn("ENJAMBRE_PRIO_B64", w)
        self.assertIn('echo "$grp claude -p', w)                # the agent runs inside the new group
        self.assertIn("*[!a-z0-9\\ -]*", w)                     # nothing but `taskpolicy -b nice -n 15`
        enc = base64.b64encode(" ".join(S.os_prefix(CFG, "baja", "macos")).encode()).decode()
        self.assertEqual(base64.b64decode(enc).decode(), "taskpolicy -b nice -n 15")

    def test_windows_rules(self):
        self.assertEqual(C.win_rule(CFG["prioridad_so"]["baja"]["windows"]), ("BelowNormal", True))
        self.assertEqual(C.win_rule(CFG["prioridad_so"]["nocturna"]["windows"]), ("Idle", True))
        self.assertEqual(C.guard_call("x", lambda: 1 / 0), (False, "x: ZeroDivisionError"))


if __name__ == "__main__":
    unittest.main()
