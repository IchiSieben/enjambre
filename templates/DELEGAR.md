# Delegar a la Mac — <repo>

Router: `../enjambre/routing.toml` (tutorial 22 de enjambre). Sin pantalla → Mac. `ssh mac` a pelo está bloqueado por un hook: todo pasa por enjambre.

| Conviene en la Mac | Comando (desde esta carpeta) |
|---|---|
| Ver la ruta sin gastar nada | `python ../enjambre/enjambre.py run --repo <repo> --task TASK-<id>.md --kind <kinds> --dry-run` |
| Ejecutarlo (agente `claude -p`, gasta cuota) | el mismo, sin `--dry-run` |
| Verificar (la suite de este repo) | un TASK con `kind: tests`; el worker solo puede ejecutar `tests-cmd` de AGENTS.md, tal cual |
| Un script pesado sin agente ($0) | `python ../enjambre/enjambre.py script --repo <repo> --cmd <nombre>` (solo los `script-cmd` de AGENTS.md) |
| Un agente de horas | `run ... --detach --budget 2 --model sonnet`; luego `status --follow <id>`, `collect`, `resume <id>` |
| ¿Está libre la Mac? | `python ../enjambre/enjambre.py status` |

Intérprete en la Mac: el `.venv` del repo (uv, en el clon: `uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt`) o `python3`. Nunca `py` (solo Windows). En Windows, `py` o `python`.
Push: los repos públicos no empujan la rama del trabajo salvo `push: true` en el TASK; la rama vuelve igual por `git fetch` desde la Mac.

Antes de la primera vez: el repo tiene que estar clonado en la Mac (`~/Documents/Portfolio/<repo>`) y con `AGENTS.md` commiteado en la raíz.
Se queda en Windows: lo que necesita pantalla, navegador con sesión o credenciales locales.
Escribe `kind: <kinds>` al principio del TASK y no hace falta `--kind`.

**Probado:** nada todavía. Anota aquí la fecha y la línea del ledger de la primera corrida real.
