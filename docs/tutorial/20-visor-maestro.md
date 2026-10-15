---
lesson:
  route: build-stack
  order: 19
  level: 2
---

# 20 — Visor maestro: una pantalla para todo el enjambre

## Objetivo

Ver en una sola página qué corre en cada máquina, cuánto consume y dónde rinde
mejor cada tipo de tarea. Y tener lo esencial siempre a la vista en la barra de estado de
Claude Code.

## Por qué

Con dos máquinas (y pronto cuatro), "¿está todo bien?" se volvió una ronda de
`ssh`, `tail` y `ls`. Eso es [observabilidad](../conceptos/observabilidad.md) hecha a mano. La
regla de este paso es **no inventar métricas**: primero se inventaría lo que los registros traen
de verdad, y lo que no traen se muestra como "no medido".

Máquinas: Windows (orquestador) · Mac mini M2 Pro. Claude Code 2.1.289 en las dos.

## Pasos

### 1. Inventario: qué se puede medir de verdad

Antes de escribir código se leyeron los registros reales de las dos máquinas. El resultado
completo está en [`docs/VISOR-FUENTES.md`](../VISOR-FUENTES.md). Lo que cambia el diseño:

| Pregunta | Respuesta honesta |
|---|---|
| ¿Tokens por sesión? | Sí, en `~/.claude/projects/**/*.jsonl`. **Pero** cada bloque de una respuesta repite el mismo `usage`: hay que contar cada `message.id` una vez (548 respuestas en 1 292 líneas). |
| ¿Coste en dólares? | No hay `costUSD` en los transcripts. Se **deriva** con los precios de `models.json`, que no tienen precio de escritura de caché. Por eso es un piso ("≈ equivalente API"). |
| ¿Cuota 5 h / 7 d? | Solo en las instantáneas que escribe la statusline (`~/.claude/state/*.json`). |
| ¿Qué sesiones están abiertas? | `~/.claude/sessions/<pid>.json` + comprobar que el PID vive. |
| ¿Tiempo de modelo vs tiempo local? | En `claude -p` sí (`duration_api_ms`), pero `worker.sh` lo tiraba. Se arregló; los registros viejos quedan "no separable". |
| ¿Qué modelo local está cargado? | No hay endpoint para eso en el gestor de la Mac: solo la tarea que corre y su pico de memoria. |
| ¿Presupuesto en dólares? | No existe ninguno definido. El límite real es la cuota. |

### 2. El recolector: cada nodo resume lo suyo

`tools/visor/collect.py` corre **en cada máquina**. Solo usa la biblioteca estándar y es
compatible con Python 3.9, porque en la Mac corre con `/usr/bin/python3` (3.9.6, sin `tomllib`).
Escribe un JSON en `~/.enjambre/visor/snapshot.json`.

Hay 1,1 GB de transcripts en Windows, así que el recolector es **incremental**. Recuerda
cuántos bytes leyó de cada archivo (`~/.enjambre/visor/cursor.json`) y solo procesa lo que se
agregó. Además ignora los archivos sin cambios en 8 días.

```
PS> python tools/visor/collect.py --node win --out $env:TEMP\visor-win.json
real 0m3.720s        ← primera vez (lee 8 días)
real 0m0.776s        ← segunda vez (solo lo nuevo)
```

En la Mac, como LaunchAgent cada 5 min (mismo patrón que `nodewatch`, sin sudo):

```
PS> scp tools/visor/collect.py mac:.enjambre/visor/collect.py
PS> scp config/dev.enjambre.visor.plist mac:/tmp/
mac$ sed "s|__HOME__|$HOME|g" /tmp/dev.enjambre.visor.plist > ~/Library/LaunchAgents/dev.enjambre.visor.plist
mac$ launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.enjambre.visor.plist
mac$ launchctl print gui/$(id -u)/dev.enjambre.visor | grep -E "last exit|run interval"
	last exit code = 0
	run interval = 300 seconds
```

### 3. El agregador: Windows junta todo, sin puertos nuevos

`tools/visor/build.py` corre en Windows cada 5 min como Tarea Programada:

1. Para cada nodo de `nodes.toml`, el local corre el recolector directamente. Para un remoto
   hace `ssh <host> cat ~/.enjambre/visor/snapshot.json`, por el mismo SSH de siempre, sin abrir
   ningún puerto.
2. Agrega lo que solo tiene el orquestador: presencia (`state/nodes.json`), cola
   (`state/queue|running|done`) y ledger (`jobs.jsonl`).
3. Escribe `state/visor/visor.html` (una página, sin dependencias externas) y el resumen de
   una línea para la statusline, que **empuja** a cada nodo por el mismo SSH.

```
PS> powershell -NoProfile -ExecutionPolicy Bypass -File tools\visor\install-visor-task.ps1
TaskPath   TaskName   State
\enjambre\ visor    Running
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*Render de la salida real con `freeze`.*

Se probó **desde la Tarea Programada**, no solo desde la terminal. Una tarea puede no tener la
llave SSH que sí tiene la terminal, y aquí `push: {'mac': True}` en el log lo confirma.

### 4. El visor

Abre `state/visor/visor.html` con doble clic. Los datos van **dentro** del HTML porque una página
`file://` no puede hacer `fetch()` de un JSON local (el navegador lo bloquea), y levantar un
servidor local sería abrir un puerto. Se recarga sola cuando cambia la instantánea (ver §4b).

#### 4b. Como aplicación de Windows (17b, 2026-10-07)

**Objetivo:** abrir el visor como una app más: con su propio icono en el escritorio, en el menú
Inicio y en la barra de tareas, sin pestañas ni barra de direcciones.

**Por qué así:** Chromium (Brave, Edge) tiene el modo `--app=<url>`: una ventana sin interfaz del
navegador que Windows trata como programa aparte, con su propio botón en la barra de tareas y el
favicon de la página como icono. No hace falta empaquetar nada (Electron, PWA instalada): solo un
acceso directo `.lnk` con ese argumento.

```bash
python enjambre.py visor --install     # icono + accesos directos (escritorio y menú Inicio)
python enjambre.py visor               # abrir la ventana ahora, sin acceso directo
```

Salida real:

```text
icono   ...\enjambre\state\visor\visor.ico (16/32/48/256 px)
destino C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe --app="file:///C:/.../state/visor/visor.html" --window-size=1280,860
acceso  C:\Users\<usuario>\Desktop\Visor enjambre.lnk
acceso  C:\Users\<usuario>\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Visor enjambre.lnk
```

Qué hace `tools/visor/instalar.py`:

1. **Icono.** `tools/visor/icon.svg` es el hexágono con punto de la cabecera del visor, en sus
   colores (fondo `#0D1117`, acento `#2DD4BF`). El script lee el SVG con la librería estándar y lo
   dibuja con Pillow a 8× para luego reducirlo (eso suaviza los bordes), y escribe
   `state/visor/visor.ico` con 16, 32, 48 y 256 px. No usa navegador ni cairo. Solo entiende las tres
   figuras que usa el icono: rectángulo redondeado, polígono con trazo y círculo.
2. **Navegador.** Busca Brave en `App Paths` del registro (HKCU, HKLM, WOW6432Node) y en Program
   Files; si no está, busca Edge.
3. **Acceso directo** con `WScript.Shell` (pywin32): destino el navegador, argumentos `--app=` con
   la ruta `file:///` de `visor.html`, el icono `visor.ico` e «Iniciar en» la carpeta del visor.
   Las rutas se calculan desde el repo, nunca se escriben a mano.

**Recarga sola.** `build.py` escribe, junto al HTML, `visor-stamp.js` con una sola línea
(`window.VISOR_STAMP = <hora del build>;`). Cada 30 s la página inserta un `<script>` nuevo que
apunta a ese archivo, y se recarga solo si la hora es más nueva que la de los datos que ya
muestra. Antes se recargaba a ciegas cada 2 min. La página no puede pedir `snapshot.json` con
`fetch()`, porque Chromium bloquea `fetch` entre archivos `file://`; en cambio sí puede cargar un
archivo vecino como script. Si el navegador bloqueara también eso, aparece en la cabecera un botón
**recargar** visible; mientras el sondeo funciona, el botón está oculto.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*Barra de título de la ventana `--app` a 1280 px CSS, y abajo su botón en la barra de tareas,
separado del de Brave. Recorte sin datos de proyectos.*

**Prueba real (2026-10-07):**
- Se abrió la app desde el acceso directo del escritorio. La ventana se llama «Visor del
  enjambre» y su icono grande (el que usa la barra de tareas, 48 px leídos con `WM_GETICON`) es
  el hexágono.
- Se reconstruyó la instantánea a las 21:46:01. A las 21:46:27 la ventana, que se había abierto
  21:45 con la instantánea anterior, ya decía «hace 19 s»: se recargó sola, sin tocarla.

**Lo que falló:** el primer `icon.svg` llevaba `--bg` dentro de un comentario XML, y `--` no está
permitido en un comentario XML, así que `ElementTree` lo rechazó (`not well-formed`). Los
navegadores lo toleran; el parser de Python, no. Además, la primera captura de la barra de tareas
salió vacía porque PowerShell no era *DPI-aware*: con escala 150 % los límites de pantalla
salían 2293×960 en vez de 3440×1440. Se arregló llamando a `SetProcessDPIAware()` antes de
`CopyFromScreen`.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*Captura de la versión `--demo`: los proyectos de trabajo salen como `proyecto-A`, `-B`...; sin
rutas, sin nombres de ventana. Se genera con `python tools/visor/build.py --demo salida.html`.*

Qué muestra, de arriba abajo:

- Nodos vivos, trabajos corriendo, tokens de hoy y de 7 días (las dos máquinas sumadas), y la
  cuota 5 h / 7 d con la máquina y la hora de la lectura.
- Una tarjeta por máquina con presencia, edad de la instantánea, memoria, discos, montajes SMB
  (los que vigila `watch` desde la sesión E), tmux y gestor de modelos.
- Sesiones de Claude Code abiertas, una fila por ventana.
- Consumo por día y máquina, con una tabla equivalente debajo.
- Trabajos del enjambre y la comparativa.

En el teléfono y en modo oscuro también funciona:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

### 5. Comparativa: ¿dónde rinde mejor cada tarea?

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Cada celda muestra **n**. Con menos de 5 corridas lleva la marca "pocos datos". Hoy la
respuesta honesta es "todavía no se puede decir": el ledger solo tiene trabajos de la Mac,
y no hay ningún test, git ni transcripción por el ledger en ninguna de las dos máquinas. La
comparativa medida a mano está en [18](18-mac-vs-windows.md). Esta tabla se llenará sola a
medida que el enjambre trabaje.

Columnas "Modelo (nube)" y "Herramientas (local)": el tiempo en la nube no depende de la
máquina; el local sí. Solo el segundo dice qué nodo conviene.

### 6. La barra de estado

La statusline (`orquestador/home/.claude/statusline.sh`, copiada a `~/.claude/` en las dos
máquinas) añade al final de la línea 2:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*Render de la salida real con `freeze`.*

`enj ⬡2/2 ⚙0 ≈$21 hoy 74.0M`: nodos vivos / total, trabajos corriendo, coste equivalente
y tokens de hoy en **todas** las máquinas. La barra solo **lee** un archivo con `read`, una
instrucción interna de bash, así que no hay `ssh` ni procesos nuevos: se dibuja en cada turno y
no puede tardar. Si el archivo tiene más de 15 min muestra `enj ⌛20m` en vez de números viejos. Si
no existe (una máquina fuera del enjambre), no muestra nada.

```
$ bash tests/statusline_test.sh                       # Git Bash 5.2 (Windows)
54 OK, 0 fallas.
mac$ /bin/bash tests/statusline_test.sh               # bash 3.2 → salta a Homebrew bash 5.3
54 OK, 0 fallas.
mac$ /opt/homebrew/bin/bash tests/statusline_test.sh
54 OK, 0 fallas.
```

Antes de copiar la barra se respaldó la versión anterior en las dos máquinas:
`~/.claude/statusline.sh.bak-<fecha>`.

### 7. Añadir un nodo (la laptop Ubuntu, la Raspberry)

Se añade **una entrada** en `nodes.toml`:

```toml
[nodes.laptop]
host = "laptop"          # alias de ~/.ssh/config
os = "linux"
arch = "x86_64"
ram_gb = 16
max_jobs = 1
workers = ["claude"]
tags = []
portfolio = "~/Documents/Portfolio"
# opcional: visor_snapshot = "~/.enjambre/visor/snapshot.json"
```

En el nodo, el recolector cada 5 min (en Linux con cron, sin root):

```
$ mkdir -p ~/.enjambre/visor && scp <win>:.../tools/visor/collect.py ~/.enjambre/visor/
$ (crontab -l; echo '*/5 * * * * python3 ~/.enjambre/visor/collect.py --node laptop') | crontab -
```

El visor, la comparativa y la barra lo cuentan solos; la página asigna colores por orden de
`nodes.toml`. Hay un test con 3 nodos (`test_n_nodes_from_config_only`) que lo garantiza, incluido
el caso de un nodo caído.

## Qué salió mal de verdad

1. **Windows contaba los modelos de la Mac.** En Windows, `127.0.0.1:8765` responde porque es el
   túnel SSH hacia el gestor de la Mac. El recolector lo leía como si fuera propio. Arreglo:
   consultar el gestor es opcional (`--models`) y solo lo activa el LaunchAgent de la Mac.
2. **Una cuota del futuro.** La Mac reportaba "5 h 41 %, 7 d 87 %" con `ts = 1800000000` (año
   2027). Era una instantánea que dejó un test viejo de la statusline en `~/.claude/state/`.
   Arreglo: una lectura con fecha futura se ignora. (Los tests actuales de la barra ya escriben en
   un directorio temporal, `CC_STATE_DIR`; el archivo viejo sigue ahí y no se borró: no es de esta sesión.)
3. **El disco de la Mac aparecía dos veces.** `/Volumes/Recovery` es otro volumen del mismo
   contenedor APFS y reporta el mismo espacio que `/`. Arreglo: volúmenes con idéntico total y
   libre se cuentan una vez.
4. **tmux "vacío" desde el LaunchAgent.** `launchd` arranca con `PATH=/usr/bin:/bin:/usr/sbin:/sbin`,
   y el tmux de Homebrew no está ahí. Desde la terminal salía bien y desde el agente no. Arreglo: buscar
   también en `/opt/homebrew/bin/tmux`.
5. **`freeze` se quedó colgado** dos minutos al renderizar las capturas: con la entrada estándar
   abierta espera teclado. Arreglo: `freeze archivo.txt -o salida.png < /dev/null`.
6. **La Mac contaba casi un 19 % de más.** La primera versión deduplicaba por `message.id`
   *dentro* de cada archivo. Una sesión reanudada copia respuestas viejas a un transcript nuevo, y
   esas contaban dos veces. Medido en 8 días: Mac 18,71 % de los tokens, Windows 0,12 %. Arreglo:
   el cursor guarda una fila por id y se unen todos los archivos. Después del cambio la Mac pasó de
   2 906 801 a 2 362 969 tokens, exactamente lo medido.
7. **Una instantánea falsa creada sin querer.** Para la captura se pasó un fixture a la barra
   (`session-long.json`) sin `CC_STATE_DIR`. La barra escribió `~/.claude/state/00000000-….json` con
   la cuota del fixture (41 % / 87 %), y por 30 min el visor la habría mostrado como real. Es el
   mismo origen del archivo "del futuro" de la Mac (punto 2). Arreglo: las capturas se renderizan con
   `CC_STATE_DIR` apuntando a una carpeta temporal.
8. **Los tests de la barra habrían mostrado números en vivo.** En una máquina del enjambre el
   archivo de resumen existe, y todos los "golden" habrían cambiado cada 5 minutos. Arreglo: el test
   apunta `CC_ENJ_SUMMARY` a una ruta inexistente por defecto, y los casos del enjambre lo fijan a
   propósito.

Un riesgo que se evitó por diseño: para saber si un PID vive, en Linux/Mac se usa
`os.kill(pid, 0)`. **En Windows la señal 0 es Ctrl+C**, así que esa "prueba" habría cortado las
sesiones abiertas. El recolector usa `OpenProcess` + `GetExitCodeProcess`.

## Cómo verificar

```
PS> python -m unittest tests.test_visor                  # 20 tests
mac$ /usr/bin/python3 -m unittest tests.test_visor       # 16 + 4 omitidos (build.py necesita 3.11)
PS> Get-ScheduledTaskInfo -TaskPath '\enjambre\' -TaskName visor | Select LastRunTime,LastTaskResult
PS> Get-Content state\visor\build.log -Tail 2            # una línea cada 5 min, push mac True
mac$ cat ~/.enjambre/visor/summary.txt                   # <epoch> TAB ⬡2/2 ...
```

Y abrir `state/visor/visor.html`: la instantánea de cada nodo debe decir "hace" menos de 10 min.

## Autoevaluación

1. ¿Por qué el coste del visor es un "piso" y no una factura? ¿Qué dato falta en `models.json`?
2. Si una sesión imprime tres bloques (thinking, texto, herramienta), ¿cuántas veces aparece su
   `usage` en el transcript y cuántas debe contarse?
3. ¿Por qué el visor local lleva los datos dentro del HTML en vez de leer un JSON?
4. La Mac sube su resumen y Windows lo baja por SSH: ¿cuál de los dos inicia la conexión, y
   por qué eso importa para no abrir puertos?
5. En la comparativa, ¿qué columna dice si conviene la Mac o Windows: "Modelo (nube)" o
   "Herramientas (local)"? ¿Por qué?
6. ¿Qué cambia en el código para añadir la Raspberry al visor?
