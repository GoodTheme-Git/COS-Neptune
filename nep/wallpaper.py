"""Procedural Neptune wallpapers: deep teal-to-navy fields with light ribbons
and bokeh, in the spirit of the concept art. Rendered once per size and cached
to disk, since it takes a second or so in numpy.
"""
import os

import numpy as np
from PIL import Image, ImageFilter

from . import theme

PRESETS = {
    # top colour, bottom colour, ribbon colour, ribbon strength, seed
    'Neptune Aurora': ((12, 120, 125), (8, 30, 90), (150, 255, 220), 1.0, 3),
    'Deep Trench': ((10, 60, 100), (2, 8, 30), (110, 190, 255), 0.8, 11),
    'Emerald Drift': ((20, 110, 70), (5, 35, 40), (190, 255, 170), 0.9, 5),
    'Nebula': ((70, 30, 110), (10, 10, 45), (255, 160, 240), 0.9, 21),
    'Solid Midnight': ((18, 42, 78), (18, 42, 78), (0, 0, 0), 0.0, 1),
}


def _ribbon(xx, yy, w, h, rng, phase, amp, freq, thick, tilt):
    """One flowing light ribbon: a sine path with a soft gaussian cross-section."""
    path = (h * (0.55 - tilt * (xx / w)) + amp * h * np.sin(freq * xx / w * 2 * np.pi + phase)
            + 0.03 * h * np.sin(3.3 * xx / w * 2 * np.pi + phase * 1.7))
    d = (yy - path) / (thick * h)
    core = np.exp(-d * d)
    # bright fibres along the ribbon
    fib = 0.5 + 0.5 * np.sin((yy - path) / (thick * h) * 9.0 + xx / w * 4.0 + phase)
    return core * (0.55 + 0.45 * fib ** 6)


def render(name, w, h):
    top, bot, rib, strength, seed = PRESETS.get(name, PRESETS['Neptune Aurora'])
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (yy / h)[..., None]
    base = (np.array(top, np.float32) * (1 - t) + np.array(bot, np.float32) * t)
    # vignette + diagonal light falloff
    cx, cy = w * 0.62, h * 0.18
    dist = np.sqrt(((xx - cx) / w) ** 2 + ((yy - cy) / h) ** 2)
    base *= (1.15 - 0.55 * np.clip(dist, 0, 1.2))[..., None]
    light = np.zeros((h, w), np.float32)
    if strength > 0:
        for i in range(7):
            light += (0.22 + 0.5 * rng.random()) * _ribbon(
                xx, yy, w, h, rng, phase=rng.uniform(0, 6.28),
                amp=rng.uniform(0.08, 0.22), freq=rng.uniform(0.4, 0.9),
                thick=rng.uniform(0.012, 0.06), tilt=rng.uniform(0.2, 0.75))
        # the big sweeping arc from the concept art (lower-left to upper-right)
        arc = np.exp(-(((yy - (h * 0.95 - (xx / w) ** 0.8 * h * 0.85)) / (h * 0.09)) ** 2))
        light += 0.55 * arc
        light *= strength
    col = base + light[..., None] * np.array(rib, np.float32) * 0.55
    img = Image.fromarray(np.clip(col, 0, 255).astype(np.uint8), 'RGB')
    if strength > 0:
        # glowing particles / bokeh
        part = Image.new('L', (w, h), 0)
        pa = np.zeros((h, w), np.float32)
        n = int(w * h / 3500)
        px = rng.uniform(0, w, n)
        py = h * (0.55 - 0.45 * (px / w)) + rng.normal(0, h * 0.12, n)
        for x, y in zip(px, py):
            xi, yi = int(x), int(y)
            if 0 <= xi < w and 0 <= yi < h:
                pa[yi, xi] = rng.uniform(120, 255)
        part = Image.fromarray(pa.astype(np.uint8), 'L')
        glow = part.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(1.6))
        halo = part.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(6))
        g = (np.asarray(glow, np.float32) * 1.6 + np.asarray(halo, np.float32) * 0.9)
        arr = np.asarray(img, np.float32) + g[..., None] * (np.array(rib, np.float32) / 255.0 * 0.6 + 0.4)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB')
        img = img.filter(ImageFilter.GaussianBlur(0.6))
    return img


def get(name, w, h):
    os.makedirs(theme.CACHE, exist_ok=True)
    fn = os.path.join(theme.CACHE, 'wall-%s-%dx%d.png' % (name.replace(' ', '_'), w, h))
    try:
        return Image.open(fn).convert('RGBA')
    except (OSError, ValueError):
        pass
    img = render(name, w, h).convert('RGBA')
    try:
        img.save(fn)
    except OSError:
        pass
    return img


# =============================================================================
# Custom backgrounds
# =============================================================================
# A background is described by a small dict (stored in settings['wall']):
#   {'kind': 'preset',    'name': 'Neptune Aurora'}
#   {'kind': 'solid',     'color': '#1e4e8c'}
#   {'kind': 'gradient',  'colors': ['#..', '#..'], 'style': 'vertical|horizontal|diagonal|radial'}
#   {'kind': 'pattern',   'name': 'Dots', 'fg': '#..', 'bg': '#..', 'scale': 1.0}
#   {'kind': 'text',      'text': '...', 'fg': '#..', 'bg': '#..', 'bg2': '#..', 'layout': 'center|tile|corner',
#                         'font': 'serif|sans|mono', 'size': 64}
#   {'kind': 'nostalgia', 'name': 'Rolling Hills'}
#   {'kind': 'image',     'path': '...', 'fit': 'fill|fit|stretch|center|tile', 'bg': '#..'}
import math

from PIL import ImageDraw

PATTERNS = ['Dots', 'Grid', 'Checker', 'Stripes', 'Diagonal', 'Diamonds', 'Waves', 'Hexagons', 'Bricks', 'Plaid']
NOSTALGIA = ['Teal Classic', 'Rolling Hills', 'Starfield', 'Sunset Grid', 'Blueprint', 'Beveled Tiles',
             'COS Druman', 'Marble']
GRADIENT_STYLES = ['vertical', 'horizontal', 'diagonal', 'radial']
FITS = ['fill', 'fit', 'stretch', 'center', 'tile']
WALL_DIR = os.path.join(theme.STATE, 'wallpapers')


def _rgb(c, default=(30, 60, 100)):
    try:
        if isinstance(c, str):
            c = c.lstrip('#')
            if len(c) == 3:
                c = ''.join(ch * 2 for ch in c)
            return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
        return tuple(int(v) for v in c[:3])
    except (ValueError, TypeError, IndexError):
        return default


def hexcolor(rgb):
    return '#%02x%02x%02x' % tuple(rgb[:3])


def _lerp(a, b, t):
    """Blend two RGB colours by t (an array in 0..1) -> HxWx3 float array."""
    a = np.array(a, np.float32)
    b = np.array(b, np.float32)
    return a + (b - a) * t[..., None]


def gradient(w, h, c1, c2, style='vertical'):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if style == 'horizontal':
        t = xx / max(1, w - 1)
    elif style == 'diagonal':
        t = (xx / max(1, w) + yy / max(1, h)) / 2.0
    elif style == 'radial':
        d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
        t = np.clip(d / 1.42, 0, 1)
    else:
        t = yy / max(1, h - 1)
    return Image.fromarray(np.clip(_lerp(c1, c2, t), 0, 255).astype(np.uint8), 'RGB')


def pattern(name, w, h, fg, bg, scale=1.0):
    s = max(4, int(24 * scale * h / 768.0))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if name == 'Dots':
        cx, cy = (xx % s) - s / 2, (yy % s) - s / 2
        m = np.clip(s * 0.18 + 0.5 - np.sqrt(cx * cx + cy * cy), 0, 1)
    elif name == 'Grid':
        m = ((xx % s) < max(1, s // 12)) | ((yy % s) < max(1, s // 12))
    elif name == 'Checker':
        m = ((xx // s + yy // s) % 2) == 0
    elif name == 'Stripes':
        m = (yy % s) < s / 2
    elif name == 'Diagonal':
        m = ((xx + yy) % s) < s / 2.5
    elif name == 'Diamonds':
        m = (np.abs((xx % s) - s / 2) + np.abs((yy % s) - s / 2)) < s * 0.35
    elif name == 'Waves':
        m = np.abs(((yy + np.sin(xx / s * 1.6) * s * 0.35) % s) - s / 2) < s * 0.12
    elif name == 'Hexagons':
        hy = s * 0.866
        row = np.floor(yy / hy)
        ox = np.where(row % 2 == 1, s / 2, 0)
        cx = ((xx + ox) % s) - s / 2
        cy = (yy % hy) - hy / 2
        d = np.maximum(np.abs(cx) * 0.866 + np.abs(cy) * 0.5, np.abs(cy))
        m = d > s * 0.40
    elif name == 'Bricks':
        bh = s / 2
        row = np.floor(yy / bh)
        ox = np.where(row % 2 == 1, s / 2, 0)
        m = ((yy % bh) < max(1, s // 14)) | (((xx + ox) % s) < max(1, s // 14))
    elif name == 'Plaid':
        a = ((xx % (s * 2)) < s * 0.6).astype(np.float32)
        b = ((yy % (s * 2)) < s * 0.6).astype(np.float32)
        m = np.clip((a + b) * 0.45, 0, 1)
    else:
        m = np.zeros((h, w), bool)
    return Image.fromarray(np.clip(_lerp(bg, fg, m.astype(np.float32)), 0, 255).astype(np.uint8), 'RGB')


def text_wall(spec, w, h):
    from . import gfx
    bg, bg2 = _rgb(spec.get('bg', '#102a4a')), _rgb(spec.get('bg2') or spec.get('bg', '#102a4a'))
    img = gradient(w, h, bg, bg2, 'vertical').convert('RGBA')
    text = str(spec.get('text') or 'COS Neptune')[:200]
    size = max(8, int(float(spec.get('size', 64)) * h / 768.0))
    kind = spec.get('font', 'serif')
    fnt = gfx.font(size, bool(spec.get('bold')), kind if kind in ('serif', 'sans', 'mono', 'light') else 'sans')
    fg = _rgb(spec.get('fg', '#e6f6ff'))
    layout = spec.get('layout', 'center')
    lines = text.split('\n')
    lh = int(size * 1.25)
    if layout == 'tile':
        d = ImageDraw.Draw(img)
        tw = max(gfx.text_width(fnt, ln) for ln in lines) + size
        th = lh * len(lines) + size // 2
        row = 0
        for y in range(-th, h + th, th):
            off = (tw // 2) if row % 2 else 0
            for x in range(-tw, w + tw, tw):
                for i, ln in enumerate(lines):
                    d.text((x + off, y + i * lh), ln, font=fnt, fill=fg + (70,))
            row += 1
        return img.convert('RGB')
    if layout == 'corner':
        x, y, anchor = w - size, h - size - lh * (len(lines) - 1), 'rm'
    else:
        x, y, anchor = w // 2, h // 2 - lh * (len(lines) - 1) // 2, 'mm'
    for i, ln in enumerate(lines):
        gfx.shadow_text(img, (x, y + i * lh), ln, fnt, fg + (255,), (0, 0, 0, 170), radius=max(2, size // 16),
                        anchor=anchor, offset=(0, max(1, size // 24)))
    return img.convert('RGB')


def image_wall(spec, w, h):
    bg = _rgb(spec.get('bg', '#000000'))
    out = Image.new('RGB', (w, h), bg)
    try:
        src = Image.open(spec.get('path', ''))
        src.load()
        src = src.convert('RGB')
    except Exception:
        return gradient(w, h, (40, 40, 48), (10, 10, 14))
    fit = spec.get('fit', 'fill')
    iw, ih = src.size
    if fit == 'stretch':
        return src.resize((w, h), Image.LANCZOS)
    if fit == 'tile':
        for y in range(0, h, ih):
            for x in range(0, w, iw):
                out.paste(src, (x, y))
        return out
    if fit == 'center':
        out.paste(src, ((w - iw) // 2, (h - ih) // 2))
        return out
    s = max(w / iw, h / ih) if fit == 'fill' else min(w / iw, h / ih)
    im = src.resize((max(1, int(iw * s)), max(1, int(ih * s))), Image.LANCZOS)
    out.paste(im, ((w - im.size[0]) // 2, (h - im.size[1]) // 2))
    return out


# -- Nostalgia: original designs in the spirit of 90s and 2000s desktops --------
def nostalgia(name, w, h):
    rng = np.random.default_rng(sum(map(ord, name)))
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    if name == 'Teal Classic':
        return Image.new('RGB', (w, h), (0, 128, 128))
    if name == 'Rolling Hills':
        sky = _lerp((70, 130, 220), (190, 225, 250), yy / h)
        img = Image.fromarray(np.clip(sky, 0, 255).astype(np.uint8), 'RGB').convert('RGBA')
        clouds = Image.new('L', (w, h), 0)
        d = ImageDraw.Draw(clouds)
        for _ in range(9):
            cx, cy = rng.uniform(0, w), rng.uniform(h * 0.05, h * 0.45)
            for _ in range(6):
                rx, ry = rng.uniform(w * 0.03, w * 0.08), rng.uniform(h * 0.025, h * 0.06)
                ox, oy = rng.uniform(-w * 0.06, w * 0.06), rng.uniform(-h * 0.02, h * 0.02)
                d.ellipse((cx + ox - rx, cy + oy - ry, cx + ox + rx, cy + oy + ry), fill=230)
        clouds = clouds.filter(ImageFilter.GaussianBlur(max(2, h / 90)))
        layer = Image.new('RGBA', (w, h), (255, 255, 255, 255))
        layer.putalpha(clouds)
        img.alpha_composite(layer)
        arr = np.asarray(img.convert('RGB'), np.float32).copy()
        for i, (base, amp, col1, col2) in enumerate(((0.62, 0.07, (90, 170, 60), (50, 120, 30)),
                                                      (0.72, 0.06, (70, 150, 45), (35, 100, 20)))):
            ridge = h * (base + amp * np.sin(xx / w * (2.2 + i) * math.pi + i * 1.7)
                         + amp * 0.5 * np.sin(xx / w * 5.3 * math.pi + i))
            m = yy >= ridge
            t = np.clip((yy - ridge) / (h * 0.35), 0, 1)
            arr[m] = _lerp(col1, col2, t)[m]
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB').filter(ImageFilter.GaussianBlur(0.7))
    if name == 'Starfield':
        base = _lerp((2, 2, 10), (10, 6, 28), yy / h)
        neb = np.exp(-(((xx - w * 0.7) / (w * 0.25)) ** 2 + ((yy - h * 0.35) / (h * 0.3)) ** 2))
        base += neb[..., None] * np.array((60, 20, 80), np.float32)
        arr = base.copy()
        n = int(w * h / 900)
        ys, xs = rng.integers(0, h, n), rng.integers(0, w, n)
        arr[ys, xs] = rng.uniform(120, 255, (n, 1)) * np.array((1.0, 1.0, 1.0))
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB')
        glow = img.filter(ImageFilter.GaussianBlur(1.2))
        return Image.blend(img, glow, 0.35)
    if name == 'Sunset Grid':
        horizon = h * 0.58
        sky = _lerp((30, 10, 60), (255, 120, 90), np.clip(yy / horizon, 0, 1))
        arr = sky.copy()
        r = h * 0.2
        sun = ((xx - w / 2) ** 2 + (yy - horizon + r * 0.35) ** 2) < r * r
        bands = ((horizon - yy) % (h * 0.04)) > (h * 0.012 * np.clip((yy - (horizon - r)) / r, 0, 1))
        sunmask = sun & (yy < horizon) & bands
        arr[sunmask] = _lerp((255, 230, 90), (255, 70, 140), np.clip((yy - (horizon - r * 1.4)) / (r * 1.4), 0, 1))[sunmask]
        below = yy >= horizon
        arr[below] = np.array((20, 0, 40), np.float32)
        # perspective floor: lines on the ground plane, each kept ~1.5 px wide on screen
        span = h - horizon + 1
        dz = np.maximum((yy - horizon) / span, 1e-3)
        k = w * 0.06
        u = (xx - w / 2) / (dz * k)
        du = 1.0 / (dz * k)
        gx = np.abs(u - np.round(u)) < np.minimum(0.5, 1.5 * du)
        z = 1.0 / dz
        dzdy = 1.0 / (dz * dz * span)
        gy = np.abs(z * 0.5 - np.round(z * 0.5)) < np.minimum(0.5, 0.75 * dzdy)
        grid = below & (gx | gy)
        arr[grid] = np.array((255, 60, 200), np.float32)
        haze = np.clip(dz * 5.0, 0, 1)[..., None]           # hide the moire where lines crowd the horizon
        arr = np.where(below[..., None], arr * haze + np.array((150, 40, 130), np.float32) * (1 - haze), arr)
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB').filter(ImageFilter.GaussianBlur(0.6))
    if name == 'Blueprint':
        img = pattern('Grid', w, h, (90, 140, 210), (20, 60, 130), 1.0)
        big = pattern('Grid', w, h, (150, 190, 240), (0, 0, 0), 4.0)
        arr = np.maximum(np.asarray(img, np.int16), np.asarray(big, np.int16))
        return Image.fromarray(arr.astype(np.uint8), 'RGB')
    if name == 'Beveled Tiles':
        s = max(8, int(h / 12))
        fx, fy = xx % s, yy % s
        edge = max(2, s // 10)
        arr = np.full((h, w, 3), (120, 130, 150), np.float32)
        hi = (fx < edge) | (fy < edge)
        lo = (fx >= s - edge) | (fy >= s - edge)
        arr[hi] = (190, 200, 215)
        arr[lo & ~hi] = (70, 78, 95)
        return Image.fromarray(arr.astype(np.uint8), 'RGB')
    if name == 'COS Druman':
        from . import gfx
        img = gradient(w, h, (0, 0, 70), (0, 0, 30), 'vertical').convert('RGBA')
        art = [' ____  ___   ____', '/ ___|/ _ \\ / ___|', '| |  | | | |\\___ \\', '| |__| |_| | ___) |',
               ' \\____\\___/ |____/']
        size = max(8, int(h / 26))
        fnt = gfx.font(size, True, 'mono')
        y = h // 2 - size * 4
        for ln in art:
            gfx.shadow_text(img, (w // 2, y), ln, fnt, (90, 255, 140, 255), (0, 80, 30, 200), radius=3,
                            anchor='ma', offset=(0, 0))
            y += int(size * 1.15)
        gfx.shadow_text(img, (w // 2, y + size), 'Channel Operating System  v2.4 "Druman"', gfx.font(max(8, size * 2 // 3), False, 'mono'),
                        (170, 255, 200, 255), (0, 60, 20, 200), radius=2, anchor='ma', offset=(0, 0))
        scan = np.zeros((h, w), np.uint8)
        scan[::3, :] = 40
        dark = Image.new('RGBA', (w, h), (0, 0, 0, 255))
        dark.putalpha(Image.fromarray(scan, 'L'))
        img.alpha_composite(dark)
        return img.convert('RGB')
    if name == 'Marble':
        noise = np.zeros((h, w), np.float32)
        for k in range(5):
            f = 2 ** k
            small = rng.random((max(2, int(h / 160 * f)), max(2, int(w / 160 * f)))).astype(np.float32)
            noise += np.asarray(Image.fromarray((small * 255).astype(np.uint8), 'L').resize((w, h), Image.BICUBIC),
                                np.float32) / 255.0 / f
        v = 0.5 + 0.5 * np.sin((xx / w * 6 + yy / h * 3) * math.pi + noise * 6)
        arr = _lerp((10, 70, 80), (210, 240, 235), v ** 3)
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB')
    return render('Neptune Aurora', w, h)


def build(spec, w, h):
    """Render a background spec (see above) at w x h. Always returns an RGBA image."""
    spec = spec or {}
    kind = spec.get('kind', 'preset')
    if kind == 'preset':
        name = spec.get('name', 'Neptune Aurora')
        if w * h > 200_000:
            return get(name, w, h)
        return render(name, w, h).convert('RGBA')
    if kind == 'solid':
        img = Image.new('RGB', (w, h), _rgb(spec.get('color', '#1e4e8c')))
    elif kind == 'gradient':
        cols = spec.get('colors') or ['#0c787d', '#081e5a']
        img = gradient(w, h, _rgb(cols[0]), _rgb(cols[-1]), spec.get('style', 'vertical'))
    elif kind == 'pattern':
        img = pattern(spec.get('name', 'Dots'), w, h, _rgb(spec.get('fg', '#2a6ab0')), _rgb(spec.get('bg', '#0e2a50')),
                      float(spec.get('scale', 1.0)))
    elif kind == 'text':
        img = text_wall(spec, w, h)
    elif kind == 'nostalgia':
        img = nostalgia(spec.get('name', 'Teal Classic'), w, h)
    elif kind == 'image':
        img = image_wall(spec, w, h)
    elif kind == 'bubble':
        return bubble_cached(spec.get('name', 'COS Bubble'), w, h)
    else:
        img = render('Neptune Aurora', w, h)
    return img.convert('RGBA')


def import_image(path):
    """Copy a picture into Neptune's own wallpaper folder; returns the new path."""
    import shutil
    os.makedirs(WALL_DIR, exist_ok=True)
    base = os.path.basename(path)
    dest = os.path.join(WALL_DIR, base)
    stem, ext = os.path.splitext(base)
    i = 2
    while os.path.exists(dest) and os.path.realpath(dest) != os.path.realpath(path):
        dest = os.path.join(WALL_DIR, '%s (%d)%s' % (stem, i, ext))
        i += 1
    if os.path.realpath(dest) != os.path.realpath(path):
        Image.open(path).verify()          # refuse things that are not pictures
        shutil.copy2(path, dest)
    return dest


# -- Bubbles: the COS 3.1 "bubble" look, drawn at any size ---------------------------
# Designs are laid out on a 1280x720 canvas (u = screen height / 720) around the
# screen centre, so they stay sharp on any screen and simply widen on ultrawides.
BUBBLES = ['COS Bubble', 'COS Multi-Bubble', 'Bubble Field', 'Bubble Sunset', 'Bubble Night', 'Bubble Mint']
_BUBBLE_STYLE = {
    # background top/mid/bottom, bubble top/bottom, rim, swoosh colours (multi only), layout
    'COS Bubble': dict(bg=((0, 174, 255), (0, 64, 255), (0, 50, 255)), ball=((44, 100, 128), (12, 32, 132)),
                       rim=(205, 205, 190), layout='single'),
    'COS Multi-Bubble': dict(bg=((255, 255, 255),) * 3, ball=((44, 100, 128), (12, 32, 132)), rim=(225, 205, 150),
                             swoosh=(((0, 156, 255), (0, 112, 244)), ((0, 122, 255), (0, 86, 250))), layout='multi'),
    'Bubble Field': dict(bg=((4, 60, 140), (2, 20, 80), (1, 8, 40)), ball=((44, 100, 128), (12, 32, 132)),
                         rim=(160, 220, 255), layout='field'),
    'Bubble Sunset': dict(bg=((255, 170, 60), (240, 70, 110), (90, 20, 110)), ball=((150, 60, 140), (60, 20, 110)),
                          rim=(255, 225, 170), layout='single'),
    'Bubble Night': dict(bg=((10, 20, 50), (4, 8, 26), (0, 2, 10)), ball=((20, 60, 90), (6, 14, 50)),
                         rim=(90, 230, 255), layout='night'),
    'Bubble Mint': dict(bg=((240, 255, 250),) * 3, ball=((30, 110, 100), (8, 60, 70)), rim=(200, 240, 220),
                        swoosh=(((40, 200, 170), (20, 160, 140)), ((30, 170, 150), (10, 120, 110))), layout='multi'),
}


def _logo_font(px):
    for p in ('/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf',
              '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        try:
            from PIL import ImageFont
            return ImageFont.truetype(p, px)
        except OSError:
            continue
    from . import gfx
    return gfx.font(px, True)


def bubble_logo(r, ball, rim, text='COS', shadow=True):
    """The glass COS bubble on a transparent square (with room for its shadow).
    r is the bubble radius in pixels."""
    pad = int(r * 0.35)
    size = 2 * (r + pad)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    cx = cy = r + pad
    if shadow:
        sh = Image.new('L', (size, size), 0)
        ImageDraw.Draw(sh).ellipse((cx - r - r * 0.05, cy - r + r * 0.07, cx + r - r * 0.05, cy + r + r * 0.07), fill=170)
        sh = sh.filter(ImageFilter.GaussianBlur(max(2, r * 0.09)))
        layer = Image.new('RGBA', (size, size), (0, 0, 20, 255))
        layer.putalpha(sh)
        out.alpha_composite(layer)
    # body: dark glass, lighter at the top right
    yy, xx = np.mgrid[0:2 * r, 0:2 * r].astype(np.float32)
    t = np.clip((yy * 0.75 + (2 * r - xx) * 0.25) / (2 * r), 0, 1)
    body = _lerp(ball[0], ball[1], t)
    hl = np.exp(-(((xx - r * 1.45) / (r * 0.8)) ** 2 + ((yy - r * 0.5) / (r * 0.9)) ** 2)) * 26
    body = np.clip(body + hl[..., None], 0, 255)
    d = np.sqrt((xx - r + 0.5) ** 2 + (yy - r + 0.5) ** 2)
    alpha = np.clip(r - d, 0, 1) * 255
    disc = Image.fromarray(np.dstack([body, alpha]).astype(np.uint8), 'RGBA')
    out.alpha_composite(disc, (cx - r, cy - r))
    # rim: a fine light ring with a blue inner glow
    ring = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring)
    w1 = max(1, int(r * 0.018))
    rd.ellipse((cx - r, cy - r, cx + r, cy + r), outline=tuple(rim) + (230,), width=w1)
    rd.ellipse((cx - r + w1, cy - r + w1, cx + r - w1, cy + r - w1), outline=(70, 150, 255, 150), width=max(1, w1))
    out.alpha_composite(ring.filter(ImageFilter.GaussianBlur(max(0.5, r * 0.004))))
    # the word, tall and condensed
    if text:
        fnt = _logo_font(max(8, int(r * 0.64)))
        tb = ImageDraw.Draw(Image.new('L', (1, 1))).textbbox((0, 0), text, font=fnt)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        lay = Image.new('L', (tw + 8, th + 8), 0)
        ImageDraw.Draw(lay).text((4 - tb[0], 4 - tb[1]), text, font=fnt, fill=255)
        lay = lay.resize((int(lay.size[0] * 0.84), int(lay.size[1] * 1.30)), Image.LANCZOS)
        white = Image.new('RGBA', lay.size, (255, 255, 255, 255))
        white.putalpha(lay)
        out.alpha_composite(white, (cx - lay.size[0] // 2, cy - lay.size[1] // 2 - int(r * 0.02)))
    return out


def _vgrad3(w, h, top, mid, bot, split=0.55):
    t = np.mgrid[0:h, 0:w][0].astype(np.float32) / max(1, h - 1)
    a = _lerp(top, mid, np.clip(t / split, 0, 1))
    b = _lerp(mid, bot, np.clip((t - split) / (1 - split), 0, 1))
    return np.where((t < split)[..., None], a, b)


def _circle3(p1, p2, p3):
    (x1, y1), (x2, y2), (x3, y3) = p1, p2, p3
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1) + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3) + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    return ux, uy, math.hypot(x1 - ux, y1 - uy)


def bubble(name, w, h):
    st = _BUBBLE_STYLE.get(name, _BUBBLE_STYLE['COS Bubble'])
    u = h / 720.0
    cx = w / 2.0
    X = lambda x: cx + (x - 640) * u          # design x -> screen x (centred)
    arr = _vgrad3(w, h, *st['bg'])
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    layout = st['layout']
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB').convert('RGBA')
    if layout == 'single':
        vig = np.clip(np.sqrt(((xx - cx) / (w * 0.75)) ** 2 + ((yy - h / 2) / (h * 0.9)) ** 2), 0, 1)
        dark = Image.fromarray((vig ** 3 * 90).astype(np.uint8), 'L')
        layer = Image.new('RGBA', (w, h), (0, 0, 30, 255))
        layer.putalpha(dark)
        img.alpha_composite(layer)
    elif layout == 'multi':
        # two great swooshes, each a slice of a huge circle, with soft shadows on the white.
        # They are laid out in the 1280x720 design space stretched to the screen, so on a
        # wide screen they sweep edge to edge instead of turning into round blobs.
        px = min(u, w / 1280.0)                               # screen pixels per design unit (for edges)
        xd = (xx - cx) * (1280.0 / w) + 640
        yd = yy * (720.0 / h)
        for (p1, p2, p3, inside), cols in zip(
                (((0, 520), (450, 385), (700, 0), 'below_left'), ((240, 720), (820, 398), (1280, 315), 'below_right')),
                st['swoosh']):
            ux, uy, rr = _circle3(p1, p2, p3)
            d = np.sqrt((xd - ux) ** 2 + (yd - uy) ** 2)
            m = np.clip((rr - d) * px, 0, 1)
            sh = np.clip((rr + 10 - np.sqrt((xd - ux - 4) ** 2 + (yd - uy - 7) ** 2)) * px, 0, 1)
            shadow = Image.fromarray((sh * 120).astype(np.uint8), 'L').filter(ImageFilter.GaussianBlur(7 * u))
            sl = Image.new('RGBA', (w, h), (0, 10, 40, 255))
            sl.putalpha(shadow)
            img.alpha_composite(sl)
            fill = _lerp(cols[0], cols[1], yy / h)
            edge = np.clip(1 - (rr - d) / 6, 0, 1) * 40              # a lighter lip along the curve
            fill = np.clip(fill + edge[..., None] * np.array((0.5, 0.8, 1.0)), 0, 255)
            lay = Image.fromarray(np.dstack([fill, m * 255]).astype(np.uint8), 'RGBA')
            img.alpha_composite(lay)
    elif layout == 'field':
        rng = np.random.default_rng(31)
        n = int(26 * w / max(1, h) + 20)
        for i in range(n):
            r = int(rng.uniform(8, 70) * u * (0.4 + 0.6 * rng.random()))
            bx, by = rng.uniform(-r, w + r), rng.uniform(-r, h + r)
            a = int(rng.uniform(30, 90))
            if r < 3:
                continue                           # too small to draw at thumbnail size
            g = Image.new('RGBA', (2 * r + 2, 2 * r + 2), (0, 0, 0, 0))
            gd = ImageDraw.Draw(g)
            gd.ellipse((1, 1, 2 * r, 2 * r), fill=(120, 200, 255, a // 3), outline=(190, 235, 255, a + 60),
                       width=max(1, r // 18))
            gd.ellipse((r * 0.45, r * 0.3, r * 0.95, r * 0.65), fill=(255, 255, 255, a))
            g = g.filter(ImageFilter.GaussianBlur(max(0.5, r * 0.04 * rng.uniform(0.5, 3))))
            img.alpha_composite(g, (int(bx - r), int(by - r)))
    elif layout == 'night':
        rng = np.random.default_rng(7)
        stars = np.zeros((h, w), np.float32)
        n = int(w * h / 1400)
        stars[rng.integers(0, h, n), rng.integers(0, w, n)] = rng.uniform(80, 255, n)
        s = Image.fromarray(stars.astype(np.uint8), 'L').filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
        sl = Image.new('RGBA', (w, h), (200, 230, 255, 255))
        sl.putalpha(s)
        img.alpha_composite(sl)
        glow = Image.new('L', (w, h), 0)
        ImageDraw.Draw(glow).ellipse((cx - 230 * u, 360 * u - 230 * u, cx + 230 * u, 360 * u + 230 * u), fill=110)
        glow = glow.filter(ImageFilter.GaussianBlur(60 * u))
        gl = Image.new('RGBA', (w, h), tuple(st['rim']) + (255,))
        gl.putalpha(glow)
        img.alpha_composite(gl)
    r = int(185 * u)
    logo = bubble_logo(r, st['ball'], st['rim'])
    img.alpha_composite(logo, (int(cx - logo.size[0] / 2), int(360 * u - logo.size[1] / 2)))
    return img.convert('RGB')


def bubble_cached(name, w, h):
    if w * h <= 200_000:
        return bubble(name, w, h).convert('RGBA')
    os.makedirs(theme.CACHE, exist_ok=True)
    fn = os.path.join(theme.CACHE, 'bubble-v4-%s-%dx%d.png' % (name.replace(' ', '_'), w, h))
    try:
        return Image.open(fn).convert('RGBA')
    except (OSError, ValueError):
        pass
    img = bubble(name, w, h).convert('RGBA')
    try:
        img.save(fn)
    except OSError:
        pass
    return img
