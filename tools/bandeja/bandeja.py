#!/usr/bin/env python3
"""bandeja.py — the PC side of the task inbox (docs/BANDEJA.md).

Every round of `watch` (rate-limited to [bandeja].every_min) this module:
  1. pushes the closed lists (repos, kinds, workers, daily limit) to the web form;
  2. GETs the tasks the web holds with no state yet (signed request, purpose "poll");
  3. per task: verifies the HMAC over the exact stored bytes, then re-applies EVERY rule the PHP
     applied (the PHP is a convenience; this is the gate): id format, duplicate, closed lists,
     confidencial/reservado, paths, sizes, daily limit;
  4. writes state/bandeja/tasks/TASK-<id>.md and puts it in enjambre's queue (never a shell:
     the only argv that ever runs is watch's own `enjambre.py run --repo <listed> --task <path>`);
  5. reports "recibida" / "rechazada", and later "corriendo" / "hecha" / "fallida" read from
     state/running, state/done and the ledger, with the 500-char summary passed through the
     same leak check as the web visor.

No retries of a job, ever: a task id is recorded as seen BEFORE it is queued.
Usage: bandeja.py --once [--dry-run]     (watch imports tick() instead)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools" / "visor"))
import web as W  # noqa: E402  (config loader + leak check, one implementation)

KEY_ENV = "ENJAMBRE_BANDEJA_KEY"
STATE = REPO / "state" / "bandeja"
SEEN = STATE / "seen.json"
LIMA = dt.timezone(dt.timedelta(hours=-5))
ID_RE = re.compile(r"b\d{8}-[0-9a-f]{8}")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
PATH_RE = re.compile(r"[A-Za-z0-9_.*][A-Za-z0-9_.*/-]{0,150}")
SENS_STRUCT = re.compile(r"confidencial|reservado", re.I)        # repo + paths: same rule as enjambre.SENSITIVE
SENS_TEXT = re.compile(r"\b(confidencial|reservado)\b", re.I)    # free text: word-bounded ("análisis" is fine)
DEFAULTS = {"enabled": False, "every_min": 2, "repos": [], "kinds": ["claude", "tests"],
            "workers": ["claude", "stub-ok"], "daily_limit": 5, "max_turns": 8, "allow_every_h": 6}


class Reject(Exception):
    """The task breaks a rule; the message goes back to the phone (never echoes the content)."""


def config(cfg: dict | None = None) -> dict:
    cfg = cfg if cfg is not None else W.load_config()
    c = dict(DEFAULTS)
    c.update(cfg.get("bandeja") or {})
    c["url"] = c.get("url") or (cfg.get("web") or {}).get("url", "")
    return c


def routing_kinds() -> set:
    import routing as R
    return set((R.load(REPO / "routing.toml").get("kinds") or {}).keys())


def today(now: float) -> str:
    return dt.datetime.fromtimestamp(now, LIMA).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ transport
def sign(key: bytes, purpose: str, ts: str, body: bytes) -> str:
    return W.sign(key, ts, body, purpose)


class Client:
    def __init__(self, url: str, key: bytes, opener=None, timeout: int = 20):
        self.url, self.key, self.timeout = url.rstrip("/"), key, timeout
        self.open = opener or W.https_open

    def _call(self, method: str, query: str, purpose: str, body: bytes = b"") -> tuple:
        ts = str(int(time.time() * 1000))
        req = urllib.request.Request(self.url + "/api/tareas.php" + query, data=body if method == "POST" else None,
                                     method=method, headers={"X-Ts": ts, "X-Firma": sign(self.key, purpose, ts, body),
                                                             "Content-Type": "application/json",
                                                             "User-Agent": "enjambre-bandeja/1"})
        try:
            with self.open(req, timeout=self.timeout) as r:
                return r.status, r.read(1_000_000)
        except urllib.error.HTTPError as e:
            return e.code, e.read(2000)
        except (urllib.error.URLError, OSError) as e:
            return 0, type(e).__name__.encode()

    def tasks(self) -> list:
        st, raw = self._call("GET", "", "poll")
        if st != 200:
            raise ConnectionError("GET tareas: HTTP %s" % st)
        return (json.loads(raw) or {}).get("tareas") or []

    def post(self, purpose: str, payload: dict) -> int:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        st, raw = self._call("POST", "?p=" + purpose, purpose, body)
        try:   # a 200 that is not our JSON (a PHP error page, a captive portal) is a failure
            return st if st != 200 or json.loads(raw).get("ok") is True else 502
        except (ValueError, AttributeError):
            return 502


# ------------------------------------------------------------------ rules
def verify(item: dict, key: bytes) -> dict:
    """The task dict, only if the HMAC over the stored bytes checks out. Verify, THEN parse."""
    raw, ts, sig = item.get("tarea"), item.get("ts"), item.get("firma")
    if not (isinstance(raw, str) and isinstance(ts, str) and isinstance(sig, str)):
        raise Reject("tarea mal formada")
    if not hmac.compare_digest(sign(key, "task", ts, raw.encode("utf-8")), sig.lower()):
        raise Reject("firma inválida")
    try:
        t = json.loads(raw)
    except ValueError:
        raise Reject("tarea no es JSON")
    if not isinstance(t, dict):
        raise Reject("tarea mal formada")
    return t


def check(t: dict, c: dict, kinds: set) -> list:
    """Every rule of docs/BANDEJA.md §3. Returns the clean list of paths."""
    if not ID_RE.fullmatch(str(t.get("id", ""))):
        raise Reject("id inválido")
    repo, kind, worker = t.get("repo"), t.get("kind"), t.get("worker")
    if not (isinstance(repo, str) and NAME_RE.fullmatch(repo) and repo in c["repos"]):
        raise Reject("repo fuera de la lista")
    if ".." in repo or SENS_STRUCT.search(repo):
        raise Reject("repo sensible")
    if kind not in c["kinds"] or kind not in kinds or kind == "gui":
        raise Reject("kind fuera de la lista")
    if worker not in c["workers"]:
        raise Reject("worker fuera de la lista")
    titulo, objetivo, criterio = t.get("titulo"), t.get("objetivo"), t.get("criterio")
    if not (isinstance(titulo, str) and 3 <= len(titulo.strip()) <= 100 and "\n" not in titulo):
        raise Reject("título inválido")
    if not (isinstance(objetivo, str) and 3 <= len(objetivo.strip()) <= 8000):
        raise Reject("objetivo inválido")
    if not (isinstance(criterio, str) and 3 <= len(criterio.strip()) <= 2000):
        raise Reject("criterio inválido")
    paths = t.get("archivos")
    if not (isinstance(paths, list) and 1 <= len(paths) <= 20):
        raise Reject("archivos: de 1 a 20")
    for p in paths:
        if not (isinstance(p, str) and PATH_RE.fullmatch(p)) or ".." in p or SENS_STRUCT.search(p):
            raise Reject("ruta no permitida")
    if any(SENS_TEXT.search(x) for x in (titulo, objetivo, criterio)):
        raise Reject("menciona confidencial/reservado")
    return list(paths)


def task_markdown(t: dict, paths: list) -> str:
    """The contract enjambre.validate expects. The owner's text is data inside a file."""
    return ("---\nkind: %s\n---\n# %s\n\n> Tarea %s, creada desde la bandeja web.\n\n## Goal\n\n%s\n\n"
            "## Files you may touch\n\n%s\n\n## Done criteria\n\n%s\n"
            % (t["kind"], t["titulo"].strip(), t["id"], t["objetivo"].strip(),
               "\n".join("- `%s`" % p for p in paths), t["criterio"].strip()))


# ------------------------------------------------------------------ queue
def enqueue(t: dict, text: str, c: dict) -> dict:
    """Into enjambre's queue (not a synchronous run): watch dispatches it when the node is
    ready, so a busy or down Mac just means the task waits. Returns {node, reason}."""
    import enjambre as E
    import presence as P
    import routing as R
    task = STATE / "tasks" / ("TASK-%s.md" % t["id"])
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_text(text, encoding="utf-8", newline="\n")
    repo_dir = E.PORTFOLIO / t["repo"]
    try:
        tid = E.validate(repo_dir, task, t["worker"])
    except E.ContractError as e:
        raise Reject("contrato: " + str(e)[:120])
    nodes = E.load_nodes()
    d, levels = E.decide(t["kind"], t["worker"], repo_dir, nodes, R.load(E.ROUTING), time.time())
    if d["action"] == "refuse" or not d.get("node"):
        raise Reject("el enrutador no tiene nodo: " + str(d.get("reason"))[:120])
    try:
        P.enqueue(E.STATE, tid, text, {
            "node": d["node"], "repo": t["repo"], "worker": t["worker"],
            "knobs": {"ENJAMBRE_MAX_TURNS": str(int(c["max_turns"]))},
            "level_at_enqueue": levels.get(d["node"]), "kind": t["kind"], "kind_source": "bandeja",
            "routed_by": d["routed_by"], "route_reason": d["reason"], "origin": "bandeja"})
    except P.QueueError as e:
        raise Reject("cola: " + str(e)[:120])
    return {"node": d["node"], "reason": d["reason"]}


# ------------------------------------------------------------------ state
def load_seen(path: Path = SEEN) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"tasks": {}, "meta": {}}


def save_seen(seen: dict, path: Path = SEEN) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def accepted_today(seen: dict, day: str) -> int:
    return sum(1 for s in seen["tasks"].values() if s.get("day") == day and s.get("accepted"))


def ledger_entry(tid: str, ledger: Path) -> dict:
    last = {}
    try:
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if tid in line:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("id") == tid:
                    last = r
    except OSError:
        pass
    return last


_WORDS: set | None = None


def private_words() -> set:
    """Local user + each node's ssh user/hostname; computed once per watch process."""
    global _WORDS
    if _WORDS is None:
        import enjambre as E
        hosts = [c.get("host") for c in E.load_nodes().values() if c.get("host") not in (None, "local")]
        _WORDS = W.forbidden_words({}, set(), W.ssh_identity_words(hosts) | set(
            (W.load_config().get("web") or {}).get("forbid") or []))
    return _WORDS


def safe_summary(text, words: set) -> str | None:
    if not text:
        return None
    text = str(text)[:500]
    hits = W.leak_check(json.dumps(text, ensure_ascii=False), words)
    return "(resumen retenido en la PC: %s)" % ", ".join(hits) if hits else text


def progress(tid: str, root: Path, ledger: Path, words: set) -> dict | None:
    """The state a queued task has reached, or None if nothing changed visibly."""
    if (root / "running" / tid).is_dir():
        return {"estado": "corriendo"}
    rc_file = root / "done" / tid / "rc"
    if not rc_file.exists():
        return None
    rc = rc_file.read_text().strip()
    e = ledger_entry(tid, ledger)
    ok = rc == "0" and e.get("status", "ok") == "ok"
    return {"estado": "hecha" if ok else "fallida", "rama": e.get("branch"), "subida": e.get("pushed"),
            "coste": e.get("cost_usd"), "nodo": e.get("node"),
            "detalle": safe_summary(e.get("result") or ("salida %s, estado %s" % (rc, e.get("status"))), words)}


# ------------------------------------------------------------------ one round
def tick(now: float | None = None, cfg: dict | None = None, client=None, enqueue_fn=enqueue,
         seen_path: Path = SEEN, state_root: Path | None = None, ledger: Path | None = None,
         kinds: set | None = None, words: set | None = None, force: bool = False) -> dict:
    """Called by `watch` every round; does nothing until [bandeja].every_min has passed."""
    now = now or time.time()
    c = config(cfg)
    if not c.get("enabled"):
        return {"bandeja": "off"}
    key = os.environ.get(KEY_ENV, "")
    if len(key) < 32 and client is None:
        return {"bandeja": "sin clave (%s)" % KEY_ENV}
    seen = load_seen(seen_path)
    if not force and now - seen["meta"].get("polled_at", 0) < c["every_min"] * 60 - 5:
        return {"bandeja": "no toca"}
    client = client or Client(c["url"], key.encode())
    state_root = state_root or (REPO / "state")
    ledger = ledger or (REPO / "jobs.jsonl")
    kinds = kinds if kinds is not None else routing_kinds()
    words = words if words is not None else private_words()
    res = {"bandeja": "ok", "nuevas": 0, "encoladas": 0, "rechazadas": 0, "estados": 0, "errores": []}

    allow = {"repos": c["repos"], "kinds": [k for k in c["kinds"] if k in kinds and k != "gui"],
             "workers": c["workers"], "daily_limit": int(c["daily_limit"])}
    sig = hashlib.sha256(json.dumps(allow, sort_keys=True).encode()).hexdigest()
    if sig != seen["meta"].get("allow_sig") or now - seen["meta"].get("allow_at", 0) > c["allow_every_h"] * 3600:
        if client.post("allow", allow) == 200:
            seen["meta"].update(allow_sig=sig, allow_at=now)
        else:
            res["errores"].append("allow")

    try:
        items = client.tasks()
    except (ConnectionError, ValueError) as e:
        res["errores"].append(str(e)[:80])
        items = []
    seen["meta"]["polled_at"] = now
    day = today(now)
    for item in items:
        res["nuevas"] += 1
        tid = None
        try:
            t = verify(item, client.key)
            tid = str(t.get("id", ""))
            if not ID_RE.fullmatch(tid):
                raise Reject("id inválido")
            prev = seen["tasks"].get(tid)
            if prev:                                   # duplicate: never queued twice
                if client.post("estado", dict(prev.get("last") or {"estado": prev["estado"]}, id=tid)) == 200:
                    res["estados"] += 1
                continue
            paths = check(t, c, kinds)
            if accepted_today(seen, day) >= int(c["daily_limit"]):
                raise Reject("límite diario de la PC (%d)" % int(c["daily_limit"]))
            # Seen BEFORE queueing: a crash between the two can lose a task, never run it twice.
            seen["tasks"][tid] = {"estado": "recibida", "day": day, "accepted": True, "at": now}
            save_seen(seen, seen_path)
            info = enqueue_fn(t, task_markdown(t, paths), c)
            last = {"estado": "recibida", "job": tid, "nodo": info.get("node"),
                    "detalle": "en cola para %s" % info.get("node")}
            seen["tasks"][tid]["last"] = last
            res["encoladas"] += 1
        except Reject as r:
            res["rechazadas"] += 1
            if tid is None:
                try:
                    tid = str(json.loads(item.get("tarea") or "{}").get("id", ""))
                except (ValueError, AttributeError):
                    tid = ""
            if not ID_RE.fullmatch(tid or ""):
                continue                                # cannot even name it: nothing to report
            if seen["tasks"].get(tid, {}).get("accepted"):
                continue
            last = {"estado": "rechazada", "detalle": str(r)}
            seen["tasks"][tid] = {"estado": "rechazada", "day": day, "accepted": False, "at": now, "last": last}
        if client.post("estado", dict(seen["tasks"][tid]["last"], id=tid)) == 200:
            res["estados"] += 1
        else:
            seen["tasks"][tid]["unsent"] = True
        save_seen(seen, seen_path)

    for tid, s in seen["tasks"].items():               # progress of what is already queued
        if s.get("estado") not in ("recibida", "corriendo") and not s.get("unsent"):
            continue
        p = progress(tid, state_root, ledger, words) if s.get("accepted") else None
        if p and p["estado"] != s["estado"]:
            s["estado"] = p["estado"]
            s["last"] = dict(p, job=tid)
            s["unsent"] = True
        if s.get("unsent"):
            if client.post("estado", dict(s["last"], id=tid)) == 200:
                s.pop("unsent", None)
                res["estados"] += 1
    save_seen(seen, seen_path)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="one round now, ignoring every_min")
    a = ap.parse_args(argv)
    print(json.dumps(tick(force=a.once), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
