# 02 — Homebrew, Claude Code y el shell de la Mac

## Objetivo

La Mac con Homebrew, Claude Code, `gh` y las herramientas base, todas visibles también para los
comandos que llegan por SSH (`ssh mac 'git --version'` responde, no dice `command not found`).

## Por qué

Homebrew es el gestor de paquetes de facto en macOS. Las herramientas base (`jq`, `tmux`,
`coreutils`, un `bash` moderno, `shellcheck`) son las que usan los scripts que despachan trabajo:
si falta una, la tarea falla a mitad de camino y sin nadie mirando.

Y hay un concepto que decide si todo funciona por SSH: **zsh lee archivos distintos según cómo se
abra**. Un comando remoto (`ssh mac "cmd"`) abre un zsh *no interactivo y no-login*, que solo lee
`~/.zshenv`:

| Archivo | Terminal de la Mac | `ssh mac "comando"` |
|---|---|---|
| `~/.zshenv` | sí | **sí** |
| `~/.zprofile` | sí (es login) | no |
| `~/.zshrc` | sí (es interactiva) | **no** |

Si el PATH vive en `.zshrc`, todo funciona en la Terminal de la Mac y todo falla por SSH.

## Pasos

### 1. Homebrew (a mano: pide tu contraseña)

```
mac$ /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

El instalador oficial instala por su cuenta las Xcode Command Line Tools si faltan, y pide la
contraseña (sudo). Al terminar imprime unas líneas "Next steps": no las pegues todavía, primero
lee el paso 3.

### 2. Claude Code y GitHub CLI

```
mac$ curl -fsSL https://claude.ai/install.sh | bash
```
```
mac$ brew install gh
```

El login de `gh` se hace con navegador, en la Terminal de la Mac (no por SSH):

```
mac$ gh auth login -h github.com
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

La captura muestra HTTPS, *Login with a web browser*, un código de un solo uso (tapado) y
`Logged in as …`. Lo último que imprime es *"You were already logged in to this account"*:
ya había un login previo. Ver "Qué salió mal" para por qué esto importa.

### 3. El PATH va en `~/.zshenv`

```
mac$ cat ~/.zshenv
export PATH="$HOME/.local/bin:$PATH"
eval "$(/opt/homebrew/bin/brew shellenv zsh)"
```

Prueba, desde Windows, de que quedó bien:

```
PS> ssh mac 'command -v git jq tmux shellcheck gh'
/opt/homebrew/bin/git
/opt/homebrew/bin/jq
/opt/homebrew/bin/tmux
/opt/homebrew/bin/shellcheck
/opt/homebrew/bin/gh
```

### 4. Herramientas base (idempotente: primero se comprueba)

```powershell
PS> ssh mac 'brew list --versions git jq bash coreutils tmux shellcheck gh'
```
```powershell
PS> ssh mac 'brew list --cask --versions visual-studio-code ghostty deskflow'
```

Si falta alguno: `brew install git jq bash coreutils tmux shellcheck` y
`brew install --cask visual-studio-code ghostty`. En esta Mac ya estaban todos:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

### 5. Carpetas simétricas

```powershell
PS> ssh mac 'mkdir -p ~/Documents/Portfolio'
```

La Mac usa la misma estructura que Windows (`Documents/Portfolio/<repo>`), porque varias
herramientas resuelven rutas relativas a esa carpeta.

## Qué salió mal de verdad

1. **`claude: command not found`** justo después de instalar Claude Code. El instalador lo pone
   en `~/.local/bin`, que no estaba en el `PATH`. Arreglo: PATH y `brew shellenv` en
   `~/.zshenv`, no en `.zshrc` (ver "Por qué").
2. **`brew install deskflow` → "Refusing to load cask from untrusted tap".** Homebrew exige
   confiar explícitamente en los taps de terceros: `brew trust deskflow/tap` y luego instalar.
3. **`brew doctor`: "Your Command Line Tools are too outdated".** Se arregla desde Ajustes →
   General → Actualización de software (instala la versión nueva de las CLT). Reapareció tras
   actualizar macOS: ver `06-actualizar-macos.md`.
4. **`gh auth status` por SSH: "The token in default is invalid".** La sesión 1 lo diagnosticó
   como "el login caducó". Después se vio que probablemente era otra cosa: el token estaba bien,
   pero guardado en el **llavero** de macOS, que una sesión SSH no puede abrir. Evidencia: la
   captura 07 muestra un `gh auth login` exitoso en la Mac antes de esa sesión, y más tarde
   `gh auth login` respondió "ya estabas logueado". Que ese login previo sea la causa del
   "invalid" es una inferencia; lo que sí está medido (en la corrida nocturna) es el bloqueo del
   llavero por SSH. Detalle y opciones en `09-agente-desatendido.md`.

## Cómo verificar

```
PS> ssh mac 'command -v git jq tmux shellcheck gh'
```

Debe imprimir cinco rutas bajo `/opt/homebrew/bin/`, y

```
PS> ssh mac 'claude --version'
2.1.289 (Claude Code)
```

(Versiones al 2026-10-04: Homebrew 7.0.7, Claude Code 2.1.289.)

## Autoevaluación

1. ¿Por qué el PATH va en `~/.zshenv` y no en `~/.zshrc`?
   <details><summary>Respuesta</summary>`ssh mac "cmd"` abre un zsh no interactivo y no-login, que
   solo lee `~/.zshenv`.</details>
2. ¿Qué significa "idempotente" en el paso 4?
   <details><summary>Respuesta</summary>Se comprueba antes de instalar y, si ya está, no se toca:
   correrlo dos veces da el mismo resultado que una.</details>
3. `gh auth login` funciona en la Terminal de la Mac pero falla por SSH. ¿Dónde buscas?
   <details><summary>Respuesta</summary>En dónde se guardó el token: el llavero de macOS no se
   abre desde una sesión SSH.</details>
