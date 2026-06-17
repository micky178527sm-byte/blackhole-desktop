#!/bin/sh
# Launch the desktop black hole overlay (singleton). Rebuilds the shader first
# so edits to blackhole.glsl / build.py tunables take effect.
#
# Capture edition: screen-captures the desktop behind the window and
# gravitationally lenses it. Needs Screen Recording permission (granted once in
# System Settings > Privacy & Security > Screen Recording).
cd "$(dirname "$0")" || exit 1

# Use the Python that has pyobjc/PyOpenGL installed. launchd's PATH is minimal
# and would otherwise pick the system python3 (no pyobjc -> "No module named objc").
PYBIN="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
[ -x "$PYBIN" ] || PYBIN="$(command -v python3)"

# first-run dependency bootstrap: install the python deps if missing, so the
# one-line "git clone ... && run.sh" works out of the box. No-op once present.
"$PYBIN" -c "import objc, OpenGL" 2>/dev/null || "$PYBIN" -m pip install --user pyobjc PyOpenGL

pkill -f "[o]verlay_gl.py" 2>/dev/null
pkill -f "[o]verlay.py" 2>/dev/null
sleep 0.3
"$PYBIN" build.py || exit 1
exec "$PYBIN" overlay_gl.py
