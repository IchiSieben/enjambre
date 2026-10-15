---
lesson:
  route: build-stack
  order: 17
  level: 2
lang: en
---

# 18 — Mac vs Windows on the same tasks, and local model vs `claude -p`

## Goal

Know with numbers what is worth sending to the Mac: tests, builds, git, a real `claude -p`
job, and whether a local 9B model replaces `claude -p` for code.

## Why

"The Mac is faster" is no basis for a decision. You have to separate two things the
clock mixes together: the **cloud model** time (the same on any machine) and the **local**
time (starting Node, hooks, MCP servers, spawning processes). Only the second depends on
the machine.

Machines: Mac mini M2 Pro 16 GB · Windows 10, Intel i5-8600, 48 GB, Git Bash.

## Steps

### 1. Same code on both: `git bundle`

```
PS> git -C ..\orquestador bundle create <bundles>\orquestador.bundle --all
PS> git -C ..\enjambre    bundle create <bundles>\enjambre.bundle --all
PS> scp <bundles>\*.bundle mac:~/modelos/benchcmp/
PS> python scripts\bench_compare.py <bundles> <work> windows --claude
mac$ python scripts/bench_compare.py ~/modelos/benchcmp ~/modelos/benchcmp/work mac --claude
```

Both sides clone from the same bundle: same commits (orquestador `89ae2fd`, enjambre
`6a14b6e`), with no dependence on GitHub or the keychain.

### 2. Local tools (median of 3)

| Task | Windows | Mac | Mac faster by |
|---|---|---|---|
| orquestador tests: statusline | 15.40 s | 1.08 s | 14× |
| orquestador tests: hooks | 17.59 s | 1.03 s | 17× |
| enjambre tests (Python, 38) | 0.55 s | 0.35 s | 1.6× |
| `build-kit.sh` build | 16.98 s | 0.62 s | 27× |
| local git clone | 0.24 s | 0.03 s | 8× |
| git status ×20 | 1.42 s | 0.14 s | 10× |
| git gc --aggressive | 1.88 s | 0.85 s | 2.2× |

The huge gap is in the **bash** scripts; in plain Python it is barely 1.6×. A hypothesis
consistent with that (not measured separately): on Windows every `jq`, `sed` or `date` goes through
MSYS's `fork` emulation, which is expensive, and these tests launch hundreds of processes.

### 3. `claude -p`: the cloud on one side, local on the other

The JSON from `claude -p --output-format json` includes `duration_api_ms`. Local = clock − API:

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

| | Clock | Cloud | Local | Cost |
|---|---|---|---|---|
| Windows | 45.4 s | 15.0 s | **30.4 s** | US$ 0.28 |
| Mac | 15.1 s | 11.1 s | **4.0 s** | US$ 0.18 |

Is it the machine or the configuration? Windows has 13 MCP servers and the Mac 8. With a trivial
prompt, 2 runs per cell:

| Local startup | Windows | Mac |
|---|---|---|
| Normal | 18.2 / 18.7 s | 4.1 / 3.6 s |
| No MCP (`--strict-mcp-config`) | 13.6 / 12.9 s | 1.8 / 1.6 s |

The MCPs add ~5 s on Windows; **the rest (~13 s against ~1.7 s) is the platform**.

### 4. Local code model vs `claude -p`

Three functions with hidden tests that neither side sees: amounts in soles, merging intervals,
RUC check digit. The local model gets one attempt and **one retry** with the test output;
`claude -p` is an agent that can write and run its own tests.

```
PS> python scripts/bench_code.py            # local sin razonamiento + claude -p
PS> python scripts/bench_code.py --think --skip-claude
```

| Engine | Passed | Time | Cost |
|---|---|---|---|
| `claude -p` (Opus 5.5) | **3/3** | 132 s | US$ 0.91 |
| Local Qwen3.5-9B | 1/3 | 155 s | US$ 0 |
| Local Qwen3.5-9B with *thinking* | 1/3 | 751 s | US$ 0 |

No sugarcoating: for code with exact rules, the local 9B **does not replace** `claude -p`.
It got `merge_intervals` right on the first try; it failed the thousands grouping and the check
digit even with a retry. Reasoning more made it 5× slower without getting more right.

## What actually went wrong

1. **`build-kit.sh` does not run on the Mac**: BSD `sed` rejects line 55
   (`RE error: parentheses not balanced`, a `\)` with no `\(`). It was measured with GNU sed
   (`brew install gnu-sed`); the bug was noted to be fixed in orquestador.
2. **"2 failures on Windows" that were not Windows's fault**: two statusline tests look up the session
   by directory and the test clone lived at an odd temporary path. On the normal checkout
   of the same commit: 50/0.
3. **`claude -p` from Python on Windows received half the prompt**: `claude` is a `.cmd` and
   `cmd.exe` cuts the arguments at the first line break. Fix: the prompt goes through stdin.
4. **Deleting the test clone failed on Windows**: git *packs* are read-only.
   Fix: remove the attribute and retry inside `rmtree`.

## How to verify

```
PS> python scripts\bench_compare.py <bundles> <work> windows --claude
mac$ python scripts/bench_compare.py ~/modelos/benchcmp ~/modelos/benchcmp/work mac --claude
```

Expected (medians of 3, step 2): statusline tests 15.40 s on Windows against 1.08 s on the
Mac; `build-kit.sh` 16.98 s against 0.62 s; enjambre tests (Python) 0.55 s against 0.35 s.
`claude -p` with the real job: local 30.4 s on Windows against 4.0 s on the Mac. Both sides
clone from the same bundle (orquestador `89ae2fd`, enjambre `6a14b6e`). With a small n, the numbers
are indicative only.

## Limits

Small n (1 run of the real job per machine). Outside load: Photos indexing on the Mac,
a browser and another Claude session on Windows. The times are a ceiling, not an average.

## Self-check

1. Why is `duration_api_ms` subtracted from the clock and not the other way round?
2. If you remove all the MCPs, how much of the Windows–Mac gap is left? What does that tell you?
3. What kind of task would you give the local model today, and which one never?
