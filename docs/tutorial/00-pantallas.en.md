---
lesson:
  route: build-stack
  order: 1
  level: 0
lang: en
---

# 00 — Screens: one ultrawide monitor split between Windows and the Mac

## Goal

See the Windows desktop on the left half of the monitor and the Mac desktop on the right, each at
its own native half-screen resolution (1720×1440).

## Why

A 3440×1440 ultrawide monitor can be shared between two computers with **Split screen** mode
(also called PBP, *picture by picture*): the monitor itself, not the operating system, divides
the panel into two video inputs and each half ends up at 1720×1440. To each computer that looks
like a separate monitor with an almost square aspect ratio. That is why order matters: Windows only
offers the half-screen resolution **once the monitor is already split** (what happened here, see
"What actually went wrong"). On the Mac, the equivalent step is choosing Extend.

> Alternative that does not split the screen: a monitor with a USB KVM (see `01-deskflow.md`). The
> monitor in this guide has 2 DP and 2 HDMI inputs and, according to its spec sheet, no USB hub or
> KVM (verified in `PLAN.md` §1.2); that is why split is used here.

## Steps

1. In the monitor menu (OSD, physical buttons): **Window settings** and choose **Split screen**.
   The menu offers three modes: *Single window*, *Split screen* and *Picture-in-picture*.

   *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

2. In Windows, **after** turning on split: Settings → System → Display →
   *Display resolution* → pick the half-screen one (1720×1440).
3. On the Mac: the right half will show *"Choose to Mirror or Extend Display from the menu"*.
   Go to Control Center → **Mirror Display** and choose **Extend** (not Mirror).

## What actually went wrong

**1. Changing the resolution before splitting the monitor.** Before the split, Windows shows
3440×1440 as "Recommended". The screenshot shows the start of the list (3440×1440, 2560×1440,
1920×1440, …); according to the logbook, 1720×1440 does not appear until split is turned on at the
monitor. Fix: turn on split first.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

**2. The Windows desktop as a box inside its half.** With a resolution that did not match half
the screen, the desktop did not fill its half: it ended up in a smaller box
(both photos; the gray rectangle is an area covered on purpose to hide file names).
Diagnosis: a resolution different from the real half-screen. Fix: choose 1720×1440 once
split is on.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

**3. The Mac "not detecting" its half.** It did detect it: the message *"Choose to Mirror or
Extend Display from the menu"* is the Mac asking you to choose what to do with the new display.
All that is missing is choosing **Extend**.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## How to verify

- Windows: Settings → Display shows the half-screen resolution you chose.
- Mac: Settings → Displays shows the monitor as extended, not mirrored.
- Sharing keyboard and mouse between the two halves is not part of this step: it is the next one
  (`01-deskflow.md`).

Still to measure (listed in `PLAN.md` as "to test"): the refresh rate each machine
reports per half. The OSD in the screenshot shows 180 Hz for the full monitor; the guide did not
measure what each half receives.

## Self-check

1. Why does Windows not offer 1720×1440 before split is turned on?
   <details><summary>Answer</summary>Because the monitor does the split. Before it is on,
   Windows only sees a single 3440×1440 panel; the half screen shows up as a resolution once
   the monitor announces it.</details>
2. The Mac says "Choose to Mirror or Extend Display": is there a connection problem?
   <details><summary>Answer</summary>No. It detected the display; you only need to choose Extend in
   Control Center → Mirror Display.</details>
3. What is the difference between a KVM and split with Deskflow?
   <details><summary>Answer</summary>With a KVM you see one machine at a time on the full screen; with
   split you see both at once, and the keyboard/mouse is shared by software (Deskflow).</details>
