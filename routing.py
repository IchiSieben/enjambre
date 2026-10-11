"""routing — pick the node for a job from a table plus live state. No model decides here.

Order (docs/conceptos/enrutamiento.md):
  (a) hard filters: capabilities (`needs` of the kind and of the worker), the confidencial/reservado rule,
      an executor on the node (`runnable`), and the node being up (`ready` in state/nodes.json);
  (b) live state: a free slot (max_jobs minus running/), no memory pressure, and no live busy
      marker (busy.py) for GPU kinds; a kind with cpu_fallback runs there on CPU instead;
  (c) measured history: with >= history_min_runs ok runs per (kind, node) on at least two
      free nodes, the fastest median wins;
  (d) tie: the first of `prefer`.
Pure functions: every input comes in as an argument, so tests drive them with simulated nodes.
Stdlib only.
"""
from __future__ import annotations

import json
import re
import statistics
import tomllib
from pathlib import Path

UP = ("ready", "local")
NUMERIC = re.compile(r"^(\w+)\s*(>=|<=|>|<|==)\s*(\d+(?:\.\d+)?)$")
FIELDS = {"ram": "ram_gb", "cores": "cores"}
FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


def load(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def need_ok(node: dict, need: str) -> bool:
    m = NUMERIC.match(need)
    if not m:
        return need in node.get("tags", [])
    key, op, val = m.group(1), m.group(2), float(m.group(3))
    have = node.get(FIELDS.get(key, key))
    if not isinstance(have, (int, float)) or have <= 0:  # 0 = not measured: not proof of anything
        return False
    return {">=": have >= val, "<=": have <= val, ">": have > val, "<": have < val,
            "==": have == val}[op]


def kind_from_task(text: str) -> str | None:
    """`kind: tests` in an optional YAML-ish frontmatter at the top of TASK-*.md."""
    m = FRONTMATTER.match(text)
    if not m:
        return None
    k = re.search(r"^kind:\s*([a-z][a-z0-9-]*)\s*$", m.group(1), re.M)
    return k.group(1) if k else None


def ledger_kind(e: dict) -> str | None:
    """Lines written before `kind` existed get one derived from what they were; the ledger
    itself is never rewritten. Stubs measure the pipe, not a kind of work: excluded."""
    if e.get("kind"):
        return e["kind"]
    w = e.get("worker") or ""
    if w == "blender":
        return "render"
    if e.get("engine") == "local" or w == "local":
        return "local-model"
    if w == "claude":
        return "claude"
    return None


def history(lines) -> dict:
    """{(kind, node): [duration_s of ok runs]} from ledger lines (str or dict)."""
    out: dict = {}
    for line in lines:
        e = json.loads(line) if isinstance(line, str) else line
        k = ledger_kind(e)
        status = e.get("status") or ("ok" if e.get("exit") == 0 else "failed")
        d = e.get("duration_s")
        if k and status == "ok" and isinstance(d, (int, float)):
            out.setdefault((k, e.get("node")), []).append(float(d))
    return out


def history_table(hist: dict, kinds, nodes, min_runs: int) -> list[tuple[str, str, int, str]]:
    """Rows (kind, node, n, median or 'sin datos'): a cell with < min_runs says so, no number."""
    rows = []
    for k in kinds:
        for n in nodes:
            runs = hist.get((k, n), [])
            med = f"{statistics.median(runs):.1f} s" if len(runs) >= min_runs else "sin datos"
            rows.append((k, n, len(runs), med))
    return rows


def mem_busy(snapshot: dict, node: str, now: float, cfg: dict, health_fn=None) -> tuple[bool, str]:
    """Memory pressure from the visor snapshot when it is fresh; otherwise ask the model manager
    whether it has a job running (that is the only 'model loaded' signal it exposes).
    Known blind spot: work started outside the manager (a bench in tmux) shows running=null."""
    d = cfg.get("defaults", {})
    fresh_s, floor = d.get("mem_fresh_s", 720), d.get("mem_free_min_pct", 20)
    built = snapshot.get("built")
    mem = ((snapshot.get("nodes") or {}).get(node) or {}).get("memory") or {}
    fresh = isinstance(built, (int, float)) and now - built <= fresh_s
    if fresh and isinstance(mem.get("free_pct"), (int, float)):
        pct = mem["free_pct"]
        return pct < floor, f"memory free {pct}% (visor, floor {floor}%)"
    if health_fn is not None:
        h = health_fn(node)
        if h is None:
            return False, "no recent memory data, model manager not answering"
        if isinstance(h.get("free_pct"), (int, float)):
            pct = h["free_pct"]
            return pct < floor, f"memory free {pct}% (live, floor {floor}%)"
        if h.get("running"):
            return True, "no recent memory data and the model manager has a job loaded"
        return False, "no recent memory data, model manager idle"
    return False, "no recent memory data"


def reposo_busy(live: dict | None, node: str, now: float, max_age_s: float = 120) -> tuple[bool, str] | None:
    """A reposo rule that says "do not dispatch here" (visor live.json, prompt 19 §2.4).
    None when live.json is missing or older than max_age_s: a dead live loop must not block
    dispatch, so stale telemetry says nothing and mem_busy decides as before."""
    ts = (live or {}).get("updated_ts")
    if not isinstance(ts, (int, float)) or now - ts > max_age_s:
        return None
    rules = ((live.get("reposo") or {}).get(node)) or []
    hit = [r for r in rules if r.get("no_dispatch")]
    if hit:
        return True, f"reposo: {hit[0].get('msg')} (since {hit[0].get('since')})"
    return None


def busy_blocks(kind: str, marker: dict | None, cfg: dict) -> bool:
    """A live busy marker blocks only the kinds that compete for the GPU / unified memory."""
    return bool(marker and marker.get("alive")) and kind in cfg.get("busy", {}).get("gpu_kinds", [])


def route(kind: str, worker: str, repo_path: str, nodes: dict, cfg: dict, levels: dict,
          running: dict, mem: dict, hist: dict, sensitive: re.Pattern | None = None,
          busy: dict | None = None) -> dict:
    """Returns {action: run|queue|refuse, node, routed_by, reason, diverted_from, trace, device}.
    `queue` names the node the job waits for; `refuse` means no node could ever take it;
    device "CPU" means: run there, but not on the GPU (busy marker + cpu_fallback)."""
    busy = busy or {}
    kinds = cfg.get("kinds", {})
    if kind not in kinds:
        return _d("refuse", None, f"unknown kind '{kind}' (routing.toml has: {', '.join(kinds)})")
    k = kinds[kind]
    needs = list(k.get("needs", [])) + list(cfg.get("workers", {}).get(worker, {}).get("needs", []))
    sens = cfg.get("sensitive", {})
    sens_re = sensitive or re.compile(sens.get("pattern", "confidencial|reservado"), re.I)
    is_sensitive = bool(repo_path) and bool(sens_re.search(repo_path))
    order = list(k.get("prefer", [])) + [n for n in nodes if n not in k.get("prefer", [])]
    order = [n for n in order if n in nodes]
    trace, capable = [], []

    # (a) hard filters
    for n in order:
        c = nodes[n]
        missing = [x for x in needs if not need_ok(c, x)]
        if missing:
            trace.append(f"{n}: lacks {', '.join(missing)}")
            continue
        if is_sensitive and (worker not in sens.get("workers", ["claude"]) or not c.get("owned")):
            trace.append(f"{n}: confidencial/reservado repo needs worker claude on an owned node")
            continue
        if c.get("runnable", True) is False:
            trace.append(f"{n}: fits, but has no executor yet (runnable = false)")
            continue
        capable.append(n)
    if not capable:
        return _d("refuse", None, f"no node can run kind '{kind}': " + "; ".join(trace), trace=trace)
    up = [n for n in capable if levels.get(n) in UP]
    for n in capable:
        if n not in up:
            trace.append(f"{n}: {levels.get(n) or 'unknown'}")
    if not up:
        n = capable[0]
        return _d("queue", n, f"{kind}: {n} is {levels.get(n) or 'unknown'}; queued until it is ready"
                  + _also(trace, n), trace=trace)

    # (b) live state
    free, cpu_only = [], {}
    for n in up:
        slots = nodes[n].get("max_jobs", 1) - running.get(n, 0)
        mbusy, why = mem.get(n, (False, ""))
        if slots <= 0:
            trace.append(f"{n}: {running.get(n, 0)}/{nodes[n].get('max_jobs', 1)} slots busy")
        elif mbusy:
            trace.append(f"{n}: busy, {why}")
        elif busy_blocks(kind, busy.get(n), cfg):
            b = busy[n]
            what = f"{b.get('tipo')} by {b.get('quien')} since {b.get('desde')}"
            if k.get("cpu_fallback"):
                cpu_only[n] = what
                free.append(n)
            else:
                trace.append(f"{n}: busy marker, {what}")
        else:
            free.append(n)
    if not free:
        n = up[0]
        return _d("queue", n, f"{kind}: every apt node is busy; queued for {n} ({_last(trace, n)})"
                  + _also(trace, n), trace=trace)
    diverted = capable[0] if free[0] != capable[0] else None

    # (c) measured history, only when there is something to compare
    min_runs = cfg.get("defaults", {}).get("history_min_runs", 5)
    measured = {n: statistics.median(hist[(kind, n)]) for n in free
                if len(hist.get((kind, n), [])) >= min_runs}
    if len(measured) >= 2:
        n = min(measured, key=measured.get)
        others = ", ".join(f"{m} {s:.1f} s" for m, s in measured.items() if m != n)
        return _d("run", n, f"{kind} -> {n}: fastest measured median {measured[n]:.1f} s (vs {others})",
                  "history", diverted, trace)

    # (d) tie: first of prefer; a node that is free only on CPU goes after the fully free ones
    n = sorted(free, key=lambda x: x in cpu_only)[0]
    why = f"{kind} -> {n}: first free node of prefer {k.get('prefer', [])}"
    if diverted:
        why += f"; {diverted} skipped ({_last(trace, diverted)})"
    d = _d("run", n, why, "rule", diverted, trace)
    if n in cpu_only:
        d["device"] = "CPU"
        d["reason"] += f"; on CPU, not Metal: busy marker, {cpu_only[n]}"
    return d


def _last(trace: list, node: str) -> str:
    for t in reversed(trace):
        if t.startswith(node + ":"):
            return t.split(": ", 1)[1]
    return "?"


def _also(trace: list, chosen: str) -> str:
    rest = [t for t in trace if not t.startswith(chosen + ":")]
    return (" (" + "; ".join(rest) + ")") if rest else ""


def _d(action, node, reason, routed_by="rule", diverted=None, trace=None) -> dict:
    return {"action": action, "node": node, "routed_by": routed_by, "reason": reason,
            "diverted_from": diverted, "trace": trace or []}


CLASSIFY_PROMPT = """Classify this task for a job router. Answer with ONE word from this list and nothing else:
{kinds}
tests = run or fix tests; git = heavy git work; scrape = download/crawl data; render = 3D render;
transcribe = audio to text; local-model = ask a local LLM; gui = needs a browser or app window;
video-edit = edit footage; claude = any other code or docs change.
TASK:
{task}"""


def classify(task_text: str, kinds, run_fn) -> tuple[str | None, float | None]:
    """Optional, off by default (--classify): one cheap haiku call. run_fn(prompt) -> (text,
    cost_usd). Returns (kind or None if the answer is not a known kind, cost)."""
    text, cost = run_fn(CLASSIFY_PROMPT.format(kinds=", ".join(kinds), task=task_text[:4000]))
    word = (text or "").strip().lower().strip(".`'\" ")
    return (word if word in kinds else None), cost
