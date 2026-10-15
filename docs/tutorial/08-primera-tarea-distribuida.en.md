---
lesson:
  route: build-stack
  order: 9
  level: 2
lang: en
---

# 08 — First distributed task: Windows commands, the Mac works

## Goal

From Windows, send a `TASK-<id>.md` to the Mac with a single command and get back a **git branch**
with the result and a **line in the ledger**.

## Why

This is the project's *walking skeleton*: the thinnest, ugliest version that works end to end. It
teaches more than a pretty dispatcher with nothing behind it, because every piece (SSH, worktree,
tmux, push, ledger) is already proven in its real place.

The isolation decisions, with their reasons:

- **A worktree per task.** Each job has its own copy of the files; two jobs (or a job and you)
  never write the same file at the same time. They share the `.git`, so the branch exists in the
  repo without copying anything.
- **A branch per task (`mac/<id>`).** The result is reviewable and disposable. Merging to `main`
  is a human decision, never the agent's.
- **Git is the bus, not a shared folder.** Two processes writing the same network folder is a race
  you do not see until a file ends up half written. With git, the result arrives complete or it
  does not arrive.
- **Explicit `--allowedTools`, never `--dangerously-skip-permissions`.** Nobody watches the Mac
  while it works: whatever is not on the list is **denied**, not asked about.
- **tmux.** If SSH drops, the job keeps going; `tmux wait-for` makes the Windows command wait for
  it to finish.
- **The contract.** An unattended agent cannot ask you what you meant, so the task must say which
  files it may touch (`## Files you may touch`) and when it is done (`## Done criteria`). Without
  that, `enjambre` rejects it before connecting.

## Steps

The round trip:

```
 WINDOWS (orquesta)                                   MAC (ejecuta)
 ──────────────────                                   ─────────────
 enjambre.py run --node mac --task TASK-x.md
   1. valida el contrato (TASK + AGENTS.md)
      y la regla de repos restringidos ── si falla: exit 3,
                              no se toca la red
   2. scp worker.sh  ───────────────────────────────▶ ~/.enjambre/worker.sh
   3. ssh mac bash worker.sh < TASK-x.md ───────────▶ 4. git worktree add ../wt/<repo>-x -b mac/x
                                                      5. tmux: worker (claude -p … | stub)
                                                      6. result.json + commit en mac/x
                                                      7. git push  (si hay credenciales)
   8. si la Mac no pudo pushear:      ◀── git fetch por ssh ── rama mac/x
      gitleaks sobre la rama → git push a GitHub
   9. una línea en jobs.jsonl
```

Commands and real output (2026-10-04), against a private test repo (`enjambre-sandbox`):

```powershell
PS> python enjambre.py run --node mac --repo enjambre-sandbox --task tasks/TASK-changelog-stub.md --worker stub-ok
```
```powershell
PS> python enjambre.py run --node mac --repo enjambre-sandbox --task tasks/TASK-must-fail.md --worker stub-fail
```
```powershell
PS> python enjambre.py status
2026-10-04T11:54:02+00:00  changelog-stub    mac  stub-ok   exit=0 pushed=True mac/changelog-stub
2026-10-04T11:54:15+00:00  must-fail         mac  stub-fail exit=1 pushed=True mac/must-fail
2026-10-04T11:54:33+00:00  claude-changelog  mac  claude    exit=1 pushed=True mac/claude-changelog
```

A real ledger line (`jobs.jsonl`):

```json
{"id": "changelog-stub", "node": "mac", "worker": "stub-ok", "repo": "enjambre-sandbox",
 "branch": "mac/changelog-stub", "start": "2026-10-04T11:54:02+00:00",
 "end": "2026-10-04T11:54:08+00:00", "exit": 0, "duration_s": 0, "pushed": true,
 "usage": {}, "cost_usd": null}
```

And on GitHub, the `mac/changelog-stub` branch carries the change and its `result.json`.

**What a *stub* is:** a fake worker that does something trivial and predictable (add a line, or
fail on purpose). It lets you test the **pipeline** without spending a model or depending on its
login. The failure path (`stub-fail`) proves criterion E4-1: even if the task fails, the branch is
published with a `result.json` explaining the error and the command exits with a code ≠ 0.

## What actually went wrong

- **The Mac cannot push to GitHub over SSH.** The `gh` credentials are in the macOS keychain,
  which an SSH session cannot open. Fix without moving tokens: Windows fetches the branch
  **from the Mac** with `git fetch mac:Documents/Portfolio/<repo> mac/<id>:mac/<id>`, scans it
  with gitleaks and pushes it itself. Git is still the bus; only who makes the last hop changes.
- **The real `claude` worker ended with `"Not logged in · Please run /login"`** for the same
  reason (the Claude Code login also lives in the keychain). The pipeline worked in full (branch,
  ledger, push) but the model never got to work; it is the third line of `status`, with `exit=1`.
  Pending for the owner (`NEEDS-OWNER.md` §1; see `09-agente-desatendido.md`).
- **`result.json` carried the machine's real name (`hostname`).** It now uses the node name
  (`mac`) that the orchestrator passes in: data that travels must not identify the machine. Two
  test branches already published keep the old value (see `09-agente-desatendido.md`).

## How to verify

Without the Mac, the tests of the Windows piece:

```powershell
PS> python -m unittest discover -s tests -v     → 12 tests OK
```

They cover the contract (file name, sections, `AGENTS.md`), the rejection rule for restricted
repos with external workers (including that `run` exits with 3 **without** touching the network)
and that the ledger is append-only, with LF line endings on Windows too.

With the Mac: `python enjambre.py status` must show a line per task, and the `mac/<id>` branch
must exist on GitHub.

## Self-check

1. Why a worktree per task and not a single working folder?
   <details><summary>Answer</summary>So that two jobs (or you and a job) never write the same file
   at the same time; they share the `.git` but not the files.</details>
2. What does the `stub-fail` worker prove?
   <details><summary>Answer</summary>That when the task fails, the branch is still published with a
   `result.json` that explains the error, and the command exits with a nonzero code.</details>
3. The Mac cannot push. How does the branch reach GitHub without copying a token?
   <details><summary>Answer</summary>Windows runs `git fetch` for the branch from the Mac over SSH,
   scans it with gitleaks and pushes it itself.</details>
