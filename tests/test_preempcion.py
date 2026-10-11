"""Prompt 20 step 3: preemption decisions (scheduler.plan) and the planner's hands (planner.py)
over recorded telemetry. No ssh, no signal: the planner's `run` is a fake that records the command,
and the lab is a local HTTP server on an ephemeral port."""
import datetime as dt
import http.server
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "visor"))
import collect as C  # noqa: E402
import planner as PL  # noqa: E402
import scheduler as S  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "scheduler"
CFG = S.load()
LIMA = dt.timezone(dt.timedelta(hours=-5))
T0 = dt.datetime(2026, 10, 10, 21, 0, tzinfo=LIMA).timestamp()
MAC = json.loads((FIX / "tele-mac-render.json").read_text(encoding="utf-8"))
BUSY = dict(MAC, cpu=dict(MAC["cpu"], pct=97))
SHORT = dict(BUSY, mem=dict(MAC["mem"], pressure="warn"))
OSES = {"mac": "macos"}


def job(key, prio, state="running", **kw):
    src, _, jid = key.partition(":")
    return dict({"key": key, "id": jid, "node": "mac", "priority": prio, "state": state,
                 "preemptable": True, "resumable": False, "agent": False,
                 "source": {"job": "enjambre", "lab": "render_lab"}.get(src, src)}, **kw)


class Plan(unittest.TestCase):
    def test_alta_pauses_baja_when_cpu_is_short(self):
        acts, st = S.plan([job("job:burn", "baja"), job("job:hot", "alta")], {"mac": BUSY}, OSES, {}, T0, CFG)
        self.assertEqual([(a["action"], a["key"]) for a in acts], [("pause", "job:burn")])
        self.assertIn("pausado por hot (alta) desde 21:00 (CPU 97 %)", acts[0]["reason"])
        self.assertIn("job:burn", st["paused"])

    def test_nothing_happens_when_the_node_has_room(self):
        acts, _ = S.plan([job("job:burn", "baja"), job("job:hot", "alta")], {"mac": MAC}, OSES, {}, T0, CFG)
        self.assertEqual(acts, [])

    def test_normal_does_not_preempt_by_default(self):
        acts, _ = S.plan([job("job:burn", "baja"), job("job:n", "normal")], {"mac": BUSY}, OSES, {}, T0, CFG)
        self.assertEqual(acts, [])

    def test_short_memory_evicts_only_what_has_a_checkpoint(self):
        jobs = [job("job:ck", "baja", resumable=True), job("job:nock", "baja"),
                job("lab:r1", "baja"), job("job:hot", "alta")]
        acts, st = S.plan(jobs, {"mac": SHORT}, OSES, {}, T0, CFG)
        got = {a["key"]: a["action"] for a in acts}
        self.assertEqual(got, {"job:ck": "evict", "job:nock": "pause", "lab:r1": "pause"})
        self.assertIn("pausar no libera su memoria", [a for a in acts if a["key"] == "job:nock"][0]["reason"])
        self.assertIn("job:ck", st["evicted"])

    def test_agents_only_between_turns_and_never_when_configured(self):
        mid = job("job:ag", "baja", agent=True, preemptable=False)
        acts, _ = S.plan([mid, job("job:hot", "alta")], {"mac": BUSY}, OSES, {}, T0, CFG)
        self.assertEqual(acts, [])
        between = job("job:ag", "baja", agent=True)
        self.assertEqual(len(S.plan([between, job("job:hot", "alta")], {"mac": BUSY}, OSES, {}, T0, CFG)[0]), 1)
        never = dict(CFG, preempcion=dict(CFG["preempcion"], agentes="nunca"))
        self.assertEqual(S.plan([between, job("job:hot", "alta")], {"mac": BUSY}, OSES, {}, T0, never)[0], [])

    def test_hysteresis_60_s_then_resume(self):
        _, st = S.plan([job("job:burn", "baja"), job("job:hot", "alta")], {"mac": BUSY}, OSES, {}, T0, CFG)
        paused = [job("job:burn", "baja", state="paused_by_priority")]
        acts, st = S.plan(paused, {"mac": MAC}, OSES, st, T0 + 10, CFG)       # alta just ended
        self.assertEqual(acts, [])
        self.assertEqual(st["free_since"]["mac"], T0 + 10)
        acts, st = S.plan(paused, {"mac": MAC}, OSES, st, T0 + 60, CFG)       # 50 s free: not yet
        self.assertEqual(acts, [])
        acts, st = S.plan(paused, {"mac": MAC}, OSES, st, T0 + 71, CFG)       # 61 s free
        self.assertEqual([(a["action"], a["key"]) for a in acts], [("resume", "job:burn")])
        self.assertIn("61 s", acts[0]["reason"])
        self.assertEqual(st["paused"], {})

    def test_a_new_alta_resets_the_hysteresis(self):
        _, st = S.plan([job("job:burn", "baja"), job("job:hot", "alta")], {"mac": BUSY}, OSES, {}, T0, CFG)
        paused = job("job:burn", "baja", state="paused_by_priority")
        _, st = S.plan([paused], {"mac": MAC}, OSES, st, T0 + 10, CFG)
        _, st = S.plan([paused, job("job:hot2", "alta")], {"mac": BUSY}, OSES, st, T0 + 50, CFG)
        acts, st = S.plan([paused], {"mac": MAC}, OSES, st, T0 + 80, CFG)
        self.assertEqual(acts, [])                                             # free only 0 s
        self.assertEqual(st["free_since"]["mac"], T0 + 80)

    def test_evicted_resumes_from_checkpoint(self):
        _, st = S.plan([job("job:ck", "baja", resumable=True), job("job:hot", "alta")], {"mac": SHORT}, OSES, {}, T0, CFG)
        gone = [job("job:ck", "baja", state="evicted_resumable", resumable=True)]
        _, st = S.plan(gone, {"mac": MAC}, OSES, st, T0 + 20, CFG)
        acts, _ = S.plan(gone, {"mac": MAC}, OSES, st, T0 + 81, CFG)
        self.assertEqual([a["action"] for a in acts], ["resume_evicted"])

    def test_the_owners_hand_wins(self):
        _, st = S.plan([job("job:burn", "baja"), job("job:hot", "alta")], {"mac": BUSY}, OSES, {}, T0, CFG)
        # the owner resumed it by hand a minute later: the scheduler forgets it, never re-resumes
        _, st = S.plan([job("job:burn", "baja"), job("job:hot", "alta")], {"mac": MAC}, OSES, st, T0 + 60, CFG)
        self.assertEqual(st["paused"], {})

    def test_a_pause_not_yet_echoed_is_kept(self):
        _, st = S.plan([job("job:burn", "baja"), job("job:hot", "alta")], {"mac": BUSY}, OSES, {}, T0, CFG)
        acts, st = S.plan([job("job:burn", "baja"), job("job:hot", "alta")], {"mac": BUSY}, OSES, st, T0 + 10, CFG)
        self.assertIn("job:burn", st["paused"])


class Hands(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.saved = PL.STATE, PL.STATE_FILE, PL.AUDIT, PL.ADOPTED
        PL.STATE, PL.STATE_FILE, PL.AUDIT, PL.ADOPTED = d, d / "scheduler.json", d / "scheduler.jsonl", d / "adopted.json"
        self.calls = []

        def run(host, cmd, timeout=20):
            self.calls.append((host, cmd))
            if "curl" in cmd:
                return 0, "200", ""
            return 0, '{"ok": true}\n', ""
        self.pl = PL.Planner({"mac": {"host": "mac", "os": "macos"}, "win": {"host": "local", "os": "windows"}},
                             CFG, run=run, log=lambda m: None)

    def tearDown(self):
        PL.STATE, PL.STATE_FILE, PL.AUDIT, PL.ADOPTED = self.saved
        self.tmp.cleanup()

    def live(self, *cards):
        return {"trabajos": list(cards), "telemetria": {"mac": BUSY}}

    def test_tick_pauses_by_jobrun_and_writes_state_and_audit(self):
        live = self.live({"id": "burn-1", "tipo": "trabajo", "nodo": "mac", "estado": "running", "prioridad": "baja"},
                         {"id": "hot-1", "tipo": "trabajo", "nodo": "mac", "estado": "running", "prioridad": "alta"})
        payloads = {"mac": {"detached": [{"id": "burn-1", "kind": "script", "priority": "baja"},
                                         {"id": "hot-1", "kind": "script", "priority": "alta"}]}}
        out = self.pl.tick(live, payloads, T0)
        self.assertEqual(len(self.calls), 1)
        host, cmd = self.calls[0]
        self.assertEqual(host, "mac")
        self.assertTrue(cmd.startswith("/usr/bin/python3 .enjambre/jobrun.py control burn-1 pause "))
        self.assertIn("job:burn-1", json.loads(PL.STATE_FILE.read_text(encoding="utf-8"))["paused"])
        line = json.loads(PL.AUDIT.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual((line["action"], line["ok"], line["by"]), ("pause", True, "planificador"))
        self.assertIn("1 alta corriendo", out["politica"]["mac"])
        self.assertTrue(live["trabajos"][0]["motivo"].startswith("pausado por hot-1 (alta)"))

    def test_lab_render_is_paused_by_its_api_and_never_evicted(self):
        live = self.live({"id": "hot-1", "tipo": "trabajo", "nodo": "mac", "estado": "running", "prioridad": "alta"})
        live["telemetria"] = {"mac": SHORT}
        payloads = {"mac": {"detached": [{"id": "hot-1", "kind": "script"}],
                            "externos": {"render_lab": [{"id": "a1b2c3", "status": "running", "preset": "dmt"}]}}}
        out = self.pl.tick(live, payloads, T0)
        self.assertEqual(self.calls[0][1], "curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST "
                                           "http://127.0.0.1:8800/jobs/a1b2c3/pause")
        self.assertEqual(out["externos"][0]["fuente"], "render_lab")
        self.assertEqual(self.pl.execute(dict(action="evict", key="lab:a1b2c3", id="a1b2c3", node="mac",
                                              source="render_lab", reason="x"))[0], False)

    def test_bad_ids_never_reach_a_shell(self):
        for src, jid in (("render_lab", "a;rm -rf ~"), ("enjambre", "x$(id)"), ("adopted", "12 13")):
            ok, msg = self.pl.execute(dict(action="pause", key="k", id=jid, node="mac", source=src, reason="r"))
            self.assertFalse(ok)
        self.assertEqual(self.calls, [])

    def test_windows_node_is_not_driven_over_ssh(self):
        ok, msg = self.pl.execute(dict(action="pause", key="job:x", id="x", node="win", source="enjambre", reason="r"))
        self.assertFalse(ok)
        self.assertEqual(self.calls, [])

    def test_failed_pause_is_not_claimed(self):
        self.pl.run = lambda host, cmd, timeout=20: (1, '{"ok": false, "error": "no pgid"}', "")
        live = self.live({"id": "burn-1", "tipo": "trabajo", "nodo": "mac", "estado": "running", "prioridad": "baja"},
                         {"id": "hot-1", "tipo": "trabajo", "nodo": "mac", "estado": "running", "prioridad": "alta"})
        self.pl.tick(live, {"mac": {"detached": [{"id": "burn-1", "kind": "script"}]}}, T0)
        self.assertEqual(self.pl.state["paused"], {})

    def test_release_all_resumes_everything_it_paused(self):
        PL.P.write_json_atomic(PL.STATE_FILE, {"paused": {
            "job:burn-1": {"node": "mac", "source": "enjambre"},
            "lab:a1b2c3": {"node": "mac", "source": "render_lab"},
            "pid:mac:4242": {"node": "mac", "source": "adopted"}}})
        pl = PL.Planner({"mac": {"host": "mac"}}, CFG, run=self.pl.run, log=lambda m: None)
        done = pl.release_all()
        self.assertEqual(len(done), 3)
        cmds = " | ".join(c for _, c in self.calls)
        self.assertIn("control burn-1 resume", cmds)
        self.assertIn("/jobs/a1b2c3/resume", cmds)
        self.assertIn("sigtree 4242 CONT", cmds)
        self.assertEqual(json.loads(PL.STATE_FILE.read_text(encoding="utf-8"))["paused"], {})

    def test_extra_args_name_the_lab_on_its_node_only(self):
        PL.P.write_json_atomic(PL.ADOPTED, {"adopted": [{"node": "mac", "pid": 4242, "priority": "baja"}]})
        ex = self.pl.extra_args()
        self.assertEqual(ex["mac"], "--lab http://127.0.0.1:8800 --lab-port 8800 "
                                    "--lab-logs ~/dev/lab-video/logs/lab_jobs --adopted 4242:baja")
        self.assertEqual(ex["win"], "")

    def test_unmanaged_needs_120_s_sustained(self):
        tele = {"mac": {"top": [{"pid": 9, "name": "ffmpeg", "cpu": 300, "rss_gb": 0.4, "tag": None},
                                {"pid": 10, "name": "x", "cpu": 99, "rss_gb": 1, "tag": "trabajo: y"}]}}
        self.assertEqual(self.pl.unmanaged(tele, T0), [])
        got = self.pl.unmanaged(tele, T0 + 121)
        self.assertEqual([(u["pid"], u["name"]) for u in got], [(9, "ffmpeg")])


class Manual(Hands):
    JOBS = [{"key": "job:burn-1", "id": "burn-1", "node": "mac", "source": "enjambre", "priority": "baja",
             "state": "running", "resumable": False, "preemptable": True, "agent": False},
            {"key": "job:ck-1", "id": "ck-1", "node": "mac", "source": "enjambre", "priority": "baja",
             "state": "running", "resumable": True, "preemptable": True, "agent": False}]

    def test_manual_pause_is_not_resumed_by_the_scheduler(self):
        res = self.pl.manual("pause", "job:burn-1", jobs=self.JOBS)
        self.assertTrue(res["ok"], res)
        self.assertIn("job:burn-1", self.pl.state["manual"])
        paused = [dict(self.JOBS[0], state="paused_by_priority")]
        live = {"trabajos": [{"id": "burn-1", "tipo": "trabajo", "nodo": "mac", "estado": "paused_by_priority",
                              "prioridad": "baja"}], "telemetria": {"mac": MAC}}
        for t in (T0, T0 + 61, T0 + 200):
            self.pl.tick(live, {"mac": {"detached": [{"id": "burn-1", "kind": "script"}]}}, t)
        self.assertEqual([c for _, c in self.calls if "resume" in c], [])
        self.assertIn("job:burn-1", self.pl.state["manual"])
        self.assertTrue(self.pl.manual("resume", "job:burn-1", jobs=paused)["ok"])
        self.assertNotIn("job:burn-1", self.pl.state.get("manual", {}))

    def test_evict_without_checkpoint_is_refused(self):
        res = self.pl.manual("evict", "job:burn-1", jobs=self.JOBS)
        self.assertFalse(res["ok"])
        self.assertIn("sin checkpoint", res["error"])
        self.assertTrue(self.pl.manual("evict", "job:ck-1", jobs=self.JOBS)["ok"])
        self.assertIn("control ck-1 evict", self.calls[-1][1])

    def test_priority_override_and_adopt(self):
        self.assertTrue(self.pl.manual("priority", "job:burn-1", "alta", jobs=self.JOBS)["ok"])
        live = {"trabajos": [{"id": "burn-1", "tipo": "trabajo", "nodo": "mac", "estado": "running",
                              "prioridad": "baja"}], "telemetria": {"mac": MAC}}
        out = self.pl.tick(live, {"mac": {"detached": [{"id": "burn-1", "kind": "script"}]}}, T0)
        self.assertEqual(out["gestionados"][0]["priority"], "alta")
        self.assertTrue(self.pl.manual("adopt", "pid:mac:4242", "baja", name="ffmpeg")["ok"])
        self.assertIn("--adopted 4242:baja", self.pl.extra_args()["mac"])
        self.assertFalse(self.pl.manual("adopt", "pid:nowhere:1")["ok"])
        self.assertFalse(self.pl.manual("rm", "job:burn-1", jobs=self.JOBS)["ok"])
        self.assertFalse(self.pl.manual("priority", "job:burn-1", "urgente", jobs=self.JOBS)["ok"])

    def test_endpoint_needs_token_and_loopback_host(self):
        import urllib.error
        import urllib.request
        self.pl.last_jobs = self.JOBS
        srv = PL.Controls(self.pl, 0, token="t0k")
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        url = f"http://127.0.0.1:{srv.port}/ctl"

        def post(body, host=None):
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Content-Type": "text/plain", **({"Host": host} if host else {})})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.loads(r.read()), r.headers.get("Access-Control-Allow-Origin")
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read()), None
        try:
            self.assertEqual(post({"token": "bad", "action": "pause", "key": "job:burn-1"})[0], 403)
            self.assertEqual(post({"token": "t0k", "action": "pause", "key": "job:burn-1"}, host="evil.example")[0], 403)
            self.assertEqual(post({"token": "t0k", "action": "pause", "key": "job:x;id"})[0], 409)
            code, body, cors = post({"token": "t0k", "action": "pause", "key": "job:burn-1"})
            self.assertEqual((code, body["ok"], cors), (200, True, "null"))
            line = json.loads(PL.AUDIT.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual((line["by"], line["action"]), ("visor", "pause"))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_the_token_lives_only_in_ctl_js(self):
        p = Path(self.tmp.name) / "ctl.js"
        PL.write_ctl(5555, "s3cret", p)
        self.assertEqual(PL.read_ctl(p), {"port": 5555, "token": "s3cret"})
        out = self.pl.tick({"trabajos": [], "telemetria": {"mac": MAC}}, {}, T0)
        self.assertNotIn("s3cret", json.dumps(out))
        import web as W   # the public copy is built from a whitelist: no planner, no token, no controls
        pub = json.dumps(W.public_snapshot({"live": {"planificador": out}, "ctl": {"token": "s3cret"}}, set(), b"k"))
        for word in ("s3cret", "planificador", "LIVE_CTL", "/ctl"):
            self.assertNotIn(word, pub)


class FakeLab(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.loads((FIX / "lab-jobs.json").read_text(encoding="utf-8"))
        body.append({"id": "live01", "status": "running", "preset": "dmt", "elapsed_s": 12})
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


class LabApi(unittest.TestCase):
    def test_lab_jobs_keeps_only_active(self):
        srv = http.server.HTTPServer(("127.0.0.1", 0), FakeLab)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            got = C.lab_jobs(f"http://127.0.0.1:{srv.server_port}")
        finally:
            srv.shutdown()
            srv.server_close()
        self.assertEqual([j["id"] for j in got], ["live01"])     # the 2 recorded ones are done


if __name__ == "__main__":
    unittest.main()
