#!/bin/bash
# node-watch.sh — runs ON a node every 60 s (LaunchAgent dev.enjambre.nodewatch). Asks "is the
# orchestrator up?" and shows a macOS notification only when the answer changes.
# bash 3.2-safe. No arguments. State: ~/.enjambre/orq-state. Log: ~/enjambre-logs/node-watch.log
#
# WHY no TCP connect to 24800 any more: Deskflow's server is TLS, and a bare `nc -z` that opens
# and closes the socket logged "failed to accept secure socket" on Windows once a minute
# (DECISIONES 47). Now passive first: if the Deskflow client already holds an ESTABLISHED
# connection to the orchestrator, it is up — no packet sent. Otherwise one ICMP ping (Windows
# runs no sshd; ping fails if its firewall blocks echo, which only delays "up" until Deskflow
# reconnects). WHY poll and not heartbeat: docs/conceptos/sondeo-vs-latido.md.
set -u
conf="$HOME/Library/Deskflow/Deskflow.conf"
state="$HOME/.enjambre/orq-state"
log="$HOME/enjambre-logs/node-watch.log"
mkdir -p "$HOME/.enjambre" "$HOME/enjambre-logs"

# The orchestrator's address lives in Deskflow's own setting: no IP in this repo. remoteHost may
# be a quoted, comma-separated list; the first entry is enough for a liveness check.
host=$(sed -n 's/^remoteHost=//p' "$conf" 2>/dev/null | tr -d '"' | cut -d, -f1)
[ -n "$host" ] || { echo "$(date '+%F %T') no remoteHost in $conf" >> "$log"; exit 0; }

# netstat prints the foreign address as a.b.c.d.port; the dots in $host match themselves too.
if /usr/sbin/netstat -anp tcp 2>/dev/null | grep ESTABLISHED | grep -qF " $host.24800 "; then
  now=up
elif /sbin/ping -c 1 -t 3 "$host" >/dev/null 2>&1; then
  now=up
else
  now=down
fi

prev=unknown; misses=0
[ -f "$state" ] && read -r prev misses < "$state"
misses=${misses:-0}

# Debounce: one miss is noise (Windows rebooting Deskflow takes ~1 s); two in a row is real.
if [ "$now" = up ]; then
  misses=0; new=up
else
  misses=$((misses + 1))
  if [ "$misses" -ge 2 ]; then new=down; else new=$prev; fi
fi
printf '%s %s\n' "$new" "$misses" > "$state"

if [ "$new" != "$prev" ]; then
  echo "$(date '+%F %T') orquestador: $prev -> $new" >> "$log"
  case "$new" in
    up)   msg="Windows (orquestador) disponible" ;;
    down) msg="Windows (orquestador) no responde: no llegarán trabajos ni teclado" ;;
    *)    msg="" ;;
  esac
  # The first run after install (prev=unknown) is logged but not announced.
  if [ -n "$msg" ] && [ "$prev" != unknown ]; then
    /usr/bin/osascript -e "display notification \"$msg\" with title \"enjambre\"" >/dev/null 2>&1
  fi
fi
exit 0
