"""Lock Screen settings: the startup login screen and an optional password.

Like COS v2.4's login screen, this locks the look of the desktop. It is not
security: the password is kept in plain text in settings.json."""
from .. import gfx, theme
from ..app import App


class LockSettings(App):
    title = 'Lock Screen'
    icon = 'lock'
    size = (440, 330)
    dialog = True
    resizable = False
    single_instance = True
    bg = theme.CLIENT_BG

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        s = shell.settings
        self.at_start = bool(s.get('login_screen'))
        self.pw = {'text': s.get('lock_password', ''), 'cur': len(s.get('lock_password', ''))}
        self.pw2 = {'text': s.get('lock_password', ''), 'cur': len(s.get('lock_password', ''))}
        self.err = ''

    def draw(self, img, ui):
        w, h = img.size
        ui.icon('lock', (20, 18), 40)
        ui.text((74, 20), 'Lock screen', 16, color=(30, 57, 91))
        ui.wrap((74, 46), 'Super+L locks Neptune. The lock screen hides your desktop; it is not real security.',
                w - 94, 11, color=theme.MUTED)
        ui.checkbox((24, 96), 'Show the lock screen when Neptune starts', 'start', self.at_start)
        ui.groupbox((16, 126, w - 16, 236), 'Password (optional)')
        ui.text((30, 152), 'Password:', 12)
        ui.textbox((140, 146, w - 32, 170), 'pw', self.pw, placeholder='leave empty for none', password=True)
        ui.text((30, 190), 'Confirm:', 12)
        ui.textbox((140, 184, w - 32, 208), 'pw2', self.pw2, password=True)
        if self.err:
            ui.text((30, 248), self.err, 12, color=(190, 30, 20))
        ui.rect((0, h - 46, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - 46, (223, 223, 223))
        ui.button((16, h - 35, 116, h - 11), 'Lock now', 'locknow')
        ui.button((w - 182, h - 35, w - 100, h - 11), 'OK', 'ok', default=True)
        ui.button((w - 92, h - 35, w - 12, h - 11), 'Cancel', 'cancel')

    def save(self):
        if self.pw['text'] != self.pw2['text']:
            self.err = 'The passwords do not match.'
            self.invalidate()
            return False
        s = self.shell.settings
        s['login_screen'] = self.at_start
        s['lock_password'] = self.pw['text']
        s.save()
        return True

    def on_command(self, cid, data):
        if cid == 'start':
            self.at_start = data
        elif cid in ('ok', 'pw', 'pw2') and (cid == 'ok' or data == 'enter'):
            if self.save():
                self.close()
        elif cid == 'cancel':
            self.close()
        elif cid == 'locknow':
            if self.save():
                self.close()
                self.shell.lock()
        self.invalidate()

    def on_key(self, ev):
        if ev['key'] == 'escape':
            self.close()
            return True
        return False
