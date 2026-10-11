"""planner — the scheduler's hands (prompt 20 step 3). Runs inside `enjambre live --serve`, one
tick per cycle: read what runs on every node (enjambre jobs, the render lab's renders, processes
the owner adopted), ask scheduler.plan() what to pause / evict / resume, do it over ssh, log it.

Only this process writes state/scheduler.json (what IT paused or evicted: the only things it will
ever resume) and state/scheduler.jsonl (one line per action). `enjambre adoptar` writes
state/adopted.json; watch only reads. Nothing it pauses may stay frozen: `release_all()` sends
CONT to everything it paused, and `live --stop` and every start of the loop call it.

The lab is asked FROM its node (ssh <host> curl http://127.0.0.1:<port>): no address of the Mac
is written anywhere, and it works with the VPN up as long as `ssh mac` does (NEEDS-OWNER §15).
"""
from __future__ import annotations

import base64
import hmac
import http.server
import json
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

import largos as L
import presence as P
import scheduler as S

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
STATE_FILE = STATE / "scheduler.json"
AUDIT = STATE / "scheduler.jsonl"
ADOPTED = STATE / "adopted.json"
PY = "/usr/bin/python3"
LAB_ID = re.compile(r"[A-Za-z0-9_-]{1,40}")
JOB_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,80}")
CTL_JS = STATE / "visor" / "ctl.js"
KEY = re.compile(r"(job:[a-z0-9][a-z0-9-]{0,80}|lab:[A-Za-z0-9_-]{1,40}|pid:[a-z0-9-]{1,30}:\d{1,7})")
MANUAL = ("pause", "resume", "evict", "priority", "adopt")


def ssh_run(host: str, cmd: str, timeout: float = 20) -> tuple:
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=6", host, cmd],
                           capture_output=True, timeout=timeout, **P.no_console())
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def read_adopted() -> list:
    return (P.read_state(ADOPTED) or {}).get("adopted") or []


def audit(entry: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with open(AUDIT, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class Planner:
    def __init__(self, nodes: dict, cfg: dict | None = None, run=ssh_run, resume_fn=None, log=print):
        self.nodes, self.cfg, self.run, self.log = nodes, cfg or S.load(), run, log
        self.resume_fn = resume_fn or L.resume
        self.state = P.read_state(STATE_FILE) or {}
        self.seen: dict = {}          # (node, pid) -> first time it looked heavy and unmanaged
        lab = (self.cfg.get("externos") or {}).get("render_lab") or {}
        self.lab_node = lab.get("nodo")
        self.lab_url = (lab.get("url") or "").replace("{host}", "127.0.0.1")
        self.lab_port = lab.get("puerto_proceso")
        self.lab_logs = lab.get("logs")
        self.lab_prio = lab.get("prioridad_por_defecto", "baja")
        self.recent: list = []
        self.last_jobs: list = []
        self.lock = threading.RLock()  # the loop's tick and the control endpoint share self.state

    # ---------------------------------------------------------------- what collect.py is asked
    def extra_args(self) -> dict:
        out = {}
        for n in self.nodes:
            parts = []
            if n == self.lab_node and self.lab_url:
                parts += ["--lab", self.lab_url] + (["--lab-port", str(int(self.lab_port))] if self.lab_port else [])
                if self.lab_logs and re.fullmatch(r"[~\w./-]{1,200}", self.lab_logs):   # goes into a remote shell
                    parts += ["--lab-logs", self.lab_logs]
            ad = [f"{int(a['pid'])}:{a.get('priority', 'baja')}" for a in read_adopted() if a.get("node") == n]
            if ad:
                parts += ["--adopted", ",".join(ad)]
            out[n] = " ".join(parts)
        return out

    # ---------------------------------------------------------------- what runs
    def jobs(self, live: dict, payloads: dict) -> list:
        det = {}
        for n, p in payloads.items():
            for d in (p or {}).get("detached") or []:
                det[d.get("id")] = d
        paused = dict(self.state.get("paused") or {}, **(self.state.get("manual") or {}))
        out = []
        for c in live.get("trabajos") or []:
            if c.get("tipo") != "trabajo" or not c.get("nodo"):
                continue
            d = det.get(c["id"]) or {}
            agent = (d.get("kind") or c.get("kind")) not in ("script",)
            out.append({"key": "job:" + c["id"], "id": c["id"], "node": c["nodo"], "source": "enjambre",
                        "priority": d.get("priority") or c.get("prioridad") or "normal",
                        "state": c.get("estado"), "agent": agent, "resumable": bool(d.get("resumable")),
                        "preemptable": (not agent) or bool(d.get("between_turns"))})
        for n, p in payloads.items():
            for j in (((p or {}).get("externos") or {}).get("render_lab") or []):
                key = "lab:" + str(j.get("id"))
                state = {"running": "running", "paused": "paused_by_priority" if key in paused else "paused"}.get(j.get("status"))
                if state:
                    out.append({"key": key, "id": str(j.get("id")), "node": n, "source": "render_lab",
                                "priority": self.lab_prio, "state": state, "agent": False,
                                "resumable": False, "preemptable": True, "preset": j.get("preset"),
                                "elapsed_s": j.get("elapsed_s"),
                                # 22 §2.2: no heartbeat of its own: the signal is the GET /jobs answer
                                "signal_ts": j.get("polled") or (p or {}).get("at"), "signal": "GET /jobs",
                                **{k: j.get(k) for k in ("pct", "frames", "frames_total", "eta_s")}})
        for a in read_adopted():
            key = f"pid:{a.get('node')}:{a.get('pid')}"
            out.append({"key": key, "id": str(a.get("pid")), "node": a.get("node"), "source": "adopted",
                        "priority": a.get("priority", "baja"), "agent": False, "resumable": False,
                        "preemptable": True, "name": a.get("name"),
                        # an adopted process has no beat either: its signal is the node's process poll
                        "signal_ts": (payloads.get(a.get("node")) or {}).get("at"), "signal": "lista de procesos",
                        "state": "paused_by_priority" if key in paused else "running"})
        over = self.state.get("prio") or {}
        for j in out:                 # `enjambre prioridad` / the visor's selector win over the job's own
            if j["key"] in over:
                j["priority"] = over[j["key"]]
        return out

    # ---------------------------------------------------------------- acting
    def execute(self, a: dict) -> tuple:
        host = (self.nodes.get(a["node"]) or {}).get("host")
        if not host or host == "local":
            return False, "nodo sin ssh (la pausa en Windows es manual: enjambre pausar en el PC)"
        if a["source"] == "enjambre":
            if not JOB_ID.fullmatch(a["id"]):
                return False, "id inválido"
            if a["action"] == "resume_evicted":
                res = self.resume_fn(a["id"])
                return bool(res.get("launched")), json.dumps(res, ensure_ascii=False)[:200]
            verb = {"pause": "pause", "resume": "resume", "evict": "evict"}[a["action"]]
            reason = base64.b64encode(a["reason"].encode("utf-8")).decode()
            rc, out, err = self.run(host, f"{PY} .enjambre/jobrun.py control {a['id']} {verb} {reason}")
            res = L.last_json(out)
            return bool(res.get("ok")), (res.get("error") or err.strip()[-160:] or "ok")
        if a["source"] == "render_lab":
            if not LAB_ID.fullmatch(a["id"]):
                return False, "id inválido"
            verb = {"pause": "pause", "resume": "resume"}.get(a["action"])
            if not verb:
                return False, "el lab no se desaloja (sin checkpoint propio): solo pausa"
            rc, out, err = self.run(host, f"curl -s -m 5 -o /dev/null -w '%{{http_code}}' -X POST "
                                          f"{self.lab_url}/jobs/{a['id']}/{verb}")
            return out.strip() == "200", f"HTTP {out.strip() or err.strip()[-80:]}"
        if a["source"] == "adopted":
            sig = {"pause": "STOP", "resume": "CONT"}.get(a["action"])
            if not sig or not a["id"].isdigit():
                return False, "un proceso adoptado solo se pausa"
            rc, out, err = self.run(host, f"{PY} .enjambre/jobrun.py sigtree {a['id']} {sig}")
            res = L.last_json(out)
            return bool(res.get("ok")), json.dumps(res)[:160]
        return False, "fuente desconocida"

    def apply(self, actions: list, new_state: dict, now: float, who: str = "planificador") -> list:
        done = []
        for a in actions:
            ok, msg = self.execute(a)
            entry = dict(a, at=L.now(), ok=ok, msg=msg, by=who)
            audit(entry)
            self.log(f"scheduler {a['action']} {a['key']} on {a['node']}: {'ok' if ok else 'FAILED'} {msg} | {a['reason']}")
            if not ok and a["action"] in ("pause", "evict"):   # not paused: do not claim it
                new_state.get("paused", {}).pop(a["key"], None)
                new_state.get("evicted", {}).pop(a["key"], None)
            done.append(entry)
        self.state = new_state
        P.write_json_atomic(STATE_FILE, new_state)
        self.recent = (done + self.recent)[:8]
        return done

    def release_all(self, live: dict | None = None, payloads: dict | None = None) -> list:
        """CONT everything this scheduler paused (never leave a process frozen). Evicted jobs
        stay evicted: they are stopped cleanly and `enjambre reanudar` / the next tick resumes them."""
        paused = (self.state or {}).get("paused") or {}
        actions = []
        for key, p in paused.items():
            src, _, rest = key.partition(":")
            src = {"job": "enjambre", "lab": "render_lab", "pid": "adopted"}.get(src, src)
            jid = rest.split(":")[-1]
            actions.append(dict(action="resume", key=key, id=jid, node=p.get("node"), source=src,
                                reason="liberado: el planificador se detiene o arranca"))
        st = dict(self.state, paused={})
        return self.apply(actions, st, time.time(), "release_all") if actions else []

    # ---------------------------------------------------------------- one tick
    def unmanaged(self, teles: dict, now: float) -> list:
        nc = self.cfg.get("no_gestionados") or {}
        cpu_min, rss_min, hold = nc.get("cpu_pct", 50), nc.get("rss_gb", 2), nc.get("sostenido_s", 120)
        out, alive = [], set()
        for node, t in teles.items():
            for r in (t or {}).get("top") or []:
                if r.get("tag") or not isinstance(r.get("pid"), int):
                    continue
                heavy = (r.get("cpu") or 0) > cpu_min or (r.get("rss_gb") or 0) > rss_min
                k = (node, r["pid"])
                if not heavy:
                    continue
                alive.add(k)
                first = self.seen.setdefault(k, now)
                if now - first >= hold:
                    out.append({"node": node, "pid": r["pid"], "name": r.get("name"), "cpu": r.get("cpu"),
                                "rss_gb": r.get("rss_gb"), "desde_s": int(now - first)})
        for k in list(self.seen):
            if k not in alive:
                self.seen.pop(k)
        return out

    def policy_line(self, node: str, jobs: list, now: float) -> str:
        mine = [j for j in jobs if j["node"] == node]
        cnt = {}
        for j in mine:
            if j.get("state") in ("running", "launching"):
                cnt[j["priority"] + " corriendo"] = cnt.get(j["priority"] + " corriendo", 0) + 1
            elif j.get("state") == "paused_by_priority":
                cnt[j["priority"] + " pausados"] = cnt.get(j["priority"] + " pausados", 0) + 1
        parts = [f"{v} {k}" for k, v in sorted(cnt.items(), key=lambda kv: S.rank(kv[0].split()[0]))]
        parts.append(S.window(self.cfg, now)[1].replace("esperando la ventana nocturna: ", "ventana nocturna "))
        fs = (self.state.get("free_since") or {}).get(node)
        if fs:
            parts.append(f"libre hace {int(now - fs)} s (reanuda a los {(self.cfg.get('preempcion') or {}).get('reanudar_tras_s_libre', 60)} s)")
        return " · ".join(parts)

    def tick(self, live: dict, payloads: dict, now: float) -> dict:
        jobs = self.jobs(live, payloads)
        teles = live.get("telemetria") or {}
        oses = {n: c.get("os", "") for n, c in self.nodes.items()}
        with self.lock:
            actions, new_state = S.plan(jobs, teles, oses, self.state, now, self.cfg)
            alive = {j["key"] for j in jobs}
            for k in ("manual", "prio"):  # plan() owns paused/evicted/free_since; these are the owner's
                kept = {key: v for key, v in (self.state.get(k) or {}).items() if key in alive}
                if kept:
                    new_state[k] = kept
            self.last_jobs = jobs
            if actions or new_state != self.state:
                self.apply(actions, new_state, now)
        paused = dict(self.state.get("paused") or {}, **(self.state.get("manual") or {}))
        evicted = self.state.get("evicted") or {}
        ext = []
        for j in jobs:
            if j["source"] == "enjambre":
                continue
            reg = paused.get(j["key"]) or {}
            ext.append({"id": j["key"], "tipo": "externo", "fuente": j["source"], "nodo": j["node"],
                        "proyecto": "lab-video (lab)" if j["source"] == "render_lab" else (j.get("name") or "adoptado"),
                        "estado": j["state"], "prioridad": j["priority"], "preset": j.get("preset"),
                        "elapsed_s": j.get("elapsed_s"), "last_activity": j.get("signal_ts"),
                        "senal": j.get("signal"), "pct": j.get("pct"), "frames": j.get("frames"),
                        "frames_total": j.get("frames_total"), "eta_s": j.get("eta_s"),
                        "motivo": reg.get("reason") or ("pausado a mano en el lab" if j["state"] == "paused" else None)})
        for c in live.get("trabajos") or []:      # the reason this scheduler gave, until the node echoes it
            reg = paused.get("job:" + str(c.get("id"))) or evicted.get("job:" + str(c.get("id")))
            if reg and not c.get("motivo"):
                c["motivo"] = reg.get("reason")
        return {"politica": {n: self.policy_line(n, jobs, now) for n in self.nodes},
                "gestionados": [{k: j.get(k) for k in ("key", "id", "node", "source", "priority", "state",
                                                       "resumable", "preemptable", "agent")} for j in jobs],
                "externos": ext, "no_gestionados": self.unmanaged(teles, now),
                "acciones": [{k: a.get(k) for k in ("at", "action", "id", "node", "ok", "reason")} for a in self.recent],
                "preemptores": (self.cfg.get("preempcion") or {}).get("preemptores", ["alta"])}

    # ---------------------------------------------------------------- the owner's hand
    def manual(self, action: str, key: str, priority: str | None = None, who: str = "manual",
               jobs: list | None = None, name: str | None = None) -> dict:
        """pause / resume / evict / priority on a job key, or adopt a pid (key pid:<node>:<pid>).
        A manual pause is NOT resumed by the scheduler; a manual resume clears every registry."""
        if action not in MANUAL or not KEY.fullmatch(key or ""):
            return {"ok": False, "error": "acción o clave no permitida"}
        if priority is not None and priority not in S.PRIOS:
            return {"ok": False, "error": f"prioridad desconocida: {priority}"}
        with self.lock:
            st = json.loads(json.dumps(self.state or {}))
            if action == "adopt":
                _, node, pid = key.split(":")
                if node not in self.nodes:
                    return {"ok": False, "error": f"nodo desconocido: {node}"}
                ad = [a for a in read_adopted() if not (a.get("node") == node and str(a.get("pid")) == pid)]
                ad.append({"node": node, "pid": int(pid), "priority": priority or "baja", "name": name, "at": L.now()})
                P.write_json_atomic(ADOPTED, {"adopted": ad})
                audit({"at": L.now(), "action": "adopt", "key": key, "node": node, "priority": priority or "baja",
                       "ok": True, "by": who, "reason": "adoptado a mano"})
                return {"ok": True, "msg": f"{key} adoptado como {priority or 'baja'}"}
            j = next((x for x in (jobs if jobs is not None else self.last_jobs) if x["key"] == key), None)
            if j is None:
                return {"ok": False, "error": f"{key} no está corriendo ni en cola"}
            if action == "priority":
                if not priority:
                    return {"ok": False, "error": "falta la prioridad"}
                st.setdefault("prio", {})[key] = priority
                q = STATE / "queue" / j["id"] / "job.json"
                if j["source"] == "enjambre" and q.exists():       # still queued: admission reads it
                    meta = P.read_state(q) or {}
                    meta["priority"] = priority
                    P.write_json_atomic(q, meta)
                why = f"prioridad {j['priority']} → {priority}"
                audit({"at": L.now(), "action": "priority", "key": key, "node": j["node"], "priority": priority,
                       "ok": True, "by": who, "reason": why})
                self.state = st
                P.write_json_atomic(STATE_FILE, st)
                return {"ok": True, "msg": why}
            if action == "evict" and not (j.get("resumable") and j["source"] == "enjambre"):
                return {"ok": False, "error": "sin checkpoint: solo se pausa (nunca se desaloja lo que no se puede reanudar)"}
            act = "resume_evicted" if action == "resume" and j.get("state") == "evicted_resumable" else action
            why = {"pause": "pausado a mano", "resume": "reanudado a mano", "evict": "desalojado a mano"}[action]
            for reg in ("paused", "evicted", "manual"):
                st.setdefault(reg, {}).pop(key, None)
            if action in ("pause", "evict"):        # the owner's: the scheduler never resumes these
                st["manual"][key] = {"by": who, "since": time.time(), "reason": why, "node": j["node"],
                                     "source": j["source"]}
            done = self.apply([dict(action=act, key=key, id=j["id"], node=j["node"], source=j["source"],
                                    reason=why)], st, time.time(), who)[0]
            if not done["ok"] and action in ("pause", "evict"):
                self.state["manual"].pop(key, None)
                P.write_json_atomic(STATE_FILE, self.state)
            return {"ok": done["ok"], "msg": done["msg"]}


def write_ctl(port: int, token: str, path: Path | None = None) -> None:
    """The control endpoint's port and token, for the LOCAL visor only. Never in live.json and
    never uploaded: web.py publishes its own whitelisted snapshot and does not read this file."""
    path = path or CTL_JS
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("window.LIVE_CTL = " + json.dumps({"port": port, "token": token}) + ";\n", encoding="utf-8")
    tmp.replace(path)


def read_ctl(path: Path | None = None) -> dict | None:
    try:
        text = (path or CTL_JS).read_text(encoding="utf-8")
        return json.loads(text.split("=", 1)[1].strip().rstrip(";"))
    except (OSError, IndexError, ValueError):
        return None


class Controls(http.server.ThreadingHTTPServer):
    """POST http://127.0.0.1:<port>/ctl with a text/plain JSON body {token, action, key, priority?}.
    text/plain keeps it a "simple" request: no CORS preflight from the file:// visor."""
    daemon_threads = True

    def __init__(self, planner: "Planner", port: int = 0, token: str | None = None):
        self.planner, self.token = planner, token or secrets.token_urlsafe(24)
        super().__init__(("127.0.0.1", port), CtlHandler)
        self.port = self.server_address[1]


class CtlHandler(http.server.BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "null")   # the visor is a file:// page
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        srv = self.server
        if self.path != "/ctl" or self.headers.get("Host") not in (f"127.0.0.1:{srv.port}", f"localhost:{srv.port}"):
            return self._send(403, {"ok": False, "error": "host"})       # DNS rebinding: loopback names only
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 4096)
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send(400, {"ok": False, "error": "json"})
        if not isinstance(body, dict) or not hmac.compare_digest(str(body.get("token") or ""), srv.token):
            return self._send(403, {"ok": False, "error": "token"})
        res = srv.planner.manual(str(body.get("action")), str(body.get("key")), body.get("priority"),
                                 who="cli" if body.get("by") == "cli" else "visor", name=body.get("name") if isinstance(body.get("name"), str) else None)
        self._send(200 if res.get("ok") else 409, res)

    def log_message(self, *a):
        pass


def serve_controls(planner: Planner, port: int = 0, log=print) -> "Controls | None":
    try:
        srv = Controls(planner, port)
    except OSError as e:
        log(f"controls: port {port} busy ({e}); the visor falls back to copy-the-command")
        return None
    write_ctl(srv.port, srv.token)
    threading.Thread(target=srv.serve_forever, name="controls", daemon=True).start()
    log(f"controls on 127.0.0.1:{srv.port}")
    return srv
