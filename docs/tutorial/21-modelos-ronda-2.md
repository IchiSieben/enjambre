# 21 — Modelos locales, ronda 2: catálogo, banco de pruebas y elección automática

**Objetivo:** pasar de "cinco modelos elegidos a mano" a un catálogo que se pueda consultar,
un banco de pruebas que mida a todos con las mismas entradas y un gestor que elija solo el
modelo de cada tarea, explicando por qué.

**Por qué:** con 16 GB de memoria unificada, el límite no es el disco (la Mac tiene ~240 GB
libres) sino la memoria: Metal deja usar unos 12,7 GB. Un modelo puede brillar en su ficha y
no caber, o caber y equivocarse en español. La única forma honesta de elegir es **medir**:
calidad con respuesta conocida, velocidad, memoria pico y tiempo de carga. Y fijar **antes** el
umbral que separa "sirve" de "ninguno sirve".

Repo: `IchiSieben/modelos-mac`. Viene del tutorial 16 (gestor, cola, un modelo a la vez).

## Pasos

### 1. Buscar candidatos sin elegir de memoria

Dos agentes investigaron en paralelo (web, código de los runtimes, API de Hugging Face). Uno
cubrió texto, código, visión, imagen y edición. El otro cubrió transcripción, hablantes y
video. Después **un script volvió a verificar todo contra la API**, porque la prosa de un
agente no cuenta como fuente:

```
win$ python scripts/catalog_check.py
llm_alt           9.04 GB  apache-2.0             gated=False 2026-09-15  mlx-community/gemma-4-12B-it-qat-OptiQ-4bit
ministral14       8.46 GB  apache-2.0             gated=False 2025-12-03  mlx-community/Ministral-3-14B-Instruct-2512-4bit
gemma_coder       6.73 GB  apache-2.0             gated=False 2026-06-21  mlx-community/gemma-4-12b-coder-fable5-composer2.5-4bit
minicoder         3.00 GB  mit                    gated=False 2026-07-01  mlx-community/mini-coder-4b-OptiQ-4bit
paddleocr         0.72 GB  apache-2.0             gated=False 2026-08-12  mlx-community/PaddleOCR-VL-1.6-4bit
glmocr            1.25 GB  mit                    gated=False 2026-02-03  mlx-community/GLM-OCR-4bit
qwen_asr          2.47 GB  apache-2.0             gated=False 2026-01-29  mlx-community/Qwen3-ASR-1.7B-8bit
granite_asr       4.52 GB  apache-2.0             gated=False 2026-05-28  mlx-community/granite-speech-4.1-2b-nar-mlx
zimage            5.90 GB  apache-2.0             gated=False 2026-09-26  mflux-community/z-image-turbo-mflux-q4
diar_alt          0.03 GB  cc-by-4.0              gated=auto  2025-09-29  pyannote/speaker-diarization-community-1  <-- CHECK
ltx              59.69 GB  other                  gated=False 2026-08-15  dgrauet/ltx-2.3-mlx-q4  <-- CHECK
```

El script marca dos casos: pyannote, porque es *gated* y exige un token del dueño (por eso no
se baja), y LTX, por su licencia propia y porque el repo trae varias variantes que suman
59,7 GB. La tabla completa, con fuentes, está en `modelos-mac/docs/CATALOGO.md`.

**Hallazgo que cambia el punto 5 del encargo:** Nemotron **no acepta** número de hablantes.
La firma real en mlx-audio 0.5.7 es
`generate(audio, sample_rate=16000, threshold=0.5, min_duration=0.0, merge_gap=0.0, verbose=False)`.
El modelo tiene 8 salidas fijas y decide solo cuántas usa.

### 2. Bajar con piso de 100 GB y sha256

El piso subió de 60 a 100 GB en `server/models.toml` (`[budget] min_free_gb`). Simulacro primero:

```
mac$ python scripts/download.py --dry-run --only llm_alt ministral14 gemma_coder minicoder paddleocr glmocr qwen_asr granite_asr zimage diar_wespeaker
...
total 42.08 GB; free now 256.8 GB; after 214.7 GB
```

**El primer intento falló** después de bajar 9 GB:

```
KeyError: 'task'
EXIT=1
```

Las entradas nuevas usan `tasks = [...]` (un modelo puede servir a varias tareas: Gemma-4-12B
sirve para texto, código y visión), y `download.py` seguía leyendo `m["task"]`. Se arregló en
una línea (`m.get("task") or m.get("tasks")`) y se relanzó. Lo ya bajado no se volvió a
descargar: se verificó contra el sha256 de LFS en segundos (`ok llm_alt 9.04 GB in 4.3 s`).

```
ok ministral14 8.46 GB in 243.5 s
ok gemma_coder 6.73 GB in 218.8 s
ok minicoder 3.00 GB in 187.6 s
ok paddleocr 0.72 GB in 33.2 s
ok glmocr 1.25 GB in 36.8 s
ok qwen_asr 2.47 GB in 55.7 s
ok granite_asr 4.52 GB in 208.6 s
```

### 3. Una descarga en paralelo frena el respaldo (y se pausa)

Mientras bajaban los modelos, otro proceso copiaba el respaldo de fotos de la Mac a `F:\` de
Windows por SSH. En ese momento era la única copia de 222 GB. La velocidad del espejo cayó de
48 a **2 MB/s**. El disco externo seguía leyendo a ~30 MB/s, así que el cuello de botella era
la red. Se pausó la descarga de video (`kill -STOP <pid>`, se reanuda con `kill -CONT`):

```
win$ # 60 s con la descarga de LTX pausada
mirror 37 MB/s
```

**Lección:** en una red doméstica, un `snapshot_download` y un `ssh | tar` se reparten el
mismo enlace. Lo irremplazable va primero.

### 4. LTX: bajar solo lo que se usa

`ltx-2-mlx` llama a `snapshot_download(repo)` sin filtro. Para la ruta `--distilled` eso son
59,7 GB, cuando hacen falta 20,5. Se añadió `allow` (patrones de archivos) a `download.py`:

```
mac$ python scripts/download.py --dry-run --only ltx ltx_gemma
ltx          dgrauet/ltx-2.3-mlx-q4                            20.48 GB  commit 56a5866d63
ltx_gemma    mlx-community/gemma-3-12b-it-4bit                  8.07 GB  commit 86cc6a8ded
total 28.55 GB; free now 205.8 GB; after 177.2 GB
```

El primer `ltx-2-mlx info` alcanzó a dejar 3,5 GB en la caché de Hugging Face antes de
cortarse. Se anotó en NEEDS-OWNER; no se borró. Desde ahí la prueba corre con
`HF_HUB_OFFLINE=1` y rutas locales.

### 5. Referencia de transcripción: audio real, transcripción conocida

Kokoro (voz sintética) favorece el WER. Por eso se usó **FLEURS es_419** (Google, CC-BY-4.0):
personas reales leyendo frases con transcripción publicada. `scripts/prep_fleurs.py` toma las
primeras 40 frases distintas del *split* `dev` (orden por nombre de archivo, determinista).

**Falló la primera vez:**

```
urllib.error.ContentTooShortError: <urlopen error retrieval incomplete: got only 234975905 out of 255285841 bytes>
```

`urlretrieve` no reanuda. Se cambió por `curl --retry 5 -C -`:

```
40 reference clips, 473 s; long 1426.260000 s
```

### 6. Pruebas de humo (sin tiempo oficial: el espejo seguía cargando la Mac)

```
qa   ok  aciertos 2/2   36.5 tok/s
ctx  @2k: lectura 217 tok/s, generación 38.5 tok/s, primer token 16.2 s
     mismo prefijo reutilizado: primer token 0.24 s (tras un primer uso de 10.8 s)
     @8k: primer token 39.5 s → 0.25 s reutilizando el prefijo
```

**El registro completo de 5000 líneas no cabe.** Son unos 125 000 tokens. Con Qwen3.5-9B:

```
         12803904480  peak memory footprint
vm.swapusage: total = 10240.00M  used = 9707.25M
```

12,2 GB de *footprint* y 9,7 GB de swap en 5 minutos, sin terminar de leer el prompt. Se mató
por PID. La prueba oficial se reordenó: primero "recortado" y "trozos", con resultado guardado
a medias, y al final "completo", bajo un vigilante que mata el proceso si el swap crece más de
3 GB.

### 7. Arranque desatendido: la Mac espera sola

La corrida (unas 6 h) no puede depender de que esta ventana de Windows siga abierta. Por eso
`scripts/sesion_g_start.sh` corre dentro de un tmux de la Mac y hace tres cosas: espera a que
desaparezca el `tar` que alimenta el espejo, reanuda la descarga pausada (`kill -CONT`) y lanza
`sesion_g_run.sh`. El fin del espejo se detecta **desde la Mac**: el emisor es el `tar` local.

**Dos tropiezos reales al lanzarlo:**

```
mac$ tmux has-session -t sesion-g && echo EXISTS
EXISTS                      # ¡falso! existía "sesion-g-dl2"
```

`-t` acepta un **prefijo**. Para pedir el nombre exacto se usa `-t =sesion-g`, pero en zsh:

```
zsh:1: sesion-g not found
```

En zsh, `=palabra` se expande a la ruta del comando `palabra`. Hay que ponerlo entre comillas:

```
mac$ tmux has-session -t '=sesion-g' || tmux new -d -s sesion-g 'bash scripts/sesion_g_start.sh'
mac$ tmux ls
sesion-g: 1 windows (created Mon Oct  5 02:28:45 2026)
mac$ cat ~/modelos/logs/sesion-g-start.log
2026-10-05 02:28:45 start; waiting for mirror sender: 35851
```

Ver el avance desde Windows, con un solo comando:

```
win$ ssh mac 'tail -n 25 ~/modelos/logs/sesion-g-start.log ~/modelos/logs/sesion-g-run.log'
```

### 8. "La Mac está caída" (no lo estaba) y el pánico que sí ocurrió

Dos días después, el vigilante de Windows llevaba horas avisando `mac: down` y `ssh mac`
daba timeout. Antes de culpar a la Mac:

```
win$ ping -n 1 <mac>.local
Reply from fe80::…%4: time=129ms          # por IPv6 responde
win$ ping -4 -n 1 <mac>.local
Request timed out.                          # por IPv4, no
win$ route print -4 | grep "<red-casa>"
      <red-casa>    255.255.255.0         On-link     <ip-windows>    281
      <red-casa>    255.255.255.0         On-link     <ip-vpn>      1
```

La segunda ruta es de la VPN corporativa (adaptador "PANGP"): reclama la red de casa con métrica
1, así que todo el IPv4 hacia la Mac se mete al túnel y muere ahí. La dirección IPv6
*link-local* (`fe80::`) no pasa por la VPN. Sin tocar la VPN, un alias de SSH para esos días:

```
Host mac6
  HostName fe80::<id>%%4          # %% porque ssh_config usa % para sus variables
  HostKeyAlias <ip-de-la-mac>     # verifica la MISMA llave de host que ya conocíamos
  User <usuario>
  IdentityFile ~/.ssh/id_mac
```

`HostKeyAlias` importa: sin él, ssh no reconoce la dirección nueva y se niega a entrar
(`Host key verification failed`). Lo tentador sería desactivar la verificación; lo correcto
es decirle qué llave conocida debe esperar.

Ya dentro, la Mac llevaba **1 día y 21 horas encendida**. La corrida no murió por un apagado:

```
mac$ last reboot | head -1
reboot time                                Mon Oct  5 03:22
mac$ ls /Library/Logs/DiagnosticReports/ | grep panic
panic-full-2026-10-05-032324.0002.panic
  "panicString" : "panic(…): watchdog timeout: no checkins from watchdogd in 92 seconds"
```

Un *watchdog panic*: el sistema estuvo tan ahogado que el proceso que avisa "sigo vivo" no
pudo correr en 92 s, y el kernel reinició la máquina.

### 9. El vigilante que mataba al proceso equivocado

El log decía que casi todos los modelos habían muerto por "swap +3 GB" a los 10–40 s. Pero el
**disco** contaba otra historia:

```
mac$ grep llm_alt:qa ~/modelos/logs/sesion-g-run.log
llm_alt:qa   killed swap +3792 MB …  wall=10.1 s           # muerto hacia las 02:56
mac$ stat -f "%Sm %N" -t "%H:%M:%S" bench-runs/2026-10-05/llm_alt-qa.json
03:08:54 llm_alt-qa.json                                   # …pero escribió 12 min después
```

Un proceso "matado" no escribe su resultado doce minutos más tarde. La causa: el banco lanza
cada modelo como `/usr/bin/time -l python bench_child.py …` para medir la memoria pico, y el
vigilante hacía `p.kill()` sobre **`time`**. `time` muere, pero no reenvía la señal: el hijo
con el modelo queda huérfano (lo adopta `launchd`) y sigue trabajando. El banco esperaba 10 s y
cargaba **el siguiente modelo encima**. A las 03:19 había varios modelos de 9–14 B en 16 GB →
pánico.

Al relanzar con el swap en cero, el patrón se repitió en minutos (22,8 GB de swap, 4 % de
memoria libre) y paró en cuanto se mataron los huérfanos. El arreglo es de dos líneas: poner al
hijo en **su propio grupo de procesos** y matar el grupo entero.

```python
p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
...
os.killpg(p.pid, signal.SIGKILL)      # antes: p.kill()  → solo moría `time`
```

Prueba de que funciona, antes de volver a gastar horas de banco:

```
mac$ python scripts/run_watched.py --label t2 --out /tmp/t2.jsonl --timeout 3 -- sleep 97
{"label": "t2", "rc": -9, "wall_s": 4.1, "killed": "timeout", …}
mac$ pgrep -fl "sleep 97"
                                      # vacío: no queda nadie
```

**Lección:** un vigilante que no verifica que su víctima murió es peor que no tener vigilante,
porque los números dicen "controlado" mientras la máquina se llena. Y los resultados de una
corrida así no valen: todos los "killed" de esa noche se volvieron a medir.

### 10. Reanudar solo lo que falta (y desconfiar también de los "ok")

`sesion_g_run.sh` no se puede reanudar: relanzarlo repite lo hecho y vuelve a entrar al paso del
pánico. Por eso se escribieron dos scripts nuevos que usan el mismo banco, el mismo vigilante y
el mismo log, y solo corren los pares sin resultado válido:

```
mac$ tmux new -d -s sesion-g "bash scripts/sesion_g2_run.sh"
mac$ tmux new -d -s sesion-g "bash ~/.enjambre/busy.sh run bench sesion-g2b -- bash scripts/sesion_g2b_run.sh"
```

El segundo va envuelto en `busy.sh`: mientras corre, la Mac publica `~/.enjambre/busy.json` y el
enrutador de enjambre no le manda renders con GPU ni trabajos de modelos.

**Un "ok" también puede estar contaminado.** Los pares que pasaron el 05-10 corrieron mientras
los huérfanos de los anteriores seguían vivos. Medidos otra vez con la Mac limpia:

```
05-10  llm:qa      ok  speed=20.3 tok/s  …
07-10  llm:qa      ok  speed=37.2 tok/s  …                    # casi el doble
05-10  vlm:vision  ok  speed=3.87 s/imagen  load=473.27 s
07-10  vlm:vision  ok  speed=1.86 s/imagen  load=0.81 s
```

La calidad era la misma, pero la velocidad estaba a la mitad. Si se hubieran publicado los números
del 05-10, el catálogo habría elegido con datos falsos.

**Dos tropiezos más, de los baratos:**

- La URL del clip de prueba (*Big Buck Bunny*) ahora da 404. El archivo sigue en el sitio de
  Blender, pero solo como `.zip`:
  ```
  curl: (56) The requested URL returned error: 404
  ```
  El script ahora baja el `.zip` y extrae el mp4.
- El filtro `coreimage` de ffmpeg no acepta el nombre a secas:
  ```
  [coreimage @ …] Parsing of filters failed.
  ```
  Necesita `filter=CIComicEffect@default`.

Y uno que salvó datos: la prueba de contexto largo solo guardaba al final. Cuando Metal se quedó
sin memoria en 32k, perdía también los resultados de 2k y 8k. Ahora guarda después de cada
tamaño, y en la segunda pasada `llm_alt` dejó sus filas de 2k y 8k antes del error:

```
RuntimeError: [METAL] Command buffer execution failed: Insufficient Memory
```

### 11. Qué salió: la tabla y el catálogo

El banco escribe una fila por modelo y tarea en `measurements.jsonl`, y
`scripts/banco_table.py` la convierte en las tablas de `modelos-mac/docs/BANCO.md`. Resumen:

| tarea | elige | calidad | por qué |
|---|---|---|---|
| texto | `llm_alt` | 0,967 | el que más acierta (`llm` acierta 0,867, pero va al doble de velocidad) |
| código | `llm_alt` | 0,75 | el único sobre el umbral de 0,50; el "especializado en código" sacó 0,33 |
| visión | `vlm` | 1,0 | `glmocr` acierta 0,625, pero es 4× más rápido para OCR |
| transcripción | `stt` | 0,969 | `qwen_asr` empata, pero ocupa 13,7 GB |
| imagen | — | 1,0 | "ninguno sirve": las tres ocupan más de 12 GB (funcionan, pero no caben) |

El gestor lee esas filas en vivo. Sin reiniciarlo:

```
mac$ curl -s 127.0.0.1:8765/catalog
"text": {"model": "llm_alt", "reason": "medido el 2026-10-07: aciertos QA 0.967, 18.8 tok/s gen,
         9820.7 MB; mejor de 3 que caben en 12000 MB"}
```

Antes decía `predeterminado de models.toml` en todas las tareas.

**Cascada local → Claude** (`scripts/bench_cascade.py`, desde Windows por túnel):

```
local    ok=38/42  1102.2 s  US$ 0       declined=0
claude   ok=42/42    92.2 s  US$ 0.2692
cascade  ok=38/42  1102.2 s  US$ 0       sent_to_claude=0
calibration: accepted_and_wrong=4
```

El modelo local nunca dijo `NO_PUEDO`, así que la cascada no escaló nada. La autoconfianza no
sirve como señal. Para el código, la señal útil es que fallen los tests.

**Contexto largo:** con 32 000 tokens, `llm` tarda 162 s en dar la primera respuesta porque tiene
que leer todo el documento. Si el documento se procesa una vez y su caché se reutiliza, las
preguntas siguientes tardan 0,34 s. Para preguntar varias cosas sobre un mismo texto largo, esa
es la palanca.

### 12. Hablantes, video y cuánta memoria queda

**Hablantes** (56 min de audio real, cuatro trabajos del gestor, uno tras otro):

| trabajo | tiempo | huella del proceso |
|---|---|---|
| transcripción | 138 s | 5,3 GB |
| Nemotron | 13 s | 2,2 GB |
| Sherpa | 496 s | 0,9 GB |
| WeSpeaker | 415 s | 0,7 GB |

Sherpa y WeSpeaker discrepan solo el 7 % del tiempo, y Nemotron discrepa de ambos un 35 %. Cuál
acierta no se decide con números sino escuchando: la hoja de revisión marca los 5 minutos que
hay que oír, y esa decisión es del owner.

**Video:** el mismo efecto cómic tarda 13,4 s en CPU (`edgedetect`) y 1,07 s en GPU (Core Image).
Es 12 veces más rápido sin cambiar de máquina. En cambio, el texto a video con LTX no cabe: el
vigilante mató el paso más pequeño a los 20 s.

**Memoria para Blender**, medida con `memory_pressure` mientras corría un trabajo de texto:

```
03:05:04 idle     free_pct=87%
03:05:13 running  free_pct=26%      # llm_alt cargado: huella 9,8 GB
03:06:02 after    free_pct=86%
```

El gestor no deja ningún modelo residente: cada trabajo es un proceso que termina. Mientras corre
un trabajo de texto quedan unos 4 GB, así que Blender con Metal solo entra si la escena es pequeña.
Si no, `--device CPU` o esperar a que `busy.json` desaparezca.

**Cierre.** Lo que no se pudo medir desatendido queda anotado con su comando en NEEDS-OWNER §13:
`ministral14` con contexto largo, LTX con la Mac vacía y la elección de diarizador.
