---
lesson:
  route: build-stack
  order: 19
  level: 2
lang: en
---

# 20 — Master viewer: one screen for the whole swarm

## Goal

See on a single page what runs on each machine, how much it consumes and where each kind of
task performs best. And keep the essentials always in view in the Claude Code status bar.

## Why

With two machines (and soon four), "is everything fine?" turned into a round of
`ssh`, `tail` and `ls`. That is [observability](../conceptos/observabilidad.md) done by hand. The
rule of this step is **do not invent metrics**: first take inventory of what the logs really
carry, and show whatever they do not carry as "not measured".

Machines: Windows (orchestrator) · Mac mini M2 Pro. Claude Code 2.1.289 on both.

## Steps

### 1. Inventory: what can really be measured

Before writing any code, the real logs of both machines were read. The full result is in
[`docs/VISOR-FUENTES.md`](../VISOR-FUENTES.md). What changes the design:

| Question | Honest answer |
|---|---|
| Tokens per session? | Yes, in `~/.claude/projects/**/*.jsonl`. **But** every block of a response repeats the same `usage`: each `message.id` must be counted once (548 responses in 1 292 lines). |
| Cost in dollars? | There is no `costUSD` in the transcripts. It is **derived** from the prices in `models.json`, which have no cache-write price. That is why it is a floor ("≈ API equivalent"). |
| 5 h / 7 d quota? | Only in the snapshots the statusline writes (`~/.claude/state/*.json`). |
| Which sessions are open? | `~/.claude/sessions/<pid>.json` + check that the PID is alive. |
| Model time vs local time? | In `claude -p` yes (`duration_api_ms`), but `worker.sh` threw it away. Fixed; old records stay "not separable". |
| Which local model is loaded? | There is no endpoint for that in the Mac's manager: only the running task and its memory peak. |
| Budget in dollars? | None is defined. The real limit is the quota. |

### 2. The collector: each node summarizes its own

`tools/visor/collect.py` runs **on each machine**. It uses only the standard library and is
compatible with Python 3.9, because on the Mac it runs with `/usr/bin/python3` (3.9.6, no `tomllib`).
It writes a JSON file to `~/.enjambre/visor/snapshot.json`.

There are 1.1 GB of transcripts on Windows, so the collector is **incremental**. It remembers
how many bytes it read from each file (`~/.enjambre/visor/cursor.json`) and processes only what was
appended. It also ignores files unchanged for 8 days.

```
PS> python tools/visor/collect.py --node win --out $env:TEMP\visor-win.json
real 0m3.720s        ← primera vez (lee 8 días)
real 0m0.776s        ← segunda vez (solo lo nuevo)
```

On the Mac, as a LaunchAgent every 5 min (same pattern as `nodewatch`, no sudo):

```
PS> scp tools/visor/collect.py mac:.enjambre/visor/collect.py
PS> scp config/dev.enjambre.visor.plist mac:/tmp/
mac$ sed "s|__HOME__|$HOME|g" /tmp/dev.enjambre.visor.plist > ~/Library/LaunchAgents/dev.enjambre.visor.plist
mac$ launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.enjambre.visor.plist
mac$ launchctl print gui/$(id -u)/dev.enjambre.visor | grep -E "last exit|run interval"
	last exit code = 0
	run interval = 300 seconds
```

### 3. The aggregator: Windows gathers everything, no new ports

`tools/visor/build.py` runs on Windows every 5 min as a Scheduled Task:

1. For each node in `nodes.toml`, the local one runs the collector directly. For a remote one it
   runs `ssh <host> cat ~/.enjambre/visor/snapshot.json`, over the same SSH as always, without
   opening any port.
2. It adds what only the orchestrator has: presence (`state/nodes.json`), queue
   (`state/queue|running|done`) and ledger (`jobs.jsonl`).
3. It writes `state/visor/visor.html` (one page, no external dependencies) and the one-line
   summary for the statusline, which it **pushes** to each node over the same SSH.

```
PS> powershell -NoProfile -ExecutionPolicy Bypass -File tools\visor\install-visor-task.ps1
TaskPath   TaskName   State
\enjambre\ visor    Running
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*Render of the real output with `freeze`.*

It was tested **from the Scheduled Task**, not only from the terminal. A task may lack the SSH
key the terminal does have, and here `push: {'mac': True}` in the log confirms it.

### 4. The viewer

Open `state/visor/visor.html` with a double click. The data goes **inside** the HTML because a
`file://` page cannot `fetch()` a local JSON (the browser blocks it), and starting a local
server would mean opening a port. It reloads itself when the snapshot changes (see §4b).

#### 4b. As a Windows app (17b, 2026-10-07)

**Goal:** open the viewer like any other app: with its own icon on the desktop, in the Start menu
and on the taskbar, with no tabs and no address bar.

**Why this way:** Chromium (Brave, Edge) has the `--app=<url>` mode: a window without the browser
interface that Windows treats as a separate program, with its own taskbar button and the page's
favicon as its icon. Nothing needs packaging (Electron, installed PWA): just a `.lnk` shortcut
with that argument.

```bash
python enjambre.py visor --install     # icono + accesos directos (escritorio y menú Inicio)
python enjambre.py visor               # abrir la ventana ahora, sin acceso directo
```

Real output:

```text
icono   ...\enjambre\state\visor\visor.ico (16/32/48/256 px)
destino C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe --app="file:///C:/.../state/visor/visor.html" --window-size=1280,860
acceso  C:\Users\<usuario>\Desktop\Visor enjambre.lnk
acceso  C:\Users\<usuario>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Visor enjambre.lnk
```

What `tools/visor/instalar.py` does:

1. **Icon.** `tools/visor/icon.svg` is the hexagon with a dot from the viewer's header, in its
   colors (background `#0D1117`, accent `#2DD4BF`). The script reads the SVG with the standard
   library and draws it with Pillow at 8× to scale it down afterwards (that smooths the edges), and
   writes `state/visor/visor.ico` with 16, 32, 48 and 256 px. It uses no browser and no cairo. It
   only understands the three shapes the icon uses: rounded rectangle, polygon with stroke and circle.
2. **Browser.** It looks for Brave in the registry's `App Paths` (HKCU, HKLM, WOW6432Node) and in
   Program Files; if it is not there, it looks for Edge.
3. **Shortcut** with `WScript.Shell` (pywin32): target the browser, `--app=` arguments with the
   `file:///` path of `visor.html`, the `visor.ico` icon and the viewer's folder as "Start in".
   The paths are computed from the repo, never typed by hand.

**Self-reload.** `build.py` writes, next to the HTML, `visor-stamp.js` with a single line
(`window.VISOR_STAMP = <hora del build>;`). Every 30 s the page inserts a new `<script>` that
points to that file, and reloads only if the time is newer than that of the data it already
shows. Before, it reloaded blindly every 2 min. The page cannot request `snapshot.json` with
`fetch()`, because Chromium blocks `fetch` between `file://` files; it can, however, load a
neighboring file as a script. If the browser blocked that too, a visible **reload** button
appears in the header; while polling works, the button is hidden.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*Title bar of the `--app` window at 1280 CSS px, and below it its taskbar button,
separate from Brave's. Crop with no project data.*

**Real test (2026-10-07):**
- The app was opened from the desktop shortcut. The window is called `Visor del enjambre`
  and its large icon (the one the taskbar uses, 48 px read with `WM_GETICON`) is
  the hexagon.
- The snapshot was rebuilt at 21:46:01. At 21:46:27 the window, which had been opened at
  21:45 with the previous snapshot, already said «19 s ago»: it reloaded itself, untouched.

**What went wrong:** the first `icon.svg` had `--bg` inside an XML comment, and `--` is not
allowed in an XML comment, so `ElementTree` rejected it (`not well-formed`). Browsers
tolerate it; Python's parser does not. Also, the first capture of the taskbar
came out empty because PowerShell was not *DPI-aware*: with 150 % scaling the screen bounds
came out as 2293×960 instead of 3440×1440. It was fixed by calling `SetProcessDPIAware()` before
`CopyFromScreen`.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*Capture of the `--demo` version: work projects appear as `proyecto-A`, `-B`...; no
paths, no window names. It is generated with `python tools/visor/build.py --demo salida.html`.*

What it shows, from top to bottom:

- Live nodes, running jobs, tokens for today and for 7 days (both machines added together), and the
  5 h / 7 d quota with the machine and the time of the reading.
- One card per machine with presence, snapshot age, memory, disks, SMB mounts
  (the ones `watch` monitors since session E), tmux and model manager.
- Open Claude Code sessions, one row per window.
- Consumption per day and machine, with an equivalent table below.
- Swarm jobs and the comparison.

It also works on the phone and in dark mode:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

### 5. Comparison: where does each task perform best?

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

Each cell shows **n**. With fewer than 5 runs it carries the "few data" mark. Today the
honest answer is "it cannot be said yet": the ledger only has jobs from the Mac,
and there is no test, git or transcription through the ledger on either machine. The
comparison measured by hand is in [18](18-mac-vs-windows.md). This table will fill itself
as the swarm works.

Columns "Model (cloud)" and "Tools (local)": cloud time does not depend on the
machine; local time does. Only the second tells which node is the better fit.

### 6. The status bar

The statusline (`orquestador/home/.claude/statusline.sh`, copied to `~/.claude/` on both
machines) adds at the end of line 2:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*Render of the real output with `freeze`.*

`enj ⬡2/2 ⚙0 ≈$21 hoy 74.0M`: live nodes / total, running jobs, equivalent cost
and today's tokens across **all** machines. The bar only **reads** a file with `read`, a bash
builtin, so there is no `ssh` and no new processes: it is drawn on every turn and
cannot be slow. If the file is more than 15 min old it shows `enj ⌛20m` instead of stale numbers. If
it does not exist (a machine outside the swarm), it shows nothing.

```
$ bash tests/statusline_test.sh                       # Git Bash 5.2 (Windows)
54 OK, 0 fallas.
mac$ /bin/bash tests/statusline_test.sh               # bash 3.2 → salta a Homebrew bash 5.3
54 OK, 0 fallas.
mac$ /opt/homebrew/bin/bash tests/statusline_test.sh
54 OK, 0 fallas.
```

Before copying the bar, the previous version was backed up on both machines:
`~/.claude/statusline.sh.bak-<fecha>`.

### 7. Adding a node (the Ubuntu laptop, the Raspberry)

Add **one entry** to `nodes.toml`:

```toml
[nodes.laptop]
host = "laptop"          # alias de ~/.ssh/config
os = "linux"
arch = "x86_64"
ram_gb = 16
max_jobs = 1
workers = ["claude"]
tags = []
portfolio = "~/Documents/Portfolio"
# opcional: visor_snapshot = "~/.enjambre/visor/snapshot.json"
```

On the node, the collector every 5 min (on Linux with cron, no root):

```
$ mkdir -p ~/.enjambre/visor && scp <win>:.../tools/visor/collect.py ~/.enjambre/visor/
$ (crontab -l; echo '*/5 * * * * python3 ~/.enjambre/visor/collect.py --node laptop') | crontab -
```

The viewer, the comparison and the bar count it on their own; the page assigns colors in the order
of `nodes.toml`. A test with 3 nodes (`test_n_nodes_from_config_only`) guarantees it, including
the case of a node that is down.

## What actually went wrong

1. **Windows counted the Mac's models.** On Windows, `127.0.0.1:8765` answers because it is the
   SSH tunnel to the Mac's manager. The collector read it as if it were its own. Fix:
   querying the manager is optional (`--models`) and only the Mac's LaunchAgent enables it.
2. **A quota from the future.** The Mac reported "5 h 41 %, 7 d 87 %" with `ts = 1800000000` (year
   2027). It was a snapshot left by an old statusline test in `~/.claude/state/`.
   Fix: a reading with a future date is ignored. (The bar's current tests already write to
   a temporary directory, `CC_STATE_DIR`; the old file is still there and was not deleted: it is not from this session.)
3. **The Mac's disk showed up twice.** `/Volumes/Recovery` is another volume of the same APFS
   container and reports the same space as `/`. Fix: volumes with identical total and
   free are counted once.
4. **tmux "empty" from the LaunchAgent.** `launchd` starts with `PATH=/usr/bin:/bin:/usr/sbin:/sbin`,
   and the Homebrew tmux is not there. From the terminal it came out fine and from the agent it did not. Fix: also
   look in `/opt/homebrew/bin/tmux`.
5. **`freeze` hung** for two minutes while rendering the captures: with standard input
   open it waits for the keyboard. Fix: `freeze archivo.txt -o salida.png < /dev/null`.
6. **The Mac counted almost 19 % too much.** The first version deduplicated by `message.id`
   *within* each file. A resumed session copies old responses into a new transcript, and
   those counted twice. Measured over 8 days: Mac 18.71 % of tokens, Windows 0.12 %. Fix:
   the cursor stores one row per id and all files are joined. After the change the Mac went from
   2 906 801 to 2 362 969 tokens, exactly as measured.
7. **A fake snapshot created by accident.** For the capture, a fixture was passed to the bar
   (`session-long.json`) without `CC_STATE_DIR`. The bar wrote `~/.claude/state/00000000-….json` with
   the fixture's quota (41 % / 87 %), and for 30 min the viewer would have shown it as real. It is
   the same origin as the "future" file on the Mac (point 2). Fix: captures are rendered with
   `CC_STATE_DIR` pointing to a temporary folder.
8. **The bar's tests would have shown live numbers.** On a swarm machine the
   summary file exists, and all the "golden" files would have changed every 5 minutes. Fix: the test
   points `CC_ENJ_SUMMARY` to a nonexistent path by default, and the swarm cases set it on
   purpose.

One risk avoided by design: to know whether a PID is alive, on Linux/Mac `os.kill(pid, 0)` is used.
**On Windows signal 0 is Ctrl+C**, so that "test" would have cut the open
sessions. The collector uses `OpenProcess` + `GetExitCodeProcess`.

## How to verify

```
PS> python -m unittest tests.test_visor                  # 20 tests
mac$ /usr/bin/python3 -m unittest tests.test_visor       # 16 + 4 omitidos (build.py necesita 3.11)
PS> Get-ScheduledTaskInfo -TaskPath '\enjambre\' -TaskName visor | Select LastRunTime,LastTaskResult
PS> Get-Content state\visor\build.log -Tail 2            # una línea cada 5 min, push mac True
mac$ cat ~/.enjambre/visor/summary.txt                   # <epoch> TAB ⬡2/2 ...
```

And open `state/visor/visor.html`: each node's snapshot must say "ago" with less than 10 min.

## Self-check

1. Why is the viewer's cost a "floor" and not a bill? What data is missing from `models.json`?
2. If a session prints three blocks (thinking, text, tool), how many times does its
   `usage` appear in the transcript and how many must be counted?
3. Why does the local viewer carry the data inside the HTML instead of reading a JSON?
4. The Mac uploads its summary and Windows downloads it over SSH: which of the two starts the
   connection, and why does that matter for not opening ports?
5. In the comparison, which column says whether the Mac or Windows is the better fit: "Model (cloud)" or
   "Tools (local)"? Why?
6. What changes in the code to add the Raspberry to the viewer?
