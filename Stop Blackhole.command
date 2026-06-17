#!/bin/sh
# Double-click this file in Finder to stop the desktop black hole.
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
"$DIR/stop.sh"

echo "Blackhole stopped."
osascript -e 'tell application "Terminal" to close front window' > /dev/null 2>&1 &
exit 0
