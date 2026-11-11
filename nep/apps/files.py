"""Neptune Explorer and Photo Viewer."""
import os
import shutil
import stat
import time

from PIL import Image

from .. import gfx, icons, theme
from ..app import App
from .dialogs import message


def _human(n):
    for unit in ('bytes', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return ('%d %s' % (n, unit)) if unit == 'bytes' else ('%.1f %s' % (n, unit))
        n /= 1024.0


def _kind(name, isdir):
    if isdir:
        return 'File folder'
    low = name.lower()
    for end, label in (('.tar.gz', 'TAR.GZ Archive'), ('.tar.xz', 'TAR.XZ Archive'), ('.tar.bz2', 'TAR.BZ2 Archive'),
                       ('.gz', 'GZip Compressed File')):
        if low.endswith(end):
            return label
    ext = name.rsplit('.', 1)[-1].upper() if '.' in name else ''
    return {'TXT': 'Text Document', 'PY': 'Python File', 'PNG': 'PNG Image', 'JPG': 'JPEG Image',
            'JPEG': 'JPEG Image', 'MP3': 'MP3 Audio', 'WAV': 'Wave Sound', 'MP4': 'MP4 Video', 'MKV': 'Matroska Video', 'WEBM': 'WebM Video', 'NEPAPP': 'Neptune App', 'MD': 'Markdown Document',
            'JSON': 'JSON File', 'SH': 'Shell Script', 'COSAPP': 'COS 2.x Program', 'CIP': 'COS Installer Package',
            'CDI': 'COS Driver Install', 'PDF': 'PDF Document', 'GIF': 'GIF Image', 'ZIP': 'Compressed (zipped) Folder',
            'TGZ': 'TAR.GZ Archive', 'TAR': 'TAR Archive', 'TXZ': 'TAR.XZ Archive', 'XZ': 'XZ Compressed File',
            'BZ2': 'BZip2 Compressed File', 'CEAP': 'CEAPApp'}.get(
        ext, ('%s File' % ext) if ext else 'File')


def inside_home(path):
    home = os.path.realpath(theme.HOME)
    p = os.path.realpath(path)
    return p == home or p.startswith(home + os.sep)


class Explorer(App):
    title = 'Explorer'
    icon = 'folder'
    size = (780, 520)
    min_size = (420, 260)

    def __init__(self, shell, path=None, **kw):
        super().__init__(shell, **kw)
        self.history = []
        self.future = []
        self.path = None
        self.sel = None
        self.scroll = 0
        self.show_hidden = False
        self.view = 'details'
        self.search = {'text': '', 'cur': 0}
        self.rename = None
        self.go(path or theme.HOME, record=False)

    # -- navigation ------------------------------------------------------------------
    def go(self, path, record=True):
        path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isdir(path):
            message(self.shell, 'Explorer', 'Neptune can\'t find "%s".' % path, 'warning',
                    parent=self.win if self.win else None)
            return
        if record and self.path:
            self.history.append(self.path)
            self.future = []
        self.path = path
        self.sel = None
        self.scroll = 0
        self.search = {'text': '', 'cur': 0}
        self.addr = {'text': path, 'cur': len(path)}
        self.refresh()
        name = os.path.basename(path.rstrip('/')) or 'Computer'
        if path == os.path.realpath(theme.HOME):
            name = 'Neptune Home'
        self.icon = 'computer' if path == '/' else ('documents' if name == 'Documents' else 'folder')
        self.set_title(name)

    def refresh(self):
        items = []
        err = None
        try:
            names = os.listdir(self.path)
        except OSError as e:
            names = []
            err = e.strerror
        q = self.search['text'].lower()
        for n in names:
            if n.startswith('.') and not self.show_hidden:
                continue
            if q and q not in n.lower():
                continue
            p = os.path.join(self.path, n)
            try:
                st = os.stat(p)
                isdir = stat.S_ISDIR(st.st_mode)
                items.append((n, isdir, st.st_size, st.st_mtime))
            except OSError:
                items.append((n, False, 0, 0))
        items.sort(key=lambda t: (not t[1], t[0].lower()))
        self.items = items
        self.error = err

    # -- drawing ------------------------------------------------------------------------
    NAV = [('Favorites', None), ('Desktop', 'Desktop'), ('Documents', 'Documents'), ('Pictures', 'Pictures'),
           ('Neptune Home', ''), (None, None), ('Computer', '/'), ('File System', '/'),
           ('User Home', '~'), ('COS', '~/cos')]

    def nav_path(self, rel):
        if rel in ('/',):
            return '/'
        if rel.startswith('~'):
            return os.path.expanduser(rel)
        return os.path.join(theme.HOME, rel) if rel else theme.HOME

    def draw(self, img, ui):
        w, h = img.size
        # address / navigation bar (glass-tinted band, as in Win7 Explorer)
        band = gfx.vgradient(w, 36, ((0, (235, 244, 250)), (1, (215, 230, 242))))
        img.alpha_composite(band, (0, 0))
        for i, (cid, glyph, en) in enumerate((('back', '◀', bool(self.history)),
                                              ('fwd', '▶', bool(self.future)))):
            cx = 18 + i * 28
            r = (cx - 12, 6, cx + 12, 30)
            hot = ui.hot(r) and en
            top, bot = ((120, 200, 250), (20, 110, 200)) if hot else (((90, 160, 220), (30, 80, 160)) if en else
                                                                   ((200, 210, 220), (160, 170, 185)))
            m = gfx.rounded_mask(24, 24, 12)
            gfx.paste_masked(img, gfx.vgradient(24, 24, ((0, top), (1, bot))), m, (r[0], r[1]))
            gfx.round_rect(img, r, 12, outline=(30, 60, 100, 200))
            ui.text((cx, 18), glyph, 11, color=(255, 255, 255), anchor='mm')
            if en:
                ui.region(r, cid)
        ui.toolbutton((64, 7, 88, 29), '▲', 'up', size=10)
        ui.textbox((94, 7, w - 210, 29), 'addr', self.addr)
        ui.textbox((w - 200, 7, w - 10, 29), 'search', self.search, placeholder='Search %s' %
                   (os.path.basename(self.path) or 'Computer'))
        # command bar
        ui.command_bar((0, 36, w, 66))
        x = 8
        cmds = [('Organize ▾', 'organize'), ('New folder', 'newfolder'), ('New text file', 'newfile')]
        if self.sel is not None:
            cmds = [('Open', 'open')] + cmds + [('Rename', 'rename'), ('Delete', 'delete')]
        for label, cid in cmds:
            tw = gfx.text_width(gfx.font(12), label) + 18
            ui.toolbutton((x, 40, x + tw, 62), label, cid)
            x += tw + 2
        ui.toolbutton((w - 120, 40, w - 64, 62), 'Details', 'v_details', toggled=self.view == 'details')
        ui.toolbutton((w - 62, 40, w - 8, 62), 'Icons', 'v_icons', toggled=self.view == 'icons')
        # navigation pane
        top, bottom = 66, h - 24
        ui.panel_gradient((0, top, 170, bottom), (250, 252, 255), (236, 243, 250))
        ui.d.line([(170, top), (170, bottom)], fill=(214, 229, 245, 255))
        y = top + 8
        for label, rel in self.NAV:
            if label is None:
                y += 8
                continue
            if rel is None:
                ui.text((12, y + 10), label, 12, True, color=(30, 57, 91), anchor='lm')
                y += 24
                continue
            p = self.nav_path(rel)
            on = os.path.abspath(p) == self.path
            ui.listrow((6, y, 164, y + 22), ('nav', p), on)
            ic = {'Desktop': 'computer', 'Documents': 'documents', 'Pictures': 'pictures', 'Computer': 'computer',
                  'File System': 'computer'}.get(label, 'folder')
            ui.icon(ic, (16, y + 3), 16)
            ui.text((38, y + 11), label, 12, anchor='lm')
            y += 24
        # main list
        lx0 = 171
        area = (lx0, top, w, bottom)
        if self.view == 'details':
            self.draw_details(img, ui, area)
        else:
            self.draw_icons(img, ui, area)
        # details pane / status bar
        ui.rect((0, bottom, w, h), fill=(240, 244, 249))
        ui.hline(0, w, bottom, (200, 210, 225))
        if self.sel is not None and self.sel < len(self.items):
            n, isdir, size, mt = self.items[self.sel]
            info = '%s   %s   %s' % (n, _kind(n, isdir), '' if isdir else _human(size))
        else:
            info = '%d items' % len(self.items)
            if not inside_home(self.path):
                info += '   (read-only: Neptune only changes files inside its home folder)'
        ui.text((10, bottom + 12), gfx.ellipsize(gfx.font(11), info, w - 20), 11, color=(50, 60, 80), anchor='lm')

    def draw_details(self, img, ui, area):
        x0, y0, x1, y1 = area
        w = x1 - x0
        cols = [('Name', 0.46), ('Date modified', 0.22), ('Type', 0.18), ('Size', 0.14)]
        hx = x0
        img.alpha_composite(gfx.vgradient(w, 24, ((0, (255, 255, 255)), (1, (242, 245, 250)))), (x0, y0))
        ui.hline(x0, x1, y0 + 23, (213, 213, 213))
        cxs = []
        for name, frac in cols:
            cw = int((w - 18) * frac)
            cxs.append(hx)
            ui.text((hx + 8, y0 + 12), name, 12, color=(76, 96, 122), anchor='lm')
            ui.d.line([(hx + cw - 1, y0 + 3), (hx + cw - 1, y0 + 21)], fill=(226, 230, 236, 255))
            hx += cw
        if self.error:
            ui.text((x0 + 20, y0 + 40), 'Access denied: %s' % self.error, 12, color=(160, 40, 30))
        rh = 22
        rows = max(1, (y1 - y0 - 26) // rh)
        self.rows = rows
        fnt = gfx.font(12)
        for i, (n, isdir, size, mt) in enumerate(self.items[self.scroll:self.scroll + rows]):
            idx = self.scroll + i
            y = y0 + 26 + i * rh
            ui.listrow((x0 + 2, y, x1 - 20, y + rh - 1), ('item', idx), self.sel == idx, kind='item')
            ui.icon(icons.for_path(n, isdir), (cxs[0] + 6, y + 3), 16)
            if self.rename and self.rename[0] == idx:
                ui.textbox((cxs[0] + 26, y + 1, cxs[1] - 6, y + rh - 2), 'rename', self.rename[1])
            else:
                ui.text((cxs[0] + 28, y + 11), gfx.ellipsize(fnt, n, cxs[1] - cxs[0] - 34), 12, anchor='lm')
            ui.text((cxs[1] + 8, y + 11), time.strftime('%m/%d/%Y %I:%M %p', time.localtime(mt)) if mt else '', 12,
                    color=(109, 109, 109), anchor='lm')
            ui.text((cxs[2] + 8, y + 11), gfx.ellipsize(fnt, _kind(n, isdir), max(20, x1 - cxs[2] - 100)), 12,
                    color=(109, 109, 109), anchor='lm')
            if not isdir:
                ui.text((x1 - 28, y + 11), _human(size), 12, color=(109, 109, 109), anchor='rm')
        ui.region((x0, y0 + 24, x1 - 18, y1), 'blank', 'blank')
        # re-register rows on top of the blank region (regions are hit-tested last-first)
        regs = [r for r in ui.regions if r[1] == 'blank']
        ui.regions = regs + [r for r in ui.regions if r[1] != 'blank']
        ui.scrollbar((x1 - 17, y0 + 24, x1, y1), 'sb', self.scroll, rows, len(self.items))

    def draw_icons(self, img, ui, area):
        x0, y0, x1, y1 = area
        cw, ch = 96, 92
        per_row = max(1, (x1 - x0 - 30) // cw)
        rows = max(1, (y1 - y0 - 8) // ch)
        self.rows = rows * per_row
        self.per_row = per_row
        fnt = gfx.font(11)
        ui.region((x0, y0, x1 - 18, y1), 'blank', 'blank')
        start = (self.scroll // per_row) * per_row
        for i, (n, isdir, size, mt) in enumerate(self.items[start:start + rows * per_row]):
            idx = start + i
            cx = x0 + 10 + (i % per_row) * cw
            cy = y0 + 8 + (i // per_row) * ch
            ui.listrow((cx, cy, cx + cw - 6, cy + ch - 6), ('item', idx), self.sel == idx, kind='item')
            name = icons.for_path(n, isdir)
            if name == 'image' and size < 8_000_000:
                th = self.thumb(os.path.join(self.path, n))
                if th is not None:
                    img.alpha_composite(th, (cx + (cw - 6 - th.size[0]) // 2, cy + 4 + (48 - th.size[1]) // 2))
                else:
                    ui.icon(name, (cx + (cw - 6 - 48) // 2, cy + 4), 48)
            else:
                ui.icon(name, (cx + (cw - 6 - 48) // 2, cy + 4), 48)
            t = gfx.ellipsize(fnt, n, cw - 10)
            ui.text((cx + (cw - 6) // 2, cy + 64), t, 11, anchor='ma')
        ui.scrollbar((x1 - 17, y0, x1, y1), 'sb', start // per_row, rows, (len(self.items) + per_row - 1) // per_row)

    _thumbs = {}

    def thumb(self, path):
        if path in self._thumbs:
            return self._thumbs[path]
        try:
            im = Image.open(path)
            im.thumbnail((56, 48))
            im = im.convert('RGBA')
        except Exception:
            im = None
        self._thumbs[path] = im
        return im

    # -- interaction ---------------------------------------------------------------------
    def open_item(self, idx):
        n, isdir, size, mt = self.items[idx]
        p = os.path.join(self.path, n)
        if isdir:
            self.go(p)
            return
        kind = icons.for_path(n, False)
        if n.lower().endswith(('.html', '.htm')):
            self.shell.launch('webinator', url='file://' + p)
        elif kind == 'image':
            self.shell.launch('photos', path=p)
        elif kind in ('music', 'video'):
            self.shell.launch('druman', path=p)
        elif kind == 'cosapp':
            self.run_cosapp(p)
        elif kind in ('package', 'driver'):
            self.shell.launch('installer', path=p)
        elif kind == 'archive':
            self.shell.launch('archiver', path=p)
        elif kind == 'theme':
            self.shell.launch('cosplace', path=p)
        elif kind == 'ceap':
            from .ceap import run_app
            run_app(self.shell, p)
        elif kind == 'text' or size < 2_000_000:
            self.shell.launch('notepad', path=p)
        else:
            message(self.shell, 'Explorer', 'Neptune has no program for "%s".' % n, 'info', parent=self.win)

    def on_command(self, cid, data):
        if cid == 'back' and self.history:
            self.future.append(self.path)
            self.go(self.history.pop(), record=False)
        elif cid == 'fwd' and self.future:
            self.history.append(self.path)
            self.go(self.future.pop(), record=False)
        elif cid == 'up':
            self.go(os.path.dirname(self.path.rstrip('/')) or '/')
        elif cid == 'addr' and data == 'enter':
            self.go(self.addr['text'])
        elif cid == 'search' and data == 'enter':
            self.refresh()
        elif cid == 'sb':
            self.scroll = data * (getattr(self, 'per_row', 1) if self.view == 'icons' else 1)
        elif cid == 'v_details':
            self.view = 'details'
            self.scroll = 0
        elif cid == 'v_icons':
            self.view = 'icons'
            self.scroll = 0
        elif cid == 'blank':
            self.sel = None
            self.commit_rename()
        elif isinstance(cid, tuple) and cid[0] == 'nav':
            self.go(cid[1])
        elif isinstance(cid, tuple) and cid[0] == 'item':
            if self.rename and self.rename[0] != cid[1]:
                self.commit_rename()
            self.sel = cid[1]
        elif cid == 'open' and self.sel is not None:
            self.open_item(self.sel)
        elif cid == 'organize':
            self.shell.popup(self.organize_menu(), *self.screen_pos(8, 62))
        elif cid == 'newfolder':
            self.make_new(folder=True)
        elif cid == 'newfile':
            self.make_new(folder=False)
        elif cid == 'rename':
            self.start_rename()
        elif cid == 'rename' and data == 'enter':
            self.commit_rename()
        elif cid == 'delete':
            self.delete()

    def screen_pos(self, x, y):
        cx, cy, _, _ = self.win.client_rect()
        return cx + x, cy + y

    def organize_menu(self):
        return [{'label': 'Show hidden files', 'checked': self.show_hidden, 'action': self.toggle_hidden},
                {'label': 'Refresh', 'shortcut': 'F5', 'action': self.refresh},
                {'sep': True},
                {'label': 'Open in Terminal', 'icon': 'terminal', 'action': lambda: self.shell.launch('terminal', cwd=self.path)},
                {'sep': True},
                {'label': 'Close', 'action': self.close}]

    def toggle_hidden(self):
        self.show_hidden = not self.show_hidden
        self.refresh()
        self.invalidate()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'item':
            self.open_item(cid[1])
        else:
            self.on_command(cid, data)

    def on_text(self, cid, state):
        if cid == 'search':
            self.scroll = 0
            self.sel = None
            self.refresh()

    def on_wheel(self, dy, x, y):
        step = 3 * (getattr(self, 'per_row', 1) if self.view == 'icons' else 1)
        self.scroll = int(gfx.clamp(self.scroll - dy * step, 0, max(0, len(self.items) - getattr(self, 'rows', 10))))

    def on_key(self, ev):
        k = ev['key']
        if k == 'f5':
            self.refresh()
            return True
        if k == 'f2':
            self.start_rename()
            return True
        if k == 'delete':
            self.delete(permanent=bool(ev.get('shift')))
            return True
        if k == 'backspace':
            self.on_command('up', None)
            return True
        if ev.get('alt') and k in ('left', 'right'):
            self.on_command('back' if k == 'left' else 'fwd', None)
            return True
        if ev.get('alt') and k == 'up':
            self.on_command('up', None)
            return True
        if k == 'enter' and self.sel is not None:
            self.open_item(self.sel)
            return True
        if k in ('up', 'down') and self.items:
            step = (getattr(self, 'per_row', 1) if self.view == 'icons' else 1) * (1 if k == 'down' else -1)
            self.sel = int(gfx.clamp((self.sel if self.sel is not None else -1) + step, 0, len(self.items) - 1))
            rows = getattr(self, 'rows', 10)
            if self.sel < self.scroll:
                self.scroll = self.sel
            elif self.sel >= self.scroll + rows:
                self.scroll = self.sel - rows + 1
            return True
        if k == 'escape' and self.rename:
            self.rename = None
            self.ui.focus = None
            return True
        return False

    def context_menu(self, x, y):
        h = self.ui.hit(x, y)
        if h and isinstance(h[0], tuple) and h[0][0] == 'item':
            self.sel = h[0][1]
            name = self.items[self.sel][0]
            p = os.path.join(self.path, name)
            extra = []
            if name.endswith('.cosapp'):
                extra = [{'label': 'Run in compatibility mode', 'icon': 'cosapp', 'action': lambda: self.run_cosapp(p)},
                         {'label': 'Compatibility settings...', 'action': lambda: self.shell.launch('compat', path=p)},
                         {'sep': True}]
            elif name.endswith(('.cip', '.cdi')):
                extra = [{'label': 'Install...', 'icon': 'package', 'action': lambda: self.shell.launch('installer', path=p)},
                         {'sep': True}]
            elif self.items[self.sel][1] and os.path.isfile(os.path.join(p, 'package.json')):
                extra = [{'label': 'Build package', 'icon': 'package', 'action': lambda: self.build_package(p)},
                         {'sep': True}]
            extra += self.archive_menu(p, self.items[self.sel][1])
            return extra + [{'label': 'Open', 'action': lambda: self.open_item(self.sel)},
                    {'label': 'Open with Notepad', 'icon': 'notepad',
                     'action': lambda: self.shell.launch('notepad', path=os.path.join(self.path, self.items[self.sel][0]))},
                    {'sep': True},
                    {'label': 'Rename', 'enabled': inside_home(self.path), 'action': self.start_rename},
                    {'label': 'Delete', 'shortcut': 'Del', 'enabled': inside_home(self.path), 'action': self.delete},
                    {'label': 'Delete for good', 'shortcut': 'Shift+Del', 'enabled': inside_home(self.path),
                     'action': lambda: self.delete(permanent=True)},
                    {'sep': True},
                    {'label': 'Properties', 'action': self.properties}]
        return [{'label': 'View', 'sub': [{'label': 'Details', 'checked': self.view == 'details',
                                           'action': lambda: self.on_command('v_details', None)},
                                          {'label': 'Icons', 'checked': self.view == 'icons',
                                           'action': lambda: self.on_command('v_icons', None)}]},
                {'label': 'Refresh', 'action': self.refresh},
                {'sep': True},
                {'label': 'New Folder', 'enabled': inside_home(self.path), 'action': lambda: self.make_new(True)},
                {'label': 'New Text Document', 'enabled': inside_home(self.path), 'action': lambda: self.make_new(False)},
                {'sep': True},
                {'label': 'Open in Terminal', 'action': lambda: self.shell.launch('terminal', cwd=self.path)}]

    # -- file operations (Neptune home only) -----------------------------------------------
    def guard(self):
        if inside_home(self.path):
            return True
        message(self.shell, 'Explorer', 'This folder is outside Neptune\'s home folder, so Neptune will not '
                                        'change anything in it.\n\nNeptune home: %s' % theme.HOME, 'warning',
                parent=self.win)
        return False

    def make_new(self, folder):
        if not self.guard():
            return
        base = 'New folder' if folder else 'New Text Document.txt'
        name = base
        i = 2
        while os.path.exists(os.path.join(self.path, name)):
            name = ('New folder (%d)' % i) if folder else ('New Text Document (%d).txt' % i)
            i += 1
        p = os.path.join(self.path, name)
        try:
            if folder:
                os.mkdir(p)
            else:
                open(p, 'w').close()
        except OSError as e:
            message(self.shell, 'Explorer', e.strerror, 'error', parent=self.win)
            return
        self.refresh()
        for idx, it in enumerate(self.items):
            if it[0] == name:
                self.sel = idx
                self.start_rename()
        self.invalidate()

    def start_rename(self):
        if self.sel is None or not self.guard():
            return
        n = self.items[self.sel][0]
        stem = n.rsplit('.', 1)[0] if '.' in n and not self.items[self.sel][1] else n
        self.rename = (self.sel, {'text': n, 'cur': len(stem)})
        self.ui.focus = 'rename'
        self.invalidate()

    def commit_rename(self):
        if not self.rename:
            return
        idx, st = self.rename
        self.rename = None
        self.ui.focus = None
        old = self.items[idx][0]
        new = st['text'].strip().replace('/', '')
        if new and new != old:
            try:
                os.rename(os.path.join(self.path, old), os.path.join(self.path, new))
            except OSError as e:
                message(self.shell, 'Rename', e.strerror, 'error', parent=self.win)
        self.refresh()
        self.invalidate()

    def key(self, ev):
        if self.rename and ev['type'] == 'key' and ev['key'] == 'enter':
            self.commit_rename()
            return True
        return super().key(ev)

    def delete(self, permanent=False):
        """Move the selection to the Recycle Bin (Shift+Delete: delete for good)."""
        if self.sel is None or not self.guard():
            return
        n, isdir, *_ = self.items[self.sel]
        p = os.path.join(self.path, n)
        from .. import trash

        def answer(r):
            if r != 'Yes':
                return
            try:
                if not permanent:
                    trash.recycle(p)
                    self.shell.desk_dirty = True
                elif isdir:
                    shutil.rmtree(p)
                else:
                    os.remove(p)
            except (OSError, trash.TrashError) as e:
                message(self.shell, 'Delete', getattr(e, 'strerror', None) or str(e), 'error')
            self.sel = None
            self.refresh()
            self.invalidate()
        if permanent:
            message(self.shell, 'Delete %s' % ('Folder' if isdir else 'File'),
                    'Are you sure you want to permanently delete "%s"? This cannot be undone.' % n, 'warning',
                    ('Yes', 'No'), answer, parent=self.win)
        else:
            message(self.shell, 'Delete %s' % ('Folder' if isdir else 'File'),
                    'Move "%s" to the Recycle Bin?' % n, 'question', ('Yes', 'No'), answer, parent=self.win)

    def archive_menu(self, p, isdir):
        """Zip / unzip / gzip / gunzip entries for one item."""
        from .. import archive as A
        name = os.path.basename(p)
        here = inside_home(self.path)
        job = lambda action: (lambda: self.shell.launch('archivejob', action=action, path=p))
        out = []
        if not isdir and A.is_archive_name(name):
            kind = A.detect(p)
            if kind in A.SINGLE:
                out += [{'label': 'Decompress (un%s)' % {'gz': 'gzip', 'bz2': 'bzip2', 'xz': 'xz'}[kind], 'icon': 'archive',
                         'action': job('decompress')}]
            elif kind:
                out += [{'label': 'Extract All...', 'icon': 'archive', 'action': lambda: self.shell.launch('extract', path=p)},
                        {'label': 'Extract Here', 'enabled': here, 'action': job('extract_here')},
                        {'label': 'Open with Archiver', 'action': lambda: self.shell.launch('archiver', path=p)}]
            out.append({'sep': True})
        send = [{'label': 'Compressed (zipped) folder', 'icon': 'archive', 'action': job('zip')},
                {'label': 'TAR.GZ archive', 'action': job('tar.gz')}]
        if not isdir and not A.is_archive_name(name):
            send.append({'label': 'GZip file (keeps the original)', 'action': job('gz')})
        out += [{'label': 'Send to', 'sub': send},
                {'label': 'Compress...', 'action': lambda: self.shell.launch('compress', paths=[p])},
                {'sep': True}]
        return out

    def run_cosapp(self, p):
        reg = getattr(self.shell, 'compat', None)
        prog = reg.by_path(p) if reg else None
        if prog is not None and prog.key in self.shell.registry:
            self.shell.launch(prog.key)
        else:
            self.shell.launch('cosrun', path=p)

    def build_package(self, folder):
        """Build a .cip/.cdi from a folder into Documents (Neptune never writes outside its home)."""
        from .. import packages
        try:
            import json
            with open(os.path.join(folder, 'package.json')) as f:
                m = json.load(f)
            kind = str(m.get('format', '')).split('/')[0]
            out = os.path.join(theme.HOME, 'Documents', '%s.%s' % (m.get('name', 'package'), kind))
            out = packages.build(folder, out)
        except Exception as e:
            message(self.shell, 'Build package', 'The package could not be built:\n%s' % e, 'error', parent=self.win)
            return
        self.shell.notify('Package built', 'Saved %s in Documents.' % os.path.basename(out), 'info', 5)
        if inside_home(self.path):
            self.refresh()

    def properties(self):
        if self.sel is None:
            return
        n, isdir, size, mt = self.items[self.sel]
        p = os.path.join(self.path, n)
        message(self.shell, '%s Properties' % n,
                'Location: %s\nType: %s\nSize: %s\nModified: %s' % (
                    self.path, _kind(n, isdir), '-' if isdir else _human(size),
                    time.strftime('%A, %B %d, %Y %I:%M %p', time.localtime(mt)) if mt else '?'),
                icons.for_path(n, isdir) if icons.for_path(n, isdir) in ('info',) else 'info', parent=self.win)


# ---------------------------------------------------------------------------
class Photos(App):
    title = 'Neptune Photo Viewer'
    icon = 'image'
    size = (760, 540)
    bg = (230, 238, 245, 255)

    def __init__(self, shell, path=None, **kw):
        super().__init__(shell, **kw)
        self.path = path
        self.zoom = None
        self.siblings = []
        if path:
            d = os.path.dirname(path)
            try:
                self.siblings = sorted([os.path.join(d, n) for n in os.listdir(d)
                                        if icons.for_path(n, False) == 'image'], key=str.lower)
            except OSError:
                pass
        self.load()

    def load(self):
        self.img = None
        self.error = None
        self._fit = None
        if self.path:
            try:
                im = Image.open(self.path)
                im.load()
                self.img = im.convert('RGBA')
            except Exception as e:
                self.error = str(e)
            self.set_title('%s - Neptune Photo Viewer' % os.path.basename(self.path))

    def draw(self, img, ui):
        w, h = img.size
        img.alpha_composite(gfx.vgradient(w, h, ((0, (238, 244, 249)), (1, (205, 220, 233)))), (0, 0))
        bar = 44
        area = (10, 10, w - 10, h - bar - 6)
        aw, ah = area[2] - area[0], area[3] - area[1]
        if self.img is not None:
            iw, ih = self.img.size
            s = min(aw / iw, ah / ih, 1.0) * (self.zoom or 1.0)
            key = (int(iw * s), int(ih * s))
            if self._fit is None or self._fit.size != key:
                self._fit = self.img.resize((max(1, key[0]), max(1, key[1])), Image.LANCZOS)
            fx = area[0] + (aw - key[0]) // 2
            fy = area[1] + (ah - key[1]) // 2
            sh, pad = gfx.shadow(key[0], key[1], 1, 8, 120)
            gfx.alpha_composite_clip(img, sh, fx - pad, fy - pad + 3)
            gfx.alpha_composite_clip(img, self._fit, fx, fy)
        else:
            ui.text((w // 2, h // 2 - 20), self.error or 'No picture selected.', 13, color=theme.MUTED, anchor='mm')
        # bottom glass-ish control bar
        img.alpha_composite(gfx.vgradient(w, bar, ((0, (60, 120, 150)), (0.5, (30, 80, 115)), (0.51, (15, 60, 95)),
                                                   (1, (30, 90, 125)))), (0, h - bar))
        cx = w // 2
        for i, (cid, glyph) in enumerate((('prev', '◀'), ('zoomout', '−'), ('fit', '▢'),
                                           ('zoomin', '+'), ('next', '▶'))):
            x = cx + (i - 2) * 46
            r = (x - 18, h - bar + 7, x + 18, h - 7)
            hot = ui.hot(r)
            m = gfx.rounded_mask(36, r[3] - r[1], 15)
            top, bot = ((140, 220, 255), (30, 130, 210)) if hot else ((110, 170, 210), (40, 90, 140))
            gfx.paste_masked(img, gfx.vgradient(36, r[3] - r[1], ((0, top), (1, bot))), m, (r[0], r[1]))
            gfx.round_rect(img, r, 15, outline=(10, 30, 50, 220))
            ui.text((x, (r[1] + r[3]) // 2), glyph, 14, True, color=(255, 255, 255), anchor='mm')
            ui.region(r, cid)
        if self.siblings and self.path in self.siblings:
            ui.text((12, h - bar // 2), '%d of %d' % (self.siblings.index(self.path) + 1, len(self.siblings)), 11,
                    color=(220, 240, 250), anchor='lm')

    def step(self, d):
        if self.path in self.siblings and self.siblings:
            i = (self.siblings.index(self.path) + d) % len(self.siblings)
            self.path = self.siblings[i]
            self.zoom = None
            self.load()

    def on_command(self, cid, data):
        if cid == 'prev':
            self.step(-1)
        elif cid == 'next':
            self.step(1)
        elif cid == 'zoomin':
            self.zoom = min(8.0, (self.zoom or 1.0) * 1.25)
        elif cid == 'zoomout':
            self.zoom = max(0.1, (self.zoom or 1.0) / 1.25)
        elif cid == 'fit':
            self.zoom = None

    def on_key(self, ev):
        k = ev['key']
        if k in ('left', 'right'):
            self.step(-1 if k == 'left' else 1)
            return True
        return False

    def on_wheel(self, dy, x, y):
        self.on_command('zoomin' if dy > 0 else 'zoomout', None)
