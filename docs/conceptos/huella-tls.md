# Huella TLS (Deskflow)

## Qué es

Deskflow cifra el tráfico de teclado y mouse entre servidor y cliente con TLS. Los certificados son autofirmados: no hay una autoridad que los avale. En su lugar, cada lado muestra la **huella** de su certificado y el otro la guarda como "de confianza" la primera vez. Es el mismo modelo que `known_hosts` en SSH: confianza en el primer uso (*trust on first use*).

Hay dos diálogos distintos. El **servidor** muestra su propia huella (para que la compares con la que ve el cliente). El **cliente** muestra la huella del servidor al que se conecta y pide aceptarla.

## Por qué importa aquí

Deskflow comparte lo que escribes, incluidas contraseñas, entre Windows y la Mac (ver `../tutorial/01-deskflow.md`). Sin huella verificada, cualquier equipo de la red podría hacerse pasar por el servidor. La captura 13 del índice de capturas crudas muestra el diálogo con las huellas de servidor y cliente (marcada para redactar la IP parcial).

El registro de Deskflow dijo "fingerprint does not match trusted fingerprint". Lectura: el cliente ya tenía guardada una huella del servidor y la que recibió ahora era otra. La causa no está documentada en las fuentes; lo habitual es que el servidor haya regenerado su certificado.

## Ejemplo real

Mensaje del registro, tal como se reportó:

```
fingerprint does not match trusted fingerprint
```

Las fuentes no conservan el resto de la línea ni la solución aplicada.

## Error típico

Aceptar sin mirar. El diálogo existe para que compares las dos huellas: si coinciden, aceptas. Si no coinciden y no regeneraste nada, no aceptes: algo se interpone o conectas al equipo equivocado.

## Para saber más

- Deskflow, repositorio y documentación del proyecto: https://github.com/deskflow/deskflow
- TLS 1.3 (RFC 8446): https://www.rfc-editor.org/rfc/rfc8446
