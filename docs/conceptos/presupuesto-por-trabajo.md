# Presupuesto por trabajo

## Qué es

Un tope de gasto que pertenece a **un trabajo**, no a la cuenta: "este agente puede gastar como
mucho US$2". Cuando lo alcanza, se detiene solo, deja lo que hizo y avisa por qué paró.

## Por qué hace falta con agentes largos

Un agente que corre horas sin nadie mirando puede entrar en un bucle o leer de más. El límite de
turnos (`--max-turns`) no alcanza: el costo de cada turno **crece** con el contexto, porque cada
turno vuelve a leer (de caché) todo lo anterior. Cien turnos al principio cuestan poco; cien turnos
con el contexto lleno cuestan mucho más. El dinero es la unidad que importa, así que el tope va en
dinero.

## Cómo lo hace enjambre

Tres capas, de la más fiable a la más aproximada:

1. **El tope del propio CLI**: `claude -p --max-budget-usd 2.00`. El CLI cuenta con los precios
   reales y, al pasarse, termina con `subtype: error_max_budget_usd` y `terminal_reason:
   budget_exhausted` (medido en la Mac, 2026-10-07). enjambre lo traduce a `status: incomplete`
   con `reason: budget`: el estado sigue siendo uno de los tres de siempre (ok, failed,
   incomplete), porque el visor y la bandeja comparan contra esos tres.
2. **Una estimación en el latido** (`~US$ x.xx (estimado)`): uso de tokens × precios de
   `models.json`. Solo sirve para mirar en vivo: el costo real solo llega en el evento final.
   Cuidado con contar doble: el flujo `stream-json` emite **un evento por bloque** de un mismo
   mensaje y cada uno repite el uso completo. La primera prueba contó 117 "turnos" y US$2,66 para
   54 mensajes reales; ahora se cuenta por id de mensaje.
3. **Un respaldo del supervisor**: si la estimación supera 1,15 × el tope (el CLI no cortó), mata
   el grupo de procesos. Es la red, no el mecanismo.

Al **reanudar** se pasa solo lo que queda: tope − lo gastado en los segmentos anteriores (su
`total_cost_usd`; si un segmento murió sin evento final, su estimación). Si no queda nada, no se
lanza otro segmento.

## La diferencia con la cuota

Con suscripción, el límite real es la cuota de 5 h / 7 días, no los dólares. El tope por trabajo
se expresa en "dólares equivalentes de API" porque es lo que el CLI sabe contar; protege la
cuota igual: un trabajo con tope de US$2 no puede comerse la cuota de la tarde.

## Para saber más

- [Mínimo privilegio para agentes](minimo-privilegio-para-agentes.md): el otro límite (qué puede
  hacer, no cuánto puede gastar).
- Tutorial [25 — Trabajos largos](../tutorial/25-trabajos-largos.md), prueba (b).
