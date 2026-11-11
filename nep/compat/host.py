"""COS compatibility mode, the Neptune side.

* CosProgram  one .cosapp file: manifest read by parsing (nothing runs until
              launch, as in v2.4), loaded on demand with the compat `cos` API.
* CosHost     a Neptune window showing one COS window as a grid of
              character cells in the old console colours.
* CompatWM    what an app's `self.wm` points at: opening dialogs, closing,
              notifications, sounds, double-click detection.
* CompatRegistry  finds COS programs and puts them in Neptune's registry.
* CompatSettings  the "Compatibility" properties page, kept per program.

Programs are found in two places:
  home/.neptune/compat/apps   installed in Neptune from a .cip
  ~/.cos/apps                 the programs you already have in COS v2.4,
                              run in place (read-only; cos.HOME stays ~/.cos
                              so their data files are where they expect)
"""
import ast
import json
import os
import signal
import time

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .. import gfx, theme
from ..app import App
from . import cos24
from .cos24 import cp, Grid, Canvas, CosFatal, CP_WINDOW, CP_STATUS, CP_TEXT
from .cursesshim import shim as curses, A_BOLD, A_DIM, A_REVERSE, A_UNDERLINE

NEPTUNE_APPS = os.path.join(theme.STATE, 'compat', 'apps')
COS24_HOME = os.path.expanduser('~/.cos')
COS24_APPS = os.path.join(COS24_HOME, 'apps')
SETTINGS_FILE = os.path.join(theme.STATE, 'compat', 'settings.json')
APP_EXT = '.cosapp'

MODES = [('2.4', 'COS 2.4 "Druman"', 'Mouse, Ctrl/Alt keys and every v2.4 dialog.'),
         ('2.3', 'COS 2.3', 'Keyboard only: the program gets no mouse clicks or scrolling, and cos.VERSION says 2.3.')]
FONT_SIZES = [('Small', 12), ('Normal', 15), ('Large', 19)]
DEFAULTS = {'enabled': True, 'mode': '2.4', 'theme': 0, 'text80': False, 'crt': False, 'font': 'Normal'}

# the Linux console's VGA palette; bold lifts the foreground to the bright half
VGA = [(0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 85, 0), (0, 0, 170), (170, 0, 170), (0, 170, 170), (170, 170, 170)]
VGA_BRIGHT = [(85, 85, 85), (255, 85, 85), (85, 255, 85), (255, 255, 85), (85, 85, 255), (255, 85, 255),
              (85, 255, 255), (255, 255, 255)]
AMBER = None


# =============================================================================
# per-program settings
# =============================================================================
def _load_all():
    try:
        with open(SETTINGS_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def get_settings(path):
    s = dict(DEFAULTS)
    s.update(_load_all().get(os.path.abspath(path), {}))
    if not s.get('enabled', True):
        s.update(mode='2.4', theme=0, text80=False, crt=False, font='Normal')
    return s


def raw_settings(path):
    s = dict(DEFAULTS)
    s.update(_load_all().get(os.path.abspath(path), {}))
    return s


def save_settings(path, s):
    d = _load_all()
    d[os.path.abspath(path)] = {k: s[k] for k in DEFAULTS}
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    tmp = SETTINGS_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, SETTINGS_FILE)


# =============================================================================
# one .cosapp
# =============================================================================
class AppTimeout(Exception):
    pass


def _alarm(_s, _f):
    raise AppTimeout()


class CosProgram:
    LOAD_TIMEOUT = 15

    def __init__(self, path, source='neptune'):
        self.path = os.path.abspath(path)
        self.source = source            # 'neptune' or 'cos24'
        self.stem = os.path.basename(path)[:-len(APP_EXT)]
        self.key = 'cos:%s:%s' % (source, self.stem)
        self.manifest = {}
        self.error = ''
        self.mtime = 0
        self.peek()

    @property
    def home(self):
        return COS24_HOME if self.source == 'cos24' else theme.STATE

    @property
    def title(self):
        return str(self.manifest.get('name') or self.stem)

    @property
    def ok(self):
        return not self.error and bool(self.manifest)

    @property
    def external(self):
        return str(self.manifest.get('kind', 'window')).lower() == 'external'

    def peek(self):
        """Read COSAPP and check for class App, by parsing only."""
        self.manifest, self.error = {}, ''
        try:
            self.mtime = os.path.getmtime(self.path)
            with open(self.path, encoding='utf-8', errors='replace') as f:
                tree = ast.parse(f.read(), filename=self.path)
        except SyntaxError as e:
            self.error = 'syntax error on line %s: %s' % (e.lineno, e.msg)
            return False
        except (OSError, ValueError) as e:
            self.error = 'cannot read: %s' % e
            return False
        has_app = False
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == 'App':
                has_app = bool(node.bases)
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'COSAPP'
                                                      for t in node.targets):
                try:
                    self.manifest = ast.literal_eval(node.value)
                except Exception:
                    self.error = 'COSAPP must be a plain dict of literals'
                    return False
        if not isinstance(self.manifest, dict) or not self.manifest:
            self.manifest = {}
            self.error = 'not a COS app: no COSAPP = {...} manifest'
            return False
        if self.external:
            self.error = '' if self.manifest.get('command') else "kind 'external' needs a command"
            return not self.error
        if not has_app:
            self.error = 'no class App(cos.Window) in the file'
            return False
        return True

    def load(self, settings, shell):
        """Run the file and return its App class (raises on failure)."""
        with open(self.path, encoding='utf-8', errors='replace') as f:
            src = f.read()
        version = settings['mode']
        api = cos24.make_api(version, self.home, dict(shell.settings),
                             COS24_APPS if self.source == 'cos24' else NEPTUNE_APPS,
                             os.path.join(theme.ASSETS, 'sounds'))
        ns = {'__name__': 'cosapp_' + self.stem, '__file__': self.path, 'cos': api, 'curses': curses}
        old = None
        armed = False
        try:
            old = signal.signal(signal.SIGALRM, _alarm)
            signal.alarm(self.LOAD_TIMEOUT)
            armed = True
        except (ValueError, OSError):
            pass
        try:
            exec(compile(src, self.path, 'exec'), ns)
        except AppTimeout:
            raise RuntimeError('still running after %ds at import time' % self.LOAD_TIMEOUT)
        finally:
            if armed:
                signal.alarm(0)
                if old is not None:
                    signal.signal(signal.SIGALRM, old)
        cls = ns.get('App')
        if not (isinstance(cls, type) and issubclass(cls, cos24.Window)):
            raise TypeError('App must be a class deriving from cos.Window')
        if isinstance(ns.get('COSAPP'), dict):
            self.manifest = ns['COSAPP']
        return cls, api

    def make(self, settings, shell):
        cls, api = self.load(settings, shell)
        try:
            win = cls(api.state)
        except TypeError:
            win = cls()
        if not isinstance(win, cos24.Window):
            raise TypeError('App() did not return a window')
        if not win.title:
            win.title = self.title
        return win


# =============================================================================
# the window manager an app sees
# =============================================================================
SOUND_MAP = {'notify': 'notify', 'warn': 'warn', 'error': 'warn', 'boot': 'boot', 'shutdown': 'shutdown'}


class CompatSound:
    def __init__(self, shell):
        self.shell = shell

    def play(self, name):
        self.shell.sound(SOUND_MAP.get(str(name), 'notify')) if str(name) in SOUND_MAP else None


class CompatWM:
    def __init__(self, shell, program, settings):
        self.shell = shell
        self.program = program
        self.settings = settings
        self.sound = CompatSound(shell)
        self.hosts = {}                 # cos window -> CosHost
        self.main = None                # the program's own window
        self._last_click = (None, None, 0)
        self.state = dict(shell.settings)

    def open(self, cwin, parent_host=None):
        cwin.wm = self
        owner = parent_host or next(iter(self.hosts.values()), None)
        host = CosHost(self.shell, cos_win=cwin, wm=self)
        self.shell.open_window(host, parent=owner.win if owner and owner.win else None,
                               center=getattr(cwin, 'dialog', False))
        return cwin

    def close(self, cwin):
        host = self.hosts.get(cwin)
        if host is not None and host.win is not None:
            self.shell.close_window(host.win)
        cwin.closed = True

    def notify(self, msg, sound='notify'):
        self.shell.notify(self.program.title if self.program else 'COS program', str(msg), 'info', 6)

    def was_double(self, win, idx):
        now = time.time()
        w, i, t = self._last_click
        self._last_click = (win, idx, now)
        return w is win and i == idx and now - t < 0.45

    def toggle_max(self, cwin):
        host = self.hosts.get(cwin)
        if host and host.win:
            if host.win.state == 'normal':
                self.shell.snap(host.win, 'max')
            else:
                self.shell.unmaximize(host.win)

    # v2.4 apps sometimes reached for these
    def notify_msg(self, msg):
        self.notify(msg)


# =============================================================================
# the Neptune window around one COS window
# =============================================================================
PAD = 6


class CosHost(App):
    title = 'COS program'
    icon = 'cosapp'
    bg = (0, 0, 0, 255)
    dark_client = True
    program = None          # set on per-program subclasses

    def __init__(self, shell, cos_win=None, wm=None, path=None, **kw):
        super().__init__(shell, **kw)
        prog = self.program
        if prog is None and path:
            prog = CosProgram(path, 'cos24' if path.startswith(COS24_APPS) else 'neptune')
            if prog.error:
                raise RuntimeError(prog.error)
        self.prog = prog
        self.settings = get_settings(prog.path) if prog else (wm.settings if wm else dict(DEFAULTS))
        if cos_win is None:
            wm = CompatWM(shell, prog, self.settings)
            cos_win = prog.make(self.settings, shell)
            cos_win.wm = wm
            if self.settings['text80']:
                cos_win.w, cos_win.h = 82, 26
            self.is_main = True
            wm.main = self
        else:
            self.is_main = False
        self.wm = wm
        self.cw = cos_win
        wm.hosts[cos_win] = self
        self.dialog = bool(getattr(cos_win, 'dialog', False))
        self.resizable = bool(getattr(cos_win, 'resizable', True)) and not self.dialog and \
            not self.settings['text80']
        self.title = str(cos_win.title or (prog.title if prog else 'COS'))
        self.has_status = self.is_main or bool(self.safe_status())
        self.apply_settings(resize=False)
        self.size = self.size_for(cos_win.cw, cos_win.chh)
        self.min_size = self.size_for(cos_win.min_w - 2, cos_win.min_h - 2)
        self.grid_snap = None
        self.status_text = ''
        self.fatal = None
        self.last_tick = 0
        self.focused = True
        self.caret_on = True
        self.render_grid()

    # -- settings -------------------------------------------------------------------
    def apply_settings(self, resize=True):
        s = self.settings
        self.pairs = cos24.pair_table(int(s.get('theme', 0)))
        size = dict(FONT_SIZES).get(s.get('font', 'Normal'), 15)
        self.fnt = gfx.font(size, False, 'mono')
        self.fnt_b = gfx.font(size, True, 'mono')
        self.cellw = max(5, int(round(self.fnt.getlength('M'))))
        self.cellh = int(size * 1.25)
        self.crt = bool(s.get('crt'))
        self.mouse_ok = s.get('mode', '2.4') != '2.3'
        self._scan = None
        if resize and self.win is not None:
            w, h = self.size_for(self.cw.cw, self.cw.chh)
            self.shell.resize_window(self.win, w, h)
            self.grid_snap = None
            self.render_grid()
            self.invalidate()

    def safe_status(self):
        try:
            return self.cw.status()
        except Exception:
            return ''

    def size_for(self, cols, rows):
        """Outer window size (Neptune frames included) for a client of cols x rows cells."""
        return (cols * self.cellw + 2 * PAD + 2 * theme.BORDER,
                (rows + (1 if getattr(self, 'has_status', True) else 0)) * self.cellh + 2 * PAD +
                theme.TITLE_H + theme.BORDER)

    # -- running the COS window ------------------------------------------------------------
    def guard(self, fn, *a):
        if self.fatal:
            return None
        try:
            return fn(*a)
        except CosFatal as e:
            # a fatal error stops the whole program: blue screen in its main
            # window, every dialog it had open goes away
            main = self.wm.main if self.wm.main is not None and self.wm.main.win is not None else self
            main.fatal = (e.title or 'Fatal error', e.reason or '')
            for cwin, host in list(self.wm.hosts.items()):
                if host is not main and host.win is not None and not host.win.closing:
                    self.shell.close_window(host.win, force=True)
            if main.win is not None:
                self.shell.activate(main.win)
            self.shell.sound('warn')
            main.invalidate()
        return None

    def render_grid(self):
        cw = self.cw
        g = Grid(cw.cw, cw.chh, cp(CP_WINDOW))
        active = self.win is None or self.shell.active is self.win
        self.guard(cw.draw_client, Canvas(g, 0, 0, cw.chh, cw.cw), active)
        st = self.guard(cw.status) or ''
        if not self.mouse_ok and self.is_main:
            st = (st + '   ' if st else '') + '[COS 2.3 mode: keyboard only]'
        snap = (g.snapshot(), st, active, cw.title, getattr(cw, 'caret', None) if cw.want_cursor else None)
        if snap != self.grid_snap:
            self.grid_snap = snap
            self.grid = g
            self.status_text = st
            if str(cw.title) != self.title and cw.title:
                self.set_title(str(cw.title))
            return True
        return False

    def on_tick(self, now):
        if self.cw.closed and self.win is not None and not self.win.closing:
            self.shell.close_window(self.win)
            return False
        if now - self.last_tick < 0.1:
            return False
        self.last_tick = now
        self.guard(self.cw.tick)
        changed = self.render_grid()
        if self.cw.want_cursor:
            on = int(now * 2) % 2 == 0
            if on != self.caret_on:
                self.caret_on = on
                changed = True
        return changed

    # -- painting -----------------------------------------------------------------------------
    def colors(self, attr):
        fg, bg = self.pairs.get((attr >> 8) & 0xff, (7, 0))
        if attr & A_REVERSE:
            fg, bg = bg, fg
        fgc = (VGA_BRIGHT if attr & A_BOLD else VGA)[fg % 8]
        bgc = VGA[bg % 8]
        if attr & A_DIM:
            fgc = tuple((a + b) // 2 for a, b in zip(fgc, bgc))
        return fgc, bgc

    def draw(self, img, ui):
        w, h = img.size
        if self.fatal:
            self.draw_fatal(img, ui)
            return
        cw, ch = self.cellw, self.cellh
        d = ui.d
        base_bg = self.colors(cp(CP_WINDOW))[1]
        d.rectangle((0, 0, w, h), fill=base_bg + (255,))
        rows = self.grid.cells
        status_attr = cp(CP_STATUS)
        lines = [row for row in rows]
        if self.has_status:
            lines.append([[c, status_attr] for c in (' ' + self.status_text).ljust(self.cw.cw)[:self.cw.cw]])
        for r, row in enumerate(lines):
            y = PAD + r * ch
            x0 = 0
            while x0 < len(row):
                attr = row[x0][1]
                x1 = x0
                while x1 < len(row) and row[x1][1] == attr:
                    x1 += 1
                fgc, bgc = self.colors(attr)
                px0, px1 = PAD + x0 * cw, PAD + x1 * cw
                last = self.has_status and r == len(lines) - 1
                if last and x1 == len(row):
                    px1 = w
                if bgc != base_bg or last:
                    d.rectangle((px0 if x0 else 0, y, px1 - 1, y + ch - 1), fill=bgc + (255,))
                text = ''.join(c[0] for c in row[x0:x1])
                if text.strip():
                    fnt = self.fnt_b if attr & A_BOLD else self.fnt
                    # draw cell by cell for box characters so the grid never drifts
                    if any(ord(c) > 0x2500 for c in text):
                        for i, c in enumerate(text):
                            if c != ' ':
                                self._glyph(d, c, PAD + (x0 + i) * cw, y, fgc, fnt)
                    else:
                        d.text((px0, y + ch // 2), text, font=gfx.font_for_obj(fnt, text), fill=fgc + (255,),
                               anchor='lm')
                if attr & A_UNDERLINE:
                    d.line([(px0, y + ch - 2), (px1 - 1, y + ch - 2)], fill=fgc + (255,))
                x0 = x1
        # caret
        caret = getattr(self.cw, 'caret', None)
        if self.cw.want_cursor and caret and self.caret_on and self.shell.active is self.win:
            cr, cc = caret
            x, y = PAD + cc * cw, PAD + cr * ch
            d.rectangle((x, y + ch - 3, x + cw - 1, y + ch - 1), fill=self.colors(cp(CP_TEXT))[0] + (255,))
        if self.crt:
            self.crt_effect(img)

    def _glyph(self, d, c, x, y, col, fnt):
        cw, ch = self.cellw, self.cellh
        cx, cy = x + cw // 2, y + ch // 2
        lines = {'─': ((0, cy - y), (cw, cy - y)), '│': ((cx - x, 0), (cx - x, ch))}
        box = {'┌': 'rd', '┐': 'ld', '└': 'ru', '┘': 'lu', '├': 'udr', '┤': 'udl', '┬': 'lrd', '┴': 'lru',
               '┼': 'lrud', '─': 'lr', '│': 'ud'}
        if c in box:
            segs = {'l': (x, cy, cx, cy), 'r': (cx, cy, x + cw, cy), 'u': (cx, y, cx, cy), 'd': (cx, cy, cx, y + ch)}
            for s in box[c]:
                d.line(segs[s], fill=col + (255,), width=1)
            return
        if c in '▒░▓':
            dens = {'░': 4, '▒': 2, '▓': 1}[c]
            for yy in range(y, y + ch):
                for xx in range(x + ((yy // 1) % 2) * (dens > 1), x + cw, dens):
                    d.point((xx, yy), fill=col + (255,))
            return
        if c == '█':
            d.rectangle((x, y, x + cw - 1, y + ch - 1), fill=col + (255,))
            return
        d.text((cx, cy), c, font=gfx.font_for_obj(fnt, c), fill=col + (255,), anchor='mm')

    def crt_effect(self, img):
        w, h = img.size
        # phosphor bloom: screen-blend a blurred copy so bright text glows
        rgb = img.convert('RGB')
        glow = rgb.filter(ImageFilter.GaussianBlur(3))
        img.paste(Image.blend(rgb, ImageChops.screen(rgb, glow), 0.8).convert('RGBA'))
        if self._scan is None or self._scan.size != img.size:
            sc = Image.new('RGBA', img.size, (0, 0, 0, 0))
            dd = ImageDraw.Draw(sc)
            for y in range(0, h, 2):
                dd.line([(0, y), (w, y)], fill=(0, 0, 0, 45))
            vig = gfx.radial(w, h, ((0, (0, 0, 0, 0)), (0.8, (0, 0, 0, 10)), (1, (0, 0, 0, 90))))
            sc.alpha_composite(vig)
            self._scan = sc
        img.alpha_composite(self._scan)

    def draw_fatal(self, img, ui):
        w, h = img.size
        ui.rect((0, 0, w, h), fill=(0, 0, 170))
        title, reason = self.fatal
        fnt = self.fnt
        y = PAD + self.cellh
        bar = ' %s ' % (self.prog.title if self.prog else 'COS')
        tw = gfx.text_width(fnt, bar)
        ui.d.rectangle(((w - tw) // 2, y - 2, (w + tw) // 2, y + self.cellh - 2), fill=(170, 170, 170, 255))
        ui.d.text((w // 2, y + self.cellh // 2 - 1), bar, font=fnt, fill=(0, 0, 170, 255), anchor='mm')
        y += self.cellh * 2
        lines = ['A fatal exception has stopped this COS program.', '', str(title) + ':']
        lines += cos24.wrap_text(reason, max(20, (w - 2 * PAD) // self.cellw - 4))
        lines += ['', 'The rest of Neptune is fine. Nothing was saved or lost.', '',
                  'Press any key or click to close this program.']
        for l in lines:
            ui.d.text((PAD + 2 * self.cellw, y), l, font=fnt, fill=(255, 255, 255, 255))
            y += self.cellh

    # -- input -----------------------------------------------------------------------------------
    KEYMAP = {'enter': 10, 'escape': 27, 'tab': 9, 'backspace': curses.KEY_BACKSPACE, 'up': curses.KEY_UP,
              'down': curses.KEY_DOWN, 'left': curses.KEY_LEFT, 'right': curses.KEY_RIGHT,
              'home': curses.KEY_HOME, 'end': curses.KEY_END, 'pageup': curses.KEY_PPAGE,
              'pagedown': curses.KEY_NPAGE, 'delete': curses.KEY_DC, 'insert': curses.KEY_IC, 'space': 32}

    def to_curses(self, ev):
        k, ch = ev['key'], ev.get('char', '')
        if ev.get('ctrl') and len(k) == 1 and k.isalpha():
            return ord(k.lower()) & 31
        if ch and not ev.get('ctrl'):
            return ord(ch)
        if k == 'tab' and ev.get('shift'):
            return curses.KEY_BTAB
        if k in self.KEYMAP:
            return self.KEYMAP[k]
        if k.startswith('f') and k[1:].isdigit():
            return curses.KEY_F0 + int(k[1:])
        return None

    def key(self, ev):
        if ev['type'] != 'key':
            return False
        if self.fatal:
            self.close()
            return True
        code = self.to_curses(ev)
        if code is None:
            return False
        used = self.guard(self.cw.on_key, code)
        if not used and not self.fatal:
            if code in (ord('q'), ord('Q')) and not self.dialog and not self.cw.want_cursor:
                self.close()
                return True
            if code == 27 and self.dialog:
                self.wm.close(self.cw)
                return True
        self.after_input()
        return bool(used)

    def cell_at(self, x, y):
        return (y - PAD) // self.cellh, (x - PAD) // self.cellw

    def mouse(self, kind, x, y, button=1, clicks=1):
        if kind == 'down':
            if self.fatal:
                self.close()
                return
            if not self.mouse_ok:
                return
            row, col = self.cell_at(x, y)
            if 0 <= row < self.cw.chh and 0 <= col < self.cw.cw:
                self.guard(self.cw.on_click, row, col, button)
                self.after_input()

    def on_wheel(self, dy, x, y):
        if self.mouse_ok and not self.fatal:
            self.guard(self.cw.on_scroll, -dy)
            self.after_input()

    def after_input(self):
        if self.cw.closed:
            return
        self.render_grid()
        self.invalidate()

    def wants_caret(self):
        return False

    def cursor_at(self, x, y):
        return 'arrow'

    def on_focus(self, focused):
        self.render_grid()

    def on_resize(self):
        if self.win is None:
            return
        w, h = self.win.client_size
        cols = max(self.cw.min_w - 2, (w - 2 * PAD) // self.cellw)
        rows = max(self.cw.min_h - 2, (h - 2 * PAD) // self.cellh - (1 if self.has_status else 0))
        if (cols, rows) != (self.cw.cw, self.cw.chh):
            self.cw.w, self.cw.h = cols + 2, rows + 2
            self.guard(self.cw.on_resize)
            self.render_grid()

    def on_close(self):
        self.guard(self.cw.on_close)
        self.cw.closed = True
        self.wm.hosts.pop(self.cw, None)
        if self.is_main:
            for cwin, host in list(self.wm.hosts.items()):
                if host.win is not None:
                    self.shell.close_window(host.win, force=True)
        return True

    def context_menu(self, x, y):
        if not self.prog:
            return None
        return [{'label': 'Compatibility settings...', 'icon': 'cosapp',
                 'action': lambda: self.shell.launch('compat', path=self.prog.path)},
                {'label': 'About compatibility mode', 'icon': 'help', 'action': lambda: self.shell.launch('help', page='compat')},
                {'sep': True},
                {'label': 'Close program', 'action': self.close}]


def program_class(prog):
    """A registry entry class bound to one COS program."""
    return type('Cos_' + ''.join(c for c in prog.stem if c.isalnum()), (CosHost,),
                {'program': prog, 'title': prog.title, 'icon': 'cosapp'})


# =============================================================================
# discovery
# =============================================================================
class CompatRegistry:
    def __init__(self, shell):
        self.shell = shell
        self.programs = {}
        self.last_scan = 0
        os.makedirs(NEPTUNE_APPS, exist_ok=True)
        self.scan()

    def sources(self):
        for folder, src in ((NEPTUNE_APPS, 'neptune'), (COS24_APPS, 'cos24')):
            try:
                for n in sorted(os.listdir(folder)):
                    if n.endswith(APP_EXT) and not n.startswith('.'):
                        yield os.path.join(folder, n), src
            except OSError:
                pass

    def scan(self):
        self.last_scan = time.time()
        reg = self.shell.registry
        seen = set()
        changed = False
        for path, src in self.sources():
            seen.add(path)
            p = self.programs.get(path)
            try:
                mt = os.path.getmtime(path)
            except OSError:
                continue
            if p is not None and p.mtime == mt:
                continue
            p = CosProgram(path, src)
            self.programs[path] = p
            changed = True
            if p.ok and not p.external:
                reg[p.key] = (program_class(p), p.title, 'cosapp')
            else:
                reg.pop(p.key, None)
        for path in list(self.programs):
            if path not in seen:
                reg.pop(self.programs.pop(path).key, None)
                changed = True
        if changed:
            self.shell.refresh_desktop_icons()
        return changed

    def tick(self, now):
        if now - self.last_scan > 2.0:
            self.scan()

    def desktop_entries(self):
        return [(p.title, 'cosapp', p.key) for p in self.programs.values()
                if p.ok and not p.external and p.manifest.get('desktop') and p.source == 'neptune']

    def by_path(self, path):
        return self.programs.get(os.path.abspath(path))


# =============================================================================
# the Compatibility properties page
# =============================================================================
class CompatSettings(App):
    title = 'Compatibility'
    icon = 'cosapp'
    size = (470, 520)
    dialog = True
    resizable = False
    bg = theme.CLIENT_BG

    def __init__(self, shell, path=None, **kw):
        super().__init__(shell, **kw)
        if not path:
            raise ValueError('no program given')
        self.path = os.path.abspath(path)
        reg = getattr(shell, 'compat', None)
        self.prog = (reg.by_path(self.path) if reg else None) or CosProgram(self.path)
        self.s = raw_settings(self.path)
        self.title = '%s Properties' % self.prog.title

    def draw(self, img, ui):
        w, h = img.size
        s = self.s
        # tab strip (one tab, like a Windows property sheet)
        ui.rect((0, 0, w, h), fill=theme.PANEL_BG)
        tab = (8, 8, 118, 32)
        gfx.round_rect(img, tab, 3, fill=(255, 255, 255, 255), outline=(137, 140, 149, 255), corners=(True, True, False, False))
        ui.text(((tab[0] + tab[2]) // 2, 20), 'Compatibility', 12, anchor='mm')
        page = (8, 31, w - 8, h - 46)
        ui.rect(page, fill=(255, 255, 255), outline=(137, 140, 149))
        ui.hline(tab[0] + 1, tab[2] - 2, 31, (255, 255, 255))
        x0 = page[0] + 12
        ui.icon('cosapp', (x0, 42), 32)
        ui.text((x0 + 42, 46), self.prog.title, 13, bold=True)
        where = 'COS 2.4 app folder (~/.cos/apps)' if self.prog.source == 'cos24' else 'Installed in Neptune'
        ui.text((x0 + 42, 64), where, 11, color=theme.MUTED)
        ui.wrap((x0, 90), 'If this program worked on an earlier version of COS but has problems now, choose the '
                'COS version it was written for.', w - 2 * x0 - 4, 11)
        y = 128
        ui.groupbox((x0, y, w - x0, y + 102), 'Compatibility mode')
        ui.checkbox((x0 + 10, y + 22), 'Run this program in compatibility mode for:', 'enabled', s['enabled'])
        yy = y + 44
        for key, label, desc in MODES:
            if s['enabled']:
                ui.radio((x0 + 30, yy), label, ('mode', key), s['mode'] == key, data=key)
            else:
                ui.text((x0 + 49, yy + 7), label, 12, color=(160, 160, 160), anchor='lm')
            yy += 22
        desc = dict((k, d) for k, _, d in MODES)[s['mode']]
        y += 112
        ui.groupbox((x0, y, w - x0, y + 180), 'Settings')
        ui.text((x0 + 10, y + 24), 'Colour scheme:', 12)
        name = cos24.THEMES[int(s['theme']) % len(cos24.THEMES)][0]
        bx = x0 + 110
        ui.button((bx, y + 16, bx + 24, y + 38), '◀', 'th_prev')
        ui.text((bx + 34, y + 27), name, 12, anchor='lm')
        ui.button((w - x0 - 34, y + 16, w - x0 - 10, y + 38), '▶', 'th_next')
        # preview strip in the scheme's colours
        pv = (x0 + 10, y + 46, w - x0 - 10, y + 76)
        pairs = cos24.pair_table(int(s['theme']))
        menu_fg, menu_bg = pairs[cos24.CP_MENUBAR]
        win_fg, win_bg = pairs[cos24.CP_WINDOW]
        ui.rect((pv[0], pv[1], pv[2], pv[1] + 12), fill=VGA[menu_bg])
        ui.text((pv[0] + 4, pv[1] + 6), 'COS  FILE  EDIT  APPS', 9, color=VGA[menu_fg], anchor='lm', kind='mono')
        ui.rect((pv[0], pv[1] + 12, pv[2], pv[3]), fill=VGA[win_bg])
        ui.text((pv[0] + 4, pv[1] + 21), 'Hello from COS!', 10, color=VGA_BRIGHT[pairs[cos24.CP_BRIGHT][0]],
                anchor='lm', kind='mono')
        ui.rect(pv, outline=(120, 120, 120))
        ui.checkbox((x0 + 10, y + 86), 'Run in 80 × 25 text mode', 'text80', s['text80'])
        ui.checkbox((x0 + 10, y + 108), 'CRT screen effect (scanlines and glow)', 'crt', s['crt'])
        ui.text((x0 + 10, y + 140), 'Text size:', 12, anchor='lm')
        fx = x0 + 80
        for label, _px in FONT_SIZES:
            ui.radio((fx, y + 133), label, ('font', label), s['font'] == label, data=label)
            fx += 80
        ui.wrap((x0, page[3] - 34), desc, w - 2 * x0, 11, color=theme.MUTED)
        # footer
        ui.button((12, h - 35, 112, h - 11), 'Run program', 'run')
        ui.button((w - 266, h - 35, w - 186, h - 11), 'OK', 'ok', default=True)
        ui.button((w - 178, h - 35, w - 98, h - 11), 'Cancel', 'cancel')
        ui.button((w - 90, h - 35, w - 12, h - 11), 'Apply', 'apply')

    def on_command(self, cid, data):
        s = self.s
        if isinstance(cid, tuple):
            if cid[0] == 'mode':
                s['mode'] = cid[1]
            elif cid[0] == 'font':
                s['font'] = cid[1]
        elif cid in ('enabled', 'text80', 'crt'):
            s[cid] = data
        elif cid in ('th_prev', 'th_next'):
            s['theme'] = (int(s['theme']) + (1 if cid == 'th_next' else -1)) % len(cos24.THEMES)
        elif cid in ('ok', 'apply'):
            self.apply()
            if cid == 'ok':
                self.close()
        elif cid == 'cancel':
            self.close()
        elif cid == 'run':
            self.apply()
            self.close()
            p = self.shell.compat.by_path(self.path) if getattr(self.shell, 'compat', None) else None
            if p is not None and p.key in self.shell.registry:
                self.shell.launch(p.key)
            else:
                self.shell.launch('cosrun', path=self.path)
        self.invalidate()

    def apply(self):
        save_settings(self.path, self.s)
        eff = get_settings(self.path)
        for w in self.shell.windows:
            a = w.app
            if isinstance(a, CosHost) and a.prog is not None and a.prog.path == self.path:
                old80 = a.settings.get('text80')
                a.settings.clear()
                a.settings.update(eff)
                if a.is_main and eff['text80'] != old80:
                    if eff['text80']:
                        a.cw.w, a.cw.h = 82, 26
                    a.resizable = not eff['text80']
                a.apply_settings()

    def on_key(self, ev):
        if ev['key'] == 'escape':
            self.close()
            return True
        if ev['key'] == 'enter':
            self.on_command('ok', None)
            return True
        return False
