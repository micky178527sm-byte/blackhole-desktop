#!/usr/bin/env python3
"""Claude Code -> blackhole-desktop bridge. Wired into ~/.claude/settings.json
three ways (see README); each run reads the JSON Claude pipes on stdin and
writes one line to ~/.claude/blackhole-level, which overlay.py polls:

    SessionStart hook -> 0.0   (fresh / resume / clear: tiny corner seed)
    SessionEnd   hook -> keep last level by default, so the hole remains visible
                          until you explicitly stop it
    statusLine        -> context-window fill 0..1, then runs your existing
                          statusline so its display is completely unchanged

Unlike the Ghostty version this touches no cursor color and needs no specific
terminal — the level goes to a file, the desktop overlay reads it. Must never
raise: a crashing statusLine blanks Claude's status bar, so every step is
best-effort and the statusLine path always passes your line through.
"""
import json
import os
import subprocess
import sys
import tempfile

LEVEL_FILE = os.path.expanduser("~/.claude/blackhole-level")
# Your existing statusline — its output is relayed unchanged on the statusLine
# path so the hole is purely additive. Set to None to print nothing.
INNER_STATUSLINE = os.path.expanduser("~/.claude/statusline.py")


def write_level(value):
    """Atomically write the level so the overlay never reads a half-written file."""
    try:
        d = os.path.dirname(LEVEL_FILE)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".blackhole-level.")
        with os.fdopen(fd, "w") as f:
            f.write("%.4f\n" % value)
        os.replace(tmp, LEVEL_FILE)
    except OSError:
        pass


def context_fill(data):
    """Fraction of the context window in use, 0.0 .. 1.0."""
    cw = data.get("context_window") or {}
    pct = cw.get("used_percentage")
    if pct is None:
        used = cw.get("total_input_tokens") or 0
        size = cw.get("context_window_size") or 0
        pct = (100.0 * used / size) if size else 0.0
    try:
        return max(0.0, min(1.0, float(pct) / 100.0))
    except (TypeError, ValueError):
        return 0.0


def relay_statusline(raw):
    """Run the user's real statusline with the same JSON and print its output."""
    if not INNER_STATUSLINE or not os.path.exists(INNER_STATUSLINE):
        return
    try:
        r = subprocess.run([INNER_STATUSLINE], input=raw, capture_output=True,
                           text=True, timeout=10)
        sys.stdout.write(r.stdout)
    except (OSError, subprocess.SubprocessError):
        pass


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        data = {}

    event = data.get("hook_event_name")
    if event == "SessionEnd":
        if os.environ.get("BH_HIDE_ON_SESSION_END") == "1":
            write_level(-1.0)
        return
    if event == "SessionStart":
        write_level(0.0)
        return

    # statusLine: record the live fill, then hand the display back to your line
    write_level(context_fill(data))
    relay_statusline(raw)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # never break Claude's status bar
        pass
