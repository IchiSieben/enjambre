---
lesson:
  route: build-stack
  order: 15
  level: 2
---

# 16 — Modelos locales en la Mac: elegir, bajar, servir

## Objetivo

Cinco modelos locales (transcripción, hablantes, texto/código, visión, imagen)
en la Mac de 16 GB, bajados con sha256 verificado, servidos de a uno por un gestor que Windows
usa por túnel SSH.

## Por qué

En la Mac la memoria es **unificada**: lo que la GPU reserva lo pierde todo lo demás.
Con 16 GB no caben dos modelos grandes a la vez, así que el diseño gira en torno a una regla:
**un modelo pesado a la vez, y se descarga al terminar**. La forma más segura de descargar un
modelo es que el proceso que lo cargó termine.

Repo: `IchiSieben/modelos-mac` (privado). Pesos fuera de git, en `~/modelos` de la Mac.

## Pasos

### 1. Medir el disco antes de bajar nada

```
mac$ diskutil info / | grep "Container Free"
   Container Free Space:      105.2 GB
```

Regla: tras bajar todo deben quedar 60 GB libres → hay **45,2 GB** para todo lo nuevo
(modelos + Python + entorno + ffmpeg). Inventario completo y recomendaciones de limpieza (no se
borró nada): `modelos-mac/docs/DISCO.md`.

### 2. Herramientas (sin sudo)

```
mac$ brew install uv ffmpeg python@<versión>
mac$ uv --version; ffmpeg -version | head -1; python3.12 --version
uv 0.12.23 (Homebrew 2026-10-03 aarch64-apple-darwin)
ffmpeg version 9.0.2
Python 3.12.15
```

Costó **0,32 GB**. El Python del sistema (3.9.6) no sirve: `mlx-lm` exige ≥ 3.11.

### 3. Elegir modelos con la documentación de hoy, no de memoria

Se leyó la API de Hugging Face (`/api/models/<repo>?blobs=true`) para tamaño, licencia y si está
*gated*. Uno por tarea, el de licencia más permisiva que cabe:

| Tarea | Modelo | Disco | Licencia |
|---|---|---|---|
| Transcripción | `mlx-community/whisper-large-v3-turbo` | 1,61 GB | MIT |
| Hablantes | `mlx-community/Nemotron-3-Diarization` (por defecto) · sherpa-onnx: pyannote seg-3.0 + 3D-Speaker (alternativa) | 0,20 · 0,05 GB | OpenMDW · MIT + Apache |
| Texto/código | `mlx-community/Qwen3.5-9B-MLX-4bit` | 5,98 GB | Apache-2.0 |
| Visión | `mlx-community/Qwen3-VL-8B-Instruct-4bit` | 5,78 GB | Apache-2.0 |
| Imagen | `mflux-community/flux2-klein-4b-mflux-q4` | 4,62 GB | Apache-2.0 |

Propuesta completa (2 candidatos por tarea, por qué uno y no el otro):
`modelos-mac/docs/MODELOS.md`.

### 4. Un entorno, fuera del repo

```
mac$ export UV_CACHE_DIR=~/modelos/uv-cache UV_PROJECT_ENVIRONMENT=~/modelos/envs/main
mac$ uv sync --python 3.12 --extra mlx --extra dev
```

Todo resolvió en un solo entorno: mlx 0.32.3, mlx-lm 0.32.0, mlx-vlm 0.7.4, mlx-whisper 0.4.3,
mlx-audio 0.5.7, mflux 0.21.0. MLX reporta `max_recommended_working_set_size` = **12,7 GB**: es
el techo práctico de la GPU en esta Mac.

### 5. Bajar con manifiesto y sha256

```
mac$ python scripts/download.py --dry-run
total 18.19 GB; free now 103.3 GB; after 85.2 GB
mac$ tmux new -d -s md-dl "python scripts/download.py > ~/modelos/logs/download.log 2>&1"
ok stt 1.61 GB in 35.4 s
ok llm 5.98 GB in 134.2 s
ok vlm 5.78 GB in 105.3 s
ok img 4.62 GB in 134.3 s
```

`HF_HOME=~/modelos/hf`: sin eso Hugging Face guarda una copia en `~/.cache/huggingface` y otra
en la carpeta de destino. Cada archivo grande se compara con el sha256 LFS que reporta la API;
el manifiesto queda en `~/modelos/MANIFEST.json` (copia sin rutas personales en el repo).
Tras todo (modelos + herramientas + entorno): **85,0 GB libres**, o sea 20,2 GB usados.

### 6. El gestor

```
mac$ tmux new -d -s md-server "python -m server.app"   # así se probó; hoy lo arranca un LaunchAgent (17 §8)
mac$ lsof -nP -iTCP:8765 -sTCP:LISTEN
Python  3730 <usuario>  7u  IPv4  TCP 127.0.0.1:8765 (LISTEN)
```

Solo `127.0.0.1`: la API no tiene autenticación, su protección es no escuchar en la red. Desde
Windows se llega por túnel, y el cliente lo abre solo:

```
PS> python client.py run image --prompt "A red bicycle ... Cusco" --params '{"steps":4,"width":768,"height":768}' --out bici.png
PS> python client.py run vision --file bici.png --prompt "Describe la imagen..."
"La imagen muestra una bicicleta roja apoyada contra una pared de piedra ... La bicicleta es roja y la puerta es azul."
```

*(captura del entorno real: no se publica; ver docs/CAPTURAS.md para las del modo demo)*

Arquitectura (por qué un proceso hijo por trabajo, por qué SQLite, por qué túnel):
`modelos-mac/docs/ARQUITECTURA.md`. Contrato: `modelos-mac/docs/SPEC.md`.

### 7. Desde `enjambre`

```
PS> python enjambre.py run --node mac --repo enjambre-sandbox --task TASK-local-smoke.md --engine local
{"id": "local-smoke", "engine": "local", "exit": 0, "duration_s": 15.99, "cost_usd": 0.0,
 "load_s": 2.47, "peak_footprint_mb": 5953.3, "output": "state/local/local-smoke.md"}
```

El motor local devuelve texto, no commits: la respuesta queda en `state/local/<id>.md` y en el
*ledger* con `engine: "local"`.

## Qué salió mal de verdad

1. **El RSS miente en memoria unificada.** El primer trabajo dio: RSS 1,43 GB, pico de MLX
   2,36 GB, *peak memory footprint* de macOS 3,0 GB. Las páginas de la GPU no cuentan en RSS. El
   gestor guarda los tres; el titular es el footprint (`/usr/bin/time -l`).
2. **Y además medía el proceso equivocado**: al envolver el hijo con `/usr/bin/time`, el RSS
   muestreado era el de `time` (1 MB). Arreglo: sumar el proceso y sus hijos.
3. **`mlx_whisper.transcribe` es a la vez un módulo y una función** (el paquete reexporta la
   función con el mismo nombre). `from mlx_whisper import transcribe` trae la función;
   para el módulo: `importlib.import_module("mlx_whisper.transcribe")`.
4. **`tmux-run.sh` se colgaba**: lee stdin hasta EOF y por SSH no llega nunca. Arreglo:
   `< /dev/null`.
5. **La Mac se reinició a mitad de la descarga** (pantalla de FileVault por SSH: "This system is
   locked"). No se teclea contraseña: se espera al dueño. La descarga reanudó sola
   (`snapshot_download` retoma lo bajado).
6. **sherpa-onnx compilaba pero no cargaba**: falta `libonnxruntime.dylib`, que viene en el
   paquete hermano `sherpa-onnx-core` (no se instala solo).
7. **mlx-audio adivina el tipo de modelo por el nombre de la carpeta**: Kokoro en `~/modelos/tts`
   fallaba ("Model type tts not supported"); en `~/modelos/kokoro` funciona.

## Cómo verificar

```
PS> python client.py status          # lista de trabajos con tiempos y memoria
mac$ curl -s 127.0.0.1:8765/health   # {"running": null, "queued": 0, "free_gb": ...}
mac$ python -m pytest -q             # 15 passed
```

## Autoevaluación

1. ¿Por qué el gestor lanza un proceso por trabajo en vez de mantener el modelo cargado?
2. ¿Qué contador de memoria usarías para decidir si dos modelos caben a la vez, y por qué no RSS?
3. ¿Qué pasa si alguien cambia `HOST` a `0.0.0.0`? ¿Qué tendría que existir antes?
