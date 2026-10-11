# Huella de host SSH

## Qué es

Cada servidor SSH tiene una llave de host. Su huella (*fingerprint*) es un resumen corto de esa llave, del tipo `SHA256:<huella>`. La primera vez que te conectas, el cliente no tiene forma de saber si el servidor es el que dices. Te muestra la huella y pregunta *"The authenticity of host ... can't be established. Are you sure you want to continue connecting (yes/no)?"*. Si respondes `yes`, la guarda en `~/.ssh/known_hosts`. Desde entonces el cliente compara y avisa si la llave cambia.

La llave de host autentica al **servidor ante ti**. La llave `id_mac` autentica al **cliente ante el servidor**. Son cosas distintas.

## Por qué importa aquí

El primer `ssh` a la Mac pasa por esa pregunta (ver `../tutorial/03-ssh.md`, paso 2). La captura 09 del índice de capturas crudas (`_capturas-enjambre-raw/INDICE.md`) muestra un bloque pegado entero: las líneas siguientes entraron como respuesta a un prompt, y el índice marca "huellas" como dato a redactar. Un prompt de `yes/no` se traga lo que pegues igual que el de `Password:`.

Después de la actualización de macOS la IP de la Mac cambió (capturas 17 y 18). Suposición, no verificada: con una IP nueva, `known_hosts` no tiene entrada para ella y la pregunta puede volver a salir, aunque la llave de host sea la misma.

## Ejemplo real

El alias que evita repetir usuario e IP, y la prueba de que la autenticación por llave funciona (de `../tutorial/03-ssh.md`):

```
Host mac
  HostName <ip>
  User <usuario>
  IdentityFile ~/.ssh/id_mac
  IdentitiesOnly yes
```

```
PS> ssh mac 'claude --version'
2.1.289 (Claude Code)
```

Las fuentes no conservan la salida literal de la pregunta de huella. Para ver la huella de una llave: `ssh-keygen -lf <archivo>`.

## Error típico

Responder `yes` sin comparar. Si estás en tu LAN y conoces la máquina, el riesgo es bajo. En una red ajena, compara la huella por otro canal. Si ssh avisa `REMOTE HOST IDENTIFICATION HAS CHANGED`, no borres la línea de `known_hosts` por reflejo: averigua primero por qué cambió.

## Para saber más

- ssh(1), sección sobre `known_hosts`: https://man.openbsd.org/ssh
- ssh-keygen(1), opción `-l`: https://man.openbsd.org/ssh-keygen
