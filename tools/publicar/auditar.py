#!/usr/bin/env python3
"""auditar.py — hard publication audit. Exit 1 on ANY finding, 0 only when the tree is clean.

    python tools/publicar/auditar.py [DIR]          # audit DIR (default: the repo root)
    python tools/publicar/auditar.py --hash WORD    # the line to add to prohibidas.txt
    python tools/publicar/auditar.py --testigo      # the key's witness line (once per key)

What it rejects, in every text file (and in file names, and in the git history if DIR has one):
  ip      IPv4 outside loopback / 0.0.0.0 / the documentation ranges (RFC 5737); IPv6 other than
          ::1 and the documentation prefix 2001:db8::/32
  mac     hardware addresses (aa:bb:.. and AA-BB-..), except the obviously fake ones tests use
  serial  a serial number or hardware UUID after its label (Serial Number, Hardware UUID, ...)
  host    Windows machine names (DESKTOP-xxx, LAPTOP-xxx), *.local hostnames
  home    /Users/<name>, /home/<name>, C:\\Users\\<name> with a real name (placeholders pass)
  email   any address outside example.*, users.noreply.github.com, noreply@anthropic.com
  secret  API keys and tokens by shape (AWS, GitHub, Anthropic, OpenAI, Slack, Google, PEM)
  word    a forbidden word or two-word name: owner, third parties, private projects. They are
          kept as HMAC-SHA256 in prohibidas.txt under a key that is NOT in the repo: env
          AUDITORIA_CLAVE (CI: an Actions secret) or the file ~/.config/enjambre/auditoria-clave.
          No key -> exit 2: the audit never passes with a rule silently off.
  binary  any non-text file whose sha256 is not in binarios.txt (images are reviewed by a human)

A finding prints the file, the line and the rule, never the matched value (CI logs are public).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORDS_FILE = HERE / "prohibidas.txt"
BINARIES_FILE = HERE / "binarios.txt"
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv"}
TEXT_EXT = {".py", ".sh", ".ps1", ".md", ".toml", ".json", ".jsonl", ".txt", ".html", ".js", ".css",
            ".yml", ".yaml", ".plist", ".svg", ".cff", ".cfg", ".ini", ".conf", ".php", ".csv", ".tsv",
            ".gitignore", ".gitattributes", ""}

IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?!\w|\.\d)")
IPV6 = re.compile(r"(?<![\w:.])((?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{1,4}(?:%\w+)?)(?![\w:])", re.I)
MAC = re.compile(r"\b([0-9a-f]{2}(?::[0-9a-f]{2}){5}|[0-9a-f]{2}(?:-[0-9a-f]{2}){5})\b", re.I)
SERIAL = re.compile(r"(?:serial(?:[ _-]?(?:number|no))?|hardware[ _-]?uuid|platform[ _-]?uuid|n[uú]mero de serie)"
                    r"(?:\s*\([^)]*\))?\s*[:=]\s*[\"']?[A-Z0-9][A-Z0-9-]{7,}", re.I)
HOST = re.compile(r"\b(?:DESKTOP|LAPTOP)-[A-Z0-9]{4,}\b|\b[\w-]+\.local\b(?![.\w-])", re.I)
HOME = re.compile(r"(?:(?<![\w.])/Users/|(?<![\w.])/home/|[A-Za-z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/))"
                  r"([\w.$%{][^/\\\s\"'`<>)\]|:]*|<[^>]*>)")
EMAIL = re.compile(r"(?<![\w.+-])\w[\w.+-]*@([\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,})\b")
SECRETS = re.compile(
    r"AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|sk-ant-[A-Za-z0-9_-]{20,}"
    r"|sk-[A-Za-z0-9]{40,}|xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----")
# SVG path data ("M8.489 1.921.96.96 0 0 1...") is full of dotted number runs: not addresses
SVG_D = re.compile(r"""\bd\\?["']?\s*[=:]\s*\\?["'][^"']{20,}""")
TOKEN = re.compile(r"[^\W_]+(?:[-_][^\W_]+)*", re.UNICODE)

HOME_OK = {"<usuario>", "<u>", "<user>", "<name>", "<nombre>", "<tu-usuario>", "someuser", "someone", "secretuser", "yo", "alice", "bob", "shared", "public",
           "runner", "example", "$user", "${user}", "%username%", "...", "x", "u", "me", "default"}
EMAIL_OK = ("example.com", "example.org", "example.net", "users.noreply.github.com")
EMAIL_EXACT = {"noreply@anthropic.com"}
MAC_OK = re.compile(r"^(aa[:-]bb[:-]cc[:-]dd[:-]ee[:-][0-9a-f]{2}|00([:-]00){5}|ff([:-]ff){5}"
                    r"|02[:-]00[:-]00[:-]00[:-]00[:-][0-9a-f]{2})$", re.I)
KEY_ENV = "AUDITORIA_CLAVE"
KEY_FILE = Path.home() / ".config" / "enjambre" / "auditoria-clave"


def ip_ok(s: str) -> bool:
    try:
        ip = ipaddress.ip_address(s)
    except ValueError:
        return True                       # 999.1.2.3 is a version string or a counter, not an IP
    if ip.is_loopback or ip.is_unspecified or s.startswith("255."):    # broadcast and netmasks
        return True
    return any(ip in ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"))


def ip6_ok(s: str) -> bool:
    if sum(c in "0123456789abcdefABCDEF" for c in s.split("%")[0]) < 8:
        return True                       # clocks, slices, C++ names: too short to carry an address
    try:
        ip = ipaddress.IPv6Address(s.split("%")[0])
    except ValueError:
        return True
    return ip.is_loopback or ip.is_unspecified or ip in ipaddress.ip_network("2001:db8::/32")


def audit_key() -> bytes:
    k = os.environ.get(KEY_ENV, "").strip()
    if not k and KEY_FILE.exists():
        k = KEY_FILE.read_text(encoding="utf-8").strip()
    return k.encode()


# LEARN: a keyed hash (HMAC) list lets a PUBLIC check reject words without publishing them. With a
# public salt a 3-letter word falls to brute force in milliseconds; with a secret key nobody can
# test guesses offline. Alternative: keep the list only in a private CI repo (loses forkability).
def load_words(path: Path = WORDS_FILE) -> tuple[bytes, set]:
    hashes = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                hashes.add(line.split()[0])
    return audit_key(), hashes


# The key's witness: prohibidas.txt carries HMAC(key, CANARY). A WRONG key (a mistyped CI secret)
# matches no word and would give a false green; without the witness's line there is no green. The
# NUL bytes keep it out of reach of TOKEN, so no file can ever hit it as a word.
CANARY = "\x00enjambre-testigo\x00"


def word_hash(salt: bytes, word: str) -> str:
    return hmac.new(salt, word.lower().encode("utf-8"), hashlib.sha256).hexdigest()


def words_hit(text: str, salt: bytes, hashes: set) -> bool:
    toks = [t.lower() for t in TOKEN.findall(text)]
    cands = set(toks) | {a + " " + b for a, b in zip(toks, toks[1:])}
    for t in list(cands):                 # "lab-video" also checks "lab" and "video"
        if "-" in t or "_" in t:
            cands.update(re.split(r"[-_]", t))
    return any(word_hash(salt, c) in hashes for c in cands)


def line_findings(line: str, salt: bytes, hashes: set) -> list:
    out = []
    nosvg = SVG_D.sub(" ", line)
    if any(not ip_ok(m.group(1)) for m in IPV4.finditer(nosvg)) or any(not ip6_ok(m.group(1)) for m in IPV6.finditer(nosvg)):
        out.append("ip")
    if SERIAL.search(line):
        out.append("serial")
    if any(not MAC_OK.match(m.group(1)) for m in MAC.finditer(line)):
        out.append("mac")
    if HOST.search(line):
        out.append("host")
    if any(m.group(1).lower() not in HOME_OK and not m.group(1).startswith(("$", "{", "%"))
           for m in HOME.finditer(line)):
        out.append("home")
    for m in EMAIL.finditer(line):
        dom = m.group(1).lower()
        if m.group(0).lower() not in EMAIL_EXACT and not dom.endswith(EMAIL_OK) and not re.fullmatch(r"[\d.]+", dom):
            out.append("email")
            break
    if SECRETS.search(line):
        out.append("secret")
    if hashes and words_hit(line, salt, hashes):
        out.append("word")
    return out


def is_text(p: Path, data: bytes) -> bool:
    return (p.suffix.lower() in TEXT_EXT or p.name.startswith(".")) and b"\0" not in data[:4096]


def reviewed_binaries(path: Path = BINARIES_FILE) -> set:
    if not path.exists():
        return set()
    return {l.split()[0] for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}


def audit_tree(root: Path) -> list:
    salt, hashes = load_words()
    ok_bin = reviewed_binaries()
    found = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            if p.resolve() in (WORDS_FILE.resolve(), BINARIES_FILE.resolve()):
                continue
            for rule in line_findings(rel, salt, hashes):
                found.append((rel, 0, "name:" + rule))
            data = p.read_bytes()
            if not is_text(p, data):
                if hashlib.sha256(data).hexdigest() not in ok_bin:
                    found.append((rel, 0, "binary"))
                continue
            for i, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
                for rule in line_findings(line, salt, hashes):
                    found.append((rel, i, rule))
    return found


def no_console() -> dict:
    """Windows: no console window flashes for git (same idiom as presence.py; standalone for CI)."""
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def audit_history(root: Path) -> list:
    """Authors, committers and every patch of every commit, when DIR is a git repo."""
    if not (root / ".git").exists():
        return []
    salt, hashes = load_words()
    found = []
    who = subprocess.run(["git", "-C", str(root), "log", "--all", "--format=%an <%ae>%n%cn <%ce>"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", **no_console()).stdout
    for line in set(who.splitlines()):
        for rule in line_findings(line, salt, hashes):
            found.append(("git:author", 0, rule))
    log = subprocess.run(["git", "-C", str(root), "log", "--all", "-p", "--format=commit %H%n%B"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", **no_console()).stdout
    for i, line in enumerate(log.splitlines(), 1):
        if line.startswith(("+", "-", " ")) or not line.startswith(("diff ", "index ", "@@")):
            body = line[1:] if line[:1] in "+- " else line     # the diff marker is not content
            for rule in line_findings(body, salt, hashes):
                found.append(("git:log", i, rule))
    return found


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    key, hashes = load_words(WORDS_FILE)
    if not key:
        print(f"auditoría: SIN CLAVE ({KEY_ENV} o ~/.config/enjambre/{KEY_FILE.name}): la regla de palabras no corre; no hay verde")
        return 2
    if argv[:1] == ["--hash"]:
        for w in argv[1:]:
            print(word_hash(key, w))
        return 0
    if not hashes:
        print("auditoría: prohibidas.txt vacío o ausente: no hay verde")
        return 2
    if argv[:1] == ["--testigo"]:
        print(word_hash(key, CANARY) + "  testigo de la clave")
        return 0
    if word_hash(key, CANARY) not in hashes:
        print(f"auditoría: CLAVE DISTINTA: {KEY_ENV} no es la clave de prohibidas.txt (falta su testigo); no hay verde")
        return 2
    root = Path(argv[0] if argv else HERE.parent.parent).resolve()
    found = audit_tree(root) + audit_history(root)
    for rel, line, rule in found[:400]:
        print(f"{rel}:{line}: {rule}")
    if len(found) > 400:
        print(f"... {len(found) - 400} more")
    summary = {}
    for _, _, rule in found:
        summary[rule] = summary.get(rule, 0) + 1
    print(f"auditoría: {'VERDE (0 hallazgos)' if not found else 'ROJA ' + str(summary)} en {root.name}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
