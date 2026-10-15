# Arquitectura de enjambre: notas de investigación

Fecha: 2026-10-15. Fuente: el código de este repo (CLI, workers, supervisor, visor, publicación), sus pruebas en `tests/` y el registro de decisiones (`docs/DECISIONES.md`). Lo que no está en el código queda como «por confirmar» o «no aplica» y no se dibuja. Las rutas de la Mac se escriben como `~/...` y las del repo como `Portfolio/{repo}`.

## Las 9 preguntas

1. **Actores.** Una persona que trabaja desde un PC con Windows y mueve trabajo a una Mac mini. Sistemas externos: GitHub (repo privado de trabajo y repo público), Hostinger (la demo estática y el sitio privado del visor), el agente `claude -p` y la propia sesión gráfica de la Mac, de la que depende el llavero. No hay usuarios finales ni roles.
2. **Contenedores.** Diecisiete cajas en la vista C4:
   - PC Windows: CLI (`enjambre.py`, `routing.py`, `largos.py`), vigilante (`enjambre watch`, `presence.py`, `scheduler.py`), visor (`live.py`, `tools/visor/build.py`, `web.py`), publicación y demo (`publicar/exportar.py`, `tools/visor/demo.py`, `publicar/subir_demo.py`).
   - Mac mini: entrada ssh, `worker.sh` con `tmux-run.sh`, servidor tmux, supervisor (`jobrun.py`), agente `claude -p`, clon con worktrees, puente (`agent/puente.py`), colector (`tools/visor/collect.py`).
   - Externos: repo privado, repo público, demo en Hostinger, sitio privado del visor en Hostinger, navegador del usuario.
   - Escala: un PC orquestador y N nodos (`nodes.toml`, `max_jobs` por nodo); un solo vigilante (candado `state/watch.lock`).
3. **Llamadas reales.** Tabla de evidencia más abajo (20 aristas).
4. **Flujos.** Cuatro: despachar y recoger; turno que sobrevive al cierre de la ventana; visor regenerado y publicado; añadir un nodo. Cada paso apunta a una función concreta (ver «Pasos de los flujos»).
5. **Componentes internos del núcleo.** No aplica: no hay API con puertos y adaptadores. El enrutado (`routing.py`) y el planificador (`scheduler.py`) son funciones puras sobre diccionarios, pero no forman un núcleo hexagonal. Vista omitida.
6. **Datos.** No hay base de datos central. Hay archivos JSON (ledger, estado de trabajos) y una base SQLite por nodo con el historial del visor. Detalle en «Datos».
7. **Despliegue.** Tres tareas programadas en el PC (`tools/install-watch-task.ps1`, `tools/install-live-task.ps1`, `tools/visor/install-visor-task.ps1`) y LaunchAgents en la Mac (`config/dev.enjambre.tmux.plist`, `dev.enjambre.visor.plist`, `dev.enjambre.nodewatch.plist`, `dev.enjambre.smbmount.plist`; el del puente lo crea `agent/install.sh`). Al escalar: `enjambre nodo add` (`nodo.py`). Un nodo Linux se rechaza porque falta el temporizador de systemd-user (`nodo.py: add`).
8. **Historias de usuario.** No aplica como requisitos de terceros: es una herramienta personal. Existe `STORIES.md`, pero no se usó para la página. Vista omitida.
9. **Decisiones.** Nueve, tomadas de `docs/DECISIONES.md` con su número: 27, 29, 174, 200, 202, 209, 237, 247 y 280. Cada una trae la alternativa descartada en el propio registro.

## Tabla de llamadas (evidencia)

| Arista | De, a | Qué viaja | Evidencia |
|---|---|---|---|
| e1 | CLI, repo privado | `git push -u origin mac/<id>` tras el escaneo | `enjambre.py: bring_branch_home`, `push_policy` |
| e2 | CLI, vigilante | carpeta `state/queue/<id>/` con la TASK y `job.json` | `enjambre.py: enqueue_job`; `presence.py: enqueue`, `pick` |
| e3 | CLI, ssh | `ssh`, `scp` de `worker.sh`, `tmux-run.sh` y `jobrun.py`; `git fetch` por ssh | `enjambre.py: cmd_run`; `largos.py: ship`, `launch`; `nodo.py: step1_reach` |
| e4 | vigilante, ssh | sondeo (`tmux has-session`) y `jobrun.py status` | `presence.py: probe`; `largos.py: statuses`, `collect`; `puente_cli.py: collect` |
| e5 | visor, ssh | `collect.py --live` y `cat` del snapshot de cada nodo | `live.py: ssh_live`; `tools/visor/build.py: fetch_remote` |
| e6 | visor, navegador | `visor.html`, `live.js`, `ring.js` en disco | `live.py: write_js`; `tools/visor/instalar.py` |
| e7 | visor, sitio privado | POST firmado con HMAC-SHA256 sobre «ts.cuerpo» | `tools/visor/web.py: post`, `maybe_upload` |
| e8 | publicación, repo público | commit nuevo (push a mano) y `gh repo view` | `publicar/actualizar_publico.py`; `publicar/subir_demo.py: gates` |
| e9 | publicación, demo | subida TUS archivo por archivo | `publicar/subir_demo.py: upload` |
| e10 | ssh, worker | `bash .enjambre/worker.sh <repo> <id> <worker> <nodo>` con la TASK por stdin | `enjambre.py: cmd_run` |
| e11 | ssh, supervisor | `python3 .enjambre/jobrun.py launch\|status\|resume\|kill` | `largos.py: launch`, `statuses`, `resume`, `kill` |
| e12 | ssh, clon | `git fetch mac:Documents/Portfolio/{repo} mac/<id>` | `enjambre.py: bring_branch_home` |
| e13 | ssh, puente | `curl` firmado a `127.0.0.1:8766` | `puente_cli.py: request`, `curl_line` |
| e14 | ssh, colector | `collect.py --node <nombre> --live` y la copia del colector | `live.py: REMOTE_COLLECT`; `nodo.py: step2_ship` |
| e15 | worker, servidor tmux | `tmux -L enjambre new-window` | `tmux-run.sh` |
| e16 | supervisor, servidor tmux | `tmux new-window ... nohup jobrun.py supervise` | `jobrun.py: launch` |
| e17 | worker, agente | `claude -p --output-format json --max-turns N --allowedTools ...` | `worker.sh` (`run.sh` generado) |
| e18 | supervisor, agente | `claude -p` con salida stream-json, en su propio grupo de procesos | `jobrun.py: agent_cmd`, `supervise` |
| e19 | agente, clon | edición del worktree del trabajo | `worker.sh` (`git worktree add`); `jobrun.py: ensure_worktree` |
| e20 | puente, clon | el turno trabaja en el clon principal, sin rama | `agent/puente.py: Runner`; DECISIONES 247 |

## Pasos de los flujos

**Despachar y recoger** (`enjambre.py`): `validate` (contrato y regla de rutas confidenciales: `SENSITIVE`, `EXTERNAL_WORKERS`) → `decide` y `routing.py: route` → `enqueue_job` si el nodo no está listo → `cmd_run` (ssh y scp) → `worker.sh` se relanza con `tmux-run.sh` → `git worktree add -b mac/<id>` → `claude -p` → `result.json` y commit → una línea JSON → `job_status` → `bring_branch_home` (fetch, `gitleaks_clean`, push) → `append_ledger` con `razones.py: enrich`.

**Turno que sobrevive al cierre de la ventana** (`enjambre.py: run_detached`, `largos.py`, `jobrun.py`, `puente_cli.py`): admisión (`admission`, `scheduler.py: admit`) → `largos.launch` (scp de `jobrun.py`, `spec.json` por stdin) → `jobrun.py launch` abre una ventana de tmux → `supervise` → latidos → `largos.collect` desde `watch` cada minuto → `ledger_entry`. Variante conversacional: `puente_cli.request` → `agent/puente.py` (responde 202 y corre el turno fuera de la petición) → `puente_cli.collect`.

**Visor regenerado y publicado**: `live.py: Loop.cycle` (cada 10 s) → `assemble` → `live.json`, `live.js`, `ring.js`; `tools/visor/build.py: main` (cada 5 min) → `snapshot.json` y `visor.html` → `web.py: public_snapshot`, `leak_check`, `post`; demo: `tools/visor/demo.py: make`, `export` → auditoría → `publicar/subir_demo.py` (`gates`, `upload`, `verify`).

**Añadir un nodo** (`nodo.py: add`): `step1_reach` → `toml_table` → `step2_ship` → `step3_launcher` (plantilla `config/dev.enjambre.visor.plist`) → `step4_snapshot` (`check_snapshot`). `doctor` repite las comprobaciones sin escribir y `rm` deshace solo lo que `add` escribió (marca en `nodes.toml`).

## Ciclo de vida de un trabajo (estados reales)

Los nombres salen del código, no de un boceto. Camino de los trabajos largos (`run --detach` y `script`); el run síncrono no escribe `status.json`.

| Estado | Dónde se escribe | Evidencia |
|---|---|---|
| `queued`, `waiting_window`, `waiting_resources` | cola del PC (`state/queue/<id>/job.json`) | `scheduler.py: WAIT_STATES`, `admit`; `enjambre.py: enqueue_detached` |
| `launching` | derivado: lanzado hace menos de 5 min y sin estado todavía | `enjambre.py: node_jobs` (`LAUNCHING_S`) |
| `running`, `paused_by_priority` | `status.json` del nodo | `jobrun.py: supervise`, `controller` |
| `evicted_resumable` | `status.json` del nodo | `jobrun.py: supervise` |
| `ok`, `failed`, `incomplete`, `killed` | `status.json` del nodo (`FINAL`) | `jobrun.py: classify_agent`, `supervise` |
| `dead` | derivado al consultar: supervisor ausente sin estado final | `jobrun.py: status` |
| `collected` | no es estado de nodo: booleano de `job.json` | `largos.py: collect` |

- Razones de `incomplete`: `budget`, `max_turns`, `permission_denials` (`jobrun.py: classify_agent`).
- `largos.py: ledger_entry` guarda `killed` como `status: failed` con `reason: killed` (prueba `tests/test_largos.py`, «killed counts as failed»).
- Un trabajo `dead` no genera línea de ledger mientras siga muerto (`tests/test_largos.py`, «dead job is notified once and never ledgered»).
- «Merged» no existe en el código: fusionar o descartar la rama `mac/<id>` es una decisión manual. En el diagrama es la transición `collected` a `fin`.
- Por confirmar: el cambio `queued` a `launching` en una cola que el vigilante reparte (`launch_queued`) deja el trabajo en `state/done/` con un marcador `launched`; el diagrama lo muestra como `admit ok`.

## Datos

**Almacenes.**

- `jobs.jsonl` (PC, en la raíz del repo): una línea por trabajo, solo se añade (`enjambre.py: append_ledger`).
- `state/jobs/<id>/` (PC): `job.json`, `status.json` (copia), `latidos.jsonl`, `log`.
- `~/.enjambre/jobs/<id>/` (Mac): `spec.json`, `status.json`, `latidos.jsonl`, `segments.jsonl`, `log`, `supervisor.log`.
- `result.json`: lo escribe `worker.sh` en el worktree y viaja en el commit.
- `history.db` (SQLite, una por nodo, nunca se poda): `tools/visor/collect.py: HISTORY_SCHEMA`.

**Campos de `jobs.jsonl`** (la tarjeta del ER muestra el núcleo común; el resto se lista aquí con quien lo escribe):

- Núcleo, escrito por todos: `id`, `node`, `worker`, `repo`, `branch`, `start`, `end`, `exit`, `status`, `duration_s`, `pushed`, `cost_usd`, `kind`, `routed_by`, `origin_session`.
- Run síncrono (`enjambre.py: cmd_run`): `permission_denials`, `usage`, `duration_api_ms`, `result`, `note`, `kind_source`, `route_reason`, `priority`, `preemptable`, `resumable`, `between_turns`, `push_policy`, `classify_cost_usd`, `device`, `diverted_from`.
- Trabajo largo (`largos.py: ledger_entry`): `reason`, `detached`, `attempts`, `latidos`, `turns`, `budget_usd`, `cmd_name`, `pct`, `session_id`, `preemptions`.
- Turno del puente (`puente_cli.py: ledger_entry`): las mismas claves con `worker: puente` y `kind: puente`.
- Añadidos por `razones.py: enrich`: `test`, `log_path` y, en trabajos con mal resultado, `reason` como frase con `reason_code` para el código original.
- Pruebas que lo confirman: `tests/test_largos.py` (estado, duración, intentos, worker, hora UTC de inicio, nota), `tests/test_razones.py` (`reason`, `reason_code`, `log_path`, `test`), `tests/test_enjambre.py` (lectura del ledger).

**Campos de `job.json`** (`largos.py: launch`, `collect`, `resume`; meta de `enjambre.py: run_detached`): `id`, `node`, `host`, `kind`, `repo`, `launched`, `attempts`, `collected`, `collected_at`, `dead_notified`, `resumed`, `push`, `push_why`, `budget_usd`, `model`, `max_turns`, `priority`, `origin_session`. Prueba: `tests/test_largos.py` («launch writes job.json only when the node confirms»).

**Campos de `spec.json`** (`enjambre.py: run_detached`, `sched_spec`): `id`, `kind`, `repo`, `task`, `allowed`, `max_turns`, `budget_usd`, `model`, `beat_s`, `beat_log_s`, `prices`, `priority`, `resumable`, `between_turns`, `prio_prefix`; los scripts llevan `cmd` y `args` en lugar de `task` y `allowed`. Prueba: `tests/test_largos.py` (`cmd`, `args`).

**Campos de `status.json`** (`jobrun.py: supervise`, `status`): `id`, `kind`, `repo`, `state`, `reason`, `rc`, `attempt`, `start`, `end`, `supervisor_pid`, `child_pid`, `pgid`, `branch`, `worktree`, `turns`, `cost_usd`, `session_id`, `priority`, `sched_reason`, `preemptions`, `latido`. Además: `result`, `permission_denials`, `usage`, `stream`, `resumable`, `between_turns`, `prio_prefix`, `segment_start`, `turns_before`, `busy_marker`, `cmd` y, solo al consultar, `alive`, `latidos_n`, `log_name`, `log_size`, `orphan_group`.

**Campos de una línea de `latidos.jsonl`** (`jobrun.py: beat`): `ts`, `state`, `attempt`, `elapsed_s`; agentes: `turns`, `last_tool`, `cost_est_usd`; scripts: `pct`, `last`.

**Campos de `result.json`** (`worker.sh`): `task_id`, `node`, `worker`, `branch`, `exit`, `start`, `end`, `duration_s`, `fetched`, `detail`. Sin prueba propia que fije sus campos: por confirmar.

**Tablas de `history.db`** (sin claves foráneas declaradas): `files`, `msgs`, `sessions`, `meta`, `agents`, con las columnas de `HISTORY_SCHEMA`. Los vínculos son implícitos: `msgs.path` y `agents.path` apuntan a `files.path` (`collect.py: db_load`), `files.sid` a `sessions.sid`. Prueba: `tests/test_visor.py` («store is sqlite with one row per session»).

**Relación entre el ledger y el historial.** `origin_session` (ledger) se compara con el id de sesión que lee el visor para marcar qué sesiones mandaron trabajos a otro nodo (`tools/visor/build.py: remote_jobs_by_session`, usado en `build`). No es una clave foránea: es una coincidencia de texto.

## Despliegue (resumen)

PC Windows: `watch` y `live --serve` al iniciar sesión; el build del visor cada 5 minutos. Mac mini: servidor tmux al abrir la sesión gráfica, colector cada 5 minutos, puente siempre vivo, vigilancia del orquestador y montajes SMB cada 60 s. Hostinger: la demo en su propio subdominio, con carpeta `enjambre-app`, y el sitio privado del visor.

## No dibujado / por confirmar

- **Sitio privado del visor.** `docs/VISOR-WEB.md` lo describe como diseño y `tools/visor/web.py: maybe_upload` solo sube con `[web] upload` activo y la clave presente. Si hoy recibe datos en producción: por confirmar.
- **Bandeja web** (`tools/bandeja`, `docs/BANDEJA.md`): entrada de tareas desde el navegador que `watch` consulta. Existe en el código; no se dibuja para no mezclar un segundo flujo.
- **Push desde la Mac.** `worker.sh` puede subir su rama con `ENJAMBRE_NODE_PUSH=1` (sin escaneo) y hace `git fetch origin` al empezar. No se dibuja: la ruta por defecto no lo usa.
- **Motor de modelos locales** (`engine_local.py`, `--engine local`) y **renders** (`cmd_render`, `tools/render_stl.py`): dos caminos más del CLI sin git ni agente. No se dibujan.
- **Montajes SMB y vigilancia de orquestador** (`smb-mount.sh`, `node-watch.sh`): scripts auxiliares de la Mac; solo aparecen en la vista de despliegue.
- **Estimación de gasto.** El tope real lo aplica el propio CLI del agente (`--max-budget-usd`); el supervisor tiene un respaldo a 1,15 veces el tope sobre su estimación (`jobrun.py: beat`).

## Privacidad

La página usa solo rutas con `~/` o relativas al repo, etiquetas genéricas de máquina y ningún nombre de host, usuario, dirección de red ni dominio. El repo privado no se nombra: aparece como «repo privado de trabajo» y el público como «repo público». La regla de rutas confidenciales se describe sin copiar su patrón.
