"""Tests for tools/visor/web.py: the allowlist, the second wall and the signed upload."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools" / "visor"))
import build as B  # noqa: E402
import web as W  # noqa: E402

KEY = b"k" * 32
# Shared with tools/bandeja/php/tests (same vector, both sides must agree byte for byte).
VECTOR = {"key": "0123456789abcdef0123456789abcdef", "ts": "1700000000", "purpose": "ingest",
          "body": '{"a":1,"b":"ñ"}',
          "hex": "a0113f08c353649bb7339be9064aa3b3aeb20acb36e96c0071c091bb14e09d63"}


def snapshot():
    return {
        "built": 1.0e9, "today": "2026-10-07", "thresholds": {"quota-5h": 90, "otra": 1},
        "quota": {"five_pct": 17, "week_pct": 38, "ts": 1.0e9, "node": "win"},
        "totals": {"today": {"in": 1, "out": 2, "cw": 3, "cr": 4, "msgs": 5, "cost": 1.5, "unpriced_msgs": 0},
                   "week": {"in": 1, "out": 2, "cw": 3, "cr": 4, "msgs": 5, "cost": 1.5, "unpriced_msgs": 0}},
        "queue": {"queue": [{"id": "secret-work-123", "node": "mac", "repo": "secret-work"}], "running": [], "done": []},
        "running_models": {"mac": None},
        "nodes": {"win": {"cfg": {"host": "local", "ram_gb": 64}, "alive": True, "stale": False, "age_s": 3,
                          "error": "", "presence": {"level": "local"},
                          "sessions": [{"project": "secret-work", "cwd": "C:\\Users\\someuser\\secret-work",
                                        "name": "ventana-privada", "status": "busy", "started": 1.0e9}],
                          "disks": [{"mount": "C:\\", "total_gb": 1000, "free_gb": 50}],
                          "memory": {"free_pct": 50, "total_gb": 64, "source": "GlobalMemoryStatusEx"},
                          "mounts": [{"path": "Z:", "share": "privado", "ok": True}], "tmux": ["sesion-privada"],
                          "usage": {"today": {}, "week": {}, "by_day": {"2026-10-07": {"cost": 1}},
                                    "projects_today": {"secret-work": {}}},
                          # a field nobody listed: must never reach the web
                          "nuevo_campo": "C:\\Users\\someuser\\secret-work\\notas.txt"}},
        "projects": {"secret-work": {"sessions": 2, "windows": 1, "cost": 3.0,
                                     "timeline": [{"start": 1, "end": 2, "node": "win", "cwd": "/Users/x"}],
                                     "jobs": {"n": 1, "ok": 1, "cost": 0.1, "nodes": {"mac": 1}},
                                     "subs": [{"start": 1, "end": 2, "node": "win", "type": "redactor",
                                               "desc": "descripcion-privada", "sid": "sid-privado", "cost": 0.2}]},
                     "enjambre": {"sessions": 1, "windows": 1, "cost": 1.0, "timeline": []}},
        "jobs": [{"id": "b-1", "node": "mac", "type": "claude -p", "status": "ok", "start": "2026-10-07T01:00:00Z",
                  "repo": "secret-work", "branch": "mac/secret-work-1", "duration_s": 3,
                  "result": "resumen-privado", "route_reason": "motivo-privado", "origin_session": "sid-privado"}],
        "job_detail": {"b-1": {"log": {"run.out": "log-privado"}, "title": "titulo-privado"}},
        "compare": [], "models_inventory": {"win": {"models": [], "measurements": [
            {"key": "llm", "task": "text", "date": "2026-10-07", "ts": 1.0e9, "note": "nota-privada"}],
            "jobs": [{"id": "job-privado", "task": "text", "state": "done", "created": 1.0e9}]}}, "history_status": {}, "ledger_n": 1, "ledger_incomplete": 0,
    }


class Allowlist(unittest.TestCase):
    def test_unknown_field_and_paths_never_leave(self):
        txt = json.dumps(W.public_snapshot(snapshot(), {"enjambre"}, KEY), ensure_ascii=False)
        for bad in ("someuser", "secret-work", "ventana-privada", "notas.txt", "nuevo_campo", "privado",
                    "sesion-privada", "GlobalMemoryStatusEx", "mac/", "C:\\\\", "resumen-privado", "motivo-privado",
                    "sid-privado", "log-privado", "titulo-privado", "nota-privada", "job-privado",
                    "descripcion-privada", "b-1"):
            self.assertNotIn(bad, txt, bad)
        self.assertIn('"enjambre"', txt)                 # public name kept
        self.assertNotIn('"otra"', txt)                  # thresholds: only quota-5h

    def test_aliases_stable_and_salted(self):
        a1 = W.public_snapshot(snapshot(), set(), KEY)["projects"].keys()
        a2 = W.public_snapshot(snapshot(), set(), KEY)["projects"].keys()
        a3 = W.public_snapshot(snapshot(), set(), b"x" * 32)["projects"].keys()
        self.assertEqual(sorted(a1), sorted(a2))
        self.assertNotEqual(sorted(a1), sorted(a3))
        self.assertTrue(all(k.startswith("proyecto-") for k in a1))

    def test_session_alias_matches_project_alias(self):
        p = W.public_snapshot(snapshot(), set(), KEY)
        self.assertIn(p["nodes"]["win"]["sessions"][0]["project"], p["projects"])
        self.assertIn(p["jobs"][0]["repo"], p["projects"])      # the job filter uses the same alias

    def test_new_fields_keep_only_numbers_and_words(self):
        p = W.public_snapshot(snapshot(), set(), KEY)
        sub = next(iter(p["projects"].values()))["subs"][0]
        self.assertEqual((sub["type"], sub["cost"]), ("redactor", 0.2))
        m = p["models_inventory"]["win"]
        self.assertEqual((m["measurements"][0]["key"], m["measurements"][0]["ts"]), ("llm", 1.0e9))
        self.assertEqual(m["jobs"][0]["task"], "text")

    def test_page_renders_the_public_copy(self):
        html = B.render_html(W.public_snapshot(snapshot(), {"enjambre"}, KEY))
        self.assertNotIn("secret-work", html)
        self.assertIn("@font-face", html)


class SecondWall(unittest.TestCase):
    def test_kinds(self):
        w = {"secret-work"}
        self.assertEqual(W.leak_check('{"a":"análisis de datos"}', w), [])
        self.assertIn("palabra confidencial/reservado", W.leak_check('{"a":"repo CONFIDENCIAL"}', w))
        self.assertIn("ruta", W.leak_check(json.dumps({"a": "C:\\Users\\x"}), w))
        self.assertIn("ruta", W.leak_check('{"a":"/Users/x"}', w))
        self.assertIn("IP", W.leak_check('{"a":"192.0.2.20"}', w))
        self.assertIn("nombre privado", W.leak_check('{"a":"Secret-Work"}', w))
        self.assertEqual(W.leak_check('{"a":"secret-workers"}', w), [])   # word-bounded

    def test_safe_payload_blocks_a_leak_through_an_allowed_field(self):
        s = snapshot()
        s["nodes"]["win"]["sessions"][0]["status"] = "secret-work"    # passes _word, caught by the wall
        with self.assertRaises(W.LeakError):
            W.safe_payload(s, {"enjambre"}, KEY)

    def test_safe_payload_clean(self):
        body = W.safe_payload(snapshot(), {"enjambre"}, KEY, extra_words={"macuser"})
        self.assertLess(len(body), W.MAX_BODY)


class Upload(unittest.TestCase):
    def test_sign_vector(self):
        v = VECTOR
        h = W.sign(v["key"].encode(), v["ts"], v["body"].encode("utf-8"), v["purpose"])
        self.assertEqual(h, v["hex"])
        self.assertNotEqual(h, W.sign(v["key"].encode(), v["ts"], v["body"].encode("utf-8"), "poll"))

    def test_dormant_until_configured(self):
        self.assertEqual(W.maybe_upload(snapshot(), {}, 1e9, cfg={}), "off")
        self.assertEqual(W.maybe_upload(snapshot(), {}, 1e9, cfg={"web": {"upload": True, "url": "https://x"}}),
                         "sin clave (%s)" % W.INGEST_ENV)

    def test_due_every_ten_minutes(self):
        with tempfile.TemporaryDirectory() as t:
            st = Path(t) / "s.json"
            self.assertTrue(W.due({"every_min": 10}, 1000, st))
            st.write_text(json.dumps({"ok_at": 1000}))
            self.assertFalse(W.due({"every_min": 10}, 1000 + 300, st))
            self.assertTrue(W.due({"every_min": 10}, 1000 + 600, st))

    def test_post_sends_signature_headers(self):
        seen = {}

        class R:
            status = 200
            def read(self, n): return b"ok"
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def opener(req, timeout):
            seen.update(dict(req.header_items()), body=req.data)
            return R()
        st, _ = W.post("https://x/api/ingest.php", b'{"a":1}', KEY, "ingest", opener=opener)
        self.assertEqual(st, 200)
        self.assertEqual(seen["X-firma"], W.sign(KEY, seen["X-ts"], b'{"a":1}', "ingest"))
        self.assertRegex(seen["X-ts"], r"^\d{13}$")     # epoch ms, what lib.inc.php accepts


try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class Render(unittest.TestCase):
    """snapshot -> HTML -> a real browser at phone width (Edge through Playwright)."""

    def test_page_runs_without_errors_on_a_phone(self):
        s = snapshot()
        s["projects"]["enjambre"]["timeline"] = [{"start": 1.0e9, "end": 1.0e9 + 3600, "active_s": 3000,
                                                  "prompts": 3, "responses": 9, "subagents": 1, "tokens": 5000,
                                                  "cost": 0.5, "node": "win"}]
        s["models_inventory"] = {"win": {"budget_mb": 12000, "models": [
            {"key": "whisper-x", "task": "transcribe", "gb": 1.5, "downloaded": True, "default": True,
             "last": {"ok": True, "quality_label": "buena", "speed": 30, "speed_unit": "x", "footprint_mb": 2048,
                      "date": "2026-10-05"}, "last_job": {"task": "transcribe", "state": "done", "created": 1.0e9, "run_s": 12}}]}}
        html = B.render_html(W.public_snapshot(s, {"enjambre"}, KEY))
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "v.html"
            f.write_text(html, encoding="utf-8")
            with sync_playwright() as p:
                try:
                    br = p.chromium.launch(channel="msedge")
                except Exception:
                    br = p.chromium.launch()
                pg = br.new_page(viewport={"width": 390, "height": 800})
                errors = []
                pg.on("pageerror", lambda e: errors.append(str(e)))
                pg.goto(f.as_uri())
                pg.wait_for_selector("#historia")
                for sec in ("vivo", "consumo", "proyectos", "modelos", "historia"):
                    self.assertTrue(pg.is_visible("#" + sec), sec)
                self.assertLessEqual(pg.evaluate("document.documentElement.scrollWidth"), 390)
                self.assertIn("whisper-x", pg.inner_text("#modelos"))
                pg.click('tr[data-p="enjambre"]')
                self.assertIn("Línea de tiempo", pg.inner_text("#detail"))
                self.assertEqual(pg.locator("#detail svg rect[data-k]").count(), 1)   # not the clipPath rect
                pg.click("#theme")
                self.assertEqual(pg.evaluate("document.documentElement.dataset.theme"), "light")
                br.close()
        self.assertEqual(errors, [])


class AliasAndCauses(unittest.TestCase):
    """F3: public aliases chosen by the owner; causes and logs only when clean."""

    def test_alias_wins_over_code_and_keep_wins_over_alias(self):
        al = W.aliaser({"enjambre"}, b"k" * 32, {"farmacias": "radar-de-precios", "enjambre": "otro"})
        self.assertEqual(al("farmacias"), "radar-de-precios")
        self.assertEqual(al("enjambre"), "enjambre")
        self.assertTrue(al("carpeta-x").startswith("proyecto-"))

    def test_alias_equal_to_folder_publishes_the_name_other_aliases_do_not(self):
        pub = W.published({"enjambre"}, {"Botanica": "botanica", "farmacias": "radar-de-precios"})
        self.assertEqual(pub, {"enjambre", "Botanica"})

    def test_load_aliases_accepts_only_plain_slugs(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "a.toml"
            f.write_text('[alias]\n"a" = { alias = "bien-1", estado = "propuesto" }\n'
                         '"b" = { alias = "Mal Nombre" }\n"c" = { alias = "../x" }\n', encoding="utf-8")
            self.assertEqual(W.load_aliases(f), {"a": "bien-1"})

    def test_why_whole_when_clean_code_only_when_not(self):
        words = {"Metallum"}
        self.assertEqual(W.clean_why("exit 2: error: unrecognized arguments: --id", words),
                         "exit 2: error: unrecognized arguments: --id")
        self.assertEqual(W.clean_why("exit 1; última línea: manifest -> /Users/x/m.tsv", words),
                         "exit 1 (detalle solo en local)")
        self.assertEqual(W.clean_why("rama de Metallum sin subir", words), "fallo (detalle solo en local)")

    def test_agent_logs_never_published_and_escapes_checked(self):
        self.assertIsNone(W.clean_log('{"type":"user","message":"hola"}\n{"session_id":"x"}\n', set()))
        self.assertIsNone(W.clean_log("ok\nC:\\\\Users\\\\x\\\\y\n", set()))
        self.assertIsNone(W.clean_log("sed 's/a\\\\\\\\b/' x\n", set()))       # becomes \\\\ in JSON
        self.assertEqual(W.clean_log("fatal: could not read Username\n", set()), "fatal: could not read Username")


class Tls(unittest.TestCase):
    def test_upload_and_bandeja_verify_with_certifi_when_installed(self):
        """The Windows store fails the Let's Encrypt YE chain here (DECISIONES 221): both clients
        must go through https_open, which keeps verification on with certifi's bundle."""
        from unittest import mock
        sys.path.insert(0, str(HERE.parent / "tools" / "bandeja"))
        import bandeja as BJ
        self.assertIs(BJ.Client("https://x", b"k").open, W.https_open)
        W._CTX.clear()
        with mock.patch("urllib.request.urlopen") as uo:
            W.https_open("req", timeout=5)
        ctx = uo.call_args.kwargs["context"]
        try:
            import certifi  # noqa: F401
            import ssl
            self.assertIsInstance(ctx, ssl.SSLContext)
            self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        except ImportError:
            self.assertIsNone(ctx)
        W._CTX.clear()


if __name__ == "__main__":
    unittest.main()
