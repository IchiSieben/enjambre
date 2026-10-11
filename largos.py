"""largos — long, detached jobs on a node: launch, follow, collect, resume, kill. Stdlib only.

The node side is jobrun.py (runs there under tmux). This side never waits for a job: `launch`
returns as soon as the node confirms the supervisor started, so the Windows window can close.
Results come back through `collect`, which watch calls every round and anyone can call by hand
(`enjambre collect`); it is idempotent: a job is collected once, then marked.

state/jobs/<id>/            (gitignored)
    job.json        what was launched, where, and whether it was collected
    status.json     the node's last status (jobrun.py status)
    latidos.jsonl   the node's heartbeat lines, copied as they appear
    log | log.N     the job's output, copied when it ends
The ledger (jobs.jsonl) gets exactly one line per job, when it ends: the same "one line per
run" contract every reader of the ledger relies on. Heartbeats never go to the ledger.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import presence as P

HERE = Path(__file__).resolve().parent
STATE = HERE / "state"
JOBS = STATE / "jobs"
JOBRUN = HERE / "jobrun.py"
FINAL = ("ok", "failed", "incomplete", "killed")
PY = "/usr/bin/python3"
# `- tests-cmd: `...`` and `- script-cmd <name>: `...`` lines in a repo's AGENTS.md
AGENTS_CMD = re.compile(r"^[ \t]*(?:[-*][ \t]*)?(tests-cmd|script-cmd[ \t]+([a-z0-9][a-z0-9-]{0,30}))"
                        r"[ \t]*:[ \t]*`([^`\n]+)`[ \t]*$", re.M)
BASE_TOOLS = "Read,Edit,Write,Glob,Grep,Bash(git status:*),Bash(git diff:*),Bash(ls:*)"


def ssh(host: str, cmd: str, stdin: bytes | None = None, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, cmd],
                          input=stdin, capture_output=True, timeout=timeout, **P.no_console())


def agents_cmds(repo_dir: Path) -> dict:
    """The commands a repo declares for enjambre. Nothing else may run on a node: the CLI takes
    a NAME, never a command line (allowlist per repo, docs/conceptos/presupuesto-por-trabajo.md)."""
    out = {"tests": None, "scripts": {}}
    try:
        text = (repo_dir / "AGENTS.md").read_text(encoding="utf-8")
    except OSError:
        return out
    for m in AGENTS_CMD.finditer(text):
        if m.group(1) == "tests-cmd":
            out["tests"] = out["tests"] or m.group(3).strip()
        else:
            out["scripts"].setdefault(m.group(2), m.group(3).strip())
    return out


def allowed_tools(kind: str, tests_cmd: str | None) -> str:
    """kind tests: exactly Bash(<tests-cmd>) on top of the read/edit set, nothing broader."""
    if kind != "tests":
        return BASE_TOOLS
    if not tests_cmd:
        raise ValueError("kind tests needs `- tests-cmd: `...`` in the repo's AGENTS.md")
    if "," in tests_cmd or "(" in tests_cmd or ")" in tests_cmd:
        raise ValueError("tests-cmd cannot contain ',' or parentheses (they split --allowedTools)")
    return f"{BASE_TOOLS},Bash({tests_cmd})"


def tests_preamble(tests_cmd: str) -> str:
    """Bash(<cmd>) is an exact match: a redirect, a pipe or `; echo` makes it a different command
    and it is denied (Metallum, 2026-10-07). Tell the agent so, before the task."""
    return ("Comando de verificación permitido (y el único Bash que puedes ejecutar): "
            f"`{tests_cmd}`\nEjecútalo exactamente así, tal cual, sin redirecciones, sin pipes, "
            "sin `cd` y sin nada antes o después. Cualquier otra forma será denegada.\n\n")


def prices_for(model: str | None, models_json: Path | None = None) -> dict:
    """USD per million tokens for the heartbeat ESTIMATE. The CLI's own --max-budget-usd is the
    real cap; this is only what the heartbeat shows while the job runs."""
    p = models_json or Path.home() / ".claude" / "rules" / "models.json"
    try:
        models = json.loads(p.read_text(encoding="utf-8"))["models"]
    except (OSError, ValueError, KeyError):
        return {}
    fam = (model or "opus").lower()
    for m in models:
        if (m.get("id") == fam or (m.get("family") == fam and m.get("current"))) and m.get("price_in"):
            return {"input": m["price_in"], "output": m["price_out"],
                    "cache_read": m.get("price_cache_hit", 0),
                    "cache_write": m.get("price_cache_write_1h") or m.get("price_cache_write_5m", 0),
                    "model": m["id"]}
    return {}


def job_id(name: str, t: dt.datetime | None = None) -> str:
    t = t or dt.datetime.now()
    base = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:40] or "job"
    return f"{base}-{t:%Y%m%d-%H%M%S}"


def read_meta(jid: str) -> dict | None:
    try:
        return json.loads((JOBS / jid / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_meta(meta: dict) -> None:
    d = JOBS / meta["id"]
    d.mkdir(parents=True, exist_ok=True)
    P.write_json_atomic(d / "job.json", meta)


def ship(host: str) -> str | None:
    """Copy the reviewed jobrun.py to the node every time (same rule as worker.sh)."""
    try:
        r = subprocess.run(["scp", "-q", str(JOBRUN), f"{host}:.enjambre/jobrun.py"],
                           capture_output=True, text=True, timeout=30, **P.no_console())
    except subprocess.TimeoutExpired:
        return "scp timed out (30 s)"
    return None if r.returncode == 0 else (r.stderr.strip() or "scp failed")


def last_json(out: bytes | str) -> dict:
    text = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
    for line in reversed(text.strip().splitlines()):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {}


def launch(node: str, host: str, spec: dict, meta: dict, ssh_fn=ssh, ship_fn=ship) -> dict:
    """Write spec.json on the node and start the supervisor there. Returns the node's answer;
    on success state/jobs/<id>/job.json exists and collect() will bring the result home."""
    jid = spec["id"]
    err = ship_fn(host)
    if err:
        return {"id": jid, "launched": False, "error": "cannot reach node: " + err}
    d = f".enjambre/jobs/{jid}"
    r = ssh_fn(host, f"mkdir -p {d} && cat > {d}/spec.json && {PY} .enjambre/jobrun.py launch {jid}",
               json.dumps(spec, ensure_ascii=False).encode("utf-8"))
    res = last_json(r.stdout)
    if not res:
        res = {"id": jid, "launched": False, "error": (r.stderr or b"").decode("utf-8", "replace")[-300:]}
    if res.get("launched"):
        write_meta(dict(meta, id=jid, node=node, host=host, kind=spec["kind"], repo=spec["repo"],
                        launched=now(), attempts=1, collected=False))
    return res


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def statuses(host: str, ids: list, ssh_fn=ssh) -> dict:
    r = ssh_fn(host, f"{PY} .enjambre/jobrun.py status " + " ".join(ids))
    out = {}
    for line in (r.stdout or b"").decode("utf-8", "replace").splitlines():
        if line.startswith("{"):
            try:
                st = json.loads(line)
                out[st["id"]] = st
            except (ValueError, KeyError):
                pass
    return out


def copy_beats(host: str, jid: str, remote_n: int, ssh_fn=ssh) -> int:
    """Append the node's new heartbeat lines to state/jobs/<id>/latidos.jsonl. Returns how many."""
    local = JOBS / jid / "latidos.jsonl"
    have = len(local.read_text(encoding="utf-8").splitlines()) if local.exists() else 0
    if remote_n <= have:
        return 0
    r = ssh_fn(host, f"tail -n +{have + 1} .enjambre/jobs/{jid}/latidos.jsonl")
    lines = [l for l in (r.stdout or b"").decode("utf-8", "replace").splitlines() if l.startswith("{")]
    with open(local, "a", encoding="utf-8", newline="\n") as f:
        for l in lines:
            f.write(l + "\n")
    return len(lines)


def fetch_file(host: str, remote: str, local: Path) -> bool:
    r = subprocess.run(["scp", "-q", f"{host}:{remote}", str(local)], capture_output=True,
                       **P.no_console())
    return r.returncode == 0


def utc(stamp: str | None) -> str | None:
    """The node writes local time with its offset; the ledger is UTC like every other line."""
    try:
        return dt.datetime.fromisoformat(stamp).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return stamp


def ledger_entry(meta: dict, st: dict, note: str | None, pushed: bool) -> dict:
    """The one ledger line of a long job: every key cmd_status/history/visor/bandeja read."""
    state = st.get("state")
    status = state if state in ("ok", "failed", "incomplete") else "failed"
    reason = st.get("reason") or ("killed" if state == "killed" else None)
    rc = st.get("rc")
    try:
        dur = int(dt.datetime.fromisoformat(st["end"]).timestamp()
                  - dt.datetime.fromisoformat(st["start"]).timestamp())
    except (KeyError, TypeError, ValueError):
        dur = None
    lat = st.get("latido") or {}
    agent = meta["kind"] != "script"
    return {
        "id": meta["id"], "node": meta["node"], "worker": "claude" if agent else "script",
        "repo": meta["repo"], "branch": st.get("branch"), "start": utc(st.get("start")),
        "end": utc(st.get("end")),
        "exit": rc if isinstance(rc, int) else 2, "status": status, "reason": reason,
        "permission_denials": st.get("permission_denials") or None,
        "duration_s": dur, "pushed": pushed,
        # token counts only: the CLI's usage also carries one `iterations` row per turn (99 rows)
        "usage": {k: v for k, v in (st.get("usage") or {}).items()
                  if k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                           "cache_read_input_tokens")} if agent else None,
        "cost_usd": st.get("cost_usd") if agent else 0, "result": st.get("result") or None,
        "note": note, "kind": meta["kind"], "detached": True, "attempts": st.get("attempt"),
        "latidos": st.get("latidos_n"), "turns": st.get("turns") if agent else None,
        "budget_usd": meta.get("budget_usd"), "cmd_name": meta.get("cmd_name"),
        "pct": lat.get("pct") if not agent else None, "session_id": st.get("session_id"),
        "origin_session": meta.get("origin_session"), "routed_by": meta.get("routed_by"),
        "route_reason": meta.get("route_reason"),
        # prompt 20: the class it ran with and every pause / eviction it went through
        "priority": meta.get("priority") or "normal", "preemptable": meta.get("preemptable"),
        "resumable": meta.get("resumable"), "preemptions": st.get("preemptions") or None,
    }


def collect(append_ledger, bring_home=None, notify=None, ssh_fn=ssh, fetch_fn=fetch_file,
            only: str | None = None, log=lambda m: None) -> list:
    """One pass over every uncollected job: copy beats, and for finished jobs copy the log,
    bring the branch home (agents) and write the ledger line. Returns what it did."""
    done = []
    metas = [m for m in (read_meta(p.parent.name) for p in sorted(JOBS.glob("*/job.json")))
             if m and not m.get("collected") and (only is None or m["id"] == only)]
    by_host: dict = {}
    for m in metas:
        by_host.setdefault(P.job_host(m), []).append(m)
    for host, ms in by_host.items():
        try:
            sts = statuses(host, [m["id"] for m in ms], ssh_fn)
        except subprocess.TimeoutExpired:
            log(f"collect: {host} timed out")
            continue
        for m in ms:
            st = sts.get(m["id"])
            if not st:
                continue
            jd = JOBS / m["id"]
            new = copy_beats(host, m["id"], int(st.get("latidos_n") or 0), ssh_fn)
            P.write_json_atomic(jd / "status.json", st)
            state = st.get("state")
            if state == "dead":
                if not m.get("dead_notified") and notify:
                    notify("enjambre", f"{m['id']} murió en {m['node']}: enjambre resume {m['id']}")
                m["dead_notified"] = True
                write_meta(m)
                done.append({"id": m["id"], "state": "dead", "beats": new})
                continue
            if state not in FINAL:
                done.append({"id": m["id"], "state": state, "beats": new})
                continue
            name = st.get("log_name") or "log"
            fetch_fn(host, f".enjambre/jobs/{m['id']}/{name}", jd / name)
            note, pushed = None, False
            if st.get("branch") and bring_home:
                pushed, note = bring_home(m, st)
            entry = ledger_entry(m, st, note, pushed)
            append_ledger(entry)
            m.update(collected=True, collected_at=now())
            write_meta(m)
            if notify:
                notify("enjambre", f"{m['id']}: {entry['status']}"
                       + (f" ({entry['reason']})" if entry.get("reason") else ""))
            done.append({"id": m["id"], "state": state, "ledger": True})
    return done


def follow(jid: str, interval: float = 5, ssh_fn=ssh, out=sys.stdout, sleep=time.sleep,
           max_rounds: int | None = None) -> dict:
    """Print the job's log as it grows, and a heartbeat line when it changes, until it ends.
    Ctrl-C stops following, never the job."""
    meta = read_meta(jid)
    if not meta:
        raise KeyError(jid)
    host, off, last_beat, rounds = P.job_host(meta), 0, None, 0
    while True:
        st = statuses(host, [jid], ssh_fn).get(jid) or {}
        name = st.get("log_name") or "log"
        size = int(st.get("log_size") or 0)
        if size < off:  # a new segment (resume) starts a new stream file
            off = 0
        if size > off:
            r = ssh_fn(host, f"tail -c +{off + 1} .enjambre/jobs/{jid}/{shlex.quote(name)} | head -c 200000")
            chunk = (r.stdout or b"")
            off += len(chunk)
            out.write(render_chunk(chunk.decode("utf-8", "replace"), meta["kind"]))
        b = st.get("latido") or {}
        if b.get("ts") != last_beat:
            last_beat = b.get("ts")
            out.write(beat_line(st) + "\n")
        out.flush()
        if st.get("state") in FINAL or st.get("state") in ("dead", "unknown"):
            return st
        rounds += 1
        if max_rounds is not None and rounds >= max_rounds:
            return st
        sleep(interval)


def render_chunk(text: str, kind: str) -> str:
    """Scripts: as printed. Agents: stream-json is unreadable raw; show tool calls and text."""
    if kind == "script":
        return text
    lines = []
    for raw in text.splitlines():
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if ev.get("type") == "assistant":
            for c in (ev.get("message") or {}).get("content") or []:
                if c.get("type") == "tool_use":
                    arg = c.get("input") or {}
                    tgt = arg.get("command") or arg.get("file_path") or arg.get("pattern") or ""
                    lines.append(f"  -> {c.get('name')} {str(tgt)[:120]}")
                elif c.get("type") == "text" and c.get("text", "").strip():
                    lines.append("  " + c["text"].strip().replace("\n", " ")[:160])
        elif ev.get("type") == "result":
            lines.append(f"  == result: {ev.get('subtype')} cost={ev.get('total_cost_usd')} "
                         f"turns={ev.get('num_turns')}")
    return "\n".join(lines) + ("\n" if lines else "")


def beat_line(st: dict) -> str:
    b = st.get("latido") or {}
    bits = [f"[latido {b.get('ts', '?')[11:19]}] {st.get('state')}", f"intento {st.get('attempt')}",
            f"{int(b.get('elapsed_s') or 0) // 60} min"]
    if b.get("pct") is not None:
        bits.append(f"{b['pct']:.1f}%")
    if b.get("turns") is not None:
        bits.append(f"turnos {b['turns']}")
    if b.get("cost_est_usd") is not None:
        bits.append(f"~US${b['cost_est_usd']:.2f} (estimado)")
    if b.get("last_tool"):
        bits.append(f"último {b['last_tool']}")
    return " | ".join(bits)


def resume(jid: str, ssh_fn=ssh, ship_fn=ship) -> dict:
    meta = read_meta(jid)
    if not meta:
        return {"id": jid, "launched": False, "error": "unknown job (no state/jobs/<id>/job.json)"}
    host = P.job_host(meta)
    err = ship_fn(host)
    if err:
        return {"id": jid, "launched": False, "error": "cannot reach node: " + err}
    res = last_json(ssh_fn(host, f"{PY} .enjambre/jobrun.py resume {jid}").stdout)
    if res.get("launched"):
        meta.update(attempts=int(meta.get("attempts") or 1) + 1, collected=False, dead_notified=False,
                    resumed=now())
        write_meta(meta)
    return res


def kill(jid: str, ssh_fn=ssh) -> dict:
    meta = read_meta(jid)
    if not meta:
        return {"id": jid, "killed": False, "error": "unknown job"}
    return last_json(ssh_fn(P.job_host(meta), f"{PY} .enjambre/jobrun.py kill {jid}", timeout=60).stdout)
