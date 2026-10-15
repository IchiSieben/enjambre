---
lesson:
  route: build-stack
  order: 7
  level: 0
lang: en
---

# 06 — Updating macOS (and what breaks and what does not)

## Goal

Move the Mac from macOS 15.3.1 to a version that supports Xcode 27, and then check, over SSH,
that the tools from step 02 still respond.

## Why

A major macOS update touches everything underneath your tools: the system,
the Command Line Tools, the network. So it pays to know **beforehand** what changes and to have one command
that measures it **afterwards**. Here the reason for updating was concrete: the Xcode listing in the App
Store requires a minimum macOS version.

## Steps

1. Check the requirement. In the App Store, the Xcode listing (version 27.0) says
   *"Requires macOS 26.6 or later"*.

   *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

2. System Settings → General → Software Update. The screenshot
   shows *"Downloading macOS 27.0.1…"* with the progress bar. This is a manual step (the owner,
   with the graphical interface; a major update restarts the Mac).

   *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

   The sources do not explain why 27.0.1 was installed and not 26.6, the minimum Xcode asked for.

3. When the Mac comes back, update the Command Line Tools too (the same software update
   panel offers them; see "What actually went wrong").

## What actually went wrong

- **Version mismatch between logbook and reality.** The manual notes say
  macOS **15.3.1** (build 24D70); the live command, later, said **27.0.1** (build 26A434).
  The Mac had been updated in between. Rule: what `sw_vers` prints today wins,
  not what was written down yesterday.
- **The Command Line Tools became outdated.** `brew doctor` warned
  *"Your Command Line Tools are too outdated"* (recorded in the HANDOFF of session 1). The
  fix is to install the CLT update from Software Update; in the overnight run
  session the owner said that "the Command Line Tools are updating".
- **Side effect on the network.** After the update, the Mac's IP changed and `ssh mac`
  gave *Connection timed out*: see `04-red-y-dhcp.md`. The sources do not prove that the update
  *caused* the change; only that it happened afterwards.

## How to verify

Same command before and after, from Windows:

```powershell
PS> ssh mac 'sw_vers; claude --version; brew --version'
```

| | Before (logbook) | After (live `sw_vers`, 2026-10-04) |
|---|---|---|
| macOS | 15.3.1 (24D70) | 27.0.1 (26A434) |
| Claude Code | 2.1.289 | 2.1.289 |
| Homebrew | 7.0.7 | 7.0.7 |

The installed tools did not change version with the update (Homebrew and Claude Code
the same before and after) and they answered over SSH.

Still to check after updating, because the sources do not record it: that `brew doctor` no longer
warns about the CLT, and that Deskflow reconnects on its own (`01-deskflow.md`).

## Self-check

1. Which macOS version did the Xcode 27.0 listing ask for?
   <details><summary>Answer</summary>macOS 26.6 or later.</details>
2. The logbook says 15.3.1 and `sw_vers` says 27.0.1. Which one do you trust?
   <details><summary>Answer</summary>`sw_vers`: it is the current measurement; the logbook is from before
   the update.</details>
3. After updating, `brew doctor` complains about the Command Line Tools. What do you do?
   <details><summary>Answer</summary>Install the CLT update from Settings →
   General → Software Update.</details>
