"""Packages, devices and compatibility, as Neptune programs:

* InstallWizard       installs a .cip or .cdi (Welcome, Read Me, License,
                      Ready, Installing, Finished), Windows 7 wizard style
* ProgramsFeatures    installed packages and COS programs: run, uninstall,
                      compatibility settings, install a package
* DeviceManager       every USB, PCI and input device with the driver bound
                      to it (kernel, Neptune, or a .cdi), Scan for changes
* CosMode             "COS v2.4 Mode": the real COS v2.4 running in a window,
                      sandboxed (no access to your mouse/keyboard devices, its
                      own home folder) the way Windows 7 ran XP Mode
"""
import os
import shlex
import shutil
import threading
import time

from .. import gfx, icons, packages, theme
from ..app import App
from ..compat import host as compat
from .dialogs import FileDialog, message
from .text import Terminal

REL = lambda p: p.replace(theme.ROOT, '~/cos/neptune').replace(os.path.expanduser('~'), '~')


def wrap_lines(text, width, fnt):
    out = []
    for para in text.replace('\r', '').split('\n'):
        line = ''
        for word in para.split(' '):
            t = (line + ' ' + word) if line else word
            if gfx.text_width(fnt, t) > width and line:
                out.append(line)
                line = word
            else:
                line = t
        out.append(line)
    return out


# =============================================================================
class InstallWizard(App):
    title = 'Install Package'
    icon = 'package'
    size = (600, 460)
    resizable = False
    bg = theme.CLIENT_BG

    def __init__(self, shell, path=None, **kw):
        super().__init__(shell, **kw)
        self.path = path
        self.pkg = None
        self.error = ''
        self.page = 'welcome'
        self.accept = False
        self.force = False
        self.scroll = 0
        self.prog = (0, 1, '')
        self.rec = None
        self.run_after = True
        self.history = []
        try:
            self.pkg = packages.Package(path)
            self.title = 'Install %s' % self.pkg.title
            self.icon = 'driver' if self.pkg.type == 'cdi' else 'package'
        except packages.PackageError as e:
            self.error = str(e)
            self.page = 'error'

    # -- page flow ---------------------------------------------------------------------
    def pages(self):
        out = ['welcome']
        if self.pkg.readme():
            out.append('readme')
        if self.pkg.license():
            out.append('license')
        out.append('ready')
        return out

    def go_next(self):
        seq = self.pages()
        if self.page in seq and self.page != 'ready':
            self.history.append(self.page)
            self.page = seq[seq.index(self.page) + 1]
            self.scroll = 0
        elif self.page == 'ready':
            self.start_install()

    def go_back(self):
        if self.history and self.page not in ('installing', 'done', 'error'):
            self.page = self.history.pop()
            self.scroll = 0

    def start_install(self):
        self.page = 'installing'
        self.history = []

        def work():
            try:
                self.rec = self.pkg.install(progress=lambda i, n, m: setattr(self, 'prog', (i, n, m)),
                                            force=self.force)
                time.sleep(0.4)
                self.page = 'done'
            except Exception as e:
                self.error = str(e)
                self.page = 'error'
        threading.Thread(target=work, daemon=True).start()

    @property
    def live(self):
        return self.page == 'installing'

    def on_tick(self, now):
        if self.page == 'installing':
            return True
        if self.page == 'done' and not getattr(self, '_announced', False):
            self._announced = True
            self.after_install()
            return True
        return False

    def after_install(self):
        sh = self.shell
        if getattr(sh, 'compat', None):
            sh.compat.scan()
        if getattr(sh, 'plugins', None):
            sh.plugins.scan(quiet=True)
        if self.pkg.type == 'cdi' and getattr(sh, 'drivers', None):
            sh.drivers.rescan()
            self.matched = [i for i in sh.drivers.instances if i.rec['name'] == self.pkg.name]
        sh.notify('Package installed', '%s %s is ready.' % (self.pkg.title, self.pkg.version), 'info', 5)

    # -- drawing ----------------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        # Aero wizard: white page, big blue title, grey footer
        ui.rect((0, 0, w, h), fill=(255, 255, 255))
        if self.history and self.page not in ('installing', 'done', 'error'):
            r = (10, 10, 36, 36)
            hot = ui.hot(r)
            gfx.round_rect(img, r, 13, fill=gfx.vgradient(26, 26, ((0, (120, 180, 240) if hot else (90, 150, 220)),
                                                                   (1, (30, 80, 170)))), outline=(20, 60, 130, 255))
            ui.d.line([(29, 23), (17, 23)], fill=(255, 255, 255, 255), width=3)
            ui.d.line([(23, 17), (17, 23), (23, 29)], fill=(255, 255, 255, 255), width=3)
            ui.region(r, 'back')
        ui.icon(self.icon if self.page != 'error' else 'error', (44, 12), 24)
        ui.text((76, 24), self.title, 12, anchor='lm')
        body = (40, 50, w - 40, h - 56)
        getattr(self, 'page_' + self.page)(img, ui, body)
        ui.rect((0, h - 46, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - 46, (223, 223, 223))
        self.footer(ui, w, h)

    def heading(self, ui, box, text):
        ui.text((box[0], box[1] + 4), text, 15, color=(0, 51, 153))
        return box[1] + 40

    def footer(self, ui, w, h):
        y0, y1 = h - 35, h - 11
        p = self.page
        if p == 'error':
            ui.button((w - 92, y0, w - 12, y1), 'Close', 'cancel', default=True)
        elif p == 'done':
            ui.button((w - 92, y0, w - 12, y1), 'Finish', 'finish', default=True)
        elif p == 'installing':
            ui.button((w - 92, y0, w - 12, y1), 'Cancel', 'cancel', enabled=False)
        else:
            label = 'Install' if p == 'ready' else 'Next'
            ok = (p != 'license' or self.accept) and (p != 'ready' or not self.clashes() or self.force)
            ui.button((w - 182, y0, w - 100, y1), label, 'next', default=True, enabled=ok)
            ui.button((w - 92, y0, w - 12, y1), 'Cancel', 'cancel')

    def page_welcome(self, img, ui, box):
        pk = self.pkg
        x0, y0, x1, y1 = box
        y = self.heading(ui, box, 'Install %s?' % pk.title)
        ui.icon(self.icon, (x0, y), 48)
        rows = [('Version', pk.version), ('Publisher', pk.author or 'Unknown publisher'),
                ('Type', 'Driver package (.cdi)' if pk.type == 'cdi' else 'Program package (.cip)'),
                ('Size', packages.human_size(pk.size))]
        yy = y
        for k, v in rows:
            ui.text((x0 + 64, yy), k + ':', 12, color=theme.MUTED)
            ui.text((x0 + 150, yy), v, 12)
            yy += 19
        yy = ui.wrap((x0, yy + 10), pk.description or 'No description.', x1 - x0, 12) + 6
        if pk.type == 'cip':
            for a in pk.apps:
                how = 'runs in COS compatibility mode' if a.endswith('.cosapp') else 'a Neptune app'
                ui.icon('cosapp' if a.endswith('.cosapp') else 'plugin', (x0, yy), 16)
                ui.text((x0 + 22, yy + 8), '%s  -  %s' % (a, how), 12, anchor='lm')
                yy += 22
        else:
            kinds = ', '.join('%s %s' % (r.get('bus'), ' '.join('%s=%s' % (k, v) for k, v in r.items() if k != 'bus'))
                              for r in pk.driver.get('match', []))
            ui.text((x0, yy), 'For devices: ' + gfx.ellipsize(gfx.font(12), kinds, x1 - x0 - 90), 12)
            yy += 22
        old = pk.installed_record()
        if old:
            ui.icon('info', (x0, yy + 2), 16)
            ui.text((x0 + 22, yy + 10), 'Version %s is installed. This replaces it.' % old.get('version', '?'), 12,
                    anchor='lm')
            yy += 24
        # trust note, Windows "publisher could not be verified" style
        tb = (x0, y1 - 62, x1, y1)
        gfx.round_rect(img, tb, 4, fill=(255, 250, 225, 255), outline=(230, 200, 110, 255))
        ui.icon('warning', (tb[0] + 10, tb[1] + 14), 32)
        ui.wrap((tb[0] + 52, tb[1] + 10), 'Installing runs nothing. Once installed, though, its programs and drivers '
                'are ordinary Python running as you. Only install packages from people you trust.',
                tb[2] - tb[0] - 64, 11)

    def scroll_text(self, img, ui, box, text):
        x0, y0, x1, y1 = box
        ui.rect(box, fill=(255, 255, 255), outline=(171, 173, 179))
        fnt = gfx.font(12, False, 'mono')
        lines = wrap_lines(text, x1 - x0 - 34, fnt)
        lh = 17
        page = max(1, (y1 - y0 - 10) // lh)
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(lines) - page))
        for i, l in enumerate(lines[self.scroll:self.scroll + page]):
            ui.d.text((x0 + 8, y0 + 6 + i * lh), l, font=gfx.font_for_obj(fnt, l), fill=(20, 20, 20, 255))
        ui.scrollbar((x1 - 18, y0 + 1, x1 - 1, y1 - 1), 'tscroll', self.scroll, page, len(lines))

    def page_readme(self, img, ui, box):
        y = self.heading(ui, box, 'Read Me')
        self.scroll_text(img, ui, (box[0], y, box[2], box[3]), self.pkg.readme())

    def page_license(self, img, ui, box):
        y = self.heading(ui, box, 'License terms')
        self.scroll_text(img, ui, (box[0], y, box[2], box[3] - 50), self.pkg.license())
        ui.radio((box[0], box[3] - 40), 'I accept the license terms', 'acc', self.accept, data=True)
        ui.radio((box[0], box[3] - 18), 'I do not accept the license terms', 'acc', not self.accept, data=False)

    def clashes(self):
        if not hasattr(self, '_clash'):
            self._clash = self.pkg.conflicts()
        return self._clash

    def page_ready(self, img, ui, box):
        x0, y0, x1, y1 = box
        y = self.heading(ui, box, 'Ready to install')
        plan = self.pkg.plan()
        ui.text((x0, y), 'These %d file%s will be written:' % (len(plan), '' if len(plan) == 1 else 's'), 12)
        lb = (x0, y + 22, x1, y1 - (70 if self.clashes() else 4))
        ui.rect(lb, outline=(171, 173, 179))
        fnt = gfx.font(11)
        lh = 18
        page = max(1, (lb[3] - lb[1] - 6) // lh)
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(plan) - page))
        for i, (m, dest) in enumerate(plan[self.scroll:self.scroll + page]):
            yy = lb[1] + 4 + i * lh
            ui.icon(icons.for_path(m, False), (lb[0] + 6, yy), 16)
            ui.text((lb[0] + 28, yy + 8), gfx.ellipsize(fnt, REL(dest), lb[2] - lb[0] - 50), 11, anchor='lm')
        ui.scrollbar((lb[2] - 18, lb[1] + 1, lb[2] - 1, lb[3] - 1), 'tscroll', self.scroll, page, len(plan))
        if self.clashes():
            ui.icon('warning', (x0, y1 - 62), 24)
            ui.wrap((x0 + 32, y1 - 62), 'Some files already exist: %s' % '; '.join(REL(c) for c in self.clashes()[:2]),
                    x1 - x0 - 32, 11)
            ui.checkbox((x0 + 32, y1 - 22), 'Overwrite them', 'force', self.force)

    def page_installing(self, img, ui, box):
        x0, y0, x1, y1 = box
        y = self.heading(ui, box, 'Installing %s...' % self.pkg.title)
        i, n, m = self.prog
        bar = (x0, y + 10, x1, y + 30)
        ui.rect(bar, fill=(230, 230, 230), outline=(188, 188, 188))
        fx = bar[0] + int((bar[2] - bar[0]) * i / max(1, n))
        if fx > bar[0] + 2:
            img.alpha_composite(gfx.vgradient(fx - bar[0] - 2, 18, ((0, (110, 220, 110)), (0.5, (40, 180, 50)),
                                                                     (1, (20, 140, 30)))), (bar[0] + 1, bar[1] + 1))
            # moving shine, like the Windows 7 progress bar
            sx = bar[0] + int((time.time() * 260) % max(1, fx - bar[0] + 80)) - 80
            shine = gfx.hgradient(80, 18, ((0, (255, 255, 255, 0)), (0.5, (255, 255, 255, 120)), (1, (255, 255, 255, 0))))
            gfx.alpha_composite_clip(img, shine, max(bar[0] + 1, sx), bar[1] + 1) if sx < fx - 10 else None
        ui.text((x0, y + 44), 'Copying %s' % m, 12, color=theme.MUTED)

    def page_done(self, img, ui, box):
        x0, y0, x1, y1 = box
        y = self.heading(ui, box, '%s is installed' % self.pkg.title)
        ui.icon('package' if self.pkg.type == 'cip' else 'driver', (x0, y), 48)
        yy = ui.wrap((x0 + 64, y + 4), 'Version %s was installed. Uninstall it any time from Programs and Features.'
                     % self.pkg.version, x1 - x0 - 64, 12) + 14
        if self.pkg.type == 'cip':
            cos_apps = [a for a in self.pkg.apps if a.endswith('.cosapp')]
            if cos_apps:
                ui.wrap((x0 + 64, yy), 'Its COS programs run in compatibility mode. Right-click one while it runs '
                        'to change how.', x1 - x0 - 64, 11, color=theme.MUTED)
                yy += 40
            ui.checkbox((x0 + 64, yy), 'Run %s now' % self.app_title(self.pkg.apps[0]), 'runafter', self.run_after)
        else:
            m = getattr(self, 'matched', [])
            txt = ('Neptune started this driver for: ' + ', '.join(i.device.get('name', '?') for i in m)) if m else \
                'No matching device is connected right now. The driver starts by itself when one is (Device Manager > ' \
                'Scan for hardware changes).'
            ui.wrap((x0 + 64, yy), txt, x1 - x0 - 64, 12)

    def page_error(self, img, ui, box):
        x0, y0, x1, y1 = box
        y = self.heading(ui, box, 'This package cannot be installed')
        ui.icon('error', (x0, y), 32)
        yy = ui.wrap((x0 + 44, y + 4), self.error, x1 - x0 - 44, 12)
        ui.wrap((x0 + 44, yy + 12), 'File: %s' % REL(self.path or '?'), x1 - x0 - 44, 11, color=theme.MUTED)

    def app_title(self, member):
        stem = os.path.splitext(member)[0]
        return stem

    def launch_installed(self):
        sh = self.shell
        a = self.pkg.apps[0]
        stem = os.path.splitext(a)[0]
        key = ('cos:neptune:%s' % stem) if a.endswith('.cosapp') else 'app:%s' % stem
        if key in sh.registry:
            sh.launch(key)
        else:
            sh.notify('Package installed', 'Find %s in the APPS menu.' % stem, 'info', 4)

    # -- input --------------------------------------------------------------------------------
    def on_command(self, cid, data):
        if cid == 'next':
            self.go_next()
        elif cid == 'back':
            self.go_back()
        elif cid == 'cancel':
            self.close()
        elif cid == 'finish':
            self.close()
            if self.pkg and self.pkg.type == 'cip' and self.run_after:
                self.launch_installed()
        elif cid == 'acc':
            self.accept = bool(data)
        elif cid == 'force':
            self.force = data
        elif cid == 'runafter':
            self.run_after = data
        elif cid == 'tscroll':
            self.scroll = data
        self.invalidate()

    def on_wheel(self, dy, x, y):
        self.scroll = max(0, self.scroll - dy * 3)
        self.invalidate()

    def on_key(self, ev):
        if ev['key'] == 'enter' and self.page not in ('installing',):
            self.on_command({'done': 'finish', 'error': 'cancel'}.get(self.page, 'next'), None)
            return True
        if ev['key'] == 'escape' and self.page != 'installing':
            self.close()
            return True
        if ev['key'] == 'backspace':
            self.go_back()
            return True
        return False


def install_dialog(shell, parent=None):
    def done(p):
        shell.launch('installer', path=p)
    start = os.path.expanduser('~/cos/pkgs') if os.path.isdir(os.path.expanduser('~/cos/pkgs')) else theme.HOME
    shell.open_window(FileDialog(shell, 'open', start=start, exts=('.cip', '.cdi'), callback=done), parent=parent)


# =============================================================================
class ProgramsFeatures(App):
    title = 'Programs and Features'
    icon = 'package'
    size = (820, 520)
    min_size = (640, 380)
    single_instance = True
    bg = theme.CLIENT_BG
    TASK_W = 200

    def __init__(self, shell, tab='packages', **kw):
        super().__init__(shell, **kw)
        self.tab = tab
        self.sel = None
        self.scroll = 0

    def rows(self):
        if self.tab == 'packages':
            return packages.installed_packages()
        reg = getattr(self.shell, 'compat', None)
        return sorted(reg.programs.values(), key=lambda p: p.title.lower()) if reg else []

    def draw(self, img, ui):
        w, h = img.size
        tw = self.TASK_W
        # task pane
        img.alpha_composite(gfx.vgradient(tw, h, ((0, (232, 241, 252)), (1, (209, 225, 245)))), (0, 0))
        ui.d.line([(tw - 1, 0), (tw - 1, h)], fill=(180, 200, 225, 255))
        ui.text((14, 18), 'Neptune Control Panel', 11, color=(30, 57, 91))
        y = 46
        for label, cid in (('Installed packages', 'tab_packages'), ('COS programs', 'tab_cos')):
            on = self.tab == cid[4:]
            ui.link((14, y), label, cid, 12) if not on else ui.text((14, y), label, 12, bold=True, color=(30, 57, 91))
            y += 24
        y += 12
        for label, cid, ic in (('Install a package...', 'install', 'package'), ('Device Manager', 'devmgr', 'devmgr'),
                               ('COS v2.4 Mode', 'cosmode', 'cosmode')):
            ui.icon(ic, (14, y - 1), 16)
            ui.link((36, y), label, cid, 12)
            y += 24
        ui.wrap((14, h - 80), 'Packages are .cip (programs) and .cdi (drivers) files, the same format as COS v2.4.',
                tw - 28, 11, color=theme.MUTED)
        # header
        x0 = tw + 20
        if self.tab == 'packages':
            ui.text((x0, 16), 'Uninstall or change a package', 16, color=(30, 57, 91))
            ui.text((x0, 42), 'To uninstall a package, select it from the list and then click Uninstall.', 12)
        else:
            ui.text((x0, 16), 'COS programs', 16, color=(30, 57, 91))
            ui.text((x0, 42), 'Programs written for COS 2.x. Neptune runs them in compatibility mode.', 12)
        # command bar
        cb = (tw, 68, w, 98)
        ui.command_bar(cb)
        rows = self.rows()
        if self.sel is not None and self.sel >= len(rows):
            self.sel = None
        cur = rows[self.sel] if self.sel is not None else None
        bx = tw + 8
        cmds = [('Organize', None)]
        if self.tab == 'packages':
            cmds = [('Install...', 'install'), ('Uninstall', 'uninstall' if cur else None),
                    ('Open folder', 'folder' if cur else None)]
        else:
            ok = cur is not None and cur.ok and not cur.external
            cmds = [('Run', 'run' if ok else None), ('Compatibility...', 'compat' if cur else None),
                    ('Open folder', 'folder' if cur else None)]
        for label, cid in cmds:
            bw = gfx.text_width(gfx.font(12), label) + 18
            ui.toolbutton((bx, cb[1] + 4, bx + bw, cb[3] - 4), label, cid or ('noop', label), enabled=cid is not None)
            bx += bw + 4
        # list
        cols = ([('Name', 0.34), ('Type', 0.14), ('Publisher', 0.2), ('Installed On', 0.18), ('Version', 0.14)]
                if self.tab == 'packages' else
                [('Name', 0.3), ('Version', 0.1), ('Author', 0.2), ('Location', 0.2), ('Mode', 0.2)])
        lx0, ly0, lx1, ly1 = tw, cb[3], w, h - 24
        width = lx1 - lx0 - 20
        hx = lx0 + 4
        spans = []
        for name, frac in cols:
            cw = int(width * frac)
            ui.text((hx + 6, ly0 + 12), name, 12, color=(76, 96, 122), anchor='lm')
            ui.d.line([(hx + cw - 1, ly0 + 4), (hx + cw - 1, ly0 + 20)], fill=(222, 225, 230, 255))
            spans.append((hx, hx + cw))
            hx += cw
        ui.hline(lx0, lx1, ly0 + 24, (213, 213, 213))
        rh = 24
        top = ly0 + 28
        page = max(1, (ly1 - top) // rh)
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(rows) - page))
        fnt = gfx.font(12)
        for i in range(self.scroll, min(len(rows), self.scroll + page)):
            r = rows[i]
            y = top + (i - self.scroll) * rh
            ui.listrow((lx0 + 4, y, lx1 - 20, y + rh - 2), ('row', i), selected=i == self.sel, data=i)
            if self.tab == 'packages':
                vals = [r.get('title') or r['name'], 'Driver' if r.get('type') == 'cdi' else 'Programs',
                        r.get('author', ''), (r.get('installed', '') or '')[:10], r.get('version', '')]
                ic = 'driver' if r.get('type') == 'cdi' else 'package'
            else:
                st = compat.raw_settings(r.path)
                mode = ('COS %s' % st['mode']) if st['enabled'] else 'Off'
                if r.error:
                    mode = 'Error'
                vals = [r.title, str(r.manifest.get('version', '')), str(r.manifest.get('author', '')),
                        'COS 2.4 (~/.cos)' if r.source == 'cos24' else 'Neptune', mode]
                ic = 'error' if r.error else 'cosapp'
            for k, (a, b) in enumerate(spans):
                tx = a + (28 if k == 0 else 6)
                if k == 0:
                    ui.icon(ic, (a + 6, y + 3), 16)
                ui.text((tx, y + rh // 2 - 1), gfx.ellipsize(fnt, str(vals[k]), b - tx - 4), 12,
                        color=(190, 30, 20) if (k == 4 and vals[k] == 'Error') else theme.CLIENT_TEXT, anchor='lm')
        if not rows:
            msg = 'No packages are installed yet. Click Install... and pick a .cip or .cdi file.' \
                if self.tab == 'packages' else 'No COS programs found in ~/.cos/apps or Neptune.'
            ui.text(((lx0 + lx1) // 2, top + 40), msg, 12, color=theme.MUTED, anchor='mm')
        ui.scrollbar((lx1 - 18, top, lx1 - 1, ly1), 'scroll', self.scroll, page, len(rows))
        # details strip
        ui.rect((tw, h - 24, w, h), fill=(240, 244, 249))
        ui.hline(tw, w, h - 24, (210, 218, 230))
        if cur is not None:
            if self.tab == 'packages':
                d = '%s  ·  %d files  ·  %s' % (cur.get('description', '')[:80], len(cur.get('files', [])),
                                                packages.human_size(cur.get('size', 0) or 0))
            else:
                d = cur.error or str(cur.manifest.get('description', '')) or REL(cur.path)
            ui.text((tw + 10, h - 12), gfx.ellipsize(fnt, d, w - tw - 20), 11, color=theme.MUTED, anchor='lm')

    def on_command(self, cid, data):
        rows = self.rows()
        cur = rows[self.sel] if self.sel is not None and self.sel < len(rows) else None
        if isinstance(cid, tuple) and cid[0] == 'row':
            self.sel = data
        elif cid in ('tab_packages', 'tab_cos'):
            self.tab = cid[4:]
            self.sel = None
            self.scroll = 0
        elif cid == 'scroll':
            self.scroll = data
        elif cid == 'install':
            install_dialog(self.shell, self.win)
        elif cid == 'devmgr':
            self.shell.launch('devmgr')
        elif cid == 'cosmode':
            self.shell.launch('cosmode')
        elif cid == 'uninstall' and cur:
            self.uninstall(cur)
        elif cid == 'folder' and cur:
            if self.tab == 'packages':
                base = os.path.join(packages.DRIVER_DIR if cur.get('type') == 'cdi' else packages.PKG_DIR, cur['name'])
            else:
                base = os.path.dirname(cur.path)
            self.shell.launch('files', path=base if os.path.isdir(base) else packages.PKG_DIR)
        elif cid == 'run' and cur:
            self.shell.launch(cur.key)
        elif cid == 'compat' and cur:
            self.shell.launch('compat', path=cur.path)
        self.invalidate()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'row':
            self.sel = data
            self.on_command('run' if self.tab == 'cos' else 'folder', None)
        else:
            self.on_command(cid, data)

    def uninstall(self, rec):
        def go(ans):
            if ans != 'Uninstall':
                return
            try:
                if rec.get('type') == 'cdi' and getattr(self.shell, 'drivers', None):
                    for inst in list(self.shell.drivers.instances):
                        if inst.rec['name'] == rec['name']:
                            inst.stop()
                removed, kept = packages.uninstall(rec['name'])
            except packages.PackageError as e:
                message(self.shell, 'Uninstall', str(e), 'error', parent=self.win)
                return
            if getattr(self.shell, 'compat', None):
                self.shell.compat.scan()
            if getattr(self.shell, 'plugins', None):
                self.shell.plugins.scan(quiet=True)
            if rec.get('type') == 'cdi' and getattr(self.shell, 'drivers', None):
                self.shell.drivers.rescan()
            txt = '%s was uninstalled. %d file%s removed.' % (rec.get('title') or rec['name'], len(removed),
                                                              '' if len(removed) == 1 else 's')
            if kept:
                txt += '\n\nKept because you changed them:\n' + '\n'.join(REL(k) for k in kept[:6])
            message(self.shell, 'Programs and Features', txt, 'info', parent=self.win)
            self.sel = None
            self.invalidate()
        message(self.shell, 'Programs and Features',
                'Are you sure you want to uninstall %s %s?' % (rec.get('title') or rec['name'], rec.get('version', '')),
                'question', buttons=('Uninstall', 'Cancel'), callback=go, parent=self.win)

    def on_wheel(self, dy, x, y):
        self.scroll = max(0, self.scroll - dy * 3)
        self.invalidate()

    def on_key(self, ev):
        if ev['key'] == 'delete' and self.tab == 'packages' and self.sel is not None:
            self.on_command('uninstall', None)
            return True
        return False


# =============================================================================
class DeviceManager(App):
    title = 'Device Manager'
    icon = 'devmgr'
    size = (640, 560)
    min_size = (460, 360)
    single_instance = True
    bg = theme.CLIENT_BG

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        self.host = getattr(shell, 'drivers', None)
        self.devices = self.host.devices if self.host else []
        if not self.devices:
            from .. import drivers
            self.devices = drivers.scan_devices()
        self.open_cats = {'gamepad', 'keyboard', 'mouse', 'display'}
        self.sel = None
        self.scroll = 0

    def tree(self):
        from ..drivers import CATEGORIES
        out = []
        for key, title, ic in CATEGORIES:
            devs = [d for d in self.devices if d['category'] == key]
            if not devs:
                continue
            out.append(('cat', key, title, ic, len(devs)))
            if key in self.open_cats:
                for d in sorted(devs, key=lambda d: d['name'].lower()):
                    out.append(('dev', d, ic))
        return out

    def driver_text(self, d):
        inst = self.host.driver_for(d) if self.host else None
        if inst:
            return inst.label.strip(), inst.status, 'error' if inst.error else 'ok'
        if d['bus'] == 'input':
            if not d.get('readable'):
                return 'no driver', 'no permission to read %s' % d['path'], 'warn'
            neptune_reads = self.shell.backend == 'gdr' and d['category'] in ('keyboard', 'mouse')
            return ('Neptune input (built-in)' if neptune_reads else 'kernel evdev'), 'working', 'ok'
        if d.get('kernel'):
            return 'kernel: %s' % d['kernel'], 'working', 'ok'
        return 'no driver', 'no driver is bound to this device', 'none'

    def draw(self, img, ui):
        w, h = img.size
        ui.command_bar((0, 0, w, 32))
        bx = 6
        for label, cid, ic, en in (('Scan for hardware changes', 'scan', 'devmgr', True),
                                   ('Properties', 'props', 'info', self.sel is not None),
                                   ('Install driver...', 'install', 'driver', True)):
            bw = gfx.text_width(gfx.font(12), label) + 40
            ui.toolbutton((bx, 4, bx + bw, 28), label, cid, icon=ic, enabled=en)
            bx += bw + 4
        tree = self.tree()
        rh = 21
        top = 38
        page = max(1, (h - top - 30) // rh)
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(tree) - page))
        y = top
        ui.icon('computer', (8, y), 16)
        ui.text((30, y + 8), os.uname().nodename, 12, anchor='lm')
        y += rh
        fnt = gfx.font(12)
        for i in range(self.scroll, min(len(tree), self.scroll + page - 1)):
            node = tree[i]
            if node[0] == 'cat':
                _, key, title, ic, n = node
                x = 18
                open_ = key in self.open_cats
                ui.d.polygon([(x, y + 6), (x + 6, y + 10), (x, y + 14)] if not open_ else
                             [(x - 1, y + 7), (x + 7, y + 7), (x + 3, y + 13)], fill=(80, 80, 80, 255))
                ui.listrow((x + 10, y, w - 20, y + rh - 1), ('cat', key), data=key)
                ui.icon(ic, (x + 14, y + 2), 16)
                ui.text((x + 36, y + rh // 2), '%s (%d)' % (title, n), 12, anchor='lm')
            else:
                _, d, ic = node
                x = 46
                ui.listrow((x - 4, y, w - 20, y + rh - 1), ('dev', d['id']), selected=self.sel == d['id'], data=d)
                ui.icon(ic, (x, y + 2), 16)
                name, status, kind = self.driver_text(d)
                if kind in ('warn', 'none', 'error'):
                    ui.d.ellipse((x + 8, y + 10, x + 17, y + 19), fill=(250, 200, 30, 255) if kind != 'error' else
                                 (220, 40, 30, 255), outline=(120, 80, 0, 255))
                    ui.text((x + 12, y + 15), '!', 8, bold=True, color=(0, 0, 0), anchor='mm')
                ui.text((x + 22, y + rh // 2), gfx.ellipsize(fnt, d['name'], w - x - 260), 12, anchor='lm')
                col = (0, 120, 40) if name.startswith(('Neptune', 'kernel')) else ((0, 70, 160) if kind == 'ok'
                                                                                  else theme.MUTED)
                ui.text((w - 26, y + rh // 2), gfx.ellipsize(fnt, name, 220), 11, color=col, anchor='rm')
            y += rh
        ui.scrollbar((w - 18, top, w - 1, h - 26), 'scroll', self.scroll, page, len(tree) + 1)
        ui.rect((0, h - 24, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - 24, (215, 215, 215))
        nd = len([i for i in (self.host.instances if self.host else [])])
        status = '%d devices  ·  %d .cdi driver%s running' % (len(self.devices), nd, '' if nd == 1 else 's')
        if self.host and self.host.off:
            status += '  ·  drivers are turned off (safe mode)'
        ui.text((10, h - 12), status, 11, color=theme.MUTED, anchor='lm')

    def selected(self):
        return next((d for d in self.devices if d['id'] == self.sel), None)

    def on_command(self, cid, data):
        if isinstance(cid, tuple):
            if cid[0] == 'cat':
                self.open_cats ^= {cid[1]}
            elif cid[0] == 'dev':
                self.sel = cid[1]
        elif cid == 'scroll':
            self.scroll = data
        elif cid == 'scan':
            if self.host:
                self.host.rescan()
                self.devices = self.host.devices
            self.shell.notify('Device Manager', 'Found %d devices.' % len(self.devices), 'info', 3)
        elif cid == 'props':
            self.properties()
        elif cid == 'install':
            install_dialog(self.shell, self.win)
        self.invalidate()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'dev':
            self.sel = cid[1]
            self.properties()
        else:
            self.on_command(cid, data)

    def properties(self):
        d = self.selected()
        if not d:
            return
        name, status, _k = self.driver_text(d)
        ids = '%s:%s' % (d.get('vendor', ''), d.get('product', d.get('device', '')))
        lines = ['Bus: %s' % d['bus'], 'ID: %s   (vendor:product %s)' % (d['id'], ids)]
        if d.get('class'):
            lines.append('Class: %s' % d['class'])
        if d.get('path'):
            lines.append('Node: %s' % d['path'])
        if d.get('caps'):
            lines.append('Capabilities: %s' % ', '.join(d['caps']))
        lines += ['', 'Driver: %s' % name, 'Device status: %s' % status, '',
                  'A .cdi matches this device with: {"bus": "%s", "vendor": "%s"%s}' % (
                      d['bus'], d.get('vendor', ''),
                      (', "product": "%s"' % d['product']) if d.get('product') else
                      ((', "device": "%s"' % d['device']) if d.get('device') else ''))]
        message(self.shell, '%s Properties' % gfx.ellipsize(gfx.font(12), d['name'], 300), '\n'.join(lines), 'info',
                parent=self.win)

    def on_wheel(self, dy, x, y):
        self.scroll = max(0, self.scroll - dy * 3)
        self.invalidate()


# =============================================================================
COS24 = os.path.expanduser('~/cos/cos.py')
COSMODE_HOME = os.path.join(theme.STATE, 'cosmode', 'home')


class CosMode(Terminal):
    """The real COS v2.4, sandboxed in a window."""
    title = 'COS v2.4 Mode'
    icon = 'cosmode'
    size = (1000, 640)
    single_instance = True

    def __init__(self, shell, **kw):
        if not os.path.isfile(COS24):
            raise FileNotFoundError('COS v2.4 is not at %s' % COS24)
        os.makedirs(COSMODE_HOME, exist_ok=True)
        py = shutil.which('python3') or 'python3'
        inner = [py, COS24]
        if shutil.which('bwrap'):
            argv = ['bwrap', '--dev-bind', '/', '/', '--tmpfs', '/dev/input', '--setenv', 'HOME', COSMODE_HOME,
                    '--die-with-parent', '--'] + inner
            self.sandboxed = True
        else:
            argv = ['env', 'HOME=%s' % COSMODE_HOME] + inner
            self.sandboxed = False
        super().__init__(shell, command=' '.join(shlex.quote(a) for a in argv), cwd=COSMODE_HOME, **kw)
        if not self.sandboxed:
            shell.notify('COS v2.4 Mode', 'bubblewrap (bwrap) is not installed, so COS v2.4 can also read your mouse '
                         'directly. Install bubblewrap for full isolation.', 'warning', 8)
