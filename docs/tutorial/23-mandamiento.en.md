---
lesson:
  route: build-stack
  order: 22
  level: 2
lang: en
---

# 23 — The commandment: every session knows the Mac exists

## Goal

Any Claude Code session, in any folder and on both machines, starts out knowing that the Mac is
a work node and that work is delegated to it through enjambre. If it tries a bare `ssh mac`, a hook
stops it and tells it what to use.
**Who does it:** Claude Code (session 13, 2026-10-07). You, nothing: just restart Claude Code
so it loads the new rule.

## Why: policy + mechanism

An agent remembers nothing between sessions. It only knows what enters its context at startup.
What is loaded in **every** session, in any folder, lives in `~/.claude/`: the global `CLAUDE.md`
and the files in `~/.claude/rules/`. That is the only place where a rule applies to
all projects without repeating it in each one.

But text is **policy**: the model reads it and almost always follows it, but nobody forces it.
The **mechanism** is a `PreToolUse` hook: Claude Code runs it before every command, the hook
sees the command and can stop it with a message. It is the same split as in the router:
`routing.toml` says what is wanted and the pure functions in `routing.py` enforce it. Concept
card: [Policy and mechanism](../conceptos/politica-y-mecanismo.md).

## Steps

The four steps below are the steps of the exercise.

## Step 1 — The rule (policy)

The rule is in `orquestador/home/.claude/rules/enjambre.md` and is installed at
`~/.claude/rules/enjambre.md`. It is in English because it is loaded in every session and costs tokens.
It says four things:
- The Mac is the node for everything that does not need a screen.
- Work is delegated with `enjambre.py run|render|status`, never with `ssh mac`.
- Before the first delegation, the repo must have `AGENTS.md` and `docs/DELEGAR.md`.
- With a restricted word in the path, only the `claude` worker runs, on our own machines.

**Why it does not go in `CLAUDE.md`, as the prompt asked.** There were two reasons:
- `CLAUDE.md` is already at 70 of its 72 lines, and that cap is a context budget.
- `CLAUDE.md` is published in `claude-kit`, and the publication filter rejects the restricted words.

The files in `rules/` are loaded in every session just the same, and `rules/personal.md` had already
proven it. **The prompt said `run|render|check`, but `check` does not exist** (`enjambre.py` has
`run`, `watch`, `status`, `history`, `render` and `pull`). The rule says `status` so as not to send
every session to a command that fails.

## Step 2 — The hook (mechanism)

`~/.claude/hooks/enjambre-guard.sh` is wired in `settings.json` as `PreToolUse` with
`matcher: "Bash|PowerShell"`. Here PowerShell is the main terminal, and with `Bash` alone
there would be a hole. For each command it does this:
1. If the command does not contain `ssh`, it exits right away without running anything else. It runs before every
   command and almost none of them mention it.
2. If the current folder has a segment called `enjambre`, `enjambre-*` or `orquestador`, it lets it
   through. The Mac is maintained by hand there.
3. It splits the command into words. After each `ssh` it skips the options (`-t`, and also
   `-p 22` or `-o X=y`, which take an argument) and looks at the destination. If it is `mac`, `mac6` or
   any `host` in `nodes.toml` other than `local`, it stops it with code 2. With that code,
   Claude Code does not run the command and returns the message to the model.
4. If the command carries `ENJAMBRE_RAW_SSH=1`, or the variable is in the environment, it lets it through and
   writes a line to `~/.claude/logs/events.jsonl` with `raw_ssh:1` and the host, never the command.

If `jq` is missing, the JSON arrives broken or `nodes.toml` cannot be read, the hook lets the
command through. A broken hook must not keep you from working.

**The detail about the variable.** In `ENJAMBRE_RAW_SSH=1 ssh mac …` the variable only exists for the
`ssh`, which runs **after** the hook, so the hook does not see it in its environment. That is why it looks for it
inside the text of the command.

```
PS> bash tests/enjambre_guard_test.sh      # 28 casos; en la Mac también con /bin/bash 3.2
```

## Step 3 — Templates and `/mac`

`enjambre/templates/AGENTS.md` is the minimum contract that `enjambre run` requires: what the worker
may touch, what it may not, and how it is verified. `enjambre/templates/DELEGAR.md` has 15 lines: what goes
to the Mac, the exact commands and what is proven. Both come from the six `DELEGAR.md` files of
session R. The new template does not recommend `ssh mac`, because the hook would now block it.

Step 0 of the `/mac` skill is the *bootstrap*:
1. It copies the templates that are missing.
2. It fills in `<repo>` and `<stack>`.
3. It asks you to confirm three lines: `<kinds>`, `<tests-cmd>` and `<off-limits>`.
4. It checks that no `<marcador>` is left.
5. It commits only those two files, with no push.

The skill's maintenance steps (waking the screen, `pmset`, `fdesetup`) carry
`ENJAMBRE_RAW_SSH=1` on purpose. They are not work, and this way they get counted.

## Step 4 — Deployment and testing

```
PS> bash kit-update.sh                                    # Windows
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -C ~/Documents/Portfolio/orquestador -- bash kit-update.sh'
```

On the Mac, the `git pull` goes through `tmux-run.sh` because the credentials are in the keychain
(tutorial 11). The tests were done with a fresh `claude -p` in a temporary repo. `enjambre-sandbox`
was not used, because that folder matches `enjambre-*` and the hook lets it through.

| Test | Windows | Mac |
|---|---|---|
| (a) the new session quotes `# Two machines, one swarm` | yes | yes (full output) |
| (b) `ssh mac echo hola` blocked with the message | yes | yes |
| (c) with `ENJAMBRE_RAW_SSH=1` it passes (`hola`) and leaves `raw_ssh:1` | yes | — |
| (d) `/mac` creates and commits `AGENTS.md` + `docs/DELEGAR.md` | yes (`7462a01`, 2 files) | — |
| hook suite | 28 OK (bash 5) | 28 OK on bash 3.2.57 and 5.3 |

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*Render of the real output with `freeze`.*

## What it does not do (yet)

- The viewer does not count the `raw_ssh` lines yet: they are written, but nobody adds them up.
- There is no `enjambre` for "run this long script without an agent". Today there are two paths: a
  TASK with `kind: tests`/`scrape`, or the explicit bypass.
- farmacias and Metallum have `ssh mac` in their uncommitted `DELEGAR.md`. Their windows
  fix it with the line from NEEDS-OWNER §16.

## What actually went wrong

No execution failure was recorded in this exercise: HANDOFF (session 13) takes it as done and tests (a) to (d) passed. The stumbles noted were design ones:

1. The prompt asked for `run|render|check`, but `check` does not exist; the rule says `status` (DECISIONES 168).
2. The prompt asked for the rule in `CLAUDE.md`, which was already at 70 of 72 lines and is published in `claude-kit` with a filter that rejects the restricted words; it went to `rules/enjambre.md` (DECISIONES 167).
3. `ENJAMBRE_RAW_SSH=1 ssh mac …` is not visible to the hook as an environment variable, because it only exists for the `ssh` that runs afterwards; the hook looks for it inside the text of the command.
4. The test could not be done in `enjambre-sandbox`: it matches `enjambre-*` and the hook lets it through; a temporary repo was used.

## How to verify

```
PS> bash tests/enjambre_guard_test.sh      # 28 casos; en la Mac también con /bin/bash 3.2
```

Expected result: 28 OK on Windows (bash 5) and on the Mac (bash 3.2.57 and 5.3). Then, from a temporary repo outside `enjambre*` and `orquestador`:

- a fresh `claude -p` session quotes `# Two machines, one swarm` (output in salidas/23-prueba-a-mac.txt);
- `ssh mac echo hola` is blocked with the hook's message (code 2);
- `ENJAMBRE_RAW_SSH=1 ssh mac echo hola` prints `hola` and leaves a `raw_ssh:1` line in `~/.claude/logs/events.jsonl`.

## Self-check

1. What is the difference between policy and mechanism here?
   Answer: the rule in `~/.claude/rules/enjambre.md` is policy (the model reads it and almost always follows it); the `PreToolUse` hook is mechanism (it stops the command with code 2 even if the model does not follow the rule).
2. Why does the hook accept `Bash|PowerShell` and not just `Bash`?
   Answer: PowerShell is the main terminal on Windows; with `Bash` alone there would be a hole.
3. What happens if `jq` is missing or `nodes.toml` cannot be read?
   Answer: the hook lets the command through; a broken hook must not keep you from working.
