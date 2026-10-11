#!/bin/bash
# tmux-run.sh — runs ON a node. Executes one command inside the tmux server that the
# LaunchAgent dev.enjambre.tmux started at GUI login, and returns its stdout, stderr and exit code
# as if it had run here. bash 3.2-safe.
#
# usage: tmux-run.sh [-C dir] [-t timeout_s] -- cmd [args...]      stdin is forwarded to cmd
#
# WHY: macOS ties the login keychain to the security session it was unlocked in (the GUI login).
# A process born from sshd lives in another session and gets "User interaction is not allowed",
# so `gh`, `git push` over HTTPS and `claude` all fail as "not logged in". A tmux server started
# by launchd in the gui/<uid> domain lives in the GUI session; every window it spawns inherits it.
# The dedicated socket (-L enjambre) matters: a server on the default socket may have been born
# from ssh, and attaching to it would silently give you no keychain.
set -u
T="tmux -L enjambre"
dir=$PWD; timeout=7200
while [ $# -gt 0 ]; do
  case "$1" in
    -C) dir=$2; shift 2 ;;
    -t) timeout=$2; shift 2 ;;
    --) shift; break ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || { echo "usage: tmux-run.sh [-C dir] [-t timeout_s] -- cmd [args...]" >&2; exit 64; }

# Never create the session from here: a session created over ssh has no keychain.
if ! $T has-session -t enjambre 2>/dev/null; then
  echo "tmux-run: no 'enjambre' session on socket -L enjambre. It is started by the LaunchAgent" >&2
  echo "  dev.enjambre.tmux at GUI login. Fix: log in on the Mac's screen, or in Terminal.app:" >&2
  echo "  launchctl kickstart -k gui/\$(id -u)/dev.enjambre.tmux" >&2
  exit 75
fi

run="$HOME/.enjambre/run/$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$run"
if [ -t 0 ]; then : > "$run/stdin"; else cat > "$run/stdin"; fi

# The window inherits the tmux server's environment (from launchd), not this ssh session's:
# pass the ENJAMBRE_* variables explicitly, quoted with printf %q.
{
  echo '#!/bin/bash'
  echo 'export GIT_TERMINAL_PROMPT=0'
  env | grep '^ENJAMBRE_' | while IFS='=' read -r k v; do printf 'export %s=%q\n' "$k" "$v"; done
  printf 'cd %q 2> %q || { echo 2 > %q; exit 2; }\n' "$dir" "$run/err" "$run/rc"
  printf '%q ' "$@"; printf '< %q > %q 2> %q\n' "$run/stdin" "$run/out" "$run/err"
  printf 'echo $? > %q && mv %q %q\n' "$run/rc.tmp" "$run/rc.tmp" "$run/rc"
} > "$run/cmd.sh"

$T new-window -d -t enjambre -n "run-$$" "bash '$run/cmd.sh'"

# Poll for the rc file instead of wait-for: a file cannot miss a signal, and a timeout is easy.
waited=0
while [ ! -f "$run/rc" ]; do
  sleep 1; waited=$((waited + 1))
  if [ "$waited" -ge "$timeout" ]; then
    echo "tmux-run: timeout after ${timeout}s; the window keeps running. Logs: $run" >&2
    exit 124
  fi
done
cat "$run/out" 2>/dev/null
cat "$run/err" >&2 2>/dev/null
exit "$(cat "$run/rc")"
