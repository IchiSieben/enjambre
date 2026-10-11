#!/bin/bash
# smb-mount.sh — runs ON the Mac every 60 s (LaunchAgent dev.enjambre.smbmount). Keeps the Windows
# SMB shares mounted under ~/Compartidos/<share> and writes ~/.enjambre/mounts.json, which
# `enjambre watch` reads (presence.py). bash 3.2-safe. No arguments.
#
# It never opens a dialog: it only tries to mount when Windows answers on 445 AND the Keychain
# has an item for (host, user); otherwise it records why and waits for the next run. That is the
# retry: launchd calls it again in 60 s. The password lives only in the Keychain (the owner saves
# it once with Finder ⌘K); this script never reads it.
#
# Host = Deskflow's remoteHost (same as node-watch.sh): no IP in this repo, and the Keychain item
# must be saved for that same address.
set -u
SHARES="${ENJAMBRE_SMB_SHARES:-Compartido-Mac Backup-Mac}"
SMBUSER="${ENJAMBRE_SMB_USER:-usuario-smb}"
BASE="${ENJAMBRE_SMB_BASE:-$HOME/Compartidos}"
conf="$HOME/Library/Deskflow/Deskflow.conf"
out="$HOME/.enjambre/mounts.json"
log="$HOME/enjambre-logs/smb-mount.log"
mkdir -p "$HOME/.enjambre" "$HOME/enjambre-logs" "$BASE"

say() { echo "$(date '+%F %T') $*" >> "$log"; }

# Run "$@" but give up after $1 seconds (macOS has no `timeout`). Returns 124 on timeout.
with_timeout() {
  local secs=$1; shift
  "$@" & local pid=$!
  local i=0
  while kill -0 "$pid" 2>/dev/null; do
    # No `wait` after the kill: a process stuck on a dead SMB mount may not die, and waiting
    # would hang this script too. It is reaped when the script exits.
    if [ "$i" -ge $((secs * 4)) ]; then kill -9 "$pid" 2>/dev/null; return 124; fi
    sleep 0.25; i=$((i + 1))
  done
  wait "$pid"
}

host=$(sed -n 's/^remoteHost=//p' "$conf" 2>/dev/null | tr -d '"' | cut -d, -f1)
reach=false; keychain=false
if [ -n "$host" ]; then
  /usr/bin/nc -z -G 3 "$host" 445 >/dev/null 2>&1 && reach=true
  /usr/bin/security find-internet-password -s "$host" -a "$SMBUSER" >/dev/null 2>&1 && keychain=true
fi

entries=""
for share in $SHARES; do
  mp="$BASE/$share"
  # Match by server and share in `mount` output, not by path: a Finder mount of the same share
  # would sit in /Volumes/<share>[-1].
  line=$(/sbin/mount | grep -i "@$host/$share on " | head -1)
  cur=$(printf '%s' "$line" | sed -n 's/^.* on \(.*\) (smbfs.*$/\1/p')
  state=""
  if [ -n "$cur" ]; then
    # A mount whose server vanished makes ls hang: 5 s and it counts as hung.
    if with_timeout 5 /bin/ls "$cur" >/dev/null 2>&1; then state=mounted
    else state=hung; fi
  elif [ -z "$host" ]; then state=no-host
  elif [ "$reach" != true ]; then state=unreachable
  elif [ "$keychain" != true ]; then state=no-keychain
  else
    mkdir -p "$mp"
    opts=""; [ "$share" = Backup-Mac ] && opts="-o rdonly"
    # shellcheck disable=SC2086  # $opts is two words on purpose
    if with_timeout 20 /sbin/mount_smbfs -N $opts "//$SMBUSER@$host/$share" "$mp" >>"$log" 2>&1; then
      state=mounted; cur="$mp"; say "mounted $share at $mp"
    else
      state=mount-failed; say "mount_smbfs failed for $share (rc $?)"
    fi
  fi
  ok=false; [ "$state" = mounted ] && ok=true
  prev=$(sed -n "s/.*\"name\": \"$share\", \"ok\": \([a-z]*\), \"state\": \"\([a-z-]*\)\".*/\2/p" "$out" 2>/dev/null | head -1)
  [ "$prev" != "$state" ] && say "$share: ${prev:-none} -> $state"
  path=$(printf '%s' "${cur:-$mp}" | sed "s|^$HOME|~|")
  entries="${entries:+$entries, }{\"name\": \"$share\", \"ok\": $ok, \"state\": \"$state\", \"path\": \"$path\"}"
done

tmp="$out.tmp"
printf '{"checked_at": "%s", "reach_445": %s, "keychain": %s, "mounts": [%s]}\n' \
  "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$reach" "$keychain" "$entries" > "$tmp" && mv "$tmp" "$out"
exit 0
