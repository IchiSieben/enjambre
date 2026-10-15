---
lesson:
  route: build-stack
  order: 21
  level: 3
lang: en
---

# 22 — The router: let the swarm pick the machine on its own

## Goal

Make `enjambre run` without `--node` pick the node, explain why in one line and,
by default, send to the Mac everything that can be done without a screen.

## Why

Until now every job carried a hand-written `--node mac`. That forces you to remember
what each machine can do and whether it is awake. The concept behind it is
[routing](../conceptos/enrutamiento.md): a table you can read and correct, plus the live
state of the nodes. No AI in the path: choosing a node is a repeatable decision.

Machines: Windows (orchestrator) · Mac mini M2 Pro 16 GB.

## Steps

### 1. Measure what was missing

`nodes.toml` said `ram_gb = 0` for Windows: "not measured". Without that number, a rule like
"editing video needs 32 GB" has nothing to compare against.

```
PS> wmic ComputerSystem get TotalPhysicalMemory
51384029184
PS> wmic cpu get NumberOfCores,NumberOfLogicalProcessors,Name
Intel(R) Core(TM) i5-8600 CPU @ 3.10GHz  6  6
mac$ sysctl hw.memsize hw.ncpu hw.perflevel0.physicalcpu hw.perflevel1.physicalcpu
hw.memsize: 17179869184
hw.ncpu: 10
hw.perflevel0.physicalcpu: 6
hw.perflevel1.physicalcpu: 4
mac$ df -h /    →  460Gi total, 166Gi libres
```

| | Windows | Mac |
|---|---|---|
| RAM | 47.9 GiB → `ram_gb = 48` | 16 GiB |
| Cores | 6 (no hyperthreading) | 10 (6 performance + 4 efficiency) |
| Free disk | C: **33 GB** of 479 (tight) · D: 807 · E: 71 · F: 1 366 | 166 GiB |

### 2. The capabilities, as tags

Each node declares in `nodes.toml` what it has (`tags`):

| Tag | win | mac | What it means |
|---|---|---|---|
| `headless` | yes | yes | can run a job with nobody watching |
| `display` | yes | — | there is a person and a screen (browser, Onshape, CapCut) |
| `keychain` | yes | yes | `claude` and `gh` have a session for unattended jobs |
| `metal` | — | yes | Apple GPU for Blender |
| `mlx` | — | yes | local models from the `modelos-mac` manager |
| `smb` | yes | yes | shared folders from step 19 |

Plus `owned = true` (it is a machine of yours: the restricted-repos rule only allows those) and
`runnable = false` on Windows (see step 5).

### 3. The table: `routing.toml`

Each kind of job says what it needs and whom it prefers:

```toml
[kinds.tests]
needs = ["headless"]
prefer = ["mac", "win"]

[kinds.video-edit]
needs = ["display", "ram>=32"]
prefer = ["win"]
```

Nine kinds: `claude`, `tests`, `git`, `scrape`, `render`, `transcribe`, `local-model`, `gui`,
`video-edit`. The worker adds its own: a `claude` agent always needs `keychain`.

### 4. The measured history

```
PS> python enjambre.py history
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*Render of the real output with `freeze`.*

Only one cell has a number: **render on the Mac, 17 good runs, median 7.9 s.** The others
have fewer than 5 runs and say `sin datos`. The old ledger lines had no kind: it is
inferred without rewriting them (`blender` → render, `engine local` → local-model, `claude` →
claude). The `stub-*` entries do not count: they measure the pipe, not the work.

Today the history **decides nothing**: comparing takes two measured nodes in the same
kind, and Windows has none.

### 5. Choose, explain, record

```
PS> python enjambre.py run --repo enjambre-sandbox --task tasks/TASK-queue-stub.md --kind tests --dry-run
enjambre route [rule] queue: tests: mac is down; queued until it is ready (win: fits, but has no executor yet (runnable = false))
```

`--dry-run` routes and executes nothing and writes nothing to the ledger. Without `--dry-run`, the same line goes
to the screen and to the ledger as `kind`, `routed_by` (`rule`, `history` or `explicit`) and
`route_reason`. The kind comes from `--kind`, otherwise from a `kind:` at the top of the TASK:

```markdown
---
kind: tests
---
# TASK — ...
```

and if there is none either, from `--classify` (a call to haiku, off by default, cost noted) or
from the default kind (`claude`).

**Windows does not execute yet.** `worker.sh` needs tmux and `/usr/bin/python3`, and Git Bash has
neither. That is why Windows has `runnable = false`. With the Mac down, the router says that
Windows fits, that it has no executor, and queues for the Mac. That fulfils "pick Windows or
queue and say so".

### 6. What went where and why

Eight real decisions from 2026-10-07, all **dry** (`--dry-run`, nothing executed). The Mac
was in the middle of the session G2 model benchmark, and sending it real jobs would have competed
with it. The "Mac down" column uses the state `watch` had. The "Mac alive" column uses
a live probe through the alias that did answer (see error 3).

| Job | Kind | Mac down | Mac alive | Why |
|---|---|---|---|---|
| tests of `enjambre-sandbox` | tests | queue → mac | **mac** | first free in `prefer [mac, win]`; win fits but has no executor |
| render of a `forja` piece | render | queue → mac | **mac** | only the Mac has `metal` |
| transcribe in `modelos-mac` | transcribe | queue → mac | **mac** | only the Mac has `mlx` |
| code change in `algoritmos/ada-pc2` | claude | queue → mac | **mac** | default kind, prefers the Mac |
| scraping of `farmacias` | scrape | queue → mac | **mac** | no screen → Mac |
| review `Metallum` in the browser | gui | rejects | rejects | only Windows has `display`, and it has no executor |
| edit video from `forja` | video-edit | rejects | rejects | the Mac has neither `display` nor 32 GB; Windows does, but has no executor |
| tests of a repo `ejemplo-restringido` with worker `agy` | tests | rejects | rejects | restricted-repos rule: only worker `claude` on owned machines |

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*Render of the real output with `freeze`.*

The Mac's memory at that moment, read by the same probe: **48 % free** (`memory_pressure`),
above the floor of 20 %. That is why it did not count as busy.

### 7. The tests

`tests/test_routing.py` has 31 tests with simulated nodes, one class per branch:

- hard filter: missing capability, `ram>=32` against an unmeasured node, node without an executor,
  unknown kind;
- restricted-repos rule;
- no data;
- with history, including the case of a single measured node and the one with 4 runs;
- tie;
- node down and `ssh` node without tmux;
- full slots and memory pressure.

Plus the wiring of `enjambre run`: dry, it picks the Mac; with the Mac down it queues with the route
inside `job.json`, and the child of `watch` keeps `routed_by`.

```
PS> python -m unittest discover -s tests
Ran 96 tests in 0.425s
OK
```

The four suites that already existed stay green.

## What actually went wrong (real errors and how they were fixed)

1. **The prompt's table shape is not valid TOML.** `[kinds.tests] needs = [...] prefer = [...]`
   on a single line cannot be parsed. Fix: one key per line under each `[kinds.x]`.
2. **Two old tests broke** with `AttributeError: 'Namespace' object has no attribute
   'routed_by'`. They build the arguments by hand, without the new flags. Fix: `getattr(a,
   "routed_by", None)` in `cmd_run`, without touching those tests.
3. **The Mac showed up as `down` while alive.** With the corporate VPN on, the route to the
   home network changes and `ssh mac` times out. Another alias does get in (HANDOFF, session G2). `nodes.toml`
   was not touched, because `watch` and the viewer depend on that alias and other sessions were
   running. The test with the Mac alive was done by changing the alias only in memory, inside a
   test script.
4. **The tests took 11 s instead of 1.** The default value `health_fn=model_health` is fixed
   when the function is *defined*, so the test's patch did not reach it and a real ssh was made
   to an unreachable Mac. Fix: `health_fn=None` and resolve it inside the function.
5. **The capture came out with `acci�n`.** The Windows console writes in cp1252. Fix: text without
   accents in the capture's header.

## Steps (continued: session R2)

### 8. The Mac announces that it is busy (session R2)

The blind spot of step 6: memory and the model manager do not see a benchmark running in tmux.
The solution is that **the long job itself announces it**. While it runs, it writes
`~/.enjambre/busy.json`, and removes it on exit:

```
mac$ bash ~/.enjambre/busy.sh run bench sesion-x -- bash scripts/mi_banco.sh     # nuevo trabajo
mac$ bash ~/.enjambre/busy.sh attach bench sesion-g2 13809                       # uno que ya corre
mac$ bash ~/.enjambre/busy.sh show
```

- `run` removes it with `trap … EXIT`, even if the job fails or is cut with Ctrl-C.
- `attach` leaves a watcher that removes it when that pid dies.
- `render_stl.py` writes its own.
- **The pid goes inside** because a `kill -9` or a kernel panic do not run the `trap`. The
  reader asks in the same ssh call whether that pid is still alive. If not, the marker is stale and
  does not count.

The session G2 benchmark was already running, so it was marked with `attach`, without touching its
process or its script. With that marker alive:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*Render of the real output with `freeze` (the macOS user, replaced by `<usuario>`).*

| Kind | With the benchmark running |
|---|---|
| `render` | goes to the Mac **on CPU**, and says so (`cpu_fallback` in `routing.toml`) |
| `transcribe`, `local-model` | is **queued** until the benchmark finishes, and says so |
| `tests`, `git`, `claude`… | ignore the marker; memory pressure is still watched |

`enjambre render` uses it too: `--device` is `auto` by default, which is Metal unless there is
a live marker. In that case it uses CPU and leaves the reason in the ledger (`device_reason`).

### 9. The first real run

With the VPN on, the `mac` alias does not connect (NEEDS-OWNER §15). To avoid touching `nodes.toml`,
which `watch` and the viewer depend on, the alias is changed **for a single command only**:

```
PS> $env:ENJAMBRE_HOST_MAC = "<alias-que-responde>"
PS> python enjambre.py run --repo enjambre-sandbox --task tasks/TASK-router-stub.md --worker stub-ok
enjambre route [rule] run: tests -> mac: first free node of prefer ['mac', 'win']
```

Without `--node` and without `--kind`: the kind comes from the `kind: tests` at the top of the TASK. The
ledger line:

```json
{"id": "router-stub", "node": "mac", "worker": "stub-ok", "repo": "enjambre-sandbox", "branch": "mac/router-stub", "start": "2026-10-07T06:48:26+00:00", "end": "2026-10-07T06:48:33+00:00", "exit": 0, "status": "ok", "permission_denials": null, "duration_s": 2, "pushed": true, "usage": {}, "cost_usd": null, "duration_api_ms": null, "note": "fetched from node over ssh, gitleaks clean, pushed from orchestrator", "kind": "tests", "kind_source": "frontmatter", "routed_by": "rule", "route_reason": "tests -> mac: first free node of prefer ['mac', 'win']"}
```

A `stub-ok` worker was used, which calls no model: it costs $0 and does not compete with the benchmark.

**Real errors of R2:**
6. **The alias override was not enough.** `decide` still trusted the `nodes.json` from `watch`, which
   probes the alias in `nodes.toml` and said `down`. With an override, the tier is probed live
   (`host_override`).
7. **`queue-stub` already existed as a worktree on the Mac** since 04-10 (day-month), and `worker.sh` refuses to
   reuse an id. `TASK-router-stub.md` was created, the same but with `kind: tests`, and nothing
   was deleted on the Mac.
8. **The "all busy" reason did not say why the chosen node was busy.** A
   test caught it and now it says so.

## What remains

- A local executor on Windows (worker without tmux and with Windows Python) so that
  `runnable` becomes `true`. It is in NEEDS-OWNER §14.
- Wrap the benchmark, LTX and transcription launchers with `busy.sh run` the next time
  they are launched. `sesion_g2_run.sh` was not touched because it was running.
- More real jobs through the router, so the history starts to decide. The first one is done.

## How to verify

```
PS> python -m unittest discover -s tests
Ran 96 tests in 0.425s
OK
```

(Figure from session R; HANDOFF records 124 OK at the close of R2.)

```
PS> python enjambre.py run --repo enjambre-sandbox --task tasks/TASK-queue-stub.md --kind tests --dry-run
enjambre route [rule] queue: tests: mac is down; queued until it is ready (win: fits, but has no executor yet (runnable = false))
```

With the Mac reachable, the same dry route picks `mac` (table of step 6). The real run of step 9 leaves in the ledger `"kind": "tests"`, `"routed_by": "rule"` and `"exit": 0`.

## Self-check

1. What two things does the router decide, and where does each come from?
   Answer: what the job needs (`routing.toml`, by kind) and which nodes satisfy it and are free (tags in `nodes.toml` plus live state). No AI in the path.
2. Why does Windows fit but receive no jobs?
   Answer: it has `runnable = false`, because `worker.sh` needs tmux and `/usr/bin/python3`, and Git Bash has neither. The router says so and queues for the Mac.
3. How does the router know the Mac is busy with a benchmark running in tmux?
   Answer: the long job writes `~/.enjambre/busy.json` with its pid; the reader checks whether that pid is still alive, and if not, the marker is stale and does not count.
