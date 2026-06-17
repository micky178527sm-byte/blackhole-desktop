#!/bin/sh
# Stop the desktop black hole overlay.
launchctl bootout "gui/$(id -u)/com.blackhole.desktop" 2>/dev/null || \
  launchctl unload "$HOME/Library/LaunchAgents/com.blackhole.desktop.plist" 2>/dev/null
pkill -f "[o]verlay_gl.py" 2>/dev/null
pkill -f "[o]verlay.py" 2>/dev/null
echo "stopped."
