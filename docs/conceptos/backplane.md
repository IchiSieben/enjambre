# Backplane

## Qué es

En electrónica, el *backplane* es la placa trasera de un chasis a la que se enchufan todas las
tarjetas: ninguna habla directamente con otra, todas hablan **a través** de la placa. En software
se llama así al servicio intermedio por el que pasan los mensajes entre participantes que no se
conectan entre sí: un bus de mensajes, un servidor de chat, un *workspace*.

```
 con backplane                         sin backplane (enjambre)
 A ──▶ [ servidor de mensajes ] ◀── B     A ──ssh──▶ B (escucha solo en su 127.0.0.1)
       (todos se conectan a él)           (A llega a B por un canal que ya existe)
```

## Por qué importa al elegir

Un backplane resuelve bien que **muchos** participantes se encuentren entre sí (descubrimiento,
@menciones, presencia), aunque estén detrás de NAT. A cambio:

- Hay **un servicio más** que operar, o que confiar a un tercero (Agentrooms usa por defecto el
  workspace público de OpenAgents).
- **Todo pasa por ahí**: texto de las conversaciones, rutas de repos, resultados. Si es de un
  tercero, esos datos salen de tus máquinas.
- Necesita su **propia autenticación** (tokens del workspace), además de la de cada máquina.

Con dos máquinas propias que ya se hablan por ssh, el backplane no aporta nada que justifique
esos costes. El puente de enjambre usa el canal que ya existe y no abre puertos ni depende de
servicios de terceros. Si un día hay cinco nodos y conversaciones entre todos, un backplane
**propio** en la LAN sería la opción a evaluar.

## Dónde aparece en enjambre

- **Evitado:** el puente ([docs/PUENTE.md](../PUENTE.md), tutorial 26).
- **Aceptado a propósito:** la bandeja del visor web. Ahí el celular no puede llegar a casa, así
  que el hosting hace de buzón y la PC pregunta cada 2 min ([Pull vs push](pull-vs-push.md)). Es
  un backplane mínimo: solo pasan contratos firmados, nunca conversaciones.
- **Remote Control** usa como backplane la nube de Anthropic. Es para una persona en el celular,
  no para máquinas.
