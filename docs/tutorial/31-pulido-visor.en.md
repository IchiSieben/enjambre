---
lesson:
  route: build-stack
  order: 30
  level: 2
lang: en
---

# 31 — Viewer polish: what is "now", what is a window

## Goal

Make every number in the viewer say when it is from and where it comes from, and make sure no
screenshot shows the username.

## Why

A gauge with no timestamp looks like "now" even if the reading is 7 minutes old (ssh failed and the
last good one is shown). A curve with no window does not say whether it covers 15 minutes or 4
hours. And a counter that does not add up to the same as the cards below it makes you doubt
everything else. It is the difference between **data** and **metadata**: a number is only useful
if it travels with its time and its origin.

Follows [28 — Telemetry](28-telemetria.md) and [29 — Priorities](29-prioridades.md).

## Steps

Sections 1 to 3 are the steps: time clarity, three things that did not add up and path privacy.

## 1. Time clarity

- Each gauge carries `ahora · hace N s` ("now · N s ago") underneath, computed from `telemetria.at`:
  the time of the measurement itself, not that of `live.json`. It turns amber at 60 s and red
  ("lectura vieja", stale reading) at 3 min. The thresholds live in `live.py` (`TELE_AMBER_S`,
  `TELE_RED_S`) and travel in `live.json` (`tele_umbrales`), so the page and the tests use the same
  numbers.
- Each curve states its window and its step: `últimos 1 h · 1 punto / 11 s`. The step is
  **measured** as the median of the gaps between points in the ring, not written in by hand.
  Measured: 11 s (the 10 s cycle plus what the reading takes).
- Next to the selector: "the gauges are always now; the selector only changes the curves".
- Processes: `Procesos · top 12 · muestra de hace N s`.
- On the node card there are two times, each with its content: `Instantánea (discos, memoria,
  montajes) hace 4 min` (the slow one, every 5 min) and the chip `telemetría hace 9 s` (the fast
  one).

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## 2. Three things that did not add up

### 2.1 The header did not count the external jobs

Lab renders and adopted processes are listed by the planner, which runs **after** the header is
built. `live.count_externos()` adds them at the end of the cycle and the header breaks them down:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

### 2.2 "Última señal hace —" on an external job

A lab job has no heartbeat of its own. Its signal is the answer to `GET /jobs`, with the time of
the poll. Progress does not come in `/jobs`: it is in each render's log
(`logs/lab_jobs/<id>.log`), on a line like `50/60 frames · … · ETA 42 s`. `collect.py` reads it
with `--lab-logs`; the folder is set in `config/scheduler.toml`. An adopted process takes the
node's process reading as its signal.

Real render launched for the test (`POST /jobs/<id>/restart` on the lab on the Mac):

```
4 min de render, 83 % (50/60 cuadros, faltan ~42 s)
última señal hace 3 s (GET /jobs)
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

The render was 60 frames with a line every 50, so there was only a single progress datum, 40 s
before the end. A single-image render does not write that line and shows only the time.

### 2.3 Network and disk I/O on the PC; interface on the Mac

On the PC, `Get-Counter` and `typeperf` took the same time (~1.7 s: the sample is 1 s). `typeperf`
was chosen: it does not start PowerShell and gives input and output separately.

```
PS> typeperf -sc 1 "\Network Interface(*)\Bytes Received/sec" "\Network Interface(*)\Bytes Sent/sec" "\PhysicalDisk(_Total)\Disk Bytes/sec"
"(PDH-CSV 4.0)","\\<equipo>\Network Interface(Intel[R] Ethernet Connection [2] I219-V)\Bytes Received/sec",...
"10/11/2026 07:12:31.415","74044.118943","0.000000","26028.697621","0.000000","524176.880953"
```

It runs in parallel with the CPU sample. The PC reading goes from ~0.25 s to ~1.35 s per 10 s
cycle. The machine name from the CSV is not stored.

On the Mac, `netstat -ibn` per interface gives the deltas between readings, and
`networksetup -listallhardwareports` says what each one is (`en0` → Ethernet). The interface that
took in the most bytes yields `entra por en0 (Ethernet)`, the way to see that traffic goes over the
cable ([tutorial 29 §8](29-prioridades.md)). Disk comes from deltas of `iostat -Id`, which is
cumulative and does not wait.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

**Failure found along the way:** `parse_netstat` read shifted columns on interfaces with no address
(`gif0`, a VPN's `utun`): they have one column fewer. With the VPN on, its traffic would have come
out wrong. It now has a test.

## 3. Privacy: the cause without the username

The Attention table showed a cause with the full path
`/Users/<usuario>/.enjambre/catalogo/backup-plus-manifest.tsv`. `razones.home_paths()` turns
`/Users/<u>/` into `~/` and `C:\Users\<u>\` into `~\`, also with JSON's doubled backslashes. It is
applied in four places:

- when writing a new cause (`_clip`);
- when showing the old ledger rows (`build.alerts`), because the ledger already written is not
  rewritten;
- in the page's `deid()`, for the history and the details;
- when publishing (`web.clean_why` / `clean_log`).

On the web, that cause still comes out as "exit 1 (detalle solo en local)": `~/` still counts as a
path for the leak check.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## How to verify (4. Check)

```
PS> python -m pytest -q tests/test_visor_pulido.py
19 passed
```

The prompt's fixtures: `telemetria.at` from 7 min ago → `crit`; an adopted process with no
heartbeat takes the time of the process reading (`lista de procesos`); the exact ledger line with
`/Users/<u>/` comes out with `~/`.

## 5. What is left

- The **local** viewer's Projects tab shows the name of the Mac's home folder as a project
  (sessions opened in `~`). It is not a cause (outside §3) and the name is stored in `history.db`.
  Changing `project_of` would split the history into two groups. Proposal: rename it when
  displaying. The web copy already publishes projects by alias only.
- In the PC detail, the thermal gauge says "(sin sudo)", which is the Mac's reason, not the PC's.
  It predates this work; it was left alone.

## What actually went wrong

- The header did not count lab renders or adopted processes (§2.1).
- A lab external job showed "Última señal hace —" because it has no heartbeat of its own (§2.2).
- `parse_netstat` read shifted columns on interfaces with no address (`gif0`, a VPN's `utun`); it now has a test (§2.3).
- The Attention table showed the username in a full path (§3).

## Self-check

1. Which field is the gauge's "N s ago" computed from?
   Answer: from `telemetria.at`, the time of the measurement, not that of `live.json`.
2. Where do the signal and progress of a lab render come from?
   Answer: the signal is the answer to `GET /jobs`; the progress, from the log line in `logs/lab_jobs/<id>.log`.
3. Why is `home_paths` also applied when showing the old ledger rows?
   Answer: because the ledger already written is not rewritten.
