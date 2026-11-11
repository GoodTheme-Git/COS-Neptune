"""CEAP App Maker: build a CEAP app by clicking, no code.

Left: the app's name, window title, icon and description, and buttons that
add Text, Input and Button pieces. Middle: a live preview of the window, and
the list of pieces (move them up and down, delete them). Right: the editor
for the selected piece; a button's actions are picked from a menu (say,
text, set, notify, open, explore, run, sound, log, close) with their values.
Log (bottom of the middle): everything the app does while you test it,
live: the window opening, each button, every action with its values,
variables changing, mistakes, and the app's own `log` lines.

Save writes a .ceap into home/.neptune/ceap, so it appears in APPS within a
couple of seconds. Test runs it straight away; View code shows the
.ceap text in Notepad; Open... loads an existing .ceap to edit.
"""
import os
import shlex
import time

from PIL import Image

from .. import gfx, theme
from ..app import App
from ..ui import UI
from . import ceap as ceapmod
from .ceap import CEAP_DIR, EXT, CeapApp, CeapNote, run_app
from .dialogs import FileDialog, message

ICONS = ['ceap', 'notepad', 'neptune', 'music', 'calc', 'folder', 'settings', 'help', 'webinator', 'paint']
VERBS = [('say', 'Pop up a message', ['message']), ('text', 'Change the window text', ['new text']),
         ('set', 'Set a variable', ['variable', 'value']), ('notify', 'Show a notification', ['message']),
         ('open', 'Open a program', ['program (e.g. calc)']), ('explore', 'Explore a folder', ['folder']),
         ('run', 'Run a command in a Terminal', ['command']), ('sound', 'Play a sound', ['notify or warn']),
         ('log', 'Write to the log', ['line (use {name} etc.)']), ('close', 'Close the window', [])]
VERB = {v[0]: v for v in VERBS}


def box(s=''):
    return {'text': str(s), 'cur': len(str(s))}


def q(s):
    s = str(s)
    return '"%s"' % s if '"' not in s and '\\' not in s else shlex.quote(s)


class Maker(App):
    title = 'CEAP App Maker'
    icon = 'ceap'
    size = (1120, 700)
    min_size = (900, 560)
    bg = theme.PANEL_BG

    def __init__(self, shell, path=None, **kw):
        super().__init__(shell, **kw)
        self.path = None
        self.dirty_doc = False
        self.log_all = False             # Log pane: this app only, or every CEAP app
        self.log_details = True          # show actions and variable changes, not just clicks and errors
        self.log_scroll = 0              # lines back from the newest
        self.log_seen = 0
        self.new_app()
        if path:
            self.load(path)

    # -- the model -----------------------------------------------------------------
    def new_app(self):
        self.name = box('My App')
        self.wtitle = box('My App')
        self.desc = box('Made with the CEAP App Maker.')
        self.icon_i = 0
        self.items = [{'kind': 'text', 'text': box('Hello! What is your name?')},
                      {'kind': 'input', 'var': box('name'), 'hint': box('Your name...')},
                      {'kind': 'button', 'label': box('Say hi'), 'actions': [['say', box('Hi, {name}!')]]}]
        self.sel = 0
        self.path = None
        self.dirty_doc = False

    def load(self, path):
        app = CeapApp(path)
        if app.error:
            message(self.shell, 'CEAP App Maker', 'That app has a mistake, so it cannot be opened here:\n%s' % app.error,
                    'error', parent=self.win)
            return
        note = next((w for w in app.windows if w['kind'] == 'note'), None)
        if note is None:
            message(self.shell, 'CEAP App Maker', 'The App Maker edits win.CEAP:Note apps. This one only has a '
                    'CEAP:Main window; open it in Notepad instead.', 'info', parent=self.win)
            return
        self.name = box(app.name)
        self.wtitle = box(note['title'])
        self.desc = box(app.meta.get('description', ''))
        ic = app.meta.get('icon', 'ceap')
        self.icon_i = ICONS.index(ic) if ic in ICONS else 0
        self.items = []
        for it in note['items']:
            if it[0] == 'text':
                self.items.append({'kind': 'text', 'text': box(it[1])})
            elif it[0] == 'input':
                self.items.append({'kind': 'input', 'var': box(it[1]), 'hint': box(it[2])})
            else:
                self.items.append({'kind': 'button', 'label': box(it[1]),
                                   'actions': [[a[0]] + [box(x) for x in a[1:]] for a in it[2]]})
        self.sel = 0 if self.items else None
        self.path = path if os.path.dirname(os.path.abspath(path)) == os.path.abspath(CEAP_DIR) else None
        self.dirty_doc = False
        self.invalidate()

    def source(self):
        out = ['# Made with the CEAP App Maker on %s' % time.strftime('%Y-%m-%d'),
               'app %s' % q(self.name['text'] or 'My App'),
               'icon %s' % ICONS[self.icon_i],
               'version 1.0',
               'author %s' % q(os.environ.get('USER', 'me'))]
        if self.desc['text'].strip():
            out.append('description %s' % q(self.desc['text']))
        out += ['', 'win.CEAP:Note %s' % q(self.wtitle['text'] or self.name['text'] or 'My App')]
        for it in self.items:
            k = it['kind']
            if k == 'text':
                out.append('    text %s' % q(it['text']['text']))
            elif k == 'input':
                var = ''.join(c for c in it['var']['text'] if c.isalnum() or c == '_') or 'answer'
                if var[0].isdigit():
                    var = 'v' + var
                out.append('    input %s %s' % (var, q(it['hint']['text'])))
            else:
                acts = []
                for a in it['actions']:
                    v = a[0]
                    args = [b['text'] for b in a[1:1 + len(VERB[v][2])]]
                    first = args[0] if args else ''
                    if v == 'set':
                        name = ''.join(c for c in first if c.isalnum() or c == '_') or 'x'
                        parts = ['set', name, q(args[1] if len(args) > 1 else '')]
                    elif v in ('open', 'sound'):
                        parts = [v, first if first and ' ' not in first and '"' not in first else q(first)]
                    elif v == 'close':
                        parts = ['close']
                    else:
                        parts = [v, q(first)]
                    acts.append(' '.join(parts))
                out.append('    button %s -> %s' % (q(it['label']['text'] or 'OK'), ' ; '.join(acts) or 'close'))
        return '\n'.join(out) + '\n'

    def spec(self):
        """Parse our own output, so the preview shows exactly what Save would write."""
        tmp = os.path.join(CEAP_DIR, '.maker-preview' + EXT)
        os.makedirs(CEAP_DIR, exist_ok=True)
        with open(tmp, 'w') as f:
            f.write(self.source())
        return CeapApp(tmp)

    # -- drawing -----------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        ui.command_bar((0, 0, w, 34))
        x = 6
        for label, cid in (('New', 'new'), ('Open...', 'open'), ('Save', 'save'), ('Test run', 'test'),
                           ('View code', 'code'), ('Log file', 'logfile')):
            bw = gfx.text_width(gfx.font(12), label) + 22
            ui.toolbutton((x, 5, x + bw, 29), label, cid)
            x += bw + 4
        where = os.path.basename(self.path) if self.path else 'not saved yet'
        ui.text((w - 12, 17), ('* ' if self.dirty_doc else '') + where, 11, color=theme.MUTED, anchor='rm')
        self.draw_left(img, ui, (0, 34, 270, h))
        self.draw_middle(img, ui, (270, 34, w - 330, h))
        self.draw_right(img, ui, (w - 330, 34, w, h))

    def draw_left(self, img, ui, b):
        x0, y0, x1, y1 = b
        img.alpha_composite(gfx.vgradient(x1 - x0, y1 - y0, ((0, (246, 249, 253)), (1, (232, 239, 247)))), (x0, y0))
        ui.d.line([(x1 - 1, y0), (x1 - 1, y1)], fill=(205, 214, 226, 255))
        y = y0 + 12
        ui.text((x0 + 12, y), 'The app', 13, bold=True, color=(30, 57, 91))
        y += 26
        for label, st, cid in (('Name', self.name, 'name'), ('Window title', self.wtitle, 'wtitle'),
                               ('Description', self.desc, 'desc')):
            ui.text((x0 + 12, y), label, 11, color=theme.MUTED)
            ui.textbox((x0 + 12, y + 16, x1 - 12, y + 40), cid, st)
            y += 50
        ui.text((x0 + 12, y), 'Icon', 11, color=theme.MUTED)
        y += 16
        for i, ic in enumerate(ICONS):
            bx = x0 + 12 + (i % 5) * 48
            by = y + (i // 5) * 44
            r = (bx, by, bx + 42, by + 40)
            ui.listrow(r, ('icon', i), selected=i == self.icon_i, data=i)
            ui.icon(ic, (bx + 5, by + 4), 32)
        y += 96
        ui.text((x0 + 12, y), 'Add a piece', 13, bold=True, color=(30, 57, 91))
        y += 24
        for label, cid in (('+ Text', 'add_text'), ('+ Input box', 'add_input'), ('+ Button', 'add_button')):
            ui.button((x0 + 12, y, x1 - 12, y + 28), label, cid)
            y += 34

    def draw_middle(self, img, ui, b):
        x0, y0, x1, y1 = b
        ui.rect(b, fill=(255, 255, 255))
        ui.text((x0 + 16, y0 + 12), 'Preview', 13, bold=True, color=(30, 57, 91))
        app = self.spec()
        ph = int((y1 - y0) * 0.36)
        log_top = y1 - 190
        area = (x0 + 16, y0 + 36, x1 - 16, y0 + 36 + ph)
        img.alpha_composite(gfx.vgradient(area[2] - area[0], area[3] - area[1], ((0, (14, 90, 120)), (1, (6, 30, 70)))),
                            (area[0], area[1]))
        if app.error:
            ui.wrap((area[0] + 16, area[1] + 16), 'Not quite: ' + app.error, area[2] - area[0] - 32, 12,
                    color=(255, 220, 200))
        else:
            note = CeapNote(self.shell, app=app, spec=app.windows[0], state={'vars': {}})
            pw, phh = note.size[0] - 2 * theme.BORDER, note.size[1] - theme.TITLE_H - theme.BORDER
            s = min(1.0, (area[2] - area[0] - 40) / pw, (area[3] - area[1] - 50) / (phh + 24))
            sub = Image.new('RGBA', (pw, phh), (255, 255, 255, 255))
            pui = UI()
            pui.begin(sub)
            note.draw(sub, pui)
            frame = Image.new('RGBA', (pw + 12, phh + 30), (0, 0, 0, 0))
            gfx.round_rect(frame, (0, 0, pw + 12, phh + 30), 7, fill=gfx.vgradient(pw + 12, phh + 30, ((0, (120, 170, 210, 235)),
                                                                                                    (1, (70, 120, 170, 235)))))
            frame.alpha_composite(sub, (6, 24))
            fi = gfx.font(11)
            from PIL import ImageDraw
            ImageDraw.Draw(frame).text((26, 12), note.title, font=fi, fill=(255, 255, 255, 255), anchor='lm')
            from .. import icons
            frame.alpha_composite(icons.get(ICONS[self.icon_i], 16), (6, 4))
            if s < 1:
                frame = frame.resize((int(frame.size[0] * s), int(frame.size[1] * s)), Image.LANCZOS)
            img.alpha_composite(frame, ((area[0] + area[2] - frame.size[0]) // 2, (area[1] + area[3] - frame.size[1]) // 2))
        # pieces
        ly = area[3] + 14
        ui.text((x0 + 16, ly), 'Pieces (top to bottom)', 13, bold=True, color=(30, 57, 91))
        ly += 24
        rh = 30
        for i, it in enumerate(self.items):
            r = (x0 + 16, ly, x1 - 120, ly + rh - 2)
            if r[3] > log_top - 6:
                break
            ui.listrow(r, ('piece', i), selected=i == self.sel, data=i)
            k = it['kind']
            summary = {'text': lambda: 'Text: ' + it['text']['text'],
                       'input': lambda: 'Input box  {%s}  "%s"' % (it['var']['text'], it['hint']['text']),
                       'button': lambda: 'Button "%s"  ->  %s' % (it['label']['text'], ', '.join(a[0] for a in it['actions']) or 'nothing')}[k]()
            ui.icon({'text': 'notepad', 'input': 'text', 'button': 'ceap'}[k], (r[0] + 6, ly + 6), 16)
            ui.text((r[0] + 28, ly + rh // 2 - 1), gfx.ellipsize(gfx.font(12), summary, r[2] - r[0] - 36), 12, anchor='lm')
            ly += rh
        if self.sel is not None:
            ui.button((x1 - 108, area[3] + 38, x1 - 16, area[3] + 62), 'Move up', 'up', enabled=self.sel > 0)
            ui.button((x1 - 108, area[3] + 68, x1 - 16, area[3] + 92), 'Move down', 'down',
                      enabled=self.sel < len(self.items) - 1)
            ui.button((x1 - 108, area[3] + 98, x1 - 16, area[3] + 122), 'Delete', 'del')
        self.draw_log(img, ui, (x0, log_top, x1, y1))

    LEVEL_COL = {'info': (40, 110, 200), 'action': (90, 90, 100), 'var': (130, 60, 170), 'user': (0, 130, 80),
                 'warn': (190, 120, 0), 'error': (200, 40, 30)}

    def log_rows(self):
        name = self.name['text'] or 'My App'
        rows = [r for r in ceapmod.LOG if (self.log_all or r[1] == name)]
        if not self.log_details:
            rows = [r for r in rows if r[2] not in ('action', 'var')]
        return rows

    def draw_log(self, img, ui, b):
        x0, y0, x1, y1 = b
        ui.d.line([(x0, y0), (x1, y0)], fill=(205, 214, 226, 255))
        ui.text((x0 + 16, y0 + 14), 'Log', 13, bold=True, color=(30, 57, 91), anchor='lm')
        rows = self.log_rows()
        ui.text((x0 + 52, y0 + 14), '%d entr%s' % (len(rows), 'y' if len(rows) == 1 else 'ies'), 10, color=theme.MUTED,
                anchor='lm')
        bx = x1 - 16
        for label, cid, on in (('Clear', 'logclear', None), ('All apps', 'logall', self.log_all),
                               ('Details', 'logdet', self.log_details)):
            bw = gfx.text_width(gfx.font(11), label) + 18
            ui.toolbutton((bx - bw, y0 + 3, bx, y0 + 25), label, cid, size=11, toggled=bool(on))
            bx -= bw + 4
        box = (x0 + 16, y0 + 30, x1 - 16, y1 - 10)
        ui.rect(box, fill=(250, 251, 253), outline=(205, 214, 226))
        fnt = gfx.font(11, False, 'mono')
        lh = 15
        n = max(1, (box[3] - box[1] - 8) // lh)
        self.log_scroll = gfx.clamp(self.log_scroll, 0, max(0, len(rows) - n))
        shown = rows[max(0, len(rows) - n - self.log_scroll):len(rows) - self.log_scroll]
        if not rows:
            ui.wrap((box[0] + 10, box[1] + 8), 'Nothing yet. Click Test run and use the app: every button, action, '
                    'variable and mistake shows up here. The "Write to the log" action adds your own lines.',
                    box[2] - box[0] - 20, 11, color=theme.MUTED)
        for i, (t, app, lvl, text) in enumerate(shown):
            y = box[1] + 5 + i * lh
            stamp = time.strftime('%H:%M:%S', time.localtime(t))
            ui.d.text((box[0] + 8, y), stamp, font=fnt, fill=(140, 145, 155, 255))
            col = self.LEVEL_COL.get(lvl, (60, 60, 60))
            ui.d.text((box[0] + 74, y), lvl.upper()[:6], font=gfx.font(10, True, 'mono'), fill=col + (255,))
            who = (app + ': ') if self.log_all else ''
            line = gfx.ellipsize(fnt, who + text, box[2] - box[0] - 140)
            ui.d.text((box[0] + 128, y), line, font=gfx.font_for_obj(fnt, line or 'x'),
                      fill=(200, 40, 30, 255) if lvl == 'error' else (30, 35, 45, 255))
        if self.log_scroll:
            ui.text((box[2] - 8, box[1] + 6), '%d newer below' % self.log_scroll, 10, color=(40, 110, 200), anchor='ra')
        ui.region(box, 'logbox', 'page')

    def draw_right(self, img, ui, b):
        x0, y0, x1, y1 = b
        img.alpha_composite(gfx.vgradient(x1 - x0, y1 - y0, ((0, (250, 252, 255)), (1, (238, 243, 249)))), (x0, y0))
        ui.d.line([(x0, y0), (x0, y1)], fill=(205, 214, 226, 255))
        y = y0 + 12
        if self.sel is None or self.sel >= len(self.items):
            ui.text((x0 + 14, y), 'Pick a piece to edit it.', 12, color=theme.MUTED)
            return
        it = self.items[self.sel]
        k = it['kind']
        ui.text((x0 + 14, y), {'text': 'Text', 'input': 'Input box', 'button': 'Button'}[k], 13, bold=True, color=(30, 57, 91))
        y += 28

        def field(label, st, cid, note=None):
            nonlocal y
            ui.text((x0 + 14, y), label, 11, color=theme.MUTED)
            ui.textbox((x0 + 14, y + 16, x1 - 14, y + 40), cid, st)
            y += 46
            if note:
                y = ui.wrap((x0 + 14, y - 4), note, x1 - x0 - 28, 10, color=theme.MUTED) + 4
        if k == 'text':
            field('What it says', it['text'], ('f', 'text'), 'Use {name} to show what someone typed in the "name" box; '
                  '{date}, {time} and {user} work too.')
        elif k == 'input':
            field('Variable name', it['var'], ('f', 'var'), 'What gets typed lands in {this}.')
            field('Hint shown when empty', it['hint'], ('f', 'hint'))
        else:
            field('Label', it['label'], ('f', 'label'))
            ui.text((x0 + 14, y), 'When clicked, do this:', 12, bold=True)
            y += 22
            for ai, a in enumerate(it['actions']):
                ui.button((x0 + 14, y, x0 + 150, y + 24), VERB[a[0]][1][:20] + ' ▾', ('verb', ai), size=10)
                ui.button((x1 - 40, y, x1 - 14, y + 24), '×', ('rmact', ai), size=11)
                y += 28
                for vi, hint in enumerate(VERB[a[0]][2]):
                    while len(a) <= vi + 1:
                        a.append(box())
                    ui.textbox((x0 + 28, y, x1 - 14, y + 24), ('arg', ai, vi), a[vi + 1], placeholder=hint)
                    y += 28
                y += 6
                if y > y1 - 70:
                    break
            ui.button((x0 + 14, y, x0 + 150, y + 26), '+ Add an action', 'addact')

    # -- input ------------------------------------------------------------------------------
    def changed(self):
        self.dirty_doc = True
        self.invalidate()

    def on_text(self, cid, state):
        self.changed()

    def on_command(self, cid, data):
        it = self.items[self.sel] if self.sel is not None and self.sel < len(self.items) else None
        if isinstance(cid, tuple):
            k = cid[0]
            if k == 'icon':
                self.icon_i = cid[1]
                self.changed()
            elif k == 'piece':
                self.sel = cid[1]
            elif k == 'verb' and it:
                ai = cid[1]
                items = [{'label': '%s  (%s)' % (desc, v), 'checked': it['actions'][ai][0] == v,
                          'action': (lambda v=v: self.set_verb(ai, v))} for v, desc, _a in VERBS]
                cx0, cy0 = self.win.client_rect()[:2]
                mx, my = self.ui.mouse
                self.shell.popup(items, cx0 + mx, cy0 + my + 10, min_w=240)
            elif k == 'rmact' and it:
                del it['actions'][cid[1]]
                self.changed()
            self.invalidate()
            return
        if cid == 'add_text':
            self.add({'kind': 'text', 'text': box('New text')})
        elif cid == 'add_input':
            n = sum(1 for x in self.items if x['kind'] == 'input') + 1
            self.add({'kind': 'input', 'var': box('answer%d' % n if n > 1 else 'answer'), 'hint': box('Type here...')})
        elif cid == 'add_button':
            self.add({'kind': 'button', 'label': box('Click me'), 'actions': [['say', box('You clicked me!')]]})
        elif cid == 'addact' and it:
            it['actions'].append(['notify', box('Hello!')])
            self.changed()
        elif cid in ('up', 'down') and it:
            j = self.sel + (-1 if cid == 'up' else 1)
            if 0 <= j < len(self.items):
                self.items[self.sel], self.items[j] = self.items[j], self.items[self.sel]
                self.sel = j
                self.changed()
        elif cid == 'del' and it:
            del self.items[self.sel]
            self.sel = min(self.sel, len(self.items) - 1) if self.items else None
            self.changed()
        elif cid == 'new':
            self.ask_discard(self.new_app)
        elif cid == 'open':
            self.ask_discard(lambda: self.shell.open_window(FileDialog(self.shell, 'open', start=CEAP_DIR, exts=(EXT,),
                                                                       callback=self.load), parent=self.win))
        elif cid == 'save':
            self.save()
        elif cid == 'test':
            app = self.spec()
            if app.error:
                ceapmod.log(self.name['text'] or 'My App', 'error', 'test run stopped: %s' % app.error)
            else:
                ceapmod.log(self.name['text'] or 'My App', 'info', 'test run from the App Maker (%d piece%s)'
                            % (len(self.items), '' if len(self.items) == 1 else 's'))
            p = os.path.join(CEAP_DIR, '.maker-test' + EXT)
            with open(p, 'w') as f:
                f.write(self.source())
            self.log_scroll = 0
            run_app(self.shell, p)
        elif cid == 'logclear':
            name = self.name['text'] or 'My App'
            keep = [r for r in ceapmod.LOG if not (self.log_all or r[1] == name)]
            ceapmod.LOG.clear()
            ceapmod.LOG.extend(keep)
            self.log_scroll = 0
        elif cid == 'logall':
            self.log_all = not self.log_all
            self.log_scroll = 0
        elif cid == 'logdet':
            self.log_details = not self.log_details
            self.log_scroll = 0
        elif cid == 'logfile':
            if not os.path.exists(ceapmod.LOG_FILE):
                ceapmod.log('CEAP', 'info', 'log file started')
            self.shell.launch('notepad', path=ceapmod.LOG_FILE)
        elif cid == 'code':
            p = os.path.join(CEAP_DIR, '.maker-code.txt')
            with open(p, 'w') as f:
                f.write(self.source())
            self.shell.launch('notepad', path=p)
        self.invalidate()

    def add(self, piece):
        self.items.append(piece)
        self.sel = len(self.items) - 1
        self.changed()

    def set_verb(self, ai, v):
        it = self.items[self.sel]
        it['actions'][ai] = [v] + [box() for _ in VERB[v][2]]
        self.changed()

    def ask_discard(self, then):
        if not self.dirty_doc:
            then()
            return

        def go(ans):
            if ans == 'Discard':
                then()
                self.invalidate()
        message(self.shell, 'CEAP App Maker', 'Your changes to "%s" are not saved. Discard them?' % self.name['text'],
                'question', ('Discard', 'Cancel'), go, parent=self.win)

    def save(self):
        app = self.spec()
        if app.error:
            ceapmod.log(self.name['text'] or 'My App', 'warn', 'not saved: %s' % app.error)
            message(self.shell, 'CEAP App Maker', 'Fix this first: %s' % app.error, 'warning', parent=self.win)
            return
        if not self.path:
            stem = ''.join(c for c in self.name['text'] if c.isalnum() or c in ' -_').strip() or 'My App'
            p = os.path.join(CEAP_DIR, stem + EXT)
            i = 2
            while os.path.exists(p):
                p = os.path.join(CEAP_DIR, '%s (%d)%s' % (stem, i, EXT))
                i += 1
            self.path = p
        with open(self.path, 'w') as f:
            f.write(self.source())
        self.dirty_doc = False
        if getattr(self.shell, 'ceap', None):
            self.shell.ceap.scan()
        ceapmod.log(self.name['text'], 'info', 'saved as %s' % os.path.basename(self.path))
        self.shell.notify('CEAP App Maker', 'Saved "%s". Find it in the APPS menu.' % self.name['text'], 'info', 5)

    @property
    def live(self):
        return False

    def on_tick(self, now):
        n = len(ceapmod.LOG) and ceapmod.LOG[-1][0]
        if n != self.log_seen:
            self.log_seen = n
            return True
        return False

    def on_wheel(self, dy, x, y):
        h = self.ui.hit(x, y)
        if h and h[0] == 'logbox':
            self.log_scroll = max(0, self.log_scroll + dy * 3)
            self.invalidate()

    def on_close(self):
        for n in ('.maker-preview', '.maker-test'):
            try:
                os.remove(os.path.join(CEAP_DIR, n + EXT))
            except OSError:
                pass
        if self.dirty_doc:
            def go(ans):
                if ans == 'Close anyway':
                    self.dirty_doc = False
                    self.close()
            message(self.shell, 'CEAP App Maker', '"%s" has unsaved changes.' % self.name['text'], 'question',
                    ('Close anyway', 'Cancel'), go, parent=self.win)
            return False
        return True
