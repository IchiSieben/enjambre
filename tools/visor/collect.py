#!/usr/bin/env python3
"""collect.py — one node's snapshot for the enjambre visor (runs ON each node).

Reads only local, already-written records and summarises them to one JSON file:
  - Claude Code transcripts  ~/.claude/projects/**/*.jsonl  -> tokens per day/model/project
  - live sessions            ~/.claude/sessions/<pid>.json  -> which windows are open now
  - statusline snapshots     ~/.claude/state/<sid>.json     -> 5h / 7d quota %, the only place it exists
  - disks, memory, SMB mounts, tmux sessions, and the local model manager (127.0.0.1:8765) if present

Never opens: ~/.claude/sessions/*.key, ~/.claude/history.jsonl, .credentials.json, message content.

Python 3.9 compatible on purpose: the mac runs it with the system /usr/bin/python3 (3.9.6),
so no tomllib, no `X | None`, no match. stdlib only.

Usage: collect.py --node mac [--out ~/.enjambre/visor/snapshot.json] [--print]
       collect.py --node mac --telemetria | --live      (fast, prompt 19: stdout only)
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request

SCHEMA = 1
WINDOW_DAYS = 8                      # keep 8 local days so a rolling 7-day window is always complete
LIMA = dt.timezone(dt.timedelta(hours=-5), "America/Lima")   # no DST in Peru since 1994
QUOTA_FRESH_S = 30 * 60              # a statusline snapshot older than this says nothing about "now"
MODELS_URL = "http://127.0.0.1:8765"
HOME = os.path.expanduser("~")


def claude_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(HOME, ".claude")


def local_day(ts: float, tz: dt.tzinfo = LIMA) -> str:
    return dt.datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d")


def parse_ts(s) -> float | None:
    if isinstance(s, (int, float)):
        return s / 1000.0 if s > 1e11 else float(s)
    if isinstance(s, str):
        try:
            return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def project_of(cwd: str | None) -> str:
    """Last path component of the cwd. Worktrees (`.../wt/<name>`) keep the repo name."""
    if not cwd:
        return "?"
    parts = [p for p in re.split(r"[\\/]+", cwd) if p]
    if len(parts) >= 2 and parts[-2] == "wt":
        return parts[-1]
    return parts[-1] if parts else "?"


# Owner decision 2026-10-11 (tutorial 31 §5): a session opened in the home folder is not a project,
# and its "name" is the login name. Renamed only where it is SHOWN; history.db keeps what it has.
HOME_CWD = re.compile(r"(?:/Users|/home)/[^/\\]+/?|[A-Za-z]:[\\/]+Users[\\/]+[^\\/]+[\\/]?", re.I)


def shown_project(name, cwd, node: str, home_name: str | None = None):
    """'inicio-<node>' for the home folder (by its exact cwd, or by the node's home_name)."""
    if (isinstance(cwd, str) and HOME_CWD.fullmatch(cwd)) or (home_name and name == home_name):
        return "inicio-" + str(node)
    return name


# ------------------------------------------------------------------ prices
# Cache-write multipliers over base input, from the pricing page's "5m Cache Writes" / "1h Cache
# Writes" columns (claude-api skill, shared/cost-optimization.md: "writes at 2x instead of 1.25x").
# Used ONLY when models.json lacks price_cache_write_5m/_1h; the snapshot then says "estimated".
CW5_MULT, CW1H_MULT = 1.25, 2.0


def load_prices(path: str | None = None) -> dict:
    """{model_id: (in, out, cache_read, cache_write_5m, cache_write_1h)} in USD per MTok, from
    ~/.claude/rules/models.json (orquestador/tools/model-intel.py writes it).

    DECISIONES 112 left cache writes unpriced; session F2 prices them (the owner asked for the
    floor to include them): model-intel now parses both write columns; an older models.json
    without them falls back to the documented multipliers above.
    """
    path = path or os.path.join(claude_dir(), "rules", "models.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for m in d.get("models", []):
        pin = m.get("price_in")
        if pin is None or m.get("price_out") is None:
            continue
        cw5 = m.get("price_cache_write_5m")
        cw1h = m.get("price_cache_write_1h")
        out[m["id"]] = (pin, m["price_out"], m.get("price_cache_hit"),
                        cw5 if cw5 is not None else round(pin * CW5_MULT, 4),
                        cw1h if cw1h is not None else round(pin * CW1H_MULT, 4))
    return out


def prices_estimated(path: str | None = None) -> bool:
    """True when any cache-write price came from the multiplier, not from models.json."""
    path = path or os.path.join(claude_dir(), "rules", "models.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return True
    return any(m.get("price_in") is not None and m.get("price_cache_write_5m") is None
               for m in d.get("models", []))


def price_for(model: str, prices: dict):
    if model in prices:
        return prices[model]
    base = re.sub(r"-\d{8}$", "", model)          # claude-haiku-4-5-20251001 -> claude-haiku-4-5
    for k, v in prices.items():
        if re.sub(r"-\d{8}$", "", k) == base:
            return v
    return None


# ------------------------------------------------------------------ transcripts (incremental)
EMPTY = [0, 0, 0, 0, 0, 0]           # input, output, cache_write, cache_read, messages, cache_write_1h
CURSOR_VERSION = 3                    # bump when the cursor layout changes: old cursors are re-read


def scan_file(path: str, cur: dict, tz: dt.tzinfo = LIMA) -> dict:
    """Parse only the bytes appended since the last run. `cur` is this file's cursor:
    {v, size, offset, msgs: {message_id: [day, project, model, in, out, cache_w, cache_r]}}.

    Every content block of one API response is its own line with the SAME message.id and the
    SAME usage, so a message id is stored once. Rows are kept per id (not summed per file)
    because a resumed session copies earlier responses into a NEW transcript: on the mac 11 of
    50 ids were in two files (18.7 % of the tokens). collect_usage() unions ids across files.
    Only complete lines are consumed: a line being written right now is read on the next run.
    """
    size = os.path.getsize(path)
    if cur.get("v") != CURSOR_VERSION or size < cur.get("offset", 0):   # new layout / truncated
        cur = {}
    off = cur.get("offset", 0)
    msgs = cur.get("msgs", {})
    res = {"v": CURSOR_VERSION, "size": size, "offset": off, "msgs": msgs}
    if size == off:
        return res
    with open(path, "rb") as f:
        f.seek(off)
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return res
    pos = off
    for raw in chunk[: end + 1].splitlines(True):
        here, pos = pos, pos + len(raw)
        if b'"usage"' not in raw:
            continue
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("type") != "assistant":
            continue
        msg = r.get("message") or {}
        u = msg.get("usage") or {}
        model = msg.get("model") or "?"
        if not u or model == "<synthetic>":
            continue
        mid = msg.get("id") or r.get("requestId") or "%s@%d" % (os.path.basename(path), here)
        if mid in msgs:
            continue
        row = [int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                             "cache_creation_input_tokens", "cache_read_input_tokens")]
        ts = parse_ts(r.get("timestamp"))
        if not any(row) or ts is None:
            continue
        msgs[mid] = [local_day(ts, tz), project_of(r.get("cwd")), model] + row + [write_1h(u)]
    res["offset"] = off + end + 1
    return res


def write_1h(u: dict) -> int:
    """Cache-write tokens with the 1-hour TTL. `usage.cache_creation` splits the writes by TTL
    (ephemeral_5m/1h_input_tokens); without it every write is counted at the 5 min rate."""
    cc = u.get("cache_creation")
    return int(cc.get("ephemeral_1h_input_tokens") or 0) if isinstance(cc, dict) else 0


def transcript_files(root: str) -> list:
    """Main transcripts AND subagent transcripts (`<sid>/subagents/agent-*.jsonl`)."""
    pats = [os.path.join(root, "projects", "*", "*.jsonl"),
            os.path.join(root, "projects", "*", "*", "subagents", "*.jsonl")]
    out = []
    for p in pats:
        out.extend(glob.glob(p))
    return out


def collect_usage(root: str, cursor_path: str, now: float, prices: dict,
                  tz: dt.tzinfo = LIMA) -> dict:
    try:
        with open(cursor_path, encoding="utf-8") as f:
            cursors = json.load(f)
    except (OSError, ValueError):
        cursors = {}
    cutoff = now - WINDOW_DAYS * 86400
    keep_from = local_day(cutoff, tz)
    fresh = {}
    for path in sorted(transcript_files(root)):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
            c = scan_file(path, cursors.get(path, {}), tz)
        except OSError:
            continue
        c["msgs"] = {k: v for k, v in c["msgs"].items() if v[0] >= keep_from}
        fresh[path] = c
    os.makedirs(os.path.dirname(cursor_path), exist_ok=True)
    tmp = cursor_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fresh, f)
    os.replace(tmp, cursor_path)

    union = {}
    for c in fresh.values():
        for mid, row in c["msgs"].items():
            union.setdefault(mid, row)            # the same response in two transcripts counts once
    days = {}
    for day, proj, model, i, o, cw, cr, w1h in union.values():
        d = days.setdefault(day, {}).setdefault(proj, {}).setdefault(model, list(EMPTY))
        d[0] += i; d[1] += o; d[2] += cw; d[3] += cr; d[4] += 1; d[5] += w1h
    return {"tz": "America/Lima (UTC-5)", "days": days,
            "unpriced_models": sorted({m for p in days.values() for ms in p.values()
                                       for m in ms if price_for(m, prices) is None})}


# ------------------------------------------------------------------ all-time history (per project)
HISTORY_VERSION = 1
IDLE_GAP_S = 30 * 60          # a gap longer than this between two lines is not "working time"
HISTORY_BUDGET_S = 40         # the Windows task is killed at 2 min: scan this long, save, go on next run
TIMELINE_MAX = 60             # sessions per project kept in the snapshot's timeline


def _is_prompt(r: dict) -> bool:
    """A turn the person typed: a user line that is not meta and not only tool results."""
    if r.get("type") != "user" or r.get("isMeta"):
        return False
    c = (r.get("message") or {}).get("content")
    if isinstance(c, str):
        return not c.startswith(("<local-command-stdout", "<local-command-caveat", "<local-command-stderr"))
    return isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "text" for b in c)


def history_file_info(path: str, root: str) -> tuple:
    """(session_id, is_subagent). Main: projects/<p>/<sid>.jsonl; sub: projects/<p>/<sid>/subagents/x.jsonl."""
    parts = os.path.normpath(os.path.relpath(path, os.path.join(root, "projects"))).split(os.sep)
    if len(parts) >= 4 and parts[-2] == "subagents":
        return parts[-3], True
    return os.path.splitext(parts[-1])[0], False


def scan_history(path: str, cur: dict, sub: bool) -> dict:
    """Incremental, all-time summary of one transcript (no window, never pruned).
    cur = {v, offset, project, first, last, active, prompts, msgs: {id: [model, in, out, cw, cr, cw1h]}}"""
    size = os.path.getsize(path)
    if cur.get("v") != HISTORY_VERSION or size < cur.get("offset", 0):
        cur = {}
    res = {"v": HISTORY_VERSION, "offset": cur.get("offset", 0), "project": cur.get("project"),
           "first": cur.get("first"), "last": cur.get("last"), "active": cur.get("active", 0.0),
           "prompts": cur.get("prompts", 0), "msgs": cur.get("msgs", {})}
    if size == res["offset"]:
        return res
    with open(path, "rb") as f:
        f.seek(res["offset"])
        chunk = f.read()
    end = chunk.rfind(b"\n")
    if end < 0:
        return res
    for raw in chunk[: end + 1].splitlines():
        if b'"timestamp"' not in raw:
            continue
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(r, dict):
            continue
        ts = parse_ts(r.get("timestamp"))
        if ts is None:
            continue
        if res["project"] is None and r.get("cwd"):
            res["project"] = project_of(r.get("cwd"))
        if res["last"] is not None and 0 <= ts - res["last"] <= IDLE_GAP_S:
            res["active"] += ts - res["last"]
        res["first"] = ts if res["first"] is None else min(res["first"], ts)
        res["last"] = ts if res["last"] is None else max(res["last"], ts)
        if not sub and _is_prompt(r):
            res["prompts"] += 1
        if r.get("type") != "assistant":
            continue
        msg = r.get("message") or {}
        u = msg.get("usage") or {}
        model = msg.get("model") or "?"
        mid = msg.get("id") or r.get("requestId")
        if not u or model == "<synthetic>" or not mid or mid in res["msgs"]:
            continue
        row = [int(u.get(k) or 0) for k in ("input_tokens", "output_tokens",
                                             "cache_creation_input_tokens", "cache_read_input_tokens")]
        if any(row):
            res["msgs"][mid] = [model] + row + [write_1h(u)]
    res["offset"] += end + 1
    return res


# The visor's own store (like the model manager's jobs.db): every transcript ever read, every
# response id, one row per session. Never pruned, so Claude Code's cleanup of old .jsonl files
# (cleanupPeriodDays) does not erase the history. A legacy history.json next to it is imported once.
HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, root TEXT, sid TEXT, sub INTEGER,
  project TEXT, first REAL, last REAL, active REAL, prompts INTEGER, off INTEGER, v INTEGER);
CREATE TABLE IF NOT EXISTS msgs (path TEXT, mid TEXT, model TEXT, i INTEGER, o INTEGER,
  cw INTEGER, cr INTEGER, w1h INTEGER, PRIMARY KEY (path, mid));
CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, project TEXT, start REAL, "end" REAL,
  active_s REAL, prompts INTEGER, responses INTEGER, subagents INTEGER, tok_in INTEGER,
  tok_out INTEGER, tok_cw INTEGER, tok_cr INTEGER, tok_cw1h INTEGER, cost REAL, win TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS agents (path TEXT PRIMARY KEY, type TEXT, descr TEXT, parent TEXT, depth INTEGER);
"""
SUBS_MAX = 120                # subagents per project kept in the snapshot (most recent)


def history_db(path: str):
    import sqlite3
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.executescript(HISTORY_SCHEMA)
    return con


def db_load(con) -> dict:
    cursors = {}
    for path, root, sid, sub, proj, first, last, active, prompts, off, v in con.execute(
            "SELECT path, root, sid, sub, project, first, last, active, prompts, off, v FROM files"):
        cursors[path] = {"v": v, "offset": off, "project": proj, "first": first, "last": last,
                         "active": active or 0.0, "prompts": prompts or 0, "msgs": {},
                         "sid": sid, "sub": bool(sub), "root": root}
    for path, mid, model, i, o, cw, cr, w1h in con.execute(
            "SELECT path, mid, model, i, o, cw, cr, w1h FROM msgs"):
        if path in cursors:
            cursors[path]["msgs"][mid] = [model, i, o, cw, cr, w1h]
    for path, typ, descr in con.execute("SELECT path, type, descr FROM agents"):
        if path in cursors:
            cursors[path]["agent"] = [typ, descr]
    return cursors


def agent_meta(path: str) -> list | None:
    """[agentType, description] from the `<agent>.meta.json` Claude Code writes next to a subagent
    transcript. Read once and kept in the db: the description survives the transcript's cleanup."""
    try:
        with open(os.path.splitext(path)[0] + ".meta.json", encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(m, dict):
        return None
    return [str(m.get("agentType") or "")[:64], str(m.get("description") or "")[:200],
            str(m.get("parentAgentId") or "")[:64], m.get("spawnDepth") if isinstance(m.get("spawnDepth"), int) else None]


def db_save(con, cursors: dict, changed: set, reset: set) -> None:
    with con:
        for path in changed:
            c = cursors[path]
            if path in reset:
                con.execute("DELETE FROM msgs WHERE path = ?", (path,))
            con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (path, c.get("root"), c.get("sid"), int(bool(c.get("sub"))), c.get("project"),
                         c.get("first"), c.get("last"), c.get("active", 0.0), c.get("prompts", 0),
                         c.get("offset", 0), c.get("v")))
            con.executemany("INSERT OR IGNORE INTO msgs VALUES (?,?,?,?,?,?,?,?)",
                            [(path, mid) + tuple(r) for mid, r in (c.get("msgs") or {}).items()])


def db_import_json(con, json_path: str) -> int:
    """One-time: the cursor of the JSON era, so nothing already read is read again."""
    if not os.path.exists(json_path) or con.execute("SELECT 1 FROM meta WHERE k='json_imported'").fetchone():
        return 0
    try:
        with open(json_path, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {}
    db_save(con, old, set(old), set())
    with con:
        con.execute("INSERT OR REPLACE INTO meta VALUES ('json_imported', ?)", (str(len(old)),))
    return len(old)


def backup_roots(home: str = HOME) -> list:
    """Old copies of ~/.claude (~/.claude.bak-*): read once like any root, then skipped by size."""
    return sorted(p for p in glob.glob(os.path.join(home, ".claude.bak-*")) if os.path.isdir(os.path.join(p, "projects")))


def collect_history(root: str, cursor_path: str, prices: dict, budget_s: float = HISTORY_BUDGET_S,
                    clock=time.monotonic, extra_roots=()) -> dict:
    """Per-project totals over EVERY transcript on this node (not the 8-day window).

    Time-boxed and resumable: files are scanned oldest first until `budget_s` runs out; the cursor
    is saved either way and the next run continues, so a first pass over a GB of transcripts
    finishes across a few runs instead of being killed and restarting from zero.

    Definitions (shown on the page):
      sessions   = main transcripts (one per start or resume of Claude Code)
      windows    = sessions merged when they share a response id (a resume copies earlier
                   responses into its new transcript: same conversation, same window)
      subagents  = subagent transcripts (<sid>/subagents/*.jsonl), one per launched subagent
      hours      = sum of gaps <= 30 min between consecutive lines of the main transcripts
      prompts    = turns the person typed; responses = unique API responses (main + subagents)
    """
    db_path = os.path.splitext(cursor_path)[0] + ".db"     # history.json -> history.db (+ import)
    con = history_db(db_path)
    try:
        db_import_json(con, cursor_path)
        cursors = db_load(con)
        t0 = clock()
        files = []
        for r in [root] + [x for x in extra_roots if x != root]:
            for p in transcript_files(r):
                try:
                    files.append((os.path.getmtime(p), p, r))
                except OSError:
                    continue
        files.sort()
        pending = 0
        changed, reset = set(), set()
        for _mt, path, r in files:
            sid, sub = history_file_info(path, r)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            c = cursors.get(path, {})
            if c.get("v") == HISTORY_VERSION and c.get("offset") == size:
                continue
            if clock() - t0 > budget_s:
                pending += 1
                continue
            try:
                new = scan_history(path, c, sub)
            except OSError:
                continue
            if new.get("offset", 0) < c.get("offset", 0) or c.get("v") not in (None, HISTORY_VERSION):
                reset.add(path)
            new["sid"], new["sub"], new["root"] = sid, sub, os.path.basename(r.rstrip("/\\"))
            cursors[path] = new
            changed.add(path)
        db_save(con, cursors, changed, reset)                # deleted .jsonl keep their rows
        known = {r[0] for r in con.execute("SELECT path FROM agents")}
        new_agents = []
        for path, c in cursors.items():
            if c.get("sub") and path not in known and os.path.exists(path):
                m = agent_meta(path)
                if m:
                    new_agents.append((path, *m))
                    c["agent"] = m[:2]
        with con:
            con.executemany("INSERT OR REPLACE INTO agents VALUES (?,?,?,?,?)", new_agents)
        out = summarize_history(cursors, prices, pending, len(files))
        with con:
            con.executemany("INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [(s["sid"], s["project"], s["start"], s["end"], round(s["active"]), s["prompts"],
                              s["tok"][4], s["subagents"], *s["tok"][:4], s["tok"][5], round(s["cost"], 6),
                              s["win"], time.time()) for s in out.pop("_sessions")])
        out["db"] = {"sessions": con.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                     "files": len(cursors),
                     "roots": sorted({c.get("root") for c in cursors.values() if c.get("root")})}
        return out
    finally:
        con.close()


def summarize_history(cursors: dict, prices: dict, pending: int = 0, n_files: int = 0) -> dict:
    # 1. sessions: main transcripts, plus their subagents' responses and counts
    sess = {}
    owner = {}                                       # response id -> first session that holds it
    order = sorted(cursors.items(), key=lambda kv: (kv[1].get("first") or 0, kv[0]))
    # The same transcript can be read from two roots (~/.claude and a ~/.claude.bak-* copy):
    # per session the longest main file speaks for hours and prompts, a subagent file counts once.
    best = {}
    for path, c in order:
        if not c.get("sub") and c.get("offset", 0) >= best.get(c["sid"], ({}, None))[0].get("offset", -1):
            best[c["sid"]] = (c, path)
    for sid, (c, _p) in best.items():
        sess[sid] = {"sid": sid, "project": c.get("project") or "?",
                     "start": c.get("first"), "end": c.get("last"), "active": c.get("active", 0.0),
                     "prompts": c.get("prompts", 0), "subagents": 0, "tok": [0, 0, 0, 0, 0, 0],
                     "cost": 0.0, "unpriced": 0, "links": set()}
    seen_sub, subs = set(), []
    for path, c in order:
        sid = c.get("sid")
        if c.get("sub"):
            if sid not in sess:                       # subagent whose main transcript is gone
                sess[sid] = {"sid": sid, "project": c.get("project") or "?", "start": c.get("first"),
                             "end": c.get("last"), "active": 0.0, "prompts": 0, "subagents": 0,
                             "tok": [0, 0, 0, 0, 0, 0], "cost": 0.0, "unpriced": 0, "links": set()}
            key = (sid, os.path.basename(path.replace("\\", "/")))
            if key not in seen_sub:
                seen_sub.add(key)
                sess[sid]["subagents"] += 1
                ag = c.get("agent") or [None, None]
                rows = list((c.get("msgs") or {}).values())
                costs = [cost_of(r[0], list(r[1:5]) + [1, r[5]], prices) for r in rows]
                subs.append({"sid": sid, "start": c.get("first"), "end": c.get("last"),
                             "type": ag[0] or None, "desc": ag[1] or None, "responses": len(rows),
                             "tokens": sum(sum(r[1:5]) for r in rows),
                             "cost": round(sum(x for x in costs if x is not None), 4)})
        s = sess[sid]
        for mid, row in (c.get("msgs") or {}).items():
            if mid in owner:
                if owner[mid] != sid:
                    s["links"].add(owner[mid])        # a resume: same window as the first holder
                continue
            owner[mid] = sid
            model, i, o, cw, cr, w1h = row
            for k, v in enumerate((i, o, cw, cr, 1, w1h)):
                s["tok"][k] += v
            cost = cost_of(model, [i, o, cw, cr, 1, w1h], prices)
            if cost is None:
                s["unpriced"] += 1
            else:
                s["cost"] += cost
    # 2. windows: union-find over the resume links
    parent = {k: k for k in sess}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for k, s in sess.items():
        for l in s["links"]:
            if l in parent:
                parent[find(k)] = find(l)
    # 3. per project
    projects = {}
    for k, s in sess.items():
        p = projects.setdefault(s["project"], {"sessions": 0, "windows": set(), "active_s": 0.0,
                                                "prompts": 0, "responses": 0, "subagents": 0,
                                                "tokens": {"in": 0, "out": 0, "cw": 0, "cr": 0, "cw1h": 0},
                                                "cost": 0.0, "unpriced_msgs": 0, "first": None,
                                                "last": None, "timeline": []})
        p["sessions"] += 1
        p["windows"].add(find(k))
        p["active_s"] += s["active"]
        p["prompts"] += s["prompts"]
        p["responses"] += s["tok"][4]
        p["subagents"] += s["subagents"]
        for n, key in enumerate(("in", "out", "cw", "cr")):
            p["tokens"][key] += s["tok"][n]
        p["tokens"]["cw1h"] += s["tok"][5]
        p["cost"] += s["cost"]
        p["unpriced_msgs"] += s["unpriced"]
        if s["start"] is not None:
            p["first"] = s["start"] if p["first"] is None else min(p["first"], s["start"])
        if s["end"] is not None:
            p["last"] = s["end"] if p["last"] is None else max(p["last"], s["end"])
        p["timeline"].append({"start": s["start"], "end": s["end"], "active_s": round(s["active"]),
                              "prompts": s["prompts"], "responses": s["tok"][4],
                              "subagents": s["subagents"], "tokens": sum(s["tok"][:4]),
                              "cost": round(s["cost"], 4), "window": find(k)[:8], "sid": k})
        s["win"] = find(k)
    for p in projects.values():
        p["windows"] = len(p["windows"])
        p["active_s"] = round(p["active_s"])
        p["cost"] = round(p["cost"], 4)
        p["timeline"] = sorted(p["timeline"], key=lambda t: t["start"] or 0)[-TIMELINE_MAX:]
        p["subs"] = []
    for x in sorted(subs, key=lambda x: x["start"] or 0):
        pr = projects.get(sess[x["sid"]]["project"])
        if pr is not None:
            pr["subs"].append(x)
    for p in projects.values():
        p["subs"] = p["subs"][-SUBS_MAX:]
    starts = [s["start"] for s in sess.values() if s["start"]]
    ends = [s["end"] for s in sess.values() if s["end"]]
    by_day = {}                                      # all-time chart: a session's cost on its start day
    for s in sess.values():
        if s["start"]:
            d = by_day.setdefault(local_day(s["start"]), {"cost": 0.0, "sessions": 0, "active_s": 0})
            d["cost"] = round(d["cost"] + s["cost"], 4)
            d["sessions"] += 1
            d["active_s"] += round(s["active"])
    # weekday x hour heat map (F3 §11): each session's cost spread evenly over the clock hours it
    # was open. Approximate by design (idle time inside a session weighs the same); the page says so.
    # Lima is a whole-hour offset with no DST, so its hour boundaries are the UTC ones.
    heat = [[0.0] * 24 for _ in range(7)]
    for s in sess.values():
        a = s["start"]
        if not a or not s["cost"]:
            continue
        b = max(s["end"] or a, a + 60)
        t = a
        while t < b:
            nxt = min(b, (t // 3600 + 1) * 3600)
            lt = dt.datetime.fromtimestamp(t, LIMA)
            heat[lt.weekday()][lt.hour] += s["cost"] * (nxt - t) / (b - a)
            t = nxt
    heat = [[round(v, 3) for v in row] for row in heat]
    return {"projects": projects, "files": n_files, "pending_files": pending, "by_day": by_day, "heat": heat,
            "complete": pending == 0, "since": min(starts) if starts else None,
            "until": max(ends) if ends else None, "_sessions": list(sess.values())}


def cost_of(model: str, row: list, prices: dict):
    """USD at API list price. row = [in, out, cache_write, cache_read, msgs(, cache_write_1h)]:
    the 1-hour part of the writes (6th element, when the transcript said) is priced at the 1 h
    rate, the rest at the 5 min rate (the cheaper one: a floor). None = model has no price."""
    p = price_for(model, prices)
    if p is None:
        return None
    pin, pout, pcr = p[0], p[1], p[2]
    cw5 = p[3] if len(p) > 3 else pin * CW5_MULT
    cw1h = p[4] if len(p) > 4 else pin * CW1H_MULT
    w1h = row[5] if len(row) > 5 else 0
    return (row[0] * pin + row[1] * pout + row[3] * (pcr or 0)
            + (row[2] - w1h) * cw5 + w1h * cw1h) / 1e6


# ------------------------------------------------------------------ live sessions
def pid_alive(pid: int) -> bool:
    """Read-only liveness. On Windows os.kill(pid, 0) is NOT a probe: signal 0 is CTRL_C_EVENT
    and would interrupt the owner's session. Use OpenProcess + GetExitCodeProcess instead."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259          # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def pid_started(pid: int) -> float | None:
    """Creation time of a process (epoch s), to tell a live session from a recycled PID."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        h = k32.OpenProcess(0x1000, False, pid)
        if not h:
            return None
        try:
            ft = [wintypes.FILETIME() for _ in range(4)]
            if not k32.GetProcessTimes(h, *[ctypes.byref(x) for x in ft]):
                return None
            t = (ft[0].dwHighDateTime << 32) | ft[0].dwLowDateTime
            return t / 1e7 - 11644473600                     # FILETIME (1601, 100 ns) -> epoch
        finally:
            k32.CloseHandle(h)
    out = run(["ps", "-o", "lstart=", "-p", str(pid)]).strip()
    try:
        return time.mktime(time.strptime(" ".join(out.split()), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


PID_REUSE_TOL_S = 600   # startedAt is written after the process boots; a recycled PID is off by hours


def session_alive(pid: int, started_at: float) -> bool:
    if not pid_alive(pid):
        return False
    st = pid_started(pid)
    return st is None or not started_at or abs(st - started_at) <= PID_REUSE_TOL_S


def handoff_mtime(cwd: str | None) -> float | None:
    """mtime of the HANDOFF.md the statusline judges for this cwd: the cwd's own, else the git
    toplevel's (walk up to the first .git, no git fork). The bar shows its clock time; the visor
    polls, so it shows the live age from this (prompt 21 §5)."""
    if not cwd:
        return None
    d = os.path.abspath(cwd)
    while True:
        try:
            return os.stat(os.path.join(d, "HANDOFF.md")).st_mtime
        except OSError:
            pass
        if os.path.exists(os.path.join(d, ".git")):
            return None
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


def collect_sessions(root: str, now: float, alive=None) -> tuple:
    sessions, quota = [], None
    states = {}
    for p in glob.glob(os.path.join(root, "state", "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
            states[s.get("session")] = s
        except (OSError, ValueError):
            continue
    for s in states.values():
        ts = s.get("ts") or 0
        # A ts in the future is a test fixture left in state/, not a reading (seen on the mac: 1800000000).
        if 0 <= now - ts <= QUOTA_FRESH_S and s.get("five_pct", -1) >= 0:
            if quota is None or ts > quota["ts"]:
                quota = {"five_pct": s.get("five_pct"), "week_pct": s.get("week_pct"), "ts": ts}
    for p in glob.glob(os.path.join(root, "sessions", "*.json")):   # *.key never matches
        try:
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            continue
        pid = int(s.get("pid") or 0)
        started = (s.get("startedAt") or 0) / 1000.0
        if not (alive(pid) if alive else session_alive(pid, started)):
            continue
        st = states.get(s.get("sessionId"), {})
        sessions.append({
            "pid": pid, "session_id": s.get("sessionId"), "name": s.get("name"),
            "cwd": s.get("cwd"), "project": project_of(s.get("cwd")),
            "kind": s.get("kind"), "entrypoint": s.get("entrypoint"), "status": s.get("status"),
            "started": started, "updated": (s.get("updatedAt") or 0) / 1000.0,
            "context_k": st.get("context_k"), "cost_usd": st.get("cost_usd"),
            "session_min": st.get("session_min"), "bar_ts": st.get("ts"),
            "handoff_mtime": handoff_mtime(s.get("cwd")),
        })
    sessions.sort(key=lambda x: x["started"])
    return sessions, quota


# ------------------------------------------------------------------ machine
def disk_mounts() -> list:
    if os.name == "nt":
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        roots = []
        for i in range(26):
            if mask & (1 << i):
                r = chr(65 + i) + ":\\"
                if ctypes.windll.kernel32.GetDriveTypeW(r) == 3:    # DRIVE_FIXED only
                    roots.append(r)
        return roots
    roots = ["/"]
    for base in ("/Volumes", "/mnt", "/media"):
        roots += [p for p in glob.glob(base + "/*") if os.path.ismount(p)]
    return roots


def collect_disks() -> list:
    out, seen = [], set()
    for r in disk_mounts():
        try:
            u = shutil.disk_usage(r)            # statvfs only: never walks the tree
        except OSError:
            continue
        if (u.total, u.free) in seen:           # APFS volumes of one container (/Volumes/Recovery) repeat "/"
            continue
        seen.add((u.total, u.free))
        out.append({"mount": r, "total_gb": round(u.total / 1e9, 1), "free_gb": round(u.free / 1e9, 1)})
    return out

def no_console() -> dict:
    """Popen kwargs: no console window for children of pythonw on Windows (CREATE_NO_WINDOW,
    plus SW_HIDE for Git's msys ssh.exe). Same as presence.no_console; this file stands alone."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}


def run(cmd: list, timeout: float = 5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              **no_console()).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def collect_memory() -> dict:
    if os.name == "nt":
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total", ctypes.c_ulonglong), ("avail", ctypes.c_ulonglong),
                        ("tp", ctypes.c_ulonglong), ("ap", ctypes.c_ulonglong),
                        ("tv", ctypes.c_ulonglong), ("av", ctypes.c_ulonglong),
                        ("ae", ctypes.c_ulonglong)]
        m = MS()
        m.len = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return {"total_gb": round(m.total / 2**30, 1), "free_pct": round(100 * m.avail / m.total),
                "source": "GlobalMemoryStatusEx"}
    if sys.platform == "darwin":
        out = run(["memory_pressure"])
        m = re.search(r"free percentage:\s*(\d+)%", out)
        total = run(["sysctl", "-n", "hw.memsize"]).strip()
        return {"total_gb": round(int(total) / 2**30, 1) if total.isdigit() else None,
                "free_pct": int(m.group(1)) if m else None, "source": "memory_pressure"}
    try:
        info = dict(l.split(":", 1) for l in open("/proc/meminfo"))
        t = int(info["MemTotal"].split()[0]); a = int(info["MemAvailable"].split()[0])
        return {"total_gb": round(t / 2**20, 1), "free_pct": round(100 * a / t), "source": "/proc/meminfo"}
    except (OSError, KeyError, ValueError):
        return {}


def collect_mounts() -> list:
    """Network mounts only (SMB/NFS/sshfs). The server part is reduced to its share name."""
    if os.name == "nt":
        out = run(["net", "use"])
        res = []
        for l in out.splitlines():
            m = re.match(r"\s*(OK|Disconnected|Unavailable)?\s*([A-Z]:)\s+(\\\\\S+)", l)
            if m:
                res.append({"path": m.group(2), "share": m.group(3).rsplit("\\", 1)[-1],
                            "type": "smb", "ok": (m.group(1) or "OK") == "OK"})
        return res
    res = []
    for l in run(["mount"]).splitlines():
        m = re.match(r"(\S+) on (.+?) \((\w+)", l) or re.match(r"(\S+) on (.+?) type (\w+)", l)
        if m and m.group(3).lower() in ("smbfs", "cifs", "nfs", "fuse.sshfs", "macfuse"):
            res.append({"path": m.group(2), "share": m.group(1).rsplit("/", 1)[-1],
                        "type": m.group(3), "ok": os.path.isdir(m.group(2))})
    return res


def collect_tmux() -> list:
    # launchd's PATH is /usr/bin:/bin:/usr/sbin:/sbin; Homebrew's tmux is not on it.
    tmux = shutil.which("tmux") or next((p for p in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux")
                                         if os.path.exists(p)), None)
    if not tmux:
        return []
    names = []
    for sock in ([], ["-L", "enjambre"]):
        for l in run([tmux] + sock + ["ls", "-F", "#{session_name}"]).splitlines():
            if l.strip():
                names.append(("enjambre:" if sock else "") + l.strip())
    return names


def collect_models(url: str = MODELS_URL) -> dict | None:
    """The local model manager answers only on 127.0.0.1. It exposes /health and /jobs; it has
    no 'loaded models' endpoint, so 'what is in memory' = the job running now + its peak memory."""
    try:
        with urllib.request.urlopen(url + "/health", timeout=2) as r:
            health = json.load(r)
    except Exception:
        return None
    catalog = None
    try:
        with urllib.request.urlopen(url + "/catalog", timeout=3) as r:
            catalog = catalog_view(json.load(r))
    except Exception:
        pass
    meas = guarded_measurements()
    jobs = model_jobs_db(os.path.join(HOME, "modelos", "jobs.db"))
    if jobs is not None:
        return {"health": health, "jobs": jobs, "jobs_source": "jobs.db", "server_rss_mb": _rss8765(),
                "catalog": catalog, "measurements": meas}
    jobs = []
    try:
        with urllib.request.urlopen(url + "/jobs", timeout=3) as r:
            for j in json.load(r):
                jobs.append({k: j.get(k) for k in ("id", "task", "state", "created", "started",
                                                    "finished", "load_s", "run_s", "wait_s",
                                                    "peak_footprint_mb", "peak_metal_mb")})
                jobs[-1]["params"] = {k: v for k, v in (j.get("params") or {}).items()
                                      if k in ("diarizer", "model", "num_speakers", "engine")}
    except Exception:
        pass
    return {"health": health, "jobs": jobs, "jobs_source": "/jobs", "server_rss_mb": _rss8765(),
            "catalog": catalog, "measurements": meas}


MEAS_KEYS = ("key", "task", "date", "ok", "quality", "quality_label", "speed", "speed_unit",
             "footprint_mb", "load_s", "swap_growth_mb", "note")


def model_measurements(home: str = HOME) -> list:
    """Every benchmark row (not only the latest per model, which is all /catalog gives): the repo
    copy plus the Mac's own ~/modelos/measurements.jsonl, identical lines once. A row carries the
    time it was measured in `ts` since modelos-mac wrote it (2026-10-07); an older row gets the
    mtime of its result file in ~/modelos/bench-runs/<date>/ when exactly one file matches
    (`ts_from` = "archivo"), otherwise the date alone."""
    seen, out = set(), []
    for path in (os.path.join(home, "Documents", "Portfolio", "modelos-mac", "bench", "measurements.jsonl"),
                 os.path.join(home, "modelos", "measurements.jsonl")):
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line in seen:
                continue
            seen.add(line)
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not isinstance(r, dict) or not r.get("key"):
                continue
            row = {k: r.get(k) for k in MEAS_KEYS}
            ts = r.get("ts")
            row["ts"], row["ts_from"] = (ts, "fila") if isinstance(ts, (int, float)) else (None, None)
            if row["ts"] is None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(r.get("date") or "")):
                hits = [g for g in glob.glob(os.path.join(home, "modelos", "bench-runs", r["date"], glob.escape(r["key"]) + "-*.json"))
                        if not g.endswith(".warm.json")]
                if len(hits) == 1:
                    row["ts"], row["ts_from"] = os.path.getmtime(hits[0]), "archivo"
            out.append(row)
    return out


def guarded_measurements() -> list | None:
    try:
        return model_measurements()
    except Exception:
        return None


def catalog_view(d) -> dict | None:
    """The manager's /catalog reduced to the inventory columns: key, task, size, downloaded,
    default (= predeterminado), the last measurement, the real model id (`repo`, the Hugging Face
    id: public) and the owner's verdict (status + nota, F3). license/env/purpose are dropped."""
    if not isinstance(d, dict):
        return None
    out = []
    for m in d.get("models") or []:
        last = m.get("last") or {}
        out.append({"key": m.get("key"), "task": m.get("task"), "gb": m.get("gb"),
                    "downloaded": m.get("downloaded"), "default": m.get("default"),
                    "runtime": m.get("runtime"), "repo": m.get("repo"), "status": m.get("status"),
                    "nota": m.get("nota") or "", "status_ts": m.get("status_ts"),
                    "last": {k: last.get(k) for k in ("ok", "quality_label", "speed", "speed_unit",
                                                      "footprint_mb", "date")} if last else None})
    return {"budget_mb": d.get("budget_mb"), "models": out}


def model_jobs_db(path: str) -> list | None:
    """Every job of the model manager, read-only, without prompt/input/params (they can name
    private files). The /jobs endpoint was capped at 40 here, which would have turned "n" in
    the comparison into "the last 40" while looking like a total."""
    if not os.path.exists(path):
        return None
    import sqlite3
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=2)
        try:
            rows = con.execute("SELECT id, task, state, created, started, finished, load_s, "
                               "peak_footprint_mb, peak_metal_mb, params FROM jobs ORDER BY created DESC").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    out = []
    for jid, task, state, created, started, finished, load_s, foot, metal, params in rows:
        out.append({"id": jid, "task": task, "state": state, "created": created, "started": started,
                    "model": _model_key(params),
                    "finished": finished, "load_s": load_s, "peak_footprint_mb": foot, "peak_metal_mb": metal,
                    "run_s": round(finished - started, 2) if finished and started else None,
                    "wait_s": round(started - created, 2) if started and created else None})
    return out


def _model_key(params) -> str | None:
    """Only the `model` key of a job's params (the catalog key the manager picked). The rest of
    params can name private files and is never read out."""
    try:
        v = json.loads(params or "{}").get("model")
    except (ValueError, AttributeError):
        return None
    return v if isinstance(v, str) and re.fullmatch(r"[\w.\-]{1,64}", v) else None


def _rss8765() -> int | None:
    pids = run(["lsof", "-ti", "tcp:8765", "-sTCP:LISTEN"]).split() if shutil.which("lsof") else []
    if pids:
        v = run(["ps", "-o", "rss=", "-p", pids[0]]).strip()
        return round(int(v) / 1024) if v.isdigit() else None
    return None


DETACHED_MAX = 30           # most recent detached jobs read per node
BEAT_KEYS = ("ts", "state", "attempt", "elapsed_s", "pct", "turns", "last_tool", "cost_est_usd")


def _tail(path: str, n: int = 40, limit: int = 8000) -> str | None:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - limit))
            lines = f.read().decode("utf-8", "replace").splitlines()[-n:]
    except OSError:
        return None
    return "\n".join(lines) if any(x.strip() for x in lines) else None


def collect_detached(home: str = HOME) -> list:
    """Long detached jobs of THIS node (jobrun.py: ~/.enjambre/jobs/<id>/status.json). Their
    `latido` is a real heartbeat: the supervisor rewrites status.json every beat_s while the job
    runs. A "running" job whose supervisor pid is gone is reported as dead, not as running.
    `cmd` is never read out (it can name private paths); the log tail is for the local page only."""
    root = os.path.join(home, ".enjambre", "jobs")
    try:
        dirs = sorted((os.path.join(root, d) for d in os.listdir(root)), key=os.path.getmtime, reverse=True)
    except OSError:
        return []
    out = []
    for d in dirs:
        st_path = os.path.join(d, "status.json")
        if len(out) >= DETACHED_MAX or not os.path.isfile(st_path):
            continue
        try:
            with open(st_path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(st, dict) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", str(st.get("id") or "")):
            continue
        state = st.get("state")
        pid = st.get("supervisor_pid")
        if state in ("running", "paused_by_priority") and isinstance(pid, int) and not pid_alive(pid):
            state = "dead"
        beat = st.get("latido") if isinstance(st.get("latido"), dict) else {}
        out.append({"id": st["id"], "kind": st.get("kind"), "repo": st.get("repo"), "state": state,
                    "start": st.get("start"), "end": st.get("end"), "rc": st.get("rc"),
                    "attempt": st.get("attempt"), "reason": st.get("reason"),
                    "cost_usd": st.get("cost_usd"), "turns": st.get("turns"),
                    # prompt 20: the scheduler's view of it
                    "priority": st.get("priority"), "resumable": st.get("resumable"),
                    "between_turns": st.get("between_turns"), "pgid": st.get("pgid"),
                    "sched_reason": st.get("sched_reason"), "sched_since": st.get("sched_since"),
                    "preemptions": (st.get("preemptions") or [])[-6:] or None,
                    "latido": {k: beat.get(k) for k in BEAT_KEYS} if beat else None,
                    "last": str(beat.get("last") or "")[:200] or None,
                    "log": _tail(os.path.join(d, "log")) if st.get("kind") == "script" else None,
                    "supervisor_log": _tail(os.path.join(d, "supervisor.log"), 15)})
    return out


def load_thresholds(path: str | None = None) -> dict | None:
    """{id: value} from ~/.claude/rules/thresholds.json, the ONLY source of thresholds. None when
    unreadable: the page then says "⚠ umbrales" instead of colouring with a remembered number."""
    path = path or os.path.join(claude_dir(), "rules", "thresholds.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {t["id"]: t["condition"]["value"] for t in d["thresholds"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def remember_quota(db_path: str, root: str, now: float) -> dict | None:
    """Last known quota reading, kept in history.db (meta 'quota_last') so the visor shows
    "13 % / 46 % · hace 2 h" instead of "sin lectura" when no window has rendered for a while.
    The newest statusline state file of any age is a candidate; a newer one replaces it."""
    best = None
    for p in glob.glob(os.path.join(root, "state", "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            continue
        ts = s.get("ts") or 0
        if 0 <= now - ts and s.get("five_pct", -1) >= 0 and (best is None or ts > best["ts"]):
            best = {"five_pct": s.get("five_pct"), "week_pct": s.get("week_pct"), "ts": ts}
    con = history_db(db_path)
    try:
        row = con.execute("SELECT v FROM meta WHERE k='quota_last'").fetchone()
        last = json.loads(row[0]) if row else None
        if best and (last is None or best["ts"] > (last.get("ts") or 0)):
            con.execute("INSERT OR REPLACE INTO meta VALUES ('quota_last', ?)", (json.dumps(best),))
            con.commit()
            last = best
        return last
    finally:
        con.close()


# ------------------------------------------------------------------ telemetry (prompt 19)
# The fast half: < 300 ms on the PC, a few hundred ms over ssh on the mac. Every source is
# guarded: a metric that cannot be read is null ("n/d" on the page), never an invented 0.
TELE_DIR = os.path.join(HOME, ".enjambre", "visor")
RING_MAX = 1440                     # 4 h at one line every 10 s (the live cycle); the file trims itself
PREV_MAX_S = 120                    # CPU ticks older than this are not a fair baseline: sample again
TOP_N = 12
PRESSURE = {1: "normal", 2: "warn", 4: "critical"}


def _num(rx: str, text: str, cast=float):
    m = re.search(rx, text or "")
    try:
        return cast(m.group(1)) if m else None
    except (TypeError, ValueError):
        return None


def parse_vm_stat(text: str) -> dict:
    """vm_stat -> GB per bucket. Page size comes from the header (16 KiB on Apple Silicon)."""
    page = _num(r"page size of (\d+) bytes", text, int) or 4096
    pages = {}
    for k, v in re.findall(r'^"?([A-Za-z][^:"]+)"?:\s+(\d+)\.?\s*$', text, re.M):
        pages[k.strip().lower()] = int(v)
    gb = lambda k: round(pages[k] * page / 2**30, 2) if k in pages else None
    return {"active_gb": gb("pages active"), "wired_gb": gb("pages wired down"),
            "compressed_gb": gb("pages occupied by compressor"), "free_gb": gb("pages free"),
            "inactive_gb": gb("pages inactive")}


def parse_swap(text: str):
    """sysctl vm.swapusage: 'total = 5120.00M  used = 3969.38M ...' -> GB used."""
    used = _num(r"used\s*=\s*([\d.]+)M", text)
    return round(used / 1024, 2) if used is not None else None


def parse_ioreg_gpu(text: str) -> dict:
    """ioreg IOAccelerator PerformanceStatistics: Device Utilization % (no sudo)."""
    return {"pct": _num(r'"Device Utilization %"\s*=\s*(\d+)', text, int),
            "vram_used_gb": None, "mem_in_use_gb": (lambda b: round(b / 2**30, 2) if b is not None else None)(
                _num(r'"In use system memory"\s*=\s*(\d+)', text, int)), "source": "ioreg"}


def parse_pmset_therm(text: str) -> dict:
    """pmset -g therm. CPU_Speed_Limit appears only once macOS has recorded a thermal event; until
    then the honest value is null (unknown), not 100. 'No ... warning level has been recorded'
    = no warning so far, which is what `throttled: false` means here."""
    limit = _num(r"CPU_Speed_Limit\s*=\s*(\d+)", text, int)
    quiet = "No thermal warning level has been recorded" in (text or "")
    throttled = (limit < 100) if limit is not None else (False if quiet else None)
    return {"cpu_speed_limit": limit, "throttled": throttled, "temp_c": None, "source": "pmset"}


def parse_nvidia(text: str):
    """nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu
    --format=csv,noheader,nounits -> '16, 2679, 4096, 50'."""
    parts = [p.strip() for p in (text or "").strip().splitlines()[0].split(",")] if (text or "").strip() else []
    if len(parts) < 4 or not parts[0].replace(".", "").isdigit():
        return None
    f = lambda s: float(s) if s.replace(".", "").isdigit() else None
    return {"pct": round(f(parts[0])) if f(parts[0]) is not None else None,
            "vram_used_gb": round(f(parts[1]) / 1024, 2) if f(parts[1]) is not None else None,
            "vram_total_gb": round(f(parts[2]) / 1024, 2) if f(parts[2]) is not None else None,
            "temp_c": f(parts[3]), "source": "nvidia-smi"}


def parse_ps(text: str) -> list:
    """`ps -Aceo pid,ppid,pgid,pcpu,rss,comm` (-c: the bare name, never a path). macOS %cpu is a
    decaying average over ~a minute: right for "who is eating the machine", not a 1 s sample."""
    rows = []
    for l in (text or "").splitlines()[1:]:
        p = l.split(None, 5)
        if len(p) < 6:
            continue
        try:
            rows.append({"pid": int(p[0]), "ppid": int(p[1]), "pgid": int(p[2]), "cpu": float(p[3]),
                         "rss_gb": round(int(p[4]) / 2**20, 2), "name": proc_name(p[5])})
        except ValueError:
            continue
    return rows


def proc_name(raw: str) -> str:
    """'sshd-session: <user>@notty' carries the login name: keep what comes before ': '; a node
    process shows up as 'playwright --viewport-size 1280x800': drop the flags."""
    return raw.strip().split(": ", 1)[0].split(" --", 1)[0][:40]   # node retitles itself with its argv


def parse_netstat(text: str):
    """netstat -ibn: bytes in/out summed over real links (<Link#..>, not lo0)."""
    per = parse_netstat_if(text)
    return (sum(v[0] for v in per.values()), sum(v[1] for v in per.values())) if per else None


def parse_netstat_if(text: str) -> dict:
    """netstat -ibn per interface: {"en0": (bytes_in, bytes_out)} over real links (prompt 22 §2.3:
    which interface the traffic takes, so the card can say "entra por en0 (Ethernet)")."""
    out = {}
    for l in (text or "").splitlines()[1:]:
        p = l.split()
        if len(p) >= 10 and p[2].startswith("<Link#") and not p[0].startswith("lo"):
            # Name Mtu Network Address Ipkts Ierrs Ibytes Opkts Oerrs Obytes Coll (macOS with -b);
            # a link without an address (gif0, utun of a VPN) has one column less
            o = 0 if len(p) >= 11 else -1
            try:
                out[p[0].rstrip("*")] = (int(p[6 + o]), int(p[9 + o]))
            except ValueError:
                continue
    return out


def parse_hw_ports(text: str) -> dict:
    """networksetup -listallhardwareports -> {"en0": "Ethernet", "en1": "Wi-Fi"}."""
    out, port = {}, None
    for l in (text or "").splitlines():
        if l.startswith("Hardware Port:"):
            port = l.split(":", 1)[1].strip()
        elif l.startswith("Device:") and port:
            out[l.split(":", 1)[1].strip()] = port[:30]
            port = None
    return out


def parse_iostat_mb(text: str):
    """iostat -Id: MB transferred per disk since boot, summed. The last line holds the numbers,
    three per disk (KB/t, xfrs, MB). None when it cannot be read."""
    lines = [l.split() for l in (text or "").splitlines() if l.strip()]
    try:
        nums = [float(x) for x in lines[-1]] if lines else []
    except ValueError:
        return None
    return sum(nums[2::3]) if nums and len(nums) % 3 == 0 else None


def busiest(deltas: dict):
    """The interface that moved the most bytes, or None when nothing moved."""
    k = max(deltas, key=lambda i: deltas[i], default=None)
    return k if k is not None and deltas[k] > 0 else None


# Windows (prompt 22 §2.3): one 1-second sample of the network and disk counters. typeperf, not
# Get-Counter: both took ~1.7 s here (the sample itself is 1 s), typeperf skips starting
# PowerShell and gives in and out apart. It runs in parallel with the CPU sample.
TYPEPERF = ["typeperf", "-sc", "1", r"\Network Interface(*)\Bytes Received/sec",
            r"\Network Interface(*)\Bytes Sent/sec", r"\PhysicalDisk(_Total)\Disk Bytes/sec"]
TP_COL = re.compile(r"\\([^\\(]+)\(([^)]*)\)\\([^\\]+)$")


def iface_kind(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("wireless", "wi-fi", "wifi", "wlan", "802.11", "airport")):
        return "Wi-Fi"
    if "ethernet" in n or "gbe" in n:
        return "Ethernet"
    return (name or "")[:30]


def parse_typeperf(text: str):
    r"""typeperf CSV -> {"net_in", "net_out", "disk"} in bytes/s plus "iface", the adapter that
    moved the most. Column headers are \\HOST\Object(instance)\Counter: only the object, instance
    and counter are read, the host is never kept. None if the sample line is missing."""
    import csv
    rows = [r for r in csv.reader((text or "").splitlines()) if len(r) > 1]
    if len(rows) < 2:
        return None
    out, per = {"net_in": 0.0, "net_out": 0.0, "disk": None}, {}
    for h, v in zip(rows[0][1:], rows[1][1:]):
        m = TP_COL.search(h)
        try:
            x = float(v)
        except ValueError:
            continue
        if not m:
            continue
        obj, inst, ctr = m.groups()
        if obj == "PhysicalDisk":
            out["disk"] = x
        elif obj == "Network Interface":
            out["net_in" if "Received" in ctr else "net_out"] += x
            per[inst] = per.get(inst, 0.0) + x
    out["iface"] = busiest(per)
    return out


def tag_processes(rows: list, tags: dict) -> None:
    """tags = {pid: label}. A row gets the label of itself, its process group or the nearest
    ancestor in `tags`: python3 at 380 % becomes 'tmux enjambre: cohbench' or 'trabajo: <id>'."""
    parent = {r["pid"]: r.get("ppid") for r in rows}
    for r in rows:
        lab, pid, hops = None, r["pid"], 0
        while pid and hops < 30 and lab is None:
            lab = tags.get(pid)
            pid, hops = parent.get(pid), hops + 1
        if lab is None and r.get("pgid") in tags:
            lab = tags[r["pgid"]]
        if lab:
            r["tag"] = lab


# prompt 20: set from --lab-port / --adopted by `enjambre live` (the PC reads config/scheduler.toml)
EXTERNAL_PORTS: dict = {}     # listening port -> label ("externo: render_lab")
ADOPTED: dict = {}            # pid -> priority the owner gave it with `enjambre adoptar`


def enjambre_tags(home: str = HOME, root: str | None = None) -> dict:
    """pid -> label for what enjambre knows is running on this node: detached jobs (jobrun
    status.json), panes of the enjambre tmux server, and live Claude Code windows."""
    tags = {os.getpid(): "enjambre: telemetría"}   # this very reading, so it is not a mystery row
    tmux = shutil.which("tmux") or next((p for p in ("/opt/homebrew/bin/tmux", "/usr/local/bin/tmux")
                                         if os.path.exists(p)), None)
    if tmux:
        for l in run([tmux, "-L", "enjambre", "list-panes", "-a", "-F", "#{pane_pid} #{session_name}"], 3).splitlines():
            p = l.split(None, 1)
            if len(p) == 2 and p[0].isdigit():
                tags[int(p[0])] = "tmux enjambre: " + p[1].strip()
    for s in glob.glob(os.path.join(root or claude_dir(), "sessions", "*.json")):
        try:
            with open(s, encoding="utf-8") as f:
                d = json.load(f)
            tags[int(d["pid"])] = "sesión: " + (d.get("name") or project_of(d.get("cwd")))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    for st_path in glob.glob(os.path.join(home, ".enjambre", "jobs", "*", "status.json")):
        try:
            with open(st_path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            continue
        if st.get("state") not in ("running", "paused_by_priority") or not pid_alive(st.get("supervisor_pid") or 0):
            continue
        for k in ("supervisor_pid", "child_pid", "pgid"):   # job labels win over the tmux pane
            if isinstance(st.get(k), int):
                tags[st[k]] = "trabajo: " + str(st.get("id"))
    # an external scheduler's server (the render lab): its renders are its children, so they
    # inherit this label and never show up as "no gestionados" next to their adopted job
    for port, label in EXTERNAL_PORTS.items():
        for l in run(["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"], 3).split():
            if l.isdigit():
                tags[int(l)] = label
    for pid, prio in ADOPTED.items():
        tags[pid] = "adoptado: " + prio
    return tags


# the lab's own progress line (lab-video render --log-every): "120/520 frames · 2.4 fps · 51 s elapsed · ETA 166 s"
LAB_PROGRESS = re.compile(r"(\d+)/(\d+) frames · ([\d.]+) fps · (\d+) s elapsed · ETA (\d+) s")


def lab_progress(log_path: str) -> dict | None:
    """The last progress line of a lab job's log -> frames, pct, eta_s. GET /jobs has no progress
    field (prompt 22 §2.2), the log does. None when there is no such line yet (or no log)."""
    text = _tail(log_path, n=400, limit=16000)
    m = list(LAB_PROGRESS.finditer(text or ""))
    if not m:
        return None
    done, total, _fps, _el, eta = m[-1].groups()
    done, total = int(done), int(total)
    return {"frames": done, "frames_total": total, "eta_s": int(eta),
            "pct": round(100.0 * done / total, 1) if total else None}


def lab_jobs(url: str, timeout: float = 2, logs: str | None = None) -> list:
    """GET <url>/jobs of the render lab: only what is active (queued / running / paused). Each
    carries `polled` (when this answer came: the job's signal, it has no heartbeat of its own) and,
    with `logs` (the lab's lab_jobs/ folder), the progress read from <logs>/<id>.log."""
    import urllib.request
    with urllib.request.urlopen(url.rstrip("/") + "/jobs", timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    polled = time.time()
    keep = ("id", "status", "preset", "kind", "started", "elapsed_s", "created")
    out = []
    for j in data:
        if not isinstance(j, dict) or j.get("status") not in ("queued", "running", "paused"):
            continue
        row = {k: j.get(k) for k in keep}
        row["polled"] = polled
        if logs and j.get("status") != "queued" and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(j.get("id"))):
            row.update(lab_progress(os.path.join(os.path.expanduser(logs), "%s.log" % j["id"])) or {})
        out.append(row)
    return out


def _cpu_pct(prev: list, cur: list):
    """Per-cpu [busy, total] tick pairs -> (total %, per-core %)."""
    if not prev or len(prev) != len(cur):
        return None, None
    per = []
    for (b0, t0), (b1, t1) in zip(prev, cur):
        per.append(round(100.0 * (b1 - b0) / (t1 - t0)) if t1 > t0 else 0)
    tb = sum(c[0] for c in cur) - sum(p[0] for p in prev)
    tt = sum(c[1] for c in cur) - sum(p[1] for p in prev)
    return (round(100.0 * tb / tt) if tt > 0 else None), per


def darwin_ticks() -> list:
    """host_processor_info(PROCESSOR_CPU_LOAD_INFO): ticks per cpu, no subprocess, no sudo."""
    import ctypes
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    count, info, n = ctypes.c_uint(), ctypes.POINTER(ctypes.c_int)(), ctypes.c_uint()
    lib.mach_host_self.restype = ctypes.c_uint
    if lib.host_processor_info(lib.mach_host_self(), 2, ctypes.byref(count), ctypes.byref(info),
                               ctypes.byref(n)) != 0:
        raise OSError("host_processor_info")
    try:
        out = []
        for i in range(count.value):   # CPU_STATE_USER, SYSTEM, IDLE, NICE
            u, s, idle, nice = (info[i * 4 + k] & 0xffffffff for k in range(4))
            out.append([u + s + nice, u + s + nice + idle])
        return out
    finally:
        task = ctypes.c_uint.in_dll(lib, "mach_task_self_")
        lib.vm_deallocate(task, ctypes.cast(info, ctypes.c_void_p), ctypes.c_size_t(n.value * 4))


def win_ticks() -> list:
    import ctypes
    ft = [ctypes.c_ulonglong() for _ in range(3)]
    ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(x) for x in ft))
    idle, kernel, user = (x.value for x in ft)       # kernel time includes idle
    return [[kernel + user - idle, kernel + user]]


# ------------------------------------------------------------------ Windows priority (prompt 20)
# The PC's side of the scheduler: OS priority class + EcoQoS, and suspend/resume of a process
# tree. Every call goes through guard_call: a refusal or a missing API is a reason, never a crash.
WIN_CLASSES = {"normal": 0x20, "BelowNormal": 0x4000, "Idle": 0x40}
PROCESS_SET_INFORMATION, PROCESS_SUSPEND_RESUME, PROCESS_QUERY_LIMITED = 0x0200, 0x0800, 0x1000


def guard_call(name, fn):
    """(ok, text) for one system call; the text says what happened or why not."""
    try:
        return fn()
    except Exception as e:
        return False, "%s: %s" % (name, type(e).__name__)


def win_rule(rule: str) -> tuple:
    """'BelowNormal+EcoQoS' -> ('BelowNormal', True)."""
    parts = [p for p in (rule or "").split("+") if p]
    cls = next((p for p in parts if p in WIN_CLASSES), "normal")
    return cls, "EcoQoS" in parts


def win_set_priority(pid: int, rule: str) -> tuple:
    """PriorityClass + EcoQoS (PROCESS_POWER_THROTTLING_STATE) on one running process. EcoQoS only
    changes scheduling on Windows 11; on 10 the call may succeed and do nothing, and it says so."""
    def go():
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        cls, eco = win_rule(rule)
        h = k.OpenProcess(PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED, False, int(pid))
        if not h:
            return False, "OpenProcess %d: error %d" % (pid, k.GetLastError())
        try:
            if not k.SetPriorityClass(h, WIN_CLASSES[cls]):
                return False, "SetPriorityClass: error %d" % k.GetLastError()
            msg = "clase %s" % cls
            if eco:
                class PPTS(ctypes.Structure):
                    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG),
                                ("StateMask", wintypes.ULONG)]
                st = PPTS(1, 1, 1)   # PROCESS_POWER_THROTTLING_EXECUTION_SPEED on
                ok = k.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st))   # 4 = ProcessPowerThrottling
                build = sys.getwindowsversion().build
                msg += (" + EcoQoS" if ok else " (EcoQoS rechazado: error %d)" % k.GetLastError())
                if ok and build < 22000:
                    msg += " (Windows 10 build %d: sin efecto hasta Windows 11)" % build
            return True, msg
        finally:
            k.CloseHandle(h)
    if os.name != "nt":
        return False, "win_set_priority: solo en Windows"
    return guard_call("win_set_priority", go)


def win_suspend(pid: int, resume: bool = False) -> tuple:
    """NtSuspendProcess / NtResumeProcess (what Process Explorer uses) on one pid."""
    def go():
        import ctypes
        k, nt = ctypes.windll.kernel32, ctypes.windll.ntdll
        h = k.OpenProcess(PROCESS_SUSPEND_RESUME, False, int(pid))
        if not h:
            return False, "OpenProcess %d: error %d" % (pid, k.GetLastError())
        try:
            st = (nt.NtResumeProcess if resume else nt.NtSuspendProcess)(h)
            return st == 0, ("reanudado" if resume else "suspendido") if st == 0 else "NTSTATUS 0x%08x" % (st & 0xFFFFFFFF)
        finally:
            k.CloseHandle(h)
    if os.name != "nt":
        return False, "win_suspend: solo en Windows"
    return guard_call("win_suspend", go)


def win_processes() -> dict:
    """NtQuerySystemInformation(SystemProcessInformation): pid -> (ppid, name, cpu 100ns, ws bytes).
    One call lists every process; wmic Win32_PerfFormattedData took 8.5 s here (measured)."""
    import ctypes
    nt = ctypes.windll.ntdll
    size = ctypes.c_ulong(1 << 20)
    while True:
        buf = ctypes.create_string_buffer(size.value)
        st = nt.NtQuerySystemInformation(5, buf, size, ctypes.byref(size)) & 0xffffffff
        if st == 0xC0000004:                          # STATUS_INFO_LENGTH_MISMATCH
            size = ctypes.c_ulong(size.value + (1 << 18))
            continue
        if st != 0:
            raise OSError("NtQuerySystemInformation %x" % st)
        break
    base, off, out = ctypes.addressof(buf), 0, {}
    u64 = lambda o: ctypes.c_ulonglong.from_address(base + off + o).value
    while True:
        nxt = ctypes.c_ulong.from_address(base + off).value
        ln = ctypes.c_ushort.from_address(base + off + 56).value
        ptr = ctypes.c_void_p.from_address(base + off + 64).value
        name = ctypes.wstring_at(ptr, ln // 2) if ptr and ln else "System Idle"
        out[u64(80)] = (u64(88), name, u64(40) + u64(48), u64(144))
        if not nxt:
            return out
        off += nxt


def mac_io(per: dict | None, disk_mb, prev: dict | None, now: float, ports: dict) -> dict | None:
    """Network (per interface) and disk rates since the previous call; None without a baseline.
    "iface" is the interface the most bytes came IN through: the card says "entra por en0
    (Ethernet)", the way to see the traffic takes the cable."""
    dts = now - ((prev or {}).get("at") or now)
    if dts <= 0:
        return None
    mb = lambda b: round(max(0, b) / dts / 2**20, 2)
    io = {"net_in_mb_s": None, "net_out_mb_s": None, "disk_mb_s": None, "iface": None, "iface_kind": None}
    pn = (prev or {}).get("net_if") or {}
    if per and pn:
        d = {k: (v[0] - pn[k][0], v[1] - pn[k][1]) for k, v in per.items() if k in pn}
        io["net_in_mb_s"] = mb(sum(max(0, x[0]) for x in d.values()))
        io["net_out_mb_s"] = mb(sum(max(0, x[1]) for x in d.values()))
        io["iface"] = busiest({k: x[0] for k, x in d.items()})
        io["iface_kind"] = iface_kind(ports.get(io["iface"], "")) if io["iface"] else None
    pd = (prev or {}).get("disk_mb")
    if disk_mb is not None and pd is not None:
        io["disk_mb_s"] = round(max(0.0, disk_mb - pd) / dts, 2)
    return io if any(v is not None for v in io.values()) else None


def win_io(tp: dict | None) -> dict | None:
    if not tp:
        return None
    mb = lambda b: None if b is None else round(b / 2**20, 2)
    return {"net_in_mb_s": mb(tp["net_in"]), "net_out_mb_s": mb(tp["net_out"]), "disk_mb_s": mb(tp["disk"]),
            "iface": (tp["iface"] or "")[:40] or None, "iface_kind": iface_kind(tp["iface"]) if tp["iface"] else None}


def collect_telemetry(prev: dict | None = None, now: float | None = None, home: str = HOME,
                      root: str | None = None) -> tuple:
    """Returns (telemetria, new_prev). `prev` = the previous call's counters (CPU ticks, net bytes):
    with one less than PREV_MAX_S old, CPU % is the average since then, no sleep at all; without
    it, two samples 250 ms apart."""
    t0 = time.time()
    now = now or t0
    errors = []
    prev = prev if prev and 0 < now - (prev.get("at") or 0) <= PREV_MAX_S else None

    def guard(name, fn, default=None):
        try:
            return fn()
        except Exception as e:
            errors.append("%s: %s" % (name, type(e).__name__))
            return default

    tele = {"at": dt.datetime.fromtimestamp(now, LIMA).isoformat(timespec="seconds"), "ms": None,
            "cpu": None, "mem": None, "gpu": None, "thermal": None, "top": [], "io": None}
    new_prev = {"at": now}
    if sys.platform == "darwin":
        ticks_fn = darwin_ticks
    elif os.name == "nt":
        ticks_fn = win_ticks
    else:
        ticks_fn = None
    ticks = guard("cpu", ticks_fn) if ticks_fn else None
    procs0 = guard("top", win_processes) if os.name == "nt" else None
    nv_proc = None
    if os.name == "nt" and shutil.which("nvidia-smi"):
        try:   # started now, read at the end: its ~140 ms overlap the CPU sample
            nv_proc = subprocess.Popen(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                                        "--format=csv,noheader,nounits"], stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, text=True, **no_console())
        except OSError:
            nv_proc = None
    tp_proc = None
    if os.name == "nt":
        try:   # 1 s counter sample, overlapping the CPU sample; read at the end
            tp_proc = subprocess.Popen(TYPEPERF, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       stdin=subprocess.DEVNULL, text=True, **no_console())
        except OSError as e:
            errors.append("io: %s" % type(e).__name__)
    base = prev.get("ticks") if prev else None
    procs1 = win0 = None
    # Windows: per-process CPU needs two snapshots, so it always samples 200 ms (the total still
    # uses the longer window since the previous call when there is one). The mac reads ps.
    if ticks is not None and (base is None or procs0 is not None):
        time.sleep(0.2)
        win0, ticks = ticks, guard("cpu", ticks_fn)
        procs1 = guard("top", win_processes) if procs0 is not None else None
        if base is None:
            base = win0
    if ticks is not None:
        new_prev["ticks"] = ticks
        pct, per = _cpu_pct(base, ticks)
        tele["cpu"] = {"pct": pct, "per_core": per if per and len(per) > 1 else None, "load1": None,
                       "cores": len(ticks) if len(ticks) > 1 else os.cpu_count(),
                       "window_s": round(now - prev["at"], 1) if prev and prev.get("ticks") else 0.2}
    if sys.platform == "darwin":
        load = run(["sysctl", "-n", "vm.loadavg"], 3)
        if tele["cpu"] is not None:
            tele["cpu"]["load1"] = _num(r"\{\s*([\d.]+)", load)
            p = run(["sysctl", "-n", "hw.perflevel0.logicalcpu", "hw.perflevel1.logicalcpu"], 3).split()
            if len(p) == 2 and all(x.isdigit() for x in p):
                tele["cpu"]["p_cores_n"], tele["cpu"]["e_cores_n"] = int(p[0]), int(p[1])

        def mem():
            m = parse_vm_stat(run(["vm_stat"], 3))
            total = run(["sysctl", "-n", "hw.memsize"], 3).strip()
            lvl = run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], 3).strip()
            parts = [m.get(k) for k in ("active_gb", "wired_gb", "compressed_gb")]
            return {"total_gb": round(int(total) / 2**30, 1) if total.isdigit() else None,
                    "used_gb": round(sum(parts), 1) if None not in parts else None,
                    "wired_gb": m.get("wired_gb"), "compressed_gb": m.get("compressed_gb"),
                    "swap_used_gb": parse_swap(run(["sysctl", "-n", "vm.swapusage"], 3)),
                    "pressure": PRESSURE.get(int(lvl)) if lvl.isdigit() else None,
                    "source": "vm_stat+memorystatus"}
        tele["mem"] = guard("mem", mem)
        tele["gpu"] = guard("gpu", lambda: parse_ioreg_gpu(run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], 3)))
        tele["thermal"] = guard("thermal", lambda: parse_pmset_therm(run(["pmset", "-g", "therm"], 3)))

        def top():
            rows = parse_ps(run(["ps", "-Aceo", "pid,ppid,pgid,pcpu,rss,comm"], 3))
            tag_processes(rows, enjambre_tags(home, root))
            rows.sort(key=lambda r: -r["cpu"])
            return [{k: r.get(k) for k in ("pid", "name", "cpu", "rss_gb", "tag")} for r in rows[:TOP_N]]
        tele["top"] = guard("top", top, [])
        per = guard("net", lambda: parse_netstat_if(run(["netstat", "-ibn"], 3)))
        disk = guard("disk", lambda: parse_iostat_mb(run(["iostat", "-Id"], 3)))
        # which port each interface is (en0 = Ethernet): asked once, then carried in prev
        ports = (prev or {}).get("ports") or guard("ports", lambda: parse_hw_ports(
            run(["networksetup", "-listallhardwareports"], 3))) or {}
        new_prev["ports"] = ports
        if per:
            new_prev["net_if"] = {k: list(v) for k, v in per.items()}
        if disk is not None:
            new_prev["disk_mb"] = disk
        tele["io"] = mac_io(per, disk, prev, now, ports)
    elif os.name == "nt":
        def mem():
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("len", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (k, ctypes.c_ulonglong) for k in ("total", "avail", "tp", "ap", "tv", "av", "ae")]
            m = MS()
            m.len = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            # Windows has no "pressure" level; memory load % is the closest honest reading.
            return {"total_gb": round(m.total / 2**30, 1), "used_gb": round((m.total - m.avail) / 2**30, 1),
                    "wired_gb": None, "compressed_gb": None, "swap_used_gb": None,
                    "commit_gb": round((m.tp - m.ap) / 2**30, 1), "load_pct": m.load,
                    "pressure": None, "source": "GlobalMemoryStatusEx"}
        tele["mem"] = guard("mem", mem)
        tele["thermal"] = {"cpu_speed_limit": None, "throttled": None, "temp_c": None,
                           "source": "n/d (MSAcpi_ThermalZoneTemperature: Not supported)"}
        if procs0 is not None and procs1 is not None and ticks is not None and win0 is not None:
            def top():
                # GetSystemTimes sums every core: cpu share of the machine x cores = % of one core,
                # the same scale as macOS ps (a busy 4-thread process reads ~400 %).
                dt100 = max(1, (ticks[0][1] - win0[0][1]))
                rows = []
                for pid, (ppid, name, cpu1, ws) in procs1.items():
                    if pid == 0:
                        continue
                    c0 = procs0.get(pid)
                    cpu = round(100.0 * (cpu1 - c0[2]) / dt100 * (os.cpu_count() or 1), 1) if c0 and c0[1] == name else 0.0
                    rows.append({"pid": pid, "ppid": ppid, "name": name[:40], "cpu": cpu,
                                 "rss_gb": round(ws / 2**30, 2)})
                tag_processes(rows, enjambre_tags(home, root))
                rows.sort(key=lambda r: (-r["cpu"], -r["rss_gb"]))
                return [{k: r.get(k) for k in ("pid", "name", "cpu", "rss_gb", "tag")} for r in rows[:TOP_N]]
            tele["top"] = guard("top", top, [])
        gpu = None
        if nv_proc is not None:
            try:
                gpu = parse_nvidia(nv_proc.communicate(timeout=3)[0])
            except (subprocess.SubprocessError, OSError):
                nv_proc.kill()
        tele["gpu"] = gpu or {"pct": None, "vram_used_gb": None, "source": "n/d"}
        if tp_proc is not None:
            def io():
                try:
                    out = tp_proc.communicate(timeout=4)[0]
                except subprocess.TimeoutExpired:
                    tp_proc.kill()
                    raise
                return win_io(parse_typeperf(out))
            tele["io"] = guard("io", io)
    tele["ms"] = int((time.time() - t0) * 1000)
    if errors:
        tele["errors"] = errors
    return tele, new_prev


def ring_line(tele: dict) -> dict:
    """What one ring line keeps: the numbers the sparklines draw, nothing that names anything."""
    g = lambda *ks: _dig(tele, ks)
    return {"at": tele.get("at"), "cpu": g("cpu", "pct"), "gpu": g("gpu", "pct"),
            "mem": g("mem", "used_gb"), "swap": g("mem", "swap_used_gb"), "pr": g("mem", "pressure"),
            "csl": g("thermal", "cpu_speed_limit"),
            "net_in": g("io", "net_in_mb_s"), "net_out": g("io", "net_out_mb_s"), "disk": g("io", "disk_mb_s")}


def _dig(d, ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def ring_append(path: str, line: dict, keep: int = RING_MAX) -> int:
    """Append one JSON line; when the file passes keep + 10 %, rewrite it with the last `keep`."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) > keep * 1.1:
        lines = lines[-keep:]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp, path)
    return len(lines)


def telemetry_cli(home: str = HOME) -> dict:
    """--telemetria: measure, keep the counters for next time, append the ring line."""
    prev_path = os.path.join(TELE_DIR, "telemetria.prev.json")
    try:
        with open(prev_path, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = None
    tele, new_prev = collect_telemetry(prev, home=home)
    os.makedirs(TELE_DIR, exist_ok=True)
    with open(prev_path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(new_prev, f)
    os.replace(prev_path + ".tmp", prev_path)
    try:
        ring_append(os.path.join(TELE_DIR, "telemetria.ring.jsonl"), ring_line(tele))
    except OSError as e:
        tele.setdefault("errors", []).append("ring: %s" % type(e).__name__)
    return tele


def live_payload(node: str, now: float | None = None, root: str | None = None, home: str = HOME) -> dict:
    """--live: what the resident `enjambre live --serve` needs every cycle, in ONE process per
    node (one ssh): sessions, running detached jobs with their beat, tmux, telemetry. No
    transcripts, no usage scan: those stay in the 5-minute snapshot."""
    now = now or time.time()
    root = root or claude_dir()
    errors = []

    def guard(name, fn, default):
        try:
            return fn()
        except Exception as e:
            errors.append("%s: %s" % (name, type(e).__name__))
            return default
    sessions, _ = guard("sessions", lambda: collect_sessions(root, now), ([], None))
    detached = [d for d in guard("detached", lambda: collect_detached(home), [])
                if d.get("state") in ("running", "dead", "paused_by_priority", "evicted_resumable")
                or (d.get("end") and _recent(d.get("end"), now))]
    for d in detached:
        d.pop("log", None); d.pop("supervisor_log", None)
    return {"schema": SCHEMA, "node": node, "at": now, "sessions": sessions, "detached": detached,
            "tmux": guard("tmux", collect_tmux, []), "telemetria": guard("telemetria", lambda: telemetry_cli(home), None),
            "errors": errors}


def _recent(stamp, now: float, within_s: float = 3600) -> bool:
    ts = parse_ts(stamp)
    return ts is not None and now - ts <= within_s


# ------------------------------------------------------------------ main
def collect(node: str, root: str | None = None, cursor_path: str | None = None,
            now: float | None = None, models: bool = False, history_path: str | None = None) -> dict:
    now = now or time.time()
    root = root or claude_dir()
    cursor_path = cursor_path or os.path.join(HOME, ".enjambre", "visor", "cursor.json")
    errors = []
    prices = load_prices()

    def guard(name, fn, default):
        try:
            return fn()
        except Exception as e:                        # one broken source must not blank the snapshot
            errors.append("%s: %s" % (name, type(e).__name__))
            return default

    sessions, quota = guard("sessions", lambda: collect_sessions(root, now), ([], None))
    usage = guard("usage", lambda: collect_usage(root, cursor_path, now, prices), {"days": {}})
    hist_path = history_path or os.path.join(os.path.dirname(cursor_path), "history.json")
    history = guard("history", lambda: collect_history(root, hist_path, prices,
                                                       extra_roots=backup_roots() if history_path is None else ()),
                    None)
    db_path = os.path.splitext(hist_path)[0] + ".db"
    quota_last = guard("quota_last", lambda: remember_quota(db_path, root, now), None)
    return {
        "schema": SCHEMA, "node": node, "collected": now, "os": platform.system().lower(),
        "python": platform.python_version(),
        "home_name": project_of(HOME),       # build.py shows it as "inicio-<node>" (never history.db)
        "sessions": sessions, "quota": quota, "quota_last": quota_last, "usage": usage, "history": history,
        "prices": {m: list(p) for m, p in prices.items()},
        "prices_estimated": prices_estimated(),
        "thresholds": load_thresholds(),
        "disks": guard("disks", collect_disks, []),
        "memory": guard("memory", collect_memory, {}),
        "mounts": guard("mounts", collect_mounts, []),
        "tmux": guard("tmux", collect_tmux, []),
        "detached": guard("detached", collect_detached, []),
        # Opt-in: on Windows 127.0.0.1:8765 is the SSH tunnel TO the mac's manager, and probing
        # it there would count the mac's models twice.
        "models": guard("models", collect_models, None) if models else None,
        "errors": errors,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--out", default=os.path.join(HOME, ".enjambre", "visor", "snapshot.json"))
    ap.add_argument("--print", action="store_true", help="write to stdout instead of --out")
    ap.add_argument("--models", action="store_true", help="probe the model manager on 127.0.0.1:8765")
    ap.add_argument("--telemetria", action="store_true",
                    help="fast: CPU, RAM, GPU, thermal, top processes -> stdout + ring line (prompt 19)")
    ap.add_argument("--live", action="store_true",
                    help="fast: sessions + running detached jobs + tmux + telemetry -> stdout (enjambre live)")
    ap.add_argument("--lab", help="--live: the render lab's base URL as seen from this node (prompt 20)")
    ap.add_argument("--lab-port", type=int, help="--live: the lab server's port, to tag its renders")
    ap.add_argument("--lab-logs", help="--live: the lab's per-job log folder, to read render progress (prompt 22)")
    ap.add_argument("--adopted", default="", help="--live: pid:priority,... adopted by the owner")
    a = ap.parse_args(argv)
    if a.lab_port:
        EXTERNAL_PORTS[a.lab_port] = "externo: render_lab"
    for item in [x for x in a.adopted.split(",") if ":" in x]:
        pid, prio = item.split(":", 1)
        if pid.isdigit():
            ADOPTED[int(pid)] = prio[:12]
    if a.telemetria or a.live:
        out = live_payload(a.node) if a.live else telemetry_cli()
        if a.live and a.lab:
            try:
                out["externos"] = {"render_lab": lab_jobs(a.lab, logs=a.lab_logs)}
            except Exception as e:           # lab down: say so, the rest of the payload stands
                out["externos"] = {"render_lab": None}
                out.setdefault("errors", []).append("render_lab: %s" % type(e).__name__)
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        return 0
    snap = collect(a.node, models=a.models)
    data = json.dumps(snap, ensure_ascii=False)
    if a.print:
        sys.stdout.write(data + "\n")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, a.out)                            # readers never see half a file
    return 0


if __name__ == "__main__":
    sys.exit(main())
