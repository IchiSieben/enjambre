"""live.py (prompt 18 step 1): session and job states with fixed clocks, the order of the "now"
list, a dead supervisor never shows green, a job's own Claude process folds into its card."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import live as LV  # noqa: E402

NOW = 1_800_000_000.0
NODES = {"win": {"host": "local", "label": "PC", "os": "windows"},
         "mac": {"host": "mac", "label": "Mac mini", "os": "macos"}}


def iso(t):
    return LV.iso(t)


class SessionState(unittest.TestCase):
    def test_busy_or_recent_is_running(self):
        self.assertEqual(LV.session_state({"status": "busy", "updated": NOW - 3000}, NOW)[0], "running")
        self.assertEqual(LV.session_state({"status": "idle", "bar_ts": NOW - 20}, NOW)[0], "running")

    def test_quiet_is_waiting_then_idle(self):
        self.assertEqual(LV.session_state({"status": "idle", "updated": NOW - 300}, NOW)[0], "waiting")
        self.assertEqual(LV.session_state({"status": "idle", "updated": NOW - 3 * 3600}, NOW)[0], "idle")

    def test_a_running_job_it_asked_for_keeps_it_running(self):
        self.assertEqual(LV.session_state({"status": "idle", "updated": NOW - 3 * 3600}, NOW, True)[0], "running")


class JobState(unittest.TestCase):
    def test_launching_before_the_first_beat(self):
        self.assertEqual(LV.job_state({"launched": iso(NOW - 5)}, None, NOW), "launching")
        self.assertEqual(LV.job_state({"launched": iso(NOW - 3600)}, None, NOW), "unknown")

    def test_running_and_stalled(self):
        st = {"state": "running", "latido": {"ts": iso(NOW - 15)}}
        self.assertEqual(LV.job_state({}, st, NOW), "running")
        st["latido"]["ts"] = iso(NOW - 6 * 60)
        self.assertEqual(LV.job_state({}, st, NOW), "stalled")

    def test_dead_is_never_running(self):
        # collect.py turns a running status.json whose supervisor pid is gone into "dead"
        self.assertEqual(LV.job_state({}, {"state": "dead"}, NOW), "dead")


def payloads(**kw):
    base = {"win": {"sessions": [], "detached": [], "telemetria": {"cpu": {"pct": 10}}},
            "mac": {"sessions": [], "detached": [], "telemetria": {"cpu": {"pct": 50}}}}
    for k, v in kw.items():
        base[k].update(v)
    return base


class Assemble(unittest.TestCase):
    def test_dispatched_job_is_on_top_before_the_node_answers(self):
        metas = [{"id": "c1", "node": "mac", "repo": "farmacias", "launched": iso(NOW - 2),
                  "max_turns": 30, "budget_usd": 0.5, "origin_session": "s-1"}]
        p = payloads(win={"sessions": [{"session_id": "s-1", "name": "farmacias-a2", "status": "idle",
                                        "updated": NOW - 5000, "project": "farmacias"}]})
        live = LV.assemble(NODES, p, metas, {}, {}, NOW)
        self.assertEqual(live["trabajos"][0]["estado"], "launching")
        self.assertEqual(live["ahora"]["running"], 2)          # the job + the session that asked for it
        self.assertEqual(live["ahora"]["por_nodo"], {"mac": 1, "win": 1})
        self.assertEqual(live["sesiones"][0]["despachos"], ["c1"])

    def test_turn_and_beat_come_from_the_node(self):
        metas = [{"id": "c1", "node": "mac", "launched": iso(NOW - 60), "max_turns": 30}]
        st = {"id": "c1", "state": "running", "repo": "farmacias", "start": iso(NOW - 60),
              "latido": {"ts": iso(NOW - 4), "turns": 7, "last_tool": "Read", "cost_est_usd": 0.12}}
        live = LV.assemble(NODES, payloads(mac={"detached": [st]}), metas, {}, {}, NOW)
        j = live["trabajos"][0]
        self.assertEqual((j["estado"], j["turno"], j["max_turnos"], j["ultima_linea"]), ("running", 7, 30, "Read"))

    def test_dead_and_old_jobs_stay_off_the_now_list(self):
        dead = {"id": "zombie", "state": "dead"}
        old = {"id": "old", "state": "ok", "end": iso(NOW - 3600)}
        live = LV.assemble(NODES, payloads(mac={"detached": [dead, old]}), [], {}, {}, NOW)
        self.assertEqual(live["trabajos"], [])
        self.assertEqual(live["ahora"]["running"], 0)

    def test_order_running_queued_waiting_idle(self):
        p = payloads(win={"sessions": [
            {"session_id": "a", "name": "idle-one", "status": "idle", "updated": NOW - 7200},
            {"session_id": "b", "name": "waiting-one", "status": "idle", "updated": NOW - 300},
            {"session_id": "c", "name": "busy-one", "status": "busy", "updated": NOW - 10}]})
        live = LV.assemble(NODES, p, [], {"queue": [{"id": "q1", "node": "mac"}]}, {}, NOW)
        self.assertEqual([s["estado"] for s in live["sesiones"]], ["running", "waiting", "idle"])
        self.assertEqual(live["ahora"]["queued"], 1)

    def test_job_own_claude_process_is_not_one_more_session(self):
        metas = [{"id": "live-probe2", "node": "mac", "launched": iso(NOW - 10)}]
        st = {"id": "live-probe2", "state": "running", "latido": {"ts": iso(NOW - 2), "turns": 3}}
        sess = {"session_id": "x", "name": "enjambre-sandbox-live-probe2-dc", "status": "busy",
                "updated": NOW, "project": "enjambre-sandbox-live-probe2", "context_k": 12}
        live = LV.assemble(NODES, payloads(mac={"detached": [st], "sessions": [sess]}), metas, {}, {}, NOW)
        self.assertEqual(live["sesiones"], [])
        self.assertEqual(live["trabajos"][0]["ctx_k"], 12)

    def test_a_node_that_did_not_answer_is_not_ok(self):
        p = payloads()
        p["mac"] = None
        live = LV.assemble(NODES, p, [], {}, {}, NOW, {"mac": {"error": "timeout", "tele_last": {"cpu": {"pct": 1}}}})
        self.assertFalse(live["nodos"]["mac"]["ok"])
        self.assertTrue(live["nodos"]["win"]["ok"])
        self.assertEqual(live["telemetria"]["mac"], {"cpu": {"pct": 1}})   # last reading, marked by the node row


class NodeWithoutSignal(unittest.TestCase):
    def test_jobs_of_a_silent_node_stay_marked_not_dropped(self):
        metas = [{"id": "c1", "node": "mac", "launched": iso(NOW - 3600)}]
        p = payloads()
        p["mac"] = None                                  # ssh failed and nothing to reuse
        live = LV.assemble(NODES, p, metas, {}, {}, NOW, {"mac": {"error": "timeout"}})
        self.assertEqual([(j["id"], j["estado"]) for j in live["trabajos"]], [("c1", "nosignal")])
        self.assertEqual(live["ahora"]["running"], 1)

    def test_a_reused_read_ages_into_nosignal(self):
        metas = [{"id": "c1", "node": "mac", "launched": iso(NOW - 600)}]
        st = {"id": "c1", "state": "running", "latido": {"ts": iso(NOW - 200), "turns": 4}}
        old = {"mac": {"error": "timeout", "read_at": NOW - 200}}
        live = LV.assemble(NODES, payloads(mac={"detached": [st]}), metas, {}, {}, NOW, old)
        self.assertEqual(live["trabajos"][0]["estado"], "nosignal")
        self.assertFalse(live["nodos"]["mac"]["ok"])
        self.assertEqual(live["nodos"]["mac"]["stale_s"], 200)
        fresh = {"mac": {"error": "timeout", "read_at": NOW - 30}}
        live = LV.assemble(NODES, payloads(mac={"detached": [st]}), metas, {}, {}, NOW, fresh)
        self.assertEqual(live["trabajos"][0]["estado"], "running")   # 30 s old: still trusted

    def test_loop_reuses_the_last_good_payload(self):
        calls = iter([({"node": "mac", "sessions": [], "detached": [], "telemetria": None}, 0.3, None),
                      (None, 6.0, "timeout")])
        loop = LV.Loop({"mac": NODES["mac"]}, ssh_fn=lambda host, n: next(calls))
        import unittest.mock as um
        with um.patch.object(LV, "read_metas", return_value=[]), um.patch.object(LV, "read_queue", return_value={}),                 um.patch.object(LV.P, "write_json_atomic"), um.patch.object(LV, "write_js"):
            loop.cycle(NOW)
            live = loop.cycle(NOW + 10)
        self.assertFalse(live["nodos"]["mac"]["ok"])
        self.assertEqual(live["nodos"]["mac"]["stale_s"], 10)


class Kick(unittest.TestCase):
    def test_kick_wakes_the_loop(self):
        import tempfile
        import time
        with tempfile.TemporaryDirectory() as d:
            old_out, old_kick = LV.OUT, LV.KICK
            LV.OUT, LV.KICK = Path(d), Path(d) / "live.kick"
            try:
                t = time.time()
                LV.kick()
                LV.wait_or_kick(5, t - 1)
                self.assertLess(time.time() - t, 2)
            finally:
                LV.OUT, LV.KICK = old_out, old_kick


if __name__ == "__main__":
    unittest.main()
