# Idempotencia

## Qué es

Una operación es idempotente si ejecutarla dos veces deja el sistema igual que ejecutarla una. No significa "no hace nada la segunda vez" por casualidad: significa que compara el estado actual con el deseado y solo cambia la diferencia.

## Por qué importa aquí

El agente corre comandos remotos sin supervisión y las conexiones se cortan. Si un instalador no es idempotente, repetirlo tras un corte puede duplicar líneas o pisar cambios. Por eso `CLAUDE.md` exige que los comandos remotos comprueben antes de instalar (`brew list`, `command -v`), como en `../tutorial/02-homebrew-y-shell.md`. Y por eso el criterio de aceptación del kit es que la segunda corrida dé cero cambios (`../tutorial/07-orquestador.md`, §3.2).

## Ejemplo real

`sync-to-home.sh` corrido dos veces en la Mac:

```
mac$ bash sync-to-home.sh   → 34 added, 0 updated, 1 merged, 0 unchanged
mac$ bash sync-to-home.sh   → 0 added, 0 updated, 0 merged, 35 unchanged
```

34 agregados + 1 fusionado = 35 elementos; la segunda vez los 35 salen "unchanged". Las cuentas cuadran.

## Error típico

Usar `>>` para "asegurar" una línea: cada corrida la agrega otra vez. La forma idempotente es comprobar con `grep -q` antes, o reescribir el archivo entero. Ojo con el caso contrario: `kit-update.sh` no pudo correrse tal cual (su `git pull` falla por SSH y los `.sh` no tenían bit de ejecución), así que se corrieron sus pasos a mano (`docs/DECISIONES.md`, punto 7). Idempotente no es lo mismo que "funciona por SSH".

## Para saber más

- Métodos idempotentes en HTTP (RFC 9110, §9.2.2): https://www.rfc-editor.org/rfc/rfc9110#name-idempotent-methods
