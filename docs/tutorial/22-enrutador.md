# 22 — El enrutador: que el enjambre elija la máquina solo

**Objetivo:** que `enjambre run` sin `--node` elija el nodo, explique por qué en una línea y,
por defecto, mande a la Mac todo lo que se puede hacer sin pantalla.

**Por qué:** hasta aquí cada trabajo llevaba `--node mac` escrito a mano. Eso obliga a recordar
qué sabe hacer cada máquina y si está despierta. El concepto detrás es el
[enrutamiento](../conceptos/enrutamiento.md): una tabla que puedes leer y corregir, más el
estado vivo de los nodos. Sin IA en el camino: elegir nodo es una decisión repetible.

Máquinas: Windows (orquestador) · Mac mini M2 Pro 16 GB.

## Pasos

### 1. Medir lo que faltaba

`nodes.toml` decía `ram_gb = 0` para Windows: "no medido". Sin ese número, una regla como
"editar video necesita 32 GB" no tiene con qué compararse.

```
PS> wmic ComputerSystem get TotalPhysicalMemory
51384029184
PS> wmic cpu get NumberOfCores,NumberOfLogicalProcessors,Name
Intel(R) Core(TM) i5-8600 CPU @ 3.10GHz  6  6
mac$ sysctl hw.memsize hw.ncpu hw.perflevel0.physicalcpu hw.perflevel1.physicalcpu
hw.memsize: 17179869184
hw.ncpu: 10
hw.perflevel0.physicalcpu: 6
hw.perflevel1.physicalcpu: 4
mac$ df -h /    →  460Gi total, 166Gi libres
```

| | Windows | Mac |
|---|---|---|
| RAM | 47,9 GiB → `ram_gb = 48` | 16 GiB |
| Núcleos | 6 (sin hyperthreading) | 10 (6 de rendimiento + 4 de eficiencia) |
| Disco libre | C: **33 GB** de 479 (justo) · D: 807 · E: 71 · F: 1 366 | 166 GiB |

### 2. Las capacidades, como etiquetas

Cada nodo declara en `nodes.toml` lo que tiene (`tags`):

| Etiqueta | win | mac | Qué significa |
|---|---|---|---|
| `headless` | sí | sí | puede correr un trabajo sin nadie mirando |
| `display` | sí | — | hay una persona y una pantalla (navegador, Onshape, CapCut) |
| `keychain` | sí | sí | `claude` y `gh` tienen sesión para trabajos desatendidos |
| `metal` | — | sí | GPU de Apple para Blender |
| `mlx` | — | sí | modelos locales del gestor `modelos-mac` |
| `smb` | sí | sí | carpetas compartidas del paso 19 |

Más `owned = true` (es una máquina tuya: la regla `confidencial`/`reservado` solo permite esas) y
`runnable = false` en Windows (ver el paso 5).

### 3. La tabla: `routing.toml`

Cada tipo de trabajo dice qué necesita y a quién prefiere:

```toml
[kinds.tests]
needs = ["headless"]
prefer = ["mac", "win"]

[kinds.video-edit]
needs = ["display", "ram>=32"]
prefer = ["win"]
```

Nueve tipos: `claude`, `tests`, `git`, `scrape`, `render`, `transcribe`, `local-model`, `gui`,
`video-edit`. El worker suma lo suyo: un agente `claude` siempre necesita `keychain`.

### 4. La historia medida

```
PS> python enjambre.py history
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*Render de la salida real con `freeze`.*

Una sola celda tiene número: **render en la Mac, 17 corridas buenas, mediana 7,9 s.** Las demás
tienen menos de 5 corridas y dicen `sin datos`. Las líneas viejas del ledger no tenían tipo: se
deduce sin reescribirlas (`blender` → render, `engine local` → local-model, `claude` →
claude). Los `stub-*` no cuentan: miden el tubo, no el trabajo.

Hoy la historia **no decide nada**: para comparar hacen falta dos nodos medidos en el mismo
tipo, y Windows no tiene ninguno.

### 5. Elegir, explicar, anotar

```
PS> python enjambre.py run --repo enjambre-sandbox --task tasks/TASK-queue-stub.md --kind tests --dry-run
enjambre route [rule] queue: tests: mac is down; queued until it is ready (win: fits, but has no executor yet (runnable = false))
```

`--dry-run` enruta y no ejecuta nada ni escribe en el ledger. Sin `--dry-run`, la misma línea va
a la pantalla y al ledger como `kind`, `routed_by` (`rule`, `history` o `explicit`) y
`route_reason`. El tipo sale de `--kind`, si no de un `kind:` al principio del TASK:

```markdown
---
kind: tests
---
# TASK — ...
```

y si tampoco hay, de `--classify` (una llamada a haiku, apagada por defecto, coste anotado) o
del tipo por defecto (`claude`).

**Windows todavía no ejecuta.** `worker.sh` necesita tmux y `/usr/bin/python3`, y Git Bash no
tiene ninguno. Por eso Windows tiene `runnable = false`. Con la Mac caída, el router dice que
Windows encaja, que no tiene ejecutor, y encola para la Mac. Eso cumple el "elige Windows o
encola y lo dice".

### 6. Qué fue a dónde y por qué

Ocho decisiones reales del 2026-10-07, todas **en seco** (`--dry-run`, nada ejecutado). La Mac
estaba en pleno banco de modelos de la sesión G2, y mandarle trabajos de verdad habría competido
con él. La columna "Mac caída" usa el estado que tenía `watch`. La columna "Mac viva" usa una
sonda en vivo por el alias que sí respondía (ver el error 3).

| Trabajo | Tipo | Mac caída | Mac viva | Por qué |
|---|---|---|---|---|
| tests de `enjambre-sandbox` | tests | encola → mac | **mac** | primera libre de `prefer [mac, win]`; win encaja pero no tiene ejecutor |
| render de una pieza de `forja` | render | encola → mac | **mac** | solo la Mac tiene `metal` |
| transcribir en `modelos-mac` | transcribe | encola → mac | **mac** | solo la Mac tiene `mlx` |
| cambio de código en `algoritmos/ada-pc2` | claude | encola → mac | **mac** | tipo por defecto, prefiere la Mac |
| scraping de `farmacias` | scrape | encola → mac | **mac** | sin pantalla → Mac |
| revisar `Metallum` en el navegador | gui | rechaza | rechaza | solo Windows tiene `display`, y no tiene ejecutor |
| editar video de `forja` | video-edit | rechaza | rechaza | la Mac no tiene `display` ni 32 GB; Windows sí, pero no tiene ejecutor |
| tests de un repo `ejemplo-confidencial` con worker `agy` | tests | rechaza | rechaza | regla `confidencial`/`reservado`: solo worker `claude` en máquinas propias |

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*Render de la salida real con `freeze`.*

La memoria de la Mac en ese momento, leída por la misma sonda: **48 % libre** (`memory_pressure`),
por encima del piso de 20 %. Por eso no contó como ocupada.

### 7. Los tests

`tests/test_routing.py` tiene 31 tests con nodos simulados, una clase por rama:

- filtro duro: capacidad que falta, `ram>=32` contra un nodo sin medir, nodo sin ejecutor, tipo
  desconocido;
- regla `confidencial`/`reservado`;
- sin datos;
- con historia, incluidos el caso de un solo nodo medido y el de 4 corridas;
- empate;
- nodo caído y nodo `ssh` sin tmux;
- huecos llenos y presión de memoria.

Además, el cableado de `enjambre run`: en seco elige la Mac, con la Mac caída encola con la ruta
dentro de `job.json`, y el hijo de `watch` conserva `routed_by`.

```
PS> python -m unittest discover -s tests
Ran 96 tests in 0.425s
OK
```

Los cuatro suites que ya existían siguen en verde.

## Errores reales y cómo se arreglaron

1. **La forma de tabla del prompt no es TOML válido.** `[kinds.tests] needs = [...] prefer = [...]`
   en una sola línea no se puede parsear. Arreglo: una clave por línea bajo cada `[kinds.x]`.
2. **Dos tests viejos se rompieron** con `AttributeError: 'Namespace' object has no attribute
   'routed_by'`. Construyen los argumentos a mano, sin los flags nuevos. Arreglo: `getattr(a,
   "routed_by", None)` en `cmd_run`, sin tocar esos tests.
3. **La Mac aparecía `down` estando viva.** Con la VPN corporativa encendida, la ruta a la red de
   casa cambia y `ssh mac` da timeout. Otro alias sí entra (HANDOFF, sesión G2). No se tocó
   `nodes.toml`, porque `watch` y el visor dependen de ese alias y otras sesiones estaban
   corriendo. La prueba con la Mac viva se hizo cambiando el alias solo en memoria, dentro de un
   script de prueba.
4. **Los tests tardaban 11 s en vez de 1.** El valor por defecto `health_fn=model_health` se fija
   cuando se *define* la función, así que el parche del test no lo alcanzaba y se hacía un ssh de
   verdad a una Mac inalcanzable. Arreglo: `health_fn=None` y resolverlo dentro de la función.
5. **La captura salió con `acci�n`.** La consola de Windows escribe en cp1252. Arreglo: texto sin
   tildes en la cabecera de la captura.

### 8. La Mac avisa que está ocupada (sesión R2)

El punto ciego del paso 6: la memoria y el gestor de modelos no ven un banco que corre en tmux.
La solución es que **el propio trabajo largo avise**. Mientras corre, escribe
`~/.enjambre/busy.json`, y lo borra al salir:

```
mac$ bash ~/.enjambre/busy.sh run bench sesion-x -- bash scripts/mi_banco.sh     # nuevo trabajo
mac$ bash ~/.enjambre/busy.sh attach bench sesion-g2 13809                       # uno que ya corre
mac$ bash ~/.enjambre/busy.sh show
```

- `run` lo borra con `trap … EXIT`, aunque el trabajo falle o lo corten con Ctrl-C.
- `attach` deja un vigilante que lo borra cuando muere ese pid.
- `render_stl.py` escribe el suyo solo.
- **El pid va dentro** porque un `kill -9` o un pánico del kernel no ejecutan el `trap`. El
  lector pregunta en la misma llamada ssh si ese pid sigue vivo. Si no, el marcador está viejo y
  no cuenta.

El banco de la sesión G2 ya estaba corriendo, así que se marcó con `attach`, sin tocar su
proceso ni su script. Con ese marcador vivo:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*Render de la salida real con `freeze` (el usuario de macOS, cambiado por `<usuario>`).*

| Tipo | Con el banco corriendo |
|---|---|
| `render` | va a la Mac **en CPU**, y lo dice (`cpu_fallback` en `routing.toml`) |
| `transcribe`, `local-model` | se **encola** hasta que el banco termine, y lo dice |
| `tests`, `git`, `claude`… | ignoran el marcador; la presión de memoria sigue mirando |

`enjambre render` también lo usa: `--device` vale `auto` por defecto, que es Metal salvo que haya
un marcador vivo. En ese caso usa CPU y deja el porqué en el ledger (`device_reason`).

### 9. La primera corrida real

Con la VPN encendida, el alias `mac` no llega (NEEDS-OWNER §15). Para no tocar `nodes.toml`,
del que dependen `watch` y el visor, se cambia el alias **solo para un comando**:

```
PS> $env:ENJAMBRE_HOST_MAC = "<alias-que-responde>"
PS> python enjambre.py run --repo enjambre-sandbox --task tasks/TASK-router-stub.md --worker stub-ok
enjambre route [rule] run: tests -> mac: first free node of prefer ['mac', 'win']
```

Sin `--node` y sin `--kind`: el tipo sale del `kind: tests` al principio del TASK. La línea del
ledger:

```json
{"id": "router-stub", "node": "mac", "worker": "stub-ok", "repo": "enjambre-sandbox", "branch": "mac/router-stub", "start": "2026-10-07T06:48:26+00:00", "end": "2026-10-07T06:48:33+00:00", "exit": 0, "status": "ok", "permission_denials": null, "duration_s": 2, "pushed": true, "usage": {}, "cost_usd": null, "duration_api_ms": null, "note": "fetched from node over ssh, gitleaks clean, pushed from orchestrator", "kind": "tests", "kind_source": "frontmatter", "routed_by": "rule", "route_reason": "tests -> mac: first free node of prefer ['mac', 'win']"}
```

Se usó un worker `stub-ok`, que no llama a ningún modelo: cuesta $0 y no compite con el banco.

**Errores reales de R2:**
6. **El override de alias no bastaba.** `decide` seguía creyendo el `nodes.json` de `watch`, que
   sondea el alias de `nodes.toml` y decía `down`. Con override, el nivel se sondea en vivo
   (`host_override`).
7. **`queue-stub` ya existía como worktree en la Mac** desde el 04-10, y `worker.sh` se niega a
   reutilizar un id. Se creó `TASK-router-stub.md`, igual pero con `kind: tests`, y no se borró
   nada en la Mac.
8. **El motivo de "todos ocupados" no decía por qué lo estaba el nodo elegido.** Lo detectó un
   test y ahora lo dice.

## Qué queda

- Un ejecutor local en Windows (worker sin tmux y con el Python de Windows) para que
  `runnable` pase a `true`. Está en NEEDS-OWNER §14.
- Envolver con `busy.sh run` los lanzadores de bancos, LTX y transcripción la próxima vez que
  se lancen. `sesion_g2_run.sh` no se tocó porque estaba corriendo.
- Más trabajos reales por el router, para que la historia empiece a decidir. El primero ya está.
