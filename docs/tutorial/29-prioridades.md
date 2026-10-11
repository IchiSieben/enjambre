# 29 — Prioridades: que lo urgente pase delante sin matar lo que espera

**Qué consigues:** cada trabajo lleva una clase (**alta > normal > baja > nocturna**). Un baja
solo arranca cuando la máquina está libre. Si llega un alta, el baja se **pausa** (o se
**desaloja** si falta memoria y tiene checkpoint) y se **reanuda solo** 60 s después de que el alta
termine. Un nocturna espera a la ventana de 00:00 a 07:00 en Lima. El visor dice en cada momento
qué clase tiene cada trabajo, en qué estado está y **por qué**.
**Quién lo hace:** Claude Code (sesión del 2026-10-10, prompt 20).
**Antes:** [28 — Visor en vivo y telemetría](28-telemetria.md). El concepto, en corto:
[conceptos/prioridades.md](../conceptos/prioridades.md).

## 0. El problema

La Mac hace de todo: renders de lab-video de una hora, suites de tests de Landing, agentes de
farmacias. Hasta ahora, el primero que llegaba se quedaba la máquina. Un render de fondo podía
retrasar media hora una verificación que el owner estaba esperando. Matar el render a mano
tampoco era solución: se perdía todo lo que llevaba hecho. Faltaban tres cosas:

1. **Una clase por trabajo** y una cola que la respete.
2. **Una forma de frenar** lo que ya corre sin perderlo: pausar (SIGSTOP) o, si tiene checkpoint,
   detenerlo limpio y retomarlo después.
3. **Que se vea por qué**: «esperando» sin motivo es igual de opaco que antes.

## 1. Las reglas, en un archivo

Todas viven en `config/scheduler.toml`. El owner edita ese archivo, no el código:

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

La clase se da con `--priority` en `run` o en `script`, con una línea `priority: baja` en el TASK,
o después con `enjambre prioridad <id> <clase>`. Por defecto es `normal`.

## 2. Admisión: quién arranca

`watch` (la cola) y `run --detach` / `script` (el despacho directo) preguntan lo mismo a
`scheduler.admit()`, y la respuesta trae siempre un motivo legible:

```
$ python enjambre.py script --node mac --repo enjambre-sandbox --cmd cpu-burn --priority nocturna
enjambre scheduler [nocturna] waiting_window: esperando la ventana nocturna: abre mañana a las 00:00 (America/Lima)
```

Si no lo admite, el trabajo **no se pierde**: entra en `state/queue/<id>` con su estado y su
motivo, y `watch` lo lanza en cuanto el motivo deja de valer.

**Tropiezo:** la ventana nocturna usa `zoneinfo`, pero Windows no trae la base de zonas horarias
(`tzdata`). La solución fue un desfase fijo de −5 para America/Lima, que no tiene horario de
verano. Una zona que no se puede leer deja la ventana **cerrada** con el motivo «ilegible»: abrirla
a ciegas lanzaría trabajos nocturnos en pleno día.

### Prueba (c): un nocturna encolado a las 21:36

```json
{
  "id": "cpu-burn-20261010-213655",
  "estado": "waiting_window",
  "prioridad": "nocturna",
  "motivo": "esperando la ventana nocturna: abre mañana a las 00:00 (America/Lima)",
  "motivo_desde": "2026-10-11T02:36:55+00:00"
}
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Después de la captura, ese trabajo se movió a `_papelera_claude/`, porque si no `watch` lo habría
lanzado a las 00:00 (DECISIONES 288).

## 3. La prioridad del sistema operativo

Además de la cola, la clase se aplica al proceso. En la Mac, un baja corre con
`taskpolicy -b nice -n 15`, dentro de `caffeinate` y en su propio grupo de procesos:

| Clase | macOS | Windows |
|---|---|---|
| alta / normal | por defecto | Normal |
| baja | `taskpolicy -b nice -n 15` | BelowNormal + EcoQoS |
| nocturna | `taskpolicy -b nice -n 19` | Idle + EcoQoS |

`taskpolicy -b` marca el proceso como *background*, y macOS lo deja en los núcleos de eficiencia.
Se ve en la prueba (a): ocho `yes` en baja usaron el **41–48 %** de la CPU de la Mac, cerca de los
4 núcleos E de 10, y dejaron libres los núcleos P.

**Tropiezo:** para pausar un trabajo entero hace falta que todos sus procesos compartan un grupo
(`setsid`), y macOS **no trae el binario `setsid`**. `worker.sh` escribe un pequeño helper en
python (`setsid.py`) que hace `os.setsid()`, guarda el pgid y ejecuta el comando. El heredoc se
llamaba `PY` y rompió un test que extraía el primer heredoc `PY` del script; ahora se llama
`SETSID`.

**Tropiezo en Windows:** EcoQoS (`SetProcessInformation(ProcessPowerThrottling)`) existe desde
Windows 11. En este Windows 10 (build 19045) la llamada no falla, pero no hace nada. La prioridad
de clase sí se aplica, y la suspensión con `NtSuspendProcess` se verificó sobre un proceso propio:
4 hilos suspendidos y 0 tras reanudar.

## 4. Preempción: el tick de `live --serve`

Cada 10 s, `live` lee qué corre en cada nodo y `planner.py` le pregunta a `scheduler.plan()`
qué hacer:

- **Hay un alta y falta CPU o GPU (> 70 %)** → **pausar** el baja: SIGSTOP a su grupo.
- **Hay un alta y falta memoria** → **desalojar** el baja *si tiene checkpoint* (`--resumable`):
  se detiene limpio y queda `evicted_resumable`. Si no lo tiene, solo se pausa, y el motivo avisa
  de que pausar no libera su memoria. **Nunca se desaloja sin checkpoint.**
- **60 s seguidos sin alta** → reanudar lo que el planificador pausó o desalojó, y nada más.

La pausa no la da el PC directamente. El PC deja la orden en `control.json` y es el supervisor
del trabajo, en la Mac, quien la aplica. Así `status.json` sigue teniendo un único escritor. Tras
el SIGSTOP, el supervisor comprueba que todo el grupo esté en estado `T`. Antes de cualquier
SIGTERM manda siempre SIGCONT, porque un proceso parado no puede morir limpio.

**Tropiezo:** la primera versión olvidaba la pausa en el tick siguiente. El nodo seguía diciendo
`running` (todavía no había leído el cambio), el planificador entendía que alguien lo había
reanudado a mano y lo borraba de su registro. Ahora una pausa recién dada tiene 45 s de gracia
(`ECHO_GRACE_S`).

### Prueba (a): baja de 3 min, alta a los 40 s

El baja es `cpu-burn-ck`, un `script-cmd` nuevo en el `AGENTS.md` de enjambre-sandbox: 8 × `yes`
durante 3 min, con un checkpoint cada 10 s. Lo cuenta un driver que muestrea `live.json` cada 5 s
(extracto):

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

Los dos grupos de procesos en la Mac, con el baja pausado:

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

`T` = parado. NI 15 / PRI 4 en el baja frente a NI 0 / PRI 31 (prioridad normal de SO) en el
alta. `caffeinate` va fuera del prefijo a propósito: solo evita que la Mac duerma.

Las líneas del ledger (`jobs.jsonl`), una por trabajo, con el historial plegado:

```json
{"id": "cpu-burn-20261010-215805", "node": "mac", "status": "ok", "priority": "alta", "resumable": false, "preemptions": null, "duration_s": 60}
{"id": "cpu-burn-ck-20261010-215720", "node": "mac", "status": "ok", "priority": "baja", "resumable": true,
 "preemptions": [{"state": "paused_by_priority", "reason": "pausado por cpu-burn-20261010-215805 (alta) desde 21:58 (CPU 88 %); se reanuda al quedar libre 60 s", "at": "2026-10-10T21:58:08-05:00"},
                 {"state": "running", "reason": "reanudado: nodo libre de alta durante 64 s", "at": "2026-10-10T22:00:18-05:00"}],
 "duration_s": 309}
```

Y cada acción del planificador queda en `state/scheduler.jsonl`, con quién la hizo (`planificador`,
`visor`, `cli`, `release_all`).

Las cuatro fases en el visor:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## 5. Externos: el lab de lab-video

El lab (`uvicorn lab-video.lab.app:app`, puerto 8800 en la Mac) tiene su propia cola y su propia API
de pausa: `POST /jobs/{id}/pause|resume`, que hace SIGSTOP/SIGCONT al render. Enjambre no lanza
esos renders, pero los **adopta**: `collect.py --lab` lee `GET /jobs`, y cada render activo entra
en el planificador como baja. La pausa va por la API del lab, llamada **desde la propia Mac**
(`ssh mac curl http://127.0.0.1:8800/...`): no hay ninguna IP en el código, y funciona con la VPN
puesta mientras funcione `ssh mac`. Un render del lab **nunca se desaloja**, porque no tiene
checkpoint.

### Prueba (b): un render real, pausado y reanudado por su API

El lab **estaba corriendo de verdad**, así que no hizo falta simularlo. Se le pidió un render por su
propia API: `talk_10s.mp4` con el preset `meina_depth065`, el mismo que el lab tarda unos 4 min en
renderizar. A los ~10 s se lanzó un alta (`cpu-burn`, 60 s). Extracto del driver: `LAB api` es lo
que dice el lab de sí mismo y `visor` es lo que muestra `live.json`:

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
{"action": "pause",  "key": "lab:363b069e", "source": "render_lab", "at": "2026-10-11T03:03:11+00:00", "ok": true, "msg": "HTTP 200", "by": "planificador"}
{"action": "resume", "key": "lab:363b069e", "source": "render_lab", "at": "2026-10-11T03:05:18+00:00", "ok": true, "msg": "HTTP 200", "by": "planificador",
 "reason": "reanudado: nodo libre de alta durante 63 s"}
```

El `elapsed_s` del lab se quedó en 17,3 durante los dos minutos de pausa: el render estaba
congelado de verdad, no solo marcado como pausado. El lab no escribe en el ledger de enjambre (no
es un trabajo de enjambre), así que su historial está en `scheduler.jsonl`.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## 6. Controles del visor (solo en la copia local)

Cada tarjeta tiene **pausar / reanudar / desalojar** y un selector de prioridad. Los procesos
pesados que enjambre no lanzó salen en **No gestionados**, con un botón **adoptar**. Cómo se
protege:

- `live --serve` abre un endpoint en `127.0.0.1` con un puerto efímero y un **token nuevo en cada
  arranque**. Los dos van en `state/visor/ctl.js`, que solo existe en el PC. El token no aparece
  en `live.json` y la copia web no lo lleva: se publica desde una lista blanca, y hay un test que
  lo comprueba.
- El endpoint comprueba la cabecera `Host` (contra DNS rebinding), acepta solo las acciones de la
  lista blanca y valida la clave con una regex. El cuerpo va como `text/plain`, para que la página
  file:// no necesite preflight de CORS. Desde la página se comprobó que la llamada llega (409 con
  un id falso).
- Si el navegador la bloquea, la tarjeta muestra el comando exacto para pegar.

Lo mismo desde la terminal (paso 5):

```
python enjambre.py prioridad <id> alta|normal|baja|nocturna
python enjambre.py pausar <id>          # o lab:<id>, o pid:<nodo>:<pid>
python enjambre.py reanudar <id>
python enjambre.py desalojar <id>       # solo si tiene checkpoint
python enjambre.py adoptar mac 12345 --prio baja
```

Con `live` corriendo, los comandos pasan por su endpoint, para que `state/scheduler.json` tenga
un solo escritor. Sin `live`, actúan directo. **Una pausa manual nunca la reanuda el
planificador:** es decisión del owner.

### Prueba extra: desalojo, checkpoint, pausa manual y el clic del visor

Las pruebas (a) y (b) solo ejercitaron la pausa: la memoria nunca llegó a faltar. Por eso se
probaron aparte, en real, el desalojo y los controles manuales. Un `cpu-burn-ck` baja `--resumable`:

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

Una sola línea en el ledger, con los dos intentos y todo el historial:

```json
{"id": "cpu-burn-ck-20261010-221051", "status": "ok", "priority": "baja", "resumable": true, "attempts": 2,
 "preemptions": [{"state": "evicted_resumable", "reason": "desalojado a mano", "at": "2026-10-10T22:11:37-05:00"},
                 {"state": "paused_by_priority", "reason": "pausado a mano", "at": "2026-10-10T22:12:27-05:00"},
                 {"state": "running", "reason": "reanudado a mano", "at": "2026-10-10T22:14:00-05:00"}],
 "duration_s": 300}
```

`state/scheduler.jsonl` dice quién hizo cada cosa:

```
03:11:36Z evict           cli    ok  desalojado a mano
03:11:57Z resume_evicted  cli    ok  reanudado a mano
03:12:26Z pause           cli    ok  pausado a mano
03:13:59Z resume          visor  ok  reanudado a mano
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Las acciones sobre los trabajos de enjambre quedan en su línea del ledger (`preemptions`). Las
acciones sobre renders del lab y procesos adoptados quedan solo en `scheduler.jsonl`, porque no
son trabajos de enjambre y no tienen línea en el ledger (DECISIONES 285).

**Tropiezo:** al encolar el primer trabajo nocturna, la tabla de historial del visor dejó de
pintarse (`RangeError: Invalid time value`). Los trabajos encolados guardan `enqueued` como texto
ISO, y la tabla lo trataba como segundos. Era un bug viejo que no se veía porque la cola casi
nunca tenía nada.

## 7. Lo que queda para el owner

Las tres decisiones de 20 §4 están en NEEDS-OWNER §24. Funcionan hoy con la recomendación de
cada una y se cambian en el toml: agentes entre turnos o nunca, desalojar o no un render sin
checkpoint, y controles solo locales.

## 8. Wi-Fi vs Ethernet (prompt 19 §8, 2026-10-10)

**Qué cambió:** la Mac tiene ahora cable Ethernet (`en0`, 1000baseT full-duplex) además del
Wi-Fi (`en1`). El alias `mac` de `~/.ssh/config` pasó de la IP del Wi-Fi a la del cable. La IP del
Wi-Fi queda comentada debajo como respaldo.

**Por qué importa:** todo el enjambre habla con la Mac por `ssh mac`: el ciclo de `live` cada
10 s, `watch`, el puente, el planificador y las copias de logs. Cada milisegundo del handshake se
paga en cada vuelta.

### Qué había en la Mac

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

Ethernet ya estaba primero en el orden de servicios y la ruta por defecto ya salía por `en0`. No
hizo falta `networksetup -ordernetworkservices` ni sudo.

### Cómo se cambió el alias sin aceptar una clave a ciegas

La IP nueva no estaba en `known_hosts`. Antes de añadirla, se comparó su huella con la que ya se
conocía para la IP del Wi-Fi:

```
$ ssh-keyscan -t ed25519 <ip-cable> | ssh-keygen -lf -     → SHA256:IoH2…BFO4
$ ssh-keygen -F <ip-wifi> (la entrada conocida) | ssh-keygen -lf -   → SHA256:IoH2…BFO4
```

Son la misma máquina, así que se añadió la línea y se cambió `HostName` en los dos bloques
`Host mac`. Había dos bloques duplicados, y ssh toma el **primer** valor de cada opción que
encuentra. La config anterior quedó en `~/.ssh/config.bak-20261010-wifi`.

**Tropiezo evitado:** el alias `mac6` (el de la VPN, NEEDS-OWNER §15) usaba la IPv6 link-local
**del Wi-Fi** (`en1`). Al apagar el Wi-Fi habría dejado de responder justo cuando hace falta. Ahora
usa la link-local de `en0`; la `%4` es el índice de la tarjeta del PC y no cambia. Verificado:
`ssh mac6` → `…%en0`.

En el código no había ninguna IP fija: `nodes.toml` usa `host = "mac"`, `smb-mount.sh` y el
puente van por el alias, y el lab de lab-video escucha en `0.0.0.0:8800` y enjambre le habla
desde la propia Mac (127.0.0.1). La IP vieja solo aparece en documentos históricos y en logs de
corridas viejas. `routing.toml` tampoco tiene ninguna (revisado en el prompt 21 §1). Para que
siga así, `tests/test_no_fixed_ips.py` falla si aparece una dirección de la LAN en la config, los
scripts o el código: la única dirección vive en la reserva DHCP del router y en `~/.ssh/config`.

### Los números

`ssh mac true`, 10 veces, cronometrado desde el PC:

| Ruta | Mediana | Mín | Máx |
|---|---|---|---|
| Wi-Fi (IP de `en1`) | **266 ms** | 244 | 395 |
| Ethernet (IP de `en0`), mismo momento | 216 ms | 209 | 251 |
| Ethernet por el alias ya cambiado | **187 ms** | 166 | 210 |

`tools/smb-bench.sh 2` (2 GiB; SMB omitido porque `Compartido-Mac` no está montado en la Mac, solo
scp):

| Corrida | Win → Mac | Mac → Win |
|---|---|---|
| 2026-10-05, solo Wi-Fi (`en1`), 5 GiB | 53,4 MB/s | 66,7 MB/s |
| 2026-10-10, a la IP del Wi-Fi con el cable ya puesto | 48,7 MB/s | 69,6 MB/s |
| 2026-10-10, por Ethernet | **64,5 MB/s** | **100,7 MB/s** |

La corrida intermedia es **mixta**: los paquetes hacia la IP del Wi-Fi entran por `en1`, pero la
Mac responde por su interfaz principal (`en0`). Por eso el bench ya anotaba `mac_iface: en0`. La
comparación limpia es la primera fila contra la última: **+21 % de subida y +51 % de bajada**, con
el handshake de ssh un 30 % más rápido. scp cifra, así que estos números son un piso; el techo del
gigabit está cerca de 110 MB/s.

Hubo trabajos de Landing corriendo en la Mac durante las tres medidas. Por eso se usaron 2 GiB y
no los 5 por defecto.

**Siguiente:** [30 — Arranque desatendido](30-arranque.md).
