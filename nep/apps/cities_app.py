"""COS Cities, as a CEAP app: build your free home page, see who lives in each
neighborhood, and look after your guestbook. The pages themselves are served
by The COS Place (nep/cities.py does the work)."""
import os
import time

from .. import cities, cosplace, gfx, theme
from .ceapframe import CeapFrame
from .dialogs import message

FIELDS = [('title', 'Page title', 'My Totally Awesome Home Page'), ('tagline', 'Tagline', 'Welcome to my corner of the web!'),
          ('owner', 'Your name', 'Webmaster'), ('about', 'About me', 'A few sentences about you'),
          ('marquee', 'Scrolling text', 'Thanks for stopping by! Sign my guestbook!')]
LINK_HINT = 'Name  http://address/'


def _st(text):
    return {'text': text or '', 'cur': len(text or '')}


class COSCities(CeapFrame):
    title = 'COS Cities'
    icon = 'cities'
    size = (1160, 720)
    min_size = (900, 600)
    single_instance = True
    tree_w = 200
    side_w = 280

    def __init__(self, shell, view=None, **kw):
        super().__init__(shell, **kw)
        self.node = cosplace.get(shell.settings)
        self.view = view or 'mine'
        self.search = _st('')
        self.sel = None
        self.scroll = 0
        self.load()

    def load(self):
        s = self.s = cities.site()
        if not s.get('owner'):
            s['owner'] = self.shell.settings.get('user_name') or os.environ.get('USER', '')
        self.f = {k: _st(s.get(k, '')) for k, _, _ in FIELDS}
        links = (list(s.get('links') or []) + ['', '', '', ''])[:4]
        for i, l in enumerate(links):
            self.f['link%d' % i] = _st(l)

    def collect(self):
        s = self.s
        for k, _, _ in FIELDS:
            s[k] = self.f[k]['text'].strip()
        s['links'] = [self.f['link%d' % i]['text'].strip() for i in range(4)]
        return s

    def live_url(self):
        return 'http://127.0.0.1:%d%s' % (self.node.port, cities.address(self.s))

    # -- drawing ----------------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        g = self.geometry()
        online = self.node.running and self.s.get('published') and cities.has_page()
        addr = ('COS Cities  ·  %s/%d  ·  %s' % (self.s['neighborhood'], self.s['number'],
                                                  'online at :%d' % self.node.port if online else 'not online'))
        self.draw_toolbar(img, ui, w, [('refresh', '⟳', self.node.running)], addr, self.search, 'Search COS Cities')
        self.draw_tree(img, ui, g['tree'])
        ui.rect(g['space'], fill=(255, 255, 255))
        if self.view == 'mine':
            self.draw_builder(img, ui, g['space'])
        elif self.view == 'guestbook':
            self.draw_guestbook(img, ui, g['space'])
        else:
            self.draw_directory(img, ui, g['space'])
        self.draw_side(img, ui, g['side'])

    def draw_tree(self, img, ui, box):
        x0, y0, x1, y1 = box
        self.tree_bg(img, ui, box, 'COS Cities')
        y = y0 + 28
        fnt = gfx.font(12)
        sites = self.sites()
        rows = [('mine', 'My home page', 'cities'), ('guestbook', 'My guestbook', 'notepad'), None,
                ('all', 'All neighborhoods', 'cosplace')] + [(nb, nb, 'folder') for nb, _ in cities.NEIGHBORHOODS]
        for r in rows:
            if r is None:
                y += 8
                continue
            key, label, ic = r
            ui.listrow((x0 + 2, y, x1 - 4, y + 24), ('view', key), selected=self.view == key, data=key)
            ui.icon(ic, (x0 + 10, y + 4), 16)
            ui.text((x0 + 32, y + 12), gfx.ellipsize(fnt, label, x1 - x0 - 70), 12, anchor='lm')
            n = len(cities.guestbook()) if key == 'guestbook' else (
                sum(1 for x in sites if x['neighborhood'] == key) if key in cities.NEIGHBORHOOD else 0)
            if n:
                ui.text((x1 - 12, y + 12), str(n), 10, color=theme.MUTED, anchor='rm')
            y += 26
        if not self.node.running:
            ui.wrap((x0 + 12, y1 - 96), 'Your page goes online through The COS Place. Join it to publish and to see '
                    'other people\'s pages.', x1 - x0 - 24, 11, color=theme.MUTED)
            ui.button((x0 + 12, y1 - 40, x1 - 12, y1 - 14), 'Open The COS Place', 'cosplace')

    def sites(self):
        out = cities.directory(self.node)
        me = cities.own_entry(self.node)
        if me:
            me = dict(me, url=self.live_url())
            out = [me] + out
        return out

    def draw_builder(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.text((x0 + 20, y0 + 16), 'My home page', 16, color=(0, 51, 153), kind='light')
        ui.text((x1 - 20, y0 + 22), 'address: %s' % cities.address(self.s), 11, color=theme.MUTED, anchor='rm')
        lx, fx, fw = x0 + 20, x0 + 130, x1 - x0 - 150
        y = y0 + 46
        for k, label, hint in FIELDS:
            ui.text((lx, y + 12), label + ':', 12, anchor='lm')
            ui.textbox((fx, y, fx + fw, y + 24), 'f_' + k, self.f[k], placeholder=hint)
            y += 30
        ui.text((lx, y + 12), 'Cool links:', 12, anchor='lm')
        half = (fw - 8) // 2
        for i in range(4):
            cx = fx + (i % 2) * (half + 8)
            cy = y + (i // 2) * 30
            ui.textbox((cx, cy, cx + half, cy + 24), 'f_link%d' % i, self.f['link%d' % i], placeholder=LINK_HINT)
        y += 66
        ui.text((lx, y + 8), 'Neighborhood:', 12, anchor='lm')
        colw = max(100, fw // 4)
        for i, (nb, desc) in enumerate(cities.NEIGHBORHOODS):
            ui.radio((fx + (i % 4) * colw, y + (i // 4) * 22), nb, 'nb', self.s['neighborhood'] == nb, data=nb)
        y += 50
        ui.text((lx, y + 8), 'Look:', 12, anchor='lm')
        tx = fx
        for k, t in cities.TEMPLATES.items():
            ui.radio((tx, y), t['label'], 'tpl', self.s.get('template') == k, data=k)
            tx += gfx.text_width(gfx.font(12), t['label']) + 40
        y += 30
        ui.text((lx, y + 8), 'Extras:', 12, anchor='lm')
        cx = fx
        for k, label in (('counter', 'Hit counter'), ('guestbook', 'Guestbook'), ('webring', 'Webring'),
                         ('construction', 'Under construction')):
            ui.checkbox((cx, y), label, 'x_' + k, bool(self.s.get(k)))
            cx += gfx.text_width(gfx.font(12), label) + 40
        y += 34
        hand = cities.hand_edited(self.s)
        if hand:
            ui.wrap((fx, y), 'You have edited index.html by hand. Saving here would replace it with the builder\'s '
                    'version (you will be asked first).', fw, 11, color=(170, 90, 0))
            y += 34
        bx = fx
        for label, cid, default in (('Save page', 'save', True), ('Preview', 'preview', False),
                                    ('Edit HTML in Notepad', 'edit', False), ('Open Sites folder', 'folder', False)):
            bw = gfx.text_width(gfx.font(12), label) + 30
            ui.button((bx, y, bx + bw, y + 28), label, cid, default=default)
            bx += bw + 8
        ui.wrap((lx, y1 - 48), 'Tip: index.html is yours to edit. Put <!--#counter-->, <!--#guestbook-->, <!--#webring--> '
                'or <!--#updated--> anywhere in it. Pictures and sounds in the Sites folder are served too; scripts '
                'are not.', x1 - x0 - 40, 11, color=theme.MUTED)

    def filtered(self, sites):
        q = self.search['text'].strip().lower()
        if self.view in cities.NEIGHBORHOOD:
            sites = [x for x in sites if x['neighborhood'] == self.view]
        if q:
            sites = [x for x in sites if q in (x['title'] + ' ' + x['owner'] + ' ' + x['tagline']).lower()]
        return sorted(sites, key=lambda x: (x['neighborhood'], x['number']))

    def draw_directory(self, img, ui, box):
        x0, y0, x1, y1 = box
        nb = self.view if self.view in cities.NEIGHBORHOOD else None
        ui.text((x0 + 20, y0 + 16), nb or 'All neighborhoods', 16, color=(0, 51, 153), kind='light')
        ui.text((x0 + 20, y0 + 42), cities.NEIGHBORHOOD.get(nb, 'Every page this computer knows about on The COS Place.'),
                11, color=theme.MUTED)
        sites = self.filtered(self.sites())
        top = y0 + 64
        if not sites:
            ui.wrap((x0 + 20, top + 6), 'No pages here yet.' + ('' if self.node.running else
                    ' Join The COS Place to see pages on other computers.') + ' Yours could be the first: '
                    'pick this neighborhood under My home page.', x1 - x0 - 40, 12, color=theme.MUTED)
            return
        cols = [('Page', 0.36), ('Address', 0.18), ('By', 0.16), ('Computer', 0.18), ('Visits', 0.12)]
        width = x1 - x0 - 40
        hx = x0 + 20
        spans = []
        for name, frac in cols:
            cw = int(width * frac)
            ui.text((hx + 6, top + 10), name, 12, color=(76, 96, 122), anchor='lm')
            spans.append((hx, hx + cw))
            hx += cw
        ui.hline(x0 + 20, x1 - 20, top + 22, (213, 213, 213))
        y = top + 26
        rh = 26
        page = max(1, (y1 - y) // rh)
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(sites) - page))
        fnt = gfx.font(12)
        for x in sites[self.scroll:self.scroll + page]:
            ui.listrow((x0 + 20, y, x1 - 24, y + rh - 2), ('site', x['url']), selected=self.sel == x['url'],
                       data=x['url'], kind='item')
            vals = [x['title'], '%s/%d' % (x['neighborhood'], x['number']), x['owner'] or '-', x['computer'],
                    str(x['visits'])]
            for k, (a, b) in enumerate(spans):
                if k == 0:
                    ui.icon('cities', (a + 4, y + 4), 16)
                tx = a + (26 if k == 0 else 6)
                ui.text((tx, y + 12), gfx.ellipsize(fnt, vals[k], b - tx - 6), 12, bold=k == 0, anchor='lm')
            y += rh
        ui.scrollbar((x1 - 18, top + 26, x1 - 2, y1), 'sb', self.scroll, page, len(sites))

    def draw_guestbook(self, img, ui, box):
        x0, y0, x1, y1 = box
        gb = cities.guestbook()
        ui.text((x0 + 20, y0 + 16), 'My guestbook', 16, color=(0, 51, 153), kind='light')
        ui.text((x1 - 20, y0 + 22), '%d entr%s' % (len(gb), 'y' if len(gb) == 1 else 'ies'), 11, color=theme.MUTED,
                anchor='rm')
        if not gb:
            ui.wrap((x0 + 20, y0 + 54), 'Nobody has signed your guestbook yet. Visitors sign it from your page.',
                    x1 - x0 - 40, 12, color=theme.MUTED)
            return
        y = y0 + 50
        rh = 58
        page = max(1, (y1 - y) // rh)
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(gb) - page))
        for i in range(self.scroll, min(len(gb), self.scroll + page)):
            g = gb[i]
            ui.listrow((x0 + 20, y, x1 - 24, y + rh - 4), ('entry', i), selected=self.sel == i, data=i, kind='item')
            ui.text((x0 + 30, y + 8), g['name'], 12, bold=True)
            ui.text((x1 - 34, y + 8), time.strftime('%m/%d/%Y %I:%M %p', time.localtime(g.get('when', 0))), 10,
                    color=theme.MUTED, anchor='ra')
            ui.text((x0 + 30, y + 28), gfx.ellipsize(gfx.font(11), g['message'], x1 - x0 - 70), 11)
            y += rh
        ui.scrollbar((x1 - 18, y0 + 50, x1 - 2, y1), 'sb', self.scroll, page, len(gb))

    def draw_side(self, img, ui, box):
        x0, y0, x1, y1 = box
        self.side_bg(img, ui, box)
        split = y0 + int((y1 - y0) * 0.5)
        self.pane(img, ui, box, 'Overview')
        inner = (x0 + 8, y0 + 26, x1 - 8, split - 8)
        ui.rect(inner, fill=(255, 255, 255), outline=(215, 222, 232))
        iw = inner[2] - inner[0] - 16
        rows, buttons = [], []
        if self.view in ('mine', 'guestbook'):
            s = self.s
            ui.icon('cities', (inner[0] + 8, inner[1] + 8), 48)
            ui.wrap((inner[0] + 64, inner[1] + 12), self.f['title']['text'] or 'My home page', iw - 56, 13, bold=True)
            online = self.node.running and s.get('published') and cities.has_page()
            state = ('Online: anyone on The COS Place can visit it.' if online else
                     'Published, but offline until you join The COS Place.' if s.get('published') else
                     'Not published: only you can see it (Preview).')
            ui.wrap((inner[0] + 8, inner[1] + 70), state, iw, 11, color=(0, 120, 60) if online else theme.MUTED)
            if self.view == 'guestbook' and isinstance(self.sel, int):
                gb = cities.guestbook()
                if self.sel < len(gb):
                    g = gb[self.sel]
                    ui.wrap((inner[0] + 8, inner[1] + 110), '%s wrote:\n%s' % (g['name'], g['message']), iw, 11)
                    buttons.append(('Delete entry', 'delentry'))
            rows = [('address', cities.address(s)), ('visitors', str(cities.counter_value())),
                    ('guestbook', '%d entries' % len(cities.guestbook())), ('look', cities.TEMPLATES.get(
                        s.get('template'), {}).get('label', '-'))]
            if online:
                buttons.append(('Visit my page', 'visitmine'))
            buttons.append(('Unpublish' if s.get('published') else 'Publish my page', 'publish'))
        else:
            x = next((x for x in self.sites() if x['url'] == self.sel), None)
            if x:
                ui.icon('cities', (inner[0] + 8, inner[1] + 8), 48)
                ui.wrap((inner[0] + 64, inner[1] + 12), x['title'], iw - 56, 13, bold=True)
                ui.wrap((inner[0] + 8, inner[1] + 70), x['tagline'] or 'No tagline.', iw, 11, color=theme.MUTED)
                rows = [('address', '%s/%d' % (x['neighborhood'], x['number'])), ('by', x['owner'] or '-'),
                        ('computer', x['computer']), ('visitors', str(x['visits'])),
                        ('updated', time.strftime('%m/%d/%Y', time.localtime(x['updated'])) if x['updated'] else '-')]
                buttons.append(('Visit', 'visit'))
            else:
                ui.wrap((inner[0] + 8, inner[1] + 10), 'Pick a page to see it here. Double-click to visit it in '
                        'Webinator.', iw, 11, color=theme.MUTED)
        self.pane(img, ui, (x0, split, x1, y1), 'Metadata')
        y = split + 28
        for k, v in rows:
            ui.text((x0 + 12, y), k + ':', 11, color=theme.MUTED)
            ui.text((x0 + 84, y), gfx.ellipsize(gfx.font(11), v, x1 - x0 - 94), 11)
            y += 18
        by = y1 - 34
        for label, cid in reversed(buttons):
            ui.button((x0 + 10, by, x1 - 10, by + 26), label, cid, default=label in ('Visit', 'Publish my page'))
            by -= 32

    # -- actions ----------------------------------------------------------------------------
    def say(self, text, kind='info'):
        self.shell.notify('COS Cities', text, kind, 5)

    def save(self, then=None):
        s = self.collect()

        def write(ans='Replace'):
            if ans != 'Replace':
                cities.save_site(s)          # keep the settings, leave their index.html alone
                return
            cities.write_page(s)
            self.say('Saved your page.' + ('' if s.get('published') else ' Publish it to put it online.'))
            if then:
                then()
            self.invalidate()
        if cities.hand_edited(s):
            message(self.shell, 'Save page', 'You have changed index.html by hand. Replace it with the page builder\'s '
                    'version?', 'warning', ('Replace', 'Keep mine'), write, parent=self.win)
        else:
            write()

    def visit(self, url):
        self.shell.launch('webinator', url=url)

    def on_command(self, cid, data):
        sh = self.shell
        if isinstance(cid, tuple):
            if cid[0] == 'view':
                self.view, self.sel, self.scroll = cid[1], None, 0
            elif cid[0] in ('site', 'entry'):
                self.sel = cid[1]
            self.invalidate()
            return
        if cid == 'nb':
            self.s['neighborhood'] = data
        elif cid == 'tpl':
            self.s['template'] = data
        elif cid.startswith('x_'):
            self.s[cid[2:]] = bool(data)
        elif cid == 'save' or (cid.startswith('f_') and data == 'enter'):
            self.save()
        elif cid == 'preview':
            self.collect()
            cities.save_site(self.s)
            if not cities.has_page() or not cities.hand_edited(self.s):
                cities.write_page(self.s)
            self.visit('file://' + cities.preview())
        elif cid == 'edit':
            if not cities.has_page():
                cities.write_page(self.collect())
            sh.launch('notepad', path=cities.index_path())
        elif cid == 'folder':
            os.makedirs(cities.SITE_DIR, exist_ok=True)
            sh.launch('files', path=cities.SITE_DIR)
        elif cid == 'publish':
            s = self.collect()
            if s.get('published'):
                s['published'] = False
                cities.save_site(s)
                self.say('Your page is offline now.')
            else:
                s['published'] = True
                if not cities.has_page():
                    cities.write_page(s)
                else:
                    cities.save_site(s)
                if self.node.running:
                    self.say('Your page is online at %s' % cities.address(s))
                else:
                    message(sh, 'Publish', 'Your page is ready. It goes online when you join The COS Place.',
                            'info', ('Open The COS Place', 'Later'),
                            lambda a: a == 'Open The COS Place' and sh.launch('cosplace'), parent=self.win)
        elif cid == 'visitmine':
            self.visit(self.live_url())
        elif cid == 'visit' and self.sel:
            self.visit(self.sel)
        elif cid == 'delentry' and isinstance(self.sel, int):
            cities.delete_entry(self.sel)
            self.sel = None
        elif cid == 'cosplace':
            sh.launch('cosplace')
        elif cid == 'refresh':
            cities._dir_cache['t'] = 0
            cities.directory(self.node)
        elif cid == 'sb':
            self.scroll = data
        self.invalidate()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'site':
            self.sel = cid[1]
            self.visit(cid[1])
        else:
            self.on_command(cid, data)

    def on_text(self, cid, state):
        if cid == 'search':
            self.scroll = 0
        self.invalidate()

    def on_wheel(self, dy, x, y):
        self.scroll = max(0, self.scroll - dy)
        self.invalidate()

    def on_tick(self, now):
        if int(now) % 5 == 0 and int(now) != getattr(self, '_sec', 0):
            self._sec = int(now)
            return self.view != 'mine'
        return False
