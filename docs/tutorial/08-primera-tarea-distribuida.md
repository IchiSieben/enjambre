---
lesson:
  route: build-stack
  order: 9
  level: 2
---

# 08 — Primera tarea distribuida: Windows manda, la Mac hace

## Objetivo

Desde Windows, mandar un `TASK-<id>.md` a la Mac con un solo comando y recibir de vuelta una
**rama de git** con el resultado y una **línea en el registro** (*ledger*).

## Por qué

Es el *walking skeleton* del proyecto: la versión más delgada y fea que funciona de punta a
punta. Enseña más que un despachador bonito sin nada detrás, porque cada pieza (SSH, worktree,
tmux, push, ledger) ya está probada en su lugar real.

Las decisiones de aislamiento, con su porqué:

- **Un worktree por tarea.** Cada trabajo tiene su propia copia de los archivos; dos trabajos (o
  un trabajo y tú) nunca escriben el mismo archivo a la vez. Comparten el `.git`, así que la rama
  existe en el repo sin copiar nada.
- **Una rama por tarea (`mac/<id>`).** El resultado es revisable y desechable. Fusionar a `main`
  es una decisión humana, nunca del agente.
- **Git es el bus, no una carpeta compartida.** Dos procesos escribiendo la misma carpeta de red
  es una carrera que no ves hasta que un archivo queda a medias. Con git, el resultado llega
  completo o no llega.
- **`--allowedTools` explícito, nunca `--dangerously-skip-permissions`.** Nadie mira la Mac
  mientras trabaja: lo que no está en la lista se **niega**, no se pregunta.
- **tmux.** Si se cae el SSH, el trabajo sigue; `tmux wait-for` hace que el comando de Windows
  espere a que termine.
- **El contrato.** Un agente desatendido no puede preguntarte qué quisiste decir, así que la
  tarea debe decir qué archivos puede tocar (`## Files you may touch`) y cuándo está terminada
  (`## Done criteria`). Sin eso, `enjambre` la rechaza antes de conectarse.

## Pasos

El viaje de ida y vuelta:

```
 WINDOWS (orquesta)                                   MAC (ejecuta)
 ──────────────────                                   ─────────────
 enjambre.py run --node mac --task TASK-x.md
   1. valida el contrato (TASK + AGENTS.md)
      y la regla de repos restringidos ── si falla: exit 3,
                              no se toca la red
   2. scp worker.sh  ───────────────────────────────▶ ~/.enjambre/worker.sh
   3. ssh mac bash worker.sh < TASK-x.md ───────────▶ 4. git worktree add ../wt/<repo>-x -b mac/x
                                                      5. tmux: worker (claude -p … | stub)
                                                      6. result.json + commit en mac/x
                                                      7. git push  (si hay credenciales)
   8. si la Mac no pudo pushear:      ◀── git fetch por ssh ── rama mac/x
      gitleaks sobre la rama → git push a GitHub
   9. una línea en jobs.jsonl
```

Comandos y salida real (2026-10-04), contra un repo privado de pruebas (`enjambre-sandbox`):

```powershell
PS> python enjambre.py run --node mac --repo enjambre-sandbox --task tasks/TASK-changelog-stub.md --worker stub-ok
```
```powershell
PS> python enjambre.py run --node mac --repo enjambre-sandbox --task tasks/TASK-must-fail.md --worker stub-fail
```
```powershell
PS> python enjambre.py status
2026-10-04T11:54:02+00:00  changelog-stub    mac  stub-ok   exit=0 pushed=True mac/changelog-stub
2026-10-04T11:54:15+00:00  must-fail         mac  stub-fail exit=1 pushed=True mac/must-fail
2026-10-04T11:54:33+00:00  claude-changelog  mac  claude    exit=1 pushed=True mac/claude-changelog
```

Línea real del ledger (`jobs.jsonl`):

```json
{"id": "changelog-stub", "node": "mac", "worker": "stub-ok", "repo": "enjambre-sandbox",
 "branch": "mac/changelog-stub", "start": "2026-10-04T11:54:02+00:00",
 "end": "2026-10-04T11:54:08+00:00", "exit": 0, "duration_s": 0, "pushed": true,
 "usage": {}, "cost_usd": null}
```

Y en GitHub, la rama `mac/changelog-stub` trae el cambio y su `result.json`.

**Qué es un *stub*:** un worker falso que hace algo trivial y predecible (añadir una línea, o
fallar a propósito). Sirve para probar la **tubería** sin gastar un modelo ni depender de su
login. El camino de fallo (`stub-fail`) prueba el criterio E4-1: aunque la tarea falle, la rama
se publica con `result.json` explicando el error y el comando sale con código ≠ 0.

## Qué salió mal de verdad

- **La Mac no puede pushear a GitHub por SSH.** Las credenciales de `gh` están en el llavero de
  macOS, que una sesión SSH no puede abrir. Solución sin mover tokens: Windows trae la rama
  **desde la Mac** con `git fetch mac:Documents/Portfolio/<repo> mac/<id>:mac/<id>`, la escanea
  con gitleaks y la pushea él. Git sigue siendo el bus; solo cambia quién hace el último salto.
- **El worker `claude` real terminó en `"Not logged in · Please run /login"`** por la misma razón
  (el login de Claude Code también vive en el llavero). La tubería funcionó entera (rama, ledger,
  push) pero el modelo no llegó a trabajar; es la tercera línea de `status`, con `exit=1`.
  Pendiente del owner (`NEEDS-OWNER.md` §1; ver `09-agente-desatendido.md`).
- **`result.json` llevaba el nombre real del equipo (`hostname`).** Ahora usa el nombre del nodo
  (`mac`) que le pasa el orquestador: los datos que viajan no deben identificar la máquina. Dos
  ramas de prueba ya publicadas conservan el valor anterior (ver `09-agente-desatendido.md`).

## Cómo verificar

Sin la Mac, las pruebas de la pieza de Windows:

```powershell
PS> python -m unittest discover -s tests -v     → 12 tests OK
```

Cubren el contrato (nombre del archivo, secciones, `AGENTS.md`), la regla de rechazo de repos restringidos
para workers externos (incluido que `run` sale con 3 **sin** tocar la red) y que el ledger es
solo-agregar, con saltos de línea LF también en Windows.

Con la Mac: `python enjambre.py status` debe mostrar una línea por tarea, y la rama `mac/<id>`
debe existir en GitHub.

## Autoevaluación

1. ¿Por qué un worktree por tarea y no una sola carpeta de trabajo?
   <details><summary>Respuesta</summary>Para que dos trabajos (o tú y un trabajo) nunca escriban
   el mismo archivo a la vez; comparten el `.git` pero no los archivos.</details>
2. ¿Qué prueba el worker `stub-fail`?
   <details><summary>Respuesta</summary>Que cuando la tarea falla, la rama se publica igual con un
   `result.json` que explica el error, y el comando sale con código distinto de cero.</details>
3. La Mac no puede pushear. ¿Cómo llega la rama a GitHub sin copiar un token?
   <details><summary>Respuesta</summary>Windows hace `git fetch` de la rama desde la Mac por SSH,
   la escanea con gitleaks y la pushea él.</details>
