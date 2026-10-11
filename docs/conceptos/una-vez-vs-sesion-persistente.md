# Una vez vs sesión persistente

## Qué es

Hay dos formas de usar un agente.

- **Una vez** (`claude -p`, un TASK): el agente nace, lee el encargo, trabaja y muere. Todo lo
  que necesita tiene que estar en el encargo, y lo que aprendió se pierde salvo lo que dejó
  escrito (commits, archivos, el resultado).
- **Sesión persistente**: la conversación sigue existiendo entre mensajes. El segundo mensaje
  ("ahora corre los tests") sabe qué pasó en el primero ("abre la web del demo").

En los dos casos el modelo no recuerda nada por sí mismo. Lo que persiste es la **transcripción**:
Claude Code la guarda en disco con un `session_id`, y "reanudar" (`resume`) significa volver a
cargarla como contexto del siguiente turno.

## Cómo lo hace enjambre

| | `enjambre run` (una vez) | `enjambre chat/send` (puente) |
|---|---|---|
| Unidad | Un TASK con contrato (archivos que puede tocar, criterio de hecho) | Un mensaje a la sesión de un repo |
| Memoria | Ninguna entre trabajos | `sessions/<repo>.json` → `session_id` → `resume` |
| Dónde corre | `worker.sh` en un worktree con rama propia | En el repo de la Mac, sin rama propia |
| Qué deja | Rama + commit + línea de ledger | Línea de ledger por turno, coste acumulado por sesión |
| Ideal para | Cambios de código revisables (una rama = un PR) | Conversar, inspeccionar, probar, "mira esto y dime" |

Ningún proceso tiene que seguir vivo para que la sesión siga existiendo: el servidor del puente
puede reiniciarse y el siguiente mensaje continúa la conversación, porque el `session_id` está en
un archivo. Persistente quiere decir que la transcripción está guardada en disco, no que haya un
proceso corriendo todo el tiempo.

## El coste de persistir

Cada turno de una sesión larga vuelve a enviar toda la conversación anterior como contexto. La
caché de prompts abarata lo que se repite, pero una sesión de 50 turnos cuesta más por turno que
una nueva. Por eso `enjambre sessions` muestra el coste acumulado: es la señal para empezar una
sesión limpia.

## Alternativa

Que el agente no recuerde nada y todo vaya en el encargo (el modelo de `run`). Es más caro de
escribir y más fácil de auditar. Las dos conviven: `run` para cambios, el puente para conversar.
