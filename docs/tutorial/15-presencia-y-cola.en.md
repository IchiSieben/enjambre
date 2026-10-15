---
lesson:
  route: build-stack
  order: 14
  level: 3
lang: en
---

# 15 — Node presence and the job queue

> Page 12 is the 3D and studio one; pages 13–14 are reserved for sessions B and C of `prompts/09-sesiones-2026-10-04.md`.

## Goal

`python enjambre.py status` tells you, without you asking by hand, which nodes can receive work right now.
In addition, an `enjambre run` against a powered-off node **does not fail**: the job waits in the queue and goes out
on its own when the node comes back.

## Why

Up to here, `enjambre run` assumed the Mac was ready. If it was not, the job failed
and you had to remember to relaunch it. For the swarm to work without you, two pieces are needed:

- **Knowing who is alive.** "Alive" has levels, because each one fails for different reasons:

  | Level | What it means | What fails if it does not arrive |
  |---|---|---|
  | `down` | Not even ping | The machine is off or off the network |
  | `ping` | Answers ping, but SSH does not get in | sshd or the key |
  | `ssh` | SSH gets in, but there is no `tmux -L enjambre` session | **Nobody logged in on the screen**, so there is no keychain (step 11) |
  | `ready` | SSH and the session with keychain | Nothing: it can be dispatched |

- **A queue.** A job requested for a node that is not `ready` waits its turn instead of getting lost.
  See the concept card [job queue](../conceptos/cola-de-trabajos.md).

**Polling or heartbeat.** Windows asks every 30 s; the nodes do not report in. The full comparison and
the reason are in the card [polling vs heartbeat](../conceptos/sondeo-vs-latido.md). In one line: polling
measures exactly the path a job uses (ping → SSH → tmux), and does not require opening any
port on Windows.

## Steps

### 1. `enjambre watch`

```powershell
PS> python enjambre.py watch --once        # una ronda y sale (para probar)
PS> python enjambre.py status
nodes  (watch, 0 s ago)
  mac    ready  since 2026-10-04T14:09:44+00:00
queue  0 waiting
```

Each round makes a single SSH call per node: `tmux -L enjambre has-session -t enjambre`. That is
enough for two levels: code 0 is `ready`, 1 is `ssh` (it got in, but there is no session), and
255 means SSH did not get in, so ping is tried. The real round against the Mac took
**0.46 s**.

What it writes, in `state/` (ignored by git):

| File | What |
|---|---|
| `nodes.json` | Level of each node, since when and last check. It is written to a temporary file and renamed, so a reader never sees half a file |
| `watch.log` | Startups, level changes and dispatches. It rotates at 512 KB and keeps 3 copies |
| `queue/`, `running/`, `done/` | One folder per job, which moves between the three. Nothing is ever deleted |
| `watch.pid`, `watch.lock` | For `--stop` and so there are never two instances |

**Debounce:** dropping a level requires two observations in a row. A single lost reply
does not trigger the "mac down" notification. Going up a level, on the other hand, is immediate.

**Windows notification** only when the level changes: "mac available" or "mac down". The
first round after starting `watch` records the level, but does not notify.

### 2. The queue

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

*Render of the real output with `freeze`. The "no tmux" state was simulated by writing `nodes.json`;
the poll that brought it back to `ready` was real.*

What happens inside:

- **The contract is validated before queueing.** The restricted-repos rule, the `AGENTS.md` and the task
  sections are checked just as if it ran now. An invalid job never enters the
  queue.
- **A copy of the task is saved** (`state/queue/<id>/TASK-<id>.md`) and of the `ENJAMBRE_*`
  variables. The job runs what you asked for, even if you edit the file afterwards.
- **There are never two jobs with the same id** waiting or running at once.
- **The ledger is written when the job runs**, not when it is queued. A line in
  `jobs.jsonl` still means "this happened".
- **`watch` dispatches each job as a separate process** (`enjambre run --no-queue`), respecting
  `max_jobs` from `nodes.toml`. A long job does not hold up the polling.
- **If `nodes.json` is stale** (more than 90 s, because `watch` is not running), `run` does not trust it and asks
  the node at that moment.

### 3. Automatic startup and a single instance

```powershell
PS> powershell -File tools\install-watch-task.ps1
TaskPath   TaskName   State
\enjambre\ watch    Running
PS> python enjambre.py watch
watch: another instance is running; not starting a second one
PS> python enjambre.py watch --stop
watch: stopped pid 38332
```

- The scheduled task `\enjambre\watch` starts when **you log in**, with `pythonw.exe` so that
  there is no window. It was registered **without administrator permissions**.
- Against duplicates there are two layers: the task has *MultipleInstances = IgnoreNew*, and `watch`
  takes a file lock that the system releases if the process dies. A stale pid file
  never blocks the next startup.
- `--stop` kills **by the pid that `watch` itself wrote**, never by process name.

### 4. The Mac side: is the orchestrator there?

`node-watch.sh` runs every 60 s (LaunchAgent `dev.enjambre.nodewatch`, template in `config/`):

1. It checks whether the Deskflow client has an `ESTABLISHED` connection with Windows (`netstat`, without
   sending anything); if not, a `ping`. *(Until October 4 it opened a TCP connection to 24800 with `nc -z`,
   and Windows logged `failed to accept secure socket` every minute: see page 01, "What actually went
   wrong", point 0.)*
2. It takes the address from Deskflow's own configuration, so there is no IP in the repo.
3. It notifies on the Mac only when the state changes.

Transition test, with a copy that does not notify:

```
up(1st)    -> state: up 0
miss1      -> state: up 1       <- una falla sola no cambia nada
miss2      -> state: down 2     <- dos seguidas: "Windows no responde"
back       -> state: up 0
```

## What actually went wrong

1. **I overwrote a file I had not read.** When saving this session's prompt to
   `prompts/09-sesiones-2026-10-04.md`, the tool answered "updated": the file already existed,
   with sessions A, B and C, and uncommitted. I recovered it from the copy that Claude Code saves
   before each write (`~/.claude/file-history/<sesión>/`), verified that the original text
   was intact and inserted session 0 in front. Lesson: an "updated" where you expected "created"
   is an alarm; and an uncommitted file has no safety net in git.
2. **`watch --once` dispatched but did not close.** It waited for the job to finish and exited without
   moving it to `done/`, which stayed in `running/`. The real test caught it, not the unit tests: the tests
   covered the normal round, not the exit of `--once`. Fix: a `reap` function that is called on
   both paths.
3. **The Mac's boot date came out as 1970.** `sysctl kern.boottime` returns
   `{ sec = …, usec = … }`, and my regex `.*sec = ` was greedy: it captured `usec`.

## How to verify

```powershell
PS> python -m unittest discover -s tests       # 28 tests: 16 con nodos simulados
PS> Get-ScheduledTask -TaskPath '\enjambre\' -TaskName watch | Select-Object State
PS> python enjambre.py status                   # "watch, N s ago" con N < 90
```

On the Mac: `ssh mac 'cat ~/.enjambre/orq-state; tail -3 ~/enjambre-logs/node-watch.log'` must
show `up`.

**Still to be checked by eye:** that the Windows notification and the Mac one are visible on screen.
They were sent without error, but the agent cannot see your screen.

## Self-check

1. The Mac answers SSH but `status` says `ssh` and not `ready`. What happened and what do you do?
   <details><summary>Answer</summary>The <code>tmux -L enjambre</code> session does not exist: nobody
   logged in on the Mac's screen (for example, after a reboot with FileVault), so there is no
   keychain. Log in; the LaunchAgent creates the session and the next round sees it
   <code>ready</code>.</details>
2. Why does the ledger not get a line when the job is queued?
   <details><summary>Answer</summary>Because a ledger line means "this ran".
   Queueing is an intention; the queue's record is the folders in
   <code>state/queue</code>.</details>
3. What prevents two `watch` instances from starting if the scheduled task fires twice?
   <details><summary>Answer</summary>Two layers: <em>MultipleInstances = IgnoreNew</em> on the
   task and a file lock taken by the process itself. The system releases it if the process
   dies.</details>
