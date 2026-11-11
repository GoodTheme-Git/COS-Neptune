"""COS themes: a whole look in one file.

(Neptune's copy of COS Aurora's themes.py, same .ctheme format 1. Neptune changes:
its own built-in themes, and menu/taskbar colours are kept in the file but not
applied, since Neptune's bars are glass in the theme colour.)

A theme is a .ctheme file: a zip holding

    theme.json      name, author, description, and the look (see LOOK_KEYS)
    wallpaper.png   (or .jpg) optional: the theme's own picture
    preview.png     optional: a small picture of the desktop, made by Aurora

Themes are data only. Nothing in a theme is ever run, which is what makes it
safe to pass them around The COS Place (nep/cosplace.py) between strangers.
read_ctheme() is strict about what it accepts for the same reason.

Installed themes live in home/.neptune/themes/<id>/ where <id> is the first 16
hex digits of the .ctheme file's sha256, so the same theme installed twice is
one theme. Built-in themes are in BUILTIN.
"""
import hashlib
import io
import json
import os
import shutil
import time
import zipfile

from PIL import Image, ImageDraw

from . import gfx, theme, wallpaper

THEME_DIR = os.path.join(theme.STATE, 'themes')
EXPORT_DIR = os.path.join(theme.HOME, 'Documents', 'Themes')
EXT = '.ctheme'
FORMAT = 1

MAX_FILE = 24 * 1024 * 1024          # a .ctheme on disk / over the network
MAX_UNPACKED = 48 * 1024 * 1024      # everything inside it, uncompressed
MAX_JSON = 64 * 1024
MAX_PIXELS = 3840 * 2400             # embedded wallpapers are scaled down to this
ALLOWED = {'theme.json', 'wallpaper.png', 'wallpaper.jpg', 'preview.png'}
PORTABLE_WALLS = {'preset', 'bubble', 'nostalgia', 'solid', 'gradient', 'pattern', 'text'}
TEXT_LIMITS = {'name': 48, 'author': 48, 'description': 400, 'version': 16}


class ThemeError(Exception):
    pass


def _rgb(v, default):
    try:
        r, g, b = [max(0, min(255, int(x))) for x in list(v)[:3]]
        return [r, g, b]
    except (TypeError, ValueError):
        return list(default)


def _clean_text(v, n):
    s = str(v or '').replace('\r', '')
    return ''.join(c for c in s if c == '\n' or c >= ' ')[:n]


def clean_look(look):
    """Keep only the look keys Aurora knows, with sane values."""
    look = look if isinstance(look, dict) else {}
    out = {}
    out['glass_color'] = _clean_text(look.get('glass_color') or 'Custom', 24)
    out['glass_rgb'] = _rgb(look.get('glass_rgb'), theme.DEFAULTS['glass_rgb'])
    try:
        out['intensity'] = max(0.0, min(1.0, float(look.get('intensity', theme.DEFAULTS['intensity']))))
    except (TypeError, ValueError):
        out['intensity'] = theme.DEFAULTS['intensity']
    out['transparency'] = bool(look.get('transparency', True))
    for k in ('menubar', 'taskbar'):
        v = look.get(k)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            out[k] = [_rgb(v[0], (90, 207, 171)), _rgb(v[1], (66, 131, 169))]
    if look.get('menu_text') is not None:
        out['menu_text'] = _rgb(look.get('menu_text'), (0, 0, 0))
    w = look.get('wall')
    if isinstance(w, dict) and w.get('kind') in PORTABLE_WALLS:
        out['wall'] = {k: v for k, v in w.items()
                       if isinstance(k, str) and len(k) < 24 and isinstance(v, (str, int, float, bool, list))}
    elif w == 'embedded':
        out['wall'] = 'embedded'
    if look.get('screensaver'):
        out['screensaver'] = _clean_text(look['screensaver'], 32)
    return out


# -- built-in themes ----------------------------------------------------------------------
BUILTIN = [
    {'id': 'builtin:neptune', 'name': 'Neptune Classic', 'author': 'COS', 'version': '3.1',
     'description': 'COS 3.1 "Neptune": deep teal glass over flowing light ribbons.',
     'look': {'glass_color': 'Neptune', 'glass_rgb': [38, 150, 160], 'intensity': 0.42, 'transparency': True,
              'wall': {'kind': 'preset', 'name': 'Neptune Aurora'}}},
    {'id': 'builtin:bubble', 'name': 'Bubble Sky', 'author': 'COS', 'version': '3.1',
     'description': 'The COS bubble on a bright sky, with sky-blue glass.',
     'look': {'glass_color': 'Sky', 'glass_rgb': [66, 140, 210], 'intensity': 0.45, 'transparency': True,
              'wall': {'kind': 'bubble', 'name': 'COS Bubble'}}},
    {'id': 'builtin:multibubble', 'name': 'Multi-Bubble', 'author': 'COS', 'version': '3.1',
     'description': 'The COS 3.1 multi-bubble design with Twilight glass.',
     'look': {'glass_color': 'Twilight', 'glass_rgb': [40, 70, 140], 'intensity': 0.5, 'transparency': True,
              'wall': {'kind': 'bubble', 'name': 'COS Multi-Bubble'}}},
    {'id': 'builtin:sunset', 'name': 'Sunset Grid', 'author': 'COS', 'version': '3.1',
     'description': 'Late-night synth: a neon grid at sunset, fuchsia glass.',
     'look': {'glass_color': 'Fuchsia', 'glass_rgb': [200, 50, 160], 'intensity': 0.5, 'transparency': True,
              'wall': {'kind': 'nostalgia', 'name': 'Sunset Grid'}, 'screensaver': 'Warp'}},
    {'id': 'builtin:trench', 'name': 'Deep Trench', 'author': 'COS', 'version': '3.1',
     'description': 'Dark water and slate glass, for late nights.',
     'look': {'glass_color': 'Slate', 'glass_rgb': [90, 100, 110], 'intensity': 0.55, 'transparency': True,
              'wall': {'kind': 'preset', 'name': 'Deep Trench'}, 'screensaver': 'Bubbles'}},
]

DEFAULT_PRESET = 'Neptune Aurora'


def _bars(tint):
    """Neptune draws its bars as glass in the theme colour; previews show that."""
    t = tuple(tint)
    return [tuple(int(c * 0.55 + 10) for c in t), tuple(int(c * 0.25) for c in t)]


class Theme:
    def __init__(self, meta, folder=None, builtin=False, tid=None):
        self.meta = meta
        self.folder = folder
        self.builtin = builtin
        self.id = tid or meta.get('id')
        self.look = clean_look(meta.get('look'))

    name = property(lambda self: self.meta.get('name') or 'Untitled')
    author = property(lambda self: self.meta.get('author') or 'Unknown')
    description = property(lambda self: self.meta.get('description') or '')
    version = property(lambda self: self.meta.get('version') or '1.0')

    def wall_spec(self):
        w = self.look.get('wall')
        if w == 'embedded' and self.folder:
            for n in ('wallpaper.png', 'wallpaper.jpg'):
                p = os.path.join(self.folder, n)
                if os.path.isfile(p):
                    return {'kind': 'image', 'path': p, 'fit': 'fill'}
            return None
        return w if isinstance(w, dict) else None

    def ctheme_path(self):
        p = os.path.join(self.folder or '', 'source' + EXT)
        return p if self.folder and os.path.isfile(p) else None

    def preview(self, w=224, h=126):
        p = os.path.join(self.folder, 'preview.png') if self.folder else None
        if p and os.path.isfile(p):
            try:
                im = Image.open(p)
                im.load()
                return im.convert('RGBA').resize((w, h), Image.LANCZOS)
            except Exception:
                pass
        return render_preview(self.look, self.wall_spec(), w, h)


def render_preview(look, wall_spec, w=224, h=126, wall_img=None):
    """A tiny desktop: wallpaper, menu bar, a window, taskbar."""
    try:
        if wall_img is not None:
            img = wall_img.convert('RGBA').resize((w, h), Image.LANCZOS)
        else:
            img = wallpaper.build(wall_spec or {'kind': 'preset', 'name': DEFAULT_PRESET}, w, h)
    except Exception:
        img = Image.new('RGBA', (w, h), (30, 60, 90, 255))
    img = img.convert('RGBA')
    tint = tuple(look.get('glass_rgb', (110, 200, 150)))
    mb = tb = _bars(tint)               # Neptune ignores a theme's bar colours: its bars are glass
    bh = max(6, h // 14)
    img.alpha_composite(gfx.vgradient(w, bh, ((0, tuple(mb[0])), (1, tuple(mb[1])))), (0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((0, bh, w, bh), fill=(6, 6, 6, 255))
    tx = (255, 255, 255)
    for i in range(6):
        x = w // 2 - 45 + i * 16
        d.rectangle((x, bh // 2 - 1, x + 10, bh // 2 + 1), fill=tx + (220,))
    img.alpha_composite(gfx.vgradient(w, bh + 2, ((0, tuple(tb[0])), (1, tuple(tb[1])))), (0, h - bh - 2))
    d.rectangle((0, h - bh - 3, w, h - bh - 3), fill=(254, 254, 254, 255))
    # a window in the theme's glass
    wx0, wy0, wx1, wy1 = int(w * 0.28), int(h * 0.22), int(w * 0.72), int(h * 0.68)
    frame = tint + (215,)
    d.rounded_rectangle((wx0, wy0, wx1, wy1), radius=3, fill=frame, outline=(0, 0, 0, 160))
    d.rectangle((wx0 + 3, wy0 + 9, wx1 - 3, wy1 - 3), fill=(244, 246, 248, 255))
    d.rectangle((wx1 - 12, wy0 + 2, wx1 - 4, wy0 + 6), fill=(200, 60, 50, 255))
    return img


# -- reading, installing ------------------------------------------------------------------
def read_ctheme(data):
    """Validate .ctheme bytes. Returns (meta, files) where files maps allowed
    names to bytes. Raises ThemeError for anything that isn't a clean theme."""
    if len(data) > MAX_FILE:
        raise ThemeError('theme file is too big (%d MB max)' % (MAX_FILE // 1048576))
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ThemeError('not a theme file (not a zip)')
    files = {}
    total = 0
    with zf:
        for info in zf.infolist():
            name = info.filename
            if info.is_dir() or name not in ALLOWED or name in files:
                raise ThemeError('unexpected file in theme: %r' % name[:60])
            if info.flag_bits & 0x1:
                raise ThemeError('encrypted themes are not supported')
            total += info.file_size
            if total > MAX_UNPACKED:
                raise ThemeError('theme unpacks too big')
            raw = zf.read(info)
            if len(raw) != info.file_size:
                raise ThemeError('theme is damaged')
            files[name] = raw
    if 'theme.json' not in files or len(files['theme.json']) > MAX_JSON:
        raise ThemeError('theme.json is missing or too big')
    try:
        meta = json.loads(files['theme.json'].decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ThemeError('theme.json is not valid JSON')
    if not isinstance(meta, dict) or int(meta.get('format', 0) or 0) > FORMAT:
        raise ThemeError('theme is for a newer COS')
    clean = {k: _clean_text(meta.get(k), n) for k, n in TEXT_LIMITS.items()}
    clean['format'] = FORMAT
    clean['created'] = _clean_text(meta.get('created'), 32)
    clean['look'] = clean_look(meta.get('look'))
    if not clean['name'].strip():
        raise ThemeError('theme has no name')
    for n in ('wallpaper.png', 'wallpaper.jpg', 'preview.png'):
        if n in files:
            try:
                with Image.open(io.BytesIO(files[n])) as im:
                    if im.format not in ('PNG', 'JPEG') or im.size[0] * im.size[1] > MAX_PIXELS * 2:
                        raise ThemeError('%s is not a usable picture' % n)
                    im.verify()
            except ThemeError:
                raise
            except Exception:
                raise ThemeError('%s is not a usable picture' % n)
    if clean['look'].get('wall') == 'embedded' and not ({'wallpaper.png', 'wallpaper.jpg'} & set(files)):
        clean['look'].pop('wall')
    return clean, files


def theme_id(data):
    return hashlib.sha256(data).hexdigest()[:16]


def install_bytes(data):
    """Install a theme from .ctheme bytes. Returns the Theme."""
    meta, files = read_ctheme(data)
    tid = theme_id(data)
    folder = os.path.join(THEME_DIR, tid)
    tmp = folder + '.tmp'
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    for n, raw in files.items():
        if n == 'theme.json':
            continue
        with open(os.path.join(tmp, n), 'wb') as f:
            f.write(raw)
    meta['id'] = tid
    meta['installed'] = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(os.path.join(tmp, 'theme.json'), 'w') as f:
        json.dump(meta, f, indent=2)
    with open(os.path.join(tmp, 'source' + EXT), 'wb') as f:
        f.write(data)
    shutil.rmtree(folder, ignore_errors=True)
    os.replace(tmp, folder)
    return Theme(meta, folder, tid=tid)


def install_file(path):
    with open(path, 'rb') as f:
        data = f.read(MAX_FILE + 1)
    return install_bytes(data)


def installed():
    """Built-in themes first, then installed ones (newest first)."""
    out = [Theme(dict(b), builtin=True) for b in BUILTIN]
    found = []
    try:
        names = os.listdir(THEME_DIR)
    except OSError:
        names = []
    for n in names:
        p = os.path.join(THEME_DIR, n, 'theme.json')
        if n.endswith('.tmp') or not os.path.isfile(p):
            continue
        try:
            with open(p) as f:
                meta = json.load(f)
            found.append((os.path.getmtime(p), Theme(meta, os.path.join(THEME_DIR, n), tid=n)))
        except (OSError, ValueError):
            pass
    out += [t for _m, t in sorted(found, key=lambda x: -x[0])]
    return out


def find(tid):
    for t in installed():
        if t.id == tid:
            return t
    return None


def uninstall(tid):
    if not tid or tid.startswith('builtin:') or '/' in tid or tid.startswith('.'):
        return False
    shutil.rmtree(os.path.join(THEME_DIR, tid), ignore_errors=True)
    return True


def apply(t, shell):
    """Make theme t the current look."""
    s = shell.settings
    look = t.look
    s['glass_color'] = look.get('glass_color', 'Custom')
    s['glass_rgb'] = list(look.get('glass_rgb', s['glass_rgb']))
    s['intensity'] = look.get('intensity', s['intensity'])
    s['transparency'] = look.get('transparency', s['transparency'])
    spec = t.wall_spec()
    if spec:
        s['wall'] = spec
        if spec.get('kind') == 'preset':
            s['wallpaper'] = spec.get('name', s['wallpaper'])
    if look.get('screensaver'):
        s['screensaver'] = look['screensaver']
    s['theme'] = t.id
    s.save()
    shell.apply_settings()
    shell.desk_dirty = True
    for w in shell.windows:
        w.frame_dirty = True


# -- making themes --------------------------------------------------------------------------
def current_look(shell):
    s = shell.settings
    tint = tuple(s['glass_rgb'])
    look = {'glass_color': s.get('glass_color', 'Custom'), 'glass_rgb': list(tint),
            'intensity': s.get('intensity'), 'transparency': s.get('transparency'),
            'screensaver': s.get('screensaver')}
    return look


def build_ctheme(shell, name, author='', description='', version='1.0'):
    """Pack the current look into .ctheme bytes."""
    look = current_look(shell)
    spec = shell.wall_spec()
    files = {}
    if spec.get('kind') in PORTABLE_WALLS:
        look['wall'] = spec
    else:
        # a picture (or anything not portable): embed what's on screen now, scaled to fit
        try:
            if spec.get('kind') == 'image' and os.path.isfile(spec.get('path', '')):
                src = Image.open(spec['path'])
                src.load()
                src = src.convert('RGB')
            else:
                src = shell.wall().convert('RGB')
            iw, ih = src.size
            if iw * ih > MAX_PIXELS:
                k = (MAX_PIXELS / float(iw * ih)) ** 0.5
                src = src.resize((max(1, int(iw * k)), max(1, int(ih * k))), Image.LANCZOS)
            buf = io.BytesIO()
            src.save(buf, 'JPEG', quality=90)
            files['wallpaper.jpg'] = buf.getvalue()
            look['wall'] = 'embedded'
        except Exception:
            look['wall'] = {'kind': 'preset', 'name': DEFAULT_PRESET}
    meta = {'format': FORMAT, 'name': name, 'author': author, 'description': description, 'version': version,
            'created': time.strftime('%Y-%m-%d %H:%M:%S'), 'made_with': theme.FULLNAME, 'look': look}
    buf = io.BytesIO()
    cl = clean_look(look)
    if look['wall'] == 'embedded':
        prev = render_preview(cl, None, 448, 252, wall_img=shell.wall())
    else:
        prev = render_preview(cl, look['wall'], 448, 252)
    prev.save(buf, 'PNG')
    files['preview.png'] = buf.getvalue()
    files['theme.json'] = json.dumps(meta, indent=2).encode('utf-8')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for n in ('theme.json', 'preview.png', 'wallpaper.jpg'):
            if n in files:
                zf.writestr(n, files[n])
    data = out.getvalue()
    read_ctheme(data)          # never hand out a theme we wouldn't accept ourselves
    return data


def safe_filename(name):
    keep = ''.join(c if c.isalnum() or c in ' -_' else '_' for c in name).strip() or 'Theme'
    return keep[:48] + EXT


def export(t):
    """Copy an installed theme's .ctheme to Documents/Themes. Returns the path."""
    src = t.ctheme_path()
    if not src:
        raise ThemeError('built-in themes come with every COS Neptune, so there is nothing to export')
    os.makedirs(EXPORT_DIR, exist_ok=True)
    dst = os.path.join(EXPORT_DIR, safe_filename(t.name))
    shutil.copyfile(src, dst)
    return dst


def save_new(shell, name, author='', description=''):
    """Make a theme from the current look, install it, and drop a copy in
    Documents/Themes. Returns (Theme, path)."""
    data = build_ctheme(shell, name, author, description)
    t = install_bytes(data)
    os.makedirs(EXPORT_DIR, exist_ok=True)
    path = os.path.join(EXPORT_DIR, safe_filename(name))
    with open(path, 'wb') as f:
        f.write(data)
    shell.settings['theme'] = t.id
    shell.settings.save()
    return t, path


def found_files():
    """.ctheme files lying around in Downloads / Documents / Desktop that aren't installed."""
    have = {t.id for t in installed()}
    out = []
    for base in ('Downloads', 'Documents', 'Desktop', os.path.join('Documents', 'Themes')):
        d = os.path.join(theme.HOME, base)
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            p = os.path.join(d, n)
            if n.lower().endswith(EXT) and os.path.isfile(p) and os.path.getsize(p) <= MAX_FILE:
                try:
                    with open(p, 'rb') as f:
                        tid = theme_id(f.read())
                except OSError:
                    continue
                if tid not in have:
                    out.append(p)
                    have.add(tid)
    return out
