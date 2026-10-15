---
lesson:
  route: build-stack
  order: 12
  level: 2
lang: en
---

# 11 — The keychain over SSH: a tmux that is born in your session

## Goal

From Windows, `ssh mac '… claude -p "di hola"'` answers, `gh auth status` says *Logged in* and
`git pull` works, **without storing any token in a file**.

## Why

This is the problem that stalled the whole previous night (page 09): over SSH, `claude` said *Not
logged in* and `gh` called the token invalid. The `gh auth login` had been done correctly. The problem
was **where** the credential was stored.

macOS keeps credentials in the **login keychain**, and that keychain is opened for one
specific **security session**: the one that is born when you log in at the screen. Every
process inherits the session of its parent:

- The Mac's Terminal → child of your graphical session → keychain open.
- A command that comes in through `ssh` → child of `sshd` → another session → *"User interaction is not
  allowed"*.

There are two ways out. One is to take the token out of the keychain and leave it in a file, which is more
fragile and less secure. The other is to **run the commands inside a process that already lives in
your graphical session**. That second one is what we use here.

Who starts something inside your graphical session without you opening a window? **launchd**, the
macOS startup system. A *LaunchAgent* in `~/Library/LaunchAgents/` is loaded into the
`gui/<uid>` domain, the one for your session. If what it starts is a `tmux` server, every new
window of that server inherits the session and can open the keychain, even when a command that
arrived over SSH is the one asking.

## Steps

### 1. The LaunchAgent (on the Mac, no sudo)

Template in the repo: `config/dev.enjambre.tmux.plist`. launchd **does not expand** `~` or `$HOME`,
so the paths have to be absolute. The template uses `__HOME__` and the install replaces it:

```bash
mac$ mkdir -p ~/Library/LaunchAgents ~/enjambre-logs
mac$ sed "s|__HOME__|$HOME|g" config/dev.enjambre.tmux.plist > ~/Library/LaunchAgents/dev.enjambre.tmux.plist
mac$ plutil -lint ~/Library/LaunchAgents/dev.enjambre.tmux.plist
mac$ launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.enjambre.tmux.plist
mac$ tmux -L enjambre ls
```

Expected output:

```
…/dev.enjambre.tmux.plist: OK
enjambre: 1 windows (created Sun Oct  4 08:10:27 2026)
```

The plist keys that matter, and why:

| Key | Why |
|---|---|
| `/opt/homebrew/bin/tmux` with the full path | launchd does not read your `PATH` |
| `-L enjambre` | Uses its **own socket**. If a tmux server born over SSH already exists on the default socket, without `-L` you attach to that one and end up with no keychain, with no error to warn you |
| `RunAtLoad` = true | Starts when the agent is loaded and at every login |
| `AbandonProcessGroup` = true | `tmux new-session -d` leaves the server in the background and exits. Without this key, launchd kills the server when the job "finishes" |
| `EnvironmentVariables` (`PATH`, `HOME`, `LANG`) | The windows inherit the server's environment, not your SSH one |

> The `launchctl bootstrap` worked **launched over SSH** because there was an open graphical session
> of the same user (`stat -f %Su /dev/console` = you). If there is none, the agent starts the
> next time you log in at the screen.

### 2. The test that decides everything

Before changing code, check that the keychain opens **inside** the tmux and not outside:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

*Render of the real output with `freeze`; the account is masked as `<cuenta>`.*

### 3. `tmux-run.sh`: run any command in there

`tmux-run.sh` (at the repo root, copied to the Mac's `~/.enjambre/`) does five things:

1. Checks that the `enjambre` session exists on `-L enjambre`. **If it does not exist, it fails** (code 75)
   instead of creating it, because a session created over SSH would have no keychain.
2. Saves standard input to a file: a tmux window does not receive the stdin of your SSH.
3. Writes a script with `cd`, the `ENJAMBRE_*` variables (the SSH environment does not travel) and the
   command, quoted with `printf %q`.
4. Opens a window with `tmux -L enjambre new-window -d -t enjambre` and **waits for an `rc` file
   to appear**. It uses polling and not `tmux wait-for`, because a file cannot miss a
   signal and the timeout is easy to set.
5. Returns standard output, standard error and **the same exit code**, as if the
   command had run in your SSH.

```bash
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -C ~/Documents/Portfolio/orquestador -- git pull --ff-only'
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -C ~/Documents/Portfolio/orquestador -t 600 -- bash kit-update.sh'
```

`worker.sh` relaunches itself through `tmux-run.sh`. That way the `fetch`, the `claude -p` and
the commit run with the keychain open. As a bonus, the job survives an SSH drop.

### 4. Who pushes: a single door

With the keychain open, the Mac **could already** push. It does not: `ENJAMBRE_NODE_PUSH` is 0 by
default. Windows brings the branch over SSH, runs gitleaks **only on the new commits** and pushes
it itself. There is a single door to GitHub, and it is on the machine that has the scanner.

### 5. The first real job

```bash
PS> $env:ENJAMBRE_MAX_TURNS=25
PS> python enjambre.py run --node mac --repo orquestador --task tasks/TASK-statusline-decimal.md --worker claude
```

The task: with `LC_ALL=es_ES.UTF-8`, the bar showed `$12,00` instead of `$12.35`.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## What actually went wrong

1. **zsh does not split variables into words.** The first attempt used `T="tmux -L enjambre"; $T ls`
   and zsh answered `command not found: tmux -L enjambre`. In bash it works and in zsh it does not: the
   content of an unquoted variable is still **one** word. Fix: write out the whole
   command, or use a `bash` script. It is the same kind of surprise as the `path` variable
   on page 07.
2. **`git pull` failed even though the keychain already worked**: *"There is no tracking information for
   the current branch"*. The Mac's repo was built from a `git bundle` with `init` + `fetch`
   (page 07), so `master` had no associated remote branch. Fix, once per repo:
   `git branch --set-upstream-to=origin/master master`. Lesson: do not blame the first suspect.
   The credentials error had already gone away; this was a different one.
3. **`kit-update.sh` over SSH tripped on an old bug**: the `.sh` files were in git without the execute
   bit, so `./sync-to-home.sh` gave *permission denied*. Fix in orquestador:
   `git update-index --chmod=+x` for the 14 scripts. Windows never noticed because Git Bash
   ignores that bit.
4. **The agent could not edit the main file.** Claude Code treats any path under `.claude/` as
   **sensitive** and rejected the `Edit` on `home/.claude/statusline.sh`, even though
   `Edit` was in `--allowedTools`. The agent did the right thing:
   - it wrote the test, which it was allowed to touch;
   - it **did not try to get around the permission** with Bash;
   - it left the complete patch in its final message.

   The patch was also saved in `out.json`, in `permission_denials`. In review it
   was applied as is, tested and committed on the same branch, with the message explaining where it
   came from.
5. **The ledger said `exit 0` with the task half done.** An agent finishing without an error does not
   mean it met the criteria. **Resolved Oct 4:** `worker.sh` copies the
   `permission_denials` from `out.json` (tool and file, relative to the worktree) and
   `enjambre.py` marks the job `incomplete` if there are any, even when `exit` is 0. Tested with a
   real job that asked to write into `.claude/`:

   ```text
   PS> python enjambre.py run --node mac --repo enjambre-sandbox --task tasks/TASK-denial-probe.md --worker claude
   {"id": "denial-probe", ..., "exit": 0, "status": "incomplete",
    "permission_denials": [{"tool": "Write", "target": ".claude/notes.md"}], ..., "cost_usd": 0.16}
   ```
   (`target` shown here with the relative path of the final version.)
6. **(Oct 4) A test that passed over SSH failed inside the tmux.** `statusline_test.sh` with macOS's
   bash 3.2 died with `seen�: unbound variable`. Over SSH the locale was `C`; the tmux is born from
   launchd with `en_US.UTF-8`. With UTF-8, bash 3.2 reads the bytes of `»` in `"$seen»"` as part
   of the variable name. Fix in orquestador (`89ae2fd`): `${seen}»`. Lesson: the tmux environment
   is not the SSH one, and tests have to be run where the jobs will run.
7. **`result.json` did not reach the branch**: orquestador's `.gitignore` is an allowlist
   (`*`) and was ignoring it. Fix in `worker.sh`: `git add -f result.json`.

## How to verify

```bash
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -- security show-keychain-info ~/Library/Keychains/login.keychain-db'
```

It must say `no-timeout` (or a time), never *User interaction is not allowed*.

```bash
PS> ssh mac 'launchctl print gui/$(id -u)/dev.enjambre.tmux | grep -E "state|pid"'
PS> python enjambre.py status
```

`launchctl print` will say `state = not running` and `last exit code = 0`. **That is correct**: the
launchd job is the `tmux new-session -d` client, which starts the server and exits. The
server stays alive (thanks to `AbandonProcessGroup`), and that is what `tmux -L enjambre ls` shows.

After rebooting the Mac and logging in, `tmux -L enjambre ls` should show the
`enjambre` session without you doing anything. **Verified Oct 4** after a real reboot:

| Step | Who | What was seen |
|---|---|---|
| 1. Reboot | You | Boot 09:26:04 |
| 2. FileVault unlock | You, over `ssh mac` with the password | `System successfully unlocked`. Still no session: no keychain and no tmux |
| 3. Login | You, with a **wired keyboard** (Bluetooth and Deskflow do not exist yet) | Graphical session |
| 4. LaunchAgent `dev.enjambre.tmux` | launchd, 09:28:58.896 | `enjambre: 1 windows (created … 09:28:59)` |
| 5. Keychain test | Claude Code, through `tmux-run.sh` | `gh auth status` ✓ (keyring) · `git pull` ✓ · `claude -p "di hola"` → `¡Hola!` |

The order matters: between step 2 and step 3 the Mac **answers SSH but cannot work**
(`enjambre status` sees it at level `ssh`, page 15). That is why a power outage needs someone to
log in at the screen. Who opened Deskflow on that boot: page 01 §7b.

## Self-check

1. Over SSH, `claude` says *Not logged in*, but in the Mac's Terminal it works. Why?
   <details><summary>Answer</summary>The keychain is open for the security session of your
   graphical login. A process born from <code>sshd</code> belongs to another session and
   cannot read it.</details>
2. What is `-L enjambre` for if a tmux is already running?
   <details><summary>Answer</summary>To avoid attaching to a tmux server born over SSH on
   the default socket. That server has no keychain, and the error would be silent.</details>
3. The agent left the ledger at `exit 0`, but the branch only has the test. What does that tell you about the
   ledger?
   <details><summary>Answer</summary>That <code>exit</code> measures whether the process finished without an
   error, not whether the task was met. The "done" criteria are checked separately: by running the
   tests on the branch and reading the <code>permission_denials</code>.</details>
