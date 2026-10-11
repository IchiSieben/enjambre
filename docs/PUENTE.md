# El puente — evaluación y diseño (sesión P, prompt 16)

> Estado (2026-10-08): **construido, desplegado y con la prueba de fuego pasada con modelo.**
> Ventana de Windows cerrada a mitad de turno → el turno terminó en la Mac → otra ventana
> continuó la misma sesión (tutorial 26 §3.6).

## 1. Qué se pedía

Que Windows pueda **hablar** con un Claude Code de la Mac que **siga vivo** entre mensajes, y no
solo disparar un `claude -p` que nace, hace una cosa y muere. Además, que esa sesión tenga ojos
y manos (navegador, capturas) y que todo quede en el ledger, en `busy.json` y en `enjambre status`.

## 2. Evaluación de lo que ya existe (parte 2)

Se miraron tres cosas que ya existen y una oficial que apareció durante la búsqueda. **No se
instaló ninguna.** Agentrooms necesita un backplane, que por defecto está en la nube de un
tercero. thurbox se instala con `curl … | sh`, y un script de terceros requiere tu OK
(MODO AUTÓNOMO §5). La evaluación se hizo leyendo el README y la documentación de cada una,
contra lo que enjambre necesita.

| Criterio de enjambre | Agentrooms (claude-code-by-agents) | thurbox | Mensajería entre sesiones (oficial) | Puente propio |
|---|---|---|---|---|
| Licencia | MIT | MIT (Rust) | parte de Claude Code | nuestro |
| ¿Qué escucha o a quién llama? | La app (macOS/iOS) y los agentes hablan por HTTPS con un **workspace** de OpenAgents. El público es `workspace-endpoint.openagents.org`; se puede alojar uno propio (otro repo) | Nada en red: sesiones en tmux, y las remotas por SSH (`hosts.toml`) | Mismo equipo: sockets Unix. Entre máquinas: **Remote Control**, a través de la nube de Anthropic | `127.0.0.1:8766` en la Mac. Se llega por `ssh mac curl`, con firma HMAC |
| Persistencia y reanudación | `WorkspaceStore`; el README no explica la reanudación | Sí: la sesión vive en tmux y sobrevive a reinicios de la TUI | La sesión viva es la interactiva; si se cierra, se acaba | `session_id` en disco y `resume` del SDK; un turno a medio correr pasa a `interrupted` |
| Ledger, coste por turno | No | No (run logs propios) | No | Una línea por turno, con `cost_usd` del SDK |
| Enrutador, `busy.json`, cola | No | No | No | Cola de un turno por sesión, `busy.json` sin pisar el de otro |
| Allowlist por repo (AGENTS.md) | No | No (lo que permita el agente interactivo) | No | Sí: Windows la calcula y la Mac la vuelve a comprobar |
| Regla confidencial/reservado | No sabe de ella | No sabe de ella | — | Solo trabaja el `claude` de nuestra Mac, sobre repos de `~/Documents/Portfolio` |
| Credenciales | `agent-connector` maneja el OAuth de Claude **y** un token del workspace | Las de cada máquina | Cuenta claude.ai | Las de la Mac (llavero) + una clave HMAC que solo tienen las dos máquinas |
| Desde un programa en Windows | Vía el workspace | `thurbox-cli` crea, maneja y captura sesiones | La herramienta `SendMessage` *dentro* de una sesión de Claude Code, no desde un script | `enjambre chat/send/sessions`, comandos normales |

**Recomendación: construir sobre el Agent SDK.** Coincide con lo que esperaba el owner, y estos son
los motivos:

1. **Agentrooms** resuelve el problema de "@mencionar agentes de otras máquinas", pero lo hace
   con un backplane: un servidor intermedio por el que pasan todos los mensajes. Si es el
   público, nuestras conversaciones y rutas de repos pasan por un tercero. Si es propio, hay que
   operar un servicio más, con su propia autenticación. En los dos casos no hay ledger, ni coste por
   turno, ni allowlist por repo, y todo eso habría que pegarlo por fuera.
2. **thurbox** es lo más parecido en espíritu (tmux persistente, SSH, mensajes entre sesiones,
   CLI). Pero envuelve el Claude Code **interactivo**: lo que pasa dentro del panel no sale como
   datos (coste, denegaciones, resultado), y las aprobaciones de permisos las da quien mira la
   TUI. Para enjambre, donde nadie mira la Mac, eso es justo lo que no sirve. Vale la pena
   **probarlo como TUI personal** si quieres ver varias sesiones a la vez (con tu OK para el
   instalador).
3. **La mensajería oficial y Remote Control** sirven para que *tú* sigas una sesión desde el
   celular (punto 7 del prompt). Pero no le dan a un script de Windows una forma de pedirle algo a
   la Mac y recoger el coste, y el tráfico pasa por la nube de Anthropic.
4. **El SDK** ya trae lo difícil: sesiones con `session_id` y `resume`, herramientas, MCP,
   `allowed_tools`, `can_use_tool` (para denegar sin preguntar), `max_budget_usd` y el coste en el
   `ResultMessage`. Lo que falta, que es la cola, el transporte y el ledger, son unas 400 líneas
   que siguen los patrones que enjambre ya usa: `ssh mac curl` como `cmd_modelo`, `busy.json`
   como `jobrun.py`, y una línea de ledger como `largos.collect`.

## 3. Diseño del puente propio (parte 3)

```
 Windows                                         Mac (sesión gráfica, LaunchAgent)
 ───────                                         ─────────────────────────────────
 enjambre chat mac --repo X "msg"                agent/puente.py  (127.0.0.1:8766)
   │  firma HMAC (clave en HKCU)                   │
   └─ ssh mac curl -H X-Puente-* ─────────────────▶│ POST /turns  → turno en cola (archivo)
                                                   │   worker del repo (1 a la vez)
   ◀── NDJSON en vivo (curl -N) ───────────────────│   Agent SDK → claude (llavero, MCP Playwright)
                                                   │   eventos → turns/<id>.ndjson
 state/puente/<turno>.json (pendiente)             │   sesión → sessions/<repo>.json (session_id, coste)
   └─ chat al terminar / collect / watch ─────────▶│ GET /turns/<id> → una línea al ledger
```

- **El turno no vive dentro de la petición HTTP.** El servidor lo corre en su propio bucle y
  escribe cada evento en un archivo. `chat` solo lee ese archivo desde un desplazamiento. Si cierras
  la ventana de Windows, se corta la lectura, pero el turno sigue. `chat --attach <turno>`
  retoma la lectura desde el principio, y un `chat` nuevo continúa la misma conversación
  (`resume`).
- **Sesión = repo.** Una por repo, con su `session_id` en disco. Si el servidor se reinicia, el
  turno que estaba corriendo queda `interrupted`, los que esperaban vuelven a la cola y el
  siguiente mensaje reanuda la conversación.
- **Transporte: `ssh mac curl`, sin puerto en la LAN y sin túnel.** ssh ya autentica y cifra.
  Además, cada petición va firmada (HMAC-SHA256 sobre marca de tiempo, nonce, método, ruta y hash
  del cuerpo). La firma se acepta una sola vez y solo dentro de ±120 s. Cualquier petición con
  cabecera `Origin` (un navegador) se rechaza antes de mirar la firma.
- **Permisos por allowlist, nunca por pregunta.** El conjunto es el de `worker.sh` **sin**
  Edit/Write (salvo `--edit`), más `Bash(<tests-cmd>)` exacto del AGENTS.md y **19 herramientas de
  Playwright con nombre**. No se permite el servidor entero, porque incluye
  `browser_run_code_unsafe` ("RCE-equivalent", según su propia descripción), `browser_evaluate`,
  `browser_file_upload` y `browser_drop`. Los setting sources son `user` y `project`, sin `local`:
  ahí se acumulan los "permitir siempre" interactivos. Windows lo calcula con el
  mismo parser de `largos.py`, y la Mac lo vuelve a comprobar contra el AGENTS.md de su copia del
  repo. Lo que no esté en la lista lo deniega `can_use_tool`, queda registrado y el turno termina
  `incomplete`.
- **Ledger, `busy.json` y status.** Cada turno deja una línea en el ledger (`worker: puente`,
  `kind: puente`, `cost_usd`, `session_id`, `origin_session`). `busy.json` se escribe solo si nadie
  vivo lo tiene, y se borra solo si el turno que lo puso es el que termina. `enjambre status`
  hace una lectura en vivo: `gui sí/no` y las sesiones con su coste.

## 4. Lo que quedó fuera, a propósito

- **Escuchar en la LAN** (`0.0.0.0` + token). No hace falta mientras exista ssh, y sería un puerto
  más expuesto a cualquier aparato de la red.
- **Varias sesiones por repo.** Por ahora es una por repo. Para tener dos conversaciones
  paralelas sobre el mismo repo, la extensión natural es `--session <nombre>`.
- **`script-cmd` en la allowlist.** Solo entra `tests-cmd`. Los scripts largos siguen por
  `enjambre script`, con su supervisor y su latido.
