# 15 — Presencia de nodos y cola de trabajos

> La página 12 es la de 3D y estudio; las 13–14 están reservadas para las sesiones B y C de `prompts/09-sesiones-2026-10-04.md`.

## Objetivo

`python enjambre.py status` dice, sin que preguntes a mano, qué nodos pueden recibir trabajo ahora.
Además, un `enjambre run` contra un nodo apagado **no falla**: el trabajo queda en cola y sale
solo cuando el nodo vuelve.

## Por qué

Hasta aquí, `enjambre run` suponía que la Mac estaba lista. Si no lo estaba, el trabajo fallaba
y había que acordarse de relanzarlo. Para que el enjambre funcione sin ti hacen falta dos piezas:

- **Saber quién está vivo.** "Vivo" tiene niveles, porque cada uno falla por motivos distintos:

  | Nivel | Qué significa | Qué falla si no llega |
  |---|---|---|
  | `down` | Ni ping | La máquina está apagada o fuera de la red |
  | `ping` | Responde a ping, pero SSH no entra | sshd o la llave |
  | `ssh` | SSH entra, pero no hay sesión `tmux -L enjambre` | **Nadie inició sesión en la pantalla**, así que no hay llavero (paso 11) |
  | `ready` | SSH y la sesión con llavero | Nada: se puede despachar |

- **Una cola.** Un trabajo pedido a un nodo que no está `ready` espera su turno en vez de perderse.
  Ver la ficha [cola de trabajos](../conceptos/cola-de-trabajos.md).

**Sondeo o latido.** Windows pregunta cada 30 s; los nodos no avisan. La comparación completa y
la razón están en la ficha [sondeo vs latido](../conceptos/sondeo-vs-latido.md). En una línea: el
sondeo mide exactamente el camino que usa un trabajo (ping → SSH → tmux), y no exige abrir ningún
puerto en Windows.

## Pasos

### 1. `enjambre watch`

```powershell
PS> python enjambre.py watch --once        # una ronda y sale (para probar)
PS> python enjambre.py status
nodes  (watch, 0 s ago)
  mac    ready  since 2026-10-04T14:09:44+00:00
queue  0 waiting
```

Cada ronda hace una sola llamada SSH por nodo: `tmux -L enjambre has-session -t enjambre`. Con eso
basta para dos niveles: el código 0 es `ready`, el 1 es `ssh` (entró, pero no hay sesión), y el
255 significa que SSH no entró, así que se prueba ping. La ronda real contra la Mac tardó
**0,46 s**.

Lo que escribe, en `state/` (ignorada por git):

| Archivo | Qué |
|---|---|
| `nodes.json` | Nivel de cada nodo, desde cuándo y última comprobación. Se escribe en un archivo temporal y se renombra, así un lector nunca ve medio archivo |
| `watch.log` | Arranques, cambios de nivel y despachos. Rota a los 512 KB y guarda 3 copias |
| `queue/`, `running/`, `done/` | Una carpeta por trabajo, que se mueve entre las tres. Nunca se borra nada |
| `watch.pid`, `watch.lock` | Para `--stop` y para que no haya dos instancias |

**Antirrebote:** bajar de nivel exige dos observaciones seguidas. Una sola respuesta perdida no
genera la notificación "mac caída". En cambio, subir de nivel es inmediato.

**Notificación de Windows** solo cuando cambia el nivel: "mac disponible" o "mac caída". La
primera ronda tras arrancar `watch` registra el nivel, pero no avisa.

### 2. La cola

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

*Render de la salida real con `freeze`. El estado "sin tmux" se simuló escribiendo `nodes.json`;
el sondeo que lo devolvió a `ready` fue real.*

Qué pasa por dentro:

- **El contrato se valida antes de encolar.** La regla `confidencial`/`reservado`, el `AGENTS.md` y las secciones
  de la tarea se comprueban igual que si se ejecutara ya. Un trabajo inválido nunca entra a la
  cola.
- **Se guarda una copia de la tarea** (`state/queue/<id>/TASK-<id>.md`) y de las variables
  `ENJAMBRE_*`. El trabajo corre lo que pediste, aunque edites el archivo después.
- **No hay dos trabajos con el mismo id** esperando o corriendo a la vez.
- **El ledger se escribe cuando el trabajo corre**, no cuando se encola. Una línea en
  `jobs.jsonl` sigue significando "esto pasó".
- **`watch` despacha cada trabajo como un proceso aparte** (`enjambre run --no-queue`), respetando
  `max_jobs` de `nodes.toml`. Un trabajo largo no frena el sondeo.
- **Si `nodes.json` está viejo** (más de 90 s, porque `watch` no corre), `run` no le cree y pregunta
  al nodo en ese momento.

### 3. Arranque automático y una sola instancia

```powershell
PS> powershell -File tools\install-watch-task.ps1
TaskPath   TaskName   State
\enjambre\ watch    Running
PS> python enjambre.py watch
watch: another instance is running; not starting a second one
PS> python enjambre.py watch --stop
watch: stopped pid 38332
```

- La tarea programada `\enjambre\watch` arranca al **iniciar tu sesión**, con `pythonw.exe` para que
  no haya ventana. Se registró **sin permisos de administrador**.
- Contra los duplicados hay dos capas: la tarea tiene *MultipleInstances = IgnoreNew*, y `watch`
  toma un candado de archivo que el sistema suelta si el proceso muere. Un archivo de pid viejo
  nunca bloquea el siguiente arranque.
- `--stop` mata **por el pid que escribió el propio `watch`**, nunca por nombre de proceso.

### 4. El lado Mac: ¿está el orquestador?

`node-watch.sh` corre cada 60 s (LaunchAgent `dev.enjambre.nodewatch`, plantilla en `config/`):

1. Mira si el cliente Deskflow tiene una conexión `ESTABLISHED` con Windows (`netstat`, sin
   enviar nada); si no, un `ping`. *(Hasta el 4 oct abría una conexión TCP al 24800 con `nc -z`,
   y Windows registraba `failed to accept secure socket` cada minuto: ver página 01, "Qué salió
   mal", punto 0.)*
2. Toma la dirección de la propia configuración de Deskflow, así que no hay IP en el repo.
3. Avisa en la Mac solo cuando el estado cambia.

Prueba de transiciones, con una copia que no notifica:

```
up(1st)    -> state: up 0
miss1      -> state: up 1       <- una falla sola no cambia nada
miss2      -> state: down 2     <- dos seguidas: "Windows no responde"
back       -> state: up 0
```

## Qué salió mal de verdad

1. **Sobrescribí un archivo que no había leído.** Al guardar el prompt de esta sesión en
   `prompts/09-sesiones-2026-10-04.md`, la herramienta respondió "updated": el archivo ya existía,
   con las sesiones A, B y C, y sin commitear. Lo recuperé de la copia que Claude Code guarda
   antes de cada escritura (`~/.claude/file-history/<sesión>/`), verifiqué que el texto original
   quedara entero e inserté la sesión 0 delante. Lección: un "updated" donde esperabas "created"
   es una alarma; y un archivo sin commitear no tiene red de seguridad en git.
2. **`watch --once` despachaba pero no cerraba.** Esperaba a que terminara el trabajo y salía sin
   moverlo a `done/`, que quedaba en `running/`. Lo vio la prueba real, no los tests: los tests
   cubrían la ronda normal, no la salida de `--once`. Arreglo: una función `reap` que se llama en
   los dos caminos.
3. **La fecha de arranque de la Mac salía en 1970.** `sysctl kern.boottime` devuelve
   `{ sec = …, usec = … }`, y mi regex `.*sec = ` era codiciosa: capturaba `usec`.

## Cómo verificar

```powershell
PS> python -m unittest discover -s tests       # 28 tests: 16 con nodos simulados
PS> Get-ScheduledTask -TaskPath '\enjambre\' -TaskName watch | Select-Object State
PS> python enjambre.py status                   # "watch, N s ago" con N < 90
```

En la Mac: `ssh mac 'cat ~/.enjambre/orq-state; tail -3 ~/enjambre-logs/node-watch.log'` debe
mostrar `up`.

**Pendiente de verificar a ojo:** que la notificación de Windows y la de la Mac se ven en pantalla.
Se mandaron sin error, pero el agente no ve tu pantalla.

## Autoevaluación

1. La Mac responde a SSH pero `status` dice `ssh` y no `ready`. ¿Qué pasó y qué haces?
   <details><summary>Respuesta</summary>No existe la sesión <code>tmux -L enjambre</code>: nadie
   inició sesión en la pantalla de la Mac (por ejemplo, tras un reinicio con FileVault), así que no
   hay llavero. Inicia sesión; el LaunchAgent crea la sesión y la siguiente ronda la ve
   <code>ready</code>.</details>
2. ¿Por qué el ledger no recibe una línea cuando el trabajo se encola?
   <details><summary>Respuesta</summary>Porque una línea del ledger significa "esto se ejecutó".
   Encolar es una intención; el registro de la cola son las carpetas de
   <code>state/queue</code>.</details>
3. ¿Qué evita que arranquen dos `watch` si la tarea programada se dispara dos veces?
   <details><summary>Respuesta</summary>Dos capas: <em>MultipleInstances = IgnoreNew</em> en la
   tarea y un candado de archivo que toma el propio proceso. El sistema lo suelta si el proceso
   muere.</details>
