"""`--engine local` without a node: a fake modelos-mac client stands in for the tunnel."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine_local as L  # noqa: E402


class FakeClient:
    def __init__(self, state="done"):
        self.state, self.calls = state, []

    def run(self, task, prompt="", params=None, file=None, node="mac"):
        self.calls.append((task, prompt, params, node))
        job = {"id": "j1", "state": self.state, "run_s": 3.2, "wait_s": 0.1, "load_s": 2.0,
               "peak_footprint_mb": 5700.0, "error": None if self.state == "done" else "rc=1: boom"}
        return job, ({"text": "```py\nx = 1\n```"} if self.state == "done" else None)


class EngineLocal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "local"

    def tearDown(self):
        self.tmp.cleanup()

    def test_done_writes_answer_and_ledger_fields(self):
        c = FakeClient()
        L.HERE = Path(self.tmp.name)
        e = L.run("t1", "# TASK", "mac", "mac", self.out, client=c)
        self.assertEqual(c.calls[0][0], "text")
        self.assertEqual(c.calls[0][3], "mac")
        self.assertEqual(e["engine"], "local")
        self.assertEqual(e["exit"], 0)
        self.assertEqual(e["cost_usd"], 0.0)
        self.assertEqual((self.out / "t1.md").read_text(encoding="utf-8"), "```py\nx = 1\n```")
        self.assertEqual(e["output"], "local/t1.md")

    def test_failed_job_exits_1_and_keeps_error(self):
        L.HERE = Path(self.tmp.name)
        e = L.run("t2", "# TASK", "mac", "mac", self.out, client=FakeClient("failed"))
        self.assertEqual(e["exit"], 1)
        self.assertIsNone(e["output"])
        self.assertIn("boom", e["note"])

    def test_missing_client_is_a_clear_error(self):
        with self.assertRaisesRegex(FileNotFoundError, "modelos-mac"):
            L.load_client(Path(self.tmp.name) / "nope.py")


if __name__ == "__main__":
    unittest.main()
