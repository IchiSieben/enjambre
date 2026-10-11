"""puente: the server (agent/puente.py, stub engine, in a thread) and the Windows client
(puente_cli.py). No ssh, no model: requests go straight to 127.0.0.1 with urllib."""
import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import puente_cli as PC  # noqa: E402

KEY = "k" * 64


def load_server(tmp: Path):
    os.environ.update(PUENTE_ROOT=str(tmp / "root"), PUENTE_KEY_FILE=str(tmp / "key"),
                      PUENTE_PORTFOLIO=str(tmp / "Portfolio"), PUENTE_BUSY=str(tmp / "busy.json"),
                      PUENTE_ENGINE="stub", PUENTE_STUB_SLEEP="0")
    (tmp / "key").write_text(KEY)
    os.chmod(tmp / "key", 0o600)     # puente refuses a group/world-readable key; do not depend on umask
    spec = importlib.util.spec_from_file_location("puente_srv", HERE / "agent" / "puente.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        repo = cls.tmp / "Portfolio" / "demo"
        repo.mkdir(parents=True)
        (repo / "AGENTS.md").write_text("- tests-cmd: `.venv/bin/python -m tests.x`\n", encoding="utf-8")
        cls.S = load_server(cls.tmp)
        cls.httpd = cls.S.serve(port=0, engine="stub")
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def call(self, method, path, obj=None, sign=True, headers=None, ts=None):
        body = json.dumps(obj).encode() if obj is not None else b""
        h = dict(headers or {})
        if sign:
            ts, nonce = ts or str(int(time.time())), os.urandom(8).hex()
            h.update({"X-Puente-Ts": ts, "X-Puente-Nonce": nonce,
                      "X-Puente-Sig": PC.sign(KEY.encode(), ts, nonce, method, path, body)})
        if obj is not None:
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body if method == "POST" else None,
                                     method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def turn(self, msg="hola"):
        allowed = PC.allowed_for(self.tmp / "Portfolio" / "demo",
                                 lambda d: {"tests": ".venv/bin/python -m tests.x"}, "Read,Edit", edit=True)
        code, body = self.call("POST", "/turns", {"repo": "demo", "message": msg, "allowed": allowed})
        self.assertEqual(code, 202, body)
        return json.loads(body)["turn"]

    def wait_end(self, tid):
        code, body = self.call("GET", f"/turns/{tid}/events?from=0&follow=1")
        evs = [json.loads(l) for l in body.splitlines()]
        self.assertEqual(evs[-1]["t"], "end", evs)
        return evs

    def test_playwright_twins_agree(self):
        self.assertEqual(self.S.PLAYWRIGHT_TOOLS, set(PC.PLAYWRIGHT))

    def test_sign_twins_agree(self):
        self.assertEqual(self.S.sign(b"x" * 40, "1", "ab" * 8, "GET", "/a?b=1", b"z"),
                         PC.sign(b"x" * 40, "1", "ab" * 8, "GET", "/a?b=1", b"z"))

    def test_unsigned_bad_old_and_browser_refused(self):
        self.assertEqual(self.call("GET", "/health", sign=False)[0], 401)
        self.assertEqual(self.call("GET", "/health", ts=str(int(time.time()) - 600))[0], 401)
        self.assertEqual(self.call("GET", "/health", headers={"Origin": "http://evil.example"})[0], 403)
        code, body = self.call("GET", "/health")
        self.assertEqual(code, 200, body)

    def test_replay_refused(self):
        ts, nonce = str(int(time.time())), "cd" * 8
        sig = PC.sign(KEY.encode(), ts, nonce, "GET", "/sessions", b"")
        h = {"X-Puente-Ts": ts, "X-Puente-Nonce": nonce, "X-Puente-Sig": sig}
        self.assertEqual(self.call("GET", "/sessions", sign=False, headers=h)[0], 200)
        self.assertEqual(self.call("GET", "/sessions", sign=False, headers=h)[0], 401)

    def test_allowlist_enforced_on_server(self):
        for bad in (["Bash(rm -rf /)"], ["Bash(.venv/bin/python:*)"], ["WebFetch"], ["mcp__other__x"],
                    ["mcp__playwright"], ["mcp__playwright__browser_run_code_unsafe"],
                    ["mcp__playwright__browser_evaluate"], ["mcp__playwright__browser_file_upload"]):
            code, body = self.call("POST", "/turns", {"repo": "demo", "message": "x", "allowed": bad})
            self.assertEqual(code, 403, (bad, body))
        code, _ = self.call("POST", "/turns", {"repo": "../etc", "message": "x", "allowed": []})
        self.assertEqual(code, 400)

    def test_turns_resume_same_session_and_cost_accumulates(self):
        t1 = self.turn("uno")
        self.wait_end(t1)
        s1 = json.loads(self.call("GET", "/sessions")[1])["sessions"][0]
        t2 = self.turn("dos")
        evs = self.wait_end(t2)
        self.assertTrue(any(e["t"] == "start" and e["resume"] for e in evs))
        s2 = json.loads(self.call("GET", "/sessions")[1])["sessions"][0]
        self.assertEqual(s1["session_id"], s2["session_id"])
        self.assertEqual(s2["turns"], s1["turns"] + 1)
        self.assertEqual(s2["last_turn"], t2)
        m = json.loads(self.call("GET", f"/turns/{t2}")[1])
        self.assertEqual(m["state"], "ok")
        self.assertIn("eco (demo): dos", m["result"])

    def test_events_from_offset(self):
        tid = self.turn("offset")
        all_ = self.wait_end(tid)
        code, body = self.call("GET", f"/turns/{tid}/events?from=2")
        rest = [json.loads(l) for l in body.splitlines()]
        self.assertEqual([e["n"] for e in rest], [e["n"] for e in all_[2:]])

    def test_recover_marks_running_interrupted(self):
        st = self.S.Store(self.tmp / "root2")
        st.save_meta({"turn": "p20261007-000000-abcdef", "repo": "demo", "state": "running", "message": "m"})
        r = self.S.Runner(st, "stub")
        self.assertEqual(r.recover(), 1)
        self.assertEqual(st.meta("p20261007-000000-abcdef")["state"], "interrupted")

    def test_out_dir_is_excluded_from_git_once(self):
        repo = self.tmp / "Portfolio" / "conGit"
        (repo / ".git" / "info").mkdir(parents=True)
        self.S.prepare_out_dir(repo)
        self.S.prepare_out_dir(repo)
        self.assertTrue((repo / ".puente").is_dir())
        self.assertEqual((repo / ".git" / "info" / "exclude").read_text().splitlines(), ["/.puente/"])

    def test_sweep_moves_only_new_captures(self):
        import subprocess
        repo = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "old.png").write_text("x")
        before = self.S.untracked(repo)
        (repo / "new.png").write_text("x")
        (repo / "notes.txt").write_text("x")
        (repo / "sub").mkdir()
        (repo / "sub" / "snap.yml").write_text("x")
        moved = self.S.sweep(repo, before, "p20261008-000000-aaaaaa")
        self.assertEqual(moved, [".puente/p20261008-000000-aaaaaa/new.png",
                                 ".puente/p20261008-000000-aaaaaa/sub/snap.yml"])
        self.assertTrue((repo / "old.png").exists())
        self.assertTrue((repo / "notes.txt").exists())
        self.assertTrue((repo / ".puente" / "p20261008-000000-aaaaaa" / "new.png").exists())
        self.assertEqual(self.S.sweep(repo, None, "x"), [])

    # os.kill(pid, 0) is a liveness probe on POSIX only; on Windows 0 is CTRL_C_EVENT and raises
    # without a console. puente.py runs on the Mac (test_presence MAC_ONLY), so test it there.
    @unittest.skipIf(sys.platform == "win32", "POSIX liveness probe; puente runs on the Mac")
    def test_busy_marker_left_alone_when_held(self):
        b = Path(os.environ["PUENTE_BUSY"])
        b.write_text(json.dumps({"pid": os.getpid(), "tipo": "bench"}))
        self.assertFalse(self.S.busy_take("t1", "demo"))
        self.S.busy_release("t1")
        self.assertTrue(b.exists())
        b.unlink()
        self.assertTrue(self.S.busy_take("t2", "demo"))
        self.S.busy_release("t2")
        self.assertFalse(b.exists())


class Client(unittest.TestCase):
    def test_collect_writes_one_ledger_line_and_moves_pending(self):
        state = Path(tempfile.mkdtemp())
        PC.remember(state, "p20261007-010101-aaaaaa", {"turn": "p20261007-010101-aaaaaa", "node": "mac",
                                                       "repo": "demo", "origin_session": "s1"})
        PC.remember(state, "p20261007-010102-bbbbbb", {"turn": "p20261007-010102-bbbbbb", "node": "mac",
                                                       "repo": "demo"})
        metas = {"/turns/p20261007-010101-aaaaaa": {"turn": "p20261007-010101-aaaaaa", "repo": "demo",
                                                    "state": "ok", "cost_usd": 0.12, "started": "a", "end": "b"},
                 "/turns/p20261007-010102-bbbbbb": {"turn": "p20261007-010102-bbbbbb", "repo": "demo",
                                                    "state": "running"}}
        lines = []
        done = PC.collect(state, {"mac": "mac"}, lines.append, req=lambda h, m, p: metas[p])
        self.assertEqual([d["turn"] for d in done], ["p20261007-010101-aaaaaa"])
        self.assertEqual(lines[0]["worker"], "puente")
        self.assertEqual(lines[0]["cost_usd"], 0.12)
        self.assertEqual(lines[0]["origin_session"], "s1")
        self.assertEqual([p["turn"] for p in PC.pending(state)], ["p20261007-010102-bbbbbb"])
        self.assertEqual(PC.collect(state, {"mac": "mac"}, lines.append, req=lambda h, m, p: metas[p]), [])

    def test_parse_live(self):
        out = "someone\n__PUENTE__\n" + json.dumps({"sessions": [{"repo": "farmacias", "cost_usd": 0.5}]}) + "\n200"
        v = PC.parse_live(out)
        self.assertTrue(v["gui"])
        self.assertEqual(v["sessions"][0]["repo"], "farmacias")
        self.assertFalse(PC.parse_live("root\n__PUENTE__\n")["gui"])
        self.assertEqual(PC.parse_live("u\n__PUENTE__\n\n000")["puente_error"], "http 000")

    def test_curl_line_quotes_and_signs(self):
        line = PC.curl_line(b"x" * 40, "POST", "/turns", b"{}", ts="5", nonce="ef" * 8)
        self.assertIn("--data-binary @-", line)
        self.assertIn("X-Puente-Sig: " + PC.sign(b"x" * 40, "5", "ef" * 8, "POST", "/turns", b"{}"), line)
        self.assertNotEqual(PC.curl_line(b"x" * 40, "GET", "/s", ts="5"), PC.curl_line(b"x" * 40, "GET", "/s", ts="5"))
        self.assertIn("http://127.0.0.1:8766/turns", line)

    def test_allowed_for_adds_tests_cmd_and_playwright_only(self):
        t = PC.allowed_for(Path("."), lambda d: {"tests": "pytest -q"}, "Read,Edit,Write", edit=True)
        self.assertEqual(t[:4], ["Read", "Edit", "Write", "Bash(pytest -q)"])
        self.assertEqual(PC.allowed_for(Path("."), lambda d: {"tests": None}, "Read,Edit,Write")[:1], ["Read"])
        self.assertNotIn("Edit", PC.allowed_for(Path("."), lambda d: {"tests": None}, "Read,Edit,Write"))
        self.assertIn("mcp__playwright__browser_take_screenshot", t)
        self.assertNotIn("mcp__playwright", t)
        self.assertFalse([x for x in t if "unsafe" in x or "evaluate" in x or "upload" in x or "drop" in x])
        t = PC.allowed_for(Path("."), lambda d: {"tests": None}, "Read")
        self.assertEqual(t[0], "Read")
        self.assertFalse([x for x in t if x.startswith("Bash")])


if __name__ == "__main__":
    unittest.main()
