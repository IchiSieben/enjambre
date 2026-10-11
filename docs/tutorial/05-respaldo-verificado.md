# 05 — Respaldo verificado: liberar la Mac sin perder una sola foto

## Objetivo

Pasar fotos, videos y respaldos de la Mac al disco externo con **una sola copia de cada archivo**
(aunque en la Mac esté repetido dos o tres veces), comprobar cada copia por sha256 y solo entonces
mandar el original a la Papelera. Todo por `ssh mac`, sin `sudo`, sin abrir ninguna foto: se
trabaja con nombres, tamaños y hashes.

## Por qué

- **Un hash es la huella del contenido.** sha256 cambia si cambia un solo bit. Comparar el hash
  del original con el de la copia demuestra que son idénticas; comparar tamaños no.
- **El hash también encuentra duplicados.** Dos archivos con el mismo sha256 son el mismo archivo,
  se llamen como se llamen. `IMG_1234.HEIC` y `IMG_1234 (1).HEIC` pasados dos veces del iPhone
  dan el mismo hash: basta guardar uno.
- **Papelera, no `rm`.** Mover a la Papelera es reversible hasta que el dueño la vacía. Por eso
  "espacio liberado" y "espacio en Papelera" son dos números distintos: el primero no se mueve
  hasta que se vacía.
- **Regla 3-2-1.** Tres copias, dos medios, una fuera de casa. El disco externo es la segunda
  copia; el disco `F:` de Windows (paso 7) es la tercera. Ninguna está fuera de casa todavía.

## Antecedente: la primera pasada (encontrada en el disco)

Una sesión anterior ya había corrido `_backup_media.py` (está en el propio disco, en
`Backup-Mac/Fotos-AirDrop/`). Lo que dejó, leído de sus archivos y no reconstruido:

```text
$ tail -3 Fotos-AirDrop/_run.log
TOTAL 2048 files, 108.22 GB; mismatches: 0
duplicate groups: 72, redundant copies: 640, 0.27 GB
EXIT=0

$ cut -d, -f5 MANIFEST.csv | sort | uniq -c
1478                      <- verificados por sha256, original aún en la Mac
 570 eliminado-vacio      <- archivos de 0 bytes: copias del Finder interrumpidas
```

Las 570 vacías tenían la marca `brokMACS` del Finder (una copia que se cortó); ya se habían
mandado a la Papelera y están listadas en `Backup-Mac/_vacios-eliminados.txt`.

**Trampa evitada:** ese script tiene un subcomando `verify` que **reescribe** `MANIFEST.csv` con
4 columnas. Volver a correrlo habría borrado la columna `estado` y las 570 filas. Por eso esta
pasada usa un script nuevo, `tools/respaldo.py`, que solo **añade** filas, y antes se guardó una
copia: `MANIFEST.antes-de-2026-10-04-noche.csv`.

## Paso 1 — Inventario: qué ocupa la Mac

```bash
ssh mac 'df -h /System/Volumes/Data; cd ~; du -xsh .[!.]* * 2>/dev/null | sort -rh | head'
```

```text
/dev/disk3s5   460Gi   383Gi    51Gi    89%   /System/Volumes/Data
180G  Pictures      109G  Downloads    33G  Library     22G  modelos
 21G  Desktop        15G  Movies      3.8G  .Trash     1.9G  Documents
```

Desglose por tipo:

| Tipo | Dónde | GB | Qué se hace |
|---|---|---|---|
| Fotos y videos | `Downloads` (AirDrop), `Pictures/<respaldo iPhone 2024>`, `Pictures/icloud backup`, `Desktop`, `Movies`, `Documents` (sueltos) | ~230 | Se respaldan y van a la Papelera |
| Biblioteca de Fotos | `Pictures/Photos Library.photoslibrary` | 103 | **No se toca** (ver abajo) |
| Cachés | `Library/Application Support/Claude/vm_bundles`, `Library/Caches`, fototeca de iCloud | ~25 | Lista con recomendación |
| Modelos locales | `~/modelos` (llm, vlm, img, stt…) | 22 | No se mueven: los usa el nodo |
| Restos de pruebas | `audios-prueba`, `renders`, `.local`, `.cache` | <1 | Despreciable |
| Instaladores | `.dmg` en Descargas y Escritorio | ~8 | Lista con recomendación |

**Por qué no se toca la biblioteca de Fotos:** un `.photoslibrary` es un *paquete* (una carpeta que
macOS muestra como un archivo) con una base de datos que apunta a cada original. Sacar archivos de
dentro la corrompe. Lo mismo vale para bibliotecas de iMovie y TV, `CacheClip` de DaVinci,
`Lightroom` y `Photo Booth Library`: el script los salta enteros.

**Dónde caen las fotos que llegan del iPhone:** se buscó todo lo cambiado en los últimos 30
minutos (`find ~ -xdev -cmin -30 -type f`). Llegaban a `~/Downloads`; dentro de la fototeca solo
se movía la caché de sincronización de iCloud.

## Paso 2 — Escanear y hashear, ignorando lo que todavía se está copiando

```bash
scp tools/respaldo.py mac:.enjambre/respaldo/
ssh mac 'cd ~/.enjambre/respaldo && tmux new -d -s respaldo \
  "caffeinate -is python3 respaldo.py scan > scan.log 2>&1; echo EXIT=\$? >> scan.log"'
```

- `tmux`: si se corta el SSH, el trabajo sigue.
- `caffeinate -is`: ni la Mac ni el disco USB se duermen a mitad.

El escaneo se salta:

- archivos de **0 bytes**;
- archivos con `mtime` **o `ctime`** de hace menos de 15 minutos. `ctime` importa: AirDrop y el
  Finder conservan la fecha original del archivo, así que `mtime` no delata una copia recién
  llegada, pero `ctime` (cuándo cambió el inodo) sí;
- archivos con la marca `brokMACS` del Finder (copia en curso).

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

```text
candidates 10834 files 230.34 GB; unique 9787 222.17 GB; skipped 0
```

Dentro de la Mac ya había 8 GB repetidos (10 834 archivos, 9 787 contenidos distintos).

## Paso 3 — Copiar una sola vez cada hash, con rsync, y verificar

```bash
ssh mac 'cd ~/.enjambre/respaldo && tmux new -d -s respaldo \
  "caffeinate -is python3 respaldo.py copy > copy.log 2>&1; echo EXIT=\$? >> copy.log"'
```

Por cada archivo escaneado:

1. Si su sha256 ya está en el disco externo → **no se copia**; se añade una fila al manifest que
   apunta a la copia que ya existe (`estado` = `ya-en-externo` si venía de la primera pasada,
   `duplicado` si se copió en esta).
2. Si no → `rsync -t origen destino`, se calcula el sha256 **de la copia** y se compara. Si no
   coincide, va a `failures.tsv` y no entra al manifest.

Dónde queda cada cosa en el disco:

- Sueltos de `Downloads` y `Desktop`: `Fotos-AirDrop/AAAA/AAAA-MM/`, igual que la primera pasada.
- Todo lo demás: **espejo de la ruta** (`Backup-Mac/Pictures/...`). Así los `.AAE` (ediciones que
  hace el iPhone) quedan al lado de su foto en vez de separados por fecha.

**Por qué `-t` y no `-a`:** el disco es exFAT, que no guarda dueños ni permisos; `-a` falla o avisa
en cada archivo. Y copiar atributos extendidos a exFAT crea los `._nombre` (AppleDouble). El
`rsync` de macOS 27 es `openrsync` (protocolo 29).

**Lo que salió mal:** la primera versión del escaneo usaba `os.getxattr`, que no existe en el
Python que trae macOS (3.9 de las Command Line Tools):

```text
AttributeError: module 'os' has no attribute 'getxattr'
```

Se cambió por el comando `xattr -px com.apple.FinderInfo <archivo>`.

**Segundo tropiezo:** la copia y el paso 4 corriendo a la vez sobre el mismo disco mecánico se
estorbaban: la copia bajó a ~6 MB/s porque el cabezal saltaba de un archivo a otro. Se pausó el
paso 4 (`kill -STOP <pid>`) y la copia subió a ~58 MB/s (`iostat -d -w 2 disk6`). Un disco duro
rinde con una sola lectura o escritura secuencial a la vez.

## Paso 4 — A la Papelera solo lo que tiene copia idéntica

```bash
ssh mac 'cd ~/.enjambre/respaldo && tmux new -d -s papelera \
  "caffeinate -is python3 respaldo.py trash > trash.log 2>&1; echo EXIT=\$? >> trash.log"'
```

Para cada fila del manifest cuyo original sigue en la Mac:

1. se **recalcula** el sha256 del original y el de la copia en el externo (la verificación del
   paso 3 tiene horas: si algo cambió desde entonces, aquí se nota);
2. si los dos coinciden con el manifest → `/usr/bin/trash <archivo>`;
3. se anota en `Backup-Mac/_movidos-a-papelera.tsv` (hora, origen, bytes, sha256, copia).

`/usr/bin/trash` es el comando nativo de macOS; se probó antes con un archivo de prueba propio. A
diferencia de `mv ~/.Trash/`, deja los archivos con "Devolver" disponible en el Finder.

Se empezó por los 1 478 archivos (108 GB) que ya estaban verificados.

**Lo que pasó de verdad.** Con la copia y la Papelera leyendo el mismo disco USB a la vez, la
copia cayó a ~6 MB/s (el cabezal salta entre dos zonas del disco). Se pausó la Papelera sin
matarla, con `kill -STOP <pid>` (estado `T` en `ps`), y se reanudó con `kill -CONT <pid>` cuando
la copia escribió `EXIT=0`:

```
copy done: 9356 rows; copied 8380 files 114.22 GB; repeated (not copied) 976 files 7.91 GB
EXIT=0
```

Antes de reanudar, una muestra independiente: 200 copias al azar del externo, sha256 recalculado
contra el manifest.

```
manifest rows 11404
unique dest 9858 dest with >1 sha 0
SAMPLE 200: ok 200 mismatch 0 missing 0 bytes 4.31 GB
```

La Papelera solo procesa las filas del manifest que leyó al arrancar, así que hace falta una
**segunda pasada** (`scan → copy → trash`), encadenada en su propio tmux para no competir por el
disco:

```bash
# ~/.enjambre/respaldo/pasada2.sh
while kill -0 <pid-papelera> 2>/dev/null; do sleep 30; done
for step in scan copy trash; do
  caffeinate -is python3 respaldo.py $step > ${step}2.log 2>&1
  echo EXIT=$? >> ${step}2.log
done
```

El primer intento con todo el comando entre comillas dentro de `tmux new "..."` perdió el `$?`
(quedaba `EXIT=\0` literal): tres niveles de comillas (PowerShell/Bash → ssh → tmux → sh). Un
script de 8 líneas no tiene ese problema.

```
trash done: 1950 files 110.01 GB moved to Trash; refused 0        # 1a pasada
copy done: 0 rows; copied 0 files 0.00 GB                          # 2a: nada nuevo
trash done: 8878 files 120.15 GB moved to Trash; refused 0        # 2a pasada
```

Al final `~/.Trash` pesaba 7,6 GB, no 230: el dueño la vació a mano (~00:52) mientras la segunda
pasada seguía corriendo; solo quedaron los ~25 archivos movidos después. Los scripts nunca la
vacían, solo llaman a `/usr/bin/trash`. Cada archivo se verificó dos veces contra el externo antes
de moverse, así que no se perdió nada, pero ya no hay "Devolver": hasta que termine la tercera
copia en Windows, el externo es la única copia.

## Paso 5 — Lo que no es foto ni video

No se movió nada de esto. Ver la tabla del informe final.

## Paso 6 — Tercera copia en Windows

Windows no tiene `sshd`, así que la Mac no puede empujar a `F:`; Windows tira de la Mac con
`tools/espejo-windows.sh` (`tar` por SSH, y después sha256 de cada archivo contra el manifest).
Para que no dependa de la ventana de Claude Code se lanza con WMI, que lo cuelga de `WmiPrvSE` y
no de la terminal:

```powershell
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = '"C:\Program Files\Git\bin\bash.exe" -lc "bash /f/Backup-Mac-espejo-lanzador.sh"' }
```

El lanzador espera a que la segunda pasada escriba `EXIT=` y recién entonces lee el disco.
`COPYFILE_DISABLE=1` en el `tar` de macOS evita que meta un `._archivo` con los xattrs de cada
foto.

```
2026-10-05 00:53:45 source: 9868 files, 222.44 GB
2026-10-05 00:53:51 to transfer: 9868 files
```

Se da por bueno cuando el log `F:\Backup-Mac-espejo.log` termina en
`WINDOWS VERIFY: ok ... mismatches 0, missing 0`.

## Cómo verificar

```bash
ssh mac 'python3 ~/.enjambre/respaldo/respaldo.py report'
```

## Autoevaluación

1. ¿Por qué se recalculan los dos hashes justo antes de mandar a la Papelera, si ya se verificó
   al copiar?
   <details><summary>Respuesta</summary>Porque entre la copia y la Papelera pueden pasar horas:
   el original pudo cambiar o la copia dañarse. La comprobación vale en el momento en que se hace.
   </details>
2. ¿Por qué `ctime` y no solo `mtime` para detectar transferencias en curso?
   <details><summary>Respuesta</summary>AirDrop y el Finder conservan la fecha de modificación
   original; `ctime` cambia al crear el archivo en esta Mac y no se puede fijar desde fuera.
   </details>
3. ¿Por qué no se sacan fotos de `Photos Library.photoslibrary`?
   <details><summary>Respuesta</summary>Es un paquete con una base de datos que apunta a cada
   original; quitarle archivos la deja corrupta.</details>
4. Mandaste 100 GB a la Papelera. ¿Cuánto espacio ganó la Mac?
   <details><summary>Respuesta</summary>Ninguno hasta vaciarla. Y en APFS puede ser menos que
   100 GB: si dos archivos eran clones que compartían bloques, solo se libera cuando se van
   todos.</details>
