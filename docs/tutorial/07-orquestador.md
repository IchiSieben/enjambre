# 07 — El kit de configuración en la Mac: repos, port y config en capas

## Objetivo

Tener los repos `orquestador` y `enjambre` en `~/Documents/Portfolio/` de la Mac, medir qué falla
**antes** de adaptar el kit a macOS, instalarlo en `~/.claude` y comprobar que pasa los tests con
los dos `bash` de la Mac. Y entender por qué la configuración personal no viaja con la pública.

## Por qué

El kit de configuración de Claude Code (`orquestador`: barra de estado, hooks, reglas, skills) se
escribió en Windows. Antes de portarlo conviene medir qué se rompe en la Mac tal como está: esa
lista es la justificación del port y, después, la prueba de que funcionó (mismo comando, antes y
después). Y como el kit se instala **sobre** `~/.claude`, que también guarda cosas tuyas, hay que
decidir qué parte es pública y compartible y qué parte es personal (sección 4).

## Pasos

### 1. Clonar sin credenciales en la Mac: `git bundle`

Lo normal sería `gh repo clone`, pero `gh` por SSH no funcionaba en la Mac (ver
`09-agente-desatendido.md`). En vez de copiar un token de una máquina a otra, se movió **el
repositorio**, no la credencial:

```powershell
# En Windows: empaquetar la rama remota (no la local: así no viaja trabajo sin pushear)
PS> git bundle create orq.bundle origin/master
PS> scp orq.bundle mac:/tmp/orq.bundle
```

```
# En la Mac (por SSH): repo nuevo + importar la rama + apuntar el remoto a GitHub
mac$ git init -b master ~/Documents/Portfolio/orquestador
mac$ cd ~/Documents/Portfolio/orquestador
mac$ git fetch --update-head-ok /tmp/orq.bundle refs/remotes/origin/master:refs/heads/master
mac$ git reset --hard master
mac$ git remote add origin https://github.com/IchiSieben/orquestador.git
```

**Concepto:** un *bundle* es un repositorio git empaquetado en un solo archivo. Se puede clonar
o hacer `fetch` desde él como si fuera un remoto. Sirve para pasar historial entre máquinas sin
red hacia GitHub o, como aquí, sin credenciales en el destino.

### 2. Línea base: `verify.sh` en la Mac, sin cambiar nada

```powershell
PS> ssh mac 'cd ~/Documents/Portfolio/orquestador && bash verify.sh'
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Resultado (2026-10-04): **6 FALLA**, 34 avisos de **DERIVA**, el resto OK.

| Falla | Por qué falla en la Mac (esperado) |
|---|---|
| `statusline.sh` / `settings.json` | La barra todavía no está instalada en `~/.claude` de la Mac |
| `xreview` llama a `agy` | Antigravity no está instalado en la Mac (fase 5) |
| `sessions.json` ≥ 41 sesiones | El historial de sesiones vive en Windows |
| `agy --version` / credencial OAuth | Antigravity no está instalado ni logueado |

Las 34 líneas DERIVA dicen lo mismo de otra forma: los archivos del kit están en el repo pero no
en `~/.claude` de la Mac. Es lo que resolverá la instalación **después** del port; no se corrió
a propósito.

### 3. Después del port: tests, instalación, medición

**3.1 El port, en una línea por cambio.** (Hecho en Windows, antes de probar en la Mac.) Una
guarda que exige bash ≥ 4.2 y se re-ejecuta en el bash de Homebrew si arranca en el de fábrica;
`stat` con formato BSD o GNU según `$OSTYPE`; `jq` buscado también en `/opt/homebrew/bin`;
`@mac`/`@win`/`@linux` en la primera línea de la barra. Este es el diff de la línea de `stat`
(de `stat -c '%Y'` a un formato elegido por sistema, `STAT_FMT`), con la barra al fondo:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

**3.2 Los tests, con el bash de fábrica y con el de Homebrew.** macOS trae **bash 3.2** (de 2007;
Apple no lo actualiza por la licencia GPLv3). Homebrew instala bash 5. El kit exige bash ≥ 4.2 y,
si arranca en el viejo, se **re-ejecuta** solo en el nuevo (*guard*). Por eso los tests se
corren con los dos: con `/bin/bash` se prueba el guard.

```
mac$ /bin/bash tests/statusline_test.sh              → 49 OK, 0 fallas
mac$ /bin/bash tests/hooks_test.sh                   → 38 OK, 0 fallas
mac$ /opt/homebrew/bin/bash tests/statusline_test.sh → 49 OK, 0 fallas
mac$ /opt/homebrew/bin/bash tests/hooks_test.sh      → 38 OK, 0 fallas
```

Se corrieron dentro de `tmux` (`tmux new -d -s phaseb "…; tmux wait-for -S phaseb-done"` y luego
`tmux wait-for phaseb-done`): si se corta el SSH, el trabajo sigue.

**3.3 Instalar en `~/.claude`, dos veces (la segunda no debe cambiar nada).** Antes, copia de
seguridad (`~/.claude` → `~/.claude.pre-kit-20261004`; el script además hace la suya).

```
mac$ bash sync-to-home.sh   → 34 added, 0 updated, 1 merged, 0 unchanged
mac$ bash sync-to-home.sh   → 0 added, 0 updated, 0 merged, 35 unchanged
```

**Concepto — idempotencia:** correr dos veces da el mismo resultado que una. Es lo que permite
re-ejecutar un instalador sin miedo.

`verify.sh` después: sin deriva, y solo quedan en rojo las 3 comprobaciones propias de otra
máquina (Antigravity y el archivo de sesiones de Windows):

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

**3.4 La barra de estado en la Mac.**

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

(El símbolo de rama sale como un cuadrado: `freeze` ignora la fuente elegida; en la terminal
real se ve bien.)

**3.5 ¿Cuánto cuesta dibujar la barra?** Mismo método que en Windows: 20 llamadas × 3
repeticiones, con un JSON de ejemplo.

| Ruta | Windows + Git Bash | Mac M2 Pro |
|---|---|---|
| Piso (arranque de bash + una llamada a `jq`, sin git) | ~225 ms | **~30 ms** |
| Render completo (git, HANDOFF, archivos sucios) | ~476 ms | **~45 ms** |

**Por qué tanta diferencia:** en Windows, crear un proceso cuesta ~50 ms y un `fork` de bash
~28 ms (Git Bash emula `fork` sobre Windows). En macOS, `fork` es nativo y cuesta muy poco.
Arrancar desde `/bin/bash` (que se re-ejecuta en bash 5) añade solo ~2–4 ms.

### 4. Config en capas: lo público y lo personal

El kit tiene una versión **pública** (`claude-kit`, un repo abierto) y se instala sobre
`~/.claude`, que también contiene reglas personales. La regla que salió de un tropiezo real
(abajo): **nada personal vive solo en un archivo que un script va a sobrescribir, y nada
personal viaja en el kit público.**

| Capa | Dónde | Idioma | Viaja en el kit público |
|---|---|---|---|
| Reglas de seguridad generales (no tocar procesos que no lanzaste; borrado recursivo solo en el repo o en un scratch, con sus cuatro comandos) | `CLAUDE.md` del repo | inglés | sí |
| Reglas personales (el "modo autónomo" del owner) | `home/.claude/rules/personal.md` | español | **nunca** |

Cómo se garantiza lo segundo: `build-kit.sh` borra cualquier copia de `personal.md` del kit y
**falla** si el texto "MODO AUTÓNOMO" aparece en cualquier parte del kit (la comprobación se
probó en las dos direcciones sobre un kit de prueba). Para que `personal.md` se cargue no hace
falta ningún `@import`: se verificó con una sesión nueva de `claude -p`, sin herramientas y fuera
de cualquier repo, que citó el punto 5 de la regla y nombró el archivo.

En la Mac, `kit-update.sh` trae también `personal.md` (está en el espejo, no en el kit) y hay que
reiniciar Claude Code después. Eso lo anticipa el handoff del orquestador; no se verificó en la
Mac.

## Qué salió mal de verdad

**1. `git clone -b master orq.bundle` → "Remote branch master not found".** Al empaquetar
`origin/master`, la referencia dentro del bundle es `refs/remotes/origin/master`, no
`refs/heads/master`. Arreglo: `fetch` explícito de esa referencia.

**2. `git fetch … :refs/heads/master` → "refusing to fetch into branch checked out".** Git
protege la rama activa; `--update-head-ok` le dice que sabemos lo que hacemos (el repo estaba
vacío).

**3. `./sync-to-home.sh` → `permission denied`.** Los scripts se commitearon desde Windows, que
no guarda el bit de ejecución (`100644` en git). En la Mac no son ejecutables. Arreglo inmediato:
`bash archivo.sh`. Arreglo de fondo (en el repo del kit): `git update-index --chmod=+x`.

**4. `kit-update.sh` falla en la Mac.** Empieza con `git pull`, que por SSH falla porque las
credenciales están en el llavero bloqueado (ver `09-agente-desatendido.md`). Como la copia ya
estaba al día (`d100eb4`, igual a `origin/master`), se corrieron sus pasos a mano.

**5. Con el sistema en español, la barra mostraba `$12,00` en vez de `$12.35`.** Con
`LC_ALL=es_ES.UTF-8`, `printf` espera coma decimal, rechaza `12.3456` ("número inválido") y la
barra imprime un número falso, peor que ninguno. Reportado al kit; la corrección prevista es
fijar `LC_NUMERIC=C` al inicio de `statusline.sh`, más un test con locale español.

**6. La instalación del kit pisó reglas personales (en Windows).** Al correr `kit-update.sh` en
Windows para resolver 8 archivos con DERIVA, la primera ejecución sobrescribió **tres reglas que
vivían solo en el `~/.claude/CLAUDE.md` en vivo**: las dos de seguridad y el modo autónomo.
Estaban a salvo porque el script respalda antes de copiar (`~/.claude.bak-20261004-052034`). La
sesión lo detectó y restauró la versión en vivo desde ese respaldo, guardando la versión del
sync aparte:

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Después el owner decidió la partición de la sección 4 (commit `1247193` del orquestador). El
tope de líneas de `CLAUDE.md` subió de 70 a 72: una regla de seguridad no se recorta para que
quepa.

## Cómo verificar

```powershell
PS> ssh mac 'cd ~/Documents/Portfolio/orquestador && bash verify.sh'
```

Debe terminar sin DERIVA y con solo tres fallas, todas propias de otra máquina
(`sessions.json`, `agy --version`, credencial OAuth de Antigravity). Y los cuatro tests de la
sección 3.2 en verde. Por último, dos pasadas seguidas de `sync-to-home.sh`: la segunda debe
decir `0 added, 0 updated, 0 merged` y todo `unchanged`.

## Autoevaluación

1. ¿Por qué se empaqueta `origin/master` y no `master` con `git bundle`?
   <details><summary>Respuesta</summary>Para que no viaje trabajo local sin pushear; el bundle
   contiene solo lo que ya está en el remoto.</details>
2. ¿Qué prueba correr los tests con `/bin/bash` además del bash de Homebrew?
   <details><summary>Respuesta</summary>El *guard* de re-ejecución: con bash 3.2 de fábrica, el
   script debe relanzarse solo en bash ≥ 4.2.</details>
3. ¿Por qué `personal.md` no viaja en el kit público y cómo se impide por accidente?
   <details><summary>Respuesta</summary>Porque contiene reglas personales; `build-kit.sh` borra
   cualquier copia y falla si el texto "MODO AUTÓNOMO" aparece en el kit.</details>
