"""scheduler — who runs where, and in what order (prompt 20). Pure rules over plain dicts, so the
tests drive them with recorded telemetry and never run a system command.

Classes, highest first: alta > normal > baja > nocturna (default normal). The numbers live in
config/scheduler.toml; the why in docs/conceptos/prioridades.md.

    admit()        may this queued job start on this node now? -> (ok, state, reason)
    order()        queue order: priority, then FIFO
    os_prefix()    the command prefix that lowers a job's OS priority on its node

Every "no" carries a reason a person can read ("esperando: presión de memoria warn desde 20:41").
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

try:
    import tomllib
except ImportError:   # py3.9 on a node: the scheduler brain runs on the PC, this only keeps imports safe
    tomllib = None

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config" / "scheduler.toml"
PRIOS = ("alta", "normal", "baja", "nocturna")
LOW = ("baja", "nocturna")
# a job holds a slot on its node in these states; dead/finished/evicted ones do not
SLOT_STATES = ("launching", "running", "paused_by_priority")
WAIT_STATES = ("queued", "waiting_window", "waiting_resources")
# zoneinfo needs the tzdata package on Windows; Lima has had no DST since 1994, so a fixed offset
# is exact for it. Any other zone without tzdata -> the window is treated as closed (never guessed).
FIXED_TZ = {"America/Lima": -5}
FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*(\n|\Z)", re.S)


def load(path: Path = CONFIG) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def rank(prio: str | None) -> int:
    return PRIOS.index(prio) if prio in PRIOS else PRIOS.index("normal")


def task_flags(text: str) -> dict:
    """priority / resumable / preempt_between_turns from a TASK's frontmatter. Unsaid -> None."""
    m = FRONT.match(text or "")
    out = {"priority": None, "resumable": None, "preempt_between_turns": None}
    if not m:
        return out
    for k in out:
        v = re.search(rf"^{k}:\s*(\S+)\s*$", m.group(1), re.M)
        if not v:
            continue
        raw = v.group(1).strip().strip("\"'").lower()
        if k == "priority":
            out[k] = raw if raw in PRIOS else None
        else:
            out[k] = {"true": True, "yes": True, "si": True, "false": False, "no": False}.get(raw)
    return out


def job_model(kind: str, priority: str | None, resumable: bool | None = None,
              between_turns: bool | None = None) -> dict:
    """The scheduler fields every job carries (job.json, spec.json, status.json, the ledger).
    Scripts are preemptable; an agent only if its TASK says preempt_between_turns: true."""
    agent = kind != "script"
    return {"priority": priority if priority in PRIOS else "normal",
            "preemptable": bool(between_turns) if agent else True,
            "resumable": bool(resumable),
            "between_turns": bool(between_turns) if agent else False}


def os_prefix(cfg: dict, prio: str, os_name: str) -> list:
    """['taskpolicy', '-b', 'nice', '-n', '15'] for a baja job on macOS; [] when it runs as the OS
    default (alta / normal) or the OS has no prefix (Windows sets the class after start)."""
    rule = (cfg.get("prioridad_so") or {}).get(prio) or {}
    if os_name != "macos" or not rule.get("macos"):
        return []
    return rule["macos"].split()


# ------------------------------------------------------------------ the night window
def lima_now(cfg: dict, now: float) -> dt.datetime | None:
    tzname = (cfg.get("nocturna") or {}).get("tz", "America/Lima")
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.fromtimestamp(now, ZoneInfo(tzname))
    except Exception:
        off = FIXED_TZ.get(tzname)
        if off is None:
            return None
        return dt.datetime.fromtimestamp(now, dt.timezone(dt.timedelta(hours=off)))


def _hm(s: str) -> tuple:
    h, m = s.split(":")
    return int(h), int(m)


def window(cfg: dict, now: float) -> tuple[bool, str]:
    """(open?, text). Closed -> the text says when it opens; open -> when it closes."""
    n = cfg.get("nocturna") or {}
    t = lima_now(cfg, now)
    if t is None:
        return False, f"zona horaria {n.get('tz')} ilegible: la ventana se da por cerrada"
    a, b = _hm(n.get("desde", "00:00")), _hm(n.get("hasta", "07:00"))
    cur = (t.hour, t.minute)
    is_open = (a <= cur < b) if a < b else (cur >= a or cur < b)
    if is_open:
        return True, f"ventana nocturna abierta hasta las {n.get('hasta')}"
    opens = t.replace(hour=a[0], minute=a[1], second=0, microsecond=0)
    if opens <= t:
        opens += dt.timedelta(days=1)
    day = "hoy" if opens.date() == t.date() else "mañana"
    return False, f"esperando la ventana nocturna: abre {day} a las {n.get('desde')} ({n.get('tz')})"


# ------------------------------------------------------------------ admission
def hhmm(ts: float | None, cfg: dict) -> str:
    t = lima_now(cfg, ts) if ts else None
    return t.strftime("%H:%M") if t else "?"


def _ts(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return dt.datetime.fromisoformat(str(v)).timestamp()
    except (TypeError, ValueError):
        return None


def pressure_since(ring: list, now: float, ok: str) -> float | None:
    """Since when the newest unbroken run of ring lines has had pressure worse than `ok`."""
    worse = PRESSURE_ORDER.index(ok) if ok in PRESSURE_ORDER else 0
    since = None
    for ln in reversed(ring):
        pr = ln.get("pr")
        if pr not in PRESSURE_ORDER or PRESSURE_ORDER.index(pr) <= worse:
            break
        since = _ts(ln.get("at"))
    return since


PRESSURE_ORDER = ("normal", "warn", "critical")


def swap_growth(ring: list, now: float, window_s: float) -> float | None:
    """GB the swap grew over the last window_s (newest minus the lowest before it); None = no data."""
    pts = [(_ts(ln.get("at")), ln.get("swap")) for ln in ring]
    pts = [(t, v) for t, v in pts if t is not None and now - t <= window_s and isinstance(v, (int, float))]
    if len(pts) < 2:
        return None
    low = min(v for _, v in pts[:-1])
    return round(pts[-1][1] - low, 2)


def memory_ok(node_os: str, tele: dict | None, tele_age: float | None, ring: list, cfg: dict,
              now: float) -> tuple[bool, str]:
    """Is there memory for one more low-priority job? macOS: pressure + swap not growing (free %
    is the wrong signal there, prompt 20 §1.1). Windows: available %."""
    a = cfg.get("admision") or {}
    if tele is None or tele_age is None or tele_age > a.get("telemetria_max_edad_s", 120):
        return False, "sin telemetría fresca del nodo (live --serve parado o nodo sin señal)"
    mem = tele.get("mem") or {}
    if node_os == "windows":
        load = mem.get("load_pct")
        if not isinstance(load, (int, float)):
            return False, "memoria disponible ilegible (n/d)"
        avail, need = 100 - load, a.get("mem_disponible_min_pct", 30)
        if avail < need:
            return False, f"memoria disponible {avail:.0f} % < {need} %"
        return True, f"memoria disponible {avail:.0f} %"
    pr, cap = mem.get("pressure"), a.get("presion_memoria_max", "normal")
    if pr not in PRESSURE_ORDER:
        return False, "presión de memoria ilegible (n/d)"
    if PRESSURE_ORDER.index(pr) > PRESSURE_ORDER.index(cap):
        since = pressure_since(ring, now, cap)
        return False, f"presión de memoria {pr}" + (f" desde {hhmm(since, cfg)}" if since else "")
    grew = swap_growth(ring, now, a.get("swap_creciendo_ventana_s", 120))
    if grew is not None and grew > 0.05:
        return False, f"swap creciendo (+{grew:.2f} GB en {a.get('swap_creciendo_ventana_s', 120) // 60} min)"
    return True, "presión de memoria normal, swap estable"


def holders(jobs: list) -> list:
    """Jobs that hold a slot on a node now."""
    return [j for j in jobs if j.get("state") in SLOT_STATES]


def admit(job: dict, node_os: str, running: list, max_jobs: int, tele: dict | None,
          tele_age: float | None, ring: list, cfg: dict, now: float) -> tuple[bool, str, str]:
    """(ok, state, reason) for one queued job on its node. `running`: the node's jobs with their
    state and priority (scheduler.holders filters). Order of the checks = order of the reasons."""
    prio = job.get("priority") or "normal"
    live = holders(running)
    if prio == "nocturna":
        is_open, why = window(cfg, now)
        if not is_open:
            return False, "waiting_window", why
    if prio in LOW:
        hi = [j for j in live if (j.get("priority") or "normal") in ("alta", "normal")]
        if hi and (cfg.get("admision") or {}).get("baja_requiere_sin_alta_normal", True):
            h = hi[0]
            return False, "waiting_resources", (f"esperando: {h.get('id')} ({h.get('priority') or 'normal'}) "
                                                f"corre en el nodo" + (f" (+{len(hi) - 1} más)" if len(hi) > 1 else ""))
        ok, why = memory_ok(node_os, tele, tele_age, ring, cfg, now)
        if not ok:
            return False, "waiting_resources", "esperando: " + why
    slots = max_jobs - len(live)
    if slots <= 0:
        extra = int((cfg.get("preempcion") or {}).get("alta_puede_exceder_max_jobs", 1))
        only_low = live and all((j.get("priority") or "normal") in LOW for j in live)
        if prio == "alta" and only_low and len(live) < max_jobs + extra:
            return True, "running", (f"alta: entra por encima de max_jobs ({len(live)}/{max_jobs}); "
                                     f"los baja quedan para pausar")
        return False, "waiting_resources", f"esperando hueco: {len(live)}/{max_jobs} trabajos en el nodo"
    return True, "running", f"{prio}: admitido ({len(live)}/{max_jobs} en el nodo)"


def order(queue: list) -> list:
    """Priority first, then FIFO (enqueued)."""
    return sorted(queue, key=lambda m: (rank(m.get("priority")), m.get("enqueued", "")))


# ------------------------------------------------------------------ preemption (live --serve tick)
def mem_short(node_os: str, tele: dict | None, cfg: dict) -> str | None:
    """Why the node is short of memory, or None. Unknown is not short: no eviction on a guess."""
    mem = (tele or {}).get("mem") or {}
    if node_os == "windows":
        load = mem.get("load_pct")
        need = (cfg.get("admision") or {}).get("mem_disponible_min_pct", 30)
        return f"memoria disponible {100 - load:.0f} % < {need} %" if isinstance(load, (int, float)) and 100 - load < need else None
    pr = mem.get("pressure")
    return f"presión de memoria {pr}" if pr in PRESSURE_ORDER and pr != "normal" else None


def cpu_gpu_busy(tele: dict | None, cfg: dict) -> str | None:
    thr = (cfg.get("preempcion") or {}).get("cpu_gpu_umbral_pct", 70)
    cpu = ((tele or {}).get("cpu") or {}).get("pct")
    gpu = ((tele or {}).get("gpu") or {}).get("pct")
    if isinstance(cpu, (int, float)) and cpu > thr:
        return f"CPU {cpu:.0f} %"
    if isinstance(gpu, (int, float)) and gpu > thr:
        return f"GPU {gpu:.0f} %"
    return None


ECHO_GRACE_S = 45   # one or two live reads: the node has not reported the new state yet


def plan(jobs: list, teles: dict, oses: dict, sstate: dict, now: float, cfg: dict) -> tuple:
    """One preemption decision for every node. `jobs`: what runs, each {key, id, node, priority,
    state, preemptable, resumable, source: enjambre|render_lab|adopted}. `sstate`: what this
    scheduler paused or evicted itself ({paused: {key: ...}, evicted: {...}, free_since: {node: ts}}),
    the only things it will ever resume. Returns (actions, new_sstate)."""
    pc = cfg.get("preempcion") or {}
    preemptors = pc.get("preemptores", ["alta"])
    hold_s = pc.get("reanudar_tras_s_libre", 60)
    st = {"paused": dict(sstate.get("paused") or {}), "evicted": dict(sstate.get("evicted") or {}),
          "free_since": dict(sstate.get("free_since") or {})}
    by_key = {j["key"]: j for j in jobs}
    # forget what is gone, or what someone else resumed (the owner's hand wins over ours)
    # (a fresh entry gets ECHO_GRACE_S: the node reports the new state one read later)
    for key, p in list(st["paused"].items()):
        j = by_key.get(key)
        if j is None or (j.get("state") not in ("paused_by_priority", "paused")
                         and now - p.get("since", 0) > ECHO_GRACE_S):
            st["paused"].pop(key)
    for key, p in list(st["evicted"].items()):
        j = by_key.get(key)
        if (j is None and now - p.get("since", 0) > ECHO_GRACE_S) or                 (j is not None and j.get("state") not in ("evicted_resumable", "running")) or                 (j is not None and j.get("state") == "running" and now - p.get("since", 0) > ECHO_GRACE_S):
            st["evicted"].pop(key)
    actions = []
    for node in sorted({j["node"] for j in jobs} | set(st["free_since"])):
        mine = [j for j in jobs if j["node"] == node]
        tele, node_os = teles.get(node), oses.get(node, "")
        hi = [j for j in mine if j.get("priority") in preemptors and j.get("state") in ("running", "launching")]
        short, busy = mem_short(node_os, tele, cfg), cpu_gpu_busy(tele, cfg)
        if hi:
            st["free_since"].pop(node, None)
            h = hi[0]
            who = f"{h['id']} ({h['priority']})"
            for j in mine:
                if j.get("priority") not in LOW or j.get("state") != "running" or not j.get("preemptable"):
                    continue
                if j.get("agent") and pc.get("agentes", "entre_turnos") == "nunca":
                    continue
                if short and j.get("resumable") and j.get("source") == "enjambre":
                    why = f"desalojado por {who}: {short}; se reanuda desde su checkpoint al quedar libre {hold_s} s"
                    actions.append(dict(action="evict", key=j["key"], id=j["id"], node=node, source=j["source"], reason=why))
                    st["evicted"][j["key"]] = {"by": h["id"], "since": now, "reason": why, "node": node}
                elif short or busy:
                    why = (f"pausado por {who} desde {hhmm(now, cfg)} ({short or busy}); se reanuda al quedar libre {hold_s} s"
                           + ("; pausar no libera su memoria" if short else ""))
                    actions.append(dict(action="pause", key=j["key"], id=j["id"], node=node, source=j["source"], reason=why))
                    st["paused"][j["key"]] = {"by": h["id"], "since": now, "reason": why, "node": node,
                                              "source": j["source"]}
            continue
        waiting = [k for k, p in list(st["paused"].items()) + list(st["evicted"].items()) if p.get("node") == node]
        if not waiting or short:
            st["free_since"].pop(node, None)
            continue
        since = st["free_since"].setdefault(node, now)
        if now - since < hold_s:
            continue
        for key in waiting:
            j = by_key.get(key)
            if j is None or j.get("state") not in ("paused_by_priority", "paused", "evicted_resumable"):
                continue          # not echoed yet: next tick
            act = "resume_evicted" if key in st["evicted"] else "resume"
            actions.append(dict(action=act, key=key, id=j["id"], node=node, source=j["source"],
                                reason=f"reanudado: nodo libre de {', '.join(preemptors)} durante {int(now - since)} s"))
            st["paused"].pop(key, None)
            st["evicted"].pop(key, None)
        st["free_since"].pop(node, None)
    return actions, st
