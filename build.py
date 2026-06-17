#!/usr/bin/env python3
"""Turn blackhole.glsl (a Ghostty custom shader) into a self-contained WebGL2
page (index.html) that renders the hole on a *transparent* background, so it
can float over the desktop instead of over terminal text.

Ghostty injects the uniforms and supplies iChannel0 = the terminal contents.
We have neither, so we:
  * declare the 7 uniforms the shader actually uses,
  * bind iChannel0 to a 1x1 transparent texture (nothing to lens -> the
    background simply reads as empty),
  * rewrite the 4 places the shader writes fragColor so empty space becomes
    transparent (alpha 0), the shadow stays opaque black (it *is* the hole),
    and disk/photon-ring light carries alpha by its own brightness.

The token level still rides in through iCurrentCursorColor exactly as
claude-token.py encodes it, so tokenDecode()/the glide logic are used
verbatim — overlay.py just sets that uniform from the level file.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "blackhole.glsl")
OUT = os.path.join(HERE, "index.html")

# ---- desktop tunables (edit these, then re-run build.py / run.sh) ----------
# HOLE_RADIUS is the master size dial: the hole's whole visual footprint
# (shadow + disk arc + lensing) scales with it.
#   0.02  small corner pet (repo default)
#   0.08  small-medium  <- current
#   0.20+ dramatic, swallows much of the screen
HOLE_RADIUS = 0.08
# Growth curve. AREA_* are the shadow area as a fraction of the screen at 0% /
# 100% context; EASE > 1 keeps it small until the context is genuinely full.
TOKEN_AREA_MIN = 0.0008
TOKEN_AREA_MAX = 0.0120
TOKEN_EASE = 1.4
DISK_OUTER = 4.3          # disk outer edge in r_s (repo 8.0; smaller = narrower light band)
DISK_GAIN = 1.15          # accretion-disk brightness (repo 2.2; lower = less glare). Pulled down so the disk's gas doesn't read as too bright / too solid over a DARK desktop.
EXPOSURE = 1.0            # tonemap exposure for the disk light (repo 1.4)
WINDOW_FALLOFF = 4.2      # how far the lensing reaches, in shadow radii (repo 7.0; lower = tighter)
# --- make the light read as alive, with VISIBLE (not noisy) rotation ---
# The disk is sampled from the live desktop at swirled coords, so a fast/fine
# swirl turns into TV-static shimmer. Slow + coarsen it so the spin is legible.
DISK_CONTRAST = 1.4       # streak sharpness (repo 1.6) — lower = softer bands, less moiré
DISK_SPEED = 2.6          # base orbital speed; the 回転速度 slider multiplies this (repo 5.0)
DISK_WIND = 3.5           # spiral winding tightness — lower = coarser, clearer rotation (repo 7.0)
DILATION_MIN = 1.0        # disk pattern rate when the hole is big. 1.0 = NO slowdown (the gravitational time-dilation theme is disabled): the disk keeps spinning at full speed as context fills, so it stays "startup-beautiful". Slowing it (e.g. 0.5) let the disk's discrete desktop-sampled specks stop blurring together and show as granular moiré over a dark terminal. Size still grows with context.

# WebGL2 (strict GLSL ES 3.00) rejects int/float mixing that Ghostty's GLSL
# compiler silently promotes. TOKEN_LEVEL is an int literal (-1) used in a
# float ternary (`live >= 0.0 ? live : TOKEN_LEVEL`) -> coerce it to float.
def apply_overrides(src):
    src = src.replace("#define TOKEN_LEVEL -1 ", "#define TOKEN_LEVEL -1.0 ")
    for name, val in (("HOLE_RADIUS", HOLE_RADIUS), ("TOKEN_AREA_MIN", TOKEN_AREA_MIN),
                      ("TOKEN_AREA_MAX", TOKEN_AREA_MAX), ("TOKEN_EASE", TOKEN_EASE),
                      ("DISK_OUTER", DISK_OUTER), ("DISK_GAIN", DISK_GAIN),
                      ("EXPOSURE", EXPOSURE), ("DISK_CONTRAST", DISK_CONTRAST),
                      ("DISK_SPEED", DISK_SPEED), ("DISK_WIND", DISK_WIND),
                      ("DILATION_MIN", DILATION_MIN)):
        src = re.sub(r"(const float %s\s*=\s*)[0-9.]+;" % name,
                     r"\g<1>%.4f;" % val, src, count=1)
    return src

shader = apply_overrides(open(SRC).read())
# WebGL sprite build: bake the lensing reach as a constant.
shader = shader.replace("plen / (7.0 * rh)", "plen / (%.2f * rh)" % WINDOW_FALLOFF)

# --- patch the output sites (exact strings, verified against the source) ----
# A helper computing coverage-alpha from emitted brightness.
HELPER = (
    "\n// --- desktop overlay: alpha from emitted brightness -----------------\n"
    "const float OVERLAY_ALPHA_GAIN = 1.6;\n"
    "float _ovAlpha(vec3 c){\n"
    "    return clamp(max(max(c.r, c.g), c.b) * OVERLAY_ALPHA_GAIN, 0.0, 1.0);\n"
    "}\n"
)

patches = [
    # no token signal -> hole hidden -> fully transparent (was: show terminal)
    ("if (lvl < 0.0) { fragColor = texture(iChannel0, uv); return; }",
     "if (lvl < 0.0) { fragColor = vec4(0.0); return; }"),
    # rested / vanished hole -> transparent (was: show terminal)
    ("        fragColor = texture(iChannel0, uv);\n        return;",
     "        fragColor = vec4(0.0);\n        return;"),
    # far field (weak deflection): term is 0 with a blank sky -> alpha by stars
    ("        fragColor = vec4(term + stars(d) * L.star * window * shield, 1.0);",
     "        vec3 _ff = term + stars(d) * L.star * window * shield;\n"
     "        fragColor = vec4(_ff, _ovAlpha(_ff));"),
    # near field final: shadow stays opaque black, everything else by brightness
    ("    fragColor = vec4(col, 1.0);",
     "    fragColor = vec4(col, captured ? 1.0 : _ovAlpha(col));"),
    # manual position: override the auto-drift center with a pinned uv point
    ("    float vis = smoothstep(0.0, 0.10, I);  // hole vanishes entirely when rested",
     "    if (uPosMode > 0.5) center = uPinned;\n"
     "    float vis = smoothstep(0.0, 0.10, I);  // hole vanishes entirely when rested"),
    # when pinned, drop the work-area shield so the hole can sit anywhere
    ("    float shield = vis * smoothstep(WORK_AREA, WORK_AREA + 0.18, yUp);",
     "    float shield = vis * smoothstep(WORK_AREA, WORK_AREA + 0.18, yUp);\n"
     "    if (uPosMode > 0.5) shield = vis;"),
]

for old, new in patches:
    if old not in shader:
        sys.exit(f"build.py: expected source fragment not found:\n{old!r}")
    shader = shader.replace(old, new, 1)

# insert the helper just before mainImage
marker = "void mainImage(out vec4 fragColor, in vec2 fragCoord) {"
shader = shader.replace(marker, HELPER + "\n" + marker, 1)

FRAG_HEADER = """#version 300 es
precision highp float;
precision highp int;
uniform vec3  iResolution;
uniform float iTime;
uniform vec4  iDate;
uniform sampler2D iChannel0;
uniform vec4  iCurrentCursorColor;
uniform vec4  iPreviousCursorColor;
uniform float iTimeCursorChange;
uniform float uPosMode;   // 0 = auto drift, 1 = pinned to uPinned
uniform vec2  uPinned;    // uv (x: left->right, y: top->bottom)
out vec4 _fragColor;
"""

FRAG_FOOTER = """
void main() {
    // Ghostty's fragCoord.y runs top-down; WebGL's gl_FragCoord.y runs
    // bottom-up. Flip it so the shader (and the uv-based positions) match the
    // top-down convention it was written for.
    vec2 fc = vec2(gl_FragCoord.x, iResolution.y - gl_FragCoord.y);
    vec4 c;
    mainImage(c, fc);
    _fragColor = c;
}
"""

fragment = FRAG_HEADER + "\n" + shader + "\n" + FRAG_FOOTER

# embed as a JS template literal; escape backticks/backslashes/${ }
esc = fragment.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")

html = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html,body{margin:0;padding:0;width:100%;height:100%;background:transparent;overflow:hidden}
  canvas{display:block;width:100vw;height:100vh;background:transparent}
</style></head><body>
<canvas id="c"></canvas>
<script>
const FRAG = `__FRAG__`;
const VERT = `#version 300 es
in vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }`;

const canvas = document.getElementById('c');
const gl = canvas.getContext('webgl2', {alpha:true, premultipliedAlpha:false, antialias:false});
if(!gl){ document.body.innerHTML = 'WebGL2 unavailable'; }

function sh(type, src){
  const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
  if(!gl.getShaderParameter(s, gl.COMPILE_STATUS)){
    const log = gl.getShaderInfoLog(s);
    console.error(log); window.__shaderError = log;
    if(type === gl.FRAGMENT_SHADER) window.__fragLog = log; else window.__vertLog = log;
  }
  return s;
}
const prog = gl.createProgram();
gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT));
gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
gl.bindAttribLocation(prog, 0, 'p');
gl.linkProgram(prog);
if(!gl.getProgramParameter(prog, gl.LINK_STATUS)){ console.error(gl.getProgramInfoLog(prog)); window.__shaderError = gl.getProgramInfoLog(prog); }
gl.useProgram(prog);

const buf = gl.createBuffer();
gl.bindBuffer(gl.ARRAY_BUFFER, buf);
gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 3,-1, -1,3]), gl.STATIC_DRAW);
gl.enableVertexAttribArray(0);
gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);

// iChannel0: 1x1 transparent texture (no terminal to lens)
const tex = gl.createTexture();
gl.bindTexture(gl.TEXTURE_2D, tex);
gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 1,1,0, gl.RGBA, gl.UNSIGNED_BYTE, new Uint8Array([0,0,0,0]));
gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);

const U = n => gl.getUniformLocation(prog, n);
const uRes=U('iResolution'), uTime=U('iTime'), uDate=U('iDate'),
      uCh0=U('iChannel0'), uCur=U('iCurrentCursorColor'), uPrev=U('iPreviousCursorColor'),
      uChg=U('iTimeCursorChange'), uPosMode=U('uPosMode'), uPinned=U('uPinned');
gl.uniform1i(uCh0, 0);

// ---- manual position: 'auto' drift, or pinned to a uv point ---------------
let posMode = 0.0, pinned = [0.5, 0.4];
window.__setPos = function(mode, x, y){
  posMode = mode ? 1.0 : 0.0;
  if(x != null && y != null){ pinned = [x, y]; }
};

// ---- token level -> amber cursor color (claude-token.py's exact encoding) --
function encode(level){
  if(level == null || level < 0) return [0,0,0];       // no signature -> hole hidden
  let fill = Math.max(0, Math.min(250, Math.round(level*250)));
  const hi = fill >> 4, lo = fill & 0xF, chk = (hi ^ lo ^ 0x5) & 0xF;
  return [(0xF0|chk)/255, (0xB0|hi)/255, (0x00|lo)/255];
}
let cur = [0,0,0], prev = [0,0,0], changeT = 0, t0 = performance.now();
window.__setLevel = function(level){
  const c = encode(level);
  if(c[0]!==cur[0]||c[1]!==cur[1]||c[2]!==cur[2]){
    prev = cur; cur = c; changeT = (performance.now()-t0)/1000;
  }
};

function resize(){
  const dpr = window.devicePixelRatio || 1;
  canvas.width  = Math.floor(window.innerWidth  * dpr);
  canvas.height = Math.floor(window.innerHeight * dpr);
  gl.viewport(0,0,canvas.width,canvas.height);
}
window.addEventListener('resize', resize); resize();

function frame(){
  const t = (performance.now()-t0)/1000;
  gl.uniform3f(uRes, canvas.width, canvas.height, 1.0);
  gl.uniform1f(uTime, t);
  gl.uniform4f(uDate, 0,0,0,0);
  gl.uniform4f(uCur, cur[0],cur[1],cur[2],1.0);
  gl.uniform4f(uPrev, prev[0],prev[1],prev[2],1.0);
  gl.uniform1f(uChg, changeT);
  gl.uniform1f(uPosMode, posMode);
  gl.uniform2f(uPinned, pinned[0], pinned[1]);
  gl.clearColor(0,0,0,0);
  gl.clear(gl.COLOR_BUFFER_BIT);
  gl.drawArrays(gl.TRIANGLES, 0, 3);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
</script></body></html>
"""

html = html.replace("__FRAG__", esc)
open(OUT, "w").write(html)
print(f"wrote {OUT} ({len(html)} bytes); fragment {len(fragment)} chars")


# ===========================================================================
# Capture-mode fragment for the native OpenGL renderer (overlay_gl.py).
# Same shader, but iChannel0 is the LIVE DESKTOP capture (not transparent), so
# the lensing actually bends the desktop. Output alpha is the lensing coverage:
# opaque where the hole warps / the disk glows / the shadow sits, transparent
# elsewhere so the untouched desktop shows through with no copy/lag.
# ===========================================================================
cap = apply_overrides(open(SRC).read())

CAP_HELPER = (
    "\n// --- capture overlay: alpha = lensing coverage ----------------------\n"
    "const float OVERLAY_ALPHA_GAIN = 3.0;\n"
    "const float CAP_EDGE = 70.0;  // coverage-alpha gain. alpha = 1-exp(-window*window*CAP_EDGE): a SMOOTH, kink-free saturating curve.\n"
    "// The overlay window is transparent, so wherever alpha < 1 the UNdistorted live\n"
    "// desktop shows through underneath. The ghost (a bent copy coexisting with the\n"
    "// straight original) happens when a *displaced* sample is drawn at partial\n"
    "// alpha. Cure: keep this RADIUS-based exp fade (gradual -> no hard 'veil' edge),\n"
    "// but make CAP_EDGE large so the FULLY-opaque core covers the whole region where\n"
    "// the lensing shift is non-negligible; alpha only fades out in the far tail where\n"
    "// the displacement (and so the bent-vs-real difference) is already sub-pixel.\n"
    "// NB: driving alpha off the per-pixel shift instead makes the alpha edge track\n"
    "// the lens's steep displacement gradient -> a thin bright ring (the 'veil'); the\n"
    "// gradual radial fade below avoids that while still killing the ghost.\n"
    "float _ovAlpha(vec3 c){ return clamp(max(max(c.r,c.g),c.b)*OVERLAY_ALPHA_GAIN,0.0,1.0); }\n"
    "vec3 _desktopSample(vec2 u){\n"
    "    // soft 9-tap blur (wider kernel + diagonals): averages out fine desktop\n"
    "    // detail so the swirling disk reads as a coherent glow, not granular\n"
    "    // 'photon' specks. Matters most over a DARK desktop, where sparse bright\n"
    "    // pixels (terminal text) would otherwise sample into visible moving dots.\n"
    "    float e = 0.017;\n"
    "    vec3 s  = texture(iChannel0, mirrorUV(u)).rgb * 0.25;\n"
    "    s += texture(iChannel0, mirrorUV(u + vec2(e, 0.0))).rgb * 0.125;\n"
    "    s += texture(iChannel0, mirrorUV(u - vec2(e, 0.0))).rgb * 0.125;\n"
    "    s += texture(iChannel0, mirrorUV(u + vec2(0.0, e))).rgb * 0.125;\n"
    "    s += texture(iChannel0, mirrorUV(u - vec2(0.0, e))).rgb * 0.125;\n"
    "    s += texture(iChannel0, mirrorUV(u + vec2(e, e))).rgb * 0.0625;\n"
    "    s += texture(iChannel0, mirrorUV(u + vec2(e, -e))).rgb * 0.0625;\n"
    "    s += texture(iChannel0, mirrorUV(u + vec2(-e, e))).rgb * 0.0625;\n"
    "    s += texture(iChannel0, mirrorUV(u + vec2(-e, -e))).rgb * 0.0625;\n"
    "    return s;\n"
    "}\n"
    "vec3 _desktopLift(vec3 c){\n"
    "    float l = dot(c, vec3(0.2126, 0.7152, 0.0722));\n"
    "    vec3 chroma = mix(vec3(l), c, 1.2);\n"
    "    // uTint sets the color tone (warm amber by default); the gentle boost\n"
    "    // keeps a bright desktop from blowing the disk out to a flat white ring\n"
    "    chroma *= uTint * 1.12;\n"
    "    return chroma * (0.22 + 0.65 * smoothstep(0.04, 0.95, l));\n"
    "}\n"
    "// relativistic jet emission per unit length at a 3D point, for a beam along\n"
    "// axisN. Shared by the near-field (traced) and far-field (analytic) passes so\n"
    "// they use an IDENTICAL profile -> no brightness seam at the trace boundary.\n"
    "// Structure for an SF look: a bright narrow SPINE inside a dimmer wide SHEATH,\n"
    "// HELICAL knots that twist with height and scroll outward over time, a hot\n"
    "// white-blue core fading to blue at the edges, and an exponential length fade.\n"
    "vec3 _jetSample(vec3 pos, vec3 axisN, float rinL){\n"
    "    float ha = dot(pos, axisN);\n"
    "    float ah = abs(ha);\n"
    "    vec3  perp = pos - axisN * ha;\n"
    "    float rp = length(perp);\n"
    "    float con = JET_WIDTH + JET_FLARE * ah;\n"
    "    float spine  = exp(-(rp * rp) / (con * con * 0.30));\n"
    "    float sheath = exp(-(rp * rp) / (con * con * 2.50));\n"
    "    float aln = smoothstep(rinL * 0.4, rinL * 1.4, ah) * exp(-ah / uJetLen);\n"
    "    // azimuth around the axis (orthonormal basis b1,b2 perpendicular to axisN)\n"
    "    vec3  b1 = normalize(cross(axisN, vec3(0.0, 0.0, 1.0)));\n"
    "    vec3  b2 = cross(axisN, b1);\n"
    "    float ang = atan(dot(perp, b2), dot(perp, b1));\n"
    "    float jy  = ah * 1.3 - mod(iTime * DRIFT_SPEED * uJetFlow * 1.3, 24.0);\n"
    "    float n1  = vnoiseWrapY(vec2(ang / 6.2831853 * 5.0 + ah * 0.6, jy), 24.0);\n"
    "    float turb = mix(0.45, 1.5, n1 * n1 * (3.0 - 2.0 * n1));\n"
    "    float bright = (spine * 1.3 + sheath * 0.5) * aln * turb;\n"
    "    vec3 col = mix(vec3(0.55, 0.74, 1.0), vec3(0.95, 0.97, 1.0), spine);\n"
    "    return col * bright;\n"
    "}\n"
    "// integrate the jet column along the (straight) view ray for screen point pxy.\n"
    "// CENTERED on z0 (where the ray is closest to the axis) and sized to a few beam\n"
    "// widths, so the thin spine is always well sampled -> no z-aliasing banding, and\n"
    "// the SAME integral is used in the near and far field -> no seam at the trace\n"
    "// boundary (bmax circle). axisN = (0, sin incl, cos incl).\n"
    "vec3 _jetColumn(vec2 pxy, vec3 axisN, float rinL){\n"
    "    float si = max(axisN.y, 0.15);\n"
    "    float z0  = (axisN.z * pxy.y) / si;\n"
    "    float ah0 = abs(pxy.y) / si;\n"
    "    float con0 = JET_WIDTH + JET_FLARE * ah0;\n"
    "    float Wz = clamp(4.0 * con0 / si, 0.6, 24.0);\n"
    "    const int JN = 24;\n"
    "    float dz = (2.0 * Wz) / float(JN);\n"
    "    vec3 acc = vec3(0.0);\n"
    "    for (int k = 0; k < JN; k++){\n"
    "        float z = z0 - Wz + (float(k) + 0.5) * dz;\n"
    "        acc += _jetSample(vec3(pxy.x, pxy.y, z), axisN, rinL);\n"
    "    }\n"
    "    return acc * dz;\n"
    "}\n"
)

cap_patches = [
    # no session -> hidden -> show real desktop (transparent)
    ("if (lvl < 0.0) { fragColor = texture(iChannel0, uv); return; }",
     "if (lvl < 0.0) { fragColor = vec4(0.0); return; }"),
    # rested -> transparent
    ("        fragColor = texture(iChannel0, uv);\n        return;",
     "        fragColor = vec4(0.0);\n        return;"),
    # far field: term = weakly-lensed desktop; alpha by the displacement fade
    ("        fragColor = vec4(term + stars(d) * L.star * window * shield, 1.0);",
     "        // far-field jets: continue the bipolar beams out PAST the traced region\n"
     "        // so they reach far into space. Bending is negligible this far out, so\n"
     "        // integrate the jet emission along the straight (undeflected) ray. Only\n"
     "        // the thin vertical strip near the projected axis (|pr.x| small) pays\n"
     "        // the loop cost; everywhere else the early reject skips it.\n"
     "        vec3 _jetf = (uJet > 0.0 && abs(pr.x) < 5.0)\n"
     "                   ? uJet * _jetColumn(pr, vec3(0.0, sin(uDiskIncl), cos(uDiskIncl)), rin) * (L.gain * 0.5)\n"
     "                   : vec3(0.0);\n"
     "        vec3 _ff = term + stars(d) * L.star * window * shield + _jetf;\n"
     "        fragColor = vec4(_ff, clamp(shield * (1.0 - exp(-window * window * CAP_EDGE)) + _ovAlpha(_jetf), 0.0, 1.0));"),
    # near field: col = lensed desktop + disk; alpha by warp fade + disk + shadow
    ("    fragColor = vec4(col, 1.0);",
     "    float diskAlpha = _ovAlpha(vec3(1.0) - exp(-emitc * L.expo)) + (1.0 - trans) * 2.0;\n"
     "    // Inside the shadow: keep the BRIGHT disk light that wraps over/under and\n"
     "    // joins the two side necks (the iconic connecting streak), but drop the\n"
     "    // dim, uniform inner rim that looked unnatural. Brightness gate does both.\n"
     "    float _capl = max(max(col.r, col.g), col.b);\n"
     "    vec3 _capcol = col * smoothstep(0.035, 0.24, _capl);\n"
     "    // jets: same per-pixel column integral as the far field -> seamless across bmax\n"
     "    vec3 _jetf = (uJet > 0.0 && abs(pr.x) < 5.0)\n"
     "               ? uJet * _jetColumn(pr, n, rin) * (L.gain * 0.5) : vec3(0.0);\n"
     "    col += _jetf;\n"
     "    fragColor = vec4(captured ? _capcol : col, captured ? 1.0 : clamp(shield * (1.0 - exp(-window * window * CAP_EDGE)) + diskAlpha + _ovAlpha(_jetf), 0.0, 1.0));"),
    # manual position override
    ("    float vis = smoothstep(0.0, 0.10, I);  // hole vanishes entirely when rested",
     "    if (uPosMode > 0.5) center = uPinned;\n"
     "    float vis = smoothstep(0.0, 0.10, I);  // hole vanishes entirely when rested"),
    # pinned -> drop work-area shield so it can sit anywhere
    ("    float shield = vis * smoothstep(WORK_AREA, WORK_AREA + 0.18, yUp);",
     "    float shield = vis * smoothstep(WORK_AREA, WORK_AREA + 0.18, yUp);\n"
     "    if (uPosMode > 0.5) shield = vis;"),
]
for old, new in cap_patches:
    if old not in cap:
        sys.exit(f"build.py(capture): fragment not found:\n{old!r}")
    cap = cap.replace(old, new, 1)
cap = cap.replace("void mainImage(out vec4 fragColor, in vec2 fragCoord) {",
                  CAP_HELPER + "\nvoid mainImage(out vec4 fragColor, in vec2 fragCoord) {", 1)

# live-adjust uniforms (driven by the settings panel): scale the hole size, the
# disk brightness/spin/contrast/inclination, and the lensing reach -- no rebuild.
def _cap_patch(old, new):
    global cap
    if old not in cap:
        sys.exit("build.py(capture): string not found: %r" % old)
    cap = cap.replace(old, new)
cap = cap.replace("float rh = HOLE_RADIUS * sz;",
                  "float rh = HOLE_RADIUS * sz * uSizeScale;")
_cap_patch("plen / (7.0 * rh)", "plen / (uLensReach * rh)")
cap = cap.replace("exp(-emitc * L.expo)", "exp(-emitc * uDiskScale * L.expo)")  # all
_cap_patch("float spd  = abs(L.speed);", "float spd  = abs(L.speed) * uDiskSpeed;")
_cap_patch("streaks = 0.35 + L.contr * streaks * streaks;",
           "streaks = 0.70 + L.contr * uDiskContrast * 0.16 * streaks;")  # mostly flat: only faint broad spokes for motion legibility; the smooth haze + radial gradient carry the look (creamy gas, not stripes)
_cap_patch("float ci = cos(L.incl), si = sin(L.incl);",
           "float ci = cos(uDiskIncl), si = sin(uDiskIncl);")
_cap_patch(
    "                vec3  cbb   = blackbody(L.temp * tprof * g);      // doppler-shifted color\n"
    "                float boost = pow(g, L.beam);                     // relativistic beaming\n"
    "\n"
    "                float density = band * streaks;\n"
    "                emitc += trans * cbb * (L.gain * 2.2 * density * tprof * tprof * boost);\n"
    "                trans *= 1.0 - clamp(L.opac * density, 0.0, 1.0);",
    "                float boost = pow(g, L.beam);                     // relativistic beaming\n"
    "\n"
    "                float density = band * streaks;\n"
    "                vec2 diskp = vec2(xc.x, dot(xc, e2));\n"
    "                vec2 local = vec2(diskp.x, -diskp.y) / W;\n"
    "                float diskr = max(length(local), 1e-4);\n"
    "                float theta = atan(local.y, local.x);\n"
    "                float pull = smoothstep(rout, rin, rc);\n"
    "                // Desktop-pull rotation for the disk light. TWO unbounded-time\n"
    "                // problems are fixed here: (1) the radial rate (inner faster)\n"
    "                // sheared the sampled desktop into moire -- blended toward rigid\n"
    "                // by KEP_DIFF; (2) spin is proportional to t (= iTime), which\n"
    "                // grows without bound, so cos/sin(theta + spin) lost float32\n"
    "                // precision after a minute or two and the sampling coords\n"
    "                // quantized. On a DARK desktop the sparse bright pixels (terminal\n"
    "                // text) sampled at those jittering coords read as granular\n"
    "                // 'photon' specks = moire; a bright desktop hides it. Bound the\n"
    "                // uniform phase by 40*PI -- seamless for both streams (40pi = 20*2pi\n"
    "                // for spin*1.0, = 27*2pi for spin*1.35) -- and saturate the\n"
    "                // differential past KEP_CAP. pull*1.9 (static swirl shape) stays.\n"
    "                float base_s = 0.6 * spd * dil;\n"
    "                float uniS   = mod(t * base_s, 125.6637061);\n"
    "                float diffS  = 0.7 * KEP_DIFF * (1.0 - kep) * clamp(t * base_s, -KEP_CAP, KEP_CAP);\n"
    "                float spin = -sdir * (uniS - diffS + pull * 1.9);\n"
    "                vec2 stream = vec2(cos(theta + spin), sin(theta + spin))\n"
    "                            * diskr * (1.15 + 0.42 * pull);\n"
    "                vec2 stream2 = vec2(cos(theta + spin * 1.35 + 0.7), sin(theta + spin * 1.35 + 0.7))\n"
    "                             * diskr * (1.55 + 0.25 * sin(mod(t * 0.35, 6.2831853) + rc));\n"
    "                vec2 uv1 = center + rot(stream, -L.roll) / vec2(aspect, 1.0);\n"
    "                vec2 uv2 = center + rot(stream2, -L.roll) / vec2(aspect, 1.0);\n"
    "                vec3 pulled = mix(_desktopSample(uv1), _desktopSample(uv2), 0.35);\n"
    "                vec3 csrc = _desktopLift(pulled);\n"
    "                // WARM color: amber desktop-pull base + a subtle temperature\n"
    "                // gradient (deep orange outer -> warm white inner), never blue.\n"
    "                float _dl = dot(csrc, vec3(0.2126, 0.7152, 0.0722));\n"
    "                float _Tk = mix(2200.0, 5200.0, tprof);\n"
    "                // blackbody floor: over a DARK desktop the desktop-pull (csrc)\n"
    "                // collapses toward 0, so the disk -- and the lensed neck that\n"
    "                // joins the two sides -- goes dim and the brightness gate below\n"
    "                // snaps it off mid-streak. A higher base keeps a content-\n"
    "                // independent glow so the disk reads over a terminal too; over a\n"
    "                // bright desktop the +1.0*_dl term still lets the pulled content lead.\n"
    "                vec3  _sf = blackbody(_Tk) * (0.95 + 1.0 * _dl);\n"
    "                csrc = mix(csrc, _sf, 0.3);\n"
    "                // OUTER = lingering dust: browner AND dimmer the farther out, so\n"
    "                // the disk fades from a hot inner glow to a diffuse dusty haze.\n"
    "                float _o = smoothstep(rin * 1.3, rout, rc);\n"
    "                csrc *= mix(vec3(1.0), vec3(0.80, 0.52, 0.30), _o * 0.85);\n"
    "                // SMOOTH gaseous haze: low-frequency, gentle (no grain/voids),\n"
    "                // advected by the spin -> soft creamy gas instead of stripes.\n"
    "                float _h1 = vnoiseWrapY(vec2(rc * 1.4, turns * 6.0  + spin * 0.35), 6.0);\n"
    "                float _h2 = vnoiseWrapY(vec2(rc * 2.8, turns * 11.0 - spin * 0.6), 11.0);\n"
    "                float _haze = mix(0.80, 1.20, 0.6 * _h1 + 0.4 * _h2);\n"
    "                // strong RADIAL gradient: the inner annulus blazes (friction-hot)\n"
    "                // and fades smoothly outward. Doppler asymmetry (brightness only)\n"
    "                // keeps the approaching side brighter than the receding side.\n"
    "                float _radial = pow(tprof, 1.35);\n"
    "                float streamGain = _radial * (0.16 + 0.9 * boost) * _haze;\n"
    "                emitc += trans * csrc * (L.gain * density * streamGain);\n"
    "                trans *= 1.0 - clamp(L.opac * density * 0.45, 0.0, 0.92);")

# --- relativistic jets: bipolar volumetric beams along the disk axis (+/-n) -
# Sampled per march step so they are lensed by the same geodesic, and accumulated
# as a line integral (* dt). A Gaussian core across the beam, a cone that widens
# with height (JET_FLARE), and an exponential falloff along the axis (uJetLen).
# Driven by the live uJet slider (0 = off, skipped at runtime via the if-guard).
# Rendered in the near-field (traced) region, so the beams emerge from the poles
# out to where the rays hand off to the analytic far field.
# (jets are no longer integrated per-march-step here; they are drawn once per pixel
#  via _jetColumn at the final composition, identically in the near and far field.)

# --- white-circular-line fixes ---------------------------------------------
# (a) Photon ring / top arc EMPHASIS (was: fade the 3rd+ ring to kill a harsh
#     white circle). Count the crossings per ray and shape them for the SF look:
#       1st = direct disk band (1.0)
#       2nd = the lensed arc that sweeps OVER THE TOP of the shadow -> boosted
#             (1.35): this is the iconic Interstellar over-the-top arc.
#       3rd+ = the photon ring hugging the shadow edge -> a controlled, hot
#             blue-white glow (tinted + capped at 0.5, decaying) so it reads as a
#             glowing warped ring, not the old flat white circle.
_cap_patch("vec3  emitc = vec3(0.0);",
           "vec3  emitc = vec3(0.0); int _ncross = 0;")
_cap_patch("emitc += trans * csrc * (L.gain * density * streamGain);",
           "_ncross++;"
           " float _xf = _ncross == 1 ? 1.0 : (_ncross == 2 ? 1.15 : 0.3 * exp(-float(_ncross - 3) * 0.8));"
           " vec3 _ring = _ncross >= 3 ? vec3(1.0, 0.92, 0.78) : vec3(1.0);"
           " emitc += trans * csrc * _ring * (L.gain * density * streamGain) * _xf;")
# (b) Einstein ring: the bright desktop is magnified into a hard white outline
#     right at the shadow edge. Fade the lensed background there (keep it further
#     out), so the edge reads clean instead of a drawn white circle.
_cap_patch("bg += texture(iChannel0, suv).rgb * toward;",
           "bg += texture(iChannel0, suv).rgb * toward * smoothstep(B_CRIT, B_CRIT * 1.7, b);")

# Replace the random-width noise stripes (which alias into moiré / static when
# they rotate) with SMOOTH, REGULAR sine bands. Sine bands alias gracefully (the
# amplitude just fades) instead of producing harsh moiré, and the regular
# spacing reads as clean rotation.
_cap_patch(
    "float streaks = vnoiseWrapY(vec2(rc * 2.8, turns * 19.0 + swirl * 3.0), 19.0) * 0.65 +\n"
    "                                vnoiseWrapY(vec2(rc * 1.0, turns * 9.0  + swirl * 1.5 + 7.0), 9.0) * 0.35;",
    "float _b1 = 0.5 + 0.5 * sin(turns * 5.0 + swirl * 2.0);\n"
    "                float _b2 = 0.5 + 0.5 * sin(turns * 9.0 - swirl * 1.3 + rc * 0.4);\n"
    "                float streaks = mix(_b1, _b2, 0.4);")

# The uniform spin phase is wrapped (mod) in blackhole.glsl so it never grows
# large enough to lose float32 precision and alias back into moiré on long runs.
# The source wraps at 114 — the common period of the NOISE bands it ships with
# (114*3 = 18*19, 114*1.5 = 19*9). But the desktop build above swaps those for
# SINE bands whose swirl frequencies are 2.0 and 1.3, so their common period is
# 20*PI (20π*2.0 = 20*2π, 20π*1.3 = 13*2π). Re-wrap at 20*PI here so the wrap
# stays output-identical (no visible pop) for the sine bands. index.html keeps
# the noise bands, so it correctly keeps 114.
_cap_patch("mod(t * base, 114.0)", "mod(t * base, 62.83185307)")  # 20*PI

CAP_HEADER = """#version 330 core
uniform vec3  iResolution;
uniform float iTime;
uniform vec4  iDate;
uniform sampler2D iChannel0;
uniform vec4  iCurrentCursorColor;
uniform vec4  iPreviousCursorColor;
uniform float iTimeCursorChange;
uniform float uPosMode;
uniform vec2  uPinned;
uniform float uSizeScale;     // settings: hole size multiplier
uniform float uDiskScale;     // settings: disk-light multiplier
uniform float uLensReach;     // settings: lensing reach (shadow radii)
uniform float uDiskSpeed;     // settings: disk spin multiplier
uniform float uDiskContrast;  // settings: streak sharpness multiplier
uniform float uDiskIncl;      // settings: disk inclination (radians)
uniform vec3  uTint;          // settings: disk light color tone
uniform float uJet;           // settings: relativistic jet strength (0 = off)
uniform float uJetLen;        // settings: jet e-fold length in r_s (higher = longer)
uniform float uJetFlow;       // settings: jet outflow speed (knots scrolling out)
out vec4 _fragColor;
"""
CAP_FOOTER = """
void main() {
    // Ghostty fragCoord.y is top-down; GL gl_FragCoord.y is bottom-up. Flip so
    // the shader and the captured desktop texture share the same orientation.
    vec2 fc = vec2(gl_FragCoord.x, iResolution.y - gl_FragCoord.y);
    vec4 c;
    mainImage(c, fc);
    _fragColor = c;
}
"""
cap_fragment = CAP_HEADER + "\n" + cap + "\n" + CAP_FOOTER
CAP_OUT = os.path.join(HERE, "frag_capture.glsl")
open(CAP_OUT, "w").write(cap_fragment)
print(f"wrote {CAP_OUT} ({len(cap_fragment)} chars)")

# Size params for overlay_gl.py: it drives the hole position itself (full-screen
# drift + drag), so it needs the size formula to know the hole's on-screen
# radius (for margins and drag hit-testing). Keep in sync with the shader.
import json as _json
_json.dump({"hole_radius": HOLE_RADIUS, "area_min": TOKEN_AREA_MIN,
            "area_max": TOKEN_AREA_MAX, "ease": TOKEN_EASE},
           open(os.path.join(HERE, "params.json"), "w"))
print("wrote params.json")
