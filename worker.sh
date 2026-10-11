#!/bin/bash
# worker.sh — runs ON a node (over ssh). bash 3.2-safe: macOS ships 3.2, so no associative
# arrays, no ${var,,}, no mapfile.
#
# usage: worker.sh <repo> <task-id> [worker] [node-name]     TASK text on stdin
#   repo    folder name under ~/Documents/Portfolio
#   worker  claude (default) | stub-ok | stub-fail   (stubs exist to test the pipe without a model)
#
# Prints exactly one JSON line on stdout; everything else goes to stderr or to files.
# Exit code: the worker's (0 = task succeeded).
set -u
export GIT_TERMINAL_PROMPT=0   # never block on a credential prompt nobody will answer

repo=${1:?repo}; id=${2:?task-id}; worker=${3:-claude}; node=${4:-node}

# WHY re-exec inside tmux: over ssh the macOS keychain is locked ("User interaction is not
# allowed"), so `claude` reads as "Not logged in" and git cannot fetch over HTTPS. tmux-run.sh
# runs this same script inside the tmux server the LaunchAgent started at GUI login, which can
# read the keychain. It also keeps the job alive if ssh drops. ENJAMBRE_NO_TMUX=1 skips it.
if [ -z "${ENJAMBRE_IN_TMUX:-}" ] && [ "${ENJAMBRE_NO_TMUX:-}" != 1 ]; then
  here=$(cd "$(dirname "$0")" && pwd)
  ENJAMBRE_IN_TMUX=1 exec bash "$here/tmux-run.sh" -- bash "$here/worker.sh" "$@"
fi
base="$HOME/Documents/Portfolio"
src="$base/$repo"
# WHY a worktree per job: each job gets its own checkout, so two jobs (or a job and a human)
# never write the same files. The repo's .git is shared, so the branch is visible to `git fetch`.
wt="$base/wt/$repo-$id"
# WHY a branch per job: the result is reviewable and disposable — merging is a human decision.
branch="mac/$id"
logdir="$HOME/.enjambre/jobs/$id"
mkdir -p "$logdir"
cat > "$logdir/TASK.md"

say() { printf '[worker %s] %s\n' "$id" "$*" >&2; }
json_str() { # minimal JSON string escaper (bash 3.2: no ${var//} on newlines issues)
  printf '%s' "$1" | /usr/bin/python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'
}

start=$(date +%s)
cd "$src" || { echo "{\"task_id\":\"$id\",\"exit\":2,\"error\":\"no repo $repo\"}"; exit 2; }

# Fetch is best-effort: if GitHub is unreachable the job still runs from the local copy.
# The ledger records whether it fetched.
fetched=true
git fetch -q origin 2>>"$logdir/git.err" || { fetched=false; say "git fetch failed (continuing from local HEAD)"; }

if [ -e "$wt" ]; then
  echo "{\"task_id\":\"$id\",\"exit\":2,\"error\":\"worktree exists: refuse to reuse a task id\"}"; exit 2
fi
# Branch from the freshly fetched upstream when there is one: the node's local branch may be
# behind, and a job built on stale code produces a diff nobody can merge.
base_ref=HEAD
if [ "$fetched" = true ] && git rev-parse -q --verify '@{u}' >/dev/null; then base_ref='@{u}'; fi
git worktree add -q "$wt" -b "$branch" "$base_ref" 2>>"$logdir/git.err" || {
  echo "{\"task_id\":\"$id\",\"exit\":2,\"error\":\"worktree add failed\"}"; exit 2; }
# The repo's venv lives in the clone (gitignored), not in a fresh worktree: link it so the
# AGENTS.md tests-cmd (`.venv/bin/python ...`) runs the same here. `.venv/` in .gitignore does
# not match a symlink, so exclude the link in the shared info/exclude (local, never committed).
if [ -d "$src/.venv" ] && [ ! -e "$wt/.venv" ]; then
  ln -s "$src/.venv" "$wt/.venv"
  ex="$(git rev-parse --git-common-dir)/info/exclude"
  grep -qx '/.venv' "$ex" 2>/dev/null || { mkdir -p "$(dirname "$ex")"; echo '/.venv' >> "$ex"; }
fi

# Optional long-lived token written by the owner (NEEDS-OWNER.md §1a). Never created here.
envfile="$HOME/.config/enjambre/env"

# WHY explicit allowedTools and no --dangerously-skip-permissions: nobody watches this node.
# Anything not on the list is denied, not asked.
allowed='Read,Edit,Write,Glob,Grep,Bash(git status:*),Bash(git diff:*),Bash(ls:*)'
# kind tests: enjambre.py sends the whole list, = the set above + exactly Bash(<tests-cmd>) from
# the repo's AGENTS.md, base64 so the parentheses and spaces survive ssh, env and tmux-run.sh.
if [ -n "${ENJAMBRE_ALLOWED_B64:-}" ]; then
  allowed=$(printf '%s' "$ENJAMBRE_ALLOWED_B64" | /usr/bin/python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read()).decode())')
fi
case "$allowed" in *"'"*) echo "{\"task_id\":\"$id\",\"exit\":2,\"error\":\"quote in allowedTools\"}"; exit 2 ;; esac
maxturns=${ENJAMBRE_MAX_TURNS:-8}

# prompt 20: every job is its own process group (setsid) so the scheduler can pause or stop the
# whole tree by pgid, and baja/nocturna run under taskpolicy/nice. macOS has no setsid binary:
# a 4-line python helper does setsid + writes the pgid + execs. The prefix comes base64 from the
# PC (config/scheduler.toml) and may only contain the letters, digits, spaces and dashes of
# `taskpolicy -b nice -n 15`.
prio=''
if [ -n "${ENJAMBRE_PRIO_B64:-}" ]; then
  prio=$(printf '%s' "$ENJAMBRE_PRIO_B64" | /usr/bin/python3 -c 'import base64,sys; print(base64.b64decode(sys.stdin.read()).decode())')
  case "$prio" in *[!a-z0-9\ -]*) echo "{\"task_id\":\"$id\",\"exit\":2,\"error\":\"bad priority prefix\"}"; exit 2 ;; esac
fi
cat > "$logdir/setsid.py" <<'SETSID'
import os, sys
os.setsid()
with open(sys.argv[1], "w") as f:
    f.write(str(os.getpid()))
os.execvp(sys.argv[2], sys.argv[2:])
SETSID
grp="/usr/bin/python3 '$logdir/setsid.py' '$logdir/pgid' $prio"

run="$logdir/run.sh"
{
  echo '#!/bin/bash'
  echo "cd '$wt' || exit 2"
  echo "[ -f '$envfile' ] && . '$envfile'"
  case "$worker" in
    # TASK on stdin, never as an argument: a TASK starting with `---` (frontmatter) was read
    # by the CLI as an option (prompt 14, 0j).
    claude)    echo "$grp claude -p --output-format json --max-turns $maxturns --allowedTools '$allowed' < '$logdir/TASK.md' > '$logdir/out.json' 2> '$logdir/worker.err'" ;
               echo "rc=\$?; if [ \$rc -eq 0 ] && grep -q '\"is_error\":true' '$logdir/out.json'; then rc=1; fi; echo \$rc > '$logdir/rc'" ;;
    stub-ok)   echo "echo \"- \$(date +%F): entrada de prueba (stub-ok)\" >> CHANGELOG.md; echo '{\"is_error\":false,\"result\":\"stub ok\",\"usage\":{}}' > '$logdir/out.json'; echo 0 > '$logdir/rc'" ;;
    stub-fail) echo "echo '{\"is_error\":true,\"result\":\"stub failure on purpose\",\"usage\":{}}' > '$logdir/out.json'; echo 1 > '$logdir/rc'" ;;
    *)         echo "echo '{\"is_error\":true,\"result\":\"unknown worker\"}' > '$logdir/out.json'; echo 2 > '$logdir/rc'" ;;
  esac
} > "$run"

# Already inside the tmux window (see the re-exec at the top): run the job directly.
bash "$run"

rc=$(cat "$logdir/rc" 2>/dev/null || echo 2)
end=$(date +%s)
usage=$(/usr/bin/python3 - "$logdir/out.json" <<'PY'
import json, os, re, sys

def rel(t):
    # Worktree-relative, and never the macOS home: the ledger lives in the repo.
    return re.sub(r"^.*/wt/[^/]+/", "", t).replace(os.path.expanduser("~"), "~")[:200]

try:
    d = json.load(open(sys.argv[1]))
except Exception:
    d = {}
# Tool + target only: the full denied input (a whole patch) stays in out.json on the node.
denials = [{"tool": x.get("tool_name"),
            "target": rel(str((x.get("tool_input") or {}).get("file_path")
                              or (x.get("tool_input") or {}).get("command") or ""))}
           for x in (d.get("permission_denials") or []) if isinstance(x, dict)]
# duration_api_ms = time spent waiting on the cloud model; duration_s - that = local tool time.
print(json.dumps({"usage": d.get("usage", {}), "cost_usd": d.get("total_cost_usd"),
                  "duration_api_ms": d.get("duration_api_ms"),
                  "result": str(d.get("result", ""))[:500], "is_error": d.get("is_error"),
                  "permission_denials": denials}))
PY
)

cd "$wt" || exit 2
cat > result.json <<EOF
{"task_id":"$id","node":"$node","worker":"$worker","branch":"$branch","exit":$rc,"start":$start,"end":$end,"duration_s":$((end-start)),"fetched":$fetched,"detail":$usage}
EOF
git add -A
git add -f result.json   # some repos ignore everything by default (allowlist .gitignore)
git -c user.name="enjambre worker" -c user.email="enjambre@localhost" \
  commit -q -m "enjambre $id: worker=$worker exit=$rc" || say "nothing to commit"

# WHY the node does not push by default: the orchestrator fetches the branch over ssh, scans
# exactly the new commits with gitleaks and pushes. One gate, on the machine that has the
# scanner. ENJAMBRE_NODE_PUSH=1 pushes from here (no scan: only for repos you trust).
pushed=false
if [ "${ENJAMBRE_NODE_PUSH:-0}" = 1 ]; then
  if git push -q -u origin "$branch" 2>>"$logdir/git.err"; then pushed=true
  else say "push failed: orchestrator must fetch $branch over ssh"; fi
fi

printf '{"task_id":"%s","branch":"%s","exit":%s,"duration_s":%s,"pushed":%s,"fetched":%s,"worktree":%s,"detail":%s}\n' \
  "$id" "$branch" "$rc" "$((end-start))" "$pushed" "$fetched" "$(json_str "$wt")" "$usage"
exit "$rc"
