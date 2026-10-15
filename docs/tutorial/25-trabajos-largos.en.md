---
lesson:
  route: build-stack
  order: 24
  level: 3
lang: en
---

# 25 — Long jobs on the Mac: launch, close the window, come back to the result

## Goal

**What you get:** a job that runs for hours on the Mac (a heavy script or a `claude -p` agent),
launched from Windows. You can close the window. The job sends heartbeats, has a spending cap,
resumes if it dies, and leaves its result in the ledger.
**Who does it:** Claude Code (session E3, 2026-10-07). All you have to do is restart `watch`
(NEEDS-OWNER §20).
**Concept cards:** [Heartbeat and resume](../conceptos/latido-y-reanudacion.md) ·
[Budget per job](../conceptos/presupuesto-por-trabajo.md).

## Why it was needed

Before, `enjambre run` waited with the ssh session open until the job finished, and only then
wrote the ledger line. If you closed the window, the job could keep going in tmux, but nobody
collected its result. On top of that, an agent running for hours gave no sign of progress, had no
spending cap and, if it died, there was no way to continue it.

## Steps

The steps are the sections below: the pieces, the allowlist in AGENTS.md, and tests (a), (c), (b) and `kind: tests`.

## The pieces

| Piece | Where it runs | What it does |
|---|---|---|
| `jobrun.py` | Mac (`/usr/bin/python3`, 3.9) | `launch` starts a **supervisor** and returns right away. The supervisor runs the job in its own process group and beats every 60 s into `status.json` and `busy.json` (every 5 min it also leaves a line in `latidos.jsonl`). When the job ends it writes the state, releases `busy.json` and, if it is an agent, makes the commit. It also has `status`, `resume` and `kill` (the last one with `killpg`). |
| `largos.py` | Windows | Launch, follow, collect (`collect`), resume and kill. |
| `enjambre script` | Windows | Runs a command **declared** in the repo's AGENTS.md. No agent, costs $0. |
| `enjambre run --detach` | Windows | An agent that runs for hours with `--budget` (USD), `--max-turns` and `--model`. |
| `enjambre status --follow <id>` | Windows | Shows the live log and the heartbeat. Ctrl-C stops you following it, but the job keeps going. |
| `enjambre collect` | Windows (`watch` calls it every 60 s) | Copies heartbeats. If the job has finished, it brings back the log and the branch and writes **one** line in the ledger. It is idempotent. |
| `enjambre resume <id>` / `kill <id>` | Windows | Resume a dead job / stop it entirely. |

The allowlist lives in each repo's AGENTS.md:

```markdown
## enjambre

- tests-cmd: `.venv/bin/python -m tests.test_matcher_regresion`
- script-cmd catalogar-disco: `/usr/bin/python3 tools/catalogar_disco.py "/Volumes/Backup Plus" --etiqueta backup-plus --manifest "..."`
```

`enjambre script --cmd <name>` only accepts a name from that list. A name that is not there is
rejected, and the reply lists the ones that do exist.

## Test (a): a script of ≥ 1 h with the window closed after 5 min

**What runs:** `tools/catalogar_disco.py` computes the sha256 of the whole external disk on the Mac
(118 469 files, 865.2 GB) and checks the MANIFEST of the photo backup (11 404 rows). Until
today, that manifest had only been checked against a sample of 200.

```
PS> python enjambre.py script --repo enjambre --cmd catalogar-disco --follow --interval 15
{"id": "catalogar-disco-20261007-052143", "launched": true, "attempt": 1, "via": "ssh", "supervisor_pid": 58351, "pgid": 58352}
scan 5,000 files, 19.0 GB
...
[latido 05:25:44] running | intento 1 | 4 min | 2.1%
```

**How the window was closed.** From here there is no real window to close, so the launcher ran as
a separate process (`Start-Process`). At 05:26:40, 5 min after launching it, it was killed by its
PID with `taskkill /PID 36192 /T /F`. The job kept beating:

```
{"ts": "2026-10-07T05:27:44-05:00", "state": "running", "attempt": 1, "elapsed_s": 360, "pct": 4.3,
 "last": "hash   4.3% 37.0 GB/865.2 GB 114 MB/s eta 2h00m (7,699/118,469 files)"}
```

While it runs, the viewer shows it: the node carries `busy` with `latido` and `pct`, and the job
detail is read from `status.json · latido`.

**Real failure no. 1: it hung in `opendir()`.** The first attempt (`catalogar-disco-20261007-050636`)
was born inside tmux and went 15 min without writing a single line. `sample` showed the process
blocked in `__open_nocancel`, inside `opendir`, on `/Volumes/Backup Plus`. What confirmed it was
running the same `ls` from tmux: it hung (`rc=124`). Over ssh it took 2 s. The cause is macOS
privacy (TCC): the first time a process from the graphical session touches an external disk, macOS
asks for permission on the screen, and in tmux nobody sees it.
**Fix:** the `script` jobs are born detached from the ssh session (their own session, so cutting
the ssh does not kill them). Agents are still born in tmux, because they need the keychain.
That first attempt was stopped with `enjambre kill`, which also served to test the kill path:
state `killed`, `busy.json` released and the line in the ledger:

```json
{"id": "catalogar-disco-20261007-050636", "status": "failed", "reason": "killed", "exit": -15, "duration_s": 871, "latidos": 5}
```

## Test (c): kill it halfway and resume

At 06:19:23, with the job at 50.7 % (438.6 of 865.2 GB), a crash was simulated. One
`kill -9` to the job's group and another to the supervisor, without letting it write anything:

```
mac$ kill -9 -58352 58351      # grupo del trabajo + supervisor; quedan 0 procesos
PS> python enjambre.py collect catalogar-disco-20261007-052143
{"id": "catalogar-disco-20261007-052143", "state": "dead", "beats": 6}
PS> python enjambre.py resume catalogar-disco-20261007-052143
{"id": "catalogar-disco-20261007-052143", "launched": true, "attempt": 2, "via": "ssh", "supervisor_pid": 67054, "pgid": 67055}
```

`status.json` still said `running`, but the supervisor's pid no longer existed, and `status`
reports it as `dead` (liveness is checked, not believed). The old `busy.json` was left with the
dead pid. `busy.py` reads it as "stale", and attempt 2 replaced it.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

Attempt 2 did not start from zero: **440.7 GB reused**, 424.5 GB left. Since
`catalogar_disco.py` saves every 200 files, the most that is lost is the batch in progress.

**Final result of (a)+(c):** it finished at 07:17:53. That was 6 969 s end to end
(1 h 56 min, 2 attempts, 27 heartbeats), with 865.2 GB and 118 469 files hashed and 0 unreadable.

```json
{"id": "catalogar-disco-20261007-052143", "status": "failed", "exit": 1, "duration_s": 6969, "attempts": 2, "latidos": 27, "pct": 100.0}
manifest: 9,858 ok, 0 distintos, 570 faltan
```

The `failed` is a false negative of the script, not of enjambre. The 570 "missing" are all
`eliminado-vacio` rows from the first backup: 0-byte files that were never copied. So the
**9 858 real copies in the backup match byte for byte** (before, only a sample of 200 had been
checked). Since this session, the script counts those rows separately and does not treat them as a failure.

## Test (b): an agent of ≥ 30 turns with a US$2 cap

The TASK is an endless file-by-file inventory (`tasks/TASK-e3-presupuesto2.md`, with
`push: false`):

```
PS> python enjambre.py run --node mac --repo enjambre --task tasks/TASK-e3-presupuesto2.md --detach --budget 2 --max-turns 300 --model sonnet
{"id": "e3-presupuesto2", "launched": true, "attempt": 1, "via": "tmux", "supervisor_pid": 61160, "pgid": 61215}
```

Beforehand, a 2 s probe was run with `--max-budget-usd 0.05` to see how the CLI cuts off. It ends
with `"subtype": "error_max_budget_usd"`, `"terminal_reason": "budget_exhausted"` and
`"is_error": true`. From that, the supervisor marks `status: incomplete` and `reason: budget`.

Ledger and segment:

```json
{"id": "e3-presupuesto2", "status": "incomplete", "reason": "budget", "exit": 1, "duration_s": 301, "cost_usd": 2.0036, "turns": 99, "latidos": 3, "note": "fetched from node, not pushed (push: false in the TASK)"}
{"attempt": 1, "turns": 99, "messages": 97, "cost_usd": 2.0036, "cost_est_usd": 1.8471, "subtype": "error_max_budget_usd"}
```

99 turns in 5 min. What stopped it was the CLI cap (US$2.0036), with 50 lines of inventory written.
The branch came back to Windows by `git fetch` and was not pushed, because the TASK said `push: false`.

**Real failure no. 2: it counted double.** The first attempt (`e3-presupuesto`) was cut off after 3 min
by the supervisor's backstop (estimate > 1.15 × cap): 117 "turns" and an estimated US$2.66. The
count came out wrong because `stream-json` emits one event per content block and each event
repeats the `usage` of the whole message: it was 117 events from 54 messages. Now it counts by
message id. In the second test the estimate (US$1.85) stayed below the real figure (US$2.00) and
it was the CLI that cut it off.

## `kind: tests` for farmacias, on the Mac

The tests worker gets exactly `Bash(<tests-cmd>)` in `--allowedTools` and nothing else, and the
TASK reaches it on stdin (a TASK that starts with `---` is no longer read as a CLI option).

```json
{"id": "farmacias-tests-e3", "status": "ok", "exit": 0, "duration_s": 11, "cost_usd": 0.217, "pushed": false,
 "push_policy": "repo visibility public: push only with `push: true` in the TASK"}
```

Agent result: `REGRESIÓN LIMPIA: 85/85 casos OK`, no denials.

**Real failure no. 3: the environment.** There was no `.venv` on the Mac. It was created with uv (`uv venv --python
3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`) and the suite would not
import: a clean install brings `selectolax` 1.x, which removed the backend that farmacias uses
(Windows had 0.4.12). `selectolax<1.0` was pinned in farmacias. Since each job's worktree
does not carry the `.venv` (it is in `.gitignore`), enjambre links it from the clone and excludes it in
`info/exclude`.

**Real failure no. 4: bringing back the branch.** The first `git fetch` of the branch from the Mac hung
for about 10 min and ended with `remote end hung up`. The same fetch done by hand took 0.6 s. Now
it has a 120 s cap and one retry.

## What was changed along the way

- **Push** is optional on public repos (`push: true` in the TASK). If gitleaks finds anything, it is
  never pushed.
- **enjambre-guard hook**: `grep 'ssh mac' docs/` is no longer blocked, because only an `ssh` in
  command position counts. `timeout 20 ssh mac`, `env X=1 ssh mac` and `bash -c 'ssh mac ls'` are still
  blocked. 41/41 on bash 5 (Windows) and 3.2 (Mac).
- **Templates** `AGENTS.md` and `DELEGAR.md`: an `## enjambre` section, `.venv/bin/python` or `python3`
  on the Mac, never `py`.

## What actually went wrong

Four real failures, each explained where it happened:

1. **It hung in `opendir()`** (test a): a process born in tmux cannot touch the external disk
   because macOS asks for permission (TCC) on the screen. Fix: the `script` jobs are born detached from the ssh session.
2. **It counted double** (test b): `stream-json` repeats the `usage` in every content block. Fix: count by message id.
3. **The environment** (`kind: tests`): no `.venv` on the Mac and `selectolax` 1.x. Fix: `selectolax<1.0` and a linked `.venv`.
4. **Bringing back the branch**: the `git fetch` hung for about 10 min. Fix: a 120 s cap and one retry.

Also, in (a) the final `failed` was a false negative of the script, not of enjambre (the 570 `eliminado-vacio` rows).

## How to verify

- Windows: `python -m unittest discover -s tests` → 209 OK (32 new in `test_largos.py`).
- Mac: `/usr/bin/python3 -m unittest discover -s tests/node` → 14 OK, with a temporary HOME and a
  fake `claude` that prints `stream-json` (it spends no quota). It covers launch, heartbeat, a `busy.json`
  that does not get stolen, `kill` by group, dead → resume, orphan, budget, `--resume` with the
  remaining budget, TASK over stdin and the `.venv` linked without being committed.

End-to-end test (already done, with its output in this tutorial): `python enjambre.py collect <id>`
on a killed job returns `"state": "dead"`, and `python enjambre.py resume <id>` relaunches it with
`"attempt": 2`, reusing what was already computed (440.7 GB in test c).

## Self-check

1. Why are `script` jobs born from the ssh session and agents in tmux?
   Answer: in tmux a process that touches an external disk hangs waiting for the macOS permission (TCC) that nobody sees; agents do need the keychain, which lives in the graphical session.
2. What happens if `status.json` says `running` but the supervisor's pid no longer exists?
   Answer: `status` reports it as `dead` (it is checked, not believed) and `resume` relaunches it as attempt 2.
3. What cuts an agent off first: the CLI cap or the supervisor's estimate?
   Answer: in the second test the CLI cut it (US$2.0036, `error_max_budget_usd`); the supervisor's estimate (US$1.85) stayed below. The supervisor's backstop cuts if the estimate exceeds 1.15 × the cap.
