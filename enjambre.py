#!/usr/bin/env python3
"""enjambre — send a TASK to another machine, get a branch back. Stdlib only.

    python enjambre.py run --node mac --repo enjambre-sandbox --task TASK-x.md [--worker claude]
    python enjambre.py run --repo enjambre-sandbox --task TASK-x.md --kind tests [--dry-run]
    python enjambre.py history                 # measured seconds per (kind, node)
    python enjambre.py run --node mac --repo enjambre-sandbox --task TASK-x.md --engine local
    python enjambre.py status
    python enjambre.py pull <task-id> --repo enjambre-sandbox
    python enjambre.py watch [--once] [--interval 30] | watch --stop

Without --node, `run` routes (routing.py + routing.toml) and prints one line saying why.
`run` against a node that is not ready (see presence.py) queues the job; `watch` probes the
nodes every 30 s, keeps state/nodes.json, notifies on changes and dispatches the queue.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import logging
import logging.handlers
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import busy as B
import largos as L
import live as LV
import presence as P
import puente_cli as PC
import razones as RZ
import routing as R
import scheduler as S

HERE = Path(__file__).resolve().parent
PORTFOLIO = HERE.parent
LEDGER = HERE / "jobs.jsonl"
WORKER = HERE / "worker.sh"
TMUX_RUN = HERE / "tmux-run.sh"  # worker.sh re-execs through it (keychain, see the script)
# Workers that send code to an external provider. A `claude` worker on our own machines is the
# only one allowed near sensitive paths (PLAN.md §4.1 rule 6, same rule as tercerizar).
EXTERNAL_WORKERS = {"agy", "codex"}
SENSITIVE = re.compile(r"confidencial|reservado", re.IGNORECASE)
REQUIRED_SECTIONS = ("## Files you may touch", "## Done criteria")
STATE = HERE / "state"            # gitignored: nodes.json, queue/, running/, done/, watch.log
NODES_JSON = STATE / "nodes.json"
KNOBS = ("ENJAMBRE_MAX_TURNS", "ENJAMBRE_NODE_PUSH")
PROBE = P.probe                   # tests replace this with a simulated node
ROUTING = HERE / "routing.toml"
VISOR_SNAPSHOT = STATE / "visor" / "snapshot.json"


class ContractError(Exception):
    """The job was refused before touching any node."""


# A repo is a folder NAME under <portfolio>/ on every node. A path is refused: on the PC
# `PORTFOLIO / "C:\...\wt\x"` IS that absolute path, so the local check passed while the Mac
# looked for `~/Documents/Portfolio/C:\...` and the job died unseen (eco-gates-2, 2026-10-15).
REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
# A branch for --ref: what git accepts, minus anything a shell or git option could read.
REF_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")


def check_repo_name(repo: str) -> None:
    if not REPO_NAME.fullmatch(repo or "") or ".." in repo:
        raise ContractError(f"--repo must be a repo name under <portfolio>/, not a path: {repo!r}. "
                            "Pick the branch with --ref <branch>.")


def check_ref(ref: str | None) -> None:
    if ref is not None and (not REF_NAME.fullmatch(ref) or ".." in ref or ref.endswith((".lock", "/"))):
        raise ContractError(f"--ref is not a plain branch name: {ref!r}")


def repo_on_node(host: str, repo: str, sh_fn=None) -> bool | None:
    """Same ssh as presence: True = <portfolio>/<repo>/.git exists on the node, False = it does
    not, None = the node did not answer (the node-side check in jobrun.launch still runs)."""
    r = (sh_fn or sh)(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                       f'test -d "$HOME/Documents/Portfolio/{repo}/.git"'])
    if r.returncode == 0:
        return True
    return False if r.returncode == 1 else None


def preflight(node_name: str, host: str, repo: str) -> int:
    """0 = go on. 3 = the repo is not on the node: say the one command that fixes it."""
    if repo_on_node(host, repo) is False:
        print(f"enjambre: {repo} is not cloned on {node_name} (~/Documents/Portfolio/{repo}/.git "
              f"missing). Fix: python enjambre.py clone --node {node_name} {repo}", file=sys.stderr)
        return 3
    return 0


def task_id_from(task: Path) -> str:
    m = re.fullmatch(r"TASK-([a-z0-9][a-z0-9-]{0,40})\.md", task.name)
    if not m:
        raise ContractError(f"task file must be named TASK-<id>.md (lowercase, digits, '-'): {task.name}")
    return m.group(1)


def validate(repo_dir: Path, task: Path, worker: str) -> str:
    """WHY a contract and not a chat: an unattended worker cannot ask what you meant, so the
    task must say what it may touch and when it is done. Returns the task id."""
    # The refusal runs first, on the path as typed, before anything else is read.
    if worker in EXTERNAL_WORKERS and SENSITIVE.search(str(repo_dir.resolve())):
        raise ContractError(f"refused: worker '{worker}' is external and the repo path matches confidencial/reservado")
    if not repo_dir.is_dir():
        raise ContractError(f"repo not found: {repo_dir}")
    if not (repo_dir / "AGENTS.md").is_file():
        raise ContractError(f"{repo_dir.name} has no AGENTS.md (rules for any agent in the repo)")
    if not task.is_file():
        raise ContractError(f"task not found: {task}")
    text = task.read_text(encoding="utf-8")
    missing = [s for s in REQUIRED_SECTIONS if s not in text]
    if missing:
        raise ContractError(f"{task.name} lacks sections: {', '.join(missing)}")
    return task_id_from(task)


def load_nodes(route: bool = True) -> dict:
    """nodes.toml, with ENJAMBRE_HOST_<NODE>=<ssh alias> overriding a node's host for this
    process only (NEEDS-OWNER §15: with the VPN up, `mac` times out and another alias answers).
    With route, a `host_alt` that watch switched to (state/route.json, prompt 21 §3) applies too;
    watch itself loads with route=False, so it keeps probing the primary.
    The file is never rewritten."""
    with open(P.nodes_path(HERE), "rb") as f:
        nodes = tomllib.load(f)["nodes"]
    for name, cfg in nodes.items():
        alias = os.environ.get(f"ENJAMBRE_HOST_{name.upper()}")
        if alias and cfg.get("host") != "local" and re.fullmatch(r"[A-Za-z0-9._-]+", alias):
            cfg["host"], cfg["host_override"] = alias, True
    return P.apply_route(nodes) if route else nodes


def save_job_log(tid: str, text: str, keep_lines: int = 2000) -> None:
    """Short jobs keep their output too (state/jobs/<id>/log), so a failure has a log to open
    (F3). Long jobs already have it from collect."""
    d = STATE / "jobs" / str(tid)
    d.mkdir(parents=True, exist_ok=True)
    lines = text.splitlines()[-keep_lines:]
    (d / "log").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def append_ledger(entry: dict, ledger: Path | None = None) -> None:
    """WHY append-only: no ledger line = the job did not happen. One line per run, never edited.
    Every row goes through razones.enrich: a failed or incomplete job carries its cause in one
    line, its log path, and `test: true` when it is a sandbox/stub/fire test (F3)."""
    entry = RZ.enrich(entry, STATE)
    # Read at call time: a default bound at import would ignore a test's E.LEDGER (2026-10-15:
    # the clone tests wrote three rows into the real jobs.jsonl).
    with open(ledger or LEDGER, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def job_status(exit_code: int, detail: dict | None) -> str:
    """WHY a third state: claude exits 0 even when a tool call was refused, so exit=0 alone
    over-reports success (statusline-decimal: exit 0, the Edit denied, branch held no fix).
    Any permission denial means the task is not done as written, whatever the exit code."""
    if (detail or {}).get("permission_denials"):
        return "incomplete"
    return "ok" if exit_code == 0 else "failed"


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, **P.no_console(), **kw)


def gitleaks_clean(repo_dir: Path, rev_range: str) -> bool:
    exe = PORTFOLIO / "orquestador" / ".tools" / "gitleaks.exe"
    if not exe.exists():
        return False  # no scanner = no push
    r = sh([str(exe), "git", str(repo_dir), f"--log-opts={rev_range}", "--no-banner"])
    return r.returncode == 0


def repo_visibility(repo_dir: Path) -> str:
    """'private' | 'public' | 'internal' | 'unknown' (gh not logged in, no remote, offline)."""
    r = sh(["gh", "repo", "view", "--json", "visibility", "-q", ".visibility"], cwd=str(repo_dir))
    v = r.stdout.strip().lower()
    return v if r.returncode == 0 and v in ("private", "public", "internal") else "unknown"


def frontmatter_flag(text: str, key: str) -> bool | None:
    """`push: true|false` in the TASK frontmatter (same block as `kind:`). None = not said."""
    m = R.FRONTMATTER.match(text)
    v = re.search(rf"^{key}:\s*(\S+)\s*$", m.group(1), re.M) if m else None
    if not v:
        return None
    return {"true": True, "yes": True, "si": True, "false": False, "no": False}.get(v.group(1).lower())


def push_policy(text: str, repo_dir: Path, vis_fn=repo_visibility) -> tuple[bool, str]:
    """WHY push is opt-in for public repos: a job branch on a public remote is published the
    moment it lands, gitleaks or not. Private repo: push (after gitleaks, as before). Public or
    unknown: keep the branch here unless the TASK says `push: true`. gitleaks always wins."""
    said = frontmatter_flag(text, "push")
    if said is False:
        return False, "push: false in the TASK"
    if said is True:
        return True, "push: true in the TASK"
    vis = vis_fn(repo_dir)
    if vis == "private":
        return True, "private repo"
    return False, f"repo visibility {vis}: push only with `push: true` in the TASK"


def bring_branch_home(node: dict, repo: str, branch: str, push: bool = True,
                      why: str = "") -> tuple[bool, str]:
    """The node does not push (ENJAMBRE_NODE_PUSH unset): git is still the bus. Fetch the
    branch from the node over ssh, scan exactly its new commits, push it from here only when
    push_policy allows it. Without a push the branch still lands here (git branch mac/<id>)."""
    repo_dir = PORTFOLIO / repo
    remote = f"{node['host']}:Documents/Portfolio/{repo}"
    # Bounded and retried once: on 2026-10-07 one fetch hung ~10 min, then "remote end hung
    # up"; the same fetch by hand took 0.6 s (E3, tutorial 25).
    for attempt in (1, 2):
        try:
            r = sh(["git", "-C", str(repo_dir), "fetch", "-q", remote, f"{branch}:{branch}"], timeout=120)
        except subprocess.TimeoutExpired:
            r = subprocess.CompletedProcess([], 124, "", "timeout after 120 s")
        if r.returncode == 0:
            break
    if r.returncode:
        return False, "fetch from node failed: " + r.stderr.strip()
    if not push:
        return False, f"fetched from node, not pushed ({why})"
    if not gitleaks_clean(repo_dir, f"{branch} --not --remotes=origin"):
        return False, "gitleaks finding (or scanner missing): not pushed"
    r = sh(["git", "-C", str(repo_dir), "push", "-q", "-u", "origin", branch])
    if r.returncode:
        return False, "push from orchestrator failed: " + r.stderr.strip()[-300:]
    return True, "fetched from node over ssh, gitleaks clean, pushed from orchestrator"


def model_health(host: str) -> dict | None:
    """One ssh call for the live state of a node: the model manager's /health (127.0.0.1 only),
    the memory_pressure free %, and the busy marker under "busy" (busy.py). None = no answer."""
    r = P._run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host,
                "curl -s -m 3 127.0.0.1:8765/health; echo; memory_pressure 2>/dev/null | tail -1; "
                f"echo {B.MARK}; " + B.READ_CMD], 20)
    if r.returncode != 0 and not r.stdout.strip():
        return None
    busy_out = B.section(r.stdout)
    r.stdout = r.stdout.split(B.MARK, 1)[0]
    lines = r.stdout.strip().splitlines()
    try:
        h = json.loads(lines[0]) if lines and lines[0].startswith("{") else {}
    except json.JSONDecodeError:
        h = {}
    m = re.search(r"free percentage:\s*(\d+)%", r.stdout)
    if m:
        h["free_pct"] = int(m.group(1))
    h["busy"] = B.parse(busy_out)
    return h


def classify_haiku(prompt: str) -> tuple[str, float | None]:
    """--classify only. One `claude -p` call on haiku, one turn; returns (answer, cost_usd)."""
    r = sh(["claude", "-p", prompt, "--model", "haiku", "--output-format", "json", "--max-turns", "1"])
    try:
        d = json.loads(r.stdout)
        return str(d.get("result", "")), d.get("total_cost_usd")
    except json.JSONDecodeError:
        return "", None


def resolve_kind(a, text: str, cfg: dict, classify_fn=classify_haiku) -> tuple[str, str, float | None]:
    """--kind, else `kind:` frontmatter in the TASK, else --classify (haiku), else the default.
    Returns (kind, source, classify_cost_usd)."""
    if getattr(a, "kind", None):
        return a.kind, "flag", None
    if getattr(a, "engine", "claude") == "local":
        return "local-model", "engine", None
    k = R.kind_from_task(text)
    if k:
        return k, "frontmatter", None
    if getattr(a, "classify", False):
        k, cost = R.classify(text, list(cfg.get("kinds", {})), classify_fn)
        if k:
            return k, "classify", cost
        return cfg["defaults"]["kind"], "default (classify gave no known kind)", cost
    return cfg["defaults"]["kind"], "default", None


def decide(kind: str, worker: str, repo_dir: Path, nodes: dict, cfg: dict, t: float,
           probe_fn=None, health_fn=None) -> tuple[dict, dict]:
    """Collect the live inputs routing.route needs, then route. Returns (decision, levels)."""
    probe_fn, health_fn = probe_fn or PROBE, health_fn or model_health
    st = P.read_state(NODES_JSON)
    # watch's nodes.json speaks for the nodes.toml host: with an override, probe the alias live.
    levels = {n: "local" if c["host"] == "local" else
              ((None if c.get("host_override") else P.fresh_level(st, n, t)) or probe_fn(c["host"]))
              for n, c in nodes.items()}
    running = {n: P.running_count(STATE, n) for n in nodes}
    snap = P.read_state(VISOR_SNAPSHOT)
    # One live ssh per remote node that is up: health + memory + busy marker together.
    live = {n: health_fn(c["host"]) for n, c in nodes.items()
            if c["host"] != "local" and levels[n] in R.UP}
    mem = {n: R.mem_busy(snap, n, t, cfg,
                         None if c["host"] == "local" else (lambda _n: live.get(_n)))
           for n, c in nodes.items() if levels[n] in R.UP}
    tele = P.read_state(LV.OUT / "live.json")
    for n in mem:
        mem[n] = R.reposo_busy(tele, n, t) or mem[n]
    busy = {n: (h or {}).get("busy") for n, h in live.items()}
    hist = R.history(LEDGER.read_text(encoding="utf-8").splitlines()) if LEDGER.exists() else {}
    return R.route(kind, worker, str(repo_dir.resolve()), nodes, cfg, levels, running, mem, hist,
                   SENSITIVE, busy), levels


def origin_session(a=None) -> str | None:
    """The Claude Code session that asked for this job (the visor shows it as win->mac).
    watch's children get ENJAMBRE_ORIGIN_SESSION from job.json, possibly empty: never the id of
    whatever session happened to start watch. A --no-queue run (only watch uses it) never falls
    back to CLAUDE_CODE_SESSION_ID: a watch started before this field existed passes nothing, and
    its inherited session id would stamp every queued job with the wrong session."""
    if "ENJAMBRE_ORIGIN_SESSION" in os.environ:
        return os.environ["ENJAMBRE_ORIGIN_SESSION"] or None
    if getattr(a, "no_queue", False):
        return None
    v = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    return v if re.fullmatch(r"[0-9a-f-]{8,64}", v) else None


def enqueue_job(a, tid: str, text: str, level: str, route_info: dict) -> int:
    knobs = {k: os.environ[k] for k in KNOBS if k in os.environ}
    try:
        job = P.enqueue(STATE, tid, text,
                        {"node": a.node, "repo": a.repo, "worker": a.worker, "knobs": knobs,
                         "ref": getattr(a, "ref", None), "level_at_enqueue": level, "origin_session": origin_session(a), **route_info})
    except P.QueueError as e:
        print(f"enjambre: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"queued": tid, "node": a.node, "level": level,
                      "dir": "state/" + job.relative_to(STATE).as_posix(), **route_info},
                     ensure_ascii=False))
    return 0


def cmd_run(a) -> int:
    nodes = load_nodes()
    if a.node and a.node not in nodes:
        print(f"unknown node {a.node}", file=sys.stderr)
        return 2
    try:
        check_repo_name(a.repo)
        check_ref(getattr(a, "ref", None))
    except ContractError as e:
        print(f"enjambre: {e}", file=sys.stderr)
        return 3
    repo_dir = PORTFOLIO / a.repo
    task = Path(a.task)
    try:
        tid = validate(repo_dir, task, a.worker)
    except ContractError as e:
        print(f"enjambre: {e}", file=sys.stderr)
        return 3
    cfg = R.load(ROUTING)
    text = task.read_text(encoding="utf-8")
    kind, kind_src, classify_cost = resolve_kind(a, text, cfg)
    route_info = {"kind": kind, "kind_source": kind_src}
    if getattr(a, "ref", None):
        route_info["ref"] = a.ref
    if frontmatter_flag(text, "test") is True:   # `test: true` in the TASK = a test, not work (F3)
        route_info["test"] = True
    if classify_cost is not None:
        route_info["classify_cost_usd"] = classify_cost
    # prompt 20: priority from --priority, else the TASK's `priority:`, else normal
    flags = S.task_flags(text)
    route_info.update(S.job_model(kind, getattr(a, "priority", None) or flags["priority"],
                                  flags["resumable"], flags["preempt_between_turns"]))
    routed_level = None
    if a.node:
        route_info.update(routed_by=getattr(a, "routed_by", None) or "explicit",
                          route_reason=getattr(a, "route_reason", None) or f"--node {a.node}")
        rest = R.reposo_busy(P.read_state(LV.OUT / "live.json"), a.node, time.time())
        if rest:   # the owner chose the node: warn, do not override (DECISIONES 265)
            print(f"enjambre: warning, {a.node} is in {rest[1]}; dispatching anyway (--node)",
                  file=sys.stderr)
            route_info["route_reason"] += f"; warned: {rest[1]}"
    else:
        d, levels = decide(kind, a.worker, repo_dir, nodes, cfg, time.time())
        route_info.update(routed_by=d["routed_by"], route_reason=d["reason"])
        if d.get("device"):
            route_info["device"] = d["device"]
        if d["diverted_from"]:
            route_info["diverted_from"] = d["diverted_from"]
        print(f"enjambre route [{d['routed_by']}] {d['action']}: {d['reason']}", file=sys.stderr)
        if getattr(a, "dry_run", False):
            print(json.dumps(dict(d, id=tid, **route_info), ensure_ascii=False))
            return 0
        if d["action"] == "refuse":
            return 2
        a.node = d["node"]
        if d["action"] == "queue" and getattr(a, "engine", "claude") == "local":
            # The queue only knows the claude worker: a queued local job would come back as a
            # `claude -p` run. The model manager queues on its own; retry when the node is up.
            print("enjambre: --engine local is not queued; run it again when the node is ready",
                  file=sys.stderr)
            return 2
        if d["action"] == "queue":
            return enqueue_job(a, tid, text, levels.get(a.node), route_info)
        routed_level = levels.get(a.node)
    if getattr(a, "dry_run", False):
        print(json.dumps(dict(route_info, id=tid, action="run", node=a.node), ensure_ascii=False))
        return 0
    node = nodes[a.node]
    cmds = L.agents_cmds(repo_dir)
    try:
        allowed = L.allowed_tools(kind, cmds["tests"])
    except ValueError as e:
        print(f"enjambre: {e}", file=sys.stderr)
        return 3
    # Same text for both paths: the tests preamble names the one command the agent may run.
    body = (L.tests_preamble(cmds["tests"]) if kind == "tests" else "") + text
    if getattr(a, "detach", False):
        push, push_why = push_policy(text, repo_dir)
        route_info["push_policy"] = push_why
        return run_detached(a, tid, kind, body, allowed, node, route_info, push, push_why)
    if a.worker not in node["workers"] and not a.worker.startswith("stub-"):
        print(f"node {a.node} has no worker {a.worker}", file=sys.stderr)
        return 2
    if node["host"] == "local":
        print("local node not implemented in the skeleton", file=sys.stderr)
        return 2

    host = node["host"]
    if getattr(a, "engine", "claude") == "local":
        # Local model on the node (modelos-mac): no git, no queue; its own server queues.
        import engine_local
        entry = engine_local.run(tid, task.read_text(encoding="utf-8"), a.node, host,
                                 STATE / "local")
        entry["repo"] = a.repo
        entry["origin_session"] = origin_session(a)
        entry.update(route_info)
        append_ledger(entry)
        print(json.dumps(entry, ensure_ascii=False))
        return entry["exit"]
    if not a.no_queue and routed_level is None:
        # Trust watch's view if it is fresh; otherwise ask the node now (one ssh call).
        level = (None if node.get("host_override") else
                 P.fresh_level(P.read_state(NODES_JSON), a.node, time.time())) or PROBE(host)
        if level != "ready":
            return enqueue_job(a, tid, text, level, route_info)
    if route_info["priority"] in S.LOW and not a.no_queue:   # watch already admitted a --no-queue run
        ok, state, reason = admission(route_info, a.node, nodes, [])
        if not ok:
            print(f"enjambre scheduler [{route_info['priority']}] {state}: {reason}", file=sys.stderr)
            return enqueue_job(a, tid, text, "ready", dict(route_info, state=state, reason=reason,
                                                           state_since=P.now_iso()))
    push, push_why = push_policy(text, repo_dir)  # after the queue: a queued job asks nobody
    route_info["push_policy"] = push_why
    if preflight(a.node, host, a.repo):
        return 3
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    # Ship the worker each run: the node always runs the version reviewed here.
    up = sh(["ssh", "-o", "BatchMode=yes", host, "mkdir -p ~/.enjambre"])
    up = up if up.returncode else sh(["scp", "-q", str(WORKER), str(TMUX_RUN), f"{host}:.enjambre/"])
    if up.returncode:
        print("cannot reach node: " + up.stderr.strip(), file=sys.stderr)
        return 4
    # ssh does not forward the environment: pass the worker's knobs on the command line.
    knobs = [f"{k}={v}" for k, v in os.environ.items()
             if k in ("ENJAMBRE_MAX_TURNS", "ENJAMBRE_NODE_PUSH") and re.fullmatch(r"[0-9]+", v)]
    if not push:  # the node-side push skips gitleaks: never when the policy says no
        knobs = [k for k in knobs if not k.startswith("ENJAMBRE_NODE_PUSH=")]
    if getattr(a, "ref", None):  # checked by check_ref: no shell or git metacharacters
        knobs.append("ENJAMBRE_REF=" + a.ref)
    if kind == "tests":  # base64: the list crosses ssh, env and tmux-run.sh untouched
        knobs.append("ENJAMBRE_ALLOWED_B64=" + base64.b64encode(allowed.encode()).decode())
    prefix = S.os_prefix(S.load(), route_info["priority"], node.get("os", ""))
    if prefix:          # baja/nocturna: worker.sh runs the agent under taskpolicy/nice (prompt 20)
        knobs.append("ENJAMBRE_PRIO_B64=" + base64.b64encode(" ".join(prefix).encode()).decode())
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", host, "env", *knobs, "bash",
                        ".enjambre/worker.sh", a.repo, tid, a.worker, a.node],
                       input=body.encode("utf-8"), capture_output=True, **P.no_console())
    save_job_log(tid, r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace"))
    out = r.stdout.decode("utf-8", "replace").strip().splitlines()
    try:
        res = json.loads(out[-1]) if out else {}
    except json.JSONDecodeError:
        res = {"raw": out[-1][:300]}
    note = ""
    if res.get("branch") and not res.get("pushed"):
        ok, note = bring_branch_home(node, a.repo, res["branch"], push, push_why)
        res["pushed_by_orchestrator"] = ok
    entry = {
        "id": tid, "node": a.node, "worker": a.worker, "repo": a.repo,
        "branch": res.get("branch"), "start": started,
        "end": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "exit": r.returncode, "status": job_status(r.returncode, res.get("detail")),
        "permission_denials": (res.get("detail") or {}).get("permission_denials") or None,
        "duration_s": res.get("duration_s"),
        "pushed": bool(res.get("pushed") or res.get("pushed_by_orchestrator")),
        "usage": (res.get("detail") or {}).get("usage"),
        "cost_usd": (res.get("detail") or {}).get("cost_usd"),
        "duration_api_ms": (res.get("detail") or {}).get("duration_api_ms"),
        # worker.sh already caps it at 500 chars; the bandeja reports it back (leak-checked)
        "result": ((res.get("detail") or {}).get("result") or None),
        "note": note or None,
        "origin_session": origin_session(a),
        **route_info,
    }
    append_ledger(entry)
    print(json.dumps(entry, ensure_ascii=False))
    if r.stderr:
        sys.stderr.write(r.stderr.decode("utf-8", "replace"))
    return r.returncode


def run_detached(a, tid: str, kind: str, body: str, allowed: str, node: dict, route_info: dict,
                 push: bool, push_why: str) -> int:
    """`run --detach`: the agent runs for hours on the node under jobrun.py; this returns as
    soon as the node confirms the start. collect (watch, or `enjambre collect`) brings it home."""
    if node["host"] == "local":
        print("enjambre: --detach needs a remote node", file=sys.stderr)
        return 2
    if preflight(a.node, node["host"], a.repo):
        return 3
    spec = {"id": tid, "kind": kind, "repo": a.repo, "task": body, "allowed": allowed,
            "ref": getattr(a, "ref", None), "max_turns": a.max_turns, "budget_usd": a.budget, "model": a.model,
            "beat_s": a.beat, "beat_log_s": a.beat_log, "prices": L.prices_for(a.model),
            **sched_spec(route_info, node)}
    meta = {"origin_session": origin_session(a), "budget_usd": a.budget, "push": push,
            "push_why": push_why, "model": a.model, "max_turns": a.max_turns, **route_info}
    nodes = load_nodes()
    ok, state, reason = admission(meta, a.node, nodes, [])
    print(f"enjambre scheduler [{meta['priority']}] {state}: {reason}", file=sys.stderr)
    if not ok:
        return enqueue_detached(a.node, nodes, spec, meta, state, reason)
    meta["sched_reason"] = reason
    res = L.launch(a.node, node["host"], spec, meta)
    print(json.dumps(res, ensure_ascii=False))
    if not res.get("launched"):
        return 4
    LV.kick()   # the visor's live loop reads it now, not at its next tick
    print(f"seguir: python enjambre.py status --follow {tid}  |  recoger: python enjambre.py collect",
          file=sys.stderr)
    return 0


def sched_spec(model: dict, node: dict) -> dict:
    """What jobrun.py needs from the scheduler model: the class, the OS prefix for its command."""
    return {"priority": model["priority"], "resumable": model["resumable"],
            "between_turns": model["between_turns"],
            "prio_prefix": S.os_prefix(S.load(), model["priority"], node.get("os", ""))}


def cmd_script(a) -> int:
    """A heavy command with no agent ($0): only a name declared in the repo's AGENTS.md
    (`- script-cmd <name>: `...``) runs, never a command line typed here."""
    nodes = load_nodes()
    if a.node not in nodes or nodes[a.node]["host"] == "local":
        print(f"script needs a remote node: {a.node}", file=sys.stderr)
        return 2
    try:
        check_repo_name(a.repo)
    except ContractError as e:
        print(f"enjambre: {e}", file=sys.stderr)
        return 3
    repo_dir = PORTFOLIO / a.repo
    if not (repo_dir / "AGENTS.md").is_file():
        print(f"enjambre: {a.repo} has no AGENTS.md", file=sys.stderr)
        return 3
    scripts = L.agents_cmds(repo_dir)["scripts"]
    if a.cmd not in scripts:
        print(f"enjambre: '{a.cmd}' is not declared in {a.repo}/AGENTS.md "
              f"(declared: {', '.join(sorted(scripts)) or 'none'})", file=sys.stderr)
        return 3
    args = [x for x in (a.args or []) if x != "--"]
    tid = L.job_id(a.cmd)
    model = S.job_model("script", getattr(a, "priority", None), getattr(a, "resumable", False))
    spec = {"id": tid, "kind": "script", "repo": a.repo, "cmd": scripts[a.cmd], "args": args,
            "beat_s": a.beat, "beat_log_s": a.beat_log, **sched_spec(model, nodes[a.node])}
    meta = {"origin_session": origin_session(a), "cmd_name": a.cmd, "args": args,
            "routed_by": "explicit", "route_reason": f"script --node {a.node}", **model}
    ok, state, reason = admission(meta, a.node, nodes, [])
    print(f"enjambre scheduler [{meta['priority']}] {state}: {reason}", file=sys.stderr)
    if a.dry_run:
        print(json.dumps(dict(spec, node=a.node, action="launch" if ok else "queue", state=state,
                              reason=reason), ensure_ascii=False))
        return 0
    if not ok:
        return enqueue_detached(a.node, nodes, spec, meta, state, reason)
    if preflight(a.node, nodes[a.node]["host"], a.repo):
        return 3
    meta["sched_reason"] = reason
    res = L.launch(a.node, nodes[a.node]["host"], spec, meta)
    print(json.dumps(res, ensure_ascii=False))
    if not res.get("launched"):
        return 4
    LV.kick()
    print(f"seguir: python enjambre.py status --follow {tid}", file=sys.stderr)
    if a.follow:
        return cmd_follow(tid, a.interval)
    return 0


CLONE_URL = re.compile(r"(https://|git@)[A-Za-z0-9@:./_~-]+")


def clone_script(repo: str, url: str) -> str:
    """The node-side line: idempotent, one word on stdout (`exists` | `cloned`)."""
    d = f'"$HOME/Documents/Portfolio/{repo}"'
    return (f"if [ -d {d}/.git ]; then echo exists; "
            f"elif [ -e {d} ]; then echo 'not a git repo: ~/Documents/Portfolio/{repo}' >&2; exit 3; "
            f"else git clone --quiet {shlex.quote(url)} {d} && echo cloned; fi")


def origin_url(repo: str) -> str | None:
    r = sh(["git", "-C", str(PORTFOLIO / repo), "remote", "get-url", "origin"])
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def clone_on_node(node_name: str, host: str, repo: str, url: str, sh_fn=None) -> tuple[int, str]:
    """Clone through tmux-run.sh: the window is born in the GUI session, so git reads the login
    keychain (HTTPS to a private repo). Over plain ssh it would fail as "not logged in"."""
    run = sh_fn or sh
    up = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host, "mkdir -p ~/.enjambre"])
    up = up if up.returncode else run(["scp", "-q", str(TMUX_RUN), f"{host}:.enjambre/"])
    if up.returncode:
        return 4, "cannot reach node: " + (up.stderr or "").strip()[-200:]
    r = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
             "bash .enjambre/tmux-run.sh -t 900 -- bash -c " + shlex.quote(clone_script(repo, url))])
    out = ((r.stdout or "").strip().splitlines() or [""])[-1]
    return r.returncode, out if r.returncode == 0 else ((r.stderr or "").strip()[-300:] or out)


def cmd_clone(a) -> int:
    """`enjambre clone --node mac <repo> [--url ...]`: no agent, no TASK, no sandbox script
    (the three `tests` jobs that tried it failed: brace expansion refused, a command not
    approved, a script nobody committed). One ledger line, worker `clone`."""
    try:
        check_repo_name(a.repo)
    except ContractError as e:
        print(f"enjambre: {e}", file=sys.stderr)
        return 3
    nodes = load_nodes()
    if a.node not in nodes or nodes[a.node]["host"] == "local":
        print(f"clone needs a remote node: {a.node}", file=sys.stderr)
        return 2
    url = a.url or origin_url(a.repo)
    if not url:
        print(f"enjambre: {a.repo} has no local origin to copy; pass --url", file=sys.stderr)
        return 3
    if not CLONE_URL.fullmatch(url):
        print(f"enjambre: --url is not an https:// or git@ URL: {url!r}", file=sys.stderr)
        return 3
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    t0 = time.time()
    rc, out = clone_on_node(a.node, nodes[a.node]["host"], a.repo, url)
    entry = {"id": L.job_id(f"clone-{a.repo}"), "node": a.node, "worker": "clone", "repo": a.repo,
             "branch": None, "start": started,
             "end": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "exit": rc, "status": "ok" if rc == 0 else "failed", "pushed": False,
             "duration_s": int(time.time() - t0), "cost_usd": 0, "kind": "clone",
             "result": out[:300] or None, "reason": None if rc == 0 else out[:160] or f"exit {rc}",
             "url": url, "routed_by": "explicit", "route_reason": f"clone --node {a.node}",
             "origin_session": origin_session(a)}
    append_ledger(entry)
    print(json.dumps(entry, ensure_ascii=False))
    return rc


def bring_home_detached(meta: dict, st: dict) -> tuple[bool, str]:
    node = load_nodes().get(meta["node"]) or {"host": meta["host"]}
    return bring_branch_home(node, meta["repo"], st["branch"], bool(meta.get("push")),
                             meta.get("push_why") or "")


def collect_now(only: str | None = None, log=lambda m: None) -> list:
    return L.collect(append_ledger, bring_home_detached, P.notify, only=only, log=log)


def cmd_collect(a) -> int:
    for d in collect_now(a.id):
        print(json.dumps(d, ensure_ascii=False))
    for d in puente_collect(a.id):
        print(json.dumps({"puente": d}, ensure_ascii=False))
    return 0


def cmd_follow(tid: str, interval: float = 5) -> int:
    try:
        st = L.follow(tid, interval)
    except KeyError:
        print(f"enjambre: unknown job {tid} (no state/jobs/{tid}/job.json)", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n(dejé de seguir; el trabajo sigue corriendo)", file=sys.stderr)
        return 0
    if st.get("state") == "dead":
        print(f"el trabajo murió sin terminar: python enjambre.py resume {tid}", file=sys.stderr)
        return 3
    for d in collect_now(tid):
        print(json.dumps(d, ensure_ascii=False))
    return 0 if st.get("state") == "ok" else 1


def cmd_cerrar(a) -> int:
    """`enjambre cerrar <id> --razon "..."`: a dead long job nobody will resume -> one terminal
    ledger line, and watch stops logging it every minute."""
    res = L.close(a.id, a.razon, append_ledger, a.estado)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("closed") else 2


def cmd_logs(a) -> int:
    """`enjambre logs <id> [--tail N]`: the node-side logs (supervisor.out has the traceback of a
    supervisor that died before writing a status) -> state/jobs/<id>/node.log, and printed."""
    meta = L.read_meta(a.id)
    nodes = load_nodes()
    node = (meta or {}).get("node") or a.node
    if node not in nodes or nodes[node]["host"] == "local":
        print(f"enjambre: unknown remote node {node}", file=sys.stderr)
        return 2
    host = P.job_host(meta) if meta else nodes[node]["host"]
    rc, text = L.node_logs(host, a.id, a.tail)
    if rc == 2 and not text.strip().startswith("=="):
        print(f"enjambre: {text}", file=sys.stderr)
        return 2
    d = STATE / "jobs" / a.id
    d.mkdir(parents=True, exist_ok=True)
    (d / "node.log").write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    print(f"-> state/jobs/{a.id}/node.log", file=sys.stderr)
    return 0 if rc == 0 else rc


def cmd_resume(a) -> int:
    res = L.resume(a.id)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("launched") else 4


def cmd_kill(a) -> int:
    res = L.kill(a.id)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("killed") else 4


def cmd_history(a) -> int:
    """The measured table the router uses: seconds per (kind, node), ok runs only."""
    cfg = R.load(ROUTING)
    hist = R.history(LEDGER.read_text(encoding="utf-8").splitlines()) if LEDGER.exists() else {}
    n_min = cfg["defaults"]["history_min_runs"]
    print(f"{'kind':<12} {'node':<5} {'ok runs':>7}  median (>= {n_min} runs)")
    for k, n, cnt, med in R.history_table(hist, cfg["kinds"], load_nodes(), n_min):
        print(f"{k:<12} {n:<5} {cnt:>7}  {med}")
    return 0



def print_live(name: str) -> None:
    """status: one live ssh call per remote node for `gui: sí/no` and the puente sessions."""
    host = load_nodes().get(name, {}).get("host")
    if not host or host == "local":
        return
    k = PC.key()
    r = P._run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, PC.live_line(k)], 20)
    if PC.GUI_MARK not in (r.stdout or ""):
        print("         gui ?   (sin respuesta por ssh)")
        return
    v = PC.parse_live(r.stdout)
    print(f"         gui {'sí' if v['gui'] else 'no (ventana de inicio de sesión)'}")
    if v["sessions"] is None:
        why = v.get("puente_error") or ("sin clave ENJAMBRE_PUENTE_KEY" if not k else "no responde")
        print(f"         puente: {why}")
        return
    total = sum(x.get("cost_usd", 0) for x in v["sessions"])
    print(f"         puente: {len(v['sessions'])} sesión(es), US${total:.4f}")
    for x in v["sessions"]:
        print("           " + PC.fmt_session(x))


def cmd_status(a) -> int:
    if getattr(a, "follow", None):
        return cmd_follow(a.follow, a.interval)
    st = P.read_state(NODES_JSON)
    if st.get("nodes"):
        age = time.time() - dt.datetime.fromisoformat(st["updated"]).timestamp()
        stale = "" if age <= P.FRESH_S else "   (STALE: is `enjambre watch` running?)"
        print(f"nodes  (watch, {int(age)} s ago){stale}")
        for name, n in st["nodes"].items():
            print(f"  {name:<6} {n['level']:<6} since {n['since']}")
            if n.get("busy"):
                print(f"         {B.describe(n['busy'])}")
            if getattr(a, "live", True):  # live, whatever watch last saw
                print_live(name)
            mv = n.get("mounts")
            for m in (mv or {}).get("mounts", []):
                print(f"         share {m['name']:<16} {'ok' if m['ok'] else 'NO':<3} {m['state']:<13} {m['path']}")
    else:
        print("nodes  (no state/nodes.json: start `enjambre watch`)")
        for name, cfg in load_nodes().items():
            if getattr(a, "live", True) and cfg["host"] != "local":
                print(f"  {name:<6} (en vivo)")
                print_live(name)
    for p in sorted(L.JOBS.glob("*/job.json")):
        m = L.read_meta(p.parent.name) or {}
        if not m.get("collected"):
            js = P.read_state(p.parent / "status.json")
            print(f"long   {m.get('id')}  {m.get('kind')} [{js.get('priority') or m.get('priority') or 'normal'}] on {m.get('node')}: "
                  f"{L.beat_line(js) if js else 'launched ' + str(m.get('launched'))}")
    q = P.queued(STATE)
    print(f"queue  {len(q)} waiting")
    for m in q:
        print(f"  {m['id']:<24} -> {m['node']} ({m['repo']}, since {m['enqueued']}) "
              f"[{m.get('priority') or 'normal'}] {m.get('state') or 'queued'}"
              + (f": {m['reason']}" if m.get("reason") else ""))
    if not LEDGER.exists():
        print("no jobs yet")
        return 0
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        # Lines written before the status field existed fall back to the exit code.
        status = e.get("status") or ("ok" if e["exit"] == 0 else "failed")
        print(f"{e['start']}  {e['id']:<24} {e['node']:<4} {e['worker']:<9} {status:<10} exit={e['exit']} "
              f"pushed={e['pushed']} {e.get('branch') or ''}"
              + (f"  [{e['reason'][:90]}]" if status != "ok" and e.get("reason") else ""))
    return 0



# ---------------------------------------------------------------- puente (prompt 16, tutorial 26)

def puente_host(node: str) -> str:
    nodes = load_nodes()
    if node not in nodes or nodes[node]["host"] == "local":
        raise PC.PuenteError(f"node {node!r} has no puente (only remote nodes run agent/puente.py)")
    return nodes[node]["host"]


def puente_collect(only: str | None = None) -> list:
    hosts = {n: c["host"] for n, c in load_nodes().items() if c["host"] != "local"}
    return PC.collect(STATE, hosts, append_ledger, only=only)


def print_event(ev: dict) -> None:
    t = ev.get("t")
    if t == "text":
        print(ev["text"], flush=True)
    elif t == "tool":
        print(f"  · {ev['name']} {ev.get('input', '')}", flush=True)
    elif t == "denied":
        print(f"  ✗ denegada: {ev['name']} {ev.get('input', '')}", flush=True)
    elif t == "queued":
        print("  (en cola en la Mac)", flush=True)
    elif t == "start":
        print("  (empieza; reanuda la sesión)" if ev.get("resume") else "  (empieza; sesión nueva)", flush=True)
    elif t == "moved":
        print(f"  (capturas movidas a: {', '.join(ev.get('files') or [])})", flush=True)
    elif t == "init":
        mcp = ", ".join(f"{x.get('name')}:{x.get('status')}" for x in ev.get("mcp") or []) or "ninguno"
        print(f"  (sesión: {ev.get('n_tools')} herramientas, MCP {mcp}, permisos {ev.get('permission_mode')})", flush=True)
    elif t == "error":
        print(f"  ERROR {ev.get('text')}", flush=True)
    elif t == "end":
        print(f"-- {ev['state']} · US${ev.get('cost_usd') or 0:.4f}", flush=True)


def follow_turn(host: str, tid: str, repo: str, node: str, stream_fn=None) -> int:
    state = None
    hint = f"enjambre chat {node} --repo {repo} --attach {tid}"
    try:
        for ev in (stream_fn or PC.stream)(host, tid):
            print_event(ev)
            if ev.get("t") == "end":
                state = ev["state"]
    except KeyboardInterrupt:
        print(f"\n(dejas de mirar; el turno sigue en la Mac. Retomar: {hint})")
        return 130
    if state is None:
        print(f"(se cortó la conexión; el turno sigue en la Mac. Retomar: {hint})")
        return 75
    puente_collect(tid)
    return 0 if state == "ok" else 1


def puente_send(a) -> tuple[str, str]:
    host = puente_host(a.node)
    repo_dir = PORTFOLIO / a.repo
    if not (repo_dir / "AGENTS.md").is_file():
        raise PC.PuenteError(f"{a.repo} has no AGENTS.md here (the allowlist comes from it)")
    allowed = PC.allowed_for(repo_dir, L.agents_cmds, L.BASE_TOOLS, edit=a.edit)
    req = {"repo": a.repo, "message": a.message, "allowed": allowed, "model": a.model,
           "max_turns": a.max_turns, "budget_usd": a.budget, "origin": "win",
           "origin_session": origin_session(a)}
    r = PC.request(host, "POST", "/turns", req)
    tid = r["turn"]
    PC.remember(STATE, tid, {"turn": tid, "node": a.node, "repo": a.repo, "sent": P.now_iso(),
                             "origin_session": req["origin_session"]})
    sid = (r.get("session_id") or "nueva")[:8]
    print(f"turno {tid} -> {a.node}:{a.repo} (sesión {sid}, posición {r.get('position')})", flush=True)
    return host, tid


def cmd_chat(a) -> int:
    """Synchronous: send (or attach) and print the turn's stream until it ends."""
    try:
        host = puente_host(a.node)
        if a.attach:
            if not PC.TID_RE.fullmatch(a.attach):
                raise PC.PuenteError(f"not a turn id: {a.attach}")
            return follow_turn(host, a.attach, a.repo, a.node)
        if not a.message:
            ss = {s["repo"]: s for s in PC.request(host, "GET", "/sessions")["sessions"]}
            s = ss.get(a.repo)
            if not s:
                print(f"{a.repo}: sin sesión en {a.node} todavía")
                return 0
            print(PC.fmt_session(s))
            tid = s.get("running") or s.get("last_turn")
            return follow_turn(host, tid, a.repo, a.node) if tid else 0
        host, tid = puente_send(a)
        return follow_turn(host, tid, a.repo, a.node)
    except PC.PuenteError as e:
        print(f"puente: {e}")
        return 2


def cmd_send(a) -> int:
    """Asynchronous: queue the turn and return; the result comes back through the ledger."""
    try:
        puente_send(a)
    except PC.PuenteError as e:
        print(f"puente: {e}")
        return 2
    print(f"vuelve por el ledger (watch cada minuto, o: enjambre collect). "
          f"Mirar: enjambre chat {a.node} --repo {a.repo}")
    return 0


def cmd_sessions(a) -> int:
    try:
        r = PC.request(puente_host(a.node), "GET", "/sessions")
    except PC.PuenteError as e:
        print(f"puente: {e}")
        return 2
    ss = r["sessions"]
    print(f"{a.node}: {len(ss)} sesión(es) · US${sum(s.get('cost_usd', 0) for s in ss):.4f} en total")
    for s in ss:
        print("  " + PC.fmt_session(s))
    return 0


RENDER_SCRIPT = HERE / "tools" / "render_stl.py"
BLENDER_MAC = "/Applications/Blender.app/Contents/MacOS/Blender"


def render_remote_cmd(stl_rel: str, out_name: str, device: str, samples: int, res: str,
                      unit: str) -> str:
    """The node-side shell line. Paths are Portfolio-relative because both machines share the
    layout; "$HOME" expands on the node, the quoted rest never does (spaces, accents)."""
    q = shlex.quote
    return (f'mkdir -p "$HOME/renders" && bash "$HOME/.enjambre/tmux-run.sh" -t 1800 -- '
            f'{BLENDER_MAC} -b --factory-startup -P "$HOME/.enjambre/render_stl.py" -- '
            f'"$HOME/Documents/Portfolio/"{q(stl_rel)} "$HOME/renders/"{q(out_name)} '
            f'--device {q(device)} --samples {int(samples)} --res {q(res)} --unit {q(unit)}')


def render_device(asked: str, host: str, health_fn=None) -> tuple[str, str]:
    """--device auto: METAL unless a live busy marker on the node says a long job holds the GPU
    (routing.toml kinds.render cpu_fallback); then CPU, slower but no clash. An explicit
    --device is obeyed, with a warning when it collides."""
    h = (health_fn or model_health)(host) or {}
    m = h.get("busy")
    held = bool(m and m.get("alive")) and m.get("tipo") != "render"
    if asked == "auto":
        return ("CPU", "busy marker: " + B.describe(m)) if held else ("METAL", B.describe(m))
    if asked == "METAL" and held:
        return "METAL", "forced by --device METAL despite " + B.describe(m)
    return asked, "--device " + asked


def cmd_render(a) -> int:
    """WHY not `run`: run is the contract for an agent that edits a repo and returns a branch.
    A render edits nothing and runs no model; the STLs often live in someone else's repo
    (Proyecto-3d is a teammate's), where a job branch would be pushed to their remote."""
    nodes = load_nodes()
    if a.node not in nodes or nodes[a.node]["host"] == "local":
        print(f"render needs a remote node with Blender: {a.node}", file=sys.stderr)
        return 2
    host = nodes[a.node]["host"]
    stl_rel = a.stl.replace("\\", "/")
    stem = re.sub(r"[^a-z0-9]+", "-", Path(stl_rel).stem.lower()).strip("-")[:40]
    rid = f"render-{stem}-{dt.datetime.now().strftime('%H%M%S')}"
    out_name = f"{rid}.png"
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    up = sh(["scp", "-q", str(RENDER_SCRIPT), str(TMUX_RUN), f"{host}:.enjambre/"])
    if up.returncode:
        print("cannot reach node: " + up.stderr.strip(), file=sys.stderr)
        return 4
    # A part just exported from Onshape sits on this machine, not yet in any git remote: if the
    # file exists here, the node renders this exact copy (same Portfolio-relative path there).
    local_stl = PORTFOLIO / stl_rel
    copied = local_stl.is_file()
    if copied:
        parent = shlex.quote(str(Path(stl_rel).parent.as_posix()))
        sh(["ssh", "-o", "BatchMode=yes", host, f'mkdir -p "$HOME/Documents/Portfolio/"{parent}'])
        cp = sh(["scp", "-q", str(local_stl), f"{host}:Documents/Portfolio/{stl_rel}"])
        if cp.returncode:
            print("cannot copy the STL to the node: " + cp.stderr.strip(), file=sys.stderr)
            return 4
    device, device_reason = render_device(a.device, host)
    print(f"enjambre render: device {device} ({device_reason})", file=sys.stderr)
    r = sh(["ssh", "-o", "BatchMode=yes", host,
            render_remote_cmd(stl_rel, out_name, device, a.samples, a.res, a.unit)])
    info = {}
    for line in reversed(r.stdout.splitlines()):
        if line.startswith("{"):
            info = json.loads(line)
            break
    local = Path(a.out) if a.out else STATE / "renders"
    local.mkdir(parents=True, exist_ok=True)
    fetched = False
    if r.returncode == 0 and info:
        fetched = sh(["scp", "-q", f"{host}:renders/{out_name}", str(local / out_name)]).returncode == 0
    rc = 0 if (r.returncode == 0 and fetched) else (r.returncode or 1)
    save_job_log(rid, r.stdout + r.stderr)
    entry = {"id": rid, "node": a.node, "worker": "blender", "repo": None, "branch": None,
             "start": started, "end": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "exit": rc, "status": job_status(rc, None), "stl": stl_rel, "copied_from_win": copied,
             "png": str(local / out_name) if fetched else None,
             "duration_s": info.get("total_s"), "render": info or None,
             "pushed": False, "cost_usd": 0, "kind": "render", "device_reason": device_reason,
             "origin_session": origin_session(a)}
    append_ledger(entry)
    print(json.dumps(entry, ensure_ascii=False))
    if rc:
        sys.stderr.write(r.stderr[-2000:])
    return rc


MODEL_STATES = ("en uso", "sirve", "por probar", "no sirve", "descartado")


def cmd_modelo(a) -> int:
    """The owner's verdict on a local model, written on the node that runs the manager
    (PATCH http://127.0.0.1:8765/catalog/<key> over ssh there: no tunnel, no CORS). The visor's
    edit box uses the tunnel when it is open and shows this command when it is not."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", a.key):
        print("clave de modelo inválida", file=sys.stderr)
        return 2
    host = load_nodes()[a.node]["host"]
    body = json.dumps({"status": a.status, "nota": a.nota or ""}, ensure_ascii=False).encode("utf-8")
    r = L.ssh(host, "curl -s -w '\\n%%{http_code}' -X PATCH -H 'content-type: application/json' "
                    "--data-binary @- http://127.0.0.1:8765/catalog/%s" % a.key, stdin=body, timeout=30)
    out = r.stdout.decode("utf-8", "replace").rstrip().rsplit("\n", 1)
    code = out[-1] if out else ""
    print(out[0] if len(out) > 1 else r.stderr.decode("utf-8", "replace").strip())
    return 0 if code == "200" else 1


def cmd_visor(a) -> int:
    """The local visor as a Windows app (tools/visor/instalar.py); --demo: the synthetic swarm."""
    sys.path.insert(0, str(HERE / "tools" / "visor"))
    if a.demo is not None:
        import demo
        return demo.main(([a.demo] if a.demo else []) + (["--serve"] if a.serve else [])
                         + (["--export"] if a.export else []))
    import instalar
    return instalar.main(["--install"] if a.install else ["--follow"] if a.follow else [])


def cmd_pull(a) -> int:
    repo_dir = PORTFOLIO / a.repo
    r = sh(["git", "-C", str(repo_dir), "fetch", "origin", f"mac/{a.id}:mac/{a.id}"])
    sys.stderr.write(r.stderr)
    return r.returncode


# ------------------------------------------------------------------ scheduler glue (prompt 20)
LAUNCHING_S = 300   # a detached job launched this recently with no status yet holds its slot


def node_jobs(node: str, now: float | None = None) -> list:
    """What holds (or held) a slot on a node, with its class: watch's own running/ dir (synchronous
    runs) + detached jobs not collected yet, with the state their last status.json says. A dead
    or finished job holds nothing; one launched < 5 min ago with no status yet is `launching`."""
    now = now or time.time()
    out = []
    rd = STATE / "running"
    if rd.is_dir():
        for j in rd.iterdir():
            m = P.read_state(j / "job.json")
            if m.get("node") == node:
                out.append({"id": m.get("id") or j.name, "priority": m.get("priority") or "normal",
                            "state": "running", "kind": m.get("kind")})
    for p in sorted(L.JOBS.glob("*/job.json")):
        m = P.read_state(p)
        if not m or m.get("collected") or m.get("node") != node:
            continue
        st = P.read_state(p.parent / "status.json")
        state = st.get("state")
        if not state:
            t = LV.ts_of(m.get("resumed") or m.get("launched"))
            state = "launching" if t and now - t < LAUNCHING_S else "unknown"
        out.append({"id": m["id"], "priority": st.get("priority") or m.get("priority") or "normal",
                    "state": state, "kind": m.get("kind"), "reason": st.get("sched_reason")})
    return out


def node_context(node: str, nodes: dict, now: float) -> dict:
    """The telemetry admission reads: live.json's reading of the node, its age, the ring tail."""
    live = P.read_state(LV.OUT / "live.json")
    tele = (live.get("telemetria") or {}).get(node)
    ts = live.get("updated_ts")
    age = now - ts if isinstance(ts, (int, float)) else None
    try:
        ring = [json.loads(x) for x in LV.ring_path(node).read_text(encoding="utf-8").splitlines()[-40:]]
    except (OSError, ValueError):
        ring = []
    return {"tele": tele, "age": age, "ring": ring, "os": (nodes.get(node) or {}).get("os", "")}


def admission(meta: dict, node: str, nodes: dict, taken: list, now: float | None = None,
              cfg: dict | None = None, cap: int | None = None, hold: str | None = None) -> tuple:
    """scheduler.admit with this machine's view of the node. `taken`: admitted earlier this round;
    `cap`: watch's max_jobs for the node this round; `hold`: why the node takes nothing (reposo)."""
    now = now or time.time()
    if hold:
        return False, "waiting_resources", hold
    cfg = cfg or S.load()
    ctx = node_context(node, nodes, now)
    running = node_jobs(node, now) + [{"id": t["id"], "priority": t.get("priority"), "state": "running"}
                                      for t in taken]
    cap = (nodes.get(node) or {}).get("max_jobs", 1) if cap is None else cap
    return S.admit(meta, ctx["os"], running, cap, ctx["tele"],
                   ctx["age"], ctx["ring"], cfg, now)


def enqueue_detached(node: str, nodes: dict, spec: dict, meta: dict, state: str, reason: str) -> int:
    """A detached job (run --detach / script) the scheduler did not admit: it waits in watch's
    queue with its spec; watch launches it when admit() says yes (it never goes to running/)."""
    tid = spec["id"]
    text = spec.get("task") or f"script {meta.get('cmd_name')} {' '.join(meta.get('args') or [])}"
    try:
        P.enqueue(STATE, tid, text, dict(meta, node=node, repo=spec["repo"], worker="detached", kind=spec["kind"],
                                         launch="detached", spec=spec, host=nodes[node]["host"],
                                         state=state, reason=reason, state_since=P.now_iso()))
    except P.QueueError as e:
        print(f"enjambre: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"queued": tid, "node": node, "priority": meta.get("priority"), "state": state,
                      "reason": reason}, ensure_ascii=False))
    LV.kick()
    return 0


def launch_queued(meta: dict, log) -> bool:
    """watch: start an admitted detached job. On success it leaves the queue for done/ with a
    `launched` marker; on failure it stays queued with the reason."""
    rest = {k: v for k, v in meta.items() if k not in ("spec", "launch", "host", "state", "reason",
                                                         "state_since", "enqueued", "worker", "knobs")}
    res = L.launch(meta["node"], P.job_host(meta), meta["spec"], rest)
    if res.get("launched"):
        done = P.move(STATE, meta["id"], "queue", "done")
        (done / "launched").write_text(L.now() + "\n", encoding="utf-8")
        log.info("detached job %s launched on %s (%s)", meta["id"], meta["node"], meta.get("reason"))
        LV.kick()
        return True
    P.write_json_atomic(STATE / "queue" / meta["id"] / "job.json",
                        dict({k: v for k, v in meta.items() if k not in ("state", "reason")},
                             state="waiting_resources", reason="no se pudo lanzar: " + str(res.get("error"))[:200]))
    log.info("detached job %s: launch failed: %s", meta["id"], res.get("error"))
    return False


def spawn_job(meta: dict) -> subprocess.Popen:
    """Run a queued job as a child `enjambre run --no-queue`, so a long job never stalls the
    30 s loop. The child writes the ledger line, exactly as a hand-typed run would."""
    job = STATE / "running" / meta["id"]
    env = dict(os.environ, **meta.get("knobs", {}))
    env["ENJAMBRE_ORIGIN_SESSION"] = meta.get("origin_session") or ""
    # The child gets --node, so it would log routed_by=explicit: pass the original routing on.
    routed = []
    for key, flag in (("kind", "--kind"), ("routed_by", "--routed-by"), ("route_reason", "--route-reason"),
                      ("ref", "--ref")):
        if meta.get(key):
            routed += [flag, meta[key]]
    return subprocess.Popen(
        [P.python_for_children(), str(HERE / "enjambre.py"), "run", "--node", meta["node"],
         "--repo", meta["repo"], "--task", str(job / f"TASK-{meta['id']}.md"),
         "--worker", meta["worker"], "--no-queue", *routed],
        stdout=open(job / "run.out", "w"), stderr=open(job / "run.err", "w"),
        env=env, cwd=HERE, **P.no_console())


def reap(procs: dict, log) -> None:
    """Move finished jobs running/ -> done/, with their exit code next to their output."""
    for tid, pr in list(procs.items()):
        if pr.poll() is not None:
            (STATE / "running" / tid / "rc").write_text(str(pr.returncode))
            P.move(STATE, tid, "running", "done")
            log.info("job %s finished rc=%s", tid, pr.returncode)
            del procs[tid]


def watch_round(nodes: dict, procs: dict, t: float, probe_fn, notify_fn, spawn_fn, log,
                mounts_fn=None, busy_fn=None, alt_probe_fn=None) -> dict:
    """One round: probe, persist, reap finished jobs, dispatch what can start. Testable: every
    side effect except the state folder comes in as a function."""
    state = P.tick(nodes, P.read_state(NODES_JSON), probe_fn, t, notify_fn, log, mounts_fn, busy_fn)
    if alt_probe_fn:   # primary silent, host_alt answers -> state/route.json + level via the alt
        P.route_round(nodes, state, t, alt_probe_fn, notify_fn, log)
    P.write_json_atomic(NODES_JSON, state)
    reap(procs, log)
    levels = {n: s["level"] for n, s in state["nodes"].items()}
    caps = {n: c.get("max_jobs", 1) for n, c in nodes.items()}
    holds = {}
    tele = P.read_state(LV.OUT / "live.json")
    for n in caps:                       # reposo (prompt 19 §2.4): max_jobs 0 while it lasts
        rest = R.reposo_busy(tele, n, t)
        if rest:
            caps[n] = 0
            holds[n] = "esperando: nodo en " + rest[1]
            if P.queued(STATE, n):       # say it only when something is actually held back
                log.info("not dispatching to %s: %s", n, rest[1])
    cfg = S.load()
    all_nodes = load_nodes()

    def admit_fn(meta, node, taken, cap):
        return admission(meta, node, all_nodes, taken, t, cfg, cap, holds.get(node))
    for meta in P.pick(STATE, levels, caps, admit_fn):
        if meta.get("launch") == "detached":       # it never enters running/: jobs/ tracks it
            launch_queued(meta, log)
            continue
        P.write_json_atomic(STATE / "queue" / meta["id"] / "job.json", meta)   # state/reason travel along
        P.move(STATE, meta["id"], "queue", "running")
        procs[meta["id"]] = spawn_fn(meta)
        log.info("job %s dispatched to %s", meta["id"], meta["node"])
    return state


def bandeja_tick(t: float) -> dict:
    sys.path.insert(0, str(HERE / "tools" / "bandeja"))
    import bandeja
    return bandeja.tick(t)


def cmd_watch(a) -> int:
    pidfile = STATE / "watch.pid"
    if a.stop:
        try:
            pid = int(pidfile.read_text().strip())
        except (OSError, ValueError):
            print("watch: no pid file, nothing to stop")
            return 0
        try:
            os.kill(pid, signal.SIGTERM)  # by the pid this watch wrote, never by name
            print(f"watch: stopped pid {pid}")
        except OSError as e:
            print(f"watch: pid {pid} not running ({e})")
        return 0
    if a.boot and P.owner_logged_on():
        print("watch --boot: the owner is logged on; the logon task runs watch")
        return 0
    lock = P.InstanceLock(STATE / "watch.lock")
    task = Path(sys.executable).name.lower() == "pythonw.exe"   # the scheduled tasks run pythonw
    if not P.acquire_or_wait(lock, 180 if task and not a.boot else 0):   # logon task: let a --boot one hand over
        print("watch: another instance is running; not starting a second one")
        return 0
    pidfile.write_text(str(os.getpid()))
    log = logging.getLogger("enjambre.watch")
    if not log.handlers:
        h = logging.handlers.RotatingFileHandler(STATE / "watch.log", maxBytes=512_000,
                                                 backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    log.info("watch start pid=%s interval=%ss%s", os.getpid(), a.interval, " (boot: no session yet)" if a.boot else "")
    nodes = {k: v for k, v in load_nodes(route=False).items() if v["host"] != "local"}
    procs: dict = {}
    # ping every round; ssh + tmux only on a ping flip or every FULL_PROBE_S (DECISIONES 139)
    gate = P.GatedProbe(full_fn=PROBE)
    last_collect = 0.0
    while True:
        t = time.time()
        watch_round(nodes, procs, t, gate, P.notify, spawn_job, log, gate.mounts, gate.busy, P.probe)
        if t - last_collect >= 60:  # long jobs (largos.py): beats + results, one ssh per node
            last_collect = t
            try:
                for d in collect_now(log=log.info):
                    if d.get("ledger") or d.get("state") == "dead":
                        log.info("long job %s", json.dumps(d, ensure_ascii=False))
            except Exception as e:  # never let a long job stop the queue
                log.info("collect error: %s", type(e).__name__)
            try:  # puente turns sent with `send` (or a chat whose window closed)
                for d in puente_collect():
                    log.info("puente %s", json.dumps(d, ensure_ascii=False))
            except Exception as e:
                log.info("puente collect error: %s", type(e).__name__)
        try:  # the web inbox (docs/BANDEJA.md); off until config/visor-web.toml enables it
            res = bandeja_tick(t)
            if res.get("bandeja") == "ok" and (res["nuevas"] or res["estados"] or res["errores"]):
                log.info("bandeja %s", json.dumps(res, ensure_ascii=False))
        except Exception as e:  # never let the inbox stop the queue
            log.info("bandeja error: %s", type(e).__name__)
        if a.once:
            for pr in procs.values():
                pr.wait()
            reap(procs, log)
            return 0
        if a.boot and P.owner_logged_on():  # hand over: the logon task (1 min delay) has the desktop
            log.info("watch --boot: owner logged on, exiting so the logon task takes over")
            return 0
        time.sleep(max(1.0, a.interval - (time.time() - t)))


def cmd_nodo(a) -> int:
    import nodo as N
    if a.accion == "add":
        if not a.host or not a.os:
            print("nodo add needs --host and --os", file=sys.stderr)
            return 2
        return N.add(a.name, a.host, a.os, a.label, a.runnable)
    return (N.doctor if a.accion == "doctor" else N.rm)(a.name, load_nodes())


CTL_VERB = {"pausar": "pause", "reanudar": "resume", "desalojar": "evict", "prioridad": "priority",
            "adoptar": "adopt"}


def cmd_ctl(a) -> int:
    """enjambre pausar|reanudar|desalojar|prioridad|adoptar (prompt 20 step 5). With live running it
    goes through live's control endpoint (one writer of state/scheduler.json); without it, it acts
    directly over what the last live.json listed."""
    import urllib.request
    import planner as PL
    action = CTL_VERB[a.cmd]
    prio = getattr(a, "prio", None)
    if action == "adopt":
        key, name = f"pid:{a.node}:{a.pid}", None
    else:
        live = P.read_state(LV.OUT / "live.json") or {}
        known = {j["key"]: j for j in ((live.get("planificador") or {}).get("gestionados") or [])}
        key = a.id if ":" in a.id else next((k for k in (f"job:{a.id}", f"lab:{a.id}") if k in known), f"job:{a.id}")
        name = None
    ctl = PL.read_ctl()
    if ctl:
        body = json.dumps({"token": ctl["token"], "action": action, "key": key, "priority": prio, "name": name, "by": "cli"}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{int(ctl['port'])}/ctl", data=body,
                                     headers={"Content-Type": "text/plain"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            res = json.loads(e.read().decode("utf-8") or "{}")
        except OSError:
            res = None                    # live is not listening: act directly
        if res is not None:
            print(f"{a.cmd} {key}: {'ok' if res.get('ok') else 'NO'} — {res.get('msg') or res.get('error')}")
            return 0 if res.get("ok") else 1
    live = P.read_state(LV.OUT / "live.json") or {}
    jobs = (live.get("planificador") or {}).get("gestionados") or []
    res = PL.Planner(load_nodes(), log=lambda m: None).manual(action, key, prio, who="cli", jobs=jobs)
    print(f"{a.cmd} {key}: {'ok' if res.get('ok') else 'NO'} — {res.get('msg') or res.get('error')} (sin live: directo)")
    return 0 if res.get("ok") else 1


def cmd_live(a) -> int:
    """The visor's live truth (prompts 18-19): state/visor/live.json + live.js every cycle."""
    pidfile = STATE / "live.pid"
    if a.stop:
        try:
            pid = int(pidfile.read_text().strip())
        except (OSError, ValueError):
            print("live: no pid file, nothing to stop")
            return 0
        try:
            os.kill(pid, signal.SIGTERM)  # by the pid this loop wrote, never by name
            print(f"live: stopped pid {pid}")
        except OSError as e:
            print(f"live: pid {pid} not running ({e})")
        # prompt 20: nothing it paused may stay frozen once it is gone
        import planner as PL
        try:
            PL.CTL_JS.unlink()            # the visor stops offering controls to a dead endpoint
        except OSError:
            pass
        for d in PL.Planner(load_nodes(), log=lambda m: None).release_all():
            print(f"live: released {d['key']} on {d['node']}: {'ok' if d['ok'] else 'FAILED ' + d['msg']}")
        return 0
    nodes = load_nodes()
    if not a.serve:
        loop = LV.Loop(nodes)
        live = loop.cycle()
        if a.print:
            print(json.dumps(live, ensure_ascii=False, indent=1))
        else:
            ah = live["ahora"]
            print(f"live: {ah['running']} corriendo {ah['por_nodo']} · {ah['queued']} en cola · "
                  f"{ah['waiting']} en espera -> {LV.OUT / 'live.json'}")
        return 0
    if a.boot and P.owner_logged_on():
        print("live --boot: the owner is logged on; the logon task runs live")
        return 0
    lock = P.InstanceLock(STATE / "live.lock")
    task = Path(sys.executable).name.lower() == "pythonw.exe"   # the scheduled tasks run pythonw
    if not P.acquire_or_wait(lock, 180 if task and not a.boot else 0):   # logon task: let a --boot one hand over
        print("live: another instance is running; not starting a second one")
        return 0
    pidfile.write_text(str(os.getpid()))
    log = logging.getLogger("enjambre.live")
    if not log.handlers:
        h = logging.handlers.RotatingFileHandler(STATE / "live.log", maxBytes=256_000,
                                                 backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    log.info("live start pid=%s interval=%ss", os.getpid(), a.interval)
    for n, cfg in nodes.items():
        if cfg.get("host") != "local":
            err = LV.ship_collector(cfg["host"])
            log.info("node %s: collector %s, ring backfill %s lines", n, err or "shipped",
                     0 if err else LV.backfill_ring(n, cfg["host"]))
    import planner as PL
    for n, cfg in nodes.items():          # control/sigtree verbs: the node needs this jobrun.py
        if cfg.get("host") != "local":
            log.info("node %s: jobrun %s", n, L.ship(cfg["host"]) or "shipped")
    plan = PL.Planner(nodes, log=log.info)
    for d in plan.release_all():          # a previous loop that died may have left something paused
        log.info("released %s on %s: %s", d["key"], d["node"], d["ok"])
    PL.serve_controls(plan, log=log.info)   # 127.0.0.1 only; port + token in state/visor/ctl.js
    loop = LV.Loop(nodes, log=log.info, planner=plan)
    LV.INTERVAL_S = a.interval
    last_err = {}
    while True:
        t = time.time()
        P.apply_route(nodes)   # watch may have switched a node to its host_alt (prompt 21 §3)
        try:
            live = loop.cycle(t)
            for n, m in loop.meta.items():
                if m.get("error") != last_err.get(n):
                    log.info("node %s: %s (%s ms)", n, m.get("error") or "ok", m.get("lat_ms"))
                    last_err[n] = m.get("error")
        except Exception as e:  # one bad cycle must not stop the loop
            log.info("cycle error: %s: %s", type(e).__name__, e)
        if a.boot and P.owner_logged_on():
            log.info("live --boot: owner logged on, exiting so the logon task takes over")
            for d in plan.release_all():   # never leave a job SIGSTOPped behind
                log.info("released %s on %s: %s", d["key"], d["node"], d["ok"])
            return 0
        LV.wait_or_kick(max(1.0, a.interval - (time.time() - t)), t)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="enjambre")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--node", help="force the node (routed_by=explicit); omit it to route")
    r.add_argument("--repo", required=True, help="repo NAME under <portfolio>/ (never a path)")
    r.add_argument("--ref", help="branch to start the job's worktree from (origin/<ref>); default: upstream")
    r.add_argument("--task", required=True)
    r.add_argument("--worker", default="claude")
    r.add_argument("--engine", choices=("claude", "local"), default="claude",
                   help="local = the node's local code model via modelos-mac (text answer, no git)")
    r.add_argument("--no-queue", action="store_true",
                   help="run now even if the node looks down (used by watch)")
    r.add_argument("--kind", help="job kind from routing.toml (tests, render, gui, ...)")
    r.add_argument("--classify", action="store_true",
                   help="no --kind and no `kind:` frontmatter: ask haiku once (costs; off by default)")
    r.add_argument("--dry-run", action="store_true", help="route, print why, run nothing, no ledger")
    r.add_argument("--detach", action="store_true",
                   help="long agent: start it on the node and return (status --follow, collect, resume)")
    r.add_argument("--budget", type=float, default=2.0, help="--detach: USD cap for the whole job")
    r.add_argument("--max-turns", type=int, default=200,
                   help="--detach: turn cap (the budget usually stops first)")
    r.add_argument("--model", help="--detach: claude model alias (sonnet, opus, haiku)")
    r.add_argument("--priority", choices=S.PRIOS, help="alta|normal|baja|nocturna (default: the TASK's "
                   "`priority:`, else normal); config/scheduler.toml")
    r.add_argument("--beat", type=float, default=10,
                   help="--detach: seconds between heartbeats (status.json rewrite; the visor reads it live)")
    r.add_argument("--beat-log", type=float, default=300,
                   help="--detach: seconds between heartbeats kept in latidos.jsonl")
    r.add_argument("--routed-by", help=argparse.SUPPRESS)
    r.add_argument("--route-reason", help=argparse.SUPPRESS)
    r.set_defaults(fn=cmd_run)
    lv = sub.add_parser("live", help="the visor's live state: state/visor/live.json (+ live.js) every cycle")
    lv.add_argument("--serve", action="store_true", help="resident loop (the enjambre live scheduled task runs this)")
    lv.add_argument("--interval", type=float, default=LV.INTERVAL_S)
    lv.add_argument("--print", action="store_true", help="one cycle: print the whole live.json")
    lv.add_argument("--stop", action="store_true", help="stop the running loop (by its pid file)")
    lv.add_argument("--boot", action="store_true", help="AtStartup task (no desktop): exit once the owner logs on")
    lv.set_defaults(fn=cmd_live)
    for verb, hlp in (("pausar", "pause a job now (SIGSTOP / lab API); the scheduler will not resume it"),
                      ("reanudar", "resume a paused or evicted job"),
                      ("desalojar", "stop a job that has a checkpoint; `reanudar` continues it")):
        c = sub.add_parser(verb, help=hlp + " (prompt 20)")
        c.add_argument("id", help="job id, lab:<id> or pid:<node>:<pid>")
        c.set_defaults(fn=cmd_ctl, cmd=verb)
    c = sub.add_parser("prioridad", help="change a job's priority: alta|normal|baja|nocturna (prompt 20)")
    c.add_argument("id")
    c.add_argument("prio", choices=list(S.PRIOS))
    c.set_defaults(fn=cmd_ctl, cmd="prioridad")
    c = sub.add_parser("adoptar", help="manage a process enjambre did not launch, as baja by default (prompt 20)")
    c.add_argument("node")
    c.add_argument("pid", type=int)
    c.add_argument("--prio", choices=list(S.PRIOS), default="baja")
    c.set_defaults(fn=cmd_ctl, cmd="adoptar")
    nd = sub.add_parser("nodo", help="add / check / remove a node: nodes.toml + collector + launcher (prompt 19)")
    nd.add_argument("accion", choices=["add", "doctor", "rm"])
    nd.add_argument("name")
    nd.add_argument("--host", help="add: ssh alias, or `local` (Windows, this PC)")
    nd.add_argument("--os", choices=["macos", "windows", "linux"], help="add")
    nd.add_argument("--label", help="add: what people read (default: the name)")
    nd.add_argument("--runnable", action="store_true", help="add: it can run jobs (default: telemetry only)")
    nd.set_defaults(fn=cmd_nodo)
    w = sub.add_parser("watch")
    w.add_argument("--interval", type=float, default=30)
    w.add_argument("--once", action="store_true", help="one probe+dispatch round, then exit")
    w.add_argument("--stop", action="store_true", help="stop the running watch (by its pid file)")
    w.add_argument("--boot", action="store_true", help="AtStartup task (no desktop): exit once the owner logs on")
    w.set_defaults(fn=cmd_watch)
    stp = sub.add_parser("status")
    stp.add_argument("--follow", metavar="ID", help="follow a long job's log and heartbeat until it ends")
    stp.add_argument("--interval", type=float, default=5)
    stp.add_argument("--no-live", dest="live", action="store_false",
                     help="skip the live ssh read (gui + puente sessions) per node")
    stp.set_defaults(fn=cmd_status)
    for verb, fn, hlp in (("chat", cmd_chat, "talk to the persistent Claude Code on a node; prints its stream"),
                          ("send", cmd_send, "queue a message for the node's persistent session; result via the ledger")):
        pp = sub.add_parser(verb, help=hlp)
        pp.add_argument("node")
        pp.add_argument("--repo", required=True)
        if verb == "chat":
            pp.add_argument("message", nargs="?", default=None)
            pp.add_argument("--attach", metavar="TURN", help="follow a turn already sent (after closing the window)")
        else:
            pp.add_argument("message")
        pp.add_argument("--model")
        pp.add_argument("--budget", type=float, default=2.0, help="US$ cap for this turn (SDK max_budget_usd)")
        pp.add_argument("--max-turns", type=int, default=30)
        pp.add_argument("--edit", action="store_true",
                        help="also allow Edit/Write in the node's main clone (default: read, tests, browser)")
        pp.set_defaults(fn=fn)
    ses = sub.add_parser("sessions", help="the node's persistent sessions with their cost")
    ses.add_argument("node", nargs="?", default="mac")
    ses.set_defaults(fn=cmd_sessions)
    sc = sub.add_parser("script", help="run a command declared in the repo's AGENTS.md on a node, no agent")
    sc.add_argument("--repo", required=True)
    sc.add_argument("--cmd", required=True, help="a `script-cmd <name>` from the repo's AGENTS.md")
    sc.add_argument("--node", default="mac")
    sc.add_argument("--priority", choices=S.PRIOS, default="normal", help="alta|normal|baja|nocturna")
    sc.add_argument("--resumable", action="store_true",
                    help="the script writes its own checkpoint: it may be evicted and resumed")
    sc.add_argument("--beat", type=float, default=10)
    sc.add_argument("--beat-log", type=float, default=300)
    sc.add_argument("--follow", action="store_true", help="follow it right away (Ctrl-C leaves it running)")
    sc.add_argument("--interval", type=float, default=5)
    sc.add_argument("--dry-run", action="store_true")
    sc.add_argument("args", nargs=argparse.REMAINDER, help="-- extra arguments, appended quoted")
    sc.set_defaults(fn=cmd_script)
    cl = sub.add_parser("clone", help="clone a repo into <portfolio>/<repo> on a node (idempotent, no agent)")
    cl.add_argument("repo", help="repo name under <portfolio>/")
    cl.add_argument("--node", default="mac")
    cl.add_argument("--url", help="default: the local clone's origin")
    cl.set_defaults(fn=cmd_clone)
    co = sub.add_parser("collect", help="bring finished long jobs home (watch does it every minute)")
    co.add_argument("id", nargs="?")
    co.set_defaults(fn=cmd_collect)
    ce = sub.add_parser("cerrar", help="close a dead long job by hand: one terminal ledger line, collected")
    ce.add_argument("id")
    ce.add_argument("--razon", required=True)
    ce.add_argument("--estado", choices=("closed", "dead-at-launch"), default="closed")
    ce.set_defaults(fn=cmd_cerrar)
    lg = sub.add_parser("logs",help="fetch a job's node-side logs into state/jobs/<id>/node.log")
    lg.add_argument("id")
    lg.add_argument("--tail", type=int, default=40, help="lines per file")
    lg.add_argument("--node", default="mac", help="for a job with no state/jobs/<id>/job.json")
    lg.set_defaults(fn=cmd_logs)
    rs = sub.add_parser("resume", help="restart a dead long job where it stopped")
    rs.add_argument("id")
    rs.set_defaults(fn=cmd_resume)
    kl = sub.add_parser("kill", help="stop a long job (its whole process group)")
    kl.add_argument("id")
    kl.set_defaults(fn=cmd_kill)
    sub.add_parser("history", help="measured seconds per (kind, node)").set_defaults(fn=cmd_history)
    rd = sub.add_parser("render", help="render an STL to PNG with Blender on a node (no model, no git)")
    rd.add_argument("stl", help="path relative to Portfolio/, same on every node")
    rd.add_argument("--node", default="mac")
    rd.add_argument("--device", choices=("auto", "METAL", "CPU"), default="auto",
                    help="auto = METAL unless the node's busy marker says a long job holds it")
    rd.add_argument("--samples", type=int, default=64)
    rd.add_argument("--res", default="1280x720")
    rd.add_argument("--unit", choices=("mm", "m"), default="mm", help="unit the STL was exported in")
    rd.add_argument("--out", help="local folder for the PNG (default state/renders/)")
    rd.set_defaults(fn=cmd_render)
    pl = sub.add_parser("pull")
    pl.add_argument("id")
    pl.add_argument("--repo", required=True)
    pl.set_defaults(fn=cmd_pull)
    mo = sub.add_parser("modelo", help="mark a local model: en uso / sirve / por probar / no sirve / descartado")
    mo.add_argument("key", help="model key in the manager catalog (llm, stt, gemma_coder, ...)")
    mo.add_argument("--status", required=True, choices=MODEL_STATES)
    mo.add_argument("--nota", default="", help="one line, up to 200 characters")
    mo.add_argument("--node", default="mac")
    mo.set_defaults(fn=cmd_modelo)
    vi = sub.add_parser("visor", help="open the local visor as an app window; --install adds the shortcuts")
    vi.add_argument("--install", action="store_true", help="icon + 'Visor enjambre' shortcut on the Desktop and Start menu")
    vi.add_argument("--follow", action="store_true", help="open it and rebuild every 30 s until Ctrl+C")
    vi.add_argument("--demo", nargs="?", const="", metavar="OUT_DIR",
                    help="build the visor from a synthetic swarm (default state/visor/demo/): screenshots, public demo")
    vi.add_argument("--serve", action="store_true", help="with --demo: fresh synthetic readings every 10 s")
    vi.add_argument("--export", action="store_true",
                    help="with --demo: the static site for the public demo (default state/web-dist/demo/)")
    vi.set_defaults(fn=cmd_visor)
    a = p.parse_args(argv)
    # A result with '≠' or an accent crashed the print on a cp1252 console after the ledger
    # line was written (E3, farmacias). Never let the console encoding fail a finished job.
    for stream in (sys.stdout, sys.stderr):
        try:
            # Piped (an agent's Bash tool, a log): UTF-8, or 'sesión' arrives as 'sesi?n' (P,
            # fire test). A real console keeps its own encoding, with replace.
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
