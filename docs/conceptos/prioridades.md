# Prioridades: admisión, pausa, desalojo y reanudación

## Qué es

Cada trabajo del enjambre tiene una clase: **alta > normal > baja > nocturna**. Si no se dice
nada, es `normal`. La clase se puede dar con `--priority`, con una línea del TASK o con
`enjambre prioridad <id> <clase>`. El planificador hace tres cosas con ella:

| Momento | Quién decide | Qué hace |
|---|---|---|
| **Admisión** (antes de lanzar) | `presence.pick` → `scheduler.admit` | Ordena la cola por clase y luego por llegada. Un baja solo arranca si no corre ningún alta ni normal en el nodo y la memoria está bien. Un nocturna, además, solo dentro de la ventana de 00:00 a 07:00 en Lima. |
| **Preempción** (mientras corre) | `live --serve` → `scheduler.plan` → `planner.py` | Cuando llega un alta y falta CPU o GPU, **pausa** al baja (SIGSTOP a su grupo de procesos). Cuando falta memoria y el baja tiene checkpoint, lo **desaloja**: lo detiene limpio y queda `evicted_resumable`. |
| **Reanudación** | igual | Cuando el nodo lleva **60 s seguidos** sin ningún alta, reanuda lo que él mismo pausó (SIGCONT) o desalojó (vuelve a lanzarlo desde su checkpoint). |

Todos los números están en `config/scheduler.toml`, no en el código.

## Por qué así

- **Pausar no libera memoria.** Un proceso parado conserva su RAM. Si lo que falta es memoria,
  pausar no sirve. La salida es desalojar, pero solo se puede desalojar lo que sabe retomar
  (`--resumable`, con checkpoint). Lo demás solo se pausa y el motivo lo avisa: «pausar no libera
  su memoria». **Nunca se desaloja sin checkpoint**, porque eso es perder el trabajo.
- **La histéresis de 60 s** evita el serrucho: si dos altas llegan seguidos, el baja no se
  despierta y se vuelve a dormir entre uno y otro.
- **Los agentes solo se pausan entre turnos** y solo si son baja. Congelar un `claude -p` a mitad
  de una llamada a herramienta deja el turno colgado. El supervisor mira el último evento del
  stream y solo pausa si fue `tool_use`.
- **Solo `alta` preempta.** Un `normal` solo bloquea que un baja arranque. Un agente normal pasa
  la mayor parte del tiempo esperando a la API, y pausar un render de una hora por eso
  desperdicia la GPU (DECISIONES 279).
- **Un único escritor.** En el nodo, el `status.json` de cada trabajo lo escribe solo su
  supervisor: el PC deja una orden en `control.json` y el supervisor la aplica. En el PC, el
  registro de lo que el planificador pausó (`state/scheduler.json`) lo escribe solo `live`.

## La prioridad del sistema operativo

La clase también baja la prioridad del proceso en el SO:

| Clase | macOS | Windows |
|---|---|---|
| alta / normal | lo de siempre (NI 0) | Normal |
| baja | `taskpolicy -b nice -n 15` | BelowNormal + EcoQoS |
| nocturna | `taskpolicy -b nice -n 19` | Idle + EcoQoS |

`taskpolicy -b` es la clave en Apple Silicon: marca el proceso como *background* y macOS lo deja
en los núcleos de eficiencia. Medido: 8 × `yes` en baja usaron el 41–48 % de la CPU de la Mac. Es
decir, cerca de los 4 núcleos E de 10, sin tocar los núcleos P que usa el alta.

## Externos y no gestionados

- El **lab de lab-video** tiene su propia API de pausa
  (`POST /jobs/<id>/pause|resume`). El planificador lo adopta como baja y lo pausa por esa API,
  llamando desde la propia Mac (`ssh mac curl 127.0.0.1:8800`). Nunca lo desaloja, porque un
  render del lab no tiene checkpoint.
- Un proceso pesado que enjambre no lanzó (CPU > 50 % o RAM > 2 GB durante 2 min) aparece en el
  visor como **no gestionado**. Nunca se toca solo: «adoptar» lo pone bajo el planificador como
  baja.

## Ejemplo real (prueba de fuego a, 2026-10-10)

```
21:57:20  baja  cpu-burn-ck   admitido (0/2 en el nodo)            CPU Mac 41–48 %
21:58:05  alta  cpu-burn      admitido (1/2 en el nodo)            CPU Mac 88 %
21:58:07  planificador: pause cpu-burn-ck  ok
          «pausado por cpu-burn-… (alta) desde 21:58 (CPU 88 %); se reanuda al quedar libre 60 s»
  PID NI PRI STAT COMM            (baja, pausado)      PID NI PRI STAT COMM   (alta)
49819 15   4 TN   yes                                50313  0  31 R    yes
21:59:15  el alta termina; el nodo queda libre
22:00:1x  planificador: resume cpu-burn-ck  («reanudado: nodo libre de alta durante 6x s»)
```

El paso a paso, con las capturas del visor, está en `../tutorial/29-prioridades.md`.
