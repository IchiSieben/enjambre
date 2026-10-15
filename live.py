"""live — one truth for "what is running right now": state/visor/live.json (prompts 18 + 19).

`enjambre live --serve` is a resident loop (a Scheduled Task at logon, like watch). Every cycle:
  - each node answers ONE call: the local one in-process, a remote one with one ssh running
    `collect.py --live` there (sessions, running detached jobs with their beat, tmux, telemetry);
  - the PC adds what only it has: dispatched jobs (state/jobs/<id>/job.json, written the moment
    `run --detach` returns, before the node's first beat), the queue and the presence state;
  - it writes state/visor/live.json and live.js (window.LIVE = ...; a file:// page cannot fetch()
    a neighbour but can load it as a script), plus ring.js with each node's last 4 h of numbers.
`run --detach` touches state/visor/live.kick: the loop wakes within a second instead of waiting.

Nothing here writes jobs.jsonl or moves queue items: watch still owns the ledger and the queue.
The one write to watch's nodes.json is mark_down (a node live could not ship to); watch's next
tick reads it as one more observation. Accepted race: a mark written between watch's read and
write is lost (watch's probe decides anyway), or carries a busy/mounts up to one tick old.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import presence as P

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
OUT = STATE / "visor"
JOBS = STATE / "jobs"
KICK = OUT / "live.kick"
sys.path.insert(0, str(HERE / "tools" / "visor"))
import collect as C  # noqa: E402

INTERVAL_S = 10            # one cycle; the page polls live.js every 5 s
SLOW_SSH_S = 2.0           # a node slower than this is read every SLOW_EVERY_S instead (and says so)
SLOW_EVERY_S = 60
RUNNING_S = 60             # a session whose bar/session file changed in the last minute is running
IDLE_S = 30 * 60           # waiting longer than this folds into "en espera larga"
STALLED_S = 5 * 60         # a running job with no beat for this long is stalled (amber), not failed
DONE_SHOW_S = 15 * 60      # a finished job stays on the "now" strip this long
NOSIGNAL_S = 120           # a node unread this long: its jobs say "sin señal del nodo", they do not vanish
TELE_AMBER_S = 60          # prompt 22 §1: a gauge reading older than this is amber ...
TELE_RED_S = 180           # ... and older than this red, "lectura vieja"
REMOTE_COLLECT = "/usr/bin/python3 ~/.enjambre/visor/collect.py --node {node} --live"
REMOTE_EXTRA: dict = {}    # node -> extra collect.py args (prompt 20: --lab / --lab-port / --adopted)
REMOTE_RING = "tail -n {n} ~/.enjambre/visor/telemetria.ring.jsonl"
SHIP_TIMEOUT_S = 30        # one scp at start; a node that does not answer fails at ConnectTimeout (6 s)
SHIP_BACKOFF_S = (5, 10, 20, 40, 80, 120)   # waits between retries of what did not ship; the last repeats
DOWN_ERR = re.compile(r"timed out|connect to host|Connection (?:refused|closed|reset)|Could not resolve"
                      r"|No route|Network is unreachable|Host is down|Operation timed out", re.I)


def iso(ts: float | None) -> str | None:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds") if ts else None


def ts_of(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return dt.datetime.fromisoformat(str(v)).timestamp()
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ pure rules (tested)
def tele_level(tele: dict | None, now: float) -> tuple:
    """(age_s, level) of a node's telemetry reading: what "ahora" on a gauge really means.
    level: ok / warn (> TELE_AMBER_S) / crit (> TELE_RED_S, "lectura vieja"); (None, None) unread."""
    at = ts_of((tele or {}).get("at"))
    if at is None:
        return None, None
    age = max(0.0, now - at)
    return round(age), "crit" if age > TELE_RED_S else "warn" if age > TELE_AMBER_S else "ok"


def session_state(s: dict, now: float, has_running_job: bool = False) -> tuple[str, float | None]:
    """running / waiting / idle (prompt 18 §2.1). Claude Code's own `status: busy` counts as
    running; so does a statusline or session file touched in the last minute, or a job of the
    swarm that this session asked for and that is still running."""
    last = max([t for t in (s.get("updated"), s.get("bar_ts"), s.get("started")) if t] or [0]) or None
    if s.get("status") == "busy" or has_running_job or (last and now - last <= RUNNING_S):
        return "running", last
    if last and now - last > IDLE_S:
        return "idle", last
    return "waiting", last


def job_state(meta: dict | None, st: dict | None, now: float, node_ok: bool = True) -> str:
    """launching (dispatched, the node has not beaten yet) / running / stalled / dead /
    ok|failed|incomplete|killed. A `running` whose supervisor is gone arrives as `dead` from
    collect.py: never green."""
    if st is None:
        if meta and meta.get("collected"):
            return "done"
        if not node_ok:                       # the node did not answer: unknown, but not gone
            return "nosignal"
        launched = ts_of((meta or {}).get("launched"))
        return "launching" if launched and now - launched < 120 else "unknown"
    state = st.get("state")
    if state == "running" and not node_ok:        # last good read is too old to say "running"
        return "nosignal"
    if state == "running":
        beat = ts_of((st.get("latido") or {}).get("ts"))
        if beat and now - beat > STALLED_S:
            return "stalled"
        return "running"
    return state or "unknown"


def job_card(jid: str, node: str, meta: dict | None, st: dict | None, now: float,
             node_ok: bool = True) -> dict:
    meta, st = meta or {}, st or {}
    b = st.get("latido") or {}
    state = job_state(meta or None, st or None, now, node_ok)
    return {"id": jid, "tipo": "trabajo", "proyecto": st.get("repo") or meta.get("repo"),
            "nodo": node, "estado": state, "kind": st.get("kind") or meta.get("kind"),
            "started_at": st.get("start") or meta.get("launched"), "heartbeat_at": b.get("ts"),
            "turno": b.get("turns") if b.get("turns") is not None else st.get("turns"),
            "max_turnos": meta.get("max_turns"), "usd": b.get("cost_est_usd") if b.get("cost_est_usd") is not None else st.get("cost_usd"),
            "tope_usd": meta.get("budget_usd"), "pct": b.get("pct"), "elapsed_s": b.get("elapsed_s"),
            "ultima_linea": (b.get("last_tool") or st.get("last") or "")[:160] or None,
            "pedido_por": meta.get("origin_session"), "end": st.get("end"),
            "prioridad": st.get("priority") or meta.get("priority") or "normal",
            "motivo": st.get("sched_reason") or meta.get("sched_reason"),
            "motivo_desde": st.get("sched_since"),
            "last_activity": ts_of(b.get("ts")) or ts_of(st.get("start")) or ts_of(meta.get("launched"))}


def assemble(nodes: dict, payloads: dict, metas: list, queue: dict, presence: dict, now: float,
             node_meta: dict | None = None) -> dict:
    """payloads = {node: collect.live_payload() or None}; metas = PC-side job.json of jobs not yet
    collected (plus recently collected ones). Pure: every input is a value."""
    node_meta = node_meta or {}
    # A node whose last good read is older than NOSIGNAL_S (or never came) is "without signal".
    node_ok = {n: payloads.get(n) is not None and now - ((node_meta.get(n) or {}).get("read_at") or now) <= NOSIGNAL_S
               for n in nodes}
    st_by_id, host_of = {}, {}
    for n, p in payloads.items():
        for d in (p or {}).get("detached") or []:
            st_by_id[d["id"]] = d
            host_of[d["id"]] = n
    jobs, seen = [], set()
    for m in metas:
        jid = m.get("id")
        if not jid:
            continue
        seen.add(jid)
        node = m.get("node") or host_of.get(jid)
        card = job_card(jid, node, m, st_by_id.get(jid), now, node_ok.get(node, True))
        jobs.append(card)
    for jid, st in st_by_id.items():                # a node's job this PC never launched
        if jid not in seen:
            jobs.append(job_card(jid, host_of[jid], None, st, now))
    jobs = [j for j in jobs if j["estado"] in ("launching", "running", "stalled", "nosignal",
                                               "paused_by_priority", "evicted_resumable")
            or (j["estado"] in ("ok", "failed", "incomplete", "killed")
                and (ts_of(j.get("end")) or 0) >= now - DONE_SHOW_S)]
    for item in queue.get("queue") or []:        # prompt 20: queued / waiting_window / waiting_resources
        st = item.get("state") if item.get("state") in ("waiting_window", "waiting_resources") else "queued"
        jobs.append({"id": item.get("id"), "tipo": "trabajo", "proyecto": item.get("repo"),
                     "nodo": item.get("node"), "estado": st, "kind": item.get("kind"),
                     "prioridad": item.get("priority") or "normal", "motivo": item.get("reason"),
                     "motivo_desde": item.get("state_since"), "last_activity": ts_of(item.get("enqueued"))})
    for item in queue.get("running") or []:      # short `run` jobs watch dispatched (worker.sh)
        jobs.append({"id": item.get("id"), "tipo": "trabajo", "proyecto": item.get("repo"),
                     "nodo": item.get("node"), "estado": "running", "kind": "run",
                     "prioridad": item.get("priority") or "normal", "motivo": item.get("reason"),
                     "last_activity": ts_of(item.get("enqueued"))})
    running_by_session = {}
    for j in jobs:
        if j["estado"] in ("running", "launching", "stalled", "nosignal") and j.get("pedido_por"):
            running_by_session.setdefault(j["pedido_por"], []).append(j["id"])
    sessions = []
    live_ids = {j["id"]: j for j in jobs if j["estado"] in ("running", "launching", "stalled", "nosignal")}
    for n, p in payloads.items():
        for s in (p or {}).get("sessions") or []:
            sid = s.get("session_id")
            # A detached agent is itself a Claude Code process in the job's worktree
            # (<repo>-<job id>): it is that job's card, not one more session.
            own = next((j for jid, j in live_ids.items() if j.get("nodo") == n
                        and str(s.get("project") or "").endswith("-" + jid)), None)
            if own is not None:
                own["ctx_k"] = s.get("context_k")
                continue
            state, last = session_state(s, now, bool(running_by_session.get(sid)))
            sessions.append({"id": s.get("name") or (sid or "")[:8], "session_id": sid, "tipo": "sesion",
                             "proyecto": C.shown_project(s.get("project"), s.get("cwd"), n), "nodo": n, "estado": state,
                             "last_activity": last, "started": s.get("started"),
                             "ctx_k": s.get("context_k"), "usd": s.get("cost_usd"),
                             "handoff_ts": s.get("handoff_mtime"),   # prompt 21 §5: live age in the visor
                             "despachos": running_by_session.get(sid) or []})
    order = {"running": 0, "launching": 0, "stalled": 1, "nosignal": 1, "paused_by_priority": 1,
             "evicted_resumable": 1, "queued": 2, "waiting_resources": 2, "waiting_window": 2,
             "waiting": 3, "idle": 4}
    key = lambda x: (order.get(x["estado"], 5), -(x.get("last_activity") or 0))
    jobs.sort(key=key)
    sessions.sort(key=key)
    run_jobs = [j for j in jobs if j["estado"] in ("running", "launching", "stalled", "nosignal")]
    run_sess = [s for s in sessions if s["estado"] == "running"]
    per_node = {}
    for x in run_jobs + run_sess:
        per_node[x["nodo"]] = per_node.get(x["nodo"], 0) + 1
    last_event = max([x.get("last_activity") or 0 for x in jobs + sessions] or [0]) or None
    out_nodes = {}
    for n, cfg in nodes.items():
        p = payloads.get(n)
        nm = node_meta.get(n, {})
        pres = (presence.get("nodes") or {}).get(n) or {}
        out_nodes[n] = {"label": cfg.get("label") or n, "os": cfg.get("os"),
                        "ok": (p is not None and not nm.get("error")) if cfg.get("host") != "local" else True,
                        "lat_ms": nm.get("lat_ms"), "read_at": iso(nm.get("read_at")),
                        "stale_s": round(now - nm["read_at"]) if nm.get("error") and nm.get("read_at") else None,
                        "slow": nm.get("slow", False), "error": nm.get("error"),
                        "level": pres.get("level") or ("local" if cfg.get("host") == "local" else None),
                        "tmux": (p or {}).get("tmux"), "errors": (p or {}).get("errors") or [],
                        "via": nm.get("via") or pres.get("via"), "via_reason": pres.get("via_reason")}
    tele = {n: (payloads.get(n) or {}).get("telemetria") or (node_meta.get(n) or {}).get("tele_last")
            for n in nodes}
    for n in nodes:
        out_nodes[n]["tele_edad_s"], out_nodes[n]["tele_nivel"] = tele_level(tele[n], now)
    return {"schema": 1, "updated_at": iso(now), "updated_ts": now, "interval_s": INTERVAL_S,
            "tele_umbrales": [TELE_AMBER_S, TELE_RED_S], "nodos": out_nodes,
            "ahora": {"running": len(run_jobs) + len(run_sess), "queued": len([j for j in jobs if j["estado"] in ("queued", "waiting_window", "waiting_resources")]),
                      "waiting": len([s for s in sessions if s["estado"] == "waiting"]),
                      "idle": len([s for s in sessions if s["estado"] == "idle"]),
                      "stalled": len([j for j in jobs if j["estado"] in ("stalled", "nosignal")]),
                      "por_nodo": per_node, "ultimo_evento": iso(last_event), "ultimo_evento_ts": last_event},
            "trabajos": jobs, "sesiones": sessions, "telemetria": tele}


def count_externos(live: dict) -> dict:
    """22 §2.1: the header counted jobs and sessions but not the planner's external jobs (lab
    renders, adopted processes), so "2 corriendo" sat over 3 running cards. Adds the running ones
    to `ahora.running` and to their node, and says how many were external."""
    A = live["ahora"]
    ext = [x for x in ((live.get("planificador") or {}).get("externos") or []) if x.get("estado") == "running"]
    A["externos"] = len(ext)
    A["running"] += len(ext)
    per = dict(A.get("por_nodo") or {})
    for x in ext:
        per[x.get("nodo")] = per.get(x.get("nodo"), 0) + 1
    A["por_nodo"] = per
    return live


# ------------------------------------------------------------------ reposo (prompt 19 §2.4)
# Visible rules over the ring; the numbers live in ~/.claude/rules/thresholds.json.
REPOSO_IDS = ("visor-reposo-pressure-s", "visor-reposo-thermal-s", "visor-reposo-swap-gb",
              "visor-reposo-swap-window-s")
# Two ring lines further apart than this break a "sustained" run. A slow node (ssh > 2 s) is read,
# and its ring appended, only every SLOW_EVERY_S: the gap must allow that, or a node under memory
# pressure (the one whose ssh slows down) could never trip the rule.
RING_GAP_S = SLOW_EVERY_S + 3 * INTERVAL_S


def _run_since(ring: list, now: float, bad) -> float | None:
    """Start of the unbroken run of ring lines, ending at the newest one, where bad(line) holds.
    None when the newest line is fine, unknown, or too old to speak for now."""
    since, after = None, now
    for ln in reversed(ring):
        t = ts_of(ln.get("at"))
        if t is None or after - t > RING_GAP_S or not bad(ln):
            break
        since, after = t, t
    return since


def reposo_rules(ring: list, thr: dict | None, now: float) -> list | None:
    """The rules that fire for one node, each {id, msg, since, held_s, no_dispatch}.
    None when the thresholds cannot be read (the page says so instead of guessing)."""
    if not thr or any(k not in thr for k in REPOSO_IDS):
        return None
    out = []
    since = _run_since(ring, now, lambda ln: ln.get("pr") not in (None, "normal"))
    if since is not None and now - since >= thr["visor-reposo-pressure-s"]:
        out.append({"id": "presion", "since": iso(since), "held_s": round(now - since), "no_dispatch": True,
                    "msg": f"sin memoria: presión {ring[-1].get('pr')} desde hace "
                           f"{round((now - since) / 60)} min: no despachar más aquí"})
    since = _run_since(ring, now, lambda ln: isinstance(ln.get("csl"), (int, float)) and ln["csl"] < 100)
    if since is not None and now - since >= thr["visor-reposo-thermal-s"]:
        out.append({"id": "termico", "since": iso(since), "held_s": round(now - since), "no_dispatch": False,
                    "msg": f"estrangulando térmicamente: CPU al {ring[-1]['csl']} %, rendimiento reducido"})
    win = thr["visor-reposo-swap-window-s"]
    pts = [(ts_of(ln.get("at")), ln.get("swap")) for ln in ring]
    pts = [(t, v) for t, v in pts if t is not None and now - t <= win and isinstance(v, (int, float))]
    if len(pts) >= 2 and now - pts[-1][0] <= RING_GAP_S:
        t0, low = min(pts, key=lambda x: x[1])
        grew = pts[-1][1] - low
        if grew >= thr["visor-reposo-swap-gb"] and pts[-1][0] > t0:
            out.append({"id": "swap", "since": iso(t0), "held_s": round(now - t0), "no_dispatch": False,
                        "msg": f"paginando: swap +{grew:.1f} GB en {round((pts[-1][0] - t0) / 60)} min"})
    return out


# ------------------------------------------------------------------ io
def read_metas(now: float, keep_done_s: float = DONE_SHOW_S) -> list:
    out = []
    for p in JOBS.glob("*/job.json"):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not m.get("collected") or (ts_of(m.get("collected_at")) or 0) >= now - keep_done_s:
            out.append(m)
    return out


def read_queue() -> dict:
    res = {}
    for box in ("queue", "running"):
        items = []
        for d in sorted((STATE / box).glob("*")) if (STATE / box).is_dir() else []:
            try:
                j = json.loads((d / "job.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                j = {"id": d.name}
            items.append({k: j.get(k) for k in ("id", "node", "repo", "enqueued", "priority", "state",
                                                 "reason", "state_since", "kind", "cmd_name")})
        res[box] = items
    return res


def ssh_live(host: str, node: str, timeout: float = 15) -> tuple[dict | None, float, str | None]:
    t = time.time()
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", host,
                            (REMOTE_COLLECT.format(node=node) + " " + REMOTE_EXTRA.get(node, "")).strip()],
                           capture_output=True, timeout=timeout,
                           **P.no_console())
    except subprocess.TimeoutExpired:
        return None, time.time() - t, "timeout"
    dur = time.time() - t
    out = (r.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    try:
        return json.loads(out[-1]), dur, None
    except (IndexError, ValueError):
        return None, dur, ((r.stderr or b"").decode("utf-8", "replace").strip()[-200:] or f"rc {r.returncode}")


def scp(src: str, dest: str, timeout: float = SHIP_TIMEOUT_S) -> str | None:
    """One bounded copy: a node that does not answer costs ConnectTimeout, never a hang."""
    try:
        r = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={min(6, int(timeout))}",
                            src, dest], capture_output=True, text=True, timeout=timeout, **P.no_console())
    except subprocess.TimeoutExpired:
        return f"scp timed out ({timeout:g} s)"
    return None if r.returncode == 0 else (r.stderr.strip()[-200:] or f"scp failed (rc {r.returncode})")


def ship_collector(host: str, timeout: float = SHIP_TIMEOUT_S) -> str | None:
    """The node runs the collector this repo has: copy it before the first cycle (the 5-minute
    LaunchAgent snapshot uses the same file, which stays backward compatible)."""
    return scp(str(HERE / "tools" / "visor" / "collect.py"), f"{host}:.enjambre/visor/collect.py", timeout)


def mark_down(node: str, reason: str, t: float | None = None, path: Path | None = None) -> None:
    """A node live could not reach says so in nodes.json at once, so nothing is routed to it
    while watch's next probe (<= 30 s) decides. `prior_level` lets watch debounce and notify as
    if this mark were one more observation of its own (presence.tick)."""
    path = path or STATE / "nodes.json"
    t = time.time() if t is None else t
    st = P.read_state(path)
    nodes = st.setdefault("nodes", {})
    old = nodes.get(node) or {}
    if old.get("down_by") == "live":
        prior, prior_since = old.get("prior_level"), old.get("prior_since")
    else:
        prior, prior_since = old.get("level"), old.get("since")
    since = old.get("since") if old.get("level") == "down" else P.now_iso(t)
    nodes[node] = dict(old, level="down", since=since, checked=P.now_iso(t), down_by="live",
                       down_reason=reason[:200], prior_level=prior, prior_since=prior_since)
    st["updated"] = P.now_iso(t)
    P.write_json_atomic(path, st)


class Shipper:
    """What live copies to each remote node at start (collect.py, jobrun.py). An item that does
    not go is retried from the loop with growing waits, and a node that does not answer is marked
    down in nodes.json; neither blocks the cycle (2026-10-15: an scp with no timeout held live
    3.5 min while the Mac was down). items: [(name, ship_fn(host) -> err | None,
    done_fn(node, host) -> log text)]."""

    def __init__(self, nodes: dict, items: list, log=print, mark=mark_down,
                 backoff: tuple = SHIP_BACKOFF_S):
        self.hosts = {n: cfg["host"] for n, cfg in nodes.items() if cfg.get("host") != "local"}
        self.todo = {n: list(items) for n in self.hosts}
        self.tries = dict.fromkeys(self.hosts, 0)
        self.next_t = dict.fromkeys(self.hosts, 0.0)
        self.log, self.mark, self.backoff = log, mark, backoff

    def pending(self) -> dict:
        return {n: [i[0] for i in it] for n, it in self.todo.items() if it}

    def tick(self, now: float, reachable=lambda node: True) -> None:
        for n, host in self.hosts.items():
            items = self.todo[n]
            if not items or now < self.next_t[n] or not reachable(n):
                continue
            err, down = None, False
            for item in list(items):
                name, ship_fn, done_fn = item
                e = ship_fn(host)
                if e is None:
                    items.remove(item)
                    self.log(f"node {n}: {done_fn(n, host)}")
                    continue
                err = f"{name}: {e}"
                if DOWN_ERR.search(e):   # the node, not the file: the rest would fail the same way
                    down = True
                    break
            if not items:
                self.tries[n] = 0
                continue
            wait = self.backoff[min(self.tries[n], len(self.backoff) - 1)]
            self.tries[n] += 1
            self.next_t[n] = now + wait
            self.log(f"node {n}: not shipped {[i[0] for i in items]} ({err}); retry in {wait:g} s"
                     + (", marked down" if down else ""))
            if down:
                self.mark(n, err, now)


def ring_path(node: str) -> Path:
    return OUT / f"ring-{node}.jsonl"


def backfill_ring(node: str, host: str) -> int:
    """At start: the node keeps its own ring (it measures even when this PC is off); bring it
    once so the sparklines are not empty after a restart."""
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", host,
                            REMOTE_RING.format(n=C.RING_MAX)], capture_output=True, timeout=20, **P.no_console())
    except subprocess.TimeoutExpired:
        return 0
    lines = [l for l in (r.stdout or b"").decode("utf-8", "replace").splitlines() if l.startswith("{")]
    if lines:
        tmp = ring_path(node).with_suffix(".tmp")
        tmp.write_text("\n".join(lines[-C.RING_MAX:]) + "\n", encoding="utf-8")
        os.replace(tmp, ring_path(node))
    return len(lines)


def read_ring(node: str, n: int = C.RING_MAX) -> list:
    try:
        lines = ring_path(node).read_text(encoding="utf-8").splitlines()[-n:]
    except OSError:
        return []
    out = []
    for l in lines:
        try:
            out.append(json.loads(l))
        except ValueError:
            pass
    return out


def write_js(path: Path, var: str, data) -> None:
    text = f"window.{var} = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)          # readers never see half a file


class Loop:
    """State across cycles: the PC's CPU counters, each node's last read and its slow flag."""

    def __init__(self, nodes: dict, ssh_fn=ssh_live, log=print, planner=None):
        self.nodes, self.ssh_fn, self.log, self.planner = nodes, ssh_fn, log, planner
        self.prev_local = None
        self.meta = {n: {} for n in nodes}
        self.last_payload = {n: None for n in nodes}

    def read_local(self, node: str, now: float) -> dict:
        tele, self.prev_local = C.collect_telemetry(self.prev_local, now=now)
        sessions, _ = C.collect_sessions(C.claude_dir(), now)
        try:
            C.ring_append(str(ring_path(node)), C.ring_line(tele))
        except OSError:
            pass
        return {"node": node, "at": now, "sessions": sessions, "detached": [], "tmux": [], "telemetria": tele,
                "errors": []}

    def cycle(self, now: float | None = None) -> dict:
        now = now or time.time()
        payloads = {}
        if self.planner:
            REMOTE_EXTRA.update(self.planner.extra_args())
        for n, cfg in self.nodes.items():
            m = self.meta[n]
            if cfg.get("host") == "local":
                t = time.time()
                payloads[n] = self.read_local(n, now)
                m.update(lat_ms=int((time.time() - t) * 1000), read_at=now, error=None)
                continue
            if m.get("slow") and now - (m.get("read_at") or 0) < SLOW_EVERY_S:
                payloads[n] = self.last_payload[n]       # keep showing the last read, marked slow
                continue
            m["via"] = cfg["host"] if cfg.get("host_fallback") else None   # prompt 21 §3: visor warning
            p, dur, err = self.ssh_fn(cfg["host"], n)
            m.update(lat_ms=int(dur * 1000), error=err, slow=dur > SLOW_SSH_S)
            if p is None:                               # one failed ssh must not empty the strip:
                payloads[n] = self.last_payload[n]      # show the last good read, aged by read_at
                continue
            if p is not None:
                m["read_at"] = now
                self.last_payload[n] = p
                if p.get("telemetria"):
                    m["tele_last"] = p["telemetria"]
                    try:
                        C.ring_append(str(ring_path(n)), C.ring_line(p["telemetria"]))
                    except OSError:
                        pass
            payloads[n] = p
        presence = P.read_state(STATE / "nodes.json") if (STATE / "nodes.json").exists() else {}
        live = assemble(self.nodes, payloads, read_metas(now), read_queue(), presence, now, self.meta)
        thr = C.load_thresholds()
        live["reposo"] = {n: reposo_rules(read_ring(n, 180), thr, now) for n in self.nodes}
        live["reposo_umbrales"] = {k: thr[k] for k in REPOSO_IDS if k in thr} if thr else None
        if self.planner:              # prompt 20: preempt, then show what it decided
            try:
                live["planificador"] = self.planner.tick(live, payloads, now)
            except Exception as e:    # the visor must keep updating even if the planner trips
                live["planificador"] = {"error": f"{type(e).__name__}: {e}"[:200]}
                self.log(f"planner error: {type(e).__name__}: {e}")
        count_externos(live)
        OUT.mkdir(parents=True, exist_ok=True)
        P.write_json_atomic(OUT / "live.json", live)
        write_js(OUT / "live.js", "LIVE", live)
        write_js(OUT / "ring.js", "LIVE_RING", {n: read_ring(n) for n in self.nodes})
        return live


def wait_or_kick(seconds: float, since: float) -> None:
    """Sleep up to `seconds`, waking early when `run --detach` touched the kick file."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            if KICK.stat().st_mtime > since:
                return
        except OSError:
            pass
        time.sleep(1)


def kick() -> None:
    try:
        OUT.mkdir(parents=True, exist_ok=True)
        KICK.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass
