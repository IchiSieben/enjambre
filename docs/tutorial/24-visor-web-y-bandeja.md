# 24 — Visor v3, copia web y bandeja de tareas

**Qué consigues:** un visor local con dos costos siempre a la vista y un historial que no se borra;
una copia web redactada del visor y una bandeja para dejar tareas desde el celular, las dos
**preparadas y probadas en local, sin desplegar**.
**Quién lo hace:** Claude Code (2026-10-07). Tú, nada hasta los pasos de
NEEDS-OWNER §19: crear el subdominio, la contraseña y las claves.

**Estado, para no confundir lo medido con lo supuesto:**

| Parte | Estado |
|---|---|
| Visor v2/v3 local | hecho y en uso en `win` |
| Copia web (`visor.ichisieben.dev`) | construida y probada contra PHP local; **no desplegada** |
| Bandeja | construida y probada de punta a punta en local con la Mac real; **no desplegada** |

## Por qué así: tirar, redactar con lista de permitidos, firmar

Tres ideas, una por parte:

- **Tirar en vez de empujar.** Hostinger no puede llamar a casa; la PC pregunta cada pocos
  minutos. Ficha: [Pull vs push](../conceptos/pull-vs-push.md).
- **Lista de permitidos.** Lo que sale a internet no es "todo menos lo secreto", sino "solo lo
  que se copió campo a campo". Ficha: [Permitidos vs prohibidos](../conceptos/permitidos-vs-prohibidos.md).
- **Firma sobre el propósito.** Cada petición lleva un HMAC que solo vale para un endpoint. Diseño
  completo de la bandeja: [BANDEJA.md](../BANDEJA.md); aquí no se repite.

## A) Visor v2/v3 (local)

### Diseño

- Sistema de diseño del sitio de portafolio del owner: oscuro por defecto, Space Grotesk y
  JetBrains Mono incrustadas como URIs `data:`. Sin dependencias externas, así que sirve con una
  CSP estricta y sin red.
- Jerarquía de arriba abajo: **en vivo → consumo → proyectos → modelos de la Mac → historial**.
- Siempre dos costos, nunca uno solo:
  - **Equivalente API:** un piso, no una factura. Precio de lista × tokens, incluidas las
    escrituras de caché (5 min = 1,25× el precio de entrada; 1 hora = 2×). Está marcado como
    *estimado*.
  - **Cuota:** el % de la suscripción (ventanas de 5 h y 7 d).

### Historial propio

Cada nodo tiene su base SQLite en `~/.enjambre/visor/history.db` (tablas `files`, `msgs`,
`sessions`, `meta`). **Nunca se poda**: cuando Claude Code borra los `.jsonl` viejos, el historial
sigue ahí. Reglas:

- El `history.json` anterior se importa una sola vez.
- Las copias `~/.claude.bak-*` se leen como raíces extra. Si la misma sesión está en dos raíces,
  cuenta una vez: gana el transcript principal más largo, y las respuestas se deduplican por id
  de mensaje.

Resultado real en `win`:

| Medida | Valor |
|---|---|
| Transcripts leídos | 498 |
| Sesiones | 149 |
| Rango | 2026-08-13 → 2026-10-07 |
| Tiempo por pasada | 2 s |
| Sesiones que solo existían en la copia de respaldo | 31 |
| Filas de respuesta / ids distintos | 30 902 / 29 305 |

### Semáforos

Los umbrales salen de `~/.claude/rules/thresholds.json`, no de números recordados:

| Semáforo | Ámbar | Rojo |
|---|---|---|
| `visor-disk` | 80 | 90 |
| `visor-mem` | 80 | 92 |
| `visor-quota-5h` | 70 (`visor-quota-5h-amber`) | 90 (`quota-5h`) |
| `visor-quota-7d` | 70 | 90 |
| trabajos fallidos / incompletos | — | cualquiera > 0 |

Llevan `hint:false`: la línea de estado los ignora (métrica desconocida = `-1`, nunca dispara).
Si el archivo no se puede leer, el semáforo sale **gris con "sin umbral"**; nunca un verde
inventado.

### De Windows a la Mac: qué sesión mandó el trabajo

`enjambre run` guarda `origin_session` (de la variable `CLAUDE_CODE_SESSION_ID`) en el ledger y en
la cola. Los trabajos que despacha `watch` reciben `ENJAMBRE_ORIGIN_SESSION` desde `job.json`,
para no heredar la sesión del propio `watch`.

### Íconos y gráficos

- Íconos de tecnología: del sprite del portafolio (`public/icons/skills.svg`, Simple Icons, CC0),
  emparejados por nombre de carpeta, nombre del repo de la tarjeta o alias en
  `config/visor-web.toml`.
- Gráficos: barras apiladas en SVG hechas a mano, por día y por máquina. Un solo tooltip
  delegado (hover, foco y toque). Sin biblioteca.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*
*Antes y después a 390 px de ancho, redactados con la lista de permitidos de `--demo`.*

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## B) Copia web en `visor.ichisieben.dev` (preparada, no desplegada)

Espera el OK del owner en cada paso. Hoy no hay nada en Hostinger.

### Dos muros contra las fugas

1. **Lista de permitidos** (`web.public_snapshot`): cada campo se copia de forma explícita. Lo que
   no está escrito ahí no sale.
   - Nombres de proyecto → alias estables `proyecto-xxxxx`, por HMAC con una sal aleatoria local.
   - Discos → `disco-N`; montajes → `recurso-N`; tmux, solo un conteo.
2. **`leak_check`**: segundo muro que **bloquea la subida** si encuentra palabras `confidencial`/`reservado`,
   cadenas con forma de ruta, IPv4, o nombres privados (los de `ssh -G` y el usuario local).

### Subida y acceso

- La subida es un POST firmado cada 10 min desde la tarea del visor que ya existía.
  Firma: HMAC-SHA256 sobre `propósito\nts\ncuerpo`, con `ts` en milisegundos de época, ventana de
  ±300 s y monótono por propósito: una repetición recibe 409.
- La página está protegida con Basic auth (`.htaccess`), cabeceras `noindex` y `robots.txt` con
  `Disallow`.
- `data.php` **falla cerrado** y usa `REMOTE_USER`, nunca `PHP_AUTH_USER`: un cliente puede
  falsificar este último si el `.htaccess` no se aplicara.

### Empaquetar

```
PS> python tools/web/build_dist.py
```

Salida: `state/web-dist/visor-app` y `visor-app.zip`, **9 archivos**. `index.html` no lleva datos.

## C) Bandeja: del celular a la Mac

Resumen del recorrido; las tablas del contrato, las firmas y los estados están en
[BANDEJA.md](../BANDEJA.md).

1. El formulario guarda una tarea firmada **fuera del docroot**.
2. Dentro de `watch`, la PC pregunta cada 2 min con un GET firmado (`poll`).
3. La PC verifica el HMAC **sobre los bytes guardados, antes de parsear**.
4. Vuelve a aplicar **todas** las reglas, sin fiarse del PHP:
   - lista cerrada de repos;
   - `kind` que exista también en `routing.toml`;
   - lista de workers;
   - regex de rutas, sin `..`;
   - `confidencial`/`reservado` estructural en rutas, y con límite de palabra en texto libre (así "análisis"
     pasa);
   - límite diario por día de Lima.
5. Escribe `TASK-<id>.md` y encola en enjambre. **Nunca un shell**: el único argv es
   `enjambre.py run --repo <de la lista> --task <ruta generada>`.
6. Mismo `worker.sh` con el mismo `--allowedTools`, `--max-turns 8` por `ENJAMBRE_MAX_TURNS`, sin
   bypass y **sin reintentos** (la tarea se marca vista *antes* de encolar).
7. Estados `recibida / corriendo / hecha / fallida`, con rama y un resumen de 500 caracteres que
   pasa por el mismo `leak_check`.

### Pruebas

- **14 unitarias:** firma válida e inválida (incluido un cuerpo alterado), repo fuera de la lista,
  rutas sensibles, duplicado que nunca se encola dos veces, límite diario y su reinicio al día
  siguiente, Mac caída (la tarea se queda en `queue/`), rechazo del enrutador, avance hasta
  `hecha`.
- **4 de integración** contra el PHP real bajo `php -S`: falla cerrado sin usuario ni firma,
  repetición 409, propósito equivocado 401, fuera de ventana 401, formulario → PC → de vuelta, y
  subida + lectura de datos.

### De punta a punta, en local

Formulario → PC → Mac con el worker `stub-ok` ($0) → estado **"hecha"**. Rama
`mac/b20261007-653a883f` subida, resumen "stub ok", unos 62 s en total.

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

## Lo que falló

1. **El antivirus borró el router de prueba.** El `php -S` de pruebas usaba un script router que
   hacía `require` de una ruta derivada de la petición; el antivirus lo tomó por una webshell, lo
   borró y mató `php -S` a mitad de prueba. Arreglo: `-d auto_prepend_file` con un archivo de una
   línea que copia una cabecera de prueba a `REMOTE_USER`, escrito en un directorio temporal.
2. **El cliente de la PC aceptaba cualquier HTTP 200**, incluida una página de error de PHP.
   Ahora exige `{"ok":true}`.
3. **Falso positivo de IPv4.** El segundo muro bloqueó la carga porque la ruta SVG del logo de
   macOS contiene `2.774.6.926.466`, que parece una IPv4. Arreglo: los íconos de sistema operativo
   pasaron a la plantilla HTML, no a los datos. El muro hizo su trabajo; el dato estaba mal
   ubicado.
4. **PHP portable sin `mbstring`.** Se añadió un `cut()` de respaldo con `preg`.

## C2) Segunda vuelta del visor

**Objetivo:** que el visor hable de aparatos y no de ids, que cuente el gasto por semana y por
mes, y que cada trabajo, modelo y subagente se pueda abrir y filtrar.

**Por qué así:** un visor que dice `win` y `mac` obliga a saber cómo está configurado el enjambre;
uno que dice "PC" y "Mac mini" se entiende sin saberlo. Los datos ya existían casi todos (el
ledger, `measurements.jsonl`, los `.meta.json` de los subagentes); faltaba leerlos completos en
vez de quedarse con el último.

1. **Etiquetas de aparato** en `nodes.toml`:

   ```toml
   [nodes.win]
   label = "PC"
   [nodes.mac]
   label = "Mac mini"
   # [nodes.laptop]     # cuando entre: label = "Laptop"
   ```

2. **Logos oficiales a color** (devicon, MIT), vendorizados y limpiados por
   `tools/visor/icons/vendor_devicon.py`. Donde no hay versión oficial, o se pierde en el tema
   oscuro, queda el glifo de color de marca del Landing. La interacción es la de las fichas del
   Landing: brillo del color, logo que se inclina y tooltip con qué es cada tecnología.

   ```text
   $ python -I tools/visor/icons/vendor_devicon.py
   ok     python             2345 bytes
   ok     githubactions      2797 bytes
   skip duckdb           HTTPError          <- devicon no lo tiene: queda el glifo de marca
   ok     linux            198906 bytes     <- Tux: demasiado pesado, se descartó a mano
   ```
3. **Consumo**: hoy, luego 7 días, luego todo el historial, cada uno con su promedio diario.
   Además, una tabla y un gráfico por semana ISO y por mes.

   *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

4. **Modelos locales**: cada medición del banco con fecha y hora, y los trabajos del gestor, con
   filtro por tarea y por fecha. El banco de `modelos-mac` guarda ahora la hora (`ts`) en cada fila
   nueva. Para una fila vieja, el visor usa la hora de su archivo de resultado solo si hay uno que
   coincide, y la marca con `~`.

   *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

5. **Historia**: todos los trabajos del ledger y los trabajos largos de cada aparato,
   filtrables por proyecto. Al tocar uno se abre su detalle: estado, aparato, rama, coste, motivo
   de ruta, resultado y log. Un trabajo largo (`jobrun.py`) trae **latido real**: su supervisor
   reescribe `status.json` en cada latido. Uno corto muestra la **última señal**, derivada de la
   hora del archivo más nuevo de su carpeta en la cola.

   *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

6. **Por proyecto**: hilos (conversaciones), subagentes (tipo y descripción, leídos del
   `.meta.json` de cada uno) y trabajos del enjambre. Tocar una fila la marca en la línea de
   tiempo.

   *(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

7. **PWA preparada y apagada**: `tools/web/pwa/` (manifest e iconos) y `[web] pwa = false`. Con
   `true`, `build_dist.py` los incluye y enlaza el manifest.

Instalar el colector nuevo en la Mac y actualizar `modelos-mac` allí:

```text
PS> scp tools/visor/collect.py mac:.enjambre/visor/collect.py
$ ssh mac 'launchctl kickstart gui/$(id -u)/dev.enjambre.visor'
        last exit code = 0
$ ssh mac '~/.enjambre/tmux-run.sh -C ~/Documents/Portfolio/modelos-mac -t 150 -- git pull --ff-only'
   73fc382..eac80df  main       -> origin/main
```

Resultado medido: 44 mediciones en la Mac; la copia web pasa el muro y pesa 136 KB (antes 64 KB;
45 KB son subagentes). Sin errores de JavaScript, sin desplazamiento horizontal a 390 px y ningún
`win`/`mac` suelto en el texto (comprobado con Playwright).

**Lo que falló:**

- El primer recorrido mostró "Mac" en vez de "Mac mini". `gather()` copiaba solo algunas claves
  de cada nodo y `label` no estaba entre ellas. Una prueba lo cubre ahora.
- La primera versión de la lista de trabajos dejaba fuera los `stub-ok` por ser "de prueba", y
  con eso desaparecía justo la prueba de la bandeja desde el celular. Ahora entran todos.
- Las filas viejas del banco solo tenían fecha. No se inventó la hora: "hora no registrada"
  cuando hay más de un archivo candidato.
- La primera versión pintaba los logos de un solo color, como el Landing, y el pedido era "los
  SVG oficiales, no monocromo". Se vendorizaron los oficiales. Astro y three.js volvieron al
  glifo de marca porque son negros y no se ven en el tema oscuro.
- La revisión de ids sueltos leía la página antes de abrir los detalles y por eso no veía el
  motivo de ruta ("tests -> mac: ..."). Ahora abre cada trabajo y revisa también el tooltip.

## D) Modelo de amenazas en una página

| Quién | Qué tiene | Puede | No puede |
|---|---|---|---|
| Alguien con solo la URL | Nada | Ver un aviso de login; `robots` y `noindex` lo mantienen fuera de buscadores | Leer datos ni tareas |
| Quien tiene la contraseña Basic | Acceso a la web | Leer la instantánea redactada (alias, conteos, costos; sin rutas, nombres ni IP). Crear tareas, pero solo contratos de repos, kinds y workers listados, con el tope diario de la PC, con herramientas restringidas, en una rama y **sin fusión automática** | Ejecutar comandos de shell, tocar rutas `confidencial`/`reservado`, llegar a la red de casa |
| Quien tiene el token de la bandeja (clave HMAC) | La clave de `bandeja_key` | Falsificar tareas con firma válida; marcar estados de tareas | Salirse de las listas cerradas ni del límite diario de la PC; saltarse `worker.sh`. Mitigación: rotar la clave en `config.php` y en la variable de entorno de Windows |
| Quien tiene la clave de subida | La clave de `ingest_key` | Sobrescribir la instantánea mostrada (desinformación) | Crear tareas: hay una clave distinta por propósito |
| Quien compromete la cuenta de Hostinger | Todo lo anterior | Todo lo anterior | Entrar a casa: la PC solo pregunta, nada escucha en la red de casa |

**Qué prueba la firma:** que la tarea la escribió el PHP que tiene la clave. **Qué no prueba:** que
la escribiste tú. Por eso las reglas de verdad corren en la PC.

## E) Parte 2: el visor en internet (2026-10-07)

**Objetivo:** `https://visor.ichisieben.dev` detrás de contraseña, con la bandeja abierta al celular.

**Por qué en este orden:** primero se sube el código y se comprueba que **falla cerrado** (sin
contraseña ni `config.php`, todo responde 401 o 503); recién después se ponen los secretos. Si un
paso sale mal, lo que queda expuesto es una página que no responde, no un visor abierto.

1. **Subdominio** con la API de Hostinger (`hosting_domains_create-website-subdomain`,
   `subdomain: visor`, `directory: visor-app`). Queda en `public_html/visor-app`, al lado de
   `radar-app` y `metal-app`. PHP del sitio: 8.3.
2. **Subida archivo por archivo** (TUS, `hosting_files_generate-upload-url`), solo dentro de
   `visor-app/`. El deploy "por zip" de la API **reemplaza el sitio padre**, que aquí es el
   Landing: por eso no se usa.

   ```text
   $ python tools/web/build_dist.py && python tus_up.py
   ok  201/204    1455 .htaccess
   ok  201/204     194 api/.htaccess
   ...
   ok  201/204     766 pwa/manifest.webmanifest
   ```
3. **Matriz de verificación** sin credenciales:

   ```text
   https://visor.ichisieben.dev/                       401
   https://visor.ichisieben.dev/data.php               401
   https://visor.ichisieben.dev/lib.inc.php            403
   https://visor.ichisieben.dev/api/tareas.php         503   {"error":"sin configurar"}
   https://visor.ichisieben.dev/pwa/manifest.webmanifest 200 application/manifest+json
   https://ichisieben.dev/visor-app/                   401   (la ruta por el dominio padre también)
   http://visor.ichisieben.dev/                        301   (lo hace Hostinger antes que el .htaccess)
   ```
4. **Claves:** un script las genera, las guarda como variables de entorno de usuario de Windows y
   escribe `config.php` en una carpeta local fuera del repo. Ningún valor se imprime ni pasa por
   un comando.
5. **Carpeta privada, contraseña y `config.php`, todo por la API** (cambio de plan del owner: sin
   hPanel). La API no escribe fuera de `public_html`, así que la carpeta es
   `public_html/visor-private`, cerrada con su propio `.htaccess`. El orden importa: primero ese
   `.htaccess` y la comprobación del 403; recién entonces los secretos.

   ```text
   1. visor-private/.htaccess
     ok  201/204    222 visor-private/.htaccess
     ok   https://ichisieben.dev/visor-private/.htaccess             403
   2. config.php
     ok  201/204    379 visor-private/config.php
     ok   https://ichisieben.dev/visor-private/config.php            403
   4. .htpasswd (user ichi, bcrypt $2y$) + visor-app/.htaccess
   ```

   La contraseña (20 caracteres al azar) se genera en memoria, se convierte en bcrypt con el
   `password_hash` de PHP y solo se muestra una vez al owner. La ruta de `AuthUserFile` sale del
   `root_directory` que devuelve la API, no de una suposición.
6. **Matriz final:**

   ```text
   / con contraseña                    200
   / sin contraseña                    401
   / contraseña equivocada             401
   visor-private/.htpasswd             403
   visor-private/config.php            403
   data.php con contraseña             200   (144 884 B, después de la primera subida firmada)
   ```
7. **Prueba firmada** con las claves leídas del registro: `build.py` → `web: ok 144884 B`;
   `bandeja.py --once` → `{"bandeja": "ok", ...}` y la página de la bandeja ya muestra la lista
   permitida (`enjambre-sandbox`, `stub-ok`).

**Lo que falló:**
- El manifest daba **401**: un `FilesMatch` con `Require all granted` dentro del `.htaccess` raíz
  no le gana a `Require valid-user` en LiteSpeed. Se movió a `pwa/` con su propio `.htaccess`
  (`Satisfy Any`), el mismo truco que ya usaba `api/`, y pasó a 200.
- La API de archivos no deja salir de `public_html` (`..` → 422).
- **La contraseña correcta daba 401.** Primero se descartó la caché del CDN (`?cb=` al azar, igual
  401). Después, usuarios de prueba con cinco formatos de hash: los cinco funcionaban, bcrypt
  incluido. El error era nuestro: en Windows, `subprocess` con `text=True` convierte el `\n` que
  se manda por stdin en `\r\n`, y se había hecho el hash de la contraseña más un `\r`. Arreglo:
  mandar bytes, sin salto de línea.
- **El token de subida dejó de valer antes de su `exp`** (403 en todo, también en un archivo
  inofensivo). Se pidió uno nuevo; conviene pedirlo justo antes de cada tanda.
- **Python decía «certificate has expired»** con un certificado emitido hoy. Pasa con todos los
  subdominios desde esta PC: el almacén de Windows hace que OpenSSL arme una cadena con un
  certificado vencido. Con el paquete de `certifi` verifica bien; se usa ese y la verificación
  sigue encendida.

## F) Pendiente del owner

Reiniciar `watch` y la prueba desde el celular: NEEDS-OWNER §19.
