---
lesson:
  route: build-stack
  order: 27
  level: 3
lang: en
---

# 28 — Live viewer and per-node telemetry: what is running now and how each machine breathes

## Goal

**What you get:** open the viewer and see, without scrolling, what is running **right now** (swarm
jobs first, in green, with the turn advancing) and, with one button, the detail of each machine:
CPU, GPU, RAM with pressure and swap, thermals, network, the chart for the last hour and which
processes are eating it, tagged with the swarm job or session they belong to.
**Who does it:** Claude Code (session of 2026-10-10, prompts 18 step 1 and 19 steps 1–3).
**Before:** [27 — Viewer v3](27-visor-v3.md). Long jobs (`run --detach`) are covered in
[tutorial 25](25-trabajos-largos.md) and the bridge in [26](26-puente.md).

## Why

The viewer said “live · 5 min ago” but did not show what was running at that moment. The three causes are in §0.

## 0. The problem

The viewer said “live · 5 min ago”, but it did not show what was running at that moment. There were
three causes, and all three were checked in the code before touching anything:

1. **“0 queued” with jobs running.** The KPI counted `state/running/` (the `watch` queue) and the
   local models. A `run --detach` does not go through that queue: it leaves `state/jobs/<id>/job.json`
   on the PC when dispatched and writes **a single line** to `jobs.jsonl`, when it is collected.
   While it runs, nothing counted it. That is what happened with c1–c3 of the pharmacies on the 09th: they are in `state/jobs/` and in
   the ledger, but only since they were collected.
2. **“5 min ago”.** The `\enjambre\visor` task runs `build.py` every 5 min and bakes the data
   into the HTML. “Live” meant “reload when there is a new snapshot”, that is,
   every 5 min at most.
3. **Sessions that were working showed up last.** `collect_sessions()` sorted by
   `started` (age), not by state or last activity.

## 1. What each machine returns (measured, not assumed)

This is the raw output used to write the parsers and the tests
(`tests/test_telemetria.py` uses these same texts). The macOS user was removed
(`<usuario>`).

### 1.1 Mac mini (macOS, Apple Silicon), 2026-10-10 20:2x

`ssh mac true` takes **0.28 s**.

```
$ vm_stat
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                     9061.
Pages active:                                 209859.
Pages inactive:                               205042.
Pages speculative:                              3981.
Pages wired down:                             510144.
Pages occupied by compressor:                  76961.
Swapins:                                    70193413.
Swapouts:                                   77543081.
$ sysctl vm.swapusage hw.memsize vm.loadavg hw.perflevel0.logicalcpu hw.perflevel1.logicalcpu
vm.swapusage: total = 5120.00M  used = 3969.38M  free = 1150.62M  (encrypted)
hw.memsize: 17179869184
vm.loadavg: { 1.98 1.49 1.35 }
hw.perflevel0.logicalcpu: 6
hw.perflevel1.logicalcpu: 4
$ sysctl kern.memorystatus_vm_pressure_level kern.memorystatus_level
kern.memorystatus_vm_pressure_level: 1
kern.memorystatus_level: 43
$ memory_pressure -Q
System-wide memory free percentage: 42%
$ pmset -g therm
Note: No thermal warning level has been recorded
Note: No performance warning level has been recorded
Note: No CPU power status has been recorded
$ ioreg -r -d 1 -c IOAccelerator   (solo PerformanceStatistics)
"Tiler Utilization %"=4  "Renderer Utilization %"=4  "Device Utilization %"=98
"In use system memory"=6654509056  "Alloc system memory"=8407318528
$ /usr/bin/top -l 2 -n 0 -s 1 | grep '^CPU usage'
CPU usage: 12.71% user, 13.15% sys, 74.12% idle
CPU usage: 13.26% user, 13.16% sys, 73.56% idle
   (1.342 s en total: demasiado lento para cada ciclo)
$ ps -Ao pid,ppid,pgid,pcpu,rss,etime,comm -c | sort -k4 -nr | head
74189 73413 73413  35.5 728128       15:24 Python
82461     1 82461   8.4  16944       00:00 sshd-session: <usuario> [priv]
  405     1   405   6.5   8304 05-17:09:02 opendirectoryd
43632 43624 43624   0.2 156144    01:11:07 claude
$ tmux -L enjambre list-panes -a -F '#{pane_pid} #{session_name}:#{window_name}'
73413 cohbench:kernel_task
1203 enjambre:zsh
43624 enjambre:run-43609
```

What was learned from this:
- **Memory pressure can be read directly**: `kern.memorystatus_vm_pressure_level` is 1, 2
  or 4 (normal, warning, critical). There is no need to infer it from “% free”. On a Mac, pressure
  matters more than free memory, because macOS uses all the RAM on purpose.
- **The thermal limit does not show up** until macOS records an event: `pmset -g therm` does not
  print `CPU_Speed_Limit`. The honest value is `null` (unknown), not 100. What is known
  is that “no thermal warning has been recorded”.
- **The GPU was at 98 %** and there was nothing obvious in `ps`: it was the proyecto-externo render. The
  `pgid` of the Python process (73413) is the pid of the `cohbench` pane in the enjambre tmux. That cross-reference
  is what tags the process.
- `top -l 2` takes 1.3 s. Instead, the CPU is read from the kernel counters with ctypes
  (`host_processor_info`), with no subprocess and no sudo.
- `ps -c` gives the short name, but `sshd-session: <usuario>@notty` includes the user name.
  The collector keeps only what comes before `: `.

### 1.2 PC (Windows 10, i5-8600, 48 GB)

```
> wmic path win32_VideoController get name,AdapterRAM          (739 ms)
AdapterRAM  Name
1073741824  Intel(R) UHD Graphics 630
4293918720  NVIDIA GeForce GTX 1050 Ti
> wmic cpu get LoadPercentage,NumberOfCores,NumberOfLogicalProcessors   (1 192–1 446 ms)
LoadPercentage  NumberOfCores  NumberOfLogicalProcessors
37              6              6
> nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader   (59–139 ms)
8 %, 2619 MiB, 4096 MiB, 52
> Get-CimInstance MSAcpi_ThermalZoneTemperature -Namespace root/wmi       (162–315 ms)
Not supported
> wmic path Win32_PerfFormattedData_PerfProc_Process get Name,IDProcess,PercentProcessorTime,WorkingSetPrivate
(8 479 ms, 1 396 líneas)
```

What was learned:
- **`wmic` for processes takes 8.5 s**: no good every 10 s. It was replaced by a single call to
  `NtQuerySystemInformation` (ctypes), which lists every process with its CPU time and its
  memory. Two readings are taken 200 ms apart and the difference is computed.
- Total CPU is computed with `GetSystemTimes` (ctypes). `wmic cpu` (1.2 s) was dropped.
- The GPU that matters is the NVIDIA one (`nvidia-smi`, 59–139 ms). It is launched at the start and read at the
  end, so its time overlaps with the CPU sample. The Intel UHD is not measured: it shows “n/a”.
- Temperature: `Not supported`. It shows “n/a”. LibreHardwareMonitor stays on the ROADMAP.

### 1.3 What the new collector takes

| | Inside the process | Over ssh |
|---|---|---|
| PC, `collect.py --telemetria` | **264–267 ms** (target < 300) | — |
| Mac, `collect.py --telemetria` | **71–80 ms** | **0.52–0.88 s** (target < 1.5 s) |
| Mac, `collect.py --live` (sessions + jobs + tmux + telemetry) | 117 ms | 0.79–1.06 s |

## 2. How it is built

```
enjambre.py live --serve        (tarea \enjambre\live, al iniciar sesión, residente)
  cada 10 s, o al instante si `run --detach` toca state/visor/live.kick:
    PC  -> collect.collect_telemetry() + sesiones, dentro del mismo proceso
    Mac -> ssh mac '/usr/bin/python3 ~/.enjambre/visor/collect.py --node mac --live'   (UNA ssh)
    + state/jobs/*/job.json  (el trabajo existe desde el despacho, antes del primer latido)
    + state/queue, state/running, state/nodes.json
  -> state/visor/live.json, live.js (window.LIVE = …), ring.js (4 h por nodo)
visor.html (file://) -> carga live.js cada 5 s como <script>; ring.js solo con el Detalle abierto
```

- **Why `live.js` and not `fetch('live.json')`:** a `file://` page cannot `fetch()`
  a neighboring file (Chromium blocks it), but it can load it as a script. It is the same trick
  that `visor-stamp.js` already used.
- **Why a single ssh and no `ControlMaster`:** Windows OpenSSH does not handle
  multiplexing well, and `ssh mac true` already costs only 0.28 s. If a node takes more than 2 s, it is
  read every 60 s instead and the Detail says so.
- **Heartbeat every 10 s** (it used to be 60): `run --detach` and `script` rewrite `status.json` on the Mac
  with the turn, the last tool and the estimated cost. The viewer shows it ~10 s later.
- **States:** session `running` (Claude Code says `busy`, or its statusline changed < 60 s ago, or
  it asked for a job that is still running), `waiting` and `idle` (> 30 min, folded). Job
  `launching` (dispatched, no heartbeat yet), `running`, `stalled` (heartbeat stopped > 5 min,
  amber) and `dead` (the supervisor no longer exists: **it never shows in green**).
- **Process tags:** each process inherits the tag of its group or of the nearest
  ancestor that enjambre knows: `trabajo: <id>` (pid, pgid and supervisor from `status.json`),
  `tmux enjambre: <sesión>` (panes of the enjambre tmux) or `sesión: <nombre>` (Claude
  Code windows). The reader itself shows up as `enjambre: telemetría`.
- **Ring:** each node keeps `~/.enjambre/visor/telemetria.ring.jsonl` (1 440 lines, 4 h at
  10 s; it trims itself). The PC keeps a copy in `state/visor/ring-<nodo>.jsonl` and, on
  startup, fetches the node's once so the charts do not start empty.

## Steps

The steps are §3 (getting it running), §7 (rest) and §8 (`nodo add`); §4 is the evidence.

## 3. Getting it running

```powershell
powershell -File tools\install-live-task.ps1      # tarea \enjambre\live, arranca ya
python enjambre.py live --print                    # un ciclo, imprime live.json entero
python enjambre.py live --stop                     # para el bucle por su pid
python enjambre.py visor                           # abre el visor
```

If `live.json` has gone more than 90 s without updating, the header shows the red warning **“viewer
asleep”**. The web copy never shows it, because it has no `live.js`.

## 4. Acceptance criteria (with the evidence)

### a) A trivial `run --detach` to the Mac shows up on top, in green and with the turn advancing, in < 30 s

```
python enjambre.py run --detach --node mac --repo enjambre-sandbox --task tasks/TASK-live-probe2.md --model haiku --budget 0.5 --max-turns 30
```

Sampling of `state/visor/live.json` every 2 s (`t0` = 20:45:29; dispatch: 20:45:30):

```
+  4.0s live@20:45:22 job=None                                   pos_en_corriendo=None/4
+  6.0s live@20:45:33 job=live-probe2 estado=running turno=0  latido=20:45:33 pos_en_corriendo=1/6
+ 18.0s live@20:45:44 job=live-probe2 estado=running turno=5  latido=20:45:43 pos_en_corriendo=1/6
+ 26.1s live@20:45:54 job=live-probe2 estado=running turno=12 latido=20:45:53 pos_en_corriendo=1/6
+ 38.1s live@20:46:05 job=live-probe2 estado=ok      turno=18 latido=20:46:00
```

It appears **3 s** after dispatch, first of 6 and in green; the turn advances 0 → 5 → 12 → 18.
**Met.**

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

What went wrong along the way: the first probe (7 steps) finished in 13 s, before the second heartbeat,
so the turn could not be seen advancing (it went from 0 to “finished ok 8”). That is why the second one has 16
steps. Also, under “Running” the job showed up 2nd or 3rd, because the Claude windows move
more often. Now swarm jobs go first and sessions after. And the job's own
Claude showed up as one more session (`enjambre-sandbox-live-probe2-dc`): it is now
folded into the job's card.

### b) The proyecto-externo render shows up tagged as belonging to the swarm

```
live.json -> telemetria.mac.top:
{'pid': 84469, 'name': 'Python', 'cpu': 80.2, 'rss_gb': 0.39, 'tag': 'tmux enjambre: cohbench'}
{'pid': 84574, 'name': 'ffmpeg', 'cpu': 13.0, 'tag': 'tmux enjambre: cohbench'}
{'pid': 43632, 'name': 'claude', 'cpu': 14.3, 'rss_gb': 0.23, 'tag': 'sesión: proyecto-externo-5b'}
```

**Met, with a nuance:** the render (`coherence_bench.sh` → `python -m externo.cli bench …`) was not
launched with `run --detach`, but inside the `cohbench` session of the enjambre tmux
(`tmux-run.sh`). That is why it has no id in the ledger, and the tag is `tmux enjambre: cohbench`,
not `trabajo: <id>`. The Claude session that drives it is tagged separately
(`sesión: proyecto-externo-5b`).

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

### c) In the Detail, the Mac CPU goes up on the chart during a heavy 60 s job

`enjambre-sandbox/AGENTS.md` declares `script-cmd cpu-burn` (8 × `yes` for 60 s; a single `yes`
would be a +10 % that is hard to see on top of the render load):

```
python enjambre.py script --repo enjambre-sandbox --cmd cpu-burn --node mac      (20:46:38)
+ 12.1s mac_cpu=98 top=[('yes', 98.6, 'trabajo: cpu-burn-20261010-204638'), ...]
+ 32.1s mac_cpu=87
+ 62.1s mac_cpu=86
+ 72.1s estado=ok mac_cpu=50
+ 92.1s mac_cpu=9
```

**Met.** The chart goes from ~13 % to 88–98 % and the 8 `yes` processes show up as
`trabajo: cpu-burn-…`.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## 5. What is not measured (and says “n/a”)

- Temperature in degrees on the Mac: it needs `sudo powermetrics`. The decision is the owner's
  (NEEDS-OWNER §23).
- Temperature on the PC: `MSAcpi_ThermalZoneTemperature` → `Not supported`.
- The PC's Intel GPU and the Mac's VRAM (unified memory): “n/a”. Disk I/O and network on the PC have been
  measured since prompt 22 ([31](31-pulido-visor.md)).
- A process's `%CPU` on macOS is the moving average from `ps` (~1 min). The machine total comes from
  the kernel counters over the cycle window, so they may not add up at a given instant.

## What actually went wrong

The errors in §6, plus the short probe and the order of “Running” (§4 a) and those of `nodo add` (end of §8).

## 6. Errors that showed up (so nobody repeats them)

- `pmset -g thermlog` **never ends**: it streams events. Inside an ssh with `| head`, the
  remote process stayed alive. It was killed by its PID (81317). Use `pmset -g therm`.
- `claude auth status` **over ssh** says `loggedIn: false`, and from tmux (`tmux-run.sh`) it says
  `true`. The macOS keychain only opens in the graphical domain. Check the login from tmux,
  which is where the jobs run.
- `--nodo`, `--modelo`, `--tope-usd` and `--max-turnos` do not exist. The real flags are `--node`,
  `--model`, `--budget` and `--max-turns`, and `--repo` and `--task` are also required.

## 7. Rest: when to let a machine rest (step 4 of 19)

**Goal:** that `run` does not send work to a machine with no memory, and that the viewer says why.

**Why:** a red light explains nothing. Here there are three visible rules, with their numbers in
`~/.claude/rules/thresholds.json` (`visor-reposo-*`), evaluated over the telemetry
ring:

| Rule | Met when | Effect |
|---|---|---|
| out of memory | pressure ≠ `normal` for ≥ 5 min in a row | nothing is dispatched there: `run` queues or picks another node; `watch` leaves that node at `max_jobs` 0 |
| throttling | `CPU_Speed_Limit` < 100 for ≥ 2 min | warning only |
| paging | swap grows ≥ 1 GB in 10 min | warning only |

“In a row” means with no gaps: if the loop was off for more than 60 s, or a reading came out
normal, the count goes back to zero. A pressure that could not be read does not count as pressure. If
`live.json` is more than 2 minutes old, `routing` ignores it, so a dead loop blocks nothing.

**Check with a staged case.** While testing, the Mac had no real pressure, so
`live.json` was replaced with a staged case: 6 minutes in `warn`, computed by
`live.reposo_rules`. With that, a real `run --dry-run` was launched against the real nodes
(`dryrun_reposo.py` in the session scratchpad):

```text
enjambre route [rule] queue: claude: every apt node is busy; queued for mac (busy, reposo: sin
memoria: presión warn desde hace 6 min: no despachar más aquí (since …)) (win: fits, but has no
executor yet (runnable = false))
```

The criterion in §5 asks that “`run` picks the other node and says so”. With two nodes that cannot
happen: the PC has `runnable = false`, so the honest thing is to queue and say why, which is
what comes out above. The “picks the other node” part is tested with three nodes in
`tests/test_reposo.py` (`Routing.test_pressure_sends_the_job_to_the_other_node_and_says_why`):
`run`, `laptop`, `diverted_from: mac`, “reposo: sin memoria…”. `Decide.test_decide_reads_live_json`
goes through the real `decide()`.

With an explicit `--node mac` it warns, but dispatches:

```text
enjambre: warning, mac is in reposo: sin memoria: presión warn desde hace 6 min: no despachar más
aquí (since …); dispatching anyway (--node)
"route_reason": "--node mac; warned: reposo: sin memoria: …"
```

Without the staged case (the real state): `claude -> mac: first free node of prefer ['mac', 'win']`.

In the viewer, Detail → **Rest** block shows the rule that fired, since when, and the
three rules with their numbers. If `thresholds.json` cannot be read, it says “⚠ thresholds” instead of
inventing a verdict.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## 8. Adding a node: `enjambre nodo add` (step 5 of 19)

**Goal:** put a machine into the swarm with a single command, without editing `nodes.toml` by
hand or copying the collector by hand.

**Why:** until now you had to write the table in `nodes.toml`, copy `collect.py`,
install the LaunchAgent and check by eye. Each of those steps could fail silently. Now
they are four named steps, and if one fails it says which.

```text
enjambre nodo add <nombre> --host <alias-ssh> --os macos [--label "…"] [--runnable]
enjambre nodo doctor <nombre>     # repite las comprobaciones sin escribir nada
enjambre nodo rm <nombre>         # deshace lo que hizo add (y nada más)
```

Real test against the Mac, with a throwaway name so as not to touch the `mac` node:

```text
$ python enjambre.py nodo add macprueba --host mac --os macos --label "Mac (prueba)"
1 ok  medido: {'ram_gb': 16, 'cores': 10, 'arch': 'arm64'}
1 ok  nodes.toml: [nodes.macprueba] añadido
2 ok  collect.py 5db5d3d833be
3 ok  state = not running · last exit code = 0
4 ok  73981 bytes, esquema ok
```

`build.py` showed it in the viewer without touching the HTML: three cards, with “Mac (prueba)” and its
snapshot from 9 s ago. Telemetry shows “n/a” and presence “unknown” because `live` and `watch`
read `nodes.toml` at startup; `add` warns about this.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

```text
$ python enjambre.py nodo rm macprueba
nodo: dev.enjambre.visor.macprueba descargado y borrado, con su snapshot y su log (collect.py se queda: lo comparten)
nodes.toml: [nodes.macprueba] quitado
$ sha256sum nodes.toml   → a82d70d7… (el mismo que antes del add; git diff vacío)
mac$ launchctl print gui/$(id -u)/dev.enjambre.visor.macprueba → no cargado; 0 archivos «macprueba»
```

**Errors that showed up (and were not visible with the fake ssh of the tests):**
- The first attempt failed at step 2: “collect.py on the node is another version”. On Windows,
  `subprocess` in text mode turns `\n` into `\r\n` when writing to stdin, so the file
  arrived with CRLF. It is now sent as bytes. Step 2 compares sha256, which is why it caught it.
- The second attempt failed at step 3: “not loaded”, but it was loaded (`last exit
  code = 0`). This macOS does not print “run interval” in `launchctl print`. Now `state =` is
  checked, and `launchctl print` only exits with 0 if the service is loaded.
- Between the two attempts, `nodo rm` left `nodes.toml` identical. That is the proof that undoing
  works even when half done.

## How to verify

```powershell
python enjambre.py live --print          # un ciclo: imprime live.json entero
python -m unittest tests.test_live tests.test_telemetria    # 26 tests (según HANDOFF)
python enjambre.py visor                 # abre el visor -> Detalle en una tarjeta de nodo
```

Expected results, already obtained above: in §4 a) the job shows up in < 30 s, in green and with the turn
advancing (0 → 5 → 12 → 18); in §4 c) the Mac CPU goes from ~13 % to 88–98 %; in §8 `nodo add` finishes with the
four steps in `ok` and `nodo rm` leaves `nodes.toml` identical. Pending: rest was tested with a staged case, not
with real memory pressure.

## Self-check

1. Why does the viewer load `live.js` instead of doing `fetch('live.json')`?
   Answer: a `file://` page cannot `fetch()` a neighboring file (Chromium blocks it), but it can load it as a script.
2. When does a machine stop receiving work because of “out of memory”?
   Answer: when memory pressure is different from `normal` for at least 5 minutes in a row (with no gaps); then `run` queues or picks another node.
3. Why does a `dead` job never show in green?
   Answer: `dead` means the supervisor no longer exists; that is how §2 defines it (“never shows in green”). `stalled` (heartbeat stopped > 5 min) goes in amber.

> **Ethernet cable (19 §8):** the Wi-Fi vs Ethernet measurements are in
> [29 — §8](29-prioridades.md#8-wi-fi-vs-ethernet-prompt-19-8-2026-10-10).
