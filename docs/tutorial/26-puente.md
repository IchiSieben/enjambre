# 26 — El puente: un Claude Code persistente en la Mac al que Windows le habla

**Qué consigues:** desde Windows (desde una terminal o desde otra sesión de Claude Code) le
escribes a un Claude Code que **sigue vivo** en la Mac, con navegador (Playwright), y que recuerda
la conversación entre mensajes, entre cortes de red y entre reinicios del servicio. Cada mensaje
deja su coste en el ledger y la sesión aparece en `enjambre status`.
**Quién lo hace:** Claude Code (sesión P, 2026-10-07/08). Tú: iniciar sesión en el
Claude Code de la Mac y dar un permiso de macOS (NEEDS-OWNER §22).
**Fichas:** [Una vez vs sesión persistente](../conceptos/una-vez-vs-sesion-persistente.md) ·
[Backplane](../conceptos/backplane.md) · evaluación y diseño en [docs/PUENTE.md](../PUENTE.md).

## Por qué hacía falta

`enjambre run` lanza `claude -p`: una sola vez. Nace, hace la tarea y muere. Para seguir con el
mismo tema hay que volver a contar todo en otro TASK. Lo que el owner pidió es otra cosa: que
una sesión de Windows pueda **conversar** con una de la Mac. "Abre la web del demo, saca captura",
y luego "ahora corre los tests", y que la segunda frase sepa de qué hablaba la primera.

## Parte 1 — Ojos y manos en la Mac

### 1.1 Playwright MCP en el Claude Code de la Mac

**Por qué así:** el MCP oficial de Playwright le da a Claude un navegador real (navegar, hacer
clic, capturar). Se registra a nivel **usuario**, así que cualquier sesión de la Mac lo tiene,
incluido el puente. Se fija la versión para que una actualización no cambie las herramientas sin
aviso.

```sh
# desde Windows, todo por tmux-run.sh (nace en la sesión gráfica: tiene pantalla y llavero)
ssh mac 'npx -y @playwright/mcp@latest --version'                      # Version 0.0.83
ssh mac 'bash ~/.enjambre/tmux-run.sh -- /bin/bash -lc "claude mcp add playwright -s user -- \
  /opt/homebrew/bin/npx -y @playwright/mcp@0.0.83 --browser chromium --isolated \
  --output-dir \$HOME/renders/playwright --viewport-size 1280x800; claude mcp list"'
```

Salida esperada: `playwright: … - ✔ Connected`.

- `--isolated`: el perfil del navegador vive en memoria. Cookies y sesiones no sobreviven entre
  usos, así que un agente no hereda tus logins.
- `--output-dir`: las capturas caen en `~/renders/playwright`, de donde las trae `scp`.
- Brave ya estaba en `/Applications` (`brew list --cask` no tenía nada que instalar). Queda como
  navegador "de persona"; los agentes usan Chromium de Playwright.

**Fallo real n.º 1 — versión de navegador.** Instalar con `npx playwright@latest install chromium`
bajó el build 1248, pero el MCP 0.0.83 trae su propio Playwright, que pide el 1247:

```
Error: Browser "chrome-for-testing" is not installed; expected executable at
~/Library/Caches/ms-playwright/chromium-1247/… Run `npx @playwright/mcp install-browser chrome-for-testing`
```

Arreglo: instalar con el instalador **del propio MCP**, en la misma versión que quedó
registrada: `npx -y @playwright/mcp@0.0.83 install-browser chrome-for-testing`.

### 1.2 Probarlo sin modelo

El Claude Code de la Mac tenía el login caducado (ver §4), así que la prueba con `claude -p`
devolvió `Failed to authenticate`. Para no depender de eso se escribió `tools/mcp_probe.py`, que
habla MCP directamente por stdio (initialize → tools/list → tools/call) desde el tmux de la Mac:

```sh
ssh mac 'bash ~/.enjambre/tmux-run.sh -C ~/renders/playwright -- /usr/bin/python3 \
  ~/.enjambre/mcp_probe.py --calls p16calls.json -- /opt/homebrew/bin/npx -y @playwright/mcp@0.0.83 …'
scp mac:renders/playwright/p16-example.png state/renders/
```

```
server: {'name': 'Playwright', 'version': '1.64.0-alpha-…'}
tools: 25 browser_close browser_resize … browser_navigate … browser_take_screenshot …
call browser_navigate: error=False
call browser_take_screenshot: error=False   -> ./p16-example.png
call browser_close: error=False
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

**Fallo real n.º 2 — el probe se colgaba.** La primera versión ignoraba los mensajes del servidor
que traían `id` *y* `method`. Eran **peticiones** del servidor al cliente (`roots/list`), y el
servidor esperaba la respuesta antes de seguir. La lección es que en MCP los dos lados pueden
preguntar: un cliente tiene que contestar lo que le piden, aunque sea con una lista vacía.

### 1.3 La pantalla real: `screencapture`

```sh
ssh mac 'bash ~/.enjambre/tmux-run.sh -- screencapture -x ~/renders/p16-pantalla.png'
# could not create image from display     (rc 1, también desde ssh directo)
```

Las dos pantallas estaban encendidas (`system_profiler`: 1920×1080 y 3440×1440, `Online: Yes`), y
despertarlas con `caffeinate -u` no cambió nada. La causa es **TCC**: macOS exige el permiso
*Grabación de pantalla* para el proceso responsable, y en este caso ese proceso es
`/opt/homebrew/bin/tmux`. Ese permiso no se puede conceder por ssh, así que lo diste tú
(NEEDS-OWNER §22.2). Playwright no lo necesita, porque captura su propia página y no la pantalla.

**Después del permiso (2026-10-08, 03:45 hora de la Mac):**

```
$ ssh mac 'bash ~/.enjambre/tmux-run.sh -- screencapture -x -t png ~/renders/p16-pantalla.png'
rc=0      ~/renders/p16-pantalla.png  1 422 678 B, 1720×1440
$ scp mac:renders/p16-pantalla.png state/renders/     # media de gris 40, desviación 37, 11 674 colores: no está en negro
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

*Captura real tomada desde Windows por `tmux-run.sh`. Todo está pixelado (bloques de 32 px)
menos el panel de Ajustes que muestra el permiso de tmux. El original, con tu escritorio, se
queda en `state/renders/` (ignorado por git).*

Dos datos que no se sabían:

- **No hizo falta reiniciar tmux.** El servidor `tmux -L enjambre` llevaba vivo desde el 5 de
  octubre (mismo pid) y el permiso se aplicó en cuanto se activó el interruptor.
- **Sale una sola pantalla.** `screencapture -x <archivo>` captura la principal. Para las dos
  hacen falta dos archivos (`screencapture -x a.png b.png`).

### 1.4 Qué necesita sesión gráfica, y qué pasa tras un reinicio

| Cosa | ¿Necesita sesión gráfica iniciada? | Por qué |
|---|---|---|
| `claude` (trabajos, puente) | **Sí** | El llavero de login se desbloquea al iniciar sesión, y solo los procesos de esa sesión lo ven (tutorial 11) |
| Playwright con navegador visible | **Sí** | Necesita WindowServer. En modo headless correría sin sesión, pero sin llavero `claude` no corre igual |
| `screencapture` | **Sí** + permiso TCC | Captura WindowServer |
| El puente (`dev.enjambre.puente`) | **Sí** | Es un LaunchAgent de `gui/<uid>`: arranca al iniciar sesión, no antes |
| ssh, `enjambre script` (sin agente) | No | Basta con sshd |

**Tras un reinicio con FileVault no corre nada de lo de arriba.** Primero hay que desbloquear el
disco (por ssh con tu contraseña: tabla del reinicio real en el tutorial 11, «Cómo verificar»), y aun así no hay
sesión: ni llavero, ni tmux, ni puente, hasta que **inicias sesión en la pantalla de la Mac**. Lo
más cómodo es `sudo fdesetup authrestart` antes de un reinicio planificado (tutorial 01).

`enjambre status` lo muestra en vivo con una sola llamada ssh: `stat -f%Su /dev/console` devuelve
tu usuario si hay sesión gráfica y `root` si la Mac está en la ventana de login.

```
  mac    …
         gui sí
         puente: 1 sesión(es), US$0.0000
           farmacias        en espera · 3 turnos · US$0.0000 · sesión ce661f8e · …
```

## Parte 2 — Evaluar antes de construir

Está en [docs/PUENTE.md §2](../PUENTE.md). En resumen: **Agentrooms** necesita un *backplane*
(un servidor de mensajes en medio), por defecto en la nube de un tercero. **thurbox** es muy
bueno, pero envuelve el Claude Code interactivo: el coste y las denegaciones no salen como datos,
y se instala con `curl | sh`. La **mensajería oficial / Remote Control** es para una persona, no
para un script. Se construyó un puente propio sobre el **Agent SDK**, que ya trae sesiones,
`resume`, MCP, allowlist y coste.

## Parte 3 — El puente

### 3.1 Las piezas

| Pieza | Dónde | Qué hace |
|---|---|---|
| `agent/puente.py` | Mac, venv Python 3.12 (`~/.enjambre/puente/venv`, `claude-agent-sdk==0.2.164`) | Servidor HTTP en `127.0.0.1:8766`. Cola de un turno a la vez por repo. Corre el turno con el SDK **fuera** de la petición y escribe los eventos en `turns/<id>.ndjson`. Guarda `session_id` y coste en `sessions/<repo>.json` |
| `agent/install.sh` | Mac | Copia el servidor, crea el venv, crea la clave (una vez, `chmod 600`) y carga el LaunchAgent `dev.enjambre.puente` en `gui/<uid>` |
| `puente_cli.py` | Windows | Firma, `ssh mac curl`, lectura en vivo, pendientes → ledger |
| `enjambre chat / send / sessions` | Windows | Los comandos |

**Por qué Python 3.12 y no `/usr/bin/python3`:** el SDK pide 3.10 o más, y el de macOS es 3.9.
`uv` ya tenía el 3.12 instalado.

**Por qué un LaunchAgent y no tmux:** tiene que nacer en la sesión gráfica, igual que el gestor
de modelos (el mismo patrón que `dev.enjambre.tmux`). launchd además lo reinicia si muere
(`KeepAlive`).

### 3.2 La clave

```sh
ssh mac 'bash ~/.enjambre/puente-src/install.sh sdk'      # key: created ~/.config/enjambre/puente.key (600)
# a Windows, sin imprimirla: ssh mac cat … | python (winreg) -> HKCU\Environment\ENJAMBRE_PUENTE_KEY
```

La clave no pasa por el chat ni por ningún archivo del repo. `puente_cli.key()` la lee del entorno
o, si la terminal se abrió antes de guardarla, directamente del registro.

### 3.3 Usarlo

```sh
python enjambre.py chat mac --repo farmacias "Abre la web del demo y saca captura"   # síncrono, imprime el stream
python enjambre.py send mac --repo farmacias "Corre los tests"                       # asíncrono, vuelve por el ledger
python enjambre.py chat mac --repo farmacias --attach p20261007-231120-9122f0        # retomar un turno
python enjambre.py chat mac --repo farmacias                                          # ¿en qué quedó? (último turno)
python enjambre.py sessions mac                                                       # sesiones y coste
# por defecto la sesión lee, corre el tests-cmd y navega; --edit le deja además editar la copia de la Mac
```

Una sesión de Claude Code en Windows los usa como cualquier comando. Como su herramienta Bash
corta a los 2 minutos, para un turno largo debe usar `send`, o `chat` en segundo plano.

### 3.4 Prueba de cableado (motor `stub`, sin modelo)

Con el login caído, el servidor se desplegó primero con `PUENTE_ENGINE=stub`: un motor que
responde "eco", tarda 20 s en pasos de 1 s y deja los mismos eventos y el mismo resultado que el
SDK. Así se probó todo menos el modelo. El `chat` se lanzó y a los 7 s se mató su árbol de
procesos por PID, que es lo mismo que cerrar la ventana:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

*Render de la salida real con `freeze`, recortada.*

Qué se comprobó:

- El turno siguió en la Mac. `--attach` lo retomó y, al terminar, escribió **una** línea en el
  ledger (`worker: puente`, `status: ok`, `duration_s: 20.1`).
- El segundo y el tercer turno reanudaron la misma sesión (`sesión ce661f8e`, 3 turnos).
- `status` muestra `gui sí` y la sesión viva con su coste.
- 13 tests (`tests/test_puente.py`): firma igual en los dos lados, sin firma → 401, firma vieja →
  401, `Origin` → 403, replay → 401, allowlist (`Bash(rm -rf /)`, comodines, otro MCP, el
  servidor Playwright entero, `browser_run_code_unsafe`, `evaluate`, `file_upload`) → 403,
  `../etc` → 400, reanudación y coste acumulado, eventos desde un desplazamiento, recuperación tras
  reinicio, `busy.json` ajeno intacto, cobro al ledger una sola vez.

**Fallo real n.º 3 — replay legítimo.** Los tests encontraron que dos `GET /sessions` idénticos en
el mismo segundo daban la misma firma, y el segundo se rechazaba como replay. Se arregló con un
**nonce** aleatorio dentro de lo firmado (`X-Puente-Nonce`).

**Fallo real n.º 4 — 50 s para arrancar.** Tras `launchctl bootstrap`, el servidor tardó unos
50 s en escuchar, y el primer `chat` recibió `curl: (28) Failed to connect`. Con `sample <pid>` no
se vio nada útil. La causa fue `HTTPServer.server_bind`, que llama a `socket.getfqdn()`: una
búsqueda DNS inversa que en la Mac tardaba. Se reemplazó por un `server_bind` que no busca nombre,
y ahora arranca en 1 s.

**Fallo real n.º 5 — farmacias atrasado en la Mac.** El primer turno real dio
`http 403: repo has no AGENTS.md`. La copia de la Mac estaba en `1a721ff`, anterior al commit del
AGENTS.md. Se arregló con `git pull --ff-only` por `tmux-run.sh` (la copia estaba limpia). El
puente obliga a que exista: sin AGENTS.md no hay allowlist.

### 3.5 Motor real (SDK) con el login caducado

El mensaje `init` del SDK llega **antes** del fallo de autenticación, así que se pudo comprobar
sin login qué carga la sesión: `54 herramientas, MCP playwright:connected, permisos default`.
El MCP registrado a nivel usuario llega al SDK con `setting_sources=["user","project"]`.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

El fallo llega como un turno `failed` con su causa en el ledger y coste 0. Un turno que falla
sin coste **no** fija el `session_id`, porque no hay conversación que reanudar. Si lo fijara,
todos los turnos siguientes fallarían al intentar reanudarla.

### 3.6 La prueba de fuego, con modelo (2026-10-08)

Iniciaste sesión en la Mac a las 03:46 (`loggedIn: true`, `claude.ai`). La prueba se hizo tal como
la pide el prompt: **una ventana de Claude Code en Windows, en `farmacias`**, le pide el trabajo a la
sesión persistente de la Mac. Esa ventana fue un `claude -p` con Haiku, que solo podía ejecutar
`python ../enjambre/enjambre.py chat:*`. A mitad del turno se cerró matando su árbol de procesos
por PID: 34 procesos (`claude`, `bash`, `python`, `ssh`, sus MCP de node…). Después se abrió **otra**
ventana en el mismo repo.

| Paso | Qué pasó | Evidencia |
|---|---|---|
| Ventana 1 pide: Radar + ichisieben.dev con Playwright, capturas de página completa, `tests-cmd`, resumen | Turno `p20261008-035208-f30ca3`, sesión `912f16e4` reanudada | `turno … (sesión 912f16e4, posición 1)` |
| Ventana 1 cerrada 13 s después de enviar | En la Mac el turno seguía `running` (4 herramientas usadas); en Windows quedó `state/puente/<turno>.json` pendiente | `running start 08:52:08 end None` |
| El turno termina solo en la Mac | `ok`, 28.9 s, 10 turnos, US$0.415, tests **85/85** | ledger, `origin_session` = la ventana 1 |
| `enjambre collect` | Una línea al ledger para el turno de la ventana muerta | `{"puente": {"turn": "…f30ca3", "state": "ok"}}` |
| Ventana 2 (nueva) pregunta "se me cerró la ventana, ¿cómo terminó?" | **Misma sesión** `912f16e4` ("reanuda la sesión"). La Mac recordó el encargo y contestó: Radar con 296 productos de 4 cadenas, 13 proyectos en ichisieben.dev, 85/85 OK | `sessions`: 5 turnos, US$1.3479 |

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

*Captura tomada por Playwright en la Mac durante la prueba y traída con `scp`. Es el demo
público, sin datos privados.*

**Fallo real n.º 6 — "Checking your browser".** El primer intento capturó una pantalla de
verificación anti-bot del hosting ("Please wait for up to 5 seconds") y el agente la tomó por un
bloqueo. En el segundo intento esperó y pasó. No es un bloqueo, es un reto que hay que esperar:
si un encargo pide capturar el Radar, conviene decir "espera a que cargue la tabla"
(`browser_wait_for`).

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

**Fallo real n.º 7 — las capturas caían en la raíz del repo.** Playwright resuelve un `filename`
relativo contra la raíz del workspace (el repo), no contra su `--output-dir`. Así, 4 PNG quedaron
sueltos en la copia de farmacias de la Mac, justo la copia que no se puede ensuciar. Pedirlo en el
system prompt ("guarda en `.puente/`") **no bastó**: el modelo lo ignoró. El arreglo es mecánico:
antes del turno, el servidor anota los archivos sin seguimiento (`git ls-files --others
--exclude-standard`); al terminar, mueve los nuevos con extensión de captura a
`.puente/<turno>/`, que está en `.git/info/exclude`. Se verificó con un turno corto:

```
  · mcp__playwright__browser_take_screenshot {"filename": "example-com.png"}
La captura … quedó en …/farmacias/example-com.png, en la raíz del repo.
  (capturas movidas a: .puente/p20261008-035451-66c6be/example-com.png)
-- ok · US$0.5295
```

El modelo cree que la dejó en la raíz, pero el evento `moved` dice dónde está de verdad, y
`git status` queda limpio. Las 4 capturas sueltas se **movieron** (no se borraron) a
`.puente/fuego-p16/`.

**Fallo real n.º 8 — acentos rotos en la ventana de Windows** (`sesi�n`). Cuando `enjambre`
escribe a una tubería (la herramienta Bash de un agente), Python usaba cp1252. Ahora, si la
salida no es una consola, se fuerza UTF-8.

**El coste de persistir, medido.** El último turno (una captura de example.com) costó US$0.53.
Lo trivial no era la tarea: se pagó por reanudar una conversación que ya llevaba cuatro turnos con modelo
de contexto. Ver [Una vez vs sesión persistente](../conceptos/una-vez-vs-sesion-persistente.md):
`enjambre sessions` muestra el acumulado (US$1.88 tras la prueba) justamente para saber cuándo
empezar limpio.

**Otro dato:** como el login es de claude.ai, la sesión del SDK también carga tus conectores de
claude.ai (Gmail, Drive, Notion…: `pending`/`needs-auth` en el `init`). No están en la allowlist,
así que cualquier intento de usarlos se deniega.

## 4. Remote Control (para seguir la sesión desde el celular)

En el CLI de la Mac (2.1.289) se activa con `--remote-control [nombre]`, al abrir una sesión
**interactiva**:

```sh
# en Terminal.app de la Mac, dentro del repo
claude --remote-control farmacias-mac
```

La sesión aparece en claude.ai/code y en la app del celular. Hoy responde
`You must be logged in to use Remote Control` (§22.1). No reemplaza el puente: pasa por la nube
de Anthropic, es para una persona y no deja ledger. Además, la sesión de Remote Control y la del
puente son **distintas**: el puente corre sesiones del SDK, no la ventana interactiva.

## 5. Modelo de amenazas: ¿qué puede hacer quien tenga la clave?

| Quién | Qué puede | Qué no puede |
|---|---|---|
| Alguien con la **clave** y acceso a la Mac (o al ssh) | Mandar turnos a cualquier repo de `~/Documents/Portfolio` que tenga AGENTS.md, con **tu** cuenta de Claude (gasta hasta `--budget` por turno, por defecto US$2; el modelo por defecto de la Mac es Opus). Leer el texto de los turnos y resultados. Con `--edit`, editar archivos de la copia principal del repo en la Mac | Usar herramientas fuera de la allowlist (Windows la calcula y la Mac la vuelve a comprobar contra su AGENTS.md: no puede pedir `Bash(*)`, ni el servidor Playwright entero, ni `browser_run_code_unsafe`, `evaluate`, `file_upload` o `drop`). Salir de Portfolio (`../` → 400). Hacer push (no hay `git push` en la lista) |
| Una **página web** que visita el agente (inyección de instrucciones) | Pedirle al agente que use sus herramientas permitidas: leer archivos del repo y mostrarlos en su respuesta, o escribirlos en un formulario de esa página (`browser_type`). Es el riesgo que queda | Correr código en la Mac por Playwright (las herramientas de código y de subir archivos no están en la lista), editar sin `--edit`, hacer push |
| Un proceso cualquiera de la Mac **sin** la clave | Ver que hay algo en el puerto 8766 | Nada más: sin firma → 401 |
| Una página web abierta en un navegador de la Mac | — | Nada: cualquier petición con `Origin` → 403, antes de mirar la firma. No hay CORS |
| Alguien en la LAN | — | Nada: escucha solo en `127.0.0.1` |
| Quien capture una petición firmada | — | Repetirla: el nonce se acepta una sola vez y solo dentro de ±120 s |

Dónde vive la clave: `~/.config/enjambre/puente.key` (600) en la Mac y
`HKCU\Environment\ENJAMBRE_PUENTE_KEY` en Windows. Cualquier proceso **de tu usuario** en
cualquiera de las dos máquinas puede leerla. El límite real de seguridad es tu cuenta de usuario,
igual que con la llave ssh. Para cambiarla: borrar el archivo, volver a correr `install.sh` y
copiarla otra vez al registro (§3.2).

## 6. Pendiente

- Nada del prompt 16. Login, captura real y prueba de fuego están hechos (§1.3, §3.6).
- `watch` (el de ahora es anterior a F2) no recoge solo los turnos de `send`: hasta que lo
  reinicies, usa `enjambre collect`.
