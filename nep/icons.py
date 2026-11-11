"""Procedural icons, cursors and the Neptune orb. All drawn with PIL at 4x and
downsampled, so they stay crisp at any size without shipping image files."""
import functools
import math

from PIL import Image, ImageDraw, ImageFilter

from . import gfx

S = 4  # supersample


def _canvas(n):
    return Image.new('RGBA', (n * S, n * S), (0, 0, 0, 0))


def _grad_poly(img, poly, top, bottom, outline=None, width=1):
    """Fill polygon with a vertical gradient."""
    w, h = img.size
    m = Image.new('L', (w, h), 0)
    ImageDraw.Draw(m).polygon(poly, fill=255)
    ys = [p[1] for p in poly]
    y0, y1 = int(min(ys)), int(max(ys)) + 1
    g = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    grad = gfx.vgradient(w, max(1, y1 - y0), ((0, top), (1, bottom)))
    g.paste(grad, (0, y0))
    g.putalpha(m)
    img.alpha_composite(g)
    if outline:
        ImageDraw.Draw(img).line(poly + [poly[0]], fill=gfx.rgba(outline), width=width, joint='curve')


def _grad_rrect(img, box, r, top, bottom, outline=None, width=1):
    x0, y0, x1, y1 = [int(v) for v in box]
    gfx.round_rect(img, box, r, fill=gfx.vgradient(x1 - x0, y1 - y0, ((0, top), (1, bottom))))
    if outline:
        gfx.round_rect(img, box, r, outline=outline, width=width)


def _gloss(img, box, r, a=110):
    x0, y0, x1, y1 = [int(v) for v in box]
    h = (y1 - y0) // 2
    gfx.round_rect(img, (x0 + 2, y0 + 2, x1 - 2, y0 + h), r,
                   fill=gfx.vgradient(x1 - x0 - 4, max(1, h - 2), ((0, (255, 255, 255, a)), (1, (255, 255, 255, 10)))))


def _finish(img, n):
    out = img.resize((n, n), Image.LANCZOS)
    return out


# ---------------------------------------------------------------------------
def draw_folder(n, open_=False):
    u = n * S / 64.0
    img = _canvas(n)
    back = [(4 * u, 12 * u), (24 * u, 12 * u), (29 * u, 18 * u), (60 * u, 18 * u), (60 * u, 54 * u), (4 * u, 54 * u)]
    _grad_poly(img, back, (230, 180, 60), (190, 130, 20), (140, 95, 10), int(1.2 * u))
    # paper
    gfx.round_rect(img, (10 * u, 20 * u, 54 * u, 46 * u), 2 * u, fill=(255, 255, 255, 240))
    front = [(4 * u, 26 * u), (60 * u, 26 * u), (58 * u, 56 * u), (6 * u, 56 * u)]
    _grad_poly(img, front, (255, 220, 120), (235, 170, 40), (160, 110, 20), int(1.2 * u))
    ImageDraw.Draw(img).line([(6 * u, 28 * u), (58 * u, 28 * u)], fill=(255, 245, 200, 200), width=int(1.5 * u))
    return _finish(img, n)


def draw_page(n, lines=True, fold=True, color=(255, 255, 255)):
    u = n * S / 64.0
    img = _canvas(n)
    poly = [(12 * u, 4 * u), (42 * u, 4 * u), (54 * u, 16 * u), (54 * u, 60 * u), (12 * u, 60 * u)]
    _grad_poly(img, poly, color, gfx.mix(color, (200, 210, 220), 0.5), (110, 120, 135), int(1.4 * u))
    d = ImageDraw.Draw(img)
    if fold:
        d.polygon([(42 * u, 4 * u), (42 * u, 16 * u), (54 * u, 16 * u)], fill=(215, 222, 232, 255), outline=(110, 120, 135, 255))
    if lines:
        for i, y in enumerate(range(24, 56, 6)):
            x1 = 46 if i % 3 != 2 else 36
            d.line([(18 * u, y * u), (x1 * u, y * u)], fill=(120, 150, 190, 255), width=int(1.6 * u))
    return img, u


def draw_notepad(n):
    img, u = draw_page(n)
    d = ImageDraw.Draw(img)
    # spiral binding
    for x in range(16, 52, 7):
        d.ellipse((x * u, 1 * u, (x + 4) * u, 8 * u), outline=(80, 80, 90, 255), width=int(1.6 * u))
    # pencil
    pen = [(30 * u, 52 * u), (56 * u, 26 * u), (61 * u, 31 * u), (35 * u, 57 * u)]
    _grad_poly(img, pen, (255, 210, 60), (220, 150, 20), (120, 80, 10), int(u))
    d.polygon([(30 * u, 52 * u), (35 * u, 57 * u), (27 * u, 60 * u)], fill=(240, 210, 170, 255), outline=(120, 80, 10, 255))
    return _finish(img, n)


def draw_text_file(n):
    img, u = draw_page(n)
    return _finish(img, n)


def draw_generic_file(n):
    img, u = draw_page(n, lines=False)
    d = ImageDraw.Draw(img)
    d.rectangle((20 * u, 28 * u, 46 * u, 50 * u), outline=(130, 150, 180, 255), width=int(2 * u))
    return _finish(img, n)


def draw_image_file(n):
    img, u = draw_page(n, lines=False)
    box = (17 * u, 24 * u, 49 * u, 52 * u)
    _grad_rrect(img, box, 2 * u, (90, 170, 240), (180, 225, 255))
    d = ImageDraw.Draw(img)
    d.polygon([(17 * u, 52 * u), (29 * u, 36 * u), (37 * u, 46 * u), (42 * u, 40 * u), (49 * u, 52 * u)], fill=(60, 150, 60, 255))
    d.ellipse((38 * u, 27 * u, 45 * u, 34 * u), fill=(255, 230, 90, 255))
    return _finish(img, n)


def draw_computer(n):
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (4 * u, 6 * u, 60 * u, 44 * u), 4 * u, (70, 75, 85), (20, 22, 28), (10, 10, 12), int(1.4 * u))
    # screen
    scr = (8 * u, 10 * u, 56 * u, 40 * u)
    from . import wallpaper  # noqa  (keeps icons in the Neptune palette)
    _grad_rrect(img, scr, 1 * u, (30, 170, 170), (10, 40, 110))
    d = ImageDraw.Draw(img)
    d.arc((0, 14 * u, 70 * u, 60 * u), 200, 300, fill=(160, 255, 230, 200), width=int(2 * u))
    _gloss(img, scr, 1 * u, 90)
    # stand
    _grad_poly(img, [(26 * u, 44 * u), (38 * u, 44 * u), (41 * u, 53 * u), (23 * u, 53 * u)], (120, 125, 135), (60, 62, 70))
    _grad_rrect(img, (14 * u, 53 * u, 50 * u, 58 * u), 2 * u, (150, 155, 165), (70, 72, 80), (40, 40, 45), int(u))
    return _finish(img, n)


def draw_calc(n):
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (10 * u, 3 * u, 54 * u, 61 * u), 5 * u, (90, 95, 105), (35, 38, 45), (15, 15, 18), int(1.4 * u))
    _grad_rrect(img, (15 * u, 8 * u, 49 * u, 20 * u), 2 * u, (200, 230, 210), (150, 190, 165), (40, 60, 50), int(u))
    for r in range(4):
        for c in range(4):
            x, y = 15 + c * 9, 24 + r * 9
            col = ((240, 140, 50), (200, 90, 20)) if c == 3 else ((235, 238, 242), (180, 185, 195))
            _grad_rrect(img, (x * u, y * u, (x + 7) * u, (y + 7) * u), 1.5 * u, col[0], col[1], (40, 40, 45), int(0.8 * u))
    return _finish(img, n)


def draw_terminal(n):
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (3 * u, 8 * u, 61 * u, 56 * u), 4 * u, (120, 180, 200), (40, 90, 120), (20, 40, 60), int(1.4 * u))
    gfx.round_rect(img, (7 * u, 16 * u, 57 * u, 52 * u), 2 * u, fill=(12, 14, 20, 255))
    d = ImageDraw.Draw(img)
    d.line([(13 * u, 24 * u), (22 * u, 31 * u), (13 * u, 38 * u)], fill=(120, 255, 200, 255), width=int(3 * u), joint='curve')
    d.line([(26 * u, 40 * u), (40 * u, 40 * u)], fill=(220, 230, 240, 255), width=int(3 * u))
    for i, x in enumerate((9, 14, 19)):
        d.ellipse((x * u, 10 * u, (x + 3.5) * u, 13.5 * u), fill=[(240, 90, 80), (240, 200, 60), (90, 200, 90)][i] + (255,))
    return _finish(img, n)


def draw_personalize(n):
    u = n * S / 64.0
    img = _canvas(n)
    # monitor with palette
    _grad_rrect(img, (4 * u, 6 * u, 56 * u, 42 * u), 3 * u, (70, 75, 85), (25, 28, 34), (10, 10, 12), int(1.2 * u))
    _grad_rrect(img, (8 * u, 10 * u, 52 * u, 38 * u), 1 * u, (220, 120, 200), (60, 120, 220))
    _grad_poly(img, [(24 * u, 42 * u), (36 * u, 42 * u), (38 * u, 50 * u), (22 * u, 50 * u)], (120, 125, 135), (60, 62, 70))
    # palette
    d = ImageDraw.Draw(img)
    pal = (30 * u, 30 * u, 62 * u, 60 * u)
    d.ellipse(pal, fill=(235, 215, 180, 255), outline=(120, 90, 50, 255), width=int(1.4 * u))
    for (cx, cy, col) in ((38, 38, (220, 40, 40)), (47, 35, (250, 200, 30)), (55, 41, (40, 160, 60)), (53, 51, (40, 100, 220)), (42, 52, (160, 60, 200))):
        d.ellipse(((cx - 3.5) * u, (cy - 3.5) * u, (cx + 3.5) * u, (cy + 3.5) * u), fill=col + (255,))
    return _finish(img, n)


def draw_round_symbol(n, top, bottom, symbol):
    u = n * S / 64.0
    img = _canvas(n)
    d = ImageDraw.Draw(img)
    box = (5 * u, 5 * u, 59 * u, 59 * u)
    m = Image.new('L', img.size, 0)
    ImageDraw.Draw(m).ellipse(box, fill=255)
    g = gfx.vgradient(img.size[0], img.size[1], ((0.08, top), (0.92, bottom)))
    layer = g.copy()
    layer.putalpha(m)
    img.alpha_composite(layer)
    d.ellipse(box, outline=gfx.mix(bottom, (0, 0, 0), 0.4), width=int(1.5 * u))
    # gloss
    gm = Image.new('L', img.size, 0)
    ImageDraw.Draw(gm).ellipse((12 * u, 7 * u, 52 * u, 34 * u), fill=120)
    gm = gm.filter(ImageFilter.GaussianBlur(2 * u))
    gl = Image.new('RGBA', img.size, (255, 255, 255, 255))
    gl.putalpha(gm)
    img.alpha_composite(gl)
    f = gfx.font(int(36 * u), True, 'serif' if symbol in ('i', '?') else 'sans')
    gfx.shadow_text(img, (32 * u, 33 * u), symbol, f, (255, 255, 255, 255), (0, 0, 0, 140), radius=int(1.5 * u), anchor='mm')
    return _finish(img, n)


def draw_warning(n):
    u = n * S / 64.0
    img = _canvas(n)
    tri = [(32 * u, 4 * u), (61 * u, 57 * u), (3 * u, 57 * u)]
    _grad_poly(img, tri, (255, 225, 80), (230, 160, 10), (150, 100, 0), int(2 * u))
    f = gfx.font(int(36 * u), True)
    gfx.draw_text(img, (32 * u, 38 * u), '!', f, (30, 30, 30, 255), anchor='mm')
    return _finish(img, n)


def draw_recycle(n):
    u = n * S / 64.0
    img = _canvas(n)
    body = [(14 * u, 16 * u), (50 * u, 16 * u), (46 * u, 60 * u), (18 * u, 60 * u)]
    _grad_poly(img, body, (200, 230, 245, 230), (120, 170, 200, 230), (60, 100, 130), int(1.4 * u))
    gfx.round_rect(img, (10 * u, 9 * u, 54 * u, 17 * u), 3 * u, fill=(150, 190, 215, 255), outline=(60, 100, 130, 255), width=int(1.4 * u))
    d = ImageDraw.Draw(img)
    for x in (24, 32, 40):
        d.line([(x * u, 22 * u), ((x - (32 - x) * 0.15) * u, 54 * u)], fill=(255, 255, 255, 170), width=int(2 * u))
    return _finish(img, n)


def draw_gear(n):
    u = n * S / 64.0
    img = _canvas(n)
    cx = cy = 32 * u
    pts = []
    for i in range(16):
        a = i * math.pi / 8
        r = (28 if i % 2 == 0 else 22) * u
        a0, a1 = a - 0.17, a + 0.17
        pts += [(cx + r * math.cos(a0), cy + r * math.sin(a0)), (cx + r * math.cos(a1), cy + r * math.sin(a1))]
    _grad_poly(img, pts, (200, 205, 215), (110, 115, 125), (50, 52, 58), int(1.4 * u))
    d = ImageDraw.Draw(img)
    d.ellipse((cx - 9 * u, cy - 9 * u, cx + 9 * u, cy + 9 * u), fill=(60, 64, 72, 255), outline=(30, 30, 35, 255), width=int(u))
    return _finish(img, n)


def draw_music(n):
    u = n * S / 64.0
    img = draw_round_symbol(n * 1, (255, 150, 60), (200, 60, 20), '').resize((n * S, n * S), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    d.ellipse((18 * u, 38 * u, 30 * u, 48 * u), fill=(255, 255, 255, 255))
    d.ellipse((36 * u, 34 * u, 48 * u, 44 * u), fill=(255, 255, 255, 255))
    d.line([(29 * u, 43 * u), (29 * u, 16 * u), (47 * u, 12 * u), (47 * u, 39 * u)], fill=(255, 255, 255, 255), width=int(3 * u))
    return _finish(img, n)


def draw_neptune(n):
    """The Neptune start orb: a glassy planet with a ring and a trident glint."""
    u = n * S / 64.0
    img = _canvas(n)
    box = (3 * u, 3 * u, 61 * u, 61 * u)
    m = Image.new('L', img.size, 0)
    ImageDraw.Draw(m).ellipse(box, fill=255)
    body = gfx.radial(img.size[0], img.size[1],
                      ((0, (140, 250, 240)), (0.45, (30, 150, 170)), (0.85, (10, 50, 110)), (1, (5, 20, 60))),
                      0.38, 0.32).copy()
    body.putalpha(m)
    img.alpha_composite(body)
    d = ImageDraw.Draw(img)
    # bands
    for i, (y, a) in enumerate(((24, 60), (34, 45), (44, 40))):
        bm = Image.new('L', img.size, 0)
        ImageDraw.Draw(bm).ellipse((0, (y - 4) * u, 64 * u, (y + 4) * u), fill=a)
        bm = ImageChopsMul(bm, m)
        band = Image.new('RGBA', img.size, (200, 255, 250, 255))
        band.putalpha(bm.filter(ImageFilter.GaussianBlur(1.5 * u)))
        img.alpha_composite(band)
    # ring
    ring = Image.new('L', img.size, 0)
    ImageDraw.Draw(ring).ellipse((-4 * u, 26 * u, 68 * u, 42 * u), outline=255, width=int(2.4 * u))
    # hide the back half of the ring behind the planet
    cut = Image.new('L', img.size, 0)
    ImageDraw.Draw(cut).rectangle((0, 0, 64 * u, 34 * u), fill=255)
    from PIL import ImageChops
    back = ImageChops.multiply(ring, ImageChops.multiply(cut, m))
    ring = ImageChops.subtract(ring, back)
    rl = Image.new('RGBA', img.size, (230, 255, 255, 255))
    rl.putalpha(ring)
    img.alpha_composite(rl)
    # gloss
    gm = Image.new('L', img.size, 0)
    ImageDraw.Draw(gm).ellipse((12 * u, 6 * u, 50 * u, 30 * u), fill=150)
    gm = ImageChops.multiply(gm.filter(ImageFilter.GaussianBlur(2.5 * u)), m)
    gl = Image.new('RGBA', img.size, (255, 255, 255, 255))
    gl.putalpha(gm)
    img.alpha_composite(gl)
    d.ellipse(box, outline=(5, 25, 50, 255), width=int(1.4 * u))
    return _finish(img, n)


def draw_power(n):
    u = n * S / 64.0
    img = draw_round_symbol(n, (250, 120, 100), (190, 40, 30), '').resize((n * S, n * S), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    d.arc((19 * u, 20 * u, 45 * u, 46 * u), -50, 230, fill=(255, 255, 255, 255), width=int(4 * u))
    d.line([(32 * u, 15 * u), (32 * u, 32 * u)], fill=(255, 255, 255, 255), width=int(4 * u))
    return _finish(img, n)


def ImageChopsMul(a, b):
    from PIL import ImageChops
    return ImageChops.multiply(a, b)


_DRAW = {
    'folder': draw_folder,
    'documents': lambda n: _badge(draw_folder(n), draw_text_file(n // 2 + 2)),
    'pictures': lambda n: _badge(draw_folder(n), draw_image_file(n // 2 + 2)),
    'computer': draw_computer,
    'notepad': draw_notepad,
    'text': draw_text_file,
    'file': draw_generic_file,
    'image': draw_image_file,
    'calc': draw_calc,
    'terminal': draw_terminal,
    'personalize': draw_personalize,
    'help': lambda n: draw_round_symbol(n, (90, 170, 250), (20, 80, 190), '?'),
    'info': lambda n: draw_round_symbol(n, (90, 170, 250), (20, 80, 190), 'i'),
    'error': lambda n: draw_round_symbol(n, (250, 110, 100), (180, 20, 20), '×'),
    'question': lambda n: draw_round_symbol(n, (90, 170, 250), (20, 80, 190), '?'),
    'warning': draw_warning,
    'recycle': draw_recycle,
    'settings': draw_gear,
    'music': draw_music,
    'neptune': draw_neptune,
    'clock': lambda n: draw_round_symbol(n, (250, 250, 255), (170, 180, 200), ''),
    'power': lambda n: draw_power(n),
    'paint': lambda n: draw_paint(n),
}


def draw_paint(n):
    u = n * S / 64.0
    img = _canvas(n)
    d = ImageDraw.Draw(img)
    pal = (2 * u, 14 * u, 50 * u, 58 * u)
    d.ellipse(pal, fill=(240, 220, 185, 255), outline=(120, 90, 50, 255), width=int(1.5 * u))
    d.ellipse((30 * u, 40 * u, 40 * u, 50 * u), fill=(0, 0, 0, 0))
    for (cx, cy, col) in ((14, 26, (220, 40, 40)), (25, 21, (250, 200, 30)), (36, 24, (40, 160, 60)),
                          (12, 40, (40, 100, 220)), (22, 48, (160, 60, 200))):
        d.ellipse(((cx - 4.5) * u, (cy - 4.5) * u, (cx + 4.5) * u, (cy + 4.5) * u), fill=col + (255,),
                  outline=(0, 0, 0, 80))
    # brush
    handle = [(36 * u, 46 * u), (58 * u, 4 * u), (62 * u, 6 * u), (41 * u, 49 * u)]
    _grad_poly(img, handle, (200, 120, 60), (130, 70, 30), (80, 40, 10), int(u))
    d.polygon([(33 * u, 44 * u), (42 * u, 49 * u), (38 * u, 56 * u), (30 * u, 58 * u), (31 * u, 50 * u)],
              fill=(40, 100, 220, 255), outline=(20, 40, 90, 255))
    d.line([(34 * u, 44 * u), (42 * u, 49 * u)], fill=(200, 200, 210, 255), width=int(3 * u))
    return _finish(img, n)


def _badge(base, small):
    n = base.size[0]
    out = base.copy()
    out.alpha_composite(small, (n - small.size[0], n - small.size[1]))
    return out


@functools.lru_cache(maxsize=256)
def get(name, size=32):
    fn = _DRAW.get(name) or _DRAW['file']
    try:
        return fn(size)
    except Exception:
        return draw_generic_file(size)


def for_path(path, isdir):
    if isdir:
        return 'folder'
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
    if ext in ('txt', 'md', 'py', 'sh', 'cfg', 'json', 'log', 'ini', 'c', 'h', 'html', 'css', 'js'):
        return 'text'
    if ext in ('png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'):
        return 'image'
    if ext in ('mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac', 'opus'):
        return 'music'
    if ext in ('mp4', 'mkv', 'webm', 'avi', 'mov', 'm4v', 'mpg', 'mpeg', 'wmv', 'flv'):
        return 'video'
    if ext == 'nepapp':
        return 'plugin'
    if ext in ('zip', 'gz', 'tgz', 'tar', 'bz2', 'tbz2', 'tbz', 'xz', 'txz'):
        return 'archive'
    if ext == 'cosapp':
        return 'cosapp'
    if ext == 'ctheme':
        return 'theme'
    if ext == 'ceap':
        return 'ceap'
    if ext == 'cip':
        return 'package'
    if ext == 'cdi':
        return 'driver'
    return 'file'


# -- cursors -------------------------------------------------------------------
@functools.lru_cache(maxsize=16)
def cursor(kind='arrow'):
    """Returns (image, hotspot)."""
    k = 3
    n = 32
    img = Image.new('RGBA', (n * k, n * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def poly(pts, fill=(255, 255, 255, 255), outline=(0, 0, 0, 255), w=1.6):
        P = [(x * k, y * k) for x, y in pts]
        d.polygon(P, fill=fill)
        d.line(P + [P[0]], fill=outline, width=int(w * k), joint='curve')

    hot = (1, 1)
    if kind == 'arrow':
        poly([(2, 2), (2, 21), (6.5, 17), (10, 25), (13, 23.7), (9.6, 16), (15.5, 16)])
        hot = (2, 2)
    elif kind == 'ibeam':
        for (x0, y0, x1, y1) in ((10, 3, 14, 3), (10, 23, 14, 23), (12, 3, 12, 23)):
            d.line([(x0 * k, y0 * k), (x1 * k, y1 * k)], fill=(255, 255, 255, 255), width=int(3.5 * k))
        for (x0, y0, x1, y1) in ((10, 3, 14, 3), (10, 23, 14, 23), (12, 3, 12, 23)):
            d.line([(x0 * k, y0 * k), (x1 * k, y1 * k)], fill=(0, 0, 0, 255), width=int(1.3 * k))
        hot = (12, 13)
    elif kind in ('size_we', 'size_ns', 'size_nwse', 'size_nesw', 'move'):
        a = [(0, -11), (5, -5), (2, -5), (2, 5), (5, 5), (0, 11), (-5, 5), (-2, 5), (-2, -5), (-5, -5)]
        ang = {'size_ns': 0, 'size_we': 90, 'size_nwse': -45, 'size_nesw': 45, 'move': 0}[kind]
        c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        pts = [(13 + x * c - y * s, 13 + x * s + y * c) for x, y in a]
        poly(pts)
        if kind == 'move':
            pts = [(13 + y, 13 + x) for x, y in a]
            poly(pts)
        hot = (13, 13)
    elif kind == 'hand':
        poly([(9, 2), (12, 2), (12, 11), (19, 12), (21, 14), (20, 22), (17, 26), (9, 26), (4, 18), (4, 14), (7, 15), (9, 18)])
        hot = (10, 2)
    elif kind == 'busy':
        return busy_cursor(0), (2, 2)
    # soft shadow
    sh = img.getchannel('A').filter(ImageFilter.GaussianBlur(2 * k))
    sh = sh.point(lambda v: v * 90 // 255)
    shadow = Image.new('RGBA', img.size, (0, 0, 0, 255))
    shadow.putalpha(sh)
    out = Image.new('RGBA', img.size, (0, 0, 0, 0))
    out.alpha_composite(shadow, (k * 1, k * 2))
    out.alpha_composite(img)
    return out.resize((n, n), Image.LANCZOS), hot


def busy_cursor(phase):
    """Vista's spinning blue ring, frame `phase` 0..1."""
    k = 3
    n = 32
    img = Image.new('RGBA', (n * k, n * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx, cy, r = 16 * k, 16 * k, 11 * k
    d.ellipse((cx - r - 3 * k, cy - r - 3 * k, cx + r + 3 * k, cy + r + 3 * k), fill=(30, 60, 110, 90))
    for i in range(24):
        a0 = phase * 360 + i * 15
        alpha = int(255 * (i / 24.0) ** 1.5)
        d.arc((cx - r, cy - r, cx + r, cy + r), a0, a0 + 16, fill=(80, 200, 255, alpha), width=int(4 * k))
    return img.resize((n, n), Image.LANCZOS)


# -- icons for the v2.4 ports: Task Manager, Druman, App Manager, Lock ----------
def draw_taskmgr(n):
    """A monitor showing a green CPU graph."""
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (4 * u, 6 * u, 60 * u, 46 * u), 4 * u, (80, 88, 100), (25, 28, 34), (10, 10, 12), int(1.4 * u))
    scr = (8 * u, 10 * u, 56 * u, 42 * u)
    gfx.round_rect(img, scr, 1 * u, fill=(0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    for gx in range(8, 57, 8):
        d.line([(gx * u, 10 * u), (gx * u, 42 * u)], fill=(0, 110, 55, 255), width=max(1, int(0.6 * u)))
    for gy in range(14, 42, 7):
        d.line([(8 * u, gy * u), (56 * u, gy * u)], fill=(0, 110, 55, 255), width=max(1, int(0.6 * u)))
    pts = [(8, 36), (14, 30), (19, 33), (25, 20), (31, 26), (37, 16), (43, 24), (49, 18), (56, 22)]
    d.line([(x * u, y * u) for x, y in pts], fill=(60, 255, 90, 255), width=int(2.4 * u), joint='curve')
    _gloss(img, scr, 1 * u, 60)
    _grad_poly(img, [(26 * u, 46 * u), (38 * u, 46 * u), (41 * u, 54 * u), (23 * u, 54 * u)], (120, 125, 135), (60, 62, 70))
    _grad_rrect(img, (14 * u, 54 * u, 50 * u, 59 * u), 2 * u, (150, 155, 165), (70, 72, 80), (40, 40, 45), int(u))
    return _finish(img, n)


def draw_druman(n):
    """A glossy drum with a play triangle on its head (drum + man)."""
    u = n * S / 64.0
    img = _canvas(n)
    d = ImageDraw.Draw(img)
    # shell
    _grad_rrect(img, (8 * u, 20 * u, 56 * u, 54 * u), 6 * u, (60, 160, 220), (10, 50, 120), (5, 25, 60), int(1.4 * u))
    d.ellipse((8 * u, 47 * u, 56 * u, 61 * u), fill=(10, 45, 110, 255), outline=(5, 25, 60, 255), width=int(1.4 * u))
    # zig-zag tension cords
    zz = []
    for i in range(7):
        zz.append(((10 + i * 7.3) * u, (26 if i % 2 == 0 else 50) * u))
    d.line(zz, fill=(230, 240, 250, 220), width=int(1.6 * u))
    # head
    d.ellipse((8 * u, 12 * u, 56 * u, 28 * u), fill=(245, 248, 252, 255), outline=(5, 25, 60, 255), width=int(1.4 * u))
    d.ellipse((12 * u, 14 * u, 52 * u, 26 * u), outline=(200, 210, 225, 255), width=int(u))
    d.polygon([(28 * u, 16 * u), (28 * u, 24 * u), (37 * u, 20 * u)], fill=(30, 120, 210, 255))
    # sticks
    d.line([(44 * u, 4 * u), (34 * u, 16 * u)], fill=(200, 150, 90, 255), width=int(3 * u))
    d.line([(20 * u, 4 * u), (30 * u, 16 * u)], fill=(200, 150, 90, 255), width=int(3 * u))
    return _finish(img, n)


def draw_plugin(n):
    """A blue jigsaw piece."""
    u = n * S / 64.0
    img = _canvas(n)
    m = Image.new('L', img.size, 0)
    md = ImageDraw.Draw(m)
    md.rounded_rectangle((8 * u, 16 * u, 50 * u, 58 * u), radius=int(4 * u), fill=255)
    md.ellipse((22 * u, 4 * u, 36 * u, 20 * u), fill=255)          # top knob
    md.ellipse((46 * u, 30 * u, 60 * u, 44 * u), fill=255)         # right knob
    md.ellipse((2 * u, 30 * u, 14 * u, 42 * u), fill=0)            # left socket
    g = gfx.vgradient(img.size[0], img.size[1], ((0, (120, 200, 255)), (1, (20, 80, 180))))
    g.putalpha(m)
    img.alpha_composite(g)
    edge = m.filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > 40 else 0)
    ol = Image.new('RGBA', img.size, (10, 40, 100, 255))
    ol.putalpha(edge.filter(ImageFilter.MaxFilter(max(3, int(u) | 1))))
    img.alpha_composite(ol)
    hl = Image.new('L', img.size, 0)
    ImageDraw.Draw(hl).rounded_rectangle((11 * u, 19 * u, 47 * u, 34 * u), radius=int(3 * u), fill=80)
    gl = Image.new('RGBA', img.size, (255, 255, 255, 0))
    gl.putalpha(hl)
    img.alpha_composite(gl)
    return _finish(img, n)


def draw_lock(n):
    """A gold padlock."""
    u = n * S / 64.0
    img = _canvas(n)
    d = ImageDraw.Draw(img)
    d.arc((18 * u, 6 * u, 46 * u, 40 * u), 180, 360, fill=(150, 155, 165, 255), width=int(5 * u))
    d.line([(18 * u + 2.5 * u, 23 * u), (18 * u + 2.5 * u, 30 * u)], fill=(150, 155, 165, 255), width=int(5 * u))
    d.line([(46 * u - 2.5 * u, 23 * u), (46 * u - 2.5 * u, 30 * u)], fill=(150, 155, 165, 255), width=int(5 * u))
    body = (10 * u, 28 * u, 54 * u, 60 * u)
    _grad_rrect(img, body, 5 * u, (255, 215, 90), (200, 130, 20), (120, 75, 10), int(1.4 * u))
    _gloss(img, body, 5 * u, 100)
    d.ellipse((28 * u, 38 * u, 36 * u, 46 * u), fill=(80, 50, 10, 255))
    d.rectangle((30.5 * u, 44 * u, 33.5 * u, 52 * u), fill=(80, 50, 10, 255))
    return _finish(img, n)


def draw_video(n):
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (4 * u, 12 * u, 60 * u, 52 * u), 4 * u, (70, 75, 85), (20, 22, 28), (10, 10, 12), int(1.4 * u))
    d = ImageDraw.Draw(img)
    for x in range(8, 58, 8):
        d.rectangle((x * u, 15 * u, (x + 4) * u, 18 * u), fill=(230, 230, 235, 255))
        d.rectangle((x * u, 46 * u, (x + 4) * u, 49 * u), fill=(230, 230, 235, 255))
    _grad_rrect(img, (8 * u, 21 * u, 56 * u, 43 * u), 1 * u, (40, 170, 200), (10, 60, 130))
    d.polygon([(27 * u, 25 * u), (27 * u, 39 * u), (39 * u, 32 * u)], fill=(255, 255, 255, 255))
    return _finish(img, n)


_DRAW.update({'taskmgr': draw_taskmgr, 'druman': draw_druman, 'plugin': draw_plugin, 'lock': draw_lock,
              'video': draw_video})


# -- icons for compatibility mode, packages and devices ---------------------------
def _crt(n, screen_top, screen_bottom, glyph=None, glyph_col=(120, 255, 140)):
    """A chunky beige CRT monitor."""
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (3 * u, 4 * u, 61 * u, 50 * u), 6 * u, (236, 228, 205), (190, 180, 150), (110, 100, 80), int(1.4 * u))
    scr = (9 * u, 9 * u, 55 * u, 41 * u)
    _grad_rrect(img, scr, 4 * u, screen_top, screen_bottom, (40, 40, 40), int(u))
    if glyph:
        d = ImageDraw.Draw(img)
        f = gfx.font(int(13 * u), True, 'mono')
        d.text((32 * u, 25 * u), glyph, font=f, fill=glyph_col + (255,), anchor='mm')
    _gloss(img, scr, 4 * u, 70)
    _grad_poly(img, [(22 * u, 50 * u), (42 * u, 50 * u), (46 * u, 57 * u), (18 * u, 57 * u)], (210, 200, 175), (150, 140, 115))
    _grad_rrect(img, (12 * u, 56 * u, 52 * u, 61 * u), 2 * u, (225, 215, 190), (160, 150, 125), (100, 90, 70), int(u))
    return img


def draw_cosapp(n):
    """An old COS program: a green-screen CRT with a C> prompt."""
    return _finish(_crt(n, (10, 40, 20), (0, 15, 5), 'C>_'), n)


def draw_cosmode(n):
    """COS v2.4 Mode: a CRT showing the classic blue/cyan COS desktop."""
    u = n * S / 64.0
    img = _crt(n, (0, 170, 170), (0, 120, 140))
    d = ImageDraw.Draw(img)
    d.rectangle((10 * u, 10 * u, 54 * u, 15 * u), fill=(0, 0, 0, 255))
    d.rectangle((17 * u, 19 * u, 47 * u, 36 * u), fill=(170, 170, 170, 255), outline=(255, 255, 255, 255), width=int(u))
    d.rectangle((17 * u, 19 * u, 47 * u, 23 * u), fill=(0, 0, 170, 255))
    d.line([(20 * u, 28 * u), (40 * u, 28 * u)], fill=(0, 0, 0, 255), width=int(1.5 * u))
    d.line([(20 * u, 32 * u), (34 * u, 32 * u)], fill=(0, 0, 0, 255), width=int(1.5 * u))
    return _finish(img, n)


def draw_package(n):
    """A cardboard box with a blue COS ribbon."""
    u = n * S / 64.0
    img = _canvas(n)
    _grad_poly(img, [(6 * u, 22 * u), (32 * u, 30 * u), (32 * u, 60 * u), (6 * u, 50 * u)], (215, 170, 100), (170, 120, 60),
               (110, 70, 25), int(1.2 * u))
    _grad_poly(img, [(32 * u, 30 * u), (58 * u, 22 * u), (58 * u, 50 * u), (32 * u, 60 * u)], (190, 145, 80), (140, 95, 40),
               (110, 70, 25), int(1.2 * u))
    _grad_poly(img, [(6 * u, 22 * u), (32 * u, 13 * u), (58 * u, 22 * u), (32 * u, 30 * u)], (240, 205, 140), (215, 170, 100),
               (110, 70, 25), int(1.2 * u))
    d = ImageDraw.Draw(img)
    d.polygon([(17 * u, 18 * u), (23 * u, 16 * u), (49 * u, 25 * u), (43 * u, 27 * u)], fill=(40, 110, 210, 255))
    d.polygon([(43 * u, 27 * u), (49 * u, 25 * u), (49 * u, 54 * u), (43 * u, 56 * u)], fill=(30, 85, 170, 255))
    return _finish(img, n)


def draw_driver(n):
    """A green circuit card with a gold edge connector."""
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (4 * u, 12 * u, 60 * u, 48 * u), 3 * u, (40, 150, 80), (20, 100, 50), (10, 60, 30), int(1.4 * u))
    d = ImageDraw.Draw(img)
    for x in range(10, 56, 5):
        d.rectangle((x * u, 48 * u, (x + 3) * u, 55 * u), fill=(230, 190, 60, 255), outline=(150, 110, 20, 255))
    _grad_rrect(img, (20 * u, 18 * u, 42 * u, 38 * u), 2 * u, (60, 60, 66), (20, 20, 24), (0, 0, 0), int(u))
    for y in (22, 27, 32):
        d.line([(8 * u, y * u), (20 * u, y * u)], fill=(200, 230, 200, 200), width=int(u))
        d.line([(42 * u, y * u), (56 * u, y * u)], fill=(200, 230, 200, 200), width=int(u))
    d.text((31 * u, 28 * u), 'COS', font=gfx.font(int(6.5 * u), True), fill=(220, 220, 220, 255), anchor='mm')
    return _finish(img, n)


def draw_gamepad(n):
    u = n * S / 64.0
    img = _canvas(n)
    m = Image.new('L', img.size, 0)
    md = ImageDraw.Draw(m)
    md.rounded_rectangle((6 * u, 20 * u, 58 * u, 44 * u), radius=int(12 * u), fill=255)
    md.ellipse((4 * u, 28 * u, 26 * u, 56 * u), fill=255)
    md.ellipse((38 * u, 28 * u, 60 * u, 56 * u), fill=255)
    g = gfx.vgradient(img.size[0], img.size[1], ((0, (110, 115, 125)), (1, (35, 38, 45))))
    g.putalpha(m)
    img.alpha_composite(g)
    d = ImageDraw.Draw(img)
    d.rectangle((13 * u, 30 * u, 17 * u, 42 * u), fill=(230, 230, 235, 255))
    d.rectangle((9 * u, 34 * u, 21 * u, 38 * u), fill=(230, 230, 235, 255))
    for (cx, cy, col) in ((47, 30, (60, 200, 80)), (53, 36, (230, 60, 60)), (41, 36, (60, 120, 230)), (47, 42, (240, 200, 40))):
        d.ellipse(((cx - 3) * u, (cy - 3) * u, (cx + 3) * u, (cy + 3) * u), fill=col + (255,))
    return _finish(img, n)


def draw_keyboard(n):
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (2 * u, 18 * u, 62 * u, 48 * u), 4 * u, (235, 238, 242), (175, 180, 190), (90, 95, 105), int(1.4 * u))
    d = ImageDraw.Draw(img)
    for r in range(3):
        for c in range(10):
            x, y = 6 + c * 5.3, 22 + r * 7
            d.rectangle((x * u, y * u, (x + 4) * u, (y + 5) * u), fill=(255, 255, 255, 255), outline=(150, 155, 165, 255))
    d.rectangle((16 * u, 43 * u, 48 * u, 46 * u), fill=(255, 255, 255, 255), outline=(150, 155, 165, 255))
    return _finish(img, n)


def draw_mouse(n):
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (18 * u, 8 * u, 46 * u, 58 * u), 14 * u, (245, 247, 250), (170, 175, 185), (90, 95, 105), int(1.4 * u))
    d = ImageDraw.Draw(img)
    d.line([(32 * u, 8 * u), (32 * u, 28 * u)], fill=(120, 125, 135, 255), width=int(u))
    d.line([(18 * u, 28 * u), (46 * u, 28 * u)], fill=(120, 125, 135, 255), width=int(u))
    d.rounded_rectangle((30 * u, 13 * u, 34 * u, 22 * u), radius=int(2 * u), fill=(60, 120, 220, 255))
    return _finish(img, n)


def draw_network(n):
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (6 * u, 22 * u, 58 * u, 46 * u), 3 * u, (90, 160, 90), (40, 110, 50), (20, 60, 25), int(1.4 * u))
    _grad_rrect(img, (24 * u, 10 * u, 40 * u, 24 * u), 2 * u, (230, 230, 235), (170, 175, 185), (90, 95, 105), int(u))
    d = ImageDraw.Draw(img)
    for x in range(10, 56, 6):
        d.rectangle((x * u, 46 * u, (x + 3) * u, 52 * u), fill=(230, 190, 60, 255))
    for i, c in enumerate(((60, 255, 90), (255, 200, 40))):
        d.ellipse(((44 + i * 6) * u, 30 * u, (48 + i * 6) * u, 34 * u), fill=c + (255,))
    return _finish(img, n)


def draw_drive(n):
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (4 * u, 22 * u, 60 * u, 46 * u), 4 * u, (200, 205, 215), (120, 125, 135), (60, 62, 70), int(1.4 * u))
    _gloss(img, (4 * u, 22 * u, 60 * u, 46 * u), 4 * u, 90)
    d = ImageDraw.Draw(img)
    d.ellipse((48 * u, 36 * u, 53 * u, 41 * u), fill=(60, 230, 90, 255))
    d.line([(10 * u, 38 * u), (38 * u, 38 * u)], fill=(80, 85, 95, 255), width=int(1.5 * u))
    return _finish(img, n)


def draw_usb(n):
    u = n * S / 64.0
    img = _canvas(n)
    d = ImageDraw.Draw(img)
    col = (40, 90, 170, 255)
    d.line([(32 * u, 6 * u), (32 * u, 52 * u)], fill=col, width=int(4 * u))
    d.polygon([(26 * u, 12 * u), (38 * u, 12 * u), (32 * u, 3 * u)], fill=col)
    d.line([(32 * u, 36 * u), (18 * u, 26 * u), (18 * u, 20 * u)], fill=col, width=int(3.5 * u), joint='curve')
    d.ellipse((13 * u, 14 * u, 23 * u, 24 * u), fill=col)
    d.line([(32 * u, 42 * u), (46 * u, 32 * u), (46 * u, 24 * u)], fill=col, width=int(3.5 * u), joint='curve')
    d.rectangle((41 * u, 17 * u, 51 * u, 26 * u), fill=col)
    d.ellipse((25 * u, 50 * u, 39 * u, 62 * u), fill=col)
    return _finish(img, n)


def draw_devmgr(n):
    return _badge(draw_computer(n), draw_gear(n // 2 + 2))


_DRAW.update({'cosapp': draw_cosapp, 'cosmode': draw_cosmode, 'package': draw_package, 'driver': draw_driver,
              'gamepad': draw_gamepad, 'keyboard': draw_keyboard, 'mouse': draw_mouse, 'network': draw_network,
              'drive': draw_drive, 'usb': draw_usb, 'devmgr': draw_devmgr})


# -- Webinator and Pigeon ---------------------------------------------------------
def draw_webinator(n):
    """A glassy blue globe with a swoosh: the COS web browser."""
    u = n * S / 64.0
    img = _canvas(n)
    d = ImageDraw.Draw(img)
    m = Image.new('L', img.size, 0)
    ImageDraw.Draw(m).ellipse((6 * u, 6 * u, 58 * u, 58 * u), fill=255)
    g = gfx.radial(img.size[0], img.size[1], ((0, (140, 210, 255)), (0.6, (30, 120, 210)), (1, (10, 50, 130))), cx=0.38, cy=0.32)
    g.putalpha(m)
    img.alpha_composite(g)
    for box in ((20 * u, 6 * u, 44 * u, 58 * u), (6 * u, 22 * u, 58 * u, 42 * u)):
        d.ellipse(box, outline=(200, 235, 255, 150), width=int(1.6 * u))
    d.line([(32 * u, 6 * u), (32 * u, 58 * u)], fill=(200, 235, 255, 150), width=int(1.6 * u))
    d.line([(6 * u, 32 * u), (58 * u, 32 * u)], fill=(200, 235, 255, 150), width=int(1.6 * u))
    d.arc((0, 14 * u, 64 * u, 54 * u), 160, 350, fill=(255, 200, 60, 255), width=int(4.5 * u))
    d.text((32 * u, 33 * u), 'W', font=gfx.font(int(22 * u), True), fill=(255, 255, 255, 255), anchor='mm')
    gl = Image.new('L', img.size, 0)
    ImageDraw.Draw(gl).ellipse((14 * u, 9 * u, 46 * u, 28 * u), fill=90)
    lay = Image.new('RGBA', img.size, (255, 255, 255, 0))
    lay.putalpha(gl)
    img.alpha_composite(lay)
    return _finish(img, n)


def draw_pigeon(n):
    """A grey-blue pigeon carrying a little letter."""
    u = n * S / 64.0
    img = _canvas(n)
    body = [(8 * u, 38 * u), (20 * u, 26 * u), (36 * u, 24 * u), (46 * u, 16 * u), (54 * u, 18 * u), (56 * u, 24 * u),
            (50 * u, 30 * u), (48 * u, 40 * u), (36 * u, 50 * u), (18 * u, 50 * u)]
    _grad_poly(img, body, (175, 185, 205), (95, 105, 130), (50, 55, 70), int(1.4 * u))
    wing = [(18 * u, 34 * u), (34 * u, 28 * u), (44 * u, 36 * u), (30 * u, 46 * u)]
    _grad_poly(img, wing, (130, 140, 165), (80, 88, 110), (50, 55, 70), int(u))
    d = ImageDraw.Draw(img)
    d.polygon([(55 * u, 20 * u), (62 * u, 22 * u), (56 * u, 25 * u)], fill=(240, 180, 60, 255))
    d.ellipse((49 * u, 18 * u, 53 * u, 22 * u), fill=(20, 20, 20, 255))
    d.line([(46 * u, 26 * u), (50 * u, 30 * u)], fill=(90, 190, 140, 255), width=int(2.5 * u))
    _grad_rrect(img, (22 * u, 48 * u, 40 * u, 60 * u), 1.5 * u, (255, 255, 255), (220, 225, 235), (110, 115, 130), int(u))
    d.line([(22 * u, 48 * u), (31 * u, 55 * u), (40 * u, 48 * u)], fill=(110, 115, 130, 255), width=int(u))
    return _finish(img, n)


_DRAW.update({'webinator': draw_webinator, 'pigeon': draw_pigeon})


# -- TCF icons: IBBS (bulletin board) and TCFNet ---------------------------------
def draw_bbs(n):
    """Two glassy speech bubbles: the TCF Internal Bulletin Board System."""
    u = n * S / 64.0
    img = _canvas(n)
    d = ImageDraw.Draw(img)
    back = (20 * u, 6 * u, 61 * u, 36 * u)
    _grad_rrect(img, back, 9 * u, (120, 230, 220), (20, 130, 150), (10, 60, 80), int(1.4 * u))
    d.polygon([(48 * u, 35 * u), (56 * u, 44 * u), (42 * u, 35 * u)], fill=(20, 130, 150, 255))
    front = (3 * u, 22 * u, 46 * u, 52 * u)
    _grad_rrect(img, front, 9 * u, (255, 255, 255), (200, 220, 235), (60, 80, 100), int(1.4 * u))
    d.polygon([(11 * u, 51 * u), (8 * u, 61 * u), (20 * u, 51 * u)], fill=(200, 220, 235, 255),
              outline=(60, 80, 100, 255))
    d.line([(12 * u, 51 * u), (19 * u, 51 * u)], fill=(200, 220, 235, 255), width=int(2 * u))
    for i, y in enumerate((31, 38, 45)):
        d.line([(10 * u, y * u), ((38 if i < 2 else 28) * u, y * u)], fill=(40, 110, 170, 255), width=int(2.4 * u))
    _gloss(img, back, 9 * u, 90)
    return _finish(img, n)


def draw_tcfnet(n):
    """A glass globe with the TCF mark: the TCF Communication Network."""
    u = n * S / 64.0
    img = _canvas(n)
    box = (4 * u, 4 * u, 60 * u, 60 * u)
    m = Image.new('L', img.size, 0)
    ImageDraw.Draw(m).ellipse(box, fill=255)
    body = gfx.radial(img.size[0], img.size[1], ((0, (150, 230, 255)), (0.55, (30, 120, 200)), (1, (8, 40, 100))),
                      0.38, 0.32).copy()
    body.putalpha(m)
    img.alpha_composite(body)
    d = ImageDraw.Draw(img)
    grid = (200, 240, 255, 150)
    w = int(1.6 * u)
    d.ellipse((18 * u, 4 * u, 46 * u, 60 * u), outline=grid, width=w)
    d.line([(32 * u, 4 * u), (32 * u, 60 * u)], fill=grid, width=w)
    for y in (18, 32, 46):
        half = (28 ** 2 - (y - 32) ** 2) ** 0.5
        d.line([((32 - half) * u, y * u), ((32 + half) * u, y * u)], fill=grid, width=w)
    d.ellipse(box, outline=(5, 25, 60, 255), width=int(1.4 * u))
    gfx.shadow_text(img, (32 * u, 33 * u), 'TCF', gfx.font(int(19 * u), True), (255, 255, 255, 255),
                    (0, 20, 50, 230), radius=int(1.5 * u), anchor='mm')
    gm = Image.new('L', img.size, 0)
    ImageDraw.Draw(gm).ellipse((14 * u, 7 * u, 50 * u, 28 * u), fill=110)
    from PIL import ImageChops
    gm = ImageChops.multiply(gm.filter(ImageFilter.GaussianBlur(2.5 * u)), m)
    gl = Image.new('RGBA', img.size, (255, 255, 255, 255))
    gl.putalpha(gm)
    img.alpha_composite(gl)
    return _finish(img, n)


_DRAW['bbs'] = draw_bbs
_DRAW['tcfnet'] = draw_tcfnet


# -- Archiver ---------------------------------------------------------------------
def draw_archive(n):
    """A compressed folder: the yellow folder with a metal zipper down its middle."""
    u = n * S / 64.0
    img = draw_folder(n).resize((n * S, n * S), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    x0, x1 = 28 * u, 36 * u
    d.rectangle((x0, 12 * u, x1, 56 * u), fill=(70, 70, 78, 255))
    for i, y in enumerate(range(14, 54, 4)):
        side = x0 if i % 2 == 0 else x1 - 4 * u
        _grad_rrect(img, (side, y * u, side + 4 * u, (y + 3) * u), 0.8 * u, (235, 238, 242), (150, 155, 165))
    _grad_rrect(img, (25 * u, 40 * u, 39 * u, 52 * u), 2.5 * u, (245, 247, 250), (160, 165, 175), (70, 72, 80), int(u))
    d.rectangle((30 * u, 44 * u, 34 * u, 50 * u), fill=(90, 92, 100, 255))
    return _finish(img, n)


_DRAW.update({'archive': draw_archive})


def draw_saver(n):
    """Screen saver: a monitor full of bubbles."""
    u = n * S / 64.0
    img = _canvas(n)
    _grad_rrect(img, (4 * u, 6 * u, 60 * u, 46 * u), 4 * u, (80, 88, 100), (25, 28, 34), (10, 10, 12), int(1.4 * u))
    scr = (8 * u, 10 * u, 56 * u, 42 * u)
    _grad_rrect(img, scr, 1 * u, (10, 90, 180), (2, 20, 70))
    d = ImageDraw.Draw(img)
    for (bx, by, r) in ((20, 32, 5), (32, 22, 7), (44, 34, 4), (48, 18, 3), (14, 18, 3)):
        d.ellipse(((bx - r) * u, (by - r) * u, (bx + r) * u, (by + r) * u), outline=(200, 240, 255, 230), width=max(1, int(u)))
        d.ellipse(((bx - r * 0.5) * u, (by - r * 0.6) * u, (bx - r * 0.05) * u, (by - r * 0.2) * u), fill=(255, 255, 255, 200))
    _gloss(img, scr, 1 * u, 50)
    _grad_poly(img, [(26 * u, 46 * u), (38 * u, 46 * u), (41 * u, 54 * u), (23 * u, 54 * u)], (120, 125, 135), (60, 62, 70))
    _grad_rrect(img, (14 * u, 54 * u, 50 * u, 59 * u), 2 * u, (150, 155, 165), (70, 72, 80), (40, 40, 45), int(u))
    return _finish(img, n)


_DRAW.update({'saver': draw_saver})


def draw_ceap(n):
    """CEAP: the isometric box from the concept sheet, in COS blue."""
    u = n * S / 64.0
    img = _canvas(n)
    top = [(32 * u, 6 * u), (58 * u, 19 * u), (32 * u, 32 * u), (6 * u, 19 * u)]
    left = [(6 * u, 19 * u), (32 * u, 32 * u), (32 * u, 60 * u), (6 * u, 47 * u)]
    right = [(32 * u, 32 * u), (58 * u, 19 * u), (58 * u, 47 * u), (32 * u, 60 * u)]
    _grad_poly(img, left, (40, 120, 230), (15, 60, 160), (10, 35, 90), int(1.2 * u))
    _grad_poly(img, right, (25, 90, 200), (10, 40, 130), (10, 35, 90), int(1.2 * u))
    _grad_poly(img, top, (150, 210, 255), (80, 160, 240), (10, 35, 90), int(1.2 * u))
    d = ImageDraw.Draw(img)
    d.line([(19 * u, 12.5 * u), (45 * u, 25.5 * u)], fill=(255, 255, 255, 170), width=int(2.5 * u))
    d.text((19 * u, 40 * u), 'CE', font=gfx.font(int(9 * u), True), fill=(255, 255, 255, 235), anchor='mm')
    d.text((45 * u, 40 * u), 'AP', font=gfx.font(int(9 * u), True), fill=(220, 235, 255, 235), anchor='mm')
    return _finish(img, n)


_DRAW.update({'ceap': draw_ceap})


def draw_cosplace(n):
    """The COS Place: a glassy globe with computers linked around it."""
    u = n * S / 64.0
    img = _canvas(n)
    d = ImageDraw.Draw(img)
    g = (12 * u, 12 * u, 52 * u, 52 * u)
    m = Image.new('L', img.size, 0)
    ImageDraw.Draw(m).ellipse(g, fill=255)
    img.paste(gfx.vgradient(img.size[0], img.size[1], ((0, (120, 220, 200)), (1, (20, 110, 150)))).convert('RGBA'),
              (0, 0), m)
    d.ellipse(g, outline=(10, 60, 80, 255), width=max(1, int(1.4 * u)))
    for box in ((22 * u, 12 * u, 42 * u, 52 * u), (12 * u, 24 * u, 52 * u, 40 * u)):
        d.ellipse(box, outline=(230, 255, 250, 150), width=max(1, int(u)))
    d.line([(32 * u, 12 * u), (32 * u, 52 * u)], fill=(230, 255, 250, 150), width=max(1, int(u)))
    pts = [(8 * u, 10 * u), (56 * u, 14 * u), (54 * u, 54 * u), (9 * u, 52 * u)]
    for i, p in enumerate(pts):
        q = pts[(i + 1) % 4]
        d.line([p, q], fill=(255, 255, 255, 120), width=max(1, int(1.2 * u)))
    for (x, y) in pts:
        d.rounded_rectangle((x - 6 * u, y - 5 * u, x + 6 * u, y + 4 * u), radius=max(1, int(u)),
                            fill=(235, 242, 250, 255), outline=(40, 60, 90, 255), width=max(1, int(u)))
        d.rectangle((x - 4 * u, y - 3 * u, x + 4 * u, y + 2 * u), fill=(60, 150, 230, 255))
    _gloss(img, (int(14 * u), int(13 * u), int(50 * u), int(32 * u)), int(16 * u), 90)
    return _finish(img, n)


def draw_theme(n):
    """A .ctheme: a little desktop on a card, with a paint swatch."""
    u = n * S / 64.0
    img = _canvas(n)
    d = ImageDraw.Draw(img)
    _grad_rrect(img, (6 * u, 12 * u, 58 * u, 50 * u), int(3 * u), (90, 170, 230), (30, 80, 160), (20, 40, 80), max(1, int(u)))
    d.rectangle((7 * u, 13 * u, 57 * u, 17 * u), fill=(20, 30, 40, 200))
    d.rectangle((7 * u, 45 * u, 57 * u, 49 * u), fill=(120, 200, 210, 230))
    d.rounded_rectangle((18 * u, 21 * u, 44 * u, 40 * u), radius=max(1, int(1.5 * u)), fill=(60, 170, 170, 220),
                        outline=(0, 0, 0, 120))
    d.rectangle((20 * u, 25 * u, 42 * u, 38 * u), fill=(245, 247, 250, 255))
    for i, col in enumerate(((220, 60, 60), (250, 200, 40), (60, 170, 80), (60, 120, 220))):
        x = 34 * u + i * 6 * u
        d.ellipse((x, 48 * u, x + 7 * u, 55 * u), fill=col + (255,), outline=(0, 0, 0, 120))
    return _finish(img, n)


_DRAW.update({'cosplace': draw_cosplace, 'theme': draw_theme})


def draw_cities(n):
    """COS Cities: a little skyline at night with lit windows and a 'home page' flag."""
    u = n * S / 64.0
    img = _canvas(n)
    d = ImageDraw.Draw(img)
    _grad_rrect(img, (4 * u, 6 * u, 60 * u, 58 * u), int(6 * u), (40, 40, 120), (10, 10, 50), (5, 5, 30), max(1, int(u)))
    for (sx, sy) in ((12, 14), (44, 11), (52, 22), (24, 10)):
        d.point((sx * u, sy * u), fill=(255, 255, 255, 255))
    blds = ((9, 30, 19), (19, 20, 28), (28, 34, 37), (37, 16, 47), (47, 28, 56))
    cols = ((80, 160, 255), (60, 130, 230), (100, 180, 255), (70, 150, 240), (90, 170, 250))
    for (bx0, top, bx1), col in zip(blds, cols):
        _grad_rrect(img, (bx0 * u, top * u, bx1 * u, 54 * u), max(1, int(u)), col, tuple(c // 2 for c in col),
                    (10, 20, 60), max(1, int(0.8 * u)))
        for wy in range(top + 4, 52, 5):
            for wx in range(bx0 + 2, bx1 - 2, 4):
                if (wx * 7 + wy * 3) % 5:
                    d.rectangle((wx * u, wy * u, (wx + 1.6) * u, (wy + 2) * u), fill=(255, 240, 120, 255))
    d.line([(42 * u, 16 * u), (42 * u, 4 * u)], fill=(230, 230, 230, 255), width=max(1, int(u)))
    d.polygon([(42 * u, 4 * u), (54 * u, 7 * u), (42 * u, 10 * u)], fill=(255, 60, 120, 255))
    return _finish(img, n)


_DRAW.update({'cities': draw_cities})
