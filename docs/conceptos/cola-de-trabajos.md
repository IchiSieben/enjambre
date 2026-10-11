# Cola de trabajos

**Qué es.** Un lugar donde los trabajos esperan hasta que haya alguien libre para hacerlos. El que
pide (`enjambre run`) y el que despacha (`enjambre watch`) no necesitan estar activos al mismo
tiempo: la cola los **desacopla**.

**Por qué importa aquí.** Sin cola, pedir un trabajo a una Mac apagada es un error que alguien
tiene que recordar y repetir. Con cola, el pedido queda escrito y sale solo cuando la Mac vuelve.
Es la diferencia entre un enjambre que te necesita y uno que trabaja mientras duermes.

**Las reglas que la hacen confiable** (las de `presence.py`):

| Regla | Qué evita |
|---|---|
| Validar el contrato **antes** de encolar | Que un trabajo inválido espere horas para fallar, o que salte la regla `confidencial`/`reservado` |
| Guardar una **copia** de la tarea | Que el trabajo corra algo distinto de lo pedido si editas el archivo después |
| Un id no puede estar dos veces en cola o en ejecución | El trabajo duplicado |
| Moverlo `queue/` → `running/` → `done/`, nunca borrar | Perder el rastro de un trabajo |
| Ledger solo al ejecutar | Que el ledger diga que pasó algo que solo estaba pedido |
| Respetar `max_jobs` por nodo | Saturar los 16 GB de la Mac |

**Una cola de carpetas, no una base de datos.** Cada trabajo es una carpeta con su tarea y un
`job.json`, y moverla de una carpeta a otra es una operación casi atómica del sistema de
archivos. Para un trabajo cada varios minutos no hace falta más. Si algún día hay varios
despachadores en máquinas distintas, entonces sí hace falta algo con bloqueo de verdad, como
SQLite o una cola de red.

**Límite conocido.** Si `watch` muere con un trabajo en `running/`, ese trabajo queda ahí. No se
reintenta solo, porque quizá ya corrió (el ledger lo diría). Lo decide una persona.

Lo aplica `../tutorial/15-presencia-y-cola.md`.
