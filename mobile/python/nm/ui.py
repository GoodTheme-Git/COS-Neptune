"""Drawing helpers for COS Neptune Mobile (Python).

Everything is drawn with Neptune's own toolkit (nep.gfx, nep.wallpaper, nep.icons),
so the glass, wallpapers and icons match the desktop. The UI is immediate-mode:
while an app draws, every button it draws registers a hit box with a callback,
and a tap runs the top-most box under the finger.
"""
import functools
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOBILE = os.path.dirname(os.path.dirname(HERE))          # ~/cos/neptune/mobile
NEPTUNE = os.path.dirname(MOBILE)                        # ~/cos/neptune
if NEPTUNE not in sys.path:
    sys.path.insert(0, NEPTUNE)

import numpy as np                                       # noqa: E402
from PIL import Image, ImageDraw, ImageFilter            # noqa: E402

from nep import gfx, icons, wallpaper                    # noqa: E402

W, H = 412, 892          # the phone, in logical pixels
STATUS_H = 28
NAV_H = 52
TITLE_H = 46

GLASS = [
    ('Neptune', (38, 150, 160)), ('Sky', (66, 140, 210)), ('Twilight', (40, 70, 140)), ('Sea', (30, 130, 120)),
    ('Leaf', (80, 160, 60)), ('Lime', (150, 190, 40)), ('Sun', (230, 180, 40)), ('Pumpkin', (230, 120, 30)),
    ('Ruby', (190, 30, 50)), ('Fuchsia', (200, 50, 160)), ('Violet', (120, 70, 190)), ('Lavender', (160, 140, 210)),
    ('Taupe', (150, 130, 110)), ('Chocolate', (100, 60, 40)), ('Slate', (90, 100, 110)), ('Frost', (220, 230, 240)),
]
GLASS_RGB = dict(GLASS)

AURORAS = list(wallpaper.PRESETS)
BUBBLES = list(getattr(wallpaper, '_BUBBLE_STYLE', {}))
WALLPAPERS = AURORAS + BUBBLES

WHITE = (255, 255, 255, 255)
INK = (20, 30, 40, 255)
DIM = (90, 100, 115, 255)
PAPER = (246, 249, 252, 255)
LINE = (210, 218, 228, 255)
RED = (205, 40, 40, 255)


def font(size, bold=False, kind='sans'):
    return gfx.font(int(size), bold, kind)


def text(img, xy, s, size=15, fill=WHITE, bold=False, kind='sans', anchor='la', shadow=False, width=None):
    """Draw text. width: ellipsize to fit. shadow: white text with a soft shadow."""
    s = str(s)
    if not s:
        return
    f = gfx.font_for(s, int(size), bold, kind)
    if width:
        s = gfx.ellipsize(f, s, int(width))
    if shadow:
        gfx.shadow_text(img, xy, s, f, fill=fill, anchor=anchor[:2] if anchor[1] in 'am' else 'la')
    else:
        ImageDraw.Draw(img).text((int(xy[0]), int(xy[1])), s, font=f, fill=gfx.rgba(fill), anchor=anchor)


def text_w(s, size=15, bold=False, kind='sans'):
    return gfx.text_width(gfx.font_for(str(s), int(size), bold, kind), str(s))


def wrap(s, size, width, bold=False, kind='sans'):
    """Split text into lines that fit width (keeps blank lines)."""
    f = gfx.font_for(s, int(size), bold, kind)
    out = []
    for para in str(s).split('\n'):
        if gfx.text_width(f, para) <= width:          # fits: keep it exactly (leading spaces too)
            out.append(para)
            continue
        line = ''
        for word in para.split(' '):
            trial = (line + ' ' + word) if line else word
            if gfx.text_width(f, trial) <= width:
                line = trial
                continue
            if line:
                out.append(line)
            while gfx.text_width(f, word) > width and len(word) > 1:     # a word longer than the line
                cut = len(word)
                while cut > 1 and gfx.text_width(f, word[:cut]) > width:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            line = word
        out.append(line)
    return out


def rrect(img, box, r, fill=None, outline=None, width=1):
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 <= x0 or y1 <= y0:
        return
    if fill is not None and len(gfx.rgba(fill)) == 4 and gfx.rgba(fill)[3] < 255:
        layer = Image.new('RGBA', (x1 - x0, y1 - y0), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), r, fill=gfx.rgba(fill),
                                                outline=gfx.rgba(outline) if outline else None, width=width)
        gfx.alpha_composite_clip(img, layer, x0, y0)
    else:
        ImageDraw.Draw(img).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), r, fill=gfx.rgba(fill) if fill else None,
                                              outline=gfx.rgba(outline) if outline else None, width=width)


def glass(img, box, tint, intensity=0.42, r=14, blur=True, highlight=True, border=True):
    gfx.glass_panel(img, box, r=r, tint=tint, intensity=intensity, blur=blur, highlight=highlight, border=border)


def vgrad(w, h, top, bottom):
    return gfx.vgradient(int(w), int(h), ((0.0, gfx.rgba(top)), (1.0, gfx.rgba(bottom))))


def hexrgb(s):
    s = s.lstrip('#')
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


# ------------------------------------------------------------------ icons

@functools.lru_cache(maxsize=128)
def bubble_icon(name, size, c1, c2):
    """An app icon: Neptune's drawn icon sitting in a glossy glass bubble."""
    s = size * 3                                             # supersample
    img = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    sh = Image.new('L', (s, s), 0)
    ImageDraw.Draw(sh).ellipse((s * 0.06, s * 0.1, s * 0.94, s * 0.98), fill=150)
    sh = sh.filter(ImageFilter.GaussianBlur(s * 0.04))
    lay = Image.new('RGBA', (s, s), (0, 0, 20, 255))
    lay.putalpha(sh)
    img.alpha_composite(lay)
    body = gfx.vgradient(s, s, ((0.0, gfx.rgba(c1)), (1.0, gfx.rgba(c2))))
    m = Image.new('L', (s, s), 0)
    ImageDraw.Draw(m).ellipse((s * 0.05, s * 0.04, s * 0.95, s * 0.94), fill=255)
    body.putalpha(m)
    img.alpha_composite(body)
    # the gloss: a soft white cap over the top half
    gl = Image.new('L', (s, s), 0)
    ImageDraw.Draw(gl).ellipse((s * 0.16, s * 0.07, s * 0.84, s * 0.5), fill=120)
    gl = gl.filter(ImageFilter.GaussianBlur(s * 0.03))
    wl = Image.new('RGBA', (s, s), (255, 255, 255, 255))
    wl.putalpha(gl)
    img.alpha_composite(wl)
    ring = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((s * 0.05, s * 0.04, s * 0.95, s * 0.94), outline=(255, 255, 255, 150),
                                 width=max(2, s // 40))
    img.alpha_composite(ring)
    img = img.resize((size, size), Image.LANCZOS)
    ic = icons.get(name, int(size * 0.56))
    img.alpha_composite(ic, ((size - ic.size[0]) // 2, int(size * 0.47 - ic.size[1] / 2)))
    return img


@functools.lru_cache(maxsize=16)
def logo(r):
    return wallpaper.bubble_logo(int(r), ((44, 100, 128), (12, 32, 132)), (205, 205, 190))


# ------------------------------------------------------------------ wallpapers

@functools.lru_cache(maxsize=8)
def wall(name, w=W, h=H):
    """A phone-shaped wallpaper: Neptune's aurora presets, or a portrait cut of the
    COS bubble wallpapers (the gradient and the glass bubble, placed lower and
    smaller so it isn't hidden behind the icons)."""
    if name.startswith('#'):
        return Image.new('RGBA', (w, h), hexrgb(name) + (255,))
    if name in wallpaper.PRESETS:
        return wallpaper.get(name, w, h)
    st = wallpaper._BUBBLE_STYLE.get(name) if hasattr(wallpaper, '_BUBBLE_STYLE') else None
    if not st:
        return wallpaper.get('Neptune Aurora', w, h)
    arr = wallpaper._vgrad3(w, h, *st['bg'])
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB').convert('RGBA')
    if st.get('layout') == 'night':
        rng = np.random.default_rng(7)
        stars = np.zeros((h, w), np.float32)
        n = w * h // 1400
        stars[rng.integers(0, h, n), rng.integers(0, w, n)] = rng.uniform(80, 255, n)
        s = Image.fromarray(stars.astype(np.uint8), 'L').filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
        sl = Image.new('RGBA', (w, h), (200, 230, 255, 255))
        sl.putalpha(s)
        img.alpha_composite(sl)
    elif st.get('layout') == 'field':
        rng = np.random.default_rng(31)
        for _ in range(40):
            r = int(rng.uniform(6, 50))
            bx, by, a = rng.uniform(-r, w + r), rng.uniform(-r, h + r), int(rng.uniform(30, 90))
            g = Image.new('RGBA', (2 * r + 2, 2 * r + 2), (0, 0, 0, 0))
            gd = ImageDraw.Draw(g)
            gd.ellipse((1, 1, 2 * r, 2 * r), fill=(120, 200, 255, a // 3), outline=(190, 235, 255, a + 60), width=max(1, r // 18))
            gd.ellipse((r * 0.45, r * 0.3, r * 0.95, r * 0.65), fill=(255, 255, 255, a))
            img.alpha_composite(g.filter(ImageFilter.GaussianBlur(0.8)), (int(bx - r), int(by - r)))
    r = int(min(w * 0.3, h * 0.2))
    lg = wallpaper.bubble_logo(r, st['ball'], st['rim'])
    img.alpha_composite(lg, (w // 2 - lg.size[0] // 2, int(h * 0.64) - lg.size[1] // 2))
    return img


@functools.lru_cache(maxsize=40)
def wall_thumb(name, w=84, h=150):
    return wall(name).resize((w, h), Image.LANCZOS)


# ------------------------------------------------------------------ hit boxes

class Hits:
    """Hit boxes collected while drawing. Later boxes are on top."""

    def __init__(self):
        self.boxes = []

    def clear(self):
        self.boxes = []

    def add(self, box, fn, hold=None):
        self.boxes.append((tuple(int(v) for v in box), fn, hold))

    def find(self, x, y):
        for box, fn, hold in reversed(self.boxes):
            if box[0] <= x < box[2] and box[1] <= y < box[3]:
                return box, fn, hold
        return None


def button(img, hits, box, label, fn, kind='normal', size=15, r=10, icon=None):
    """A rounded button. kind: normal, primary, danger, flat, key (calculator)."""
    x0, y0, x1, y1 = box
    if kind == 'primary':
        top, bot, fg = (90, 170, 230), (20, 90, 170), WHITE
    elif kind == 'danger':
        top, bot, fg = (240, 110, 100), (180, 25, 25), WHITE
    elif kind == 'flat':
        top = bot = None
        fg = INK
    elif kind == 'dark':
        top, bot, fg = (80, 90, 105), (40, 46, 56), WHITE
    else:
        top, bot, fg = (252, 253, 255), (222, 229, 238), INK
    if top:
        g = vgrad(x1 - x0, y1 - y0, top + (255,), bot + (255,))
        m = gfx.rounded_mask(x1 - x0, y1 - y0, r)
        g.putalpha(m)
        gfx.alpha_composite_clip(img, g, x0, y0)
        rrect(img, box, r, outline=(0, 0, 0, 70))
        rrect(img, (x0 + 1, y0 + 1, x1 - 1, y0 + (y1 - y0) // 2), max(1, r - 1), fill=(255, 255, 255, 40))
    cx = (x0 + x1) // 2
    if icon:
        ic = icons.get(icon, int(size * 1.4))
        tw = ic.size[0] + (6 + text_w(label, size) if label else 0)
        img.alpha_composite(ic, (cx - tw // 2, (y0 + y1) // 2 - ic.size[1] // 2))
        if label:
            text(img, (cx - tw // 2 + ic.size[0] + 6, (y0 + y1) // 2), label, size, fg, anchor='lm')
    else:
        text(img, (cx, (y0 + y1) // 2), label, size, fg, bold=kind in ('primary', 'danger', 'key'), anchor='mm',
             width=x1 - x0 - 8)
    if hits is not None and fn:
        hits.add(box, fn)


def toggle(img, hits, box, on, fn):
    """An iOS-ish switch, right-aligned in box."""
    x1, cy = box[2], (box[1] + box[3]) // 2
    x0 = x1 - 46
    rrect(img, (x0, cy - 13, x1, cy + 13), 13, fill=(40, 170, 90, 255) if on else (190, 198, 208, 255))
    kx = x1 - 25 if on else x0 + 1
    ImageDraw.Draw(img).ellipse((kx, cy - 12, kx + 24, cy + 12), fill=WHITE, outline=(0, 0, 0, 60))
    hits.add(box, fn)


class Layer:
    """Draw see-through shapes properly: with Layer(img) as d: d.ellipse(...).
    (ImageDraw on an RGBA image replaces pixels instead of blending them.)"""

    def __init__(self, img):
        self.img = img
        self.layer = Image.new('RGBA', img.size, (0, 0, 0, 0))

    def __enter__(self):
        return ImageDraw.Draw(self.layer)

    def __exit__(self, *a):
        self.img.alpha_composite(self.layer)


def dark_fg(rgb):
    """True when a colour is light enough that text on it should be dark."""
    r, g, b = rgb[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b > 170


def angle_xy(cx, cy, r, frac):
    a = frac * 2 * math.pi - math.pi / 2
    return cx + r * math.cos(a), cy + r * math.sin(a)
