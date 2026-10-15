"""presence — which nodes can take a job right now, and a queue for the ones that cannot.

Stdlib only. The pure parts (levels, debounce, queue moves) take their inputs as arguments so
tests can drive them with simulated nodes; the I/O parts (ssh, ping, toast) are small wrappers.

Levels, lowest to highest (each one implies the previous):
    down   no answer at all
    ping   answers ICMP, but ssh does not get in
    ssh    ssh works, but there is no `tmux -L enjambre` session (no keychain: jobs would fail)
    ready  ssh works and the GUI-session tmux exists: `enjambre run` can dispatch here

WHY polling from Windows and not a heartbeat from the nodes: see docs/conceptos/sondeo-vs-latido.md.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

LEVELS = ("down", "ping", "ssh", "ready")
DROP_AFTER = 2          # consecutive lower observations before a node is marked lower
FRESH_S = 90            # nodes.json older than this is not trusted by `run`
MOUNTS_STALE_S = 180    # smb-mount.sh writes every 60 s: three missed runs = unknown
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # pythonw must not flash consoles
FULL_PROBE_S = 300      # watch: ssh + tmux at most this often while the ping answer is unchanged
KEEP = object()         # mounts_fn result meaning "not read this round: keep the last view"


def no_console() -> dict:
    """Popen kwargs so a child of pythonw never shows a console window (see
    docs/conceptos/consola-heredada-en-windows.md). CREATE_NO_WINDOW alone is not enough for
    Git's msys ssh.exe, which can allocate its own console: SW_HIDE hides that one too."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}


def rank(level: str) -> int:
    return LEVELS.index(level)


def now_iso(t: float | None = None) -> str:
    return dt.datetime.fromtimestamp(time.time() if t is None else t, dt.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- probes (I/O) ---------------

def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout,
                              encoding="utf-8", errors="replace", **no_console())
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, "", "timeout")


def ssh_target(alias: str) -> str:
    """`mac` is an ssh alias, not a hostname: ask ssh what it would connect to."""
    r = _run(["ssh", "-G", alias], 10)
    m = re.search(r"^hostname (\S+)$", r.stdout, re.M)
    return m.group(1) if m else alias


def ping_ok(host: str) -> bool:
    # Windows ping exits 0 on "Destination host unreachable"; only a reply carries TTL=.
    if os.name == "nt":
        r = _run(["ping", "-n", "1", "-w", "1500", host], 5)
    else:
        r = _run(["ping", "-c", "1", "-W", "2", host], 5)
    return "ttl=" in r.stdout.lower()


# Prompt 21 §2: `ready` means claude can work, not only that tmux exists. After a reboot the PC's
# watch is up before the owner logs in on the Mac; tmux may exist while the keychain is still
# locked. `tmux run-shell` runs in the tmux server's (GUI) security session, the one that holds
# the keychain; a plain ssh session always reads "loggedIn": false. 0.5 s, no window, no files.
READY_CMD = ("tmux -L enjambre has-session -t enjambre || exit 1; "
             "n=$(tmux -L enjambre run-shell "
             "'PATH=$HOME/.local/bin:/opt/homebrew/bin:$PATH claude auth status 2>&1 "
             "| grep -cE \"\\\"loggedIn\\\": *true\"'); [ \"$n\" = 1 ] || exit 3")   # compact JSON too


def probe(alias: str) -> str:
    """One ssh call answers two levels: rc 0 = tmux session AND claude logged in from it (ready),
    rc 1 = ssh but no session, rc 3 = session but claude not logged in (keychain locked: ssh),
    rc 255 = ssh itself failed, so fall back to ping."""
    r = _run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", alias, READY_CMD], 15)
    if r.returncode == 0:
        return "ready"
    if r.returncode not in (255, 124):
        return "ssh"
    return "ping" if ping_ok(ssh_target(alias)) else "down"


class GatedProbe:
    """`watch` probe with a cheap and an expensive half. ping every round; the full `probe`
    (ssh + tmux, plus the mounts read) only when the ping answer flips or FULL_PROBE_S passed
    since the last full one, or every round while the ping answers but the node is not yet
    `ready` (a Mac after a reboot: ping, then sshd, then the GUI login; prompt 21 §2).
    In between, the last full level stands. ping_fn, full_fn, target_fn
    and clock are injectable for tests."""

    def __init__(self, full_fn=None, ping_fn=None, target_fn=None, clock=time.time,
                 every: float = FULL_PROBE_S):
        self.full_fn, self.ping_fn = full_fn or probe, ping_fn or ping_ok
        self.target_fn, self.clock, self.every = target_fn or ssh_target, clock, every
        self.targets: dict[str, str] = {}   # `ssh -G` is itself an ssh.exe: resolve once
        self.last: dict[str, dict] = {}
        self.full_now: set[str] = set()     # hosts that got a full probe in the current round

    def __call__(self, alias: str) -> str:
        if alias not in self.targets:
            self.targets[alias] = self.target_fn(alias)
        t, ping = self.clock(), self.ping_fn(self.targets[alias])
        prev = self.last.get(alias)
        half_up = ping and prev is not None and prev["level"] != "ready"   # booting / at the login
        if prev is None or ping != prev["ping"] or half_up or t - prev["full_t"] >= self.every:
            level = self.full_fn(alias)
            self.last[alias] = {"ping": ping, "full_t": t, "level": level}
            self.full_now.add(alias)
            return level
        self.full_now.discard(alias)
        return prev["level"]

    def mounts(self, alias: str, read_fn=None):
        """read_mounts only on full rounds; otherwise KEEP (tick keeps the previous view)."""
        if alias not in self.full_now:
            return KEEP
        return (read_fn or read_mounts)(alias)

    def busy(self, alias: str, read_fn=None):
        """read_busy only on full rounds, like mounts. `run` reads the marker live anyway."""
        if alias not in self.full_now:
            return KEEP
        return (read_fn or read_busy)(alias)


def read_busy(alias: str) -> dict | None:
    """The node's busy marker (busy.py), with "alive" checked on the node. None = no marker."""
    import busy as B
    r = _run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", alias, B.READ_CMD], 15)
    return B.parse(r.stdout) if r.returncode == 0 else None


def read_mounts(alias: str) -> dict | None:
    """The node's own report of its SMB mounts: ~/.enjambre/mounts.json, written every 60 s by
    smb-mount.sh. A separate ssh call that only cats a local file, so a hung SMB mount can never
    stall `probe` (an ls on a dead mount blocks; cat on ~/.enjambre does not). None if the node
    has no such file (no shares configured) or it is unreadable."""
    r = _run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", alias,
              "cat ~/.enjambre/mounts.json"], 15)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def notify(title: str, body: str) -> None:
    """Windows toast through PowerShell's WinRT bindings: no module to install."""
    if os.name != "nt":
        return
    esc = lambda s: s.replace("'", "''")  # noqa: E731
    ps = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null;"
        "$x = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        f"$t = $x.GetElementsByTagName('text'); $t.Item(0).AppendChild($x.CreateTextNode('{esc(title)}')) > $null;"
        f"$t.Item(1).AppendChild($x.CreateTextNode('{esc(body)}')) > $null;"
        "$n = [Windows.UI.Notifications.ToastNotification]::new($x);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe').Show($n)"
    )
    _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], 20)


# ---------------------------------------------------------------- state (pure) ---------------

def step(prev: dict | None, observed: str, t: float) -> tuple[dict, bool]:
    """Debounced transition. Going up is immediate; going down needs DROP_AFTER observations
    in a row, so one lost packet does not page anybody. Returns (new_state, level_changed)."""
    if prev is None:
        return {"level": observed, "since": now_iso(t), "checked": now_iso(t),
                "observed": observed, "lower_count": 0}, True
    s = dict(prev, checked=now_iso(t), observed=observed)
    if rank(observed) >= rank(prev["level"]):
        s["lower_count"] = 0
        if observed != prev["level"]:
            s.update(level=observed, since=now_iso(t))
            return s, True
        return s, False
    s["lower_count"] = prev.get("lower_count", 0) + 1
    if s["lower_count"] >= DROP_AFTER:
        s.update(level=observed, since=now_iso(t), lower_count=0)
        return s, True
    return s, False


def mounts_view(raw: dict | None, t: float, max_age: float = MOUNTS_STALE_S) -> dict | None:
    """What nodes.json stores under nodes.<n>.mounts: {checked_at, stale, mounts: [{name, ok,
    state, path}]}. A report older than max_age means smb-mount.sh stopped running: every mount
    is then ok=False, state="stale" (unknown is not alive)."""
    if not raw:
        return None
    try:
        checked = dt.datetime.fromisoformat(raw["checked_at"].replace("Z", "+00:00")).timestamp()
        stale = t - checked > max_age
    except (KeyError, ValueError, AttributeError):
        stale = True
    return {"checked_at": raw.get("checked_at"), "stale": stale,
            "mounts": [{"name": m.get("name"), "ok": bool(m.get("ok")) and not stale,
                        "state": "stale" if stale else m.get("state"), "path": m.get("path")}
                       for m in raw.get("mounts", [])]}


def mounts_changed(old: dict | None, new: dict | None) -> list[tuple[str, bool]]:
    """(share, ok) for every mount whose ok flipped since the last round. A share seen for the
    first time is not a change (same rule as levels: the first observation is not announced)."""
    if not old or not new:
        return []
    before = {m["name"]: m["ok"] for m in old.get("mounts", [])}
    return [(m["name"], m["ok"]) for m in new.get("mounts", [])
            if m["name"] in before and before[m["name"]] != m["ok"]]


def message(node: str, old: str | None, new: str) -> tuple[str, str]:
    if new == "ready":
        return f"{node} disponible", "Acepta trabajos (ssh + tmux con llavero)."
    if old == "ready" or old is None:
        return f"{node} caída", f"Nivel ahora: {new}. Los trabajos nuevos se encolan."
    return f"{node}: {new}", f"Antes: {old}."


def write_json_atomic(path: Path, data: dict) -> None:
    """Write to a temp file and os.replace it. On Windows a reader holding the file open makes
    replace fail with PermissionError for a moment: retry briefly instead of crashing."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    for i in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1 * (i + 1))
    os.replace(tmp, path)


def read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def fresh_level(state: dict, node: str, t: float, max_age: float = FRESH_S) -> str | None:
    """The level from nodes.json if `watch` wrote it recently, else None (caller probes live)."""
    n = state.get("nodes", {}).get(node)
    if not n:
        return None
    try:
        age = t - dt.datetime.fromisoformat(n["checked"]).timestamp()
    except (KeyError, ValueError):
        return None
    return n["level"] if age <= max_age else None


# ---------------------------------------------------------------- queue (filesystem) ---------
# One folder per job. Jobs move queue/ -> running/ -> done/; nothing is ever deleted.

class QueueError(Exception):
    pass


def qdirs(root: Path) -> dict[str, Path]:
    return {k: root / k for k in ("queue", "running", "done")}


def enqueue(root: Path, tid: str, task_text: str, meta: dict) -> Path:
    d = qdirs(root)
    for k in ("queue", "running"):
        if (d[k] / tid).exists():
            raise QueueError(f"task id {tid} is already in {k}/")
    job = d["queue"] / tid
    job.mkdir(parents=True)
    # Snapshot the task text: the job runs what was asked, even if the file is edited later.
    (job / f"TASK-{tid}.md").write_text(task_text, encoding="utf-8", newline="\n")
    write_json_atomic(job / "job.json", dict(meta, id=tid, enqueued=now_iso()))
    return job


def queued(root: Path, node: str | None = None) -> list[dict]:
    out = []
    qd = qdirs(root)["queue"]
    if not qd.is_dir():
        return out
    for job in qd.iterdir():
        meta = read_state(job / "job.json")
        if meta and (node is None or meta.get("node") == node):
            out.append(meta)
    return sorted(out, key=lambda m: m.get("enqueued", ""))


def running_count(root: Path, node: str) -> int:
    rd = qdirs(root)["running"]
    if not rd.is_dir():
        return 0
    return sum(1 for j in rd.iterdir() if read_state(j / "job.json").get("node") == node)


def move(root: Path, tid: str, src: str, dst: str) -> Path:
    d = qdirs(root)
    d[dst].mkdir(parents=True, exist_ok=True)
    target = d[dst] / tid
    if target.exists():  # a re-run of the same id: keep both, never overwrite
        target = d[dst] / f"{tid}.{int(time.time())}"
    shutil.move(str(d[src] / tid), str(target))
    return target


def pick(root: Path, levels: dict[str, str], max_jobs: dict[str, int], admit_fn=None) -> list[dict]:
    """Jobs that can start now: node ready and under its max_jobs, oldest first.
    With admit_fn (prompt 20, the scheduler): highest priority first, then FIFO, and each job asks
    admit_fn(meta, node, admitted_this_round, cap) -> (ok, state, reason); cap = max_jobs[node]. A job that waits gets its
    state and reason written into its job.json (only when they change), so `status` and the visor
    say why: "esperando la ventana nocturna: abre hoy a las 00:00"."""
    out = []
    for node, level in levels.items():
        if level != "ready":
            continue
        if admit_fn is None:
            free = max_jobs.get(node, 1) - running_count(root, node)
            out.extend(queued(root, node)[:max(free, 0)])
            continue
        import scheduler as S
        taken: list = []
        for meta in S.order(queued(root, node)):
            ok, state, reason = admit_fn(meta, node, taken, max_jobs.get(node, 1))
            if ok:
                taken.append(meta)
                out.append(dict(meta, state="running", reason=reason))
            elif (meta.get("state"), meta.get("reason")) != (state, reason):
                path = qdirs(root)["queue"] / meta["id"] / "job.json"
                since = meta.get("state_since") if meta.get("state") == state else now_iso()
                write_json_atomic(path, dict(meta, state=state, reason=reason, state_since=since))
    return out


# ---------------------------------------------------------------- fallback alias -------------
# prompt 21 §3: with the WORK VPN up, `mac` (LAN IPv4) can be routed into the tunnel while `mac6`
# (IPv6 link-local) still answers. nodes.toml declares `host_alt`; watch is the only writer of
# state/route.json; everything that ssh's to a node reads it through apply_route / job_host.
# Precedence: ENJAMBRE_HOST_<NODE> > state/route.json > nodes.toml `host`.

HERE = Path(__file__).resolve().parent


def nodes_path(root: Path = HERE) -> Path:
    """nodes.toml; a fresh clone of the public repo has only nodes.example.toml: read that until
    the owner copies it (reads only: nodo.py add/rm always write nodes.toml)."""
    p = root / "nodes.toml"
    return p if p.exists() else root / "nodes.example.toml"
ROUTE_JSON = HERE / "state" / "route.json"
ALT_EVERY_S = 60   # while on the alternate, re-check it at most once a minute
_ALT_TRIED: dict = {}   # node -> last time watch tried its host_alt (in-process only)


def read_route() -> dict:
    return read_state(ROUTE_JSON) or {}


def apply_route(nodes: dict, route: dict | None = None) -> dict:
    """In place: a node with an active entry in state/route.json gets host = its host_alt; one
    without gets its primary back. A node forced by ENJAMBRE_HOST_<NODE> is left alone. Cheap
    (one small file), so long loops call it every cycle."""
    route = read_route() if route is None else route
    for name, cfg in nodes.items():
        if cfg.get("host") == "local" or (cfg.get("host_override") and not cfg.get("host_fallback")):
            continue
        prim = cfg.setdefault("host_primary", cfg["host"])
        alt = (route.get(name) or {}).get("alias")
        if alt and alt == cfg.get("host_alt"):
            cfg.update(host=alt, host_override=True, host_fallback=True)
        elif cfg.get("host_fallback"):
            cfg["host"] = prim
            cfg.pop("host_override", None)
            cfg.pop("host_fallback", None)
    return nodes


def job_host(meta: dict) -> str:
    """The alias to reach a job's node NOW. job.json stores the host it was launched through,
    which may be the alias that just stopped answering."""
    import tomllib
    node, host = meta.get("node"), meta["host"]
    env = os.environ.get(f"ENJAMBRE_HOST_{str(node).upper()}")
    if env and re.fullmatch(r"[A-Za-z0-9._-]+", env):
        return env
    try:
        with open(nodes_path(), "rb") as f:
            cfg = dict(tomllib.load(f)["nodes"][node])
    except (OSError, KeyError, ValueError):
        return host
    if host not in (cfg.get("host"), cfg.get("host_alt")):
        return host          # launched through something else (an env override): keep it
    return apply_route({node: cfg})[node]["host"]


def route_round(nodes: dict, state: dict, t: float, probe_fn, notify_fn, log,
                route: dict | None = None, every: float = ALT_EVERY_S) -> dict:
    """watch, after tick: for nodes with `host_alt`, decide which alias to use and write
    state/route.json when that changes. `nodes` are the PRIMARY aliases (watch keeps probing
    them, so it notices when the primary is back). The node's entry in `state` gets the level
    reached through the alternate plus `via`, so dispatch and the visor see a usable node.
    Returns the new route."""
    route = dict(read_route() if route is None else route)
    before = json.dumps(route, sort_keys=True)
    for name, cfg in nodes.items():
        alt, st = cfg.get("host_alt"), (state.get("nodes") or {}).get(name)
        if not alt or st is None:
            continue
        cur = route.get(name)
        if st["level"] in ("ssh", "ready"):          # the primary answers: use it
            if cur:
                route.pop(name)
                log.info("node %s: back on %s (was via %s)", name, cfg["host"], alt)
                notify_fn(f"enjambre: {name} vuelve por {cfg['host']}", f"ya no hace falta {alt}")
            continue
        if not cur and t - _ALT_TRIED.get(name, 0) < every:
            continue                                 # a node that is really off: one alt try a minute
        if cur and t - cur.get("checked_ts", 0) < every:
            lvl = cur.get("level")
        else:
            _ALT_TRIED[name] = t
            lvl = probe_fn(alt)
            if lvl in ("ssh", "ready"):
                if not cur:
                    log.info("node %s: %s %s, %s %s -> using %s", name, cfg["host"], st["level"], alt, lvl, alt)
                    notify_fn(f"enjambre: {name} por {alt}",
                              f"{cfg['host']} no responde (¿VPN?); uso {alt}")
                route[name] = {"alias": alt, "primary": cfg["host"],
                               "since": (cur or {}).get("since") or now_iso(t), "checked_ts": t,
                               "level": lvl, "reason": f"{cfg['host']} no responde ({st['level']}); {alt} sí"}
            elif cur:
                route.pop(name)
                log.info("node %s: %s stopped answering too (%s)", name, alt, lvl)
        if name in route:
            st.update(primary_level=st["level"], level=route[name]["level"], via=alt,
                      via_reason=route[name]["reason"])
    if json.dumps(route, sort_keys=True) != before:
        write_json_atomic(ROUTE_JSON, route)
    return route


# ---------------------------------------------------------------- single instance -------------

class InstanceLock:
    """Held for the whole life of `watch`. The OS releases it if the process dies, so a stale
    pid file can never block the next start."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False


def acquire_or_wait(lock: "InstanceLock", wait_s: float, every: float = 5.0, sleep=time.sleep) -> bool:
    """A logon-task instance may start while the `--boot` one is mid-round and has not yet seen
    the login; it hands over at its next check. Exiting at once would leave nothing running
    (exit 0 does not trigger the task's RestartCount), so retry for wait_s."""
    end = time.time() + wait_s
    while True:
        if lock.acquire():
            return True
        if time.time() >= end:
            return False
        sleep(every)


def console_user() -> str:
    """Windows: the user signed in on the physical console ('' at the sign-in screen, while
    locked-out of a session, or off Windows). A `--boot` loop (AtStartup task, S4U, session 0)
    asks this to know when to hand over to the logon task, which has the desktop for toasts."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes
    sid = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
    if sid == 0xFFFFFFFF:
        return ""
    wts = ctypes.windll.wtsapi32
    buf, n = wintypes.LPWSTR(), wintypes.DWORD()
    if not wts.WTSQuerySessionInformationW(None, sid, 5, ctypes.byref(buf), ctypes.byref(n)):  # 5 = WTSUserName
        return ""
    try:
        return buf.value or ""
    finally:
        wts.WTSFreeMemory(buf)


def owner_logged_on() -> bool:
    import getpass
    u = console_user()
    return bool(u) and u.lower() == getpass.getuser().lower()


def python_for_children() -> str:
    """Under pythonw, sys.executable is pythonw.exe; children that print JSON need python.exe."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


# ---------------------------------------------------------------- one watch round -------------

def tick(nodes: dict, prev: dict, probe_fn, t: float, notify_fn, log, mounts_fn=None,
         busy_fn=None) -> dict:
    """Probe every node once and return the new nodes.json content. Notifies and logs only on
    a level change; the first observation after a (re)start is logged but not notified.
    With mounts_fn, nodes reachable over ssh also get `mounts` (see mounts_view); a share whose
    ok flips is logged and notified like a level change. With busy_fn, they also get `busy`
    (the node's marker, busy.py), kept from the last full round when busy_fn returns KEEP."""
    out = {"updated": now_iso(t), "nodes": {}}
    for name, cfg in nodes.items():
        old = prev.get("nodes", {}).get(name)
        if old and old.get("down_by"):   # live.mark_down: one more lower observation, not a level of its own
            prior = old.get("prior_level")
            since = old.get("prior_since") or old.get("since")
            old = {k: v for k, v in old.items() if k not in ("down_by", "down_reason", "prior_level", "prior_since")}
            old = dict(old, level=prior, since=since, lower_count=DROP_AFTER - 1) if prior else None
        if old and old.get("via"):   # route_round raised it through host_alt: debounce the primary's own level
            old = {k: v for k, v in old.items() if k not in ("via", "via_reason", "primary_level")}
            old["level"] = prev["nodes"][name].get("primary_level", old["level"])
        s, changed = step(old, probe_fn(cfg["host"]), t)
        s.pop("mounts", None)
        s.pop("busy", None)
        out["nodes"][name] = s
        if busy_fn and cfg["host"] != "local" and rank(s["level"]) >= rank("ssh"):
            b = busy_fn(cfg["host"])
            b = (old or {}).get("busy") if b is KEEP else b
            if b:
                s["busy"] = b
        if changed:
            log.info("node %s: %s -> %s", name, old["level"] if old else None, s["level"])
            if old is not None:
                notify_fn(*message(name, old["level"], s["level"]))
        if mounts_fn and cfg["host"] != "local" and rank(s["level"]) >= rank("ssh"):
            raw = mounts_fn(cfg["host"])
            if raw is KEEP:  # not read this round (GatedProbe): the last view stands, unjudged
                if (old or {}).get("mounts"):
                    s["mounts"] = old["mounts"]
                continue
            mv = mounts_view(raw, t)
            if mv is not None:
                s["mounts"] = mv
                for share, ok in mounts_changed((old or {}).get("mounts"), mv):
                    log.info("node %s: share %s %s", name, share, "up" if ok else "DOWN")
                    notify_fn(f"{name}: {share}", "Montado de nuevo." if ok else
                              "Desmontado o colgado: la Mac no ve esa carpeta de Windows.")
    return out
