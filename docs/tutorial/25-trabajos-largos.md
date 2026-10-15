---
lesson:
  route: build-stack
  order: 24
  level: 3
---

# 25 — Trabajos largos en la Mac: lanzar, cerrar la ventana, volver con el resultado

## Objetivo

**Qué consigues:** dejar corriendo en la Mac un trabajo de horas (un script pesado o un agente
`claude -p`) lanzado desde Windows. Se puede cerrar la ventana. El trabajo late, tiene un tope de
gasto, se reanuda si muere y deja su resultado en el ledger.
**Quién lo hace:** Claude Code (sesión E3, 2026-10-07). Tú solo tienes que reiniciar `watch`
(NEEDS-OWNER §20).
**Fichas:** [Latido y reanudación](../conceptos/latido-y-reanudacion.md) ·
[Presupuesto por trabajo](../conceptos/presupuesto-por-trabajo.md).

## Por qué hacía falta

Antes, `enjambre run` esperaba con el ssh abierto hasta que el trabajo terminara y solo entonces
escribía la línea del ledger. Si cerrabas la ventana, el trabajo podía seguir en tmux, pero nadie
recogía su resultado. Además, un agente de horas no daba señales de avance, no tenía tope de
gasto y, si moría, no había forma de continuarlo.

## Pasos

Los pasos son las secciones siguientes: las piezas, la lista blanca en el AGENTS.md y las pruebas (a), (c), (b) y `kind: tests`.

## Las piezas

| Pieza | Dónde corre | Qué hace |
|---|---|---|
| `jobrun.py` | Mac (`/usr/bin/python3`, 3.9) | `launch` arranca un **supervisor** y vuelve enseguida. El supervisor corre el trabajo en su propio grupo de procesos y late cada 60 s en `status.json` y `busy.json` (cada 5 min deja además una línea en `latidos.jsonl`). Al terminar escribe el estado, suelta `busy.json` y, si es un agente, hace el commit. También tiene `status`, `resume` y `kill` (este último con `killpg`). |
| `largos.py` | Windows | Lanzar, seguir, recoger (`collect`), reanudar y matar. |
| `enjambre script` | Windows | Corre un comando **declarado** en el AGENTS.md del repo. No hay agente, cuesta $0. |
| `enjambre run --detach` | Windows | Un agente de horas con `--budget` (USD), `--max-turns` y `--model`. |
| `enjambre status --follow <id>` | Windows | Muestra el log en vivo y el latido. Con Ctrl-C dejas de seguirlo, pero el trabajo sigue. |
| `enjambre collect` | Windows (`watch` lo llama cada 60 s) | Copia latidos. Si el trabajo terminó, trae el log y la rama y escribe **una** línea en el ledger. Es idempotente. |
| `enjambre resume <id>` / `kill <id>` | Windows | Reanudar un trabajo muerto / pararlo entero. |

La lista blanca vive en el AGENTS.md de cada repo:

```markdown
## enjambre

- tests-cmd: `.venv/bin/python -m tests.test_matcher_regresion`
- script-cmd catalogar-disco: `/usr/bin/python3 tools/catalogar_disco.py "/Volumes/Backup Plus" --etiqueta backup-plus --manifest "..."`
```

`enjambre script --cmd <nombre>` solo acepta un nombre de esa lista. Un nombre que no está se
rechaza y la respuesta lista los que sí existen.

## Prueba (a): un script de ≥ 1 h con la ventana cerrada a los 5 min

**Qué corre:** `tools/catalogar_disco.py` calcula el sha256 de todo el disco externo de la Mac
(118 469 archivos, 865,2 GB) y verifica el MANIFEST del respaldo de fotos (11 404 filas). Hasta
hoy, ese manifest solo se había comprobado con una muestra de 200.

```
PS> python enjambre.py script --repo enjambre --cmd catalogar-disco --follow --interval 15
{"id": "catalogar-disco-20261007-052143", "launched": true, "attempt": 1, "via": "ssh", "supervisor_pid": 58351, "pgid": 58352}
scan 5,000 files, 19.0 GB
...
[latido 05:25:44] running | intento 1 | 4 min | 2.1%
```

**Cómo se cerró la ventana.** Desde aquí no hay una ventana real que cerrar, así que el lanzador
corrió como un proceso aparte (`Start-Process`). A las 05:26:40, 5 min después de lanzarlo, se
mató por su PID con `taskkill /PID 36192 /T /F`. El trabajo siguió latiendo:

```
{"ts": "2026-10-07T05:27:44-05:00", "state": "running", "attempt": 1, "elapsed_s": 360, "pct": 4.3,
 "last": "hash   4.3% 37.0 GB/865.2 GB 114 MB/s eta 2h00m (7,699/118,469 files)"}
```

Mientras corre, el visor lo muestra: el nodo trae `busy` con `latido` y `pct`, y el detalle del
trabajo se lee de `status.json · latido`.

**Fallo real n.º 1: se colgó en `opendir()`.** El primer intento (`catalogar-disco-20261007-050636`)
nació dentro de tmux y estuvo 15 min sin escribir una sola línea. `sample` mostró el proceso
bloqueado en `__open_nocancel`, dentro de `opendir`, sobre `/Volumes/Backup Plus`. La prueba
que lo confirmó fue correr el mismo `ls` desde tmux: se colgó (`rc=124`). Por ssh tardó 2 s. La
causa es la privacidad de macOS (TCC): la primera vez que un proceso de la sesión gráfica toca
un disco externo, macOS pide permiso en la pantalla, y en tmux nadie lo ve.
**Arreglo:** los `script` nacen desacoplados desde la sesión ssh (sesión propia, de modo que
cortar el ssh no los mata). Los agentes siguen naciendo en tmux, porque necesitan el llavero.
Ese primer intento se paró con `enjambre kill`, y esto también sirvió para probar el camino de
matar: estado `killed`, `busy.json` liberado y la línea en el ledger:

```json
{"id": "catalogar-disco-20261007-050636", "status": "failed", "reason": "killed", "exit": -15, "duration_s": 871, "latidos": 5}
```

## Prueba (c): matarlo a la mitad y reanudar

A las 06:19:23, con el trabajo en 50,7 % (438,6 de 865,2 GB), se simuló una caída. Un
`kill -9` al grupo del trabajo y otro al supervisor, sin dejarle escribir nada:

```
mac$ kill -9 -58352 58351      # grupo del trabajo + supervisor; quedan 0 procesos
PS> python enjambre.py collect catalogar-disco-20261007-052143
{"id": "catalogar-disco-20261007-052143", "state": "dead", "beats": 6}
PS> python enjambre.py resume catalogar-disco-20261007-052143
{"id": "catalogar-disco-20261007-052143", "launched": true, "attempt": 2, "via": "ssh", "supervisor_pid": 67054, "pgid": 67055}
```

`status.json` todavía decía `running`, pero el pid del supervisor ya no existía, y `status` lo
reporta como `dead` (vivo se comprueba, no se cree). El `busy.json` viejo quedó con el pid muerto.
`busy.py` lo lee como "rancio", y el intento 2 lo reemplazó.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

El intento 2 no empezó de cero: **440,7 GB reutilizados**, quedaban 424,5 GB. Como
`catalogar_disco.py` guarda cada 200 archivos, lo máximo que se pierde es el lote en curso.

**Resultado final de (a)+(c):** terminó a las 07:17:53. Fueron 6 969 s de punta a punta
(1 h 56 min, 2 intentos, 27 latidos), con 865,2 GB y 118 469 archivos hasheados y 0 ilegibles.

```json
{"id": "catalogar-disco-20261007-052143", "status": "failed", "exit": 1, "duration_s": 6969, "attempts": 2, "latidos": 27, "pct": 100.0}
manifest: 9,858 ok, 0 distintos, 570 faltan
```

El `failed` es un falso negativo del script, no de enjambre. Las 570 "faltantes" son todas filas
`eliminado-vacio` del primer respaldo: archivos de 0 bytes que nunca se copiaron. Entonces las
**9 858 copias reales del respaldo coinciden byte a byte** (antes solo se había verificado una
muestra de 200). Desde esta sesión, el script cuenta esas filas aparte y no las trata como falla.

## Prueba (b): un agente de ≥ 30 turnos con tope de US$2

El TASK es un inventario archivo por archivo sin fin (`tasks/TASK-e3-presupuesto2.md`, con
`push: false`):

```
PS> python enjambre.py run --node mac --repo enjambre --task tasks/TASK-e3-presupuesto2.md --detach --budget 2 --max-turns 300 --model sonnet
{"id": "e3-presupuesto2", "launched": true, "attempt": 1, "via": "tmux", "supervisor_pid": 61160, "pgid": 61215}
```

Antes se hizo una sonda de 2 s con `--max-budget-usd 0.05` para ver cómo corta el CLI. Termina
con `"subtype": "error_max_budget_usd"`, `"terminal_reason": "budget_exhausted"` e
`"is_error": true`. A partir de eso, el supervisor marca `status: incomplete` y `reason: budget`.

Ledger y segmento:

```json
{"id": "e3-presupuesto2", "status": "incomplete", "reason": "budget", "exit": 1, "duration_s": 301, "cost_usd": 2.0036, "turns": 99, "latidos": 3, "note": "fetched from node, not pushed (push: false in the TASK)"}
{"attempt": 1, "turns": 99, "messages": 97, "cost_usd": 2.0036, "cost_est_usd": 1.8471, "subtype": "error_max_budget_usd"}
```

99 turnos en 5 min. Lo paró el tope del CLI (US$2,0036), con 50 líneas de inventario escritas.
La rama volvió a Windows por `git fetch` y no se empujó, porque el TASK decía `push: false`.

**Fallo real n.º 2: contaba doble.** El primer intento (`e3-presupuesto`) se cortó a los 3 min
por el respaldo del supervisor (estimación > 1,15 × tope): 117 "turnos" y US$2,66 estimados. La
cuenta salía mal porque `stream-json` emite un evento por bloque de contenido y cada evento
repite el `usage` del mensaje entero: eran 117 eventos de 54 mensajes. Ahora se cuenta por id de
mensaje. En la segunda prueba la estimación (US$1,85) quedó por debajo del real (US$2,00) y fue
el CLI el que cortó.

## `kind: tests` de farmacias, en la Mac

El worker de tests recibe en `--allowedTools` exactamente `Bash(<tests-cmd>)` y nada más, y el
TASK le llega por stdin (un TASK que empieza con `---` ya no se lee como opción del CLI).

```json
{"id": "farmacias-tests-e3", "status": "ok", "exit": 0, "duration_s": 11, "cost_usd": 0.217, "pushed": false,
 "push_policy": "repo visibility public: push only with `push: true` in the TASK"}
```

Resultado del agente: `REGRESIÓN LIMPIA: 85/85 casos OK`, sin denegaciones.

**Fallo real n.º 3: el entorno.** En la Mac no había `.venv`. Se creó con uv (`uv venv --python
3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`) y la suite no
importaba: la instalación limpia trae `selectolax` 1.x, que quitó el backend que usa farmacias
(Windows tenía 0.4.12). Se fijó `selectolax<1.0` en farmacias. Como el worktree de cada trabajo
no trae el `.venv` (está en `.gitignore`), enjambre lo enlaza desde el clon y lo excluye en
`info/exclude`.

**Fallo real n.º 4: traer la rama.** El primer `git fetch` de la rama desde la Mac quedó colgado
unos 10 min y terminó con `remote end hung up`. El mismo fetch hecho a mano tardó 0,6 s. Ahora
tiene un tope de 120 s y un reintento.

## Lo que se cambió de paso

- **Push** opcional en repos públicos (`push: true` en el TASK). Si gitleaks encuentra algo, no
  se empuja nunca.
- **Hook enjambre-guard**: `grep 'ssh mac' docs/` ya no se bloquea, porque solo cuenta un `ssh` en
  posición de comando. `timeout 20 ssh mac`, `env X=1 ssh mac` y `bash -c 'ssh mac ls'` siguen
  bloqueados. 41/41 en bash 5 (Windows) y 3.2 (Mac).
- **Plantillas** `AGENTS.md` y `DELEGAR.md`: sección `## enjambre`, `.venv/bin/python` o `python3`
  en la Mac, nunca `py`.

## Qué salió mal de verdad

Cuatro fallos reales, cada uno explicado donde ocurrió:

1. **Se colgó en `opendir()`** (prueba a): un proceso nacido en tmux no puede tocar el disco externo
   porque macOS pide permiso (TCC) en la pantalla. Arreglo: los `script` nacen desacoplados desde la sesión ssh.
2. **Contaba doble** (prueba b): `stream-json` repite el `usage` en cada bloque de contenido. Arreglo: contar por id de mensaje.
3. **El entorno** (`kind: tests`): sin `.venv` en la Mac y con `selectolax` 1.x. Arreglo: `selectolax<1.0` y `.venv` enlazado.
4. **Traer la rama**: el `git fetch` quedó colgado unos 10 min. Arreglo: tope de 120 s y un reintento.

Además, en (a) el `failed` final fue un falso negativo del script, no de enjambre (las 570 filas `eliminado-vacio`).

## Cómo verificar

- Windows: `python -m unittest discover -s tests` → 209 OK (32 nuevos en `test_largos.py`).
- Mac: `/usr/bin/python3 -m unittest discover -s tests/node` → 14 OK, con HOME temporal y un
  `claude` falso que imprime `stream-json` (no gasta cuota). Cubre lanzar, latido, `busy.json`
  que no se roba, `kill` por grupo, muerto → reanudar, huérfano, presupuesto, `--resume` con el
  presupuesto restante, TASK por stdin y el `.venv` enlazado sin commitearlo.

Prueba de extremo a extremo (ya hecha, con salida en este tutorial): `python enjambre.py collect <id>`
sobre un trabajo matado devuelve `"state": "dead"`, y `python enjambre.py resume <id>` lo relanza con
`"attempt": 2` reutilizando lo ya calculado (440,7 GB en la prueba c).

## Autoevaluación

1. ¿Por qué los `script` nacen desde la sesión ssh y los agentes en tmux?
   Respuesta: en tmux un proceso que toca un disco externo se cuelga esperando el permiso de macOS (TCC) que nadie ve; los agentes sí necesitan el llavero, que está en la sesión gráfica.
2. ¿Qué pasa si `status.json` dice `running` pero el pid del supervisor ya no existe?
   Respuesta: `status` lo reporta como `dead` (se comprueba, no se cree) y `resume` lo relanza como intento 2.
3. ¿Qué corta primero a un agente: el tope del CLI o la estimación del supervisor?
   Respuesta: en la segunda prueba cortó el CLI (US$2,0036, `error_max_budget_usd`); la estimación del supervisor (US$1,85) quedó por debajo. El respaldo del supervisor corta si la estimación supera 1,15 × el tope.
