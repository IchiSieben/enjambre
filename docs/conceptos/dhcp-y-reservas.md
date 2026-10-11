# DHCP, reservas y la MAC privada del Wi-Fi

## Qué es

DHCP es el servicio del módem que presta direcciones IP. La IP es un **préstamo** (*lease*) con vencimiento: el equipo puede recibir otra la próxima vez. Una **reserva** le dice al módem "a esta MAC dale siempre esta IP". La MAC es la dirección de la tarjeta de red.

macOS puede usar una MAC distinta por red Wi-Fi ("Dirección Wi-Fi privada"). Con la opción **Fija** (*Fixed*) usa una MAC propia pero estable para esa red; con **Rotativa** la cambia con el tiempo. Una reserva solo funciona si la MAC no cambia.

## Por qué importa aquí

`~/.ssh/config` guarda `HostName <ip>`. Si la IP de la Mac cambia, `ssh mac` deja de responder. Pasó dos veces:

- Al inicio, la Mac tenía una IP del rango 172.20.10.x: era el hotspot del iPhone, aunque "parecía" la misma red. Windows estaba en la red del módem. Lección anotada en la bitácora: comparar las IP, no los nombres de red (`../tutorial/_bitacora-manual.md`, sección Red).
- Tras actualizar macOS, `ssh mac` dio *Connection timed out* (captura 17) y la Mac tenía otra IP (captura 18). La captura 19 muestra "Private Wi-Fi address = Fixed". Por qué cambió la IP no está verificado; suposiciones razonables: el lease venció o la actualización cambió la MAC.

La reserva en el módem figura como pendiente (`RUNBOOK.md`, paso 8, y `HANDOFF.md`).

## Ejemplo real

```
PS> ssh mac
ssh: connect to host <ip> port 22: Connection timed out
```

(Mensaje estándar de ssh; la captura 17 muestra el timeout, el texto exacto no está en las fuentes.)

## Error típico

Reservar la IP con una MAC que luego rota: la reserva deja de aplicar sin avisar. Revisa la MAC en Ajustes → Wi-Fi → Detalles antes de reservar. La MAC es un dato que se redacta en las capturas.

## Para saber más

- DHCP (RFC 2131): https://www.rfc-editor.org/rfc/rfc2131
- Apple, "Usar direcciones Wi-Fi privadas": https://support.apple.com/es-es/102509
