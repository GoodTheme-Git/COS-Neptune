"""The COS Place, as a CEAP app: browse the peer-to-peer web of COS computers,
get things (themes, pictures, videos, music, apps), share your own, keep
endangered items alive, and manage your themes.

The network is nep/cosplace.py (protocol v1, shared with COS Aurora) and the
theme files are nep/themes.py. Joining is off until you say so: everything on
your shelf is then public to your network, with no accounts by design.
Nothing that comes from The COS Place is ever run: apps only land in
Downloads/COS Place, and installing one is up to you.
"""
import os
import threading
import time

from PIL import Image

from .. import cosplace, gfx, icons, theme, themes
from .ceapframe import CeapFrame
from .dialogs import FileDialog, message

KIND_ICON = {'theme': 'theme', 'image': 'image', 'video': 'video', 'music': 'music', 'app': 'cosapp'}
VIEWS = [('all', 'Everything', 'cosplace'), ('theme', 'Themes', 'theme'), ('image', 'Pictures', 'image'),
         ('video', 'Videos', 'video'), ('music', 'Music', 'music'), ('app', 'Apps', 'cosapp'),
         ('shelf', 'My shelf', 'folder'), ('endangered', 'Endangered', 'warning'),
         ('computers', 'Computers', 'computer'), ('mythemes', 'Themes on this computer', 'personalize')]
VIEW = {v[0]: v for v in VIEWS}


def human(n):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'bytes' else ('%.1f %s' % (n, unit))
        n /= 1024.0


def ensure_running(shell):
    """Start the node if the user has joined (called by the shell at startup and by the app)."""
    if not shell.settings.get('cosplace_join'):
        return None
    node = cosplace.get(shell.settings)
    if not node.running:
        node.start()
    return node


class COSPlace(CeapFrame):
    title = 'The COS Place'
    icon = 'cosplace'
    size = (1180, 720)
    min_size = (860, 520)
    single_instance = True
    tree_w = 220
    side_w = 290

    def __init__(self, shell, view=None, path=None, **kw):
        super().__init__(shell, **kw)
        self.node = cosplace.get(shell.settings)
        self.view = view if view in VIEW else 'all'
        self.search = {'text': '', 'cur': 0}
        self.addr = {'text': '', 'cur': 0}          # Computers: add host[:port]
        self.newname = {'text': '', 'cur': 0}       # Themes: name for "make a theme"
        self.sel = None                             # sha256, theme id or peer address
        self.scroll = 0
        self.thumbs = {}
        self.busy = {}                              # sha -> (got bytes, total)
        self.notes = []
        if path:                                    # opened with a .ctheme file
            self.view = 'mythemes'
            self.install_theme_file(path)
        ensure_running(shell)

    def reopen(self, path=None, view=None, **kw):
        if path:
            self.view, self.scroll = 'mythemes', 0
            self.install_theme_file(path)
        elif view in VIEW:
            self.view, self.sel, self.scroll = view, None, 0
        self.invalidate()

    # -- data --------------------------------------------------------------------------
    def items(self):
        """[(sha, item, holders)] for the current view."""
        n = self.node
        with n.lock:
            net = {sha: (e['item'], list(e['holders'])) for sha, e in n.network.items()}
            for sha, it in n.shelf.items():
                if sha not in net:
                    net[sha] = (it, ['self'])
        rows = [(sha, it, h) for sha, (it, h) in net.items()]
        v = self.view
        if v in ('theme', 'image', 'video', 'music', 'app'):
            rows = [r for r in rows if r[1]['kind'] == v]
        elif v == 'shelf':
            rows = [r for r in rows if 'self' in r[2]]
        elif v == 'endangered':
            rows = [r for r in rows if len(r[2]) == 1 and r[2][0] != 'self']
        q = self.search['text'].strip().lower()
        if q:
            rows = [r for r in rows if q in r[1]['name'].lower() or q in r[1].get('author', '').lower()
                    or q in r[1].get('description', '').lower()]
        rows.sort(key=lambda r: (r[1].get('published') or ''), reverse=True)
        return rows

    def preview_for(self, sha, it, w, h):
        key = (sha, w, h)
        if key in self.thumbs:
            return self.thumbs[key]
        im = None
        blob = cosplace.Node.blob(sha)
        try:
            if os.path.isfile(blob):
                if it['kind'] == 'theme':
                    with open(blob, 'rb') as f:
                        meta, files = themes.read_ctheme(f.read())
                    if 'preview.png' in files:
                        import io
                        im = Image.open(io.BytesIO(files['preview.png'])).convert('RGBA')
                    else:
                        im = themes.render_preview(meta['look'], meta['look'].get('wall') if isinstance(meta['look'].get('wall'), dict) else None, w, h)
                elif it['kind'] == 'image':
                    im = Image.open(blob)
                    im.thumbnail((w * 2, h * 2))
                    im = im.convert('RGBA')
        except Exception:
            im = None
        if im is not None:
            im = im.copy()
            im.thumbnail((w, h), Image.LANCZOS)
        if len(self.thumbs) > 300:
            self.thumbs.clear()
        self.thumbs[key] = im
        return im

    # -- drawing -------------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        g = self.geometry()
        joined = self.node.running
        self.draw_toolbar(img, ui, w, [('refresh', '⟳', joined and not self.node.crawling)],
                          'The COS Place  ·  ' + (self.node.status if joined else 'Not joined'),
                          self.search, 'Search The COS Place')
        self.draw_tree(img, ui, g['tree'])
        ui.rect(g['space'], fill=(255, 255, 255))
        if self.view == 'computers':
            self.draw_computers(img, ui, g['space'])
        elif self.view == 'mythemes':
            self.draw_mythemes(img, ui, g['space'])
        elif not joined and self.view != 'shelf':
            self.draw_join(img, ui, g['space'])
        else:
            self.draw_items(img, ui, g['space'])
        self.draw_side(img, ui, g['side'])

    def draw_tree(self, img, ui, box):
        x0, y0, x1, y1 = box
        self.tree_bg(img, ui, box, 'The COS Place')
        y = y0 + 28
        fnt = gfx.font(12)
        counts = {}
        if self.node.running:
            for sha, it, hold in self.items_unfiltered():
                counts[it['kind']] = counts.get(it['kind'], 0) + 1
        for i, (key, label, ic) in enumerate(VIEWS):
            if key in ('shelf', 'computers'):
                y += 8
            r = (x0 + 2, y, x1 - 4, y + 24)
            ui.listrow(r, ('view', key), selected=self.view == key, data=key)
            ui.icon(ic, (x0 + 10, y + 4), 16)
            ui.text((x0 + 32, y + 12), gfx.ellipsize(fnt, label, x1 - x0 - 70), 12, anchor='lm')
            n = counts.get(key) if key in counts else (len(self.node.endangered()) if key == 'endangered' and self.node.running else None)
            if n:
                ui.text((x1 - 12, y + 12), str(n), 10, color=(200, 90, 0) if key == 'endangered' else theme.MUTED, anchor='rm')
            y += 26
        y = y1 - 120
        joined = self.node.running
        ui.text((x0 + 12, y), 'You are %s' % ('sharing on port %d' % self.node.port if joined else 'not joined'), 11,
                color=(0, 120, 60) if joined else theme.MUTED)
        ui.button((x0 + 12, y + 20, x1 - 12, y + 46), 'Leave The COS Place' if joined else 'Join The COS Place',
                  'leave' if joined else 'join', default=not joined)
        ui.button((x0 + 12, y + 52, x1 - 12, y + 78), 'Share a file...', 'publish', enabled=joined)

    def items_unfiltered(self):
        v, q = self.view, self.search
        self.view, self.search = 'all', {'text': '', 'cur': 0}
        try:
            return self.items()
        finally:
            self.view, self.search = v, q

    def draw_join(self, img, ui, box):
        x0, y0, x1, y1 = box
        cx = (x0 + x1) // 2
        ui.icon('cosplace', (cx - 40, y0 + 50), 80)
        ui.text((cx, y0 + 150), 'The COS Place', 22, color=(0, 51, 153), anchor='mm', kind='light')
        ui.wrap((x0 + 60, y0 + 180), 'A web of COS computers with no server, like the early internet. Every computer '
                'that joins shares its shelf (themes, pictures, videos, music and apps), and keeps a copy of whatever it '
                'gets, so nothing turns into lost media while anyone still has it. COS Neptune and COS Aurora share the '
                'same web.', x1 - x0 - 120, 12)
        ui.wrap((x0 + 60, y0 + 270), 'Joining makes everything on your shelf public to your network. There are no '
                'accounts. Nothing you get is ever run by Neptune: apps only land in Downloads.', x1 - x0 - 120, 11,
                color=theme.MUTED)
        ui.button((cx - 100, y0 + 330, cx + 100, y0 + 362), 'Join The COS Place', 'join', default=True)

    def draw_items(self, img, ui, box):
        x0, y0, x1, y1 = box
        rows = self.items()
        ui.text((x0 + 20, y0 + 18), VIEW[self.view][1], 16, color=(0, 51, 153), kind='light')
        if self.node.crawling:
            ui.text((x1 - 24, y0 + 18), 'looking around...', 11, color=theme.MUTED, anchor='rm')
        if not rows:
            msg = {'shelf': 'Your shelf is empty. Share a file, or get something.',
                   'endangered': 'Nothing is endangered right now: everything has more than one home.'}.get(
                self.view, 'Nothing here yet. Other computers show up a minute or two after they join; Refresh '
                           'looks again, and Computers lets you add one by hand.')
            ui.wrap((x0 + 20, y0 + 50), msg, x1 - x0 - 40, 12, color=theme.MUTED)
            return
        cw, ch = 214, 190
        cols = max(1, (x1 - x0 - 30) // cw)
        vis_rows = max(1, (y1 - y0 - 44) // ch)
        total_rows = -(-len(rows) // cols)
        self.page = vis_rows
        self.scroll = gfx.clamp(self.scroll, 0, max(0, total_rows - vis_rows))
        for i in range(self.scroll * cols, min(len(rows), (self.scroll + vis_rows + 1) * cols)):
            sha, it, hold = rows[i]
            r, c = divmod(i, cols)
            x = x0 + 14 + c * cw
            y = y0 + 40 + (r - self.scroll) * ch
            if y > y1:
                break
            box2 = (x, y, x + cw - 12, y + ch - 10)
            ui.listrow(box2, ('item', sha), selected=self.sel == sha, data=sha, kind='item')
            pw, ph = cw - 28, 112
            prev = self.preview_for(sha, it, pw, ph)
            px, py = x + 8, y + 8
            if prev is not None:
                img.alpha_composite(prev, (px + (pw - prev.size[0]) // 2, py + (ph - prev.size[1]) // 2))
            else:
                ui.rect((px, py, px + pw, py + ph), fill=(238, 243, 249), outline=(215, 222, 232))
                ui.icon(KIND_ICON[it['kind']], (px + pw // 2 - 24, py + ph // 2 - 24), 48)
            ui.text((x + 10, y + 132), gfx.ellipsize(gfx.font(12, True), it['name'], cw - 32), 12, bold=True)
            sub = '%s · %s' % (cosplace.KINDS[it['kind']]['label'], it.get('author') or 'unknown')
            ui.text((x + 10, y + 150), gfx.ellipsize(gfx.font(11), sub, cw - 32), 11, color=theme.MUTED)
            n = len(hold)
            tag = 'on your shelf' if 'self' in hold else ('ENDANGERED · 1 copy' if n == 1 else '%d copies' % n)
            col = (0, 120, 60) if 'self' in hold else ((200, 90, 0) if n == 1 else theme.MUTED)
            if sha in self.busy:
                got, tot = self.busy[sha]
                tag = 'getting... %d%%' % (100 * got // max(1, tot))
                col = (40, 110, 200)
            ui.text((x + 10, y + 166), tag, 10, bold=n == 1, color=col)
        ui.scrollbar((x1 - 16, y0 + 40, x1, y1), 'sb', self.scroll, vis_rows, total_rows)

    def draw_computers(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.text((x0 + 20, y0 + 18), 'Computers', 16, color=(0, 51, 153), kind='light')
        ui.text((x0 + 20, y0 + 50), 'Add a computer by name or address:', 12)
        ui.textbox((x0 + 20, y0 + 70, x0 + 320, y0 + 96), 'addr', self.addr, placeholder='brainpad or 192.168.1.62:8643')
        ui.button((x0 + 328, y0 + 69, x0 + 420, y0 + 97), 'Add', 'addpeer', enabled=bool(self.addr['text'].strip()))
        with self.node.lock:
            peers = sorted(self.node.peers.items(), key=lambda kv: (not kv[1].get('ok'), -kv[1].get('seen', 0)))
        shown, ids = [], set()
        for addr, p in peers:             # one row per computer, even when it's known by several addresses
            if p.get('node') and p['node'] in ids:
                continue
            ids.add(p.get('node'))
            shown.append((addr, p))
        peers = shown
        y = y0 + 116
        cols = [('Computer', 0.26), ('Address', 0.22), ('Running', 0.30), ('Items', 0.08), ('Last seen', 0.14)]
        width = x1 - x0 - 40
        hx = x0 + 20
        spans = []
        for name, frac in cols:
            cw = int(width * frac)
            ui.text((hx + 6, y + 10), name, 12, color=(76, 96, 122), anchor='lm')
            spans.append((hx, hx + cw))
            hx += cw
        ui.hline(x0 + 20, x1 - 20, y + 22, (213, 213, 213))
        y += 26
        fnt = gfx.font(12)
        if not peers:
            ui.wrap((x0 + 26, y + 10), 'No computers yet. COS computers on your network find each other within a '
                    'minute of joining; you can also add one above.', x1 - x0 - 60, 12, color=theme.MUTED)
        now = time.time()
        for addr, p in peers[:40]:
            r = (x0 + 20, y, x1 - 20, y + 24)
            ui.listrow(r, ('peer', addr), selected=self.sel == addr, data=addr)
            seen = p.get('seen', 0)
            ago = 'now' if p.get('ok') else ('never' if not seen else ('%d min ago' % ((now - seen) // 60) if now - seen < 3600 * 24
                                                                       else time.strftime('%m/%d', time.localtime(seen))))
            vals = [p.get('name') or '?', addr, p.get('software') or '-', str(p.get('items', 0)), ago]
            for k, (a, b) in enumerate(spans):
                ui.icon('computer', (a + 4, y + 4), 16) if k == 0 else None
                tx = a + (26 if k == 0 else 6)
                ui.text((tx, y + 12), gfx.ellipsize(fnt, vals[k], b - tx - 6), 12,
                        color=(0, 120, 60) if (k == 4 and p.get('ok')) else theme.CLIENT_TEXT, anchor='lm')
            y += 26
            if y > y1 - 30:
                break

    def draw_mythemes(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.text((x0 + 20, y0 + 18), 'Themes on this computer', 16, color=(0, 51, 153), kind='light')
        ui.text((x0 + 20, y0 + 52), 'Make a theme from your look:', 12)
        ui.textbox((x0 + 210, y0 + 42, x0 + 450, y0 + 66), 'newname', self.newname, placeholder='Theme name')
        ui.button((x0 + 458, y0 + 41, x0 + 560, y0 + 67), 'Make theme', 'maketheme', enabled=bool(self.newname['text'].strip()))
        ui.button((x1 - 130, y0 + 6, x1 - 20, y0 + 30), 'Install file...', 'installfile')
        ts = themes.installed()
        cw, ch = 214, 186
        cols = max(1, (x1 - x0 - 30) // cw)
        top = y0 + 82
        vis_rows = max(1, (y1 - top) // ch)
        total_rows = -(-len(ts) // cols)
        self.scroll = gfx.clamp(self.scroll, 0, max(0, total_rows - vis_rows))
        cur = self.shell.settings.get('theme')
        for i, t in enumerate(ts):
            r, c = divmod(i, cols)
            r -= self.scroll
            x, y = x0 + 14 + c * cw, top + r * ch
            if r < 0:
                continue
            if y + ch > y1 + 4:
                break
            box2 = (x, y, x + cw - 12, y + ch - 10)
            ui.listrow(box2, ('theme', t.id), selected=self.sel == t.id, data=t.id, kind='item')
            key = ('theme:' + t.id, 186, 105)
            if key not in self.thumbs:
                try:
                    self.thumbs[key] = t.preview(186, 105)
                except Exception:
                    self.thumbs[key] = None
            pv = self.thumbs[key]
            if pv is not None:
                img.alpha_composite(pv, (x + 8, y + 8))
            ui.text((x + 10, y + 124), gfx.ellipsize(gfx.font(12, True), t.name, cw - 32), 12, bold=True)
            ui.text((x + 10, y + 142), gfx.ellipsize(gfx.font(11), ('built in' if t.builtin else t.author), cw - 32), 11,
                    color=theme.MUTED)
            if t.id == cur:
                ui.text((x + 10, y + 158), 'in use', 10, bold=True, color=(0, 120, 60))
        if total_rows > vis_rows:
            ui.scrollbar((x1 - 16, top, x1, y1), 'sb', self.scroll, vis_rows, total_rows)

    def draw_side(self, img, ui, box):
        x0, y0, x1, y1 = box
        self.side_bg(img, ui, box)
        split = y0 + int((y1 - y0) * 0.52)
        self.pane(img, ui, box, 'Overview')
        inner = (x0 + 8, y0 + 26, x1 - 8, split - 8)
        ui.rect(inner, fill=(255, 255, 255), outline=(215, 222, 232))
        meta_rows = []
        buttons = []
        if self.view == 'mythemes' and self.sel:
            t = themes.find(self.sel)
            if t:
                pv = t.preview(inner[2] - inner[0] - 16, int((inner[2] - inner[0] - 16) * 9 / 16))
                img.alpha_composite(pv, (inner[0] + 8, inner[1] + 8))
                ui.wrap((inner[0] + 8, inner[1] + 16 + pv.size[1]), t.description or 'No description.', inner[2] - inner[0] - 16, 11)
                meta_rows = [('name', t.name), ('author', t.author), ('version', t.version),
                             ('id', t.id), ('glass', t.look.get('glass_color', '-'))]
                buttons = [('Apply', 'apply')]
                if not t.builtin:
                    buttons += [('Share', 'sharetheme'), ('Export', 'export'), ('Uninstall', 'uninstall')]
        elif self.view == 'computers' and self.sel:
            p = self.node.peers.get(self.sel)
            if p:
                ui.icon('computer', (inner[0] + 8, inner[1] + 8), 48)
                ui.wrap((inner[0] + 64, inner[1] + 14), p.get('name') or self.sel, inner[2] - inner[0] - 72, 13, bold=True)
                ui.wrap((inner[0] + 8, inner[1] + 70), p.get('software') or 'Not reached yet.', inner[2] - inner[0] - 16, 11)
                meta_rows = [('address', self.sel), ('node', p.get('node') or '-'), ('items', str(p.get('items', 0)))]
        else:
            ent = self.node.network.get(self.sel) if self.sel else None
            it = ent['item'] if ent else self.node.shelf.get(self.sel) if self.sel else None
            hold = list(ent['holders']) if ent else (['self'] if it else [])
            if it:
                pv = self.preview_for(self.sel, it, inner[2] - inner[0] - 16, int((inner[2] - inner[0] - 16) * 9 / 16))
                yy = inner[1] + 8
                if pv is not None:
                    img.alpha_composite(pv, ((inner[0] + inner[2] - pv.size[0]) // 2, yy))
                    yy += pv.size[1] + 8
                else:
                    ui.icon(KIND_ICON[it['kind']], ((inner[0] + inner[2]) // 2 - 32, yy + 4), 64)
                    yy += 76
                ui.wrap((inner[0] + 8, yy), it['name'], inner[2] - inner[0] - 16, 13, bold=True)
                ui.wrap((inner[0] + 8, yy + 22), it.get('description') or 'No description.', inner[2] - inner[0] - 16, 11,
                        color=theme.MUTED)
                meta_rows = [('kind', cosplace.KINDS[it['kind']]['label']), ('size', human(it['size'])),
                             ('author', it.get('author') or '-'), ('from', it.get('origin') or '-'),
                             ('published', it.get('published') or '-'),
                             ('copies', '%d computer%s%s' % (len(hold), '' if len(hold) == 1 else 's',
                                                            ' (you)' if 'self' in hold else '')),
                             ('sha256', self.sel[:16] + '...')]
                if 'self' in hold:
                    if it['kind'] == 'theme':
                        buttons.append(('Install + apply', 'installapply'))
                    if it['kind'] in ('image', 'video', 'music'):
                        buttons.append(('Open', 'open'))
                    buttons += [('Save to Downloads', 'save'), ('Remove from shelf', 'remove')]
                else:
                    buttons.append(('Get it', 'get'))
            else:
                ui.wrap((inner[0] + 8, inner[1] + 10), 'Pick something to see it here.', inner[2] - inner[0] - 16, 11,
                        color=theme.MUTED)
        self.pane(img, ui, (x0, split, x1, y1), 'Metadata')
        y = split + 28
        for k, v in meta_rows:
            ui.text((x0 + 12, y), k + ':', 11, color=theme.MUTED)
            ui.text((x0 + 82, y), gfx.ellipsize(gfx.font(11), str(v), x1 - x0 - 92), 11)
            y += 18
        by = y1 - 34
        for label, cid in reversed(buttons):
            ui.button((x0 + 10, by, x1 - 10, by + 26), label, cid, default=(label in ('Get it', 'Apply', 'Install + apply')))
            by -= 32

    # -- actions --------------------------------------------------------------------------
    def say(self, text, kind='info'):
        self.shell.notify('The COS Place', text, kind, 5)

    def join(self):
        def go(ans):
            if ans != 'Join':
                return
            self.shell.settings['cosplace_join'] = True
            self.shell.settings.save()
            if self.node.start():
                self.say('Joined. Sharing your shelf on port %d.' % self.node.port)
            else:
                message(self.shell, 'The COS Place', self.node.status, 'error', parent=self.win)
            self.invalidate()
        message(self.shell, 'Join The COS Place',
                'Joining shares everything on your shelf with anyone on your network (and anyone who can reach port '
                '8643 on this computer). There are no accounts or passwords.\n\nNothing you get is ever run: apps only '
                'go to Downloads.', 'question', ('Join', 'Not now'), go, parent=self.win)

    def leave(self):
        self.node.stop()
        self.shell.settings['cosplace_join'] = False
        self.shell.settings.save()
        self.say('Left The COS Place. Your shelf is kept for next time.')

    def get(self, sha):
        ent = self.node.network.get(sha)
        if not ent or sha in self.busy:
            return
        tot = ent['item']['size']
        self.busy[sha] = (0, tot)

        def work():
            try:
                it = self.node.fetch(sha, progress=lambda got: self.busy.__setitem__(sha, (got, tot)))
                self.notes.append(('Got "%s". It is on your shelf now, so you share it too.' % it['name'], 'info'))
            except Exception as e:
                self.notes.append(('Could not get it: %s' % e, 'warning'))
            self.busy.pop(sha, None)
            self.thumbs.clear()
        threading.Thread(target=work, daemon=True).start()

    def install_theme_file(self, path):
        try:
            t = themes.install_file(path)
        except (OSError, themes.ThemeError) as e:
            message(self.shell, 'Install theme', 'That is not a theme Neptune can use:\n%s' % e, 'error', parent=self.win)
            return None
        self.sel = t.id
        self.thumbs.clear()
        self.say('Installed the theme "%s".' % t.name)
        return t

    def on_command(self, cid, data):
        sh = self.shell
        if isinstance(cid, tuple):
            k = cid[0]
            if k == 'view':
                self.view, self.sel, self.scroll = cid[1], None, 0
            elif k in ('item', 'peer', 'theme'):
                self.sel = cid[1]
            self.invalidate()
            return
        sel = self.sel
        if cid == 'join':
            self.join()
        elif cid == 'leave':
            self.leave()
        elif cid == 'refresh' and self.node.running:
            self.node.crawl_async()
        elif cid == 'get' and sel:
            self.get(sel)
        elif cid == 'save' and sel in self.node.shelf:
            p = self.node.save_to_downloads(sel)
            it = self.node.shelf[sel]
            note = ' Installing it is your choice: COS never runs things from The COS Place.' if it['kind'] == 'app' else ''
            self.say('Saved %s in Downloads/COS Place.%s' % (os.path.basename(p), note))
        elif cid == 'open' and sel in self.node.shelf:
            from .archiver import open_path
            open_path(sh, self.node.save_to_downloads(sel))
        elif cid == 'remove' and sel in self.node.shelf:
            it = self.node.shelf[sel]
            ent = self.node.network.get(sel)
            alone = not ent or all(h == 'self' for h in ent['holders'])

            def go(ans):
                if ans == 'Remove':
                    self.node.remove(sel)
                    self.thumbs.clear()
                    self.invalidate()
            message(sh, 'Remove from shelf', 'Stop sharing "%s" and delete your copy?%s' % (
                it['name'], '\n\nYou are the only computer that has it: it will be gone from The COS Place.' if alone else ''),
                'warning' if alone else 'question', ('Remove', 'Cancel'), go, parent=self.win)
        elif cid == 'installapply' and sel in self.node.shelf:
            t = self.install_theme_file(cosplace.Node.blob(sel))
            if t:
                themes.apply(t, sh)
        elif cid == 'apply' and sel:
            t = themes.find(sel)
            if t:
                themes.apply(t, sh)
                self.say('Now using the theme "%s".' % t.name)
        elif cid == 'maketheme' or (cid == 'newname' and data == 'enter'):
            name = self.newname['text'].strip()
            if name:
                try:
                    t, path = themes.save_new(sh, name, author=sh.settings.get('user_name') or os.environ.get('USER', ''))
                    self.sel = t.id
                    self.newname = {'text': '', 'cur': 0}
                    self.thumbs.clear()
                    self.say('Made the theme "%s" (saved in Documents/Themes).' % name)
                except Exception as e:
                    message(sh, 'Make theme', 'Could not make the theme: %s' % e, 'error', parent=self.win)
        elif cid == 'installfile':
            start = os.path.join(theme.HOME, 'Downloads')
            sh.open_window(FileDialog(sh, 'open', start=start if os.path.isdir(start) else theme.HOME,
                                      exts=(themes.EXT,), callback=self.install_theme_file), parent=self.win)
        elif cid == 'sharetheme' and sel:
            t = themes.find(sel)
            if t and t.ctheme_path():
                if not self.node.running:
                    message(sh, 'Share theme', 'Join The COS Place first.', 'info', parent=self.win)
                else:
                    it = self.node.publish_file(t.ctheme_path(), name=t.name, author=t.author, description=t.description,
                                                kind='theme')
                    self.say('Sharing "%s" on The COS Place.' % it['name'])
        elif cid == 'export' and sel:
            t = themes.find(sel)
            try:
                self.say('Exported to %s' % themes.export(t).replace(theme.ROOT, '~/cos/neptune'))
            except (OSError, themes.ThemeError) as e:
                message(sh, 'Export', str(e), 'info', parent=self.win)
        elif cid == 'uninstall' and sel:
            t = themes.find(sel)
            if t and themes.uninstall(t.id):
                self.sel = None
                self.thumbs.clear()
        elif cid == 'publish':
            def chosen(p):
                try:
                    it = self.node.publish_file(p, author=sh.settings.get('user_name') or os.environ.get('USER', ''))
                    self.sel = it['sha256']
                    self.say('Sharing "%s" on The COS Place.' % it['name'])
                except Exception as e:
                    message(sh, 'Share a file', 'That file cannot go on The COS Place: %s' % e, 'warning', parent=self.win)
                self.invalidate()
            exts = tuple(e for k in cosplace.KINDS.values() for e in k['exts'])
            sh.open_window(FileDialog(sh, 'open', start=theme.HOME, exts=exts, callback=chosen), parent=self.win)
        elif cid in ('addpeer',) or (cid == 'addr' and data == 'enter'):
            a = self.node.learn(self.addr['text'], seen=False)
            if a:
                peers = list(sh.settings.get('cosplace_peers') or [])
                if a not in peers:
                    peers.append(a)
                    sh.settings['cosplace_peers'] = peers
                    sh.settings.save()
                self.addr = {'text': '', 'cur': 0}
                if self.node.running:
                    self.node.crawl_async()
                self.say('Added %s.' % a)
            else:
                message(sh, 'Add a computer', 'Type a computer name or address, like brainpad or 192.168.1.62:8643.',
                        'info', parent=self.win)
        elif cid == 'sb':
            self.scroll = data
        self.invalidate()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'item':
            self.sel = cid[1]
            self.on_command('get' if cid[1] not in self.node.shelf else ('installapply' if self.node.shelf[cid[1]]['kind'] == 'theme' else 'open'), None)
        elif isinstance(cid, tuple) and cid[0] == 'theme':
            self.sel = cid[1]
            self.on_command('apply', None)
        else:
            self.on_command(cid, data)

    def on_text(self, cid, state):
        if cid == 'search':
            self.scroll = 0
        self.invalidate()

    def on_wheel(self, dy, x, y):
        self.scroll = max(0, self.scroll - dy)
        self.invalidate()

    @property
    def live(self):
        return bool(self.busy) or self.node.crawling

    def on_tick(self, now):
        changed = False
        while self.notes:
            text, kind = self.notes.pop(0)
            self.say(text, kind)
            changed = True
        if self.busy or self.node.crawling:
            changed = True
        if int(now) % 3 == 0 and int(now) != getattr(self, '_last_sec', 0):
            self._last_sec = int(now)
            changed = True                       # catalog / peers change in the background
        return changed
