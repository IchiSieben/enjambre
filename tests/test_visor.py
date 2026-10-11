"""Tests for tools/visor (collect.py runs on 3.9; this file must too: no tomllib at import)."""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools" / "visor"))
import collect as C  # noqa: E402

try:
    import build as B  # needs tomllib (3.11+); the mac's 3.9 runs only the collector tests
    import web as W
except ModuleNotFoundError:
    B = W = None


def line(mid, model="claude-opus-5-5", ts="2026-10-05T15:00:00Z", cwd="/x/enjambre", **u):
    usage = {"input_tokens": 10, "output_tokens": 100, "cache_creation_input_tokens": 1000,
             "cache_read_input_tokens": 5000}
    usage.update(u)
    return json.dumps({"type": "assistant", "timestamp": ts, "cwd": cwd,
                       "message": {"id": mid, "model": model, "usage": usage}}) + "\n"


class TranscriptParse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "projects" / "p" / "sid" / "subagents").mkdir(parents=True)
        self.main = self.root / "projects" / "p" / "sid.jsonl"
        self.sub = self.root / "projects" / "p" / "sid" / "subagents" / "agent-1.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def usage(self):
        return C.collect_usage(str(self.root), str(self.root / "cur.json"),
                               dt.datetime(2026, 10, 5, 20, tzinfo=dt.timezone.utc).timestamp(), {})

    def test_same_message_id_counted_once(self):
        # three content blocks of one response = three lines, one usage
        self.main.write_text(line("m1") * 3 + line("m2"))
        row = self.usage()["days"]["2026-10-05"]["enjambre"]["claude-opus-5-5"]
        self.assertEqual(row, [20, 200, 2000, 10000, 2, 0])

    def test_subagents_included(self):
        self.main.write_text(line("m1"))
        self.sub.write_text(line("s1"))
        self.assertEqual(self.usage()["days"]["2026-10-05"]["enjambre"]["claude-opus-5-5"][4], 2)

    def test_incremental_and_partial_line(self):
        self.main.write_text(line("m1") + line("m2")[:30])      # second line still being written
        self.assertEqual(self.usage()["days"]["2026-10-05"]["enjambre"]["claude-opus-5-5"][4], 1)
        with open(self.main, "w") as f:
            f.write(line("m1") + line("m2") + line("m2"))
        self.assertEqual(self.usage()["days"]["2026-10-05"]["enjambre"]["claude-opus-5-5"][4], 2)

    def test_resumed_session_copy_counted_once(self):
        # a resumed session copies earlier responses into a new transcript (seen on the mac)
        self.main.write_text(line("m1") + line("m2"))
        (self.root / "projects" / "p" / "sid2.jsonl").write_text(line("m1") + line("m3"))
        self.assertEqual(self.usage()["days"]["2026-10-05"]["enjambre"]["claude-opus-5-5"][4], 3)

    def test_old_cursor_layout_is_reread(self):
        self.main.write_text(line("m1"))
        (self.root / "cur.json").write_text(json.dumps({str(self.main): {"size": 1, "offset": 999999,
                                                                         "ids": [], "agg": {}}}))
        self.assertEqual(self.usage()["days"]["2026-10-05"]["enjambre"]["claude-opus-5-5"][4], 1)

    def test_synthetic_and_zero_usage_skipped(self):
        self.main.write_text(line("a", model="<synthetic>") +
                             line("b", input_tokens=0, output_tokens=0,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0))
        self.assertEqual(self.usage()["days"], {})

    def test_lima_day_boundary(self):
        # 03:00 UTC on the 5th is 22:00 on the 4th in Lima
        self.main.write_text(line("m1", ts="2026-10-05T03:00:00Z"))
        self.assertIn("2026-10-04", self.usage()["days"])

    def test_worktree_keeps_repo_name(self):
        self.assertEqual(C.project_of("/Users/u/wt/enjambre-sandbox"), "enjambre-sandbox")
        self.assertEqual(C.project_of("C:\\Users\\u\\Documents\\Portfolio\\enjambre"), "enjambre")


class Prices(unittest.TestCase):
    P = {"claude-opus-5-5": (4.0, 20.0, 0.2), "claude-haiku-4-5-20251001": (1.0, 5.0, 0.1)}

    def test_cost_prices_cache_writes_by_ttl(self):
        # 3 M written: 1 M with the 1 h TTL (2x input = $8), 2 M at 5 min (1.25x = $5 each)
        c = C.cost_of("claude-opus-5-5", [1_000_000, 1_000_000, 3_000_000, 1_000_000, 1, 1_000_000], self.P)
        self.assertAlmostEqual(c, 4 + 20 + 0.2 + 2 * 5 + 8)

    def test_old_row_without_ttl_split_is_the_5m_floor(self):
        c = C.cost_of("claude-opus-5-5", [0, 0, 1_000_000, 0, 1], self.P)
        self.assertAlmostEqual(c, 5.0)

    def test_models_json_write_prices_win_over_multiplier(self):
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "m.json")
            with open(p, "w") as f:
                json.dump({"models": [{"id": "a", "price_in": 4, "price_out": 20, "price_cache_hit": 0.2,
                                       "price_cache_write_5m": 4.4, "price_cache_write_1h": 7.7},
                                      {"id": "b", "price_in": 1, "price_out": 5}]}, f)
            pr = C.load_prices(p)
            self.assertEqual(pr["a"][3:], (4.4, 7.7))
            self.assertEqual(pr["b"][3:], (1.25, 2.0))
            self.assertTrue(C.prices_estimated(p))

    def test_unknown_model_is_unpriced_not_guessed(self):
        self.assertIsNone(C.cost_of("claude-sonnet-5-5", [1, 1, 1, 1, 1], self.P))

    def test_date_suffix_match(self):
        self.assertEqual(C.price_for("claude-haiku-4-5", self.P), (1.0, 5.0, 0.1))


def uline(text, ts="2026-10-05T15:00:00Z", cwd="/x/enjambre", meta=False):
    return json.dumps({"type": "user", "timestamp": ts, "cwd": cwd, "isMeta": meta,
                       "message": {"role": "user", "content": text}}) + "\n"


class History(unittest.TestCase):
    """All-time per-project totals: sessions, windows (resume merge), subagents, hours, prompts."""
    P = {"claude-opus-5-5": (4.0, 20.0, 0.2, 5.0, 8.0)}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pdir = self.root / "projects" / "p"
        (self.pdir / "s1" / "subagents").mkdir(parents=True)
        self.cur = str(self.root / "hist.json")

    def tearDown(self):
        self.tmp.cleanup()

    def hist(self, **kw):
        return C.collect_history(str(self.root), self.cur, self.P, **kw)

    def test_sessions_windows_subagents_hours_prompts(self):
        # s1: two prompts 10 min apart, then a 2 h gap (idle, not counted), then 5 min more
        (self.pdir / "s1.jsonl").write_text(
            uline("hola", ts="2026-10-01T10:00:00Z") + line("m1", ts="2026-10-01T10:10:00Z")
            + uline("x", ts="2026-10-01T10:10:00Z", meta=True)
            + uline([{"type": "tool_result", "content": "ok"}], ts="2026-10-01T10:10:00Z")
            + uline("otra", ts="2026-10-01T12:10:00Z") + line("m2", ts="2026-10-01T12:15:00Z"))
        (self.pdir / "s1" / "subagents" / "agent-a.jsonl").write_text(line("sa", ts="2026-10-01T10:05:00Z"))
        # s2 resumes s1 (copies m1) and adds m3: same window, m1 counted once
        (self.pdir / "s2.jsonl").write_text(line("m1", ts="2026-10-01T10:10:00Z")
                                            + uline("sigue", ts="2026-10-02T09:00:00Z")
                                            + line("m3", ts="2026-10-02T09:01:00Z"))
        # s3: unrelated session of the same project
        (self.pdir / "s3.jsonl").write_text(uline("nuevo", ts="2026-10-03T09:00:00Z") + line("m4", ts="2026-10-03T09:00:30Z"))
        p = self.hist()["projects"]["enjambre"]
        self.assertEqual(p["sessions"], 3)
        self.assertEqual(p["windows"], 2)
        self.assertEqual(p["subagents"], 1)
        self.assertEqual(p["prompts"], 4)               # meta and tool_result lines are not prompts
        self.assertEqual(p["responses"], 5)             # m1 m2 m3 m4 sa, m1 once
        self.assertEqual(p["tokens"]["out"], 500)
        # s1: 10 + 5 min; s2: 22h50 gap idle, then 1 min; s3: 30 s
        self.assertEqual(p["active_s"], 15 * 60 + 60 + 30)
        self.assertEqual(len(p["timeline"]), 3)
        self.assertGreater(p["cost"], 0)

    def test_subagent_type_and_description_survive_cleanup(self):
        (self.pdir / "s1.jsonl").write_text(uline("hola", ts="2026-10-01T10:00:00Z") + line("m1", ts="2026-10-01T10:01:00Z"))
        sub = self.pdir / "s1" / "subagents" / "agent-a.jsonl"
        sub.write_text(line("sa", ts="2026-10-01T10:05:00Z"))
        sub.with_name("agent-a.meta.json").write_text(json.dumps(
            {"agentType": "redactor", "description": "README del repo", "spawnDepth": 1}), encoding="utf-8")
        s1 = self.hist()["projects"]["enjambre"]["subs"]
        self.assertEqual([(x["type"], x["desc"], x["sid"]) for x in s1], [("redactor", "README del repo", "s1")])
        self.assertGreater(s1[0]["cost"], 0)
        sub.unlink()                                      # Claude Code cleaned the transcript up
        sub.with_name("agent-a.meta.json").unlink()
        s2 = self.hist()["projects"]["enjambre"]["subs"]
        self.assertEqual(s2[0]["type"], "redactor")       # the db kept it

    def test_resumable_under_a_time_budget(self):
        for i in range(3):
            (self.pdir / ("t%d.jsonl" % i)).write_text(line("x%d" % i))
        ticks = iter([0, 0, 100, 100, 100])   # budget gone after the first file
        h = self.hist(budget_s=10, clock=lambda: next(ticks))
        self.assertFalse(h["complete"])
        self.assertEqual(h["pending_files"], 2)
        h = self.hist()                                 # next run finishes the rest
        self.assertTrue(h["complete"])
        self.assertEqual(h["projects"]["enjambre"]["sessions"], 3)

    def test_incremental_append_and_deleted_file_kept(self):
        f = self.pdir / "s9.jsonl"
        f.write_text(line("a1"))
        self.hist()
        with open(f, "a") as fh:
            fh.write(line("a2"))
        self.assertEqual(self.hist()["projects"]["enjambre"]["responses"], 2)
        f.unlink()                                      # Claude Code cleanup must not erase history
        self.assertEqual(self.hist()["projects"]["enjambre"]["responses"], 2)

    def test_store_is_sqlite_with_one_row_per_session(self):
        (self.pdir / "s1.jsonl").write_text(uline("hola", ts="2026-09-01T10:00:00Z") + line("m1", ts="2026-09-01T10:01:00Z"))
        h = self.hist()
        self.assertEqual((h["db"]["sessions"], h["since"] < h["until"]), (1, True))
        import sqlite3
        con = sqlite3.connect(str(self.root / "hist.db"))
        self.assertEqual(con.execute("SELECT sid, project, prompts, responses FROM sessions").fetchall(),
                         [("s1", "enjambre", 1, 1)])
        con.close()
        shutil.rmtree(self.root / "projects")           # every transcript gone: the db still answers
        self.assertEqual(self.hist()["projects"]["enjambre"]["sessions"], 1)

    def test_backup_root_is_read_once_and_not_double_counted(self):
        body = uline("hola", ts="2026-09-01T10:00:00Z") + line("m1", ts="2026-09-01T10:05:00Z")
        bak = self.root / "bak"
        (bak / "projects" / "p" / "s1" / "subagents").mkdir(parents=True)
        (bak / "projects" / "p" / "s0.jsonl").write_text(uline("viejo", ts="2026-08-01T10:00:00Z") + line("m0", ts="2026-08-01T10:01:00Z"))
        (bak / "projects" / "p" / "s1.jsonl").write_text(body)              # copy of an older state
        (bak / "projects" / "p" / "s1" / "subagents" / "agent-a.jsonl").write_text(line("sa"))
        (self.pdir / "s1.jsonl").write_text(body + uline("más", ts="2026-09-01T10:10:00Z") + line("m2", ts="2026-09-01T10:11:00Z"))
        (self.pdir / "s1" / "subagents" / "agent-a.jsonl").write_text(line("sa"))
        h = self.hist(extra_roots=[str(bak)])
        p = h["projects"]["enjambre"]
        self.assertEqual((p["sessions"], p["prompts"], p["responses"], p["subagents"]), (2, 3, 4, 1))
        self.assertEqual(C.local_day(h["since"]), "2026-08-01")             # the backup extends the range
        self.assertEqual(sorted(h["db"]["roots"]), ["bak", Path(self.root).name])

    def test_legacy_json_cursor_is_imported(self):
        (self.pdir / "s1.jsonl").write_text(line("m1"))
        old = {str(self.pdir / "s1.jsonl"): dict(C.scan_history(str(self.pdir / "s1.jsonl"), {}, False), sid="s1", sub=False)}
        Path(self.cur).write_text(json.dumps(old))
        (self.pdir / "s1.jsonl").unlink()               # only the json knows it now
        self.assertEqual(self.hist()["projects"]["enjambre"]["responses"], 1)


class Sessions(unittest.TestCase):
    def test_dead_pid_dropped_and_future_quota_ignored(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t)
            (r / "sessions").mkdir(); (r / "state").mkdir()
            now = time.time()
            for pid in (111, 222):
                (r / "sessions" / f"{pid}.json").write_text(json.dumps(
                    {"pid": pid, "sessionId": f"s{pid}", "cwd": "/a/enjambre", "startedAt": 1000}))
            (r / "sessions" / "111.abc.key").write_text("SECRET")          # must never be read
            (r / "state" / "a.json").write_text(json.dumps({"session": "s111", "ts": now - 60,
                                                             "five_pct": 12, "week_pct": 30, "context_k": 90}))
            (r / "state" / "b.json").write_text(json.dumps({"session": "x", "ts": 1800000000 + now,
                                                             "five_pct": 99, "week_pct": 99}))
            sessions, quota = C.collect_sessions(str(r), now, alive=lambda pid: pid == 111)
            self.assertEqual([s["pid"] for s in sessions], [111])
            self.assertEqual(sessions[0]["context_k"], 90)
            self.assertEqual(quota["five_pct"], 12)

    def test_stale_quota_is_none(self):
        with tempfile.TemporaryDirectory() as t:
            r = Path(t); (r / "state").mkdir()
            (r / "state" / "a.json").write_text(json.dumps({"session": "s", "ts": 1, "five_pct": 5}))
            self.assertIsNone(C.collect_sessions(str(r), time.time(), alive=lambda p: True)[1])

    def test_own_pid_alive(self):
        self.assertTrue(C.pid_alive(os.getpid()))
        self.assertFalse(C.pid_alive(0))

    def test_recycled_pid_is_not_a_session(self):
        st = C.pid_started(os.getpid())
        self.assertIsNotNone(st)
        self.assertLess(abs(st - time.time()), 3600)
        self.assertTrue(C.session_alive(os.getpid(), st))
        self.assertFalse(C.session_alive(os.getpid(), st - 86400))   # file says it started yesterday

    def test_thresholds_single_source(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "thresholds.json"
            p.write_text(json.dumps({"thresholds": [{"id": "quota-5h", "condition": {"value": 90}}]}))
            self.assertEqual(C.load_thresholds(str(p)), {"quota-5h": 90})
            p.write_text('{"thresholds": [ {"id": "roto",')
            self.assertIsNone(C.load_thresholds(str(p)))


def snap(node, now, day="2026-10-05", tokens=1000, sessions=()):
    return {"schema": 1, "node": node, "collected": now - 30, "os": "x",
            "sessions": list(sessions), "quota": None, "prices": {"m": [1.0, 1.0, 0.0]},
            "usage": {"days": {day: {"secret-work": {"m": [tokens, 0, 0, 0, 1]}}}},
            "disks": [], "memory": {}, "mounts": [], "tmux": [], "models": None, "errors": []}


@unittest.skipIf(B is None, "build.py needs Python 3.11+ (tomllib)")
class Build(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 5, 20, tzinfo=dt.timezone.utc).timestamp()

    def nodes(self, n=3):
        d = {"win": {"host": "local"}}
        for i in range(1, n):
            d[f"n{i}"] = {"host": f"h{i}"}
        return d

    def test_n_nodes_from_config_only(self):
        nodes = self.nodes(3)
        fetched = []

        def fetch(host, path):
            fetched.append(host)
            return (snap(host, self.NOW), "") if host != "h2" else (None, "ssh timeout")
        g = B.gather(nodes, self.NOW, fetch=fetch, local=lambda n: snap(n, self.NOW))
        self.assertEqual(fetched, ["h1", "h2"])
        b = B.build(nodes, self.NOW, g, [], {}, {"queue": [], "running": [], "done": []})
        self.assertEqual(set(b["nodes"]), {"win", "n1", "n2"})
        self.assertTrue(b["nodes"]["n2"]["stale"])
        self.assertFalse(b["nodes"]["n2"]["alive"])
        self.assertEqual(b["totals"]["today"]["in"], 2000)                 # win + n1, n2 missing
        self.assertTrue(B.summary_line(b).split("\t")[1].startswith("⬡2/3 ⚙0"))

    def test_compare_split_and_few(self):
        ledger = [
            {"node": "mac", "worker": "claude", "exit": 0, "duration_s": 30, "duration_api_ms": 20000, "cost_usd": 0.5},
            {"node": "mac", "worker": "claude", "exit": 1, "duration_s": 10, "cost_usd": 0.1},
            {"node": "mac", "worker": "blender", "status": "ok", "duration_s": 9, "render": {"total_s": 8}},
            {"node": "mac", "worker": "stub-ok", "exit": 0},
        ]
        rows = {(r["type"], r["node"]): r for r in B.compare(ledger, {})}
        self.assertNotIn(("stub-ok", "mac"), rows)
        c = rows[("claude -p", "mac")]
        self.assertEqual((c["n"], c["success_pct"], c["separable_n"]), (2, 50, 1))
        self.assertEqual((c["median_model_s"], c["median_tool_s"]), (20.0, 10.0))
        self.assertTrue(c["few"])
        self.assertEqual(rows[("render", "mac")]["median_model_s"], 0.0)

    def test_redaction_removes_paths_names_projects(self):
        s = snap("win", self.NOW, sessions=[{"pid": 1, "session_id": "abcdef", "name": "trabajo-x",
                                              "cwd": "C:\\Users\\someuser\\secret-work", "project": "secret-work",
                                              "started": 0}])
        s["mounts"] = [{"path": "/Users/yo/Compartidos/X", "share": "X", "ok": True}]
        g = B.gather({"win": {"host": "local"}}, self.NOW, local=lambda n: s)
        ledger = [{"node": "mac", "worker": "blender", "exit": 0, "stl": "laptop/Proyecto-3d/a.stl",
                   "png": "C:\\Users\\someuser\\x.png", "repo": "r", "id": "render-a"}]
        pres = {"win": {"level": "local", "mounts": {"checked_at": "x", "stale": False, "mounts": [
            {"name": "Compartido-Mac", "ok": True, "state": "mounted", "path": "~/Compartidos/Compartido-Mac"}]}}}
        b = B.build({"win": {"host": "local"}}, self.NOW, g, ledger, pres, {"queue": [], "running": [], "done": []})
        txt = json.dumps(W.public_snapshot(b, {"enjambre"}, b"k" * 32), ensure_ascii=False)
        for bad in ("someuser", "secret-work", "trabajo-x", "PI_Equipo", "Compartidos", "Compartido-Mac", "abcdef"):
            self.assertNotIn(bad, txt)
        self.assertIn("proyecto-", txt)

    def test_html_embeds_data_safely(self):
        b = {"x": "</script><script>alert(1)</script>"}
        html = B.render_html(b)
        self.assertNotIn("</script><script>alert", html)
        self.assertNotIn("/*__DATA__*/null", html)



@unittest.skipIf(B is None, "build.py needs Python 3.11+ (tomllib)")
class BuildF2(unittest.TestCase):
    """Owner feedback F2: alerts on top, win->mac, Landing icons, history range."""
    NOW = dt.datetime(2026, 10, 7, 12, tzinfo=dt.timezone.utc).timestamp()

    def test_alerts_count_recent_failed_and_incomplete(self):
        L = [{"id": "a", "status": "ok", "start": "2026-10-07T01:00:00+00:00"},
             {"id": "b", "status": "failed", "start": "2026-10-06T01:00:00+00:00", "node": "mac", "result": "boom"},
             {"id": "c", "status": "incomplete", "start": "2026-10-05T01:00:00+00:00"},
             {"id": "d", "exit": 1, "start": "2026-09-01T01:00:00+00:00"}]
        a = B.alerts(L, self.NOW)
        self.assertEqual((a["failed_7d"], a["incomplete_7d"], a["failed_all"], a["incomplete_all"]), (1, 1, 2, 1))
        self.assertEqual([r["id"] for r in a["rows"]], ["d", "c", "b"])      # newest ledger line first
        self.assertEqual([r["recent"] for r in a["rows"]], [False, True, True])
        self.assertEqual(a["rows"][2]["why"], "boom")

    def test_session_sent_jobs_to_mac(self):
        L = [{"origin_session": "s-1", "node": "mac"}, {"origin_session": "s-1", "node": "mac"},
             {"origin_session": "s-1", "node": "win"}, {"node": "mac"}]
        self.assertEqual(B.remote_jobs_by_session(L), {"s-1": {"mac": 2, "win": 1}})
        s = snap("win", self.NOW, sessions=[{"session_id": "s-1", "project": "p"}])
        g = B.gather({"win": {"host": "local"}}, self.NOW, local=lambda n: s)
        b = B.build({"win": {"host": "local"}}, self.NOW, g, L, {}, {"queue": [], "running": [], "done": []})
        self.assertEqual(b["nodes"]["win"]["sessions"][0]["sent_to"], {"mac": 2})   # own node is not a flow

    def test_landing_icons_by_folder_repo_or_alias(self):
        with tempfile.TemporaryDirectory() as t:
            L = Path(t)
            (L / "public" / "icons").mkdir(parents=True)
            (L / "public" / "icons" / "skills.svg").write_text(
                '<svg><symbol id="python" viewBox="0 0 24 24"><path d="M1 1"/></symbol>'
                '<symbol id="threedotjs" viewBox="0 0 24 24"><path d="M2 2"/></symbol></svg>', encoding="utf-8")
            pj, sk = L / "src" / "content" / "projects" / "es", L / "src" / "content" / "skills"
            pj.mkdir(parents=True)
            sk.mkdir(parents=True)
            (pj / "radar-precios.md").write_text("---\ntitle: R\nrepoUrl: \"https://github.com/x/farmacias\"\n"
                                                 "skills:\n  - \"python\"\n  - \"nada\"\n---\n", encoding="utf-8")
            (pj / "metal-globe.md").write_text("---\nskills: [\"threejs\"]\n---\n", encoding="utf-8")
            (pj / "signal-agent.md").write_text("---\nskills: [python]\n---\n", encoding="utf-8")
            (sk / "python.md").write_text("---\nname: \"Python\"\nbrand:\n  icon: \"python\"\n---\n", encoding="utf-8")
            (sk / "threejs.md").write_text("---\nname: \"three.js\"\nbrand:\n  icon: \"threedotjs\"\n---\n", encoding="utf-8")
            icons, proj = B.landing_icons(["farmacias", "Metallum", "signal agent", "otro"], {"Metallum": "metal-globe"}, L)
        self.assertEqual(proj["farmacias"], {"card": "radar-precios", "icons": ["python"]})   # by repo name
        self.assertEqual(proj["Metallum"]["icons"], ["threedotjs"])                         # by alias
        self.assertEqual(proj["signal agent"]["card"], "signal-agent")                      # by folder name
        self.assertNotIn("otro", proj)
        self.assertEqual(icons["threedotjs"]["name"], "three.js")

    def test_history_range_and_by_day_merge_nodes(self):
        a = dict(snap("win", self.NOW), history={"since": 1.0e9, "until": 1.1e9, "by_day": {"2026-10-01": {"cost": 2.0}}})
        m = dict(a, history={"by_day": {"2026-10-01": {"cost": 1.0}}})
        g = {"win": {"snap": a, "cfg": {"host": "local"}}, "mac": {"snap": m, "cfg": {}}}
        self.assertEqual(B.history_by_day(g), {"2026-10-01": {"win": {"cost": 2.0}, "mac": {"cost": 1.0}}})
        self.assertEqual(B.history_status(g)["win"]["since"], 1.0e9)


class SecondPass(unittest.TestCase):
    """Owner feedback, second pass: node labels, job detail, model measurements with time."""

    def test_node_label_from_config_else_os(self):
        self.assertEqual(B.node_label("mac", {"label": "Mac mini"}), "Mac mini")
        self.assertEqual(B.node_label("win", {"os": "windows"}), "PC")
        self.assertEqual(B.node_label("x", {"label": "<b>no</b>", "os": "linux"}), "Linux")
        g = B.gather({"mac": {"host": "mac", "label": "Mac mini"}}, 1.0e9, fetch=lambda h, p: (None, "down"))
        self.assertEqual(g["mac"]["cfg"]["label"], "Mac mini")

    def test_jobs_view_and_detail(self):
        L = [{"id": "j1", "worker": "claude", "repo": "r", "exit": 0, "start": "2026-10-07T01:00:00+00:00",
              "usage": {"x": 1}, "note": "n"},
             {"id": "j2", "worker": "stub-ok", "repo": "r", "status": "failed"}]
        J = B.jobs_view(L)
        self.assertEqual([j["id"] for j in J], ["j2", "j1"])               # newest first
        self.assertNotIn("usage", J[1])
        self.assertEqual(J[1]["status"], "ok")
        with tempfile.TemporaryDirectory() as t:
            st = Path(t)
            d = st / "done" / "j1"
            d.mkdir(parents=True)
            (d / "run.out").write_text("\n".join("l%d" % i for i in range(300)), encoding="utf-8")
            (d / "run.err").write_text("", encoding="utf-8")
            (d / "TASK-j1.md").write_text("---\nkind: claude\n---\n# Arregla el test\n", encoding="utf-8")
            det = B.jobs_detail(J, {"running": [], "queue": []}, st)
            self.assertEqual(set(det), {"j1"})                              # j2 ran by hand: no folder
            self.assertEqual(det["j1"]["title"], "Arregla el test")
            self.assertEqual(det["j1"]["log"]["run.out"].splitlines()[-1], "l299")
            self.assertEqual(len(det["j1"]["log"]["run.out"].splitlines()), 200)   # F3: last 200 lines
            self.assertNotIn("run.err", det["j1"]["log"])                   # empty log left out
            self.assertIsNotNone(det["j1"]["signal"])
            self.assertIsNone(B.job_files(st, "../done"))

    def test_measurements_time_from_row_or_single_file(self):
        with tempfile.TemporaryDirectory() as t:
            h = Path(t)
            (h / "modelos" / "bench-runs" / "2026-10-05").mkdir(parents=True)
            (h / "modelos" / "bench-runs" / "2026-10-05" / "llm-qa.json").write_text("{}")
            (h / "modelos" / "bench-runs" / "2026-10-05" / "llm-qa.warm.json").write_text("{}")
            (h / "modelos" / "bench-runs" / "2026-10-05" / "vlm-a.json").write_text("{}")
            (h / "modelos" / "bench-runs" / "2026-10-05" / "vlm-b.json").write_text("{}")
            rows = [{"key": "llm", "task": "text", "date": "2026-10-05"},
                    {"key": "vlm", "task": "vision", "date": "2026-10-05"},
                    {"key": "stt", "task": "transcribe", "date": "2026-10-07", "ts": 1791355551}]
            (h / "modelos" / "measurements.jsonl").write_text("\n".join(json.dumps(r) for r in rows + rows[:1]) + "\n")
            M = {r["key"]: r for r in C.model_measurements(str(h))}
        self.assertEqual(len(M), 3)                                          # identical line once
        self.assertEqual((M["stt"]["ts"], M["stt"]["ts_from"]), (1791355551, "fila"))
        self.assertEqual(M["llm"]["ts_from"], "archivo")                     # one file (warm ignored)
        self.assertIsNone(M["vlm"]["ts"])                                    # two files: no guess


class Icons(unittest.TestCase):
    def test_devicon_sanitiser_strips_active_content_and_prefixes_ids(self):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "visor" / "icons"))
        import vendor_devicon as V
        raw = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 128 128">'
               '<script>alert(1)</script><defs><linearGradient id="a"><stop offset="0" stop-color="#fff"/></linearGradient></defs>'
               '<path d="M0 0h1" fill="url(#a)" onclick="x()"/><use xlink:href="https://evil/x.svg#y"/>'
               '<foreignObject><div/></foreignObject><rect style="fill:url(https://evil)" width="1"/></svg>')
        vb, inner = V.clean(raw, "python")
        self.assertEqual(vb, "0 0 128 128")
        for bad in ("script", "onclick", "evil", "foreignObject", 'id="a"'):
            self.assertNotIn(bad, inner, bad)
        self.assertIn('id="vi-python-a"', inner)
        self.assertIn('fill="url(#vi-python-a)"', inner)
        with self.assertRaises(ValueError):
            V.clean('<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"/>', "k")

    def test_vendored_logo_wins_over_the_brand_glyph(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "python.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128"><path d="M1"/></svg>\n')
            self.assertEqual(B.vendored("python", Path(t)), ("0 0 128 128", '<path d="M1"/>'))
            self.assertIsNone(B.vendored("nada", Path(t)))


class Detached(unittest.TestCase):
    def test_real_heartbeat_and_dead_supervisor(self):
        with tempfile.TemporaryDirectory() as t:
            h = Path(t)
            for jid, state, pid in (("largo-1", "running", os.getpid()), ("largo-2", "running", 999999), ("largo-3", "ok", None)):
                d = h / ".enjambre" / "jobs" / jid
                d.mkdir(parents=True)
                (d / "status.json").write_text(json.dumps({"id": jid, "kind": "script", "repo": "r", "state": state,
                    "supervisor_pid": pid, "start": "2026-10-07T05:00:00-05:00", "cmd": ["secreto", "/Volumes/privado"],
                    "latido": {"ts": "2026-10-07T05:01:00-05:00", "elapsed_s": 60, "pct": 40.0, "last": "avance 40%"}}))
                (d / "log").write_text("avance 40%\n")
            got = {x["id"]: x for x in C.collect_detached(str(h))}
        self.assertEqual(got["largo-1"]["state"], "running")
        self.assertEqual(got["largo-2"]["state"], "dead")           # supervisor gone: not "running"
        self.assertEqual(got["largo-3"]["state"], "ok")
        self.assertEqual(got["largo-1"]["latido"]["pct"], 40.0)
        self.assertNotIn("cmd", got["largo-1"])
        jobs, detail = [], {}
        B.merge_detached(jobs, detail, {"mac": {"snap": {"detached": list(got.values())}}})
        self.assertEqual({j["status"] for j in jobs}, {"running", "muerto", "ok"})
        self.assertEqual(detail["largo-1"]["signal_file"], "status.json · latido")
        self.assertEqual(detail["largo-1"]["log"]["log"], "avance 40%")


if __name__ == "__main__":
    unittest.main()
