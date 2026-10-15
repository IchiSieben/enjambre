---
lesson:
  route: build-stack
  order: 27
  level: 3
---

# 28 — Visor en vivo y telemetría por nodo: qué corre ahora y cómo respira cada máquina

## Objetivo

**Qué consigues:** abrir el visor y ver, sin hacer scroll, lo que está corriendo **ahora** (trabajos
del enjambre primero, en verde, con el turno avanzando) y, con un botón, el detalle de cada
máquina: CPU, GPU, RAM con presión y swap, térmico, red, la gráfica de la última hora y qué
procesos se la están comiendo, etiquetados con el trabajo o la sesión de enjambre a la que
pertenecen.
**Quién lo hace:** Claude Code (sesión del 2026-10-10, prompts 18 paso 1 y 19 pasos 1–3).
**Antes:** [27 — Visor v3](27-visor-v3.md). Los trabajos largos (`run --detach`) son del
[tutorial 25](25-trabajos-largos.md) y el puente, del [26](26-puente.md).

## Por qué

El visor decía «en vivo · hace 5 min» pero no mostraba lo que corría en ese momento. Las tres causas están en el §0.

## 0. El problema

El visor decía «en vivo · hace 5 min», pero no mostraba lo que corría en ese momento. Tenía tres
causas, y las tres se comprobaron en el código antes de tocar nada:

1. **«0 en cola» con trabajos corriendo.** El KPI contaba `state/running/` (la cola de `watch`) y
   los modelos locales. Un `run --detach` no pasa por esa cola: deja `state/jobs/<id>/job.json`
   en el PC al despachar y escribe **una sola línea** en `jobs.jsonl`, al recogerlo. Mientras
   corre, nada lo contaba. Así pasó con c1–c3 de farmacias el 09: están en `state/jobs/` y en
   el ledger, pero solo desde que se recogieron.
2. **«hace 5 min».** La tarea `\enjambre\visor` ejecuta `build.py` cada 5 min y mete los datos
   dentro del HTML. «En vivo» significaba «recargar cuando haya una instantánea nueva», o sea,
   cada 5 min como mucho.
3. **Las sesiones que trabajaban salían al final.** `collect_sessions()` ordenaba por
   `started` (antigüedad), no por estado ni por última actividad.

## 1. Lo que devuelve cada máquina (medido, no supuesto)

Esto es la salida cruda que se usó para escribir los parsers y los tests
(`tests/test_telemetria.py` usa estos mismos textos). Se quitó el usuario de macOS
(`<usuario>`).

### 1.1 Mac mini (macOS, Apple Silicon), 2026-10-10 20:2x

`ssh mac true` tarda **0,28 s**.

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

Qué se aprendió de esto:
- **La presión de memoria se lee directamente**: `kern.memorystatus_vm_pressure_level` vale 1, 2
  o 4 (normal, aviso, crítica). No hace falta deducirla del «% libre». En una Mac importa más la
  presión que la memoria libre, porque macOS usa toda la RAM a propósito.
- **El límite térmico no aparece** hasta que macOS registra un evento: `pmset -g therm` no
  imprime `CPU_Speed_Limit`. El valor honesto es `null` (desconocido), no 100. Lo que sí se sabe
  es que «no hay aviso térmico registrado».
- **La GPU estaba al 98 %** y en `ps` no había nada evidente: era el render de proyecto-externo. El
  `pgid` del Python (73413) es el pid del panel `cohbench` del tmux de enjambre. Ese cruce es el
  que etiqueta el proceso.
- `top -l 2` tarda 1,3 s. En su lugar, la CPU se lee de los contadores del kernel con ctypes
  (`host_processor_info`), sin subproceso y sin sudo.
- `ps -c` da el nombre corto, pero `sshd-session: <usuario>@notty` incluye el nombre de usuario.
  El colector se queda solo con lo que va antes de `: `.

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

Qué se aprendió:
- **`wmic` para procesos tarda 8,5 s**: no sirve cada 10 s. Se cambió por una sola llamada a
  `NtQuerySystemInformation` (ctypes), que lista todos los procesos con su tiempo de CPU y su
  memoria. Se hacen dos lecturas separadas por 200 ms y se calcula la diferencia.
- La CPU total se calcula con `GetSystemTimes` (ctypes). Se descartó `wmic cpu` (1,2 s).
- La GPU que importa es la NVIDIA (`nvidia-smi`, 59–139 ms). Se lanza al empezar y se lee al
  final, así que su tiempo se solapa con la muestra de CPU. La Intel UHD no se mide: sale «n/d».
- La temperatura: `Not supported`. Sale «n/d». LibreHardwareMonitor queda en el ROADMAP.

### 1.3 Lo que tarda el colector nuevo

| | Dentro del proceso | Con ssh |
|---|---|---|
| PC, `collect.py --telemetria` | **264–267 ms** (objetivo < 300) | — |
| Mac, `collect.py --telemetria` | **71–80 ms** | **0,52–0,88 s** (objetivo < 1,5 s) |
| Mac, `collect.py --live` (sesiones + trabajos + tmux + telemetría) | 117 ms | 0,79–1,06 s |

## 2. Cómo está hecho

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

- **Por qué `live.js` y no `fetch('live.json')`:** una página `file://` no puede hacer `fetch()`
  de un archivo vecino (Chromium lo bloquea), pero sí cargarlo como script. Es el mismo truco
  que ya usaba `visor-stamp.js`.
- **Por qué una sola ssh y sin `ControlMaster`:** el OpenSSH de Windows no admite bien la
  multiplexación, y `ssh mac true` ya cuesta solo 0,28 s. Si un nodo tarda más de 2 s, pasa a
  leerse cada 60 s y el Detalle lo indica.
- **Latido cada 10 s** (antes 60): `run --detach` y `script` reescriben `status.json` en la Mac
  con el turno, la última herramienta y el coste estimado. El visor lo muestra ~10 s después.
- **Estados:** sesión `running` (Claude Code dice `busy`, o su statusline cambió hace < 60 s, o
  pidió un trabajo que sigue corriendo), `waiting` y `idle` (> 30 min, plegada). Trabajo
  `launching` (despachado, sin latido todavía), `running`, `stalled` (latido parado > 5 min,
  ámbar) y `dead` (el supervisor ya no existe: **nunca sale en verde**).
- **Etiquetas de procesos:** cada proceso hereda la etiqueta de su grupo o del ancestro más
  cercano que enjambre conoce: `trabajo: <id>` (pid, pgid y supervisor de `status.json`),
  `tmux enjambre: <sesión>` (panes del tmux de enjambre) o `sesión: <nombre>` (ventanas de
  Claude Code). El propio lector sale como `enjambre: telemetría`.
- **Anillo:** cada nodo guarda `~/.enjambre/visor/telemetria.ring.jsonl` (1 440 líneas, 4 h a
  10 s; se recorta solo). El PC guarda una copia en `state/visor/ring-<nodo>.jsonl` y, al
  arrancar, trae el del nodo una vez para que las gráficas no empiecen vacías.

## Pasos

Los pasos son el §3 (ponerlo en marcha), el §7 (reposo) y el §8 (`nodo add`); el §4 es la evidencia.

## 3. Ponerlo en marcha

```powershell
powershell -File tools\install-live-task.ps1      # tarea \enjambre\live, arranca ya
python enjambre.py live --print                    # un ciclo, imprime live.json entero
python enjambre.py live --stop                     # para el bucle por su pid
python enjambre.py visor                           # abre el visor
```

Si `live.json` lleva más de 90 s sin actualizarse, la cabecera muestra el aviso rojo **«visor
dormido»**. La copia web nunca lo muestra, porque no tiene `live.js`.

## 4. Criterios de aceptación (con la evidencia)

### a) Un `run --detach` trivial a la Mac sale arriba, en verde y con el turno avanzando, en < 30 s

```
python enjambre.py run --detach --node mac --repo enjambre-sandbox --task tasks/TASK-live-probe2.md --model haiku --budget 0.5 --max-turns 30
```

Muestreo de `state/visor/live.json` cada 2 s (`t0` = 20:45:29; despacho: 20:45:30):

```
+  4.0s live@20:45:22 job=None                                   pos_en_corriendo=None/4
+  6.0s live@20:45:33 job=live-probe2 estado=running turno=0  latido=20:45:33 pos_en_corriendo=1/6
+ 18.0s live@20:45:44 job=live-probe2 estado=running turno=5  latido=20:45:43 pos_en_corriendo=1/6
+ 26.1s live@20:45:54 job=live-probe2 estado=running turno=12 latido=20:45:53 pos_en_corriendo=1/6
+ 38.1s live@20:46:05 job=live-probe2 estado=ok      turno=18 latido=20:46:00
```

Aparece **3 s** después del despacho, primero de 6 y en verde; el turno avanza 0 → 5 → 12 → 18.
**Cumplido.**

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Lo que falló por el camino: la primera sonda (7 pasos) terminó en 13 s, antes del segundo latido,
así que no se veía avanzar el turno (pasó de 0 a «terminó ok 8»). Por eso la segunda tiene 16
pasos. Además, en «Corriendo» el trabajo salía 2.º o 3.º, porque las ventanas de Claude se mueven
más a menudo. Ahora van primero los trabajos del enjambre y después las sesiones. Y el propio
Claude del trabajo aparecía como una sesión más (`enjambre-sandbox-live-probe2-dc`): ahora se
pliega en la tarjeta del trabajo.

### b) El render de proyecto-externo sale etiquetado como del enjambre

```
live.json -> telemetria.mac.top:
{'pid': 84469, 'name': 'Python', 'cpu': 80.2, 'rss_gb': 0.39, 'tag': 'tmux enjambre: cohbench'}
{'pid': 84574, 'name': 'ffmpeg', 'cpu': 13.0, 'tag': 'tmux enjambre: cohbench'}
{'pid': 43632, 'name': 'claude', 'cpu': 14.3, 'rss_gb': 0.23, 'tag': 'sesión: proyecto-externo-5b'}
```

**Cumplido, con un matiz:** el render (`coherence_bench.sh` → `python -m externo.cli bench …`) no
se lanzó con `run --detach`, sino dentro de la sesión `cohbench` del tmux de enjambre
(`tmux-run.sh`). Por eso no tiene id en el ledger, y la etiqueta es `tmux enjambre: cohbench`,
no `trabajo: <id>`. La sesión de Claude que lo dirige se etiqueta aparte
(`sesión: proyecto-externo-5b`).

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

### c) En el Detalle, la CPU de la Mac sube en la gráfica durante un trabajo pesado de 60 s

`enjambre-sandbox/AGENTS.md` declara `script-cmd cpu-burn` (8 × `yes` durante 60 s; un solo `yes`
sería un +10 % difícil de ver sobre la carga del render):

```
python enjambre.py script --repo enjambre-sandbox --cmd cpu-burn --node mac      (20:46:38)
+ 12.1s mac_cpu=98 top=[('yes', 98.6, 'trabajo: cpu-burn-20261010-204638'), ...]
+ 32.1s mac_cpu=87
+ 62.1s mac_cpu=86
+ 72.1s estado=ok mac_cpu=50
+ 92.1s mac_cpu=9
```

**Cumplido.** La gráfica pasa de ~13 % a 88–98 % y los 8 `yes` aparecen como
`trabajo: cpu-burn-…`.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## 5. Lo que no se mide (y dice «n/d»)

- Temperatura en grados en la Mac: necesita `sudo powermetrics`. La decisión es del owner
  (NEEDS-OWNER §23).
- Temperatura en el PC: `MSAcpi_ThermalZoneTemperature` → `Not supported`.
- GPU Intel del PC y VRAM de la Mac (memoria unificada): «n/d». Disco I/O y red del PC ya se
  miden desde el prompt 22 ([31](31-pulido-visor.md)).
- `%CPU` de un proceso en macOS es la media móvil de `ps` (~1 min). El total de la máquina sale de
  los contadores del kernel en la ventana del ciclo, así que pueden no cuadrar en un instante.

## Qué salió mal de verdad

Los errores del §6, más los de la sonda corta y el orden de «Corriendo» (§4 a) y los de `nodo add` (final del §8).

## 6. Errores que aparecieron (para que nadie los repita)

- `pmset -g thermlog` **no termina nunca**: transmite eventos. Dentro de una ssh con `| head`, el
  proceso remoto siguió vivo. Se mató por su PID (81317). Usa `pmset -g therm`.
- `claude auth status` **por ssh** dice `loggedIn: false`, y desde tmux (`tmux-run.sh`) dice
  `true`. El llavero de macOS solo se abre en el dominio gráfico. Comprueba el login desde tmux,
  que es donde corren los trabajos.
- `--nodo`, `--modelo`, `--tope-usd` y `--max-turnos` no existen. Los flags reales son `--node`,
  `--model`, `--budget` y `--max-turns`, y además hacen falta `--repo` y `--task`.

## 7. Reposo: cuándo dejar descansar una máquina (paso 4 de 19)

**Objetivo:** que `run` no mande trabajo a una máquina sin memoria, y que el visor diga por qué.

**Por qué:** un semáforo rojo no explica nada. Aquí son tres reglas visibles, con sus números en
`~/.claude/rules/thresholds.json` (`visor-reposo-*`), que se evalúan sobre el anillo de
telemetría:

| Regla | Se cumple cuando | Efecto |
|---|---|---|
| sin memoria | presión ≠ `normal` durante ≥ 5 min seguidos | no se despacha ahí: `run` encola o elige otro nodo; `watch` deja ese nodo con `max_jobs` 0 |
| estrangulando | `CPU_Speed_Limit` < 100 durante ≥ 2 min | solo aviso |
| paginando | swap crece ≥ 1 GB en 10 min | solo aviso |

«Seguidos» quiere decir sin huecos: si el bucle estuvo apagado más de 60 s, o una lectura salió
normal, la cuenta vuelve a cero. Una presión que no se pudo leer no cuenta como presión. Si
`live.json` tiene más de 2 minutos, `routing` lo ignora, para que un bucle caído no bloquee nada.

**Comprobación con un caso preparado.** Mientras se probaba, la Mac no tenía presión real, así
que se reemplazó `live.json` por un caso preparado: 6 minutos en `warn`, calculados por
`live.reposo_rules`. Con eso se lanzó un `run --dry-run` real contra los nodos reales
(`dryrun_reposo.py` en el scratchpad de la sesión):

```text
enjambre route [rule] queue: claude: every apt node is busy; queued for mac (busy, reposo: sin
memoria: presión warn desde hace 6 min: no despachar más aquí (since …)) (win: fits, but has no
executor yet (runnable = false))
```

El criterio de §5 pide que «`run` elige el otro nodo y lo dice». Con dos nodos eso no puede
pasar: el PC tiene `runnable = false`, así que lo honesto es encolar y decir por qué, que es lo
que sale arriba. Lo de «elige el otro nodo» se prueba con tres nodos en
`tests/test_reposo.py` (`Routing.test_pressure_sends_the_job_to_the_other_node_and_says_why`):
`run`, `laptop`, `diverted_from: mac`, «reposo: sin memoria…». `Decide.test_decide_reads_live_json`
pasa por el `decide()` de verdad.

Con `--node mac` explícito avisa, pero despacha:

```text
enjambre: warning, mac is in reposo: sin memoria: presión warn desde hace 6 min: no despachar más
aquí (since …); dispatching anyway (--node)
"route_reason": "--node mac; warned: reposo: sin memoria: …"
```

Sin el caso preparado (el estado real): `claude -> mac: first free node of prefer ['mac', 'win']`.

En el visor, Detalle → bloque **Reposo** muestra la regla que se disparó, desde cuándo, y las
tres reglas con sus números. Si no se puede leer `thresholds.json`, dice «⚠ umbrales» en lugar de
inventar un veredicto.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## 8. Añadir un nodo: `enjambre nodo add` (paso 5 de 19)

**Objetivo:** meter una máquina en el enjambre con un solo comando, sin editar `nodes.toml` a
mano ni copiar el colector a mano.

**Por qué:** hasta ahora había que escribir la tabla en `nodes.toml`, copiar `collect.py`,
instalar el LaunchAgent y comprobar a ojo. Cada uno de esos pasos podía fallar sin avisar. Ahora
son cuatro pasos con nombre, y si uno falla dice cuál.

```text
enjambre nodo add <nombre> --host <alias-ssh> --os macos [--label "…"] [--runnable]
enjambre nodo doctor <nombre>     # repite las comprobaciones sin escribir nada
enjambre nodo rm <nombre>         # deshace lo que hizo add (y nada más)
```

Prueba real contra la Mac, con un nombre desechable para no tocar el nodo `mac`:

```text
$ python enjambre.py nodo add macprueba --host mac --os macos --label "Mac (prueba)"
1 ok  medido: {'ram_gb': 16, 'cores': 10, 'arch': 'arm64'}
1 ok  nodes.toml: [nodes.macprueba] añadido
2 ok  collect.py 5db5d3d833be
3 ok  state = not running · last exit code = 0
4 ok  73981 bytes, esquema ok
```

`build.py` lo mostró en el visor sin tocar el HTML: tres tarjetas, con «Mac (prueba)» y su
instantánea de hace 9 s. La telemetría sale «n/d» y la presencia «unknown» porque `live` y `watch`
leen `nodes.toml` al arrancar; `add` lo avisa.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

```text
$ python enjambre.py nodo rm macprueba
nodo: dev.enjambre.visor.macprueba descargado y borrado, con su snapshot y su log (collect.py se queda: lo comparten)
nodes.toml: [nodes.macprueba] quitado
$ sha256sum nodes.toml   → a82d70d7… (el mismo que antes del add; git diff vacío)
mac$ launchctl print gui/$(id -u)/dev.enjambre.visor.macprueba → no cargado; 0 archivos «macprueba»
```

**Errores que aparecieron (y no se veían con la ssh falsa de los tests):**
- El primer intento falló en el paso 2: «collect.py en el nodo es otra versión». En Windows,
  `subprocess` en modo texto convierte `\n` en `\r\n` al escribir en stdin, así que el archivo
  llegaba con CRLF. Ahora se envía en bytes. El paso 2 compara sha256, y por eso lo detectó.
- El segundo intento falló en el paso 3: «no está cargado», pero sí estaba cargado (`last exit
  code = 0`). Este macOS no imprime «run interval» en `launchctl print`. Ahora se comprueba
  `state =`, y `launchctl print` solo sale con 0 si el servicio está cargado.
- Entre los dos intentos, `nodo rm` dejó `nodes.toml` idéntico. Es la prueba de que deshacer
  funciona incluso a medias.

## Cómo verificar

```powershell
python enjambre.py live --print          # un ciclo: imprime live.json entero
python -m unittest tests.test_live tests.test_telemetria    # 26 tests (según HANDOFF)
python enjambre.py visor                 # abre el visor -> Detalle en una tarjeta de nodo
```

Resultados esperados, ya obtenidos arriba: en §4 a) el trabajo aparece en < 30 s, en verde y con el turno
avanzando (0 → 5 → 12 → 18); en §4 c) la CPU de la Mac pasa de ~13 % a 88–98 %; en §8 `nodo add` termina con los
cuatro pasos en `ok` y `nodo rm` deja `nodes.toml` idéntico. Pendiente: el reposo se probó con un caso preparado, no
con presión de memoria real.

## Autoevaluación

1. ¿Por qué el visor carga `live.js` y no hace `fetch('live.json')`?
   Respuesta: una página `file://` no puede hacer `fetch()` de un archivo vecino (Chromium lo bloquea), pero sí cargarlo como script.
2. ¿Cuándo una máquina deja de recibir trabajo por «sin memoria»?
   Respuesta: cuando la presión de memoria es distinta de `normal` durante al menos 5 minutos seguidos (sin huecos); entonces `run` encola o elige otro nodo.
3. ¿Por qué un trabajo `dead` nunca sale en verde?
   Respuesta: `dead` significa que el supervisor ya no existe; así lo define el §2 («nunca sale en verde»). `stalled` (latido parado > 5 min) va en ámbar.

> **Cable Ethernet (19 §8):** las medidas Wi-Fi vs Ethernet están en
> [29 — §8](29-prioridades.md#8-wi-fi-vs-ethernet-prompt-19-8-2026-10-10).
