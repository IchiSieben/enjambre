"""`enjambre run --engine local`: send a TASK to the local code model on a node.

The model manager lives in the sibling repo `modelos-mac` (one heavy model at a time, single
lane queue, API on the node's 127.0.0.1:8765). This engine opens an SSH tunnel, submits the
TASK text as a `text` job, waits, stores the answer under state/local/<id>.md and writes one
ledger line with engine "local". It never touches git: a local model returns text, not commits.
Stdlib only, like the rest of enjambre.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent
CLIENT = HERE.parent / "modelos-mac" / "client.py"
SYSTEM = ("You are a careful senior engineer. Follow the task contract exactly. Answer with the "
          "complete content of every file you change, each in a fenced block preceded by its "
          "path, and nothing outside 'Files you may touch'.")


def load_client(path: Path = CLIENT):
    if not path.is_file():
        raise FileNotFoundError(f"modelos-mac client not found at {path}")
    spec = importlib.util.spec_from_file_location("modelos_client", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(tid: str, task_text: str, node_name: str, host: str, out_dir: Path,
        client=None, max_tokens: int = 4096) -> dict:
    """Returns the ledger entry. `client` is injectable for tests."""
    client = client or load_client()
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    job, result = client.run("text", prompt=task_text,
                             params={"system": SYSTEM, "max_tokens": max_tokens, "temp": 0.2},
                             node=host)
    out = None
    if job["state"] == "done":
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{tid}.md"
        out.write_text(result["text"], encoding="utf-8")
    return {
        "id": tid, "node": node_name, "worker": "local", "engine": "local", "repo": None,
        "branch": None, "start": started,
        "end": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "exit": 0 if job["state"] == "done" else 1, "duration_s": job.get("run_s"),
        "pushed": False, "usage": None, "cost_usd": 0.0,
        "local_job": job["id"], "wait_s": job.get("wait_s"), "load_s": job.get("load_s"),
        "peak_footprint_mb": job.get("peak_footprint_mb"),
        "output": out.relative_to(HERE).as_posix() if out else None,
        "note": job.get("error"),
    }
