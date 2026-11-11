"""The Neptune shell: compositor, window manager, menu bar, taskbar, start
menu, Aero Snap / Peek / Shake / Flip, notifications, boot & shutdown.

Rendering model: whenever anything visible changes, `dirty` is set and the
whole scene is recomposed back-to-front into one RGBA image (every glass
surface blurs whatever was composed beneath it, exactly like DWM). The mouse
cursor is a separate overlay, so plain pointer motion only repaints the two
small rectangles it left and entered.
"""
import itertools
import json
import math
import os
import subprocess
import time
import traceback
import zlib

from PIL import Image, ImageDraw, ImageFilter

from . import gfx, icons, theme, wallpaper
from .theme import TITLE_H, BORDER, RADIUS, MENUBAR_H, TASKBAR_H

ANIM_OPEN = 0.20
ANIM_CLOSE = 0.16
ANIM_MIN = 0.24
ANIM_MAX = 0.16
DOUBLE_CLICK = 0.42


class Window:
    _ids = itertools.count(1)

    def __init__(self, app, x, y, w, h):
        self.id = next(self._ids)
        self.app = app
        self.x, self.y, self.w, self.h = int(x), int(y), int(w), int(h)
        self.state = 'normal'          # normal | max | snapl | snapr
        self.restore_rect = None
        self.minimized = False
        self.anim = None
        self.content = None
        self.content_dirty = True
        self.frame_dirty = True
        self.hover = None
        self.press = None
        self.parent = None
        self.closing = False
        self.flash_until = 0

    @property
    def rect(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    def metrics(self):
        if self.state == 'max':
            return TITLE_H - 6, 0, 0
        return TITLE_H, BORDER, RADIUS

    def client_rect(self, box=None):
        x0, y0, x1, y1 = box or self.rect
        th, b, _r = self.metrics()
        return (x0 + b, y0 + th, x1 - b, y1 - b)

    @property
    def client_size(self):
        c = self.client_rect()
        return (max(1, c[2] - c[0]), max(1, c[3] - c[1]))

    def buttons(self, box=None):
        x0, y0, x1, y1 = box or self.rect
        th, b, _r = self.metrics()
        names = [] if getattr(self.app, 'no_close', False) else (
            ['close'] if self.app.dialog else ['min', 'max', 'close'])
        widths = {'min': 27, 'max': 27, 'close': 45 if not self.app.dialog else 43}
        y = y0 + (0 if self.state == 'max' else 1)
        h = 19 if self.state != 'max' else 18
        x = x1 - b - (2 if self.state == 'max' else 0)
        out = []
        for n in reversed(names):
            out.append((n, (x - widths[n], y, x, y + h)))
            x -= widths[n]
        return list(reversed(out))

    def hit(self, mx, my):
        if not gfx.in_rect(mx, my, self.rect):
            return None
        for n, r in self.buttons():
            if gfx.in_rect(mx, my, r):
                return n
        x0, y0, x1, y1 = self.rect
        if self.state == 'normal' and self.app.resizable and not self.app.dialog:
            e = 6
            c = 16
            left, right = mx < x0 + e, mx >= x1 - e
            top, bottom = my < y0 + 4, my >= y1 - e
            if (left or right or top or bottom) or (mx < x0 + c and my >= y1 - c) or (mx >= x1 - c and my >= y1 - c):
                d = ''
                if my < y0 + (c if (left or right) else 4):
                    d += 'n'
                elif my >= y1 - (c if (left or right) else e):
                    d += 's'
                if mx < x0 + (c if (top or bottom) else e):
                    d += 'w'
                elif mx >= x1 - (c if (top or bottom) else e):
                    d += 'e'
                if d:
                    return 'resize-' + d
        cx0, cy0, cx1, cy1 = self.client_rect()
        if gfx.in_rect(mx, my, (cx0, cy0, cx1, cy1)):
            return 'client'
        if my < cy0:
            return 'title'
        return 'frame'


class Menu:
    """A popup menu. items: dicts with label, action, icon, checked, enabled,
    sub (list of items), sep (True), shortcut.

    A menu always fits on screen: it opens to the left or upward when there is
    no room, splits into columns when it is taller than the screen, and
    scrolls (wheel, the arrow strips, or the keyboard) when even columns
    would be too wide."""
    ITEM_H = 24
    SEP_H = 9
    ARROW_H = 16
    MAX_COLS = 4

    def __init__(self, items, x, y, shell, parent=None, min_w=0, owner=None):
        self.items = items
        self.shell = shell
        self.parent = parent
        self.owner = owner
        self.hover = -1
        self.scroll = 0                 # first item shown, when scrolling
        self.last_scroll = 0.0
        fnt = gfx.font(12)
        cw = min_w
        for it in items:
            if it.get('sep'):
                continue
            tw = gfx.text_width(fnt, it.get('label', '')) + 70
            if it.get('shortcut'):
                tw += gfx.text_width(fnt, it['shortcut']) + 20
            cw = max(cw, tw)
        W, H = shell.W, shell.H
        top, bottom = MENUBAR_H - 1, H - TASKBAR_H
        avail = max(self.ITEM_H * 3, bottom - top - 2)
        cw = min(cw, max(120, W - 8))
        self.colw = cw
        heights = [self.SEP_H if it.get('sep') else self.ITEM_H for it in items]
        total = 6 + sum(heights)
        self.columns = [list(range(len(items)))]
        self.scrolling = False
        if total > avail:
            cols, cur, ch = [], [], 6
            for i, hh in enumerate(heights):
                if cur and ch + hh > avail:
                    while cur and items[cur[-1]].get('sep'):
                        cur.pop()                       # no separator at the bottom of a column
                    cols.append(cur)
                    cur, ch = [], 6
                if not cur and items[i].get('sep'):
                    continue                            # nor at the top of one
                cur.append(i)
                ch += hh
            if cur:
                cols.append(cur)
            if len(cols) <= self.MAX_COLS and len(cols) * cw <= W - 8:
                self.columns = cols
            else:
                self.scrolling = True
        if self.scrolling:
            w, h = cw, avail
        else:
            w = cw * len(self.columns)
            h = max(6 + sum(heights[i] for i in c) for c in self.columns)
        # horizontal: open to the left of the parent (or the point) when it won't fit on the right
        if x + w > W:
            x = (parent.x - w + 4) if parent else (W - w)
            if x < 0:
                x = W - w
        x = max(0, min(x, W - w))
        # vertical: slide up so the bottom stays above the taskbar
        if y + h > bottom:
            y = bottom - h
        y = max(top, y)
        self.x, self.y, self.w, self.h = int(x), int(y), int(w), int(h)

    @property
    def rect(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    # -- scrolling -------------------------------------------------------------------
    def visible_count(self):
        room = self.h - 6 - 2 * self.ARROW_H
        n, used = 0, 0
        for it in self.items[self.scroll:]:
            hh = self.SEP_H if it.get('sep') else self.ITEM_H
            if used + hh > room:
                break
            used += hh
            n += 1
        return max(1, n)

    def max_scroll(self):
        if not self.scrolling:
            return 0
        room = self.h - 6 - 2 * self.ARROW_H
        used, i = 0, len(self.items)
        while i > 0:
            hh = self.SEP_H if self.items[i - 1].get('sep') else self.ITEM_H
            if used + hh > room:
                break
            used += hh
            i -= 1
        return i

    def scroll_by(self, d):
        old = self.scroll
        self.scroll = max(0, min(self.max_scroll(), self.scroll + d))
        return self.scroll != old

    def ensure_visible(self, i):
        if not self.scrolling or i < 0:
            return
        if i < self.scroll:
            self.scroll = i
        while i >= self.scroll + self.visible_count() and self.scroll < self.max_scroll():
            self.scroll += 1

    def arrow_rects(self):
        """(up, down) strips while scrolling, else (None, None)."""
        if not self.scrolling:
            return None, None
        x0, y0, x1, y1 = self.rect
        return (x0 + 2, y0 + 2, x1 - 2, y0 + 2 + self.ARROW_H), (x0 + 2, y1 - 2 - self.ARROW_H, x1 - 2, y1 - 2)

    def arrow_at(self, mx, my):
        up, dn = self.arrow_rects()
        if up and gfx.in_rect(mx, my, up) and self.scroll > 0:
            return -1
        if dn and gfx.in_rect(mx, my, dn) and self.scroll < self.max_scroll():
            return 1
        return 0

    # -- layout --------------------------------------------------------------------------
    def item_rects(self):
        out = []
        if self.scrolling:
            y = self.y + 3 + self.ARROW_H
            for i in range(self.scroll, self.scroll + self.visible_count()):
                if i >= len(self.items):
                    break
                h = self.SEP_H if self.items[i].get('sep') else self.ITEM_H
                out.append((i, (self.x + 3, y, self.x + self.w - 3, y + h)))
                y += h
            return out
        for c, col in enumerate(self.columns):
            x0 = self.x + c * self.colw
            y = self.y + 3
            for i in col:
                h = self.SEP_H if self.items[i].get('sep') else self.ITEM_H
                out.append((i, (x0 + 3, y, x0 + self.colw - 3, y + h)))
                y += h
        return out

    def item_at(self, mx, my):
        if not gfx.in_rect(mx, my, self.rect):
            return -1
        for i, r in self.item_rects():
            if gfx.in_rect(mx, my, r) and not self.items[i].get('sep'):
                return i
        return -1


class Shell:
    MENU_TITLES = ['COS', 'FILE', 'EDIT', 'APPS', 'CONTROL', 'POWER', 'HELP']

    def __init__(self, W, H, settings, backend='window', registry=None, boot=True, oobe=None):
        theme.ensure_dirs()
        gfx.set_aero(settings.get('aero', True))
        self.W, self.H = W, H
        self.settings = settings
        self.backend = backend
        self.registry = registry or {}
        self.windows = []
        self.active = None
        self.dirty = True
        self.mouse = (W // 2, H // 2)
        self.cursor_kind = 'arrow'
        self.drag = None
        self.capture = None            # window whose app has the mouse
        self.menus = []
        self.menubar_open = None       # index of open top menu
        self.menubar_hover = -1
        self.start_open = False
        self.start_hover = None
        self.start_all = False
        self.start_search = {'text': '', 'cur': 0}
        self.start_power_hover = None
        self.task_hover = None
        self.task_hover_t = 0
        self.thumb = None              # window shown in taskbar thumbnail
        self.thumb_hover = False
        self.peek = False
        self.peek_t = 0
        self.peek_window = None
        self.showdesk_hover = False
        self.minimized_all = None
        self.alttab = None
        self.balloons = []
        self.icon_sel = None
        self.icon_hover = None
        self.last_click = (0, 0, 0, None)
        self.clicks = 1
        self.snap_preview = None
        self.shake = []
        self.exit_code = None
        self.phase = 'boot' if (boot and settings.get('boot_screen', True)) else 'desktop'
        self.phase_t = time.time()
        self.desk_layer = None
        self.desk_dirty = True
        self.last_clock = ''
        self.last_second = -1
        self.caret_t = 0
        self.caret_on = True
        self.scene = None
        self.out = None
        self.cursor_box = None
        self.cursor_drawn = None
        self.frame_times = []
        self.gdr_confirm = None        # callable to confirm GDR, or None
        self.sound_proc = None
        self.fade_in_t = None
        self._wall = None
        self._wall_name = None
        self._thumb_cache = {}
        self.display_name = backend
        self.input_info = []
        self.clipboard = ''
        self.shot_request = None          # 'screen' | 'window', taken on the next render
        self.shot_flash = 0
        self.exit_request = 'exit'
        if self.phase == 'desktop':
            self.fade_in_t = time.time()
        self.desktop_icons = [
            ('Computer', 'computer', lambda: self.launch('files', path='/')),
            ('Documents', 'documents', lambda: self.launch('files', path=os.path.join(theme.HOME, 'Documents'))),
            ('Notepad', 'notepad', lambda: self.launch('notepad')),
            ('Calculator', 'calc', lambda: self.launch('calc')),
            ('Terminal', 'terminal', lambda: self.launch('terminal')),
            ('Webinator', 'webinator', lambda: self.launch('webinator')),
            ('CEAP', 'ceap', lambda: self.launch('ceap')),
            ('Recycle Bin', 'recycle', lambda: self.launch('recycle')),
            ('TCFNet', 'tcfnet', lambda: self.launch('tcfnet')),
            ('The COS Place', 'cosplace', lambda: self.launch('cosplace')),
            ('COS Cities', 'cities', lambda: self.launch('cities')),
            ('Personalize', 'personalize', lambda: self.launch('personalize')),
            ('Neptune Help', 'help', lambda: self.launch('help')),
        ]
        self.base_icons = list(self.desktop_icons)
        self.last_input = time.time()
        self.emergency = None                  # the Ctrl+Shift+Alt+Q prompt, when it is up
        from . import fun
        self.pet = fun.Bubbo(self)
        self.confetti = None
        self.konami = []
        self.saver = None
        self.saver_prev = 'desktop'
        self.saver_t0 = 0
        self.saver_frame_t = 0
        self.saver_preview = False
        self.lock_pw = {'text': '', 'cur': 0}
        self.lock_t = 0
        self.lock_msg = ''
        self.lock_shake = 0
        self.lock_hover = None
        self.lock_after = 'desktop'
        from .apps.plugins import PluginHost
        self.plugins = None
        self.compat = None
        self.plugins = PluginHost(self)
        from .compat.host import CompatRegistry
        self.compat = CompatRegistry(self)
        from .apps.ceap import CeapRegistry
        self.ceap = CeapRegistry(self)
        from .drivers import DriverHost
        self.drivers = DriverHost(self, start=backend != 'headless')
        import atexit
        atexit.register(self.drivers.stop_all)
        self.refresh_desktop_icons()
        self.oobe_win = None
        self.welcome_text = None
        if oobe is None:
            oobe = boot and not settings.get('oobe_done', True)
        if oobe:
            settings['oobe_done'] = False
            if self.phase != 'boot':
                self.start_oobe()

    def refresh_desktop_icons(self):
        if self.plugins is None:
            return
        entries = self.plugins.desktop_entries() + (self.compat.desktop_entries() if self.compat else [])
        extra = [(name, icon, (lambda k=key: self.launch(k))) for name, icon, key in entries]
        self.desktop_icons = self.base_icons + extra
        self.icon_sel = None
        self.desk_dirty = True
        self.dirty = True

    # ======================================================================
    # geometry helpers
    # ======================================================================
    @property
    def work_area(self):
        return (0, MENUBAR_H, self.W, self.H - TASKBAR_H)

    def visible_windows(self):
        return [w for w in self.windows if not w.minimized or w.anim]

    def window_at(self, mx, my):
        for w in reversed(self.windows):
            if w.minimized or w.closing:
                continue
            if gfx.in_rect(mx, my, w.rect):
                return w
        return None

    # ======================================================================
    # sound
    # ======================================================================
    def sound(self, name):
        if not self.settings.get('sounds', True):
            return
        self.sound_file(os.path.join(theme.ASSETS, 'sounds', name + '.mp3'))

    SOUND_MAX_LIVE = 4        # ffplay processes allowed at once
    SOUND_REPEAT_GAP = 0.15   # seconds before the same sound may start again

    def sound_file(self, path):
        # Every sound is its own ffplay process, so a burst of them (a CEAP button with
        # 5000 `notify` actions) used to start thousands at once and run the whole
        # computer out of memory. Drop sounds while 4 are playing or when the same one
        # started less than 0.15 s ago.
        if not os.path.isfile(path):
            return
        now = time.time()
        procs = [p for p in getattr(self, '_sound_procs', []) if p.poll() is None]
        self._sound_procs = procs
        last = getattr(self, '_sound_last', {})
        self._sound_last = last
        if len(procs) >= self.SOUND_MAX_LIVE or now - last.get(path, 0) < self.SOUND_REPEAT_GAP:
            return
        try:
            vol = int(gfx.clamp(int(self.settings.get('sound_volume', 100) or 0), 0, 100))
            if vol <= 0:
                return
            last[path] = now
            procs.append(subprocess.Popen(['ffplay', '-nodisp', '-autoexit', '-loglevel', 'quiet', '-volume', str(vol), path],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL, start_new_session=True))
        except OSError:
            pass

    # ======================================================================
    # windows
    # ======================================================================
    def launch(self, name, **kw):
        if name == 'files' and self.settings.get('explorer_style', 'CEAP') == 'CEAP' and 'ceap' in self.registry:
            name = 'ceap'                      # File Explorer, reworked on the CEAP engine
        elif name == 'webinator' and self.settings.get('web_style', 'CEAP') == 'CEAP' and 'ceapweb' in self.registry:
            name = 'ceapweb'                   # the web, in CEAP's Exploring Space
        entry = self.registry.get(name)
        if not entry:
            self.notify('Neptune', 'No program called "%s".' % name, 'warning')
            return None
        cls = entry[0]
        if cls.single_instance:
            for w in self.windows:
                if type(w.app) is cls and not w.closing:
                    if w.minimized:
                        self.restore_window(w)
                    self.activate(w)
                    if kw and hasattr(w.app, 'reopen'):
                        w.app.reopen(**kw)          # e.g. a file opened while the app is already up
                    return w.app
        try:
            app = cls(self, **kw)
        except Exception as e:
            self.crash(entry[1] if len(entry) > 1 else name, e, relaunch=(name, kw))
            return None
        app._launch = (name, kw)
        return self.open_window(app)

    def open_window(self, app, parent=None, center=False):
        w, h = app.size
        wa = self.work_area
        w = min(w, wa[2] - wa[0])
        h = min(h, wa[3] - wa[1])
        if getattr(app, 'position', None) == 'tray':
            x, y = wa[2] - w - 10, wa[3] - h - 10
        elif parent is not None or center or app.dialog:
            ref = parent.rect if parent is not None else wa
            x = (ref[0] + ref[2] - w) // 2
            y = (ref[1] + ref[3] - h) // 2
        else:
            n = len([x for x in self.windows if not x.closing]) % 8
            x = 90 + n * 28 + (wa[2] - wa[0] - w - 180) // 3
            y = wa[1] + 30 + n * 26
        x = gfx.clamp(x, wa[0], max(wa[0], wa[2] - w))
        y = gfx.clamp(y, wa[1], max(wa[1], wa[3] - h))
        win = Window(app, x, y, w, h)
        win.parent = parent
        app.win = win
        self.windows.append(win)
        self.activate(win)
        if self.settings.get('animations', True):
            win.anim = {'kind': 'open', 't0': time.time(), 'dur': ANIM_OPEN}
        self.dirty = True
        return app

    def activate(self, win):
        if win is None:
            if self.active is not None:
                self._focus_changed(self.active, False)
            self.active = None
            self.dirty = True
            return
        # a window with an open modal dialog hands focus to the dialog
        for d in self.windows:
            if d.parent is win and not d.closing and d.app.dialog:
                win = d
                break
        if win in self.windows:
            self.windows.remove(win)
            self.windows.append(win)
            # keep its dialogs above it
            for d in [d for d in self.windows if d.parent is win]:
                self.windows.remove(d)
                self.windows.append(d)
        if self.active is not win:
            if self.active is not None:
                self._focus_changed(self.active, False)
            self.active = win
            self._focus_changed(win, True)
        self.dirty = True

    def _focus_changed(self, win, focused):
        win.frame_dirty = True
        try:
            win.app.on_focus(focused)
        except Exception as e:
            self.crash(win.app.title, e, win=win)

    def close_window(self, win, force=False):
        if win.closing:
            return
        if not force:
            try:
                if not win.app.on_close():
                    return
            except Exception:
                pass
        for d in list(self.windows):
            if d.parent is win:
                self.close_window(d, force=True)
        win.closing = True
        if self.capture is win:
            self.capture = None
        if self.drag and self.drag.get('win') is win:
            self.drag = None
        if self.settings.get('animations', True) and not win.minimized:
            win.anim = {'kind': 'close', 't0': time.time(), 'dur': ANIM_CLOSE}
        else:
            self._remove(win)
        if self.active is win:
            self.active = None
            nxt = [w for w in self.windows if not w.minimized and not w.closing]
            if win.parent is not None and win.parent in nxt:
                self.activate(win.parent)
            elif nxt:
                self.activate(nxt[-1])
        self.dirty = True

    def _remove(self, win):
        if win in self.windows:
            self.windows.remove(win)
        if self.thumb is win:
            self.thumb = None
        gone = getattr(win.app, 'on_destroy', None)       # release processes even after a forced close
        if gone is not None:
            try:
                gone()
            except Exception:
                pass

    def taskbar_button_rect(self, win):
        for w, r in self.taskbar_buttons():
            if w is win:
                return r
        return (self.W // 2 - 40, self.H - TASKBAR_H, self.W // 2 + 40, self.H)

    def minimize(self, win):
        if win.minimized:
            return
        win.minimized = True
        if self.settings.get('animations', True):
            win.anim = {'kind': 'min', 't0': time.time(), 'dur': ANIM_MIN}
        if self.active is win:
            self.active = None
            nxt = [w for w in self.windows if not w.minimized and not w.closing]
            if nxt:
                self.activate(nxt[-1])
        self.dirty = True

    def restore_window(self, win):
        if win.minimized:
            win.minimized = False
            if self.settings.get('animations', True):
                win.anim = {'kind': 'unmin', 't0': time.time(), 'dur': ANIM_MIN}
        self.activate(win)

    def _set_geometry(self, win, rect, state, animate=True):
        old = win.rect
        x0, y0, x1, y1 = rect
        win.x, win.y, win.w, win.h = int(x0), int(y0), int(x1 - x0), int(y1 - y0)
        win.state = state
        win.content_dirty = True
        try:
            win.app.on_resize()
        except Exception:
            pass
        if animate and self.settings.get('animations', True):
            win.anim = {'kind': 'geom', 't0': time.time(), 'dur': ANIM_MAX, 'from': old}
        self.dirty = True

    def resize_window(self, win, w, h):
        """Resize a normal (not snapped) window, keeping it on screen."""
        if win.state != 'normal':
            return
        wa = self.work_area
        win.w = int(min(w, wa[2] - wa[0]))
        win.h = int(min(h, wa[3] - wa[1]))
        win.x = gfx.clamp(win.x, wa[0], max(wa[0], wa[2] - win.w))
        win.y = gfx.clamp(win.y, wa[1], max(wa[1], wa[3] - win.h))
        win.frame_dirty = win.content_dirty = True
        self.safe(win, win.app.on_resize)
        self.dirty = True

    def maximize(self, win, animate=True):
        if win.app.dialog or not win.app.resizable:
            return
        if win.state == 'max':
            self.unmaximize(win)
            return
        if win.state == 'normal':
            win.restore_rect = win.rect
        self._set_geometry(win, self.work_area, 'max', animate)

    def unmaximize(self, win, animate=True):
        r = win.restore_rect or (100, 100, 100 + win.app.size[0], 100 + win.app.size[1])
        self._set_geometry(win, r, 'normal', animate)

    def snap(self, win, side):
        if win.app.dialog or not win.app.resizable:
            return
        wa = self.work_area
        if win.state == 'normal':
            win.restore_rect = win.rect
        if side == 'max':
            self._set_geometry(win, wa, 'max')
        elif side == 'left':
            self._set_geometry(win, (wa[0], wa[1], wa[2] // 2, wa[3]), 'snapl')
        elif side == 'right':
            self._set_geometry(win, (wa[2] // 2, wa[1], wa[2], wa[3]), 'snapr')

    # ======================================================================
    # notifications & crash dialog
    # ======================================================================
    def notify(self, title, text, icon='info', dur=6.0):
        self.balloons.append({'title': title, 'text': text, 'icon': icon, 't0': time.time(), 'dur': dur})
        self.balloons = self.balloons[-4:]
        self.sound('notify')
        self.dirty = True

    def crash(self, appname, exc, win=None, relaunch=None):
        tb = traceback.format_exc()
        try:
            os.makedirs(theme.STATE, exist_ok=True)
            with open(os.path.join(theme.STATE, 'crash.log'), 'a') as f:
                f.write('--- %s %s\n%s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), appname, tb))
        except OSError:
            pass
        if win is not None:
            relaunch = relaunch or getattr(win.app, '_launch', None)
            self.close_window(win, force=True)
        entry = self.registry.get('crash')
        if entry:
            code = zlib.crc32(type(exc).__name__.encode()) & 0xfff
            app = entry[0](self, appname=appname, code=code, message=str(exc) or type(exc).__name__,
                           relaunch=relaunch)
            self.open_window(app, center=True)
            self.sound('warn')

    def safe(self, win, fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            self.crash(win.app.title, e, win=win)
            return None

    # ======================================================================
    # power
    # ======================================================================
    def cosplace_events(self, now):
        """Tell the user, now and then, when new things turn up on The COS Place."""
        from . import cosplace
        if not cosplace.running():
            return
        node = cosplace.get(self.settings)
        fresh = False
        while not node.events.empty():
            try:
                fresh = node.events.get_nowait()[0] == 'crawl' or fresh
            except Exception:
                break
        if not fresh:
            return
        with node.lock:
            seen = set(node.network) - set(node.shelf)
        known = getattr(self, '_cosplace_known', None)
        self._cosplace_known = seen if known is None else known | seen
        new = seen - (known if known is not None else seen)
        if new and now - getattr(self, '_cosplace_said', 0) > 600 and \
                not any(w.app.__class__.__name__ == 'COSPlace' for w in self.windows):
            self._cosplace_said = now
            self.notify('The COS Place', '%d new thing%s on The COS Place.' % (len(new), '' if len(new) == 1 else 's'),
                        'cosplace', 6)

    def shutdown(self, code='exit'):
        if self.phase == 'shutdown':
            return
        from . import cosplace
        if cosplace.running():
            cosplace.get(self.settings).stop()
        self.close_popups()
        self.phase = 'shutdown'
        self.phase_t = time.time()
        self.exit_request = code
        self.sound('shutdown')
        self._shutdown_bg = None
        self.dirty = True

    # ======================================================================
    # popups
    # ======================================================================
    def close_popups(self):
        changed = bool(self.menus or self.start_open or self.menubar_open is not None or self.thumb)
        self.menus = []
        self.menubar_open = None
        self.menubar_hover = -1
        self.start_open = False
        self.start_all = False
        self.start_search = {'text': '', 'cur': 0}
        self.thumb = None
        if changed:
            self.dirty = True

    def popup(self, items, x, y, min_w=0, owner=None):
        self.menus = [Menu(items, x, y, self, min_w=min_w, owner=owner)]
        self.dirty = True

    def toggle_start(self):
        if self.start_open:
            self.close_popups()
        else:
            self.close_popups()
            self.start_open = True
            self.sound_click = True
        self.dirty = True

    # -- menus content -------------------------------------------------------------
    def menubar_items(self, idx):
        name = self.MENU_TITLES[idx]
        L = self.launch
        act = self.active
        if name == 'COS':
            return [
                {'label': 'About COS Neptune', 'icon': 'neptune', 'action': lambda: L('about')},
                {'sep': True},
                {'label': 'Personalize...', 'icon': 'personalize', 'action': lambda: L('personalize')},
                {'label': 'System Information', 'icon': 'computer', 'action': lambda: L('sysinfo')},
                {'label': 'Task Manager', 'icon': 'taskmgr', 'action': lambda: L('taskmgr'), 'shortcut': 'Ctrl+Shift+Esc'},
                {'label': 'App Manager', 'icon': 'plugin', 'action': lambda: L('appmgr')},
                {'label': 'COS v2.4 Mode', 'icon': 'cosmode', 'action': lambda: L('cosmode')},
                {'label': 'Take Screenshot', 'icon': 'pictures', 'shortcut': 'PrtSc',
                 'action': lambda: self.request_screenshot('screen')},
                {'sep': True},
                {'label': 'Lock', 'icon': 'lock', 'action': self.lock, 'shortcut': 'Super+L'},
                {'label': 'Exit Neptune', 'icon': 'power', 'action': lambda: self.shutdown('exit'), 'shortcut': 'Ctrl+Alt+Bksp'},
            ]
        if name == 'FILE':
            return [
                {'label': 'New Text Document', 'icon': 'notepad', 'action': lambda: L('notepad')},
                {'label': 'Open Documents', 'icon': 'documents', 'action': lambda: L('files', path=os.path.join(theme.HOME, 'Documents'))},
                {'label': 'Computer', 'icon': 'computer', 'action': lambda: L('files', path='/')},
                {'sep': True},
                {'label': 'Close Window', 'enabled': act is not None, 'action': lambda: act and self.close_window(act), 'shortcut': 'Alt+F4'},
            ]
        if name == 'EDIT':
            items = []
            if act is not None and hasattr(act.app, 'edit_menu'):
                items = act.app.edit_menu()
            if items:
                items.append({'sep': True})
            items += [
                {'label': 'Minimize All', 'action': self.show_desktop, 'shortcut': 'Super+D'},
                {'label': 'Cascade Windows', 'action': self.cascade},
                {'label': 'Show Windows Side by Side', 'action': self.tile},
            ]
            return items
        if name == 'APPS':
            out = []
            for key, entry in self.program_list():
                out.append({'label': entry[1], 'icon': entry[2], 'action': (lambda k=key: L(k))})
            from .apps.pkgapps import install_dialog
            out += [{'sep': True},
                    {'label': 'Install Package...', 'icon': 'package', 'action': lambda: install_dialog(self)}]
            return out
        if name == 'CONTROL':
            s = self.settings
            return [
                {'label': 'Control Panel', 'icon': 'settings', 'action': lambda: L('control')},
                {'sep': True},
                {'label': 'Personalize...', 'icon': 'personalize', 'action': lambda: L('personalize')},
                {'label': 'Run Setup Again...', 'icon': 'neptune', 'action': self.start_oobe},
                {'sep': True},
                {'label': 'Aero Glass', 'checked': s.get('aero', True), 'action': lambda: self.toggle_setting('aero')},
                {'label': 'Transparency', 'checked': s['transparency'], 'enabled': s.get('aero', True),
                 'action': lambda: self.toggle_setting('transparency')},
                {'label': 'Desktop Background...', 'icon': 'pictures', 'action': lambda: L('background')},
                {'label': 'Animations', 'checked': s['animations'], 'action': lambda: self.toggle_setting('animations')},
                {'label': 'Sounds', 'checked': s['sounds'], 'action': lambda: self.toggle_setting('sounds')},
                {'label': 'Clock Gadget', 'checked': s['show_gadget'], 'action': lambda: self.toggle_setting('show_gadget')},
                {'label': '24-hour Clock', 'checked': s['clock_24h'], 'action': lambda: self.toggle_setting('clock_24h')},
                {'label': 'Show Build Number on Desktop', 'checked': s['show_build'], 'action': lambda: self.toggle_setting('show_build')},
                {'sep': True},
                {'label': 'Lock Screen...', 'icon': 'lock', 'action': lambda: L('lockset')},
                {'label': 'Screen Saver...', 'icon': 'saver', 'action': lambda: L('saverset')},
                {'sep': True},
                {'label': 'System Information', 'icon': 'computer', 'action': lambda: L('sysinfo')},
                {'label': 'Task Manager', 'icon': 'taskmgr', 'action': lambda: L('taskmgr')},
                {'label': 'App Manager', 'icon': 'plugin', 'action': lambda: L('appmgr')},
                {'label': 'Programs and Features', 'icon': 'package', 'action': lambda: L('programs')},
                {'label': 'Device Manager', 'icon': 'devmgr', 'action': lambda: L('devmgr')},
            ]
        if name == 'POWER':
            return [
                {'label': 'Lock', 'icon': 'lock', 'action': self.lock, 'shortcut': 'Super+L'},
                {'sep': True},
                {'label': 'Restart Neptune', 'action': lambda: self.shutdown('restart')},
                {'label': 'Exit to Console', 'action': lambda: self.shutdown('exit')},
                {'sep': True},
                {'label': 'Shut Down Computer...', 'icon': 'power', 'action': lambda: L('powerdlg', mode='poweroff')},
                {'label': 'Restart Computer...', 'action': lambda: L('powerdlg', mode='reboot')},
            ]
        if name == 'HELP':
            return [
                {'label': 'Neptune Help', 'icon': 'help', 'action': lambda: L('help'), 'shortcut': 'F1'},
                {'label': 'Keyboard Shortcuts', 'action': lambda: L('help', page='keys')},
                {'sep': True},
                {'label': 'About COS Neptune', 'icon': 'neptune', 'action': lambda: L('about')},
            ]
        return []

    # -- Print Screen -------------------------------------------------------------------
    SHOT_DIR = os.path.join(theme.HOME, 'Pictures', 'Screenshots')

    def request_screenshot(self, what='screen'):
        """PrtSc = whole screen, Alt+PrtSc = the active window. Captured from the next
        composed frame (so an open menu has closed), without the mouse pointer."""
        if what == 'window' and (self.active is None or self.active.minimized):
            what = 'screen'
        self.close_popups()
        self.shot_request = what
        self.dirty = True

    def take_screenshot(self, what):
        img = self.scene
        label = 'Screen'
        if what == 'window' and self.active is not None:
            x0, y0, x1, y1 = self.active.rect
            box = (max(0, x0), max(0, y0), min(self.W, x1), min(self.H, y1))
            if box[2] > box[0] and box[3] > box[1]:
                img = img.crop(box)
                label = self.active.app.title
        os.makedirs(self.SHOT_DIR, exist_ok=True)
        name = time.strftime('Screenshot %Y-%m-%d %H-%M-%S')
        path = os.path.join(self.SHOT_DIR, name + '.png')
        i = 2
        while os.path.exists(path):
            path = os.path.join(self.SHOT_DIR, '%s (%d).png' % (name, i))
            i += 1
        try:
            img.convert('RGB').save(path)
        except OSError as e:
            self.notify('Print Screen', 'Could not save the screenshot: %s' % (e.strerror or e), 'warning', 5)
            return None
        self.last_screenshot = path
        self.shot_flash = time.time()
        self.notify('Screenshot saved', '%s \u2192 Pictures/Screenshots/%s' % (label, os.path.basename(path)),
                    'pictures', 5)
        return path

    def sync_look(self):
        """Push look settings that live outside the shell (the Aero switch)."""
        gfx.set_aero(self.settings.get('aero', True))

    def toggle_setting(self, key):
        self.settings[key] = not self.settings[key]
        self.settings.save()
        self.sync_look()
        self.desk_dirty = True
        for w in self.windows:
            w.content_dirty = True
        self.dirty = True

    def apply_settings(self):
        self.sync_look()
        self.desk_dirty = True
        for w in self.windows:
            w.content_dirty = True
        self.dirty = True

    def show_desktop(self):
        live = [w for w in self.windows if not w.closing]
        if self.minimized_all:
            for w in self.minimized_all:
                if w in self.windows and w.minimized:
                    self.restore_window(w)
            self.minimized_all = None
        else:
            self.minimized_all = [w for w in live if not w.minimized]
            for w in self.minimized_all:
                self.minimize(w)
        self.dirty = True

    def cascade(self):
        wa = self.work_area
        for i, w in enumerate([w for w in self.windows if not w.minimized and not w.closing]):
            if w.state != 'normal':
                w.state = 'normal'
            ww, hh = w.app.size
            self._set_geometry(w, (wa[0] + 40 + i * 30, wa[1] + 20 + i * 30,
                                   wa[0] + 40 + i * 30 + ww, wa[1] + 20 + i * 30 + hh), 'normal')

    def tile(self):
        ws = [w for w in self.windows if not w.minimized and not w.closing and not w.app.dialog]
        if not ws:
            return
        wa = self.work_area
        n = len(ws)
        cw = (wa[2] - wa[0]) // n
        for i, w in enumerate(ws):
            self._set_geometry(w, (wa[0] + i * cw, wa[1], wa[0] + (i + 1) * cw, wa[3]), 'normal')

    # ======================================================================
    # input
    # ======================================================================
    def handle(self, ev):
        t = ev['type']
        if t == 'quit':
            self.shutdown('exit')
            return
        if self.gdr_confirm and t in ('key', 'down'):
            self.gdr_confirm()
            self.gdr_confirm = None
            self.notify('Graphics Desktop Rendering', 'Display confirmed. Neptune owns the screen.', 'info', 4)
        if t == 'key' and ev.get('ctrl') and ev.get('shift') and ev.get('alt') and ev.get('key') == 'q' \
                and self.settings.get('emergency_exit', True) and self.phase != 'shutdown':
            if self.phase == 'saver':
                self.stop_saver()
            self.emergency = {'t0': time.time(), 'hot': None}
            self.sound('warn')
            self.dirty = True
            return
        if self.emergency is not None:
            self.emergency_event(ev)
            return
        if t == 'wheel' and self.settings.get('natural_scroll'):
            ev = dict(ev, dy=-ev.get('dy', 0))
        if t in ('key', 'down', 'up', 'wheel', 'move'):
            if self.phase == 'saver':
                self.saver_event(ev)
                return
            self.last_input = time.time()
        if self.phase in ('boot', 'welcome'):
            if t in ('key', 'down'):
                self.enter_desktop()
            return
        if self.phase == 'lock':
            self.lock_event(ev)
            return
        if self.phase == 'oobe':
            self.oobe_event(ev)
            return
        if self.phase != 'desktop':
            return
        if t == 'down' and ev.get('button', 1) == 1 and self.settings.get('desktop_pet', True) and \
                gfx.in_rect(ev['x'], ev['y'], self.pet.rect()) and not self.menus and not self.start_open:
            self.pet.click()
            self.dirty = True
            return
        if t == 'key':
            from . import fun
            self.konami = (self.konami + [ev.get('key')])[-len(fun.KONAMI):]
            if self.konami == fun.KONAMI:
                self.konami = []
                self.confetti = fun.Confetti(self.W, self.H)
                self.pet.party()
                self.sound('boot')
                self.notify('Konami code', 'You found the secret. Bubbo is partying.', 'neptune', 6)
        if t == 'move':
            self.on_move(ev['x'], ev['y'])
        elif t == 'down':
            self.on_down(ev['x'], ev['y'], ev.get('button', 1))
        elif t == 'up':
            self.on_up(ev['x'], ev['y'], ev.get('button', 1))
        elif t == 'wheel':
            self.on_wheel(ev['dy'])
        elif t == 'key':
            self.on_key(ev)
        elif t == 'keyup':
            if ev['key'] == 'alt' and self.alttab is not None:
                self.finish_alttab()

    # -- pointer ------------------------------------------------------------------
    def set_cursor(self, kind):
        if kind != self.cursor_kind:
            self.cursor_kind = kind

    def on_move(self, mx, my):
        self.mouse = (mx, my)
        now = time.time()
        if self.drag:
            self.do_drag(mx, my, now)
            return
        if self.capture is not None:
            w = self.capture
            cx0, cy0, _, _ = w.client_rect()
            self.safe(w, w.app.mouse, 'move', mx - cx0, my - cy0)
            return
        cursor = 'arrow'
        # menus
        if self.menus:
            for i in range(len(self.menus) - 1, -1, -1):
                m = self.menus[i]
                if not gfx.in_rect(mx, my, m.rect):
                    continue
                if m.arrow_at(mx, my):
                    self.dirty = True           # the tick scrolls while the pointer rests here
                    break
                idx = m.item_at(mx, my)
                if idx != m.hover:
                    m.hover = idx
                    self.menus = self.menus[:i + 1]
                    it = m.items[idx] if idx >= 0 else None
                    if it and it.get('sub') and it.get('enabled', True):
                        r = dict(m.item_rects())[idx]
                        self.menus.append(Menu(it['sub'], m.x + m.w - 4, r[1] - 3, self, parent=m))
                    self.dirty = True
                break
            if any(gfx.in_rect(mx, my, m.rect) for m in self.menus):
                self.set_cursor('arrow')
                return
        # menubar hover (switches open menus too)
        mb = self.menubar_hit(mx, my)
        if mb != self.menubar_hover:
            self.menubar_hover = mb
            self.dirty = True
            if self.menubar_open is not None and mb >= 0 and mb != self.menubar_open:
                self.open_menubar(mb)
        if self.start_open:
            h = self.start_hit(mx, my)
            if h != self.start_hover:
                self.start_hover = h
                self.dirty = True
            if gfx.in_rect(mx, my, self.start_rect()):
                self.set_cursor('ibeam' if h == ('search',) else 'arrow')
                return
        # taskbar
        tb = self.taskbar_hit(mx, my)
        if tb != self.task_hover:
            self.task_hover = tb
            self.task_hover_t = now
            self.dirty = True
        sd = tb == ('showdesk',)
        if sd != self.showdesk_hover:
            self.showdesk_hover = sd
            self.peek_t = now
            if not sd and self.peek:
                self.peek = False
            self.dirty = True
        # thumbnail
        if self.thumb is not None:
            r = self.thumb_rect()
            inside = r and gfx.in_rect(mx, my, r)
            if inside != self.thumb_hover:
                self.thumb_hover = inside
                self.peek_window = self.thumb if inside else None
                self.dirty = True
            if not inside and not (tb and tb[0] == 'win'):
                self.thumb = None
                self.peek_window = None
                self.dirty = True
        # windows
        w = self.window_at(mx, my) if not (tb or my < MENUBAR_H) else None
        for other in self.windows:
            if other is not w and other.hover is not None:
                other.hover = None
                other.frame_dirty = True
                self.dirty = True
        if w is not None:
            h = w.hit(mx, my)
            hb = h if h in ('min', 'max', 'close') else None
            if hb != w.hover:
                w.hover = hb
                self.dirty = True
            if h == 'client':
                cx0, cy0, _, _ = w.client_rect()
                self.safe(w, w.app.mouse, 'move', mx - cx0, my - cy0)
                cursor = self.safe(w, w.app.cursor_at, mx - cx0, my - cy0) or 'arrow'
            elif h and h.startswith('resize-'):
                d = h[7:]
                cursor = {'n': 'size_ns', 's': 'size_ns', 'e': 'size_we', 'w': 'size_we',
                          'nw': 'size_nwse', 'se': 'size_nwse', 'ne': 'size_nesw', 'sw': 'size_nesw'}[d]
        for other in self.windows:
            if other is not w and other.app.ui.mouse != (-1, -1):
                other.app.mouse('leave', -1, -1)
        # desktop icons
        ih = None
        if w is None and not tb and my >= MENUBAR_H:
            ih = self.icon_at(mx, my)
        if ih != self.icon_hover:
            self.icon_hover = ih
            self.desk_dirty = True
            self.dirty = True
        self.set_cursor(cursor)

    def _click_count(self, mx, my, target):
        now = time.time()
        lt, lx, ly, ltarget = self.last_click
        if now - lt < DOUBLE_CLICK and abs(mx - lx) < 5 and abs(my - ly) < 5 and ltarget == target:
            self.clicks += 1
        else:
            self.clicks = 1
        self.last_click = (now, mx, my, target)
        return self.clicks

    def on_down(self, mx, my, button):
        self.mouse = (mx, my)
        # alt-tab: click a tile
        if self.alttab is not None:
            self.finish_alttab()
            return
        # popup menus
        if self.menus:
            for m in reversed(self.menus):
                a = m.arrow_at(mx, my)
                if a:
                    if m.scroll_by(a * 3):
                        self.menus = self.menus[:self.menus.index(m) + 1]
                    self.dirty = True
                    return
                idx = m.item_at(mx, my)
                if idx >= 0:
                    it = m.items[idx]
                    if it.get('sub') or not it.get('enabled', True):
                        return
                    self.close_popups()
                    act = it.get('action')
                    if act:
                        try:
                            act()
                        except Exception as e:
                            self.crash('Neptune Shell', e)
                    return
                if gfx.in_rect(mx, my, m.rect):
                    return
            was_bar = self.menubar_open
            self.menus = []
            self.menubar_open = None
            self.dirty = True
            mb = self.menubar_hit(mx, my)
            if mb >= 0 and mb == was_bar:
                return
        # start menu
        if self.start_open:
            if gfx.in_rect(mx, my, self.start_rect()):
                self.start_click(mx, my, button)
                return
            if self.taskbar_hit(mx, my) == ('start',):
                self.close_popups()
                return
            self.close_popups()
        # thumbnail
        if self.thumb is not None:
            r = self.thumb_rect()
            if r and gfx.in_rect(mx, my, r):
                w = self.thumb
                self.thumb = None
                self.peek_window = None
                if w.minimized:
                    self.restore_window(w)
                else:
                    self.activate(w)
                return
            self.thumb = None
            self.peek_window = None
            self.dirty = True
        # menubar
        mb = self.menubar_hit(mx, my)
        if mb >= 0:
            self.open_menubar(mb)
            return
        if my < MENUBAR_H:
            return
        # taskbar
        tb = self.taskbar_hit(mx, my)
        if tb:
            self.taskbar_click(tb, mx, my, button)
            return
        if my >= self.H - TASKBAR_H:
            if button == 3:
                self.popup([{'label': 'Cascade Windows', 'action': self.cascade},
                            {'label': 'Show Windows Side by Side', 'action': self.tile},
                            {'label': 'Show the Desktop', 'action': self.show_desktop},
                            {'sep': True},
                            {'label': 'Start Task Manager', 'action': lambda: self.launch('taskmgr')},
                            {'label': 'Personalize...', 'action': lambda: self.launch('personalize')}],
                           mx, my - 150)
            return
        # windows
        w = self.window_at(mx, my)
        if w is not None:
            self.window_down(w, mx, my, button)
            return
        # desktop
        self.activate(None)
        ic = self.icon_at(mx, my)
        clicks = self._click_count(mx, my, ('icon', ic))
        if ic is not None:
            self.icon_sel = ic
            self.desk_dirty = True
            self.dirty = True
            if button == 1 and clicks >= 2:
                self.desktop_icons[ic][2]()
            elif button == 3:
                self.popup([{'label': 'Open', 'action': self.desktop_icons[ic][2]}], mx, my)
            return
        if self.icon_sel is not None:
            self.icon_sel = None
            self.desk_dirty = True
            self.dirty = True
        if button == 3:
            self.popup(self.desktop_menu(), mx, my)

    def desktop_menu(self):
        s = self.settings
        return [
            {'label': 'View', 'sub': [
                {'label': 'Clock Gadget', 'checked': s['show_gadget'], 'action': lambda: self.toggle_setting('show_gadget')},
                {'label': 'Transparency', 'checked': s['transparency'], 'action': lambda: self.toggle_setting('transparency')}]},
            {'label': 'Refresh', 'action': self.refresh_desktop},
            {'sep': True},
            {'label': 'New', 'sub': [
                {'label': 'Text Document', 'icon': 'notepad', 'action': lambda: self.launch('notepad')},
                {'label': 'Terminal', 'icon': 'terminal', 'action': lambda: self.launch('terminal')}]},
            {'sep': True},
            {'label': 'Gadgets', 'icon': 'clock', 'action': lambda: self.toggle_setting('show_gadget')},
            {'label': 'Desktop Background', 'icon': 'pictures', 'action': lambda: self.launch('background')},
            {'label': 'Personalize', 'icon': 'personalize', 'action': lambda: self.launch('personalize')},
        ]

    def refresh_desktop(self):
        self.desk_dirty = True
        self._wall = None
        self.dirty = True

    def window_down(self, w, mx, my, button):
        h = w.hit(mx, my)
        self.activate(w)
        top = self.active
        if top is not w:            # a modal dialog took focus instead
            top.flash_until = time.time() + 0.6
            self.sound('warn')
            return
        clicks = self._click_count(mx, my, ('win', w.id, h))
        if h in ('min', 'max', 'close'):
            w.press = h
            self.drag = {'kind': 'button', 'win': w, 'btn': h}
            self.dirty = True
            return
        if h == 'title':
            if button == 3:
                self.popup(self.window_menu(w), mx, my)
                return
            if clicks >= 2:
                self.maximize(w)
                return
            self.drag = {'kind': 'move', 'win': w, 'ox': mx - w.x, 'oy': my - w.y, 'sx': mx, 'sy': my,
                         'moved': False}
            self.shake = [(time.time(), mx)]
            return
        if h and h.startswith('resize-'):
            self.drag = {'kind': 'resize', 'win': w, 'dir': h[7:], 'start': w.rect, 'sx': mx, 'sy': my}
            return
        if h == 'client':
            cx0, cy0, _, _ = w.client_rect()
            self.capture = w
            self.safe(w, w.app.mouse, 'down', mx - cx0, my - cy0, button, clicks)
            if button == 3:
                items = self.safe(w, w.app.context_menu, mx - cx0, my - cy0)
                if items:
                    self.capture = None
                    w.app.ui.down = None
                    self.popup(items, mx, my)
            return

    def window_menu(self, w):
        return [
            {'label': 'Restore', 'enabled': w.state != 'normal', 'action': lambda: self.unmaximize(w)},
            {'label': 'Minimize', 'enabled': not w.app.dialog, 'action': lambda: self.minimize(w)},
            {'label': 'Maximize', 'enabled': w.state != 'max' and w.app.resizable and not w.app.dialog,
             'action': lambda: self.maximize(w)},
            {'sep': True},
            {'label': 'Close', 'shortcut': 'Alt+F4', 'action': lambda: self.close_window(w)},
        ]

    def do_drag(self, mx, my, now):
        d = self.drag
        w = d.get('win')
        k = d['kind']
        if k == 'button':
            h = w.hit(mx, my)
            p = d['btn'] if h == d['btn'] else None
            if p != w.press:
                w.press = p
                self.dirty = True
            return
        if k == 'move':
            if not d['moved'] and abs(mx - d['sx']) + abs(my - d['sy']) < 4:
                return
            d['moved'] = True
            if w.state != 'normal':
                # tear a maximized / snapped window off, keeping the grab point proportional
                rr = w.restore_rect or (0, 0) + w.app.size
                rw, rh = rr[2] - rr[0], rr[3] - rr[1]
                frac = (mx - w.x) / max(1, w.w)
                w.state = 'normal'
                w.w, w.h = rw, rh
                d['ox'] = int(frac * rw)
                d['oy'] = min(d['oy'], TITLE_H // 2)
                w.content_dirty = True
                self.safe(w, w.app.on_resize)
            w.x = mx - d['ox']
            w.y = max(MENUBAR_H - 4, my - d['oy'])
            # aero snap preview
            wa = self.work_area
            prev = None
            if not w.app.dialog and w.app.resizable:
                if my <= MENUBAR_H + 1:
                    prev = wa
                elif mx <= 1:
                    prev = (wa[0], wa[1], wa[2] // 2, wa[3])
                elif mx >= self.W - 2:
                    prev = (wa[2] // 2, wa[1], wa[2], wa[3])
            if prev != self.snap_preview:
                self.snap_preview = prev
                self.snap_preview_t = now
            # aero shake
            self.shake.append((now, mx))
            self.shake = [s for s in self.shake if now - s[0] < 1.0]
            if self._shaken():
                self.shake = []
                others = [o for o in self.windows if o is not w and not o.closing]
                if any(not o.minimized for o in others):
                    for o in others:
                        if not o.minimized:
                            self.minimize(o)
                else:
                    for o in others:
                        self.restore_window(o)
                    self.activate(w)
            self.dirty = True
            return
        if k == 'resize':
            x0, y0, x1, y1 = d['start']
            dx, dy = mx - d['sx'], my - d['sy']
            mw, mh = w.app.min_size
            mw += 2 * BORDER
            mh += TITLE_H + BORDER
            dirn = d['dir']
            if 'e' in dirn:
                x1 = max(x0 + mw, x1 + dx)
            if 'w' in dirn:
                x0 = min(x1 - mw, x0 + dx)
            if 's' in dirn:
                y1 = max(y0 + mh, y1 + dy)
            if 'n' in dirn:
                y0 = max(MENUBAR_H, min(y1 - mh, y0 + dy))
            if (x0, y0, x1, y1) != w.rect:
                w.x, w.y, w.w, w.h = x0, y0, x1 - x0, y1 - y0
                w.content_dirty = True
                self.safe(w, w.app.on_resize)
                self.dirty = True

    def _shaken(self):
        xs = [x for _t, x in self.shake]
        if len(xs) < 6:
            return False
        turns, direction, anchor = 0, 0, xs[0]
        for x in xs[1:]:
            dx = x - anchor
            if abs(dx) >= 30:
                nd = 1 if dx > 0 else -1
                if direction and nd != direction:
                    turns += 1
                direction = nd
                anchor = x
        return turns >= 4

    def on_up(self, mx, my, button):
        d = self.drag
        if d:
            self.drag = None
            w = d.get('win')
            if d['kind'] == 'button':
                w.press = None
                if w.hit(mx, my) == d['btn']:
                    b = d['btn']
                    if b == 'close':
                        self.close_window(w)
                    elif b == 'min':
                        self.minimize(w)
                    elif b == 'max':
                        self.maximize(w)
                self.dirty = True
            elif d['kind'] == 'move':
                if self.snap_preview is not None:
                    wa = self.work_area
                    sp = self.snap_preview
                    self.snap_preview = None
                    w.restore_rect = (w.x, w.y, w.x + w.w, w.y + w.h)
                    side = 'max' if sp == wa else ('left' if sp[0] == wa[0] else 'right')
                    self.snap(w, side)
                else:
                    # keep the title bar reachable
                    w.y = gfx.clamp(w.y, MENUBAR_H, self.H - TASKBAR_H - 30)
                    w.x = gfx.clamp(w.x, 60 - w.w, self.W - 60)
                self.dirty = True
            return
        if self.capture is not None:
            w = self.capture
            self.capture = None
            cx0, cy0, _, _ = w.client_rect()
            self.safe(w, w.app.mouse, 'up', mx - cx0, my - cy0, button)

    def on_wheel(self, dy):
        mx, my = self.mouse
        for m in reversed(self.menus):
            if gfx.in_rect(mx, my, m.rect):
                if m.scrolling and m.scroll_by(-dy * 2):
                    self.menus = self.menus[:self.menus.index(m) + 1]
                    m.hover = -1
                    self.dirty = True
                return
        if self.start_open and gfx.in_rect(mx, my, self.start_rect()):
            return
        w = self.window_at(mx, my)
        if w is not None:
            cx0, cy0, _, _ = w.client_rect()
            self.safe(w, w.app.on_wheel, dy, mx - cx0, my - cy0)
            w.content_dirty = True
            self.dirty = True

    # -- keyboard -------------------------------------------------------------------
    def on_key(self, ev):
        k = ev['key']
        if k == 'printscreen':
            self.request_screenshot('window' if ev.get('alt') else 'screen')
            return
        if ev.get('ctrl') and ev.get('alt') and k == 'backspace':
            self.shutdown('exit')
            return
        if (ev.get('ctrl') and ev.get('shift') and k == 'escape') or (ev.get('ctrl') and ev.get('alt') and k == 'delete'):
            self.close_popups()
            self.launch('taskmgr')
            return
        if ev.get('super') and k == 'l':
            self.lock()
            return
        if k == 'super' or (ev.get('ctrl') and k == 'escape'):
            self.toggle_start()
            return
        if ev.get('alt') and k == 'tab':
            self.step_alttab(-1 if ev.get('shift') else 1)
            return
        if self.alttab is not None and k == 'escape':
            self.alttab = None
            self.dirty = True
            return
        if ev.get('alt') and k == 'f4':
            if self.active is not None:
                self.close_window(self.active)
            else:
                self.launch('powerdlg', mode='menu')
            return
        if ev.get('super'):
            if k == 'd':
                self.show_desktop()
            elif k == 'up' and self.active:
                self.snap(self.active, 'max')
            elif k == 'down' and self.active:
                if self.active.state != 'normal':
                    self.unmaximize(self.active)
                else:
                    self.minimize(self.active)
            elif k == 'left' and self.active:
                self.snap(self.active, 'left')
            elif k == 'right' and self.active:
                self.snap(self.active, 'right')
            elif k == 'e':
                self.launch('files', path=theme.HOME)
            elif k == 'r':
                self.launch('run')
            elif k in ('home',) and self.active:
                self.shake = []
            return
        if k == 'f1' and not ev.get('ctrl'):
            self.launch('help')
            return
        if self.menus:
            if k == 'escape':
                self.close_popups()
                return
            m = self.menus[-1]
            if k in ('down', 'up'):
                n = len(m.items)
                i = m.hover
                for _ in range(n):
                    i = (i + (1 if k == 'down' else -1)) % n
                    if not m.items[i].get('sep'):
                        break
                m.hover = i
                m.ensure_visible(i)
                self.dirty = True
                return
            if k == 'enter' and 0 <= m.hover < len(m.items):
                it = m.items[m.hover]
                if it.get('action') and it.get('enabled', True):
                    self.close_popups()
                    it['action']()
                return
            if k in ('left', 'right') and self.menubar_open is not None and len(self.menus) == 1:
                self.open_menubar((self.menubar_open + (1 if k == 'right' else -1)) % len(self.MENU_TITLES))
                return
            return
        if self.start_open:
            self.start_key(ev)
            return
        if k == 'f10' or (k == 'alt' and False):
            self.open_menubar(0)
            return
        w = self.active
        if w is not None and not w.minimized:
            r = self.safe(w, w.app.key, ev)
            w.content_dirty = True
            self.dirty = True

    def step_alttab(self, step):
        order = [w for w in reversed(self.windows) if not w.closing and w.app.show_in_taskbar]
        if not order:
            return
        if self.alttab is None:
            self.alttab = {'order': order, 'i': 1 % len(order) if step > 0 else len(order) - 1}
        else:
            self.alttab['order'] = order if len(order) != len(self.alttab['order']) else self.alttab['order']
            self.alttab['i'] = (self.alttab['i'] + step) % len(self.alttab['order'])
        self.dirty = True

    def finish_alttab(self):
        a = self.alttab
        self.alttab = None
        if a and a['order']:
            w = a['order'][a['i'] % len(a['order'])]
            if w.minimized:
                self.restore_window(w)
            else:
                self.activate(w)
        self.dirty = True

    # ======================================================================
    # menubar
    # ======================================================================
    def menubar_layout(self):
        fnt = gfx.font(17, False, 'serif')
        gap = 18
        widths = [gfx.text_width(fnt, t) for t in self.MENU_TITLES]
        total = sum(widths) + gap * (len(widths) - 1)
        x = (self.W - total) // 2
        out = []
        for t, wd in zip(self.MENU_TITLES, widths):
            out.append((t, (x - 9, 4, x + wd + 9, MENUBAR_H - 4)))
            x += wd + gap
        return out

    def menubar_hit(self, mx, my):
        if my >= MENUBAR_H:
            return -1
        for i, (_t, r) in enumerate(self.menubar_layout()):
            if gfx.in_rect(mx, my, r):
                return i
        return -1

    def open_menubar(self, idx):
        self.start_open = False
        r = self.menubar_layout()[idx][1]
        self.menus = [Menu(self.menubar_items(idx), r[0], MENUBAR_H - 1, self, min_w=180)]
        self.menubar_open = idx
        self.dirty = True

    # ======================================================================
    # taskbar
    # ======================================================================
    def taskbar_buttons(self):
        wins = [w for w in self.windows if w.app.show_in_taskbar and not (w.closing and not w.anim)]
        wins = [w for w in wins if not w.closing]
        x = 62
        right = self.W - 200
        n = len(wins)
        if not n:
            return []
        bw = max(44, min(180, (right - x) // n))
        y0 = self.H - TASKBAR_H + 3
        return [(w, (x + i * (bw + 2), y0, x + i * (bw + 2) + bw, self.H - 3)) for i, w in enumerate(wins)]

    def taskbar_hit(self, mx, my):
        if my < self.H - TASKBAR_H:
            return None
        if mx < 56:
            return ('start',)
        if mx >= self.W - 16:
            return ('showdesk',)
        for w, r in self.taskbar_buttons():
            if gfx.in_rect(mx, my, r):
                return ('win', w)
        if mx >= self.W - 100:
            return ('clock',)
        return None

    def taskbar_click(self, tb, mx, my, button):
        if tb == ('start',):
            self.toggle_start()
        elif tb == ('showdesk',):
            self.peek = False
            self.show_desktop()
        elif tb == ('clock',):
            self.launch('calendar')
        elif tb[0] == 'win':
            w = tb[1]
            self.thumb = None
            if button == 3:
                self.popup([{'label': w.app.title, 'icon': w.app.icon, 'enabled': False}, {'sep': True}] +
                           self.window_menu(w), mx, self.H - TASKBAR_H - 160)
                return
            if w.minimized:
                self.restore_window(w)
            elif w is self.active:
                self.minimize(w)
            else:
                self.activate(w)
        self.dirty = True

    def thumb_rect(self):
        w = self.thumb
        if w is None:
            return None
        r = self.taskbar_button_rect(w)
        tw, th = 200, 150
        x = gfx.clamp((r[0] + r[2]) // 2 - tw // 2, 4, self.W - tw - 4)
        return (x, self.H - TASKBAR_H - th - 6, x + tw, self.H - TASKBAR_H - 6)

    # ======================================================================
    # start menu
    # ======================================================================
    START_W = 410

    def start_rect(self):
        h = min(540, self.H - TASKBAR_H - MENUBAR_H - 6)
        return (0, self.H - TASKBAR_H - h, self.START_W, self.H - TASKBAR_H)

    # which copy wins when two programs share a name: built-in first, then
    # Neptune apps, programs installed in Neptune, CEAP apps, and last the
    # COS v2.4 programs run in place from ~/.cos/apps
    SOURCE_RANK = (('cos:cos24:', 4), ('ceapapp:', 3), ('cos:neptune:', 2), ('app:', 1))

    def program_list(self):
        """[(key, entry)] for the APPS menu and Start: no hidden ones, and one
        entry per name (the other copies are still installed and can be
        opened from their files)."""
        def rank(key):
            return next((r for pre, r in self.SOURCE_RANK if key.startswith(pre)), 0)
        best = {}
        for key, e in self.registry.items():
            if len(e) > 3 and e[3] == 'hidden':
                continue
            name = str(e[1]).strip().lower()
            if name not in best or rank(key) < rank(best[name][0]):
                best[name] = (key, e)
        return sorted(best.values(), key=lambda kv: str(kv[1][1]).lower())

    def start_programs(self):
        q = self.start_search['text'].strip().lower()
        pinned = ['files', 'webinator', 'tcfnet', 'notepad', 'druman', 'calc', 'terminal', 'paint', 'pigeon', 'taskmgr', 'help']
        items = []
        if q:
            for key, e in self.program_list():
                if q in e[1].lower() or q in key:
                    items.append((key, e[1], e[2]))
            return items
        keys = pinned if not self.start_all else [k for k, _e in self.program_list()]
        for key in keys:
            e = self.registry.get(key)
            if e:
                items.append((key, e[1], e[2]))
        return items

    def start_layout(self):
        x0, y0, x1, y1 = self.start_rect()
        left = (x0 + 8, y0 + 8, x0 + 258, y1 - 48)
        out = {'left': left, 'items': [], 'links': []}
        progs = self.start_programs()
        small = self.start_all and not self.start_search['text']
        ih = 26 if small else 40
        y = left[1] + 6
        for key, label, icon in progs:
            if y + ih > left[3] - 34:
                break
            out['items'].append((key, label, icon, (left[0] + 4, y, left[2] - 4, y + ih), small))
            y += ih
        out['all'] = (left[0] + 4, left[3] - 30, left[2] - 4, left[3] - 6)
        out['search'] = (x0 + 14, y1 - 38, x0 + 252, y1 - 14)
        links = [('Documents', lambda: self.launch('files', path=os.path.join(theme.HOME, 'Documents'))),
                 ('Pictures', lambda: self.launch('files', path=os.path.join(theme.HOME, 'Pictures'))),
                 ('Computer', lambda: self.launch('files', path='/')),
                 ('Neptune Home', lambda: self.launch('files', path=theme.HOME)),
                 (None, None),
                 ('Control Panel', lambda: self.launch('control')),
                 ('System Information', lambda: self.launch('sysinfo')),
                 ('Run...', lambda: self.launch('run')),
                 (None, None),
                 ('Help and Support', lambda: self.launch('help')),
                 ('About COS Neptune', lambda: self.launch('about'))]
        y = y0 + 84
        for label, act in links:
            if label is None:
                y += 10
                continue
            out['links'].append((label, act, (x0 + 266, y, x1 - 10, y + 28)))
            y += 30
        out['power'] = (x0 + 266, y1 - 40, x0 + 360, y1 - 14)
        out['power_more'] = (x0 + 360, y1 - 40, x0 + 386, y1 - 14)
        out['pic'] = (x0 + 300, y0 - 22, x0 + 372, y0 + 50)
        return out

    def start_hit(self, mx, my):
        L = self.start_layout()
        for key, label, icon, r, small in L['items']:
            if gfx.in_rect(mx, my, r):
                return ('prog', key)
        if gfx.in_rect(mx, my, L['all']):
            return ('all',)
        if gfx.in_rect(mx, my, L['search']):
            return ('search',)
        for label, act, r in L['links']:
            if gfx.in_rect(mx, my, r):
                return ('link', label)
        if gfx.in_rect(mx, my, L['power']):
            return ('power',)
        if gfx.in_rect(mx, my, L['power_more']):
            return ('power_more',)
        return None

    def start_click(self, mx, my, button):
        h = self.start_hit(mx, my)
        if not h:
            return
        L = self.start_layout()
        if h[0] == 'prog':
            self.close_popups()
            self.launch(h[1])
        elif h[0] == 'all':
            self.start_all = not self.start_all
            self.dirty = True
        elif h[0] == 'link':
            for label, act, r in L['links']:
                if label == h[1]:
                    self.close_popups()
                    act()
        elif h[0] == 'power':
            self.close_popups()
            self.shutdown('exit')
        elif h[0] == 'power_more':
            r = L['power_more']
            self.menus = [Menu([
                {'label': 'Lock', 'action': self.lock},
                {'sep': True},
                {'label': 'Restart Neptune', 'action': lambda: self.shutdown('restart')},
                {'label': 'Exit to Console', 'action': lambda: self.shutdown('exit')},
                {'sep': True},
                {'label': 'Shut Down Computer...', 'action': lambda: self.launch('powerdlg', mode='poweroff')},
                {'label': 'Restart Computer...', 'action': lambda: self.launch('powerdlg', mode='reboot')},
            ], r[2] + 2, r[1] - 140, self)]
            self.start_open = False
            self.dirty = True

    def start_key(self, ev):
        k = ev['key']
        s = self.start_search
        if k == 'escape':
            self.close_popups()
            return
        if k == 'enter':
            progs = self.start_programs()
            if progs:
                self.close_popups()
                self.launch(progs[0][0])
            return
        if k == 'backspace':
            s['text'] = s['text'][:-1]
        elif ev.get('char'):
            s['text'] += ev['char']
        s['cur'] = len(s['text'])
        self.dirty = True

    # ======================================================================
    # desktop icons
    # ======================================================================
    def icon_rects(self):
        if not self.settings.get('show_icons', True):
            return []
        out = []
        x, y = 14, MENUBAR_H + 14
        for i, _ in enumerate(self.desktop_icons):
            out.append((x, y, x + 80, y + 82))
            y += 90
            if y + 82 > self.H - TASKBAR_H:
                y = MENUBAR_H + 14
                x += 90
        return out

    def icon_at(self, mx, my):
        for i, r in enumerate(self.icon_rects()):
            if gfx.in_rect(mx, my, r):
                return i
        return None

    # ======================================================================
    # ticking
    # ======================================================================
    def tick(self, now):
        self.cosplace_events(now)
        if self.menus:
            mx, my = self.mouse
            for m in self.menus:
                a = m.arrow_at(mx, my) if m.scrolling else 0
                if a and now - m.last_scroll > 0.09:
                    m.last_scroll = now
                    if m.scroll_by(a):
                        self.menus = self.menus[:self.menus.index(m) + 1]
                        self.dirty = True
        self.saver_check(now)
        if self.phase == 'saver':
            if now - self.saver_frame_t >= 1.0 / 30:
                self.saver_frame_t = now
                self.dirty = True
            return
        # phases
        if self.phase == 'boot':
            if now - self.phase_t > 3.2:
                if not self.settings.get('oobe_done', True):
                    self.start_oobe()
                    return
                self.phase = 'welcome'
                self.phase_t = now
                self.sound('boot')
            self.dirty = True
            return
        if self.phase == 'oobe':
            w = self.oobe_win
            if now - self.caret_t > 0.53:
                self.caret_t = now
                self.caret_on = not self.caret_on
                if w is not None and w.app.wants_caret():
                    w.app.ui.caret_on = self.caret_on
                    w.content_dirty = True
                    self.dirty = True
            if w is not None and self.safe(w, w.app.on_tick, now):
                w.content_dirty = True
                self.dirty = True
            return
        if self.phase == 'welcome':
            if now - self.phase_t > 1.6:
                self.enter_desktop()
            self.dirty = True
            return
        if self.phase == 'lock':
            sec = int(now)
            if now - self.lock_t < 0.5 or now - self.lock_shake < 0.5 or sec != self.last_second:
                self.last_second = sec
                self.dirty = True
            if now - self.caret_t > 0.53:
                self.caret_t = now
                self.caret_on = not self.caret_on
                self.dirty = True
            return
        if self.phase == 'shutdown':
            if now - self.phase_t > 1.8:
                self.exit_code = getattr(self, 'exit_request', 'exit')
            self.dirty = True
            return
        if self.fade_in_t is not None:
            if now - self.fade_in_t > 0.6:
                self.fade_in_t = None
            self.dirty = True
        # animations
        for w in list(self.windows):
            if w.anim:
                if now - w.anim['t0'] >= w.anim['dur']:
                    kind = w.anim['kind']
                    w.anim = None
                    if kind == 'close':
                        self._remove(w)
                self.dirty = True
            if w.flash_until and now < w.flash_until + 0.05:
                self.dirty = True
        # clock
        fmt = '%H:%M' if self.settings.get('clock_24h') else '%I:%M %p'
        c = time.strftime(fmt)
        if c != self.last_clock:
            self.last_clock = c
            self.dirty = True
        if self.settings.get('show_gadget', True):
            s = int(now)
            if s != self.last_second:
                self.last_second = s
                self.desk_dirty = True
                self.dirty = True
        # balloons
        nb = [b for b in self.balloons if now - b['t0'] < b['dur'] + 0.4]
        if len(nb) != len(self.balloons) or any(now - b['t0'] < 0.4 or now - b['t0'] > b['dur'] for b in nb):
            self.dirty = True
        self.balloons = nb
        # hover timers: taskbar thumbnails, aero peek
        th = self.task_hover
        if th and th[0] == 'win' and self.thumb is not th[1] and now - self.task_hover_t > 0.45 and not self.drag:
            self.thumb = th[1]
            self.thumb_hover = False
            self.dirty = True
        if self.showdesk_hover and not self.peek and now - self.peek_t > 0.5:
            self.peek = True
            self.dirty = True
        if self.snap_preview is not None:
            self.dirty = True
        # caret blink
        if now - self.caret_t > 0.53:
            self.caret_t = now
            self.caret_on = not self.caret_on
            for w in self.windows:
                if w is self.active and w.app.wants_caret():
                    w.app.ui.caret_on = self.caret_on
                    w.content_dirty = True
                    self.dirty = True
            if self.start_open:
                self.dirty = True
        self.plugins.tick(now)
        if self.phase == 'desktop' and self.settings.get('desktop_pet', True) and self.pet.tick(now):
            self.dirty = True
        if self.confetti is not None:
            self.dirty = True
        self.compat.tick(now)
        self.ceap.tick(now)
        self.drivers.drain()
        self.drivers.tick(now)
        from . import evinput
        evinput.OPTIONS['speed'] = float(self.settings.get('cursor_speed', 1.0) or 1.0)
        evinput.OPTIONS['tap'] = bool(self.settings.get('tap_to_click', True))
        # apps
        for w in list(self.windows):
            try:
                if w.app.on_tick(now):
                    w.content_dirty = True
                    self.dirty = True
            except Exception as e:
                self.crash(w.app.title, e, win=w)

    def busy(self):
        """True when something is animating and frames should flow."""
        if self.phase != 'desktop' or self.fade_in_t is not None:
            return True
        if any(getattr(w.app, 'live', False) and not w.minimized for w in self.windows):
            return True
        return any(w.anim for w in self.windows) or self.snap_preview is not None or \
            any(time.time() - b['t0'] < 0.4 or time.time() - b['t0'] > b['dur'] for b in self.balloons)

    # ======================================================================
    # rendering
    # ======================================================================
    @property
    def tint(self):
        return tuple(self.settings['glass_rgb'])

    def wall_spec(self):
        return self.settings.get('wall') or {'kind': 'preset', 'name': self.settings.get('wallpaper', 'Neptune Aurora')}

    def wall(self):
        spec = self.wall_spec()
        key = json.dumps(spec, sort_keys=True)
        if self._wall is None or self._wall_name != key:
            try:
                self._wall = wallpaper.build(spec, self.W, self.H)
            except Exception:
                self._wall = wallpaper.get('Neptune Aurora', self.W, self.H)
            self._wall_name = key
            self._welcome_cache = None
        return self._wall

    def render_desk(self):
        img = self.wall().copy()
        fnt = gfx.font(12)
        for i, (r, (label, icon, _a)) in enumerate(zip(self.icon_rects(), self.desktop_icons)):
            sel, hot = i == self.icon_sel, i == self.icon_hover
            if sel or hot:
                a = 70 if sel else 38
                gfx.round_rect(img, r, 3, fill=gfx.vgradient(r[2] - r[0], r[3] - r[1],
                                                         ((0, (255, 255, 255, a + 20)), (1, (255, 255, 255, a)))),
                               outline=(255, 255, 255, 140 if sel else 90))
            img.alpha_composite(icons.get(icon, 48), (r[0] + 16, r[1] + 4))
            lines = self._wrap_label(label, fnt, 76)
            for j, ln in enumerate(lines[:2]):
                gfx.shadow_text(img, ((r[0] + r[2]) // 2, r[1] + 62 + j * 14), ln, fnt, (255, 255, 255, 255),
                                (0, 0, 0, 230), radius=2, anchor='ma')
        if self.settings.get('show_gadget', True):
            self.draw_gadget(img)
        if self.settings.get('show_build', True):
            # Windows 7 test-build style watermark, bottom right above the taskbar
            f1, f2 = gfx.font(12), gfx.font(11)
            x, y = self.W - 14, self.H - TASKBAR_H - 12
            gfx.shadow_text(img, (x, y - 34), '%s' % theme.FULLNAME, f1, (255, 255, 255, 215), (0, 0, 0, 150),
                            radius=2, anchor='ra')
            gfx.shadow_text(img, (x, y - 17), 'Build %s' % theme.BUILD, f2, (255, 255, 255, 190), (0, 0, 0, 150),
                            radius=2, anchor='ra')
        return img

    def _wrap_label(self, label, fnt, width):
        words = label.split()
        lines, cur = [], ''
        for w in words:
            t = (cur + ' ' + w) if cur else w
            if gfx.text_width(fnt, t) > width and cur:
                lines.append(cur)
                cur = w
            else:
                cur = t
        lines.append(cur)
        return lines

    def draw_gadget(self, img):
        size = 150
        x0, y0 = self.W - size - 24, MENUBAR_H + 24
        cx, cy = x0 + size // 2, y0 + size // 2
        sh, pad = gfx.shadow(size, size, size // 2, 10, 120)
        img.alpha_composite(sh, (x0 - pad, y0 - pad + 3))
        face = gfx.radial(size, size, ((0, (255, 255, 255, 250)), (0.82, (225, 235, 242, 250)),
                                       (0.9, (150, 170, 190, 255)), (0.95, (235, 240, 245, 255)),
                                       (1.0, (90, 110, 130, 255))))
        m = Image.new('L', (size, size), 0)
        ImageDraw.Draw(m).ellipse((0, 0, size - 1, size - 1), fill=255)
        f = face.copy()
        f.putalpha(m)
        img.alpha_composite(f, (x0, y0))
        d = ImageDraw.Draw(img)
        for i in range(60):
            a = i * math.pi / 30
            r0 = size * (0.36 if i % 5 == 0 else 0.39)
            r1 = size * 0.42
            d.line([(cx + r0 * math.sin(a), cy - r0 * math.cos(a)), (cx + r1 * math.sin(a), cy - r1 * math.cos(a))],
                   fill=(40, 50, 60, 255) if i % 5 == 0 else (120, 130, 140, 255), width=2 if i % 5 == 0 else 1)
        gfx.draw_text(img, (cx, cy + 34), 'NEPTUNE', gfx.font(9, False, 'serif'), (90, 110, 125, 255), anchor='mm')
        t = time.localtime()
        sec = t.tm_sec
        mins = t.tm_min + sec / 60.0
        hrs = (t.tm_hour % 12) + mins / 60.0
        for ang, length, width, col in ((hrs * 30, 0.24, 4, (30, 35, 45)), (mins * 6, 0.34, 3, (30, 35, 45)),
                                        (sec * 6, 0.38, 1, (200, 40, 30))):
            a = math.radians(ang)
            d.line([(cx - 10 * math.sin(a), cy + 10 * math.cos(a)),
                    (cx + size * length * math.sin(a), cy - size * length * math.cos(a))],
                   fill=col + (255,), width=width)
        d.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(200, 40, 30, 255))
        # glass dome highlight
        hl = Image.new('L', (size, size), 0)
        ImageDraw.Draw(hl).ellipse((size * 0.12, size * 0.05, size * 0.88, size * 0.5), fill=90)
        hl = hl.filter(ImageFilter.GaussianBlur(6))
        from PIL import ImageChops
        hl = ImageChops.multiply(hl, m)
        g = Image.new('RGBA', (size, size), (255, 255, 255, 255))
        g.putalpha(hl)
        img.alpha_composite(g, (x0, y0))

    def window_content(self, w):
        cw, ch = w.client_size
        if w.content is None or w.content_dirty or w.content.size != (cw, ch):
            img = Image.new('RGBA', (cw, ch), w.app.bg)
            w.app.ui.begin(img)
            w.app.ui.caret_on = self.caret_on if w is self.active else False
            try:
                w.app.draw(img, w.app.ui)
                if w is self.active:
                    w.app.draw_focus(img)
            except Exception as e:
                self.crash(w.app.title, e, win=w)
            w.content = img
            w.content_dirty = False
        return w.content

    def _glass_params(self, w, active):
        s = self.settings
        inten = round(s['intensity'] * (1.0 if active else 0.5), 3)
        flash = w.flash_until and time.time() < w.flash_until and int((w.flash_until - time.time()) * 10) % 2 == 0
        tint = self.tint if not flash else tuple(gfx.mix(self.tint, (255, 255, 255), 0.5)[:3])
        return tint, inten

    def window_chrome(self, w, box, active):
        """Everything on a window frame that does not depend on what is behind
        it: borders, icon, glowing title, caption buttons. Cached per state."""
        x0, y0, x1, y1 = [int(v) for v in box]
        bw, bh = x1 - x0, y1 - y0
        th, b, r = w.metrics()
        pressed = w.press if (self.drag is not None and self.drag.get('win') is w) else None
        key = (bw, bh, active, w.hover, pressed, w.app.title, w.app.icon, w.state, w.app.dialog)
        if getattr(w, '_chrome_key', None) == key:
            return w._chrome
        img = Image.new('RGBA', (bw, bh), (0, 0, 0, 0))
        corners = (True,) * 4 if r else (False,) * 4
        gfx.fill_mask(img, gfx.rounded_outline(bw, bh, r, 1, corners), (10, 20, 30, 190))
        if bw > 4 and bh > 4:
            gfx.fill_mask(img, gfx.rounded_outline(bw - 2, bh - 2, max(0, r - 1), 1, corners), (255, 255, 255, 120), (1, 1))
        iy = (th - 16) // 2 + (1 if r else 0)
        img.alpha_composite(icons.get(w.app.icon, 16), (b + 5, iy))
        btns = w.buttons(box)
        title_room = (btns[0][1][0] if btns else x1 - b) - x0 - (b + 28) - 8
        fnt = gfx.font(12)
        title = gfx.ellipsize(fnt, w.app.title, title_room)
        gfx.glow_text(img, (b + 27, th // 2 + (1 if r else 0)), title, fnt,
                      fill=(0, 0, 0, 255) if active else (60, 60, 60, 255),
                      strength=230 if active else 150, radius=5, anchor='lm')
        for i, (name, br) in enumerate(btns):
            lr = (br[0] - x0, br[1] - y0, br[2] - x0, br[3] - y0)
            self.draw_caption_button(img, name, lr, active, w.hover == name, pressed == name,
                                     first=i == 0, last=i == len(btns) - 1, maxed=w.state == 'max')
        cx0, cy0, cx1, cy1 = w.client_rect(box)
        ImageDraw.Draw(img).rectangle((cx0 - x0 - 1, cy0 - y0 - 1, cx1 - x0, cy1 - y0),
                                      outline=(0, 0, 0, 110 if active else 70))
        w._chrome_key, w._chrome = key, img
        return img

    def _frame_base(self, canvas, box, tint, inten, active, size):
        """Blurred backdrop + tint overlay for one region of a frame."""
        s = self.settings
        bw, bh = size
        if s['transparency'] and gfx.AERO:
            piece = gfx.blur_region(canvas, box, 12)
        else:
            piece = Image.new('RGBA', (box[2] - box[0], box[3] - box[1]), gfx.opaque_frame(tint))
        return piece

    def render_window_layer(self, canvas, w, box, active):
        """The whole window as one RGBA layer (used while animating)."""
        x0, y0, x1, y1 = [int(v) for v in box]
        bw, bh = x1 - x0, y1 - y0
        th, b, r = w.metrics()
        tint, inten = self._glass_params(w, active)
        tr = (self.settings['transparency'] and gfx.AERO)
        layer = self._frame_base(canvas, box, tint, inten, active, (bw, bh))
        layer.alpha_composite(gfx.glass_overlay(bw, bh, tint, inten if tr else 0.0, True, 0 if active else 28))
        layer.putalpha(gfx.rounded_mask(bw, bh, r) if r else Image.new('L', (bw, bh), 255))
        layer.alpha_composite(self.window_chrome(w, box, active))
        layer.paste(self.window_content(w), (b, th))
        return layer

    def compose_window(self, canvas, w, active):
        """Static window: glass only on the four frame strips, the opaque client pasted straight in."""
        box = w.rect
        x0, y0, x1, y1 = box
        bw, bh = x1 - x0, y1 - y0
        th, b, r = w.metrics()
        tint, inten = self._glass_params(w, active)
        tr = (self.settings['transparency'] and gfx.AERO)
        overlay = gfx.glass_overlay(bw, bh, tint, inten if tr else 0.0, True, 0 if active else 28)
        mask = gfx.rounded_mask(bw, bh, r) if r else None
        chrome = self.window_chrome(w, box, active)
        cx0, cy0, cx1, cy1 = w.client_rect()
        strips = [(x0, y0, x1, cy0), (x0, cy0, cx0, cy1), (cx1, cy0, x1, cy1), (x0, cy1, x1, y1)]
        strips = [s for s in strips if s[2] > s[0] and s[3] > s[1]
                  and s[2] > 0 and s[3] > 0 and s[0] < self.W and s[1] < self.H]
        pieces = [(s, self._frame_base(canvas, s, tint, inten, active, (bw, bh))) for s in strips]
        for s, piece in pieces:
            lb = (s[0] - x0, s[1] - y0, s[2] - x0, s[3] - y0)
            piece.alpha_composite(overlay.crop(lb))
            if mask is not None:
                piece.putalpha(mask.crop(lb))
            piece.alpha_composite(chrome.crop(lb))
            gfx.alpha_composite_clip(canvas, piece, s[0], s[1])
        canvas.paste(self.window_content(w), (cx0, cy0))

    def draw_caption_button(self, img, name, r, active, hover, pressed, first, last, maxed):
        x0, y0, x1, y1 = r
        w, h = x1 - x0, y1 - y0
        corners = (False, False, last, first)
        if name == 'close' and (active or hover):
            if pressed:
                stops = ((0, (220, 110, 90)), (0.5, (180, 50, 30)), (0.51, (150, 20, 5)), (1, (120, 20, 10)))
            elif hover:
                stops = ((0, (255, 170, 150)), (0.5, (235, 90, 70)), (0.51, (210, 40, 20)), (1, (240, 110, 60)))
            else:
                stops = ((0, (230, 150, 140)), (0.5, (205, 80, 70)), (0.51, (190, 45, 35)), (1, (200, 90, 70)))
        else:
            if pressed:
                stops = ((0, (130, 180, 220, 230)), (0.5, (60, 120, 180, 230)), (0.51, (30, 90, 150, 230)), (1, (80, 170, 230, 230)))
            elif hover:
                stops = ((0, (200, 235, 255, 230)), (0.5, (120, 190, 240, 220)), (0.51, (60, 150, 220, 220)), (1, (130, 220, 255, 230)))
            else:
                stops = ((0, (255, 255, 255, 120)), (0.5, (255, 255, 255, 60)), (0.51, (255, 255, 255, 20)), (1, (255, 255, 255, 50)))
        gfx.round_rect(img, r, 4, fill=gfx.vgradient(w, h, stops), corners=corners)
        if hover and not pressed:
            glow = (255, 140, 100) if name == 'close' else (120, 210, 255)
            gm = Image.new('L', (w + 16, h + 16), 0)
            ImageDraw.Draw(gm).ellipse((4, h // 2 + 2, w + 12, h + 22), fill=200)
            gm = gm.filter(ImageFilter.GaussianBlur(5))
            gl = Image.new('RGBA', gm.size, glow + (255,))
            gl.putalpha(gm.point(lambda v: v * 120 // 255))
            gfx.alpha_composite_clip(img, gl, x0 - 8, y0 - 8)
        gfx.round_rect(img, r, 4, outline=(30, 30, 40, 200), corners=corners)
        gfx.round_rect(img, (x0 + 1, y0 + 1, x1 - 1, y1 - 1), 3, outline=(255, 255, 255, 90), corners=corners)
        # glyph
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        d = ImageDraw.Draw(img)
        dark, white = (30, 30, 40, 230), (255, 255, 255, 255)
        if name == 'close':
            for col, wd in ((dark, 5), (white, 2)):
                d.line([(cx - 4, cy - 4), (cx + 4, cy + 4)], fill=col, width=wd)
                d.line([(cx - 4, cy + 4), (cx + 4, cy - 4)], fill=col, width=wd)
        elif name == 'min':
            d.rectangle((cx - 5, cy + 2, cx + 5, cy + 5), fill=dark)
            d.rectangle((cx - 4, cy + 3, cx + 4, cy + 4), fill=white)
        elif name == 'max':
            if maxed:
                for (ax, ay) in ((2, -2), (-2, 2)):
                    d.rectangle((cx - 5 + ax, cy - 4 + ay, cx + 4 + ax, cy + 4 + ay), outline=dark, width=3)
                    d.rectangle((cx - 4 + ax, cy - 3 + ay, cx + 3 + ax, cy + 3 + ay), outline=white, width=1)
            else:
                d.rectangle((cx - 6, cy - 5, cx + 6, cy + 5), outline=dark, width=4)
                d.rectangle((cx - 5, cy - 4, cx + 5, cy + 4), outline=white, width=2)

    def draw_window(self, canvas, w, now, ghost=False):
        active = w is self.active and not ghost
        box = w.rect
        scale, alpha, target = 1.0, 1.0, None
        a = w.anim
        if a:
            t = (now - a['t0']) / a['dur']
            t = gfx.clamp(t, 0, 1)
            k = a['kind']
            if k == 'open':
                e = gfx.ease_out(t)
                scale, alpha = 0.88 + 0.12 * e, e
            elif k == 'close':
                e = gfx.ease_in_out(t)
                scale, alpha = 1 - 0.08 * e, 1 - e
            elif k in ('min', 'unmin'):
                e = gfx.ease_in_out(t if k == 'min' else 1 - t)
                tb = self.taskbar_button_rect(w)
                x0, y0, x1, y1 = box
                target = tuple(int(box[i] + (tb[i] - box[i]) * e) for i in range(4))
                alpha = 1 - 0.85 * e
            elif k == 'geom':
                e = gfx.ease_out(t)
                f = a['from']
                target = tuple(int(f[i] + (box[i] - f[i]) * e) for i in range(4))
        elif w.minimized:
            return
        th, b, r = w.metrics()
        if ghost:
            # Aero Peek: the window becomes a sheet of empty glass with an outline
            x0, y0, x1, y1 = box
            sheet = gfx.glass_layer(canvas, box, r or 2, self.tint, 0.08, transparency=True, blur=False,
                                    highlight=False)
            sheet = gfx.with_alpha(sheet, 0.55)
            gfx.alpha_composite_clip(canvas, sheet, x0, y0)
            return
        if not a:
            x0, y0, x1, y1 = box
            if r:
                sh, pad = gfx.shadow(x1 - x0, y1 - y0, r, 14 if active else 9, 150 if active else 90)
                gfx.alpha_composite_clip(canvas, sh, x0 - pad, y0 - pad + 4)
            self.compose_window(canvas, w, active)
            return
        layer = self.render_window_layer(canvas, w, box, active)
        x0, y0, x1, y1 = box
        bw, bh = x1 - x0, y1 - y0
        if target is not None:
            tw, th2 = max(2, target[2] - target[0]), max(2, target[3] - target[1])
            layer = layer.resize((tw, th2), Image.BILINEAR)
            dx, dy = target[0], target[1]
            bw, bh = tw, th2
        elif scale != 1.0:
            nw, nh = max(2, int(bw * scale)), max(2, int(bh * scale))
            layer = layer.resize((nw, nh), Image.BILINEAR)
            dx, dy = x0 + (bw - nw) // 2, y0 + (bh - nh) // 2
            bw, bh = nw, nh
        else:
            dx, dy = x0, y0
        if r or w.state != 'max':
            sh, pad = gfx.shadow(bw, bh, max(r, 4), 14 if active else 9, 150 if active else 90)
            if alpha < 1:
                sh = gfx.with_alpha(sh, alpha)
            gfx.alpha_composite_clip(canvas, sh, dx - pad, dy - pad + 4)
        if alpha < 1:
            layer = gfx.with_alpha(layer, alpha)
        gfx.alpha_composite_clip(canvas, layer, dx, dy)

    def render_menubar(self, canvas):
        tint = gfx.mix(self.tint, (0, 10, 20), 0.35)[:3]
        gfx.glass_panel(canvas, (0, 0, self.W, MENUBAR_H), 0, tint, 0.5, corners=(False,) * 4,
                        transparency=(self.settings['transparency'] and gfx.AERO), border=False, blur_radius=14)
        d = ImageDraw.Draw(canvas)
        d.line([(0, MENUBAR_H - 1), (self.W, MENUBAR_H - 1)], fill=(0, 0, 0, 150))
        d.line([(0, MENUBAR_H - 2), (self.W, MENUBAR_H - 2)], fill=(255, 255, 255, 50))
        # top sheen like the concept art
        canvas.alpha_composite(gfx.vgradient(self.W, 12, ((0, (0, 0, 0, 120)), (1, (0, 0, 0, 0)))), (0, 0))
        fnt = gfx.font(17, False, 'serif')
        for i, (t, r) in enumerate(self.menubar_layout()):
            if i == self.menubar_open or i == self.menubar_hover:
                a = 70 if i == self.menubar_open else 40
                gfx.round_rect(canvas, r, 4, fill=gfx.vgradient(r[2] - r[0], r[3] - r[1],
                                                            ((0, (255, 255, 255, a + 30)), (0.5, (255, 255, 255, a)),
                                                             (0.51, (255, 255, 255, a // 2)), (1, (255, 255, 255, a)))),
                               outline=(255, 255, 255, 110))
            gfx.shadow_text(canvas, ((r[0] + r[2]) // 2, MENUBAR_H // 2), t, fnt, (255, 255, 255, 255),
                            (0, 0, 0, 200), radius=2, anchor='mm')
        # right: active program, left: Neptune mark
        canvas.alpha_composite(icons.get('neptune', 22), (8, (MENUBAR_H - 22) // 2))
        if self.active is not None:
            f2 = gfx.font(11)
            t = gfx.ellipsize(f2, self.active.app.title, 220)
            gfx.shadow_text(canvas, (self.W - 12, MENUBAR_H // 2), t, f2, (230, 245, 250, 255), (0, 0, 0, 200),
                            anchor='rm')

    def render_taskbar(self, canvas, now):
        H, W = self.H, self.W
        y0 = H - TASKBAR_H
        tint = gfx.mix(self.tint, (0, 8, 16), 0.45)[:3]
        gfx.glass_panel(canvas, (0, y0, W, H), 0, tint, 0.55, corners=(False,) * 4,
                        transparency=(self.settings['transparency'] and gfx.AERO), border=False, blur_radius=14)
        d = ImageDraw.Draw(canvas)
        d.line([(0, y0), (W, y0)], fill=(0, 0, 0, 170))
        d.line([(0, y0 + 1), (W, y0 + 1)], fill=(255, 255, 255, 70))
        mx, my = self.mouse
        # buttons
        fnt = gfx.font(12)
        for w, r in self.taskbar_buttons():
            hot = self.task_hover == ('win', w)
            act = w is self.active and not w.minimized
            bw, bh = r[2] - r[0], r[3] - r[1]
            if act or hot or self.thumb is w:
                a = 90 if act else 50
                gfx.round_rect(canvas, r, 3, fill=gfx.vgradient(bw, bh, ((0, (255, 255, 255, a)), (0.5, (255, 255, 255, a // 2)),
                                                                     (0.51, (255, 255, 255, a // 4)), (1, (255, 255, 255, a // 2)))),
                               outline=(255, 255, 255, 140 if act else 100))
                gfx.round_rect(canvas, r, 3, outline=(0, 0, 0, 120))
                if hot:
                    # Win7 colour hot-track: glow follows the pointer
                    glow = gfx.radial(bw * 2, bh * 2, ((0, gfx.mix(self.tint, (255, 255, 255), 0.5)[:3] + (150,)),
                                                       (1, (255, 255, 255, 0))))
                    gl = glow.crop((bw - (mx - r[0]), bh // 2, 2 * bw - (mx - r[0]), bh // 2 + bh))
                    gfx.paste_masked(canvas, gl, gfx.rounded_mask(bw, bh, 3), (r[0], r[1]))
            elif not w.minimized:
                pass
            canvas.alpha_composite(icons.get(w.app.icon, 24), (r[0] + 6, r[1] + (bh - 24) // 2))
            if bw > 70:
                t = gfx.ellipsize(fnt, w.app.title, bw - 42)
                gfx.shadow_text(canvas, (r[0] + 36, (r[1] + r[3]) // 2), t, fnt, (255, 255, 255, 255), (0, 0, 0, 220),
                                anchor='lm')
        # start orb
        hot = self.task_hover == ('start',)
        cx, cy = 28, y0 + TASKBAR_H // 2
        if hot or self.start_open:
            gsize = 70
            glow = gfx.radial(gsize, gsize, ((0, (140, 255, 250, 210 if self.start_open else 160)), (1, (60, 200, 255, 0))))
            gfx.alpha_composite_clip(canvas, glow, cx - gsize // 2, cy - gsize // 2)
        orb = icons.get('neptune', 38)
        canvas.alpha_composite(orb, (cx - 19, cy - 19))
        # tray
        tx = W - 16 - 86
        f1, f2 = gfx.font(12), gfx.font(11)
        t = time.strftime('%H:%M' if self.settings.get('clock_24h') else '%I:%M %p').lstrip('0')
        dt = time.strftime('%m/%d/%Y').lstrip('0').replace('/0', '/')
        if self.task_hover == ('clock',):
            r = (tx - 4, y0 + 3, W - 18, H - 3)
            gfx.round_rect(canvas, r, 3, fill=(255, 255, 255, 40), outline=(255, 255, 255, 90))
        gfx.shadow_text(canvas, (tx + 41, y0 + 13), t, f1, (255, 255, 255, 255), (0, 0, 0, 220), anchor='mm')
        gfx.shadow_text(canvas, (tx + 41, y0 + 29), dt, f2, (255, 255, 255, 255), (0, 0, 0, 220), anchor='mm')
        # backend badge
        badge = {'gdr': 'GDR', 'window': 'WIN', 'headless': 'SIM'}.get(self.backend, self.backend.upper()[:3])
        bx = tx - 52
        gfx.round_rect(canvas, (bx, y0 + 12, bx + 38, y0 + 30), 4, fill=(255, 255, 255, 30), outline=(255, 255, 255, 90))
        gfx.shadow_text(canvas, (bx + 19, y0 + 21), badge, gfx.font(10, True), (220, 255, 250, 255), (0, 0, 0, 200),
                        anchor='mm')
        # show desktop slab
        r = (W - 15, y0 + 1, W, H)
        a = 90 if self.showdesk_hover else 30
        canvas.alpha_composite(gfx.vgradient(15, TASKBAR_H - 1, ((0, (255, 255, 255, a)), (1, (255, 255, 255, a // 2)))), (r[0], r[1]))
        d.line([(r[0], r[1]), (r[0], r[3])], fill=(0, 0, 0, 150))
        d.line([(r[0] + 1, r[1]), (r[0] + 1, r[3])], fill=(255, 255, 255, 80))

    def render_menu(self, canvas, m):
        x0, y0, x1, y1 = m.rect
        w, h = x1 - x0, y1 - y0
        sh, pad = gfx.shadow(w, h, 3, 6, 110)
        gfx.alpha_composite_clip(canvas, sh, x0 - pad + 2, y0 - pad + 3)
        layer = gfx.glass_layer(canvas, m.rect, 3, (240, 244, 248), 0.88, transparency=(self.settings['transparency'] and gfx.AERO),
                                highlight=False, border=False, blur_radius=8)
        d = ImageDraw.Draw(layer)
        for c in range(1 if m.scrolling else len(m.columns)):
            gx = c * m.colw
            d.rectangle((gx + 26, 2, gx + 27, h - 3), fill=(226, 227, 227, 255))
            d.line([(gx + 28, 2), (gx + 28, h - 3)], fill=(255, 255, 255, 255))
            if c:
                d.line([(gx, 4), (gx, h - 5)], fill=(205, 205, 205, 255))
        gfx.fill_mask(layer, gfx.rounded_outline(w, h, 3), (151, 151, 151, 255))
        up, dn = m.arrow_rects()
        for r, d_ in ((up, -1), (dn, 1)):
            if r is None:
                continue
            lr = (r[0] - x0, r[1] - y0, r[2] - x0, r[3] - y0)
            live = (m.scroll > 0) if d_ < 0 else (m.scroll < m.max_scroll())
            hot = live and gfx.in_rect(self.mouse[0], self.mouse[1], r)
            layer.alpha_composite(gfx.vgradient(lr[2] - lr[0], lr[3] - lr[1], ((0, (236, 244, 253) if hot else (248, 248, 250)),
                                                                              (1, (216, 232, 250) if hot else (236, 238, 242)))),
                                  (lr[0], lr[1]))
            cx, cy = (lr[0] + lr[2]) // 2, (lr[1] + lr[3]) // 2
            col = (40, 40, 40, 255) if live else (190, 190, 190, 255)
            pts = [(cx - 5, cy + 2), (cx + 5, cy + 2), (cx, cy - 3)] if d_ < 0 else [(cx - 5, cy - 2), (cx + 5, cy - 2), (cx, cy + 3)]
            d.polygon(pts, fill=col)
        fnt = gfx.font(12)
        for i, r in m.item_rects():
            it = m.items[i]
            lr = (r[0] - x0, r[1] - y0, r[2] - x0, r[3] - y0)
            if it.get('sep'):
                cy = (lr[1] + lr[3]) // 2
                d.line([(lr[0] + 27, cy), (lr[2] - 1, cy)], fill=(224, 224, 224, 255))
                d.line([(lr[0] + 27, cy + 1), (lr[2] - 1, cy + 1)], fill=(255, 255, 255, 255))
                continue
            en = it.get('enabled', True)
            if i == m.hover and en:
                gfx.round_rect(layer, lr, 3, fill=gfx.vgradient(lr[2] - lr[0], lr[3] - lr[1],
                                                            ((0, (236, 244, 253)), (1, (216, 232, 250)))),
                               outline=(168, 202, 240, 255))
            col = (0, 0, 0, 255) if en else (141, 141, 141, 255)
            if it.get('icon'):
                layer.alpha_composite(icons.get(it['icon'], 16), (lr[0] + 4, lr[1] + 4))
            if it.get('checked'):
                gfx.round_rect(layer, (lr[0] + 1, lr[1] + 1, lr[0] + 22, lr[3] - 1), 2, fill=(230, 240, 250, 255),
                               outline=(168, 202, 240, 255))
                d.line([(lr[0] + 7, lr[1] + 12), (lr[0] + 10, lr[1] + 15), (lr[0] + 16, lr[1] + 8)], fill=(20, 40, 80, 255), width=2)
            d.text((lr[0] + 32, (lr[1] + lr[3]) // 2), it.get('label', ''), font=fnt, fill=col, anchor='lm')
            if it.get('shortcut'):
                d.text((lr[2] - 10, (lr[1] + lr[3]) // 2), it['shortcut'], font=fnt, fill=(110, 110, 110, 255), anchor='rm')
            if it.get('sub'):
                cy = (lr[1] + lr[3]) // 2
                d.polygon([(lr[2] - 12, cy - 4), (lr[2] - 8, cy), (lr[2] - 12, cy + 4)], fill=col)
        gfx.alpha_composite_clip(canvas, layer, x0, y0)

    def render_start(self, canvas):
        x0, y0, x1, y1 = self.start_rect()
        w, h = x1 - x0, y1 - y0
        sh, pad = gfx.shadow(w, h, 6, 12, 150)
        gfx.alpha_composite_clip(canvas, sh, x0 - pad, y0 - pad + 3)
        gfx.glass_panel(canvas, (x0, y0, x1, y1), 6, gfx.mix(self.tint, (0, 0, 0), 0.25)[:3], 0.55,
                        corners=(True, True, False, False), transparency=(self.settings['transparency'] and gfx.AERO))
        L = self.start_layout()
        left = L['left']
        gfx.round_rect(canvas, left, 4, fill=(255, 255, 255, 250), outline=(80, 90, 100, 255))
        hov = self.start_hover
        fnt, fnt_s = gfx.font(12), gfx.font(12)
        q = self.start_search['text']
        if q:
            gfx.draw_text(canvas, (left[0] + 10, left[1] + 6), 'Programs', gfx.font(12, True), (30, 60, 110, 255))
        for key, label, icon, r, small in L['items']:
            if q and r[1] < left[1] + 24:
                continue
            hot = hov == ('prog', key)
            if hot:
                gfx.round_rect(canvas, r, 3, fill=gfx.vgradient(r[2] - r[0], r[3] - r[1], ((0, (220, 235, 252)), (1, (193, 219, 252)))),
                               outline=(125, 162, 206, 255))
            isz = 16 if small else 32
            canvas.alpha_composite(icons.get(icon, isz), (r[0] + 4, (r[1] + r[3] - isz) // 2))
            gfx.draw_text(canvas, (r[0] + isz + 12, (r[1] + r[3]) // 2), label, fnt, (0, 0, 0, 255), anchor='lm')
        if q and not L['items']:
            gfx.draw_text(canvas, (left[0] + 12, left[1] + 30), 'No items match your search.', fnt, (110, 110, 110, 255))
        # all programs
        ar = L['all']
        ImageDraw.Draw(canvas).line([(ar[0] + 4, ar[1] - 4), (ar[2] - 4, ar[1] - 4)], fill=(214, 229, 245, 255))
        if hov == ('all',):
            gfx.round_rect(canvas, ar, 3, fill=gfx.vgradient(ar[2] - ar[0], ar[3] - ar[1], ((0, (220, 235, 252)), (1, (193, 219, 252)))),
                           outline=(125, 162, 206, 255))
        cy = (ar[1] + ar[3]) // 2
        dd = ImageDraw.Draw(canvas)
        if self.start_all:
            dd.polygon([(ar[0] + 14, cy - 4), (ar[0] + 10, cy), (ar[0] + 14, cy + 4)], fill=(30, 60, 110, 255))
            gfx.draw_text(canvas, (ar[0] + 24, cy), 'Back', fnt, (0, 0, 0, 255), anchor='lm')
        else:
            dd.polygon([(ar[0] + 10, cy - 4), (ar[0] + 14, cy), (ar[0] + 10, cy + 4)], fill=(30, 60, 110, 255))
            gfx.draw_text(canvas, (ar[0] + 24, cy), 'All Programs', fnt, (0, 0, 0, 255), anchor='lm')
        # search box
        sr = L['search']
        gfx.round_rect(canvas, sr, 3, fill=(255, 255, 255, 255), outline=(60, 90, 120, 255))
        if q:
            gfx.draw_text(canvas, (sr[0] + 8, (sr[1] + sr[3]) // 2), q, fnt, (0, 0, 0, 255), anchor='lm')
            if self.caret_on:
                cx = sr[0] + 8 + gfx.text_width(fnt, q) + 1
                dd.line([(cx, sr[1] + 5), (cx, sr[3] - 5)], fill=(0, 0, 0, 255))
        else:
            gfx.draw_text(canvas, (sr[0] + 8, (sr[1] + sr[3]) // 2), 'Search programs', gfx.font(12, False, 'sans'),
                          (120, 130, 140, 255), anchor='lm')
        # magnifier
        mx0 = sr[2] - 20
        mcy = (sr[1] + sr[3]) // 2
        dd.ellipse((mx0, mcy - 6, mx0 + 9, mcy + 3), outline=(60, 90, 120, 255), width=2)
        dd.line([(mx0 + 8, mcy + 2), (mx0 + 12, mcy + 6)], fill=(60, 90, 120, 255), width=2)
        # right column links
        for label, act, r in L['links']:
            if hov == ('link', label):
                gfx.round_rect(canvas, r, 3, fill=gfx.vgradient(r[2] - r[0], r[3] - r[1], ((0, (255, 255, 255, 90)), (0.5, (255, 255, 255, 40)),
                                                                                       (0.51, (255, 255, 255, 15)), (1, (255, 255, 255, 50)))),
                               outline=(255, 255, 255, 140))
            gfx.shadow_text(canvas, (r[0] + 10, (r[1] + r[3]) // 2), label, fnt, (255, 255, 255, 255), (0, 0, 0, 200), anchor='lm')
        # user tile: the Neptune orb in a glass frame
        pr = L['pic']
        pw = pr[2] - pr[0]
        gfx.round_rect(canvas, pr, 6, fill=gfx.vgradient(pw, pw, ((0, (255, 255, 255, 200)), (1, (200, 220, 230, 200)))),
                       outline=(40, 60, 80, 255))
        gfx.round_rect(canvas, (pr[0] + 5, pr[1] + 5, pr[2] - 5, pr[3] - 5), 3,
                       fill=gfx.vgradient(pw - 10, pw - 10, ((0, (20, 110, 130)), (1, (5, 30, 70)))))
        canvas.alpha_composite(icons.get('neptune', 52), (pr[0] + 10, pr[1] + 10))
        gfx.shadow_text(canvas, ((x0 + 266 + x1 - 10) // 2 if False else pr[0] + pw // 2, pr[3] + 8),
                        self.lock_user(), gfx.font(13, True), (255, 255, 255, 255), (0, 0, 0, 220), anchor='ma')
        # power buttons
        for key, r, label in (('power', L['power'], 'Shut down'), ('power_more', L['power_more'], '')):
            hot = hov == (key,)
            stops = ((0, (255, 255, 255, 120 if hot else 60)), (0.5, (255, 255, 255, 60 if hot else 25)),
                     (0.51, (255, 255, 255, 30 if hot else 8)), (1, (255, 255, 255, 80 if hot else 30)))
            corners = (True, False, False, True) if key == 'power' else (False, True, True, False)
            gfx.round_rect(canvas, r, 3, fill=gfx.vgradient(r[2] - r[0], r[3] - r[1], stops), corners=corners)
            gfx.round_rect(canvas, r, 3, outline=(0, 0, 0, 160), corners=corners)
            cy = (r[1] + r[3]) // 2
            if label:
                gfx.shadow_text(canvas, ((r[0] + r[2]) // 2, cy), label, fnt, (255, 255, 255, 255), (0, 0, 0, 220), anchor='mm')
            else:
                cx = (r[0] + r[2]) // 2
                dd.polygon([(cx - 3, cy - 4), (cx + 2, cy), (cx - 3, cy + 4)], fill=(255, 255, 255, 255))

    def render_thumb(self, canvas):
        r = self.thumb_rect()
        w = self.thumb
        if not r or w is None:
            return
        x0, y0, x1, y1 = r
        sh, pad = gfx.shadow(x1 - x0, y1 - y0, 5, 8, 120)
        gfx.alpha_composite_clip(canvas, sh, x0 - pad, y0 - pad + 2)
        gfx.glass_panel(canvas, r, 5, gfx.mix(self.tint, (0, 0, 0), 0.3)[:3], 0.55, transparency=(self.settings['transparency'] and gfx.AERO))
        if self.thumb_hover:
            gfx.round_rect(canvas, (x0 + 3, y0 + 3, x1 - 3, y1 - 3), 4, fill=(255, 255, 255, 40), outline=(255, 255, 255, 140))
        canvas.alpha_composite(icons.get(w.app.icon, 16), (x0 + 8, y0 + 7))
        fnt = gfx.font(11)
        gfx.shadow_text(canvas, (x0 + 30, y0 + 15), gfx.ellipsize(fnt, w.app.title, x1 - x0 - 40), fnt,
                        (255, 255, 255, 255), (0, 0, 0, 220), anchor='lm')
        content = self.window_content(w)
        tw, th = x1 - x0 - 20, y1 - y0 - 40
        cw, ch = content.size
        s = min(tw / cw, th / ch)
        im = content.resize((max(1, int(cw * s)), max(1, int(ch * s))), Image.BILINEAR)
        px = x0 + (x1 - x0 - im.size[0]) // 2
        py = y0 + 30 + (th - im.size[1]) // 2
        sh2, p2 = gfx.shadow(im.size[0], im.size[1], 1, 4, 140)
        gfx.alpha_composite_clip(canvas, sh2, px - p2, py - p2 + 1)
        canvas.alpha_composite(im, (px, py))

    def render_alttab(self, canvas):
        a = self.alttab
        order = a['order']
        n = len(order)
        tw, th = 150, 120
        gap = 14
        total = n * tw + (n - 1) * gap + 40
        total = min(total, self.W - 40)
        per_row = max(1, (total - 40 + gap) // (tw + gap))
        rows = (n + per_row - 1) // per_row
        pw, ph = total, rows * (th + gap) + 60
        x0 = (self.W - pw) // 2
        y0 = (self.H - ph) // 2
        sh, pad = gfx.shadow(pw, ph, 8, 16, 160)
        gfx.alpha_composite_clip(canvas, sh, x0 - pad, y0 - pad + 4)
        gfx.glass_panel(canvas, (x0, y0, x0 + pw, y0 + ph), 8, gfx.mix(self.tint, (0, 0, 0), 0.3)[:3], 0.6,
                        transparency=(self.settings['transparency'] and gfx.AERO), blur_radius=16)
        sel = order[a['i'] % n]
        fnt = gfx.font(13)
        gfx.shadow_text(canvas, (x0 + pw // 2, y0 + 22), gfx.ellipsize(fnt, sel.app.title, pw - 40), fnt,
                        (255, 255, 255, 255), (0, 0, 0, 230), anchor='mm')
        for i, w in enumerate(order):
            rx = x0 + 20 + (i % per_row) * (tw + gap)
            ry = y0 + 44 + (i // per_row) * (th + gap)
            if w is sel:
                gfx.round_rect(canvas, (rx - 6, ry - 6, rx + tw + 6, ry + th + 6), 5,
                               fill=(255, 255, 255, 60), outline=(255, 255, 255, 180))
            content = self.window_content(w)
            cw, ch = content.size
            s = min(tw / cw, (th - 10) / ch)
            im = content.resize((max(1, int(cw * s)), max(1, int(ch * s))), Image.BILINEAR)
            px, py = rx + (tw - im.size[0]) // 2, ry + (th - 10 - im.size[1]) // 2
            canvas.alpha_composite(im, (px, py))
            ImageDraw.Draw(canvas).rectangle((px - 1, py - 1, px + im.size[0], py + im.size[1]), outline=(0, 0, 0, 160))
            canvas.alpha_composite(icons.get(w.app.icon, 32), (rx + tw - 30, ry + th - 34))

    def render_balloons(self, canvas, now):
        y = self.H - TASKBAR_H - 10
        for b in reversed(self.balloons):
            t = now - b['t0']
            a = gfx.clamp(min(t / 0.3, (b['dur'] + 0.4 - t) / 0.4), 0, 1)
            if a <= 0:
                continue
            fnt_t, fnt = gfx.font(12, True), gfx.font(12)
            words = b['text']
            lines = []
            cur = ''
            for wd in words.split():
                tt = (cur + ' ' + wd) if cur else wd
                if gfx.text_width(fnt, tt) > 240 and cur:
                    lines.append(cur)
                    cur = wd
                else:
                    cur = tt
            lines.append(cur)
            bw, bh = 320, 40 + 17 * len(lines)
            x0 = self.W - bw - 14
            y0 = y - bh
            layer = Image.new('RGBA', (bw + 40, bh + 40), (0, 0, 0, 0))
            sh, pad = gfx.shadow(bw, bh, 5, 8, 130)
            panel = gfx.glass_layer(canvas, (x0, y0, x0 + bw, y0 + bh), 5, (250, 252, 255), 0.82,
                                    transparency=(self.settings['transparency'] and gfx.AERO), highlight=False, border=False)
            gfx.fill_mask(panel, gfx.rounded_outline(bw, bh, 5), (110, 120, 130, 255))
            panel.alpha_composite(gfx.vgradient(bw, bh, ((0, (255, 255, 255, 120)), (1, (220, 230, 240, 60)))), (0, 0))
            panel.putalpha(gfx.rounded_mask(bw, bh, 5))
            gfx.fill_mask(panel, gfx.rounded_outline(bw, bh, 5), (100, 110, 125, 255))
            panel.alpha_composite(icons.get(b['icon'], 32), (10, 10))
            d = ImageDraw.Draw(panel)
            d.text((52, 12), b['title'], font=fnt_t, fill=(0, 51, 153, 255))
            for i, ln in enumerate(lines):
                d.text((52, 32 + i * 17), ln, font=fnt, fill=(30, 30, 30, 255))
            gfx.alpha_composite_clip(canvas, gfx.with_alpha(sh, a), x0 - pad, y0 - pad + 3)
            off = int((1 - a) * 20)
            gfx.alpha_composite_clip(canvas, gfx.with_alpha(panel, a), x0 + off, y0)
            y = y0 - 8

    def render_snap_preview(self, canvas, now):
        r = self.snap_preview
        t = gfx.ease_out((now - getattr(self, 'snap_preview_t', now)) / 0.18)
        mx, my = self.mouse
        x0 = int(mx + (r[0] - mx) * t)
        y0 = int(my + (r[1] - my) * t)
        x1 = int(mx + (r[2] - mx) * t)
        y1 = int(my + (r[3] - my) * t)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return
        box = (x0 + 6, y0 + 6, x1 - 6, y1 - 6)
        gfx.glass_panel(canvas, box, 6, gfx.mix(self.tint, (255, 255, 255), 0.4)[:3], 0.25,
                        transparency=(self.settings['transparency'] and gfx.AERO), blur_radius=6)

    # -- phases -----------------------------------------------------------------------
    def render_boot(self, now):
        W, H = self.W, self.H
        img = Image.new('RGBA', (W, H), (0, 0, 0, 255))
        t = now - self.phase_t
        fade = gfx.clamp(t / 0.8, 0, 1) * gfx.clamp((3.2 - t) / 0.4, 0, 1)
        cx, cy = W // 2, int(H * 0.40)
        # pulsing glow
        pulse = 0.65 + 0.35 * math.sin(t * 2.6)
        gs = 360
        glow = gfx.radial(gs, gs, ((0, (60, 220, 230, int(150 * pulse * fade))), (0.5, (20, 120, 170, int(50 * fade))),
                                   (1, (0, 0, 0, 0))))
        img.alpha_composite(glow, (cx - gs // 2, cy - gs // 2))
        orb = icons.get('neptune', 128)
        img.alpha_composite(gfx.with_alpha(orb, fade), (cx - 64, cy - 64))
        fnt = gfx.font(34, False, 'serif')
        layer = Image.new('RGBA', (W, 120), (0, 0, 0, 0))
        gfx.shadow_text(layer, (cx, 40), 'COS  NEPTUNE', fnt, (235, 250, 255, 255), (60, 200, 220, 200), radius=6,
                        anchor='mm', offset=(0, 0))
        img.alpha_composite(gfx.with_alpha(layer, fade), (0, cy + 70))
        # Vista-style sweeping progress lights
        bw, bh = 200, 10
        bx, by = cx - bw // 2, cy + 190
        gfx.round_rect(img, (bx, by, bx + bw, by + bh), 5, fill=(255, 255, 255, int(25 * fade)),
                       outline=(120, 160, 180, int(160 * fade)))
        for k in range(3):
            p = ((t * 0.55 + k * 0.08) % 1.3) - 0.15
            px = bx + int(p * bw)
            seg = gfx.radial(40, 10, ((0, (120, 255, 240, int(255 * fade))), (1, (0, 100, 140, 0))))
            mask = Image.new('L', (bw, bh), 0)
            mask.paste(gfx.rounded_mask(bw, bh, 5))
            lay = Image.new('RGBA', (bw, bh), (0, 0, 0, 0))
            gfx.alpha_composite_clip(lay, seg, px - bx - 20, 0)
            gfx.paste_masked(img, lay, mask, (bx, by))
        f2 = gfx.font(11)
        gfx.draw_text(img, (cx, H - 40), '%s  ·  Build %s  ·  Graphics Desktop Rendering' % (theme.FULLNAME, theme.BUILD), f2,
                      (120, 150, 160, int(255 * fade)), anchor='mm')
        return img

    def _welcome_bg(self):
        if getattr(self, '_welcome_cache', None) is None or self._welcome_cache.size != (self.W, self.H):
            bg = self.wall().filter(ImageFilter.GaussianBlur(14))
            bg.alpha_composite(Image.new('RGBA', bg.size, (0, 10, 30, 120)))
            self._welcome_cache = bg
        return self._welcome_cache

    def render_spinner(self, img, cx, cy, now, size=32):
        sp = icons.busy_cursor((now * 1.1) % 1.0).resize((size, size), Image.LANCZOS)
        img.alpha_composite(sp, (cx - size // 2, cy - size // 2))

    def render_welcome(self, now):
        img = self._welcome_bg().copy()
        t = now - self.phase_t
        a = gfx.clamp(t / 0.4, 0, 1)
        fnt = gfx.font(40, False, 'light')
        layer = Image.new('RGBA', (self.W, self.H), (0, 0, 0, 0))
        msg = getattr(self, 'welcome_text', None) or 'Welcome'
        tw = gfx.text_width(fnt, msg)
        cx, cy = self.W // 2, self.H // 2
        gfx.shadow_text(layer, (cx + 22, cy), msg, fnt, (255, 255, 255, 255), (0, 0, 0, 160), radius=4, anchor='mm')
        self.render_spinner(layer, cx - tw // 2 - 10, cy + 2, now, 34)
        img.alpha_composite(gfx.with_alpha(layer, a))
        return img

    def render_shutdown(self, now):
        if getattr(self, '_shutdown_bg', None) is None:
            base = self.scene if self.scene is not None else self.wall()
            bg = base.filter(ImageFilter.GaussianBlur(10))
            self._shutdown_bg = bg
        t = now - self.phase_t
        img = self._shutdown_bg.copy()
        img.alpha_composite(Image.new('RGBA', img.size, (0, 10, 30, int(gfx.clamp(t / 0.5, 0, 1) * 140))))
        msg = {'restart': 'Restarting...', 'exit': 'Shutting down...'}.get(getattr(self, 'exit_request', 'exit'),
                                                                          'Shutting down...')
        fnt = gfx.font(30, False, 'light')
        cx, cy = self.W // 2, self.H // 2
        tw = gfx.text_width(fnt, msg)
        gfx.shadow_text(img, (cx + 20, cy), msg, fnt, (255, 255, 255, 255), (0, 0, 0, 160), radius=4, anchor='mm')
        self.render_spinner(img, cx - tw // 2 - 8, cy + 2, now, 30)
        if t > 1.2:
            img.alpha_composite(Image.new('RGBA', img.size, (0, 0, 0, int(gfx.clamp((t - 1.2) / 0.6, 0, 1) * 255))))
        return img

    # ======================================================================
    # lock / login screen (COS v2.4's login screen, Aero style: it locks the
    # look of the desktop, it is not a security boundary)
    # ======================================================================
    # -- Set Up COS Neptune (OOBE) ------------------------------------------------------
    def start_oobe(self):
        from .apps.oobe import OOBE
        self.close_popups()
        self.drag = self.capture = self.alttab = None
        app = OOBE(self)
        w, h = app.size
        win = Window(app, (self.W - w) // 2, (self.H - h) // 2, w, h)
        app.win = win
        self.oobe_win = win
        self._oobe_prev_active = self.active
        self.active = win
        self.phase = 'oobe'
        self.phase_t = time.time()
        self.cursor_kind = 'arrow'
        self.sound('boot')
        self.dirty = True

    def finish_oobe(self, notes=()):
        self.oobe_win = None
        self.active = None
        self.welcome_text = 'Preparing your desktop...'
        self.phase = 'welcome'
        self.phase_t = time.time()
        self._welcome_cache = None
        self.desk_dirty = True
        self.dirty = True
        if notes:
            self._oobe_notes = list(notes)

    def oobe_event(self, ev):
        w = self.oobe_win
        if w is None:
            return
        t = ev['type']
        if t == 'key' and ev.get('ctrl') and ev.get('alt') and ev['key'] == 'backspace':
            self.shutdown('exit')
            return
        cx0, cy0, cx1, cy1 = w.client_rect()
        if t in ('move', 'down', 'up'):
            mx, my = ev['x'], ev['y']
            self.mouse = (mx, my)
            inside = cx0 <= mx < cx1 and cy0 <= my < cy1
            if t == 'move':
                if inside or getattr(self, '_oobe_capture', False):
                    self.safe(w, w.app.mouse, 'move', mx - cx0, my - cy0)
                    self.set_cursor(self.safe(w, w.app.cursor_at, mx - cx0, my - cy0) or 'arrow')
                else:
                    w.app.mouse('leave', -1, -1)
                    self.set_cursor('arrow')
            elif t == 'down' and inside:
                clicks = self._click_count(mx, my, ('oobe',))
                self._oobe_capture = True
                self.safe(w, w.app.mouse, 'down', mx - cx0, my - cy0, ev.get('button', 1), clicks)
            elif t == 'up' and getattr(self, '_oobe_capture', False):
                self._oobe_capture = False
                self.safe(w, w.app.mouse, 'up', mx - cx0, my - cy0, ev.get('button', 1))
        elif t == 'wheel':
            self.safe(w, w.app.on_wheel, ev['dy'], self.mouse[0] - cx0, self.mouse[1] - cy0)
        elif t == 'key':
            self.safe(w, w.app.key, ev)
        if self.oobe_win is not None:
            w.content_dirty = True
        self.dirty = True

    def render_oobe(self, now):
        key = (json.dumps(self.wall_spec(), sort_keys=True), self.W, self.H)
        if getattr(self, '_oobe_bg_key', None) != key:
            bg = self.wall().filter(ImageFilter.GaussianBlur(10))
            bg.alpha_composite(gfx.vgradient(self.W, self.H, ((0, (0, 10, 30, 70)), (1, (0, 10, 30, 150)))))
            self._oobe_bg, self._oobe_bg_key = bg, key
        canvas = self._oobe_bg.copy()
        a = gfx.clamp((now - self.phase_t) / 0.5, 0, 1)
        canvas.alpha_composite(icons.get('neptune', 44), (24, 20))
        gfx.shadow_text(canvas, (78, 42), 'COS Neptune', gfx.font(24, False, 'serif'), (235, 250, 255, 255),
                        (0, 0, 0, 180), radius=3, anchor='lm')
        w = self.oobe_win
        if w is not None:
            w.x, w.y = (self.W - w.w) // 2, (self.H - w.h) // 2
            sh, pad = gfx.shadow(w.w, w.h, RADIUS, 18, 170)
            gfx.alpha_composite_clip(canvas, sh, w.x - pad, w.y - pad + 6)
            if a < 1:
                layer = self.render_window_layer(canvas, w, w.rect, True)
                gfx.alpha_composite_clip(canvas, gfx.with_alpha(layer, a), w.x, w.y)
            else:
                self.compose_window(canvas, w, True)
        gfx.shadow_text(canvas, (self.W // 2, self.H - 24), 'Ctrl+Alt+Backspace exits', gfx.font(11),
                        (200, 220, 230, 255), (0, 0, 0, 160), anchor='mm')
        if a < 1:
            self.dirty = True
        return canvas

    def enter_desktop(self):
        if self.settings.get('login_screen') and not getattr(self, '_logged_in', False):
            self.lock()
            return
        self._logged_in = True
        self.phase = 'desktop'
        self.fade_in_t = time.time()
        self.welcome_text = None
        if self.settings.get('cosplace_join') and not getattr(self, '_cosplace_started', False):
            self._cosplace_started = True
            try:
                from .apps.cosplace_app import ensure_running
                ensure_running(self)
            except Exception:
                pass
        if self.settings.get('daily_tip', True) and not getattr(self, '_tipped', False):
            from . import fun
            self._tipped = True
            self.notify('Tip of the day', fun.tip_of_the_day(), 'info', 10)
        if getattr(self, '_oobe_notes', None):
            self.notify('Welcome to COS Neptune', 'Brought over from COS v2.4: %s.' % ', '.join(self._oobe_notes),
                        'neptune', 8)
            self._oobe_notes = None
        self.dirty = True

    # ======================================================================
    # screensaver
    # ======================================================================
    def saver_check(self, now):
        """Start the screensaver once nothing has happened for the chosen time."""
        if self.phase not in ('desktop', 'lock') or self.saver is not None:
            return
        name = self.settings.get('screensaver', 'Bubbles')
        wait = float(self.settings.get('saver_wait', 10) or 0) * 60
        if not name or name == '(None)' or wait <= 0 or now - self.last_input < wait:
            return
        if any(getattr(w.app, 'inhibit_saver', False) for w in self.windows if not w.minimized):
            self.last_input = now                  # a video is playing: wait another full period
            return
        self.start_saver()

    def start_saver(self, preview=False, name=None):
        from . import savers
        self.close_popups()
        self.drag = None
        self.saver_prev = self.phase if self.phase in ('desktop', 'lock') else 'desktop'
        self.saver = savers.make(name or self.settings.get('screensaver', 'Bubbles'), self.W, self.H,
                                 self.settings.tint)
        self.saver_t0 = time.time()
        self.saver_mouse = self.mouse
        self.saver_preview = preview
        self.phase = 'saver'
        self.dirty = True

    def saver_event(self, ev):
        now = time.time()
        if now - self.saver_t0 < 0.8:
            return                                  # the click that started a preview, or a twitch
        if ev['type'] == 'move':
            mx, my = self.saver_mouse
            if abs(ev['x'] - mx) + abs(ev['y'] - my) < 12:
                return                              # a nudged desk is not someone coming back
            self.mouse = (ev['x'], ev['y'])
        self.stop_saver()

    def stop_saver(self):
        back = self.saver_prev
        self.saver = None
        self.last_input = time.time()
        self.phase = back
        self.desk_dirty = True
        for w in self.windows:
            w.frame_dirty = w.content_dirty = True
        self.dirty = True
        if back == 'desktop' and self.settings.get('saver_lock') and not self.saver_preview:
            self.lock()
        elif back == 'desktop':
            self.fade_in_t = time.time()

    def lock(self):
        self.close_popups()
        self.drag = None
        self.capture = None
        self.alttab = None
        self.phase = 'lock'
        self.lock_t = time.time()
        self.lock_pw = {'text': '', 'cur': 0}
        self.lock_msg = ''
        self.cursor_kind = 'arrow'
        self.dirty = True

    def unlock(self):
        pw = self.settings.get('lock_password', '')
        if pw and self.lock_pw['text'] != pw:
            self.lock_msg = 'The password is incorrect. Try again.'
            self.lock_pw = {'text': '', 'cur': 0}
            self.lock_shake = time.time()
            self.sound('warn')
            self.dirty = True
            return
        self._logged_in = True
        self.phase = 'desktop'
        self.fade_in_t = time.time()
        self.desk_dirty = True
        for w in self.windows:
            w.frame_dirty = True
            w.content_dirty = True
        self.dirty = True

    def lock_user(self):
        if self.settings.get('user_name'):
            return self.settings['user_name']
        import pwd
        try:
            e = pwd.getpwuid(os.getuid())
            return (e.pw_gecos.split(',')[0] or e.pw_name).strip() or e.pw_name
        except (KeyError, ImportError):
            return os.environ.get('USER', 'User')

    def lock_layout(self):
        cx, cy = self.W // 2, int(self.H * 0.42)
        return {
            'tile': (cx - 70, cy - 150, cx + 70, cy - 10),
            'field': (cx - 120, cy + 44, cx + 92, cy + 72),
            'go': (cx + 98, cy + 44, cx + 126, cy + 72),
        }

    def lock_event(self, ev):
        t = ev['type']
        L = self.lock_layout()
        has_pw = bool(self.settings.get('lock_password', ''))
        if t == 'move':
            self.mouse = (ev['x'], ev['y'])
            hov = None
            L['go'] = getattr(self, '_lock_go', L['go'])
            for k in ('go', 'tile', 'field'):
                if gfx.in_rect(ev['x'], ev['y'], L[k]):
                    hov = k
                    break
            kind = 'ibeam' if hov == 'field' and has_pw else ('hand' if hov in ('go', 'tile') else 'arrow')
            if hov != self.lock_hover:
                self.lock_hover = hov
                self.dirty = True
            self.cursor_kind = kind if kind in ('ibeam', 'arrow') else 'arrow'
        elif t == 'down':
            x, y = ev['x'], ev['y']
            go = getattr(self, '_lock_go', L['go'])
            if gfx.in_rect(x, y, go) or (not has_pw and gfx.in_rect(x, y, L['tile'])):
                self.unlock()
        elif t == 'key':
            k = ev['key']
            st = self.lock_pw
            if k == 'enter':
                self.unlock()
            elif not has_pw:
                if k == 'space':
                    self.unlock()
            elif k == 'backspace':
                st['text'] = st['text'][:-1]
            elif k == 'escape':
                st['text'] = ''
                self.lock_msg = ''
            elif ev.get('char') and not ev.get('ctrl') and len(st['text']) < 64:
                st['text'] += ev['char']
                self.lock_msg = ''
            st['cur'] = len(st['text'])
            self.caret_on = True
            self.dirty = True

    def render_lock(self, now):
        img = self._welcome_bg().copy()
        W, H = self.W, self.H
        a = gfx.clamp((now - self.lock_t) / 0.35, 0, 1)
        layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        L = self.lock_layout()
        tint = self.settings.tint
        has_pw = bool(self.settings.get('lock_password', ''))
        # user tile: glass frame around a picture
        tx0, ty0, tx1, ty1 = L['tile']
        hot = self.lock_hover == 'tile' and not has_pw
        gfx.round_rect(layer, (tx0 - 4, ty0 - 4, tx1 + 4, ty1 + 4), 10,
                       fill=gfx.vgradient(tx1 - tx0 + 8, ty1 - ty0 + 8,
                                          ((0, (255, 255, 255, 150 if hot else 110)), (1, (255, 255, 255, 50)))))
        gfx.round_rect(layer, (tx0 - 4, ty0 - 4, tx1 + 4, ty1 + 4), 10, outline=(255, 255, 255, 170))
        pic = gfx.vgradient(tx1 - tx0, ty1 - ty0, ((0, gfx.mix(tint, (255, 255, 255), 0.35)), (1, gfx.mix(tint, (0, 0, 0), 0.5))))
        mask = gfx.rounded_mask(tx1 - tx0, ty1 - ty0, 6)
        gfx.paste_masked(layer, pic, mask, (tx0, ty0))
        orb = icons.get('neptune', 96)
        layer.alpha_composite(orb, ((tx0 + tx1) // 2 - 48, (ty0 + ty1) // 2 - 48))
        gl = gfx.vgradient(tx1 - tx0, (ty1 - ty0) // 2, ((0, (255, 255, 255, 90)), (1, (255, 255, 255, 8))))
        gfx.paste_masked(layer, gl, gfx.rounded_mask(tx1 - tx0, (ty1 - ty0) // 2, 6, (True, True, False, False)), (tx0, ty0))
        # name
        cx = W // 2
        fnt = gfx.font(26, False, 'light')
        gfx.shadow_text(layer, (cx, ty1 + 28), self.lock_user(), fnt, (255, 255, 255, 255), (0, 0, 0, 170), radius=4, anchor='mm')
        # password field or "Locked"
        fx0, fy0, fx1, fy1 = L['field']
        shake = 0
        if now - self.lock_shake < 0.5:
            shake = int(math.sin((now - self.lock_shake) * 50) * 10 * (1 - (now - self.lock_shake) / 0.5))
        if has_pw:
            box = (fx0 + shake, fy0, fx1 + shake, fy1)
            gfx.round_rect(layer, box, 3, fill=(255, 255, 255, 235), outline=(60, 110, 160, 255))
            txt = self.lock_pw['text']
            f2 = gfx.font(13)
            if txt:
                dots = '●' * len(txt)
                gfx.draw_text(layer, (box[0] + 8, (fy0 + fy1) // 2), dots, gfx.font_for(dots, 11), (20, 20, 20, 255), anchor='lm')
                cxp = box[0] + 8 + gfx.text_width(gfx.font_for(dots, 11), dots) + 1
            else:
                gfx.draw_text(layer, (box[0] + 8, (fy0 + fy1) // 2), 'Password', f2, (130, 135, 140, 255), anchor='lm')
                cxp = box[0] + 8
            if self.caret_on:
                ImageDraw.Draw(layer).line([(cxp, fy0 + 6), (cxp, fy1 - 7)], fill=(0, 0, 0, 255))
        else:
            gfx.shadow_text(layer, (cx - 14, (fy0 + fy1) // 2), 'Locked', gfx.font(14), (225, 240, 250, 255),
                            (0, 0, 0, 150), radius=3, anchor='mm')
        # round glass arrow button
        gx0, gy0, gx1, gy1 = L['go']
        if not has_pw:
            gx0, gx1 = cx + 30, cx + 58
        gx0 += shake
        gx1 += shake
        self._lock_go = (gx0, gy0, gx1, gy1)
        hot = self.lock_hover == 'go'
        top, bot = ((120, 200, 255), (20, 90, 180)) if hot else ((90, 160, 220), (20, 60, 130))
        r = (gx1 - gx0) // 2
        m = Image.new('L', (r * 2, r * 2), 0)
        ImageDraw.Draw(m).ellipse((0, 0, r * 2 - 1, r * 2 - 1), fill=255)
        gfx.paste_masked(layer, gfx.vgradient(r * 2, r * 2, ((0, top), (1, bot))), m, (gx0, gy0))
        d = ImageDraw.Draw(layer)
        d.ellipse((gx0, gy0, gx0 + r * 2 - 1, gy0 + r * 2 - 1), outline=(255, 255, 255, 200))
        mx, my = gx0 + r, gy0 + r
        d.line([(mx - 6, my), (mx + 6, my)], fill=(255, 255, 255, 255), width=3)
        d.line([(mx + 1, my - 6), (mx + 7, my), (mx + 1, my + 6)], fill=(255, 255, 255, 255), width=3)
        if self.lock_msg:
            gfx.shadow_text(layer, (cx, fy1 + 26), self.lock_msg, gfx.font(13), (255, 220, 210, 255), (0, 0, 0, 180),
                            radius=3, anchor='mm')
        elif not has_pw:
            gfx.shadow_text(layer, (cx, fy1 + 26), 'Click the arrow or press Enter to unlock', gfx.font(12),
                            (200, 220, 235, 255), (0, 0, 0, 160), radius=3, anchor='mm')
        # clock, bottom left
        tm = time.localtime()
        clock = time.strftime('%H:%M' if self.settings.get('clock_24h') else '%I:%M', tm).lstrip('0') or '0'
        f3 = gfx.font(64, False, 'light')
        gfx.shadow_text(layer, (48, H - 150), clock, f3, (255, 255, 255, 255), (0, 0, 0, 150), radius=5)
        date = time.strftime('%A, %B ', tm) + str(tm.tm_mday)
        gfx.shadow_text(layer, (52, H - 70), date, gfx.font(20, False, 'light'), (230, 240, 250, 255), (0, 0, 0, 150),
                        radius=4)
        # brand, bottom centre
        layer.alpha_composite(icons.get('neptune', 28), (cx - 80, H - 52))
        gfx.shadow_text(layer, (cx - 44, H - 38), 'COS  Neptune', gfx.font(18, False, 'serif'), (235, 250, 255, 230),
                        (40, 180, 210, 160), radius=5, anchor='lm', offset=(0, 0))
        img.alpha_composite(gfx.with_alpha(layer, a) if a < 1 else layer)
        return img

    # -- emergency exit (Ctrl+Shift+Alt+Q): a prompt drawn over whatever is on screen -------
    def cursor_image(self, kind):
        """The pointer in the chosen scheme: Aero, Aero (large), or Midnight (black)."""
        scheme = self.settings.get('cursor_scheme', 'Aero')
        key = (kind, scheme)
        cache = self.__dict__.setdefault('_cursor_cache', {})
        if key not in cache:
            cur, hot = icons.cursor(kind)
            if scheme == 'Aero (large)':
                cur = cur.resize((int(cur.size[0] * 1.6), int(cur.size[1] * 1.6)), Image.LANCZOS)
                hot = (int(hot[0] * 1.6), int(hot[1] * 1.6))
            elif scheme == 'Midnight (black)':
                import numpy as np
                a = np.asarray(cur).copy()
                rgb = a[..., :3].astype(np.int16)
                a[..., :3] = (255 - rgb).astype(np.uint8)           # white body, black edge -> black body, white edge
                cur = Image.fromarray(a, 'RGBA')
            cache[key] = (cur, hot)
        return cache[key]

    def emergency_rects(self):
        cx, cy = self.W // 2, self.H // 2
        box = (cx - 230, cy - 90, cx + 230, cy + 90)
        return box, (cx + 20, cy + 40, cx + 120, cy + 72), (cx + 130, cy + 40, cx + 220, cy + 72)

    def emergency_event(self, ev):
        t = ev['type']
        box, yes, no = self.emergency_rects()
        if t == 'key':
            k = ev.get('key')
            if k in ('y', 'enter'):
                self.emergency = None
                self.exit_code = 'exit'          # straight out: no shutdown animation
            elif k in ('n', 'escape'):
                self.emergency = None
                self.last_input = time.time()
        elif t == 'move':
            self.mouse = (ev['x'], ev['y'])
            hot = 'yes' if gfx.in_rect(ev['x'], ev['y'], yes) else ('no' if gfx.in_rect(ev['x'], ev['y'], no) else None)
            if hot != self.emergency.get('hot'):
                self.emergency['hot'] = hot
        elif t == 'down':
            if gfx.in_rect(ev['x'], ev['y'], yes):
                self.emergency = None
                self.exit_code = 'exit'
            elif gfx.in_rect(ev['x'], ev['y'], no):
                self.emergency = None
                self.last_input = time.time()
        self.dirty = True

    def render_emergency(self, img):
        box, yes, no = self.emergency_rects()
        dim = Image.new('RGBA', img.size, (0, 0, 0, 140))
        img.alpha_composite(dim)
        x0, y0, x1, y1 = box
        inner = (x0 + 8, y0 + 34, x1 - 8, y1 - 8)
        gfx.round_rect(img, (x0, y0, x1, y1), 10, fill=(60, 20, 24, 235), outline=(230, 120, 120, 255))
        gfx.round_rect(img, inner, 6, fill=(250, 250, 252, 255), outline=(200, 170, 170, 255))
        d = ImageDraw.Draw(img)
        d.text((x0 + 16, y0 + 17), 'Emergency exit', font=gfx.font(13, True), fill=(255, 235, 235, 255), anchor='lm')
        img.alpha_composite(icons.get('warning', 32), (inner[0] + 14, inner[1] + 16))
        d.text((inner[0] + 60, inner[1] + 18), 'Exit COS Neptune now?', font=gfx.font(15, True), fill=(20, 20, 20, 255))
        d.text((inner[0] + 60, inner[1] + 42), 'Unsaved work in open windows will be lost.', font=gfx.font(11),
               fill=(90, 90, 90, 255))
        for rect, label, key in ((yes, 'Yes (Y)', 'yes'), (no, 'No (N)', 'no')):
            hot = self.emergency.get('hot') == key
            top, bot = ((234, 246, 253), (167, 217, 245)) if hot else ((242, 242, 242), (207, 207, 207))
            gfx.round_rect(img, rect, 3, fill=gfx.vgradient(rect[2] - rect[0], rect[3] - rect[1], ((0, top), (1, bot))),
                           outline=(60, 127, 177, 255) if hot or key == 'no' else (112, 112, 112, 255))
            d.text(((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2), label, font=gfx.font(12),
                   fill=(20, 20, 20, 255), anchor='mm')

    def render_scene(self, now):
        img = self._render_scene(now)
        if self.emergency is not None:
            img = img.copy() if img is getattr(self, 'desk_layer', None) or img is getattr(self, '_wall', None) else img
            self.render_emergency(img)
        return img

    def _render_scene(self, now):
        if self.phase == 'oobe':
            return self.render_oobe(now)
        if self.phase == 'boot':
            return self.render_boot(now)
        if self.phase == 'welcome':
            return self.render_welcome(now)
        if self.phase == 'shutdown':
            return self.render_shutdown(now)
        if self.phase == 'saver' and self.saver is not None:
            return self.saver.frame(now)
        if self.phase == 'lock':
            return self.render_lock(now)
        if self.desk_layer is None or self.desk_dirty:
            self.desk_layer = self.render_desk()
            self.desk_dirty = False
        canvas = self.desk_layer.copy()
        peek_all = self.peek
        for w in self.windows:
            if w.minimized and not w.anim:
                continue
            ghost = peek_all or (self.peek_window is not None and w is not self.peek_window)
            self.draw_window(canvas, w, now, ghost=ghost)
        if self.snap_preview is not None:
            self.render_snap_preview(canvas, now)
        self.render_menubar(canvas)
        self.render_taskbar(canvas, now)
        if self.settings.get('desktop_pet', True) and not self.start_open:
            self.pet.draw(canvas, now)
        if self.confetti is not None:
            if self.confetti.done:
                self.confetti = None
            else:
                self.confetti.draw(canvas, now)
        if self.thumb is not None:
            self.render_thumb(canvas)
        if self.start_open:
            self.render_start(canvas)
        for m in self.menus:
            self.render_menu(canvas, m)
        if self.alttab is not None:
            self.render_alttab(canvas)
        if self.balloons:
            self.render_balloons(canvas, now)
        if self.fade_in_t is not None:
            a = 1 - gfx.clamp((now - self.fade_in_t) / 0.6, 0, 1)
            if a > 0:
                over = self._welcome_bg() if hasattr(self, '_welcome_cache') and self._welcome_cache is not None else None
                if over is not None:
                    canvas.alpha_composite(gfx.with_alpha(over, a))
                else:
                    canvas.alpha_composite(Image.new('RGBA', canvas.size, (0, 0, 0, int(255 * a))))
        return canvas

    def frame(self, now):
        """(image, dirty_rect) to present, or None when nothing changed."""
        full = False
        if self.dirty or self.scene is None:
            self.dirty = False
            t0 = time.time()
            self.scene = self.render_scene(now)
            self.frame_times = (self.frame_times + [time.time() - t0])[-30:]
            if self.shot_request is not None:
                self.take_screenshot(self.shot_request)
                self.shot_request = None
            self.out = self.scene.copy()
            if self.shot_flash and now - self.shot_flash < 0.18:
                a = int(170 * (1 - (now - self.shot_flash) / 0.18))
                self.out.alpha_composite(Image.new('RGBA', self.out.size, (255, 255, 255, a)))
                self.dirty = True
            full = True
        if self.phase in ('boot', 'shutdown', 'saver') and self.emergency is None:
            if full:
                return self.out, None
            return None
        kind = self.cursor_kind
        if self.drag and self.drag['kind'] == 'move':
            kind = 'arrow'
        cur, hot = self.cursor_image(kind)
        mx, my = self.mouse
        box = (mx - hot[0], my - hot[1], mx - hot[0] + cur.size[0], my - hot[1] + cur.size[1])
        sig = (box, kind)
        if full:
            gfx.alpha_composite_clip(self.out, cur, box[0], box[1])
            self.cursor_drawn = sig
            return self.out, None
        if sig == self.cursor_drawn:
            return None
        old = self.cursor_drawn[0] if self.cursor_drawn else box
        u = gfx.rect_union(old, box)
        u = (max(0, u[0]), max(0, u[1]), min(self.W, u[2]), min(self.H, u[3]))
        if u[2] > u[0] and u[3] > u[1]:
            self.out.paste(self.scene.crop(u), (u[0], u[1]))
        gfx.alpha_composite_clip(self.out, cur, box[0], box[1])
        self.cursor_drawn = sig
        return self.out, u
