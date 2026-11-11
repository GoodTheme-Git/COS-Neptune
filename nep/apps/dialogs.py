"""Dialogs and small system programs."""
import calendar as _cal
import os
import platform
import shlex
import subprocess
import sys
import time

from PIL import Image, ImageDraw

from .. import gfx, icons, theme
from ..app import App


# ---------------------------------------------------------------------------
class MessageBox(App):
    dialog = True
    resizable = False
    icon = 'info'

    def __init__(self, shell, title='Neptune', text='', kind='info', buttons=('OK',), callback=None, **kw):
        super().__init__(shell, **kw)
        self.title = title
        self.text = text
        self.kind = kind
        self.icon = kind
        self.buttons = buttons
        self.callback = callback
        fnt = gfx.font(12)
        lines = 0
        for para in text.split('\n'):
            lines += max(1, gfx.text_width(fnt, para) // 300 + 1)
        w = max(320, min(460, gfx.text_width(fnt, max(text.split('\n'), key=len)) + 110))
        self.size = (w, 120 + lines * 18)

    def draw(self, img, ui):
        w, h = img.size
        ui.icon(self.kind, (20, 20), 32)
        ui.wrap((66, 26), self.text, w - 90, 12)
        ui.rect((0, h - 42, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - 42, (223, 223, 223))
        x = w - 12
        for i, b in reversed(list(enumerate(self.buttons))):
            bw = max(75, gfx.text_width(gfx.font(12), b) + 24)
            ui.button((x - bw, h - 32, x, h - 9), b, ('btn', b), default=(i == 0))
            x -= bw + 8

    def on_command(self, cid, data):
        if cid[0] == 'btn':
            self.result(cid[1])

    def result(self, r):
        self.close()
        if self.callback:
            self.callback(r)

    def on_key(self, ev):
        if ev['key'] == 'enter':
            self.result(self.buttons[0])
            return True
        if ev['key'] == 'escape':
            self.result(self.buttons[-1])
            return True
        return False


def message(shell, title, text, kind='info', buttons=('OK',), callback=None, parent=None):
    app = MessageBox(shell, title=title, text=text, kind=kind, buttons=buttons, callback=callback)
    shell.open_window(app, parent=parent, center=True)
    shell.sound('warn' if kind in ('warning', 'error') else 'notify')
    return app


# ---------------------------------------------------------------------------
class FileDialog(App):
    """Open / Save As. Writes are confined to the Neptune home folder."""
    dialog = True
    resizable = False
    size = (560, 400)
    icon = 'folder'

    def __init__(self, shell, mode='open', start=None, filename='', exts=None, callback=None, **kw):
        super().__init__(shell, **kw)
        self.mode = mode
        self.title = 'Save As' if mode == 'save' else 'Open'
        self.path = start or os.path.join(theme.HOME, 'Documents')
        self.name = {'text': filename, 'cur': len(filename)}
        self.exts = exts
        self.callback = callback
        self.sel = None
        self.scroll = 0
        self.ui.focus = 'name'
        self.refresh()

    def refresh(self):
        try:
            names = sorted(os.listdir(self.path), key=str.lower)
        except OSError:
            names = []
        items = []
        for n in names:
            if n.startswith('.'):
                continue
            p = os.path.join(self.path, n)
            d = os.path.isdir(p)
            if not d and self.exts and not n.lower().endswith(tuple(self.exts)):
                continue
            items.append((n, d))
        items.sort(key=lambda t: (not t[1], t[0].lower()))
        self.items = items

    def draw(self, img, ui):
        w, h = img.size
        ui.command_bar((0, 0, w, 34))
        ui.toolbutton((6, 5, 34, 29), '', 'up', icon='folder')
        ui.textbox((40, 6, w - 10, 28), 'addr', {'text': self.path, 'cur': len(self.path)})
        lst = (10, 42, w - 10, h - 80)
        ui.rect(lst, fill=(255, 255, 255), outline=(130, 135, 144))
        rows = (lst[3] - lst[1] - 4) // 22
        self.rows = rows
        for i, (n, d) in enumerate(self.items[self.scroll:self.scroll + rows]):
            y = lst[1] + 2 + i * 22
            ui.listrow((lst[0] + 2, y, lst[2] - 18, y + 21), ('item', self.scroll + i), self.sel == self.scroll + i,
                       kind='item')
            ui.icon(icons.for_path(n, d), (lst[0] + 6, y + 2), 16)
            ui.text((lst[0] + 28, y + 11), n, 12, anchor='lm')
        ui.scrollbar((lst[2] - 17, lst[1] + 1, lst[2] - 1, lst[3] - 1), 'sb', self.scroll, rows, len(self.items))
        ui.text((10, h - 62), 'File name:', 12, anchor='lm')
        ui.textbox((80, h - 73, w - 10, h - 51), 'name', self.name)
        ui.rect((0, h - 42, w, h), fill=(240, 240, 240))
        ui.button((w - 176, h - 32, w - 96, h - 9), 'Save' if self.mode == 'save' else 'Open', 'ok', default=True)
        ui.button((w - 88, h - 32, w - 10, h - 9), 'Cancel', 'cancel')

    def on_command(self, cid, data):
        if cid == 'cancel':
            self.close()
        elif cid == 'up':
            self.path = os.path.dirname(self.path.rstrip('/')) or '/'
            self.scroll, self.sel = 0, None
            self.refresh()
        elif cid == 'sb':
            self.scroll = data
        elif isinstance(cid, tuple) and cid[0] == 'item':
            self.sel = cid[1]
            n, d = self.items[cid[1]]
            if not d:
                self.name = {'text': n, 'cur': len(n)}
        elif cid in ('ok', 'name'):
            self.accept()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'item':
            n, d = self.items[cid[1]]
            if d:
                self.path = os.path.join(self.path, n)
                self.scroll, self.sel = 0, None
                self.refresh()
            else:
                self.name = {'text': n, 'cur': len(n)}
                self.accept()
        else:
            self.on_command(cid, data)

    def on_wheel(self, dy, x, y):
        self.scroll = int(gfx.clamp(self.scroll - dy * 3, 0, max(0, len(self.items) - getattr(self, 'rows', 10))))

    def accept(self):
        n = self.name['text'].strip()
        if not n:
            return
        p = n if n.startswith('/') else os.path.join(self.path, n)
        p = os.path.abspath(p)
        if self.mode == 'save':
            home = os.path.realpath(theme.HOME)
            if not os.path.realpath(os.path.dirname(p)).startswith(home):
                message(self.shell, 'Save As', 'Neptune only saves inside its own home folder:\n%s' % theme.HOME,
                        'warning', parent=self.win)
                return
        elif not os.path.isfile(p):
            message(self.shell, 'Open', '%s\nFile not found.' % p, 'warning', parent=self.win)
            return
        self.close()
        if self.callback:
            self.callback(p)

    def on_key(self, ev):
        if ev['key'] == 'escape':
            self.close()
            return True
        return False


# ---------------------------------------------------------------------------
class CrashDialog(App):
    """The COS runtime error dialog from the Neptune concept art."""
    dialog = True
    resizable = False
    size = (430, 300)
    icon = 'error'
    show_in_taskbar = True
    bg = (0, 0, 0, 255)

    def __init__(self, shell, appname='COS', code=0xf3, message='', relaunch=None, **kw):
        super().__init__(shell, **kw)
        self.appname = appname
        self.title = appname.upper()
        self.code = code
        self.message = message
        self.relaunch = relaunch

    def draw(self, img, ui):
        w, h = img.size
        img.alpha_composite(gfx.vgradient(w, h, ((0, (0, 0, 0)), (0.35, (25, 28, 32)), (1, (95, 104, 112)))), (0, 0))
        serif = 'serif'
        ui.text((w // 2, 30), '%s RUNTIME ERROR' % self.appname.upper(), 19, color=(255, 255, 255), anchor='mm', kind=serif)
        ui.text((w // 2, 52), '-' * 46, 12, color=(200, 200, 200), anchor='mm')
        ui.text((w // 2, 80), 'EXCEPTION 0x%03x' % self.code, 19, color=(255, 255, 255), anchor='mm', kind=serif)
        msg = self.message if len(self.message) < 60 else self.message[:57] + '...'
        ui.text((w // 2, 108), msg[:1].upper() + msg[1:], 17, color=(255, 255, 255), anchor='mm', kind=serif)
        y = 160
        fnt = gfx.font(18, False, 'serif')
        for label, cid in ((('[Start %s Again]' % self.appname) if self.relaunch else None, 'again'),
                           ('[Close Dialog]', 'close')):
            if not label:
                continue
            tw = gfx.text_width(fnt, label)
            r = (w // 2 - tw // 2 - 6, y - 14, w // 2 + tw // 2 + 6, y + 14)
            hot = ui.hot(r)
            if hot:
                gfx.round_rect(img, r, 4, fill=(255, 255, 255, 30), outline=(255, 255, 255, 90))
            ui.text((w // 2, y), label, 18, color=(255, 255, 255) if not hot else (160, 240, 255), anchor='mm', kind=serif)
            ui.region(r, cid)
            y += 30

    def on_command(self, cid, data):
        if cid == 'close':
            self.close()
        elif cid == 'again' and self.relaunch:
            self.close()
            name, kw = self.relaunch
            self.shell.launch(name, **kw)

    def on_key(self, ev):
        if ev['key'] in ('escape', 'enter'):
            self.close()
            return True
        return False


# ---------------------------------------------------------------------------
class About(App):
    title = 'About COS Neptune'
    icon = 'neptune'
    dialog = True
    resizable = False
    size = (470, 360)
    single_instance = True

    def draw(self, img, ui):
        w, h = img.size
        banner = gfx.vgradient(w, 96, ((0, (8, 90, 110)), (1, (6, 30, 80))))
        img.alpha_composite(banner, (0, 0))
        img.alpha_composite(self.shell.wall().crop((300, 120, 300 + w, 216)).resize((w, 96)), (0, 0))
        img.alpha_composite(Image.new('RGBA', (w, 96), (0, 20, 40, 80)), (0, 0))
        img.alpha_composite(icons.get('neptune', 72), (22, 12))
        gfx.shadow_text(img, (108, 26), 'COS', gfx.font(20, False, 'serif'), (220, 245, 250, 255), (0, 0, 0, 200))
        gfx.shadow_text(img, (106, 46), 'Neptune', gfx.font(30, False, 'serif'), (255, 255, 255, 255), (0, 0, 0, 200))
        ui.hline(0, w, 96, (60, 140, 160))
        y = 114
        for line in ('COS - Channel Operating System',
                     'Version %s (Build %s)  "%s"' % (theme.VERSION, theme.BUILD, theme.CODENAME),
                     'General version %s, revision %s, built %s' % theme.build_parts(),
                     'Copyright © 2026 TCF (The Coloradan Financial). All rights reserved.'):
            ui.text((24, y), line, 12)
            y += 20
        y += 8
        ui.wrap((24, y), 'COS Neptune renders its whole desktop through Graphics Desktop Rendering (GDR): '
                         'every pixel, glass pane and shadow is composed by Neptune itself, straight to the '
                         'display controller. No X server, no Wayland, no curses underneath.', w - 48, 12,
                color=(60, 70, 80))
        ui.text((24, h - 58), 'Display: %s' % self.shell.display_name, 11, color=theme.MUTED)
        ui.button((w - 95, h - 34, w - 14, h - 10), 'OK', 'ok', default=True)

    def on_command(self, cid, data):
        if cid == 'ok':
            self.close()

    def on_key(self, ev):
        if ev['key'] in ('enter', 'escape'):
            self.close()
            return True
        return False


# ---------------------------------------------------------------------------
class PowerDialog(App):
    title = 'Shut Down COS Neptune'
    icon = 'power'
    dialog = True
    resizable = False
    size = (380, 250)
    single_instance = True
    CHOICES = [('exit', 'Exit to console'), ('restart', 'Restart Neptune'),
               ('poweroff', 'Shut down the computer'), ('reboot', 'Restart the computer')]

    def __init__(self, shell, mode='menu', **kw):
        super().__init__(shell, **kw)
        self.choice = mode if mode in dict(self.CHOICES) else 'exit'

    def draw(self, img, ui):
        w, h = img.size
        img.alpha_composite(gfx.vgradient(w, 58, ((0, (20, 110, 130)), (1, (8, 40, 90)))), (0, 0))
        img.alpha_composite(icons.get('neptune', 40), (14, 9))
        gfx.shadow_text(img, (64, 29), 'What do you want Neptune to do?', gfx.font(14), (255, 255, 255, 255),
                        (0, 0, 0, 200), anchor='lm')
        y = 74
        for key, label in self.CHOICES:
            ui.radio((26, y), label, ('pick', key), self.choice == key, data=key)
            y += 26
        if self.choice in ('poweroff', 'reboot') and self.shell.backend != 'gdr':
            ui.text((26, y + 2), 'Only available in GDR mode; Neptune will just exit.', 11, color=(160, 80, 0))
        ui.rect((0, h - 42, w, h), fill=(240, 240, 240))
        ui.button((w - 176, h - 32, w - 96, h - 9), 'OK', 'ok', default=True)
        ui.button((w - 88, h - 32, w - 10, h - 9), 'Cancel', 'cancel')

    def on_command(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'pick':
            self.choice = cid[1]
        elif cid == 'cancel':
            self.close()
        elif cid == 'ok':
            self.close()
            c = self.choice
            if c in ('poweroff', 'reboot') and self.shell.backend != 'gdr':
                c = 'exit'
            self.shell.shutdown(c)

    def on_key(self, ev):
        if ev['key'] == 'escape':
            self.close()
            return True
        if ev['key'] == 'enter':
            self.on_command('ok', None)
            return True
        if ev['key'] in ('up', 'down'):
            keys = [k for k, _ in self.CHOICES]
            i = keys.index(self.choice) + (1 if ev['key'] == 'down' else -1)
            self.choice = keys[i % len(keys)]
            return True
        return False


# ---------------------------------------------------------------------------
class Run(App):
    title = 'Run'
    icon = 'terminal'
    dialog = True
    resizable = False
    size = (400, 190)
    single_instance = True

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        self.cmd = {'text': '', 'cur': 0}
        self.ui.focus = 'cmd'

    def draw(self, img, ui):
        w, h = img.size
        ui.icon('terminal', (16, 16), 32)
        ui.wrap((60, 18), 'Type the name of a program, folder, or command, and Neptune will open it for you.',
                w - 80, 12)
        ui.text((16, 82), 'Open:', 12, anchor='lm')
        ui.textbox((60, 71, w - 16, 93), 'cmd', self.cmd)
        ui.rect((0, h - 42, w, h), fill=(240, 240, 240))
        ui.button((w - 260, h - 32, w - 180, h - 9), 'OK', 'ok', default=True)
        ui.button((w - 172, h - 32, w - 96, h - 9), 'Cancel', 'cancel')
        ui.button((w - 88, h - 32, w - 10, h - 9), 'Browse...', 'browse')

    def on_command(self, cid, data):
        if cid == 'cancel':
            self.close()
        elif cid == 'browse':
            self.shell.launch('files', path=theme.HOME)
        elif cid in ('ok', 'cmd'):
            self.run(self.cmd['text'].strip())

    def run(self, cmd):
        if not cmd:
            return
        reg = self.shell.registry
        low = cmd.lower()
        for key, e in reg.items():
            if low in (key, e[1].lower()):
                self.close()
                self.shell.launch(key)
                return
        if os.path.isdir(os.path.expanduser(cmd)):
            self.close()
            self.shell.launch('files', path=os.path.expanduser(cmd))
            return
        if os.path.isfile(os.path.expanduser(cmd)):
            self.close()
            self.shell.launch('notepad', path=os.path.expanduser(cmd))
            return
        self.close()
        self.shell.launch('terminal', command=cmd)

    def on_key(self, ev):
        if ev['key'] == 'escape':
            self.close()
            return True
        return False


# ---------------------------------------------------------------------------
class Calendar(App):
    title = 'Date and Time'
    icon = 'clock'
    resizable = False
    size = (300, 290)
    single_instance = True
    position = 'tray'

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        t = time.localtime()
        self.year, self.month = t.tm_year, t.tm_mon

    def on_tick(self, now):
        s = int(now)
        if s != getattr(self, '_s', None):
            self._s = s
            return True
        return False

    def draw(self, img, ui):
        w, h = img.size
        img.alpha_composite(gfx.vgradient(w, h, ((0, (255, 255, 255)), (1, (225, 235, 245)))), (0, 0))
        ui.text((w // 2, 18), time.strftime('%A, %B %d, %Y'), 12, color=(0, 51, 153), anchor='mm')
        ui.text((w // 2, 44), time.strftime('%H:%M:%S' if self.shell.settings.get('clock_24h') else '%I:%M:%S %p'),
                22, anchor='mm', kind='light')
        ui.toolbutton((10, 64, 34, 86), '◀', 'prev')
        ui.toolbutton((w - 34, 64, w - 10, 86), '▶', 'next')
        ui.text((w // 2, 75), '%s %d' % (_cal.month_name[self.month], self.year), 13, True, anchor='mm')
        cw = (w - 20) // 7
        for i, d in enumerate(['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']):
            ui.text((10 + i * cw + cw // 2, 102), d, 11, color=theme.MUTED, anchor='mm')
        today = time.localtime()
        c = _cal.Calendar(6)
        for r, week in enumerate(c.monthdayscalendar(self.year, self.month)):
            for i, day in enumerate(week):
                if not day:
                    continue
                cx, cy = 10 + i * cw + cw // 2, 124 + r * 24
                if (day, self.month, self.year) == (today.tm_mday, today.tm_mon, today.tm_year):
                    gfx.round_rect(img, (cx - 14, cy - 10, cx + 14, cy + 11), 3,
                                   fill=gfx.vgradient(28, 21, ((0, (220, 235, 252)), (1, (193, 219, 252)))),
                                   outline=(60, 127, 177, 255))
                ui.text((cx, cy), str(day), 12, anchor='mm')

    def on_command(self, cid, data):
        if cid == 'prev':
            self.month -= 1
            if self.month < 1:
                self.month, self.year = 12, self.year - 1
        elif cid == 'next':
            self.month += 1
            if self.month > 12:
                self.month, self.year = 1, self.year + 1


# ---------------------------------------------------------------------------
def _cpu():
    try:
        for line in open('/proc/cpuinfo'):
            if line.startswith('model name'):
                return line.split(':', 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or '?'


def _mem():
    try:
        for line in open('/proc/meminfo'):
            if line.startswith('MemTotal'):
                return '%.1f GB' % (int(line.split()[1]) / 1048576)
    except OSError:
        pass
    return '?'


def _uptime():
    try:
        s = float(open('/proc/uptime').read().split()[0])
        return '%dh %02dm' % (s // 3600, (s % 3600) // 60)
    except OSError:
        return '?'


class SysInfo(App):
    title = 'System'
    icon = 'computer'
    size = (620, 460)
    single_instance = True

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        self.cpu, self.mem = _cpu(), _mem()

    def on_tick(self, now):
        if now - getattr(self, '_t', 0) > 1.0:
            self._t = now
            return True
        return False

    def draw(self, img, ui):
        w, h = img.size
        ui.command_bar((0, 0, w, 32))
        ui.text((12, 16), 'Control Panel  ▸  System', 12, color=(30, 50, 80), anchor='lm')
        ui.text((24, 48), 'View basic information about your computer', 17, color=(0, 51, 153), kind='light')
        y = 86
        ui.text((24, y), 'COS edition', 12, True, color=(30, 60, 110))
        ui.hline(110, w - 24, y + 8, (220, 228, 235))
        y += 24
        ui.text((40, y), theme.FULLNAME, 12)
        ui.text((40, y + 18), 'Build %s  ·  Graphics Desktop Rendering' % theme.BUILD, 12, color=theme.MUTED)
        img.alpha_composite(icons.get('neptune', 64), (w - 100, 82))
        y += 52
        ui.text((24, y), 'System', 12, True, color=(30, 60, 110))
        ui.hline(80, w - 24, y + 8, (220, 228, 235))
        y += 24
        ft = self.shell.frame_times
        avg = (sum(ft) / len(ft) * 1000) if ft else 0
        score = gfx.clamp(7.9 - avg / 9.0, 1.0, 7.9) if ft else 0
        rows = [('Rating:', None),
                ('Processor:', self.cpu),
                ('Installed memory:', self.mem),
                ('Kernel:', '%s %s' % (platform.system(), platform.release())),
                ('Display:', self.shell.display_name),
                ('Renderer:', 'GDR software compositor (PIL/numpy), %.1f ms per frame' % avg),
                ('Uptime:', _uptime()),
                ('Python:', sys.version.split()[0])]
        for k, v in rows:
            ui.text((40, y), k, 12, color=theme.MUTED)
            if v is None:
                # Neptune Experience Index badge
                bx = 170
                gfx.round_rect(img, (bx, y - 4, bx + 44, y + 22), 4,
                               fill=gfx.vgradient(44, 26, ((0, (80, 170, 230)), (1, (20, 90, 170)))),
                               outline=(20, 60, 120, 255))
                gfx.shadow_text(img, (bx + 22, y + 9), '%.1f' % score, gfx.font(14, True), (255, 255, 255, 255),
                                (0, 0, 0, 160), anchor='mm')
                ui.link((bx + 54, y), 'Neptune Experience Index', 'nei')
                y += 30
                continue
            ui.text((170, y), gfx.ellipsize(gfx.font(12), v, w - 200), 12)
            y += 22
        if self.shell.input_info:
            y += 6
            ui.text((24, y), 'Input', 12, True, color=(30, 60, 110))
            ui.hline(70, w - 24, y + 8, (220, 228, 235))
            y += 22
            for line in self.shell.input_info[:4]:
                ui.text((40, y), gfx.ellipsize(gfx.font(11), line, w - 70), 11, color=theme.MUTED)
                y += 17

    def on_command(self, cid, data):
        if cid == 'nei':
            message(self.shell, 'Neptune Experience Index',
                    'The index is measured from how long the GDR compositor takes to build a frame. '
                    '7.9 means the whole glass desktop is recomposed in well under a frame.', 'info', parent=self.win)


# ---------------------------------------------------------------------------
HELP_PAGES = [
    ('welcome', 'Welcome to Neptune', [
        ('Welcome to COS Neptune', None),
        ('COS Neptune (version %s) is the Aero release of COS.' % theme.VERSION + ' The whole desktop is drawn by Neptune itself '
         'through Graphics Desktop Rendering (GDR): translucent glass windows that blur what is behind them, '
         'soft shadows, glowing captions and smooth animations.', ''),
        ('Getting started', None),
        ('• Click the Neptune orb (bottom left) or press the Super key to open the Start menu, then type '
         'to search for programs.\n• The menu bar along the top (COS, FILE, EDIT, APPS, CONTROL, POWER, HELP) '
         'works from anywhere.\n• Double-click desktop icons to open them.\n• Right-click the desktop to '
         'personalize Neptune.', ''),
    ]),
    ('aero', 'Aero features', [
        ('Aero Glass', None),
        ('Window frames, the taskbar, the menu bar and the Start menu are glass. Pick the glass colour, its '
         'intensity and transparency in Personalize.', ''),
        ('Aero Snap', None),
        ('Drag a window to the top edge to maximize it, or to the left or right edge to fill half the screen. '
         'Drag it away again to restore it. Super+Arrow keys do the same.', ''),
        ('Aero Peek', None),
        ('Rest the pointer on the small slab at the right end of the taskbar and every window turns to clear '
         'glass so you can see the desktop. Hover over a taskbar thumbnail to peek at just that window.', ''),
        ('Aero Shake', None),
        ('Grab a window by its title bar and shake it to minimize every other window. Shake again to bring '
         'them back.', ''),
        ('Flip', None),
        ('Hold Alt and press Tab to flip between windows with live previews.', ''),
    ]),
    ('keys', 'Keyboard shortcuts', [
        ('Keyboard shortcuts', None),
        ('Super — open or close the Start menu\nAlt+Tab — switch windows (Flip)\nAlt+F4 — close the '
         'active window\nSuper+D — show the desktop\nSuper+Up / Down — maximize / restore or minimize\n'
         'Super+Left / Right — snap to half of the screen\nSuper+E — open your Neptune home folder\n'
         'Super+R — Run\nSuper+L — lock the screen\nCtrl+Shift+Esc — Task Manager\nF1 — Neptune Help\n'
         'F10 — open the menu bar\nCtrl+Alt+Backspace — exit Neptune immediately\n'
         'Ctrl+Shift+Alt+Q — emergency exit: asks first, works from anywhere (even the lock screen)', ''),
        ('Inside any window', None),
        ('Tab / Shift+Tab — move between buttons, boxes and checkboxes (a dotted ring shows where you are)\n'
         'Enter or Space — press the button or tick the box that has the ring\n'
         'Up / Down in an open menu — move; Enter — choose; Esc — close', ''),
        ('File Explorer (CEAP)', None),
        ('Backspace or Alt+Up — up a folder\nAlt+Left / Right — back / forward\nF5 — refresh\nF2 — rename\nDelete — delete (Neptune '
         'home only)\nClick in Code.Exe.Explore, then type — the docked terminal gets the keys', ''),
        ('CEAP:Web', None),
        ('Ctrl+L — address bar\nCtrl+F — find on the page\nCtrl+D — add to favorites\nCtrl+E — JavaScript '
         'console\nF5 — reload\nAlt+Left / Right — back / forward', ''),
        ('Druman', None),
        ('Space — play / pause\nLeft / Right — seek 5 s (Ctrl: 30 s)\nUp / Down — volume\nN / P — next / '
         'previous\nS — stop\nL — show or hide the playlist', ''),
    ]),
    ('v24', 'Back from COS v2.4', [
        ('Task Manager', None),
        ('COS v2.4\'s system monitor and process manager, now one window. Applications lists your Neptune '
         'windows, Processes lists everything running (sort by any column, Delete ends the selected process), '
         'and Performance graphs every CPU core and memory. Open it with Ctrl+Shift+Esc or by right-clicking '
         'the taskbar.', ''),
        ('Druman', None),
        ('The COS music AND video player. In v2.4 it drew video in character cells; in Neptune it plays real '
         'pixels inside its window, with a live spectrum visualiser for music. Double-click any audio or video '
         'file in Explorer to open it.', ''),
        ('Your own apps (.nepapp)', None),
        ('The .cosapp plugin system, reborn. Put a Python file ending in .nepapp in home/.neptune/apps and it '
         'appears in the APPS menu and Start menu. Save the file and Neptune reloads it on the spot. COS > App '
         'Manager makes new ones from a template; open Bubbles.nepapp to see how one is built.', ''),
        ('Webinator', None),
        ('The COS web browser. In v2.4 it drew pages as text; Neptune\'s has its own layout engine with fonts, '
         'pictures, tables and forms. Like v2.4 it runs no JavaScript, so text-first sites work best. Ctrl+L goes '
         'to the address bar, Ctrl+F finds, Ctrl+D adds a favourite, and files that are not pages download to '
         'Downloads. A .cip offers to install.', ''),
        ('Pigeon', None),
        ('Shares a folder read-only on your network and speaks the same protocol as v2.4, so Neptune and v2.4 '
         'machines sync with each other. Receive mirrors another machine\'s share into Downloads/Pigeon, moving '
         'only files that changed.', ''),
        ('Bubble backgrounds and screen savers', None),
        ('Personalize > Desktop Background > Bubbles has the COS 3.1 bubble look (COS Bubble, COS Multi-Bubble and four '
         'more), drawn sharp at any screen size; the original pictures are under Picture. CONTROL > Screen Saver... '
         'picks a screen saver (Bubbles, Bouncing COS, Warp, Aurora, Channel Rain, Clock, Blank), how many idle '
         'minutes before it starts, and whether coming back shows the lock screen. Any key, click or real mouse '
         'movement ends it. A playing video keeps it away.', ''),
        ('Zip, unzip, gzip and gunzip', None),
        ('Archiver opens .zip, .tar.gz, .tar.xz, .tar.bz2, .tar, .gz, .bz2 and .xz files like folders: browse inside, '
         'open a file straight from the archive, extract everything or just what you pick, test the checksums, and '
         'add files to a .zip. In Explorer, right-click an archive for Extract All / Extract Here / Decompress, or '
         'right-click any file or folder for Send to > Compressed (zipped) folder, TAR.GZ, GZip, or Compress... '
         'Extracting skips anything unsafe (paths that would escape the folder, links, devices) and stops "zip bombs" '
         'before they fill the disk.', ''),
        ('Build numbers', None),
        ('A build number is the general version, then the revision, then when the build was made '
         '(YYYYMMDDHHMMSS). 3020261031201548 is COS 3.0, built 31 October 2026 at 20:15:48.', ''),
        ('Lock screen', None),
        ('Super+L locks Neptune. CONTROL > Lock Screen... can show it at startup and set a password. Like '
         'v2.4\'s login screen, it hides the desktop; it is not real security.', ''),
    ]),
    ('compat', 'Compatibility & packages', [
        ('Compatibility mode', None),
        ('Neptune runs programs written for COS 2.x (.cosapp files) the way Windows runs programs for older '
         'Windows. Each one gets the COS v2.4 `cos` API it was written against, and its window is drawn as '
         'character cells in the old console colours, inside an Aero frame. Neptune finds them in two places: '
         'programs installed from a .cip, and the ones already in your COS v2.4 folder (~/.cos/apps), which run '
         'in place.', ''),
        ('Compatibility settings', None),
        ('Right-click a running COS program, or the file in Explorer, and choose Compatibility settings. Run it '
         'as COS 2.4 or COS 2.3 (keyboard only), pick any of the seven v2.4 colour schemes, force 80 x 25 text '
         'mode, add a CRT effect, or change the text size. Settings are kept per program. If a program raises a '
         'fatal error, only its own window shows the blue screen.', ''),
        ('COS v2.4 Mode', None),
        ('Like Windows XP Mode: the real COS v2.4 (~/cos/cos.py) running in a window. It is sandboxed with '
         'bubblewrap, so it cannot read your mouse or keyboard devices, and it gets its own home folder, so it '
         'never changes your real ~/.cos.', ''),
        ('Packages (.cip and .cdi)', None),
        ('The same files as COS v2.4. Double-click one in Explorer, or use APPS > Install Package. The '
         'installer checks the package, shows its Read Me and license, lists every file it will write, and '
         'records them so Programs and Features can uninstall cleanly. A .cip can carry COS programs '
         '(.cosapp) and Neptune apps (.nepapp). Right-click a folder with a package.json to build one.', ''),
        ('Drivers and Device Manager', None),
        ('A .cdi driver runs on its own thread for every device it matches. What it types, clicks and moves '
         'arrives in Neptune as real input, so a gamepad driver drives Aero windows too. CONTROL > Device '
         'Manager shows every device, the driver bound to it, and its status. Start Neptune with '
         'NEPTUNE_NO_DRIVERS=1 to skip drivers.', ''),
    ]),
    ('ceap', 'CEAP', [
        ('CEAP: the COS Explorer Application Platform', None),
        ('CEAP:Main is an explorer with a directory tree, three ways to look at a folder (Icon.Arrange: drag icons '
         'anywhere and CEAP remembers; Icon.Standard: a tidy grid; Text.Display: a numbered list), Overview and '
         'Metadata panes, and Code.Exe.Explore, a terminal at the bottom that follows the folder you are in. CEAP '
         'apps are .ceap text files built from CEAP windows, no Python needed. Put one in home/.neptune/ceap to '
         'list it under APPS, or double-click it anywhere. Right-click in CEAP:Main > New CEAP App starts one.', ''),
        ('The CEAP language', None),
        (__import__('nep.apps.ceap', fromlist=['LANGUAGE']).LANGUAGE, ''),
    ]),
    ('new31', "What's new in 3.1", [
        ('COS 3.1 at a glance', None),
        ('COS 3.0 is kept, ready to run, in ~/cos/neptune/old/v3-0. Everything else below is new or reworked.', ''),
        ('The CEAP apps', None),
        ('File Explorer is now CEAP:Main: directory tree, three views (Icon.Arrange, Icon.Standard, Text.Display), '
         'Overview and Metadata panes, and a terminal at the bottom that follows the folder you are in. The web '
         'browser opens as CEAP:Web, with favorites and history in the tree and a JavaScript console. Control '
         'Panel > Programs switches either back to the classic version.', ''),
        ('Control Panel', None),
        ('CONTROL > Control Panel holds every COS 2.4 and Neptune setting, with search. Point at a setting to see '
         'what it does and where it came from. Settings that only make sense on a text console are listed under '
         'Compatibility and still work in COS v2.4 Mode.', ''),
        ('CEAP App Maker', None),
        ('APPS > CEAP App Maker builds apps by clicking: text, input boxes and buttons whose actions you pick from '
         'a menu, with a live preview. Test run, and the Log pane shows every click, action and variable change.', ''),
        ('Less getting stuck', None),
        ('Ctrl+Shift+Alt+Q asks whether to leave Neptune, from anywhere. Menus never go off the screen: they open '
         'the other way, split into columns, or scroll. Unplug a mouse, keyboard or gamepad and plug it back in: it '
         'works again within a couple of seconds. COS v2.4 Mode now gets real mouse clicks.', ''),
        ('Recycle Bin', None),
        ('Deleting in File Explorer moves things to the Recycle Bin (desktop icon), so a mistake can be undone: '
         'open it, pick the item and click Restore. Shift+Delete skips it and deletes for good.', ''),
        ('The COS Place and themes', None),
        ('The COS Place (desktop icon) is a web of COS computers with no server. Join it and your shelf of themes, '
         'pictures, videos, music and apps is shared with your network; get things from other computers and they '
         'stay on your shelf, so nothing becomes lost media. Endangered lists things only one computer still has. '
         'COS Aurora speaks the same protocol. Themes on this computer makes a .ctheme from your look, installs '
         'one, or applies a built-in. Joining is off until you choose it, and nothing you get is ever run.', ''),
        ('COS Cities', None),
        ('A free home page for every COS computer, old-web style. Open COS Cities (desktop icon), fill in your page, '
         'pick a neighborhood and a look, tick the hit counter, guestbook, webring or under-construction sign, and '
         'Publish. Your page is served by The COS Place, so other computers visit it in Webinator; All neighborhoods '
         'lists everyone else\'s. index.html in the Sites folder is yours to hand-edit; scripts are never served.', ''),
        ('And Bubbo', None),
        ('The little bubble walking along the taskbar. Click him. Control Panel > Fun turns him off.', ''),
    ]),
    ('gdr', 'GDR & recovery', [
        ('Graphics Desktop Rendering', None),
        ('Run from a real text console (Ctrl+Alt+F3, log in, then python3 ~/cos/neptune/neptune.py) and Neptune '
         'takes the screen: it switches the console to KD_GRAPHICS, becomes DRM master and draws every pixel '
         'itself. On machines without a KMS driver it falls back to /dev/fb0.', ''),
        ('If the screen goes black', None),
        ('1. Neptune undoes it by itself: if you do not press a key or click within 20 seconds of the desktop '
         'appearing, it gives the console back.\n2. A marker file (home/.neptune/gdr-attempt) stops Neptune '
         'walking into the same wall twice; it then starts in a safe mode and tells you.\n3. By hand: '
         'Ctrl+Alt+Backspace exits, or from another console run  pkill -f neptune.py', ''),
        ('Inside a desktop session', None),
        ('Started from a terminal under X or Wayland, Neptune opens in a window instead (the taskbar badge '
         'says WIN rather than GDR). Use --fullscreen to fill the screen.', ''),
    ]),
    ('files', 'Files and safety', [
        ('Where your files live', None),
        ('Neptune keeps everything in its own home folder, %s. Documents, Pictures and settings are '
         'stored there. You can browse the rest of the computer with Explorer, but Neptune only writes inside '
         'its home folder.' % theme.HOME, ''),
    ]),
]


class Help(App):
    title = 'Neptune Help and Support'
    icon = 'help'
    size = (700, 500)
    single_instance = True

    def __init__(self, shell, page='welcome', **kw):
        super().__init__(shell, **kw)
        self.page = page
        self.scroll = 0

    def draw(self, img, ui):
        w, h = img.size
        img.alpha_composite(gfx.vgradient(w, 64, ((0, (30, 120, 170)), (1, (10, 60, 120)))), (0, 0))
        img.alpha_composite(icons.get('help', 40), (16, 12))
        gfx.shadow_text(img, (66, 32), 'Neptune Help and Support', gfx.font(20, False, 'light'), (255, 255, 255, 255),
                        (0, 0, 0, 150), anchor='lm')
        nav = (0, 64, 190, h)
        ui.panel_gradient(nav, (240, 246, 252), (220, 232, 245))
        ui.d.line([(190, 64), (190, h)], fill=(180, 195, 210, 255))
        y = 76
        for key, label, _content in HELP_PAGES:
            ui.listrow((8, y, 182, y + 28), ('page', key), self.page == key)
            ui.text((18, y + 14), label, 12, anchor='lm')
            y += 32
        content = dict((k, c) for k, _l, c in HELP_PAGES).get(self.page, [])
        y = 80 - self.scroll
        for text, kind in content:
            if kind is None:
                ui.text((210, y), text, 16 if y < 100 else 14, color=(0, 51, 153), kind='light' if y < 100 else 'sans')
                y += 30
            else:
                y = ui.wrap((210, y), text, w - 240, 12, line_h=19) + 12

    def on_command(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'page':
            self.page = cid[1]
            self.scroll = 0

    def on_wheel(self, dy, x, y):
        self.scroll = max(0, self.scroll - dy * 30)
