---
lesson:
  route: build-stack
  order: 18
  level: 2
---

# 19 — Discos compartidos: la Mac usa carpetas de Windows como propias

## Objetivo

Que la Mac vea `F:\Compartido-Mac` (intercambio y videos) y `F:\Backup-Mac` (su
respaldo, solo lectura) como carpetas montadas. Que se monten solas al iniciar sesión,
reintenten si Windows no está y que `enjambre watch` diga si siguen vivas.

## Por qué

Copiar con `scp` sirve para un archivo, no para un flujo de trabajo. SMB es el
protocolo de carpetas compartidas de Windows, y macOS lo habla de serie. Dos ideas para entender
los permisos:
- **Se comparten carpetas, no discos enteros.** Un error expone una carpeta, no todo el disco.
- **Hay dos capas de permisos.** El permiso del *recurso compartido* (SMB) y el del *sistema de
  archivos* (NTFS) se aplican los dos, y **gana el más restrictivo**. Por eso "solo lectura" se
  pone en las dos capas.

Y un usuario **dedicado**, `usuario-smb`, que solo sirve para esto. Si su contraseña se filtra, no
abre tu sesión de Windows.

## Qué va en cada disco

| Disco | Qué es | Libre | Salud | Uso | ¿Compartido con la Mac? |
|---|---|---|---|---|---|
| C: | SSD SATA 480 GB | 21,7 GB | Windows: *Healthy* | Sistema | **No.** Hoy `Users` lo expone (ver diagnóstico) |
| D: | HDD 2 TB | 763 GB | Windows: *Healthy* | Datos de Windows | No |
| E: | SSD NVMe 500 GB (Samsung 970 EVO) | 65,7 GB (86 % lleno) | **SMART pendiente** (pide administrador) | Trabajo rápido | Solo si SMART sale sano: `E:\Trabajo-Mac`. Poco espacio para video |
| F: | HDD 4 TB para NAS (Seagate IronWolf) | 1 330 GB | Windows: *Healthy* | Intercambio, videos, respaldos | `F:\Compartido-Mac` (lectura/escritura) · `F:\Backup-Mac` (solo lectura) |

*Healthy* es la opinión resumida de Windows, no una lectura SMART. Para E:, que sería el disco de
trabajo, se exige SMART (`NEEDS-OWNER` §11, bloque C).

## Pasos

### 1. Diagnóstico: qué comparte Windows hoy

```
PS> net share
F            F:\
ixf          F:\
Users        C:\Users
(+ los administrativos C$, D$, E$, F$, ADMIN$, IPC$)
PS> Get-SmbShareAccess -Name Users
Everyone                Allow  Full
PS> Get-NetFirewallProfile | Select Name,Enabled
Domain False · Private False · Public False
```

Hay tres recursos viejos para *Everyone*: el disco F: entero (dos veces, en lectura) y
`C:\Users` con **control total**. Además, el firewall está **apagado**: cualquier equipo de la red
llega al puerto 445. Invitado está desactivado, y la Mac recibe "Authentication error" si intenta
listar sin usuario (`smbutil view -N`), así que no hay acceso anónimo. Pero *Everyone* incluye a
cualquier cuenta que sí entre.

La Mac sale por Wi-Fi, en la misma subred que Windows, que va por cable a 1 Gbps.

### 2. La propuesta (escrita antes de aplicar nada)

| Recurso | Carpeta | Desde la Mac | Capa SMB | Capa NTFS | Cifrado |
|---|---|---|---|---|---|
| `Compartido-Mac` | `F:\Compartido-Mac` | leer y escribir | `usuario-smb`: Full | `usuario-smb`: Modify | sí (SMB 3) |
| `Backup-Mac` | `F:\Backup-Mac` | **solo leer** | `usuario-smb`: Read | `usuario-smb`: **Deny** escribir/borrar | sí |
| `Trabajo-Mac` | `E:\Trabajo-Mac` | leer y escribir | solo si SMART sale sano | | |

Nada para *Everyone* ni para Invitado. Y antes, quitar los recursos viejos `F`, `ixf` y `Users`.
Con `icacls F:\` se ve por qué: F: entero da **Modificar** a *Authenticated Users* por NTFS. Con
`F` publicado para *Everyone*, cualquier cuenta que entre, `usuario-smb` incluida, leería todo el
disco. Y en `Backup-Mac` "dar lectura" no restringe nada, porque la cuenta ya hereda Modificar:
hay que **denegar** escribir y borrar. Una denegación gana a cualquier permiso. Los comandos exactos y el orden están en `NEEDS-OWNER` §11.
Windows exige administrador, así que los ejecuta el dueño.

`F:\Backup-Mac` ya existe: es el espejo del respaldo de la Mac (tutorial 05), y se compartirá
**cuando el espejo termine**. Agregar un permiso heredable reescribe la lista de acceso de cada
archivo, y no conviene hacerlo en plena copia.

### 3. Usuario dedicado y recursos (dueño, como administrador)

*Pendiente: el dueño ejecuta el bloque A de `NEEDS-OWNER` §11.* La línea clave es la de la
contraseña:

```powershell
$p = Read-Host -AsSecureString "Contrasena para usuario-smb"
```

`Read-Host -AsSecureString` no muestra lo que tecleas, no lo deja en el historial de PowerShell y
lo guarda cifrado en memoria. Ningún agente la ve. La cuenta se crea con `-PasswordNeverExpires`,
porque si la contraseña caducara, el montaje de la Mac dejaría de funcionar a las semanas sin
ningún aviso.

La credencial llega a la Mac por el **Llavero**: el dueño se conecta una vez con Finder (⌘K →
`smb://<ip>/Compartido-Mac`) y marca "Recordar en el llavero". Después nadie vuelve a teclearla.

### 4. Montaje automático en la Mac

`smb-mount.sh` corre cada 60 s desde un LaunchAgent (`dev.enjambre.smbmount`), dentro de la sesión
gráfica, que es donde está el Llavero:

```
mac$ cat ~/.enjambre/mounts.json
{"checked_at": "2026-10-05T06:18:57Z", "reach_445": true, "keychain": false,
 "mounts": [{"name": "Compartido-Mac", "ok": false, "state": "no-keychain", "path": "~/Compartidos/Compartido-Mac"}, ...]}
```

Tres reglas para que no moleste:
- **Nunca abre un diálogo.** Solo intenta montar si Windows responde en el puerto 445 **y** el
  Llavero tiene la credencial. Si no, anota el motivo (`unreachable`, `no-keychain`) y espera.
  Un montaje fallido desde Finder abre una ventana de error, y cada 60 s se apilarían.
- **Reintentar es simplemente la siguiente vuelta.** Si Windows está apagado, el siguiente
  minuto lo vuelve a probar.
- **Detecta montajes colgados.** Si Windows desaparece con la carpeta montada, `ls` se queda
  esperando para siempre. macOS no trae `timeout`, así que el script lo corta a los 5 s y marca
  `hung`.

Se monta en `~/Compartidos/<recurso>` (rutas fijas) y `Backup-Mac` con `-o rdonly`, además del
permiso de Windows.

### 5. Que `enjambre watch` vea el montaje

`watch` copia el informe de la Mac en `state/nodes.json`, bajo `nodes.mac.mounts`, y avisa si un
recurso se cae o vuelve. Formato (ejemplo; aparece cuando la tarea `watch` se reinicia y carga el
código nuevo):

```
PS> python enjambre.py status
nodes  (watch, 12 s ago)
  mac    ready  since ...
         share Compartido-Mac   NO  no-keychain   ~/Compartidos/Compartido-Mac
```

El informe se lee con un `ssh cat` **aparte** de la sonda de presencia: un montaje colgado no
puede bloquear la respuesta de "¿está viva la Mac?". Un informe de más de 3 min se trata como
desconocido, porque desconocido no es "vivo".

### 6. Velocidad: SMB contra scp

```
PS> bash tools/smb-bench.sh 5      # una línea JSON en state/smb-bench.jsonl
```

Mide cuatro cosas con un archivo de 5 GB: lectura y escritura SMB desde la Mac, y `scp` en los dos
sentidos. `scp` siempre se lanza desde Windows, porque Windows no tiene servidor SSH. Las trampas
que evita:
- **La lectura usa un archivo que Windows escribe nuevo en cada corrida.** Si la Mac leyera algo
  que acaba de escribir, lo serviría desde su propia caché y mediría la RAM, no la red.
- **La escritura sale de `/dev/urandom`.** Antes se cronometra esa fuente sola, para no confundir
  un generador lento con una red lenta.
- **Cada línea anota la interfaz de la Mac (`en1` = Wi-Fi, `en0` = cable) y el cifrado del
  recurso.** Así, cuando llegue el cable, basta con repetir el mismo comando y comparar.

Primera corrida, **solo `scp`**: el recurso todavía no existe, así que las columnas SMB salen
`null`. Se esperó a que el espejo de 222 GB terminara (03:36), porque usaba el mismo Wi-Fi.

```
PS> bash tools/smb-bench.sh 5
Compartido-Mac not mounted on the Mac: SMB legs skipped (null), scp only
{"gib": 5.0, "mac_iface": "en1", "smb_read_mbs": null, "smb_write_mbs": null,
 "scp_win_to_mac_mbs": 53.4, "scp_mac_to_win_mbs": 66.7, "mac_load_and_sesion_g_tmux": "1.34 4.22 6.90 0"}
```

| Medición (5 GiB, Mac por Wi-Fi, Windows por cable) | MB/s |
|---|---|
| `scp` Windows → Mac | 53,4 |
| `scp` Mac → Windows | 66,7 |
| SMB lectura / escritura | *pendiente del bloque A* |

`en1` es el Wi-Fi de la Mac. La carga de la Mac venía bajando (1,34 en el último minuto, 6,90 de
media en 15 min) y no había ningún tmux `sesion-g*`. A 53–67 MB/s, un video de 10 GB tarda unos
3 min. Cuando llegue el cable, se repite el mismo comando y la línea dirá `en0`.

### 7. Firewall: solo SMB, Deskflow y ping desde la red local

*Pendiente: bloque D de `NEEDS-OWNER` §11. Lo ejecuta el dueño como administrador.* El orden
importa:
1. **Primero la auditoría.** Toda regla de entrada que ya esté activada se vuelve real en el
   momento en que se enciende el firewall.
2. **Después, tres reglas propias** limitadas a la subred local: 445, 24800 y ping. Van en un
   grupo para poder borrarlas de una vez.
3. **Al final, encenderlo.**

SSH no lleva regla: Windows no recibe conexiones SSH, solo las abre hacia la Mac.

Volver atrás:

```powershell
Set-NetFirewallProfile -All -Enabled False
Remove-NetFirewallRule -Group enjambre-LAN
```

## Qué salió mal de verdad

1. **Sin administrador no se ve casi nada de lo que importa.** La salud SMART de E:
   (`Get-StorageReliabilityCounter`) y la lista de reglas del firewall respondieron
   "Access denied". Los dos quedan como bloques que ejecuta el dueño; no se inventó un "parece
   sano".
2. **La carpeta propuesta para el respaldo ya existía, y se estaba escribiendo.** `F:\Backup-Mac`
   es el destino de un espejo de 222 GB que otra sesión dejó corriendo. Compartirla en ese momento
   habría reescrito permisos en plena copia, así que su bloque espera a que el espejo cierre.
3. **Una virgulilla que no se expande.** En el script de velocidad, `dd if=~/Compartidos/...`
   enviado por SSH no llega a ser la carpeta del usuario: el shell solo expande `~` al principio
   de una palabra, y `if=~` no lo es. Lo detectó `shellcheck` (SC2088) antes de correrlo.
   Arreglo: `$HOME`, que expande el shell de la Mac.

4. **"Dos capas de permisos" no basta si una capa ya está abierta.** El plan inicial daba a
   `usuario-smb` lectura por NTFS en `Backup-Mac`, pero `icacls F:\` mostró que *Authenticated Users*
   tiene Modificar heredado en todo F:. Esa lectura no habría quitado nada. Arreglo: una entrada
   *Deny* para escribir y borrar, y quitar los recursos viejos antes de crear los nuevos.

## Cómo verificar

Pendiente: el montaje real no se ha podido comprobar, porque el dueño no ha ejecutado los
bloques A y D de `NEEDS-OWNER` §11. Lo único medido hasta hoy es la línea base con `scp`.
Cuando se ejecuten, los comandos son estos:

```
mac$ cat ~/.enjambre/mounts.json                 # cada recurso debe decir "ok": true
PS> python enjambre.py status                    # una línea "share ..." por recurso
PS> bash tools/smb-bench.sh 5                    # llena smb_read_mbs y smb_write_mbs
```

Observado hoy (antes del bloque A): `reach_445: true`, `keychain: false`, estado `no-keychain`, y
`scp` a 53,4 MB/s (Windows a Mac) y 66,7 MB/s (Mac a Windows), con las columnas SMB en `null`.

## Autoevaluación

1. Si el recurso compartido da "Full" pero NTFS da "Read", ¿qué puede hacer la Mac? ¿Por qué?
2. ¿Por qué el script de montaje mira el puerto 445 y el Llavero *antes* de intentar montar?
3. Con el firewall encendido y solo las tres reglas, ¿sigue funcionando `ssh mac` desde Windows?
