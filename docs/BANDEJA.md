# Bandeja de tareas — diseño (sesión F2)

> Estado: **construido y probado en local** (PHP 8.3 + `php -S` + Mac real). En Hostinger
> **no hay nada** hasta que el owner dé el OK de cada paso (NEEDS-OWNER §19).

## 1. En una línea

Desde el celular dejas un **contrato de enjambre** (repo de una lista cerrada, kind, título,
objetivo, archivos que puede tocar, criterio de hecho) en `visor.ichisieben.dev/bandeja/`.
Tu PC lo **recoge** en cada ronda de `watch`, lo valida otra vez y lo **encola**; la Mac lo
corre como cualquier otro trabajo; la PC devuelve el estado a la bandeja.

```
 celular ──(contraseña)──▶ Hostinger: bandeja/index.php ──▶ visor-private/tareas/<id>.json (firmada)
                                                                  ▲
 PC (watch, cada 30 s; bandeja cada 2 min) ──GET firmado──────────┘  nada entra a casa
   └─ verifica firma, lista cerrada, confidencial/reservado, límite diario, duplicado
   └─ escribe state/bandeja/TASK-<id>.md  ──▶  cola de enjambre  ──▶  Mac (worker.sh)
   └─ POST firmado "recibida" / "corriendo" / "hecha" / "fallida" ──▶ visor-private/estado/<id>.json
```

## 2. Por qué tirar (pull) y no empujar (push)

Hostinger no puede llamar a tu casa (y no queremos que pueda): no hay puertos abiertos ni
túneles. La PC **pregunta** cada pocos minutos. El precio es la latencia (hasta 2 min de espera
para que la tarea se vea "recibida"); la ganancia es que comprometer el hosting no da un camino
hacia la red de casa. Ficha: [pull vs push](conceptos/pull-vs-push.md).

## 3. El contrato (lo único que la bandeja acepta)

| Campo | Regla (PHP **y** PC) |
|---|---|
| `repo` | Igual, carácter por carácter, a uno de `[bandeja].repos` en `config/visor-web.toml`. Sin `/`, `\`, `..`. Nada que contenga `confidencial`/`reservado`. |
| `kind` | De `[bandeja].kinds` **y** existente en `routing.toml`. Por defecto `claude`, `tests`. `gui` nunca (correría en Windows). |
| `worker` | De `[bandeja].workers`: `claude` y `stub-ok` (prueba a $0). |
| `titulo` | 3–100 caracteres, una línea. |
| `objetivo` | Markdown, hasta 8 000 caracteres. **Se escribe en un archivo, nunca pasa por un shell.** |
| `archivos` | 1–20 rutas relativas (`[\w.\-/*]`), sin `..`, sin `/` inicial, sin `confidencial`/`reservado`. Van a `## Files you may touch`. |
| `criterio` | 3–2 000 caracteres. Va a `## Done criteria`. |

La PC arma `TASK-<id>.md` con esas secciones (las que `enjambre.validate` exige) y encola con
`presence.enqueue` + `--max-turns` de la configuración. Ningún valor del formulario llega a una
línea de comandos: el único argv es `enjambre.py run --repo <de la lista> --task <ruta generada>`.

## 4. Firmas (HMAC-SHA256)

Dos claves, una por propósito:

| Variable (Windows, de usuario) | Archivo en Hostinger (fuera del docroot) | Para |
|---|---|---|
| `ENJAMBRE_VISOR_INGEST_KEY` | `visor-private/config.php` → `ingest_key` | Subir la instantánea del visor |
| `ENJAMBRE_BANDEJA_KEY` | `visor-private/config.php` → `bandeja_key` | Firmar tareas, el GET de la PC y los estados |

Formato único: `hex(HMAC(clave, propósito + "\n" + ts + "\n" + cuerpo))`, propósitos
`ingest`, `task`, `poll`, `estado`, `allow`. El propósito impide reusar una firma de un endpoint
en otro. Cabeceras `X-Ts` y `X-Firma`; el PHP rechaza `|ts − ahora| > 300 s` y, en las
escrituras, un `ts` que no sea mayor que el último aceptado (anti-repetición).

La tarea se firma **sobre los bytes guardados**: el JSON viaja como texto dentro de
`{"tarea": "<json>", "firma": "<hex>"}` y la PC verifica antes de parsear. Nunca se re-serializa
para verificar.

**Qué prueba la firma y qué no:** prueba que la tarea la escribió el PHP con la clave (y no
alguien que solo pudo escribir en la carpeta, ni un intermediario). **No** prueba que la escribiste
tú: quien tenga tu contraseña de la bandeja produce tareas con firma válida. Por eso el límite
diario, la lista cerrada, el duplicado y confidencial/reservado se aplican **en la PC**; en el PHP son una
comodidad (respuesta inmediata en el celular), no la defensa.

## 5. Permisos del trabajo

- Mismo `worker.sh` de siempre: `--allowedTools` acotado
  (`Read,Edit,Write,Glob,Grep,Bash(git status:*),Bash(git diff:*),Bash(ls:*)`), en un worktree
  y rama `mac/<id>`. **Sin** `--dangerously-skip-permissions`.
- `--max-turns` = `[bandeja].max_turns` (por defecto 8), por el knob `ENJAMBRE_MAX_TURNS`.
- Un trabajo por tarea, **sin reintentos**: la cola de enjambre no reencola un trabajo fallido
  (`reap` solo mueve `running/ → done/`) y la bandeja marca la tarea como vista antes de encolar.
- Límite diario `[bandeja].daily_limit` (por defecto 5) contado en la PC por día de Lima.

## 6. Estados

| Estado | Cuándo | Lo que ve el celular |
|---|---|---|
| `nueva` | El PHP la guardó | "esperando a la PC" |
| `recibida` | La PC la validó y la encoló | id del trabajo, nodo previsto |
| `rechazada` | Falló una regla en la PC | motivo (sin eco del contenido) |
| `corriendo` | Está en `state/running/` | nodo |
| `hecha` / `fallida` | `state/done/` + línea del ledger | rama, si se subió, coste, resumen de 500 caracteres **pasado por el mismo filtro de fugas del visor web** |

Si la Mac está caída u ocupada, la tarea se queda `recibida` (en la cola) hasta que `watch`
la despache: la cola ya lo maneja.

## 7. Lado Hostinger

```
domains/ichisieben.dev/public_html/visor-app/      (docroot de visor.ichisieben.dev)
  .htaccess            Basic auth para todo menos api/, noindex, deny a *.inc.php
  index.html           el visor (la misma plantilla, lee data.php)
  data.php             exige usuario autenticado; devuelve la instantánea
  bandeja/index.php    formulario + últimas 20 tareas; exige usuario; CSRF
  api/.htaccess        sin Basic auth (la máquina se autentica con HMAC)
  api/ingest.php       POST firmado (ingest) → visor-private/snapshot.json
  api/tareas.php       GET firmado (poll) → tareas nuevas; POST firmado (estado|allow)
  lib.inc.php          config, firma, respuestas; nunca se sirve
  robots.txt           Disallow: /
domains/ichisieben.dev/visor-private/              (FUERA del docroot)
  config.php           claves y límite: lo crea el owner, nadie más lo ve
  .htpasswd            lo crea hPanel (Directorios protegidos) o el owner
  snapshot.json  allow.json  tareas/  estado/  last-ts/
```

**Falla cerrado**: `data.php` y `bandeja/` responden 401 si el servidor no les pasa usuario
(si el `.htaccess` no se aplicara, no se abren solos). `api/*` responde 401 sin firma válida y
503 si `config.php` no existe.

## 8. Lo que tiene que hacer el owner (NEEDS-OWNER §19)

1. OK para crear el subdominio `visor` → `public_html/visor-app`.
2. Crear la contraseña (hPanel → Directorios protegidos), sin pasarla por el chat.
3. Generar las dos claves en Windows, ponerlas como variables de usuario y en `config.php`.
4. OK para la primera subida de archivos y para activar `upload = true` y `enabled = true`.
5. Reiniciar `watch` (es su proceso) para que la ronda incluya la bandeja.
