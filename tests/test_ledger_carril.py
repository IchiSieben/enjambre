"""kind tests: the job's verdict is the lane's exit (tests-cmd), not claude's.

cert-media-4 (2026-10-15): media.sh finished with exit 21, the agent said so in its report,
claude exited 0 and the ledger recorded `ok`. These tests run a fake lane that really exits 23,
put its output in the stream the way Claude Code reports a Bash result, and check that the
job ends `failed` with `lane rc=23: <last line>` and the ledger's exit is 23.
The end-to-end version through a real supervisor is tests/node/test_jobrun_node.py (Mac).
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jobrun as J  # noqa: E402
import largos as L  # noqa: E402

TESTS_CMD = "bash tools/verify.sh"
FAKE_LANE = """import sys
print("verify.sh: lane=media")
print("[cert-studio] CLIP FAILED: waiting for locator('.certcard')")
print("media.sh: finished, exit 23")
sys.exit(23)
"""


def run_fake_lane(code: str = FAKE_LANE) -> tuple:
    with tempfile.TemporaryDirectory() as d:
        lane = Path(d) / "lane.py"
        lane.write_text(code, encoding="utf-8")
        r = subprocess.run([sys.executable, str(lane)], capture_output=True, text=True)
    return r.returncode, r.stdout


def bash_result(tool_id: str, rc: int, out: str) -> dict:
    """What Claude Code puts in the stream for a Bash call: `Exit code N` first when N != 0."""
    text = (f"Exit code {rc}\n" if rc else "") + out
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": text, "is_error": bool(rc)}]}}


def bash_call(tool_id: str, cmd: str, mid: str) -> dict:
    return {"type": "assistant", "message": {"id": mid, "usage": {}, "content": [
        {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": cmd, "timeout": 600000}}]}}


def stream_of(events: list, tests_cmd: str | None = TESTS_CMD) -> "J.StreamState":
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "log.1"
        log.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        s = J.StreamState(str(log), None, tests_cmd)
        s.poll()
    return s


class LaneVerdict(unittest.TestCase):
    def test_fake_lane_exit_23_makes_an_ok_agent_failed(self):
        rc, out = run_fake_lane()
        self.assertEqual(rc, 23)
        s = stream_of([bash_call("t1", TESTS_CMD, "m1"), bash_result("t1", rc, out),
                       {"type": "result", "subtype": "success", "is_error": False, "result": "fallo"}])
        self.assertEqual(s.lane, {"rc": 23, "last": "media.sh: finished, exit 23"})
        agent = J.classify_agent(s.result, 0, 2, 0.1)
        self.assertEqual(agent, ("ok", None))  # what the ledger used to say
        self.assertEqual(J.classify_lane(*agent, s.lane),
                         ("failed", "lane rc=23: media.sh: finished, exit 23"))

    def test_the_last_call_decides(self):
        # media lane: exit 90 = still running, then the same command again until it finishes
        s = stream_of([bash_call("t1", TESTS_CMD, "m1"), bash_result("t1", 90, "running\n"),
                       bash_call("t2", TESTS_CMD, "m2"), bash_result("t2", 0, "media.sh: finished, exit 0\n")])
        self.assertEqual(s.lane, {"rc": 0, "last": "media.sh: finished, exit 0"})
        self.assertEqual(J.classify_lane("ok", None, s.lane), ("ok", None))

    def test_other_bash_calls_and_other_kinds_are_ignored(self):
        events = [bash_call("t1", "ls", "m1"), bash_result("t1", 2, "ls: nope\n")]
        self.assertIsNone(stream_of(events).lane)
        events = [bash_call("t1", TESTS_CMD, "m1"), bash_result("t1", 23, "x\n")]
        self.assertIsNone(stream_of(events, tests_cmd=None).lane)  # kind claude: no lane

    def test_a_lane_that_never_ran_is_not_ok(self):
        self.assertEqual(J.classify_lane("ok", None, None), ("failed", "lane: tests-cmd never ran"))
        self.assertEqual(J.classify_lane("incomplete", "budget", None), ("incomplete", "budget"))

    def test_a_failing_lane_wins_over_incomplete(self):
        self.assertEqual(J.classify_lane("incomplete", "permission_denials", {"rc": 23, "last": "boom"}),
                         ("failed", "lane rc=23: boom"))

    def test_list_content_and_empty_output(self):
        block = {"type": "tool_result", "is_error": True,
                 "content": [{"type": "text", "text": "Exit code 23\nuno\ndos"}]}
        self.assertEqual(J.lane_result(block), {"rc": 23, "last": "dos"})
        self.assertEqual(J.lane_result({"content": "Exit code 23\n", "is_error": True}),
                         {"rc": 23, "last": ""})
        self.assertEqual(J.classify_lane("ok", None, {"rc": 23, "last": ""}), ("failed", "lane rc=23"))

    def test_tests_cmd_comes_from_the_spec_or_the_allowlist(self):
        allowed = L.allowed_tools("tests", TESTS_CMD)
        self.assertEqual(J.tests_cmd_of({"allowed": allowed}), TESTS_CMD)
        self.assertEqual(J.tests_cmd_of({"tests_cmd": " x.sh ", "allowed": allowed}), "x.sh")
        self.assertIsNone(J.tests_cmd_of({"allowed": "Read,Edit"}))

    def test_ledger_exit_is_the_lanes_and_keeps_claudes_rc(self):
        meta = {"id": "fake-lane", "node": "mac", "kind": "tests", "repo": "r",
                "launched": "2026-10-15T15:00:00+00:00"}
        st = {"state": "failed", "reason": "lane rc=23: media.sh: finished, exit 23", "rc": 0,
              "lane_rc": 23, "start": "2026-10-15T10:00:00-05:00", "end": "2026-10-15T10:01:00-05:00"}
        row = L.ledger_entry(meta, st, None, False)
        self.assertEqual((row["status"], row["exit"], row["agent_rc"], row["reason"]),
                         ("failed", 23, 0, "lane rc=23: media.sh: finished, exit 23"))
        st.pop("lane_rc")
        row = L.ledger_entry(meta, dict(st, state="ok", reason=None), None, False)
        self.assertEqual((row["exit"], row["agent_rc"]), (0, None))


if __name__ == "__main__":
    unittest.main()
