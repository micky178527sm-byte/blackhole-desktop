#!/bin/sh
# Double-click this file in Finder to start the desktop black hole.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.blackhole.desktop.plist"

mkdir -p "$HOME/.claude"
LEVEL="$(cat "$HOME/.claude/blackhole-level" 2>/dev/null || true)"
case "$LEVEL" in
  ""|-*) printf "0.6\n" > "$HOME/.claude/blackhole-level" ;;
esac
printf "auto\n" > "$HOME/.claude/blackhole-pos"

if [ -f "$PLIST" ]; then
  if launchctl print "gui/$(id -u)/com.blackhole.desktop" >/dev/null 2>&1; then
    launchctl kickstart -k "gui/$(id -u)/com.blackhole.desktop" 2>/dev/null || true
  else
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST"
  fi
else
  "$DIR/run.sh" &
fi

echo "Blackhole started."
osascript -e 'tell application "Terminal" to close front window' > /dev/null 2>&1 &
exit 0
