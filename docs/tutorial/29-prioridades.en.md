---
lesson:
  route: build-stack
  order: 28
  level: 3
lang: en
---

# 29 — Priorities: let the urgent job through without killing what waits

## Goal

Every job carries a class (**alta > normal > baja > nocturna**; high > normal > low > night). A baja
job only starts when the machine is idle. If an alta job arrives, the baja job is **paused** (or
**evicted** if memory runs short and it has a checkpoint) and **resumes on its own** 60 s after the
alta job finishes. A nocturna job waits for the 00:00 to 07:00 window in Lima. At any moment the
viewer says what class each job has, what state it is in and **why**.
**Who did it:** Claude Code (session of 2026-10-10, prompt 20).
**Before:** [28 — Live viewer and telemetry](28-telemetria.md). The concept, in brief:
[conceptos/prioridades.md](../conceptos/prioridades.md).

## Why (0. The problem)

The Mac does everything: hour-long proyecto-externo renders, Landing test suites, pharmacy agents.
Until now, whoever arrived first kept the machine. A background render could delay by half an hour
a check the owner was waiting on. Killing the render by hand was no fix either: everything it had
done was lost. Three things were missing:

1. **A class per job** and a queue that respects it.
2. **A way to slow down** what is already running without losing it: pause (SIGSTOP) or, if it has
   a checkpoint, stop it cleanly and pick it up later.
3. **Making the reason visible**: a "waiting" with no reason is as opaque as before.

## Steps

The numbered sections 1 to 6 are the steps: rules, admission, system priority, preemption, external jobs and viewer controls.

## 1. The rules, in a single file

They all live in `config/scheduler.toml`. The owner edits that file, not the code:

```toml
[admision]
baja_requiere_sin_alta_normal = true   # baja/nocturna solo si no corre alta ni normal en el nodo
presion_memoria_max = "normal"         # macOS: presión de memoria máxima que todavía admite baja
mem_disponible_min_pct = 30            # Windows: % de memoria disponible mínimo
swap_creciendo_ventana_s = 120         # macOS: el swap no puede haber crecido en esta ventana
telemetria_max_edad_s = 120            # sin telemetría fresca, un baja espera (nunca adivina)

[nocturna]
desde = "00:00"
hasta = "07:00"
tz = "America/Lima"

[preempcion]
cpu_gpu_umbral_pct = 70
reanudar_tras_s_libre = 60             # histéresis
agentes = "entre_turnos"               # o "nunca"
preemptores = ["alta"]                 # normal solo bloquea la ADMISIÓN de un baja
```

The class is set with `--priority` in `run` or in `script`, with a `priority: baja` line in the
TASK, or afterwards with `enjambre prioridad <id> <clase>`. The default is `normal`.

## 2. Admission: who starts

`watch` (the queue) and `run --detach` / `script` (direct dispatch) ask the same question of
`scheduler.admit()`, and the answer always comes with a readable reason:

```
$ python enjambre.py script --node mac --repo enjambre-sandbox --cmd cpu-burn --priority nocturna
enjambre scheduler [nocturna] waiting_window: esperando la ventana nocturna: abre mañana a las 00:00 (America/Lima)
```

If it is not admitted, the job is **not lost**: it goes into `state/queue/<id>` with its state and
its reason, and `watch` launches it as soon as the reason no longer holds.

**Stumble:** the night window uses `zoneinfo`, but Windows ships without the time zone database
(`tzdata`). The fix was a fixed offset of −5 for America/Lima, which has no daylight saving time.
A zone that cannot be read leaves the window **closed** with the reason "ilegible" (unreadable):
opening it blindly would launch night jobs in the middle of the day.

### Test (c): a nocturna job queued at 21:36

```json
{
  "id": "cpu-burn-20261010-213655",
  "estado": "waiting_window",
  "prioridad": "nocturna",
  "motivo": "esperando la ventana nocturna: abre mañana a las 00:00 (America/Lima)",
  "motivo_desde": "2026-10-11T02:36:55+00:00"
}
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

After the screenshot, that job was moved to `_papelera_claude/`, because otherwise `watch` would
have launched it at 00:00 (DECISIONES 288).

## 3. Operating system priority

Besides the queue, the class is applied to the process itself. On the Mac, a baja job runs with
`taskpolicy -b nice -n 15`, inside `caffeinate` and in its own process group:

| Clase | macOS | Windows |
|---|---|---|
| alta / normal | por defecto | Normal |
| baja | `taskpolicy -b nice -n 15` | BelowNormal + EcoQoS |
| nocturna | `taskpolicy -b nice -n 19` | Idle + EcoQoS |

`taskpolicy -b` marks the process as *background*, and macOS keeps it on the efficiency cores.
You can see it in test (a): eight `yes` processes at baja used **41–48 %** of the Mac's CPU, close
to the 4 E cores out of 10, and left the P cores free.

**Stumble:** to pause a whole job, all its processes must share a group (`setsid`), and macOS
**does not ship a `setsid` binary**. `worker.sh` writes a small python helper (`setsid.py`) that
calls `os.setsid()`, saves the pgid and runs the command. The heredoc was called `PY` and broke a
test that extracted the first `PY` heredoc from the script; it is now called `SETSID`.

**Stumble on Windows:** EcoQoS (`SetProcessInformation(ProcessPowerThrottling)`) exists since
Windows 11. On this Windows 10 (build 19045) the call does not fail, but it does nothing. The class
priority is applied, and suspension with `NtSuspendProcess` was verified on a process of our own:
4 threads suspended and 0 after resuming.

## 4. Preemption: the `live --serve` tick

Every 10 s, `live` reads what is running on each node and `planner.py` asks `scheduler.plan()`
what to do:

- **There is an alta job and CPU or GPU is short (> 70 %)** → **pause** the baja job: SIGSTOP to
  its group.
- **There is an alta job and memory is short** → **evict** the baja job *if it has a checkpoint*
  (`--resumable`): it is stopped cleanly and left as `evicted_resumable`. If it has none, it is
  only paused, and the reason warns that pausing does not free its memory. **A job is never
  evicted without a checkpoint.**
- **60 s in a row with no alta job** → resume what the planner paused or evicted, and nothing else.

The PC does not apply the pause directly. The PC leaves the order in `control.json` and it is the
job's supervisor, on the Mac, that applies it. That way `status.json` keeps a single writer. After
the SIGSTOP, the supervisor checks that the whole group is in state `T`. Before any SIGTERM it
always sends SIGCONT, because a stopped process cannot die cleanly.

**Stumble:** the first version forgot the pause on the next tick. The node still said `running`
(it had not yet read the change), the planner concluded someone had resumed the job by hand and
erased it from its registry. Now a freshly applied pause gets 45 s of grace (`ECHO_GRACE_S`).

### Test (a): a 3 min baja job, an alta job at 40 s

The baja job is `cpu-burn-ck`, a new `script-cmd` in the `AGENTS.md` of enjambre-sandbox: 8 × `yes`
for 3 min, with a checkpoint every 10 s. It is timed by a driver that samples `live.json` every
5 s (excerpt):

```
21:57:26 t=5    LOW running baja        | baja: admitido (0/2 en el nodo)            cpu 16
21:57:55 t=34   LOW running baja 11 %                                                cpu 46
21:58:06 LAUNCH alta cpu-burn-20261010-215805 | alta: admitido (1/2 en el nodo)
21:58:21 t=60   LOW paused_by_priority baja 22 % | pausado por cpu-burn-20261010-215805 (alta)
                desde 21:58 (CPU 88 %); se reanuda al quedar libre 60 s           HI running alta
21:59:20 t=119  LOW paused_by_priority     || HI ok alta || cpu 41
22:00:10 t=169  pol: 1 baja pausados · libre hace 53 s (reanuda a los 60 s)
22:00:44 t=203  LOW running baja 33 % | reanudado: nodo libre de alta durante 64 s
22:02:39 t=318  LOW ok baja 100 %
```

The two process groups on the Mac, with the baja job paused:

```
== cpu-burn-ck-20261010-215720 pgid=49816 state=paused_by_priority
  PID NI PRI STAT  %CPU COMM
49816 15   4 TNs    0.0 /bin/bash
49817  0  31 T      0.0 caffeinate
49819 15   4 TN     0.0 yes
== cpu-burn-20261010-215805 pgid=50311 state=running
  PID NI PRI STAT  %CPU COMM
50311  0  31 Ss     0.0 /bin/bash
50313  0  31 R     98.3 yes
```

`T` = stopped. NI 15 / PRI 4 on the baja job versus NI 0 / PRI 31 (normal OS priority) on the
alta job. `caffeinate` sits outside the prefix on purpose: it only keeps the Mac from sleeping.

The ledger lines (`jobs.jsonl`), one per job, with the history folded in:

```json
{"id": "cpu-burn-20261010-215805", "node": "mac", "status": "ok", "priority": "alta", "resumable": false, "preemptions": null, "duration_s": 60}
{"id": "cpu-burn-ck-20261010-215720", "node": "mac", "status": "ok", "priority": "baja", "resumable": true,
 "preemptions": [{"state": "paused_by_priority", "reason": "pausado por cpu-burn-20261010-215805 (alta) desde 21:58 (CPU 88 %); se reanuda al quedar libre 60 s", "at": "2026-10-10T21:58:08-05:00"},
                 {"state": "running", "reason": "reanudado: nodo libre de alta durante 64 s", "at": "2026-10-10T22:00:18-05:00"}],
 "duration_s": 309}
```

And every planner action is recorded in `state/scheduler.jsonl`, with who did it (`planificador`,
`visor`, `cli`, `release_all`).

The four phases in the viewer:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## 5. External jobs: the proyecto-externo lab

The lab (`uvicorn externo.lab.app:app`, port 8800 on the Mac) has its own queue and its own pause
API: `POST /jobs/{id}/pause|resume`, which sends SIGSTOP/SIGCONT to the render. Enjambre does not
launch those renders, but it **adopts** them: `collect.py --lab` reads `GET /jobs`, and every
active render enters the planner as a baja job. The pause goes through the lab's API, called
**from the Mac itself** (`ssh mac curl http://127.0.0.1:8800/...`): there is no IP in the code, and
it works with the VPN on as long as `ssh mac` works. A lab render is **never evicted**, because it
has no checkpoint.

### Test (b): a real render, paused and resumed through its API

The lab **was really running**, so nothing had to be simulated. It was asked for a render through
its own API: `talk_10s.mp4` with the `meina_depth065` preset, the same one the lab takes about 4 min
to render. At ~10 s an alta job was launched (`cpu-burn`, 60 s). Driver excerpt: `LAB api` is what
the lab says about itself and `visor` is what `live.json` shows:

```
22:02:54 SUBMIT {"id":"363b069e"}
22:02:59 t=5    LAB api running 5.6  | visor running baja
22:03:09 LAUNCH alta cpu-burn-20261010-220308 | alta: admitido (0/2 en el nodo)
22:03:14 t=20   LAB api paused 17.3  | pausado por cpu-burn-20261010-220308 (alta) desde 22:03 (CPU 93 %);
                                       se reanuda al quedar libre 60 s
22:03:28 t=34   LAB api paused 17.3  | visor paused_by_priority baja
22:05:15 t=141  LAB api paused 17.3  | HI ok   (el render no avanzó mientras estuvo parado)
22:05:20 t=146  LAB api running 18.9 | reanudado: nodo libre de alta durante 63 s
```

`state/scheduler.jsonl`:

```json
{"action": "pause",  "key": "lab:363b069e", "source": "externo_lab", "at": "2026-10-11T03:03:11+00:00", "ok": true, "msg": "HTTP 200", "by": "planificador"}
{"action": "resume", "key": "lab:363b069e", "source": "externo_lab", "at": "2026-10-11T03:05:18+00:00", "ok": true, "msg": "HTTP 200", "by": "planificador",
 "reason": "reanudado: nodo libre de alta durante 63 s"}
```

The lab's `elapsed_s` stayed at 17.3 through the two minutes of pause: the render was truly
frozen, not just marked as paused. The lab does not write to the enjambre ledger (it is not an
enjambre job), so its history is in `scheduler.jsonl`.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## 6. Viewer controls (local copy only)

Every card has **pause / resume / evict** and a priority selector. Heavy processes that enjambre
did not launch show up under **Unmanaged**, with an **adopt** button. How it is protected:

- `live --serve` opens an endpoint on `127.0.0.1` with an ephemeral port and a **new token on
  every start**. Both go in `state/visor/ctl.js`, which only exists on the PC. The token does not
  appear in `live.json` and the web copy does not carry it: it is published from an allowlist,
  and a test checks that.
- The endpoint checks the `Host` header (against DNS rebinding), accepts only the allowlisted
  actions and validates the key with a regex. The body goes as `text/plain`, so the file:// page
  needs no CORS preflight. From the page it was verified that the call arrives (409 with a fake
  id).
- If the browser blocks it, the card shows the exact command to paste.

The same from the terminal (step 5):

```
python enjambre.py prioridad <id> alta|normal|baja|nocturna
python enjambre.py pausar <id>          # o lab:<id>, o pid:<nodo>:<pid>
python enjambre.py reanudar <id>
python enjambre.py desalojar <id>       # solo si tiene checkpoint
python enjambre.py adoptar mac 12345 --prio baja
```

With `live` running, the commands go through its endpoint, so that `state/scheduler.json` has a
single writer. Without `live`, they act directly. **A manual pause is never resumed by the
planner:** that is the owner's decision.

### Extra test: eviction, checkpoint, manual pause and the viewer click

Tests (a) and (b) only exercised the pause: memory never ran short. That is why eviction and the
manual controls were tested separately, for real. A baja `--resumable` `cpu-burn-ck`:

```
22:10:52 $ enjambre script ... --cmd cpu-burn-ck --priority baja --resumable    → attempt 1
22:11:36 $ enjambre desalojar cpu-burn-ck-20261010-221051                       → ok
22:11:48 live.json: "estado": "evicted_resumable", "pct": 22.0, "motivo": "desalojado a mano"
22:11:57 $ enjambre reanudar cpu-burn-ck-20261010-221051                        → attempt 2
         log del nodo: «resume from checkpoint 40 s»
22:12:26 $ enjambre pausar cpu-burn-ck-20261010-221051                          → pausado a mano
22:13:52 80 s con el nodo libre: sigue «pausado a mano» (el planificador no lo toca)
22:14:02 clic en «reanudar» en el visor (Playwright)  → «✓ ok» → running, «reanudado a mano»
22:15:56 ok, 100 %
```

A single ledger line, with both attempts and the whole history:

```json
{"id": "cpu-burn-ck-20261010-221051", "status": "ok", "priority": "baja", "resumable": true, "attempts": 2,
 "preemptions": [{"state": "evicted_resumable", "reason": "desalojado a mano", "at": "2026-10-10T22:11:37-05:00"},
                 {"state": "paused_by_priority", "reason": "pausado a mano", "at": "2026-10-10T22:12:27-05:00"},
                 {"state": "running", "reason": "reanudado a mano", "at": "2026-10-10T22:14:00-05:00"}],
 "duration_s": 300}
```

`state/scheduler.jsonl` says who did each thing:

```
03:11:36Z evict           cli    ok  desalojado a mano
03:11:57Z resume_evicted  cli    ok  reanudado a mano
03:12:26Z pause           cli    ok  pausado a mano
03:13:59Z resume          visor  ok  reanudado a mano
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

Actions on enjambre jobs are recorded in their ledger line (`preemptions`). Actions on lab renders
and adopted processes are recorded only in `scheduler.jsonl`, because they are not enjambre jobs
and have no ledger line (DECISIONES 285).

**Stumble:** when the first nocturna job was queued, the viewer's history table stopped rendering
(`RangeError: Invalid time value`). Queued jobs store `enqueued` as ISO text, and the table treated
it as seconds. It was an old bug that went unseen because the queue was almost always empty.

## 7. What is left for the owner

The three decisions of 20 §4 are in NEEDS-OWNER §24. They work today with the recommendation for
each and are changed in the toml: agents between turns or never, whether to evict a render with no
checkpoint, and local-only controls.

## 8. Wi-Fi vs Ethernet (prompt 19 §8, 2026-10-10)

**What changed:** the Mac now has an Ethernet cable (`en0`, 1000baseT full-duplex) in addition to
Wi-Fi (`en1`). The `mac` alias in `~/.ssh/config` went from the Wi-Fi IP to the cable IP. The Wi-Fi
IP stays commented out underneath as a fallback.

**Why it matters:** the whole swarm talks to the Mac through `ssh mac`: the `live` cycle every
10 s, `watch`, the bridge, the planner and the log copies. Every millisecond of the handshake is
paid on every round.

### What was on the Mac

```
mac$ ifconfig en0      → ether <mac-en0> · inet <ip-cable> · media: 1000baseT <full-duplex> · status: active
mac$ ifconfig en1      → ether <mac-wifi-privada> · inet <ip-wifi> · status: active
mac$ route -n get default
    gateway: <router>
  interface: en0
mac$ networksetup -listnetworkserviceorder
(1) Ethernet        (Hardware Port: Ethernet, Device: en0)
(2) Thunderbolt Bridge
(3) Wi-Fi           (Hardware Port: Wi-Fi, Device: en1)
```

Ethernet was already first in the service order and the default route already went out through
`en0`. There was no need for `networksetup -ordernetworkservices` or sudo.

### How the alias was changed without blindly accepting a key

The new IP was not in `known_hosts`. Before adding it, its fingerprint was compared with the one
already known for the Wi-Fi IP:

```
$ ssh-keyscan -t ed25519 <ip-cable> | ssh-keygen -lf -     → SHA256:IoH2…BFO4
$ ssh-keygen -F <ip-wifi> (la entrada conocida) | ssh-keygen -lf -   → SHA256:IoH2…BFO4
```

They are the same machine, so the line was added and `HostName` was changed in both `Host mac`
blocks. There were two duplicate blocks, and ssh takes the **first** value it finds for each
option. The previous config was kept as `~/.ssh/config.bak-20261010-wifi`.

**Stumble avoided:** the `mac6` alias (the VPN one, NEEDS-OWNER §15) used the link-local IPv6
**of the Wi-Fi** (`en1`). With Wi-Fi off it would have stopped answering just when it is needed.
It now uses the link-local of `en0`; the `%4` is the index of the PC's network card and does not
change. Verified: `ssh mac6` → `…%en0`.

There was no fixed IP anywhere in the code: `nodes.toml` uses `host = "mac"`, `smb-mount.sh` and
the bridge go through the alias, and the proyecto-externo lab listens on `0.0.0.0:8800` and
enjambre talks to it from the Mac itself (127.0.0.1). The old IP only appears in historical
documents and in logs of old runs. `routing.toml` has none either (checked in prompt 21 §1). To
keep it that way, `tests/test_no_fixed_ips.py` fails if a LAN address shows up in the config, the
scripts or the code: the only address lives in the router's DHCP reservation and in
`~/.ssh/config`.

### The numbers

`ssh mac true`, 10 times, timed from the PC:

| Ruta | Mediana | Mín | Máx |
|---|---|---|---|
| Wi-Fi (IP de `en1`) | **266 ms** | 244 | 395 |
| Ethernet (IP de `en0`), mismo momento | 216 ms | 209 | 251 |
| Ethernet por el alias ya cambiado | **187 ms** | 166 | 210 |

`tools/smb-bench.sh 2` (2 GiB; SMB skipped because `Compartido-Mac` is not mounted on the Mac,
scp only):

| Corrida | Win → Mac | Mac → Win |
|---|---|---|
| 2026-10-05, solo Wi-Fi (`en1`), 5 GiB | 53,4 MB/s | 66,7 MB/s |
| 2026-10-10, a la IP del Wi-Fi con el cable ya puesto | 48,7 MB/s | 69,6 MB/s |
| 2026-10-10, por Ethernet | **64,5 MB/s** | **100,7 MB/s** |

The middle run is **mixed**: packets to the Wi-Fi IP come in through `en1`, but the Mac answers
through its primary interface (`en0`). That is why the bench already recorded `mac_iface: en0`.
The clean comparison is the first row against the last: **+21 % upload and +51 % download**, with
the ssh handshake 30 % faster. scp encrypts, so these numbers are a floor; the gigabit ceiling is
near 110 MB/s.

Landing jobs were running on the Mac during all three measurements. That is why 2 GiB was used
and not the default 5.

**Next:** [30 — Unattended startup](30-arranque.md).

## What actually went wrong

Real failures from this practice, each with its fix in the section indicated:

- Windows ships without `tzdata`: the night window could not read America/Lima. Fix: a fixed offset of −5 and a closed window if the zone is unreadable (§2).
- The viewer's history table stopped rendering with `RangeError: Invalid time value`: `enqueued` is ISO text and was treated as seconds (§5).
- macOS ships without the `setsid` binary; the `PY` heredoc broke a test. Fix: `setsid.py` helper and `SETSID` heredoc (§3).
- EcoQoS does nothing on Windows 10 (build 19045); the class priority is applied (§3).
- The first version forgot the pause on the next tick. Fix: 45 s of grace, `ECHO_GRACE_S` (§4).

## How to verify

With the outputs already shown above:

1. Queue a nocturna job outside the window: `python enjambre.py script --node mac --repo enjambre-sandbox --cmd cpu-burn --priority nocturna`. Expected: `enjambre scheduler [nocturna] waiting_window: esperando la ventana nocturna: abre mañana a las 00:00 (America/Lima)`.
2. Launch a baja job (`cpu-burn-ck`) and, at 40 s, an alta job. Expected in `live.json`: `paused_by_priority`, with the reason "pausado por … (alta)".
3. With the baja job paused, `ps` on the Mac must show the baja job in state `T` with NI 15 / PRI 4, and the alta job with NI 0 / PRI 31.
4. After 60 s with no alta job, the baja job goes to `running` with "reanudado: nodo libre de alta durante 64 s" and ends `ok`.
5. In `jobs.jsonl`, the baja job's line carries `preemptions` with the two state changes.

## Self-check

1. When is a baja job evicted instead of paused?
   Answer: when memory is short and it has a checkpoint (`--resumable`). Without a checkpoint it is only paused; it is never evicted.
2. Who resumes a job that was paused by hand?
   Answer: nobody on its own. The planner does not touch it; the owner resumes it (`enjambre reanudar` or the viewer button).
3. Why does the pause go through `control.json` and not through the PC directly?
   Answer: so that `status.json` has a single writer; the job's supervisor, on the Mac, applies the order.
