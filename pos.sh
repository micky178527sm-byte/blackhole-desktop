#!/bin/sh
# Move the desktop black hole, or hand it back to auto-drift.
#
#   ./pos.sh auto         resume the slow automatic drift (default)
#   ./pos.sh 0.5 0.4      pin it to a uv point (x: 0=left..1=right,
#                                               y: 0=top..1=bottom)
#   ./pos.sh center       shortcut for 0.5 0.5
#
# Takes effect within ~0.2 s while overlay.py is running.
POS_FILE="$HOME/.claude/blackhole-pos"

case "$1" in
  ""|auto)   echo auto            > "$POS_FILE"; echo "drift: auto" ;;
  center)    echo "0.5 0.5"       > "$POS_FILE"; echo "pinned: 0.5 0.5" ;;
  *)
    if [ -n "$2" ]; then
      echo "$1 $2" > "$POS_FILE"; echo "pinned: $1 $2"
    else
      echo "usage: $0 [auto|center|X Y]   (X,Y in 0..1)" >&2; exit 1
    fi ;;
esac
