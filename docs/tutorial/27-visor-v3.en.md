---
lesson:
  route: build-stack
  order: 26
  level: 2
lang: en
---

# 27 — Viewer v3: every failure with its cause, sortable projects and models with a verdict

## Goal

**What you get:** open the viewer (on the PC or from your phone) and know, without opening a terminal,
why each job failed, how much each project spent and on which machine, what time of day
you work, and which local models are worth using and which are not.
**Who does it:** Claude Code (session F3, 2026-10-07). You approve the aliases and the model
statuses (NEEDS-OWNER §21).
**Before:** [24 — Viewer v3, web copy and tray](24-visor-web-y-bandeja.md) (F2). The Windows
shortcut is in [20 §4b](20-visor-maestro.md).

## Why

In F2 the attention card said "failed" with no cause, and there were dozens of local models with no way to note which ones are worth the effort (details in §1 and §4).

## Before and after

The same snapshot, in the same web copy and at 390 px (phone):

| Before (F2) | After (F3) |
|---|---|
| *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)* | *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)* |

In F2 the attention card said "failed" and nothing else: to find out what happened you had to go in
over ssh and look for the log. In F3 each failure carries its **why**, in one line, and failures with the
same cause are grouped ("2 failed · exit 2: … unrecognized arguments: --id").

## Steps

The steps are the numbered sections below (§1 to §6).

## 1. Every failure with its cause (part 1)

**Why:** the ledger is append-only and stored the state, but not the reason. An alert with no cause
forces you to investigate by hand and, by the third identical failure, nobody looks at it anymore.

- `razones.py` writes a cause sentence for every failed or incomplete job: permission
  denied, spending cap, killed, `ssh` 255, the error line from the log, the agent's result
  or the push note. It also stores the log path and whether it is a test.
- `append_ledger` enriches each new row. The old rows were filled in once, with
  a prior copy at `state/jobs.jsonl.bak-20261007-213211`:

```
PS> python razones.py --backfill
{"rows": 53, "changed": 28, "bad": 18, "bad_without_reason": 0, "bad_with_log": 9, "tests": 15}
```

- **Tests** (sandbox, stubs, `e3-*` fire drills, smoke) are counted separately and folded:
  failing is their job (DECISIONES 230).
- A quota with no recent data is shown in gray with "last data N h ago", instead of disappearing.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## 2. Machines with their own color and public aliases (part 2)

- Each machine has its own color, based on the operating system: PC blue, Mac mini light gray and Linux
  amber. It is used in the labels, the bars and the charts (DECISIONES 228).
- The **Machine** column of projects splits the active hours between PC and Mac.
- On the web, projects show up with an **alias** (`config/visor-alias.toml`) and not with the folder
  name. The 17 aliases were proposed by Claude and await your approval (DECISIONES 229). The local viewer
  always shows the real name.

## 3. Projects, timeline and consumption (part 3)

- **Projects:** a table sortable by any column (USD, sessions, hours, tokens, last use)
  and filterable by text, machine and date. The filter stays in the URL (`#ps=cost&pd=-1`), so it
  can be saved or shared.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

- **Timeline** rebuilt: real width, Lima hours, named lanes, zoom with wheel,
  pinch or drag, and highlighting in both directions (hovering over a bar marks its row
  and vice versa). Checked at 1920 and at 390 px:

| 1920 px | 390 px | 390 px with zoom |
|---|---|---|
| *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)* | *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)* | *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)* |

- **Consumption:** a single chart with four lenses (8 days, day, week, month) instead of three
  tables, plus a **heat map** of weekday × hour. Each session splits its cost across the
  clock hours it was open. Lima has a whole-hour offset with no daylight saving time, so
  split hours do not need correcting.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

- **"Avoided equivalent" is not computed**: the manager does not store tokens or audio minutes per
  job. The card shows the count, the formula and which data is missing (DECISIONES 231).

## 4. Local models as a living inventory (part 4)

**Why:** there were dozens of models on the Mac and no way to note which ones are worth the effort. The
decision lived in the owner's memory.

- Each model shows the logo and color of its family (Gemma, Qwen, Mistral, Whisper…), its real
  Hugging Face name, its status ("in use / works / to test / does not work / discarded")
  and a note.
- It can be filtered by task and by status. The quality chart compares the models of a single task
  and there is a ranking. Long tables stay folded.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)* *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

- **Editing the status** from the viewer: the button does `PATCH /catalog/<key>` to the manager on the Mac
  through the tunnel. Without a tunnel, it shows the equivalent command:

```
PS> python enjambre.py modelo <key> --status "sirve" --nota "una línea, hasta 200 caracteres"
```

It prints the manager's response and exits with code 0 only if the manager answered 200. The tunnel
route was really tested from a `file://` page (`diar`, saved with no changes).

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

The status lives in `~/modelos/estado.json` on the Mac, outside git (DECISIONES 233).

## 5. Purposeful motion and live mode (part 5)

- Numbers count up when they change, new rows appear with a fade and "N s ago"
  advances every second. With `prefers-reduced-motion` there is no animation.
- The **live** button (in the Windows app) reloads when there is a new snapshot.
  `python enjambre.py visor --follow` rebuilds every 30 s.
- The footer says how often the web copy is uploaded (10 min).
- Weight cap: the page may grow at most 20 % over F2 (162 247 B → cap 194 696 B).
  It ended at 188 353 B, and a test watches it (DECISIONES 227).

## 6. Deployment

There is no `enjambre publicar`, and Hostinger's archive deploy would replace the
Landing. That is why only `index.html` was uploaded, over TUS, and the previous version was saved first
(DECISIONES 234):

```
rollback  state/web-dist/rollback/index-f2-20261007.html  162 512 B  sha 764a1de530fe
nuevo     public_html/visor-app/index.html                188 618 B  sha eb88477111d5
sin credenciales:  /  401   /data.php  401   /pwa/manifest.webmanifest  200
```

## What actually went wrong (the failure we found)

After the deploy, the data upload blocked itself:

```
2026-10-07T22:19:16 ... | web: BLOQUEADA: IP | ...
```

The leak detector looks for IPs with an `n.n.n.n` expression, and the SVG paths of the model logos
have numbers like `<cuatro números separados por puntos>`. No real IP was leaking; the detector did its
job and failed closed. Fix: logos now go through an **allowlist** (`glyph_ok`: only a
`<path d>` with path data, or the 1-to-3-letter monogram plate), and the detector checks
everything else, logo names included (DECISIONES 235). The next upload went through (171 072 B).

**Lesson:** a pattern detector over free text sooner or later collides with data that is not
text. Better to validate the shape of that data and take it out of the sweep than to loosen the pattern
for everyone.

## How to verify

```
PS> python -m unittest discover -s tests
Ran 230 tests in 11.851s
OK (skipped=1)
```

Deployed page with the real load simulated, at 390 px: no JS errors, no horizontal
scroll, and sorting by USD puts the highest-spending project on top.

## Self-check

1. Why did the leak detector block the data upload if no IP was leaking?
   Answer: it looked for `n.n.n.n` and the SVG paths of the logos have numbers like `<cuatro números separados por puntos>`; it failed closed. It was fixed with an allowlist (`glyph_ok`).
2. What does `razones.py` do and why are tests counted separately?
   Answer: it writes a cause sentence for every failed or incomplete job; for tests (sandbox, stubs, `e3-*`, smoke) failing is their job (DECISIONES 230).
3. What happens when you edit a model's status without a tunnel to the Mac?
   Answer: the viewer shows the equivalent command `python enjambre.py modelo <key> --status … --nota …`; with a tunnel it does `PATCH /catalog/<key>` to the manager.
