---
lesson:
  route: build-stack
  order: 15
  level: 2
lang: en
---

# 16 — Local models on the Mac: choose, download, serve

## Goal

Five local models (transcription, speakers, text/code, vision, image) on the 16 GB Mac,
downloaded with verified sha256, served one at a time by a manager that Windows uses through an
SSH tunnel.

## Why

On the Mac, memory is **unified**: whatever the GPU reserves, everything else loses. With 16 GB
two large models do not fit at once, so the design revolves around one rule: **one heavy model at
a time, unloaded when done**. The safest way to unload a model is for the process that loaded it
to exit.

Repo: `IchiSieben/modelos-mac` (private). Weights outside git, in `~/modelos` on the Mac.

## Steps

### 1. Measure the disk before downloading anything

```
mac$ diskutil info / | grep "Container Free"
   Container Free Space:      105.2 GB
```

Rule: after downloading everything, 60 GB must remain free → there are **45.2 GB** for everything
new (models + Python + environment + ffmpeg). Full inventory and cleanup recommendations (nothing
was deleted): `modelos-mac/docs/DISCO.md`.

### 2. Tools (no sudo)

```
mac$ brew install uv ffmpeg python@<versión>
mac$ uv --version; ffmpeg -version | head -1; python3.12 --version
uv 0.12.23 (Homebrew 2026-10-03 aarch64-apple-darwin)
ffmpeg version 9.0.2
Python 3.12.15
```

It cost **0.32 GB**. The system Python (3.9.6) is no use: `mlx-lm` requires ≥ 3.11.

### 3. Choose models from today's documentation, not from memory

The Hugging Face API (`/api/models/<repo>?blobs=true`) was read for size, license and whether the
model is *gated*. One per task, the most permissively licensed one that fits:

| Task | Model | Disk | License |
|---|---|---|---|
| Transcription | `mlx-community/whisper-large-v3-turbo` | 1.61 GB | MIT |
| Speakers | `mlx-community/Nemotron-3-Diarization` (default) · sherpa-onnx: pyannote seg-3.0 + 3D-Speaker (alternative) | 0.20 · 0.05 GB | OpenMDW · MIT + Apache |
| Text/code | `mlx-community/Qwen3.5-9B-MLX-4bit` | 5.98 GB | Apache-2.0 |
| Vision | `mlx-community/Qwen3-VL-8B-Instruct-4bit` | 5.78 GB | Apache-2.0 |
| Image | `mflux-community/flux2-klein-4b-mflux-q4` | 4.62 GB | Apache-2.0 |

Full proposal (2 candidates per task, why one and not the other):
`modelos-mac/docs/MODELOS.md`.

### 4. A single environment, outside the repo

```
mac$ export UV_CACHE_DIR=~/modelos/uv-cache UV_PROJECT_ENVIRONMENT=~/modelos/envs/main
mac$ uv sync --python 3.12 --extra mlx --extra dev
```

Everything resolved in a single environment: mlx 0.32.3, mlx-lm 0.32.0, mlx-vlm 0.7.4,
mlx-whisper 0.4.3, mlx-audio 0.5.7, mflux 0.21.0. MLX reports `max_recommended_working_set_size`
= **12.7 GB**: the practical GPU ceiling on this Mac.

### 5. Download with a manifest and sha256

```
mac$ python scripts/download.py --dry-run
total 18.19 GB; free now 103.3 GB; after 85.2 GB
mac$ tmux new -d -s md-dl "python scripts/download.py > ~/modelos/logs/download.log 2>&1"
ok stt 1.61 GB in 35.4 s
ok llm 5.98 GB in 134.2 s
ok vlm 5.78 GB in 105.3 s
ok img 4.62 GB in 134.3 s
```

`HF_HOME=~/modelos/hf`: without it Hugging Face keeps one copy in `~/.cache/huggingface` and
another in the destination folder. Each large file is compared against the LFS sha256 reported by
the API; the manifest ends up in `~/modelos/MANIFEST.json` (a copy without personal paths in the
repo). After everything (models + tools + environment): **85.0 GB free**, that is, 20.2 GB used.

### 6. The manager

```
mac$ tmux new -d -s md-server "python -m server.app"   # así se probó; hoy lo arranca un LaunchAgent (17 §8)
mac$ lsof -nP -iTCP:8765 -sTCP:LISTEN
Python  3730 <usuario>  7u  IPv4  TCP 127.0.0.1:8765 (LISTEN)
```

Only `127.0.0.1`: the API has no authentication, its protection is not listening on the network.
From Windows it is reached through a tunnel, and the client opens it by itself:

```
PS> python client.py run image --prompt "A red bicycle ... Cusco" --params '{"steps":4,"width":768,"height":768}' --out bici.png
PS> python client.py run vision --file bici.png --prompt "Describe la imagen..."
"La imagen muestra una bicicleta roja apoyada contra una pared de piedra ... La bicicleta es roja y la puerta es azul."
```

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

Architecture (why a child process per job, why SQLite, why a tunnel):
`modelos-mac/docs/ARQUITECTURA.md`. Contract: `modelos-mac/docs/SPEC.md`.

### 7. From `enjambre`

```
PS> python enjambre.py run --node mac --repo enjambre-sandbox --task TASK-local-smoke.md --engine local
{"id": "local-smoke", "engine": "local", "exit": 0, "duration_s": 15.99, "cost_usd": 0.0,
 "load_s": 2.47, "peak_footprint_mb": 5953.3, "output": "state/local/local-smoke.md"}
```

The local engine returns text, not commits: the answer lands in `state/local/<id>.md` and in the
*ledger* with `engine: "local"`.

## What actually went wrong

1. **RSS lies on unified memory.** The first job gave: RSS 1.43 GB, MLX peak 2.36 GB, macOS *peak
   memory footprint* 3.0 GB. GPU pages do not count toward RSS. The manager stores all three; the
   headline is the footprint (`/usr/bin/time -l`).
2. **And it was also measuring the wrong process**: by wrapping the child with `/usr/bin/time`,
   the sampled RSS was that of `time` (1 MB). Fix: add up the process and its children.
3. **`mlx_whisper.transcribe` is both a module and a function** (the package re-exports the
   function under the same name). `from mlx_whisper import transcribe` gets the function;
   for the module: `importlib.import_module("mlx_whisper.transcribe")`.
4. **`tmux-run.sh` hung**: it reads stdin until EOF and over SSH that never comes. Fix:
   `< /dev/null`.
5. **The Mac rebooted halfway through the download** (FileVault screen over SSH: "This system is
   locked"). No password is typed: you wait for the owner. The download resumed by itself
   (`snapshot_download` picks up what was already downloaded).
6. **sherpa-onnx compiled but would not load**: `libonnxruntime.dylib` is missing, which ships in
   the sibling package `sherpa-onnx-core` (it does not install itself).
7. **mlx-audio guesses the model type from the folder name**: Kokoro in `~/modelos/tts`
   failed ("Model type tts not supported"); in `~/modelos/kokoro` it works.

## How to verify

```
PS> python client.py status          # lista de trabajos con tiempos y memoria
mac$ curl -s 127.0.0.1:8765/health   # {"running": null, "queued": 0, "free_gb": ...}
mac$ python -m pytest -q             # 15 passed
```

## Self-check

1. Why does the manager launch a process per job instead of keeping the model loaded?
2. Which memory counter would you use to decide whether two models fit at once, and why not RSS?
3. What happens if someone changes `HOST` to `0.0.0.0`? What would have to exist first?
