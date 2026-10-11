#!/bin/bash
# busy.sh — runs ON the Mac. Marks the node busy (~/.enjambre/busy.json) while a long job runs,
# so `enjambre` routes around it (busy.py, DECISIONES 160). bash 3.2-safe.
#
#   busy.sh run <tipo> <quien> -- cmd [args...]   write the marker, run cmd, remove it on exit
#   busy.sh attach <tipo> <quien> <pid>           mark for a job that is already running; a
#                                                 background watcher removes it when pid exits
#   busy.sh show | clear                          print / remove the marker (clear: only if stale)
#
# tipo: bench | ltx | transcribe | render | video | other. The marker is never overwritten while
# its pid is alive: a nested job (a CPU render during a bench) runs, but leaves the outer mark.
set -u
F="$HOME/.enjambre/busy.json"
mkdir -p "$HOME/.enjambre"

pid_of() { sed -n 's/.*"pid": *\([0-9][0-9]*\).*/\1/p' "$F" 2>/dev/null; }
held() { local p; p=$(pid_of); [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
write() { # tipo quien pid
  local tmp="$F.$$"
  printf '{"quien": "%s", "desde": "%s", "tipo": "%s", "pid": %s}\n' "$2" "$(date +%Y-%m-%dT%H:%M:%S%z)" "$1" "$3" > "$tmp"
  mv -f "$tmp" "$F"
}
release() { [ "$(pid_of)" = "$1" ] && rm -f "$F"; return 0; } # only our own mark

cmd=${1:-show}; shift || true
case "$cmd" in
  run)
    tipo=${1:?tipo}; quien=${2:?quien}; shift 2; [ "${1:-}" = "--" ] && shift
    [ $# -gt 0 ] || { echo "usage: busy.sh run <tipo> <quien> -- cmd..." >&2; exit 64; }
    mine=no
    if ! held; then write "$tipo" "$quien" $$; mine=yes; fi
    # WHY trap and not "rm after cmd": Ctrl-C, a tmux kill or set -e would skip the last line.
    [ $mine = yes ] && trap 'release '$$ EXIT INT TERM HUP
    "$@"
    exit $?   # the EXIT trap runs here
    ;;
  attach)
    tipo=${1:?tipo}; quien=${2:?quien}; pid=${3:?pid}
    kill -0 "$pid" 2>/dev/null || { echo "busy.sh: pid $pid is not running" >&2; exit 1; }
    if held && [ "$(pid_of)" != "$pid" ]; then echo "busy.sh: already marked: $(cat "$F")" >&2; exit 1; fi
    write "$tipo" "$quien" "$pid"
    # Watcher: the job was not started by us, so no trap can reach it. Poll its pid, then release.
    nohup bash -c "while kill -0 $pid 2>/dev/null; do sleep 20; done; \
      [ \"\$(sed -n 's/.*\"pid\": *\\([0-9][0-9]*\\).*/\\1/p' '$F')\" = $pid ] && rm -f '$F'" \
      >/dev/null 2>&1 &
    echo "marked: $(cat "$F")"
    ;;
  show) [ -f "$F" ] && { cat "$F"; held && echo "(alive)" || echo "(stale: pid gone)"; } || echo "not busy" ;;
  clear) if held; then echo "busy.sh: pid $(pid_of) is alive, not clearing" >&2; exit 1; fi; rm -f "$F"; echo cleared ;;
  *) echo "usage: busy.sh run|attach|show|clear ..." >&2; exit 64 ;;
esac
