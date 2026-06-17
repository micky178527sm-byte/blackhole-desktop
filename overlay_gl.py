"""blackhole-desktop, capture edition: a real gravitational lens over your
desktop. ScreenCaptureKit grabs the live screen behind this window; a native
OpenGL view runs the ported black-hole shader with that capture as the lensed
"sky", so the actual desktop bends/magnifies around the hole. The window is
transparent + click-through, and excluded from its own capture (no feedback).

Set BH_PASSTHROUGH=1 to bypass the shader and just blit the capture (pipeline
test). Otherwise it loads frag_capture.glsl (written by build.py).
"""
import os
import json
import math
import threading

import objc
from AppKit import (
    NSApplication, NSWindow, NSColor, NSScreen, NSOpenGLView, NSEvent,
    NSOpenGLPixelFormat, NSBackingStoreBuffered,
    NSApplicationActivationPolicyAccessory, NSApplicationActivationPolicyRegular,
    NSScreenSaverWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSMenu, NSMenuItem, NSSlider, NSStepper, NSTextField, NSView, NSFont, NSButton,
    NSBox, NSColorWell, NSSegmentedControl,
    NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
)
from Foundation import NSObject, NSTimer, NSRunLoop, NSRunLoopCommonModes, NSMakeRect, NSLocale
import ScreenCaptureKit as SCK

# Language: "auto" follows the system; the settings panel can override it to
# "ja"/"en" (stored in the config) so you can preview either without changing
# System Settings. T() resolves dynamically against the current _LANG.
_CFG_PATH = os.path.expanduser("~/.claude/blackhole-config.json")


def _resolve_lang():
    pref = "auto"
    try:
        pref = json.load(open(_CFG_PATH)).get("lang", "auto")
    except Exception:
        pref = "auto"
    if pref in ("ja", "en"):
        return pref
    try:
        return "ja" if NSLocale.currentLocale().languageCode() == "ja" else "en"
    except Exception:
        return "en"


_LANG = _resolve_lang()


def T(en, ja):
    return ja if _LANG == "ja" else en


def TR(pair):
    """Resolve a (en, ja) tuple to the current language (for static data)."""
    return pair[1] if _LANG == "ja" else pair[0]
from Quartz import (
    CVPixelBufferLockBaseAddress, CVPixelBufferUnlockBaseAddress,
    CVPixelBufferGetBaseAddress, CVPixelBufferGetBytesPerRow,
    CVPixelBufferGetWidth, CVPixelBufferGetHeight,
)
from CoreMedia import CMSampleBufferGetImageBuffer
from OpenGL import GL

NSWindowStyleMaskBorderless = 0
NSWindowSharingNone = 0
NSOpenGLPFAOpenGLProfile = 99
NSOpenGLProfileVersion3_2Core = 0x3200
NSOpenGLPFAColorSize = 8
NSOpenGLPFAAlphaSize = 11
NSOpenGLPFADoubleBuffer = 5
NSOpenGLPFAAccelerated = 73
NSOpenGLCPSurfaceOpacity = 236

HERE = os.path.dirname(os.path.abspath(__file__))
LEVEL_FILE = os.path.expanduser("~/.claude/blackhole-level")
POS_FILE = os.path.expanduser("~/.claude/blackhole-pos")
FRAG_FILE = os.path.join(HERE, "frag_capture.glsl")
PASSTHROUGH = os.environ.get("BH_PASSTHROUGH") == "1"
# capture resolution cap (downscaled sky is plenty for lensing; saves CPU/mem)
CAP_W, CAP_H = 0, 0  # set from display, optionally halved below
CAP_SCALE = float(os.environ.get("BH_CAP_SCALE", "0.5"))

# size params written by build.py — the overlay drives the hole position itself
# (full-screen drift + drag), so it needs the same size formula as the shader.
try:
    PARAMS = json.load(open(os.path.join(HERE, "params.json")))
except (OSError, ValueError):
    PARAMS = {"hole_radius": 0.08, "area_min": 0.0008, "area_max": 0.012, "ease": 1.4}


def hole_radius_uv(level, aspect, size_scale=1.0):
    """Approx on-screen radius of the whole hole (shadow+disk+lens reach), as a
    fraction of screen height. Mirrors the shader's token-mode size formula."""
    lv = max(0.0, min(1.0, level))
    g = lv ** PARAMS["ease"]
    rh_min = math.sqrt(PARAMS["area_min"] * aspect / math.pi)
    rh_max = math.sqrt(PARAMS["area_max"] * aspect / math.pi)
    rh = (rh_min + (rh_max - rh_min) * g) * (PARAMS["hole_radius"] / 0.08) * size_scale
    return rh * 3.2  # the bright disk/lens reaches ~3x the shadow radius


# Fixed "fill" the shader uses for SIZE, so the hole never resizes on its own.
# (The real context level only decides show vs hide; the 大きさ slider scales
# the actual size.) Raise/lower this for a bigger/smaller baseline.
SIZE_LEVEL = 0.55


# --- live settings (right-click > 設定), persisted across restarts -----------
CONFIG_FILE = _CFG_PATH
# "tint" is the disk light color [r,g,b] (warm amber by default); "lang" is the
# UI language override ("auto"/"ja"/"en", handled separately from the sliders).
WARM_TINT = [0.96, 0.80, 0.58]
CONFIG_DEFAULTS = {"size": 1.0, "speed": 1.0, "spin": 1.0, "incl": 1.5,
                   "disk": 1.3, "contrast": 1.0, "lens": 3.4, "jet": 0.7,
                   "jetlen": 10.0, "jetflow": 0.8,
                   "span": 0.0, "tint": list(WARM_TINT), "lang": "auto"}
_config_cache = {"mtime": -1.0, "values": dict(CONFIG_DEFAULTS)}


def read_config():
    """Cached config read (re-reads only when the file changes on disk)."""
    try:
        m = os.stat(CONFIG_FILE).st_mtime
    except OSError:
        return _config_cache["values"]
    if m != _config_cache["mtime"]:
        try:
            v = dict(CONFIG_DEFAULTS)
            v.update(json.load(open(CONFIG_FILE)))
            _config_cache["values"] = v
            _config_cache["mtime"] = m
        except (OSError, ValueError):
            pass
    return _config_cache["values"]


def write_config(values):
    try:
        json.dump(values, open(CONFIG_FILE, "w"))
    except OSError:
        pass

PASS_FRAG = """#version 330 core
uniform sampler2D iChannel0;
uniform vec3 iResolution;
out vec4 o;
void main(){
  vec2 uv = gl_FragCoord.xy / iResolution.xy;
  o = texture(iChannel0, vec2(uv.x, 1.0 - uv.y));
}
"""
PASS_VERT = """#version 330 core
const vec2 P[3] = vec2[3](vec2(-1.,-1.), vec2(3.,-1.), vec2(-1.,3.));
void main(){ gl_Position = vec4(P[gl_VertexID], 0., 1.); }
"""


# ----------------------------------------------------------------- capture --
class Frame:
    """Latest captured frame, shared bg-thread -> main-thread under a lock."""
    def __init__(self):
        self.lock = threading.Lock()
        self.data = None
        self.w = self.h = self.row = 0
        self.dirty = False


class CapOut(NSObject):
    def initWithFrame_(self, frame):
        self = objc.super(CapOut, self).init()
        self._f = frame
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, stype):
        # CRITICAL: this runs on an SCK background queue. Any exception that
        # escapes a delegate method aborts the whole app via PyObjC (on Python
        # 3.14 it surfaces as "catching classes that do not inherit from
        # BaseException"). So swallow everything and always unlock the buffer.
        try:
            if stype != 0:
                return
            px = CMSampleBufferGetImageBuffer(sbuf)
            if px is None:
                return
            CVPixelBufferLockBaseAddress(px, 1)
            try:
                w = CVPixelBufferGetWidth(px); h = CVPixelBufferGetHeight(px)
                row = CVPixelBufferGetBytesPerRow(px)
                base = CVPixelBufferGetBaseAddress(px)
                data = bytes(base.as_buffer(h * row))   # copy off the locked buffer
            finally:
                CVPixelBufferUnlockBaseAddress(px, 1)
            f = self._f
            with f.lock:
                f.data, f.w, f.h, f.row, f.dirty = data, w, h, row, True
        except Exception:
            pass


class Capturer(NSObject):
    """Owns the SCStream. Built once; started/stopped on demand so capture
    costs nothing while no Claude session is driving the hole."""
    def initWithFrame_displayID_(self, frame, did):
        self = objc.super(Capturer, self).init()
        self._frame = frame
        self._did = did
        self._stream = None
        self._out = None
        self._ready = False
        self._running = False
        self.build()
        return self

    @objc.python_method
    def build(self):
        def got(content, error):
            try:
                if error is not None or not content.displays():
                    print("capture: no content", error, flush=True); return
                disp = content.displays()[0]
                for d in content.displays():
                    if d.displayID() == self._did:
                        disp = d
                        break
                ours = [w for w in content.windows()
                        if w.owningApplication() and
                        w.owningApplication().processID() == os.getpid()]
                filt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(disp, ours)
                cfg = SCK.SCStreamConfiguration.alloc().init()
                cfg.setWidth_(max(2, int(disp.width() * CAP_SCALE)))
                cfg.setHeight_(max(2, int(disp.height() * CAP_SCALE)))
                cfg.setPixelFormat_(1111970369)  # 32BGRA
                cfg.setShowsCursor_(True)
                cfg.setQueueDepth_(3)
                self._out = CapOut.alloc().initWithFrame_(self._frame)
                self._stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
                    filt, cfg, None)
                self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
                    self._out, 0, None, None)
                self._ready = True
            except Exception as e:
                print("capture build error:", repr(e), flush=True)
        SCK.SCShareableContent.getShareableContentWithCompletionHandler_(got)

    def sync_(self, timer):
        """Start capturing while a session is live, stop when it isn't."""
        if not self._ready:
            return
        want = read_level() >= 0.0
        if want and not self._running:
            self._stream.startCaptureWithCompletionHandler_(lambda e: None)
            self._running = True
        elif not want and self._running:
            self._stream.stopCaptureWithCompletionHandler_(lambda e: None)
            self._running = False


# -------------------------------------------------------------- GL overlay --
def encode_cursor(level):
    """level -> amber cursor color bytes (claude-token.py encoding), or zeros."""
    if level is None or level < 0:
        return (0.0, 0.0, 0.0)
    fill = max(0, min(250, round(level * 250)))
    hi, lo = fill >> 4, fill & 0xF
    chk = (hi ^ lo ^ 0x5) & 0xF
    return ((0xF0 | chk) / 255.0, (0xB0 | hi) / 255.0, (0x00 | lo) / 255.0)


def read_level():
    try:
        v = float(open(LEVEL_FILE).read().strip() or "-1")
    except (OSError, ValueError):
        return -1.0
    return v if v < 0 else max(0.0, min(1.0, v))


def read_pos():
    try:
        s = open(POS_FILE).read().strip()
    except OSError:
        return None
    if not s or s.lower().startswith("auto"):
        return None
    try:
        x, y = (float(v) for v in s.replace(",", " ").split()[:2])
        return (max(0., min(1., x)), max(0., min(1., y)))
    except (ValueError, IndexError):
        return None


# settings window rows: (config key, (en,ja) label, min, max, (en,ja) desc).
# Labels/descriptions are language pairs resolved at build time via TR(). The
# handlers use the row INDEX as the widget tag, so SETTINGS_ROWS order ==
# display order. "tint" is a color (NSColorWell); "span" is a toggle.
SETTINGS_ROWS = [
    # --- Motion / 動き ---
    ("speed", ("Drift Speed", "移動速度"), 0.0, 10.0, ("How fast it wanders the screen.", "画面内をさまよう速さ。")),
    ("spin", ("Disk Spin", "回転速度"), 0.0, 3.0, ("How fast the accretion disk turns.", "降着円盤が回る速さ。")),
    # --- Black Hole / ブラックホール ---
    ("size", ("Size", "大きさ"), 0.3, 2.5, ("Overall size of the black hole.", "ブラックホール全体の大きさ。")),
    ("incl", ("Tilt", "傾き"), 0.15, 1.55, ("Disk angle, face-on to edge-on.", "円盤の傾き（正面〜真横）。")),
    ("lens", ("Lens Reach", "レンズ範囲"), 1.5, 8.0, ("How wide the desktop bends around it.", "背景が歪む範囲の広さ。")),
    # --- Accretion Disk / 降着円盤 ---
    ("disk", ("Brightness", "光の強さ"), 0.0, 3.5, ("Brightness of the disk light.", "降着円盤の光の明るさ。")),
    ("contrast", ("Contrast", "くっきり感"), 0.2, 2.5, ("Sharpness of the light streaks.", "光の筋のくっきり感。")),
    ("tint", ("Light Color", "光の色"), 0.0, 1.0, ("Color tone of the disk light — pick your own.", "降着円盤の光の色味。お好みの色を選べます。")),
    # --- Jets / ジェット ---
    ("jet", ("Strength", "強さ"), 0.0, 1.5, ("Strength of the relativistic jets (0 = off).", "相対論的ジェットの強さ（0でオフ）。")),
    ("jetlen", ("Length", "長さ"), 4.0, 40.0, ("How far the jets reach into space.", "ジェットが宇宙へ伸びる長さ。")),
    ("jetflow", ("Outflow Speed", "噴出速度"), 0.0, 3.0, ("How fast the jet plasma blasts outward.", "ジェットのプラズマが噴き出す速さ。")),
    # --- Display / 表示 ---
    ("span", ("Multi-Display", "拡張ディスプレイ"), 0.0, 1.0,
     ("Move the black hole across all monitors. Saving relaunches blackhole-desktop (just this app, not your Mac); settings are kept.",
      "ブラックホールがモニター間を横断します。保存すると blackhole-desktop（このアプリ／Mac本体ではありません）が再起動して反映します。設定は維持されます。")),
]

# index -> (en,ja) section header shown ABOVE that row
SECTION_HEADERS = {
    0: ("Motion", "動き"),
    2: ("Black Hole", "ブラックホール"),
    5: ("Accretion Disk", "降着円盤"),
    8: ("Jets", "ジェット"),
    11: ("Display", "表示"),
}


def _label(text, x, y, w, align_right=False):
    f = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, 18))
    f.setStringValue_(text)
    f.setBezeled_(False); f.setDrawsBackground_(False)
    f.setEditable_(False); f.setSelectable_(False)
    f.setFont_(NSFont.systemFontOfSize_(12))
    if align_right:
        f.setAlignment_(2)  # NSTextAlignmentRight
    return f


class SettingsController(NSObject):
    """Native settings window (size / speed / disk light / lens reach).

    Changes preview live; 保存 keeps them, キャンセル (or closing) reverts to the
    values from when the window was opened, デフォルトに戻す loads the defaults."""
    def init(self):
        self = objc.super(SettingsController, self).init()
        self._win = None
        self._value_fields = {}
        self._sliders = {}
        self._steppers = {}
        self._original = dict(CONFIG_DEFAULTS)
        self._saved = False
        return self

    @objc.python_method
    def show(self):
        # rebuild every time so a language change re-renders all labels
        self._original = dict(read_config())   # snapshot to revert to on cancel
        self._saved = False
        self._build()
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyRegular)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._win.makeKeyAndOrderFront_(None)

    @objc.python_method
    def _build(self):
        if self._win is not None:
            self._win.setDelegate_(None)
            self._win.close()
            self._win = None
        self._value_fields = {}
        self._sliders = {}
        cfg = read_config()
        WIN_W, PAD = 480, 22
        PAD_TOP = 14
        OVERVIEW_H, OVERVIEW_GAP = 34, 18   # two-line intro at the very top
        LANG_H, LANG_GAP = 24, 20
        HINT_H, HINT_GAP = 14, 16
        SEC_GAP, SEC_H = 18, 18
        LINE_H, DESC_H, ROW_GAP = 22, 16, 16
        RESET_W = 24                        # per-row "reset to default" button
        VAL_W = 52                          # numeric field / color-well width
        VAL_X = WIN_W - PAD - RESET_W - 8 - VAL_W      # right value column
        SLIDER_X = 150
        SLIDER_W = VAL_X - 8 - SLIDER_X     # slider fills the gap to the value
        DIV_GAP, STATUS_H, BTN_H, PAD_BOT = 30, 16, 30, 14

        SPAN_DESC_EXTRA = 14   # the span row's description wraps to two lines
        h = (PAD_TOP + OVERVIEW_H + OVERVIEW_GAP + LANG_H + LANG_GAP + HINT_H + HINT_GAP
             + len(SECTION_HEADERS) * (SEC_GAP + SEC_H)
             + len(SETTINGS_ROWS) * (LINE_H + DESC_H + ROW_GAP)
             + SPAN_DESC_EXTRA
             + DIV_GAP + STATUS_H + BTN_H + PAD_BOT)

        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIN_W, h),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable,
            NSBackingStoreBuffered, False)
        win.setTitle_(T("Black Hole Settings", "ブラックホール設定"))
        win.setReleasedWhenClosed_(False)
        win.setDelegate_(self)
        win.center()
        content = win.contentView()

        # registry of translatable views so language switching can relabel them
        # in place (no window teardown / reopen). Each entry: (view, kind, pair).
        self._i18n = [(win, "wintitle", ("Black Hole Settings", "ブラックホール設定"))]

        def reg(view, pair, kind="field"):
            self._i18n.append((view, kind, pair))
            return view

        def secondary(text, x, yy, w, lines=1):
            lbl = _label(text, x, yy, w)
            lbl.setFont_(NSFont.systemFontOfSize_(10))
            lbl.setTextColor_(NSColor.secondaryLabelColor())
            if lines > 1:                       # let long text wrap instead of clip
                lbl.setFrame_(NSMakeRect(x, yy - (lines - 1) * 13, w, 18 + (lines - 1) * 13))
                lbl.cell().setWraps_(True)
                lbl.cell().setLineBreakMode_(0)  # NSLineBreakByWordWrapping
                lbl.cell().setUsesSingleLineMode_(False)
            content.addSubview_(lbl)
            return lbl

        OVERVIEW = ("A black hole drifts across your screen on a three-body-style "
                    "orbit, warping the desktop behind it.",
                    "ブラックホールが画面上で、三体問題のような軌道を描いて周囲の情報を歪ませます。")

        def reset_btn(i, yy):
            rb = NSButton.alloc().initWithFrame_(NSMakeRect(WIN_W - PAD - RESET_W, yy - 1, RESET_W, 22))
            rb.setTitle_("↺")           # ↺ anticlockwise arrow = "revert"
            rb.setBezelStyle_(1)
            rb.setFont_(NSFont.systemFontOfSize_(13))
            rb.setTag_(i)
            rb.setTarget_(self); rb.setAction_("resetItem:")
            rb.setToolTip_(T("Reset to default", "初期値に戻す"))
            self._i18n.append((rb, "tooltip", ("Reset to default", "初期値に戻す")))
            content.addSubview_(rb)
            return rb

        y = h - PAD_TOP

        # overview (topmost) — what this tool does, wrapped to two lines
        y -= OVERVIEW_H
        ov = _label(TR(OVERVIEW), PAD, y, WIN_W - 2 * PAD)
        ov.setFrame_(NSMakeRect(PAD, y, WIN_W - 2 * PAD, OVERVIEW_H))
        ov.setFont_(NSFont.systemFontOfSize_(11))
        ov.cell().setWraps_(True)
        ov.cell().setLineBreakMode_(0)
        ov.cell().setUsesSingleLineMode_(False)
        content.addSubview_(ov)
        reg(ov, OVERVIEW)
        y -= OVERVIEW_GAP

        # language switcher (top-right)
        y -= LANG_H
        ll = _label(T("Language", "言語"), PAD, y + 2, 90)
        content.addSubview_(ll)
        reg(ll, ("Language", "言語"))
        seg = NSSegmentedControl.alloc().initWithFrame_(
            NSMakeRect(WIN_W - PAD - 230, y, 230, LANG_H))
        seg.setSegmentCount_(3)
        for idx, t in enumerate((T("Auto", "自動"), "日本語", "English")):
            seg.setLabel_forSegment_(t, idx)
            seg.setWidth_forSegment_(74, idx)
        seg.setSelectedSegment_({"auto": 0, "ja": 1, "en": 2}.get(cfg.get("lang", "auto"), 0))
        seg.setTarget_(self); seg.setAction_("langChanged:")
        content.addSubview_(seg)
        self._lang_seg = seg                 # only "Auto"/"自動" is language-dependent
        y -= LANG_GAP

        # hint
        y -= HINT_H
        reg(secondary(T("Drag the hole to move it · double-click to free it · right-click for this menu.",
                        "ドラッグで移動・ダブルクリックで自動巡回・右クリックでこのメニュー。"),
                      PAD, y, WIN_W - 2 * PAD),
            ("Drag the hole to move it · double-click to free it · right-click for this menu.",
             "ドラッグで移動・ダブルクリックで自動巡回・右クリックでこのメニュー。"))
        y -= HINT_GAP

        for i, (key, labelpair, lo, hi, descpair) in enumerate(SETTINGS_ROWS):
            label, desc = TR(labelpair), TR(descpair)
            if i in SECTION_HEADERS:
                y -= SEC_GAP + SEC_H
                hdr = _label(TR(SECTION_HEADERS[i]), PAD, y, WIN_W - 2 * PAD)
                hdr.setFont_(NSFont.boldSystemFontOfSize_(12))
                content.addSubview_(hdr)
                reg(hdr, SECTION_HEADERS[i])

            y -= LINE_H
            if key == "span":
                # no per-item reset here: a toggle is trivially flipped back,
                # and a relaunch-triggering revert button would be a foot-gun.
                btn = NSButton.alloc().initWithFrame_(
                    NSMakeRect(PAD, y - 1, WIN_W - 2 * PAD, 20))
                btn.setButtonType_(3)  # switch
                btn.setTitle_("  " + label)
                btn.setState_(int(round(float(cfg.get(key, lo)))))
                btn.setTag_(i)
                btn.setTarget_(self); btn.setAction_("toggleChanged:")
                self._sliders[i] = btn
                content.addSubview_(btn)
                reg(btn, labelpair, "btn_pad")
            elif key == "tint":
                lbl = _label(label, PAD, y, 120)
                content.addSubview_(lbl)
                reg(lbl, labelpair)
                # right value column, shared with the numeric fields
                well = NSColorWell.alloc().initWithFrame_(NSMakeRect(VAL_X, y - 2, VAL_W, 22))
                tnt = cfg.get(key, WARM_TINT)
                if not (isinstance(tnt, (list, tuple)) and len(tnt) == 3):
                    tnt = WARM_TINT
                well.setColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(
                    tnt[0], tnt[1], tnt[2], 1.0))
                well.setTag_(i)
                well.setTarget_(self); well.setAction_("colorChanged:")
                self._sliders[i] = well
                content.addSubview_(well)
                reset_btn(i, y)
            else:
                lbl = _label(label, PAD, y, 120)
                content.addSubview_(lbl)
                reg(lbl, labelpair)
                sl = NSSlider.alloc().initWithFrame_(NSMakeRect(SLIDER_X, y, SLIDER_W, 20))
                sl.setMinValue_(lo); sl.setMaxValue_(hi)
                sl.setDoubleValue_(float(cfg.get(key, lo)))
                sl.setTag_(i)
                sl.setTarget_(self); sl.setAction_("valueChanged:")
                self._sliders[i] = sl
                content.addSubview_(sl)
                vf = NSTextField.alloc().initWithFrame_(NSMakeRect(VAL_X, y - 1, VAL_W, 20))
                vf.setStringValue_("%.2f" % float(cfg.get(key, lo)))
                vf.setAlignment_(2)
                vf.setFont_(NSFont.monospacedDigitSystemFontOfSize_weight_(11, 0.0))
                vf.setTag_(i)
                vf.setTarget_(self); vf.setAction_("textChanged:")
                self._value_fields[i] = vf
                content.addSubview_(vf)
                reset_btn(i, y)

            y -= DESC_H + 4
            if key == "span":
                y -= SPAN_DESC_EXTRA
                reg(secondary(desc, PAD, y, WIN_W - 2 * PAD, lines=2), descpair)
            else:
                reg(secondary(desc, PAD, y, WIN_W - 2 * PAD), descpair)
            y -= ROW_GAP

        # (no divider — the spacing alone separates the footer cleanly)
        self._status = secondary("", PAD, PAD_BOT + BTN_H + 6, WIN_W - 2 * PAD)

        def button(title, x, w, sel, default=False):
            b = NSButton.alloc().initWithFrame_(NSMakeRect(x, PAD_BOT, w, BTN_H))
            b.setTitle_(title); b.setBezelStyle_(1)
            b.setTarget_(self); b.setAction_(sel)
            if default:
                b.setKeyEquivalent_("\r")
            content.addSubview_(b)
            return b
        reg(button(T("Cancel", "キャンセル"), PAD, 96, "cancelSettings:"),
            ("Cancel", "キャンセル"), "title")
        reg(button(T("Reset all", "設定を初期化"), PAD + 102, 110, "resetDefaults:"),
            ("Reset all", "設定を初期化"), "title")
        reg(button(T("Save", "保存"), WIN_W - PAD - 110, 110, "saveSettings:", default=True),
            ("Save", "保存"), "title")
        self._win = win

    @objc.python_method
    def _set_status(self, text):
        if getattr(self, "_status", None) is not None:
            self._status.setStringValue_(text)

    @objc.python_method
    def _apply(self, values):
        values = dict(values)
        values["lang"] = read_config().get("lang", "auto")   # never revert language
        write_config(values)
        for i, (key, _, lo, _hi, _) in enumerate(SETTINGS_ROWS):
            w = self._sliders.get(i)
            if w is None:
                continue
            if key == "span":
                w.setState_(int(round(float(values.get(key, lo)))))
            elif key == "tint":
                tnt = values.get(key, WARM_TINT)
                if isinstance(tnt, (list, tuple)) and len(tnt) == 3:
                    w.setColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(
                        tnt[0], tnt[1], tnt[2], 1.0))
            else:
                v = float(values.get(key, lo))
                w.setDoubleValue_(v)
                if i in self._value_fields:
                    self._value_fields[i].setStringValue_("%.2f" % v)

    def valueChanged_(self, sender):
        i = sender.tag()
        key = SETTINGS_ROWS[i][0]
        val = round(float(sender.doubleValue()), 3)
        cfg = dict(read_config()); cfg[key] = val
        write_config(cfg)                                    # live preview
        self._sliders[i].setDoubleValue_(val)
        self._value_fields[i].setStringValue_("%.2f" % val)

    def toggleChanged_(self, sender):
        i = sender.tag()
        key = SETTINGS_ROWS[i][0]
        val = float(sender.state())
        cfg = dict(read_config()); cfg[key] = val
        write_config(cfg)
        # span (multi-display) needs a relaunch to add/remove the per-monitor
        # windows, but only AFTER the user commits with Save. We deliberately
        # show NO status here: the row's own description already explains the
        # Save-relaunch behavior, so a status line would just duplicate it.

    def doRestart_(self, timer):
        import subprocess
        try:
            subprocess.Popen(["/bin/launchctl", "kickstart", "-k",
                              "gui/%d/com.blackhole.desktop" % os.getuid()])
        except OSError:
            pass

    def colorChanged_(self, sender):
        i = sender.tag()
        key = SETTINGS_ROWS[i][0]
        c = sender.color()
        rgb = c.colorUsingColorSpaceName_("NSCalibratedRGBColorSpace") or c
        try:
            vals = [round(rgb.redComponent(), 3), round(rgb.greenComponent(), 3),
                    round(rgb.blueComponent(), 3)]
        except Exception:
            return
        cfg = dict(read_config()); cfg[key] = vals
        write_config(cfg)                                    # live preview

    def langChanged_(self, sender):
        global _LANG
        pref = {0: "auto", 1: "ja", 2: "en"}.get(sender.selectedSegment(), "auto")
        cfg = dict(read_config()); cfg["lang"] = pref
        write_config(cfg)
        _LANG = _resolve_lang()
        # relabel every translatable view in place — no window teardown/reopen
        for view, kind, pair in getattr(self, "_i18n", []):
            t = TR(pair)
            if kind in ("title", "wintitle"):
                view.setTitle_(t)
            elif kind == "btn_pad":
                view.setTitle_("  " + t)
            elif kind == "tooltip":
                view.setToolTip_(t)
            else:
                view.setStringValue_(t)
        # the only language-dependent segment label is "Auto"/"自動"
        self._lang_seg.setLabel_forSegment_(T("Auto", "自動"), 0)

    def textChanged_(self, sender):
        i = sender.tag()
        key, _, lo, hi, _ = SETTINGS_ROWS[i]
        try:
            val = float(sender.stringValue())
            val = max(lo, min(hi, val))
        except ValueError:
            val = float(read_config().get(key, lo))
        
        val = round(val, 3)
        cfg = dict(read_config()); cfg[key] = val
        write_config(cfg)

        self._sliders[i].setDoubleValue_(val)
        self._value_fields[i].setStringValue_("%.2f" % val)

    def resetItem_(self, sender):
        """Reset a single setting to its default and update just that control."""
        i = sender.tag()
        key = SETTINGS_ROWS[i][0]
        dv = CONFIG_DEFAULTS.get(key)
        cfg = dict(read_config())
        cfg[key] = list(dv) if isinstance(dv, list) else dv
        write_config(cfg)                                    # live preview
        ctrl = self._sliders.get(i)
        if ctrl is None:
            return
        if key == "tint":
            ctrl.setColor_(NSColor.colorWithSRGBRed_green_blue_alpha_(
                dv[0], dv[1], dv[2], 1.0))
        elif key == "span":
            ctrl.setState_(int(round(float(dv))))
        else:
            ctrl.setDoubleValue_(float(dv))
            if i in self._value_fields:
                self._value_fields[i].setStringValue_("%.2f" % float(dv))

    def resetDefaults_(self, sender):
        self._apply(dict(CONFIG_DEFAULTS))                   # preview defaults

    def saveSettings_(self, sender):
        self._saved = True
        # if multi-display was changed, applying needs a relaunch -> do it now
        cur = float(read_config().get("span", 0.0))
        orig = float(self._original.get("span", 0.0))
        if int(round(cur)) != int(round(orig)):
            self._set_status(T("Relaunching blackhole-desktop (not your Mac) — settings are kept.",
                               "blackhole-desktop（Mac本体ではありません）を再起動しています。設定は維持されます。"))
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.6, self, "doRestart:", None, False)
        else:
            self._win.close()

    def cancelSettings_(self, sender):
        self._win.close()                                    # revert in willClose

    def windowWillClose_(self, note):
        if not self._saved:
            self._apply(self._original)                      # cancel / X = revert
        NSApplication.sharedApplication().setActivationPolicy_(
            NSApplicationActivationPolicyAccessory)


# --- physics sim: restricted three-body, dramatic but smooth ----------------
class ThreeBodySim:
    """Restricted three-body motion. Two invisible *stars* form a binary that
    slowly turns about a center which itself drifts across the screen; the one
    visible body — the black hole — falls freely through their combined gravity.

    Modelling it as a heavy binary + a light test body (rather than three equal
    bodies tugging on each other) is the key to the *feel*: the black hole gets
    swept around in big, swooping, cinematic arcs as the gravity field rotates,
    instead of the twitchy, small-amplitude zig-zag that equal-mass close passes
    produce. Heavy softening caps the peak pull, so even a near pass to a star
    bends the path into a dramatic slingshot rather than a sharp jerk."""
    def __init__(self):
        import random
        self.t = 0.0
        self.roam_ph = random.uniform(0.0, 2.0 * math.pi)   # roaming-center phase
        self.bin_ph = random.uniform(0.0, 2.0 * math.pi)    # binary orbital phase
        # visible black hole: start near center with a gentle random heading
        self.x = 0.5 + random.uniform(-0.12, 0.12)
        self.y = 0.5 + random.uniform(-0.12, 0.12)
        ang = random.uniform(0.0, 2.0 * math.pi)
        sp0 = 0.013
        self.vx = sp0 * math.cos(ang)
        self.vy = sp0 * math.sin(ang)
        # gravity field of the two stars
        self.G = 0.0019            # star pull — strong enough for big arcs
        self.soft = 0.15           # large softening — smooth, never jerky
        self.r_bin = 0.20          # half-separation of the binary
        self.bin_rate = 0.055      # how fast the two stars circle each other (slow)
        self.center_pull = 0.010   # faint leash so the BH never escapes the field
        self.wall_push = 0.18      # firm but soft push back from the edges
        self.v_max = 0.032         # speed ceiling (dramatic yet never noisy)
        self.v_min = 0.006         # floor (never fully freezes)
        self.damp = 0.9985         # bleed a touch of energy so it can't wind up

    def _stars(self):
        """Position of the roaming binary center and its two stars at time t."""
        cx = 0.5 + 0.26 * math.sin(self.t * 0.037 + self.roam_ph)
        cy = 0.5 + 0.20 * math.sin(self.t * 0.026 + self.roam_ph * 1.3 + 0.7)
        a = self.t * self.bin_rate + self.bin_ph
        ox, oy = self.r_bin * math.cos(a), self.r_bin * math.sin(a)
        return cx, cy, (cx + ox, cy + oy), (cx - ox, cy - oy)

    def step(self, dt, speed=1.0):
        # The "speed" setting scales the simulated time advanced per real frame.
        # Previously this multiplied dt and then a single hard cap (0.05) clipped
        # it, so at ~60fps everything from ~1.5x upward saturated at the cap and
        # 2.0 vs 3.0 felt identical. Instead, advance the requested amount of
        # simulated time in small *stable* substeps: the speed setting now scales
        # motion linearly all the way to the top with no saturation.
        eff = min(dt * speed, 0.50)          # overall guard against huge jumps after a stall
        # Let faster settings reach proportionally higher peak speeds too, so the
        # dramatic gravitational swoops aren't re-throttled at the top end.
        self._vmax_eff = self.v_max * max(1.0, speed)
        h = 0.02                             # stable substep for the Euler-Cromer integrator
        while eff > 1e-6:
            self._substep(min(h, eff))
            eff -= h

    def _substep(self, dt):
        self.t += dt
        cx, cy, s1, s2 = self._stars()

        # faint pull toward the roaming center keeps the BH bound to the field
        ax = (cx - self.x) * self.center_pull
        ay = (cy - self.y) * self.center_pull

        # gravity of the two stars — the source of the dramatic swooping arcs
        for sx, sy in (s1, s2):
            dx, dy = sx - self.x, sy - self.y
            d2 = dx*dx + dy*dy + self.soft*self.soft
            f = self.G / (d2 * math.sqrt(d2))
            ax += f * dx
            ay += f * dy

        # soft walls (keep it in 0.08..0.92)
        if self.x < 0.08: ax += (0.08 - self.x) * self.wall_push
        if self.x > 0.92: ax += (0.92 - self.x) * self.wall_push
        if self.y < 0.08: ay += (0.08 - self.y) * self.wall_push
        if self.y > 0.92: ay += (0.92 - self.y) * self.wall_push

        # integrate (Euler-Cromer) with mild damping
        vx = (self.vx + ax * dt) * self.damp
        vy = (self.vy + ay * dt) * self.damp

        # speed clamp (not a target): dramatic but never fast/noisy, never frozen.
        # Upper ceiling scales with the speed setting (see step()).
        vmax = getattr(self, "_vmax_eff", self.v_max)
        v = math.sqrt(vx*vx + vy*vy)
        if v > vmax:
            s = vmax / v; vx *= s; vy *= s
        elif 1e-6 < v < self.v_min:
            s = self.v_min / v; vx *= s; vy *= s

        self.vx, self.vy = vx, vy
        self.x += vx * dt
        self.y += vy * dt

    def get_pos(self):
        return self.x, self.y

_sim = ThreeBodySim()


class GLView(NSOpenGLView):
    def initWithFrame_frame_screen_total_(self, rect, frame, screen, total_frame):
        attrs = [NSOpenGLPFAOpenGLProfile, NSOpenGLProfileVersion3_2Core,
                 NSOpenGLPFAColorSize, 24, NSOpenGLPFAAlphaSize, 8,
                 NSOpenGLPFADoubleBuffer, NSOpenGLPFAAccelerated, 0]
        pf = NSOpenGLPixelFormat.alloc().initWithAttributes_(attrs)
        self = objc.super(GLView, self).initWithFrame_pixelFormat_(rect, pf)
        if self is None:
            return None
        self._frame = frame
        self._screen_frame = screen.frame()
        self._total_frame = total_frame
        self._tex = None
        self._prog = None
        self._vao = None
        self._cur = (0., 0., 0.)
        self._prev = (0., 0., 0.)
        self._tchange = 0.0
        self._t0 = None
        self._posmode = 1.0
        self._pin = (0.5, 0.4)
        self._dragging = False
        self._drag_uv = (0.5, 0.4)
        self._level = -1.0
        self._last_t = None
        self._cfg = dict(CONFIG_DEFAULTS)
        return self

    # --- shader helpers ---
    @objc.python_method
    def _compile(self, vsrc, fsrc):
        def sh(kind, src):
            s = GL.glCreateShader(kind)
            GL.glShaderSource(s, src)
            GL.glCompileShader(s)
            if GL.glGetShaderiv(s, GL.GL_COMPILE_STATUS) != GL.GL_TRUE:
                raise RuntimeError(GL.glGetShaderInfoLog(s).decode())
            return s
        p = GL.glCreateProgram()
        GL.glAttachShader(p, sh(GL.GL_VERTEX_SHADER, vsrc))
        GL.glAttachShader(p, sh(GL.GL_FRAGMENT_SHADER, fsrc))
        GL.glLinkProgram(p)
        if GL.glGetProgramiv(p, GL.GL_LINK_STATUS) != GL.GL_TRUE:
            raise RuntimeError(GL.glGetProgramInfoLog(p).decode())
        return p

    def prepareOpenGL(self):
        self.openGLContext().setValues_forParameter_([0], NSOpenGLCPSurfaceOpacity)
        self.openGLContext().makeCurrentContext()
        if PASSTHROUGH:
            vsrc, fsrc = PASS_VERT, PASS_FRAG
        else:
            vsrc = PASS_VERT
            fsrc = open(FRAG_FILE).read()
        try:
            self._prog = self._compile(vsrc, fsrc)
        except Exception as e:
            print("SHADER COMPILE/LINK FAILED:\n", str(e)[:2000], flush=True)
            raise
        self._vao = GL.glGenVertexArrays(1)
        self._tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        self._uni = {n: GL.glGetUniformLocation(self._prog, n) for n in (
            "iResolution", "iTime", "iDate", "iChannel0", "iCurrentCursorColor",
            "iPreviousCursorColor", "iTimeCursorChange", "uPosMode", "uPinned",
            "uSizeScale", "uDiskScale", "uLensReach", "uDiskSpeed",
            "uDiskContrast", "uDiskIncl", "uTint", "uJet", "uJetLen", "uJetFlow")}

    @objc.python_method
    def _upload(self):
        f = self._frame
        with f.lock:
            if not f.dirty or f.data is None:
                return
            data, w, h, row = f.data, f.w, f.h, f.row
            f.dirty = False
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, row // 4)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0,
                        GL.GL_BGRA, GL.GL_UNSIGNED_BYTE, data)
        GL.glPixelStorei(GL.GL_UNPACK_ROW_LENGTH, 0)

    # --- geometry helpers (screen <-> uv, with y top-down) -------------------
    @objc.python_method
    def _screen(self):
        sf = self._screen_frame
        return sf.origin.x, sf.origin.y, sf.size.width, sf.size.height

    @objc.python_method
    def _cursor_uv(self):
        # returns local uv for the current window
        p = NSEvent.mouseLocation()
        ox, oy, w, h = self._screen()
        return ((p.x - ox) / w, 1.0 - (p.y - oy) / h)

    @objc.python_method
    def _global_cursor_uv(self):
        # returns uv relative to total desktop
        p = NSEvent.mouseLocation()
        tf = self._total_frame
        return ((p.x - tf.origin.x) / tf.size.width,
                (p.y - tf.origin.y) / tf.size.height)

    @objc.python_method
    def _aspect(self):
        _, _, w, h = self._screen()
        return w / h if h else 1.0

    @objc.python_method
    def _drift_center_global(self, level, size):
        """Chaotic center from the 3-body sim. Sync is implicit via global _sim."""
        gx, gy = _sim.get_pos()
        
        tf = self._total_frame
        total_aspect = tf.size.width / tf.size.height
        
        # radius in units of total desktop height
        r = hole_radius_uv(level, total_aspect, size)
        mx = r / total_aspect; my = r
        gx = max(mx, min(1.0 - mx, gx))
        gy = max(my, min(1.0 - my, gy))
        return (gx, gy)

    @objc.python_method
    def _hole_center_local(self, level, size):
        pinned = read_pos()
        if self._dragging:
            # while dragging, we use the local cursor uv directly
            return self._drag_uv
        
        tf = self._total_frame
        sf = self._screen_frame
        
        if pinned is not None:
            # pinned is in 0..1 of main screen. We should probably treat it as global
            # or just pin it in global space. For now, treat it as global 0..1.
            gx, gy = pinned
        else:
            gx, gy = self._drift_center_global(level, size)
        
        # Convert global uv (gx, gy) to local window uv (lx, ly).
        # Global uv is top-down (y: 0=top of desktop) to match pos.sh / drag;
        # NSScreen coords are bottom-up, so flip y on the way to pixels.
        px = tf.origin.x + gx * tf.size.width
        py = tf.origin.y + (1.0 - gy) * tf.size.height

        lx = (px - sf.origin.x) / sf.size.width
        ly = 1.0 - (py - sf.origin.y) / sf.size.height  # shader is top-down
        return (lx, ly)

    @objc.python_method
    def _update_state(self, now):
        self._cfg = read_config()
        # advance physics (speed setting scales the time step). Only ONE view
        # drives the shared sim, else with multi-display it would step N times
        # per frame and run N times too fast.
        dt = 0.0 if self._last_t is None else max(0.0, now - self._last_t)
        self._last_t = now
        if getattr(self, "_is_driver", True):
            _sim.step(dt, self._cfg.get("speed", 1.0))

        lvl = read_level()
        self._level = lvl
        # Size is controlled ONLY by the 大きさ (size) setting, so the hole does
        # not resize on its own. The context level just shows (>=0) / hides (<0).
        c = encode_cursor(None if lvl < 0 else SIZE_LEVEL)
        if c != self._cur:
            self._prev, self._cur, self._tchange = self._cur, c, now

        self._pin = self._hole_center_local(SIZE_LEVEL, self._cfg.get("size", 1.0))
        self._posmode = 1.0
        self._update_clickthrough(lvl)

    @objc.python_method
    def _update_clickthrough(self, lvl):
        win = self.window()
        if win is None:
            return
        if lvl < 0.0:
            win.setIgnoresMouseEvents_(True)
            return
        
        lx, ly = self._pin
        ux, uy = self._cursor_uv()
        aspect = self._aspect()
        dist = math.hypot((ux - lx) * aspect, uy - ly)
        grab = max(hole_radius_uv(SIZE_LEVEL, aspect, self._cfg.get("size", 1.0)), 0.04)
        over = self._dragging or dist < grab
        win.setIgnoresMouseEvents_(not over)

    # --- dragging ------------------------------------------------------------
    def acceptsFirstMouse_(self, event):
        return True

    def mouseDown_(self, event):
        if event.clickCount() == 2:
            try:
                open(POS_FILE, "w").write("auto\n")
            except OSError:
                pass
            return
        self._dragging = True
        self._drag_uv = self._cursor_uv()

    def mouseDragged_(self, event):
        if self._dragging:
            ux, uy = self._cursor_uv()
            self._drag_uv = (ux, uy)

    def mouseUp_(self, event):
        if self._dragging:
            self._dragging = False
            ux, uy = self._drag_uv
            
            # Convert local uv back to GLOBAL top-down uv to save (matches
            # the read in _hole_center_local and pos.sh's y:0=top convention)
            tf = self._total_frame
            sf = self._screen_frame
            px = sf.origin.x + ux * sf.size.width
            py = sf.origin.y + (1.0 - uy) * sf.size.height

            gx = (px - tf.origin.x) / tf.size.width
            gy = 1.0 - (py - tf.origin.y) / tf.size.height
            gx = max(0.0, min(1.0, gx)); gy = max(0.0, min(1.0, gy))
            try:
                open(POS_FILE, "w").write("%.4f %.4f\n" % (gx, gy))
            except OSError:
                pass

    # --- right-click context menu -------------------------------------------
    def rightMouseDown_(self, event):
        menu = NSMenu.alloc().initWithTitle_("blackhole")
        def item(title, sel):
            mi = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, sel, "")
            mi.setTarget_(self)
            menu.addItem_(mi)
        item(T("Settings…", "設定…"), "openSettings:")
        item(T("Resume Drift", "自動巡回に戻す"), "resumeDrift:")
        item(T("Center Hole", "中央に配置"), "centerHole:")
        menu.addItem_(NSMenuItem.separatorItem())
        item(T("Quit", "終了"), "quitApp:")
        NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self)

    def openSettings_(self, sender):
        if getattr(self, "_settings", None) is None:
            self._settings = SettingsController.alloc().init()
        self._settings.show()

    def resumeDrift_(self, sender):
        try:
            open(POS_FILE, "w").write("auto\n")
        except OSError:
            pass

    def centerHole_(self, sender):
        try:
            open(POS_FILE, "w").write("0.5 0.5\n")
        except OSError:
            pass

    def quitApp_(self, sender):
        import subprocess
        try:
            subprocess.Popen(["/bin/launchctl", "bootout",
                              "gui/%d/com.blackhole.desktop" % os.getuid()])
        except OSError:
            pass
        NSApplication.sharedApplication().terminate_(None)

    def redraw_(self, timer):
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        import time
        if self._t0 is None:
            self._t0 = time.time()
        now = time.time() - self._t0
        self.openGLContext().makeCurrentContext()
        self._upload()
        if not PASSTHROUGH:
            self._update_state(now)
        bb = self.bounds()
        sc = self.window().backingScaleFactor() if self.window() else 1.0
        pw, ph = int(bb.size.width * sc), int(bb.size.height * sc)
        GL.glViewport(0, 0, pw, ph)
        GL.glClearColor(0.0, 0.0, 0.0, 0.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT)
        GL.glUseProgram(self._prog)
        GL.glBindVertexArray(self._vao)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._tex)
        u = self._uni if not PASSTHROUGH else None
        try:
            GL.glUniform1i(GL.glGetUniformLocation(self._prog, "iChannel0"), 0)
            GL.glUniform3f(GL.glGetUniformLocation(self._prog, "iResolution"), pw, ph, 1.0)
            if u is not None:
                def su(n, fn, *v):
                    loc = u[n]
                    if loc is not None and loc != -1:
                        fn(loc, *v)
                su("iTime", GL.glUniform1f, now)
                su("iDate", GL.glUniform4f, 0, 0, 0, 0)
                su("iCurrentCursorColor", GL.glUniform4f, *self._cur, 1.0)
                su("iPreviousCursorColor", GL.glUniform4f, *self._prev, 1.0)
                su("iTimeCursorChange", GL.glUniform1f, self._tchange)
                su("uPosMode", GL.glUniform1f, self._posmode)
                su("uPinned", GL.glUniform2f, self._pin[0], self._pin[1])
                su("uSizeScale", GL.glUniform1f, float(self._cfg.get("size", 1.0)))
                su("uDiskScale", GL.glUniform1f, float(self._cfg.get("disk", 1.3)))
                su("uLensReach", GL.glUniform1f, float(self._cfg.get("lens", 3.4)))
                su("uDiskSpeed", GL.glUniform1f, float(self._cfg.get("spin", 1.0)))
                su("uDiskContrast", GL.glUniform1f, float(self._cfg.get("contrast", 1.0)))
                su("uDiskIncl", GL.glUniform1f, float(self._cfg.get("incl", 1.5)))
                su("uJet", GL.glUniform1f, float(self._cfg.get("jet", 0.7)))
                su("uJetLen", GL.glUniform1f, float(self._cfg.get("jetlen", 10.0)))
                su("uJetFlow", GL.glUniform1f, float(self._cfg.get("jetflow", 0.8)))
                tnt = self._cfg.get("tint", WARM_TINT)
                if not (isinstance(tnt, (list, tuple)) and len(tnt) == 3):
                    tnt = WARM_TINT
                su("uTint", GL.glUniform3f, float(tnt[0]), float(tnt[1]), float(tnt[2]))
        except Exception as e:
            if getattr(self, "_ue", 0) < 2:
                print("uniform set error:", repr(e), flush=True)
                self._ue = getattr(self, "_ue", 0) + 1
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        self.openGLContext().flushBuffer()


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    
    cfg = read_config()
    span = float(cfg.get("span", 0.0)) > 0.5
    screens = NSScreen.screens()
    
    # Calculate unified bounding box of all selected screens
    target_screens = screens if span else [NSScreen.mainScreen()]
    min_x = min(s.frame().origin.x for s in target_screens)
    min_y = min(s.frame().origin.y for s in target_screens)
    max_x = max(s.frame().origin.x + s.frame().size.width for s in target_screens)
    max_y = max(s.frame().origin.y + s.frame().size.height for s in target_screens)
    
    from Foundation import NSPoint, NSSize
    total_frame = type('Frame', (), {
        'origin': NSPoint(min_x, min_y),
        'size': NSSize(max_x - min_x, max_y - min_y)
    })
    
    global_windows = []
    global_views = []
    global_frames = []
    global_capturers = []
    
    for screen in target_screens:
        sframe = screen.frame()
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            sframe, NSWindowStyleMaskBorderless, NSBackingStoreBuffered, False)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setHasShadow_(False)
        win.setIgnoresMouseEvents_(True)
        win.setLevel_(NSScreenSaverWindowLevel)
        win.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces |
            NSWindowCollectionBehaviorFullScreenAuxiliary |
            NSWindowCollectionBehaviorStationary)
        
        frame = Frame()
        view = GLView.alloc().initWithFrame_frame_screen_total_(
            NSMakeRect(0, 0, sframe.size.width, sframe.size.height), frame, screen, total_frame)
        view._is_driver = (len(global_views) == 0)   # only the first view steps the sim
        win.setContentView_(view)
        win.orderFrontRegardless()
        
        try:
            did = screen.deviceDescription()["NSScreenNumber"].unsignedIntValue()
        except Exception:
            did = 0
            
        capturer = Capturer.alloc().initWithFrame_displayID_(frame, did)
        
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0 / 60.0, view, b"redraw:", None, True)
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        
        captimer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, capturer, b"sync:", None, True)
        NSRunLoop.currentRunLoop().addTimer_forMode_(captimer, NSRunLoopCommonModes)
        
        global_windows.append(win)
        global_views.append(view)
        global_frames.append(frame)
        global_capturers.append(capturer)

    import signal
    signal.signal(signal.SIGINT, lambda *a: app.stop_(None))
    signal.signal(signal.SIGTERM, lambda *a: app.stop_(None))

    app.retain() 
    
    # Store references globally so they are not garbage collected
    global _persistent_refs
    _persistent_refs = {
        "windows": global_windows,
        "views": global_views,
        "frames": global_frames,
        "capturers": global_capturers
    }

    app.run()


if __name__ == "__main__":
    main()
