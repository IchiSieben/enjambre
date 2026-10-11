"""puente_cli — Windows side of the bridge to the persistent Claude Code on the Mac (agent/puente.py).

Transport: `ssh <node> curl ... http://127.0.0.1:8766/...`. The server listens on the Mac's
loopback only; ssh is the authenticated, encrypted hop, and every request also carries an HMAC
signature (the key never leaves the two machines: here it is ENJAMBRE_PUENTE_KEY in the user
environment, there ~/.config/enjambre/puente.key, chmod 600).

Ledger: every turn sent from here leaves state/puente/<turn>.json ("pending"). Whoever sees the
turn finished first — the `chat` that waited for it, a later `collect`, or watch every minute —
writes ONE ledger line and moves the pending file to state/puente/done/. Closing the window in the
middle loses nothing: the turn lives on the Mac, the pending file lives here.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import subprocess
import time
from pathlib import Path

import presence as P

PORT = 8766
FINAL = ("ok", "failed", "incomplete", "interrupted")
# Twin of agent/puente.py PLAYWRIGHT_TOOLS (a test checks they agree): named tools only, never
# the bare server (it carries browser_run_code_unsafe, evaluate, file_upload, drop).
PLAYWRIGHT = ("browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_find",
              "browser_take_screenshot", "browser_click", "browser_type", "browser_press_key",
              "browser_hover", "browser_select_option", "browser_fill_form", "browser_wait_for",
              "browser_console_messages", "browser_network_requests", "browser_network_request",
              "browser_resize", "browser_tabs", "browser_handle_dialog", "browser_close")
TID_RE = re.compile(r"p\d{8}-\d{6}-[0-9a-f]{6}")


def sign(key: bytes, ts: str, nonce: str, method: str, path: str, body: bytes) -> str:
    """Twin of agent/puente.py sign (tests check they agree)."""
    msg = f"{ts}\n{nonce}\n{method}\n{path}\n{hashlib.sha256(body).hexdigest()}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def key() -> bytes | None:
    """ENJAMBRE_PUENTE_KEY from the process env, else from HKCU\\Environment (a terminal opened
    before the key was stored, or watch started earlier, does not have it in os.environ)."""
    v = os.environ.get("ENJAMBRE_PUENTE_KEY", "")
    if not v and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                v = winreg.QueryValueEx(k, "ENJAMBRE_PUENTE_KEY")[0]
        except OSError:
            v = ""
    v = v.strip()
    return v.encode() if len(v) >= 32 else None


def curl_line(k: bytes, method: str, path: str, body: bytes = b"", stream: bool = False,
              max_time: int = 30, ts: str | None = None, nonce: str | None = None) -> str:
    """The shell line run on the node. Everything variable is quoted; the body goes by stdin."""
    ts = ts or str(int(time.time()))
    nonce = nonce or secrets.token_hex(8)
    sig = sign(k, ts, nonce, method, path, body)
    parts = ["curl", "-sS", "--max-time", str(int(max_time)), "-H", f"X-Puente-Ts: {ts}",
             "-H", f"X-Puente-Nonce: {nonce}", "-H", f"X-Puente-Sig: {sig}"]
    if stream:
        parts += ["-N"]
    else:
        parts += ["-w", "\\n%{http_code}"]
    if method == "POST":
        parts += ["-X", "POST", "-H", "Content-Type: application/json", "--data-binary", "@-"]
    parts.append(f"http://127.0.0.1:{PORT}{path}")
    return " ".join(shlex.quote(p) for p in parts)


class PuenteError(RuntimeError):
    pass


def request(host: str, method: str, path: str, obj: dict | None = None, timeout: int = 30,
            k: bytes | None = None, run=None) -> dict:
    """One signed request over ssh. Returns the JSON body; raises PuenteError with the reason."""
    k = k or key()
    if not k:
        raise PuenteError("sin clave: falta ENJAMBRE_PUENTE_KEY (tutorial 26, paso de la clave)")
    body = json.dumps(obj, ensure_ascii=False).encode() if obj is not None else b""
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
           curl_line(k, method, path, body, max_time=timeout)]
    r = (run or _run)(cmd, body, timeout + 15)
    out = (r.stdout or b"").decode("utf-8", "replace").rstrip()
    if r.returncode != 0:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()[-300:]
        if "Connection refused" in err or r.returncode == 7:
            raise PuenteError("el puente no responde en la Mac (¿servicio dev.enjambre.puente cargado?)")
        raise PuenteError(f"ssh/curl rc={r.returncode}: {err or out[-300:]}")
    text, _, code = out.rpartition("\n")
    try:
        data = json.loads(text) if text else {}
    except json.JSONDecodeError:
        raise PuenteError(f"respuesta no JSON (http {code}): {text[:200]}")
    if not code.startswith("2"):
        raise PuenteError(f"http {code}: {data.get('error', data)}")
    return data


def _run(cmd, body: bytes, timeout: float):
    return subprocess.run(cmd, input=body, capture_output=True, timeout=timeout, **P.no_console())


def stream(host: str, tid: str, start: int = 0, k: bytes | None = None, max_time: int = 6 * 3600):
    """Yield the turn's events (dicts) from `start`, following until the turn ends. Ctrl+C or a
    dropped ssh ends only this generator; the turn keeps running on the Mac."""
    k = k or key()
    path = f"/turns/{tid}/events?from={int(start)}&follow=1"
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=30", host,
           curl_line(k, "GET", path, stream=True, max_time=max_time)]
    pr = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **P.no_console())
    try:
        for raw in pr.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                yield {"t": "error", "text": line[:300]}
    finally:
        if pr.poll() is None:
            pr.terminate()  # our own ssh child, by handle


# ---------------------------------------------------------------- allowlist

def allowed_for(repo_dir: Path, agents_cmds, base: str, edit: bool = False) -> list[str]:
    """worker.sh's set, + Bash(<tests-cmd>) exactly, + the named Playwright tools. Without
    edit, Edit/Write are dropped: a puente turn works in the Mac's MAIN clone, with no branch and
    nothing that commits, and a dirty clone breaks the next `git pull --ff-only` and the worktree
    workers. Changes to code go through `enjambre run` (branch + review) (DECISIONES 247)."""
    tools = [t for t in base.split(",") if edit or t not in ("Edit", "Write")]
    tests = agents_cmds(repo_dir).get("tests")
    if tests and not set(",()") & set(tests):
        tools.append(f"Bash({tests})")
    tools += [f"mcp__playwright__{t}" for t in PLAYWRIGHT]
    return tools


# ---------------------------------------------------------------- pending -> ledger

def pdir(state: Path) -> Path:
    d = state / "puente"
    (d / "done").mkdir(parents=True, exist_ok=True)
    return d


def remember(state: Path, tid: str, info: dict) -> None:
    (pdir(state) / f"{tid}.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")


def pending(state: Path) -> list[dict]:
    out = []
    for p in sorted(pdir(state).glob("p*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return out


def ledger_entry(info: dict, m: dict) -> dict:
    return {
        "id": m["turn"], "node": info["node"], "worker": "puente", "repo": m["repo"], "branch": None,
        "start": m.get("started") or m.get("enqueued"), "end": m.get("end"),
        "exit": 0 if m["state"] == "ok" else 1, "status": m["state"] if m["state"] != "interrupted" else "failed",
        "reason": m.get("reason"), "permission_denials": m.get("permission_denials"),
        "duration_s": m.get("duration_s"), "pushed": False, "usage": m.get("usage"),
        "cost_usd": m.get("cost_usd") or 0, "result": (m.get("result") or "")[:500] or None, "note": None,
        "kind": "puente", "detached": True, "turns": m.get("num_turns"), "budget_usd": m.get("budget_usd"),
        "session_id": m.get("session_id"), "origin_session": info.get("origin_session"),
        "routed_by": "explicit", "route_reason": f"puente --repo {m['repo']}",
    }


def collect(state: Path, hosts: dict, append_ledger, only: str | None = None, req=request) -> list[dict]:
    """Every pending turn whose meta says it ended -> one ledger line, file to done/.
    hosts: node name -> ssh alias. Unreachable node: the pending file stays for next time."""
    done = []
    for info in pending(state):
        if only and info["turn"] != only:
            continue
        host = hosts.get(info["node"])
        if not host:
            continue
        try:
            m = req(host, "GET", f"/turns/{info['turn']}")
        except PuenteError:
            continue
        if m.get("state") not in FINAL:
            continue
        append_ledger(ledger_entry(info, m))
        src = pdir(state) / f"{info['turn']}.json"
        os.replace(src, pdir(state) / "done" / src.name)
        done.append({"turn": info["turn"], "state": m["state"], "cost_usd": m.get("cost_usd")})
    return done


# ---------------------------------------------------------------- status

GUI_MARK = "__PUENTE__"


def live_line(k: bytes | None) -> str:
    """One ssh call for status: console owner (GUI session or not) + the bridge's sessions."""
    first = "stat -f%Su /dev/console 2>/dev/null; echo " + GUI_MARK
    if not k:
        return first
    return first + "; " + curl_line(k, "GET", "/sessions", max_time=5)


def parse_live(out: str) -> dict:
    head, _, rest = out.partition(GUI_MARK)
    user = head.strip().splitlines()[-1].strip() if head.strip() else ""
    res = {"gui": bool(user) and user != "root", "console": user or None, "sessions": None}
    text, _, code = rest.strip().rpartition("\n")
    if code.strip().startswith("2"):
        try:
            res["sessions"] = json.loads(text).get("sessions", [])
        except json.JSONDecodeError:
            pass
    elif rest.strip():
        res["puente_error"] = f"http {code.strip() or '?'}"
    return res


def fmt_session(s: dict) -> str:
    state = f"corriendo {s['running']}" if s.get("running") else "en espera"
    q = f" +{s['queued']} en cola" if s.get("queued") else ""
    sid = (s.get("session_id") or "-")[:8]
    return (f"{s['repo']:<16} {state}{q} · {s.get('turns', 0)} turnos · US${s.get('cost_usd', 0):.4f}"
            f" · sesión {sid} · último {s.get('last') or '-'}")
