---
lesson:
  route: build-stack
  order: 25
  level: 3
lang: en
---

# 26 — The bridge: a persistent Claude Code on the Mac that Windows talks to

## Goal

**What you get:** from Windows (from a terminal or from another Claude Code session) you
write to a Claude Code that **stays alive** on the Mac, with a browser (Playwright), and that remembers
the conversation between messages, across network drops and across service restarts. Every message
leaves its cost in the ledger and the session shows up in `enjambre status`.
**Who does it:** Claude Code (session P, 2026-10-07/08). You: log in to the
Claude Code on the Mac and grant one macOS permission (NEEDS-OWNER §22).
**Notes:** [One-shot vs persistent session](../conceptos/una-vez-vs-sesion-persistente.md) ·
[Backplane](../conceptos/backplane.md) · evaluation and design in [docs/PUENTE.md](../PUENTE.md).

## Why it was needed

`enjambre run` launches `claude -p`: one shot. It is born, does the task and dies. To keep going on the
same topic you have to explain everything again in another TASK. What the owner asked for is something else: that
a Windows session can **converse** with one on the Mac. "Open the demo site, take a screenshot",
and then "now run the tests", and the second sentence knows what the first was about.

## Steps

The steps are the following parts: Part 1 (Playwright and screen), Part 3 (the bridge: pieces, key, usage and tests) and §4 (Remote Control). Part 2 is only the prior evaluation.

## Part 1 — Eyes and hands on the Mac

### 1.1 Playwright MCP in the Mac's Claude Code

**Why this way:** the official Playwright MCP gives Claude a real browser (navigate, click,
capture). It is registered at **user** level, so any session on the Mac has it,
the bridge included. The version is pinned so that an update does not change the tools without
notice.

```sh
# desde Windows, todo por tmux-run.sh (nace en la sesión gráfica: tiene pantalla y llavero)
ssh mac 'npx -y @playwright/mcp@latest --version'                      # Version 0.0.83
ssh mac 'bash ~/.enjambre/tmux-run.sh -- /bin/bash -lc "claude mcp add playwright -s user -- \
  /opt/homebrew/bin/npx -y @playwright/mcp@<versión fijada, 0.0.83> --browser chromium --isolated \
  --output-dir \$HOME/renders/playwright --viewport-size 1280x800; claude mcp list"'
```

Expected output: `playwright: … - ✔ Connected`.

- `--isolated`: the browser profile lives in memory. Cookies and sessions do not survive between
  uses, so an agent does not inherit your logins.
- `--output-dir`: screenshots land in `~/renders/playwright`, from where `scp` fetches them.
- Brave was already in `/Applications` (`brew list --cask` had nothing to install). It stays as the
  "human" browser; the agents use Playwright's Chromium.

**Real failure no. 1 — browser version.** Installing with `npx playwright@latest install chromium`
downloaded build 1248, but MCP 0.0.83 ships its own Playwright, which asks for 1247:

```
Error: Browser "chrome-for-testing" is not installed; expected executable at
~/Library/Caches/ms-playwright/chromium-1247/… Run `npx @playwright/mcp install-browser chrome-for-testing`
```

Fix: install with the **MCP's own** installer, at the same version that ended up
registered: `npx -y @playwright/mcp@<versión fijada, 0.0.83> install-browser chrome-for-testing`.

### 1.2 Testing it without a model

The Mac's Claude Code had an expired login (see §4), so the test with `claude -p`
returned `Failed to authenticate`. To avoid depending on that, `tools/mcp_probe.py` was written; it
speaks MCP directly over stdio (initialize → tools/list → tools/call) from the Mac's tmux:

```sh
ssh mac 'bash ~/.enjambre/tmux-run.sh -C ~/renders/playwright -- /usr/bin/python3 \
  ~/.enjambre/mcp_probe.py --calls p16calls.json -- /opt/homebrew/bin/npx -y @playwright/mcp@<versión fijada, 0.0.83> …'
scp mac:renders/playwright/p16-example.png state/renders/
```

```
server: {'name': 'Playwright', 'version': '1.64.0-alpha-…'}
tools: 25 browser_close browser_resize … browser_navigate … browser_take_screenshot …
call browser_navigate: error=False
call browser_take_screenshot: error=False   -> ./p16-example.png
call browser_close: error=False
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

**Real failure no. 2 — the probe hung.** The first version ignored server messages
that carried both `id` *and* `method`. Those were **requests** from the server to the client (`roots/list`), and the
server waited for the reply before continuing. The lesson is that in MCP both sides can
ask: a client has to answer what it is asked, even if only with an empty list.

### 1.3 The real screen: `screencapture`

```sh
ssh mac 'bash ~/.enjambre/tmux-run.sh -- screencapture -x ~/renders/p16-pantalla.png'
# could not create image from display     (rc 1, también desde ssh directo)
```

Both screens were on (`system_profiler`: 1920×1080 and 3440×1440, `Online: Yes`), and
waking them with `caffeinate -u` changed nothing. The cause is **TCC**: macOS requires the
*Screen Recording* permission for the responsible process, and here that process is
`/opt/homebrew/bin/tmux`. That permission cannot be granted over ssh, so you gave it
(NEEDS-OWNER §22.2). Playwright does not need it, because it captures its own page and not the screen.

**After the permission (2026-10-08, 03:45 Mac time):**

```
$ ssh mac 'bash ~/.enjambre/tmux-run.sh -- screencapture -x -t png ~/renders/p16-pantalla.png'
rc=0      ~/renders/p16-pantalla.png  1 422 678 B, 1720×1440
$ scp mac:renders/p16-pantalla.png state/renders/     # media de gris 40, desviación 37, 11 674 colores: no está en negro
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

*Real capture taken from Windows by `tmux-run.sh`. Everything is pixelated (32 px blocks)
except the Settings panel that shows tmux's permission. The original, with your desktop, stays
in `state/renders/` (ignored by git).*

Two facts that were not known:

- **There was no need to restart tmux.** The `tmux -L enjambre` server had been alive since October
  5 (same pid) and the permission took effect as soon as the switch was turned on.
- **Only one screen comes out.** `screencapture -x <file>` captures the main one. For both
  you need two files (`screencapture -x a.png b.png`).

### 1.4 What needs a graphical session, and what happens after a reboot

| Thing | Needs a logged-in graphical session? | Why |
|---|---|---|
| `claude` (jobs, bridge) | **Yes** | The login keychain is unlocked at login, and only processes in that session see it (tutorial 11) |
| Playwright with a visible browser | **Yes** | Needs WindowServer. In headless mode it would run without a session, but without the keychain `claude` does not run anyway |
| `screencapture` | **Yes** + TCC permission | Captures WindowServer |
| The bridge (`dev.enjambre.puente`) | **Yes** | It is a `gui/<uid>` LaunchAgent: it starts at login, not before |
| ssh, `enjambre script` (no agent) | No | sshd is enough |

**After a reboot with FileVault none of the above runs.** First the disk has to be unlocked
(over ssh with your password: table of the real reboot in tutorial 11, "How to verify"), and even then there is no
session: no keychain, no tmux, no bridge, until you **log in on the Mac's screen**. The
most convenient is `sudo fdesetup authrestart` before a planned reboot (tutorial 01).

`enjambre status` shows it live with a single ssh call: `stat -f%Su /dev/console` returns
your user if there is a graphical session and `root` if the Mac is at the login window.

```
  mac    …
         gui sí
         puente: 1 sesión(es), US$0.0000
           farmacias        en espera · 3 turnos · US$0.0000 · sesión ce661f8e · …
```

## Part 2 — Evaluate before building

It is in [docs/PUENTE.md §2](../PUENTE.md). In short: **Agentrooms** needs a *backplane*
(a message server in the middle), by default in a third party's cloud. **thurbox** is very
good, but it wraps the interactive Claude Code: cost and denials do not come out as data,
and it installs with `curl | sh`. **Official messaging / Remote Control** is for a person, not
for a script. A bridge of our own was built on the **Agent SDK**, which already brings sessions,
`resume`, MCP, allowlist and cost.

## Part 3 — The bridge

### 3.1 The pieces

| Piece | Where | What it does |
|---|---|---|
| `agent/puente.py` | Mac, Python 3.12 venv (`~/.enjambre/puente/venv`, `claude-agent-sdk==0.2.164`) | HTTP server on `127.0.0.1:8766`. One-turn-at-a-time queue per repo. Runs the turn with the SDK **outside** the request and writes the events to `turns/<id>.ndjson`. Stores `session_id` and cost in `sessions/<repo>.json` |
| `agent/install.sh` | Mac | Copies the server, creates the venv, creates the key (once, `chmod 600`) and loads the `dev.enjambre.puente` LaunchAgent in `gui/<uid>` |
| `puente_cli.py` | Windows | Signs, `ssh mac curl`, live reading, pending → ledger |
| `enjambre chat / send / sessions` | Windows | The commands |

**Why Python 3.12 and not `/usr/bin/python3`:** the SDK needs 3.10 or newer, and macOS's is 3.9.
`uv` already had 3.12 installed.

**Why a LaunchAgent and not tmux:** it has to be born in the graphical session, like the model
manager (the same pattern as `dev.enjambre.tmux`). launchd also restarts it if it dies
(`KeepAlive`).

### 3.2 The key

```sh
ssh mac 'bash ~/.enjambre/puente-src/install.sh sdk'      # key: created ~/.config/enjambre/puente.key (600)
# a Windows, sin imprimirla: ssh mac cat … | python (winreg) -> HKCU\Environment\ENJAMBRE_PUENTE_KEY
```

The key does not pass through chat or through any file in the repo. `puente_cli.key()` reads it from the environment
or, if the terminal was opened before it was saved, straight from the registry.

### 3.3 Using it

```sh
python enjambre.py chat mac --repo farmacias "Abre la web del demo y saca captura"   # síncrono, imprime el stream
python enjambre.py send mac --repo farmacias "Corre los tests"                       # asíncrono, vuelve por el ledger
python enjambre.py chat mac --repo farmacias --attach p20261007-231120-9122f0        # retomar un turno
python enjambre.py chat mac --repo farmacias                                          # ¿en qué quedó? (último turno)
python enjambre.py sessions mac                                                       # sesiones y coste
# por defecto la sesión lee, corre el tests-cmd y navega; --edit le deja además editar la copia de la Mac
```

A Claude Code session on Windows uses them like any command. Since its Bash tool
cuts off at 2 minutes, for a long turn it should use `send`, or `chat` in the background.

### 3.4 Wiring test (`stub` engine, no model)

With the login down, the server was first deployed with `PUENTE_ENGINE=stub`: an engine that
answers "echo", takes 20 s in steps of 1 s and leaves the same events and the same result as
the SDK. That way everything was tested except the model. The `chat` was launched and after 7 s its process
tree was killed by PID, which is the same as closing the window:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

*Render of the real output with `freeze`, cropped.*

What was checked:

- The turn kept going on the Mac. `--attach` resumed it and, when it finished, wrote **one** line in the
  ledger (`worker: puente`, `status: ok`, `duration_s: 20.1`).
- The second and third turns resumed the same session (`sesión ce661f8e`, 3 turns).
- `status` shows `gui sí` and the live session with its cost.
- 13 tests (`tests/test_puente.py`): same signature on both sides, no signature → 401, old signature →
  401, `Origin` → 403, replay → 401, allowlist (`Bash(rm -rf /)`, wildcards, another MCP, the whole
  Playwright server, `browser_run_code_unsafe`, `evaluate`, `file_upload`) → 403,
  `../etc` → 400, resume and accumulated cost, events from an offset, recovery after a
  restart, someone else's `busy.json` left intact, charge to the ledger only once.

**Real failure no. 3 — legitimate replay.** The tests found that two identical `GET /sessions` in
the same second produced the same signature, and the second was rejected as a replay. It was fixed with a random
**nonce** inside what is signed (`X-Puente-Nonce`).

**Real failure no. 4 — 50 s to start.** After `launchctl bootstrap`, the server took about
50 s to listen, and the first `chat` got `curl: (28) Failed to connect`. `sample <pid>` showed
nothing useful. The cause was `HTTPServer.server_bind`, which calls `socket.getfqdn()`: a
reverse DNS lookup that was slow on the Mac. It was replaced with a `server_bind` that does not look up a name,
and now it starts in 1 s.

**Real failure no. 5 — farmacias behind on the Mac.** The first real turn gave
`http 403: repo has no AGENTS.md`. The Mac's copy was at `1a721ff`, before the AGENTS.md
commit. Fixed with `git pull --ff-only` through `tmux-run.sh` (the copy was clean). The
bridge requires it to exist: without AGENTS.md there is no allowlist.

### 3.5 Real engine (SDK) with the expired login

The SDK's `init` message arrives **before** the authentication failure, so it was possible to check
without a login what the session loads: `54 herramientas, MCP playwright:connected, permisos default`.
The MCP registered at user level reaches the SDK with `setting_sources=["user","project"]`.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

The failure arrives as a `failed` turn with its cause in the ledger and cost 0. A turn that fails
with no cost does **not** set the `session_id`, because there is no conversation to resume. If it did,
all the following turns would fail trying to resume it.

### 3.6 The acid test, with a model (2026-10-08)

You logged in on the Mac at 03:46 (`loggedIn: true`, `claude.ai`). The test was done exactly as
the prompt asks: **a Claude Code window on Windows, in `farmacias`**, asks the persistent session on the Mac
for the work. That window was a `claude -p` with Haiku, which could only run
`python ../enjambre/enjambre.py chat:*`. Halfway through the turn it was closed by killing its process tree
by PID: 34 processes (`claude`, `bash`, `python`, `ssh`, its node MCPs…). Then **another**
window was opened in the same repo.

| Step | What happened | Evidence |
|---|---|---|
| Window 1 asks for: Radar + ichisieben.dev with Playwright, full-page screenshots, `tests-cmd`, summary | Turn `p20261008-035208-f30ca3`, session `912f16e4` resumed | `turno … (sesión 912f16e4, posición 1)` |
| Window 1 closed 13 s after sending | On the Mac the turn was still `running` (4 tools used); on Windows `state/puente/<turno>.json` was left pending | `running start 08:52:08 end None` |
| The turn finishes by itself on the Mac | `ok`, 28.9 s, 10 turns, US$0.415, tests **85/85** | ledger, `origin_session` = window 1 |
| `enjambre collect` | One line to the ledger for the dead window's turn | `{"puente": {"turn": "…f30ca3", "state": "ok"}}` |
| Window 2 (new) asks "my window closed, how did it end?" | **Same session** `912f16e4` ("resumes the session"). The Mac remembered the request and answered: Radar with 296 products from 4 chains, 13 projects in ichisieben.dev, 85/85 OK | `sessions`: 5 turns, US$1.3479 |

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

*Screenshot taken by Playwright on the Mac during the test and brought over with `scp`. It is the public
demo, with no private data.*

**Real failure no. 6 — "Checking your browser".** The first attempt captured an anti-bot
verification screen from the hosting ("Please wait for up to 5 seconds") and the agent took it for a
block. On the second attempt it waited and got through. It is not a block, it is a challenge you have to wait out:
if a request asks to capture the Radar, it helps to say "wait for the table to load"
(`browser_wait_for`).

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

**Real failure no. 7 — screenshots landed in the repo root.** Playwright resolves a relative `filename`
against the workspace root (the repo), not against its `--output-dir`. So 4 PNGs ended up
loose in the Mac's farmacias copy, exactly the copy that must not get dirty. Asking for it in the
system prompt ("save in `.puente/`") was **not enough**: the model ignored it. The fix is mechanical:
before the turn, the server notes the untracked files (`git ls-files --others
--exclude-standard`); when it finishes, it moves the new ones with a screenshot extension to
`.puente/<turno>/`, which is in `.git/info/exclude`. It was verified with a short turn:

```
  · mcp__playwright__browser_take_screenshot {"filename": "example-com.png"}
La captura … quedó en …/farmacias/example-com.png, en la raíz del repo.
  (capturas movidas a: .puente/p20261008-035451-66c6be/example-com.png)
-- ok · US$0.5295
```

The model believes it left it in the root, but the `moved` event says where it really is, and
`git status` stays clean. The 4 loose screenshots were **moved** (not deleted) to
`.puente/fuego-p16/`.

**Real failure no. 8 — broken accents in the Windows window** (`sesi�n`). When `enjambre`
writes to a pipe (an agent's Bash tool), Python used cp1252. Now, if the
output is not a console, UTF-8 is forced.

**The cost of persisting, measured.** The last turn (a screenshot of example.com) cost US$0.53.
The trivial part was not the task: the price was paid for resuming a conversation that already carried four turns with a model
of context. See [One-shot vs persistent session](../conceptos/una-vez-vs-sesion-persistente.md):
`enjambre sessions` shows the running total (US$1.88 after the test) precisely so you know when to
start clean.

**One more fact:** since the login is a claude.ai one, the SDK session also loads your claude.ai
connectors (Gmail, Drive, Notion…: `pending`/`needs-auth` in the `init`). They are not in the allowlist,
so any attempt to use them is denied.

## 4. Remote Control (to follow the session from your phone)

Pending: it was not tried again after the 2026-10-08 login; the text below describes the earlier state.

On the Mac's CLI (2.1.289) it is turned on with `--remote-control [name]`, when opening an
**interactive** session:

```sh
# en Terminal.app de la Mac, dentro del repo
claude --remote-control farmacias-mac
```

The session shows up in claude.ai/code and in the phone app. Today it answers
`You must be logged in to use Remote Control` (§22.1). It does not replace the bridge: it goes through Anthropic's
cloud, it is for a person and it leaves no ledger. Also, the Remote Control session and the bridge's
are **different**: the bridge runs SDK sessions, not the interactive window.

## 5. Threat model: what can whoever has the key do?

| Who | What they can do | What they cannot do |
|---|---|---|
| Someone with the **key** and access to the Mac (or ssh) | Send turns to any repo in `~/Documents/Portfolio` that has an AGENTS.md, with **your** Claude account (spends up to `--budget` per turn, US$2 by default; the Mac's default model is Opus). Read the text of turns and results. With `--edit`, edit files in the repo's main copy on the Mac | Use tools outside the allowlist (Windows computes it and the Mac checks it again against its AGENTS.md: it cannot ask for `Bash(*)`, nor the whole Playwright server, nor `browser_run_code_unsafe`, `evaluate`, `file_upload` or `drop`). Leave Portfolio (`../` → 400). Push (there is no `git push` in the list) |
| A **web page** the agent visits (instruction injection) | Ask the agent to use its allowed tools: read repo files and show them in its reply, or type them into a form on that page (`browser_type`). This is the risk that remains | Run code on the Mac through Playwright (the code and file-upload tools are not in the list), edit without `--edit`, push |
| Any process on the Mac **without** the key | See that something is on port 8766 | Nothing else: no signature → 401 |
| A web page open in a browser on the Mac | — | Nothing: any request with `Origin` → 403, before looking at the signature. There is no CORS |
| Someone on the LAN | — | Nothing: it listens only on `127.0.0.1` |
| Whoever captures a signed request | — | Replay it: the nonce is accepted only once and only within ±120 s |

Where the key lives: `~/.config/enjambre/puente.key` (600) on the Mac and
`HKCU\Environment\ENJAMBRE_PUENTE_KEY` on Windows. Any process **of your user** on
either machine can read it. The real security boundary is your user account,
just like with the ssh key. To change it: delete the file, run `install.sh` again and
copy it to the registry again (§3.2).

## What actually went wrong

Eight real failures, each explained where it happened: no. 1 Playwright browser version (§1.1);
no. 2 the MCP probe hung for not answering server requests (§1.2); no. 3 legitimate replay, solved
with a nonce (§3.4); no. 4 50 s to start because of `getfqdn()` (§3.4); no. 5 farmacias behind on the Mac
(§3.4); no. 6 anti-bot challenge «Checking your browser» (§3.6); no. 7 screenshots in the repo root (§3.6);
no. 8 broken accents in the Windows window (§3.6). Also, the Mac's expired login blocked the model until 2026-10-08 (§3.5).

## How to verify

Expected outputs, all already obtained and copied above:

- `claude mcp list` on the Mac: `playwright: … - ✔ Connected` (§1.1).
- MCP probe: `call browser_navigate: error=False`, `call browser_take_screenshot: error=False`, `call browser_close: error=False` (§1.2).
- `screencapture` through `tmux-run.sh`: `rc=0` with a 1720×1440 PNG (§1.3).
- `python enjambre.py sessions mac`: the persistent session with its turns and its cost (in the test, `912f16e4`, 5 turns).
- `tests/test_puente.py`: 13 tests (§3.4).

## Self-check

1. Why is the bridge a LaunchAgent and not a process in tmux?
   Answer: it has to be born in the graphical session, like the model manager, and launchd restarts it if it dies (`KeepAlive`).
2. What happens if you close the Windows window halfway through a turn?
   Answer: the turn keeps going on the Mac; `enjambre collect` or `chat --attach` pick it up and write a single line in the ledger, and another window continues the same session.
3. What can whoever has the key do, and what can they not do?
   Answer: send turns to Portfolio repos with AGENTS.md using your account; they cannot use tools outside the allowlist, leave Portfolio or push.

## 6. Pending

- Nothing from prompt 16. Login, real capture and acid test are done (§1.3, §3.6).
- `watch` (the current one predates F2) does not pick up `send` turns on its own: until you
  restart it, use `enjambre collect`. Update (NEEDS-OWNER §22.5): `watch` was restarted on 2026-10-10, but
  the automatic pickup of `send` turns has still not been checked with a real turn.
