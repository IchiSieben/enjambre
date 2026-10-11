"""razones.py: every failed/incomplete job gets a cause, a log path and a test flag (F3)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import razones as R  # noqa: E402


def row(**kw):
    base = {"id": "j1", "node": "mac", "worker": "claude", "repo": "Metallum", "status": "failed", "exit": 1}
    base.update(kw)
    return base


class Explain(unittest.TestCase):
    def test_ok_jobs_have_no_reason(self):
        self.assertIsNone(R.explain(row(status="ok", exit=0)))

    def test_causes_in_order(self):
        self.assertEqual(R.explain(row(status="incomplete", permission_denials=[{"tool": "Bash"}, {"tool": "Write"}, {"tool": "Bash"}])),
                         "permisos denegados: Bash, Write")
        self.assertEqual(R.explain(row(status="incomplete", reason="budget", budget_usd=2)), "presupuesto agotado (tope US$2.00)")
        self.assertEqual(R.explain(row(reason="killed", exit=-15)), "muerto: supervisor terminado (exit -15)")
        self.assertEqual(R.explain(row(exit=255)), "mac inalcanzable (ssh exit 255)")
        self.assertEqual(R.explain(row(exit=2), "setup\nusage: x\n__main__.py: error: unrecognized arguments: --id\n"),
                         "exit 2: __main__.py: error: unrecognized arguments: --id")
        self.assertEqual(R.explain(row(note="gitleaks finding (or scanner missing): not pushed")),
                         "rama sin subir: gitleaks finding (or scanner missing): not pushed")
        self.assertEqual(R.explain(row(exit=1), "hash 100%\nmanifest: 570 faltan\n{\"files\": 1}\n"),
                         "exit 1; última línea: manifest: 570 faltan")
        self.assertEqual(R.explain(row(exit=3)), "exit 3, sin log guardado")

    def test_stream_json_agent_logs_are_not_scanned_for_errors(self):
        log = '{"type":"user","content":"Error: something in a tool result"}\n{"session_id":"x","total_cost_usd":1}\n'
        self.assertEqual(R.explain(row(exit=1), log), "exit 1 (ver log)")


class Enrich(unittest.TestCase):
    def test_backfill_adds_fields_keeps_the_rest_and_backs_up(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "jobs" / "j2").mkdir(parents=True)
            (t / "jobs" / "j2" / "log").write_text("fatal: could not read Username\n", encoding="utf-8")
            led = t / "jobs.jsonl"
            rows = [row(id="j1", status="ok", exit=0), row(id="j2", exit=128, worker="script"),
                    row(id="j3", status="incomplete", reason="budget", budget_usd=1.5),
                    row(id="s1", repo="enjambre-sandbox", worker="stub-ok", status="ok", exit=0),
                    row(id="e3-presupuesto", status="incomplete", reason="budget")]
            led.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            got = R.backfill(led, t)
            out = {r["id"]: r for r in map(json.loads, led.read_text(encoding="utf-8").splitlines())}
            self.assertEqual(got["bad_without_reason"], 0)
            self.assertEqual(out["j1"], rows[0])                        # ok rows untouched
            self.assertEqual(out["j2"]["reason"], "exit 128: fatal: could not read Username")
            self.assertTrue(out["j2"]["log_path"].endswith("jobs/j2/log"))
            self.assertEqual((out["j3"]["reason"], out["j3"]["reason_code"]), ("presupuesto agotado (tope US$1.50)", "budget"))
            self.assertTrue(out["s1"]["test"] and out["e3-presupuesto"]["test"])
            self.assertNotIn("test", out["j2"])
            self.assertEqual(len(list(t.glob("jobs.jsonl.bak-*"))), 1)
            self.assertEqual(R.backfill(led, t)["changed"], 0)         # idempotent

    def test_a_written_sentence_is_never_replaced(self):
        e = R.enrich(row(reason="mac apagada por mantenimiento"))
        self.assertEqual(e["reason"], "mac apagada por mantenimiento")


if __name__ == "__main__":
    unittest.main()
