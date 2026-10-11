# 23 — El mandamiento: toda sesión sabe que la Mac existe

**Qué consigues:** cualquier sesión de Claude Code, en cualquier carpeta y en las dos máquinas,
arranca sabiendo que la Mac es un nodo de trabajo y que se le delega por enjambre. Si intenta
un `ssh mac` a pelo, un hook la frena y le dice qué usar.
**Quién lo hace:** Claude Code (sesión 13, 2026-10-07). Tú, nada: solo reiniciar Claude Code
para que cargue la regla nueva.

## Por qué así: política + mecanismo

Un agente no recuerda nada entre sesiones. Solo sabe lo que entra a su contexto al arrancar.
Lo que se carga en **toda** sesión, en cualquier carpeta, vive en `~/.claude/`: el `CLAUDE.md`
global y los archivos de `~/.claude/rules/`. Ese es el único sitio donde una regla vale para
todos los proyectos sin repetirla en cada uno.

Pero un texto es **política**: el modelo lo lee y casi siempre lo sigue, pero nadie lo obliga.
El **mecanismo** es un hook `PreToolUse`: Claude Code lo ejecuta antes de cada comando, el hook
ve el comando y puede frenarlo con un mensaje. Es el mismo reparto que en el enrutador:
`routing.toml` dice qué se quiere y las funciones puras de `routing.py` lo hacen cumplir. Ficha
de concepto: [Política y mecanismo](../conceptos/politica-y-mecanismo.md).

## Paso 1 — La regla (política)

La regla está en `orquestador/home/.claude/rules/enjambre.md` y se instala en
`~/.claude/rules/enjambre.md`. Está en inglés porque se carga en cada sesión y cuesta tokens.
Dice cuatro cosas:
- La Mac es el nodo para todo lo que no necesita pantalla.
- Se delega con `enjambre.py run|render|status`, nunca con `ssh mac`.
- Antes de la primera delegación, el repo tiene que tener `AGENTS.md` y `docs/DELEGAR.md`.
- Con `confidencial` u `reservado` en la ruta, solo trabaja el worker `claude` en máquinas propias.

**Por qué no va en `CLAUDE.md`, como pedía el prompt.** Hubo dos razones:
- `CLAUDE.md` ya está en 70 de sus 72 líneas, y ese tope es un presupuesto de contexto.
- `CLAUDE.md` se publica en `claude-kit`, y el filtro de publicación rechaza `confidencial`/`reservado`.

Los archivos de `rules/` se cargan igual en todas las sesiones, y `rules/personal.md` ya lo
había probado. **El prompt decía `run|render|check`, pero `check` no existe** (`enjambre.py` tiene
`run`, `watch`, `status`, `history`, `render` y `pull`). La regla dice `status` para no mandar a
todas las sesiones a un comando que falla.

## Paso 2 — El hook (mecanismo)

`~/.claude/hooks/enjambre-guard.sh` está conectado en `settings.json` como `PreToolUse` con
`matcher: "Bash|PowerShell"`. Aquí PowerShell es la terminal principal, y con solo `Bash`
quedaría un agujero. Para cada comando hace esto:
1. Si el comando no contiene `ssh`, sale enseguida sin ejecutar nada más. Corre antes de cada
   comando y casi ninguno lo menciona.
2. Si la carpeta actual tiene un tramo llamado `enjambre`, `enjambre-*` u `orquestador`, lo deja
   pasar. Ahí sí se mantiene la Mac a mano.
3. Parte el comando en palabras. Después de cada `ssh` salta las opciones (`-t`, y también
   `-p 22` o `-o X=y`, que llevan un argumento) y mira el destino. Si es `mac`, `mac6` o
   cualquier `host` de `nodes.toml` distinto de `local`, lo frena con código 2. Con ese código,
   Claude Code no ejecuta el comando y le devuelve el mensaje al modelo.
4. Si el comando lleva `ENJAMBRE_RAW_SSH=1`, o la variable está en el entorno, lo deja pasar y
   escribe una línea en `~/.claude/logs/events.jsonl` con `raw_ssh:1` y el host, nunca el comando.

Si falta `jq`, el JSON llega roto o no se puede leer `nodes.toml`, el hook deja pasar el
comando. Un hook roto no debe impedirte trabajar.

**El detalle de la variable.** En `ENJAMBRE_RAW_SSH=1 ssh mac …` la variable solo existe para el
`ssh`, que corre **después** del hook, así que el hook no la ve en su entorno. Por eso la busca
dentro del texto del comando.

```
PS> bash tests/enjambre_guard_test.sh      # 28 casos; en la Mac también con /bin/bash 3.2
```

## Paso 3 — Plantillas y `/mac`

`enjambre/templates/AGENTS.md` es el contrato mínimo que exige `enjambre run`: qué puede tocar
el worker, qué no, y cómo se verifica. `enjambre/templates/DELEGAR.md` tiene 15 líneas: qué va a
la Mac, los comandos exactos y qué está probado. Las dos salen de los seis `DELEGAR.md` de la
sesión R. La plantilla nueva no recomienda `ssh mac`, porque ahora el hook lo bloquearía.

El paso 0 de la skill `/mac` es el *bootstrap*:
1. Copia las plantillas que falten.
2. Rellena `<repo>` y `<stack>`.
3. Te pide confirmar tres líneas: `<kinds>`, `<tests-cmd>` y `<off-limits>`.
4. Comprueba que no quede ningún `<marcador>`.
5. Commitea solo esos dos archivos, sin push.

Los pasos de mantenimiento de la skill (despertar la pantalla, `pmset`, `fdesetup`) llevan
`ENJAMBRE_RAW_SSH=1` a propósito. No son trabajo, y así quedan contados.

## Paso 4 — Despliegue y prueba

```
PS> bash kit-update.sh                                    # Windows
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -C ~/Documents/Portfolio/orquestador -- bash kit-update.sh'
```

En la Mac, el `git pull` va por `tmux-run.sh` porque las credenciales están en el llavero
(tutorial 11). Las pruebas se hicieron con un `claude -p` nuevo en un repo temporal. No se usó
`enjambre-sandbox`, porque esa carpeta coincide con `enjambre-*` y el hook la deja pasar.

| Prueba | Windows | Mac |
|---|---|---|
| (a) la sesión nueva cita `# Two machines, one swarm` | sí | sí (salida completa) |
| (b) `ssh mac echo hola` bloqueado con el mensaje | sí | sí |
| (c) con `ENJAMBRE_RAW_SSH=1` pasa (`hola`) y deja `raw_ssh:1` | sí | — |
| (d) `/mac` crea y commitea `AGENTS.md` + `docs/DELEGAR.md` | sí (`7462a01`, 2 archivos) | — |
| suite del hook | 28 OK (bash 5) | 28 OK en bash 3.2.57 y 5.3 |

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*Render de la salida real con `freeze`.*

## Qué no hace (todavía)

- El visor aún no cuenta las líneas `raw_ssh`: se escriben, pero nadie las suma.
- No existe un `enjambre` para "corre este script largo sin agente". Hoy hay dos caminos: un
  TASK con `kind: tests`/`scrape`, o el salto explícito.
- farmacias y Metallum tienen `ssh mac` en su `DELEGAR.md` sin commitear. Lo arreglan sus
  ventanas con la línea de NEEDS-OWNER §16.
