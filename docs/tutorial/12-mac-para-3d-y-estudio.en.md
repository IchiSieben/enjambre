---
lesson:
  route: build-stack
  order: 13
  level: 2
lang: en
---

# 12 — The Mac for 3D and for studying

## Goal

From Windows, one line renders any STL part of the capstone project on the Mac and brings back
the PNG. The Mac also runs the algorithms course checks just like Windows does.

## Why

The Mac mini M2 Pro has a 16-core GPU that Blender uses through **Metal**, Apple's graphics API.
On Windows this job would compete with everything else; on the Mac it runs on its own, headless,
while you keep working. Two ideas are worth getting straight:

- **A render does not need an AI model.** It is a **deterministic** job: same STL, same
  image. That is why it does not go through `claude`, costs $0 and is launched by a script, not an agent.
- **STL does not store units.** It is a list of triangles with unitless numbers. Blender reads
  1 unit = 1 meter; almost every CAD tool, Onshape included, exports in millimeters. If you do not
  fix the scale, a 230 mm base arrives as a 230 m object.

## Steps

### 1. What is in the mechanical module

The team's repo (a teammate's remote), folder `Proyecto/MóduloMecánico/`:

| File | Triangles | Size (bounding box) | Format |
|---|---|---|---|
| `Estacion_v2_Base_v2.stl` | 4 846 | 229.5 × 229.5 × 73.2 | Binary STL, 237 KB |
| `Estacion_v2_windmill_v4.stl` | 3 010 | 80 × 80 × 110 | Binary STL, 147 KB |

The numbers only make sense in **millimeters**: a 23 cm station and an 11 cm rotor.
I computed the box by reading the binary STL (an 80-byte header, an integer with the number of
triangles and 50 bytes per triangle). The rest of the repo: the electronics schematic as a PDF and a
`main.ino` (Arduino/ESP32).

### 2. Getting the repo onto the Mac

```bash
PS> ssh mac 'mkdir -p ~/Documents/Portfolio/laptop && bash ~/.enjambre/tmux-run.sh -C ~/Documents/Portfolio/laptop -- git clone -q https://github.com/<compañero>/Proyecto-3d.git'
PS> ssh mac 'cd ~/Documents/Portfolio/laptop/Proyecto-3d && shasum -a 256 Proyecto/MóduloMecánico/*.stl'
fedde192…0a6ed90948a05eeaf  Proyecto/MóduloMecánico/Estacion_v2_Base_v2.stl
a54f0e90…751d3af74a1d0daa  Proyecto/MóduloMecánico/Estacion_v2_windmill_v4.stl
```

Same `sha256` as on Windows: they are the same bytes. The `git clone` goes through `tmux-run.sh` because
it needs the keychain (page 11).

### 3. Installing Blender

```bash
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -- bash -c "brew list --cask blender >/dev/null 2>&1 && echo ya-estaba || brew install --cask blender"'
🍺  blender was successfully installed!
PS> ssh mac '/Applications/Blender.app/Contents/MacOS/Blender --version | head -1; lipo -archs /Applications/Blender.app/Contents/MacOS/Blender'
Blender 5.2.2 LTS
arm64
```

`arm64` and nothing else: it is a native Apple Silicon binary, no Rosetta. It takes up 907 MB. It did not ask for a
password: a cask that goes to `/Applications` does not need `sudo` if your user is an administrator.

### 4. The render script

`tools/render_stl.py` runs **inside** Blender, with no interface:

```bash
blender -b --factory-startup -P render_stl.py -- PIEZA.stl SALIDA.png --device METAL --samples 64
```

- `-b` means no window; `--factory-startup` ignores your preferences, so the result does not depend
  on how you have Blender configured.
- It imports with `global_scale=0.001` (`--unit mm`, the default value).
- It centers the part and places the camera **from the bounding box**: the same calculation frames
  a screw or a chassis.
- Cycles with `--device METAL` (GPU) or `CPU`, 1280×720, 64 samples with denoising.
- The last line is a JSON with the timings.

### 5. CPU versus Metal

Measured Oct 4, M2 Pro, 1280×720, 64 samples + denoise, each combination twice:

| Part | Metal (16-core GPU) | CPU (10 cores) | Metal is |
|---|---|---|---|
| Base (4 846 triangles) | 3.61 s · 3.58 s | 10.62 s · 10.59 s | 2.9× faster |
| Windmill (3 010 triangles) | 3.57 s · 3.64 s | 10.86 s · 10.82 s | 3.0× faster |

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

*Render of the real output with `freeze`.*

The times cover only the render (not Blender's startup) and barely vary between repetitions.
**Watch out for the first render on Metal: it took 80 s** with only 16 samples. Cycles compiles its
kernels for your GPU the first time and caches them; from the second run on, 3.6 s. If you measure,
always discard the first run.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

### 6. One line from Windows

```bash
PS> python enjambre.py render "laptop/Proyecto-3d/Proyecto/MóduloMecánico/Estacion_v2_Base_v2.stl"
{"id": "render-Estacion-v2-base-v2-094837", "worker": "blender", "status": "ok",
 "png": "...\\enjambre\\state\\renders\\render-Estacion-v2-base-v2-094837.png",
 "render": {"device": "METAL:Apple M2 Pro (GPU - 16 cores)", "samples": 64, "triangles": 4846,
            "size_m": [0.2295, 0.2295, 0.0732], "render_s": 3.69, ...}, "cost_usd": 0}
```

9.5 s end to end the first time; 13 s when it also copies the STL from Windows. What it does:

1. Copies `tools/render_stl.py` to the Mac (always the version reviewed here).
2. If the STL **exists on Windows**, it copies it to the same path on the Mac. That way it also works for
   a part just exported from Onshape that is not in git yet. Tested with an STL that only
   existed on Windows, in a path with spaces: `"copied_from_win": true`, render in 4.0 s.
3. Runs Blender inside the graphical session's tmux: if the SSH drops, the render keeps going.
4. Brings the PNG back to `state/renders/` and writes a line in the ledger (`worker: blender`).

Options: `--device CPU`, `--samples 128`, `--res 1920x1080`, `--unit m`, `--out <folder>`.

Why `enjambre render` and not `enjambre run`? `run` is the contract of an **agent that edits a
repo** and returns a branch. A render edits nothing. Also, this repo belongs to a teammate: a
work branch would be pushed to **their** GitHub. (DECISIONES 51.)

### 7. From Onshape to the image

Onshape runs in the browser, so you do the export yourself; the rest is one line.

1. **Export.** In the *Part Studio*, right-click the part in the parts list →
   **Export**. For rendering: format **STL**, *Format* **Binary** (smaller than *Text*),
   *Units* **Millimeter**, *Resolution* **Fine** (or *Custom*: chord tolerance, angular
   deviation and minimum facet width). To keep the design (another CAD, an archive): **STEP**, which
   carries exact geometry and not triangles. **Careful: Onshape's STEP comes out in meters by
   default**; tick *Use custom units for export* → **Millimeter** if you want it in mm.
   Source: [Onshape Help — Exporting Files](https://cad.onshape.com/help/Content/File/exporting_files.htm)
   (accessed Oct 4, 2026; I did not try it in the interface).
2. **Save it in the repo**, under the version name the team already uses:
   `Proyecto/MóduloMecánico/<Pieza>_v<N>.stl`. The STEP, if you want it, next to it.
3. **Render:** `python enjambre.py render "laptop/Proyecto-3d/Proyecto/MóduloMecánico/<Pieza>_v<N>.stl"`.
4. **Check the scale:** `size_m` in the JSON must match the Onshape measurement. If
   Onshape says 229.5 mm, `size_m` must say `0.2295`. If it says `229.5`, you exported in meters
   or imported without scale: repeat with `--unit m`, or re-export in millimeters.

| You exported in | You pass | Example |
|---|---|---|
| mm (the usual) | `--unit mm` (default) | 229.5 mm → `size_m` 0.2295 |
| m | `--unit m` | 0.2295 m → `size_m` 0.2295 |
| inches (*Inch*) | not supported yet: re-export in mm | — |

Blender does not open STEP without add-ons. For rendering, always STL.

### 8. Algorithms on the Mac

The course has no dependencies: the scripts use only the Python standard library, and the
visualizer fuzz uses Node. Node was missing on the Mac:

```bash
PS> ssh mac 'bash ~/.enjambre/tmux-run.sh -- brew install node'
PS> ssh mac '/opt/homebrew/bin/node --version'
v26.10.0
```

(On Windows it is v22.14.0; the fuzz passes on both.) The `ada-pc2` repo was cloned into
`~/Documents/Portfolio/algoritmos/ada-pc2`; `parcial/` is not a git repo, so only its checks
were copied (`parcial/verificacion/`).

The same checks on both machines (108 checks from the midterm, the QuickSelect
visualizer fuzz and the site regeneration), measured with a Python stopwatch:

| Step | Windows (Python 3.12.10, Node 22) | Mac M2 Pro (Python 3.12.15, Node 26) |
|---|---|---|
| `verificar_tarjetas.py` (27 OK) | 68.9 s | 35.8 s |
| `verificar_pd5.py` (18 OK) | 13.2 s | 6.5 s |
| The other 5 (63 OK) | 7.6 s | 3.4 s |
| **Python total, 108/108 OK** | **89.7 s** | **45.7 s** |
| Partition fuzz (Node) | 1.5 s | 0.2 s |
| `deploy/build.py` | not run (it rewrites `deploy/` in your copy) | 32 pages, then `git checkout -- deploy/` |

The Mac is almost twice as fast at single-core Python. Careful with the comparison: Windows
ran an hour before the Mac (11:29), with macOS `mediaanalysisd` analyzing photos in the
background (over 100 % CPU). The finer Mac-versus-Windows measurements are on page
18, from another session.

### 9. A study job through the queue

The next pending task for `ada-pc2` (items 7–11 of `SONNET-TODO.md`: induction labels already
drafted) launched as a normal job:

```bash
PS> $env:ENJAMBRE_MAX_TURNS=20; python enjambre.py run --node mac --repo algoritmos/ada-pc2 --task tasks/TASK-ada-induccion-labels.md --worker claude
{"id": "ada-induccion-labels", "branch": "mac/ada-induccion-labels", "exit": 0, "status": "incomplete",
 "permission_denials": [{"tool": "Bash", "target": "python3 - <<'EOF' ..."}], "duration_s": 40, "cost_usd": 0.35}
```

40 s and $0.35. The agent applied items 7, 8, 9 and 11 and skipped 10, which had nowhere to go (the phrase
`Hipótesis inductiva` does not exist in those files: I checked). It marked `incomplete` because it **first
tried** a Python script through Bash, which is not allowed, and then did the same with `Edit`.
Lesson: `incomplete` says "there was a denial", not "work is missing". You have to read the branch.

## What actually went wrong

1. **80 s for the first render on Metal.** It was not slowness: it was kernel compilation. You can see it in
   that the second run takes 3.6 s with 4 times as many samples.
2. **First render washed out** (background and part almost the same gray). I lowered the background light (0.6 →
   0.35) and darkened the material.
3. **A JSON with my macOS username.** The ledger line carried the absolute path of the PNG on the
   Mac. The ledger is not in git, but I fixed it anyway: the script now returns only the file name.

## How to verify

```bash
PS> python enjambre.py render "laptop/Proyecto-3d/Proyecto/MóduloMecánico/Estacion_v2_windmill_v4.stl"
PS> python enjambre.py status      # última línea: render-… mac blender ok
```

Open the PNG in `state/renders/`: the part should look whole, centered and with a shadow.

## Self-check

1. You import an STL and Blender shows it 1000 times larger than in Onshape. What happened?
   <details><summary>Answer</summary>STL does not store units. Onshape exported in millimeters and
   Blender read each unit as a meter. It is fixed by scaling by 0.001
   (<code>--unit mm</code>).</details>
2. You measure Metal and the first render takes 80 s. Do you conclude that Metal is slower than the CPU?
   <details><summary>Answer</summary>No. The first run includes compiling the Cycles
   kernels for the GPU. Discard it and measure from the second: 3.6 s versus 10.6 s.</details>
3. Why is the render not a job for <code>claude</code>?
   <details><summary>Answer</summary>Because it is deterministic: there is nothing to decide. Paying
   for a model to run a fixed command costs money and adds variation. The model is reserved
   for judgment.</details>
