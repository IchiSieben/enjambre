---
lesson:
  route: build-stack
  order: 4
  level: 2
---

# 03 — SSH con llave (y sin contraseña)

## Objetivo

Que `ssh mac '<comando>'` funcione desde Windows sin pedir contraseña y, en un segundo momento,
que nadie pueda entrar a la Mac **con** contraseña.

Estado al 2026-10-04: la primera parte está **hecha y verificada**; apagar la contraseña sigue
**pendiente** (lo hace el owner, requiere `sudo`).

## Por qué

Un agente (Claude Code) va a mandar comandos a la Mac sin que estés mirando. Eso exige
autenticación por llave: la privada vive en Windows, la pública en la Mac, y nunca se teclea
nada. Luego se apaga la contraseña, porque una Mac con SSH abierto y contraseña habilitada se
puede atacar por fuerza bruta desde cualquier equipo de la red.

## Pasos

### 1. Activar Inicio de sesión remoto (en la Mac, a mano)

Ajustes del Sistema → General → Compartir → **Remote Login** activado, con acceso solo para tu
usuario. En la captura también están activados File Sharing y Screen Sharing; en la bitácora se
anotan "restringidos".

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

### 2. Comprobar que las máquinas se ven (en Windows)

```powershell
PS> ping <ip>
```

En la captura de ese momento: `Packets: Sent = 4, Received = 4, Lost = 0 (0% loss)`, tiempos de
4 a 89 ms (promedio 36 ms).

### 3. Llave dedicada (en Windows)

```powershell
PS> ssh-keygen -t ed25519 -f $env:USERPROFILE\.ssh\id_mac -C "win-to-mac"
```

Sin passphrase: Claude Desktop salta las llaves con passphrase, y esta llave solo abre la Mac
dentro de la red local.

### 4. Copiar la llave pública a la Mac (pide la contraseña de la Mac UNA vez)

Este es el único comando de la guía que pide contraseña. Córrelo **solo**, sin nada pegado
detrás:

```powershell
PS> type $env:USERPROFILE\.ssh\id_mac.pub | ssh <usuario>@<ip> "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

La primera vez ssh pregunta si confías en la huella de la Mac (`yes`) y luego `Password:`.

### 5. Alias `mac` (en Windows)

En `%USERPROFILE%\.ssh\config`, **un solo** bloque:

```
Host mac
  HostName <ip>
  User <usuario>
  IdentityFile ~/.ssh/id_mac
  IdentitiesOnly yes
  ServerAliveInterval 30
```

`IdentitiesOnly yes` evita que ssh pruebe todas tus llaves antes que la correcta (el servidor
corta tras pocos intentos fallidos).

### 6. Apagar la contraseña (en la Mac, con sudo — lo hace el owner) — PENDIENTE

Primero, mirar qué hay. macOS ya trae un archivo de configuración propio:

```
mac$ ls /etc/ssh/sshd_config.d/
100-macos.conf
```

**Concepto:** `sshd` se queda con el **primer** valor que lee de cada opción, y los archivos de
`sshd_config.d/` se leen en orden alfabético. Por eso el nuestro se llama `050-…`: se lee antes
que `100-macos.conf` y gana. No se edita `/etc/ssh/sshd_config` directamente porque las
actualizaciones de macOS lo reemplazan.

Una línea a la vez (la primera pide tu contraseña de administrador):

```
mac$ printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n' | sudo tee /etc/ssh/sshd_config.d/050-no-password.conf
```
```
mac$ sudo sshd -t
```
```
mac$ sudo launchctl kickstart -k system/com.openssh.sshd
```
```
mac$ sudo sshd -T | grep -Ei 'passwordauth|kbdinteractive'
```

- `sshd -t` valida la sintaxis **antes** de reiniciar: si imprime algo, no sigas.
- La última línea debe mostrar `passwordauthentication no` y `kbdinteractiveauthentication no`.
- No cierres tu sesión abierta en la Mac hasta comprobar que una conexión **nueva** entra con la
  llave.

> Estado: **pendiente**. `NEEDS-OWNER.md` §2 registra que el servidor seguía ofreciendo
> `password,keyboard-interactive` en la última verificación (2026-10-04).

## Qué salió mal de verdad

**1. Casi sobrescribo una llave existente.** `ssh-keygen -t ed25519 -C "win-to-mac"` sin `-f` se
detuvo en *"Enter file in which to save the key (…/.ssh/id_ed25519)"*, y el listado previo
mostraba que `id_ed25519.pub` ya existía (la usan otros servicios). Aceptar el valor por defecto
la habría sobrescrito. Arreglo: llave dedicada con nombre propio (`id_mac`).

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

**2. Pegué un bloque entero que incluía un `Password:`.** Se pegaron juntos varios pasos
(generar llave, copiarla, escribir el alias, probar). Al llegar al `Password:` de la copia, las
líneas siguientes del bloque entraron como si fueran la contraseña: la llave no se copió y
`ssh mac` quedó colgado pidiendo contraseña (en la captura, `^C`). Arreglo: abrir el config en
el Bloc de notas, repetir **solo** el comando de copia, teclear la contraseña, y entonces
`ssh mac "sw_vers && claude --version && brew --version"` respondió sin pedir nada.
**Regla: nunca pegues un bloque que contiene un prompt de contraseña.**

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

**3. Dos bloques `Host mac` en el config.** El bloque pegado la primera vez no traía
`IdentityFile`; el segundo sí. Funciona por casualidad: ssh toma el **primer** valor que
encuentra para cada opción y el segundo bloque aporta `IdentityFile`. Es frágil. La bitácora
dice que se limpió a un solo bloque; la sesión 1 aún veía dos. No se re-verificó: ver "Cómo
verificar".

**4. El comando "obvio" de verificación se cuelga.** `ssh -o PreferredAuthentications=password
-o PubkeyAuthentication=no mac true` espera una contraseña que nadie va a teclear; y añadir
`BatchMode=yes` lo hace fallar del lado del **cliente**, diga lo que diga el servidor. La
variante que sirve usa `NumberOfPasswordPrompts=0` (abajo).

**5. El agente no puede sacar capturas de la Mac por SSH.**

```
PS> ssh mac 'screencapture -x /tmp/t.png'
could not create image from display
```

macOS exige el permiso de **Grabación de pantalla** para capturar, y un proceso que llega por
SSH no lo tiene. Es una frontera de privacidad deliberada y no se abre. Las capturas de la
interfaz de macOS las sacas tú con `Cmd+Shift+4` (quedan en el Escritorio) y el agente las
trae con `scp mac:~/Desktop/<archivo> docs/tutorial/img/`.

## Cómo verificar

**a) Entra con llave, sin pedir nada, e inventario de la Mac** (2026-10-04):

```powershell
PS> ssh mac 'sw_vers; uname -m; df -h /; brew --version; claude --version'
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

| Dato | Valor |
|---|---|
| Equipo | Mac mini M2 Pro, 16 GB de memoria unificada |
| macOS | 27.0.1 (build 26A434) |
| Arquitectura | `arm64` |
| Disco libre | 111 GiB según `df -h /`; 119,1 GB según `diskutil info /` |
| Homebrew | 7.0.7 |
| Claude Code | 2.1.289 |

Dos cifras de disco porque `df` cuenta en GiB (1024³) y `diskutil` en GB (1000³), y `diskutil`
mira el contenedor APFS completo. Pendiente: el objetivo es **más de 150 GB libres** (Xcode,
cachés de Homebrew/Node y proyectos de Blender crecen rápido); con 111–119 GB todavía falta
mover respaldos a otro disco. Nota: la bitácora anotó macOS 15.3.1; la Mac se actualizó después
(ver `06-actualizar-macos.md`). Manda lo que dice `sw_vers` hoy.

**b) Un solo bloque `Host mac`:**

```powershell
PS> Select-String -Path $env:USERPROFILE\.ssh\config -Pattern '^Host '
```

Debe dar una sola coincidencia (`Host mac`). Pendiente de re-verificar.

**c) La contraseña sigue habilitada (estado actual)**, o ya no (después del paso 6):

```powershell
PS> ssh -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no -o NumberOfPasswordPrompts=0 mac true
```

`NumberOfPasswordPrompts=0` hace que el cliente no pregunte nada: solo se lee la lista de métodos
entre paréntesis. **Antes** del paso 6 (capturado el 2026-10-04):

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

**Después** debe decir solo `Permission denied (publickey).` (esa captura aún no existe).

## Autoevaluación

1. ¿Por qué una llave dedicada en vez de la `id_ed25519` que ya existía?
   <details><summary>Respuesta</summary>Porque `ssh-keygen` sin `-f` propone esa ruta y la
   sobrescribiría; además esa llave la usan otros servicios.</details>
2. ¿Por qué el archivo de hardening se llama `050-…` y no `200-…`?
   <details><summary>Respuesta</summary>`sshd` toma el primer valor leído y lee `sshd_config.d/`
   en orden alfabético; `050` se lee antes que `100-macos.conf` y gana.</details>
3. ¿Qué pasa si pegas un bloque de comandos donde uno de ellos pide contraseña?
   <details><summary>Respuesta</summary>Las líneas siguientes entran como respuesta al
   `Password:`, el comando falla y el resto del bloque no se ejecuta como pensabas.</details>
