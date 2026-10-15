---
lesson:
  route: build-stack
  order: 2
  level: 0
---

# 01 — Un teclado y un mouse para las dos máquinas (Deskflow)

## Objetivo

Mover el cursor de la pantalla de Windows a la de la Mac (y escribir allí) con el mismo teclado y
mouse, y copiar en una máquina y pegar en la otra.

## Por qué

Deskflow (el sucesor libre de Synergy y Barrier) comparte teclado y mouse **por la red**, sin
hardware. Una máquina es el *servidor* (la que tiene el teclado físico: Windows) y las demás son
*clientes* (la Mac). Cuando el cursor toca el borde configurado, el servidor deja de mover su
puntero y manda los eventos al cliente. La conexión va cifrada con TLS, y por eso la primera vez
aparece un diálogo de huellas (ver más abajo).

> **¿Tienes un switch KVM, o tu monitor trae KVM USB?** Entonces quizá no necesitas Deskflow: el
> KVM cambia teclado, mouse **y** pantalla con un botón. La diferencia: con KVM ves una máquina a
> la vez; con Deskflow + pantalla partida ves las dos y el cursor cruza solo. Si tu monitor tiene
> KVM USB, conecta el teclado al monitor y cada PC por su cable USB-C/USB-B.

> Requisito: la instalación en la Mac usa Homebrew; haz primero `02-homebrew-y-shell.md`.
> Estado: funcionando, configurado a mano por el owner. La verificación del reinicio (que el
> cliente se reconecte solo) sigue **pendiente**.

## Pasos

### 1. Instalar

- **Mac (cliente)**, un comando por bloque:
  ```
  mac$ brew tap deskflow/tap
  ```
  ```
  mac$ brew trust deskflow/tap
  ```
  ```
  mac$ brew install --cask deskflow
  ```
- **Windows (servidor):** el instalador de la página de *releases* del proyecto Deskflow en
  GitHub. Necesita el *Visual C++ Redistributable* de Microsoft si no está.

### 2. Permisos en la Mac (los das tú, con clics)

Ajustes del Sistema → Privacidad y seguridad:
1. **Accesibilidad** → activar Deskflow (para mover el puntero y teclear).
2. **Monitorización de entrada** → activar Deskflow (para leer teclado y mouse locales).

Sin estos dos permisos el cliente se conecta pero nada se mueve (es el fallo número uno según
`PLAN.md` §1.1). Es una frontera de seguridad de macOS que ningún script cruza por ti.

### 3. Servidor en Windows

Al abrir Deskflow en Windows, elige el modo: *Use this computer's keyboard and mouse (make this
computer the server)*. La ventana muestra el nombre de la máquina ("This computer's name").

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

*Configure Server* abre la cuadrícula de pantallas. Al inicio solo está la de Windows.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Arrastra la pantalla de la Mac a la celda de la derecha. El nombre debe ser **exactamente** el
que la Mac usa en Deskflow; por eso la Mac se renombró a algo simple y sin apóstrofes: `macmini`
(Ajustes → General → Información → Nombre).

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Si guardas el layout en un archivo, el diálogo de *Save server configuration as…* abre por
defecto en la carpeta de instalación de Deskflow.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

### 4. Opciones avanzadas

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Lo que muestra la captura:
- *Switch after waiting* marcado en **250 ms**: el cursor debe esperar 250 ms en el borde antes
  de cruzar; evita cruces accidentales.
- *Enable clipboard sharing* marcado, límite 200 MB: es el copiar/pegar entre máquinas.
- *Network protocol*: **Barrier** seleccionado (no Synergy).
- *Use a server config file* **sin marcar**: la configuración en uso es la de la cuadrícula
  (paso 3), no el archivo. El repo guarda además `config/deskflow-server.conf` como referencia;
  si lo quisieras usar, se marca esa casilla.

Contenido esencial de ese archivo de referencia:

```
section: screens
	<pc-windows>:
	macmini:
		ctrl = super
		super = ctrl
end
section: links
	<pc-windows>:
		right = macmini
	macmini:
		left = <pc-windows>
end
section: options
	switchDelay = 250
	clipboardSharing = true
	keystroke(ScrollLock) = lockCursorToScreen(toggle)
end
```

Las dos líneas de `macmini` cambian el teclado de Windows al llegar a la Mac: **Ctrl actúa como
Cmd** (Ctrl+C/V/Z funcionan igual que en Windows) y la tecla Windows actúa como Ctrl. Si las
borras, vuelve el mapeo nativo. Como la casilla del archivo está sin marcar, este mapeo solo
aplica si activas el archivo. Supuesto (no verificado): en la GUI equivale a los modificadores de la pantalla `macmini`
(doble clic sobre ella en la cuadrícula).

La pestaña *Hotkeys* de la GUI estaba vacía en la captura, y "Disable lock to computer (scroll
lock key)" estaba sin marcar. Supuesto (no verificado): el bloqueo con ScrollLock depende del
archivo de referencia o del valor por defecto de Deskflow, no de un atajo creado en la GUI.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

### 5. Primera conexión: las huellas TLS

Al conectar el cliente por primera vez, el servidor muestra *"New client connecting"* con dos
huellas (*Server Fingerprint* y *Client Fingerprint*, dibujadas como arte ASCII) y la
instrucción: *"Compare the fingerprints in this dialog to those on the client. Only connect if
they match!"*.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Esto es el mismo modelo de confianza de SSH ("¿confías en esta clave?"): el cifrado protege el
canal, pero solo la **comparación de huellas** te asegura que al otro lado está *tu* Mac y no
otra máquina de la red que se hizo pasar por ella. Se compara lo que ves en el servidor con lo
que muestra Deskflow en el cliente, y solo entonces se pulsa *Yes*.

### 6. Que arranque y reconecte solo

Objetivo: encender las máquinas en cualquier orden y que el teclado cruce sin abrir nada.
Antes de tocar una opción la verifiqué contra la documentación que trae la instalación
(`C:\Program Files\Deskflow\docs\Configuration.md`) y contra `deskflow-core --help`. En la
versión 1.27 el core casi no tiene opciones de línea de comandos: todo sale del archivo de
configuración (`-s <archivo>`).

**Diagnóstico de cómo estaba:**

| | Windows (servidor) | Mac (cliente `macmini`) |
|---|---|---|
| Arranque | **Servicio `Deskflow`** (automático, LocalSystem) que lanza el core **elevado** en tu sesión | La app, abierta a mano |
| Inicio automático | El servicio sí; la ventana (GUI), no | Nada (ni Login Item ni LaunchAgent) |
| Evidencia en los logs | 06:41:56 se reinicia el core de Windows → la Mac reconecta **0,8 s** después, sola | La Mac arrancó 05:42 y conectó 05:55: los 13 min que tardó alguien en abrir Deskflow |

**Windows: el servicio ya resuelve UAC.** El log del daemon dice `running command (elevated)`:
el core corre con privilegios de sistema dentro de tu sesión, y por eso puede seguir capturando
el mouse cuando aparece un aviso de UAC (escritorio seguro). La documentación confirma la clave
`[daemon] elevate` (por defecto `true`). Lo que faltaba era la ventana al iniciar sesión, que es
la que le pide al daemon que arranque el core:

```powershell
PS> New-ItemProperty -Path HKCU:\Software\Microsoft\Windows\CurrentVersion\Run -Name Deskflow -Value '"C:\Program Files\Deskflow\deskflow.exe"' -PropertyType String
```

Y en `%APPDATA%\Deskflow\Deskflow.conf`, sección `[gui]`: `autoHide=true` (documentada: "the app
will hide itself on start up"), para que no aparezca la ventana cada vez.

> La documentación dice que en Windows la configuración vive en la carpeta de instalación o en
> el registro (`HKCU\Software\Deskflow\Deskflow`). En 1.27 no es así: el daemon le pasa al core
> `--settings %APPDATA%\Deskflow\Deskflow.conf`, y ese es el archivo que manda. Verificar contra
> la documentación sirve también para encontrar dónde la documentación está desactualizada.

**Mac: un LaunchAgent que abre la app como un doble clic.** Plantilla en el repo:
`config/dev.enjambre.deskflow.plist`. Ejecuta `/usr/bin/open -g -j -a /Applications/Deskflow.app`
al iniciar sesión (`RunAtLoad`, `LimitLoadToSessionType Aqua`). Se instala igual que
`dev.enjambre.tmux` (paso 11):

```bash
mac$ sed "s|__HOME__|$HOME|g" config/dev.enjambre.deskflow.plist > ~/Library/LaunchAgents/dev.enjambre.deskflow.plist
mac$ launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.enjambre.deskflow.plist
```

¿Por qué `open` y no lanzar directamente el binario de dentro de la app? Lo probé y es la lección
más fina de este paso (ver "Qué salió mal", punto 3). macOS concede los permisos (Accesibilidad,
Red local) **a la app**, identificada por su firma. `open` arranca la app exactamente igual que
un doble clic, así que hereda todos sus permisos.

**¿Sobreviven los permisos de Accesibilidad?** Sí, y la razón está en la firma:

```bash
mac$ codesign -dv --verbose=2 /Applications/Deskflow.app 2>&1 | grep -E 'Identifier|Authority'
Identifier=org.deskflow.deskflow
Authority=Developer ID Application: <desarrollador> (<team>)
mac$ spctl -a -vv /Applications/Deskflow.app
source=Notarized Developer ID
```

Una app firmada con *Developer ID* y notarizada conserva el permiso entre reinicios y entre
actualizaciones, porque macOS lo ata a su identificador y a su equipo de desarrollo. Una app con
firma *ad hoc* lo perdería con cada `brew upgrade`.

**Reintento:** el cliente reintenta por su cuenta, **cada ~1 s y sin límite** (lo vi en un cliente
de prueba arrancado antes que su servidor). No activé `dynamicConnectionInterval`: espacia los
reintentos tras cada fallo, que es justo lo que no quieres cuando Windows arranca segundo.

**Por nombre o por IP:** `remoteHost` sigue con la **IP**. El nombre `<pc-windows>.local` resuelve
desde la Mac (mDNS), pero Windows anuncia también las direcciones `169.254.x.x` de sus adaptadores
sin red, y el cliente pierde tiempo probándolas. La solución robusta es una **reserva DHCP para
Windows** en el módem (tarea del owner, `NEEDS-OWNER.md`).

### 7. Órdenes de encendido (los prueba el owner)

Reiniciar Windows mataría la sesión del agente, y reiniciar la Mac con FileVault pide tu
contraseña. Así que estas pruebas son tuyas. Después de cada una, desde Git Bash en Windows:

```bash
$ bash tools/deskflow-postcheck.sh      # en Git Bash, no en PowerShell (allí `bash` puede ser el de WSL)
```

El script lee los logs de las dos máquinas y dice, desde el último arranque de cada una, cuándo
subió Deskflow y cuándo conectó la Mac.

| Prueba | Qué haces | Qué debería pasar |
|---|---|---|
| Windows primero | Enciende Windows, inicia sesión; después la Mac | Al iniciar sesión en la Mac, el cursor cruza sin abrir nada |
| Mac primero | Enciende la Mac, inicia sesión; después Windows | La Mac reintenta cada segundo; conecta en cuanto inicias sesión en Windows |
| Reiniciar solo Windows | Mac encendida | La Mac reconecta sola (ya visto: 0,8 s tras reiniciar el core) |
| Reiniciar solo la Mac | `sudo fdesetup authrestart` (FileVault) | Tras iniciar sesión, conecta sin abrir Deskflow |
| Pantalla bloqueada | Bloquea la Mac (Ctrl+Cmd+Q) y vuelve | **Sin verificar**: ver la tabla de abajo |

### 7b. El primer reinicio real de la Mac (4 de octubre)

Objetivo: comprobar, con los logs y no de memoria, qué pasa de verdad al reiniciar la Mac.

**La secuencia que hiciste tú:**

1. Reiniciaste la Mac. Con FileVault el disco arranca cifrado.
2. Desde Windows, `ssh mac`. Antes de macOS no hay usuario ni llave: el servidor SSH de arranque
   pide **la contraseña** (la del usuario de la Mac, tecleada por ti) y responde
   `System successfully unlocked`. Eso desbloquea el disco, nada más: **no inicia tu sesión**.
3. En la pantalla de inicio de sesión, el teclado Bluetooth no sirve (todavía no hay sesión que lo
   empareje) y Deskflow tampoco (vive dentro de tu sesión, ver la tabla de abajo). Entraste con un
   **teclado por cable**.
4. Al aparecer el escritorio, Deskflow conectó solo.

**¿Quién abrió Deskflow: nuestro LaunchAgent o macOS?** Deskflow estaba abierto antes de
reiniciar, y macOS reabre las apps que había ("Volver a abrir las ventanas al iniciar sesión").
Las dos cosas explicarían lo que viste. El log unificado lo decide. Ojo: por SSH `log show`
devuelve **0 líneas** (sshd no tiene permiso para leerlo); hay que correrlo dentro del tmux de la
sesión gráfica:

```bash
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -- bash -c "log show --style compact --start \"2026-10-04 09:28:00\" --end \"2026-10-04 09:29:10\" | grep -iE \"deskflow\""'
09:28:43.114 loginwindow  [TAL] persistentAppPreLaunch | Index:9, bundleID:org.deskflow.deskflow
09:28:43.114 loginwindow  [TAL] Launching:/Applications/Deskflow.app, hiddenFlag:0, stoppedFlag:1
09:28:43.275 launchd      [gui/501/application.org.deskflow.deskflow…] xpcproxy spawned with pid 1067
09:28:58.888 launchd      [gui/501/dev.enjambre.deskflow [1483]] xpcproxy spawned with pid 1483
09:28:59.478 launchd      [gui/501/dev.enjambre.deskflow [1483]] exited due to exit(0), ran for 591ms
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

*Render de la salida real con `freeze`, recortada.*

(Recortado; las horas son de la Mac.) `TAL` es *Transparent App Lifecycle*, la reapertura de
apps de macOS: **fue macOS** quien abrió Deskflow (pid 1067). Nuestro LaunchAgent corrió
**15,8 s después**, y su `open -a` encontró la app ya abierta y no hizo nada. `launchctl print`
lo confirma con `runs = 1, last exit code = 0`, que por sí solo no distingue los dos casos.

Conclusión: hoy el arranque funciona por dos caminos redundantes, pero **el LaunchAgent como único
lanzador sigue sin probarse**. Para probarlo hay que quitar la otra vía una vez: en la Mac, al
reiniciar, desmarcar "Volver a abrir las ventanas al iniciar sesión" (casilla del diálogo de
Reiniciar), y repetir el `log show` de arriba. Es tarea tuya (`NEEDS-OWNER.md`).

El tmux del paso 11 (`dev.enjambre.tmux`) sí arrancó por su LaunchAgent: lo prueba la misma
consulta, `09:28:58.896 … dev.enjambre.tmux [1487] spawned`, y no hay otra vía que lo abra.

### 8. Lo que NO se puede

| Situación | ¿Teclado y mouse de Windows en la Mac? | Por qué |
|---|---|---|
| Pantalla de FileVault al encender (desbloqueo del disco) | **No, nunca** | Corre antes de macOS y antes de la red: no existe ningún Deskflow todavía |
| Ventana de inicio de sesión de macOS | **No** | El cliente vive dentro de tu sesión de usuario. Un agente para la ventana de login necesitaría `/Library/LaunchAgents` (sudo) y con FileVault no serviría de nada |
| Pantalla bloqueada (sesión abierta) | **Sin verificar** | La sesión sigue viva y el cliente también; falta ver si macOS acepta la contraseña tecleada desde Deskflow |
| Aviso de UAC en Windows | **Sí** | Core elevado por el servicio |

Para un reinicio planificado de la Mac: `sudo fdesetup authrestart` desbloquea FileVault una vez
y llega hasta tu sesión. Para un corte de luz: hace falta un teclado USB en la Mac (o el KVM del
recuadro de arriba).

## Qué salió mal de verdad

0. **(4 oct) `failed to accept secure socket` en Windows, una vez por minuto.** El log del daemon
   de Windows lo repetía cada ~60 s desde que se instaló el vigilante de la Mac
   (`node-watch.sh`, página 15). La causa: para saber si Windows estaba vivo, el vigilante abría
   una conexión TCP al puerto 24800 con `nc -z` y la cerraba sin hablar TLS. Deskflow espera un
   saludo TLS y registra el error. La prueba fue la hora: el primer error tras el reinicio llegó
   a las 09:28:59.774, 0,9 s después de que launchd lanzara el vigilante, y luego uno por minuto.
   Arreglo: el vigilante ya no toca el puerto. Primero mira si el propio cliente Deskflow tiene
   una conexión `ESTABLISHED` con Windows (`netstat`, sin enviar nada), y solo si no la hay hace
   un `ping`. Después del cambio: **0 errores** en 9 sondeos seguidos.

El registro de Deskflow, en la misma captura, terminó con
`WARNING: fingerprint does not match trusted fingerprint` (la línea aparece cortada en la
captura). Lectura más probable, no verificada: la huella presentada no coincidía con una ya
confiada. La bitácora no anota cómo se resolvió; sí consta que
después Deskflow quedó funcionando. Si te pasa, no pulses *Yes* a ciegas: compara las huellas.

Sin otros tropiezos registrados en este paso, aparte del `brew trust` (ver `02-homebrew-y-shell.md`).

**Tropiezos del arranque automático (pasos 6–8):**

1. **La lista de servidores rompió el cliente.** La documentación dice que `remoteHost` acepta
   varias direcciones separadas por coma. Con `remoteHost=<ip>,<pc-windows>.local` el core dijo
   `FATAL: No server addresses configured` y no arrancó. Causa: Deskflow usa el formato INI de Qt,
   donde `a,b` sin comillas se lee como una **lista** y no como texto. Entre comillas
   (`remoteHost="<ip>,<pc-windows>.local"`) sí funciona: el log dice `trying next server in list`.
   Lo descubrí con un cliente de prueba, revertí la configuración real antes de que Deskflow la
   leyera, y dejé la IP sola, porque la ventana de Deskflow podría reescribirla sin comillas.
2. **Dos servidores no caben en una sesión de Windows.** Un servidor de prueba en otro puerto
   murió con `failed to create event for windows event loop`. La prueba de "el cliente espera al
   servidor" se hizo con la evidencia del log real (reconexión en 0,8 s) y con un cliente de prueba
   que reintenta cada segundo.
3. **El binario suelto no tiene red.** Desde el mismo tmux, `nc` conectaba a Windows y
   `deskflow-core` lanzado directamente decía `No route to host`. Es la privacidad de **Red local**
   de macOS: el permiso es de *Deskflow.app*, y un binario lanzado fuera de la app no lo hereda.
   Por eso el LaunchAgent usa `open -a` y no el binario.

## Cómo verificar

Hecho (por el owner, a mano): el cursor cruza de Windows a la Mac y el teclado escribe allí.

Hecho por Claude Code (2026-10-04):
- [x] Windows: servicio automático con core elevado; GUI en `HKCU\...\Run` con `autoHide=true`.
- [x] Mac: LaunchAgent `dev.enjambre.deskflow` cargado; `open` sobre la app ya abierta no duplica
      instancias.
- [x] Reconexión tras reiniciar el servidor: 0,8 s (log del daemon, 06:41:56 → 06:41:57).

Pendiente (owner, con `tools/deskflow-postcheck.sh` después de cada uno):
- [ ] Los cinco escenarios de la tabla del paso 7.
- [ ] Copiar texto en Windows y pegarlo en la Mac, y al revés.

## Autoevaluación

1. ¿Por qué el servidor es Windows y no la Mac?
   <details><summary>Respuesta</summary>Porque el servidor es la máquina donde está físicamente el
   teclado y el mouse.</details>
2. El cliente se conecta pero el cursor no se mueve en la Mac. ¿Qué miras primero?
   <details><summary>Respuesta</summary>Los permisos de Accesibilidad y Monitorización de entrada
   de Deskflow en Privacidad y seguridad.</details>
3. ¿Para qué sirve comparar huellas en el diálogo TLS?
   <details><summary>Respuesta</summary>Para confirmar que el cliente que se conecta es el tuyo y
   no otro equipo de la red; el cifrado solo no lo garantiza.</details>
