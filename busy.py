"""busy — "this node is running something long" marker, written on the node, read from Windows.

A long job on the Mac (model bench, LTX video, transcription, a Blender render) writes
~/.enjambre/busy.json while it runs:

    {"quien": "sesion-g", "desde": "2026-10-07T01:40:00-05:00", "tipo": "bench", "pid": 46464}

and removes it on exit (tools/busy.sh with `trap ... EXIT`, render_stl.py with try/finally).
WHY a pid in it: a job killed with -9 or a kernel panic never runs its trap, so the reader
checks the pid is alive in the same ssh call; a dead pid means the marker is stale, not busy.
WHY it exists at all: memory_pressure and the model manager's /health cannot see a bench run in
tmux or the GPU (DECISIONES 155); the job itself is the only one that knows. Stdlib only.
"""
from __future__ import annotations

import json

PATH = "~/.enjambre/busy.json"
# One shell line, POSIX sh: print the marker, then ALIVE if its pid still runs.
READ_CMD = ('f=$HOME/.enjambre/busy.json; [ -f "$f" ] || exit 0; cat "$f"; echo; '
            'p=$(sed -n \'s/.*"pid": *\\([0-9][0-9]*\\).*/\\1/p\' "$f"); '
            '[ -n "$p" ] && kill -0 "$p" 2>/dev/null && echo ALIVE; exit 0')
MARK = "__BUSY__"


def parse(out: str) -> dict | None:
    """Output of READ_CMD -> the marker with "alive": bool, or None when there is no marker.
    A marker that is not JSON is reported as such (alive False): unknown is not busy."""
    lines = [l for l in (out or "").splitlines() if l.strip()]
    if not lines:
        return None
    alive = lines[-1].strip() == "ALIVE"
    body = lines[:-1] if alive else lines
    try:
        m = json.loads("\n".join(body))
    except json.JSONDecodeError:
        return {"tipo": "?", "quien": "?", "alive": False, "error": "busy.json is not JSON"}
    if not isinstance(m, dict):
        return None
    m["alive"] = alive
    return m


def section(out: str) -> str:
    """The part of a combined ssh output after MARK (callers append READ_CMD after `echo MARK`)."""
    return out.split(MARK, 1)[1] if MARK in out else ""


def describe(m: dict | None) -> str:
    if not m:
        return "no busy marker"
    state = "running" if m.get("alive") else "stale (pid gone)"
    return f"busy: {m.get('tipo')} by {m.get('quien')} since {m.get('desde')} ({state})"
