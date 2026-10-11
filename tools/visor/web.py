"""web.py — the visor snapshot that may leave the house, and its signed upload.

`public_snapshot(b)` builds a NEW dict field by field from an explicit allowlist. Nothing is
copied wholesale and then pruned: a field added to build.py tomorrow does not reach the web
until someone adds it here on purpose (docs/conceptos/permitidos-vs-prohibidos.md).

`leak_check(text)` is the second wall, run on the serialized JSON: real project names that
are not public, paths, the local and remote user names, IPs and the words confidencial/reservado. Any hit =
no upload, and the hit is logged by kind (never the value).

`upload()` POSTs to <url>/api/ingest.php with an HMAC-SHA256 signature over "<ts>.<body>",
key from ENJAMBRE_VISOR_INGEST_KEY (user environment variable, never in the repo). The house
only sends; nothing on the internet can reach it (docs/VISOR-WEB.md).
"""
from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
import razones as RZ  # noqa: E402  (home_paths: prompt 22 §3)
CONFIG = REPO / "config" / "visor-web.toml"
SALT = Path(os.path.expanduser("~/.enjambre/visor/alias.salt"))
STAMP = REPO / "state" / "visor" / "web-upload.json"
INGEST_ENV = "ENJAMBRE_VISOR_INGEST_KEY"
MAX_BODY = 256 * 1024                       # same cap as ingest.php

SENSITIVE = re.compile(r"\b(confidencial|reservado)\b", re.I)          # word-bounded: "análisis" is fine here
PATHLIKE = re.compile(r"[A-Za-z]:\\\\|\\\\\\\\|/Users/|/home/|/Volumes/|~/")
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
SAFE_WORD = re.compile(r"[\w.\-: ]{0,64}")


class LeakError(Exception):
    pass


def load_config(path: Path = CONFIG) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


# ------------------------------------------------------------------ aliases
def salt(path: Path = SALT) -> bytes:
    """Random, created once, never uploaded: keeps aliases stable across uploads (the detail view
    links by alias) without letting anyone hash a dictionary of project names to undo them."""
    try:
        s = path.read_bytes()
        if len(s) >= 16:
            return s
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    s = secrets.token_bytes(32)
    path.write_bytes(s)
    return s


ALIAS_FILE = REPO / "config" / "visor-alias.toml"
ALIAS_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")


def load_aliases(path: Path = ALIAS_FILE) -> dict:
    """{folder: public alias} from config/visor-alias.toml (owner-edited, F3). Only plain
    lowercase slugs are accepted: anything else is ignored and the project keeps its code."""
    try:
        d = tomllib.loads(path.read_text(encoding="utf-8")).get("alias") or {}
    except (OSError, ValueError):
        return {}
    out = {}
    for folder, v in d.items():
        a = (v or {}).get("alias") if isinstance(v, dict) else v
        if isinstance(a, str) and ALIAS_RE.fullmatch(a):
            out[str(folder)] = a
    return out


def published(keep: set, aliases: dict) -> set:
    """Names that may appear as they are: `keep`, plus folders whose alias is their own name."""
    return set(keep) | {f for f, a in aliases.items() if a.lower() == f.lower()}


def aliaser(keep: set, key: bytes, aliases: dict | None = None):
    """Web name of a project: keep -> itself; alias in visor-alias.toml -> the alias; otherwise an
    opaque code. The local visor never calls this: it always shows the real name."""
    aliases = aliases or {}

    def al(name):
        name = str(name or "?")
        if name in keep:
            return name
        if name in aliases:
            return aliases[name]
        return "proyecto-" + hmac.new(key, name.encode("utf-8"), hashlib.sha256).hexdigest()[:5]
    return al


# ------------------------------------------------------------------ allowlist
def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _pick(d, keys, f=_num) -> dict:
    d = d if isinstance(d, dict) else {}
    return {k: f(d.get(k)) for k in keys}


def _word(x):
    """Short label (task type, state, model key): kept only if it is a plain word."""
    return x if isinstance(x, str) and SAFE_WORD.fullmatch(x) else None


def _stamp(x):
    """ISO date or datetime (2026-10-07T01:40:00Z): digits and separators only."""
    return x if isinstance(x, str) and re.fullmatch(r"[\dTZ:+\-. ]{0,32}", x) else None


def _bool(x):
    return bool(x) if x is not None else None


USAGE_KEYS = ("in", "out", "cw", "cr", "msgs", "cost", "unpriced_msgs")
PROJECT_KEYS = ("sessions", "windows", "active_s", "prompts", "responses", "subagents", "cost",
                "unpriced_msgs", "first", "last")
TIMELINE_KEYS = ("start", "end", "active_s", "prompts", "responses", "subagents", "tokens", "cost")
LAST_KEYS = ("speed", "footprint_mb")
SUB_KEYS = ("start", "end", "responses", "tokens", "cost")
MEAS_KEYS = ("ts", "quality", "speed", "footprint_mb", "load_s", "swap_growth_mb")
MJOB_KEYS = ("created", "run_s", "load_s", "wait_s", "peak_footprint_mb")
JOB_KEYS = ("t0", "duration_s", "cost_usd", "exit")
LABEL = re.compile(r"[A-Za-z0-9 .()+-]{1,24}")


MODEL_STATES = ("en uso", "sirve", "por probar", "no sirve", "descartado")
WEB_LOG_LINES = 200
WEB_LOG_BYTES = 12000
WEB_LOGS_MAX = 12


def clean_why(text, words: set) -> str | None:
    """A cause line for the web: whole if it passes the leak check, else only its code part
    ("exit 2") and a note that the detail stays home. Never half-redacted."""
    if not isinstance(text, str) or not text:
        return None
    t = RZ.home_paths(text)[:160]       # the login name never travels, not even in the fallback head
    if not leak_check(t, words):
        return t
    head = t.split(":", 1)[0].split(";", 1)[0]
    return (head if len(head) <= 40 and not leak_check(head, words) else "fallo") + " (detalle solo en local)"


def clean_log(text, words: set) -> str | None:
    """The tail of a log, only if the whole tail passes the leak check (no paths, IPs, private
    names, confidencial/reservado). Otherwise None: the page says "log solo en local"."""
    if not isinstance(text, str) or not text.strip():
        return None
    lines = text.splitlines()[-WEB_LOG_LINES:]
    # an agent's stream-json log is its whole conversation (commands, file contents): never
    # published, however clean it looks
    if any(l.lstrip().startswith('{"') for l in lines):
        return None
    t = RZ.home_paths("\n".join(lines))[-WEB_LOG_BYTES:]
    # checked as it will travel: JSON escaping can turn text into something path-like
    return None if leak_check(t, words) or leak_check(json.dumps(t, ensure_ascii=False), words) else t


def public_snapshot(b: dict, keep: set, key: bytes, aliases: dict | None = None) -> dict:
    aliases = load_aliases() if aliases is None else aliases
    al = aliaser(keep, key, aliases)
    words = forbidden_words(b, published(keep, aliases))
    # stable opaque ids for jobs and threads: the page keeps them in its URL across refreshes,
    # and neither a job id nor a session-id prefix leaves the house
    hid = lambda kind, v: (kind[0] + "-" + hmac.new(key, (kind + ":" + str(v)).encode("utf-8"), hashlib.sha256).hexdigest()[:8]) if v else None
    nodes = list((b.get("nodes") or {}).keys())
    node_ok = lambda n: n if n in nodes else None            # node names come from nodes.toml (win, mac)
    out = {"redacted": True, "built": _num(b.get("built")), "today": _word(b.get("today")),
           "thresholds": {k: _num(v) for k, v in (b.get("thresholds") or {}).items()
                          if k == "quota-5h" or re.fullmatch(r"visor-[a-z0-9-]{1,40}", k)} if b.get("thresholds") else None,
           "prices_estimated": _bool(b.get("prices_estimated")),
           "ledger_n": _num(b.get("ledger_n")), "ledger_incomplete": _num(b.get("ledger_incomplete")),
           "totals": {k: _pick((b.get("totals") or {}).get(k), USAGE_KEYS) for k in ("today", "week")}}
    q = b.get("quota")
    out["quota"] = dict(_pick(q, ("five_pct", "week_pct", "ts")), node=node_ok((q or {}).get("node")),
                        stale=_bool(q.get("stale"))) if q else None
    out["queue"] = {box: [{"node": node_ok(j.get("node"))} for j in (b.get("queue") or {}).get(box) or []]
                    for box in ("queue", "running", "done")}
    out["running_models"] = {n: bool(v) for n, v in (b.get("running_models") or {}).items() if n in nodes}
    out["nodes"] = {}
    for n in nodes:
        x = b["nodes"][n] or {}
        p = x.get("presence") or {}
        busy = p.get("busy") or None
        pm = (p.get("mounts") or {}).get("mounts") if isinstance(p.get("mounts"), dict) else None
        mo = x.get("models") or None
        usage = x.get("usage") or None
        out["nodes"][n] = {
            "cfg": _pick(x.get("cfg"), ("ram_gb",)),
            "label": x.get("label") if isinstance(x.get("label"), str) and LABEL.fullmatch(x["label"]) else None,
            "presence": {"level": _word(p.get("level")),
                         "busy": {"alive": bool(busy.get("alive")), "tipo": _word(busy.get("tipo")),
                                  "quien": "(trabajo)", "desde": _stamp((busy.get("desde") or "")[:16])} if busy else None,
                         "mounts": {"mounts": [{"name": "recurso-%d" % (i + 1), "ok": bool(m.get("ok"))}
                                               for i, m in enumerate(pm or [])]} if pm is not None else None},
            "os": x.get("os") if x.get("os") in ("windows", "darwin", "linux") else None,
            "alive": bool(x.get("alive")), "stale": bool(x.get("stale")), "age_s": _num(x.get("age_s")),
            "error": "error" if x.get("error") else "",
            "usage": {"today": _pick(usage.get("today"), USAGE_KEYS), "week": _pick(usage.get("week"), USAGE_KEYS),
                      "by_day": {d: _pick(v, USAGE_KEYS) for d, v in (usage.get("by_day") or {}).items()
                                 if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)}} if usage else None,
            "sessions": [{"project": al(s.get("project")), "status": _word(s.get("status")),
                          "started": _num(s.get("started")), "context_k": _num(s.get("context_k")),
                          "cost_usd": _num(s.get("cost_usd")),
                          "sent_to": {k: _num(v) for k, v in (s.get("sent_to") or {}).items() if k in nodes}}
                         for s in x.get("sessions") or []],
            "disks": [{"mount": "disco-%d" % (i + 1), **_pick(d, ("total_gb", "free_gb"))}
                      for i, d in enumerate(x.get("disks") or [])],
            "memory": _pick(x.get("memory"), ("free_pct", "total_gb")),
            "mounts": [{"share": "recurso-%d" % (i + 1), "ok": bool(m.get("ok"))} for i, m in enumerate(x.get("mounts") or [])],
            "tmux": ["(%d sesiones)" % len(x.get("tmux") or [])],
            "models": {"health": {"running": bool((mo.get("health") or {}).get("running")),
                                  "task": _word((mo.get("health") or {}).get("task")),
                                  "queued": _num((mo.get("health") or {}).get("queued"))},
                       "server_rss_mb": _num(mo.get("server_rss_mb"))} if mo else None,
            "errors": ["fuente" for _ in x.get("errors") or []],
        }
    out["projects"] = {}
    for name, p in (b.get("projects") or {}).items():
        a = al(name)
        o = _pick(p, PROJECT_KEYS)
        o["tokens"] = _pick(p.get("tokens"), ("in", "out", "cw", "cr", "cw1h"))
        o["nodes"] = {n: _pick(v, ("sessions", "cost", "active_s")) for n, v in (p.get("nodes") or {}).items() if n in nodes}
        o["timeline"] = [dict(_pick(t, TIMELINE_KEYS), node=node_ok(t.get("node")), window=hid("window", t.get("window")),
                              sent_to={k: _num(v) for k, v in (t.get("sent_to") or {}).items() if k in nodes})
                         for t in p.get("timeline") or []]
        # subagents: type and numbers only; the description is the task text (local page only)
        o["subs"] = [dict(_pick(x, SUB_KEYS), node=node_ok(x.get("node")), type=_word(x.get("type")))
                     for x in p.get("subs") or []]
        j = p.get("jobs") or {}
        o["jobs"] = dict(_pick(j, ("n", "ok", "cost")),
                         nodes={n: _num(v) for n, v in (j.get("nodes") or {}).items() if n in nodes},
                         ids=[hid("job", x) for x in j.get("ids") or [] if x])
        out["projects"][a] = o
    out["history_status"] = {n: _pick(v, ("files", "pending_files", "since", "until")) | {"complete": _bool((v or {}).get("complete"))}
                             for n, v in (b.get("history_status") or {}).items() if n in nodes}
    out["every_min"] = _num(b.get("web_every_min"))
    # model family logos only (brand glyphs built by build.model_icons: no private data)
    out["icons"] = {k: {"vb": "0 0 24 24", "name": v.get("name") if re.fullmatch(r"[\w .·-]{1,40}", str(v.get("name"))) else "", "svg": v.get("svg"),
                        "color": {"d": v["color"]["d"], "l": v["color"]["l"]}}
                    for k, v in (b.get("icons") or {}).items()
                    if re.fullmatch(r"mf-[a-z0-9]{2,12}", k) and glyph_ok(v.get("svg"))}
    out["heat"] = {n: [[_num(v) for v in row[:24]] for row in m[:7]] for n, m in (b.get("heat") or {}).items() if n in nodes}
    out["history_by_day"] = {d: {n: _pick(v, ("cost", "sessions", "active_s")) for n, v in (per or {}).items() if n in nodes}
                             for d, per in (b.get("history_by_day") or {}).items() if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)}
    a = b.get("alerts") or {}
    arow = lambda r: {"id": hid("job", r.get("id")), "status": _word(r.get("status")), "node": node_ok(r.get("node")),
                      "repo": None, "why": clean_why(r.get("why"), words), "type": _word(r.get("type")),
                      "recent": _bool(r.get("recent")), "test": _bool(r.get("test")), "log": _bool(r.get("log")),
                      "t0": _num(r.get("t0")), "start": _stamp((r.get("start") or "")[:19])}
    out["alerts"] = dict(_pick(a, ("days", "failed_7d", "incomplete_7d", "failed_all", "incomplete_all", "tests_7d", "tests_all")),
                         rows=[arow(r) for r in a.get("rows") or [] if r.get("status") in ("failed", "incomplete")],
                         test_rows=[arow(r) for r in a.get("test_rows") or [] if r.get("status") in ("failed", "incomplete")],
                         groups=[dict(_pick(g, ("n", "recent", "first", "last")), status=_word(g.get("status")),
                                      why=clean_why(g.get("why"), words),
                                      nodes=[n for n in g.get("nodes") or [] if n in nodes],
                                      ids=[hid("job", x) for x in g.get("ids") or [] if x])
                                 for g in a.get("groups") or []])
    # logs of the failed/incomplete jobs, only when the whole tail is clean (F3)
    det = b.get("job_detail") or {}
    out["job_logs"] = {}
    for r in (a.get("rows") or [])[:WEB_LOGS_MAX]:
        lg = ((det.get(r.get("id")) or {}).get("log") or {})
        t = clean_log(lg.get("log") or lg.get("run.err") or lg.get("run.out"), words)
        out["job_logs"][hid("job", r.get("id"))] = t if t else "local"
    out["models_inventory"] = {}
    for n, c in (b.get("models_inventory") or {}).items():
        if n not in nodes:
            continue
        rows = []
        for m in c.get("models") or []:
            l, j = m.get("last") or None, m.get("last_job") or None
            repo = m.get("repo") if isinstance(m.get("repo"), str) else ""
            name = repo.rsplit("/", 1)[-1]                # the public model name, never a path
            rows.append({"key": _word(m.get("key")), "task": _word(m.get("task")), "gb": _num(m.get("gb")),
                         "name": name if re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name) else None,
                         "status": m.get("status") if m.get("status") in MODEL_STATES else None,
                         "family": _word(m.get("family")),
                         "nota": clean_why(m.get("nota"), words), "status_ts": _num(m.get("status_ts")),
                         "downloaded": _bool(m.get("downloaded")), "default": _bool(m.get("default")),
                         "last": dict(_pick(l, LAST_KEYS), ok=_bool(l.get("ok")), quality_label=_word(l.get("quality_label")),
                                      speed_unit=_word(l.get("speed_unit")), date=_stamp(l.get("date"))) if l else None,
                         "last_job": dict(_pick(j, ("created", "run_s", "peak_footprint_mb")), task=_word(j.get("task")),
                                          state=_word(j.get("state"))) if j else None})
        meas = [dict(_pick(r, MEAS_KEYS), key=_word(r.get("key")), task=_word(r.get("task")), date=_stamp(r.get("date")),
                     ts_from=_word(r.get("ts_from")), ok=_bool(r.get("ok")), quality_label=_word(r.get("quality_label")),
                     speed_unit=_word(r.get("speed_unit"))) for r in c.get("measurements") or []]
        mjobs = [dict(_pick(j, MJOB_KEYS), task=_word(j.get("task")), state=_word(j.get("state")), model=_word(j.get("model")))
                 for j in c.get("jobs") or []]
        out["models_inventory"][n] = {"budget_mb": _num(c.get("budget_mb")), "models": rows,
                                      "measurements": meas, "jobs": mjobs}
    # jobs: the repo goes through the same alias as the projects, so the project filter works;
    # result text, route reason, branch, session ids and logs (job_detail) stay on the local page
    out["jobs"] = [dict(_pick(r, JOB_KEYS), id=hid("job", r.get("id") or i), node=node_ok(r.get("node")), type=_word(r.get("type")),
                        repo=al(r.get("project") or r.get("repo")) if (r.get("project") or r.get("repo")) else None,
                        status=_word(r.get("status")), test=_bool(r.get("test")),
                        why=clean_why(r.get("reason"), words) if r.get("status") in ("failed", "incomplete") else None,
                        pushed=_bool(r.get("pushed")), detached=_bool(r.get("detached")), start=_stamp((r.get("start") or "")[:19]))
                   for i, r in enumerate(b.get("jobs") or [])]
    out["compare"] = [dict(_pick(r, ("n", "ok", "success_pct", "median_total_s", "median_model_s", "median_tool_s",
                                     "separable_n", "cost_usd")), type=_word(r.get("type")), node=node_ok(r.get("node")),
                           few=_bool(r.get("few")), source=_word(r.get("source")))
                      for r in b.get("compare") or []]
    return out


# ------------------------------------------------------------------ second wall
def forbidden_words(b: dict, keep: set, extra=()) -> set:
    """Real names that must not appear: every project seen (minus the public ones), the local
    user and each node's ssh user/hostname (from `ssh -G`, which reads config, no network)."""
    words = set()
    for name in (b.get("projects") or {}):
        words.add(name)
    for n in (b.get("nodes") or {}).values():
        for s in (n or {}).get("sessions") or []:
            words.add(s.get("project"))
    words = {w for w in words if w and w not in keep and len(w) >= 3}
    try:
        words.add(getpass.getuser())
    except Exception:
        pass
    words.update(w for w in extra if w)
    return words


def no_console() -> dict:
    """Popen kwargs: no console window for children of pythonw on Windows (CREATE_NO_WINDOW,
    plus SW_HIDE for Git's msys ssh.exe). Same as presence.no_console; this file stands alone."""
    if sys.platform != "win32":
        return {}
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return {"creationflags": subprocess.CREATE_NO_WINDOW, "startupinfo": si}


def ssh_identity_words(hosts) -> set:
    out = set()
    for h in hosts:
        try:
            r = subprocess.run(["ssh", "-G", h], capture_output=True, text=True, timeout=5,
                               **no_console())
        except (OSError, subprocess.SubprocessError):
            continue
        for line in r.stdout.splitlines():
            k, _, v = line.partition(" ")
            if k in ("user", "hostname") and v and v != h:
                out.add(v)
    return out


GLYPH_PATH = re.compile(r'<path d="[MmLlHhVvCcSsQqTtAaZzEe0-9.,\s+-]{1,20000}"/>')
GLYPH_MONO = re.compile(r'<rect width="24" height="24" rx="6" opacity="\.22"/><text x="12" y="1[56](?:\.5)?" '
                        r'text-anchor="middle" font-size="(?:11|8)" font-weight="700" '
                        r'font-family="system-ui,sans-serif">[A-Za-z0-9]{1,3}</text>')


def glyph_ok(svg) -> bool:
    """A model-family logo is published only in the two shapes build.model_icons makes: one bare
    path (simple-icons) or the monogram tile. No other markup, no word longer than 3 letters."""
    return isinstance(svg, str) and bool(GLYPH_PATH.fullmatch(svg) or GLYPH_MONO.fullmatch(svg))


def leak_check(text: str, words: set) -> list:
    """Kinds of leak found in the serialized snapshot (empty = clean). Never returns the value."""
    hits = []
    if SENSITIVE.search(text):
        hits.append("palabra confidencial/reservado")
    if PATHLIKE.search(text):
        hits.append("ruta")
    if IPV4.search(text):
        hits.append("IP")
    low = text.lower()
    for w in words:
        if re.search(r"(?<![\w-])" + re.escape(w.lower()) + r"(?![\w-])", low):
            hits.append("nombre privado")
            break
    return hits


def safe_payload(b: dict, keep: set, key: bytes, extra_words=(), aliases: dict | None = None) -> bytes:
    aliases = load_aliases() if aliases is None else aliases
    pub = public_snapshot(b, keep, key, aliases)
    body = json.dumps(pub, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    # glyphs passed glyph_ok (path data or a 1-3 letter monogram, nothing else); their numbers
    # ("1.01.1.223") read as IPs, so the leak check runs on the payload without them
    vetted = dict(pub, icons={k: dict(v, svg="") for k, v in (pub.get("icons") or {}).items()})
    hits = leak_check(json.dumps(vetted, ensure_ascii=False, separators=(",", ":")),
                      forbidden_words(b, published(keep, aliases), extra_words))
    if hits:
        raise LeakError(", ".join(sorted(set(hits))))
    if len(body) > MAX_BODY:
        raise LeakError("demasiado grande: %d bytes" % len(body))
    return body


# ------------------------------------------------------------------ signed upload
def sign(key: bytes, ts: str, body: bytes, purpose: str = "ingest") -> str:
    """hex HMAC-SHA256 over "<purpose>\\n<ts>\\n<body>". The purpose prefix keeps a signature made
    for one endpoint from being replayed on another (the PHP checks the same layout)."""
    return hmac.new(key, purpose.encode() + b"\n" + ts.encode() + b"\n" + body, hashlib.sha256).hexdigest()


_CTX = []


def https_open(req, timeout: int = 20):
    """urlopen with certifi's CA bundle when it is installed. On this PC the default context (the
    Windows store) builds an expired path for the Let's Encrypt YE chain of *.ichisieben.dev and
    fails with "certificate has expired" (DECISIONES 221). Verification stays on either way."""
    if not _CTX:
        try:
            import certifi
            import ssl
            _CTX.append(ssl.create_default_context(cafile=certifi.where()))
        except ImportError:
            _CTX.append(None)
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX[0])


def post(url: str, body: bytes, key: bytes, purpose: str, timeout: int = 20, opener=None) -> tuple:
    ts = str(int(time.time() * 1000))       # epoch ms: the PHP wants 13 digits (monotonic per purpose)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Ts": ts, "X-Firma": sign(key, ts, body, purpose),
        "User-Agent": "enjambre-visor/1"})
    try:
        with (opener or https_open)(req, timeout=timeout) as r:
            return r.status, r.read(4096).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(512).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        return 0, type(e).__name__


def due(cfg: dict, now: float, stamp: Path = STAMP) -> bool:
    try:
        last = json.loads(stamp.read_text(encoding="utf-8")).get("ok_at", 0)
    except (OSError, ValueError):
        last = 0
    return now - last >= max(1, cfg.get("every_min", 10)) * 60 - 30


def maybe_upload(b: dict, nodes_cfg: dict, now: float, cfg: dict | None = None) -> str:
    """Called by build.py on every run. Returns a short word for build.log. Dormant until the
    owner sets [web] upload = true and the key env var: nothing leaves the house before that."""
    cfg = (cfg if cfg is not None else load_config()).get("web") or {}
    if not cfg.get("upload"):
        return "off"
    key = os.environ.get(INGEST_ENV, "")
    if len(key) < 32:
        return "sin clave (%s)" % INGEST_ENV
    if not due(cfg, now):
        return "no toca"
    keep = set(cfg.get("keep") or [])
    hosts = [c.get("host") for c in nodes_cfg.values() if c.get("host") not in (None, "local")]
    try:
        body = safe_payload(dict(b, web_every_min=cfg.get("every_min", 10)), keep, salt(),
                            ssh_identity_words(hosts) | set(cfg.get("forbid") or []))
    except LeakError as e:
        return "BLOQUEADA: " + str(e)
    status, text = post(cfg["url"].rstrip("/") + "/api/ingest.php", body, key.encode(), "ingest")
    if status == 200:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(json.dumps({"ok_at": now, "bytes": len(body)}), encoding="utf-8")
        return "ok %d B" % len(body)
    return "fallo HTTP %s" % status
