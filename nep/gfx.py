"""Neptune graphics primitives.

Everything is a PIL RGBA image. PIL does the heavy lifting in C (blur,
resize, alpha compositing, antialiased text); numpy fills in where PIL has
no primitive (multi-stop gradients, procedural textures).
"""
import functools
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageChops

FONT_DIRS = ['/usr/share/fonts/truetype/noto', '/usr/share/fonts/truetype/dejavu',
             '/usr/share/fonts/truetype/ubuntu', '/usr/share/fonts/truetype/liberation']

_FONT_FILES = {
    ('sans', False): ['NotoSans-Regular.ttf', 'DejaVuSans.ttf', 'Ubuntu-R.ttf'],
    ('sans', True): ['NotoSans-Bold.ttf', 'DejaVuSans-Bold.ttf', 'Ubuntu-B.ttf'],
    ('serif', False): ['NotoSerif-Regular.ttf', 'DejaVuSerif.ttf'],
    ('serif', True): ['NotoSerif-Bold.ttf', 'DejaVuSerif-Bold.ttf'],
    ('mono', False): ['DejaVuSansMono.ttf', 'UbuntuMono-R.ttf', 'LiberationMono-Regular.ttf'],
    ('mono', True): ['DejaVuSansMono-Bold.ttf', 'UbuntuMono-B.ttf'],
    ('light', False): ['Ubuntu-L.ttf', 'DejaVuSans-ExtraLight.ttf', 'NotoSans-Regular.ttf'],
    ('italic', False): ['NotoSans-Italic.ttf', 'DejaVuSans-Oblique.ttf'],
    ('italic', True): ['NotoSans-BoldItalic.ttf', 'DejaVuSans-BoldOblique.ttf'],
    ('serif-italic', False): ['NotoSerif-Italic.ttf', 'DejaVuSerif-Italic.ttf'],
    ('serif-italic', True): ['NotoSerif-BoldItalic.ttf', 'DejaVuSerif-BoldItalic.ttf'],
}


def _find_font(kind, bold):
    for name in _FONT_FILES.get((kind, bold), []) + _FONT_FILES[('sans', False)]:
        for d in FONT_DIRS:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    return None


@functools.lru_cache(maxsize=128)
def font(size, bold=False, kind='sans'):
    path = _find_font(kind, bold)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def needs_symbol_font(text):
    # general punctuation (dashes, quotes, ellipsis, bullet) is in the UI font itself
    return any((0x2000 <= ord(c) < 0x2c00 and not 0x2010 <= ord(c) <= 0x2027) or 0x25a0 <= ord(c) < 0x2700
               for c in text)


@functools.lru_cache(maxsize=64)
def symbol_font(size, bold=False):
    for d in FONT_DIRS:
        p = os.path.join(d, 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')
        if os.path.isfile(p):
            return ImageFont.truetype(p, size)
    return font(size, bold)


def font_for(text, size, bold=False, kind='sans'):
    if kind in ('sans', 'light') and needs_symbol_font(text):
        return symbol_font(size, bold)
    return font(size, bold, kind)


def text_size(fnt, text):
    if not text:
        return 0, fnt.size
    l, t, r, b = fnt.getbbox(text)
    return r - l, fnt.size


def text_width(fnt, text):
    return int(fnt.getlength(text)) if text else 0


def ellipsize(fnt, text, width):
    if text_width(fnt, text) <= width:
        return text
    while text and text_width(fnt, text + '...') > width:
        text = text[:-1]
    return text + '...'


def rgba(c, a=None):
    """'#rrggbb' / (r,g,b) / (r,g,b,a) -> (r,g,b,a)."""
    if isinstance(c, str):
        c = c.lstrip('#')
        c = tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    if len(c) == 3:
        c = tuple(c) + (255 if a is None else a,)
    elif a is not None:
        c = tuple(c[:3]) + (a,)
    return tuple(int(v) for v in c)


def mix(a, b, t):
    a, b = rgba(a), rgba(b)
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(4))


def new(w, h, color=(0, 0, 0, 0)):
    return Image.new('RGBA', (max(1, int(w)), max(1, int(h))), rgba(color))


# -- gradients ---------------------------------------------------------------
def _stops_array(n, stops):
    pos = np.array([s[0] for s in stops], dtype=np.float32)
    cols = np.array([rgba(s[1]) for s in stops], dtype=np.float32)
    x = np.linspace(0.0, 1.0, n, dtype=np.float32)
    out = np.empty((n, 4), dtype=np.float32)
    for ch in range(4):
        out[:, ch] = np.interp(x, pos, cols[:, ch])
    return out


@functools.lru_cache(maxsize=256)
def vgradient(w, h, stops):
    """Vertical gradient. stops = ((pos, color), ...) as a tuple (cacheable)."""
    col = _stops_array(max(1, h), stops)
    arr = np.broadcast_to(col[:, None, :], (max(1, h), max(1, w), 4))
    return Image.fromarray(np.ascontiguousarray(arr).astype(np.uint8), 'RGBA')


@functools.lru_cache(maxsize=64)
def hgradient(w, h, stops):
    row = _stops_array(max(1, w), stops)
    arr = np.broadcast_to(row[None, :, :], (max(1, h), max(1, w), 4))
    return Image.fromarray(np.ascontiguousarray(arr).astype(np.uint8), 'RGBA')


@functools.lru_cache(maxsize=64)
def radial(w, h, stops, cx=0.5, cy=0.5):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = (xx + 0.5 - cx * w) / (w / 2.0)
    dy = (yy + 0.5 - cy * h) / (h / 2.0)
    d = np.clip(np.sqrt(dx * dx + dy * dy), 0, 1)
    table = _stops_array(256, stops)
    idx = (d * 255).astype(np.int32)
    return Image.fromarray(table[idx].astype(np.uint8), 'RGBA')


# -- shapes ------------------------------------------------------------------
SS = 3  # supersampling for antialiased shapes


@functools.lru_cache(maxsize=512)
def rounded_mask(w, h, r, corners=(True, True, True, True)):
    """Antialiased L-mode rounded rectangle mask. corners = (tl, tr, br, bl)."""
    w, h = max(1, int(w)), max(1, int(h))
    r = max(0, min(int(r), w // 2, h // 2))
    if r == 0:
        return Image.new('L', (w, h), 255)
    big = Image.new('L', (w * SS, h * SS), 0)
    d = ImageDraw.Draw(big)
    d.rounded_rectangle((0, 0, w * SS - 1, h * SS - 1), radius=r * SS, fill=255,
                        corners=corners)
    return big.resize((w, h), Image.LANCZOS)


@functools.lru_cache(maxsize=256)
def rounded_outline(w, h, r, width=1, corners=(True, True, True, True)):
    """Antialiased L-mode rounded outline (ring) mask."""
    outer = rounded_mask(w, h, r, corners)
    if w - 2 * width <= 0 or h - 2 * width <= 0:
        return outer
    inner = rounded_mask(w - 2 * width, h - 2 * width, max(0, r - width), corners)
    m = outer.copy()
    hole = Image.new('L', (w, h), 0)
    hole.paste(inner, (width, width))
    return ImageChops.subtract(m, hole)


def fill_mask(img, mask, color, xy=(0, 0)):
    """Composite a solid color through an L mask onto img at xy."""
    layer = Image.new('RGBA', mask.size, rgba(color))
    a = layer.getchannel('A')
    layer.putalpha(ImageChops.multiply(a, mask))
    img.alpha_composite(layer, (int(xy[0]), int(xy[1])))


def paste_masked(img, src, mask, xy=(0, 0)):
    """alpha_composite src onto img with its alpha multiplied by mask."""
    src = src.copy()
    src.putalpha(ImageChops.multiply(src.getchannel('A'), mask))
    img.alpha_composite(src, (int(xy[0]), int(xy[1])))


def round_rect(img, box, r, fill=None, outline=None, width=1, corners=(True,) * 4):
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    if fill is not None:
        if isinstance(fill, Image.Image):
            paste_masked(img, fill.resize((w, h)) if fill.size != (w, h) else fill,
                         rounded_mask(w, h, r, tuple(corners)), (x0, y0))
        else:
            fill_mask(img, rounded_mask(w, h, r, tuple(corners)), fill, (x0, y0))
    if outline is not None:
        fill_mask(img, rounded_outline(w, h, r, width, tuple(corners)), outline, (x0, y0))


# Aero on/off. Off is "Neptune Basic" for slow computers: no blur, no glass
# reflections, no drop shadows. Flat, opaque frames that are cheap to draw.
AERO = True
_NO_SHADOW = None


def set_aero(on):
    global AERO
    AERO = bool(on)


def shadow(w, h, r, blur=14, alpha=110, spread=0):
    """Soft drop shadow image, padded by `pad` on every side. Returns (img, pad).
    With Aero off it is an empty 1x1 image, so callers need no special case."""
    global _NO_SHADOW
    if not AERO:
        if _NO_SHADOW is None:
            _NO_SHADOW = (Image.new('RGBA', (1, 1), (0, 0, 0, 0)), 0)
        return _NO_SHADOW
    return _shadow(w, h, r, blur, alpha, spread)


@functools.lru_cache(maxsize=128)
def _shadow(w, h, r, blur=14, alpha=110, spread=0):
    pad = blur * 2 + spread
    m = Image.new('L', (w + pad * 2, h + pad * 2), 0)
    m.paste(rounded_mask(w + spread * 2, h + spread * 2, r + spread), (pad - spread, pad - spread))
    m = m.filter(ImageFilter.GaussianBlur(blur))
    m = m.point(lambda v: v * alpha // 255)
    img = Image.new('RGBA', m.size, (0, 0, 0, 255))
    img.putalpha(m)
    return img, pad


def blur_region(canvas, box, radius=8, scale=4):
    """Blurred copy of a canvas region. Downscale, blur, upscale: cheap and smooth."""
    x0, y0, x1, y1 = [int(v) for v in box]
    W, H = canvas.size
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    w, h = x1 - x0, y1 - y0
    out = Image.new('RGBA', (max(1, w), max(1, h)), (0, 0, 0, 255))
    if cx1 <= cx0 or cy1 <= cy0:
        return out
    # take a margin so the blur does not darken at the edges
    m = radius * 2
    mx0, my0, mx1, my1 = max(0, cx0 - m), max(0, cy0 - m), min(W, cx1 + m), min(H, cy1 + m)
    src = canvas.crop((mx0, my0, mx1, my1)).convert('RGB')
    sw, sh = max(1, (mx1 - mx0) // scale), max(1, (my1 - my0) // scale)
    small = src.resize((sw, sh), Image.BOX)
    small = small.filter(ImageFilter.GaussianBlur(max(0.5, radius / scale)))
    big = small.resize((mx1 - mx0, my1 - my0), Image.BILINEAR)
    out.paste(big.crop((cx0 - mx0, cy0 - my0, cx1 - mx0, cy1 - my0)), (cx0 - x0, cy0 - y0))
    return out


# -- text ----------------------------------------------------------------------
def draw_text(img, xy, text, fnt, fill=(0, 0, 0, 255), anchor='la'):
    d = ImageDraw.Draw(img)
    d.text((int(xy[0]), int(xy[1])), text, font=fnt, fill=rgba(fill), anchor=anchor)


def _anchor_xy(xy, fnt, text, anchor):
    x, y = int(xy[0]), int(xy[1])
    if anchor[0] == 'm':
        x -= text_width(fnt, text) // 2
    elif anchor[0] == 'r':
        x -= text_width(fnt, text)
    if anchor[1] == 'm':
        y -= fnt.size // 2 + 1
    return x, y


@functools.lru_cache(maxsize=1024)
def _glow_layer(text, fnt, fill, glow, strength, radius):
    tw = text_width(fnt, text) + radius * 6
    th = fnt.size + radius * 6
    m = Image.new('L', (tw, th), 0)
    o = radius * 3
    ImageDraw.Draw(m).text((o, o), text, font=fnt, fill=255)
    halo = m.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(radius))
    halo = halo.point(lambda v: min(255, v * strength // 110))
    layer = Image.new('RGBA', m.size, rgba(glow, 255))
    layer.putalpha(halo)
    ImageDraw.Draw(layer).text((o, o), text, font=fnt, fill=rgba(fill))
    return layer, o


@functools.lru_cache(maxsize=1024)
def _shadow_layer(text, fnt, fill, shade, radius, offset):
    o = radius * 3 + 2
    tw = text_width(fnt, text) + o * 2 + abs(offset[0])
    th = fnt.size + o * 2 + abs(offset[1]) + 4
    m = Image.new('L', (tw, th), 0)
    ImageDraw.Draw(m).text((o + offset[0], o + offset[1]), text, font=fnt, fill=255)
    if radius:
        m = m.filter(ImageFilter.GaussianBlur(radius))
    sc = rgba(shade)
    m = m.point(lambda v: min(255, v * sc[3] // 120))
    layer = Image.new('RGBA', m.size, sc[:3] + (255,))
    layer.putalpha(m)
    ImageDraw.Draw(layer).text((o, o), text, font=fnt, fill=rgba(fill))
    return layer, o


def glow_text(img, xy, text, fnt, fill=(0, 0, 0, 255), glow=(255, 255, 255),
              strength=200, radius=6, anchor='la'):
    """Vista/7 caption text: dark text sitting on a soft white halo. Cached."""
    if not text:
        return
    fnt = font_for_obj(fnt, text)
    layer, o = _glow_layer(text, fnt, rgba(fill), tuple(glow[:3]), strength, radius)
    x, y = _anchor_xy(xy, fnt, text, anchor)
    _ac(img, layer, x - o, y - o)


def shadow_text(img, xy, text, fnt, fill=(255, 255, 255, 255), shade=(0, 0, 0, 160),
                radius=2, anchor='la', offset=(0, 1)):
    """White text with a soft dark shadow (taskbar, desktop icons). Cached."""
    if not text:
        return
    fnt = font_for_obj(fnt, text)
    layer, o = _shadow_layer(text, fnt, rgba(fill), rgba(shade), radius, tuple(offset))
    x, y = _anchor_xy(xy, fnt, text, anchor)
    _ac(img, layer, x - o, y - o)


def font_for_obj(fnt, text):
    if needs_symbol_font(text):
        try:
            return symbol_font(fnt.size)
        except Exception:
            return fnt
    return fnt


def _ac(img, layer, x, y):
    """alpha_composite that tolerates negative / out-of-range positions."""
    W, H = img.size
    w, h = layer.size
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    img.alpha_composite(layer.crop((x0 - x, y0 - y, x1 - x, y1 - y)), (x0, y0))


alpha_composite_clip = _ac


def with_alpha(img, a):
    """Copy of img with its alpha scaled by a (0..1)."""
    if a >= 0.999:
        return img
    img = img.copy()
    al = img.getchannel('A').point(lambda v: int(v * a))
    img.putalpha(al)
    return img


# -- glass ---------------------------------------------------------------------
@functools.lru_cache(maxsize=64)
def glass_streaks(w, h, seed=7):
    """The faint diagonal reflections that run across Aero glass."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = (xx * 0.62 + yy) / 1.0
    a = np.zeros((h, w), np.float32)
    for _ in range(5):
        c = rng.uniform(-h, w)
        width = rng.uniform(18, 70)
        strength = rng.uniform(10, 26)
        a += strength * np.exp(-((d - c) / width) ** 2)
    a = np.clip(a, 0, 60).astype(np.uint8)
    img = Image.new('RGBA', (w, h), (255, 255, 255, 0))
    img.putalpha(Image.fromarray(a, 'L'))
    return img


def glass_panel(canvas, box, r=8, tint=(64, 140, 170), intensity=0.45,
                corners=(True,) * 4, blur=True, highlight=True, border=True,
                transparency=True, blur_radius=10):
    """Composite a sheet of Aero glass onto canvas at box. Returns nothing."""
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    corners = tuple(corners)
    mask = rounded_mask(w, h, r, corners)
    layer = glass_layer(canvas, box, r, tint, intensity, corners, blur, highlight,
                        border, transparency, blur_radius)
    _ac(canvas, layer, x0, y0)
    return mask


def glass_layer(canvas, box, r=8, tint=(64, 140, 170), intensity=0.45,
                corners=(True,) * 4, blur=True, highlight=True, border=True,
                transparency=True, blur_radius=10):
    """The same glass, returned as a standalone RGBA layer (for animation)."""
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    corners = tuple(corners)
    mask = rounded_mask(w, h, r, corners)
    tint = tuple(tint[:3])
    if not AERO:
        transparency = False
    if transparency and blur:
        base = blur_region(canvas, (x0, y0, x1, y1), blur_radius)
        base.alpha_composite(glass_overlay(w, h, tint, round(intensity, 3), highlight))
    elif transparency:
        base = canvas.crop((x0, y0, x1, y1)).convert('RGBA')
        base.alpha_composite(glass_overlay(w, h, tint, round(intensity, 3), highlight))
    else:
        base = Image.new('RGBA', (w, h), opaque_frame(tint))
        if highlight:
            base.alpha_composite(glass_overlay(w, h, tint, 0.0, True))
    base.putalpha(mask)
    if border:
        fill_mask(base, rounded_outline(w, h, r, 1, corners), (10, 20, 30, 190))
        if w > 4 and h > 4:
            inner = rounded_outline(w - 2, h - 2, max(0, r - 1), 1, corners)
            fill_mask(base, inner, (255, 255, 255, 120), (1, 1))
    return base


def opaque_frame(tint):
    return mix((235, 240, 245), tint, 0.55)


def glass_overlay(w, h, tint, intensity, highlight=True, whiten=0):
    """Tint + reflection gradient + streaks, pre-composited (Porter-Duff 'over'
    is associative, so one composite of this equals the three separately).
    With Aero off: no tint over a backdrop, just a soft Basic-style gradient."""
    return _glass_overlay(w, h, tuple(tint[:3]), intensity if AERO else 0.0, highlight, whiten, AERO)


@functools.lru_cache(maxsize=96)
def _glass_overlay(w, h, tint, intensity, highlight, whiten, aero):
    base = Image.new('RGBA', (w, h), rgba(tint, int(255 * intensity)))
    if highlight and not aero:
        base.alpha_composite(vgradient(w, h, ((0.0, (255, 255, 255, 90)), (min(0.5, 30.0 / h), (255, 255, 255, 25)),
                                              (1.0, (255, 255, 255, 0)))))
    elif highlight:
        gl = vgradient(w, h, ((0.0, (255, 255, 255, 80)), (min(0.45, 40.0 / h), (255, 255, 255, 34)),
                              (min(0.46, 41.0 / h), (255, 255, 255, 10)), (1.0, (255, 255, 255, 22))))
        base.alpha_composite(gl)
        base.alpha_composite(glass_streaks(w, h))
    if whiten:
        base.alpha_composite(Image.new('RGBA', (w, h), (255, 255, 255, whiten)))
    return base


# -- misc ------------------------------------------------------------------------
def scale_image(img, factor):
    w, h = img.size
    nw, nh = max(1, int(w * factor)), max(1, int(h * factor))
    return img.resize((nw, nh), Image.BILINEAR)


def tint_image(img, color):
    """Recolor an image to color, keeping its alpha."""
    out = Image.new('RGBA', img.size, rgba(color))
    out.putalpha(img.getchannel('A'))
    return out


def ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def rect_union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def in_rect(x, y, r):
    return r[0] <= x < r[2] and r[1] <= y < r[3]
