# 04 — Red: misma subred y una IP que no cambie

## Objetivo

Que Windows y la Mac estén en la **misma red** y que la IP de la Mac no cambie sola, para que
`ssh mac` siga funcionando mañana.

Estado al 2026-10-04: misma red, **hecho**. IP fija por reserva DHCP en el módem, **pendiente**
(lo hace el owner).

## Por qué

SSH, Deskflow y los despachos de tareas se conectan a la Mac por su dirección IP. Dos cosas
pueden romperlo en silencio:

1. **Estar en otra subred.** Las dos máquinas deben compartir los primeros tres números de la IP
   (por ejemplo `192.168.1.x`). El nombre de la red WiFi no basta: dos redes pueden llamarse
   igual o parecer la misma.
2. **Que el módem le dé otra IP a la Mac.** El módem reparte IP por DHCP y, por defecto, puede
   cambiarlas. Una **reserva DHCP** le dice al módem "a este equipo siempre dale esta IP", y
   lo identifica por su dirección MAC. Eso exige que la MAC sea estable; si la Mac usa una MAC
   "privada rotativa" en esa red, el módem ve un equipo nuevo cada vez y la reserva no sirve.

## Pasos

### 1. Comparar las IP, no los nombres de red

En la Mac (Terminal):

```
mac$ ipconfig getifaddr en0; ipconfig getifaddr en1
```

En Windows:

```powershell
PS> ipconfig
```

Compara los tres primeros números. Si no coinciden, la Mac está en otra red.

### 2. Reserva DHCP en el módem — PENDIENTE

Pendiente: este paso se escribe cuando el owner lo ejecute en la interfaz de su módem (el menú
depende del modelo). Receta general: en el módem, sección de DHCP o "LAN", reservar la IP actual
de la Mac asociándola a la dirección MAC que muestra la Mac en Ajustes → Wi-Fi → Detalles
(con "Private Wi-Fi address" en **Fixed**, ver abajo).

## Qué salió mal de verdad

**1. La Mac estaba en el hotspot del iPhone.** La Mac tenía una IP `172.20.10.x` (rango del
hotspot del iPhone) mientras Windows estaba en `192.168.1.x`, por cable. El nombre de la red
"parecía" el mismo. En la captura, el mismo comando `ipconfig getifaddr` se corrió dos veces:
primero la IP del hotspot, luego la del módem (ambas tapadas).

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Arreglo: conectar la Mac al WiFi del módem y quitar la autoconexión al hotspot.
**Lección: compara las IP, no los nombres.**

**2. Tras actualizar macOS, `ssh mac` dejó de responder.** En la captura, un `ssh mac` que
respondía bien (mostraba macOS 15.3.1) es seguido por `ssh: connect to host <ip> port 22:
Connection timed out`. La IP de la Mac había cambiado después de la actualización (ver
`06-actualizar-macos.md`).

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Diagnóstico: desde la Terminal de la Mac se pidió la IP nueva con el mismo comando de arriba.
La captura muestra además "Restored session", señal de que la Mac se había reiniciado
(supuesto razonable, no verificado por otra vía).

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Arreglo: actualizar `HostName` en `~/.ssh/config` de Windows con la IP nueva. (Las fuentes no
registran la edición, pero las órdenes `ssh mac` posteriores funcionaron.) La corrida nocturna
llevaba además una instrucción de respaldo: si `ssh mac` falla, probar una vez un `ping` al nombre
`.local` de la Mac para encontrar la IP nueva, sin escanear la red.

**3. La causa del cambio de IP no está confirmada.** Las fuentes registran que cambió tras la
actualización, no por qué. Lo que sí se hizo: mirar la configuración del WiFi de la Mac y ver
**Private Wi-Fi address = Fixed** para esa red. La captura muestra "Automatically join this
network" activado y la dirección MAC tapada. Las fuentes no dicen si antes estaba en *Rotating*.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

El texto de la propia pantalla lo explica: una dirección privada reduce el rastreo por parte de
las redes WiFi; una dirección privada **fija** permite que *esa* red te reconozca, pero sigue
ocultando tu MAC real frente a otras redes. Para una reserva DHCP, la fija es la que sirve.

## Cómo verificar

Desde Windows, sin depender de nada más:

```powershell
PS> ssh mac 'echo ok'
```

Debe responder `ok` sin pedir contraseña. Y que la IP no cambie: reiniciar la Mac y repetir el
comando **sin editar** `~/.ssh/config`. Esa prueba de reinicio está pendiente hasta que exista la
reserva DHCP.

## Autoevaluación

1. Windows está en `192.168.1.x` y la Mac en `172.20.10.x`. ¿Están en la misma red?
   <details><summary>Respuesta</summary>No: los tres primeros números difieren (el segundo rango
   es el del hotspot del iPhone). Hay que mover la Mac al WiFi del módem.</details>
2. ¿Por qué una reserva DHCP necesita una MAC estable?
   <details><summary>Respuesta</summary>El módem identifica el equipo por su MAC. Si la Mac la
   rota, el módem la ve como otro equipo y le da otra IP.</details>
3. `ssh mac` da "Connection timed out" y ayer funcionaba. ¿Qué compruebas primero?
   <details><summary>Respuesta</summary>La IP actual de la Mac (`ipconfig getifaddr …` en su
   terminal) contra el `HostName` de `~/.ssh/config`.</details>
