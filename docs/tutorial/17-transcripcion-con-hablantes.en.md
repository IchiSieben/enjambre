---
lesson:
  route: build-stack
  order: 16
  level: 2
lang: en
---

# 17 — Transcription with speakers: measure before you believe

## Goal

Audio → text with timestamps and "Speaker 1, 2…" → rename once and have the
name carry over to SRT, VTT and TXT. Measured on 5, 20 and 60 min audios.

## Why

Transcribing is already easy; knowing **who** spoke is the hard part (*diarization*). They are
two different models: Whisper hears words, the diarizer groups chunks of voice by
similarity. The pipeline crosses them by time. And an error number is only worth something if you know which
answer was the correct one: that is why it is measured with audio whose truth you know.

Repo: `IchiSieben/modelos-mac`. Requires the manager from step 16 running on the Mac.

## Steps

### 1. Test audio with a known truth

The owner's personal videos were not used: they have identifiable people. Dialogues are generated
in Spanish with synthetic voices and who said what and when is saved:

```
mac$ python scripts/make_synthetic.py --minutes 60 --voices Mónica,Paulina,kokoro:em_alex,kokoro:ef_dora --name real-60
```

It leaves `~/modelos/audios/real-60.wav` (60.1 min) and `real-60.truth.json` (each turn with its voice,
start, end and text).

`kokoro:*` are voices from the Kokoro-82M model (Apache-2.0), via `mlx-audio`. Only for generating
tests: its phonemizer drags in a GPL dependency, so it lives in a separate extra
(`--extra synth`), outside the service.

### 2. The pipeline

```
audio ──ffmpeg──► WAV 16 kHz mono ──► whisper-large-v3-turbo ──► segmentos con texto
                                  └─► Nemotron-3-Diarization ──► turnos (quién, cuándo)
                         merge: cada segmento toma el hablante con más solape → "Hablante N"
```

Speakers are numbered in order of appearance. Renaming does not touch the original result:
a map `{"Hablante 1": "Moderadora"}` is saved and applied on export.

### 3. From the web

Tunnel (`ssh -L 8765:127.0.0.1:8765 mac`) and in the browser `http://127.0.0.1:8765/`: *Task:
Transcription with speakers*, upload the file, *Queue*. The 5 min audio took **19.0 s**
(model load 3.2 s, footprint 2.9 GB). Click the row, type the names,
*Rename*:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

The SRT now comes out with the names:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

### 4. Measuring

```
mac$ ~/modelos/envs/main/bin/python scripts/bench_speakers.py ~/modelos/audios/real-{05,20,60}.wav
```

| Audio | Voices | Time | × real time | Hours of audio per hour | Footprint | Speaker error (DER) | Speakers found | Word error (WER) |
|---|---|---|---|---|---|---|---|---|
| 5.2 min | 2 | 18.2 s | 0.059 | 17.0 | 2.93 GB | 5.3 % | 2 ✔ | 0.6 % |
| 20.0 min | 3 | 51.3 s | 0.043 | 23.4 | 3.56 GB | 4.6 % | 3 ✔ | 1.6 % |
| 60.1 min | 4 | 147.3 s | 0.041 | **24.5** | 5.34 GB | 8.8 % | 4 ✔ | 0.2 % |

**~24 hours of audio per hour** on long audios. In the 60 min one, Whisper takes 135 s and
diarization only 12 s. Every wrongly assigned segment is listed in
`bench/speakers-<audio>.review.txt` to review by hand.

### 5. Two diarizers, one by default

| | Nemotron (default) | sherpa-onnx (alternative) |
|---|---|---|
| 60 min | 147 s, DER 8.8 %, **4** speakers | 829 s, DER 7.2 %, **13** speakers |
| Where it runs | GPU (MLX) | CPU (ONNX) |
| License | OpenMDW | MIT + Apache |

Similar error, but Nemotron gets right **how many** people there are, which is what makes
renaming useful, and it is 5.5× faster. Risk: on a 5 min audio it gave **36.6 %** error (on another
with the same two voices, 3.0 %). If the result looks odd: `{"diarizer": "sherpa", "num_speakers": N}`.

### 6. Why Nemotron failed with 36.6 %

Nemotron does not cluster voices at the end, as sherpa does. It processes the audio in chunks and has **8 speaker
slots** that fill **in order of arrival**: a speaker cache. What is decided at the
start weighs on everything else. The model was loaded once and variants were tried on the same
5 min audio (Mónica + Eddy es-MX, distinct voices):

| Variant | Error (DER) | Max probability of the 2nd speaker |
|---|---|---|
| Full audio (2 runs) | 36.6 % and 36.6 % | 0.53 |
| Threshold 0.3 / 0.2 instead of 0.5 | 22.9 % / 13.3 % | 0.53 |
| Without the first 5 s | 31.6 % | 0.84 |
| Without the first 15 s | 14.4 % | 0.94 |
| Without the first 30 s | **3.6 %** | 0.96 |

Three conclusions:
- **It is not chance**: the same audio always gives the same error.
- **The start decides it.** What the model hears in the first ~30 s conditions the cache:
  the second voice ends up in slot 0 and slot 1 never gains confidence. If the audio starts
  after those 30 s, it separates well. Which exact part of the start causes it was not isolated: the
  second voice has a short turn there, but cutting the first 15 s (which removes it entirely) still
  leaves 14.4 %.
- **Lowering the threshold is a patch**: it improves the error, but does not fix it.

Practical consequence: if the result has almost all the time on a single speaker, distrust it.
Repeat with sherpa, which clusters the whole audio at the end and does not depend on the start.

### 7. With a real audio (56 min)

A real audio from the owner, 56 min, with real people. The audio and the transcripts **do not leave
the Mac** and do not enter any repo: they stay in `~/audios-prueba/salidas/`. It is queued from the Mac
itself:

```
mac$ curl -s -F task=speakers -F 'params={"diarizer":"nemotron"}' -F "file=@$HOME/audios-prueba/<audio>.m4a" 127.0.0.1:8765/jobs
mac$ curl -s "127.0.0.1:8765/jobs/<id>/export?fmt=srt" > ~/audios-prueba/salidas/nemotron.srt
```

| Diarizer | Time | Hours of audio per hour | Peak footprint | Speakers found |
|---|---|---|---|---|
| Nemotron | 158 s | 21.2 | 5.25 GB | 3 (47 % · 52 % · 1 % of the time) |
| sherpa, threshold 0.5 | 616 s | 5.5 | 5.26 GB | **129** |
| sherpa, `num_speakers: 3` | 629 s* | 5.3 | 5.26 GB | 3 (8 % · 92 % · 0 %) |

\* This run shared the Mac with another test; sherpa's clean time is the one in the row
above.

What you can see without knowing the correct answer:
- **Whisper performs the same as with synthetic voices**: 129–146 s for 56 min.
- **sherpa, without being told how many people there are, is useless on real audio**: 129 groups.
- **The two diarizers do not agree**: they disagree on **48 %** of the time, measured by taking one
  as the reference for the other. Of that, ~38 % is *who* is speaking and ~11 % is that one hears voice where the
  other does not. Nemotron splits the time almost in halves; sherpa with 3 speakers
  fixed puts 92 % on one. Given what we know from step 6, neither of the two deserves
  blind trust.

The number 3 was given to sherpa by Nemotron. So the number of speakers is not an
independent opinion, but the split of who speaks when is. Which one is right is decided by a person
reading both SRTs.

### 8. Making the manager start on its own: LaunchAgent

```
mac$ bash scripts/install-launchagent.sh
.../Library/LaunchAgents/dev.modelos.server.plist: OK
{"version":"0.1.0","running":null,"task":null,"queued":0,"free_gb":54.3}
mac$ lsof -nP -iTCP:8765 -sTCP:LISTEN
Python  94703 <usuario>  7u  IPv4  TCP 127.0.0.1:8765 (LISTEN)
```

What the LaunchAgent does:
- Starts the manager at login on the screen (`LimitLoadToSessionType: Aqua`).
- If it goes down, it relaunches it (`KeepAlive`). Tested: the process was killed by its PID and it came back on its own after
  ~21 s.
- It still listens only on `127.0.0.1`.
- A 5 min job ran the same as before: 18.7 s and 2.93 GB.

The installer is idempotent; `--uninstall` removes it.

## What actually went wrong

1. **My "3 and 4 speakers" audios had 2 voices.** The first batch gave 37–50 % error on
   both diarizers and they looked bad. The confusion matrix showed that **both
   merged the same pairs**. Cause: macOS only ships two Spanish voices (Mónica and
   Paulina); any other name (Eddy, Reed…) falls back to one of those and the audio comes out **byte for
   byte identical** (same sha1). The models were right and my truth was wrong. Fix: Kokoro
   voices. Lesson: check that the test data is what it says it is before blaming the
   model.
2. **I killed my own 60 min synthesis.** The wait loop ran `tmux kill-session`
   when its deadline expired, whether or not the job had finished. It was regenerated; since then the wait is a
   `until` loop that checks the result and never kills anything.
3. **sherpa-onnx installed but would not load**: it was missing `libonnxruntime.dylib`, which comes in
   `sherpa-onnx-core`.
4. **Kokoro: "Model type tts not supported"**: `mlx-audio` infers the type from the folder
   name. `~/modelos/tts` → `~/modelos/kokoro`.
5. **The first attempt with the real audio died from the GPU**:
   `[METAL] Command buffer execution failed: Discarded (victim of GPU error/recovery)
   (…InnocentVictim)`. *Innocent victim* means the GPU reset because of
   someone else and our job went down with it. It could not be determined who: the system log is not
   readable over SSH. Photos was open. The second attempt, identical, finished fine. The manager
   marks the job `failed` with the full error, and it is enough to queue it again.
6. **The LaunchAgent started and went to sleep**: 7 MB, 0 % CPU, without opening the port or
   writing the log. Cause: launched by `launchd`, Python read the repo in `~/Documents`. macOS
   protects that folder (privacy, TCC) and the process waits for a permission nobody sees.
   From tmux it does not happen; most likely there the permission is inherited from the terminal (this was not
   checked separately). Fix: the installer copies
   `server/` to `~/modelos/app`, outside `~/Documents`, and the agent runs that copy. That way you do not
   have to give Python access to all your documents. Cost: after changing the code you
   have to run the installer again.

## How to verify

```
mac$ lsof -nP -iTCP:8765 -sTCP:LISTEN
Python  94703 <usuario>  7u  IPv4  TCP 127.0.0.1:8765 (LISTEN)
mac$ ~/modelos/envs/main/bin/python scripts/bench_speakers.py ~/modelos/audios/real-{05,20,60}.wav
```

Expected (table from step 4): a 5.2 min audio in about 18 s, 2 speakers found, DER 5.3 %,
WER 0.6 %; the 60.1 min one, 4 speakers found, DER 8.8 %, close to 24 hours of audio per hour.
The manager answers on `127.0.0.1:8765` and is still alive after killing its process (it comes back after ~21 s).
The times are a ceiling, not an average (see Limits).

## Limits (read them before selling this)

- The tables in steps 4 to 6 use synthetic voices: no noise, no overlaps, no
  interruptions. The real audio in step 7 confirms it: there the two diarizers disagree for
  almost half the time.
- n = 1 per audio. The Mac was indexing Photos in the background (60–320 % CPU): the times are the ceiling.

## Self-check

1. Why can't Whisper alone tell who is speaking?
2. A diarizer gives DER 7 % but finds 13 speakers where there are 4. Why is it worse for the
   user than one with DER 9 % and 4 speakers?
3. What would you have checked first on seeing that two different models fail in exactly the same way?
