# Visor maestro — qué se puede medir de verdad

Inventario hecho el 2026-10-05 leyendo los archivos reales de las dos máquinas (Claude Code
2.1.289 en ambas). Regla: si un registro no trae el dato, el visor dice "no medido"; no se
estima con una fórmula recordada.

Leyenda: **medido** = el registro lo trae tal cual · **derivado** = cálculo sobre datos
medidos, con la fórmula a la vista · **no disponible** = ningún registro lo guarda.

## 1. Sesiones de Claude Code (cada máquina, `~/.claude/`)

| Dato | Estado | De dónde sale | Nota |
|---|---|---|---|
| Tokens de entrada, salida, caché escrita, caché leída | **medido** | `projects/<dir>/<sesión>.jsonl`, líneas `type: assistant`, campo `message.usage` | Cada bloque de una respuesta (thinking, texto, herramienta) es una línea con el **mismo** `message.id` y el **mismo** `usage`: hay que contar cada id una vez. En un transcript de 49 MB: 1 292 líneas, 548 respuestas. |
| Respuestas repetidas entre archivos | **medido** | mismo `message.id` en dos transcripts | Una sesión reanudada copia respuestas viejas al transcript nuevo. En 8 días: Mac 11 de 50 ids (18,7 % de los tokens), Windows 2 de 5 094 (0,12 %). El visor une los ids de todos los archivos. |
| Subagentes | **medido** | `projects/<dir>/<sesión>/subagents/agent-*.jsonl` | Si no se leen, el consumo sale bajo. |
| Modelo | **medido** | `message.model` | Las entradas `<synthetic>` (mensajes locales) se descartan. |
| Carpeta del proyecto | **medido** | campo `cwd` de cada línea | El nombre del directorio (`C--Users-...`) pierde información (`_` y `\` → `-`); el visor usa `cwd`. |
| Hora | **medido** | `timestamp` (UTC) | El visor agrupa por día de Lima (UTC−5). |
| Coste en USD por sesión interactiva | **no disponible** en los transcripts | — | Ningún `costUSD` en 396 archivos. |
| Coste "equivalente API" | **derivado** | tokens × precio de lista de `~/.claude/rules/models.json` | Esa tabla no trae precio de **escritura** de caché ni de `claude-sonnet-5-5`. El visor no los inventa: suma entrada + salida + lectura de caché y marca lo que quedó sin precio. Es un piso, no una factura. Con suscripción no es dinero gastado. |
| Coste de la sesión abierta | **medido** | `~/.claude/state/<sesión>.json` → `cost_usd` | Lo escribe la statusline en cada render (`cost.total_cost_usd` del payload). Solo existe para sesiones que renderizaron la barra. |
| Cuota 5 h y 7 días (%) | **medido** | `~/.claude/state/<sesión>.json` → `five_pct`, `week_pct` | Es el **único** lugar donde existe: viene del payload de la statusline, no de los transcripts. Se usa la lectura más reciente de los últimos 30 min; más vieja = "sin lectura". |
| Qué sesiones están abiertas ahora | **medido** | `~/.claude/sessions/<pid>.json` (pid, sessionId, cwd, startedAt, status busy/idle, entrypoint) + comprobar que el PID vive | En Windows con `OpenProcess`/`GetExitCodeProcess`. **No** `os.kill(pid, 0)`: en Windows la señal 0 es Ctrl+C y cortaría la sesión. Los `*.key` del mismo directorio no se abren. |
| Duración por turno / tiempo de modelo en sesiones interactivas | **no disponible** | — | Las líneas `assistant` solo traen `thinkingDurationMs`. `~/.claude.json` guarda `lastDuration`/`lastAPIDuration` solo de la **última** sesión por proyecto. |
| Contenido de prompts | **no se lee** | `history.jsonl`, cuerpo de los mensajes | Puede contener secretos pegados. El visor nunca lo abre. |

## 2. Ledger del enjambre (`jobs.jsonl`, solo en Windows)

31 registros al 2026-10-05, **todos de la Mac**.

| (nodo, worker) | n | Estado |
|---|---|---|
| mac · blender | 22 | 17 ok, 5 fallidos |
| mac · claude | 4 | 2 incompletos, 1 ok, 1 fallido |
| mac · local | 1 | ok |
| mac · stub-ok / stub-fail | 4 | pruebas de cañería, fuera de la comparativa |

| Dato | Estado | Nota |
|---|---|---|
| Duración total | **medido** | `duration_s` (y `render.total_s` para Blender). |
| Coste real de `claude -p` | **medido** | `cost_usd` = `total_cost_usd` que devuelve `claude -p`. |
| Tiempo del modelo (nube) vs herramientas (local) | **medido desde el 2026-10-05**, antes **no disponible** | `worker.sh` tiraba el `duration_api_ms` que da `claude -p`. Ahora pasa al ledger. Los 4 registros viejos de `claude` quedan "no separable". Render y modelos locales no llaman a la nube: su tiempo es todo local. |
| Estado | **medido / derivado** | `status` (`ok`, `failed`, `incomplete`); los registros viejos sin `status` se derivan de `exit` como hace `enjambre status`. |
| Tests, git, transcripción en el ledger | **no disponible** | Ningún registro de esos tipos ni de Windows todavía. La comparativa lo dice celda por celda. |

## 3. Presencia y cola (Windows, `state/`)

| Dato | Estado | Nota |
|---|---|---|
| Nivel de cada nodo (`down < ping < ssh < ready`) | **medido** | `state/nodes.json`, escrito por `enjambre watch` cada 30 s. Más de 90 s sin actualizar = "desconocido". `win` no aparece: es el que pregunta. |
| Montajes SMB de la Mac | **medido** | `nodes.<nodo>.mounts` (sesión E): `{checked_at, stale, mounts:[{name, ok, state}]}`. Si falta, el recolector mira `mount` en la propia Mac. |
| Cola: en espera, corriendo, hechos | **medido** | `state/queue|running|done/<id>/job.json` (+ `rc`). |

## 4. Gestor de modelos de la Mac (`127.0.0.1:8765`)

| Dato | Estado | Nota |
|---|---|---|
| Tarea corriendo, en cola, disco libre | **medido** | `GET /health`. |
| Historial: tarea, carga, ejecución, espera, pico de memoria (Metal, footprint) | **medido** | `~/modelos/jobs.db` en solo lectura (todas las filas; `/jobs` corta en 40). Sin `prompt`, `input` ni `params`. |
| Qué modelo está cargado en memoria ahora | **no disponible** | No hay endpoint de "modelos cargados" (no es Ollama). Lo más cercano: la tarea que corre y su pico de memoria. |
| Memoria del servidor | **medido** | RSS del PID que escucha en 8765 (`lsof` + `ps`). |

Desde Windows, `127.0.0.1:8765` es el **túnel SSH** hacia la Mac; por eso el recolector de
Windows no lo consulta (contaría la Mac dos veces).

## 5. Máquina

| Dato | Estado | Fuente |
|---|---|---|
| Disco libre por volumen | **medido** | `statvfs`/`GetDiskFreeSpaceEx` (nunca recorre carpetas: el espejo sigue escribiendo en `F:`). |
| Memoria libre | **medido** | Windows `GlobalMemoryStatusEx`; Mac `memory_pressure` ("free percentage"); Linux `/proc/meminfo`. |
| Sesiones tmux | **medido** | `tmux ls` (socket por defecto y `-L enjambre`). |

## 6. Límites

| Límite | Estado |
|---|---|
| Cuota 5 h / 7 d de la suscripción | **medido** (sección 1). Es el límite real. |
| Presupuesto diario/semanal en dólares | **no existe**: `~/.claude/rules/thresholds.json` solo tiene umbrales de contexto, caché, cuota 5 h y sesión larga. Pedido en NEEDS-OWNER §12. |
