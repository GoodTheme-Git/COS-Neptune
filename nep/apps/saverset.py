"""Screen Saver Settings: pick a saver, the idle time, and whether coming
back shows the lock screen. The little monitor plays the chosen saver live."""
import time

from .. import gfx, savers, theme
from ..app import App

CHOICES = ['(None)'] + savers.NAMES


class SaverSettings(App):
    title = 'Screen Saver Settings'
    icon = 'saver'
    size = (470, 560)
    dialog = True
    resizable = False
    single_instance = True
    bg = theme.CLIENT_BG
    PW, PH = 240, 135

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        s = shell.settings
        self.choice = s.get('screensaver', 'Bubbles') if s.get('screensaver') in CHOICES else '(None)'
        self.wait = int(s.get('saver_wait', 10) or 10)
        self.lock = bool(s.get('saver_lock'))
        self.mini = None
        self.mini_t = 0
        self.make_mini()

    def make_mini(self):
        self.mini = None if self.choice == '(None)' else savers.make(self.choice, self.PW, self.PH, self.shell.settings.tint)

    @property
    def live(self):
        return self.mini is not None

    def on_tick(self, now):
        if self.mini is not None and now - self.mini_t > 1 / 15:
            self.mini_t = now
            return True
        return False

    def draw(self, img, ui):
        w, h = img.size
        ui.rect((0, 0, w, h), fill=theme.PANEL_BG)
        # monitor with the live preview
        mx, my = (w - self.PW) // 2, 18
        bezel = (mx - 14, my - 12, mx + self.PW + 14, my + self.PH + 22)
        gfx.round_rect(img, bezel, 8, fill=gfx.vgradient(bezel[2] - bezel[0], bezel[3] - bezel[1],
                                                        ((0, (80, 86, 96)), (1, (28, 30, 36)))))
        gfx.round_rect(img, bezel, 8, outline=(15, 15, 18, 255))
        ui.d.ellipse((w // 2 - 3, bezel[3] - 12, w // 2 + 3, bezel[3] - 6), fill=(80, 200, 255, 255))
        stand = [(w // 2 - 22, bezel[3]), (w // 2 + 22, bezel[3]), (w // 2 + 34, bezel[3] + 18), (w // 2 - 34, bezel[3] + 18)]
        ui.d.polygon(stand, fill=(60, 64, 72, 255))
        if self.mini is not None:
            img.alpha_composite(self.mini.frame(time.time()), (mx, my))
        else:
            img.alpha_composite(self.shell.wall().resize((self.PW, self.PH)), (mx, my))
        y = bezel[3] + 36
        ui.groupbox((14, y, w - 14, y + 34 + 22 * ((len(CHOICES) + 1) // 2)), 'Screen saver')
        for i, n in enumerate(CHOICES):
            ui.radio((30 + (i % 2) * 210, y + 24 + (i // 2) * 22), n, ('pick', n), self.choice == n, data=n)
        y += 46 + 22 * ((len(CHOICES) + 1) // 2)
        en = self.choice != '(None)'
        ui.text((22, y + 12), 'Wait:', 12, anchor='lm', color=theme.CLIENT_TEXT if en else (160, 160, 160))
        ui.button((64, y, 90, y + 24), '−', 'less', enabled=en)
        ui.rect((94, y, 140, y + 24), fill=(255, 255, 255), outline=(171, 173, 179))
        ui.text((117, y + 12), str(self.wait), 12, anchor='mm')
        ui.button((144, y, 170, y + 24), '+', 'more', enabled=en)
        ui.text((178, y + 12), 'minutes', 12, anchor='lm')
        ui.button((w - 110, y, w - 20, y + 26), 'Preview', 'preview', enabled=en)
        y += 36
        ui.checkbox((22, y), 'On resume, display lock screen', 'lock', self.lock)
        if self.lock and not self.shell.settings.get('lock_password'):
            ui.wrap((40, y + 20), 'No lock screen password is set, so anyone can click past it. '
                    'Set one in CONTROL > Lock Screen...', w - 60, 11, color=theme.MUTED)
        ui.rect((0, h - 46, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - 46, (223, 223, 223))
        ui.button((w - 266, h - 35, w - 186, h - 11), 'OK', 'ok', default=True)
        ui.button((w - 178, h - 35, w - 98, h - 11), 'Cancel', 'cancel')
        ui.button((w - 90, h - 35, w - 12, h - 11), 'Apply', 'apply')

    def save(self):
        s = self.shell.settings
        s['screensaver'] = self.choice
        s['saver_wait'] = self.wait
        s['saver_lock'] = self.lock
        s.save()
        self.shell.last_input = time.time()

    def on_command(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'pick':
            self.choice = cid[1]
            self.make_mini()
        elif cid == 'less':
            self.wait = max(1, self.wait - (5 if self.wait > 10 else 1))
        elif cid == 'more':
            self.wait = min(120, self.wait + (5 if self.wait >= 10 else 1))
        elif cid == 'lock':
            self.lock = data
        elif cid == 'preview':
            self.shell.start_saver(preview=True, name=self.choice)
        elif cid in ('ok', 'apply'):
            self.save()
            if cid == 'ok':
                self.close()
        elif cid == 'cancel':
            self.close()
        self.invalidate()

    def on_key(self, ev):
        if ev['key'] == 'escape':
            self.close()
            return True
        if ev['key'] == 'enter':
            self.on_command('ok', None)
            return True
        return False
