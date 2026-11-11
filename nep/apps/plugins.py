"""Neptune apps you write yourself: COS v2.4's .cosapp system, in Aero form.

Drop a Python file ending in .nepapp into  home/.neptune/apps/  and it shows
up in the APPS menu and the Start menu. Neptune watches the folder and
reloads an app when its file changes, so you can edit and see the result
without restarting. The App Manager lists them, shows load errors, and makes
new ones from a template.

A .nepapp needs two things:

    NEPAPP = {'name': 'Hello', 'icon': 'neptune', 'version': '1.0',
              'author': 'you', 'description': '...', 'desktop': False}

    class App(neptune.App):
        def draw(self, img, ui): ...

Everything an app needs is on the `neptune` object it is given: App, gfx,
theme, icons, message, FileDialog, Image, ImageDraw.
"""
import os
import time
import traceback
import types

from PIL import Image, ImageDraw

from .. import gfx, icons, theme
from ..app import App
from .dialogs import FileDialog, message

PLUGIN_DIR = os.path.join(theme.STATE, 'apps')
EXT = '.nepapp'

API = types.SimpleNamespace(App=App, gfx=gfx, theme=theme, icons=icons, message=message, FileDialog=FileDialog,
                            Image=Image, ImageDraw=ImageDraw)

TEMPLATE = '''"""A Neptune app. Save this file and Neptune reloads it by itself."""

NEPAPP = {
    'name':        %(name)r,
    'icon':        'neptune',     # any Neptune icon name: neptune, music, calc, settings, ...
    'version':     '1.0',
    'author':      %(author)r,
    'description': 'Describe your app here.',
    'desktop':     False,         # True to also put it on the desktop
}


class App(neptune.App):
    title = NEPAPP['name']
    icon = NEPAPP['icon']
    size = (420, 260)

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        self.clicks = 0

    def draw(self, img, ui):
        w, h = img.size
        ui.panel_gradient((0, 0, w, h))
        ui.text((20, 20), 'Hello from ' + NEPAPP['name'] + '!', 18, bold=True, color=(0, 51, 153))
        ui.text((20, 56), 'You clicked the button %%d time(s).' %% self.clicks, 12)
        ui.button((20, 90, 140, 116), 'Click me', 'click', default=True)
        ui.button((150, 90, 270, 116), 'Say hi', 'hi')

    def on_command(self, cid, data):
        if cid == 'click':
            self.clicks += 1
        elif cid == 'hi':
            neptune.message(self.shell, NEPAPP['name'], 'Hi there!', 'info', parent=self.win)
'''


class Plugin:
    def __init__(self, path):
        self.path = path
        self.key = 'app:' + os.path.splitext(os.path.basename(path))[0]
        self.meta = {}
        self.cls = None
        self.error = None
        self.mtime = 0

    @property
    def name(self):
        return self.meta.get('name') or os.path.splitext(os.path.basename(self.path))[0]

    def load(self):
        self.mtime = os.path.getmtime(self.path)
        self.cls, self.error = None, None
        try:
            with open(self.path) as f:
                src = f.read()
            ns = {'neptune': API, '__name__': 'nepapp_' + self.key[4:], '__file__': self.path}
            exec(compile(src, self.path, 'exec'), ns)
            self.meta = dict(ns.get('NEPAPP') or {})
            cls = ns.get('App')
            if not (isinstance(cls, type) and issubclass(cls, App)):
                raise TypeError('the file has no "class App(neptune.App)"')
            if 'title' not in cls.__dict__:
                cls.title = self.name
            if 'icon' not in cls.__dict__:
                cls.icon = self.meta.get('icon', 'neptune')
            cls._plugin = self
            self.cls = cls
        except Exception as e:
            self.error = '%s: %s' % (type(e).__name__, e)
            self.trace = traceback.format_exc()
        return self.error is None


class PluginHost:
    """Owned by the shell: keeps the registry in sync with the apps folder."""

    def __init__(self, shell):
        self.shell = shell
        self.plugins = {}
        self.last_scan = 0
        os.makedirs(PLUGIN_DIR, exist_ok=True)
        self.ensure_sample()
        self.scan(quiet=True)

    def ensure_sample(self):
        marker = os.path.join(PLUGIN_DIR, '.sample-installed')
        if os.path.exists(marker):
            return
        src = os.path.join(theme.ROOT, 'nep', 'apps', 'samples')
        try:
            for n in os.listdir(src):
                if n.endswith(EXT) and not os.path.exists(os.path.join(PLUGIN_DIR, n)):
                    with open(os.path.join(src, n)) as f, open(os.path.join(PLUGIN_DIR, n), 'w') as g:
                        g.write(f.read())
            open(marker, 'w').close()
        except OSError:
            pass

    def files(self):
        try:
            return sorted(os.path.join(PLUGIN_DIR, n) for n in os.listdir(PLUGIN_DIR)
                          if n.endswith(EXT) and not n.startswith('.'))
        except OSError:
            return []

    def scan(self, quiet=False):
        self.last_scan = time.time()
        reg = self.shell.registry
        present = set()
        changed = []
        for path in self.files():
            present.add(path)
            p = self.plugins.get(path)
            try:
                mt = os.path.getmtime(path)
            except OSError:
                continue
            if p is None:
                p = self.plugins[path] = Plugin(path)
            elif p.mtime == mt:
                continue
            ok = p.load()
            if ok:
                reg[p.key] = (p.cls, p.name, p.cls.icon)
            else:
                reg.pop(p.key, None)
            changed.append(p)
        for path in list(self.plugins):
            if path not in present:
                reg.pop(self.plugins[path].key, None)
                del self.plugins[path]
                changed.append(None)
        if changed:
            self.shell.refresh_desktop_icons()
            for w in self.shell.windows:
                if isinstance(w.app, AppManager):
                    w.app.invalidate()
            if not quiet:
                for p in changed:
                    if p is None:
                        continue
                    if p.error:
                        self.shell.notify('App failed to load', '%s\n%s' % (os.path.basename(p.path), p.error),
                                          'error', 8)
                    else:
                        self.shell.notify('App reloaded', '%s %s is ready.' % (p.name, p.meta.get('version', '')),
                                          'info', 4)
        return changed

    def tick(self, now):
        if now - self.last_scan > 1.5:
            self.scan()

    def desktop_entries(self):
        return [(p.name, p.cls.icon, p.key) for p in self.plugins.values()
                if p.cls is not None and p.meta.get('desktop')]

    def new_app(self, name):
        stem = ''.join(c for c in name if c.isalnum() or c in '-_') or 'MyApp'
        path = os.path.join(PLUGIN_DIR, stem + EXT)
        i = 2
        while os.path.exists(path):
            path = os.path.join(PLUGIN_DIR, '%s%d%s' % (stem, i, EXT))
            i += 1
        with open(path, 'w') as f:
            f.write(TEMPLATE % {'name': name, 'author': os.environ.get('USER', 'me')})
        self.scan()
        return path


class AppManager(App):
    title = 'App Manager'
    icon = 'plugin'
    size = (640, 440)
    min_size = (480, 320)
    single_instance = True
    bg = theme.CLIENT_BG

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        self.sel = None
        self.newname = {'text': '', 'cur': 0}

    @property
    def host(self):
        return self.shell.plugins

    def items(self):
        return sorted(self.host.plugins.values(), key=lambda p: p.name.lower())

    def draw(self, img, ui):
        w, h = img.size
        # header
        img.alpha_composite(gfx.vgradient(w, 64, ((0, (250, 252, 255)), (1, (225, 236, 248)))), (0, 0))
        ui.icon('plugin', (16, 12), 40)
        ui.text((66, 16), 'Your Neptune apps', 16, color=(30, 57, 91))
        ui.text((66, 40), 'Files ending in %s in %s' % (EXT, PLUGIN_DIR.replace(theme.ROOT, '~/cos/neptune')), 11,
                color=theme.MUTED)
        ui.hline(0, w, 63, (190, 205, 222))
        items = self.items()
        if self.sel not in items:
            self.sel = items[0] if items else None
        # list
        lx1 = 250
        y = 72
        for p in items:
            r = (8, y, lx1, y + 44)
            ui.listrow(r, ('p', p.key), selected=p is self.sel, data=p)
            ui.icon(p.cls.icon if p.cls else 'error', (14, y + 6), 32)
            ui.text((54, y + 13), gfx.ellipsize(gfx.font(12, True), p.name, lx1 - 60), 12, bold=True, anchor='lm')
            st = 'Error' if p.error else 'v%s · %s' % (p.meta.get('version', '?'), p.meta.get('author', 'unknown'))
            ui.text((54, y + 31), gfx.ellipsize(gfx.font(11), st, lx1 - 60), 11,
                    color=(190, 40, 30) if p.error else theme.MUTED, anchor='lm')
            y += 48
            if y > h - 110:
                break
        if not items:
            ui.wrap((16, 80), 'No apps yet. Type a name below and click New App.', lx1 - 20, 12, color=theme.MUTED)
        ui.d.line([(lx1 + 8, 72), (lx1 + 8, h - 52)], fill=(220, 225, 232, 255))
        # details
        dx = lx1 + 22
        if self.sel is not None:
            p = self.sel
            ui.text((dx, 76), p.name, 18, color=(30, 57, 91))
            ui.text((dx, 104), os.path.basename(p.path), 11, color=theme.MUTED)
            if p.error:
                ui.icon('error', (dx, 128), 24)
                yy = ui.wrap((dx + 32, 130), p.error, w - dx - 50, 12, color=(170, 30, 20))
                ui.wrap((dx, yy + 8), 'Fix the file and save it. Neptune reloads it automatically.', w - dx - 20,
                        11, color=theme.MUTED)
            else:
                ui.wrap((dx, 130), p.meta.get('description', 'No description.'), w - dx - 20, 12)
            by = h - 92
            ui.button((dx, by, dx + 80, by + 26), 'Open', 'open', default=True, enabled=p.cls is not None)
            ui.button((dx + 88, by, dx + 168, by + 26), 'Edit', 'edit')
            ui.button((dx + 176, by, dx + 256, by + 26), 'Reload', 'reload')
            ui.button((dx + 264, by, dx + 344, by + 26), 'Remove', 'remove')
        # footer
        ui.rect((0, h - 46, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - 46, (223, 223, 223))
        ui.textbox((12, h - 34, 200, h - 12), 'newname', self.newname, placeholder='New app name')
        ui.button((208, h - 35, 300, h - 11), 'New App', 'new', enabled=bool(self.newname['text'].strip()))
        ui.button((w - 120, h - 35, w - 12, h - 11), 'Open Folder', 'folder')

    def on_command(self, cid, data):
        p = self.sel
        if isinstance(cid, tuple) and cid[0] == 'p':
            self.sel = data
        elif cid == 'open' and p and p.cls:
            self.shell.launch(p.key)
        elif cid == 'edit' and p:
            self.shell.launch('notepad', path=p.path)
        elif cid == 'reload' and p:
            p.mtime = 0
            self.host.scan()
        elif cid == 'remove' and p:
            def go(ans):
                if ans == 'Remove':
                    try:
                        os.remove(p.path)
                    except OSError:
                        pass
                    self.host.scan(quiet=True)
                    self.invalidate()
            message(self.shell, 'App Manager', 'Delete %s? This removes the file\n%s' % (p.name, p.path),
                    'warning', buttons=('Remove', 'Cancel'), callback=go, parent=self.win)
        elif cid in ('new', 'newname') and (cid == 'new' or data == 'enter') and self.newname['text'].strip():
            path = self.host.new_app(self.newname['text'].strip())
            self.newname = {'text': '', 'cur': 0}
            self.sel = self.host.plugins.get(path)
            self.shell.launch('notepad', path=path)
        elif cid == 'folder':
            self.shell.launch('files', path=PLUGIN_DIR)
        self.invalidate()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'p':
            self.sel = data
            self.on_command('open', None)
        else:
            self.on_command(cid, data)
