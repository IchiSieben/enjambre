# Enrutamiento

**Qué es.** Decidir en qué máquina corre un trabajo. Aquí lo decide una **tabla más el estado
vivo**, no un modelo de IA: `routing.toml` dice qué necesita cada tipo de trabajo y qué máquina
se prefiere; `nodes.toml` dice qué tiene cada máquina; `state/` dice quién está despierto y
ocupado. El código está en `routing.py` (funciones puras) y lo usa `enjambre run` cuando no le
das `--node`.

**Por qué una tabla y no un LLM.** Elegir nodo es una decisión repetible: con las mismas
entradas tiene que dar lo mismo, y tienes que poder leer por qué. Una tabla se corrige con un
editor de texto y se prueba con tests. Un modelo cuesta cuota en cada trabajo y no sabe si la
Mac está apagada. Un LLM solo entra, opcional y barato (haiku), para **clasificar** una tarea
cuyo tipo nadie declaró (`--classify`), y su coste queda anotado.

## El algoritmo, en este orden

| Paso | Qué mira | Si nadie pasa |
|---|---|---|
| (a) Filtros duros | Capacidades (`needs` del tipo + las del worker: `claude` necesita `keychain`), `ram>=32` contra `ram_gb`, regla `confidencial`/`reservado` (solo worker `claude` y solo nodos `owned`), que el nodo tenga ejecutor (`runnable`), que esté `ready` | Si ninguno *podría* hacerlo: **rechaza**. Si alguno podría pero está caído: **encola** para el primero |
| (b) Estado vivo | Huecos libres (`max_jobs` menos lo que hay en `running/`) y presión de memoria | Todos ocupados: **encola** |
| (c) Historia medida | Si al menos **dos** nodos libres tienen ≥ 5 corridas `ok` de ese tipo, gana la mediana más rápida | Pasa a (d) |
| (d) Empate | El primero de `prefer` | — |

Cada decisión imprime **una línea** con el porqué y se guarda en el ledger:
`kind`, `kind_source` (flag, frontmatter, classify, default), `routed_by` (`rule`, `history`
o `explicit`) y `route_reason`. Si el preferido estaba ocupado y se fue a otro, también
`diverted_from`. `--node` sigue existiendo y manda: `routed_by = explicit`.

## Las trampas que este diseño evita

- **Una sola celda medida no es una comparación.** Si solo la Mac tiene 17 renders medidos y
  Windows ninguno, "la Mac es la más rápida" es falso: es la única medida. Por eso la historia
  solo decide con dos nodos medidos; si no, manda la regla y lo dice.
- **"Sin datos" no es cero.** Con menos de 5 corridas, la tabla dice `sin datos`, no un número
  (`python enjambre.py history`).
- **`ram_gb = 0` no es "poca RAM", es "no medido".** Un requisito `ram>=32` contra un nodo sin
  medir falla. Se midió Windows (48 GB) para que la regla tuviera con qué comparar.
- **Un trabajo encolado no se re-enruta.** Cuando `watch` lo despacha, el hijo recibe `--node`;
  para que el ledger no diga `explicit`, el hijo hereda `kind`, `routed_by` y `route_reason`.

## Presión de memoria: de dónde sale

1. La instantánea del visor (`state/visor/snapshot.json`), si tiene menos de 12 min: el
   `free_pct` de `memory_pressure`.
2. Si no es reciente, una llamada ssh al nodo: `/health` del gestor de modelos y
   `memory_pressure` en la misma línea. Si hay porcentaje, manda el porcentaje; si no, "el
   gestor tiene un trabajo cargado" cuenta como ocupado.
3. Por debajo de `mem_free_min_pct` (20 %) el nodo cuenta como **ocupado**.

**Punto ciego (cerrado en R2 con un marcador).** Lo que corre fuera del gestor de modelos (un banco en tmux, un render
de Blender) no aparece en `/health`, y la GPU no tiene un "porcentaje libre". Medido el
2026-10-07: el banco de la sesión G2 tenía la Mac cargada y `/health` decía `running: null`, con
48 % de memoria libre. El router habría mandado un render Metal en paralelo, que es justo lo
que el HANDOFF prohíbe. La respuesta no fue medir mejor: fue que **el propio trabajo avise**.

## El marcador de ocupado (`~/.enjambre/busy.json`)

Un trabajo largo en la Mac escribe al empezar `{"quien", "desde", "tipo", "pid"}` y lo borra al
salir. Lo hacen el banco de modelos, LTX, la transcripción y un render.
- Un script se envuelve con `busy.sh run <tipo> <quien> -- <comando>`, que lo borra con un
  `trap … EXIT`.
- `render_stl.py` lo escribe solo y lo borra con `atexit`.
- Un trabajo que ya estaba corriendo se marca con `busy.sh attach <tipo> <quien> <pid>`; un
  vigilante lo borra cuando el pid muere.

| Si el marcador está vivo y el tipo es… | El router… |
|---|---|
| `render` (tiene `cpu_fallback`) | lo manda igual a la Mac, **en CPU**, y lo dice |
| `transcribe`, `local-model` (`[busy] gpu_kinds`) | va a otro nodo apto o **encola**, y lo dice |
| cualquier otro (`tests`, `git`, `claude`…) | lo ignora; la presión de memoria sigue vigilando |

**Por qué el pid va dentro.** Un `kill -9` o un pánico del kernel no ejecutan el `trap`, así que
el marcador quedaría para siempre. El lector comprueba en la misma llamada ssh que ese pid siga
vivo (`kill -0`). Si el pid ya murió, el marcador está "viejo" y no cuenta. Un trabajo anidado
(un render en CPU durante el banco) no pisa el marcador de fuera.

## Límites de hoy

- **Windows no tiene ejecutor.** `worker.sh` necesita tmux y `/usr/bin/python3`, que Git Bash
  no tiene. Por eso `win` tiene `runnable = false`: el router lo considera, explica que encaja,
  y no lo elige. Con la Mac caída, un trabajo de `tests` se **encola** para la Mac y lo dice.
  Un trabajo `gui` se **rechaza**: el único nodo con pantalla no puede ejecutarlo todavía.
- `watch` despacha la cola mirando nivel y huecos, no memoria.
- `render` no pasa por el router: su nodo por defecto ya es la Mac, y ahora deja `kind: render`
  en el ledger. No existe un subcomando `check`.
