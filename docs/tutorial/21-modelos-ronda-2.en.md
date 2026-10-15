---
lesson:
  route: build-stack
  order: 20
  level: 2
lang: en
---

# 21 — Local models, round 2: catalog, benchmark and automatic selection

## Goal

Go from "five hand-picked models" to a catalog you can query, a benchmark that measures every
model on the same inputs, and a manager that picks the right model for each task on its own,
explaining why.

## Why

With 16 GB of unified memory, the limit is not disk (the Mac has ~240 GB free) but memory: Metal
lets you use about 12.7 GB. A model can shine on its model card and not fit, or fit and get
Spanish wrong. The only honest way to choose is to **measure**: quality against a known answer,
speed, peak memory and load time. And to set **beforehand** the threshold that separates "good
enough" from "none is good enough".

Repo: `IchiSieben/modelos-mac`. It follows from tutorial 16 (manager, queue, one model at a time).

## Steps

### 1. Find candidates without choosing from memory

Two agents researched in parallel (web, runtime source code, Hugging Face API). One covered text,
code, vision, image and editing. The other covered transcription, speakers and video. Then **a
script re-checked everything against the API**, because an agent's prose does not count as a
source:

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

The script flags two cases: pyannote, because it is *gated* and requires a token from the owner
(so it is not downloaded), and LTX, because of its own license and because the repo ships several
variants that add up to 59.7 GB. The full table, with sources, is in
`modelos-mac/docs/CATALOGO.md`.

**A finding that changes point 5 of the brief:** Nemotron does **not** accept a number of
speakers. The real signature in mlx-audio 0.5.7 is
`generate(audio, sample_rate=16000, threshold=0.5, min_duration=0.0, merge_gap=0.0, verbose=False)`.
The model has 8 fixed outputs and decides on its own how many to use.

### 2. Download with a 100 GB floor and sha256

The floor went up from 60 to 100 GB in `server/models.toml` (`[budget] min_free_gb`). Dry run first:

```
mac$ python scripts/download.py --dry-run --only llm_alt ministral14 gemma_coder minicoder paddleocr glmocr qwen_asr granite_asr zimage diar_wespeaker
...
total 42.08 GB; free now 256.8 GB; after 214.7 GB
```

**The first attempt failed** after downloading 9 GB:

```
KeyError: 'task'
EXIT=1
```

The new entries use `tasks = [...]` (one model can serve several tasks: Gemma-4-12B serves text,
code and vision), and `download.py` was still reading `m["task"]`. It was fixed in one line
(`m.get("task") or m.get("tasks")`) and relaunched. What was already downloaded was not fetched
again: it was checked against the LFS sha256 in seconds (`ok llm_alt 9.04 GB in 4.3 s`).

```
ok ministral14 8.46 GB in 243.5 s
ok gemma_coder 6.73 GB in 218.8 s
ok minicoder 3.00 GB in 187.6 s
ok paddleocr 0.72 GB in 33.2 s
ok glmocr 1.25 GB in 36.8 s
ok qwen_asr 2.47 GB in 55.7 s
ok granite_asr 4.52 GB in 208.6 s
```

### 3. A parallel download slows the backup (and gets paused)

While the models were downloading, another process was copying the Mac's photo backup to the
Windows `F:\` drive over SSH. At that moment it was the only copy of 222 GB. The mirror's speed
fell from 48 to **2 MB/s**. The external disk was still reading at ~30 MB/s, so the bottleneck
was the network. The video download was paused (`kill -STOP <pid>`, resumed with `kill -CONT`):

```
win$ # 60 s con la descarga de LTX pausada
mirror 37 MB/s
```

**Lesson:** on a home network, a `snapshot_download` and an `ssh | tar` share the same link.
Irreplaceable things go first.

### 4. LTX: download only what is used

`ltx-2-mlx` calls `snapshot_download(repo)` with no filter. For the `--distilled` route that is
59.7 GB, when 20.5 are needed. An `allow` option (file patterns) was added to `download.py`:

```
mac$ python scripts/download.py --dry-run --only ltx ltx_gemma
ltx          dgrauet/ltx-2.3-mlx-q4                            20.48 GB  commit 56a5866d63
ltx_gemma    mlx-community/gemma-3-12b-it-4bit                  8.07 GB  commit 86cc6a8ded
total 28.55 GB; free now 205.8 GB; after 177.2 GB
```

The first `ltx-2-mlx info` managed to leave 3.5 GB in the Hugging Face cache before it was cut
off. It was noted in NEEDS-OWNER; it was not deleted. From then on the test runs with
`HF_HUB_OFFLINE=1` and local paths.

### 5. Transcription reference: real audio, known transcript

Kokoro (synthetic voice) favors WER. That is why **FLEURS es_419** (Google, CC-BY-4.0) was used:
real people reading sentences with a published transcript. `scripts/prep_fleurs.py` takes the
first 40 distinct sentences of the `dev` *split* (ordered by file name, deterministic).

**It failed the first time:**

```
urllib.error.ContentTooShortError: <urlopen error retrieval incomplete: got only 234975905 out of 255285841 bytes>
```

`urlretrieve` does not resume. It was replaced with `curl --retry 5 -C -`:

```
40 reference clips, 473 s; long 1426.260000 s
```

### 6. Smoke tests (no official timing: the mirror was still loading the Mac)

```
qa   ok  aciertos 2/2   36.5 tok/s
ctx  @2k: lectura 217 tok/s, generación 38.5 tok/s, primer token 16.2 s
     mismo prefijo reutilizado: primer token 0.24 s (tras un primer uso de 10.8 s)
     @8k: primer token 39.5 s → 0.25 s reutilizando el prefijo
```

**The full 5000-line log does not fit.** It is about 125 000 tokens. With Qwen3.5-9B:

```
         12803904480  peak memory footprint
vm.swapusage: total = 10240.00M  used = 9707.25M
```

12.2 GB of *footprint* and 9.7 GB of swap in 5 minutes, without finishing reading the prompt. It
was killed by PID. The official test was reordered: first "trimmed" and "chunks", with the result
saved partway, and "full" at the end, under a watchdog that kills the process if swap grows more
than 3 GB.

### 7. Unattended start: the Mac waits on its own

The run (about 6 h) cannot depend on this Windows window staying open. That is why
`scripts/sesion_g_start.sh` runs inside a tmux on the Mac and does three things: it waits for the
`tar` feeding the mirror to disappear, resumes the paused download (`kill -CONT`) and launches
`sesion_g_run.sh`. The end of the mirror is detected **from the Mac**: the sender is the local
`tar`.

**Two real stumbles when launching it:**

```
mac$ tmux has-session -t sesion-g && echo EXISTS
EXISTS                      # ¡falso! existía "sesion-g-dl2"
```

`-t` accepts a **prefix**. To ask for the exact name you use `-t =sesion-g`, but in zsh:

```
zsh:1: sesion-g not found
```

In zsh, `=word` expands to the path of the command `word`. It has to be quoted:

```
mac$ tmux has-session -t '=sesion-g' || tmux new -d -s sesion-g 'bash scripts/sesion_g_start.sh'
mac$ tmux ls
sesion-g: 1 windows (created Mon Oct  5 02:28:45 2026)
mac$ cat ~/modelos/logs/sesion-g-start.log
2026-10-05 02:28:45 start; waiting for mirror sender: 35851
```

To follow progress from Windows, with a single command:

```
win$ ssh mac 'tail -n 25 ~/modelos/logs/sesion-g-start.log ~/modelos/logs/sesion-g-run.log'
```

### 8. "The Mac is down" (it was not) and the panic that did happen

Two days later, the Windows watchdog had been reporting `mac: down` for hours and `ssh mac` was
timing out. Before blaming the Mac:

```
win$ ping -n 1 <mac>.local
Reply from fe80::…%4: time=129ms          # por IPv6 responde
win$ ping -4 -n 1 <mac>.local
Request timed out.                          # por IPv4, no
win$ route print -4 | grep "<red-casa>"
      <red-casa>    <máscara /24>         On-link     <ip-windows>    281
      <red-casa>    <máscara /24>         On-link     <ip-vpn>      1
```

The second route belongs to the corporate VPN ("PANGP" adapter): it claims the home network with
metric 1, so all IPv4 traffic to the Mac goes into the tunnel and dies there. The *link-local*
IPv6 address (`fe80::`) does not go through the VPN. Without touching the VPN, an SSH alias for
those days:

```
Host mac6
  HostName fe80::<id>%%4          # %% porque ssh_config usa % para sus variables
  HostKeyAlias <ip-de-la-mac>     # verifica la MISMA llave de host que ya conocíamos
  User <usuario>
  IdentityFile ~/.ssh/id_mac
```

`HostKeyAlias` matters: without it, ssh does not recognize the new address and refuses to log in
(`Host key verification failed`). The tempting move is to turn verification off; the right one is
to tell it which known key to expect.

Once inside, the Mac had been up for **1 day and 21 hours**. The run did not die from a shutdown:

```
mac$ last reboot | head -1
reboot time                                Mon Oct  5 03:22
mac$ ls /Library/Logs/DiagnosticReports/ | grep panic
panic-full-2026-10-05-032324.0002.panic
  "panicString" : "panic(…): watchdog timeout: no checkins from watchdogd in 92 seconds"
```

A *watchdog panic*: the system was so choked that the process that reports "still alive" could
not run for 92 s, and the kernel rebooted the machine.

### 9. The watchdog that killed the wrong process

The log said almost all the models had died from "swap +3 GB" at 10–40 s. But the **disk** told
another story:

```
mac$ grep llm_alt:qa ~/modelos/logs/sesion-g-run.log
llm_alt:qa   killed swap +3792 MB …  wall=10.1 s           # muerto hacia las 02:56
mac$ stat -f "%Sm %N" -t "%H:%M:%S" bench-runs/2026-10-05/llm_alt-qa.json
03:08:54 llm_alt-qa.json                                   # …pero escribió 12 min después
```

A "killed" process does not write its result twelve minutes later. The cause: the benchmark
launches each model as `/usr/bin/time -l python bench_child.py …` to measure peak memory, and the
watchdog did `p.kill()` on **`time`**. `time` dies, but it does not forward the signal: the child
holding the model is orphaned (`launchd` adopts it) and keeps working. The benchmark waited 10 s
and loaded **the next model on top**. By 03:19 there were several 9–14 B models in 16 GB → panic.

When relaunched with swap at zero, the pattern repeated within minutes (22.8 GB of swap, 4 % free
memory) and stopped as soon as the orphans were killed. The fix is two lines: put the child in
**its own process group** and kill the whole group.

```python
p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
...
os.killpg(p.pid, signal.SIGKILL)      # antes: p.kill()  → solo moría `time`
```

Proof that it works, before spending hours of benchmark again:

```
mac$ python scripts/run_watched.py --label t2 --out /tmp/t2.jsonl --timeout 3 -- sleep 97
{"label": "t2", "rc": -9, "wall_s": 4.1, "killed": "timeout", …}
mac$ pgrep -fl "sleep 97"
                                      # vacío: no queda nadie
```

**Lesson:** a watchdog that does not verify its victim died is worse than having no watchdog,
because the numbers say "under control" while the machine fills up. And the results of a run like
that are worthless: every "killed" from that night was measured again.

### 10. Resume only what is missing (and distrust the "ok"s too)

`sesion_g_run.sh` cannot be resumed: relaunching it repeats what was done and walks back into the
step that caused the panic. That is why two new scripts were written that use the same benchmark,
the same watchdog and the same log, and only run the pairs without a valid result:

```
mac$ tmux new -d -s sesion-g "bash scripts/sesion_g2_run.sh"
mac$ tmux new -d -s sesion-g "bash ~/.enjambre/busy.sh run bench sesion-g2b -- bash scripts/sesion_g2b_run.sh"
```

The second one is wrapped in `busy.sh`: while it runs, the Mac publishes `~/.enjambre/busy.json`
and the enjambre router does not send it GPU renders or model jobs.

**An "ok" can also be contaminated.** The pairs that passed on 05-10 ran while the orphans of the
earlier ones were still alive. Measured again with the Mac clean:

```
05-10  llm:qa      ok  speed=20.3 tok/s  …
07-10  llm:qa      ok  speed=37.2 tok/s  …                    # casi el doble
05-10  vlm:vision  ok  speed=3.87 s/imagen  load=473.27 s
07-10  vlm:vision  ok  speed=1.86 s/imagen  load=0.81 s
```

The quality was the same, but the speed was half. Had the 05-10 numbers been published, the
catalog would have chosen with false data.

**Two more stumbles, cheap ones:**

- The URL of the test clip (*Big Buck Bunny*) now returns 404. The file is still on the Blender
  site, but only as a `.zip`:
  ```
  curl: (56) The requested URL returned error: 404
  ```
  The script now downloads the `.zip` and extracts the mp4.
- ffmpeg's `coreimage` filter does not accept the bare name:
  ```
  [coreimage @ …] Parsing of filters failed.
  ```
  It needs `filter=CIComicEffect@default`.

And one that saved data: the long-context test only saved at the end. When Metal ran out of
memory at 32k, it also lost the 2k and 8k results. Now it saves after each size, and on the second
pass `llm_alt` left its 2k and 8k rows before the error:

```
RuntimeError: [METAL] Command buffer execution failed: Insufficient Memory
```

### 11. What came out: the table and the catalog

The benchmark writes one row per model and task into `measurements.jsonl`, and
`scripts/banco_table.py` turns it into the tables of `modelos-mac/docs/BANCO.md`. Summary:

| task | picks | quality | why |
|---|---|---|---|
| text | `llm_alt` | 0.967 | the one that gets the most right (`llm` gets 0.867 but runs at twice the speed) |
| code | `llm_alt` | 0.75 | the only one over the 0.50 threshold; the "code specialist" scored 0.33 |
| vision | `vlm` | 1.0 | `glmocr` gets 0.625 but is 4× faster for OCR |
| transcription | `stt` | 0.969 | `qwen_asr` ties, but takes 13.7 GB |
| image | — | 1.0 | "none is good enough": all three take more than 12 GB (they work, but do not fit) |

The manager reads those rows live. Without restarting it:

```
mac$ curl -s 127.0.0.1:8765/catalog
"text": {"model": "llm_alt", "reason": "medido el 2026-10-07: aciertos QA 0.967, 18.8 tok/s gen,
         9820.7 MB; mejor de 3 que caben en 12000 MB"}
```

Before, it said `predeterminado de models.toml` on every task.

**Local → Claude cascade** (`scripts/bench_cascade.py`, from Windows through a tunnel):

```
local    ok=38/42  1102.2 s  US$ 0       declined=0
claude   ok=42/42    92.2 s  US$ 0.2692
cascade  ok=38/42  1102.2 s  US$ 0       sent_to_claude=0
calibration: accepted_and_wrong=4
```

The local model never said `NO_PUEDO`, so the cascade escalated nothing. Self-confidence is not a
usable signal. For code, the useful signal is failing tests.

**Long context:** with 32 000 tokens, `llm` takes 162 s to give the first answer because it has to
read the whole document. If the document is processed once and its cache is reused, the following
questions take 0.34 s. To ask several things about the same long text, that is the lever.

### 12. Speakers, video and how much memory is left

**Speakers** (56 min of real audio, four manager jobs, one after another):

| job | time | process footprint |
|---|---|---|
| transcription | 138 s | 5.3 GB |
| Nemotron | 13 s | 2.2 GB |
| Sherpa | 496 s | 0.9 GB |
| WeSpeaker | 415 s | 0.7 GB |

Sherpa and WeSpeaker disagree only 7 % of the time, and Nemotron disagrees with both 35 %. Which
one is right is not decided with numbers but by listening: the review sheet marks the 5 minutes
that need to be heard, and that decision is the owner's.

**Video:** the same comic effect takes 13.4 s on CPU (`edgedetect`) and 1.07 s on GPU (Core
Image). That is 12 times faster without changing machines. Text-to-video with LTX, on the other
hand, does not fit: the watchdog killed the smallest step at 20 s.

**Memory for Blender**, measured with `memory_pressure` while a text job ran:

```
03:05:04 idle     free_pct=87%
03:05:13 running  free_pct=26%      # llm_alt cargado: huella 9,8 GB
03:06:02 after    free_pct=86%
```

The manager leaves no model resident: each job is a process that ends. While a text job runs
there are about 4 GB left, so Blender with Metal only fits if the scene is small. If not,
`--device CPU` or wait for `busy.json` to disappear.

**Closing.** What could not be measured unattended is noted with its command in NEEDS-OWNER §13:
`ministral14` with long context, LTX with the Mac empty, and the choice of diarizer.

## What actually went wrong

All of this happened and is described in the steps above:

1. `download.py` failed with `KeyError: 'task'` after downloading 9 GB: the new entries use `tasks = [...]` (step 2).
2. A parallel download dropped the backup from 48 to 2 MB/s; it was paused with `kill -STOP` (step 3).
3. `urlretrieve` truncated FLEURS (`ContentTooShortError`); it was replaced with `curl --retry 5 -C -` (step 5).
4. The 5000-line log took Qwen3.5-9B to 12.2 GB of footprint and 9.7 GB of swap (step 6).
5. `tmux has-session -t sesion-g` matched by prefix, and `-t =sesion-g` failed in zsh without quotes (step 7).
6. The corporate VPN made the Mac look down; the `mac6` alias over IPv6 reached it (step 8).
7. The watchdog killed `time` and left the child with the model orphaned: kernel panic by *watchdog*. Fixed with `start_new_session=True` and `os.killpg` (steps 8 and 9).
8. The 05-10 "ok"s were contaminated by orphans (half the speed) and were measured again on 07-10 (step 10).
9. The *Big Buck Bunny* URL returned 404 and the `coreimage` filter needs `filter=CIComicEffect@default` (step 10).
10. Metal ran out of memory at 32k and the 2k and 8k results were lost; now it saves after each size (step 10).

## How to verify

Three commands and their expected output, copied from the steps:

```
win$ python scripts/catalog_check.py
llm_alt           9.04 GB  apache-2.0             gated=False 2026-09-15  mlx-community/gemma-4-12B-it-qat-OptiQ-4bit
...
diar_alt          0.03 GB  cc-by-4.0              gated=auto  2025-09-29  pyannote/speaker-diarization-community-1  <-- CHECK
```

```
mac$ python scripts/run_watched.py --label t2 --out /tmp/t2.jsonl --timeout 3 -- sleep 97
{"label": "t2", "rc": -9, "wall_s": 4.1, "killed": "timeout", …}
mac$ pgrep -fl "sleep 97"
                                      # vacío: no queda nadie
```

```
mac$ curl -s 127.0.0.1:8765/catalog
"text": {"model": "llm_alt", "reason": "medido el 2026-10-07: aciertos QA 0.967, ..."}
```

Before the measurement, the reason said `predeterminado de models.toml` on every task.

## Self-check

1. Why must the "good enough" threshold be set before measuring?
   Answer: so that "none is good enough" can be declared without bending the criterion to the results; in image, all three options take more than 12 GB and so none is chosen.
2. Why did the earlier watchdog leave orphan processes?
   Answer: it killed `/usr/bin/time`, which does not forward the signal to the child with the model; the fix is to put the child in its own process group and kill the whole group.
3. Why were the 05-10 "ok"s discarded?
   Answer: they ran while the orphans were still alive; quality was the same but speed was half (20.3 against 37.2 tok/s on `llm:qa`).

Pending (NEEDS-OWNER §13): the choice of diarizer, `ministral14` with long context and LTX with the Mac empty.
