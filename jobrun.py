#!/usr/bin/env python3
"""jobrun — detached long jobs ON a node (the Mac). Stdlib only, Python 3.9-safe (/usr/bin/python3).

    jobrun.py launch <id>        spec.json already in ~/.enjambre/jobs/<id>/: start the supervisor
                                 in a tmux window and return at once (the caller may hang up)
    jobrun.py supervise <id>     (inside tmux) run the job, beat, finish; never call it by hand
    jobrun.py status <id>...     one JSON line per id: status.json + "alive" checked here
    jobrun.py resume <id>        relaunch a dead job: same worktree, `claude --resume <session>`
                                 for an agent, the same command again for a script
    jobrun.py kill <id>          stop it: TERM to the child's process group, then KILL
    jobrun.py control <id> pause|resume|evict [b64 reason]
                                 the PC's scheduler (prompt 20); the supervisor applies it
    jobrun.py cont <id>          SIGCONT to the group of a paused job whose supervisor died
    jobrun.py sigtree <pid> STOP|CONT   a process the owner adopted, and its children

spec.json (written by enjambre.py on Windows):
    {"id", "kind": "script"|"claude"|"tests", "repo", "cmd" (script: one shell line, already
     resolved from the repo's AGENTS.md), "task" (agent), "allowed" (agent --allowedTools),
     "max_turns", "budget_usd", "model", "beat_s", "beat_log_s", "prices" (USD per Mtok)}

Files in ~/.enjambre/jobs/<id>/: spec.json, status.json (rewritten every beat), log (the
script's stdout+stderr, or the agent's stream-json), latidos.jsonl (one line every beat_log_s),
supervisor.log, segments.jsonl (one line per agent segment: first run + each resume).

WHY a supervisor between tmux and the job: it is the only process that outlives the job by a
few seconds, so it can write the final status, clear busy.json and commit the branch even when
the job crashes. WHY the job gets its own session (start_new_session): every process it starts
inherits that process group, so killpg reaches the whole tree and never orphans a child holding
a model in memory (the 2026-10-05 kernel panic: `p.kill()` on a wrapper, DECISIONES 133).
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time

HOME = os.path.expanduser("~")
JOBS = os.path.join(HOME, ".enjambre", "jobs")
BUSY = os.path.join(HOME, ".enjambre", "busy.json")
PORTFOLIO = os.path.join(HOME, "Documents", "Portfolio")
TMUX = ["tmux", "-L", "enjambre"]
# launchd gives the tmux server a bare PATH; the job needs Homebrew (uv, gh) and ~/.local (claude)
PATH = ":".join([os.path.join(HOME, ".local", "bin"), "/opt/homebrew/bin", "/usr/local/bin",
                 "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
FINAL = ("ok", "failed", "incomplete", "killed")
PCT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s?%")


def now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def jdir(jid: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", jid):
        raise SystemExit(f"jobrun: bad job id {jid!r}")
    return os.path.join(JOBS, jid)


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path: str, data) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def append_jsonl(path: str, data) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


# ---------- busy.json: one marker per node, never steal a live one -------------------------

def busy_owner() -> int | None:
    m = read_json(BUSY, {}) or {}
    try:
        return int(m.get("pid"))
    except (TypeError, ValueError):
        return None


def busy_write(jid: str, kind: str, since: str, extra: dict) -> bool:
    """Write the marker if it is free, stale, or already ours. Returns True when it is ours.
    WHY pid = the supervisor: it lives exactly as long as the job; busy.py's sed reads "pid": N."""
    owner = busy_owner()
    me = os.getpid()
    if owner and owner != me and pid_alive(owner):
        return False
    data = {"quien": jid, "desde": since, "tipo": kind, "pid": me}
    data.update({k: v for k, v in extra.items() if v is not None})
    write_json(BUSY, data)
    return True


def busy_release() -> None:
    if busy_owner() == os.getpid():
        try:
            os.remove(BUSY)
        except OSError:
            pass


# ---------- progress readers ----------------------------------------------------------------

def last_pct(path: str) -> float | None:
    """Last 'NN%' the script printed (tail of the log only: a 2 h log is never read whole)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    m = PCT.findall(tail)
    if not m:
        return None
    v = float(m[-1].replace(",", "."))
    return v if 0 <= v <= 100 else None


def last_line(path: str) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 2048))
            lines = [l for l in f.read().decode("utf-8", "replace").splitlines() if l.strip()]
    except OSError:
        return ""
    return lines[-1][-200:] if lines else ""


class StreamState:
    """Reads claude's stream-json log incrementally: session id, turns, last tool, usage, and
    the final result event. Cost before the end is an ESTIMATE (usage x prices from spec): the
    CLI only reports total_cost_usd in the final result event."""

    KEYS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

    def __init__(self, path: str, prices: dict | None):
        self.path, self.prices, self.pos = path, prices or {}, 0
        self.session_id = None
        self.last_tool = None
        # WHY per message id: stream-json emits one `assistant` event per content block (text,
        # tool_use...) and each repeats the whole message's usage. Summing events counted 117
        # "turns" and US$2.66 for 54 real messages (E3 fire test b, first try).
        self.msgs: dict = {}
        self.result = None
        self._buf = b""
        # prompt 20: "tool_use" = the model answered and its tools are running, so no API call is
        # in flight: the only moment an agent may be paused (a frozen HTTP call times out and
        # loses the turn). "tool_result" = the next API call is about to start or is in flight.
        self.last_kind = None

    @property
    def turns(self) -> int:
        return len(self.msgs)

    @property
    def usage(self) -> dict:
        return {k: sum(int(u.get(k) or 0) for u in self.msgs.values()) for k in self.KEYS}

    def poll(self) -> None:
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                chunk = f.read()
        except OSError:
            return
        self.pos += len(chunk)
        data = self._buf + chunk
        lines = data.split(b"\n")
        self._buf = lines.pop()  # an incomplete last line waits for the next poll
        for raw in lines:
            try:
                ev = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(ev, dict):
                self.feed(ev)

    def feed(self, ev: dict) -> None:
        t = ev.get("type")
        if ev.get("session_id"):
            self.session_id = ev["session_id"]
        if t == "assistant":
            msg = ev.get("message") or {}
            mid = msg.get("id") or f"anon-{len(self.msgs)}"
            self.msgs[mid] = msg.get("usage") or {}  # same id again: same usage, overwrite
            kinds = [c.get("type") for c in msg.get("content") or [] if isinstance(c, dict)]
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    self.last_tool = c.get("name")
            self.last_kind = "tool_use" if "tool_use" in kinds else "text"
        elif t == "user":
            self.last_kind = "tool_result"
        elif t == "result":
            self.result = ev

    def est_cost(self) -> float | None:
        p = self.prices
        if not p:
            return None
        u = self.usage
        return round((u["input_tokens"] * p.get("input", 0) + u["output_tokens"] * p.get("output", 0)
                      + u["cache_creation_input_tokens"] * p.get("cache_write", 0)
                      + u["cache_read_input_tokens"] * p.get("cache_read", 0)) / 1e6, 4)


# ---------- the supervisor --------------------------------------------------------------------

def git(args: list, cwd: str, log) -> int:
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        log(f"git {' '.join(args)} -> {r.returncode}: {r.stderr.strip()[-300:]}")
    return r.returncode


def ensure_worktree(spec: dict, st: dict, log) -> str | None:
    """Agent jobs: one worktree + branch per job id, created once, reused by every resume."""
    wt = os.path.join(PORTFOLIO, "wt", f"{spec['repo']}-{spec['id']}")
    if os.path.isdir(wt):
        return wt
    src = os.path.join(PORTFOLIO, spec["repo"])
    fetched = git(["fetch", "-q", "origin"], src, log) == 0
    base = "HEAD"
    if fetched and subprocess.run(["git", "rev-parse", "-q", "--verify", "@{u}"], cwd=src,
                                  capture_output=True).returncode == 0:
        base = "@{u}"
    st["fetched"] = fetched
    if git(["worktree", "add", "-q", wt, "-b", f"mac/{spec['id']}", base], src, log):
        return None
    # Same as worker.sh: the clone's gitignored venv, linked, excluded locally (never committed).
    venv = os.path.join(src, ".venv")
    if os.path.isdir(venv) and not os.path.lexists(os.path.join(wt, ".venv")):
        os.symlink(venv, os.path.join(wt, ".venv"))
        r = subprocess.run(["git", "rev-parse", "--git-common-dir"], cwd=src, capture_output=True, text=True)
        ex = os.path.join(src, r.stdout.strip(), "info", "exclude")
        try:
            have = open(ex, encoding="utf-8").read().splitlines()
        except OSError:
            have = []
        if "/.venv" not in have:
            os.makedirs(os.path.dirname(ex), exist_ok=True)
            with open(ex, "a", encoding="utf-8") as f:
                f.write("/.venv\n")
    return wt


def script_cmd(spec: dict, line: str) -> list:
    """caffeinate -i: no idle sleep while it runs; -s: on AC power. Dies with the job. prompt 20:
    baja/nocturna run under `taskpolicy -b nice -n N` (prefix from the PC's scheduler.toml),
    inside caffeinate so the whole tree inherits the low priority."""
    return ["caffeinate", "-is"] + list(spec.get("prio_prefix") or []) + ["/bin/bash", "-c", line]


def agent_cmd(spec: dict, st: dict, budget_left: float | None) -> list:
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
           "--max-turns", str(int(spec.get("max_turns") or 200)),
           "--allowedTools", spec.get("allowed") or "Read,Edit,Write,Glob,Grep"]
    if spec.get("model"):
        cmd += ["--model", spec["model"]]
    if budget_left is not None:
        cmd += ["--max-budget-usd", f"{max(budget_left, 0.01):.2f}"]
    if st.get("session_id"):
        cmd += ["--resume", st["session_id"]]
    return cmd


def spent_so_far(d: str) -> float:
    """Closed segments report total_cost_usd; a killed segment only has the estimate."""
    total = 0.0
    try:
        with open(os.path.join(d, "segments.jsonl"), encoding="utf-8") as f:
            for line in f:
                s = json.loads(line)
                total += float(s.get("cost_usd") if s.get("cost_usd") is not None
                               else (s.get("cost_est_usd") or 0))
    except (OSError, ValueError):
        pass
    return round(total, 4)


def classify_agent(res: dict | None, rc: int, budget: float | None, spent: float) -> tuple:
    """(state, reason) from the final result event. reason is a separate field: status stays
    one of ok/failed/incomplete, the values the visor and the bandeja compare against."""
    if res is None:
        return ("failed", "no result event (rc %s)" % rc)
    sub = str(res.get("subtype") or "")
    if "budget" in sub or (budget is not None and spent >= budget):
        return ("incomplete", "budget")
    if "max_turns" in sub:
        return ("incomplete", "max_turns")
    if res.get("permission_denials"):
        return ("incomplete", "permission_denials")
    if res.get("is_error") or rc:
        return ("failed", sub or f"rc {rc}")
    return ("ok", None)


def supervise(jid: str) -> int:
    d = jdir(jid)
    spec = read_json(os.path.join(d, "spec.json"))
    if not spec:
        raise SystemExit(f"jobrun: no spec for {jid}")
    st = read_json(os.path.join(d, "status.json"), {}) or {}
    logf = open(os.path.join(d, "supervisor.log"), "a", encoding="utf-8")

    def log(msg: str) -> None:
        logf.write(f"{now()} {msg}\n")
        logf.flush()

    kind = spec["kind"]
    st.update(priority=spec.get("priority") or "normal", resumable=bool(spec.get("resumable")),
              between_turns=bool(spec.get("between_turns")), prio_prefix=spec.get("prio_prefix") or [])
    st.update(id=jid, kind=kind, repo=spec["repo"], state="running", supervisor_pid=os.getpid(),
              attempt=int(st.get("attempt") or 0) + 1, segment_start=now())
    st.setdefault("start", st["segment_start"])
    for k in ("rc", "end", "reason"):
        st.pop(k, None)
    out = os.path.join(d, "log")
    env = dict(os.environ, PATH=PATH, GIT_TERMINAL_PROMPT="0", ENJAMBRE_JOB_ID=jid,
               ENJAMBRE_ATTEMPT=str(st["attempt"]))
    envfile = os.path.join(HOME, ".config", "enjambre", "env")  # owner's long-lived token, if any

    budget = spec.get("budget_usd")
    stream = None
    if kind == "script":
        cwd = os.path.join(PORTFOLIO, spec["repo"])
        line = spec["cmd"] + "".join(" " + shlex.quote(a) for a in spec.get("args") or [])
        # caffeinate -i: no idle sleep while it runs; -s: on AC power. Dies with the job.
        cmd = script_cmd(spec, line)
        stdin = subprocess.DEVNULL
        mode = "a"  # a resumed script appends to the same log
    else:
        cwd = ensure_worktree(spec, st, log)
        if not cwd:
            st.update(state="failed", reason="worktree add failed", end=now(), rc=2)
            write_json(os.path.join(d, "status.json"), st)
            return 2
        st["worktree"], st["branch"] = cwd, f"mac/{jid}"
        spent = spent_so_far(d)
        left = None if budget is None else round(float(budget) - spent, 2)
        if left is not None and left <= 0:
            st.update(state="incomplete", reason="budget", end=now(), rc=0, cost_usd=spent)
            write_json(os.path.join(d, "status.json"), st)
            return 0
        acmd = list(spec.get("prio_prefix") or []) + agent_cmd(spec, st, left)
        if os.path.isfile(envfile):
            cmd = ["/bin/bash", "-c", f". {shlex.quote(envfile)}; exec \"$@\"", "_"] + acmd
        else:
            cmd = acmd
        # WHY stdin and never argv: a TASK that starts with `---` (frontmatter) is read by the
        # CLI as an option (0j). The resume prompt goes the same way.
        prompt = spec.get("task", "") if not st.get("session_id") else (
            "La sesión anterior se interrumpió (proceso muerto). Continúa la tarea exactamente "
            "donde la dejaste; no repitas lo ya hecho.")
        with open(os.path.join(d, "prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt)
        stdin = open(os.path.join(d, "prompt.txt"), "rb")
        mode = "w"
        out = os.path.join(d, f"log.{st['attempt']}")  # one stream per segment
        st["stream"] = os.path.basename(out)
        stream = StreamState(out, spec.get("prices"))

    st["cmd"] = cmd if kind == "script" else cmd[:3] + ["…"]
    write_json(os.path.join(d, "status.json"), st)
    log(f"attempt {st['attempt']} start: {' '.join(shlex.quote(c) for c in cmd)[:400]}")
    with open(out, mode + "b") as o:
        if kind == "script" and st["attempt"] > 1:
            o.write(f"\n=== enjambre resume: attempt {st['attempt']} {now()} ===\n".encode())
            o.flush()
        child = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=stdin, stdout=o,
                                 stderr=subprocess.STDOUT, start_new_session=True)
    st["child_pid"] = st["pgid"] = child.pid
    write_json(os.path.join(d, "status.json"), st)

    beat_s = float(spec.get("beat_s") or 60)
    beat_log_s = float(spec.get("beat_log_s") or 300)
    stop = threading.Event()
    over_budget = {"hit": False}

    def beat(final: bool = False) -> dict:
        b = {"ts": now(), "state": st["state"], "attempt": st["attempt"],
             "elapsed_s": int(time.time() - t0)}
        if stream:
            stream.poll()
            st["session_id"] = stream.session_id or st.get("session_id")
            b.update(turns=stream.turns, last_tool=stream.last_tool, cost_est_usd=stream.est_cost())
            # Backstop for --max-budget-usd: the estimate crossed the cap -> stop the group.
            if (budget is not None and b["cost_est_usd"] is not None and not over_budget["hit"]
                    and spent_before + b["cost_est_usd"] >= float(budget) * 1.15):
                over_budget["hit"] = True
                log("estimate crossed budget x1.15: killing the process group")
                killpg(child.pid)
        else:
            b.update(pct=last_pct(out), last=last_line(out))
        st["latido"] = b
        write_json(os.path.join(d, "status.json"), st)
        if final:
            return b
        mine = busy_write(jid, kind, st["start"],
                          {"latido": b["ts"], "pct": b.get("pct"), "turnos": b.get("turns"),
                           "coste_est_usd": b.get("cost_est_usd"), "intento": st["attempt"]})
        st["busy_marker"] = mine
        return b

    def beater() -> None:
        last_log = 0.0
        while not stop.wait(beat_s):
            b = beat()
            if time.time() - last_log >= beat_log_s:
                append_jsonl(os.path.join(d, "latidos.jsonl"), b)
                last_log = time.time()

    t0 = time.time()
    spent_before = spent_so_far(d) if stream else 0.0
    b0 = beat()
    append_jsonl(os.path.join(d, "latidos.jsonl"), b0)
    th = threading.Thread(target=beater, daemon=True)
    th.start()
    # ---- prompt 20: the scheduler's hand on this job. The PC writes control.json (jobrun.py
    # control <id> pause|resume|evict); this thread applies it, so status.json has one writer.
    ctl_path = os.path.join(d, "control.json")
    evicting = {"yes": False}

    def set_state(new: str, reason: str) -> None:
        st["state"], st["sched_reason"], st["sched_since"] = new, reason, now()
        st.setdefault("preemptions", []).append({"state": new, "reason": reason, "at": st["sched_since"]})
        write_json(os.path.join(d, "status.json"), st)
        log(f"scheduler: {new} ({reason})")

    def controller() -> None:
        pending, since = None, 0.0
        while not stop.wait(1.0):
            c = read_json(ctl_path)
            if c:
                try:
                    os.remove(ctl_path)
                except OSError:
                    pass
                pending, since = c, time.time()
            if not pending:
                continue
            act, reason = pending.get("action"), str(pending.get("reason") or "")[:300]
            if act == "pause" and st["state"] == "running":
                if stream is not None:
                    if not spec.get("between_turns"):
                        log("scheduler: pause refused, agent without preempt_between_turns")
                        pending = None
                        continue
                    stream.poll()
                    if stream.last_kind != "tool_use":   # wait for the turn boundary (<= 10 min)
                        if time.time() - since > 600:
                            log("scheduler: pause dropped, no turn boundary in 10 min")
                            pending = None
                        continue
                stopped = stop_group(child.pid)
                set_state("paused_by_priority", reason + ("" if stopped else " (algún proceso no se detuvo)"))
            elif act == "resume" and st["state"] == "paused_by_priority":
                try:
                    os.killpg(child.pid, signal.SIGCONT)
                except OSError:
                    pass
                set_state("running", reason or "reanudado")
            elif act == "evict" and st["state"] in ("running", "paused_by_priority"):
                if not spec.get("resumable"):
                    log("scheduler: evict refused, job has no checkpoint (resumable: false)")
                else:
                    evicting["yes"] = True
                    st["sched_reason"] = reason
                    log(f"scheduler: evicting ({reason})")
                    killpg(child.pid)          # CONT + TERM: the script writes its checkpoint
            pending = None

    ctl = threading.Thread(target=controller, daemon=True)
    ctl.start()

    def on_term(signum, _frame):  # tmux kill-window / kill <supervisor>: take the group down too
        log(f"signal {signum}: killing the job's process group")
        killpg(child.pid)

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, on_term)
    rc = child.wait()
    stop.set()
    th.join(5)
    ctl.join(3)
    killpg(child.pid, grace=2)  # anything the job left behind in its group

    st["rc"], st["end"] = rc, now()
    if stream:
        stream.poll()
        res = stream.result
        # The CLI's own count wins when there is a result event (num_turns, total_cost_usd).
        seg_turns = (res or {}).get("num_turns") or stream.turns
        seg = {"attempt": st["attempt"], "session_id": stream.session_id, "turns": seg_turns,
               "messages": stream.turns,
               "cost_usd": (res or {}).get("total_cost_usd"), "cost_est_usd": stream.est_cost(),
               "subtype": (res or {}).get("subtype"), "rc": rc, "end": st["end"]}
        append_jsonl(os.path.join(d, "segments.jsonl"), seg)
        st["cost_usd"] = spent_so_far(d)
        st["turns"] = int(st.get("turns_before") or 0) + seg_turns
        st["turns_before"] = st["turns"]
        st["session_id"] = stream.session_id or st.get("session_id")
        state, reason = classify_agent(res, rc, budget, st["cost_usd"])
        if over_budget["hit"]:
            state, reason = "incomplete", "budget"
        st["state"], st["reason"] = state, reason
        st["result"] = str((res or {}).get("result") or "")[:500]
        st["permission_denials"] = [
            {"tool": x.get("tool_name"), "target": str((x.get("tool_input") or {}).get("command")
                                                       or (x.get("tool_input") or {}).get("file_path") or "")[:200]}
            for x in ((res or {}).get("permission_denials") or []) if isinstance(x, dict)]
        st["usage"] = (res or {}).get("usage") or stream.usage
        commit(cwd, jid, st, log)
    else:
        st["state"] = "ok" if rc == 0 else ("killed" if rc < 0 else "failed")
        st["pct"] = last_pct(out)
    if evicting["yes"]:          # stopped on purpose, checkpoint written: `resume` continues it
        st["state"] = "evicted_resumable"
        st.setdefault("preemptions", []).append({"state": "evicted_resumable",
                                                 "reason": st.get("sched_reason"), "at": st["end"]})
    bl = beat(final=True)
    append_jsonl(os.path.join(d, "latidos.jsonl"), bl)
    write_json(os.path.join(d, "status.json"), st)
    busy_release()
    log(f"attempt {st['attempt']} end: state={st['state']} rc={rc} reason={st.get('reason')}")
    return 0


def commit(wt: str, jid: str, st: dict, log) -> None:
    """Agent jobs: the branch is the result. Never pushed from here (enjambre.py decides)."""
    res = {k: st.get(k) for k in ("state", "reason", "rc", "turns", "cost_usd", "session_id",
                                  "start", "end", "attempt")}
    with open(os.path.join(wt, "result.json"), "w", encoding="utf-8") as f:
        json.dump(dict(res, task_id=jid), f, ensure_ascii=False)
        f.write("\n")
    git(["add", "-A"], wt, log)
    git(["add", "-f", "result.json"], wt, log)
    git(["-c", "user.name=enjambre worker", "-c", "user.email=enjambre@localhost", "commit", "-q",
         "-m", f"enjambre {jid}: state={st['state']} attempt={st['attempt']}"], wt, log)


def stop_group(pgid: int) -> bool:
    """SIGSTOP the job's group and check every process in it is stopped (`T`). A STOP that lands
    while a process is exec'ing can be lost (the lab's own code hit it): send it once more."""
    for _ in range(2):
        try:
            os.killpg(pgid, signal.SIGSTOP)
        except OSError:
            return False
        time.sleep(0.3)
        try:
            r = subprocess.run(["ps", "-o", "stat=", "-g", str(pgid)], capture_output=True, text=True, timeout=3)
            states = [x.strip() for x in r.stdout.splitlines() if x.strip()]
        except (OSError, subprocess.SubprocessError):
            states = []
        if states and all(x.startswith("T") for x in states):
            return True
    return False


def descendants(pid: int) -> list:
    """pid and every process below it (ps -A -o pid=,ppid=)."""
    try:
        r = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return [pid]
    kids: dict = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        if p not in out:
            out.append(p)
            todo.extend(kids.get(p, []))
    return out


def group_alive(pgid) -> bool:
    try:
        os.killpg(int(pgid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def killpg(pgid: int, grace: float = 10) -> None:
    """TERM the whole group, wait, KILL what is left. Never the single pid of a wrapper.
    CONT first: a stopped (paused_by_priority) process does not handle TERM, so its checkpoint
    would never be written and only the KILL would land (prompt 20)."""
    try:
        os.killpg(pgid, signal.SIGCONT)
        os.killpg(pgid, signal.SIGTERM)
    except OSError:
        return
    end = time.time() + grace
    while time.time() < end:
        try:
            os.killpg(pgid, 0)
        except OSError:
            return
        time.sleep(0.5)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except OSError:
        pass


# ---------- verbs called over ssh -------------------------------------------------------------

def status(jid: str) -> dict:
    d = jdir(jid)
    st = read_json(os.path.join(d, "status.json"))
    if st is None:
        return {"id": jid, "state": "unknown", "alive": False,
                "launched": os.path.isfile(os.path.join(d, "spec.json"))}
    live_states = ("running", "paused_by_priority")
    alive = pid_alive(st.get("supervisor_pid")) and st.get("state") in live_states
    st["alive"] = alive
    if st.get("state") in live_states and not alive:
        # The supervisor is gone without writing an end: reboot, kill -9, panic. Resumable.
        st["state"] = "dead"
        st["orphan_group"] = group_alive(st.get("pgid"))
    try:
        with open(os.path.join(d, "latidos.jsonl"), encoding="utf-8") as f:
            st["latidos_n"] = sum(1 for _ in f)
    except OSError:
        st["latidos_n"] = 0
    log = os.path.join(d, st.get("stream") or "log")
    st["log_name"] = os.path.basename(log)
    st["log_size"] = os.path.getsize(log) if os.path.isfile(log) else 0
    return st


def launch(jid: str, resume: bool = False) -> dict:
    d = jdir(jid)
    if not os.path.isfile(os.path.join(d, "spec.json")):
        return {"id": jid, "launched": False, "error": "no spec.json"}
    cur = status(jid)
    if cur.get("alive"):
        return {"id": jid, "launched": False, "error": "already running"}
    if cur.get("state") not in ("unknown",) and not resume:
        return {"id": jid, "launched": False, "error": f"job exists ({cur.get('state')}): use resume"}
    if resume and cur.get("state") == "ok":
        return {"id": jid, "launched": False, "error": "job already finished ok"}
    if cur.get("orphan_group"):
        killpg(int(cur["pgid"]))  # a dead supervisor's job still running: never run it twice
    me = os.path.abspath(__file__)
    spec = read_json(os.path.join(d, "spec.json"), {}) or {}
    # WHERE the supervisor is born decides what it may touch (measured 2026-10-07, E3):
    # - tmux (launchd GUI session): has the login keychain -> `claude` is logged in, git over
    #   HTTPS works. But macOS privacy (TCC) blocks it on an external disk: opendir() on
    #   /Volumes/<disk> waits forever for a "removable volumes" prompt nobody clicks.
    # - ssh session, detached (own session, so the ssh hang-up never reaches it): reads the
    #   external disk, survives the ssh connection closing, but has no keychain.
    # Scripts need disks, agents need the keychain: that is the default split.
    via = spec.get("via") or ("ssh" if spec.get("kind") == "script" else "tmux")
    out_path = os.path.join(d, "supervisor.out")
    if via == "ssh":
        with open(out_path, "ab") as o:
            subprocess.Popen(["/usr/bin/python3", me, "supervise", jid], stdin=subprocess.DEVNULL,
                             stdout=o, stderr=subprocess.STDOUT, start_new_session=True,
                             close_fds=True, cwd=HOME)
    else:
        if subprocess.run(TMUX + ["has-session", "-t", "enjambre"], capture_output=True).returncode:
            # Never create the session over ssh: it would have no keychain (tmux-run.sh explains).
            return {"id": jid, "launched": False, "error": "no tmux session 'enjambre' (GUI login needed)"}
        line = (f"nohup /usr/bin/python3 {shlex.quote(me)} supervise {jid} "
                f">> {shlex.quote(out_path)} 2>&1")
        r = subprocess.run(TMUX + ["new-window", "-d", "-t", "enjambre", "-n", f"job-{jid[:30]}", line],
                           capture_output=True, text=True)
        if r.returncode:
            return {"id": jid, "launched": False, "error": "tmux: " + r.stderr.strip()[-200:]}
    for _ in range(40):  # wait until the supervisor has written its pid (<= 10 s)
        st = read_json(os.path.join(d, "status.json"), {}) or {}
        if st.get("child_pid") and st.get("state") == "running" and \
                st.get("attempt", 0) > int(cur.get("attempt") or 0):
            return {"id": jid, "launched": True, "attempt": st["attempt"], "via": via,
                    "supervisor_pid": st["supervisor_pid"], "pgid": st["pgid"]}
        time.sleep(0.25)
    return {"id": jid, "launched": True, "attempt": None, "warning": "supervisor not confirmed in 10 s"}


def control(jid: str, action: str, reason: str) -> dict:
    """The PC's scheduler asks; the supervisor applies it within a second (see controller)."""
    if action not in ("pause", "resume", "evict"):
        return {"id": jid, "ok": False, "error": f"bad action {action!r}"}
    d = jdir(jid)
    st = status(jid)
    if not st.get("alive"):
        return {"id": jid, "ok": False, "error": f"supervisor not alive ({st.get('state')})"}
    write_json(os.path.join(d, "control.json"), {"action": action, "reason": reason, "at": now()})
    return {"id": jid, "ok": True, "queued": action, "state": st.get("state")}


def cont(jid: str) -> dict:
    """SIGCONT straight to the job's group: for a paused job whose supervisor died (nothing may
    stay frozen because its supervisor is gone)."""
    st = read_json(os.path.join(jdir(jid), "status.json"), {}) or {}
    try:
        os.killpg(int(st["pgid"]), signal.SIGCONT)
        return {"id": jid, "ok": True}
    except (KeyError, TypeError, ValueError, OSError) as e:
        return {"id": jid, "ok": False, "error": type(e).__name__}


def sigtree(pid: int, sig: str) -> dict:
    """STOP / CONT a process the owner adopted (enjambre adoptar) and everything below it."""
    if sig not in ("STOP", "CONT"):
        return {"pid": pid, "ok": False, "error": "STOP or CONT only"}
    done = []
    for p in descendants(pid):
        try:
            os.kill(p, getattr(signal, "SIG" + sig))
            done.append(p)
        except OSError:
            pass
    return {"pid": pid, "ok": bool(done), "signalled": done}


def kill(jid: str) -> dict:
    st = status(jid)
    if st.get("pgid") and st.get("state") in ("running", "dead"):
        killpg(int(st["pgid"]))
    sp = st.get("supervisor_pid")
    if sp and pid_alive(sp):
        os.kill(int(sp), signal.SIGTERM)
    return {"id": jid, "killed": True, "pgid": st.get("pgid")}


def main(argv: list) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 64
    verb, ids = argv[0], argv[1:]
    if verb == "supervise":
        return supervise(ids[0])
    if verb == "control":         # control <id> pause|resume|evict [base64 reason]
        reason = base64.b64decode(ids[2]).decode("utf-8", "replace") if len(ids) > 2 else ""
        print(json.dumps(control(ids[0], ids[1], reason), ensure_ascii=False))
        return 0
    if verb == "sigtree":         # sigtree <pid> STOP|CONT
        print(json.dumps(sigtree(int(ids[0]), ids[1]), ensure_ascii=False))
        return 0
    for jid in ids:
        if verb == "status":
            out = status(jid)
        elif verb == "launch":
            out = launch(jid)
        elif verb == "resume":
            out = launch(jid, resume=True)
        elif verb == "kill":
            out = kill(jid)
        elif verb == "cont":
            out = cont(jid)
        else:
            print(__doc__)
            return 64
        print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
