#!/bin/bash
# install.sh — runs ON the Mac. Installs/refreshes the puente server as a LaunchAgent in the GUI
# session (gui/<uid>), where `claude` sees the login keychain (tutorial 11). Idempotent.
#
#   bash install.sh [sdk|stub]        default sdk; stub = echo engine, no model (wiring checks)
#
# Layout: ~/.enjambre/puente/{app/puente.py, venv/, sessions/, turns/, server.log}
#         ~/.config/enjambre/puente.key  (created once, chmod 600, never printed)
set -eu
engine=${1:-sdk}
case "$engine" in sdk|stub) ;; *) echo "engine must be sdk or stub" >&2; exit 64 ;; esac
here=$(cd "$(dirname "$0")" && pwd)
root="$HOME/.enjambre/puente"
label=dev.enjambre.puente
plist="$HOME/Library/LaunchAgents/$label.plist"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"

mkdir -p "$root/app" "$HOME/.config/enjambre"
cp "$here/puente.py" "$root/app/puente.py"

if [ ! -x "$root/venv/bin/python" ]; then
  uv venv -q -p 3.12 "$root/venv"
fi
# Pinned: the SDK's option names are the contract run_sdk relies on.
uv pip install -q -p "$root/venv/bin/python" "claude-agent-sdk==0.2.164"

key="$HOME/.config/enjambre/puente.key"
if [ ! -s "$key" ]; then
  (umask 077; /usr/bin/python3 -c 'import secrets; print(secrets.token_hex(32))' > "$key")
  echo "key: created $key (600)"
else
  chmod 600 "$key"; echo "key: kept"
fi

cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>
    <string>$root/venv/bin/python</string><string>$root/app/puente.py</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>PUENTE_ENGINE</key><string>$engine</string>
    <key>PUENTE_STUB_SLEEP</key><string>20</string>
  </dict>
  <key>WorkingDirectory</key><string>$root</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$root/server.log</string>
  <key>StandardErrorPath</key><string>$root/server.log</string>
</dict></plist>
EOF

uid=$(id -u)
launchctl bootout "gui/$uid/$label" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$uid" "$plist"
sleep 2
launchctl print "gui/$uid/$label" | grep -E "^\s*(state|pid) =" | head -2
tail -2 "$root/server.log"
