"""`enjambre nodo add|doctor|rm` (prompt 19 §2.5): put a machine in the swarm without editing
nodes.toml by hand, copying the collector by hand and hoping.

add     1 reach it (`ssh <host> true`), measure RAM / cores / arch there and append its table to
          nodes.toml with those numbers
        2 copy tools/visor/collect.py to ~/.enjambre/visor/ on the node
        3 install the periodic snapshot launcher (macOS LaunchAgent, from config/dev.enjambre.visor.plist)
        4 wait for the first snapshot (<= 60 s) and check its shape; on failure, say which step
doctor  the same four checks, writing nothing
rm      undo add: launcher, its snapshot file, the nodes.toml table. Only for nodes `add` wrote
        (the table carries a marker line); a hand-written node is never touched.

Windows only as `--host local` (build.py collects in-process: nothing to copy or install).
Linux needs a systemd-user timer that does not exist yet, so `add` refuses before writing.
Every remote step goes through one `run` function, so tests replace ssh with a fake."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import presence as P

HERE = Path(__file__).resolve().parent
NODES_TOML = HERE / "nodes.toml"
COLLECT = HERE / "tools" / "visor" / "collect.py"
PLIST = HERE / "config" / "dev.enjambre.visor.plist"
MARK = "# added by `enjambre nodo add`"
NAME_RE = re.compile(r"[a-z][a-z0-9_-]{0,30}")
SNAP_KEYS = ("schema", "node", "collected", "os", "sessions", "memory", "disks")
WAIT_S = 60


def default_run(cmd: list, stdin: str | None = None, timeout: float = 20) -> tuple[int, str, str]:
    try:   # bytes, not text=True: on Windows text mode turns \n into \r\n on the way to the node
        r = subprocess.run(cmd, input=None if stdin is None else stdin.encode("utf-8"),
                           capture_output=True, timeout=timeout, **P.no_console())
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout:.0f} s"
    except OSError as e:
        return 127, "", str(e)


def ssh(host: str, cmd: str) -> list:
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, cmd]


def label_of(name: str) -> str:
    return f"dev.enjambre.visor.{name}"


def snapshot_path(name: str) -> str:
    return f"~/.enjambre/visor/snapshot-{name}.json"


# ------------------------------------------------------------------ pure parts (tested)
def parse_measure(os_: str, out: str) -> dict:
    """RAM / cores / arch from the probe each OS answers. Unreadable -> None, never a guess."""
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    try:
        if os_ == "macos":        # sysctl -n hw.memsize hw.ncpu hw.machine
            return {"ram_gb": round(int(lines[0]) / 2**30), "cores": int(lines[1]), "arch": lines[2]}
        if os_ == "linux":        # uname -m; nproc; MemTotal kB
            return {"arch": lines[0], "cores": int(lines[1]), "ram_gb": round(int(lines[2]) / 2**20)}
    except (IndexError, ValueError):
        pass
    return {"ram_gb": None, "cores": None, "arch": None}


def measure_local() -> dict:
    import ctypes
    import platform

    class MS(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong), ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                    ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong), ("ae", ctypes.c_ulonglong)]
    ram = None
    if sys.platform == "win32":
        m = MS()
        m.len = ctypes.sizeof(MS)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            ram = round(m.total / 2**30)
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(platform.machine().lower(), platform.machine().lower())
    return {"ram_gb": ram, "cores": os.cpu_count(), "arch": arch or None}


def toml_table(name: str, host: str, os_: str, label: str, m: dict, runnable: bool, today: str) -> str:
    q = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731  (TOML basic strings = JSON strings)
    rows = [f"{MARK} {today}; `enjambre nodo rm {name}` removes this table",
            f"[nodes.{name}]", f"label = {q(label)}", f"host = {q(host)}", f"os = {q(os_)}"]
    for k in ("arch", "ram_gb", "cores"):
        if m.get(k) is not None:                     # unmeasured -> left out, not invented
            rows.append(f"{k} = {q(m[k])}")
    rows += ["max_jobs = 1", 'workers = ["claude"]', "tags = []", "owned = true",
             f"runnable = {'true' if runnable else 'false'}"]
    if host != "local":
        rows.append(f"visor_snapshot = {q(snapshot_path(name))}")
    return "\n".join(rows) + "\n"


def add_table(text: str, table: str) -> str:
    return text.rstrip("\n") + "\n\n" + table


def remove_table(text: str, name: str) -> str | None:
    """The text without the table `add` wrote for `name` (marker line through the line before the
    next blank line or table). None when there is no marked table for it."""
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.startswith(MARK) and i + 1 < len(lines) and lines[i + 1].strip() == f"[nodes.{name}]":
            j = i + 2
            while j < len(lines) and lines[j].strip() and not lines[j].startswith("["):
                j += 1
            start = i - 1 if i > 0 and not lines[i - 1].strip() else i   # the blank line add put before
            return "\n".join(lines[:start] + lines[j:])
    return None


def plist_for(name: str, template: str) -> str:
    """The 5-minute collector LaunchAgent for one more node on a Mac: own label, own --node, own
    snapshot file, no --models (the model manager belongs to the node that runs it)."""
    t = re.sub(r"<!--.*?-->\n?", "", template, flags=re.S)
    t = t.replace("<string>dev.enjambre.visor</string>", f"<string>{label_of(name)}</string>")
    t = re.sub(r"<string>--node</string><string>[^<]*</string><string>--models</string>",
               f"<string>--node</string><string>{name}</string><string>--out</string>"
               f"<string>__HOME__/.enjambre/visor/snapshot-{name}.json</string>", t)
    t = t.replace("launchagent-visor.log", f"launchagent-visor-{name}.log")
    if label_of(name) not in t or f"snapshot-{name}.json" not in t:
        raise ValueError("config/dev.enjambre.visor.plist changed shape; nodo.plist_for needs updating")
    return t


def check_snapshot(raw: str, name: str, now: float, max_age: float) -> str | None:
    """None when the snapshot is good; else what is wrong with it."""
    try:
        d = json.loads(raw)
    except ValueError:
        return "not JSON"
    missing = [k for k in SNAP_KEYS if k not in d]
    if missing:
        return "missing keys: " + ", ".join(missing)
    if d["node"] != name:
        return f"node is {d['node']!r}, expected {name!r}"
    if not isinstance(d["collected"], (int, float)) or now - d["collected"] > max_age:
        return f"older than {max_age:.0f} s"
    return None


# ------------------------------------------------------------------ steps
class Fail(Exception):
    def __init__(self, step: int, msg: str):
        super().__init__(f"paso {step}: {msg}")
        self.step = step


def step1_reach(host: str, os_: str, run) -> dict:
    if host == "local":
        return measure_local()
    rc, _, err = run(ssh(host, "true"), timeout=10)
    if rc != 0:
        raise Fail(1, f"`ssh {host} true` falló ({err.strip()[-160:] or rc})")
    probe = {"macos": "sysctl -n hw.memsize hw.ncpu hw.machine",
             "linux": "uname -m; nproc; awk '/MemTotal/{print $2}' /proc/meminfo"}[os_]
    rc, out, err = run(ssh(host, probe), timeout=10)
    return parse_measure(os_, out if rc == 0 else "")


def step2_ship(host: str, run, write: bool) -> str:
    local = hashlib.sha256(COLLECT.read_bytes()).hexdigest()
    if write:
        rc, _, err = run(ssh(host, "mkdir -p ~/.enjambre/visor ~/enjambre-logs && "
                                   "cat > ~/.enjambre/visor/collect.py.tmp && "
                                   "mv ~/.enjambre/visor/collect.py.tmp ~/.enjambre/visor/collect.py"),
                         stdin=COLLECT.read_text(encoding="utf-8"))
        if rc != 0:
            raise Fail(2, f"no se pudo copiar collect.py ({err.strip()[-160:]})")
    rc, out, _ = run(ssh(host, "shasum -a 256 ~/.enjambre/visor/collect.py 2>/dev/null || "
                               "sha256sum ~/.enjambre/visor/collect.py"))
    remote = out.split()[0] if rc == 0 and out.split() else None
    if remote != local:
        raise Fail(2, "collect.py en el nodo " + ("no existe" if remote is None else "es otra versión"))
    return local[:12]


def step3_launcher(name: str, host: str, run, write: bool) -> str:
    lab = label_of(name)
    dst = f"~/Library/LaunchAgents/{lab}.plist"
    if write:
        rc, out, _ = run(ssh(host, f"test -e {dst} && echo exists || true"))
        if "exists" in out:
            raise Fail(3, f"{dst} ya existe en el nodo; `enjambre nodo rm {name}` primero")
        rc, _, err = run(ssh(host, f'mkdir -p ~/Library/LaunchAgents && sed "s|__HOME__|$HOME|g" > {dst} && '
                                   f'launchctl bootstrap gui/$(id -u) {dst}'),
                         stdin=plist_for(name, PLIST.read_text(encoding="utf-8")))
        if rc != 0:
            raise Fail(3, f"launchctl bootstrap falló ({err.strip()[-160:]})")
    # `launchctl print` exits 0 only for a loaded service; its first `state =` line is the job's
    rc, out, _ = run(ssh(host, f"launchctl print gui/$(id -u)/{lab} | grep -E 'state =|last exit' | head -2"))
    if rc != 0 or "state =" not in out:
        raise Fail(3, f"el LaunchAgent {lab} no está cargado")
    return " · ".join(x.strip() for x in out.splitlines())


def step4_snapshot(name: str, host: str, run, wait_s: float, max_age: float, sleep=time.sleep,
                   clock=time.time) -> str:
    end, why = clock() + wait_s, "no llegó"
    while True:
        rc, out, _ = run(ssh(host, "cat " + snapshot_path(name)))
        if rc == 0:
            why = check_snapshot(out, name, clock(), max_age)
            if why is None:
                return f"{len(out)} bytes, esquema ok"
        if clock() >= end:
            raise Fail(4, f"el primer snapshot {why} en {wait_s:.0f} s")
        sleep(5)


# ------------------------------------------------------------------ commands
def add(name: str, host: str, os_: str, label: str | None, runnable: bool = False, run=default_run,
        toml: Path = NODES_TOML, say=print, sleep=time.sleep, clock=time.time) -> int:
    if not NAME_RE.fullmatch(name):
        say(f"nombre inválido {name!r}: minúsculas, dígitos, - y _")
        return 2
    text = toml.read_text(encoding="utf-8")
    if re.search(rf"^\[nodes\.{re.escape(name)}\]", text, re.M):
        say(f"{name} ya está en nodes.toml")
        return 2
    if host == "local" and os_ != "windows" or host != "local" and os_ not in ("macos", "linux"):
        say("combinación no soportada: Windows solo como --host local; macOS/Linux por ssh")
        return 2
    if os_ == "linux":
        say("paso 3: Linux necesita un timer de systemd-user que aún no existe (ROADMAP). No se escribió nada.")
        return 2
    try:
        m = step1_reach(host, os_, run)
        say(f"1 ok  medido: {m}")
        toml.write_text(add_table(text, toml_table(name, host, os_, label or name, m, runnable,
                                                   dt.date.today().isoformat())), encoding="utf-8", newline="\n")
        say(f"1 ok  nodes.toml: [nodes.{name}] añadido")
        if host == "local":
            say("2-4 en el propio PC: build.py lo recoge en proceso, no hay nada que copiar ni instalar")
            return 0
        say("2 ok  collect.py " + step2_ship(host, run, True))
        say("3 ok  " + step3_launcher(name, host, run, True))
        say("4 ok  " + step4_snapshot(name, host, run, WAIT_S, WAIT_S + 30, sleep, clock))
        say("build.py lo muestra en el siguiente ciclo (5 min). `live` y `watch` leen nodes.toml al "
            "arrancar: `enjambre live --stop` / `watch --stop` y sus tareas los relanzan con el nodo nuevo.")
        return 0
    except Fail as e:
        say(str(e) + f"  (lo ya hecho se deshace con `enjambre nodo rm {name}`)")
        return 3


def doctor(name: str, nodes: dict, run=default_run, say=print) -> int:
    cfg = nodes.get(name)
    if cfg is None:
        say(f"{name} no está en nodes.toml")
        return 2
    host, os_ = cfg["host"], cfg.get("os", "macos")
    try:
        m = step1_reach(host, os_, run)
        diff = {k: (cfg.get(k), m[k]) for k in ("ram_gb", "cores", "arch") if m.get(k) is not None and cfg.get(k) != m[k]}
        say(f"1 ok  alcanzable; medido {m}" + (f"; distinto de nodes.toml: {diff}" if diff else ""))
        if host == "local":
            say("2-4 en el propio PC: recogido en proceso")
            return 0
        say("2 ok  collect.py " + step2_ship(host, run, False))
        if cfg.get("visor_snapshot"):           # nodes from `add`; the hand-installed mac has its own label
            say("3 ok  " + step3_launcher(name, host, run, False))
            say("4 ok  " + step4_snapshot(name, host, run, 0, 600))
        else:
            say("3-4 no comprobados: este nodo no lo instaló `nodo add` (lanzador y snapshot propios)")
        return 0
    except Fail as e:
        say(str(e))
        return 3


def rm(name: str, nodes: dict, run=default_run, toml: Path = NODES_TOML, say=print) -> int:
    text = toml.read_text(encoding="utf-8")
    new = remove_table(text, name)
    if new is None:
        say(f"{name}: no hay una tabla escrita por `nodo add` en nodes.toml; no se toca nada")
        return 2
    host = (nodes.get(name) or {}).get("host")
    if host and host != "local":
        lab, dst = label_of(name), f"~/Library/LaunchAgents/{label_of(name)}.plist"
        rc, out, err = run(ssh(host, f"launchctl bootout gui/$(id -u)/{lab} 2>/dev/null; rm -f {dst} "
                                   f"{snapshot_path(name)} ~/enjambre-logs/launchagent-visor-{name}.log; "
                                   f"launchctl print gui/$(id -u)/{lab} >/dev/null 2>&1 && echo still || echo gone"))
        if rc != 0 or "gone" not in out:
            say(f"el LaunchAgent {lab} sigue cargado o el nodo no respondió ({err.strip()[-120:]}); nodes.toml intacto")
            return 3
        say(f"nodo: {lab} descargado y borrado, con su snapshot y su log (collect.py se queda: lo comparten)")
    toml.write_text(new, encoding="utf-8", newline="\n")
    say(f"nodes.toml: [nodes.{name}] quitado")
    return 0
