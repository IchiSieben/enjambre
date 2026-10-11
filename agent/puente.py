"""puente — a persistent Claude Code on the Mac that Windows can talk to (prompt 16, tutorial 26).

One server, launched by a LaunchAgent in the GUI session (so `claude` has the login keychain),
listening on 127.0.0.1 only. Windows reaches it with `ssh mac curl ...` (enjambre.py chat/send/
sessions): no LAN port, no tunnel to keep alive. Every request is signed (HMAC-SHA256 with a key
only the owner's two machines hold), because 127.0.0.1 is still reachable by any process on the
Mac and by a browser page trying a request against localhost.

Model of the work:
  session  = one repo. Holds the Claude session_id; the next turn resumes it (also after a
             reboot: it is a file, not a process).
  turn     = one message to that session. Queued, one at a time per session, run by the Agent SDK
             in this process, NOT inside the HTTP request: the caller may hang up (close the
             Windows window) and the turn keeps going. Its events go to an NDJSON file that any
             later request can read from any offset.

Python 3.10+ for the SDK (venv in ~/.enjambre/puente/venv). PUENTE_ENGINE=stub swaps the SDK for
an echo engine: tests and wiring checks without a model or a login.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HOST = "127.0.0.1"   # never 0.0.0.0: see tutorial 26, threat model
PORT = int(os.environ.get("PUENTE_PORT", "8766"))
ROOT = Path(os.environ.get("PUENTE_ROOT", str(Path.home() / ".enjambre" / "puente")))
KEY_FILE = Path(os.environ.get("PUENTE_KEY_FILE", str(Path.home() / ".config" / "enjambre" / "puente.key")))
PORTFOLIO = Path(os.environ.get("PUENTE_PORTFOLIO", str(Path.home() / "Documents" / "Portfolio")))
BUSY = Path(os.environ.get("PUENTE_BUSY", str(Path.home() / ".enjambre" / "busy.json")))
CLAUDE = os.environ.get("PUENTE_CLAUDE", str(Path.home() / ".local" / "bin" / "claude"))
ENGINE = os.environ.get("PUENTE_ENGINE", "sdk")
SKEW_S = 120          # signed requests older/newer than this are refused
BEAT_S = 15           # heartbeat into the turn meta, and keepalive line on a followed stream
FINAL = ("ok", "failed", "incomplete", "interrupted")
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,60}")
TID_RE = re.compile(r"p\d{8}-\d{6}-[0-9a-f]{6}")

# What a puente session may use without asking (nobody is there to answer): the same read/edit
# set as worker.sh, the repo's tests-cmd exactly, and the Playwright MCP the Mac's Claude Code has
# registered. Windows computes the list (largos.allowed_tools, one parser); the server re-checks it.
BASE_TOOLS = {"Read", "Edit", "Write", "Glob", "Grep", "Bash(git status:*)", "Bash(git diff:*)", "Bash(ls:*)"}
# Playwright tools by NAME, never the whole server: it also offers browser_run_code_unsafe
# ("RCE-equivalent" in its own description), browser_evaluate (arbitrary JS), and
# browser_file_upload / browser_drop (push local files into a page = exfiltration). An agent that
# browses untrusted pages must not reach those (DECISIONES 245). Twin list: puente_cli.PLAYWRIGHT.
MCP_PREFIX = "mcp__playwright__"
PLAYWRIGHT_TOOLS = {"browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_find",
                    "browser_take_screenshot", "browser_click", "browser_type", "browser_press_key",
                    "browser_hover", "browser_select_option", "browser_fill_form", "browser_wait_for",
                    "browser_console_messages", "browser_network_requests", "browser_network_request",
                    "browser_resize", "browser_tabs", "browser_handle_dialog", "browser_close"}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------- auth

def sign(key: bytes, ts: str, nonce: str, method: str, path: str, body: bytes) -> str:
    """Twin of puente_cli.sign on Windows (a test checks they agree). The nonce makes two
    identical requests in the same second two different signatures (replay check below)."""
    msg = f"{ts}\n{nonce}\n{method}\n{path}\n{hashlib.sha256(body).hexdigest()}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


class Auth:
    def __init__(self, key: bytes):
        self.key, self.seen, self.lock = key, {}, threading.Lock()

    def check(self, ts: str, nonce: str, sig: str, method: str, path: str, body: bytes) -> str | None:
        """None = ok, else the reason. A signature is accepted once (replay inside the window)."""
        try:
            age = abs(time.time() - float(ts))
        except (TypeError, ValueError):
            return "no timestamp"
        if age > SKEW_S:
            return "timestamp outside window"
        if not re.fullmatch(r"[0-9a-f]{16,64}", nonce or ""):
            return "no nonce"
        if not sig or not hmac.compare_digest(sign(self.key, ts, nonce, method, path, body), sig):
            return "bad signature"
        with self.lock:
            t = time.time()
            self.seen = {s: e for s, e in self.seen.items() if e > t}
            if sig in self.seen:
                return "replayed"
            self.seen[sig] = t + 2 * SKEW_S
        return None


def load_key() -> bytes:
    text = KEY_FILE.read_text(encoding="utf-8").strip()
    if len(text) < 32:
        raise SystemExit(f"puente: key in {KEY_FILE} is too short")
    if os.name == "posix" and KEY_FILE.stat().st_mode & 0o077:
        raise SystemExit(f"puente: {KEY_FILE} must be chmod 600")
    return text.encode()


# ---------------------------------------------------------------- store

class Store:
    """Everything on disk under ROOT: sessions/<repo>.json, turns/<tid>.json + <tid>.ndjson."""

    def __init__(self, root: Path):
        self.root = root
        (root / "sessions").mkdir(parents=True, exist_ok=True)
        (root / "turns").mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def session(self, repo: str) -> dict:
        return read_json(self.root / "sessions" / f"{repo}.json") or {
            "repo": repo, "session_id": None, "cost_usd": 0.0, "turns": 0, "created": now(),
            "last": None, "last_turn": None}

    def save_session(self, s: dict) -> None:
        write_json(self.root / "sessions" / f"{s['repo']}.json", s)

    def sessions(self) -> list[dict]:
        out = []
        for p in sorted((self.root / "sessions").glob("*.json")):
            s = read_json(p)
            if s:
                out.append(s)
        return out

    def meta(self, tid: str) -> dict | None:
        return read_json(self.root / "turns" / f"{tid}.json")

    def save_meta(self, m: dict) -> None:
        with self.lock:
            write_json(self.root / "turns" / f"{m['turn']}.json", m)

    def emit(self, tid: str, ev: dict) -> None:
        ev = {"ts": now(), **ev}
        with self.lock, open(self.root / "turns" / f"{tid}.ndjson", "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    def events(self, tid: str, start: int = 0) -> list[str]:
        try:
            with open(self.root / "turns" / f"{tid}.ndjson", encoding="utf-8") as f:
                return f.read().splitlines()[start:]
        except OSError:
            return []

    def turns(self) -> list[dict]:
        return [m for m in (read_json(p) for p in sorted((self.root / "turns").glob("p*.json"))) if m]


# ---------------------------------------------------------------- validation

def check_allowed(repo_dir: Path, allowed: list[str]) -> str | None:
    """Every requested tool must be in the base set, the Playwright MCP, or Bash(<cmd>) where
    <cmd> is a backticked command in the repo's AGENTS.md. A check, not a second parser."""
    try:
        agents = (repo_dir / "AGENTS.md").read_text(encoding="utf-8")
    except OSError:
        return "repo has no AGENTS.md"
    for t in allowed:
        if t in BASE_TOOLS or (t.startswith(MCP_PREFIX) and t[len(MCP_PREFIX):] in PLAYWRIGHT_TOOLS):
            continue
        m = re.fullmatch(r"Bash\((.+)\)", t)
        if m and f"`{m.group(1)}`" in agents and ":*" not in m.group(1):
            continue
        return f"tool not allowed for a puente session: {t}"
    return None


def repo_dir_for(repo: str) -> Path | None:
    if not REPO_RE.fullmatch(repo or ""):
        return None
    d = (PORTFOLIO / repo).resolve()
    if d.parent != PORTFOLIO.resolve() or not d.is_dir():
        return None
    return d


# ---------------------------------------------------------------- busy marker

def busy_take(tid: str, repo: str) -> bool:
    """Write busy.json only when nobody (alive) holds it; True = it is ours to remove."""
    m = read_json(BUSY)
    if m and m.get("pid"):
        try:
            os.kill(int(m["pid"]), 0)
            return False          # a live long job holds it: leave it alone
        except (OSError, ValueError):
            pass
    BUSY.parent.mkdir(parents=True, exist_ok=True)
    write_json(BUSY, {"quien": f"puente:{repo}", "desde": now(), "tipo": "puente", "pid": os.getpid(),
                      "turno": tid})
    return True


def busy_release(tid: str) -> None:
    m = read_json(BUSY)
    if m and m.get("turno") == tid:
        try:
            BUSY.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------- engines

async def run_stub(store: Store, m: dict, session: dict) -> dict:
    """Echo engine: same events and result shape as the SDK, no model, no cost."""
    delay = float(os.environ.get("PUENTE_STUB_SLEEP", "0"))
    store.emit(m["turn"], {"t": "text", "text": f"eco ({m['repo']}): {m['message']}"})
    steps = int(delay) if delay >= 1 else 0
    for i in range(steps):
        await asyncio.sleep(1)
        store.emit(m["turn"], {"t": "tool", "name": "stub_sleep", "input": f"{i + 1}/{steps}"})
    if delay and not steps:
        await asyncio.sleep(delay)
    sid = session.get("session_id") or str(uuid.uuid4())
    return {"session_id": sid, "cost_usd": 0.0, "is_error": False, "num_turns": 1,
            "result": f"eco ({m['repo']}): {m['message']}", "permission_denials": []}


OUT_DIR = ".puente"   # captures and scratch files of a turn, inside the repo but never in git
APPEND = ("Esta sesion la maneja enjambre desde otra maquina; nadie mira esta pantalla. "
          "Guarda toda captura o archivo temporal en .puente/ dentro del repo (por ejemplo "
          "filename '.puente/captura.png'), nunca en la raiz ni en carpetas versionadas.")


def prepare_out_dir(repo_dir: Path) -> None:
    """.puente/ exists and is in .git/info/exclude: a capture never dirties the clone that
    `git pull --ff-only` and the worktree workers rely on (DECISIONES 248)."""
    (repo_dir / OUT_DIR).mkdir(exist_ok=True)
    ex = repo_dir / ".git" / "info" / "exclude"
    if (repo_dir / ".git").is_dir():
        ex.parent.mkdir(parents=True, exist_ok=True)
        lines = ex.read_text(encoding="utf-8").splitlines() if ex.exists() else []
        if f"/{OUT_DIR}/" not in lines:
            with open(ex, "a", encoding="utf-8") as f:
                f.write(f"/{OUT_DIR}/" + chr(10))


SWEEP_EXT = (".png", ".jpg", ".jpeg", ".webp", ".pdf", ".yml")


def untracked(repo_dir: Path) -> set[str] | None:
    """Untracked, not-ignored files of the repo (git's view). None = not a git repo / git failed."""
    try:
        r = subprocess.run(["git", "-C", str(repo_dir), "ls-files", "--others", "--exclude-standard", "-z"],
                           capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return set(filter(None, r.stdout.decode("utf-8", "replace").split(chr(0)))) if r.returncode == 0 else None


def sweep(repo_dir: Path, before: set[str] | None, tid: str) -> list[str]:
    """Captures the turn left loose in the repo -> .puente/<turn>/. The system prompt asks the
    agent for .puente/, but the fire test showed it does not always listen (it saved 4 PNGs in
    the root): this makes it mechanical. Only files that were NOT there before the turn, only
    capture-like extensions; moved, never deleted (DECISIONES 248)."""
    after = untracked(repo_dir)
    if before is None or after is None:
        return []
    moved = []
    for rel in sorted(after - before):
        if not rel.lower().endswith(SWEEP_EXT) or rel.startswith(OUT_DIR + "/"):
            continue
        src, dst = repo_dir / rel, repo_dir / OUT_DIR / tid / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved.append(f"{OUT_DIR}/{tid}/{rel}")
    return moved


def short(v, n: int = 160) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[: n - 1] + "…"


async def run_sdk(store: Store, m: dict, session: dict) -> dict:
    from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
                                  PermissionResultDeny, ResultMessage, SystemMessage, TextBlock,
                                  ToolUseBlock)
    denials: list[dict] = []

    async def deny(tool_name, tool_input, context):
        # Only reached for tools NOT in allowed_tools. Nobody is watching: deny, record, go on.
        denials.append({"tool_name": tool_name, "tool_input": short(tool_input, 300)})
        store.emit(m["turn"], {"t": "denied", "name": tool_name, "input": short(tool_input)})
        return PermissionResultDeny(message="not on this repo's puente allowlist (AGENTS.md)")

    repo_dir = repo_dir_for(m["repo"])
    prepare_out_dir(repo_dir)
    opts = ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code", "append": APPEND},
        cwd=str(repo_dir), resume=session.get("session_id") or None,
        allowed_tools=m["allowed"], permission_mode="default", can_use_tool=deny,
        # No "local": .claude/settings.local.json is where interactive "always allow" answers
        # pile up, and its allow rules would widen this allowlist behind the server's back.
        setting_sources=["user", "project"], max_turns=m.get("max_turns") or 30,
        max_budget_usd=m.get("budget_usd") or 2.0, model=m.get("model") or None,
        cli_path=CLAUDE if Path(CLAUDE).exists() else None)
    res: dict = {"permission_denials": denials}
    async with ClaudeSDKClient(options=opts) as c:
        await c.query(m["message"])
        async for msg in c.receive_response():
            if isinstance(msg, SystemMessage) and getattr(msg, "subtype", "") == "init":
                d = getattr(msg, "data", {}) or {}
                if d.get("session_id"):
                    res["session_id"] = d["session_id"]
                # What this session really got: proves the Mac's MCP (Playwright) is loaded.
                store.emit(m["turn"], {"t": "init", "permission_mode": d.get("permissionMode"),
                                       "mcp": d.get("mcp_servers"), "n_tools": len(d.get("tools") or []),
                                       "model": d.get("model")})
            elif isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if isinstance(b, TextBlock) and b.text.strip():
                        store.emit(m["turn"], {"t": "text", "text": b.text})
                    elif isinstance(b, ToolUseBlock):
                        store.emit(m["turn"], {"t": "tool", "name": b.name, "input": short(b.input)})
            elif isinstance(msg, ResultMessage):
                res.update(session_id=msg.session_id, cost_usd=msg.total_cost_usd or 0.0,
                           is_error=bool(msg.is_error), num_turns=msg.num_turns,
                           result=msg.result, usage=msg.usage, subtype=msg.subtype)
    return res


ENGINES = {"stub": run_stub, "sdk": run_sdk}


# ---------------------------------------------------------------- runner

class Runner:
    """One asyncio loop in a thread; one worker per repo, so a session runs one turn at a time."""

    def __init__(self, store: Store, engine: str = ENGINE):
        self.store, self.engine = store, ENGINES[engine]
        self.engine_name = engine
        self.loop = asyncio.new_event_loop()
        self.queues: dict[str, asyncio.Queue] = {}
        threading.Thread(target=self.loop.run_forever, daemon=True, name="puente-loop").start()

    def recover(self) -> int:
        """After a restart: a turn that was running is lost (its process died) -> interrupted;
        queued ones are queued again. The session_id is on disk, so the next turn resumes."""
        n = 0
        for m in self.store.turns():
            if m["state"] == "running":
                m.update(state="interrupted", end=now(), reason="server restarted during the turn")
                self.store.save_meta(m)
                self.store.emit(m["turn"], {"t": "end", "state": "interrupted"})
                n += 1
            elif m["state"] == "queued":
                self.submit(m)
        return n

    def submit(self, m: dict) -> int:
        fut = asyncio.run_coroutine_threadsafe(self._put(m), self.loop)
        return fut.result(10)

    async def _put(self, m: dict) -> int:
        q = self.queues.get(m["repo"])
        if q is None:
            q = self.queues[m["repo"]] = asyncio.Queue()
            self.loop.create_task(self._worker(m["repo"], q))
        await q.put(m["turn"])
        return q.qsize()

    async def _worker(self, repo: str, q: asyncio.Queue) -> None:
        while True:
            tid = await q.get()
            try:
                await self._run(tid)
            except Exception:  # never let one turn kill the session's worker
                traceback.print_exc()

    async def _beat(self, tid: str) -> None:
        while True:
            await asyncio.sleep(BEAT_S)
            m = self.store.meta(tid)
            if not m or m["state"] != "running":
                return
            m["beat"] = now()
            self.store.save_meta(m)

    async def _run(self, tid: str) -> None:
        s, m = self.store, self.store.meta(tid)
        if not m or m["state"] != "queued":
            return
        session = s.session(m["repo"])
        m.update(state="running", started=now(), beat=now(), resumed_from=session.get("session_id"))
        s.save_meta(m)
        s.emit(tid, {"t": "start", "resume": bool(session.get("session_id"))})
        ours = busy_take(tid, m["repo"])
        beat = self.loop.create_task(self._beat(tid))
        t0 = time.time()
        repo_dir = repo_dir_for(m["repo"])
        before = untracked(repo_dir) if repo_dir else None
        try:
            res = await self.engine(s, m, session)
            denied = res.get("permission_denials") or []
            state = "failed" if res.get("is_error") else ("incomplete" if denied else "ok")
            reason = (res.get("result") if res.get("is_error") else
                      f"{len(denied)} herramienta(s) denegada(s)" if denied else None)
        except Exception as e:
            res, state, reason = {}, "failed", f"{type(e).__name__}: {e}"[:500]
            s.emit(tid, {"t": "error", "text": reason})
        finally:
            beat.cancel()
            if ours:
                busy_release(tid)
            moved = sweep(repo_dir, before, tid) if repo_dir else []
            if moved:
                s.emit(tid, {"t": "moved", "files": moved})
        cost = float(res.get("cost_usd") or 0.0)
        m.update(state=state, end=now(), duration_s=round(time.time() - t0, 1), cost_usd=cost, files=moved or None,
                 session_id=res.get("session_id"), num_turns=res.get("num_turns"),
                 result=short(res.get("result") or "", 2000), reason=reason,
                 permission_denials=res.get("permission_denials") or None, usage=res.get("usage"))
        s.save_meta(m)
        # A turn that failed before the model answered (login expired: cost 0) left no
        # conversation behind; resuming its id would fail every next turn.
        if res.get("session_id") and not (state == "failed" and not cost):
            session["session_id"] = res["session_id"]
        session.update(cost_usd=round(session.get("cost_usd", 0.0) + cost, 6),
                       turns=session.get("turns", 0) + 1, last=now(), last_turn=tid)
        s.save_session(session)
        s.emit(tid, {"t": "end", "state": state, "cost_usd": cost, "result": short(res.get("result") or "", 2000)})


# ---------------------------------------------------------------- http

def new_tid() -> str:
    return "p" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


def make_handler(store: Store, runner: Runner, auth: Auth):
    class H(BaseHTTPRequestHandler):
        server_version = "puente/1"

        def log_message(self, fmt, *args):  # one line per request on stderr (launchd log)
            sys.stderr.write("%s %s\n" % (now(), fmt % args))

        def _send(self, code: int, obj) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self, body: bytes) -> bool:
            # No Origin header is ever sent by curl; a browser always sends one on a cross-site
            # request. Refuse it outright, before the signature: no CORS, ever.
            if self.headers.get("Origin"):
                self._send(403, {"error": "browser requests are refused"})
                return False
            why = auth.check(self.headers.get("X-Puente-Ts", ""), self.headers.get("X-Puente-Nonce", ""),
                             self.headers.get("X-Puente-Sig", ""),
                             self.command, self.path, body)
            if why:
                self._send(401, {"error": why})
                return False
            return True

        def do_GET(self):
            if not self._auth(b""):
                return
            u = urlsplit(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            parts = [p for p in u.path.split("/") if p]
            if parts == ["health"]:
                return self._send(200, {"ok": True, "engine": runner.engine_name, "pid": os.getpid(),
                                        "gui_user": console_user()})
            if parts == ["sessions"]:
                running = {m["repo"]: m["turn"] for m in store.turns() if m["state"] == "running"}
                queued: dict[str, int] = {}
                for m in store.turns():
                    if m["state"] == "queued":
                        queued[m["repo"]] = queued.get(m["repo"], 0) + 1
                out = [{**x, "running": running.get(x["repo"]), "queued": queued.get(x["repo"], 0)}
                       for x in store.sessions()]
                return self._send(200, {"sessions": out})
            if parts == ["turns"]:
                since = q.get("since", "")
                return self._send(200, {"turns": [m for m in store.turns()
                                                  if (m.get("end") or m.get("enqueued", "")) >= since]})
            if len(parts) >= 2 and parts[0] == "turns" and TID_RE.fullmatch(parts[1]):
                m = store.meta(parts[1])
                if not m:
                    return self._send(404, {"error": "no such turn"})
                if len(parts) == 2:
                    return self._send(200, m)
                if parts[2] == "events":
                    return self._stream(parts[1], int(q.get("from", "0") or 0), q.get("follow") == "1")
            self._send(404, {"error": "not found"})

        def _stream(self, tid: str, start: int, follow: bool) -> None:
            """NDJSON. With follow=1 the response stays open until the turn ends; the caller
            hanging up stops only this stream, never the turn."""
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Connection", "close")
            self.end_headers()
            n, last = start, time.time()
            try:
                while True:
                    lines = store.events(tid, n)
                    for line in lines:
                        self.wfile.write((json.dumps({"n": n, **json.loads(line)}, ensure_ascii=False) + "\n").encode())
                        n += 1
                    if lines:
                        self.wfile.flush()
                        last = time.time()
                    m = store.meta(tid) or {}
                    if not follow or (m.get("state") in FINAL and not store.events(tid, n)):
                        return
                    if time.time() - last >= BEAT_S:
                        self.wfile.write((json.dumps({"n": None, "t": "beat", "state": m.get("state")}) + "\n").encode())
                        self.wfile.flush()
                        last = time.time()
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 200_000:
                return self._send(413, {"error": "body too large"})
            body = self.rfile.read(length)
            if not self._auth(body):
                return
            if urlsplit(self.path).path != "/turns":
                return self._send(404, {"error": "not found"})
            try:
                req = json.loads(body)
            except json.JSONDecodeError:
                return self._send(400, {"error": "body is not JSON"})
            repo, msg = req.get("repo", ""), (req.get("message") or "").strip()
            d = repo_dir_for(repo)
            if not d:
                return self._send(400, {"error": f"repo not found under Portfolio: {repo!r}"})
            if not msg:
                return self._send(400, {"error": "empty message"})
            allowed = [t for t in (req.get("allowed") or []) if isinstance(t, str)]
            why = check_allowed(d, allowed)
            if why:
                return self._send(403, {"error": why})
            tid = new_tid()
            m = {"turn": tid, "repo": repo, "message": msg, "state": "queued", "enqueued": now(),
                 "allowed": allowed, "model": req.get("model"), "max_turns": req.get("max_turns"),
                 "budget_usd": req.get("budget_usd"), "origin": req.get("origin"),
                 "origin_session": req.get("origin_session")}
            store.save_meta(m)
            store.emit(tid, {"t": "queued"})
            pos = runner.submit(m)
            self._send(202, {"turn": tid, "position": pos, "session_id": store.session(repo).get("session_id")})

    return H


def console_user() -> str | None:
    """Who owns the Mac's console: a user name = there is a GUI session; root = login window."""
    try:
        import pwd
        return pwd.getpwuid(os.stat("/dev/console").st_uid).pw_name
    except (OSError, KeyError, ImportError):
        return None


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind calls socket.getfqdn(): a reverse DNS lookup that took ~50 s on
        # the Mac after a launchd restart (measured 2026-10-07). The name is never used here.
        import socketserver
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = HOST, self.server_address[1]


def serve(port: int = PORT, engine: str = ENGINE) -> ThreadingHTTPServer:
    store, auth = Store(ROOT), Auth(load_key())
    runner = Runner(store, engine)
    n = runner.recover()
    httpd = Server((HOST, port), make_handler(store, runner, auth))
    sys.stderr.write(f"{now()} puente on {HOST}:{port} engine={engine} root={ROOT} interrupted={n}\n")
    return httpd


if __name__ == "__main__":
    serve().serve_forever()
