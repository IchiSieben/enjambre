---
lesson:
  route: build-stack
  order: 31
  level: 2
lang: en
---

# 32 — Blind start: jobs that were "launched" but never existed

## Goal

No job should stay in `unknown` forever. If a job cannot run, it must fail on the spot and say why.
If it dies before starting, it has to leave a state and a reason on disk.
**Who does it:** Claude Code (session V2b of 2026-10-15, prompt `PROMPT-ecosistema-claude-code.md` §10).
**Before:** [31 — Viewer polish](31-pulido-visor.md).

## Why

In the early hours of October 15, four windows sent work to the Mac. Three jobs (`cert-media`,
`kit-media` and `eco-gates-2`) ended up like this:

```json
{"id": "cert-media", "state": "unknown", "alive": false, "launched": true}
```

There was no log, no heartbeats and no line in the ledger. As far as the PC was concerned, the job
had been "launched". As far as the Mac was concerned, it never started. A distributed system that
cannot tell "did not start" from "is running" lies exactly when nobody is looking, and in sleep
mode nobody is looking.

## Steps

Sections 1 to 4 are the steps: the cause with evidence, the preflight, `clone` and "never
`unknown` again". Section 5 relaunches what was left over.

## 1. The cause, with evidence (not with intuition)

**First, cloning was attempted with an agent.** So that the Mac would have `claude-cert-studio` and
`claude-kit`, three `tests` jobs were sent to the `enjambre-sandbox` repo. All three failed, each
for a different reason. They are in `state/jobs/clone-*/log*` (Mac paths redacted to `~`):

| Job | What the agent asked for | What the system answered |
|---|---|---|
| `clone-cert-studio` | `for r in portfolio-landing:Landing ...; do d=$HOME/Documents/Portfolio/${r#*:}; ...` | `A variable in braces in this command can't be checked before it runs` (permission denied) |
| `clone-cert-studio-2` | `bash tools/clone-repos.sh` | `This command requires approval` (it was not in `allowedTools`) |
| `clone-cert-studio-3`, `clone-claude-kit` | `bash tools/clone-repos.sh` | `Exit code 127` · `bash: tools/clone-repos.sh: No such file or directory` (the script was never committed in the sandbox) |

Cloning is a deterministic operation. Asking a model to do it inside a permissions sandbox is using
the wrong layer: the rule "does a model have to DO this, or only ORCHESTRATE it?" said so from the
start.

**Then the media jobs were launched anyway.** `python enjambre.py logs cert-media` (the new command
from section 4) brought back the node's `supervisor.out`:

```text
  File "~/.enjambre/jobrun.py", line 248, in ensure_worktree
    fetched = git(["fetch", "-q", "origin"], src, log) == 0
  File "~/.enjambre/jobrun.py", line 236, in git
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
FileNotFoundError: [Errno 2] No such file or directory: '~/Documents/Portfolio/claude-cert-studio'
```

`subprocess.run` with a `cwd` that does not exist raises an exception before git runs. The
supervisor died in its first second, before writing `status.json`. `jobrun.py status` sees the
`spec.json` and sees no state, so it answers `unknown, launched: true`. Meanwhile, `launch` had
already replied "launched (not confirmed within 10 s)", and the PC had written `job.json`.

The third one, `eco-gates-2`, received `--repo C:\Users\...\wt\landing-ecosistema`. On the PC,
`PORTFOLIO / "C:\\..."` **is** that absolute path (that is how `pathlib` treats absolute paths), and
that is why the local contract passed. On the Mac the result was `~/Documents/Portfolio/C:\Users\...`.
There was a fourth case from the same family: the zombie `sync-branch-20261007-135456`, with
`repo: "C:/Users/.../<otro-repo>"`, had been dead for a week and `watch` noted it every minute.

## 2. Preflight: fail before launching

- `--repo` accepts only a **name** (`[A-Za-z0-9][A-Za-z0-9._-]*`, no `..`). Any path, Windows or
  POSIX, is rejected in `run` and in `script`. The starting branch is chosen with
  `--ref <rama>`: the worktree is born from `origin/<rama>`. If the fetch fails, the job fails; it
  never falls back silently to `HEAD`.
- Before launching, `run` uses the same ssh as `presence` (`test -d ~/Documents/Portfolio/<repo>/.git`):

```text
$ python enjambre.py run --repo claude-kit --task ... --node mac --detach
enjambre: claude-kit is not cloned on mac (~/Documents/Portfolio/claude-kit/.git missing). Fix: python enjambre.py clone --node mac claude-kit
```

  It exits with exit 3, with nothing in `state/jobs/` or in the ledger.
- `jobrun.py launch` repeats the check on the node. This is needed because a queued job is launched
  by `watch` hours later, and that path does not go through the PC's preflight.

## 3. `enjambre clone`: the right layer

```text
$ python enjambre.py clone --node mac claude-cert-studio
{"id": "clone-claude-cert-studio-20261015-083731", "worker": "clone", "status": "ok", "result": "cloned", "duration_s": 3, ...}
$ python enjambre.py clone --node mac claude-kit
{... "result": "cloned" ...}
$ python enjambre.py clone --node mac claude-kit
{... "result": "exists" ...}
```

It runs with no agent, no TASK and no script in the sandbox, in three seconds and at US$0. `git clone`
goes through `tmux-run.sh`: the window is born in the graphical session, so git reads the keychain,
just like the worker (tutorial 30). It is idempotent: if the repo is already there, it says
`exists`. It leaves one line in the ledger with `worker: clone`.

## 4. Never `unknown` again

- **Node, `jobrun.py supervise`:** the first thing it does is write `{state: "starting"}` and a line
  in `supervisor.log`. All the work runs inside a `try`: any exception ends in `failed`, with
  `reason`, `rc` and the tail of the traceback. `launch` returns that reason on the spot.
- **Node, `worker.sh`** (synchronous path): it writes `starting` and the first line of `worker.log`,
  copies stderr into the log, and a `trap EXIT` turns any early exit into `failed` with its
  reason.
- **PC, `collect`:** if a `launched` job has no state (or has `starting`/`dead` with no child and
  no heartbeat) after 90 s, it becomes `dead-at-launch`. It is terminal: one line in the ledger,
  with the error the node itself left in `supervisor.out`, and it is marked as collected. A live
  supervisor is never declared dead, even if its `git fetch` takes two minutes.
- **`enjambre logs <id> [--tail N]`** brings `supervisor.out`, `supervisor.log`, `worker.log` and the
  tail of the stream into `state/jobs/<id>/node.log`.
- **`enjambre cerrar <id> --razon "..."`** manually closes a dead job that nobody is going to
  resume. That is how the two zombies were closed, with "cerrado manualmente 2026-10-15".

```text
$ python enjambre.py status
... objcap-build-20261008-033233 mac  script  failed  exit=2  [cerrado manualmente 2026-10-15]
... cert-media                   mac  claude  failed  exit=2  [dead-at-launch: FileNotFoundError: ... '~/Documents/Portfolio/claude-cert-studio']
... kit-media                    mac  claude  failed  exit=2  [dead-at-launch: FileNotFoundError: ... '~/Documents/Portfolio/claude-kit']
... eco-gates-2                  mac  claude  failed  exit=2  [dead-at-launch: FileNotFoundError: ... 'C:\\Users\\...\\wt\\landing-ecosistema']
```

## How to verify

```bash
python -m unittest tests.test_arranque_ciego -v   # 26 pruebas: nombre/ruta, preflight, clone, starting, trap, dead-at-launch, cerrar, carrera
python -m unittest discover -s tests              # la suite completa
```

The supervisor that dies before starting is simulated: `ensure_worktree` raises
`FileNotFoundError`, just like on October 15. The test checks that the state was already `starting`
before the failure, and that afterwards it is `failed`, with a reason and without the home path.

## 5. Relaunching what was left over

With the clones done, the media jobs were relaunched with new ids (`cert-media-2`, `kit-media-2`).
The id was not reused: doing so rewrites an already-closed `job.json` and leaves two lines with the
same id. The Lighthouse run of `cert.ichisieben.dev` (`cert-lh`) was the first real use of `--ref`:
the lane lives on the `enjambre/cert-lh` branch of the Landing, because the Landing's `main` deploys
itself. The results are in the HANDOFF, section "arranque ciego".

## What actually went wrong

1. **The three agent clones** (table in section 1): it was a layer mistake, not a permissions one.
2. **My own tests wrote to the real ledger.** `append_ledger(entry, ledger=LEDGER)` fixes the path
   when the function is defined, so the tests' `E.LEDGER = tmp` never reached it. Three
   `clone-proj-*` lines landed in `jobs.jsonl`. They were removed, with a backup in `state/`. Now
   the path is read at call time, and each test checks that the real ledger did not gain rows from
   the test repo.
3. **A race with the `watch` that was already running.** `cerrar sync-branch` coincided with the ssh
   of a `collect` round from the resident `watch`, which was running old code. The `watch` rewrote
   its old copy of `job.json` and erased `collected`. The next `collect` closed the job a second
   time. Now `collect` re-reads `job.json` after the ssh and skips whatever is already collected.
   The duplicate line was removed, with a backup. For the `watch` to use all of this, it has to be
   restarted (NEEDS-OWNER §29).
4. **A `heredoc` ate the backslashes.** When editing with `python - <<'EOF'` from Git Bash, `\\n`
   arrived as a real line break and `\\b` as a backspace character. The parser caught it in the
   `.py` files. In `largos.py`, the backspace inside a regex went unnoticed until a test failed.
   Now, after every edit, the touched files are searched for any control character.

## Self-check

1. Why does `PORTFOLIO / "C:\\x"` not give `PORTFOLIO\C:\x` in `pathlib`, and what rule prevents it in enjambre?
2. A supervisor has been in `starting` for 3 minutes with its pid alive. Is it `dead-at-launch`? Why not?
3. Why does `jobrun.py launch` repeat the preflight that the PC already does?
4. What is wrong with relaunching an already-closed job under the same id?
5. Which principle did the three agent clones break?

<details><summary>Answers</summary>

1. Joining an absolute path discards everything before it. `check_repo_name` accepts only names.
2. No. As long as the pid is alive, the supervisor is working (`clone-landing` spent more than 120 s
   in git). It only counts as dead with no live pid, no child and no heartbeat.
3. Queued jobs are launched by `watch` hours later, through `launch_queued`, without going through `run`.
4. It rewrites a terminal `job.json` and leaves two lines with the same id. It breaks the
   one-line-per-job contract.
5. What is deterministic goes in code, not in a model (layer before lane).

</details>
