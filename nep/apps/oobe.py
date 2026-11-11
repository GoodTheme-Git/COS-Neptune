"""Set Up COS Neptune: the out-of-box experience.

Runs full screen (the shell's 'oobe' phase) the first time Neptune starts, and
again from CONTROL > Run Setup Again or `neptune.py --oobe`. Look choices apply
live, so the screen behind the wizard changes as you pick.
"""
import os
import platform
import time

from .. import gfx, icons, migrate, theme, wallpaper
from ..app import App

LOOK_WALLS = [('preset', 'Neptune Aurora'), ('preset', 'Deep Trench'), ('preset', 'Emerald Drift'),
              ('preset', 'Nebula'), ('nostalgia', 'Rolling Hills'), ('nostalgia', 'Starfield'),
              ('nostalgia', 'Sunset Grid'), ('nostalgia', 'Teal Classic')]


class OOBE(App):
    title = 'Set Up COS Neptune'
    icon = 'neptune'
    size = (820, 560)
    resizable = False
    dialog = True
    no_close = True
    show_in_taskbar = False

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        s = shell.settings
        self.imports = migrate.available()
        self.pages = ['welcome', 'name', 'password', 'look', 'background', 'clock'] + \
            (['import'] if self.imports else []) + ['done']
        self.page = 0
        uname = s.get('user_name') or shell.lock_user()
        cname = s.get('computer_name') or platform.node().split('.')[0] or 'neptune'
        self.name = {'text': uname, 'cur': len(uname)}
        self.computer = {'text': cname, 'cur': len(cname)}
        self.pw = {'text': '', 'cur': 0}
        self.pw2 = {'text': '', 'cur': 0}
        self.ask_pw = bool(s.get('login_screen'))
        self.pick_imports = set(self.imports)
        self.error = ''
        self.thumbs = {}
        self.ui.focus = None

    # -- helpers -------------------------------------------------------------------
    @property
    def current(self):
        return self.pages[self.page]

    def thumb(self, spec):
        k = (spec['kind'], spec.get('name'))
        if k not in self.thumbs:
            self.thumbs[k] = wallpaper.build(spec, 148, 84)
        return self.thumbs[k]

    def measured_ms(self):
        ft = self.shell.frame_times[-20:]
        return (sum(ft) / len(ft) * 1000) if ft else 0.0

    def recommend_aero(self):
        ms = self.measured_ms()
        return ms == 0 or ms < 45

    # -- drawing --------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        s = self.shell.settings
        # header band
        img.alpha_composite(gfx.vgradient(w, 74, ((0, (16, 96, 112)), (1, (8, 36, 78)))), (0, 0))
        img.alpha_composite(icons.get('neptune', 46), (18, 14))
        gfx.shadow_text(img, (76, 25), 'Set Up COS Neptune', gfx.font(19, False, 'light'), (255, 255, 255, 255),
                        (0, 0, 0, 170), anchor='lm')
        gfx.shadow_text(img, (76, 50), self.subtitle(), gfx.font(12), (190, 230, 240, 255), (0, 0, 0, 150), anchor='lm')
        n = len(self.pages)
        for i in range(n):
            cx = w - 24 - (n - 1 - i) * 16
            col = (255, 255, 255, 255) if i == self.page else ((150, 220, 230, 255) if i < self.page else
                                                               (80, 120, 140, 255))
            ui.d.ellipse((cx - 4, 33, cx + 4, 41), fill=col)
        body = (40, 98, w - 40, h - 64)
        getattr(self, 'draw_' + self.current)(img, ui, body)
        if self.error:
            ui.text((40, h - 84), self.error, 12, color=(190, 40, 30))
        # footer
        ui.rect((0, h - 52, w, h), fill=(240, 242, 245))
        ui.hline(0, w, h - 52, (215, 220, 228))
        ui.text((20, h - 26), '%s  ·  build %s' % (theme.FULLNAME, theme.BUILD), 11, color=(120, 130, 140),
                anchor='lm')
        last = self.current == 'done'
        ui.button((w - 116, h - 40, w - 20, h - 12), 'Finish' if last else 'Next', 'next', default=True)
        if self.page > 0:
            ui.button((w - 216, h - 40, w - 124, h - 12), 'Back', 'back')

    def subtitle(self):
        return {'welcome': "Let's get Neptune ready for you.", 'name': 'Choose a name for you and this computer',
                'password': 'Set a password (optional)', 'look': 'Choose how Neptune looks',
                'background': 'Pick a desktop background', 'clock': 'Clock, sounds and motion',
                'import': 'Bring your COS v2.4 settings', 'done': 'All set'}[self.current]

    def draw_welcome(self, img, ui, b):
        x0, y0, x1, y1 = b
        img.alpha_composite(icons.get('neptune', 128), (x0, y0 + 10))
        ui.text((x0 + 160, y0 + 20), 'Welcome to COS Neptune', 24, kind='light', color=(0, 51, 153))
        y = ui.wrap((x0 + 160, y0 + 64),
                    'COS 3.0 "Neptune" is the Aero release of COS: glass windows, a taskbar and Start menu, and a '
                    'desktop drawn pixel by pixel through Graphics Desktop Rendering.', x1 - x0 - 170, 13, line_h=21)
        y = ui.wrap((x0 + 160, y + 12),
                    'This takes about a minute. You will pick a name, an optional password, and how Neptune looks. '
                    'You can change all of it later in Personalize and the CONTROL menu.', x1 - x0 - 170, 13,
                    color=(70, 80, 90), line_h=21)
        if self.imports:
            ui.wrap((x0 + 160, y + 12), 'COS v2.4 is on this computer too: Setup can bring your settings over.',
                    x1 - x0 - 170, 13, color=(30, 110, 60), line_h=21)
        ui.text((x0 + 160, y1 - 24), 'Press Next to begin.', 13, color=(0, 51, 153))

    def draw_name(self, img, ui, b):
        x0, y0, x1, y1 = b
        ui.text((x0, y0 + 6), 'Type your name', 14, color=(0, 51, 153))
        ui.text((x0, y0 + 30), 'It appears on the Start menu and the lock screen.', 12, color=(90, 100, 110))
        ui.textbox((x0, y0 + 56, x0 + 360, y0 + 84), 'name', self.name, placeholder='Your name', size=13)
        ui.text((x0, y0 + 120), 'Name this computer', 14, color=(0, 51, 153))
        ui.text((x0, y0 + 144), 'Other computers see it in Pigeon, IBBS and on your network.', 12,
                color=(90, 100, 110))
        ui.textbox((x0, y0 + 170, x0 + 360, y0 + 198), 'computer', self.computer, placeholder='neptune-pc', size=13)
        img.alpha_composite(icons.get('computer', 96), (x1 - 150, y0 + 40))

    def draw_password(self, img, ui, b):
        x0, y0, x1, y1 = b
        ui.wrap((x0, y0 + 4), 'A password keeps the lock screen (Super+L) from being opened by anyone walking by. '
                'Leave both boxes empty to skip it.', x1 - x0 - 160, 13, color=(60, 70, 80), line_h=20)
        ui.text((x0, y0 + 70), 'Password', 12, anchor='la')
        ui.textbox((x0 + 120, y0 + 64, x0 + 420, y0 + 90), 'pw', self.pw, password=True, size=13)
        ui.text((x0, y0 + 108), 'Type it again', 12, anchor='la')
        ui.textbox((x0 + 120, y0 + 102, x0 + 420, y0 + 128), 'pw2', self.pw2, password=True, size=13)
        ui.checkbox((x0 + 120, y0 + 146), 'Ask for this password when Neptune starts', 'askpw', self.ask_pw)
        ui.wrap((x0 + 120, y0 + 176), 'Note: this locks the desktop, it does not encrypt your files.', 360, 11,
                color=(120, 130, 140))
        img.alpha_composite(icons.get('lock', 96), (x1 - 150, y0 + 40))

    def draw_look(self, img, ui, b):
        x0, y0, x1, y1 = b
        s = self.shell.settings
        aero = s.get('aero', True)
        rec = self.recommend_aero()
        ms = self.measured_ms()
        ui.radio((x0, y0 + 4), 'Neptune Aero: glass, blur, shadows and animations', ('look', 'aero'), aero,
                 data='aero')
        ui.radio((x0, y0 + 32), 'Neptune Basic: flat and quick, for slower computers', ('look', 'basic'), not aero,
                 data='basic')
        verdict = ('Neptune timed this screen at %.0f ms per frame, so %s is recommended.' %
                   (ms, 'Aero' if rec else 'Basic')) if ms else 'Aero is recommended.'
        ui.text((x0 + 20, y0 + 62), verdict, 12, color=(30, 110, 60) if rec == aero else (160, 90, 0))
        ui.text((x0, y0 + 100), 'Glass color', 14, color=(0, 51, 153))
        sw = 40
        for i, (name, rgb) in enumerate(theme.GLASS_COLORS):
            cx = x0 + (i % 8) * (sw + 12)
            cy = y0 + 130 + (i // 8) * (sw + 26)
            r = (cx, cy, cx + sw, cy + sw)
            sel = s.get('glass_color') == name
            if sel or ui.hot(r):
                gfx.round_rect(img, (r[0] - 4, r[1] - 4, r[2] + 4, r[3] + 4), 5,
                               fill=(220, 235, 252, 255) if sel else (240, 246, 252, 255),
                               outline=(60, 127, 177, 255) if sel else (170, 200, 230, 255))
            face = gfx.vgradient(sw, sw, ((0, gfx.mix(rgb, (255, 255, 255), 0.45)), (0.5, rgb),
                                          (0.51, gfx.mix(rgb, (0, 0, 0), 0.15)), (1, gfx.mix(rgb, (255, 255, 255), 0.2))))
            gfx.round_rect(img, r, 4, fill=face, outline=(60, 60, 70, 255))
            ui.text(((r[0] + r[2]) // 2, r[3] + 9), name, 10, color=(60, 70, 80), anchor='mm')
            ui.region(r, ('glass', name))
        ui.text((x0, y1 - 12), 'Changes show behind this window straight away.', 11, color=(120, 130, 140))

    def draw_background(self, img, ui, b):
        x0, y0, x1, y1 = b
        cur = self.shell.wall_spec()
        for i, (kind, name) in enumerate(LOOK_WALLS):
            cx = x0 + (i % 4) * 182
            cy = y0 + 6 + (i // 4) * 124
            r = (cx, cy, cx + 148, cy + 84)
            sel = cur.get('kind') == kind and cur.get('name') == name
            if sel or ui.hot(r):
                gfx.round_rect(img, (r[0] - 5, r[1] - 5, r[2] + 5, r[3] + 24), 5,
                               fill=(220, 235, 252, 255) if sel else (240, 246, 252, 255),
                               outline=(60, 127, 177, 255) if sel else (170, 200, 230, 255))
            img.alpha_composite(self.thumb({'kind': kind, 'name': name}), (cx, cy))
            ui.rect(r, outline=(90, 100, 110))
            ui.text(((r[0] + r[2]) // 2, r[3] + 10), name, 11, color=(60, 70, 80), anchor='mm')
            ui.region(r, ('wall', kind, name))
        ui.text((x0, y1 - 12), 'More in CONTROL > Desktop Background: colors, gradients, patterns, text or your own '
                               'picture.', 11, color=(120, 130, 140))

    def draw_clock(self, img, ui, b):
        x0, y0, x1, y1 = b
        s = self.shell.settings
        ui.text((x0, y0 + 4), 'Clock', 14, color=(0, 51, 153))
        now = time.localtime()
        ui.radio((x0 + 10, y0 + 32), '12-hour  (%s)' % time.strftime('%I:%M %p', now).lstrip('0'), ('clock', '12'),
                 not s.get('clock_24h'), data='12')
        ui.radio((x0 + 10, y0 + 58), '24-hour  (%s)' % time.strftime('%H:%M', now), ('clock', '24'),
                 bool(s.get('clock_24h')), data='24')
        ui.checkbox((x0 + 10, y0 + 86), 'Show the clock gadget on the desktop', 'show_gadget', s.get('show_gadget', True))
        ui.text((x0, y0 + 130), 'Sounds and motion', 14, color=(0, 51, 153))
        ui.checkbox((x0 + 10, y0 + 158), 'Play sounds (start-up, notifications)', 'sounds', s.get('sounds', True))
        ui.checkbox((x0 + 10, y0 + 184), 'Animate windows', 'animations', s.get('animations', True))
        ui.checkbox((x0 + 10, y0 + 210), 'Show the boot screen when Neptune starts', 'boot_screen',
                    s.get('boot_screen', True))
        img.alpha_composite(icons.get('clock', 96), (x1 - 150, y0 + 30))

    def draw_import(self, img, ui, b):
        x0, y0, x1, y1 = b
        ui.wrap((x0, y0 + 4), 'COS v2.4 is installed on this computer. Pick what to bring into Neptune. COS v2.4 itself '
                'is not changed, and your COS 2.x programs already run in Neptune’s compatibility mode.',
                x1 - x0, 13, color=(60, 70, 80), line_h=20)
        y = y0 + 70
        for k, label in self.imports.items():
            ui.checkbox((x0 + 10, y), label, ('imp', k), k in self.pick_imports)
            y += 28

    def draw_done(self, img, ui, b):
        x0, y0, x1, y1 = b
        s = self.shell.settings
        ui.text((x0, y0 + 4), 'Neptune is ready, %s.' % (self.name['text'].strip() or 'friend'), 20, kind='light',
                color=(0, 51, 153))
        rows = [('Computer name', self.computer['text'].strip() or '-'),
                ('Password', ('set, asked at start-up' if self.ask_pw else 'set') if self.pw['text'] else 'none'),
                ('Look', 'Neptune Aero, %s glass' % s.get('glass_color') if s.get('aero', True) else 'Neptune Basic'),
                ('Background', self.shell.wall_spec().get('name') or self.shell.wall_spec().get('kind', '')),
                ('Clock', '24-hour' if s.get('clock_24h') else '12-hour'),
                ('Sounds', 'on' if s.get('sounds', True) else 'off')]
        if self.imports:
            rows.append(('From COS v2.4', ', '.join(self.imports[k].split(' (')[0] for k in self.pick_imports)
                         or 'nothing'))
        y = y0 + 50
        for k, v in rows:
            ui.text((x0 + 10, y), k, 12, color=(100, 110, 120))
            ui.text((x0 + 170, y), gfx.ellipsize(gfx.font(12), v, x1 - x0 - 190), 12)
            y += 24
        ui.text((x0 + 10, y + 16), 'Press Finish to go to your desktop.', 13, color=(0, 51, 153))

    # -- input ----------------------------------------------------------------------------
    def on_command(self, cid, data):
        s = self.shell.settings
        self.error = ''
        if cid == 'next' or (cid in ('name', 'computer', 'pw2') and data == 'enter'):
            return self.next()
        if cid == 'back':
            self.page = max(0, self.page - 1)
            self.focus_page()
            return
        if cid == 'askpw':
            self.ask_pw = bool(data)
        elif cid in ('show_gadget', 'sounds', 'animations', 'boot_screen'):
            s[cid] = bool(data)
            self.shell.apply_settings()
        elif isinstance(cid, tuple):
            if cid[0] == 'look':
                s['aero'] = cid[1] == 'aero'
                s['animations'] = s['aero']
                self.shell.apply_settings()
            elif cid[0] == 'glass':
                s['glass_color'] = cid[1]
                s['glass_rgb'] = list(dict(theme.GLASS_COLORS)[cid[1]])
                self.shell.apply_settings()
            elif cid[0] == 'wall':
                s['wall'] = {'kind': cid[1], 'name': cid[2]}
                if cid[1] == 'preset':
                    s['wallpaper'] = cid[2]
                self.shell.apply_settings()
            elif cid[0] == 'clock':
                s['clock_24h'] = cid[1] == '24'
            elif cid[0] == 'imp':
                (self.pick_imports.add if data else self.pick_imports.discard)(cid[1])

    def focus_page(self):
        self.ui.focus = {'name': 'name', 'password': 'pw'}.get(self.current)
        self.invalidate()

    def next(self):
        k = self.current
        if k == 'name' and not self.name['text'].strip():
            self.error = 'Please type a name.'
            return
        if k == 'password' and self.pw['text'] != self.pw2['text']:
            self.error = 'The two passwords do not match.'
            self.pw2 = {'text': '', 'cur': 0}
            self.ui.focus = 'pw2'
            return
        if k == 'password' and self.ask_pw and not self.pw['text']:
            self.ask_pw = False
        if k == 'done':
            return self.finish()
        self.page += 1
        self.focus_page()

    def finish(self):
        s = self.shell.settings
        s['user_name'] = self.name['text'].strip()
        s['computer_name'] = self.computer['text'].strip()
        if self.pw['text']:
            s['lock_password'] = self.pw['text']
        s['login_screen'] = bool(self.ask_pw and self.pw['text'])
        notes = migrate.import_cos24(s, self.pick_imports) if self.pick_imports else []
        s['oobe_done'] = True
        s.save()
        self.shell.finish_oobe(notes)

    def on_key(self, ev):
        if ev['key'] == 'enter':
            self.next()
            return True
        if ev['key'] == 'escape' and self.page > 0:
            self.page -= 1
            self.focus_page()
            return True
        return False
