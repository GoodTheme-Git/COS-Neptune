"""Neptune theme: glass colours, metrics, persisted settings."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.join(ROOT, 'home')
STATE = os.path.join(HOME, '.neptune')
SETTINGS_FILE = os.path.join(STATE, 'settings.json')
CACHE = os.path.join(ROOT, 'cache')
ASSETS = os.path.join(ROOT, 'assets')

VERSION = '3.1'
CODENAME = 'Neptune'


def _build_time():
    """When this build was made: the newest modification time of Neptune's
    own source files, so the build number moves only when the code does."""
    newest = 0.0
    for base, dirs, files in os.walk(os.path.join(ROOT, 'nep')):
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for n in files:
            if n.endswith(('.py', '.nepapp')):
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(base, n)))
                except OSError:
                    pass
    try:
        newest = max(newest, os.path.getmtime(os.path.join(ROOT, 'neptune.py')))
    except OSError:
        pass
    return newest or __import__('time').time()


def make_build(version, when):
    """<general version><revision><YYYYMMDDHHMMSS>: COS 3.0 built 2026-10-31
    19:45:12 is build 3020261031194512."""
    import time as _t
    major, _, rev = version.partition('.')
    return '%s%s%s' % (major, rev or '0', _t.strftime('%Y%m%d%H%M%S', _t.localtime(when)))


BUILD_TIME = _build_time()
BUILD = make_build(VERSION, BUILD_TIME)
FULLNAME = 'COS %s "%s"' % (VERSION, CODENAME)
FULLNAME_BUILD = '%s  Build %s' % (FULLNAME, BUILD)


def build_parts(build=None):
    """Split a build number back into (general version, revision, build time)."""
    b = build or BUILD
    if build is None:
        major, _, rev = VERSION.partition('.')
        rev = rev or '0'
    else:
        major, rev = b[:-15], b[-15:-14]
    stamp = b[-14:]
    return major, rev, '%s-%s-%s %s:%s:%s' % (stamp[:4], stamp[4:6], stamp[6:8], stamp[8:10], stamp[10:12], stamp[12:])

# Window metrics (Windows 7-ish proportions)
TITLE_H = 30
BORDER = 7
RADIUS = 8
MENUBAR_H = 34
TASKBAR_H = 42

# Glass colour presets: name -> (r, g, b)
GLASS_COLORS = [
    ('Neptune', (38, 150, 160)),
    ('Sky', (66, 140, 210)),
    ('Twilight', (40, 70, 140)),
    ('Sea', (30, 130, 120)),
    ('Leaf', (80, 160, 60)),
    ('Lime', (150, 190, 40)),
    ('Sun', (230, 180, 40)),
    ('Pumpkin', (230, 120, 30)),
    ('Ruby', (190, 30, 50)),
    ('Fuchsia', (200, 50, 160)),
    ('Violet', (120, 70, 190)),
    ('Lavender', (160, 140, 210)),
    ('Taupe', (150, 130, 110)),
    ('Chocolate', (100, 60, 40)),
    ('Slate', (90, 100, 110)),
    ('Frost', (220, 230, 240)),
]

WALLPAPERS = ['Neptune Aurora', 'Deep Trench', 'Emerald Drift', 'Nebula', 'Solid Midnight']

DEFAULTS = {
    'glass_color': 'Neptune',
    'glass_rgb': [38, 150, 160],
    'intensity': 0.42,
    'transparency': True,
    'wallpaper': 'Neptune Aurora',
    'animations': True,
    'sounds': True,
    'show_gadget': True,
    'clock_24h': False,
    'boot_screen': True,
    'cursor_speed': 1.0,
    'gdr_mode': '',
    'login_screen': False,
    'webinator_js': True,
    'lock_password': '',
    'show_build': True,
    'screensaver': 'Bubbles',
    'saver_wait': 10,
    'saver_lock': False,
    'emergency_exit': True,
    'show_icons': True,          # from COS 2.4: show_icons
    'cursor_scheme': 'Aero',     # from COS 2.4: cursor_shape
    'natural_scroll': False,     # from COS 2.4: natural_scroll
    'tap_to_click': True,        # from COS 2.4: tap_to_click
    'sound_volume': 100,         # from COS 2.4: sound_volume
    'druman_volume': 80,         # from COS 2.4: druman_volume
    'drivers_off': False,        # from COS 2.4: drivers_off (safe mode)
    'explorer_style': 'CEAP',    # CEAP or Classic
    'web_style': 'CEAP',         # CEAP:Web or classic Webinator
    'desktop_pet': True,
    'daily_tip': True,
    'theme': '',                 # id of the theme last applied (nep/themes.py)
    'cosplace_join': False,      # share and browse The COS Place (off until the user joins)
    'cosplace_peers': [],        # computers added by hand, 'host' or 'host:port'
    'aero': True,          # False = Neptune Basic (no glass, blur or shadows)
    'wall': None,          # custom background spec; None = the 'wallpaper' preset
    'oobe_done': False,    # Set Up COS Neptune has been completed
    'user_name': '',       # shown on the Start menu and lock screen ('' = the account name)
    'computer_name': '',
}


class Settings(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        try:
            with open(SETTINGS_FILE) as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in DEFAULTS:
                        self[k] = v
                if 'oobe_done' not in data:
                    self['oobe_done'] = True     # an install from before Setup existed: don't ambush it
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            os.makedirs(STATE, exist_ok=True)
            tmp = SETTINGS_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(dict(self), f, indent=2)
            os.replace(tmp, SETTINGS_FILE)
        except OSError:
            pass

    @property
    def tint(self):
        return tuple(self['glass_rgb'])


# client-area palette (the opaque parts of windows)
CLIENT_BG = (255, 255, 255, 255)
CLIENT_TEXT = (20, 20, 20, 255)
PANEL_BG = (240, 243, 247, 255)
SEL_BG = (205, 232, 255, 255)
SEL_BORDER = (102, 167, 232, 255)
HOVER_BG = (229, 243, 251, 255)
HOVER_BORDER = (176, 214, 241, 255)
LINK = (0, 102, 204, 255)
MUTED = (100, 110, 120, 255)


def ensure_dirs():
    for d in (HOME, STATE, CACHE, os.path.join(HOME, 'Documents'),
              os.path.join(HOME, 'Pictures'), os.path.join(HOME, 'Desktop')):
        os.makedirs(d, exist_ok=True)
