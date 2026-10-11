"""Tests for the parts of enjambre that need no node: contract, refusal rule, ledger.

    python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import enjambre as E  # noqa: E402

GOOD_TASK = "# TASK\n\n## Files you may touch\n- CHANGELOG.md\n\n## Done criteria\n- [ ] entry added\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "proj"
        self.repo.mkdir()
        (self.repo / "AGENTS.md").write_text("# rules\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, name="TASK-add-entry.md", text=GOOD_TASK):
        p = self.root / name
        p.write_text(text, encoding="utf-8")
        return p


class Contract(Base):
    def test_valid_contract_returns_id(self):
        self.assertEqual(E.validate(self.repo, self.task(), "claude"), "add-entry")

    def test_bad_filename(self):
        with self.assertRaises(E.ContractError):
            E.validate(self.repo, self.task("tarea.md"), "claude")

    def test_uppercase_id_rejected(self):
        with self.assertRaises(E.ContractError):
            E.validate(self.repo, self.task("TASK-Add.md"), "claude")

    def test_missing_sections(self):
        with self.assertRaisesRegex(E.ContractError, "Done criteria"):
            E.validate(self.repo, self.task(text="# TASK\n## Files you may touch\n"), "claude")

    def test_missing_agents_md(self):
        (self.repo / "AGENTS.md").unlink()
        with self.assertRaisesRegex(E.ContractError, "AGENTS.md"):
            E.validate(self.repo, self.task(), "claude")

    def test_missing_repo(self):
        with self.assertRaisesRegex(E.ContractError, "repo not found"):
            E.validate(self.root / "nope", self.task(), "claude")


class Refusal(Base):
    def make(self, name):
        d = self.root / name
        d.mkdir(parents=True)
        (d / "AGENTS.md").write_text("x", encoding="utf-8")
        return d

    def test_external_worker_refused_on_confidencial_path(self):
        d = self.make("Plataforma-confidencial/app")
        for w in ("agy", "codex"):
            with self.assertRaisesRegex(E.ContractError, "refused"):
                E.validate(d, self.task(), w)

    def test_case_insensitive_reservado(self):
        with self.assertRaisesRegex(E.ContractError, "refused"):
            E.validate(self.make("Reservado-tools"), self.task(), "codex")

    def test_claude_allowed_on_confidencial_path(self):
        self.assertEqual(E.validate(self.make("confidencial-data"), self.task(), "claude"), "add-entry")

    def test_refusal_happens_before_repo_checks(self):
        # Even a path that does not exist is refused first: nothing is read, no ssh happens.
        with self.assertRaisesRegex(E.ContractError, "refused"):
            E.validate(self.root / "ghost-confidencial", self.task(), "agy")

    def test_run_exits_3_without_contacting_node(self):
        calls = []
        orig = E.sh
        E.sh = lambda *a, **k: calls.append(a)  # would record any ssh/scp
        try:
            rc = E.main(["run", "--node", "mac", "--repo", "definitely-missing-repo",
                         "--task", str(self.task())])
        finally:
            E.sh = orig
        self.assertEqual(rc, 3)
        self.assertEqual(calls, [])


class Ledger(Base):
    def test_one_line_per_run_and_append_only(self):
        led = self.root / "jobs.jsonl"
        E.append_ledger({"id": "a", "exit": 0}, led)
        E.append_ledger({"id": "b", "exit": 1, "note": "ñ"}, led)
        lines = led.read_bytes().split(b"\n")
        self.assertEqual(lines[-1], b"")            # ends with newline
        self.assertNotIn(b"\r", led.read_bytes())   # LF only, also on Windows
        recs = [json.loads(x) for x in lines[:-1]]
        self.assertEqual([r["id"] for r in recs], ["a", "b"])
        self.assertEqual(recs[1]["note"], "ñ")


class Status(Base):
    def test_exit_zero_with_denials_is_incomplete(self):
        d = {"permission_denials": [{"tool": "Edit", "target": "home/.claude/statusline.sh"}]}
        self.assertEqual(E.job_status(0, d), "incomplete")

    def test_denials_win_over_failure_too(self):
        self.assertEqual(E.job_status(1, {"permission_denials": [{"tool": "Bash"}]}), "incomplete")

    def test_clean_runs(self):
        self.assertEqual(E.job_status(0, {"permission_denials": []}), "ok")
        self.assertEqual(E.job_status(0, None), "ok")
        self.assertEqual(E.job_status(2, {}), "failed")

    def test_worker_extracts_denials_from_out_json(self):
        # Run the exact Python embedded in worker.sh against a claude-shaped out.json.
        import subprocess
        src = (Path(E.__file__).parent / "worker.sh").read_text(encoding="utf-8")
        snippet = src.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        out = self.root / "out.json"
        out.write_text(json.dumps({
            "is_error": False, "result": "done", "total_cost_usd": 0.1, "usage": {},
            "permission_denials": [{"tool_name": "Edit", "tool_use_id": "x",
                                    "tool_input": {"file_path": "/Users/u/Documents/Portfolio/wt/sb-x/home/.claude/s.sh",
                                                   "old_string": "a" * 5000}}]}), encoding="utf-8")
        r = subprocess.run([sys.executable, "-", str(out)], input=snippet, text=True,
                           capture_output=True, check=True)
        detail = json.loads(r.stdout)
        self.assertEqual(detail["permission_denials"],
                         [{"tool": "Edit", "target": "home/.claude/s.sh"}])
        self.assertEqual(E.job_status(0, detail), "incomplete")

    def test_worker_without_denials_key(self):
        import subprocess
        src = (Path(E.__file__).parent / "worker.sh").read_text(encoding="utf-8")
        snippet = src.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        out = self.root / "out.json"
        out.write_text('{"is_error":false,"result":"stub ok","usage":{}}', encoding="utf-8")
        r = subprocess.run([sys.executable, "-", str(out)], input=snippet, text=True,
                           capture_output=True, check=True)
        self.assertEqual(E.job_status(0, json.loads(r.stdout)), "ok")


class Render(Base):
    def test_remote_cmd_quotes_accents_and_spaces(self):
        import shlex
        cmd = E.render_remote_cmd("laptop/Proyecto-3d/Proyecto/MóduloMecánico/a b.stl",
                                  "render-a-b-1.png", "CPU", 32, "1280x720", "mm")
        self.assertIn("\"$HOME/Documents/Portfolio/\"'laptop/Proyecto-3d/Proyecto/MóduloMecánico/a b.stl'", cmd)
        self.assertIn("--device CPU --samples 32", cmd)
        self.assertIn("tmux-run.sh", cmd)   # long render survives an ssh drop
        shlex.split(cmd)                     # balanced quoting

    def test_render_refuses_local_node(self):
        self.assertEqual(E.main(["render", "x.stl", "--node", "win"]), 2)


if __name__ == "__main__":
    unittest.main()
