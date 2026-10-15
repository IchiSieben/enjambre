---
lesson:
  route: build-stack
  order: 23
  level: 3
lang: en
---

# 24 — Viewer v3, web copy and task inbox

## Goal

A local viewer that always shows two costs and keeps a history that never gets pruned; a redacted
web copy of the viewer; and an inbox for leaving tasks from a phone. Both were tested locally
first and deployed on 2026-10-07 (section E).

**Who does it:** Claude Code (2026-10-07). You: the phone test
(NEEDS-OWNER §19, item 2).

**Status, so measured is not confused with assumed:**

| Part | Status |
|---|---|
| Viewer v2/v3 local | done and in use on `win` |
| Web copy (`visor.ichisieben.dev`) | built and tested against local PHP; **deployed on 2026-10-07** (section E) |
| Inbox | tested end to end locally with the real Mac; **deployed on 2026-10-07**, inbox round `ok`; the phone test is still missing |

## Why: pull, redact with an allowlist, sign

Three ideas, one per part:

- **Pull instead of push.** Hostinger cannot call home; the PC asks every few minutes. Concept
  note: [Pull vs push](../conceptos/pull-vs-push.md).
- **Allowlist.** What goes out to the internet is not "everything except the secrets" but "only
  what was copied field by field". Concept note: [Allowed vs forbidden](../conceptos/permitidos-vs-prohibidos.md).
- **Signature bound to purpose.** Every request carries an HMAC that is only valid for one
  endpoint. The full inbox design is in [BANDEJA.md](../BANDEJA.md); it is not repeated here.

## Steps

Sections A) to E) are the steps, in the order they were done.

## A) Viewer v2/v3 (local)

### Design

- The owner's portfolio-site design system: dark by default, Space Grotesk and JetBrains Mono
  embedded as `data:` URIs. No external dependencies, so it serves under a strict CSP and with
  no network.
- Hierarchy from top to bottom: **live → usage → projects → Mac models → history**.
- Always two costs, never just one:
  - **API equivalent:** a floor, not a bill. List price × tokens, including cache writes
    (5 min = 1.25× the input price; 1 hour = 2×). It is marked as *estimated*.
  - **Quota:** the % of the subscription (5 h and 7 d windows).

### Own history

Each node has its own SQLite database at `~/.enjambre/visor/history.db` (tables `files`, `msgs`,
`sessions`, `meta`). It is **never pruned**: when Claude Code deletes the old `.jsonl` files, the
history is still there. Rules:

- The earlier `history.json` is imported only once.
- The `~/.claude.bak-*` copies are read as extra roots. If the same session is in two roots, it
  counts once: the longest main transcript wins, and replies are deduplicated by message id.

Real result on `win`:

| Measure | Value |
|---|---|
| Transcripts read | 498 |
| Sessions | 149 |
| Range | 2026-08-13 → 2026-10-07 |
| Time per pass | 2 s |
| Sessions that only existed in the backup copy | 31 |
| Reply rows / distinct ids | 30 902 / 29 305 |

### Traffic lights

The thresholds come from `~/.claude/rules/thresholds.json`, not from remembered numbers:

| Traffic light | Amber | Red |
|---|---|---|
| `visor-disk` | 80 | 90 |
| `visor-mem` | 80 | 92 |
| `visor-quota-5h` | 70 (`visor-quota-5h-amber`) | 90 (`quota-5h`) |
| `visor-quota-7d` | 70 | 90 |
| failed / incomplete jobs | — | any > 0 |

They carry `hint:false`: the status line ignores them (unknown metric = `-1`, it never fires).
If the file cannot be read, the traffic light turns **gray with "no threshold"**; never an
invented green.

### From Windows to the Mac: which session sent the job

`enjambre run` stores `origin_session` (from the `CLAUDE_CODE_SESSION_ID` variable) in the ledger
and in the queue. Jobs dispatched by `watch` get `ENJAMBRE_ORIGIN_SESSION` from `job.json`, so
they do not inherit the session of `watch` itself.

### Icons and charts

- Technology icons: from the portfolio sprite (`public/icons/skills.svg`, Simple Icons, CC0),
  matched by folder name, the card's repo name, or an alias in `config/visor-web.toml`.
- Charts: hand-made stacked SVG bars, per day and per machine. A single delegated tooltip
  (hover, focus and touch). No library.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*
*Before and after at 390 px wide, redacted with the `--demo` allowlist.*

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## B) Web copy at `visor.ichisieben.dev` (built locally; deployed in E)

It was built and tested locally before anything was uploaded. The deployment was done on
2026-10-07 with the owner's explicit OK (section E).

### Two walls against leaks

1. **Allowlist** (`web.public_snapshot`): every field is copied explicitly. What is not written
   there does not go out.
   - Project names → stable aliases `proyecto-xxxxx`, by HMAC with a random local salt.
   - Disks → `disco-N`; mounts → `recurso-N`; tmux, only a count.
2. **`leak_check`**: a second wall that **blocks the upload** if it finds restricted words,
   path-shaped strings, IPv4 addresses, or private names (those from `ssh -G` and the local user).

### Upload and access

- The upload is a signed POST every 10 min from the viewer task that already existed.
  Signature: HMAC-SHA256 over `purpose\nts\nbody`, with `ts` in epoch milliseconds, a ±300 s
  window, and monotonic per purpose: a replay gets 409.
- The page is protected with Basic auth (`.htaccess`), `noindex` headers and a `robots.txt` with
  `Disallow`.
- `data.php` **fails closed** and uses `REMOTE_USER`, never `PHP_AUTH_USER`: a client can forge
  the latter if the `.htaccess` were not applied.

### Packaging

```
PS> python tools/web/build_dist.py
```

Output: `state/web-dist/visor-app` and `visor-app.zip`, **9 files**. `index.html` carries no data.

## C) Inbox: from the phone to the Mac

Summary of the path; the contract tables, signatures and states are in
[BANDEJA.md](../BANDEJA.md).

1. The form saves a signed task **outside the docroot**.
2. Inside `watch`, the PC asks every 2 min with a signed GET (`poll`).
3. The PC verifies the HMAC **over the stored bytes, before parsing**.
4. It applies **all** the rules again, without trusting the PHP:
   - closed list of repos;
   - a `kind` that also exists in `routing.toml`;
   - list of workers;
   - path regex, no `..`;
   - the restricted words, structurally in paths, and with a word boundary in free text (so
     "análisis" passes);
   - daily limit by Lima day.
5. It writes `TASK-<id>.md` and enqueues in enjambre. **Never a shell**: the only argv is
   `enjambre.py run --repo <from the list> --task <generated path>`.
6. The same `worker.sh` with the same `--allowedTools`, `--max-turns 8` through
   `ENJAMBRE_MAX_TURNS`, no bypass and **no retries** (the task is marked seen *before*
   enqueueing).
7. States `recibida / corriendo / hecha / fallida`, with branch and a 500-character summary that
   goes through the same `leak_check`.

### Tests

- **14 unit tests:** valid and invalid signature (including a tampered body), repo outside the
  list, sensitive paths, a duplicate that is never enqueued twice, the daily limit and its reset
  the next day, Mac down (the task stays in `queue/`), router rejection, progress up to `hecha`.
- **4 integration tests** against the real PHP under `php -S`: fails closed with no user or
  signature, replay 409, wrong purpose 401, outside the window 401, form → PC → back, and
  upload + data read.

### End to end, locally

Form → PC → Mac with the `stub-ok` worker ($0) → state **"hecha"**. Branch
`mac/b20261007-653a883f` pushed, summary "stub ok", about 62 s in total.

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

## What actually went wrong (what failed)

1. **The antivirus deleted the test router.** The test `php -S` used a router script that did a
   `require` of a path derived from the request; the antivirus took it for a webshell, deleted it
   and killed `php -S` halfway through the test. Fix: `-d auto_prepend_file` with a one-line file
   that copies a test header into `REMOTE_USER`, written in a temporary directory.
2. **The PC client accepted any HTTP 200**, including a PHP error page. Now it requires
   `{"ok":true}`.
3. **IPv4 false positive.** The second wall blocked the upload because the SVG path of the macOS
   logo contains `2.774.6.926.466`, which looks like an IPv4 address. Fix: the operating-system
   icons moved to the HTML template, not the data. The wall did its job; the data was in the
   wrong place.
4. **Portable PHP without `mbstring`.** A fallback `cut()` with `preg` was added.

## C2) Second pass on the viewer

**Goal:** make the viewer talk about devices and not ids, count spending by week and by month,
and let every job, model and subagent be opened and filtered.

**Why this way:** a viewer that says `win` and `mac` forces you to know how the swarm is
configured; one that says "PC" and "Mac mini" can be understood without that. Almost all the data
already existed (the ledger, `measurements.jsonl`, the subagents' `.meta.json`); what was missing
was reading them in full instead of keeping only the last one.

1. **Device labels** in `nodes.toml`:

   ```toml
   [nodes.win]
   label = "PC"
   [nodes.mac]
   label = "Mac mini"
   # [nodes.laptop]     # cuando entre: label = "Laptop"
   ```

2. **Official full-color logos** (devicon, MIT), vendored and cleaned by
   `tools/visor/icons/vendor_devicon.py`. Where there is no official version, or it gets lost in
   the dark theme, the Landing's brand-color glyph stays. The interaction is the one from the
   Landing cards: color glow, a logo that tilts, and a tooltip saying what each technology is.

   ```text
   $ python -I tools/visor/icons/vendor_devicon.py
   ok     python             2345 bytes
   ok     githubactions      2797 bytes
   skip duckdb           HTTPError          <- devicon no lo tiene: queda el glifo de marca
   ok     linux            198906 bytes     <- Tux: demasiado pesado, se descartó a mano
   ```
3. **Usage**: today, then 7 days, then the whole history, each with its daily average. Plus a
   table and a chart per ISO week and per month.

   *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

4. **Local models**: every benchmark measurement with date and time, and the manager's jobs, with
   a filter by task and by date. The `modelos-mac` benchmark now stores the time (`ts`) in every
   new row. For an old row, the viewer uses the time of its result file only if there is one that
   matches, and marks it with `~`.

   *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

5. **History**: all the ledger jobs and each device's long jobs, filterable by project. Tapping
   one opens its detail: state, device, branch, cost, route reason, result and log. A long job
   (`jobrun.py`) has a **real heartbeat**: its supervisor rewrites `status.json` on every beat. A
   short one shows the **last signal**, derived from the time of the newest file in its queue
   folder.

   *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

6. **Per project**: threads (conversations), subagents (type and description, read from each
   one's `.meta.json`) and swarm jobs. Tapping a row marks it on the timeline.

   *(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

7. **PWA prepared and switched off**: `tools/web/pwa/` (manifest and icons) and `[web] pwa = false`.
   With `true`, `build_dist.py` includes them and links the manifest.

Install the new collector on the Mac and update `modelos-mac` there:

```text
PS> scp tools/visor/collect.py mac:.enjambre/visor/collect.py
$ ssh mac 'launchctl kickstart gui/$(id -u)/dev.enjambre.visor'
        last exit code = 0
$ ssh mac '~/.enjambre/tmux-run.sh -C ~/Documents/Portfolio/modelos-mac -t 150 -- git pull --ff-only'
   73fc382..eac80df  main       -> origin/main
```

Measured result: 44 measurements on the Mac; the web copy passes the wall and weighs 136 KB
(before 64 KB; 45 KB are subagents). No JavaScript errors, no horizontal scroll at 390 px and no
stray `win`/`mac` in the text (checked with Playwright).

**What failed:**

- The first walkthrough showed "Mac" instead of "Mac mini". `gather()` copied only some keys of
  each node and `label` was not among them. A test covers it now.
- The first version of the job list left out the `stub-ok` jobs for being "test" jobs, and that
  made the very test of the inbox from the phone disappear. Now all of them are included.
- The old benchmark rows only had a date. The time was not invented: "time not recorded" when
  there is more than one candidate file.
- The first version painted the logos in a single color, like the Landing, and the request was
  "the official SVGs, not monochrome". The official ones were vendored. Astro and three.js went
  back to the brand glyph because they are black and cannot be seen in the dark theme.
- The check for stray ids read the page before opening the details and so did not see the route
  reason ("tests -> mac: ..."). Now it opens each job and also checks the tooltip.

## D) Threat model on one page

| Who | What they have | Can | Cannot |
|---|---|---|---|
| Someone with only the URL | Nothing | See a login prompt; `robots` and `noindex` keep it out of search engines | Read data or tasks |
| Whoever has the Basic password | Access to the web | Read the redacted snapshot (aliases, counts, costs; no paths, names or IPs). Create tasks, but only contracts for listed repos, kinds and workers, with the PC's daily cap, with restricted tools, on a branch and **with no automatic merge** | Run shell commands, touch paths of restricted repos, reach the home network |
| Whoever has the inbox token (HMAC key) | The `bandeja_key` key | Forge tasks with a valid signature; mark task states | Go outside the closed lists or the PC's daily limit; skip `worker.sh`. Mitigation: rotate the key in `config.php` and in the Windows environment variable |
| Whoever has the upload key | The `ingest_key` key | Overwrite the displayed snapshot (disinformation) | Create tasks: there is a different key per purpose |
| Whoever compromises the Hostinger account | Everything above | Everything above | Get into the home: the PC only asks, nothing listens on the home network |

**What the signature proves:** that the task was written by the PHP that holds the key. **What it
does not prove:** that you wrote it. That is why the real rules run on the PC.

## E) Part 2: the viewer on the internet (2026-10-07)

**Goal:** `https://visor.ichisieben.dev` behind a password, with the inbox open to the phone.

**Why in this order:** first the code is uploaded and checked to **fail closed** (with no password
or `config.php`, everything answers 401 or 503); only then are the secrets put in. If a step goes
wrong, what stays exposed is a page that does not respond, not an open viewer.

1. **Subdomain** with the Hostinger API (`hosting_domains_create-website-subdomain`,
   `subdomain: visor`, `directory: visor-app`). It ends up in `public_html/visor-app`, next to
   `radar-app` and `metal-app`. The site's PHP: 8.3.
2. **File-by-file upload** (TUS, `hosting_files_generate-upload-url`), only inside `visor-app/`.
   The API's "by zip" deploy **replaces the parent site**, which here is the Landing: that is why
   it is not used.

   ```text
   $ python tools/web/build_dist.py && python tus_up.py
   ok  201/204    1455 .htaccess
   ok  201/204     194 api/.htaccess
   ...
   ok  201/204     766 pwa/manifest.webmanifest
   ```
3. **Verification matrix** without credentials:

   ```text
   https://visor.ichisieben.dev/                       401
   https://visor.ichisieben.dev/data.php               401
   https://visor.ichisieben.dev/lib.inc.php            403
   https://visor.ichisieben.dev/api/tareas.php         503   {"error":"sin configurar"}
   https://visor.ichisieben.dev/pwa/manifest.webmanifest 200 application/manifest+json
   https://ichisieben.dev/visor-app/                   401   (la ruta por el dominio padre también)
   http://visor.ichisieben.dev/                        301   (lo hace Hostinger antes que el .htaccess)
   ```
4. **Keys:** a script generates them, stores them as Windows user environment variables and
   writes `config.php` in a local folder outside the repo. No value is printed or passed through
   a command.
5. **Private folder, password and `config.php`, all through the API** (the owner's change of plan:
   no hPanel). The API does not write outside `public_html`, so the folder is
   `public_html/visor-private`, closed with its own `.htaccess`. The order matters: first that
   `.htaccess` and the 403 check; only then the secrets.

   ```text
   1. visor-private/.htaccess
     ok  201/204    222 visor-private/.htaccess
     ok   https://ichisieben.dev/visor-private/.htaccess             403
   2. config.php
     ok  201/204    379 visor-private/config.php
     ok   https://ichisieben.dev/visor-private/config.php            403
   4. .htpasswd (user ichi, bcrypt $2y$) + visor-app/.htaccess
   ```

   The password (20 random characters) is generated in memory, turned into bcrypt with PHP's
   `password_hash` and shown only once to the owner. The `AuthUserFile` path comes from the
   `root_directory` that the API returns, not from a guess.
6. **Final matrix:**

   ```text
   / con contraseña                    200
   / sin contraseña                    401
   / contraseña equivocada             401
   visor-private/.htpasswd             403
   visor-private/config.php            403
   data.php con contraseña             200   (144 884 B, después de la primera subida firmada)
   ```
7. **Signed test** with the keys read from the registry: `build.py` → `web: ok 144884 B`;
   `bandeja.py --once` → `{"bandeja": "ok", ...}` and the inbox page already shows the allowed
   list (`enjambre-sandbox`, `stub-ok`).

Later note: the private folder was moved to `domains/ichisieben.dev/private/visor`, outside
`public_html`, because a Landing deploy would wipe `public_html/visor-private` (DECISIONES 224).

**What failed:**
- The manifest returned **401**: a `FilesMatch` with `Require all granted` inside the root
  `.htaccess` does not beat `Require valid-user` on LiteSpeed. It was moved to `pwa/` with its
  own `.htaccess` (`Satisfy Any`), the same trick `api/` already used, and it went to 200.
- The files API does not let you leave `public_html` (`..` → 422).
- **The correct password gave 401.** First the CDN cache was ruled out (random `?cb=`, still
  401). Then test users with five hash formats: all five worked, bcrypt included. The mistake
  was ours: on Windows, `subprocess` with `text=True` turns the `\n` sent through stdin into
  `\r\n`, and the hash had been made of the password plus a `\r`. Fix: send bytes, with no
  newline.
- **The upload token stopped being valid before its `exp`** (403 on everything, even a harmless
  file). A new one was requested; it is best to request it right before each batch.
- **Python said «certificate has expired»** with a certificate issued today. It happens with all
  subdomains from this PC: the Windows store makes OpenSSL build a chain with an expired
  certificate. With the `certifi` bundle it verifies fine; that one is used and verification
  stays on.

## F) Pending for the owner

The restart of `watch` is done (2026-10-10, pid 46420). The phone test is still missing:
NEEDS-OWNER §19, item 2.

## How to verify

```
PS> python tools/web/build_dist.py
```

Expected output: `state/web-dist/visor-app` and `visor-app.zip`, 9 files, with `index.html` carrying no data.

With the viewer deployed, the credential-free matrix from section E must give:

```text
https://visor.ichisieben.dev/                       401
https://visor.ichisieben.dev/data.php               401
https://visor.ichisieben.dev/lib.inc.php            403
https://visor.ichisieben.dev/api/tareas.php         503   (antes de poner config.php)
```

With the password, `/` gives 200 and `data.php` gives 200. Signed test: `build.py` prints `web: ok <bytes> B` and `bandeja.py --once` prints `{"bandeja": "ok", ...}`.

## Self-check

1. Why does the web copy use an allowlist and not a blocklist?
   Answer: what goes out to the internet is only what was copied field by field; what is not written does not go out. `leak_check` is a second wall that blocks the upload.
2. Why does the PC verify the signature before parsing and apply all the rules again?
   Answer: the signature proves that the task was written by the PHP that holds the key, not that you wrote it; that is why the real rules run on the PC.
3. Why are the files uploaded one by one and not by zip?
   Answer: the API's zip deploy replaces the parent site, which here is the Landing.
