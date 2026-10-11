#!/usr/bin/env python3
"""razones.py — every failed or incomplete job gets a one-line cause, a log path and a test flag.

`enrich(entry)` is called by append_ledger before a row is written; `--backfill` applies the same
to the rows already in jobs.jsonl (adds missing fields only, never changes a written one; the file
is copied to state/jobs.jsonl.bak-<stamp> first).

The cause, in this order (the first that says something wins):
  permission_denials  -> "permisos denegados: Bash, Write"
  reason budget       -> "presupuesto agotado (tope US$2.00)"
  reason killed / dead -> "muerto: supervisor terminado (exit -15)"
  exit 255 over ssh   -> "mac inalcanzable (ssh exit 255)"
  a log with an error -> "exit 2: __main__.py: error: unrecognized arguments: --id"
  note about the push -> "rama sin subir: gitleaks ..."
  anything else       -> "exit N, sin log guardado"
Nothing is invented: when the only fact is the exit code, the line says so.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "jobs.jsonl"
STATE = HERE / "state"
BAD = ("failed", "incomplete")
MAX_REASON = 160
# a line in a script log that names the error (stream-json agent logs are not scanned: their
# cause is in the structured fields)
ERR_RE = re.compile(r"(Traceback|Error\b|error:|fatal:|FAILED|No such file|not found|denied|"
                    r"Exception|cannot |could not )", re.I)
SANDBOX_REPOS = {"enjambre-sandbox"}
# fire tests of session E3 and the smoke tests (tutorial 25): failures on purpose, not work
TEST_ID_RE = re.compile(r"^(e3-|prueba-|denial-probe$|must-fail$|.*-smoke(-|$)|.*smoke-\d)")


def log_of(jid: str, state: Path = STATE) -> Path | None:
    """The job's log, newest attempt first: state/jobs/<id>/log, log.2, log.1 (long jobs and,
    since F3, short ones too)."""
    d = state / "jobs" / str(jid)
    for name in ("log", "log.3", "log.2", "log.1"):
        if (d / name).is_file():
            return d / name
    return None


# prompt 22 §3: a cause line is shown in the Atención table and in screenshots; the login name in a
# home path is the owner's identity, not information. /Users/<u>/ -> ~/, C:\Users\<u>\ -> ~\
# (also with JSON-doubled backslashes or forward slashes).
HOME_RE = re.compile(r"(?:/Users|/home)/[^/\s\"'<>|:]+|[A-Za-z]:(?:\\+|/)Users(?:\\+|/)[^\\/\s\"'<>|:]+", re.I)


def home_paths(s):
    """Cut the user name out of home paths; anything that is not a string comes back as is."""
    return HOME_RE.sub("~", s) if isinstance(s, str) else s


def _clip(s: str) -> str:
    s = home_paths(" ".join(str(s).split()))
    return s if len(s) <= MAX_REASON else s[:MAX_REASON - 1] + "…"


def error_line(text: str) -> str | None:
    """Last line naming an error in a plain (non stream-json) log."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines or lines[-1].lstrip().startswith("{\"") and "session_id" in lines[-1]:
        return None
    for l in reversed(lines[-200:]):
        if not l.lstrip().startswith("{") and ERR_RE.search(l):
            return l.strip()
    return None


def tools_denied(pd) -> list[str]:
    out = []
    for d in pd or []:
        t = (d.get("tool") or d.get("tool_name")) if isinstance(d, dict) else None
        if t and t not in out:
            out.append(t)
    return out


def explain(e: dict, log_text: str | None = None) -> str | None:
    """One line on why a failed/incomplete job did not finish. None for ok jobs."""
    if e.get("status") not in BAD:
        return None
    code = e.get("exit")
    pd = e.get("permission_denials")
    reason = e.get("reason")
    if pd or reason == "permission_denials":
        tools = tools_denied(pd)
        return _clip("permisos denegados: " + (", ".join(tools) if tools else "%d llamadas" % len(pd or [])))
    if reason == "budget":
        b = e.get("budget_usd")
        return "presupuesto agotado" + (" (tope US$%.2f)" % b if isinstance(b, (int, float)) else "")
    if reason in ("killed", "dead"):
        return "muerto: supervisor terminado" + (" (exit %s)" % code if code is not None else "")
    if reason and reason not in ("permission_denials", "budget", "killed", "dead"):
        return _clip(str(reason))
    if code == 255 and e.get("node") not in (None, "win", "local"):
        return "%s inalcanzable (ssh exit 255)" % e.get("node")
    line = error_line(log_text) if log_text else None
    if line:
        return _clip("exit %s: %s" % (code, line))
    if e.get("result"):            # the agent's own last words
        return _clip("exit %s: %s" % (code, e["result"]) if code not in (None, 0) else e["result"])
    note = e.get("note") or ""
    if "not pushed" in note or "gitleaks" in note:
        return _clip("rama sin subir: " + note.splitlines()[-1])
    if log_text:   # the last line of text (a closing JSON summary is skipped)
        text = [l for l in log_text.splitlines() if l.strip() and not l.lstrip().startswith("{")]
        if text:
            return _clip("exit %s; última línea: %s" % (code, text[-1].strip()))
        return "exit %s (ver log)" % code
    return "exit %s, sin log guardado" % code


def is_test(e: dict) -> bool:
    if e.get("test") is True:
        return True
    return (e.get("repo") in SANDBOX_REPOS or str(e.get("worker") or "").startswith("stub")
            or bool(TEST_ID_RE.match(str(e.get("id") or ""))))


def enrich(e: dict, state: Path = STATE) -> dict:
    """Adds `test`, and for failed/incomplete jobs `reason` (a sentence; a bare code like
    "budget" is kept in `reason_code`) and `log_path` (relative to the repo). Never overwrites
    a sentence that is already there."""
    e = dict(e)
    if is_test(e):
        e["test"] = True
    if e.get("status") in BAD:
        lp = log_of(e.get("id"), state)
        if lp and not e.get("log_path"):
            try:
                e["log_path"] = lp.relative_to(HERE).as_posix()
            except ValueError:
                e["log_path"] = lp.as_posix()
        r = e.get("reason")
        if not r or r in ("budget", "killed", "dead", "permission_denials"):
            text = lp.read_text(encoding="utf-8", errors="replace") if lp else None
            sentence = explain(e, text)
            if r:
                e["reason_code"] = r
            e["reason"] = sentence
    return e


def backfill(ledger: Path = LEDGER, state: Path = STATE) -> dict:
    rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    out = [enrich(r, state) for r in rows]
    changed = sum(1 for a, b in zip(rows, out) if a != b)
    if changed:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(ledger, state / ("jobs.jsonl.bak-" + stamp))
        tmp = ledger.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out),
                       encoding="utf-8", newline="\n")
        tmp.replace(ledger)
    bad = [r for r in out if r.get("status") in BAD]
    return {"rows": len(out), "changed": changed, "bad": len(bad),
            "bad_without_reason": sum(1 for r in bad if not r.get("reason")),
            "bad_with_log": sum(1 for r in bad if r.get("log_path")),
            "tests": sum(1 for r in out if r.get("test"))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", action="store_true", help="enrich the rows already in jobs.jsonl")
    ap.add_argument("--show", action="store_true", help="print id, status and reason of bad jobs")
    a = ap.parse_args(argv)
    if a.backfill:
        print(json.dumps(backfill(), ensure_ascii=False))
    if a.show:
        for l in LEDGER.read_text(encoding="utf-8").splitlines():
            r = json.loads(l)
            if r.get("status") in BAD:
                print("%-34s %-10s %s%s" % (r["id"][:34], r["status"], "[prueba] " if r.get("test") else "", r.get("reason")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
