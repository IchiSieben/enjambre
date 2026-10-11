# 30 — Arranque desatendido: que el enjambre vuelva solo después de un reinicio

**Qué consigues:** saber exactamente qué arranca solo en cada máquina y cuándo; que el PC levante
`watch` y `live` aunque nadie haya iniciado sesión; y que el visor no diga «Mac lista» antes de que
la Mac pueda de verdad correr un agente.
**Quién lo hace:** Claude Code (sesión del 2026-10-10, prompt 21 §2). La prueba de fuego del
reinicio la dispara el owner (pide su clave).
**Antes:** [29 — Prioridades](29-prioridades.md).

## 0. El problema

Después de un corte de luz o de una actualización, ¿qué vuelve solo? Hasta hoy nadie lo había
comprobado. Y había una mentira escondida: `watch` marcaba la Mac `ready` en cuanto respondía ssh y
existía la sesión tmux. Pero un agente `claude` necesita el **llavero** de macOS, y el llavero solo
se abre con el login gráfico. Por ssh, `claude auth status` dice `"loggedIn": false`.

## 1. Inventario: qué arranca solo y cómo

**Mac** — todo son LaunchAgents en `~/Library/LaunchAgents/` (dominio `gui/<uid>`, es decir, la
sesión gráfica del usuario):

| Agente | Qué hace | Arranque |
|---|---|---|
| `dev.enjambre.tmux` | sesión tmux `enjambre` (el «escritorio» de los agentes) | RunAtLoad |
| `dev.enjambre.puente` | el puente de mensajes (prompt 16) | RunAtLoad + KeepAlive |
| `dev.enjambre.nodewatch` | marca de ocupado del nodo | cada 60 s |
| `dev.enjambre.smbmount` | monta `Compartido-Mac` / `Backup-Mac` del PC | RunAtLoad + cada 60 s |
| `dev.enjambre.visor` | instantánea del visor del lado Mac | cada 300 s |
| `dev.enjambre.deskflow` | teclado/ratón compartido | RunAtLoad |
| `dev.modelos.server` | gestor de modelos locales | RunAtLoad + KeepAlive |
| `dev.lab-video.lab` | laboratorio de lab-video | RunAtLoad + KeepAlive |

Comprobado con:

```bash
launchctl list | grep -E 'dev\.(enjambre|modelos|lab-video)'
pmset -g | grep -E 'autorestart|womp| sleep'     # autorestart 1 · womp 1 · sleep 0
fdesetup status                                  # FileVault is On
```

Los LaunchAgents **no arrancan antes del login gráfico**. Con FileVault activo, el login automático
está prohibido por macOS, así que después de un reinicio la Mac se queda en la pantalla de
FileVault: sin ssh, sin agentes, sin telemetría, hasta que el owner teclea la clave. Es el límite
real, y es una decisión del owner (NEEDS-OWNER §26.1).

**PC (Windows)** — tareas programadas en `\enjambre\`:

| Tarea | Qué corre | Disparador |
|---|---|---|
| `watch` | `enjambre.py watch`: presencia de nodos, cola, recogida de trabajos largos | al iniciar sesión (+1 min) |
| `live` | `enjambre.py live --serve`: `live.json` del visor cada 10 s, planificador | al iniciar sesión (+1 min) |
| `visor` | `tools/visor/build.py` cada 5 min | horario, solo con sesión |
| `boot-watch`, `boot-live` | lo mismo con `--boot` | **al encender, sin sesión** (las instala el owner) |

`watch.lock` y `live.lock` son bloqueos del sistema operativo: si el proceso muere, se sueltan
solos. Un `watch.pid` viejo nunca impide el arranque.

## 2. `ready` solo cuando `claude` puede trabajar

La sonda de `watch` (`presence.probe`) hacía un ssh con `tmux has-session`. Ahora, en la misma
llamada, le pide a la sesión tmux que ejecute `claude auth status`:

```bash
tmux -L enjambre run-shell 'claude auth status 2>&1 | grep -c "\"loggedIn\": true"'
```

El patrón es `"loggedIn": *true` (con `grep -E`), así que también vale si `claude` emite el JSON
compacto.

- rc 0 → `ready` (sesión tmux y `claude` con sesión iniciada).
- rc 3 → `ssh` (responde, pero el llavero está cerrado: aún no hubo login gráfico).
- rc 255 → cae a ping, como antes.

> **LEARN: `tmux run-shell` como puerta a la sesión gráfica.** El servidor tmux lo lanzó un
> LaunchAgent, así que vive en la sesión de seguridad gráfica (la del llavero). `run-shell` ejecuta
> el comando *dentro de ese servidor*, no en la sesión ssh: por eso ve el llavero abierto. Tarda
> 0,5 s, no abre ventana ni deja archivos. Alternativa: `launchctl asuser <uid> …`, que exige root.

Medido: `probe('mac')` → `ready` en 0,58 s; con un comando que falla a propósito, rc 3 → `ssh`.

Un detalle que habría roto el criterio de 5 min: `watch` solo hace la sonda completa (ssh) cuando
el ping cambia o cada 5 min. Tras un reinicio, el ping vuelve primero, la sonda da `ssh` (aún sin
login) y nada más cambia… durante 5 min. Ahora, mientras el ping responde y el nodo no está
`ready`, la sonda completa va en cada vuelta (30 s).

## 3. El PC sin sesión: tareas `--boot`

Una tarea «al iniciar sesión» no corre si el PC se reinicia por una actualización y nadie entra.
`tools/install-boot-tasks.ps1` (admin) registra `boot-watch` y `boot-live`: disparador **al
encender** (+30 s para la red), principal **S4U** (corre como el usuario, sin guardar contraseña,
en la sesión 0).

- Lo que funciona en S4U: ssh con la clave en archivo (`~/.ssh/id_mac`, sin agente), escribir
  `state/`, el endpoint local del planificador.
- Lo que no: toasts (no hay escritorio) y credenciales de red (no hacen falta).
- **El relevo:** cada bucle `--boot` pregunta en cada vuelta quién está en la consola
  (`WTSGetActiveConsoleSessionId` + `WTSQuerySessionInformationW`). Cuando es el owner, `live`
  suelta lo que haya pausado y ambos salen; las tareas de siempre arrancan 1 min después del login
  con escritorio y toasts. Si la de logon llega mientras la `--boot` aún está a mitad de vuelta,
reintenta el lock durante 3 min en vez de salir: salir con código 0 no dispara el RestartCount
de la tarea, y quedarían las dos fuera. A mano (`python`, no `pythonw`), no espera.

```text
$ python enjambre.py watch --boot
watch --boot: the owner is logged on; the logon task runs watch
```

## 4. Montajes SMB

`Compartido-Mac` no está montado. No es un fallo de `smb-mount.sh`: su estado es `no-keychain`,
porque en el PC todavía no existen el usuario `usuario-smb` ni el recurso (el Bloque A de
NEEDS-OWNER §11). El automontaje sí sobrevive a un reinicio: es un LaunchAgent con RunAtLoad que
reintenta cada 60 s, en cuanto haya clave en el llavero.

## 5. La prueba de fuego

`tools/reboot-check.py` cronometra la vuelta. En modo `mac` espera a que la Mac caiga (avisa si
tiene trabajos corriendo: no se reinicia encima de Landing o lab-video) y mide desde ese
instante. En modo `pc` mide desde el arranque de Windows (`GetTickCount64`). Hitos: ssh responde ·
`ready` · `watch` lo ve · `live.json` 2/2 con telemetría · montajes · un `run --detach` trivial
(`tasks/TASK-reboot-probe.md`, haiku, tope US$0,30) termina `ok`. Escribe una línea en
`state/reboot-check.jsonl`.

El reinicio de la Mac lo dispara el owner, y el motivo es FileVault. Cualquier reinicio que no
sea `authrestart` se queda en la pantalla de desbloqueo previa al arranque: la Mac sin ssh hasta
que el owner vuelva, con lo que tuviera pendiente. `authrestart` pide sudo, y la Mac no tiene
sudo sin contraseña (a propósito). Por eso: `sudo fdesetup authrestart`, tecleado por el owner. Según `man fdesetup`, eso solo desbloquea
el disco una vez: la Mac arranca hasta la ventana de inicio de sesión, donde sshd ya responde pero
no hay LaunchAgents ni llavero. Por eso `reboot-check` también cronometra el login gráfico
(`stat -f %Su /dev/console` deja de ser `root`) y da los tiempos contados desde ahí
(`after_login_s`). «Caída» son 3 fallos seguidos de ssh **y** de ping, no un tropiezo de red.
Pasos y lista del PC: NEEDS-OWNER §26.2–§26.4.

Ensayo en modo `pc` (t0 ya pasado, así que mide que cada hito se cumple y que el trabajo corre):
todos los hitos al instante; el `run --detach` (`reboot-probe-20261010-230314`) terminó `ok`
18 s después de lanzarse. El primer ensayo terminó `incomplete`: el agente encadenó
`mkdir && printf > … && ls` en un solo Bash y la lista de permitidos del worker lo negó; el TASK
ahora pide una herramienta por paso.

**Resultados:** pendientes del reinicio del owner. Se pegan aquí con la salida de `reboot-check`.

## 6. La VPN del trabajo sin romper la LAN (prompt 21 §3)

**El problema** (medido el 2026-10-07, NEEDS-OWNER §15): GlobalProtect anuncia la misma red de
casa (`<red>/24`) con métrica 1. Windows manda entonces los paquetes hacia la Mac por el túnel, y
`ssh mac` muere por timeout. `mac6` (la IPv6 link-local de la Mac) sigue respondiendo, porque la
VPN no toca IPv6 link-local.

**Medir antes de tocar nada.** `tools/vpn-check.ps1` (sin admin) se corre dos veces, con la VPN
apagada y con la VPN encendida:

```text
PS> powershell -ExecutionPolicy Bypass -File tools\vpn-check.ps1
== winning route to <ip>
InterfaceAlias InterfaceIndex DestinationPrefix NextHop RouteMetric
Ethernet                    4 <red>/24          0.0.0.0         256
...
ssh mac true: OK in 161 ms
ssh mac6 true: OK in 193 ms
== verdict
No VPN adapter up: this is the baseline. Run again with the VPN on.
```

> **LEARN: la ruta más específica gana antes que la métrica.** Windows elige la ruta por el
> prefijo más largo y solo desempata por métrica (de la ruta más la de la interfaz). Dos /24
> compiten por métrica, y la de la VPN (1) gana a casi todo. Un /32 hacia la Mac gana siempre, sin
> importar métricas, y solo saca del túnel a la Mac: si el CONFIDENCIAL tuviera un host en esa misma /24,
> seguiría yendo por la VPN. Alternativa: una ruta /24 con métrica baja. Pierde contra la de la
> VPN y además se llevaría la red entera.

**La ruta, lista para aplicar.** `tools/vpn-route.ps1 -Apply` (admin) añade `<ip>/32` por la
interfaz LAN. La IP sale de `ssh -G mac` y el índice de la interfaz se calcula en el momento, así
que no hay nada escrito a mano que pueda quedarse viejo. Con `-Persistent` sobrevive al reinicio;
`-Remove` la quita.

**Plan B automático.** Hay clientes VPN que borran las rutas locales al conectar. Para ese caso,
`nodes.toml` declara `host_alt = "mac6"` en la Mac. Después de cada vuelta de `watch`:

- Si `mac` no responde, prueba `mac6` (como mucho una vez por minuto).
- Si `mac6` responde, escribe `state/route.json` con el cambio, avisa con un toast y marca el
  nodo con el nivel que dio `mac6`. Así la cola sigue despachando.
- Todo lo que hace ssh a un nodo lee ese archivo:
  - `load_nodes()`, en cada comando;
  - `live`, en cada ciclo;
  - largos, por trabajo, porque `job.json` guarda el alias con el que se lanzó;
  - `build.py` del visor.
- El visor muestra «vía mac6 ¿VPN?» en la tarjeta de la Mac.
- `watch` sigue sondeando `mac`. Cuando vuelve, borra la entrada y avisa «vuelve por mac».
- `ENJAMBRE_HOST_MAC` sigue mandando sobre todo.

Prueba real sin VPN: con un alias primario que no resuelve, `watch` lo dio por caído en 9,3 s. En
la misma vuelta probó `mac6`, que respondió `ready`. El cambio se escribió en 0,4 s y el ssh por
el alias efectivo devolvió `Darwin`. Con el primario de vuelta, la entrada se borró y llegaron dos
avisos: «mac por mac6» y «mac vuelve por mac». Un test cubre el fallo que apareció al
combinarlo con la histéresis de `tick`: comparar el primario contra el nivel «ready vía mac6»
habría notificado una caída cada pocas vueltas. Ahora `tick` compara contra el nivel propio del
primario (`primary_level`).

**Pendiente del owner:** correr `vpn-check.ps1` con la VPN encendida y, según el veredicto,
`vpn-route.ps1 -Apply` (NEEDS-OWNER §15).

## 7. La edad del HANDOFF que se quedaba pegada (prompt 21 §5)

La statusline de Claude Code solo se redibuja cuando pasa algo en la sesión, no con el reloj. Una
barra que decía «⚠HANDOFF 1h» podía seguir diciéndolo tres horas después. Hay dos arreglos, cada
uno donde le toca:

- **La barra muestra la hora**, no la edad: `⚠HANDOFF 21:40` (o `09/10 21:40` si no es de hoy). Una
  hora no envejece. El cambio está en el repo `orquestador` (`home/.claude/statusline.sh`, commit
  `04b69e4`, con un test nuevo: 55/55 OK). `--drift` y el snapshot siguen dando la edad en horas,
  porque el hook de SessionStart y `/ahora` la usan.
- **El visor muestra la edad viva**, porque el visor sí consulta cada 10 s. En cada nodo,
  `collect.py` lee el mtime del `HANDOFF.md` de cada sesión abierta: el del cwd, o el de la raíz
  git, el mismo que juzga la barra. Nunca toma uno de más arriba, que sería de otro proyecto.
  `live.json` lo lleva como `handoff_ts`, y la tarjeta de la sesión dice «HANDOFF hace …»: gris
  hasta 60 min, ámbar desde 60 min y rojo desde 3 h.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Medido con Playwright sobre el visor real: «HANDOFF hace 41 min» en gris y «HANDOFF hace 2.0 h»
en ámbar (`rgb(210, 153, 34)`), sin errores de página. Las sesiones en espera o inactivas (la
lista plegada) llevan la misma marca junto al nombre: medido con 8 ventanas reales, desde
«hace 3 min» en gris hasta «hace 39.8 h» y «hace 19 d» en rojo (`rgb(248, 81, 73)`). La Mac la recibe la próxima vez que corra `kit-update.sh` allí
(NEEDS-OWNER §26.6).
