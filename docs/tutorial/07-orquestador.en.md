---
lesson:
  route: build-stack
  order: 8
  level: 2
lang: en
---

# 07 — The configuration kit on the Mac: repos, port and layered config

## Goal

Get the `orquestador` and `enjambre` repos into `~/Documents/Portfolio/` on the Mac, measure what
breaks **before** adapting the kit to macOS, install it into `~/.claude`, and check that it passes
its tests under both of the Mac's `bash` versions. And understand why your personal configuration
does not travel with the public one.

## Why

The Claude Code configuration kit (`orquestador`: status line, hooks, rules, skills) was written on
Windows. Before porting it, it pays to measure what breaks on the Mac as it stands: that list is the
justification for the port and, afterwards, the proof that it worked (same command, before and
after). And because the kit installs **on top of** `~/.claude`, which also holds things of yours,
you have to decide which part is public and shareable and which part is personal (section 4).

## Steps

### 1. Cloning on the Mac without credentials: `git bundle`

The normal route would be `gh repo clone`, but `gh` over SSH did not work on the Mac (see
`09-agente-desatendido.md`). Instead of copying a token from one machine to another, the
**repository** was moved, not the credential:

```powershell
# En Windows: empaquetar la rama remota (no la local: así no viaja trabajo sin pushear)
PS> git bundle create orq.bundle origin/master
PS> scp orq.bundle mac:/tmp/orq.bundle
```

```
# En la Mac (por SSH): repo nuevo + importar la rama + apuntar el remoto a GitHub
mac$ git init -b master ~/Documents/Portfolio/orquestador
mac$ cd ~/Documents/Portfolio/orquestador
mac$ git fetch --update-head-ok /tmp/orq.bundle refs/remotes/origin/master:refs/heads/master
mac$ git reset --hard master
mac$ git remote add origin https://github.com/IchiSieben/orquestador.git
```

**Concept:** a *bundle* is a git repository packed into a single file. You can clone or `fetch`
from it as if it were a remote. It is useful for passing history between machines with no network
route to GitHub or, as here, with no credentials on the destination.

### 2. Baseline: `verify.sh` on the Mac, changing nothing

```powershell
PS> ssh mac 'cd ~/Documents/Portfolio/orquestador && bash verify.sh'
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

Result (2026-10-04): **6 FALLA**, 34 **DERIVA** warnings, the rest OK.

| Failure | Why it fails on the Mac (expected) |
|---|---|
| `statusline.sh` / `settings.json` | The status line is not yet installed in the Mac's `~/.claude` |
| `xreview` calls `agy` | Antigravity is not installed on the Mac (phase 5) |
| `sessions.json` ≥ 41 sessions | The session history lives on Windows |
| `agy --version` / OAuth credential | Antigravity is neither installed nor logged in |

The 34 DERIVA lines all say the same thing in different words: the kit's files are in the repo but
not in the Mac's `~/.claude`. That is what the installation will fix **after** the port; it was
deliberately not run yet.

### 3. After the port: tests, installation, measurement

**3.1 The port, one line per change.** (Done on Windows, before testing on the Mac.) A guard that
requires bash ≥ 4.2 and re-executes itself under Homebrew's bash if it starts under the factory
one; `stat` with BSD or GNU format depending on `$OSTYPE`; `jq` also looked up in
`/opt/homebrew/bin`; `@mac`/`@win`/`@linux` on the first line of the status line. This is the diff
of the `stat` line (from `stat -c '%Y'` to a per-system format, `STAT_FMT`), with the status line
at the bottom:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

**3.2 The tests, under the factory bash and under Homebrew's.** macOS ships **bash 3.2** (from
2007; Apple does not update it because of the GPLv3 license). Homebrew installs bash 5. The kit
requires bash ≥ 4.2 and, if it starts under the old one, it **re-executes** itself under the new
one (the *guard*). That is why the tests are run with both: running with `/bin/bash` exercises the
guard.

```
mac$ /bin/bash tests/statusline_test.sh              → 49 OK, 0 fallas
mac$ /bin/bash tests/hooks_test.sh                   → 38 OK, 0 fallas
mac$ /opt/homebrew/bin/bash tests/statusline_test.sh → 49 OK, 0 fallas
mac$ /opt/homebrew/bin/bash tests/hooks_test.sh      → 38 OK, 0 fallas
```

They were run inside `tmux` (`tmux new -d -s phaseb "…; tmux wait-for -S phaseb-done"` and then
`tmux wait-for phaseb-done`): if the SSH connection drops, the job keeps going.

**3.3 Installing into `~/.claude`, twice (the second run must change nothing).** First, a backup
(`~/.claude` → `~/.claude.pre-kit-20261004`; the script also makes its own).

```
mac$ bash sync-to-home.sh   → 34 added, 0 updated, 1 merged, 0 unchanged
mac$ bash sync-to-home.sh   → 0 added, 0 updated, 0 merged, 35 unchanged
```

**Concept — idempotence:** running it twice gives the same result as running it once. That is what
lets you re-run an installer without fear.

`verify.sh` afterwards: no drift, and only the 3 checks that belong to another machine remain red
(Antigravity and the Windows sessions file):

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

**3.4 The status line on the Mac.**

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

(The branch symbol shows up as a square: `freeze` ignores the chosen font; in the real terminal it
looks fine.)

**3.5 How much does it cost to draw the status line?** Same method as on Windows: 20 calls × 3
repetitions, with a sample JSON.

| Path | Windows + Git Bash | Mac M2 Pro |
|---|---|---|
| Floor (bash startup + one `jq` call, no git) | ~225 ms | **~30 ms** |
| Full render (git, HANDOFF, dirty files) | ~476 ms | **~45 ms** |

**Why such a difference:** on Windows, creating a process costs ~50 ms and a bash `fork` ~28 ms
(Git Bash emulates `fork` on top of Windows). On macOS, `fork` is native and costs very little.
Starting from `/bin/bash` (which re-executes into bash 5) adds only ~2–4 ms.

### 4. Layered config: public and personal

The kit has a **public** version (`claude-kit`, an open repo) and it installs on top of
`~/.claude`, which also holds personal rules. The rule that came out of a real stumble (below):
**nothing personal lives only in a file that a script is going to overwrite, and nothing personal
travels in the public kit.**

| Layer | Where | Language | Travels in the public kit |
|---|---|---|---|
| General safety rules (do not touch processes you did not launch; recursive delete only inside the repo or a scratch dir, with its four commands) | the repo's `CLAUDE.md` | English | yes |
| Personal rules (the owner's "autonomous mode") | `home/.claude/rules/personal.md` | Spanish | **never** |

How the second point is guaranteed: `build-kit.sh` deletes any copy of `personal.md` from the kit
and **fails** if the text "MODO AUTÓNOMO" appears anywhere in the kit (the check was tested in both
directions on a test kit). For `personal.md` to be loaded no `@import` is needed: this was verified
with a fresh `claude -p` session, with no tools and outside any repo, which quoted point 5 of the
rule and named the file.

On the Mac, `kit-update.sh` also brings in `personal.md` (it is in the mirror, not in the kit) and
Claude Code has to be restarted afterwards. The orquestador's handoff anticipates this; it was not
verified on the Mac.

## What actually went wrong

**1. `git clone -b master orq.bundle` → "Remote branch master not found".** When `origin/master` is
packed, the reference inside the bundle is `refs/remotes/origin/master`, not `refs/heads/master`.
Fix: an explicit `fetch` of that reference.

**2. `git fetch … :refs/heads/master` → "refusing to fetch into branch checked out".** Git protects
the active branch; `--update-head-ok` tells it we know what we are doing (the repo was empty).

**3. `./sync-to-home.sh` → `permission denied`.** The scripts were committed from Windows, which
does not store the execute bit (`100644` in git). On the Mac they are not executable. Immediate
fix: `bash archivo.sh`. Real fix (in the kit's repo): `git update-index --chmod=+x`.

**4. `kit-update.sh` fails on the Mac.** It starts with `git pull`, which fails over SSH because
the credentials are in the locked keychain (see `09-agente-desatendido.md`). Since the copy was
already up to date (`d100eb4`, equal to `origin/master`), its steps were run by hand.

**5. With the system in Spanish, the status line showed `$12,00` instead of `$12.35`.** With
`LC_ALL=es_ES.UTF-8`, `printf` expects a decimal comma, rejects `12.3456` ("invalid number") and
the status line prints a false number, worse than none. Reported to the kit; the planned fix is to
set `LC_NUMERIC=C` at the top of `statusline.sh`, plus a test with a Spanish locale.

**6. Installing the kit overwrote personal rules (on Windows).** When running `kit-update.sh` on
Windows to resolve 8 files with DERIVA, the first run overwrote **three rules that lived only in
the live `~/.claude/CLAUDE.md`**: the two safety rules and autonomous mode. They were safe because
the script backs up before copying (`~/.claude.bak-20261004-052034`). The session noticed and
restored the live version from that backup, keeping the sync's version aside:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

Afterwards the owner decided on the split in section 4 (orquestador commit `1247193`). The line
cap on `CLAUDE.md` went from 70 to 72: a safety rule is not trimmed to make it fit.

## How to verify

```powershell
PS> ssh mac 'cd ~/Documents/Portfolio/orquestador && bash verify.sh'
```

It must finish with no DERIVA and with only three failures, all belonging to another machine
(`sessions.json`, `agy --version`, Antigravity's OAuth credential). And the four tests from
section 3.2 green. Finally, two runs of `sync-to-home.sh` in a row: the second must say
`0 added, 0 updated, 0 merged` and everything `unchanged`.

## Self-check

1. Why do you pack `origin/master` and not `master` with `git bundle`?
   <details><summary>Answer</summary>So that unpushed local work does not travel; the bundle
   contains only what is already on the remote.</details>
2. What does running the tests with `/bin/bash` prove, in addition to Homebrew's bash?
   <details><summary>Answer</summary>The re-execution *guard*: with the factory bash 3.2, the
   script must relaunch itself under bash ≥ 4.2.</details>
3. Why does `personal.md` not travel in the public kit, and how is that prevented by accident?
   <details><summary>Answer</summary>Because it contains personal rules; `build-kit.sh` deletes
   any copy and fails if the text "MODO AUTÓNOMO" appears in the kit.</details>
