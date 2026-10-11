#!/usr/bin/env python3
"""build.py — the orchestrator half of the visor. Runs on Windows every few minutes.

1. For every node in nodes.toml: the local one runs collect.py in-process; a remote one is read
   with `ssh <host> cat <snapshot>` (that node's LaunchAgent/cron wrote it). No new ports.
2. Adds what only the orchestrator has: presence (state/nodes.json), the queue
   (state/queue|running|done) and the ledger (jobs.jsonl) -> per-task-type comparison.
3. Writes state/visor/snapshot.json, state/visor/visor.html (data embedded: a file:// page
   cannot fetch() a local JSON) and the one-line summary the statusline shows, pushed to every
   node over the same ssh.

Adding a node = one [nodes.<name>] table in nodes.toml + the collector installed there.
Optional per-node keys: visor_snapshot (default ~/.enjambre/visor/snapshot.json).

Usage: build.py [--no-push] [--demo OUT.html]   (--demo: redacted copy for screenshots)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
import collect as C  # noqa: E402
import web as W  # noqa: E402
sys.path.insert(0, str(REPO))
import razones as RZ  # noqa: E402

STATE = REPO / "state"
OUT = STATE / "visor"
DEFAULT_SNAPSHOT = "~/.enjambre/visor/snapshot.json"
STALE_S = 12 * 60          # remote collector runs every 5 min; two missed runs = stale
PRESENCE_FRESH_S = 90      # same rule as presence.FRESH_S
FEW = 5                    # below this many runs a comparison cell says "pocos datos"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ------------------------------------------------------------------ gather
def load_nodes(path: Path = REPO / "nodes.toml") -> dict:
    """nodes.toml plus the host_alt watch switched to (state/route.json, prompt 21 §3); same rule
    as presence.apply_route, repeated because this file stands alone. A fresh public clone has
    only nodes.example.toml: read that until the owner copies it."""
    if not path.exists() and path.with_name("nodes.example.toml").exists():
        path = path.with_name("nodes.example.toml")
    with open(path, "rb") as f:
        nodes = tomllib.load(f)["nodes"]
    try:
        route = json.loads((REPO / "state" / "route.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        route = {}
    for name, cfg in nodes.items():
        alt = (route.get(name) or {}).get("alias")
        if alt and alt == cfg.get("host_alt") and not os.environ.get(f"ENJAMBRE_HOST_{name.upper()}"):
            cfg["host"] = alt
    return nodes

def no_console() -> dict:
    """Popen kwargs: no console window for children of pythonw on Windows (CREATE_NO_WINDOW,
    plus SW_HIDE for Git's msys ssh.exe). Same as presence.no_console; this file stands alone."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}


def ssh(host: str, cmd: str, stdin: bytes | None = None, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, cmd],
                          input=stdin, capture_output=True, timeout=timeout, **no_console())


def fetch_remote(host: str, path: str) -> tuple[dict | None, str]:
    try:
        r = ssh(host, "cat " + path)
    except subprocess.TimeoutExpired:
        return None, "ssh timeout"
    if r.returncode != 0:
        return None, (r.stderr.decode("utf-8", "replace").strip().splitlines() or ["ssh failed"])[-1][:120]
    try:
        return json.loads(r.stdout), ""
    except ValueError:
        return None, "snapshot is not JSON"


def gather(nodes: dict, now: float, fetch=fetch_remote, local=None) -> dict:
    out = {}
    for name, cfg in nodes.items():
        if cfg.get("host") == "local":
            snap = (local or (lambda n: C.collect(n)))(name)
            err = ""
        else:
            snap, err = fetch(cfg["host"], cfg.get("visor_snapshot", DEFAULT_SNAPSHOT))
        age = now - snap["collected"] if snap else None
        out[name] = {"cfg": {k: cfg.get(k) for k in ("label", "host", "os", "arch", "ram_gb", "max_jobs", "tags")},
                     "snap": snap, "error": err, "age_s": age,
                     "stale": snap is None or age > STALE_S}
    return out


def presence(state: Path, nodes: dict, now: float) -> dict:
    try:
        d = json.loads((state / "nodes.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    upd = C.parse_ts(d.get("updated")) or 0
    fresh = now - upd <= PRESENCE_FRESH_S
    res = {}
    for name, cfg in nodes.items():
        if cfg.get("host") == "local":
            res[name] = {"level": "local", "fresh": True}
            continue
        n = (d.get("nodes") or {}).get(name) or {}
        res[name] = {"level": n.get("level", "unknown") if fresh else "unknown",
                     "last_level": n.get("level"), "since": n.get("since"), "fresh": fresh,
                     "updated_age_s": round(now - upd) if upd else None,
                     # Session E's SMB check: {checked_at, stale, mounts:[{name, ok, state, path}]};
                     # absent below ssh level. The collector's own `mount` scan is the fallback.
                     "mounts": n.get("mounts"),
                     # busy.py marker {quien, desde, tipo, pid, alive}: a long job owns the node
                     "busy": n.get("busy") if fresh else None}
    return res


def queue(state: Path) -> dict:
    res = {}
    for box in ("queue", "running", "done"):
        items = []
        for d in sorted((state / box).glob("*")) if (state / box).is_dir() else []:
            try:
                j = json.loads((d / "job.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                j = {"id": d.name}
            rc = (d / "rc").read_text().strip() if (d / "rc").exists() else None
            items.append({"id": j.get("id", d.name), "node": j.get("node"), "worker": j.get("worker"),
                          "enqueued": j.get("enqueued"), "rc": rc})
        res[box] = items
    return res


# ------------------------------------------------------------------ ledger -> comparison
def task_type(rec: dict) -> str | None:
    w = (rec.get("worker") or "").lower()
    if w.startswith("stub"):
        return None                                  # plumbing tests, not work
    if w == "blender":
        return "render"
    if w == "claude":
        return "claude -p"
    if w == "local":
        return "modelo local"
    if "test" in w:
        return "tests"
    if "git" in w:
        return "git"
    if "transcri" in w or "whisper" in w:
        return "transcripción"
    return w or None


def rec_status(rec: dict) -> str:
    return rec.get("status") or ("ok" if rec.get("exit") == 0 else "failed")


def split_times(rec: dict) -> tuple[float | None, float | None, float | None]:
    """(total_s, model_s in the cloud, tool_s on the node). None = the record does not say."""
    total = rec.get("duration_s")
    if rec.get("worker") == "blender":
        r = rec.get("render") or {}
        t = r.get("total_s", total)
        return t, 0.0, t                              # Blender never calls a cloud model
    if rec.get("worker") == "local":
        return total, 0.0, total
    api = rec.get("duration_api_ms")
    if api is not None and total is not None:
        m = api / 1000.0
        return total, m, max(total - m, 0.0)
    return total, None, None                          # older claude -p records: not separable


def read_ledger(path: Path) -> list:
    recs = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                recs.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return recs


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def compare(ledger: list, model_jobs: dict) -> list:
    """Rows per (task type, node). model_jobs: {node: [jobs from the model manager]}."""
    cells = {}
    for r in ledger:
        t = task_type(r)
        if not t:
            continue
        tot, mod, tool = split_times(r)
        c = cells.setdefault((t, r.get("node") or "?"), {"n": 0, "ok": 0, "tot": [], "mod": [], "tool": [],
                                                         "cost": 0.0, "cost_n": 0, "source": "ledger"})
        c["n"] += 1
        c["ok"] += rec_status(r) == "ok"
        c["tot"].append(tot); c["mod"].append(mod); c["tool"].append(tool)
        if r.get("cost_usd") is not None:
            c["cost"] += r["cost_usd"]; c["cost_n"] += 1
    for node, jobs in model_jobs.items():
        for j in jobs:
            if j.get("state") not in ("done", "failed", "error"):
                continue
            t = {"speakers": "hablantes", "transcribe": "transcripción"}.get(j.get("task"), "modelo local: " + str(j.get("task")))
            c = cells.setdefault((t, node), {"n": 0, "ok": 0, "tot": [], "mod": [], "tool": [],
                                             "cost": 0.0, "cost_n": 0, "source": "gestor de modelos"})
            c["n"] += 1
            c["ok"] += j.get("state") == "done"
            run_s = j.get("run_s")
            c["tot"].append((run_s or 0) + (j.get("wait_s") or 0) if run_s is not None else None)
            c["mod"].append(0.0)
            c["tool"].append(run_s)
            c["cost"] += 0.0; c["cost_n"] += 1
    rows = []
    for (t, node), c in sorted(cells.items()):
        rows.append({"type": t, "node": node, "n": c["n"], "ok": c["ok"],
                     "success_pct": round(100 * c["ok"] / c["n"]) if c["n"] else None,
                     "median_total_s": _med(c["tot"]), "median_model_s": _med(c["mod"]),
                     "median_tool_s": _med(c["tool"]),
                     "separable_n": sum(1 for x in c["mod"] if x is not None),
                     "cost_usd": round(c["cost"], 4) if c["cost_n"] else None,
                     "few": c["n"] < FEW, "source": c["source"]})
    return rows


# ------------------------------------------------------------------ usage roll-up
def roll_usage(snap: dict, today: str, week_from: str) -> dict:
    prices = {m: tuple(p) for m, p in (snap.get("prices") or {}).items()}
    res = {"today": _empty(), "week": _empty(), "by_day": {}, "projects_today": {}, "models_week": {}}
    for day, projs in (snap.get("usage") or {}).get("days", {}).items():
        for proj, models in projs.items():
            for model, row in models.items():
                cost = C.cost_of(model, row, prices)
                d = res["by_day"].setdefault(day, _empty())
                _acc(d, row, cost)
                if day >= week_from:
                    _acc(res["week"], row, cost)
                    _acc(res["models_week"].setdefault(model, _empty()), row, cost)
                if day == today:
                    _acc(res["today"], row, cost)
                    _acc(res["projects_today"].setdefault(proj, _empty()), row, cost)
    return res


def _empty() -> dict:
    return {"in": 0, "out": 0, "cw": 0, "cr": 0, "msgs": 0, "cost": 0.0, "unpriced_msgs": 0}


def _acc(d: dict, row: list, cost) -> None:
    for i, k in enumerate(("in", "out", "cw", "cr", "msgs")):
        d[k] += row[i]
    if cost is None:
        d["unpriced_msgs"] += row[4]
    else:
        d["cost"] += cost


# ------------------------------------------------------------------ all-time, per project
ALIAS_FILE = REPO / "config" / "visor-alias.toml"


def load_alias(path: Path = ALIAS_FILE) -> dict:
    """config/visor-alias.toml: {"alias": {folder: {alias, estado}}, "repos": {repo: project}}."""
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def job_project(r: dict, projects: dict, rmap: dict | None = None) -> str | None:
    """The visor project a ledger job belongs to: [repos] in visor-alias.toml, then the repo by
    exact name, last path component, first path component (algoritmos/ada-pc2 -> algoritmos),
    case aside. A render has no repo: the deepest folder of its STL path that is a project."""
    low = {k.lower(): k for k in projects}
    repo = str(r.get("repo") or "").replace("\\", "/").strip("/")
    if repo:
        if repo in (rmap or {}):
            return (rmap or {})[repo] if (rmap or {})[repo] in projects else None
        parts = [x for x in repo.split("/") if x]
        for cand in (repo, parts[-1], parts[0]):
            if cand.lower() in low:
                return low[cand.lower()]
        return None
    parts = [x for x in str(r.get("stl") or "").replace("\\", "/").split("/")[:-1] if x]
    for cand in reversed(parts):
        if cand.lower() in low:
            return low[cand.lower()]
    return None


def fold_job_worktrees(out: dict, ledger: list) -> None:
    """A job's agent works in a worktree named <repo>-<job id> (wt/Metallum-p12-ficha-genero);
    its sessions are that repo's work: fold them into the repo's project."""
    rows = {str(r.get("id")): r for r in ledger if r.get("id")}
    for name in list(out):
        for jid, row in rows.items():
            if name.endswith("-" + jid) and len(name) > len(jid) + 1:
                others = {k: v for k, v in out.items() if k != name}
                prefix = name[: -len(jid) - 1]
                repo = str(row.get("repo") or "").replace("\\", "/").rstrip("/")
                same = repo and repo.split("/")[-1].lower() == prefix.lower()
                parent = job_project(row if same else {"repo": prefix}, others)
                if parent is None:
                    continue
                src, dst = out.pop(name), out[parent]
                for k in ("sessions", "windows", "active_s", "prompts", "responses", "subagents", "unpriced_msgs"):
                    dst[k] += src.get(k) or 0
                for k in dst["tokens"]:
                    dst["tokens"][k] += src["tokens"].get(k) or 0
                dst["cost"] = round(dst["cost"] + src["cost"], 4)
                for k, f in (("first", min), ("last", max)):
                    if src.get(k) is not None:
                        dst[k] = src[k] if dst[k] is None else f(dst[k], src[k])
                for n, v in src["nodes"].items():
                    d = dst["nodes"].setdefault(n, {"sessions": 0, "cost": 0, "active_s": 0})
                    d["sessions"] = (d.get("sessions") or 0) + (v.get("sessions") or 0)
                    d["active_s"] = (d.get("active_s") or 0) + (v.get("active_s") or 0)
                    d["cost"] = round((d.get("cost") or 0) + (v.get("cost") or 0), 4)
                dst["timeline"] += [dict(t, job=jid) for t in src["timeline"]]
                dst.setdefault("subs", []).extend(src.get("subs") or [])
                dst.setdefault("folded", []).append(jid)
                break


def projects_all_time(gathered: dict, ledger: list) -> dict:
    """Merge every node's history.projects (collect.collect_history) and add the enjambre jobs
    whose `repo` is that project. Same project name on two nodes = one row, per-node detail kept."""
    out = {}
    for node, g in gathered.items():
        h = (g["snap"] or {}).get("history") or {}
        home = (g["snap"] or {}).get("home_name")
        for name, p in (h.get("projects") or {}).items():
            name = C.shown_project(name, None, node, home)       # the home folder is not a project
            o = out.setdefault(name, {"sessions": 0, "windows": 0, "active_s": 0, "prompts": 0,
                                      "responses": 0, "subagents": 0,
                                      "tokens": {"in": 0, "out": 0, "cw": 0, "cr": 0, "cw1h": 0},
                                      "cost": 0.0, "unpriced_msgs": 0, "first": None, "last": None,
                                      "nodes": {}, "timeline": [], "jobs": {"n": 0, "ok": 0, "cost": 0.0,
                                                                             "nodes": {}}})
            for k in ("sessions", "windows", "active_s", "prompts", "responses", "subagents", "unpriced_msgs"):
                o[k] += p.get(k) or 0
            for k in o["tokens"]:
                o["tokens"][k] += (p.get("tokens") or {}).get(k) or 0
            o["cost"] = round(o["cost"] + (p.get("cost") or 0), 4)
            for k, f in (("first", min), ("last", max)):
                if p.get(k) is not None:
                    o[k] = p[k] if o[k] is None else f(o[k], p[k])
            o["nodes"][node] = {"sessions": p.get("sessions"), "cost": p.get("cost"),
                                "active_s": p.get("active_s")}      # the "PC 80 % · Mac 20 %" split (F3)
            o["timeline"] += [dict(t, node=node) for t in p.get("timeline") or []]
            o.setdefault("subs", []).extend(dict(x, node=node) for x in p.get("subs") or [])
    fold_job_worktrees(out, ledger)
    rmap = (load_alias().get("repos") or {})
    for r in ledger:
        if task_type(r) is None:
            continue
        name = job_project(r, out, rmap)
        if name is None:
            continue
        j = out[name]["jobs"]
        j["n"] += 1
        j["ok"] += rec_status(r) == "ok"
        j["cost"] = round(j["cost"] + (r.get("cost_usd") or 0), 4)
        j["nodes"][r.get("node") or "?"] = j["nodes"].get(r.get("node") or "?", 0) + 1
        j.setdefault("ids", []).append(r.get("id"))
    for o in out.values():
        o["timeline"].sort(key=lambda t: t.get("start") or 0)
        o["timeline"] = o["timeline"][-C.TIMELINE_MAX:]
        o["subs"] = sorted(o.get("subs") or [], key=lambda x: x.get("start") or 0)[-C.SUBS_MAX:]
    return out


def history_status(gathered: dict) -> dict:
    return {n: {k: ((g["snap"] or {}).get("history") or {}).get(k)
                for k in ("files", "pending_files", "complete", "since", "until", "db")}
            for n, g in gathered.items()}


def history_by_day(gathered: dict) -> dict:
    """{day: {node: {cost, sessions, active_s}}} over all history (session cost on its start day)."""
    out = {}
    for n, g in gathered.items():
        for d, v in (((g["snap"] or {}).get("history") or {}).get("by_day") or {}).items():
            out.setdefault(d, {})[n] = v
    return dict(sorted(out.items()))


def heat(gathered: dict) -> dict:
    """{node: 7x24 matrix} weekday (Mon=0) x hour, Lima, approximate cost (collect.summarize_history)."""
    return {n: h for n, g in gathered.items()
            if isinstance(h := ((g["snap"] or {}).get("history") or {}).get("heat"), list) and len(h) == 7}


def _started_ts(r: dict):
    try:
        return dt.datetime.fromisoformat(str(r.get("start"))).timestamp()
    except ValueError:
        return None


def alerts(ledger: list, now: float, days: int = 7) -> dict:
    """Failed and incomplete jobs. Tests (sandbox, stubs, fire tests: razones.is_test) are
    counted apart and never colour the traffic light. Real ones are also grouped by cause:
    "9 fallidos: mac inalcanzable (ssh exit 255) 23:38–23:52" beats nine equal rows."""
    since = now - days * 86400
    rows = []
    for r in ledger:
        st = rec_status(r)
        if st in ("failed", "incomplete"):
            t = _started_ts(r)
            why = r.get("reason") if r.get("reason") and r.get("reason") not in (
                "budget", "killed", "dead", "permission_denials") else RZ.explain(dict(r, status=st))
            rows.append({"id": r.get("id"), "node": r.get("node"), "repo": r.get("repo"), "status": st,
                         "type": task_type(r) or r.get("worker"), "start": r.get("start"), "t0": t,
                         "recent": t is not None and t >= since, "test": RZ.is_test(r),
                         "why": RZ.home_paths(why), "log": bool(r.get("log_path"))})   # 22 §3
    rows.reverse()
    real = [x for x in rows if not x["test"]]
    groups = {}
    for x in real:
        g = groups.setdefault((x["why"] or "sin causa", x["status"]), {
            "why": x["why"] or "sin causa", "status": x["status"], "n": 0, "recent": 0,
            "first": None, "last": None, "nodes": [], "ids": []})
        g["n"] += 1
        g["recent"] += x["recent"]
        if x["t0"] is not None:
            g["first"] = x["t0"] if g["first"] is None else min(g["first"], x["t0"])
            g["last"] = x["t0"] if g["last"] is None else max(g["last"], x["t0"])
        if x["node"] and x["node"] not in g["nodes"]:
            g["nodes"].append(x["node"])
        g["ids"].append(x["id"])
    tests = [x for x in rows if x["test"]]
    return {"days": days,
            "failed_7d": sum(1 for x in real if x["recent"] and x["status"] == "failed"),
            "incomplete_7d": sum(1 for x in real if x["recent"] and x["status"] == "incomplete"),
            "failed_all": sum(1 for x in real if x["status"] == "failed"),
            "incomplete_all": sum(1 for x in real if x["status"] == "incomplete"),
            "tests_7d": sum(1 for x in tests if x["recent"]), "tests_all": len(tests),
            "groups": sorted(groups.values(), key=lambda g: -(g["last"] or 0)),
            "rows": real[:40], "test_rows": tests[:40]}


def remote_jobs_by_session(ledger: list) -> dict:
    """{origin_session: {node: n}}: jobs a Claude Code session sent to some node (win->mac)."""
    out = {}
    for r in ledger:
        sid = r.get("origin_session")
        if sid and r.get("node"):
            d = out.setdefault(sid, {})
            d[r["node"]] = d.get(r["node"], 0) + 1
    return out


# ------------------------------------------------------------------ node labels
OS_LABEL = {"windows": "PC", "macos": "Mac", "darwin": "Mac", "linux": "Linux"}
LABEL_RE = re.compile(r"[\w .()+-]{1,24}")


def node_label(name: str, cfg: dict) -> str:
    """What people read for a node: nodes.toml `label` ("PC", "Mac mini", "Laptop"); without
    one, a word from its OS. The id (win, mac) stays the key, never the text."""
    v = str((cfg or {}).get("label") or "").strip()
    if LABEL_RE.fullmatch(v):
        return v
    return OS_LABEL.get(str((cfg or {}).get("os") or "").lower(), name)


# ------------------------------------------------------------------ jobs
JOB_KEYS = ("id", "node", "worker", "repo", "branch", "start", "end", "exit", "duration_s", "pushed",
            "cost_usd", "duration_api_ms", "result", "kind", "routed_by", "route_reason",
            "permission_denials", "origin", "origin_session", "reason", "reason_code", "test",
            "log_path", "kind")
JOB_ID = re.compile(r"[\w.-]{1,80}")


def jobs_view(ledger: list) -> list:
    """Every job in the ledger, newest first, for the filterable list and its detail panel (stub
    runs included: the bandeja's phone test is one). The ledger is small; it is sent whole."""
    out = []
    for r in ledger:
        t = task_type(r) or r.get("worker") or "?"
        j = {k: r.get(k) for k in JOB_KEYS if r.get(k) is not None}
        j.update(type=t, status=rec_status(r), t0=_started_ts(r))
        out.append(j)
    return out[::-1]


LOG_TAIL = 200
LOG_BYTES = 60000


def _tail(path: Path, n: int = LOG_TAIL) -> str | None:
    try:
        lines = path.read_bytes()[-LOG_BYTES:].decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return None
    return "\n".join(lines) if any(x.strip() for x in lines) else None


def job_files(state: Path, jid: str, tail: int = LOG_TAIL) -> dict | None:
    """What the orchestrator kept of a job: its queue folder state/<box>/<id> (run.out, run.err,
    TASK-*.md) and/or its log in state/jobs/<id>/ (long jobs since E3, short ones since F3).
    "Ultima senal" = the newest mtime in the queue folder: derived, not a heartbeat
    (DECISIONES 40). A job run by hand before F3 may have neither."""
    if not JOB_ID.fullmatch(jid or ""):
        return None
    d = box = None
    for bx in ("running", "done", "queue"):
        if (state / bx / jid).is_dir():
            d, box = state / bx / jid, bx
            break
    log, title, newest = {}, None, None
    if d is not None:
        files = [f for f in d.iterdir() if f.is_file()]
        for name in ("run.out", "run.err"):
            t = _tail(d / name, tail)
            if t:
                log[name] = t
        for f in files:
            if f.name.startswith("TASK-") and f.suffix == ".md":
                try:
                    m = re.search(r"^#\s+(.+)$", f.read_text(encoding="utf-8", errors="replace"), re.M)
                except OSError:
                    m = None
                title = m.group(1).strip()[:120] if m else None
        newest = max(files, key=lambda f: f.stat().st_mtime) if files else None
    lp = RZ.log_of(jid, state)
    if lp is not None:
        t = _tail(lp, tail)
        if t:
            log["log"] = t
    if d is None and not log:
        return None
    return {"box": box, "signal": newest.stat().st_mtime if newest else None,
            "signal_file": newest.name if newest else None, "log": log, "title": title}


def jobs_detail(jobs: list, q: dict, state: Path = STATE) -> dict:
    """{job id: job_files} for the ledger's jobs and those still queued or running. Local only:
    logs and task titles never reach the web copy (web.public_snapshot does not read this key)."""
    ids = [j["id"] for j in jobs if j.get("id")] + [x["id"] for box in ("running", "queue")
                                                   for x in (q or {}).get(box) or [] if x.get("id")]
    out = {}
    for jid in ids:
        d = job_files(state, jid)
        if d:
            out[jid] = d
    return out


def _iso_ts(v):
    try:
        return dt.datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        return None


def merge_detached(jobs: list, detail: dict, gathered: dict) -> None:
    """Detached long jobs (jobrun.py on a node) into the job list and the detail panel. Their
    heartbeat is real (status.json `latido`), so it replaces the derived "last signal"."""
    known = {j.get("id") for j in jobs}
    extra = []
    for node, g in gathered.items():
        for x in (g["snap"] or {}).get("detached") or []:
            jid = x.get("id")
            if not jid or not JOB_ID.fullmatch(jid):
                continue
            st = x.get("state")
            status = {"running": "running", "ok": "ok", "dead": "muerto"}.get(st, st or "?")
            t0, t1 = _iso_ts(x.get("start")), _iso_ts(x.get("end"))
            if jid not in known:
                extra.append({"id": jid, "node": node, "repo": x.get("repo"), "type": "largo · " + str(x.get("kind") or "?"),
                              "status": status, "t0": t0, "start": x.get("start"), "end": x.get("end"),
                              "duration_s": round(t1 - t0) if t0 and t1 else None,
                              "cost_usd": x.get("cost_usd"), "result": x.get("reason"), "detached": True})
            b = x.get("latido") or {}
            d = detail.setdefault(jid, {"box": "node", "log": {}, "title": None})
            d.update(beat=b or None, signal=_iso_ts(b.get("ts")) if b else d.get("signal"),
                     signal_file="status.json · latido" if b else d.get("signal_file"),
                     attempt=x.get("attempt"), last=x.get("last"), state=status)
            if x.get("log"):
                d["log"]["log"] = x["log"]
            if x.get("supervisor_log"):
                d["log"]["supervisor.log"] = x["supervisor_log"]
    jobs[:0] = sorted(extra, key=lambda j: j.get("t0") or 0, reverse=True)


# ------------------------------------------------------------------ Landing tech icons
LANDING = REPO.parent / "Landing"
SYMBOL = re.compile(r'<symbol id="([^"]+)" viewBox="([^"]+)">(.*?)</symbol>', re.S)


def _frontmatter(text: str) -> str:
    m = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    return m.group(1) if m else ""


def _yaml_list(fm: str, key: str) -> list:
    m = re.search(r"^%s:\s*\[(.*?)\]" % key, fm, re.M)
    if m:
        return [x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()]
    m = re.search(r"^%s:\s*\r?\n((?:\s+-\s+.*\r?\n?)+)" % key, fm, re.M)
    return [x.strip().strip("\"'") for x in re.findall(r"-\s+(.*)", m.group(1))] if m else []


def _norm(s: str) -> str:
    return re.sub(r"[\s_]+", "-", s.strip().lower())


VENDOR = HERE / "icons"       # official multicolour SVGs (devicon, MIT): tools/visor/icons/SOURCES.json


def vendored(key: str, folder: Path = VENDOR) -> tuple[str, str] | None:
    """(viewBox, inner markup) of the official colour logo for an icon id, if one is vendored."""
    try:
        t = (folder / (key + ".svg")).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.match(r'<svg[^>]*viewBox="([^"]+)"[^>]*>(.*)</svg>\s*$', t, re.S)
    return (m.group(1), m.group(2)) if m else None


def os_icons(landing: Path = LANDING) -> dict:
    """Node OS marks. Apple and Linux from the Landing's simple-icons (CC0); simple-icons has no
    Windows mark (removed at Microsoft's request), so that one is four plain panes drawn here."""
    out = {"os-windows": {"vb": "0 0 24 24", "name": "Windows", "color": {"d": "#3A96DD", "l": "#0078D4"},
                          "svg": '<path d="M1 1h10v10H1zM13 1h10v10H13zM1 13h10v10H1zM13 13h10v10H13z"/>'}}
    for key, f in (("os-darwin", "apple"), ("os-linux", "linux")):
        s = ""
        # the Landing's copy first; a copy vendored here (simple-icons 16.23.0, CC0) when there is no
        # Landing next door (a public clone, the Mac): else the chip showed a blank square
        for src in (landing / "node_modules" / "simple-icons" / "icons" / (f + ".svg"), VENDOR / ("simple-" + f + ".svg")):
            try:
                s = src.read_text(encoding="utf-8")
                break
            except OSError:
                continue
        m = re.search(r'<path d="([^"]+)"', s)
        if m:
            out[key] = {"vb": "0 0 24 24", "name": "macOS" if f == "apple" else "Linux", "svg": '<path d="%s"/>' % m.group(1),
                        # Apple's mark is monochrome by brand rule: it takes the text colour
                        "color": None if f == "apple" else {"d": "#FCC624", "l": "#8A6D00"}}
    for key, i in out.items():
        v = vendored(key)
        if v:
            i.update(vb=v[0], svg=v[1], multi=True)
    return out


# Local model families (F3 §13): matched on the Hugging Face id (or the key), first hit wins.
# Logo = the brand's simple-icons glyph (CC0, the Landing's node_modules, same as the OS marks) when
# simple-icons has one; IBM, OpenAI, Zhipu, Lightricks, Black Forest Labs and the small projects
# have none there, so they get a monogram in their colour. (regex, id, name, slug, monogram, hex)
MODEL_FAMILIES = (
    (r"gemma", "gemma", "Gemma · Google", "google", None, "4285F4"),
    (r"qwen", "qwen", "Qwen · Alibaba", "qwen", None, "6950EF"),
    (r"z-image", "zimage", "Z-Image · Alibaba", "alibabacloud", None, "FF6A00"),
    (r"mistral|ministral", "mistral", "Mistral AI", "mistralai", None, "FA520F"),
    (r"granite", "granite", "Granite · IBM", None, "IBM", "0F62FE"),
    (r"llama", "llama", "Llama · Meta", "meta", None, "0467DF"),
    (r"glm", "glm", "GLM · Zhipu", None, "GLM", "3B6BF5"),
    (r"paddle", "paddle", "PaddleOCR · Baidu", "paddlepaddle", None, "2F7BD9"),
    (r"whisper", "whisper", "Whisper · OpenAI", None, "W", "10A37F"),
    (r"nemotron|parakeet", "nvidia", "NVIDIA", "nvidia", None, "76B900"),
    (r"flux", "flux", "FLUX · Black Forest Labs", None, "BFL", "8B95A5"),
    (r"ltx", "ltx", "LTX · Lightricks", None, "LTX", "8E6CFF"),
    (r"pyannote", "pyannote", "pyannote", None, "pa", "E5484D"),
    (r"sherpa|k2-fsa", "sherpa", "sherpa-onnx · k2-fsa", None, "k2", "2BA3A3"),
    (r"kokoro", "kokoro", "Kokoro", None, "K", "D97757"),
    (r"", "hf", "Hugging Face", "huggingface", None, "E8B500"),
)


def model_family(repo: str | None, key: str | None) -> str:
    text = "%s %s" % (repo or "", key or "")
    return next(f[1] for f in MODEL_FAMILIES if re.search(f[0], text, re.I))


def model_icons(landing: Path = LANDING) -> dict:
    """{mf-<family>: icon} in the shape of the stack icons (vb, svg, name, color)."""
    out = {}
    for _, fid, name, slug, mono, hexc in MODEL_FAMILIES:
        svg = None
        if slug:
            try:
                m = re.search(r'<path d="([^"]+)"', (landing / "node_modules" / "simple-icons" / "icons" / (slug + ".svg")).read_text(encoding="utf-8"))
                svg = '<path d="%s"/>' % m.group(1) if m else None
            except OSError:
                pass
        if not svg:
            mono = mono or re.sub(r"[^0-9A-Za-z]", "", name)[:2]     # no Landing next door: "Z-Image" -> "ZI"
            svg = ('<rect width="24" height="24" rx="6" opacity=".22"/><text x="12" y="%s" text-anchor="middle" '
                   'font-size="%s" font-weight="700" font-family="system-ui,sans-serif">%s</text>'
                   % (16 if len(mono) < 3 else 15.5, 11 if len(mono) < 3 else 8, mono))
        out["mf-" + fid] = {"vb": "0 0 24 24", "name": name, "svg": svg, "color": {"d": "#" + hexc, "l": "#" + hexc}}
    return out


def landing_icons(project_names, aliases: dict | None = None, landing: Path = LANDING) -> tuple[dict, dict]:
    """({icon_id: {vb, svg, name}}, {project: [icon_id]}) from the Landing's own sprite
    (public/icons/skills.svg) and each project card's `skills:` list. A visor project matches a
    card by folder name (normalised), by the card's repo name, or by an alias in visor-web.toml."""
    try:
        sprite = {m.group(1): (m.group(2), m.group(3))
                  for m in SYMBOL.finditer((landing / "public" / "icons" / "skills.svg").read_text(encoding="utf-8"))}
    except OSError:
        return {}, {}
    try:
        colors = json.loads((landing / "src" / "data" / "logo-colors.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        colors = {}
    cards = {}
    for f in sorted((landing / "src" / "content" / "projects" / "es").glob("*.md")):
        fm = _frontmatter(f.read_text(encoding="utf-8"))
        repo = re.search(r'^repoUrl:\s*"?[^"\n]*/([^/"\n]+?)"?\s*$', fm, re.M)
        cards[f.stem] = {"skills": _yaml_list(fm, "skills"), "repo": repo.group(1).lower() if repo else None}
    by_key = {}
    for slug, c in cards.items():
        by_key.setdefault(slug, slug)
        if c["repo"]:
            by_key.setdefault(c["repo"], slug)
    icons, proj = {}, {}
    for name in project_names:
        slug = (aliases or {}).get(name) or by_key.get(_norm(name))
        if slug not in cards:
            continue
        ids = []
        for sk in cards[slug]["skills"]:
            try:
                fm = _frontmatter((landing / "src" / "content" / "skills" / (sk + ".md")).read_text(encoding="utf-8"))
            except OSError:
                fm = ""
            icon = (re.search(r'^\s+icon:\s*"?([\w.-]+)"?', fm, re.M) or [None, sk])[1]
            label = (re.search(r'^name:\s*"?([^"\n]+)"?', fm, re.M) or [None, sk])[1]
            what = re.search(r'^what:\s*\n\s+es:\s*"?([^"\n]+)"?', fm, re.M)
            if icon in sprite:
                vb, svg = sprite[icon]
                c = colors.get(icon) or {}
                icons[icon] = {"vb": vb, "svg": svg, "name": label, "what": what.group(1) if what else None,
                               "color": {"d": c["dark"], "l": c["light"]} if c.get("dark") and c.get("light") else None}
                v = vendored(icon)
                if v:                             # the official colour logo; brand colour kept for the wash
                    icons[icon].update(vb=v[0], svg=v[1], multi=True)
                ids.append(icon)
        if ids:
            proj[name] = {"card": slug, "icons": ids}
    return icons, proj


def models_inventory(out_nodes: dict) -> dict:
    """{node: [catalog rows + last job of that model]} for nodes whose manager answered /catalog.
    Jobs carry the picked model key since modelos-mac stored it in params (auto pick); older jobs
    have none and count for no model."""
    inv = {}
    for name, n in out_nodes.items():
        mo = n.get("models") or {}
        cat = mo.get("catalog")
        if not cat:
            continue
        last = {}
        for j in mo.get("jobs") or []:
            k = j.get("model")
            if k and (k not in last or (j.get("created") or 0) > (last[k].get("created") or 0)):
                last[k] = j
        rows = []
        for m in cat.get("models") or []:
            j = last.get(m.get("key"))
            rows.append(dict(m, family=model_family(m.get("repo"), m.get("key")),
                             last_job={k: j.get(k) for k in ("task", "state", "created", "run_s",
                                                                  "peak_footprint_mb")} if j else None))
        meas = sorted(mo.get("measurements") or [], key=lambda r: (r.get("ts") or 0, r.get("date") or ""), reverse=True)
        jobs = [{k: j.get(k) for k in ("id", "task", "state", "created", "run_s", "load_s", "wait_s", "model",
                                       "peak_footprint_mb")} for j in mo.get("jobs") or []]
        inv[name] = {"budget_mb": cat.get("budget_mb"), "models": rows, "measurements": meas, "jobs": jobs}
    return inv


# ------------------------------------------------------------------ assemble
def build(nodes: dict, now: float, gathered: dict, ledger: list, pres: dict, q: dict) -> dict:
    today = C.local_day(now)
    week_from = C.local_day(now - 6 * 86400)
    out_nodes, model_jobs, quota = {}, {}, None
    for name, g in gathered.items():
        s = g["snap"] or {}
        usage = roll_usage(s, today, week_from) if s else None
        if s.get("models"):
            model_jobs[name] = s["models"].get("jobs") or []
        qq = s.get("quota")
        if qq and (quota is None or qq["ts"] > quota["ts"]):
            quota = dict(qq, node=name)
        p = pres.get(name, {})
        alive = p.get("level") in ("local", "ready") or (not g["stale"])
        out_nodes[name] = {"cfg": g["cfg"], "label": node_label(name, g["cfg"]), "presence": p, "alive": alive, "stale": g["stale"],
                           "age_s": round(g["age_s"]) if g["age_s"] is not None else None,
                           "error": g["error"], "usage": usage,
                           **{k: s.get(k) for k in ("sessions", "disks", "memory", "mounts", "tmux",
                                                    "models", "errors", "os", "python")}}
    if quota is None:      # no fresh reading: the last known one, with its age (F3)
        for name, g in gathered.items():
            ql = (g["snap"] or {}).get("quota_last")
            if ql and ql.get("ts") and (quota is None or ql["ts"] > quota["ts"]):
                quota = dict(ql, node=name, stale=True)
    tot_today, tot_week = _empty(), _empty()
    for n in out_nodes.values():
        if n["usage"]:
            for k in tot_today:
                tot_today[k] += n["usage"]["today"][k]
                tot_week[k] += n["usage"]["week"][k]
    jobs = jobs_view(ledger)
    job_detail = jobs_detail(jobs, q)
    merge_detached(jobs, job_detail, gathered)
    thr = next((g["snap"].get("thresholds") for g in gathered.values()
                if g["snap"] and g["cfg"].get("host") == "local"), None)
    sent = remote_jobs_by_session(ledger)
    for name, n in out_nodes.items():                 # live sessions that sent jobs elsewhere: win->mac
        n["sessions"] = [dict(s, project=C.shown_project(s.get("project"), s.get("cwd"), name),
                              sent_to={k: v for k, v in sent.get(s.get("session_id"), {}).items() if k != name})
                         for s in n.get("sessions") or []]
    projects = projects_all_time(gathered, ledger)
    rmap, by_id = load_alias().get("repos") or {}, {str(r.get("id")): r for r in ledger}
    for j in jobs:                                    # the project a job counts in (F3)
        j["project"] = job_project(by_id.get(str(j.get("id"))) or j, projects, rmap)
    for p in projects.values():
        for t in p["timeline"]:
            t["sent_to"] = {k: v for k, v in sent.get(t.get("sid"), {}).items() if k != t.get("node")}
    icons, proj_icons = landing_icons(projects.keys(), (W.load_config().get("icons") or {}).get("aliases"))
    icons.update(model_icons())
    for name, v in proj_icons.items():
        projects[name]["icons"] = v["icons"]
        projects[name]["card"] = v["card"]
    return {"built": now, "today": today, "thresholds": thr, "week_from": week_from, "nodes": out_nodes,
            "quota": quota, "totals": {"today": tot_today, "week": tot_week},
            "queue": q, "ledger_n": len(ledger),
            "ledger_incomplete": sum(1 for r in ledger if rec_status(r) == "incomplete"),
            "jobs": jobs, "job_detail": job_detail, "compare": compare(ledger, model_jobs),
            "projects": projects, "icons": icons, "history_status": history_status(gathered),
            "history_by_day": history_by_day(gathered), "heat": heat(gathered), "alerts": alerts(ledger, now),
            "models_inventory": models_inventory(out_nodes),
            "prices_estimated": any((g["snap"] or {}).get("prices_estimated") for g in gathered.values()),
            "running_models": {n: (s.get("models") or {}).get("health", {}).get("running")
                               for n, s in out_nodes.items() if s.get("models")}}


def summary_line(b: dict) -> str:
    """`<epoch>\\t<text>` — the statusline prints <text> and judges freshness by <epoch>."""
    alive = sum(1 for n in b["nodes"].values() if n["alive"])
    running = len(b["queue"]["running"]) + sum(1 for v in b["running_models"].values() if v)
    t = b["totals"]["today"]
    tok = t["in"] + t["out"] + t["cw"] + t["cr"]
    text = "⬡%d/%d ⚙%d ≈$%.0f hoy %s" % (alive, len(b["nodes"]), running, t["cost"], _human(tok))
    return "%d\t%s" % (int(b["built"]), text)


def _human(n: int) -> str:
    for unit, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return "%.1f%s" % (n / div, unit)
    return str(n)


# ------------------------------------------------------------------ redaction
# The demo and the web copy both go through web.public_snapshot (an allowlist): see web.py.


# ------------------------------------------------------------------ output
FONTS = (("Space Grotesk Variable", "space-grotesk-latin-wght-normal.woff2", "300 700"),
         ("JetBrains Mono Variable", "jetbrains-mono-latin-wght-normal.woff2", "100 800"))


def font_faces(folder: Path = HERE / "fonts") -> str:
    """The Landing's two variable fonts inlined as data: URIs (vendored from @fontsource-variable,
    OFL, licences next to them). One file then works from file:// and behind the web login, and
    the Landing CSP already allows `font-src data:`. A missing font falls back to system fonts."""
    import base64
    out = []
    for fam, fn, w in FONTS:
        try:
            b64 = base64.b64encode((folder / fn).read_bytes()).decode("ascii")
        except OSError:
            continue
        out.append("@font-face{font-family:'%s';font-style:normal;font-display:swap;font-weight:%s;"
                   "src:url(data:font/woff2;base64,%s) format('woff2')}" % (fam, w, b64))
    return "\n".join(out)


def render_html(b: dict | None, template: Path = HERE / "visor.html") -> str:
    """b=None: the web page, which fetches data.php instead of carrying the data."""
    html = (template.read_text(encoding="utf-8").replace("/*__FONTS__*/", font_faces())
            .replace("/*__OSICONS__*/{}", json.dumps(os_icons()).replace("</", "<\\/")))   # template, not data
    if b is None:
        return html
    data = json.dumps(b, ensure_ascii=False).replace("</", "<\\/")
    return html.replace("/*__DATA__*/null", data)


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def push_summary(nodes: dict, line: str) -> dict:
    res = {}
    home = Path(os.path.expanduser("~/.enjambre/visor"))
    write_atomic(home / "summary.txt", line + "\n")
    for name, cfg in nodes.items():
        if cfg.get("host") == "local":
            continue
        try:
            r = ssh(cfg["host"], "mkdir -p ~/.enjambre/visor && cat > ~/.enjambre/visor/summary.tmp"
                                 " && mv -f ~/.enjambre/visor/summary.tmp ~/.enjambre/visor/summary.txt",
                    stdin=(line + "\n").encode("utf-8"))
            res[name] = r.returncode == 0
        except subprocess.TimeoutExpired:
            res[name] = False
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--demo", help="also write a redacted HTML here (for tutorial screenshots)")
    ap.add_argument("--keep", default="enjambre", help="comma list of project names not aliased in --demo")
    a = ap.parse_args(argv)
    now = time.time()
    nodes = load_nodes()
    gathered = gather(nodes, now)
    b = build(nodes, now, gathered, read_ledger(REPO / "jobs.jsonl"), presence(STATE, nodes, now), queue(STATE))
    write_atomic(OUT / "snapshot.json", json.dumps(b, ensure_ascii=False, indent=1))
    write_atomic(OUT / "visor.html", render_html(b))
    write_atomic(OUT / "visor-stamp.js", "window.VISOR_STAMP = %r;\n" % b["built"])   # the page reloads on change
    line = summary_line(b)
    pushed = {} if a.no_push else push_summary(nodes, line)
    if a.demo:                                    # the same allowlist the web gets
        write_atomic(Path(a.demo), render_html(W.public_snapshot(b, set(a.keep.split(",")), W.salt())))
    try:
        web = W.maybe_upload(b, nodes, now)
    except Exception as e:                        # the web copy must never break the local visor
        web = "error %s" % type(e).__name__
    msg = "%s %s | push: %s | web: %s | errors: %s" % (
        dt.datetime.now().isoformat(timespec="seconds"), line.split("\t", 1)[1], pushed, web,
        {n: (g["error"] or (g["snap"] or {}).get("errors")) for n, g in gathered.items()})
    log = OUT / "build.log"                       # pythonw has no console: this is the only trace
    if log.exists() and log.stat().st_size > 1_000_000:
        os.replace(log, log.with_suffix(".log.1"))
    with open(log, "a", encoding="utf-8") as f:
        f.write(msg + "\n")
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
