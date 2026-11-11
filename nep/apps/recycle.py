"""Recycle Bin: see what was deleted, restore it, or delete it for good."""
import os
import time

from PIL import Image

from .. import gfx, icons, theme, trash
from .ceapframe import CeapFrame
from .dialogs import message
from .files import _human, _kind

REL = lambda p: p.replace(theme.ROOT, '~/cos/neptune').replace(os.path.expanduser('~'), '~', 1)


class RecycleBin(CeapFrame):
    title = 'Recycle Bin'
    icon = 'recycle'
    size = (980, 600)
    min_size = (720, 420)
    single_instance = True
    tree_w = 190

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        self.sel = set()
        self.anchor = None
        self.search = {'text': '', 'cur': 0}
        self.scroll = 0
        self.items = []
        self.t_scan = 0
        self.refresh()

    def refresh(self):
        q = self.search['text'].strip().lower()
        self.items = [r for r in trash.items() if not q or q in r['name'].lower()]
        ids = {r['id'] for r in self.items}
        self.sel &= ids
        self.t_scan = time.time()

    def chosen(self):
        return [r for r in self.items if r['id'] in self.sel]

    # -- drawing -------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        g = self.geometry()
        self.draw_toolbar(img, ui, w, [('restore', '↶', bool(self.sel)), ('refresh', '⟳', True)],
                          'Recycle Bin  ·  %d item%s  ·  %s' % (len(self.items), '' if len(self.items) == 1 else 's',
                                                               _human(sum(r.get('size', 0) for r in self.items))),
                          self.search, 'Search the Recycle Bin')
        self.draw_tree(img, ui, g['tree'])
        self.draw_list(img, ui, g['space'])
        self.draw_side(img, ui, g['side'])

    def draw_tree(self, img, ui, box):
        x0, y0, x1, y1 = box
        self.tree_bg(img, ui, box, 'Recycle Bin tasks')
        y = y0 + 30
        for label, cid, en in (('Restore selected', 'restore', bool(self.sel)),
                               ('Restore all items', 'restore_all', bool(self.items)),
                               ('Delete selected', 'forever', bool(self.sel)),
                               ('Empty the Recycle Bin', 'empty', bool(self.items))):
            if en:
                ui.link((x0 + 14, y), label, cid, 12)
            else:
                ui.text((x0 + 14, y), label, 12, color=(160, 165, 175))
            y += 24
        ui.wrap((x0 + 14, y1 - 92), 'Files you delete in Neptune come here first. Shift+Delete in File Explorer skips '
                'the Recycle Bin.', x1 - x0 - 28, 11, color=theme.MUTED)

    def draw_list(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.rect(box, fill=(255, 255, 255))
        ui.region(box, 'blank', 'item')
        if not self.items:
            ui.icon('recycle', ((x0 + x1) // 2 - 32, y0 + 60), 64)
            ui.text(((x0 + x1) // 2, y0 + 146), 'The Recycle Bin is empty.' if not self.search['text'].strip() else
                    'Nothing in the Recycle Bin matches.', 13, color=theme.MUTED, anchor='mm')
            return
        cols = [('Name', 0.36), ('Original location', 0.34), ('Date deleted', 0.18), ('Size', 0.12)]
        width = x1 - x0 - 20
        hx = x0 + 4
        spans = []
        for name, frac in cols:
            cw = int(width * frac)
            right = name == 'Size'
            ui.text((hx + cw - 8 if right else hx + 6, y0 + 12), name, 12, color=(76, 96, 122), anchor='rm' if right else 'lm')
            spans.append((hx, hx + cw, right))
            hx += cw
        ui.hline(x0, x1, y0 + 24, (213, 213, 213))
        rh = 24
        top = y0 + 28
        page = max(1, (y1 - top) // rh)
        self.page = page
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(self.items) - page))
        fnt = gfx.font(12)
        for i in range(self.scroll, min(len(self.items), self.scroll + page)):
            r = self.items[i]
            y = top + (i - self.scroll) * rh
            ui.listrow((x0 + 4, y, x1 - 20, y + rh - 2), ('item', i), r['id'] in self.sel, data=i, kind='item')
            ic = 'folder' if r.get('is_dir') else icons.for_path(r['name'], False)
            vals = [r['name'], REL(r['from']), time.strftime('%m/%d/%Y %I:%M %p', time.localtime(r['deleted'])),
                    '' if r.get('is_dir') else _human(r.get('size', 0))]
            for k, (a, b, right) in enumerate(spans):
                tx = a + (28 if k == 0 else 6)
                if k == 0:
                    ui.icon(ic, (a + 6, y + 3), 16)
                ui.text((b - 8 if right else tx, y + rh // 2 - 1), gfx.ellipsize(fnt, vals[k], b - tx - 6), 12,
                        color=theme.CLIENT_TEXT if k == 0 else (90, 96, 104), anchor='rm' if right else 'lm')
        ui.scrollbar((x1 - 18, top, x1 - 1, y1), 'sb', self.scroll, page, len(self.items))

    def draw_side(self, img, ui, box):
        x0, y0, x1, y1 = box
        self.side_bg(img, ui, box)
        ch = self.chosen()
        split = y0 + int((y1 - y0) * 0.55)
        self.pane(img, ui, box, 'Overview')
        inner = (x0 + 8, y0 + 26, x1 - 8, split - 8)
        ui.rect(inner, fill=(255, 255, 255), outline=(215, 222, 232))
        if len(ch) == 1:
            r = ch[0]
            p = os.path.join(trash.FILES, r['id'])
            shown = False
            if not r.get('is_dir') and icons.for_path(r['name'], False) == 'image':
                try:
                    im = Image.open(p)
                    im.thumbnail((inner[2] - inner[0] - 16, inner[3] - inner[1] - 16))
                    im = im.convert('RGBA')
                    img.alpha_composite(im, ((inner[0] + inner[2] - im.size[0]) // 2, (inner[1] + inner[3] - im.size[1]) // 2))
                    shown = True
                except Exception:
                    pass
            if not shown and not r.get('is_dir') and r.get('size', 0) < 1_000_000:
                try:
                    with open(p, 'rb') as f:
                        raw = f.read(3000)
                    if b'\x00' not in raw:
                        fnt = gfx.font(10, False, 'mono')
                        y = inner[1] + 5
                        for ln in raw.decode('utf-8', 'replace').splitlines():
                            if y + 13 > inner[3]:
                                break
                            ui.d.text((inner[0] + 6, y), gfx.ellipsize(fnt, ln.replace('\t', '    '), inner[2] - inner[0] - 12),
                                      font=gfx.font_for_obj(fnt, ln or 'x'), fill=(40, 50, 60, 255))
                            y += 13
                        shown = True
                except OSError:
                    pass
            if not shown:
                ic = 'folder' if r.get('is_dir') else icons.for_path(r['name'], False)
                ui.icon(ic, ((inner[0] + inner[2]) // 2 - 32, (inner[1] + inner[3]) // 2 - 32), 64)
        else:
            msg = '%d items selected.' % len(ch) if ch else 'Pick an item to see what it was.'
            ui.wrap((inner[0] + 8, inner[1] + 10), msg, inner[2] - inner[0] - 16, 11, color=theme.MUTED)
        self.pane(img, ui, (x0, split, x1, y1), 'Metadata')
        y = split + 28
        rows = []
        if len(ch) == 1:
            r = ch[0]
            rows = [('name', r['name']), ('was in', REL(r['from'])), ('type', _kind(r['name'], r.get('is_dir'))),
                    ('size', _human(r.get('size', 0))),
                    ('deleted', time.strftime('%m/%d/%Y %I:%M:%S %p', time.localtime(r['deleted'])))]
        elif ch:
            rows = [('items', str(len(ch))), ('size', _human(sum(r.get('size', 0) for r in ch)))]
        for k, v in rows:
            ui.text((x0 + 12, y), k + ':', 11, color=theme.MUTED)
            ui.text((x0 + 70, y), gfx.ellipsize(gfx.font(11), v, x1 - x0 - 80), 11)
            y += 18
        if ch:
            ui.button((x0 + 10, y1 - 34, x0 + 110, y1 - 8), 'Restore', 'restore', default=True)
            ui.button((x0 + 116, y1 - 34, x1 - 10, y1 - 8), 'Delete for good', 'forever')

    # -- actions ---------------------------------------------------------------------
    def notify_explorers(self, folders):
        from .files import Explorer
        real = {os.path.realpath(f) for f in folders}
        for w in self.shell.windows:
            if isinstance(w.app, Explorer) and os.path.realpath(w.app.path) in real:
                w.app.refresh()
                w.app.invalidate()

    def restore(self, recs):
        done, fail, folders = 0, [], set()
        for r in recs:
            try:
                dest = trash.restore(r['id'])
                folders.add(os.path.dirname(dest))
                done += 1
            except (OSError, trash.TrashError) as e:
                fail.append('%s: %s' % (r['name'], getattr(e, 'strerror', None) or e))
        self.sel.clear()
        self.refresh()
        self.notify_explorers(folders)
        if done:
            self.shell.notify('Recycle Bin', 'Restored %d item%s.' % (done, '' if done == 1 else 's'), 'info', 4)
        if fail:
            message(self.shell, 'Recycle Bin', 'Some items could not be restored:\n' + '\n'.join(fail[:6]), 'error',
                    parent=self.win)

    def forever(self, recs, all_=False):
        if not recs:
            return
        what = 'all %d items in the Recycle Bin' % len(recs) if all_ else (
            '"%s"' % recs[0]['name'] if len(recs) == 1 else 'these %d items' % len(recs))

        def go(ans):
            if ans != 'Delete':
                return
            for r in recs:
                try:
                    trash.delete_forever(r['id'])
                except OSError:
                    pass
            self.sel.clear()
            self.refresh()
            self.shell.desk_dirty = True
            self.invalidate()
        message(self.shell, 'Delete for good', 'Permanently delete %s? This cannot be undone.' % what, 'warning',
                ('Delete', 'Cancel'), go, parent=self.win)

    def on_command(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'item':
            i = cid[1]
            rid = self.items[i]['id']
            if self.ctrl:
                self.sel ^= {rid}
            elif self.shift and self.anchor is not None:
                a, b = sorted((self.anchor, i))
                self.sel = {r['id'] for r in self.items[a:b + 1]}
            else:
                self.sel = {rid}
            if not self.shift:
                self.anchor = i
        elif cid == 'blank':
            self.sel.clear()
        elif cid == 'restore':
            self.restore(self.chosen())
        elif cid == 'restore_all':
            self.restore(list(self.items))
        elif cid == 'forever':
            self.forever(self.chosen())
        elif cid == 'empty':
            self.forever(list(trash.items()), all_=True)
        elif cid == 'refresh':
            self.refresh()
        elif cid == 'sb':
            self.scroll = data
        self.invalidate()

    ctrl = shift = False

    def key(self, ev):
        if ev['type'] in ('key', 'keyup'):
            self.ctrl, self.shift = bool(ev.get('ctrl')), bool(ev.get('shift'))
        if ev['type'] == 'key' and self.ui.focus is None:
            k = ev['key']
            if k == 'delete' and self.sel:
                self.forever(self.chosen())
                return True
            if ev.get('ctrl') and k == 'a':
                self.sel = {r['id'] for r in self.items}
                self.invalidate()
                return True
            if k == 'enter' and self.sel:
                self.restore(self.chosen())
                return True
        return super().key(ev)

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'item':
            self.sel = {self.items[cid[1]]['id']}
            self.restore(self.chosen())
        else:
            self.on_command(cid, data)

    def on_text(self, cid, state):
        if cid == 'search':
            self.refresh()
            self.invalidate()

    def on_wheel(self, dy, x, y):
        self.scroll = max(0, self.scroll - dy * 3)
        self.invalidate()

    def on_tick(self, now):
        if now - self.t_scan > 2:
            n = len(self.items)
            self.refresh()
            return len(self.items) != n
        return False

    def context_menu(self, x, y):
        h = self.ui.hit(x, y)
        if h and isinstance(h[0], tuple) and h[0][0] == 'item':
            rid = self.items[h[0][1]]['id']
            if rid not in self.sel:
                self.sel = {rid}
            return [{'label': 'Restore', 'action': lambda: self.restore(self.chosen())},
                    {'sep': True},
                    {'label': 'Delete for good', 'action': lambda: self.forever(self.chosen())}]
        return [{'label': 'Restore all items', 'enabled': bool(self.items), 'action': lambda: self.restore(list(self.items))},
                {'label': 'Empty the Recycle Bin', 'enabled': bool(self.items),
                 'action': lambda: self.forever(list(trash.items()), all_=True)}]
