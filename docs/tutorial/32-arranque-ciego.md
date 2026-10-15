---
lesson:
  route: build-stack
  order: 31
  level: 2
---

# 32 — Arranque ciego: trabajos «lanzados» que nunca existieron

## Objetivo

Que ningún trabajo quede en `unknown` para siempre. Si un trabajo no puede correr, debe fallar en el
acto y decir por qué. Si muere antes de arrancar, tiene que dejar un estado y una razón en disco.
**Quién lo hace:** Claude Code (sesión V2b del 2026-10-15, prompt `PROMPT-ecosistema-claude-code.md` §10).
**Antes:** [31 — Pulido del visor](31-pulido-visor.md).

## Por qué

En la madrugada del 15-oct, cuatro ventanas mandaron trabajo a la Mac. Tres trabajos (`cert-media`,
`kit-media` y `eco-gates-2`) quedaron así:

```json
{"id": "cert-media", "state": "unknown", "alive": false, "launched": true}
```

No había log, no había latidos y no había línea en el registro. Para el PC, el trabajo «se lanzó».
Para la Mac, nunca empezó. Un sistema distribuido que no distingue «no empezó» de «está corriendo»
miente justo cuando nadie mira, y en modo dormido nadie mira.

## Pasos

Las secciones 1 a 4 son los pasos: la causa con evidencia, el preflight, `clone` y «nunca más
unknown». La 5 relanza lo que había quedado.

## 1. La causa, con evidencia (no con intuición)

**Primero se intentó clonar con un agente.** Para que la Mac tuviera `claude-cert-studio` y
`claude-kit`, se mandaron tres trabajos `tests` al repo `enjambre-sandbox`. Los tres fallaron, cada
uno por un motivo distinto. Están en `state/jobs/clone-*/log*` (rutas de la Mac redactadas a `~`):

| Trabajo | Qué pidió el agente | Qué respondió el sistema |
|---|---|---|
| `clone-cert-studio` | `for r in portfolio-landing:Landing ...; do d=$HOME/Documents/Portfolio/${r#*:}; ...` | `A variable in braces in this command can't be checked before it runs` (permiso denegado) |
| `clone-cert-studio-2` | `bash tools/clone-repos.sh` | `This command requires approval` (no estaba en `allowedTools`) |
| `clone-cert-studio-3`, `clone-claude-kit` | `bash tools/clone-repos.sh` | `Exit code 127` · `bash: tools/clone-repos.sh: No such file or directory` (el script nunca se commiteó en el sandbox) |

Clonar es una operación determinista. Pedírsela a un modelo dentro de un sandbox de permisos es usar
la capa equivocada: la regla «¿un modelo tiene que HACER esto o solo ORQUESTARLO?» lo decía desde
el principio.

**Después se lanzaron los medios igual.** `python enjambre.py logs cert-media` (el comando nuevo de
la sección 4) trajo `supervisor.out` del nodo:

```text
  File "~/.enjambre/jobrun.py", line 248, in ensure_worktree
    fetched = git(["fetch", "-q", "origin"], src, log) == 0
  File "~/.enjambre/jobrun.py", line 236, in git
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
FileNotFoundError: [Errno 2] No such file or directory: '~/Documents/Portfolio/claude-cert-studio'
```

`subprocess.run` con un `cwd` que no existe lanza una excepción antes de ejecutar git. El supervisor
murió en su primer segundo, antes de escribir `status.json`. `jobrun.py status` ve el `spec.json`
y no ve estado, así que responde `unknown, launched: true`. Mientras tanto, `launch` ya había
contestado «lanzado (sin confirmar en 10 s)», y el PC escribió `job.json`.

El tercero, `eco-gates-2`, recibió `--repo C:\Users\...\wt\landing-ecosistema`. En el PC,
`PORTFOLIO / "C:\\..."` **es** esa ruta absoluta (así funciona `pathlib` con rutas absolutas), y por
eso el contrato local pasó. En la Mac el resultado fue `~/Documents/Portfolio/C:\Users\...`. Hubo un
cuarto caso de la misma familia: el zombi `sync-branch-20261007-135456`, con `repo: "C:/Users/.../<otro-repo>"`,
llevaba una semana muerto y `watch` lo anotaba cada minuto.

## 2. Preflight: fallar antes de lanzar

- `--repo` acepta solo un **nombre** (`[A-Za-z0-9][A-Za-z0-9._-]*`, sin `..`). Se rechaza cualquier
  ruta, sea de Windows o POSIX, en `run` y en `script`. La rama de partida se elige con
  `--ref <rama>`: el worktree nace de `origin/<rama>`. Si el fetch falla, el trabajo falla; nunca
  cae en silencio a `HEAD`.
- Antes de lanzar, `run` usa el mismo ssh que `presence` (`test -d ~/Documents/Portfolio/<repo>/.git`):

```text
$ python enjambre.py run --repo claude-kit --task ... --node mac --detach
enjambre: claude-kit is not cloned on mac (~/Documents/Portfolio/claude-kit/.git missing). Fix: python enjambre.py clone --node mac claude-kit
```

  Sale con exit 3, sin nada en `state/jobs/` ni en el registro.
- `jobrun.py launch` repite la comprobación en el nodo. Hace falta porque un trabajo en cola lo
  lanza `watch` horas después, y ese camino no pasa por el preflight del PC.

## 3. `enjambre clone`: la capa correcta

```text
$ python enjambre.py clone --node mac claude-cert-studio
{"id": "clone-claude-cert-studio-20261015-083731", "worker": "clone", "status": "ok", "result": "cloned", "duration_s": 3, ...}
$ python enjambre.py clone --node mac claude-kit
{... "result": "cloned" ...}
$ python enjambre.py clone --node mac claude-kit
{... "result": "exists" ...}
```

Corre sin agente, sin TASK y sin script en el sandbox, en tres segundos y a US$0. `git clone` pasa
por `tmux-run.sh`: la ventana nace en la sesión gráfica, así que git lee el llavero, igual que el
worker (tutorial 30). Es idempotente: si el repo ya está, dice `exists`. Deja una línea en el
registro con `worker: clone`.

## 4. Nunca más `unknown`

- **Nodo, `jobrun.py supervise`:** lo primero que hace es escribir `{state: "starting"}` y una línea
  en `supervisor.log`. Todo el trabajo corre dentro de un `try`: cualquier excepción termina en
  `failed`, con `reason`, `rc` y la cola del traceback. `launch` devuelve esa razón en el acto.
- **Nodo, `worker.sh`** (camino síncrono): escribe `starting` y la primera línea de `worker.log`,
  copia stderr al log, y un `trap EXIT` convierte cualquier salida temprana en `failed` con su
  razón.
- **PC, `collect`:** si un trabajo `launched` no tiene estado (o tiene `starting`/`dead` sin hijo y
  sin latido) después de 90 s, pasa a `dead-at-launch`. Es terminal: una línea en el registro, con
  el error que el propio nodo dejó en `supervisor.out`, y queda recogido. Un supervisor vivo nunca
  se declara muerto, aunque su `git fetch` tarde dos minutos.
- **`enjambre logs <id> [--tail N]`** trae `supervisor.out`, `supervisor.log`, `worker.log` y la
  cola del stream a `state/jobs/<id>/node.log`.
- **`enjambre cerrar <id> --razon "..."`** cierra a mano un trabajo muerto que nadie va a
  reanudar. Así se cerraron los dos zombis, con «cerrado manualmente 2026-10-15».

```text
$ python enjambre.py status
... objcap-build-20261008-033233 mac  script  failed  exit=2  [cerrado manualmente 2026-10-15]
... cert-media                   mac  claude  failed  exit=2  [dead-at-launch: FileNotFoundError: ... '~/Documents/Portfolio/claude-cert-studio']
... kit-media                    mac  claude  failed  exit=2  [dead-at-launch: FileNotFoundError: ... '~/Documents/Portfolio/claude-kit']
... eco-gates-2                  mac  claude  failed  exit=2  [dead-at-launch: FileNotFoundError: ... 'C:\\Users\\...\\wt\\landing-ecosistema']
```

## Cómo verificar

```bash
python -m unittest tests.test_arranque_ciego -v   # 26 pruebas: nombre/ruta, preflight, clone, starting, trap, dead-at-launch, cerrar, carrera
python -m unittest discover -s tests              # la suite completa
```

El supervisor que muere antes de arrancar se simula: `ensure_worktree` lanza `FileNotFoundError`,
igual que el 15-oct. El test comprueba que el estado ya era `starting` antes del fallo, y que
después queda `failed`, con razón y sin la ruta del home.

## 5. Relanzar lo que quedó

Con los clones hechos, se relanzaron los medios con ids nuevos (`cert-media-2`, `kit-media-2`). No se
reusó el id: hacerlo reescribe un `job.json` ya cerrado y deja dos líneas con el mismo id. El
Lighthouse de `cert.ichisieben.dev` (`cert-lh`) fue el primer uso real de `--ref`: el carril
vive en la rama `enjambre/cert-lh` del Landing, porque `main` del Landing se despliega solo. Los
resultados están en el HANDOFF, sección «arranque ciego».

## Qué salió mal de verdad

1. **Los tres clones por agente** (tabla de la sección 1): fue un error de capa, no de permisos.
2. **Mis propios tests escribieron en el registro real.** `append_ledger(entry, ledger=LEDGER)`
   fija la ruta cuando se define la función, así que el `E.LEDGER = tmp` de los tests no le
   llegaba. Tres líneas `clone-proj-*` cayeron en `jobs.jsonl`. Se quitaron con respaldo en
   `state/`. Ahora la ruta se lee al llamar, y cada test comprueba que el registro real no ganó
   filas del repo de prueba.
3. **Una carrera con el `watch` que ya corría.** `cerrar sync-branch` coincidió con el ssh de una
   ronda de `collect` del `watch` residente, que corría código viejo. El `watch` reescribió su copia
   vieja de `job.json` y borró `collected`. El siguiente `collect` cerró el trabajo por segunda vez.
   Ahora `collect` relee el `job.json` después del ssh y salta lo que ya está recogido. La línea
   duplicada se quitó con respaldo. Para que el `watch` use todo esto, hay que reiniciarlo
   (NEEDS-OWNER §29).
4. **Un `heredoc` se comió las barras invertidas.** Al editar con `python - <<'EOF'` desde Git
   Bash, `\\n` llegó como salto de línea real y `\\b` como un carácter de retroceso. El parser lo
   detectó en los `.py`. En `largos.py`, el retroceso dentro de una regex no lo detectó nadie hasta
   que falló un test. Ahora, después de cada edición, se busca cualquier carácter de control en
   los archivos tocados.

## Autoevaluación

1. ¿Por qué `PORTFOLIO / "C:\\x"` no da `PORTFOLIO\C:\x` en `pathlib`, y qué regla lo evita en enjambre?
2. Un supervisor lleva 3 minutos en `starting` con su pid vivo. ¿Es `dead-at-launch`? ¿Por qué no?
3. ¿Por qué `jobrun.py launch` repite el preflight que ya hace el PC?
4. ¿Qué tiene de malo relanzar con el mismo id un trabajo que ya se cerró?
5. ¿Qué principio rompieron los tres clones por agente?

<details><summary>Respuestas</summary>

1. Unir una ruta absoluta descarta todo lo anterior. `check_repo_name` solo acepta nombres.
2. No. Mientras el pid esté vivo, el supervisor está trabajando (`clone-landing` pasó más de 120 s en
   git). Solo cuenta como muerto sin pid vivo, sin hijo y sin latido.
3. Los trabajos en cola los lanza `watch` horas después, por `launch_queued`, sin pasar por `run`.
4. Reescribe un `job.json` terminal y deja dos líneas con el mismo id. Rompe el contrato de una
   línea por trabajo.
5. Lo determinista va en código, no en un modelo (capa antes que carril).

</details>
