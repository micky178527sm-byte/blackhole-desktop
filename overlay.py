#!/usr/bin/env python3
"""blackhole-desktop — a ray-traced black hole that floats over your desktop,
its size tracking Claude Code's context-window fill. The Ghostty-shader port
runs in a transparent, click-through, always-on-top WKWebView (index.html);
this process just hosts that window and feeds it the live level.

The level arrives out-of-band through a small file written by level.py
(wired into Claude Code as a statusLine + SessionStart/SessionEnd hooks):

    >= 0.0   context fill 0..1   -> hole shown at that size
    <  0.0   "no session"        -> hole hidden
    missing / stale              -> hole hidden

Model: native Terminal (or anything) stays your shell; the hole lives on the
desktop like clawd-on-desk. No Ghostty, no terminal switch.
"""
import os
import signal
import time

import objc
from AppKit import (
    NSApplication, NSWindow, NSColor, NSScreen,
    NSBackingStoreBuffered, NSApplicationActivationPolicyAccessory,
    NSScreenSaverWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
)
from Foundation import (
    NSObject, NSTimer, NSRunLoop, NSRunLoopCommonModes, NSMakeRect, NSURL,
)
from WebKit import WKWebView, WKWebViewConfiguration

NSWindowStyleMaskBorderless = 0

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "index.html")
LEVEL_FILE = os.path.expanduser("~/.claude/blackhole-level")
POS_FILE = os.path.expanduser("~/.claude/blackhole-pos")
STALE_SEC = 30 * 60   # no statusline update in this long -> assume session gone


def read_level():
    """Current level from the file: 0..1, or -1 for hidden (missing/stale/<0)."""
    try:
        st = os.stat(LEVEL_FILE)
    except OSError:
        return -1.0
    if time.time() - st.st_mtime > STALE_SEC:
        return -1.0
    try:
        with open(LEVEL_FILE) as f:
            v = float(f.read().strip() or "-1")
    except (OSError, ValueError):
        return -1.0
    return v if v < 0 else max(0.0, min(1.0, v))


def read_pos():
    """Manual position from the file, or None for auto-drift.

    'auto' (or missing) -> None. 'x y' (two floats in 0..1, origin top-left) ->
    (x, y) to pin the hole there."""
    try:
        with open(POS_FILE) as f:
            s = f.read().strip()
    except OSError:
        return None
    if not s or s.lower().startswith("auto"):
        return None
    try:
        x, y = (float(v) for v in s.replace(",", " ").split()[:2])
    except (ValueError, IndexError):
        return None
    return (max(0.0, min(1.0, x)), max(0.0, min(1.0, y)))


class Driver(NSObject):
    def initWithWebView_(self, webview):
        self = objc.super(Driver, self).init()
        if self is None:
            return None
        self._web = webview
        return self

    def tick_(self, timer):
        # Push every tick (5 Hz): cheap, and immune to the page-load race that
        # change-detection would lose (a value "sent" before __setLevel exists
        # would otherwise never be retried).
        lvl = read_level()
        jl = "window.__setLevel && window.__setLevel(%s);" % (
            "null" if lvl < 0 else repr(round(lvl, 4)))

        pos = read_pos()
        if pos is None:
            jp = "window.__setPos && window.__setPos(0);"
        else:
            jp = "window.__setPos && window.__setPos(1, %r, %r);" % (
                round(pos[0], 4), round(pos[1], 4))

        self._web.evaluateJavaScript_completionHandler_(jl + jp, None)


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    screen = NSScreen.mainScreen()
    if screen is None:
        print("no screen")
        return
    frame = screen.frame()

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
    win.setOpaque_(False)
    win.setBackgroundColor_(NSColor.clearColor())
    win.setHasShadow_(False)
    win.setIgnoresMouseEvents_(True)          # click-through
    win.setLevel_(NSScreenSaverWindowLevel)   # above normal windows
    win.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces |
        NSWindowCollectionBehaviorFullScreenAuxiliary |
        NSWindowCollectionBehaviorStationary)

    cfg = WKWebViewConfiguration.alloc().init()
    web = WKWebView.alloc().initWithFrame_configuration_(
        NSMakeRect(0, 0, frame.size.width, frame.size.height), cfg)
    # transparent webview so only the hole's pixels show; the rest is desktop
    web.setValue_forKey_(False, "drawsBackground")
    web.setAutoresizingMask_(1 << 1 | 1 << 4)  # width-sizable | height-sizable

    url = NSURL.fileURLWithPath_(INDEX)
    web.loadFileURL_allowingReadAccessToURL_(url, NSURL.fileURLWithPath_(HERE))

    win.setContentView_(web)
    win.orderFrontRegardless()

    driver = Driver.alloc().initWithWebView_(web)
    timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
        0.2, driver, b"tick:", None, True)
    NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)

    def _stop(sig, frame):
        app.stop_(None)
    try:
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
    except Exception:
        pass

    app.run()


if __name__ == "__main__":
    main()
