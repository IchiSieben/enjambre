---
lesson:
  route: build-stack
  order: 12
  level: 2
---

# 11 — El llavero por SSH: un tmux que nace en tu sesión

## Objetivo

Desde Windows, `ssh mac '… claude -p "di hola"'` responde, `gh auth status` dice *Logged in* y
`git pull` funciona, **sin guardar ningún token en un archivo**.

## Por qué

Este es el problema que frenó toda la noche anterior (página 09): por SSH, `claude` decía *Not
logged in* y `gh` daba el token por inválido. El `gh auth login` estaba bien hecho. El problema
era **dónde** quedó guardada la credencial.

macOS guarda las credenciales en el **llavero de inicio de sesión**, y ese llavero se abre para
una **sesión de seguridad** concreta: la que nace cuando inicias sesión en la pantalla. Cada
proceso hereda la sesión de su padre:

- La Terminal de la Mac → hija de tu sesión gráfica → llavero abierto.
- Un comando que entra por `ssh` → hijo de `sshd` → otra sesión → *"User interaction is not
  allowed"*.

Hay dos salidas. Una es sacar el token del llavero y dejarlo en un archivo, lo que es más
frágil y menos seguro. La otra es **ejecutar los comandos dentro de un proceso que ya vive en
tu sesión gráfica**. Esa segunda es la que usamos aquí.

¿Quién arranca algo dentro de tu sesión gráfica sin que tú abras una ventana? **launchd**, el
sistema de arranque de macOS. Un *LaunchAgent* en `~/Library/LaunchAgents/` se carga en el
dominio `gui/<uid>`, el de tu sesión. Si lo que arranca es un servidor `tmux`, cada ventana
nueva de ese servidor hereda la sesión y puede abrir el llavero, aunque la pida un comando que
llegó por SSH.

## Pasos

### 1. El LaunchAgent (en la Mac, sin sudo)

Plantilla en el repo: `config/dev.enjambre.tmux.plist`. launchd **no expande** `~` ni `$HOME`,
así que las rutas tienen que ser absolutas. La plantilla usa `__HOME__` y la instalación lo
sustituye:

```bash
mac$ mkdir -p ~/Library/LaunchAgents ~/enjambre-logs
mac$ sed "s|__HOME__|$HOME|g" config/dev.enjambre.tmux.plist > ~/Library/LaunchAgents/dev.enjambre.tmux.plist
mac$ plutil -lint ~/Library/LaunchAgents/dev.enjambre.tmux.plist
mac$ launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.enjambre.tmux.plist
mac$ tmux -L enjambre ls
```

Salida esperada:

```
…/dev.enjambre.tmux.plist: OK
enjambre: 1 windows (created Sun Oct  4 08:10:27 2026)
```

Las claves que importan del plist, y por qué:

| Clave | Por qué |
|---|---|
| `/opt/homebrew/bin/tmux` con ruta completa | launchd no lee tu `PATH` |
| `-L enjambre` | Usa un **socket propio**. Si ya hay un servidor tmux en el socket por defecto que nació por SSH, sin `-L` te enganchas a ese y te quedas sin llavero, sin ningún error que lo avise |
| `RunAtLoad` = true | Arranca al cargar el agente y en cada inicio de sesión |
| `AbandonProcessGroup` = true | `tmux new-session -d` deja el servidor en segundo plano y termina. Sin esta clave, launchd mata el servidor cuando el trabajo "termina" |
| `EnvironmentVariables` (`PATH`, `HOME`, `LANG`) | Las ventanas heredan el entorno del servidor, no el de tu SSH |

> El `launchctl bootstrap` funcionó **lanzado por SSH** porque había una sesión gráfica abierta
> del mismo usuario (`stat -f %Su /dev/console` = tú). Si no la hay, el agente arranca la
> próxima vez que inicies sesión en la pantalla.

### 2. La prueba que decide todo

Antes de cambiar código, comprueba que el llavero se abre **dentro** del tmux y no fuera:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

*Render de la salida real con `freeze`; la cuenta está tapada como `<cuenta>`.*

### 3. `tmux-run.sh`: correr cualquier comando ahí dentro

`tmux-run.sh` (en la raíz del repo, se copia a `~/.enjambre/` de la Mac) hace cinco cosas:

1. Comprueba que existe la sesión `enjambre` en `-L enjambre`. **Si no existe, falla** (código 75)
   en vez de crearla, porque una sesión creada por SSH no tendría llavero.
2. Guarda la entrada estándar en un archivo: una ventana de tmux no recibe el stdin de tu SSH.
3. Escribe un script con `cd`, las variables `ENJAMBRE_*` (el entorno de SSH no viaja) y el
   comando, entrecomillado con `printf %q`.
4. Abre una ventana con `tmux -L enjambre new-window -d -t enjambre` y **espera a que aparezca
   un archivo `rc`**. Usa sondeo y no `tmux wait-for`, porque un archivo no puede perderse una
   señal y así el tiempo límite es fácil de poner.
5. Devuelve la salida estándar, la de errores y **el mismo código de salida**, como si el
   comando hubiera corrido en tu SSH.

```bash
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -C ~/Documents/Portfolio/orquestador -- git pull --ff-only'
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -C ~/Documents/Portfolio/orquestador -t 600 -- bash kit-update.sh'
```

`worker.sh` se re-lanza a sí mismo a través de `tmux-run.sh`. Así, el `fetch`, el `claude -p` y
el commit corren con el llavero abierto. De paso, el trabajo sobrevive a un corte de SSH.

### 4. Quién pushea: una sola puerta

Con el llavero abierto, la Mac **ya podría** pushear. No lo hace: `ENJAMBRE_NODE_PUSH` vale 0 por
defecto. Windows trae la rama por SSH, pasa gitleaks **solo sobre los commits nuevos** y pushea
él. Hay una sola puerta hacia GitHub, y está en la máquina que tiene el escáner.

### 5. El primer trabajo real

```bash
PS> $env:ENJAMBRE_MAX_TURNS=25
PS> python enjambre.py run --node mac --repo orquestador --task tasks/TASK-statusline-decimal.md --worker claude
```

La tarea: con `LC_ALL=es_ES.UTF-8`, la barra mostraba `$12,00` en vez de `$12.35`.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## Qué salió mal de verdad

1. **zsh no parte variables en palabras.** El primer intento usó `T="tmux -L enjambre"; $T ls`
   y zsh respondió `command not found: tmux -L enjambre`. En bash funciona y en zsh no: el
   contenido de una variable sin comillas sigue siendo **una** palabra. Arreglo: escribir el
   comando entero, o usar un script `bash`. Es el mismo tipo de sorpresa que la variable `path`
   de la página 07.
2. **`git pull` falló aunque el llavero ya funcionaba**: *"There is no tracking information for
   the current branch"*. El repo de la Mac se armó desde un `git bundle` con `init` + `fetch`
   (página 07), así que `master` no tenía rama remota asociada. Arreglo, una sola vez por repo:
   `git branch --set-upstream-to=origin/master master`. Lección: no culpes al primer sospechoso.
   El error de credenciales ya había desaparecido; este era otro.
3. **`kit-update.sh` por SSH tropezó con un bug viejo**: los `.sh` estaban en git sin bit de
   ejecución, así que `./sync-to-home.sh` daba *permission denied*. Arreglo en orquestador:
   `git update-index --chmod=+x` para los 14 scripts. Windows nunca lo notó porque Git Bash
   ignora ese bit.
4. **El agente no pudo editar el archivo principal.** Claude Code trata como **sensible**
   cualquier ruta bajo `.claude/` y rechazó el `Edit` sobre `home/.claude/statusline.sh`, aunque
   `Edit` estaba en `--allowedTools`. El agente hizo lo correcto:
   - escribió el test, que sí podía tocar;
   - **no intentó saltarse el permiso** con Bash;
   - dejó el parche completo en su mensaje final.

   El parche también quedó guardado en `out.json`, en `permission_denials`. En la revisión se
   aplicó tal cual, se probó y se commiteó en la misma rama, con el mensaje que explica de dónde
   salió.
5. **El ledger dijo `exit 0` con la tarea a medias.** Que el agente termine sin error no
   significa que haya cumplido los criterios. **Resuelto el 4 oct:** `worker.sh` copia los
   `permission_denials` de `out.json` (herramienta y archivo, relativo al worktree) y
   `enjambre.py` marca el trabajo `incomplete` si hay alguno, aunque `exit` sea 0. Probado con un
   trabajo real que pedía escribir en `.claude/`:

   ```text
   PS> python enjambre.py run --node mac --repo enjambre-sandbox --task tasks/TASK-denial-probe.md --worker claude
   {"id": "denial-probe", ..., "exit": 0, "status": "incomplete",
    "permission_denials": [{"tool": "Write", "target": ".claude/notes.md"}], ..., "cost_usd": 0.16}
   ```
   (`target` mostrado ya con la ruta relativa de la versión final.)
6. **(4 oct) Un test que pasaba por SSH falló dentro del tmux.** `statusline_test.sh` con el bash
   3.2 de macOS murió con `seen�: unbound variable`. Por SSH el locale era `C`; el tmux nace de
   launchd con `en_US.UTF-8`. Con UTF-8, bash 3.2 lee los bytes de `»` en `"$seen»"` como parte
   del nombre de la variable. Arreglo en orquestador (`89ae2fd`): `${seen}»`. Lección: el entorno
   del tmux no es el de SSH, y los tests hay que correrlos donde correrán los trabajos.
7. **`result.json` no llegó a la rama**: el `.gitignore` de orquestador es una lista blanca
   (`*`) y lo ignoraba. Arreglo en `worker.sh`: `git add -f result.json`.

## Cómo verificar

```bash
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -- security show-keychain-info ~/Library/Keychains/login.keychain-db'
```

Debe decir `no-timeout` (o un tiempo), nunca *User interaction is not allowed*.

```bash
PS> ssh mac 'launchctl print gui/$(id -u)/dev.enjambre.tmux | grep -E "state|pid"'
PS> python enjambre.py status
```

`launchctl print` dirá `state = not running` y `last exit code = 0`. **Es lo correcto**: el
trabajo de launchd es el cliente `tmux new-session -d`, que arranca el servidor y termina. El
servidor sigue vivo (gracias a `AbandonProcessGroup`), y eso es lo que muestra `tmux -L enjambre ls`.

Tras reiniciar la Mac e iniciar sesión, `tmux -L enjambre ls` debe mostrar la sesión
`enjambre` sin que hagas nada. **Verificado el 4 oct** tras un reinicio real:

| Paso | Quién | Qué se vio |
|---|---|---|
| 1. Reinicio | Tú | Arranque 09:26:04 |
| 2. Desbloqueo de FileVault | Tú, por `ssh mac` con la contraseña | `System successfully unlocked`. Aún sin sesión: no hay llavero ni tmux |
| 3. Inicio de sesión | Tú, con **teclado por cable** (Bluetooth y Deskflow no existen todavía) | Sesión gráfica |
| 4. LaunchAgent `dev.enjambre.tmux` | launchd, 09:28:58.896 | `enjambre: 1 windows (created … 09:28:59)` |
| 5. Prueba del llavero | Claude Code, por `tmux-run.sh` | `gh auth status` ✓ (keyring) · `git pull` ✓ · `claude -p "di hola"` → `¡Hola!` |

El orden importa: entre el paso 2 y el 3 la Mac **responde a SSH pero no puede trabajar**
(`enjambre status` la ve en nivel `ssh`, página 15). Por eso un corte de luz necesita que alguien
inicie sesión en la pantalla. Quién abrió Deskflow en ese arranque: página 01 §7b.

## Autoevaluación

1. Por SSH, `claude` dice *Not logged in*, pero en la Terminal de la Mac funciona. ¿Por qué?
   <details><summary>Respuesta</summary>El llavero está abierto para la sesión de seguridad de tu
   inicio de sesión gráfico. Un proceso que nace de <code>sshd</code> pertenece a otra sesión y
   no puede leerlo.</details>
2. ¿Para qué sirve `-L enjambre` si ya hay un tmux funcionando?
   <details><summary>Respuesta</summary>Para no engancharse a un servidor tmux nacido por SSH en
   el socket por defecto. Ese servidor no tiene llavero, y el error sería silencioso.</details>
3. El agente dejó el ledger en `exit 0`, pero la rama solo trae el test. ¿Qué te dice eso del
   ledger?
   <details><summary>Respuesta</summary>Que <code>exit</code> mide si el proceso terminó sin
   error, no si se cumplió la tarea. Los criterios de "hecho" se verifican aparte: corriendo los
   tests sobre la rama y leyendo los <code>permission_denials</code>.</details>
