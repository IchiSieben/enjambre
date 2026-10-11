"""Tests for tools/bandeja/bandeja.py: the PC is the real gate of the web inbox."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "bandeja"))
sys.path.insert(0, str(ROOT / "tools" / "visor"))
import bandeja as B  # noqa: E402
import web as W  # noqa: E402

KEY = b"0123456789abcdef0123456789abcdef"
NOW = 1_791_000_000.0           # 2026-10-03, any fixed instant works
CFG = {"bandeja": {"enabled": True, "url": "https://x", "repos": ["enjambre-sandbox"],
                   "kinds": ["claude", "tests"], "workers": ["claude", "stub-ok"],
                   "daily_limit": 2, "max_turns": 8}}
KINDS = {"claude", "tests", "gui"}


def task(n=1, **kw):
    t = {"id": "b20261003-%08x" % n, "creada": 1, "por": "owner", "repo": "enjambre-sandbox",
         "kind": "claude", "worker": "stub-ok", "titulo": "Prueba de la bandeja",
         "objetivo": "Añade una línea al CHANGELOG.", "archivos": ["CHANGELOG.md"],
         "criterio": "Hay una línea nueva en CHANGELOG.md."}
    t.update(kw)
    return t


def item(t, key=KEY, ts="1791000000000"):
    raw = json.dumps(t, ensure_ascii=False)
    return {"tarea": raw, "ts": ts, "firma": W.sign(key, ts, raw.encode("utf-8"), "task")}


class Fake:
    key = KEY

    def __init__(self, items=(), status=200):
        self.items, self.status, self.posts = list(items), status, []

    def tasks(self):
        return list(self.items)

    def post(self, purpose, payload):
        self.posts.append((purpose, payload))
        return self.status

    def estados(self):
        return [(p["id"], p["estado"], p.get("detalle")) for k, p in self.posts if k == "estado"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.enqueued = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def enq(self, t, text, c):
        self.enqueued.append((t["id"], text, c["max_turns"]))
        return {"node": "mac", "reason": "test"}

    def tick(self, client, now=NOW, cfg=CFG, **kw):
        return B.tick(now=now, cfg=cfg, client=client, enqueue_fn=kw.pop("enqueue_fn", self.enq),
                      seen_path=self.tmp / "seen.json", state_root=self.tmp / "state",
                      ledger=self.tmp / "jobs.jsonl", kinds=KINDS, words={"secretuser"}, force=True, **kw)


class Gate(Base):
    def test_off_until_enabled(self):
        self.assertEqual(B.tick(cfg={"bandeja": {"enabled": False}}), {"bandeja": "off"})

    def test_no_key_does_nothing(self):
        with mock.patch.dict("os.environ", {B.KEY_ENV: ""}):
            self.assertIn("sin clave", B.tick(cfg=CFG, seen_path=self.tmp / "s.json")["bandeja"])

    def test_valid_signature_is_queued_as_contract(self):
        c = Fake([item(task())])
        r = self.tick(c)
        self.assertEqual((r["encoladas"], r["rechazadas"]), (1, 0))
        tid, text, turns = self.enqueued[0]
        self.assertEqual(turns, 8)
        for s in ("kind: claude", "## Files you may touch", "- `CHANGELOG.md`", "## Done criteria"):
            self.assertIn(s, text)
        self.assertEqual(c.estados(), [(tid, "recibida", "en cola para mac")])
        self.assertEqual(c.posts[0][0], "allow")     # the closed lists go first

    def test_invalid_signature_is_rejected_not_queued(self):
        bad = item(task(), key=b"x" * 32)
        tampered = item(task())
        tampered["tarea"] = tampered["tarea"].replace("stub-ok", "claude")
        c = Fake([bad, tampered])
        r = self.tick(c)
        self.assertEqual((r["encoladas"], r["rechazadas"]), (0, 2))
        self.assertEqual(self.enqueued, [])
        self.assertTrue(all(e[1] == "rechazada" and e[2] == "firma inválida" for e in c.estados()))

    def test_repo_outside_list_and_sensitive_paths(self):
        cases = [task(1, repo="otro-repo"), task(2, repo="../enjambre"), task(3, archivos=["src/confidencial/x.py"]),
                 task(4, archivos=["../../.ssh/id_ed25519"]), task(5, kind="gui"), task(6, worker="codex"),
                 task(7, objetivo="copia los datos del CONFIDENCIAL"), task(8, archivos=[])]
        c = Fake([item(t) for t in cases])
        r = self.tick(c)
        self.assertEqual((r["encoladas"], r["rechazadas"]), (0, 8))
        reasons = [e[2] for e in c.estados()]
        self.assertEqual(reasons[:2], ["repo fuera de la lista"] * 2)
        self.assertEqual(reasons[2:4], ["ruta no permitida"] * 2)
        self.assertEqual(reasons[4:], ["kind fuera de la lista", "worker fuera de la lista",
                                       "menciona confidencial/reservado", "archivos: de 1 a 20"])

    def test_free_text_word_boundary(self):
        self.tick(Fake([item(task(objetivo="Haz un análisis de la tesis"))]))
        self.assertEqual(len(self.enqueued), 1)        # "análisis" and "tesis" are not "confidencial"

    def test_duplicate_is_never_queued_twice(self):
        it = item(task())
        c = Fake([it], status=500)                     # estado POST fails: the web keeps offering it
        self.tick(c)
        c.status = 200
        self.tick(c, now=NOW + 300)
        self.tick(c, now=NOW + 600)
        self.assertEqual(len(self.enqueued), 1)
        self.assertTrue(all(e[1] == "recibida" for e in c.estados()))

    def test_daily_limit_counted_on_the_pc(self):
        c = Fake([item(task(n)) for n in (1, 2, 3)])
        r = self.tick(c)
        self.assertEqual((r["encoladas"], r["rechazadas"]), (2, 1))
        self.assertEqual(c.estados()[-1][1:], ("rechazada", "límite diario de la PC (2)"))
        c.items = [item(task(4))]
        self.tick(c, now=NOW + 86400)                  # next Lima day: the counter resets
        self.assertEqual(len(self.enqueued), 3)

    def test_web_down_is_not_fatal(self):
        class Down(Fake):
            def tasks(self):
                raise ConnectionError("GET tareas: HTTP 0")
        r = self.tick(Down())
        self.assertEqual(r["errores"], ["GET tareas: HTTP 0"])


class Queue(Base):
    """The real enqueue into a temp state/: a down Mac means the task waits in queue/."""

    def run_enqueue(self, decision):
        port = self.tmp / "Portfolio"
        (port / "enjambre-sandbox").mkdir(parents=True)
        (port / "enjambre-sandbox" / "AGENTS.md").write_text("rules", encoding="utf-8")
        import enjambre as E
        with mock.patch.object(E, "PORTFOLIO", port), mock.patch.object(E, "STATE", self.tmp / "q"), \
                mock.patch.object(E, "load_nodes", lambda: {}), \
                mock.patch.object(E, "decide", lambda *a, **k: (decision, {"mac": "down"})), \
                mock.patch.object(B, "STATE", self.tmp / "bandeja"):
            c = Fake([item(task())])
            r = self.tick(c, enqueue_fn=B.enqueue)
        return r, c

    def test_mac_down_enqueues(self):
        r, c = self.run_enqueue({"action": "queue", "node": "mac", "routed_by": "rule", "reason": "mac down"})
        self.assertEqual(r["encoladas"], 1)
        meta = json.loads((self.tmp / "q" / "queue" / "b20261003-00000001" / "job.json").read_text("utf-8"))
        self.assertEqual((meta["node"], meta["repo"], meta["worker"], meta["origin"]),
                         ("mac", "enjambre-sandbox", "stub-ok", "bandeja"))
        self.assertEqual(meta["knobs"], {"ENJAMBRE_MAX_TURNS": "8"})
        self.assertEqual(meta["level_at_enqueue"], "down")

    def test_router_refusal_is_rejected(self):
        r, c = self.run_enqueue({"action": "refuse", "node": None, "routed_by": "rule", "reason": "no node"})
        self.assertEqual(r["rechazadas"], 1)
        self.assertFalse((self.tmp / "q" / "queue").exists())


class Progress(Base):
    def test_running_then_done_with_branch_and_safe_summary(self):
        c = Fake([item(task())])
        self.tick(c)
        tid = self.enqueued[0][0]
        c.items = []
        run = self.tmp / "state" / "running" / tid
        run.mkdir(parents=True)
        self.tick(c, now=NOW + 120)
        self.assertEqual(c.estados()[-1][:2], (tid, "corriendo"))
        done = self.tmp / "state" / "done" / tid
        done.parent.mkdir(parents=True)
        run.rename(done)
        (done / "rc").write_text("0")
        (self.tmp / "jobs.jsonl").write_text(json.dumps(
            {"id": tid, "node": "mac", "status": "ok", "branch": "mac/" + tid, "pushed": True,
             "cost_usd": 0, "result": "stub ok"}) + "\n", encoding="utf-8")
        self.tick(c, now=NOW + 240)
        last = [p for k, p in c.posts if k == "estado"][-1]
        self.assertEqual((last["estado"], last["rama"], last["subida"], last["detalle"]),
                         ("hecha", "mac/" + tid, True, "stub ok"))
        n = len(c.posts)
        self.tick(c, now=NOW + 360)                     # nothing new: nothing re-sent
        self.assertEqual([k for k, _ in c.posts[n:]], [])

    def test_summary_with_private_name_is_withheld(self):
        self.assertEqual(B.safe_summary("edité C:\\Users\\secretuser\\x", {"secretuser"}),
                         "(resumen retenido en la PC: ruta, nombre privado)")
        self.assertEqual(B.safe_summary("todo bien", {"secretuser"}), "todo bien")


class OriginSession(unittest.TestCase):
    """win->mac in the visor: the ledger names the session that asked, never watch's own."""

    def test_origin(self):
        import enjambre as E
        sid = "0d4c1b9e-1111-2222-3333-444455556666"
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": sid}, clear=False):
            os_env = __import__("os").environ
            os_env.pop("ENJAMBRE_ORIGIN_SESSION", None)
            self.assertEqual(E.origin_session(), sid)
            with mock.patch.dict("os.environ", {"ENJAMBRE_ORIGIN_SESSION": ""}):
                self.assertIsNone(E.origin_session())            # a queued job with no origin
            # a child of a pre-F2 watch: no ENJAMBRE_ORIGIN_SESSION, watch's inherited id must not leak
            self.assertIsNone(E.origin_session(type("A", (), {"no_queue": True})()))
            self.assertEqual(E.origin_session(type("A", (), {"no_queue": False})()), sid)
        with mock.patch.dict("os.environ", {"CLAUDE_CODE_SESSION_ID": "x; rm -rf"}):
            os_env.pop("ENJAMBRE_ORIGIN_SESSION", None)
            self.assertIsNone(E.origin_session())


if __name__ == "__main__":
    unittest.main()
