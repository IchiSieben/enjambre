---
lesson:
  route: build-stack
  order: 10
  level: 2
lang: en
---

# 09 — An agent that works alone all night

## Goal

Leave Claude Code running several phases of work while you sleep and, in the morning, have a
report with evidence of what finished, what failed and what only you can do.

## Why

An unattended agent has two problems that an attended one does not: nobody answers if it asks, and
nobody stops it if it gets something wrong. This guide's answer to both is structural, not a
matter of trust:

1. **Hard rules written beforehand** (what never to do), so the agent does not have to decide in
   the heat of the moment.
2. **A file to leave what it cannot do** (`NEEDS-OWNER.md`), so a blocker does not halt the night:
   it is noted, the agent skips to the next independent task and carries on.

But there is a warning that this page documents with a real case: rules written in a prompt are
worth only as much as the agent's compliance with them. Here the session was launched with Claude
Code's permissions **turned off** (`--allow-dangerously-skip-permissions`), so nothing technical
stopped it from breaking a rule. One was broken, and the agent declared it (see "What actually
went wrong").

Contrast with step 08: the worker that runs **on the Mac** never uses that flag and carries a list
of allowed tools. Tonight's Windows session did use it; safety depended on the rules and on the
review afterwards.

## Steps

### 1. Launching the session

In PowerShell, inside the `enjambre` repo:

```powershell
PS> claude --allow-dangerously-skip-permissions
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

The first message (visible in the screenshot) told it, in short: that `gh auth login` was already
done on the Mac, that the Command Line Tools were being updated, to run the prompt
`prompts/00-overnight-run.md`, that Phase A was already done (session 1), and to start at Phase B
and keep going until all the phases were finished without asking anything.

### 2. The prompt's hard rules

The prompt sets, above any other instruction (paraphrased):

- No `sudo` on the Mac; do not type, request or store passwords.
- **No deletion** (files, branches, repos), no `git push --force`, no `git reset --hard` on
  anything that was not created that night.
- No new public repo and no visibility change; new repos only private and only where a phase asks
  for them.
- Do not push: the public kit (`claude-kit`, held back until tested on the Mac), the website repo
  (its `main` deploys itself), or any repo whose path contains a restricted word.
- **gitleaks before every push**; with a finding, do not push and note it.
- Do not touch processes the agent did not launch; long remote jobs go inside `tmux`.
- If `ssh mac` fails: try a `ping` once against the Mac's `.local` name to find the new IP (it had
  already changed once, see `04-red-y-dhcp.md`); no network scanning.
- If a usage limit hits: write the state to `HANDOFF.md` before stopping.

### 3. The NEEDS-OWNER pattern

Everything that asks for a password, a macOS click, a browser login or a decision from the owner is
noted in `NEEDS-OWNER.md` with **exactly what to click or run**, skipped, and the agent moves on to
the next thing. In the morning, the file is an ordered list of human tasks. Each item says what is
happening, why, and the options with their cost.

### 4. The morning report

`MORNING-REPORT.md`, short and in Spanish, with four blocks: what finished **with evidence**
(command and result), what failed and why, the NEEDS-OWNER items in order, and a plan for the day.
What finished that night (all from the report):

| Phase | Result |
|---|---|
| B · orquestador on the Mac | 4 of 4 suites green; idempotent installation; `verify.sh` with no drift (see `07-orquestador.md`) |
| C · walking skeleton | Round trip working; failure path with exit 1 (see `08-primera-tarea-distribuida.md`); 12 tests OK |
| D · backup audit | 59 repos audited, **0 pushes** (none met all the conditions); 26 private repos mirrored on the Mac with `git bundle` |
| E · Drive and site | Documentation only: the per-machine steps were left in `NEEDS-OWNER.md` §6 |

That the audit ends in **zero pushes** is a correct result, not a failure: the literal rule was
applied plus the owner's exclusions, and no repo ended up eligible.

## What actually went wrong

### The macOS keychain does not open over SSH

The most important thing the night uncovered. The owner had run `gh auth login` on the Mac and it
worked, but the token ended up in the **keychain** (Keychain). An SSH session has no access to the
graphical session's keychain:

```
ssh mac 'security show-keychain-info ~/Library/Keychains/login.keychain-db'
→ User interaction is not allowed.
ssh mac 'gh auth status'           → The token in default is invalid.
ssh mac 'claude -p "ok" ...'       → "Not logged in · Please run /login"
```

The `gh auth status` screenshot in the Mac's Terminal (not over SSH) shows the login as valid,
with storage `(keyring)`: proof that the problem is not the login but **where** it was stored.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

Consequences: `gh`, `git push` and `claude -p` do not work over SSH. Two documented options (the
agent **did not choose** on the owner's behalf; they are still pending):

- **(a) Token in a file.** `gh auth login --insecure-storage` and `claude setup-token` saved in a
  file with `chmod 600`. It survives reboots, but anyone who reads the disk has the token.
- **(b) `tmux` started from the graphical session**, once per boot. Anything launched from SSH
  inside that tmux inherits the graphical context and sees the keychain. No plain text, but it has
  to be repeated on every reboot.

And one prohibition: **never** `security unlock-keychain` from a script, because it asks for the
password.

### The agent's own stumbles (told as they are in the report)

- **Log name collision.** A collision between log names forced the tests to be re-run.
- **In zsh, the variable `path` clobbers `PATH`.** In zsh, `path` is an array tied to `PATH`:
  assigning to it in a loop rewrites the `PATH`. One of the agent's loops left that SSH session
  without commands.
- **The first push to the test repo went out without a prior gitleaks scan.** It broke the rule
  "gitleaks before every push". It was scanned afterwards: clean. A later scan does not replace a
  prior one.
- **A real machine name traveled to two test branches.** `result.json` included the Mac's
  `hostname` in two branches of the test repo (private). It was fixed for the following ones (see
  `08-primera-tarea-distribuida.md`); those two branches were not rewritten.
- **It deleted files of its own despite the "no deletion" rule.** Logs and bundles that the agent
  itself had created that night in the Mac's `/tmp`. It touched nothing of the owner's, but the
  rule was absolute, and that is why it declared it.

A counterexample of the rule properly applied: the 4 folders left half-done after a first cloning
attempt were **not deleted**; they were completed with a second `fetch`
(`docs/DECISIONES.md` §15).

### Decisions without the owner

Every time the agent decided something on its own, it noted it in `docs/DECISIONES.md` with what it
chose, why, what it discarded and how to revert it. Example: the branch that the Mac cannot push is
pushed from Windows, instead of copying a token (decision 9).

## How to verify

For each row of the report, reproduce its command and compare with the recorded result. And the
central claim, that the keychain blocks SSH, is reproduced like this:

```powershell
PS> ssh mac 'security show-keychain-info ~/Library/Keychains/login.keychain-db'
```

Expected result: `User interaction is not allowed.` If it stopped happening, one of the two
options (a) or (b) has already been applied.

Left to check by hand, because there is no command that proves it on its own: that the list of
hard rules was followed. What the agent itself declared breaking is in the previous section; what
it did not declare is not verified.

## Self-check

1. Why does a blocker not halt the night?
   <details><summary>Answer</summary>Because it is noted in `NEEDS-OWNER.md` with the exact
   instructions and the agent skips to the next independent task.</details>
2. The session was launched without permissions. What protected against an `rm -rf` then?
   <details><summary>Answer</summary>Only the rule written in the prompt and the review
   afterwards; nothing technical prevented it. That is why the report has to be reviewed and the
   hard rules must be few and clear.</details>
3. `gh auth status` says "valid" in the Mac's Terminal and "invalid" over SSH. What changes?
   <details><summary>Answer</summary>The token is in the keychain, which only the graphical
   session can open; SSH has no access to it.</details>
