---
lesson:
  route: build-stack
  order: 6
  level: 2
lang: en
---

# 05 — Verified backup: freeing the Mac without losing a single photo

## Goal

Move photos, videos and backups from the Mac to the external disk with **a single copy of each file**
(even if on the Mac it is repeated two or three times), check every copy by sha256 and only then
send the original to the Trash. All through `ssh mac`, without `sudo`, without opening any photo: the work is
done with names, sizes and hashes.

## Why

- **A hash is the fingerprint of the content.** sha256 changes if a single bit changes. Comparing the hash
  of the original with that of the copy proves they are identical; comparing sizes does not.
- **The hash also finds duplicates.** Two files with the same sha256 are the same file,
  whatever they are called. `IMG_1234.HEIC` and `IMG_1234 (1).HEIC` transferred twice from the iPhone
  give the same hash: keeping one is enough.
- **Trash, not `rm`.** Moving to the Trash is reversible until the owner empties it. That is why
  "space freed" and "space in the Trash" are two different numbers: the first does not move
  until it is emptied.
- **The 3-2-1 rule.** Three copies, two media, one off-site. The external disk is the second
  copy; the Windows `F:` disk (step 7) is the third. None is off-site yet.

## Background: the first pass (found on the disk)

An earlier session had already run `_backup_media.py` (it is on the disk itself, in
`Backup-Mac/Fotos-AirDrop/`). What it left, read from its files and not reconstructed:

```text
$ tail -3 Fotos-AirDrop/_run.log
TOTAL 2048 files, 108.22 GB; mismatches: 0
duplicate groups: 72, redundant copies: 640, 0.27 GB
EXIT=0

$ cut -d, -f5 MANIFEST.csv | sort | uniq -c
1478                      <- verificados por sha256, original aún en la Mac
 570 eliminado-vacio      <- archivos de 0 bytes: copias del Finder interrumpidas
```

The 570 empty ones carried the Finder's `brokMACS` mark (a copy that was cut off); they had already been
sent to the Trash and are listed in `Backup-Mac/_vacios-eliminados.txt`.

**Trap avoided:** that script has a `verify` subcommand that **rewrites** `MANIFEST.csv` with
4 columns. Running it again would have erased the `estado` column and the 570 rows. That is why this
pass uses a new script, `tools/respaldo.py`, which only **appends** rows, and a copy was saved
beforehand: `MANIFEST.antes-de-2026-10-04-noche.csv`.

## Step 1 — Inventory: what takes up the Mac

```bash
ssh mac 'df -h /System/Volumes/Data; cd ~; du -xsh .[!.]* * 2>/dev/null | sort -rh | head'
```

```text
/dev/disk3s5   460Gi   383Gi    51Gi    89%   /System/Volumes/Data
180G  Pictures      109G  Downloads    33G  Library     22G  modelos
 21G  Desktop        15G  Movies      3.8G  .Trash     1.9G  Documents
```

Breakdown by type:

| Type | Where | GB | What is done |
|---|---|---|---|
| Photos and videos | `Downloads` (AirDrop), `Pictures/<respaldo iPhone 2024>`, `Pictures/icloud backup`, `Desktop`, `Movies`, `Documents` (loose) | ~230 | Backed up and sent to the Trash |
| Photos library | `Pictures/Photos Library.photoslibrary` | 103 | **Not touched** (see below) |
| Caches | `Library/Application Support/Claude/vm_bundles`, `Library/Caches`, iCloud photo library | ~25 | List with recommendation |
| Local models | `~/modelos` (llm, vlm, img, stt…) | 22 | Not moved: the node uses them |
| Test leftovers | `audios-prueba`, `renders`, `.local`, `.cache` | <1 | Negligible |
| Installers | `.dmg` in Downloads and Desktop | ~8 | List with recommendation |

**Why the Photos library is not touched:** a `.photoslibrary` is a *package* (a folder that
macOS shows as a single file) with a database that points to every original. Taking files out of
it corrupts it. The same goes for iMovie and TV libraries, DaVinci's `CacheClip`,
`Lightroom` and `Photo Booth Library`: the script skips them whole.

**Where photos arriving from the iPhone land:** everything changed in the last 30
minutes was searched (`find ~ -xdev -cmin -30 -type f`). They landed in `~/Downloads`; inside the photo library only
the iCloud sync cache was moving.

## Step 2 — Scan and hash, ignoring what is still being copied

```bash
scp tools/respaldo.py mac:.enjambre/respaldo/
ssh mac 'cd ~/.enjambre/respaldo && tmux new -d -s respaldo \
  "caffeinate -is python3 respaldo.py scan > scan.log 2>&1; echo EXIT=\$? >> scan.log"'
```

- `tmux`: if the SSH drops, the job keeps going.
- `caffeinate -is`: neither the Mac nor the USB disk goes to sleep midway.

The scan skips:

- files of **0 bytes**;
- files with `mtime` **or `ctime`** less than 15 minutes old. `ctime` matters: AirDrop and the
  Finder keep the file's original date, so `mtime` does not give away a copy that just
  arrived, but `ctime` (when the inode changed) does;
- files with the Finder's `brokMACS` mark (copy in progress).

*(capture of the real setup: not published; see docs/CAPTURAS.md for the demo-mode ones)*

```text
candidates 10834 files 230.34 GB; unique 9787 222.17 GB; skipped 0
```

Inside the Mac there were already 8 GB of repeats (10 834 files, 9 787 distinct contents).

## Step 3 — Copy each hash only once, with rsync, and verify

```bash
ssh mac 'cd ~/.enjambre/respaldo && tmux new -d -s respaldo \
  "caffeinate -is python3 respaldo.py copy > copy.log 2>&1; echo EXIT=\$? >> copy.log"'
```

For each scanned file:

1. If its sha256 is already on the external disk → **it is not copied**; a row is added to the manifest that
   points to the copy that already exists (`estado` = `ya-en-externo` if it came from the first pass,
   `duplicado` if it was copied in this one).
2. If not → `rsync -t source destination`, the sha256 **of the copy** is computed and compared. If it does not
   match, it goes to `failures.tsv` and does not enter the manifest.

Where each thing ends up on the disk:

- Loose files from `Downloads` and `Desktop`: `Fotos-AirDrop/AAAA/AAAA-MM/`, same as the first pass.
- Everything else: **a mirror of the path** (`Backup-Mac/Pictures/...`). That way the `.AAE` files (edits
  the iPhone makes) stay next to their photo instead of being split apart by date.

**Why `-t` and not `-a`:** the disk is exFAT, which stores neither owners nor permissions; `-a` fails or warns
on every file. And copying extended attributes to exFAT creates the `._nombre` files (AppleDouble). The
`rsync` of macOS 27 is `openrsync` (protocol 29).

**What went wrong:** the first version of the scan used `os.getxattr`, which does not exist in the
Python that ships with macOS (3.9 from the Command Line Tools):

```text
AttributeError: module 'os' has no attribute 'getxattr'
```

It was replaced with the command `xattr -px com.apple.FinderInfo <archivo>`.

**Second stumble:** the copy and step 4 running at the same time on the same mechanical disk
got in each other's way: the copy dropped to ~6 MB/s because the head jumped from one file to another. Step 4
was paused (`kill -STOP <pid>`) and the copy rose to ~58 MB/s (`iostat -d -w 2 disk6`). A hard drive
performs well with a single sequential read or write at a time.

## Step 4 — Only what has an identical copy goes to the Trash

```bash
ssh mac 'cd ~/.enjambre/respaldo && tmux new -d -s papelera \
  "caffeinate -is python3 respaldo.py trash > trash.log 2>&1; echo EXIT=\$? >> trash.log"'
```

For each manifest row whose original is still on the Mac:

1. the sha256 of the original and that of the copy on the external disk are **recomputed** (the verification
   from step 3 is hours old: if anything changed since then, it shows here);
2. if both match the manifest → `/usr/bin/trash <archivo>`;
3. it is recorded in `Backup-Mac/_movidos-a-papelera.tsv` (time, source, bytes, sha256, copy).

`/usr/bin/trash` is the native macOS command; it was tested beforehand with a test file of our own. Unlike
`mv ~/.Trash/`, it leaves the files with "Put Back" available in the Finder.

It started with the 1 478 files (108 GB) that were already verified.

**What really happened.** With the copy and the Trash reading the same USB disk at the same time, the
copy dropped to ~6 MB/s (the head jumps between two zones of the disk). The Trash job was paused without
killing it, with `kill -STOP <pid>` (state `T` in `ps`), and resumed with `kill -CONT <pid>` when
the copy wrote `EXIT=0`:

```
copy done: 9356 rows; copied 8380 files 114.22 GB; repeated (not copied) 976 files 7.91 GB
EXIT=0
```

Before resuming, an independent sample: 200 random copies from the external disk, sha256 recomputed
against the manifest.

```
manifest rows 11404
unique dest 9858 dest with >1 sha 0
SAMPLE 200: ok 200 mismatch 0 missing 0 bytes 4.31 GB
```

The Trash job only processes the manifest rows it read at startup, so a **second pass**
(`scan → copy → trash`) is needed, chained in its own tmux so as not to compete for the
disk:

```bash
# ~/.enjambre/respaldo/pasada2.sh
while kill -0 <pid-papelera> 2>/dev/null; do sleep 30; done
for step in scan copy trash; do
  caffeinate -is python3 respaldo.py $step > ${step}2.log 2>&1
  echo EXIT=$? >> ${step}2.log
done
```

The first attempt, with the whole command in quotes inside `tmux new "..."`, lost the `$?`
(it came out as a literal `EXIT=\0`): three levels of quoting (PowerShell/Bash → ssh → tmux → sh). An
8-line script does not have that problem.

```
trash done: 1950 files 110.01 GB moved to Trash; refused 0        # 1a pasada
copy done: 0 rows; copied 0 files 0.00 GB                          # 2a: nada nuevo
trash done: 8878 files 120.15 GB moved to Trash; refused 0        # 2a pasada
```

In the end `~/.Trash` weighed 7.6 GB, not 230: the owner emptied it by hand (~00:52) while the second
pass was still running; only the ~25 files moved afterwards were left. The scripts never
empty it, they only call `/usr/bin/trash`. Every file was verified twice against the external disk before
being moved, so nothing was lost, but there is no "Put Back" any more: until the third
copy on Windows is finished, the external disk is the only copy.

## Step 5 — What is neither photo nor video

None of this was moved. See the table in the final report.

## Step 6 — Third copy on Windows

Windows has no `sshd`, so the Mac cannot push to `F:`; Windows pulls from the Mac with
`tools/espejo-windows.sh` (`tar` over SSH, and then sha256 of every file against the manifest).
So that it does not depend on the Claude Code window, it is launched with WMI, which hangs it from `WmiPrvSE` and
not from the terminal:

```powershell
Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine = '"C:\Program Files\Git\bin\bash.exe" -lc "bash /f/Backup-Mac-espejo-lanzador.sh"' }
```

The launcher waits for the second pass to write `EXIT=` and only then reads the disk.
`COPYFILE_DISABLE=1` in the macOS `tar` keeps it from slipping in a `._archivo` with the xattrs of each
photo.

```
2026-10-05 00:53:45 source: 9868 files, 222.44 GB
2026-10-05 00:53:51 to transfer: 9868 files
```

It is considered good when the log `F:\Backup-Mac-espejo.log` ends in
`WINDOWS VERIFY: ok ... mismatches 0, missing 0`.

## What actually went wrong

1. **Copy and Trash fighting over the same USB disk.** The copy dropped to ~6 MB/s. Fix: pause
   the Trash with `kill -STOP <pid>` and resume it with `kill -CONT <pid>` when the copy wrote
   `EXIT=0` (step 4).
2. **Three levels of quoting lost the `$?`.** With the whole command in quotes inside
   `tmux new "..."`, the log had a literal `EXIT=\0`. Fix: an 8-line script
   (`pasada2.sh`) instead of a line with nested quotes.
3. **The Trash was emptied by hand midway through the second pass.** The scripts never empty it; the
   owner did, and since then there is no "Put Back". Nothing was lost because every file was
   verified twice against the external disk before being moved, but until the third copy existed the
   external disk was the only one.

## How to verify

```bash
ssh mac 'python3 ~/.enjambre/respaldo/respaldo.py report'
```

The third copy is considered good by the last verification line of the mirror on Windows
(`F:\Backup-Mac-espejo.log`), which ended up like this:

```
WINDOWS VERIFY: ok 9858 (222.44 GB), mismatches 0, missing 0
```

Pending: the capture of `report` (still to be taken) and freeing space on the Mac until it exceeds 150 GB
free (`NEEDS-OWNER.md` §3).

## Self-check

1. Why are both hashes recomputed right before sending to the Trash, if the copy was already verified
   when it was made?
   <details><summary>Answer</summary>Because hours can pass between the copy and the Trash:
   the original may have changed or the copy been damaged. The check is only valid at the moment it is made.
   </details>
2. Why `ctime` and not just `mtime` to detect transfers in progress?
   <details><summary>Answer</summary>AirDrop and the Finder keep the original modification date;
   `ctime` changes when the file is created on this Mac and cannot be set from outside.
   </details>
3. Why are photos not taken out of `Photos Library.photoslibrary`?
   <details><summary>Answer</summary>It is a package with a database that points to every
   original; removing files from it leaves it corrupt.</details>
4. You sent 100 GB to the Trash. How much space did the Mac gain?
   <details><summary>Answer</summary>None until it is emptied. And on APFS it can be less than
   100 GB: if two files were clones sharing blocks, space is only freed when all of them
   are gone.</details>
