"""Control Panel: every COS setting in one place, on the CEAP engine.

The settings of COS v2.4 and COS Neptune, combined: Neptune's own, plus the
v2.4 ones that mean something here (now real Neptune settings: desktop icons,
cursor scheme, natural scrolling, tap to click, sound volume, Druman volume,
safe mode). The ones that only make sense on a text console (AsciiVid, console
fonts, cell aspect...) are listed under Compatibility, pointing at COS v2.4
Mode, where they still work.

Left: categories. Middle (the Exploring Space): category icons, or the
settings with live controls. Right: Overview explains the setting under the
pointer; Metadata shows its key, value, default and where it came from.
Changes apply at once and are saved.
"""
import os

from .. import gfx, savers, theme
from .ceapframe import CeapFrame
from .dialogs import message

CATS = [
    ('appearance', 'Appearance and Personalization', 'personalize', 'Glass, background, desktop icons, clock, pointer'),
    ('saver', 'Screen Saver and Lock Screen', 'saver', 'Screen saver, lock screen, password, startup screen'),
    ('sound', 'Sounds', 'music', 'Sounds on or off, volume, Druman'),
    ('input', 'Mouse and Keyboard', 'mouse', 'Pointer speed, scrolling, tapping, emergency exit'),
    ('display', 'Display and GDR', 'computer', 'Full-screen mode resolution, boot screen'),
    ('programs', 'Programs', 'package', 'Installed packages, apps, CEAP, File Explorer, Webinator'),
    ('devices', 'Devices and Drivers', 'devmgr', 'Device Manager, drivers, safe mode'),
    ('compat', 'Compatibility', 'cosapp', 'COS 2.x programs, COS v2.4 Mode, v2.4-only settings'),
    ('network', 'Network', 'pigeon', 'Pigeon file sharing, IBBS'),
    ('fun', 'Fun', 'neptune', 'Desktop pet, tip of the day, secrets'),
    ('system', 'System', 'settings', 'About, system information, reset'),
]
CAT = {c[0]: c for c in CATS}
GDR_MODES = [('', "Screen's preferred"), ('3840x1080@60', '3840 x 1080 @ 60 Hz'), ('5120x1440', '5120 x 1440'),
             ('2560x1440', '2560 x 1440'), ('1920x1080@60', '1920 x 1080 @ 60 Hz'), ('1366x768', '1366 x 768')]
V24_ONLY = ('Renderer (AsciiPic / ffplay), AsciiPic/AsciiVid quality, AsciiVid frame rate cap, pre-render video, '
            'monochrome rendering, cell aspect ratio, console font, ffplay takes the console, bar position, outline '
            'drag, pointer coords in the status bar, two-finger scroll divisor, pointer auto-calibration. These steer '
            'the text-mode COS 2.4 and still work there: open COS v2.4 Mode, then CTRL > Preferences.')


def S(cat, label, kind, key=None, opts=None, help='', origin='Neptune', v24=None):
    return {'cat': cat, 'label': label, 'kind': kind, 'key': key, 'opts': opts, 'help': help, 'origin': origin,
            'v24': v24}


SETTINGS = [
    # Appearance
    S('appearance', 'Glass colour and intensity', 'action', 'app:personalize', help='Pick the Aero glass colour, how '
      'strong it is and how see-through.'),
    S('appearance', 'Transparency', 'bool', 'transparency', help='See-through glass on window frames, menus and the '
      'taskbar. Turn off for solid frames.'),
    S('appearance', 'Glass intensity', 'pct', 'intensity', help='How strongly the glass colour shows.'),
    S('appearance', 'Window animations', 'bool', 'animations', help='Windows grow, shrink and fade as they open, close '
      'and minimize.'),
    S('appearance', 'Desktop background', 'action', 'app:background', help='Neptune, Bubbles, Nostalgia, colours, '
      'patterns, text or your own picture.', origin='both', v24='wallpaper_idx'),
    S('appearance', 'Show desktop icons', 'bool', 'show_icons', help='The icons down the left of the desktop.',
      origin='both', v24='show_icons'),
    S('appearance', 'Clock gadget', 'bool', 'show_gadget', help='The analog clock on the desktop.'),
    S('appearance', '24-hour clock', 'bool', 'clock_24h', help='13:00 instead of 1:00 PM, on the taskbar and lock '
      'screen.', origin='both', v24='pref_clock'),
    S('appearance', 'Show build number on desktop', 'bool', 'show_build', help='The COS version and build number in '
      'the bottom-right corner.'),
    S('appearance', 'Pointer scheme', 'choice', 'cursor_scheme', ['Aero', 'Aero (large)', 'Midnight (black)'],
      help='The look of the mouse pointer.', origin='both', v24='cursor_shape'),
    # Screen saver and lock
    S('saver', 'Screen saver', 'choice', 'screensaver', ['(None)'] + savers.NAMES, help='What plays when Neptune '
      'has been idle for a while. CONTROL > Screen Saver... has a live preview.'),
    S('saver', 'Wait before the screen saver (minutes)', 'int', 'saver_wait', (1, 120, 1), help='Idle time before '
      'the screen saver starts.'),
    S('saver', 'Show the lock screen when coming back', 'bool', 'saver_lock', help='After the screen saver, show the '
      'lock screen.'),
    S('saver', 'Lock screen at startup', 'bool', 'login_screen', help='Start Neptune on the lock screen.',
      origin='both', v24='login_enabled'),
    S('saver', 'Lock screen password...', 'action', 'app:lockset', help='Set or clear the lock screen password. It '
      'hides the desktop; it is not real security.', origin='both', v24='set_password'),
    S('saver', 'Boot screen', 'bool', 'boot_screen', help='The COS Neptune boot animation when Neptune starts.',
      origin='both', v24='boot_splash'),
    # Sounds
    S('sound', 'Sounds', 'bool', 'sounds', help='Neptune\'s sounds: notifications, warnings, boot and shutdown.',
      origin='both', v24='sound_on'),
    S('sound', 'Sound volume', 'int', 'sound_volume', (0, 100, 5), help='How loud Neptune\'s own sounds are.',
      origin='both', v24='sound_volume'),
    S('sound', 'Druman starting volume', 'int', 'druman_volume', (0, 100, 5), help='The volume Druman starts at.',
      origin='both', v24='druman_volume'),
    S('sound', 'Play a test sound', 'action', 'fn:test_sound', help='Plays the notification sound.', origin='both',
      v24='test_sound'),
    # Mouse and keyboard
    S('input', 'Pointer speed', 'float', 'cursor_speed', (0.25, 3.0, 0.25), help='How fast the pointer moves in GDR '
      '(full-screen) mode. In a desktop window your desktop\'s own setting applies.', origin='both',
      v24='mouse_speed'),
    S('input', 'Natural (reversed) scrolling', 'bool', 'natural_scroll', help='Scroll the content the way your '
      'fingers move, like a phone.', origin='both', v24='natural_scroll'),
    S('input', 'Tap to click', 'bool', 'tap_to_click', help='A quick tap on the touchpad clicks (two fingers: right '
      'click). GDR mode.', origin='both', v24='tap_to_click'),
    S('input', 'Emergency exit (Ctrl+Shift+Alt+Q)', 'bool', 'emergency_exit', help='Ctrl+Shift+Alt+Q asks whether '
      'to leave COS Neptune straight away, from anywhere, even the lock screen.'),
    S('input', 'Keyboard shortcuts', 'action', 'help:keys', help='Every Neptune keyboard shortcut. Tab and Shift+Tab '
      'move between buttons and boxes; Enter or Space presses.'),
    # Display
    S('display', 'GDR resolution', 'choice', 'gdr_mode', [m[0] for m in GDR_MODES], help='The screen mode Neptune '
      'asks for when it runs full-screen from a console. Takes effect next time GDR starts.'),
    S('display', 'Restart Neptune', 'action', 'fn:restart', help='Restart Neptune to apply display changes.'),
    S('display', 'What is GDR?', 'action', 'help:gdr', help='Graphics Desktop Rendering: how Neptune draws the whole '
      'screen itself, and how to get out of trouble.'),
    # Programs
    S('programs', 'File Explorer style', 'choice', 'explorer_style', ['CEAP', 'Classic'], help='CEAP: the reworked '
      'File Explorer with the directory tree, three views, Overview, Metadata and Code.Exe.Explore. Classic: the '
      'original Neptune Explorer.'),
    S('programs', 'Web browser style', 'choice', 'web_style', ['CEAP', 'Classic'], help='CEAP: CEAP:Web, with '
      'favorites and history in the tree, the page outline and details beside it, and a JavaScript console. '
      'Classic: Webinator on its own.'),
    S('programs', 'Programs and Features', 'action', 'app:programs', help='Install and uninstall .cip and .cdi '
      'packages, and see your COS programs.'),
    S('programs', 'App Manager (.nepapp)', 'action', 'app:appmgr', help='Neptune apps written in Python.'),
    S('programs', 'CEAP App Maker', 'action', 'app:ceapmaker', help='Build a CEAP app by clicking, no code.'),
    S('programs', 'CEAP apps folder', 'action', 'folder:ceap', help='Where CEAP apps that appear in the APPS menu '
      'live.'),
    S('programs', 'JavaScript in Webinator', 'bool', 'webinator_js', help='Let web pages run scripts (sandboxed).'),
    # Devices
    S('devices', 'Device Manager', 'action', 'app:devmgr', help='Every device, the driver it uses, and its status. '
      'Devices you plug in or pull out show up by themselves.'),
    S('devices', 'Safe mode: no .cdi drivers', 'bool', 'drivers_off', help='Start no .cdi drivers (if one causes '
      'trouble). Takes effect on the next device scan or restart.', origin='both', v24='drivers_off'),
    S('devices', 'Install a driver...', 'action', 'fn:install', help='Install a .cdi (or .cip) package.'),
    # Compatibility
    S('compat', 'COS v2.4 Mode', 'action', 'app:cosmode', help='The real COS v2.4 in a window, sandboxed, with the '
      'mouse working.'),
    S('compat', 'COS programs and compatibility', 'action', 'fn:cosprograms', help='COS 2.x programs and the '
      'compatibility mode each one runs in.'),
    S('compat', 'COS 2.4-only settings', 'info', None, help=V24_ONLY, origin='COS 2.4'),
    # Network
    S('network', 'Pigeon (share and sync folders)', 'action', 'app:pigeon', help='Share a folder on your network, '
      'read-only, and pull only what changed. Talks to COS v2.4 too.', origin='both'),
    S('network', 'IBBS', 'action', 'app:app:IBBS', help='The TCF bulletin board.', origin='both', v24='ibbs'),
    # Fun
    S('fun', 'Desktop pet', 'bool', 'desktop_pet', help='Bubbo, the COS bubble, wanders along the taskbar. Click him.'),
    S('fun', 'Tip of the day', 'bool', 'daily_tip', help='A tip (or a joke) when Neptune starts.'),
    S('fun', 'Secrets', 'info', None, help='Some things in Neptune only happen if you know the right keys. '
      'Hint: it is a very old code, and it starts with up, up.'),
    # System
    S('system', 'About COS Neptune', 'action', 'app:about', help='Version, build number and credits.'),
    S('system', 'System Information', 'action', 'app:sysinfo', help='Hardware, display, input devices and the '
      'Neptune Experience Index.'),
    S('system', 'Task Manager', 'action', 'app:taskmgr', help='Programs, processes and performance.'),
    S('system', 'Reset all settings', 'action', 'fn:reset', help='Put every setting back to its default. Your lock '
      'password, name and computer name are kept.', origin='both', v24='reset'),
]


class ControlPanel(CeapFrame):
    title = 'Control Panel'
    icon = 'settings'
    size = (1060, 680)
    min_size = (800, 480)
    single_instance = True
    tree_w = 250

    def __init__(self, shell, cat=None, **kw):
        super().__init__(shell, **kw)
        self.cat = cat if cat in CAT else None
        self.history, self.future = [], []
        self.search = {'text': '', 'cur': 0}
        self.hover = None
        self.sel = None
        self.scroll = 0

    # -- data --------------------------------------------------------------------
    def visible(self):
        q = self.search['text'].strip().lower()
        if q:
            return [s for s in SETTINGS if q in s['label'].lower() or q in s['help'].lower()
                    or (s['key'] or '').lower().find(q) >= 0]
        if self.cat:
            return [s for s in SETTINGS if s['cat'] == self.cat]
        return []

    def value(self, s):
        return self.shell.settings.get(s['key'], theme.DEFAULTS.get(s['key']))

    def set(self, s, v):
        self.shell.settings[s['key']] = v
        try:
            self.shell.settings.save()
        except Exception:
            pass
        self.shell.apply_settings()
        if s['key'] in ('screensaver', 'saver_wait'):
            import time
            self.shell.last_input = time.time()
        self.invalidate()

    def go(self, cat):
        if cat != self.cat:
            self.history.append(self.cat)
            self.future.clear()
        self.cat, self.scroll, self.sel = cat, 0, None
        self.search = {'text': '', 'cur': 0}

    # -- drawing -------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        g = self.geometry()
        self.hover = None                      # set again below by whatever is under the pointer
        crumb = 'Control Panel' + ('  ▸  ' + CAT[self.cat][1] if self.cat else '')
        if self.search['text'].strip():
            crumb = 'Control Panel  ▸  Search results'
        self.draw_toolbar(img, ui, w, [('back', '←', bool(self.history)), ('fwd', '→', bool(self.future)),
                                       ('up', '↑', self.cat is not None), ('refresh', '⟳', True)],
                          crumb, self.search, 'Search settings')
        self.draw_tree(img, ui, g['tree'])
        ui.rect(g['space'], fill=(255, 255, 255))
        if self.visible() or self.cat or self.search['text'].strip():
            self.draw_rows(img, ui, g['space'])
        else:
            self.draw_home(img, ui, g['space'])
        self.draw_side(img, ui, g['side'])

    def draw_tree(self, img, ui, box):
        x0, y0, x1, y1 = box
        self.tree_bg(img, ui, box, 'Categories')
        y = y0 + 28
        r = (x0 + 2, y, x1 - 4, y + 24)
        ui.listrow(r, ('cat', None), selected=self.cat is None and not self.search['text'], data=None)
        ui.icon('settings', (x0 + 10, y + 4), 16)
        ui.text((x0 + 32, y + 12), 'All categories', 12, anchor='lm')
        y += 28
        fnt = gfx.font(12)
        for key, label, ic, _d in CATS:
            r = (x0 + 2, y, x1 - 4, y + 24)
            ui.listrow(r, ('cat', key), selected=self.cat == key and not self.search['text'], data=key)
            ui.icon(ic, (x0 + 18, y + 4), 16)
            ui.text((x0 + 40, y + 12), gfx.ellipsize(fnt, label, x1 - x0 - 48), 12, anchor='lm')
            y += 26

    def draw_home(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.text((x0 + 24, y0 + 22), 'Adjust your computer\'s settings', 16, color=(0, 51, 153), kind='light')
        cols = 2 if x1 - x0 < 720 else 3
        cw = (x1 - x0 - 40) // cols
        for i, (key, label, ic, desc) in enumerate(CATS):
            cx = x0 + 20 + (i % cols) * cw
            cy = y0 + 56 + (i // cols) * 86
            r = (cx, cy, cx + cw - 12, cy + 78)
            ui.listrow(r, ('cat', key), data=key)
            ui.icon(ic, (cx + 8, cy + 10), 48)
            ui.text((cx + 66, cy + 16), label, 12, bold=True, color=(30, 80, 160))
            ui.wrap((cx + 66, cy + 36), desc, cw - 90, 11, color=theme.MUTED)
            if ui.hot(r):
                self.hover = ('cat', key)

    def draw_rows(self, img, ui, box):
        x0, y0, x1, y1 = box
        rows = self.visible()
        title = 'Search results for "%s"' % self.search['text'].strip() if self.search['text'].strip() else CAT[self.cat][1]
        ui.text((x0 + 24, y0 + 22), title, 16, color=(0, 51, 153), kind='light')
        rh = 46
        top = y0 + 50
        page = max(1, (y1 - top) // rh)
        self.page = page
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(rows) - page))
        if not rows:
            ui.text((x0 + 24, top + 10), 'No settings match.', 12, color=theme.MUTED)
        for i, s in enumerate(rows[self.scroll:self.scroll + page]):
            y = top + i * rh
            r = (x0 + 12, y, x1 - 20, y + rh - 4)
            ui.listrow(r, ('row', s['label']), selected=self.sel is s, data=s)
            if ui.hot(r):
                self.hover = s
            tag = {'both': 'COS 2.4 + Neptune', 'COS 2.4': 'COS 2.4'}.get(s['origin'], '')
            ui.text((x0 + 24, y + 13), s['label'], 12, bold=s['kind'] != 'info')
            sub = CAT[s['cat']][1] if self.search['text'].strip() else (tag or ' ')
            if s['kind'] == 'info':
                ui.text((x0 + 24, y + 30), gfx.ellipsize(gfx.font(11), s['help'], x1 - x0 - 60), 11, color=theme.MUTED)
            else:
                ui.text((x0 + 24, y + 30), sub, 10, color=(0, 120, 70) if tag and not self.search['text'] else theme.MUTED)
            self.control(img, ui, s, x1 - 26, y + 7)
        ui.scrollbar((x1 - 16, top, x1, y1), 'sb', self.scroll, page, len(rows))

    def control(self, img, ui, s, xr, y):
        k = s['kind']
        if k == 'bool':
            on = bool(self.value(s))
            box = (xr - 64, y + 4, xr, y + 28)
            hot = ui.hot(box)
            col = ((80, 180, 90), (40, 140, 60)) if on else ((200, 205, 212), (160, 165, 172))
            gfx.round_rect(img, box, 12, fill=gfx.vgradient(64, 24, ((0, col[0]), (1, col[1]))),
                           outline=(60, 127, 177, 255) if hot else (120, 125, 132, 255))
            kx = box[2] - 22 if on else box[0] + 2
            gfx.round_rect(img, (kx, y + 6, kx + 20, y + 26), 10, fill=gfx.vgradient(20, 20, ((0, (255, 255, 255)), (1, (225, 228, 232)))),
                           outline=(120, 125, 132, 255))
            ui.text(((box[0] + 22) if on else (box[2] - 20), y + 16), 'On' if on else 'Off', 10, bold=True,
                    color=(255, 255, 255) if on else (90, 90, 90), anchor='mm')
            ui.region(box, ('toggle', s['label']), data=s)
        elif k == 'choice':
            v = self.value(s)
            label = dict(GDR_MODES).get(v, v) if s['key'] == 'gdr_mode' else str(v)
            ui.button((xr - 200, y + 3, xr, y + 29), gfx.ellipsize(gfx.font(12), label, 170) + '  ▾', ('choose', s['label']),
                      data=s)
        elif k in ('int', 'float', 'pct'):
            v = self.value(s)
            if k == 'pct':
                lo, hi, step = 0, 100, 5
                v = int(round(float(v) * 100))
            else:
                lo, hi, step = s['opts']
            frac = (float(v) - lo) / max(1e-9, hi - lo)
            ui.button((xr - 236, y + 3, xr - 210, y + 29), '−', ('step', s['label'], -1), data=s)
            ui.slider((xr - 204, y + 8, xr - 84, y + 24), ('slide', s['label']), frac)
            ui.button((xr - 78, y + 3, xr - 52, y + 29), '+', ('step', s['label'], 1), data=s)
            shown = ('%g' % v) if k == 'float' else ('%d%%' % v if k == 'pct' or s['key'] in ('sound_volume', 'druman_volume') else str(int(v)))
            ui.text((xr - 4, y + 16), shown, 12, anchor='rm')
        elif k == 'action':
            ui.button((xr - 110, y + 3, xr, y + 29), 'Open', ('act', s['label']), data=s)

    def draw_side(self, img, ui, box):
        x0, y0, x1, y1 = box
        self.side_bg(img, ui, box)
        split = y0 + int((y1 - y0) * 0.55)
        h = self.hover if self.hover is not None else self.sel
        self.pane(img, ui, box, 'Overview', 'Setting.over')
        inner = (x0 + 8, y0 + 26, x1 - 8, split - 8)
        ui.rect(inner, fill=(255, 255, 255), outline=(215, 222, 232))
        if isinstance(h, dict):
            ui.icon(CAT[h['cat']][2], (inner[0] + 8, inner[1] + 8), 32)
            ui.wrap((inner[0] + 48, inner[1] + 10), h['label'], inner[2] - inner[0] - 56, 12, bold=True)
            ui.wrap((inner[0] + 8, inner[1] + 54), h['help'], inner[2] - inner[0] - 16, 11)
        elif isinstance(h, tuple) and h[0] == 'cat':
            c = CAT[h[1]]
            ui.icon(c[2], (inner[0] + 8, inner[1] + 8), 32)
            ui.wrap((inner[0] + 48, inner[1] + 10), c[1], inner[2] - inner[0] - 56, 12, bold=True)
            ui.wrap((inner[0] + 8, inner[1] + 54), c[3] + '.', inner[2] - inner[0] - 16, 11)
            n = sum(1 for s in SETTINGS if s['cat'] == h[1])
            ui.text((inner[0] + 8, inner[3] - 16), '%d setting%s' % (n, '' if n == 1 else 's'), 10, color=theme.MUTED)
        else:
            ui.wrap((inner[0] + 8, inner[1] + 10), 'Point at a setting to see what it does. Every COS v2.4 and '
                    'Neptune setting lives here; search finds any of them.', inner[2] - inner[0] - 16, 11,
                    color=theme.MUTED)
        self.pane(img, ui, (x0, split, x1, y1), 'Metadata')
        y = split + 28
        rows = []
        if isinstance(h, dict):
            if h['key'] and h['kind'] not in ('action', 'info'):
                rows = [('key', h['key']), ('value', str(self.value(h))), ('default', str(theme.DEFAULTS.get(h['key'], '-'))),
                        ('from', {'both': 'COS 2.4 + Neptune', 'COS 2.4': 'COS 2.4'}.get(h['origin'], 'COS Neptune'))]
                if h['v24']:
                    rows.append(('v2.4 name', h['v24']))
            else:
                rows = [('type', 'opens ' + (h['key'] or 'information').split(':')[-1]),
                        ('from', {'both': 'COS 2.4 + Neptune', 'COS 2.4': 'COS 2.4'}.get(h['origin'], 'COS Neptune'))]
        rows.append(('saved in', 'settings.json'))
        for k, v in rows:
            ui.text((x0 + 12, y), k + ':', 11, color=theme.MUTED)
            ui.text((x0 + 84, y), gfx.ellipsize(gfx.font(11), v, x1 - x0 - 94), 11)
            y += 18

    # -- input ------------------------------------------------------------------------
    def find(self, label):
        return next((s for s in SETTINGS if s['label'] == label), None)

    def on_command(self, cid, data):
        if isinstance(cid, tuple):
            k = cid[0]
            if k == 'cat':
                self.go(cid[1])
            elif k == 'row':
                self.sel = data
            elif k == 'toggle':
                s = self.find(cid[1])
                self.set(s, not bool(self.value(s)))
                self.sel = s
            elif k == 'choose':
                s = self.find(cid[1])
                self.sel = s
                cur = self.value(s)
                items = [{'label': dict(GDR_MODES).get(o, o) if s['key'] == 'gdr_mode' else o, 'checked': o == cur,
                          'action': (lambda o=o: self.set(s, o))} for o in s['opts']]
                cx0, cy0 = self.win.client_rect()[:2]
                mx, my = self.ui.mouse
                self.shell.popup(items, cx0 + mx - 120, cy0 + my + 12, min_w=200)
            elif k == 'step':
                s = self.find(cid[1])
                self.sel = s
                self.nudge(s, cid[2])
            elif k == 'act':
                self.action(self.find(cid[1]))
        elif cid == 'back' and self.history:
            self.future.append(self.cat)
            self.cat = self.history.pop()
            self.scroll = 0
        elif cid == 'fwd' and self.future:
            self.history.append(self.cat)
            self.cat = self.future.pop()
            self.scroll = 0
        elif cid == 'up':
            self.go(None)
        elif cid == 'refresh':
            pass
        elif cid == 'sb':
            self.scroll = data
        self.invalidate()

    def on_text(self, cid, state):
        if cid == 'search':
            self.scroll = 0
            self.invalidate()

    def on_slider(self, cid, value):
        if isinstance(cid, tuple) and cid[0] == 'slide':
            s = self.find(cid[1])
            if s['kind'] == 'pct':
                self.set(s, round(gfx.clamp(value, 0, 1), 2))
                return
            lo, hi, step = s['opts']
            v = lo + round((hi - lo) * gfx.clamp(value, 0, 1) / step) * step
            self.set(s, float(v) if s['kind'] == 'float' else int(v))

    def nudge(self, s, d):
        v = self.value(s)
        if s['kind'] == 'pct':
            self.set(s, round(gfx.clamp(float(v) + d * 0.05, 0, 1), 2))
            return
        lo, hi, step = s['opts']
        nv = gfx.clamp(float(v) + d * step, lo, hi)
        self.set(s, float(nv) if s['kind'] == 'float' else int(nv))

    def action(self, s):
        t = s['key']
        sh = self.shell
        if t.startswith('app:'):
            name = t[4:]
            if name in sh.registry:
                sh.launch(name)
            else:
                message(sh, 'Control Panel', '"%s" is not installed on this Neptune.' % s['label'], 'info', parent=self.win)
        elif t.startswith('help:'):
            sh.launch('help', page=t[5:])
        elif t == 'folder:ceap':
            sh.launch('files', path=os.path.join(theme.STATE, 'ceap'))
        elif t == 'fn:test_sound':
            sh.sound('notify')
        elif t == 'fn:restart':
            sh.shutdown('restart')
        elif t == 'fn:install':
            from .pkgapps import install_dialog
            install_dialog(sh, self.win)
        elif t == 'fn:cosprograms':
            sh.launch('programs', tab='cos')
        elif t == 'fn:reset':
            def go(ans):
                if ans != 'Reset':
                    return
                keep = {k: sh.settings.get(k) for k in ('lock_password', 'user_name', 'computer_name', 'oobe_done')}
                for k, v in theme.DEFAULTS.items():
                    sh.settings[k] = v.copy() if isinstance(v, (list, dict)) else v
                sh.settings.update({k: v for k, v in keep.items() if v is not None})
                sh.settings.save()
                sh.apply_settings()
                sh.notify('Control Panel', 'Every setting is back to its default.', 'info', 4)
                self.invalidate()
            message(sh, 'Reset all settings', 'Put every setting back to its default?\n\nYour lock password, name and '
                    'computer name are kept.', 'warning', ('Reset', 'Cancel'), go, parent=self.win)

    def on_wheel(self, dy, x, y):
        self.scroll = max(0, self.scroll - dy * 2)
        self.invalidate()
