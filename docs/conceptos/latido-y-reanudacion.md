# Latido y reanudación

## Qué es

Un trabajo largo que nadie mira tiene que poder responder dos preguntas sin que alguien entre a la
máquina: **¿sigue vivo?** y **si murió, ¿puede seguir donde quedó?**

- El **latido** es una señal periódica que el propio trabajo (o quien lo vigila de cerca) escribe
  en un lugar conocido: "a las 06:14 seguía, iba en 41 %, último paso X". Si el latido se
  detiene, algo pasó, aunque nadie haya visto el error.
- La **reanudación** es la capacidad de volver a lanzar el trabajo y que no empiece de cero:
  retoma desde lo último que dejó guardado (un punto de control).

## Cómo lo hace enjambre

```
Windows                         Mac
enjambre script/run --detach ─► jobrun.py launch ─► supervisor (sesión propia)
  (vuelve en segundos)                               │  cada 60 s: status.json + busy.json
                                                     │  cada 5 min: una línea en latidos.jsonl
                                                     └► el trabajo (su propio grupo de procesos)
watch / enjambre collect ◄── ssh: jobrun.py status ──┘  copia latidos; al final, el log y 1 línea al ledger
```

- **El supervisor no es el trabajo.** Es un proceso pequeño que lanza el trabajo, escribe el
  latido y, cuando el trabajo termina (bien, mal o porque lo mataron), escribe el estado final,
  suelta `busy.json` y hace el commit. Si el supervisor mismo muere (reinicio, `kill -9`), su
  `pid` deja de existir y `jobrun.py status` lo dice: estado `dead`. Esa es la diferencia entre
  "corriendo" y "un archivo que dice corriendo".
- **Vivo se comprueba, no se cree.** `status.json` puede decir `running` para siempre si nadie lo
  reescribe; por eso el lector pregunta al sistema si el `pid` del supervisor existe.
- **Reanudar = el mismo trabajo, otro intento.** `enjambre resume <id>`:
  - un script vuelve a correr el mismo comando; el script es el que sabe saltarse lo hecho
    (`catalogar_disco.py` guarda cada 200 archivos en SQLite y reusa los hash ya calculados);
  - un agente sigue la misma conversación con `claude -p --resume <session_id>` en el mismo
    worktree, con el presupuesto que le queda.
- **Nunca dos copias.** Si el supervisor murió pero el trabajo siguió (huérfano), `resume` mata
  primero su grupo de procesos. Por eso cada trabajo corre en su **propio grupo** y se mata con
  `killpg`: nunca al envoltorio solo, que deja hijos vivos (el pánico del kernel del 5/10).

## Cuándo no sirve

- Si el trabajo no tiene punto de control, reanudar es repetir. Un latido sin reanudación sigue
  valiendo: al menos sabes que murió y cuándo.
- Si el latido lo escribe un hilo distinto del que trabaja, un trabajo **colgado** (vivo pero sin
  avanzar) sigue latiendo. Por eso el latido también lleva el avance (`%`, turnos, último paso):
  si el avance no cambia en varios latidos, está colgado. Eso pasó en la sesión E3: el catálogo
  latía, pero se quedó 15 min en `opendir()` esperando un permiso de macOS; el `%` vacío lo delató.

## Para saber más

- [Sondeo vs latido](sondeo-vs-latido.md): quién pregunta a quién.
- [Idempotencia](idempotencia.md): por qué repetir un paso no debe romper nada.
- Tutorial [25 — Trabajos largos](../tutorial/25-trabajos-largos.md), con los números reales.
