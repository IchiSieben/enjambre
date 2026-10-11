#!/usr/bin/env python3
"""demo.py — the visor in demo mode (prompt 19 §3): the real page, fed by a SYNTHETIC swarm.

    python enjambre.py visor --demo [OUT_DIR] [--serve]
    python enjambre.py visor --demo --export [OUT_DIR]     # the static public demo

Nothing here reads the real machine into the output: the topology is the real one (a Windows
PC, a Mac mini, a laptop, and the hosting that serves the web copy) but every name, path, number
and job is invented, seeded and repeatable. The synthetic data goes through the SAME code as the
real visor (collect.collect over generated Claude Code transcripts, build.build, live.assemble),
so the demo shows what the visor really does, not a mock-up of it.

Output (default state/visor/demo/): visor.html, live.js, ring.js, demo-fixture.json. The page
carries a clock shim: its "now" is the build time plus the seconds since it was opened, looped
every 2 min, so a static copy never turns "visor dormido" or "lectura vieja". --serve rewrites
live.js every 10 s with fresh synthetic readings (for recording a video).

--export (default state/web-dist/demo/) is the public demo for a shared host: index.html, live.js,
ring.js, demo-fixture.json and an .htaccess, nothing that runs on the server. The clock shim keeps
the static copy "live" forever. Then the publication audit runs over the folder (when its key is
present) and a zip + sha listing are written, like tools/web/build_dist.py does for the web visor.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
import build as B  # noqa: E402
import collect as C  # noqa: E402
import live as LV  # noqa: E402

OUT = REPO / "state" / "visor" / "demo"
WEB_OUT = REPO / "state" / "web-dist" / "demo"
WEB_FILES = ("index.html", "live.js", "ring.js", "demo-fixture.json", ".htaccess")
HTACCESS = """# enjambre demo: static files only (no PHP, no process). Synthetic data, nothing private.
Options -Indexes
DirectoryIndex index.html
<IfModule mod_headers.c>
  Header set X-Content-Type-Options "nosniff"
  Header set Referrer-Policy "strict-origin-when-cross-origin"
  <FilesMatch "\\.(js|json)$">
    Header set Cache-Control "public, max-age=300"
  </FilesMatch>
</IfModule>
"""
SEED = 22
LIMA = C.LIMA

# The real topology, generic names. "hosting" only serves the web copy of the visor: it runs no job.
NODES = {
    "pc": {"label": "PC", "host": "local", "os": "windows", "arch": "x86_64", "ram_gb": 48, "max_jobs": 2},
    "mac": {"label": "Mac mini", "host": "mac", "os": "darwin", "arch": "arm64", "ram_gb": 16, "max_jobs": 2},
    "laptop": {"label": "Laptop", "host": "laptop", "os": "windows", "arch": "x86_64", "ram_gb": 16, "max_jobs": 1},
    "hosting": {"label": "Hosting", "host": "hosting", "os": "linux", "arch": "x86_64", "ram_gb": 2, "max_jobs": 0},
}
# invented projects: (name, nodes where it has sessions, weight)
PROJECTS = [("proyecto-a", ("pc", "mac"), 5), ("tienda-demo", ("pc",), 3), ("atlas-demo", ("pc", "mac"), 3),
            ("radar-demo", ("pc", "laptop"), 2), ("render-lab", ("mac",), 2), ("enjambre", ("pc", "mac", "laptop"), 4)]
MODELS = [("claude-opus-4-1", 2), ("claude-sonnet-4-5", 5), ("claude-haiku-4-5", 2)]
CAUSES = [("failed", 1, "exit 1: ModuleNotFoundError: No module named 'numpy._core'"),
          ("incomplete", 0, "permisos denegados: Bash"),
          ("failed", 255, None),
          ("failed", 2, "exit 2: error: unrecognized arguments: --id"),
          ("incomplete", 0, "presupuesto agotado (tope US$1.00)")]


def iso(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds")


def pick(rnd, pairs):
    return rnd.choices([p[0] for p in pairs], weights=[p[-1] for p in pairs])[0]


# ------------------------------------------------------------------ transcripts -> collect.collect
def write_transcripts(root: Path, node: str, now: float, rnd: random.Random) -> None:
    """Claude Code transcripts in the layout collect.py reads (projects/<dir>/<sid>.jsonl), over the
    last 20 days, so usage, history, heat map and timelines all come out of the real code."""
    mine = [p for p in PROJECTS if node in p[1]]
    if not mine:
        return
    for day in range(20, -1, -1):
        for _ in range(rnd.randint(0, 3 if node == "pc" else 2)):
            name = pick(rnd, mine)
            cwd = f"/srv/proyectos/{name}"
            sid = "%08x-demo-%04x-%04x-%012x" % (rnd.getrandbits(32), rnd.getrandbits(16), rnd.getrandbits(16), rnd.getrandbits(48))
            d = root / "projects" / ("-srv-proyectos-" + name)
            d.mkdir(parents=True, exist_ok=True)
            t = now - day * 86400 - rnd.randint(600, 50000)
            if t > now - 300:
                t = now - 3000
            lines = []
            for turn in range(rnd.randint(3, 14)):
                t += rnd.randint(40, 600)
                lines.append({"type": "user", "timestamp": iso(t), "cwd": cwd, "sessionId": sid,
                              "message": {"role": "user", "content": "paso %d" % (turn + 1)}})
                for k in range(rnd.randint(1, 4)):
                    t += rnd.randint(5, 90)
                    lines.append({"type": "assistant", "timestamp": iso(t), "cwd": cwd, "sessionId": sid,
                                  "requestId": "req_demo_%x" % rnd.getrandbits(48),
                                  "message": {"id": "msg_demo_%x" % rnd.getrandbits(64), "model": pick(rnd, MODELS),
                                              "usage": {"input_tokens": rnd.randint(2, 40),
                                                        "output_tokens": rnd.randint(80, 2500),
                                                        "cache_creation_input_tokens": rnd.randint(0, 30000),
                                                        "cache_read_input_tokens": rnd.randint(5000, 160000)}}})
            with open(d / (sid + ".jsonl"), "w", encoding="utf-8") as f:
                f.write("\n".join(json.dumps(x) for x in lines) + "\n")


def machine(node: str, rnd: random.Random) -> dict:
    """What collect.py reads from the machine itself, invented (the real one is never read in)."""
    disks = {"pc": [("C:\\", 931, 402), ("D:\\", 1863, 1210)], "mac": [("/", 494, 121), ("/Volumes/Datos", 2000, 1084)],
             "laptop": [("C:\\", 476, 188)], "hosting": [("/", 50, 31)]}[node]
    mem = {"pc": (48, 38), "mac": (16, 27), "laptop": (16, 44), "hosting": (2, 61)}[node]
    return {"disks": [{"mount": m, "total_gb": t, "free_gb": f} for m, t, f in disks],
            "memory": {"total_gb": mem[0], "free_pct": mem[1]},
            "mounts": [{"share": "Compartido-PC", "ok": True}] if node == "mac" else [],
            "tmux": ["enjambre", "demo-job"] if node == "mac" else [],
            "detached": [], "models": None, "errors": [], "home_name": None,
            "os": {"pc": "windows", "mac": "darwin", "laptop": "windows", "hosting": "linux"}[node]}


def snapshots(now: float, tmp: Path, rnd: random.Random) -> dict:
    out = {}
    for node in NODES:
        root = tmp / node
        root.mkdir(parents=True, exist_ok=True)
        write_transcripts(root, node, now, rnd)
        s = C.collect(node, root=str(root), cursor_path=str(root / "cursor.json"), now=now,
                      history_path=str(root / "history.json"))
        s.update(machine(node, rnd))
        s["python"] = "3.12"
        s["sessions"] = []                 # the live sessions come from live.json, below
        s["quota"] = {"five_pct": 38, "week_pct": 52, "ts": now - 120} if node == "pc" else None
        s["quota_last"] = None
        s["thresholds"] = DEMO_THRESHOLDS
        out[node] = s
    return out


DEMO_THRESHOLDS = {"visor-disk-amber": 80, "visor-disk-red": 92, "visor-mem-amber": 85, "visor-mem-red": 95,
                   "visor-quota-5h-amber": 70, "quota-5h": 90, "visor-quota-7d-amber": 70, "visor-quota-7d-red": 90,
                   "visor-reposo-pressure-s": 300, "visor-reposo-thermal-s": 120, "visor-reposo-swap-gb": 1,
                   "visor-reposo-swap-window-s": 600}


# ------------------------------------------------------------------ ledger, presence, queue
def ledger(now: float, rnd: random.Random) -> list:
    rows = []
    workers = [("claude", "pc", 3), ("claude", "mac", 5), ("blender", "mac", 2), ("script", "mac", 2),
               ("script", "laptop", 1), ("local", "mac", 1)]
    for i in range(60):
        w, node, _ = rnd.choices(workers, weights=[x[2] for x in workers])[0]
        start = now - rnd.randint(600, 9 * 86400)
        dur = rnd.randint(40, 2400)
        repo = pick(rnd, [(p[0], p[2]) for p in PROJECTS])
        r = {"id": "%s-%03d" % (repo.split("-")[0], i), "node": node, "worker": w, "repo": repo,
             "branch": "%s/%s-%03d" % (node, repo, i), "start": iso(start), "end": iso(start + dur),
             "duration_s": dur, "pushed": rnd.random() < 0.7, "exit": 0, "status": "ok",
             "cost_usd": round(rnd.uniform(0.02, 1.4), 4) if w == "claude" else 0.0,
             "kind": {"claude": rnd.choice(["tests", "agent", "docs"]), "blender": "render",
                      "script": "script", "local": "transcripcion"}[w],
             "routed_by": rnd.choice(["table", "explicit"]), "priority": rnd.choice(["normal", "normal", "baja", "alta"])}
        if w == "claude":
            r.update(turns=rnd.randint(3, 30), budget_usd=1.0,
                     usage={"input_tokens": 12, "output_tokens": rnd.randint(500, 9000),
                            "cache_creation_input_tokens": rnd.randint(1000, 40000),
                            "cache_read_input_tokens": rnd.randint(20000, 400000)})
        if rnd.random() < 0.12:
            st, code, why = rnd.choice(CAUSES)
            r.update(status=st, exit=code, reason=why)
        rows.append(r)
    rows.sort(key=lambda r: r["start"])
    return rows


def presence(now: float) -> dict:
    p = {"pc": {"level": "local", "fresh": True}}
    for n, lvl in (("mac", "ready"), ("laptop", "ssh"), ("hosting", "ping")):
        p[n] = {"level": lvl, "last_level": lvl, "since": iso(now - 3 * 3600), "fresh": True, "updated_age_s": 12}
    return p


# ------------------------------------------------------------------ live: telemetry, jobs, ring
def tele(node: str, t: float, rnd: random.Random, phase: float = 0.0) -> dict:
    w = 0.5 + 0.5 * math.sin(t / 300.0 + phase)
    base = {"pc": (22, 30, 29.0), "mac": (35, 70, 9.5), "laptop": (12, 0, 7.1), "hosting": (4, 0, 0.6)}[node]
    cpu = max(1, min(99, base[0] + 30 * w + rnd.uniform(-6, 6)))
    gpu = max(0, min(100, base[1] * w + rnd.uniform(-4, 4))) if base[1] else None
    total = NODES[node]["ram_gb"]
    used = round(min(total * 0.95, base[2] + 2.5 * w + rnd.uniform(-0.3, 0.3)), 1)
    darwin = NODES[node]["os"] == "darwin"
    top = [{"pid": 4000 + i * 37, "name": n, "cpu": round(c * (0.6 + w), 1), "rss_gb": r, "tag": tag}
           for i, (n, c, r, tag) in enumerate({
               "pc": [("python.exe", 41, 0.3, "enjambre: telemetría"), ("node.exe", 18, 0.6, "sesión: proyecto-a"),
                      ("code.exe", 9, 1.1, None), ("chrome.exe", 7, 0.9, None)],
               "mac": [("blender", 160, 3.2, "trabajo: atlas-031"), ("Python", 85, 1.9, "externo: render_lab"),
                       ("claude", 12, 0.4, "trabajo: proyecto-044"), ("WindowServer", 6, 0.3, None)],
               "laptop": [("python.exe", 22, 0.2, "trabajo: radar-052"), ("explorer.exe", 2, 0.1, None)],
               "hosting": [("php-fpm", 3, 0.1, None)]}[node])]
    iface, kind = ("en0", "Ethernet") if darwin else (("Intel(R) Ethernet", "Ethernet") if node == "pc" else ("Wi-Fi", "Wi-Fi"))
    return {"at": dt.datetime.fromtimestamp(t, LIMA).isoformat(timespec="seconds"), "ms": rnd.randint(70, 400),
            "cpu": {"pct": round(cpu, 1), "per_core": None, "load1": round(cpu / 25, 2) if darwin else None,
                    "cores": {"pc": 6, "mac": 10, "laptop": 8, "hosting": 2}[node], "window_s": 10.0,
                    **({"p_cores_n": 6, "e_cores_n": 4} if darwin else {})},
            "mem": {"total_gb": total, "used_gb": used, "wired_gb": None, "compressed_gb": 0.4 if darwin else None,
                    "swap_used_gb": 1.2 if darwin else None, "load_pct": None if darwin else round(100 * used / total),
                    "pressure": "normal" if darwin else None, "source": "demo"},
            "gpu": {"pct": round(gpu, 1) if gpu is not None else None, "vram_used_gb": 2.1 if node == "pc" else None,
                    "temp_c": 51 if node == "pc" else None, "source": "demo" if gpu is not None else "n/d"},
            "thermal": {"cpu_speed_limit": 100 if darwin else None, "throttled": False if darwin else None,
                        "temp_c": None, "source": "demo"},
            "top": top,
            "io": {"net_in_mb_s": round(abs(rnd.gauss(0.4, 0.5)), 2), "net_out_mb_s": round(abs(rnd.gauss(0.2, 0.2)), 2),
                   "disk_mb_s": round(abs(rnd.gauss(2.0, 2.5)), 2), "iface": iface, "iface_kind": kind}}


def ring(node: str, now: float, rnd: random.Random, n: int = 1440, step: float = 10.0) -> list:
    out = []
    for i in range(n):
        t = now - (n - i) * step
        x = tele(node, t, rnd)
        out.append(C.ring_line(x))
    return out


def live_now(now: float, rnd: random.Random) -> dict:
    """live.json as `enjambre live --serve` writes it, built by live.assemble from synthetic payloads."""
    sess = {"pc": [("proyecto-a", "running", 20), ("tienda-demo", "waiting", 600), ("radar-demo", "idle", 4000)],
            "mac": [("render-lab", "running", 30)], "laptop": [("radar-demo", "waiting", 900)], "hosting": []}
    payloads = {}
    for node in NODES:
        ss = []
        for k, (proj, status, ago) in enumerate(sess[node]):
            ss.append({"session_id": "demo-%s-%d" % (node, k), "name": "%s-%d" % (proj, k + 1), "project": proj,
                       "cwd": "/srv/proyectos/" + proj, "status": "busy" if status == "running" else "idle",
                       "started": now - 7200, "updated": now - ago, "context_k": rnd.randint(40, 260),
                       "cost_usd": round(rnd.uniform(0.5, 9), 2), "handoff_mtime": now - ago * 4})
        payloads[node] = {"node": node, "at": now - 2, "sessions": ss, "tmux": [],
                          "telemetria": tele(node, now - 2, rnd), "errors": [],
                          "detached": []}
    payloads["mac"]["detached"] = [
        {"id": "atlas-031", "state": "running", "repo": "atlas-demo", "kind": "render", "priority": "normal",
         "start": iso(now - 1500), "beat": {"ts": now - 8, "state": "running", "elapsed_s": 1500, "pct": 62.0}},
        {"id": "proyecto-044", "state": "running", "repo": "proyecto-a", "kind": "tests", "priority": "alta",
         "start": iso(now - 300), "beat": {"ts": now - 4, "state": "running", "turns": 7, "cost_est_usd": 0.31}}]
    metas = [{"id": "atlas-031", "node": "mac", "repo": "atlas-demo", "launched": iso(now - 1505), "kind": "render"},
             {"id": "proyecto-044", "node": "mac", "repo": "proyecto-a", "launched": iso(now - 305),
              "max_turns": 30, "budget_usd": 1.0, "origin_session": "demo-pc-0", "kind": "tests"}]
    queue = {"queue": [{"id": "radar-061", "repo": "radar-demo", "node": "laptop", "state": "waiting_window",
                        "reason": "nocturna: la ventana abre a las 00:00", "enqueued": iso(now - 900),
                        "priority": "nocturna"}], "running": []}
    pres = {"nodes": {n: {"level": v["level"]} for n, v in presence(now).items()}}
    meta = {n: {"read_at": now - 2, "lat_ms": {"pc": 310, "mac": 380, "laptop": 520, "hosting": 140}[n]} for n in NODES}
    live = LV.assemble(NODES, payloads, metas, queue, pres, now, meta)
    live["planificador"] = {
        "politica": {"mac": "1 alta corriendo · las bajas se pausan si falta CPU",
                     "laptop": "ventana nocturna abre mañana a las 00:00"},
        "externos": [{"id": "lab:7c1e9a", "tipo": "externo", "fuente": "render_lab", "nodo": "mac",
                      "proyecto": "render-lab (lab)", "estado": "running", "prioridad": "baja", "preset": "acuarela",
                      "elapsed_s": 212.0, "last_activity": now - 3, "senal": "GET /jobs", "pct": 83.3,
                      "frames": 50, "frames_total": 60, "eta_s": 42, "motivo": None}],
        "no_gestionados": []}
    LV.count_externos(live)
    live["reposo"] = {n: [] for n in NODES}
    live["reposo_umbrales"] = {k: DEMO_THRESHOLDS[k] for k in LV.REPOSO_IDS}
    return live


# ------------------------------------------------------------------ the page
def clock_shim(built: float) -> str:
    """The page's clock = build time + seconds since it opened, looped every 120 s."""
    return ("<script>(()=>{const b=%d,t0=Date.now(),n=Date.now.bind(Date);"
            "Date.now=()=>b+((n()-t0)%%120000);})();</script>" % int(built * 1000))


def make(out: Path = OUT, now: float | None = None, seed: int = SEED) -> dict:
    now = now or time.time()
    rnd = random.Random(seed)
    saved = B.load_alias, B.landing_icons, B.LANDING
    B.load_alias = lambda *a, **k: {}                      # never the real aliases
    B.landing_icons = lambda names, aliases=None, landing=None: ({}, {})
    try:
        with tempfile.TemporaryDirectory() as td:
            snaps = snapshots(now, Path(td), rnd)
        gathered = {n: {"cfg": {k: cfg.get(k) for k in ("label", "host", "os", "arch", "ram_gb", "max_jobs", "tags")},
                        "snap": snaps[n], "error": "", "age_s": 40.0, "stale": False} for n, cfg in NODES.items()}
        b = B.build(NODES, now, gathered, ledger(now, rnd), presence(now), {"queue": [], "running": [], "done": []})
    finally:
        B.load_alias, B.landing_icons, B.LANDING = saved
    b["demo"] = True
    live = live_now(now, rnd)
    rings = {n: ring(n, now, rnd) for n in NODES}
    out.mkdir(parents=True, exist_ok=True)
    html = B.render_html(b).replace("<head>", "<head>" + clock_shim(now), 1)
    B.write_atomic(out / "visor.html", html)
    LV.write_js(out / "live.js", "LIVE", live)
    LV.write_js(out / "ring.js", "LIVE_RING", rings)
    B.write_atomic(out / "demo-fixture.json", json.dumps({"seed": seed, "built": now, "nodes": NODES,
                                                          "live": live}, ensure_ascii=False, indent=1))
    return {"out": str(out), "projects": sorted(b["projects"]), "jobs": len(b["jobs"]), "nodes": list(NODES)}


def serve(out: Path, every: float = 10.0) -> None:
    """Fresh synthetic readings every 10 s, for a recording (Ctrl-C stops)."""
    rnd = random.Random(SEED + 1)
    while True:
        LV.write_js(out / "live.js", "LIVE", live_now(time.time(), rnd))
        time.sleep(every)


def no_console() -> dict:
    """Windows: no console window flashes for the audit (same idiom as presence.py)."""
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def export(out: Path = WEB_OUT, now: float | None = None) -> int:
    """The static site of the public demo. Refuses to clear a folder it did not build."""
    out = Path(out)
    if out.exists():
        if any(p.name not in WEB_FILES for p in out.iterdir()):
            print(f"REFUSED: {out} has files this export did not write", file=sys.stderr)
            return 2
        shutil.rmtree(out)
    res = make(out, now)
    (out / "visor.html").replace(out / "index.html")
    (out / ".htaccess").write_text(HTACCESS, encoding="utf-8", newline="\n")
    files = sorted(p for p in out.iterdir() if p.is_file())
    if sorted(p.name for p in files) != sorted(WEB_FILES):
        print("REFUSED: unexpected files: " + ", ".join(p.name for p in files), file=sys.stderr)
        return 2
    audit = REPO / "tools" / "publicar" / "auditar.py"
    rc = 0
    if audit.is_file():
        r = subprocess.run([sys.executable, str(audit), str(out)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                           **no_console())
        print((r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else f"audit rc {r.returncode}")
        if r.returncode == 2:
            print("aviso: sin clave de auditoría; quien suba esta carpeta debe auditarla antes", file=sys.stderr)
        else:
            rc = r.returncode
    z = out.parent / (out.name + ".zip")
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.name)
    for p in files:
        print("%s  %7d  %s" % (hashlib.sha256(p.read_bytes()).hexdigest()[:12], p.stat().st_size, p.name))
    print("zip: %s (%d bytes) · proyectos %s" % (z, z.stat().st_size, ", ".join(res["projects"])))
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", default=None)
    ap.add_argument("--serve", action="store_true", help="rewrite live.js every 10 s (for a video)")
    ap.add_argument("--export", action="store_true", help="the static public demo (default state/web-dist/demo/)")
    a = ap.parse_args(argv)
    if a.export:
        return export(Path(a.out) if a.out else WEB_OUT)
    a.out = a.out or str(OUT)
    res = make(Path(a.out))
    print(json.dumps(res, ensure_ascii=False))
    if a.serve:
        serve(Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
