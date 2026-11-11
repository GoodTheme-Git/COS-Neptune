"""The COS Neptune Mobile shell, in Python: boot, lock, home, apps, shade, recents.

One Shell draws the whole phone into a PIL image every time something changes and
hands it to the window. Input arrives as pointer down/move/up (the mouse is your
finger), wheel, and keys.
"""
import json
import math
import os
import time
import traceback

from PIL import Image, ImageDraw, ImageFilter

from . import ui
from .ui import W, H, STATUS_H, NAV_H, TITLE_H, gfx

PY_ROOT = os.path.dirname(ui.HERE)                         # mobile/python
HOME = os.environ.get('NEPTUNE_MOBILE_HOME', os.path.join(PY_ROOT, 'home'))
SETTINGS_FILE = os.path.join(HOME, '.settings.json')
CRASH_LOG = os.path.join(HOME, '.crash.log')
SOUNDS = os.path.join(ui.MOBILE, 'web', 'sounds')
VERSION = '3.1'
FULLNAME = 'COS Neptune Mobile'

DEFAULTS = {
    'glass': 'Neptune', 'intensity': 0.42, 'wallpaper': 'Neptune Aurora', 'clock24': False,
    'sounds': True, 'volume': 80, 'pin': '', 'lock_on_start': True, 'boot_screen': True,
    'animations': True, 'user': '', 'oobe': False, 'brightness': 100,
    'ibbs_server': '192.168.1.115:8619', 'ibbs_name': '', 'ibbs_token': '', 'ibbs_room': 'general',
    'ibbs_notify': True,
    'dock': ['files', 'ibbs', 'terminal', 'settings'],
}

LONG_PRESS = 0.55
DRAG_START = 8


def build_number():
    """<major><rev><YYYYMMDDHHMMSS> of the newest source file, like Neptune's."""
    newest = 0
    for base, dirs, files in os.walk(PY_ROOT):
        dirs[:] = [d for d in dirs if d not in ('__pycache__', 'home')]
        for n in files:
            if n.endswith('.py'):
                newest = max(newest, os.path.getmtime(os.path.join(base, n)))
    major, _, rev = VERSION.partition('.')
    return '%s%s%s' % (major, rev, time.strftime('%Y%m%d%H%M%S', time.localtime(newest or time.time())))


class App:
    """Base class for phone apps. Override draw(); the rest is optional."""
    id = 'app'
    name = 'App'
    icon = 'file'
    c1, c2 = '#4b8fd6', '#123f8a'
    dark = False                 # dark client area

    def __init__(self, shell, **args):
        self.shell = shell
        self.title = self.name
        self.scroll = 0
        self.max_scroll = 0

    # drawing: box is the client area (screen coords); add buttons to hits
    def draw(self, img, box, hits):
        pass

    def tick(self, dt):
        """Called ~20 times a second while the app is in front. True = redraw."""
        return False

    def key(self, ev):
        return False

    def drag(self, dx, dy, x, y):
        """Finger moved by (dx, dy). Default: scroll the content."""
        old = self.scroll
        self.scroll = max(0, min(self.max_scroll, self.scroll - dy))
        return self.scroll != old

    def wheel(self, dy):
        return self.drag(0, dy * 40, 0, 0)

    def release(self, x, y):
        pass

    def back(self):
        """Android Back: return True if the app handled it (otherwise it closes)."""
        return False

    def args(self, **a):
        pass

    def closing(self):
        pass

    def status(self):
        return ''


class Shell:
    def __init__(self):
        os.makedirs(HOME, exist_ok=True)
        self.settings = dict(DEFAULTS)
        try:
            with open(SETTINGS_FILE) as f:
                self.settings.update(json.load(f))
        except (OSError, ValueError):
            pass
        self.build = build_number()
        self.apps = {}               # id -> App class
        self.order = []              # registration order (home grid)
        self.running = {}            # id -> App instance
        self.stack = []              # most recent last
        self.current = None
        self.snapshots = {}          # id -> small picture for Recents
        self.phase = 'boot'
        self.boot_t = time.time()
        self.locked = False
        self.lock_drag = 0
        self.pin_entry = None        # '' while the PIN pad is up
        self.shade = 0.0             # 0 closed .. 1 open
        self.shade_target = 0.0
        self.recents = False
        self.recents_x = 0
        self.dialog = None
        self.toasts = []
        self.notes = []
        self.badges = {}
        self.anim = None             # (kind, app id, start)
        self.hits = ui.Hits()
        self.dirty = True
        self.quit = False
        self.ptr = None
        self.last_minute = None
        self.listeners = {}
        self.sound_cache = {}
        self.mixer = None
        self.ensure_home()

    # ------------------------------------------------------------ settings & events
    def set(self, key, value):
        self.settings[key] = value
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(self.settings, f, indent=1)
        except OSError:
            pass
        if key in ('wallpaper', 'glass', 'intensity', 'brightness'):
            self.home_cache = None
        self.emit('settings', key)
        self.dirty = True

    def on(self, ev, fn):
        self.listeners.setdefault(ev, []).append(fn)

    def emit(self, ev, *a):
        for fn in self.listeners.get(ev, []):
            try:
                fn(*a)
            except Exception:
                traceback.print_exc()

    @property
    def tint(self):
        return ui.GLASS_RGB.get(self.settings['glass'], ui.GLASS_RGB['Neptune'])

    def ensure_home(self):
        for d in ('Documents', 'Pictures', 'Music', 'Downloads'):
            os.makedirs(os.path.join(HOME, d), exist_ok=True)
        welcome = os.path.join(HOME, 'Documents', 'Welcome.txt')
        if not os.path.exists(welcome):
            with open(welcome, 'w') as f:
                f.write('Welcome to COS Neptune Mobile!\n\nThis is the Python version: the same phone shell as the '
                        'web and Android ones, drawn with Neptune\'s own glass.\n\nTips:\n'
                        '- Drag down from the top for the notification shade.\n'
                        '- The middle button is Home; hold it for the power menu.\n'
                        '- Esc is Back. F5 takes a screenshot into Pictures.\n')

    # ------------------------------------------------------------ apps
    def register(self, cls):
        self.apps[cls.id] = cls
        if cls.id not in self.order:
            self.order.append(cls.id)

    def open(self, app_id, **args):
        cls = self.apps.get(app_id)
        if not cls:
            return self.toast('No app called %s' % app_id)
        self.close_overlays()
        try:
            if app_id in self.running:
                if args:
                    self.running[app_id].args(**args)
            else:
                self.running[app_id] = cls(self, **args)
        except Exception as e:
            return self.crash(app_id, e)
        if app_id in self.stack:
            self.stack.remove(app_id)
        self.stack.append(app_id)
        if self.current != app_id:
            self.anim = ('open', app_id, time.time())
        self.current = app_id
        if app_id in self.badges:
            self.badge(app_id, 0)
        self.dirty = True

    def close(self, app_id):
        app = self.running.pop(app_id, None)
        if app:
            try:
                app.closing()
            except Exception:
                traceback.print_exc()
        if app_id in self.stack:
            self.stack.remove(app_id)
        self.snapshots.pop(app_id, None)
        if self.current == app_id:
            self.current = None
            self.anim = ('close', app_id, time.time())
        self.dirty = True

    def go_home(self):
        self.close_overlays()
        if self.current:
            self.anim = ('home', self.current, time.time())
        self.current = None
        self.dirty = True

    def back(self):
        if self.dialog:
            d, self.dialog = self.dialog, None
            if d.get('cancel'):
                d['cancel']()
            self.dirty = True
            return
        if self.shade_target > 0:
            return self.close_shade()
        if self.recents:
            self.recents = False
            self.dirty = True
            return
        if self.current:
            app = self.running[self.current]
            handled = self.guard(self.current, app.back)
            if not handled:
                self.close(self.current)
        self.dirty = True

    def close_overlays(self):
        self.recents = False
        self.shade_target = 0.0
        self.dirty = True

    def close_shade(self):
        self.shade_target = 0.0
        self.dirty = True

    def guard(self, app_id, fn, *a):
        """Run app code; a crash shows the COS runtime error dialog instead of killing the shell."""
        try:
            return fn(*a)
        except Exception as e:
            self.crash(app_id, e)
            return None

    def crash(self, app_id, exc):
        name = self.apps[app_id].name if app_id in self.apps else app_id
        detail = ''.join(traceback.format_exception_only(type(exc), exc)).strip()
        try:
            with open(CRASH_LOG, 'a') as f:
                f.write('%s %s\n%s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), name, traceback.format_exc()))
        except OSError:
            pass
        if app_id in self.running:
            self.running.pop(app_id, None)
            if app_id in self.stack:
                self.stack.remove(app_id)
            if self.current == app_id:
                self.current = None
        self.sound('warn')
        self.dialog = {'kind': 'crash', 'title': 'COS Runtime Error',
                       'message': '%s has stopped working.' % name, 'detail': detail,
                       'buttons': [('Start %s Again' % name, lambda: self.open(app_id), 'primary'),
                                   ('Close Dialog', None, 'normal')]}
        self.dirty = True

    # ------------------------------------------------------------ feedback
    def toast(self, msg, secs=2.6):
        self.toasts = [t for t in self.toasts if t[0] != msg][-2:] + [(str(msg), time.time() + secs)]
        self.dirty = True

    def notify(self, title, body='', app=None, quiet=False):
        self.notes.insert(0, {'title': title, 'body': body, 'app': app, 'time': time.time()})
        del self.notes[30:]
        if not quiet:
            self.sound('notify')
            self.toast(title + (': ' + body if body else ''))
        self.dirty = True

    def badge(self, app_id, n):
        if n:
            self.badges[app_id] = n
        else:
            self.badges.pop(app_id, None)
        self.home_cache = None
        self.dirty = True

    def sound(self, name):
        """One pygame mixer channel per sound: no processes, so a burst can't pile up."""
        if not self.settings.get('sounds') or self.mixer is False:
            return
        try:
            if self.mixer is None:
                import pygame
                pygame.mixer.init()
                self.mixer = pygame.mixer
            snd = self.sound_cache.get(name)
            if snd is None:
                snd = self.mixer.Sound(os.path.join(SOUNDS, name + '.mp3'))
                self.sound_cache[name] = snd
            snd.set_volume(self.settings.get('volume', 80) / 100.0)
            if not snd.get_num_channels():
                snd.play()
        except Exception:
            self.mixer = False          # no audio device: stay quiet

    # ------------------------------------------------------------ dialogs
    def alert(self, title, message, done=None):
        self.dialog = {'title': title, 'message': message, 'buttons': [('OK', done, 'primary')]}
        self.dirty = True

    def confirm(self, title, message, yes, label='OK', danger=False):
        self.dialog = {'title': title, 'message': message,
                       'buttons': [('Cancel', None, 'normal'), (label, yes, 'danger' if danger else 'primary')]}
        self.dirty = True

    def prompt(self, title, message, done, default='', secret=False):
        d = {'title': title, 'message': message, 'field': str(default), 'secret': secret}
        d['buttons'] = [('Cancel', None, 'normal'), ('OK', lambda: done(d['field']), 'primary')]
        self.dialog = d
        self.dirty = True

    def choose(self, title, options, done):
        """A list of choices; done(index)."""
        self.dialog = {'title': title, 'options': list(options), 'pick': done, 'buttons': [('Cancel', None, 'normal')]}
        self.dirty = True

    # ------------------------------------------------------------ lock & power
    def lock(self):
        self.locked = True
        self.lock_drag = 0
        self.pin_entry = None
        self.close_overlays()
        self.dirty = True

    def unlock(self):
        if self.settings.get('pin'):
            self.pin_entry = ''
        else:
            self.locked = False
        self.dirty = True

    def power_menu(self):
        self.choose('Power', ['Lock', 'Restart', 'Shut Down'],
                    lambda i: [self.lock, self.restart, self.shutdown][i]())

    def restart(self):
        self.running.clear()
        self.stack.clear()
        self.current = None
        self.phase = 'boot'
        self.boot_t = time.time()
        self.dirty = True

    def shutdown(self):
        self.sound('shutdown')
        self.phase = 'off'
        self.off_t = time.time()
        self.dirty = True

    # ------------------------------------------------------------ time
    def clock_text(self, secs=False):
        if self.settings.get('clock24'):
            return time.strftime('%H:%M:%S' if secs else '%H:%M')
        t = time.strftime('%I:%M:%S %p' if secs else '%I:%M %p')
        return t.lstrip('0')

    def tick(self, dt):
        now = time.time()
        if self.phase == 'boot':
            if now - self.boot_t > (2.2 if self.settings.get('boot_screen') else 0.1):
                self.phase = 'run'
                self.sound('boot')
                if self.settings.get('lock_on_start'):
                    self.lock()
                self.emit('started')
                if not self.settings.get('oobe'):
                    self.oobe()
            self.dirty = True
        elif self.phase == 'off':
            if now - self.off_t > 1.6:
                self.quit = True
            self.dirty = True
        if self.anim and now - self.anim[2] > 0.25:
            self.anim = None
            self.dirty = True
        elif self.anim:
            self.dirty = True
        if abs(self.shade - self.shade_target) > 0.001:
            step = dt / 0.18
            self.shade = min(self.shade_target, self.shade + step) if self.shade < self.shade_target \
                else max(self.shade_target, self.shade - step)
            self.dirty = True
        before = len(self.toasts)
        self.toasts = [t for t in self.toasts if t[1] > now]
        if len(self.toasts) != before:
            self.dirty = True
        minute = time.strftime('%H:%M')
        if minute != self.last_minute:
            self.last_minute = minute
            self.dirty = True
        if self.current and self.current in self.running and not self.locked:
            if self.guard(self.current, self.running[self.current].tick, dt):
                self.dirty = True
        self.emit('tick', dt)

    def oobe(self):
        def named(name):
            self.set('user', (name or '').strip() or 'User')
            self.set('oobe', True)
            self.notify('Welcome, %s!' % self.settings['user'], 'Drag down from the top for notifications.', quiet=True)
        self.prompt('Set Up COS Neptune Mobile', 'Welcome! What should Neptune call you?', named, '')

    # ================================================================ input
    ptr_last = None

    def pointer(self, kind, x, y):
        now = time.time()
        self.ptr_last = (x, y)
        if kind == 'down':
            self.ptr = {'x0': x, 'y0': y, 'x': x, 'y': y, 't': now, 'drag': False, 'held': False,
                        'target': self.drag_target(x, y)}
            return
        p = self.ptr
        if not p:
            return
        if kind == 'move':
            dx, dy = x - p['x'], y - p['y']
            p['x'], p['y'] = x, y
            if not p['drag'] and math.hypot(x - p['x0'], y - p['y0']) > DRAG_START:
                p['drag'] = True
                dx, dy = x - p['x0'], y - p['y0']
            if p['drag']:
                self.drag(p, dx, dy, x, y)
            return
        if kind == 'up':
            self.ptr = None
            if p['drag']:
                self.drag_end(p, x, y)
            elif not p['held']:
                self.tap(x, y)
            self.dirty = True

    def check_hold(self):
        p = self.ptr
        if p and not p['drag'] and not p['held'] and time.time() - p['t'] > LONG_PRESS:
            p['held'] = True
            hit = self.hits.find(p['x'], p['y'])
            if hit and hit[2]:
                self.run_hit(hit[2])
            self.dirty = True

    def drag_target(self, x, y):
        if self.phase != 'run':
            return None
        if self.dialog:
            return 'dialog'
        if self.locked:
            return 'lock'
        if self.shade > 0.05:
            return 'shade'
        if self.recents:
            return 'recents'
        if y < STATUS_H + 6:
            return 'pull'
        if y > H - NAV_H:
            return 'nav'
        if self.current:
            return 'app'
        return 'home'

    def drag(self, p, dx, dy, x, y):
        t = p['target']
        if t == 'lock' and self.pin_entry is None:
            self.lock_drag = max(0, p['y0'] - y)
        elif t in ('pull', 'shade') or (t == 'home' and p['y0'] < H * 0.6 and y > p['y0']):
            if t != 'shade' and 's0' not in p:
                p['target'], p['s0'] = 'pull', 0.0
            s0 = p.setdefault('s0', self.shade)
            self.shade = self.shade_target = max(0.0, min(1.0, s0 + (y - p['y0']) / self.shade_h()))
        elif t == 'recents':
            self.recents_x = max(0, min(self.recents_max(), self.recents_x - dx))
        elif t == 'app' and self.current in self.running:
            self.guard(self.current, self.running[self.current].drag, dx, dy, x, y)
        self.dirty = True

    def drag_end(self, p, x, y):
        t = p['target']
        if t == 'lock' and self.pin_entry is None:
            if self.lock_drag > 140:
                self.unlock()
            self.lock_drag = 0
        elif t in ('pull', 'shade'):
            self.shade_target = 1.0 if self.shade > 0.4 else 0.0
        elif t == 'recents':
            # a card flicked upward closes that app
            if p['y0'] - y > 120 and abs(x - p['x0']) < 80:
                hit = self.card_at(p['x0'], p['y0'])
                if hit:
                    self.close(hit)
        elif t == 'app' and self.current in self.running:
            self.guard(self.current, self.running[self.current].release, x, y)
        elif t == 'nav' and p['y0'] - y > 60:          # swipe up from the nav bar: Recents
            self.recents = True
        self.dirty = True

    def tap(self, x, y):
        hit = self.hits.find(x, y)
        if hit:
            self.run_hit(hit[1])
        elif self.recents:
            self.recents = False
        elif self.shade > 0.05 and y > self.shade_h():
            self.close_shade()
        elif self.dialog and self.dialog.get('options') is None and self.dialog.get('kind') != 'crash':
            pass
        self.dirty = True

    def run_hit(self, fn):
        if not fn:
            return
        app = self.current if (self.current and not self.dialog and not self.recents and self.shade < 0.05) else None
        if app:
            self.guard(app, fn)
        else:
            try:
                fn()
            except Exception as e:
                traceback.print_exc()
                self.toast('Error: %s' % e)

    def wheel(self, dy):
        if self.recents:
            self.recents_x = max(0, min(self.recents_max(), self.recents_x - dy * 60))
        elif self.current and not self.locked and not self.dialog and self.shade < 0.05:
            self.guard(self.current, self.running[self.current].wheel, dy)
        self.dirty = True

    def key(self, ev):
        k, ch = ev.get('key'), ev.get('char', '')
        if ev.get('ctrl') and k == 'q':
            self.quit = True
            return
        if k == 'f5':
            return self.screenshot()
        if self.dialog and 'field' in self.dialog:
            d = self.dialog
            if k == 'enter':
                return self.press_dialog(len(d['buttons']) - 1)
            if k == 'escape':
                return self.back()
            if k == 'backspace':
                d['field'] = d['field'][:-1]
            elif ch:
                d['field'] += ch
            self.dirty = True
            return
        if self.locked and self.pin_entry is not None:
            if ch.isdigit():
                self.pin_digit(ch)
            elif k == 'backspace':
                self.pin_entry = self.pin_entry[:-1]
            elif k == 'escape':
                self.pin_entry = None
            self.dirty = True
            return
        if self.locked:
            if k in ('enter', 'space', 'up'):
                self.unlock()
            return
        if self.current and not self.dialog and self.shade < 0.05 and not self.recents:
            if self.guard(self.current, self.running[self.current].key, ev):
                self.dirty = True
                return
        if k == 'escape':
            self.back()
        elif k == 'home' and ev.get('alt'):
            self.go_home()
        self.dirty = True

    def pin_digit(self, ch):
        self.pin_entry += ch
        if len(self.pin_entry) >= len(self.settings.get('pin', '')):
            if self.pin_entry == self.settings.get('pin'):
                self.locked = False
                self.pin_entry = None
            else:
                self.pin_entry = ''
                self.toast('Wrong PIN')
        self.dirty = True

    def press_dialog(self, i):
        d = self.dialog
        if not d:
            return
        self.dialog = None
        fn = d['buttons'][i][1]
        if fn:
            try:
                fn()
            except Exception as e:
                traceback.print_exc()
                self.toast('Error: %s' % e)
        self.dirty = True

    def screenshot(self):
        if getattr(self, 'frame', None) is None:
            return
        fn = os.path.join(HOME, 'Pictures', time.strftime('Screenshot %Y-%m-%d %H.%M.%S.png'))
        self.frame.convert('RGB').save(fn)
        self.toast('Screenshot saved to Pictures')

    # ================================================================ drawing
    home_cache = None
    blur_cache = None

    def render(self):
        self.hits.clear()
        if self.phase == 'boot':
            img = self.draw_boot()
        elif self.phase == 'off':
            img = self.draw_off()
        else:
            img = ui.wall(self.settings['wallpaper']).copy()
            if self.locked:
                self.draw_lock(img)
            else:
                self.draw_main(img)
                self.draw_status(img)
                self.draw_nav(img)
                if self.recents:
                    self.draw_recents(img)
                if self.shade > 0.01:
                    self.draw_shade(img)
            if self.dialog:
                self.draw_dialog(img)
            self.draw_toasts(img)
            b = self.settings.get('brightness', 100)
            if b < 100:
                img.alpha_composite(Image.new('RGBA', img.size, (0, 0, 0, int((100 - b) * 2.0))))
        self.frame = img
        self.dirty = False
        return img

    # ---------------------------------------------------------------- boot / off
    def draw_boot(self):
        img = Image.new('RGBA', (W, H), (0, 0, 0, 255))
        t = time.time() - self.boot_t
        a = min(1.0, t / 0.6)
        lg = ui.logo(70)
        pulse = 1 + 0.03 * math.sin(t * 5)
        s = int(lg.size[0] * pulse)
        l2 = gfx.with_alpha(lg.resize((s, s), Image.LANCZOS), a)
        img.alpha_composite(l2, (W // 2 - s // 2, int(H * 0.40) - s // 2))
        ui.text(img, (W // 2, H * 0.40 + 110), 'COS Neptune', 30, (255, 255, 255, int(255 * a)), kind='serif', anchor='mm')
        ui.text(img, (W // 2, H * 0.40 + 146), 'MOBILE', 13, (150, 220, 230, int(255 * a)), bold=True, anchor='mm')
        # the progress dots
        for i in range(5):
            ph = (t * 1.6 - i * 0.15) % 1.0
            c = int(80 + 175 * max(0, math.sin(ph * math.pi)))
            x = W // 2 - 40 + i * 20
            ImageDraw.Draw(img).ellipse((x - 4, H * 0.75 - 4, x + 4, H * 0.75 + 4), fill=(c // 2, c, c, 255))
        ui.text(img, (W // 2, H - 30), '%s  Build %s' % (FULLNAME, self.build), 11, (120, 130, 140, 255), anchor='mm')
        return img

    def draw_off(self):
        img = Image.new('RGBA', (W, H), (0, 0, 0, 255))
        a = max(0.0, 1 - (time.time() - self.off_t) / 1.4)
        ui.text(img, (W // 2, H // 2), 'Shutting down...', 20, (255, 255, 255, int(255 * a)), kind='serif', anchor='mm')
        return img

    # ---------------------------------------------------------------- status & nav
    def draw_status(self, img):
        dark = Image.new('RGBA', (W, STATUS_H), (0, 0, 0, 70))
        img.alpha_composite(dark)
        title = self.running[self.current].title if self.current in self.running else 'COS'
        ui.text(img, (12, STATUS_H // 2), self.clock_text(), 14, bold=True, anchor='lm', shadow=True)
        ui.text(img, (W // 2, STATUS_H // 2), title, 12, (230, 240, 245, 255), anchor='mm', width=170)
        x = W - 10
        # battery (desktops have none: a plug)
        cap = self.battery()
        d = ImageDraw.Draw(img)
        cy = STATUS_H // 2
        if cap is None:
            ui.text(img, (x, cy), 'AC', 12, bold=True, anchor='rm', shadow=True)
            x -= 24
        else:
            d.rounded_rectangle((x - 24, cy - 6, x - 2, cy + 6), 2, outline=(255, 255, 255, 230), width=1)
            d.rectangle((x - 2, cy - 3, x, cy + 3), fill=(255, 255, 255, 230))
            fw = int(19 * cap / 100)
            d.rectangle((x - 22, cy - 4, x - 22 + fw, cy + 4), fill=(255, 255, 255, 255) if cap > 20 else (255, 90, 80, 255))
            ui.text(img, (x - 28, cy), '%d%%' % cap, 12, anchor='rm', shadow=True)
            x -= 64
        # wifi bars
        for i in range(4):
            h = 3 + i * 3
            d.rectangle((x - 18 + i * 5, cy + 6 - h, x - 15 + i * 5, cy + 6), fill=(255, 255, 255, 235))
        x -= 26
        if self.badges.get('ibbs'):
            d.ellipse((x - 9, cy - 4, x - 1, cy + 4), fill=(250, 190, 60, 255))
            x -= 14
        if self.notes:
            ui.text(img, (x, cy), '● %d' % len(self.notes), 11, (220, 235, 245, 255), anchor='rm')

    _bat = (0, None)

    def battery(self):
        if time.time() - self._bat[0] < 30:
            return self._bat[1]
        cap = None
        base = '/sys/class/power_supply'
        try:
            for n in os.listdir(base):
                if n.startswith('BAT'):
                    with open(os.path.join(base, n, 'capacity')) as f:
                        cap = int(f.read().strip())
                    break
        except (OSError, ValueError):
            pass
        self._bat = (time.time(), cap)
        return cap

    def draw_nav(self, img):
        y0 = H - NAV_H
        ui.glass(img, (0, y0, W, H), self.tint, self.settings['intensity'] * 0.8, r=0, border=False)
        ImageDraw.Draw(img).line((0, y0, W, y0), fill=(255, 255, 255, 90))
        cy = y0 + NAV_H // 2
        d = ImageDraw.Draw(img)
        # back
        bx = W // 2 - 120
        d.polygon([(bx - 9, cy), (bx + 7, cy - 10), (bx + 7, cy + 10)], outline=(255, 255, 255, 240), width=2)
        self.hits.add((bx - 40, y0, bx + 40, H), self.back)
        # home orb: the COS bubble
        orb = ui.logo(17)
        img.alpha_composite(orb, (W // 2 - orb.size[0] // 2, cy - orb.size[1] // 2))
        self.hits.add((W // 2 - 40, y0, W // 2 + 40, H), self.go_home, hold=self.power_menu)
        # recents
        rx = W // 2 + 120
        d.rounded_rectangle((rx - 9, cy - 9, rx + 9, cy + 9), 3, outline=(255, 255, 255, 240), width=2)
        self.hits.add((rx - 40, y0, rx + 40, H), self.toggle_recents)

    def toggle_recents(self):
        self.recents = not self.recents
        self.recents_x = max(0, self.recents_max())
        self.shade_target = 0
        self.dirty = True

    # ---------------------------------------------------------------- home & windows
    def draw_main(self, img):
        anim = self.anim
        if self.current in self.running:
            layer = self.draw_window(self.current)
            self.snapshot(self.current, layer)
            if anim and anim[0] == 'open' and anim[1] == self.current:
                self.draw_home(img, hits=False)
                t = gfx.ease_out(min(1.0, (time.time() - anim[2]) / 0.25))
                self.composite_scaled(img, layer, 0.85 + 0.15 * t, t)
            else:
                img.alpha_composite(layer, (0, STATUS_H))
        else:
            self.draw_home(img)
            if anim and anim[0] in ('home', 'close') and anim[1] in self.snapshots_full:
                t = gfx.ease_in_out(min(1.0, (time.time() - anim[2]) / 0.25))
                self.composite_scaled(img, self.snapshots_full[anim[1]], 1 - 0.25 * t, 1 - t)

    snapshots_full = {}

    def composite_scaled(self, img, layer, scale, alpha):
        w, h = max(1, int(layer.size[0] * scale)), max(1, int(layer.size[1] * scale))
        l2 = gfx.with_alpha(layer.resize((w, h), Image.BILINEAR), max(0.0, min(1.0, alpha)))
        img.alpha_composite(l2, ((W - w) // 2, STATUS_H + (layer.size[1] - h) // 2))

    def snapshot(self, app_id, layer):
        self.snapshots_full[app_id] = layer
        self.snapshots[app_id] = layer

    def draw_window(self, app_id):
        """The app's full-screen window (status bar to nav bar) as its own layer."""
        app = self.running[app_id]
        cls = self.apps[app_id]
        wh = H - STATUS_H - NAV_H
        base = ui.wall(self.settings['wallpaper']).crop((0, STATUS_H, W, STATUS_H + wh)).copy()
        layer = base
        ui.glass(layer, (0, 0, W, TITLE_H), self.tint, self.settings['intensity'], r=0, border=False)
        ImageDraw.Draw(layer).line((0, TITLE_H - 1, W, TITLE_H - 1), fill=(0, 0, 0, 90))
        ic = ui.bubble_icon(cls.icon, 30, cls.c1, cls.c2)
        layer.alpha_composite(ic, (10, TITLE_H // 2 - 15))
        gfx.glow_text(layer, (48, TITLE_H // 2), app.title, ui.font(17), fill=(10, 20, 30, 255), anchor='lm')
        # the red close button from the concept art
        bx0, by0 = W - 52, 8
        g = ui.vgrad(42, TITLE_H - 16, (235, 120, 100, 255), (170, 20, 20, 255))
        g.putalpha(gfx.rounded_mask(42, TITLE_H - 16, 6))
        layer.alpha_composite(g, (bx0, by0))
        ui.rrect(layer, (bx0, by0, bx0 + 42, by0 + TITLE_H - 16), 6, outline=(60, 0, 0, 170))
        ui.text(layer, (bx0 + 21, TITLE_H // 2), '✕', 15, bold=True, anchor='mm')
        # client area
        client = (0, TITLE_H, W, wh)
        bg = (28, 32, 40, 255) if cls.dark else ui.PAPER
        ImageDraw.Draw(layer).rectangle(client, fill=bg)
        # app draws in screen coordinates; give it a scratch canvas the size of the screen
        sub = ui.Hits()
        canvas = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        canvas.paste(layer, (0, STATUS_H))
        box = (0, STATUS_H + TITLE_H, W, H - NAV_H)
        titlebar = layer.crop((0, 0, W, TITLE_H))
        self.guard(app_id, app.draw, canvas, box, sub)
        if app_id not in self.running:          # it crashed while drawing
            return layer
        canvas.paste(titlebar, (0, STATUS_H))   # whatever scrolled up under the title bar
        layer = canvas.crop((0, STATUS_H, W, H - NAV_H))
        # clip the app's hit boxes to its client area, then the title bar's
        for b, fn, hold in sub.boxes:
            clipped = (b[0], max(b[1], box[1]), b[2], min(b[3], box[3]))
            if clipped[3] > clipped[1]:
                self.hits.add(clipped, fn, hold)
        self.hits.add((bx0 - 6, STATUS_H, W, STATUS_H + TITLE_H), lambda: self.close(app_id))
        return layer

    def home_layout(self):
        cols, size = 4, 62
        cell_w = W // cols
        y = STATUS_H + 250
        items = []
        grid = [a for a in self.order if a not in self.settings.get('dock', [])]
        for i, a in enumerate(grid):
            r, c = divmod(i, cols)
            cx = c * cell_w + cell_w // 2
            cy = y + r * 96
            items.append((a, cx, cy, size))
        dock_y = H - NAV_H - 50
        dock = [a for a in self.settings.get('dock', []) if a in self.apps]
        for i, a in enumerate(dock):
            cx = int(W * (i + 0.5) / max(1, len(dock)))
            items.append((a, cx, dock_y, 58))
        return items, dock_y

    def draw_home(self, img, hits=True):
        if self.home_cache is None:
            lay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            items, dock_y = self.home_layout()
            base = ui.wall(self.settings['wallpaper']).copy()
            ui.glass(base, (10, dock_y - 42, W - 10, dock_y + 40), self.tint, self.settings['intensity'], r=22)
            lay.alpha_composite(base.crop((10, dock_y - 42, W - 10, dock_y + 40)), (10, dock_y - 42))
            for a, cx, cy, size in items:
                cls = self.apps[a]
                ic = ui.bubble_icon(cls.icon, size, cls.c1, cls.c2)
                lay.alpha_composite(ic, (cx - size // 2, cy - size // 2))
                if cy < dock_y - 50:
                    ui.text(lay, (cx, cy + size // 2 + 12), cls.name, 12, anchor='mm', shadow=True, width=W // 4 - 6)
                n = self.badges.get(a)
                if n:
                    bx, by = cx + size // 2 - 8, cy - size // 2 + 4
                    ImageDraw.Draw(lay).ellipse((bx - 10, by - 10, bx + 10, by + 10), fill=(215, 30, 30, 255),
                                                outline=(255, 255, 255, 255), width=2)
                    ui.text(lay, (bx, by), str(min(n, 99)), 11, bold=True, anchor='mm')
            self.home_cache = (lay, items)
        lay, items = self.home_cache
        self.draw_clock_widget(img)
        img.alpha_composite(lay)
        if hits:
            for a, cx, cy, size in items:
                self.hits.add((cx - 40, cy - size // 2 - 4, cx + 40, cy + size // 2 + 22),
                              (lambda a=a: self.open(a)), hold=(lambda a=a: self.app_info(a)))

    def app_info(self, a):
        cls = self.apps[a]
        opts = ['Open', 'Remove from dock' if a in self.settings['dock'] else 'Add to dock', 'Close it'
                if a in self.running else None]
        opts = [o for o in opts if o]

        def pick(i):
            o = opts[i]
            if o == 'Open':
                self.open(a)
            elif o == 'Close it':
                self.close(a)
            else:
                dock = [d for d in self.settings['dock'] if d != a]
                if o.startswith('Add') and len(dock) < 5:
                    dock.append(a)
                elif o.startswith('Add'):
                    return self.toast('The dock holds 5 apps')
                self.set('dock', dock)
                self.home_cache = None
        self.choose(cls.name, opts, pick)

    def draw_clock_widget(self, img):
        x0, y0, x1, y1 = 14, STATUS_H + 18, W - 14, STATUS_H + 200
        ui.glass(img, (x0, y0, x1, y1), self.tint, self.settings['intensity'] * 0.8, r=22)
        cx, cy, r = x0 + 92, (y0 + y1) // 2, 70
        with ui.Layer(img) as d:
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(0, 20, 40, 70), outline=(255, 255, 255, 170), width=2)
        d = ImageDraw.Draw(img)
        for i in range(12):
            a = ui.angle_xy(cx, cy, r - 7, i / 12)
            b = ui.angle_xy(cx, cy, r - (14 if i % 3 == 0 else 10), i / 12)
            d.line((a, b), fill=(255, 255, 255, 220), width=3 if i % 3 == 0 else 1)
        t = time.localtime()
        hr = ((t.tm_hour % 12) + t.tm_min / 60) / 12
        mn = (t.tm_min + t.tm_sec / 60) / 60
        d.line(((cx, cy), ui.angle_xy(cx, cy, r * 0.5, hr)), fill=(255, 255, 255, 255), width=5)
        d.line(((cx, cy), ui.angle_xy(cx, cy, r * 0.75, mn)), fill=(255, 255, 255, 255), width=3)
        d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(250, 90, 70, 255))
        tx = cx + r + 22
        clock = self.clock_text()
        parts = clock.split(' ')
        ui.text(img, (tx, cy - 30), parts[0], 44, kind='light', anchor='lm', shadow=True)
        if len(parts) > 1:
            ui.text(img, (tx + ui.text_w(parts[0], 44, kind='light') + 6, cy - 22), parts[1], 16, anchor='lm', shadow=True)
        ui.text(img, (tx, cy + 14), time.strftime('%A'), 16, bold=True, anchor='lm', shadow=True)
        ui.text(img, (tx, cy + 36), time.strftime('%B %-d, %Y'), 14, anchor='lm', shadow=True)
        hi = 'Hi, %s' % (self.settings.get('user') or 'there')
        ui.text(img, (tx, cy + 60), hi, 13, (200, 235, 240, 255), anchor='lm', width=x1 - tx - 10)

    # ---------------------------------------------------------------- recents
    CARD_W, CARD_H = 230, 440

    def recents_max(self):
        return max(0, (len(self.stack) - 1) * (self.CARD_W + 20))

    def card_rects(self):
        out = []
        cy = STATUS_H + 60
        for i, a in enumerate(self.stack):
            x = W // 2 - self.CARD_W // 2 + i * (self.CARD_W + 20) - self.recents_x
            out.append((a, (int(x), cy, int(x + self.CARD_W), cy + self.CARD_H)))
        return out

    def card_at(self, x, y):
        for a, r in self.card_rects():
            if r[0] <= x < r[2] and r[1] <= y < r[3]:
                return a
        return None

    def draw_recents(self, img):
        img.alpha_composite(Image.new('RGBA', (W, H - NAV_H), (0, 10, 25, 150)))
        ui.text(img, (W // 2, STATUS_H + 28), 'Recent Apps', 18, kind='serif', anchor='mm', shadow=True)
        self.hits.add((0, 0, W, H - NAV_H), lambda: setattr(self, 'recents', False))
        if not self.stack:
            ui.text(img, (W // 2, H // 2), 'No apps open', 16, (220, 230, 240, 255), anchor='mm')
            return
        for a, (x0, y0, x1, y1) in self.card_rects():
            if x1 < 0 or x0 > W:
                continue
            cls = self.apps[a]
            ui.glass(img, (x0 - 6, y0 - 40, x1 + 6, y1 + 6), self.tint, self.settings['intensity'], r=16)
            ic = ui.bubble_icon(cls.icon, 26, cls.c1, cls.c2)
            img.alpha_composite(ic, (x0, y0 - 34))
            ui.text(img, (x0 + 32, y0 - 21), cls.name, 14, bold=True, anchor='lm', shadow=True, width=self.CARD_W - 70)
            snap = self.snapshots.get(a)
            if snap:
                th = snap.resize((x1 - x0, y1 - y0), Image.BILINEAR)
                th.putalpha(gfx.rounded_mask(x1 - x0, y1 - y0, 10))
                gfx.alpha_composite_clip(img, th, x0, y0)
            else:
                ui.rrect(img, (x0, y0, x1, y1), 10, fill=(240, 244, 248, 255))
            self.hits.add((x0, y0, x1, y1), lambda a=a: self.open(a))
            ui.button(img, self.hits, (x1 - 34, y0 - 36, x1, y0 - 8), '✕', lambda a=a: self.close(a), 'danger',
                      size=13, r=6)
        ui.button(img, self.hits, (W // 2 - 60, H - NAV_H - 52, W // 2 + 60, H - NAV_H - 16), 'Close all',
                  self.close_all, size=14, r=18)
        ui.text(img, (W // 2, H - NAV_H - 66), 'Flick a card up to close it', 11, (210, 225, 235, 255), anchor='mm')

    def close_all(self):
        for a in list(self.running):
            self.close(a)
        self.recents = False
        self.anim = None

    # ---------------------------------------------------------------- shade
    def shade_h(self):
        return 560

    def draw_shade(self, img):
        sh = self.shade_h()
        top = int(-sh + sh * gfx.ease_out(self.shade))
        img.alpha_composite(Image.new('RGBA', (W, H), (0, 0, 0, int(110 * self.shade))))
        if top + sh <= 0:
            return
        panel = Image.new('RGBA', (W, sh), (0, 0, 0, 0))
        base = ui.wall(self.settings['wallpaper']).crop((0, 0, W, sh)).copy()
        ui.glass(base, (0, 0, W, sh), self.tint, min(0.85, self.settings['intensity'] + 0.25), r=18)
        panel.alpha_composite(base)
        h = ui.Hits()
        ui.text(panel, (18, 30), self.clock_text(), 34, kind='light', anchor='lm', shadow=True)
        ui.text(panel, (18, 62), time.strftime('%A, %B %-d'), 14, anchor='lm', shadow=True)
        # quick tiles
        s = self.settings
        tiles = [('Sound', s.get('sounds'), lambda: self.set('sounds', not s.get('sounds'))),
                 ('24-hour', s.get('clock24'), lambda: self.set('clock24', not s.get('clock24'))),
                 ('IBBS alerts', s.get('ibbs_notify'), lambda: self.set('ibbs_notify', not s.get('ibbs_notify'))),
                 ('Animations', s.get('animations'), lambda: self.set('animations', not s.get('animations'))),
                 ('Lock', False, self.lock),
                 ('Settings', False, lambda: self.open('settings'))]
        tw = (W - 36 - 2 * 10) // 3
        for i, (label, on, fn) in enumerate(tiles):
            r, c = divmod(i, 3)
            x0, y0 = 18 + c * (tw + 10), 88 + r * 58
            ui.rrect(panel, (x0, y0, x0 + tw, y0 + 48), 12,
                     fill=(255, 255, 255, 220) if on else (0, 0, 0, 70), outline=(255, 255, 255, 120))
            ui.text(panel, (x0 + tw // 2, y0 + 24), label, 13, ui.INK if on else ui.WHITE, bold=True, anchor='mm',
                    width=tw - 8)
            h.add((x0, y0, x0 + tw, y0 + 48), fn)
        # brightness
        by = 222
        ui.text(panel, (18, by), '☀', 18, anchor='lm', shadow=True)
        bx0, bx1 = 44, W - 22
        ui.rrect(panel, (bx0, by - 4, bx1, by + 4), 4, fill=(0, 0, 0, 90))
        bw = int((bx1 - bx0) * (s.get('brightness', 100) - 20) / 80)
        ui.rrect(panel, (bx0, by - 4, bx0 + bw, by + 4), 4, fill=(255, 255, 255, 230))
        ImageDraw.Draw(panel).ellipse((bx0 + bw - 10, by - 10, bx0 + bw + 10, by + 10), fill=ui.WHITE)
        for i in range(9):
            lvl = 20 + i * 10
            sx = bx0 + (bx1 - bx0) * i // 8
            h.add((sx - 18, by - 16, sx + 18, by + 16), lambda lvl=lvl: self.set('brightness', lvl))
        # notifications
        ny = 252
        ui.text(panel, (18, ny + 10), 'NOTIFICATIONS', 12, (220, 240, 245, 255), bold=True, anchor='lm')
        if self.notes:
            ui.button(panel, h, (W - 90, ny - 2, W - 18, ny + 22), 'Clear all', self.clear_notes, 'flat', size=12)
        y = ny + 30
        if not self.notes:
            ui.text(panel, (W // 2, y + 40), 'No notifications', 14, (220, 230, 240, 255), anchor='mm')
        for n in self.notes[:4]:
            ui.rrect(panel, (14, y, W - 14, y + 58), 12, fill=(255, 255, 255, 225))
            cls = self.apps.get(n.get('app'))
            if cls:
                panel.alpha_composite(ui.bubble_icon(cls.icon, 34, cls.c1, cls.c2), (22, y + 12))
            ui.text(panel, (64, y + 18), n['title'], 14, ui.INK, bold=True, anchor='lm', width=W - 150)
            ui.text(panel, (W - 24, y + 18), time.strftime('%H:%M', time.localtime(n['time'])), 11, ui.DIM, anchor='rm')
            ui.text(panel, (64, y + 40), n['body'], 13, ui.DIM, anchor='lm', width=W - 100)
            h.add((14, y, W - 14, y + 58), lambda n=n: self.open_note(n))
            y += 64
        if len(self.notes) > 4:
            ui.text(panel, (W // 2, y + 8), '+%d more' % (len(self.notes) - 4), 12, anchor='mm', shadow=True)
        # the handle
        ui.rrect(panel, (W // 2 - 22, sh - 14, W // 2 + 22, sh - 9), 3, fill=(255, 255, 255, 180))
        gfx.alpha_composite_clip(img, panel, 0, top)
        self.hits.add((0, top + sh, W, H), self.close_shade)
        self.hits.add((0, top, W, top + sh), lambda: None)
        for b, fn, hold in h.boxes:
            self.hits.add((b[0], b[1] + top, b[2], b[3] + top), fn, hold)

    def clear_notes(self):
        self.notes = []
        self.dirty = True

    def open_note(self, n):
        self.notes.remove(n)
        self.close_shade()
        if n.get('app'):
            self.open(n['app'])

    # ---------------------------------------------------------------- lock
    def draw_lock(self, img):
        if self.blur_cache is None or self.blur_cache[0] != self.settings['wallpaper']:
            b = ui.wall(self.settings['wallpaper']).filter(ImageFilter.GaussianBlur(16))
            b.alpha_composite(Image.new('RGBA', (W, H), (0, 10, 25, 90)))
            self.blur_cache = (self.settings['wallpaper'], b)
        off = -int(self.lock_drag)
        a = max(0.0, 1 - self.lock_drag / 260)
        if self.lock_drag:
            img.paste(ui.wall(self.settings['wallpaper']))
            self.draw_home(img, hits=False)
        layer = self.blur_cache[1].copy()
        if self.pin_entry is not None:
            return self.draw_pin(img, layer)
        clock = self.clock_text().split(' ')
        ui.text(layer, (W // 2, 170), clock[0], 88, kind='light', anchor='mm', shadow=True)
        if len(clock) > 1:
            ui.text(layer, (W // 2, 235), clock[1], 18, anchor='mm', shadow=True)
        ui.text(layer, (W // 2, 270), time.strftime('%A, %B %-d'), 20, kind='serif', anchor='mm', shadow=True)
        y = 330
        for n in self.notes[:3]:
            ui.glass(layer, (24, y, W - 24, y + 54), self.tint, self.settings['intensity'], r=14)
            ui.text(layer, (40, y + 18), n['title'], 14, bold=True, anchor='lm', shadow=True, width=W - 90)
            ui.text(layer, (40, y + 38), n['body'], 13, (225, 235, 240, 255), anchor='lm', width=W - 90)
            y += 62
        lg = ui.logo(26)
        layer.alpha_composite(lg, (W // 2 - lg.size[0] // 2, H - 150))
        t = time.time()
        bob = int(4 * math.sin(t * 3))
        ui.text(layer, (W // 2, H - 60 + bob), '⌃', 22, anchor='mm', shadow=True)
        ui.text(layer, (W // 2, H - 38), 'Swipe up to unlock' + (' (PIN)' if self.settings.get('pin') else ''), 14,
                anchor='mm', shadow=True)
        self.hits.add((0, H - 180, W, H), self.unlock)
        if a < 1:
            layer = gfx.with_alpha(layer, a)
        gfx.alpha_composite_clip(img, layer, 0, off)
        self.dirty = True              # the arrow bobs

    def draw_pin(self, img, layer):
        ui.text(layer, (W // 2, 150), 'Enter PIN', 22, kind='serif', anchor='mm', shadow=True)
        n = len(self.settings.get('pin', ''))
        d = ImageDraw.Draw(layer)
        for i in range(n):
            x = W // 2 + (i - (n - 1) / 2) * 26
            filled = i < len(self.pin_entry)
            d.ellipse((x - 7, 200 - 7, x + 7, 200 + 7), fill=ui.WHITE if filled else None, outline=ui.WHITE, width=2)
        keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'Back', '0', '⌫']
        for i, k in enumerate(keys):
            r, c = divmod(i, 3)
            cx, cy = W // 2 + (c - 1) * 96, 300 + r * 92
            if k.isdigit():
                ui.glass(layer, (cx - 36, cy - 36, cx + 36, cy + 36), self.tint, self.settings['intensity'], r=36)
            ui.text(layer, (cx, cy), k, 30 if k.isdigit() else 16, anchor='mm', shadow=True)
            fn = (lambda k=k: self.pin_digit(k)) if k.isdigit() else (
                (lambda: setattr(self, 'pin_entry', None)) if k == 'Back' else (lambda: setattr(self, 'pin_entry', self.pin_entry[:-1])))
            self.hits.add((cx - 40, cy - 40, cx + 40, cy + 40), fn)
        img.alpha_composite(layer)

    # ---------------------------------------------------------------- dialog
    def draw_dialog(self, img):
        d = self.dialog
        img.alpha_composite(Image.new('RGBA', (W, H), (0, 0, 0, 120)))
        self.hits.add((0, 0, W, H), lambda: None)          # modal: swallow taps
        x0, x1 = 24, W - 24
        inner = x1 - x0 - 36
        crash = d.get('kind') == 'crash'
        lines = ui.wrap(d.get('message', ''), 15, inner - (60 if crash else 0)) if d.get('message') else []
        detail = ui.wrap(d.get('detail', ''), 12, inner, kind='mono')[:6] if d.get('detail') else []
        opts = d.get('options') or []
        hgt = 56 + len(lines) * 21 + (len(detail) * 16 + 16 if detail else 0) + (52 if 'field' in d else 0) \
            + len(opts) * 50 + 70 + (20 if crash else 0)
        y0 = max(STATUS_H + 10, (H - hgt) // 2)
        y1 = y0 + hgt
        ui.glass(img, (x0, y0, x1, y1), self.tint, self.settings['intensity'], r=12)
        gfx.glow_text(img, (x0 + 16, y0 + 22), d.get('title', ''), ui.font(17), fill=(10, 20, 30, 255), anchor='lm')
        ImageDraw.Draw(img).rectangle((x0 + 8, y0 + 42, x1 - 8, y1 - 8), fill=(250, 252, 254, 255))
        y = y0 + 58
        tx = x0 + 18
        if crash:
            ic = ui.icons.get('error', 44)
            img.alpha_composite(ic, (tx, y - 4))
            tx += 60
        for ln in lines:
            ui.text(img, (tx, y + 8), ln, 15, ui.INK, anchor='lm')
            y += 21
        if crash:
            y += 20
        if detail:
            ui.rrect(img, (x0 + 16, y, x1 - 16, y + len(detail) * 16 + 8), 6, fill=(238, 241, 245, 255), outline=ui.LINE)
            for i, ln in enumerate(detail):
                ui.text(img, (x0 + 24, y + 12 + i * 16), ln, 12, ui.RED, kind='mono', anchor='lm')
            y += len(detail) * 16 + 16
        if 'field' in d:
            ui.rrect(img, (x0 + 16, y + 4, x1 - 16, y + 44), 8, fill=ui.WHITE, outline=(80, 140, 200, 255), width=2)
            shown = ('•' * len(d['field'])) if d.get('secret') else d['field']
            caret = '|' if int(time.time() * 2) % 2 == 0 else ' '
            ui.text(img, (x0 + 28, y + 24), shown[-34:] + caret, 16, ui.INK, anchor='lm')
            y += 52
            self.dirty = True
        for i, o in enumerate(opts):
            def pick(i=i):
                self.dialog = None
                d['pick'](i)
            ui.button(img, self.hits, (x0 + 16, y + 4, x1 - 16, y + 44), o, pick, size=15)
            y += 50
        btns = d['buttons']
        bw = (x1 - x0 - 32 - (len(btns) - 1) * 10) // len(btns)
        by = y1 - 58
        for i, (label, fn, kind) in enumerate(btns):
            bx = x0 + 16 + i * (bw + 10)
            ui.button(img, self.hits, (bx, by, bx + bw, by + 42), label, lambda i=i: self.press_dialog(i), kind, size=14)

    def draw_toasts(self, img):
        y = H - NAV_H - 80
        for msg, _ in reversed(self.toasts):
            lines = ui.wrap(msg, 14, W - 90)[:3]
            h = 18 + 19 * len(lines)
            wmax = max(ui.text_w(ln, 14) for ln in lines) + 36
            x0 = (W - wmax) // 2
            ui.rrect(img, (x0, y - h, x0 + wmax, y), h // 2 if len(lines) == 1 else 14, fill=(20, 28, 36, 225),
                     outline=(255, 255, 255, 60))
            for i, ln in enumerate(lines):
                ui.text(img, (W // 2, y - h + 18 + i * 19), ln, 14, anchor='mm')
            y -= h + 8
