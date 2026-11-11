"""CEAP:Web: the Internet Explorer of COS, on the CEAP engine.

Webinator's page (its whole engine: layout, images, forms, JavaScript) lives
in the Exploring Space. Around it: Back / Forward / Up / Refresh, the
address bar and search; Favorites, History and start pages in the tree;
Page.over (the page outline: headings, links, pictures, forms) and Metadata
(address, security, size, cookies, scripts) on the right; and
Code.Exe.Explore as a JavaScript console for the page (or its source when
scripts aren't running).

Webinator itself is not changed: EmbeddedWeb subclasses it and draws only
the page, and a small stand-in window keeps its popups in the right place.
"""
import json
import os
import threading
import time
import urllib.parse

from PIL import Image

from .. import gfx, theme
from ..ui import UI
from .ceapframe import CeapFrame, TOOL_H
from .webinator import Webinator, HOME_URL, SEARCH, FIND_H, SB_W, load_favorites

CODE_H = 170
START = [('Webinator start page', HOME_URL), ('Wikipedia', 'https://en.wikipedia.org/'),
         ('Hacker News', 'https://news.ycombinator.com/'), ('DuckDuckGo', 'https://html.duckduckgo.com/html/'),
         ('The first web page', 'https://info.cern.ch/hypertext/WWW/TheProject.html')]


class _PaneWindow:
    """What an embedded Webinator sees as its window: the host's window, moved
    to where the Exploring Space is, so popups and dialogs land in the right
    place and repaints reach the host."""

    def __init__(self, host):
        self.host = host

    @property
    def _w(self):
        return self.host.win

    def client_rect(self, box=None):
        cx0, cy0, cx1, cy1 = self._w.client_rect()
        sx0, sy0, sx1, sy1 = self.host.geometry()['space']
        return (cx0 + sx0, cy0 + sy0, cx0 + sx1, cy0 + sy1)

    @property
    def rect(self):
        return self._w.rect

    @property
    def client_size(self):
        return self.host.emb.size

    def __getattr__(self, name):
        return getattr(self.host.win, name)

    def __setattr__(self, name, value):
        if name == 'host':
            object.__setattr__(self, name, value)
        elif name in ('content_dirty', 'frame_dirty'):
            if self.host.win is not None:
                setattr(self.host.win, name, value)
        else:
            object.__setattr__(self, name, value)


class EmbeddedWeb(Webinator):
    def __init__(self, shell, host, url=None):
        self.host = host
        self.size = (800, 500)
        super().__init__(shell, url=url)

    @property
    def client_size(self):
        return self.size

    def viewport(self):
        w, h = self.size
        return (0, 0, w - SB_W, h - (FIND_H if self.find is not None else 0))

    def draw(self, img, ui):
        w, h = img.size
        self.draw_page(img, ui)
        if self.find is not None:
            self.draw_findbar(img, ui, w, h + 0)

    def set_title(self, t):
        self.title = t
        self.host.page_title(t)

    def invalidate(self):
        self.host.invalidate()

    def close(self):
        self.host.close()


class CeapWeb(CeapFrame):
    title = 'CEAP:Web'
    icon = 'webinator'
    size = (1180, 740)
    min_size = (820, 480)

    def __init__(self, shell, url=None, **kw):
        super().__init__(shell, **kw)
        self.addr = {'text': '', 'cur': 0}
        self.search = {'text': '', 'cur': 0}
        self.recent = []                   # (title, url), newest first
        self.code = False
        self.code_in = {'text': '', 'cur': 0}
        self.focus_page = True
        self.page_url = None
        self.tree_scroll = 0
        self.emb = None
        self.emb = EmbeddedWeb(shell, self, url or HOME_URL)
        self.emb.win = None

    @property
    def bottom_h(self):
        return CODE_H if self.code else 0

    # -- hooks from the embedded page ----------------------------------------------
    def page_title(self, t):
        t = t.replace(' - Webinator', '') or 'CEAP:Web'
        self.set_title('%s - CEAP:Web' % t if t != 'Webinator' else 'CEAP:Web')

    def sync(self):
        e = self.emb
        if e.url != self.page_url:
            self.page_url = e.url
            if self.ui.focus != 'addr':
                self.addr = {'text': e.url, 'cur': len(e.url)}
            if e.url and not e.url.startswith('about:'):
                t = (e.doc.title if e.doc is not None and e.doc.title else e.url)
                self.recent = [(t, e.url)] + [r for r in self.recent if r[1] != e.url]
                self.recent = self.recent[:40]

    # -- drawing ----------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        e = self.emb
        if e.win is None and self.win is not None:
            e.win = _PaneWindow(self)
        g = self.geometry()
        loading = e.loading is not None
        self.draw_toolbar(img, ui, w, [('back', '←', e.can_back() if hasattr(e, 'can_back') else bool(e.history)),
                                       ('fwd', '→', e.can_forward() if hasattr(e, 'can_forward') else bool(e.future)),
                                       ('up', '↑', bool(e.url) and e.url.startswith('http')),
                                       ('stop' if loading else 'refresh', '×' if loading else '⟳', True)],
                          self.addr, self.search, 'Search DuckDuckGo')
        self.draw_tree(img, ui, g['tree'])
        self.draw_space(img, ui, g['space'])
        self.draw_side(img, ui, g['side'])
        if self.code:
            self.draw_code(img, ui, g['bottom'])

    def tree_items(self):
        out = [('head', 'Favorites')]
        favs = load_favorites()
        out += [('link', t, u) for t, u in favs] or [('note', 'No favorites yet (★ or Ctrl+D)')]
        out.append(('head', 'History'))
        out += [('link', t, u) for t, u in self.recent[:15]] or [('note', 'Pages you visit show here')]
        out.append(('head', 'Start somewhere'))
        out += [('link', t, u) for t, u in START]
        return out

    def draw_tree(self, img, ui, box):
        x0, y0, x1, y1 = box
        self.tree_bg(img, ui, box, 'Explore the web')
        items = self.tree_items()
        rh = 21
        top = y0 + 26
        page = max(1, (y1 - top) // rh)
        self.tree_scroll = gfx.clamp(self.tree_scroll, 0, max(0, len(items) - page))
        fnt = gfx.font(12)
        for i, it in enumerate(items[self.tree_scroll:self.tree_scroll + page]):
            y = top + i * rh
            if it[0] == 'head':
                ui.text((x0 + 10, y + rh // 2 + 2), it[1], 11, bold=True, color=(30, 57, 91), anchor='lm')
                continue
            if it[0] == 'note':
                ui.text((x0 + 18, y + rh // 2), it[1], 10, color=theme.MUTED, anchor='lm')
                continue
            r = (x0 + 4, y, x1 - 6, y + rh - 1)
            ui.listrow(r, ('go', it[2]), selected=it[2] == self.emb.url, data=it[2])
            ui.icon('webinator', (x0 + 12, y + 2), 16)
            ui.text((x0 + 34, y + rh // 2), gfx.ellipsize(fnt, it[1] or it[2], x1 - x0 - 44), 12, anchor='lm')

    def draw_space(self, img, ui, box):
        x0, y0, x1, y1 = box
        e = self.emb
        size = (x1 - x0, y1 - y0)
        if e.size != size:
            e.size = size
            e.on_resize()
        sub = Image.new('RGBA', size, (255, 255, 255, 255))
        e.ui.begin(sub)
        try:
            e.draw(sub, e.ui)
        except Exception as ex:
            e.ui.text((20, 20), 'The page could not be drawn: %s' % ex, 12, color=(170, 30, 20))
        img.alpha_composite(sub, (x0, y0))
        if e.loading is not None:
            t = (time.time() * 0.8) % 1.0
            sx = x0 + int(t * (x1 - x0))
            ui.d.rectangle((max(x0, sx - 80), y0, min(x1, sx), y0 + 2), fill=(70, 160, 230, 255))
        ui.region(box, 'page', 'page')

    # In EarthDog mode the page lives in Firefox: there is no local DOM, so ask
    # Firefox for the outline once the page settles (on a thread: it can take
    # a moment), and cache it per address.
    ED_OUTLINE_JS = ('JSON.stringify({h:[...document.querySelectorAll("h1,h2,h3")].slice(0,30).map(e=>'
                     '[+e.tagName[1],(e.innerText||"").trim().replace(/\\s+/g," ").slice(0,120)]).filter(x=>x[1]),'
                     'l:document.querySelectorAll("a[href]").length,i:document.images.length,'
                     'f:document.forms.length,n:document.documentElement.outerHTML.length,t:document.title})')

    def ed_outline_tick(self, now):
        e = self.emb
        ed = getattr(e, 'ed', None)
        if ed is None or not getattr(ed, 'ready', False) or ed.recently_changed():
            return False
        key = e.url
        cache = self.__dict__.setdefault('_ed_outline', {})
        if key in cache or getattr(self, '_ed_busy', False):
            return False
        self._ed_busy = True

        def work():
            data = None
            try:
                raw = ed.eval(self.ED_OUTLINE_JS, timeout=6)
                data = json.loads(raw) if isinstance(raw, str) else None
            except Exception:
                data = None
            cache[key] = data or {}
            if len(cache) > 40:
                cache.pop(next(iter(cache)))
            self._ed_busy = False
            self.invalidate()
        threading.Thread(target=work, daemon=True).start()
        return False

    def outline(self):
        e = self.emb
        if getattr(e, 'ed', None) is not None:
            d = self.__dict__.get('_ed_outline', {}).get(e.url)
            if not d:
                return [], 0, 0, 0
            return [(int(a), str(b)) for a, b in d.get('h', [])], d.get('l', 0), d.get('i', 0), d.get('f', 0)
        if e.doc is None:
            return [], 0, 0, 0
        heads, links, pics, forms = [], 0, 0, 0
        for n in e.doc.root.iter():
            t = n.tag
            if t in ('h1', 'h2', 'h3') and len(heads) < 30:
                txt = ' '.join(n.text_content().split())
                if txt:
                    heads.append((int(t[1]), txt))
            elif t == 'a' and n.get('href'):
                links += 1
            elif t == 'img':
                pics += 1
            elif t == 'form':
                forms += 1
        return heads, links, pics, forms

    def page_size(self):
        e = self.emb
        if getattr(e, 'ed', None) is not None:
            return (self.__dict__.get('_ed_outline', {}).get(e.url) or {}).get('n', 0)
        return len(getattr(e, 'source', '') or '')

    def draw_side(self, img, ui, box):
        x0, y0, x1, y1 = box
        self.side_bg(img, ui, box)
        e = self.emb
        split = y0 + int((y1 - y0) * 0.56)
        heads, links, pics, forms = self.outline()
        self.pane(img, ui, box, 'Overview', 'Page.over')
        inner = (x0 + 8, y0 + 26, x1 - 8, split - 8)
        ui.rect(inner, fill=(255, 255, 255), outline=(215, 222, 232))
        y = inner[1] + 6
        fnt = gfx.font(11)
        if not heads:
            ui.text((inner[0] + 8, y + 4), 'No headings on this page.' if e.doc is not None else 'Loading...', 11,
                    color=theme.MUTED)
        for lvl, txt in heads:
            if y + 16 > inner[3] - 20:
                break
            ix = inner[0] + 8 + (lvl - 1) * 10
            ui.text((ix, y), gfx.ellipsize(gfx.font(11, lvl == 1), txt, inner[2] - ix - 8), 11, bold=lvl == 1,
                    color=(30, 57, 91) if lvl == 1 else theme.CLIENT_TEXT)
            y += 16
        ui.text((inner[0] + 8, inner[3] - 12), '%d links  ·  %d pictures  ·  %d forms' % (links, pics, forms), 10,
                color=theme.MUTED, anchor='lm')
        self.pane(img, ui, (x0, split, x1, y1), 'Metadata')
        url = e.url or ''
        u = urllib.parse.urlsplit(url)
        try:
            from ..web import cookies
            ck = len(cookies.jar().matching(url)) if url.startswith('http') else 0
        except Exception:
            ck = 0
        secure = 'Secure (https)' if u.scheme == 'https' and not getattr(e, 'insecure', False) else (
            'Not secure' if u.scheme == 'http' else 'Local')
        rows = [('site', u.netloc or u.scheme or '-'), ('title', (e.doc.title if e.doc is not None else '') or '-'),
                ('security', secure), ('size', '%s chars' % '{:,}'.format(self.page_size())),
                ('cookies', str(ck)), ('scripts', 'Firefox (EarthDog)' if getattr(e, 'ed', None) is not None else
                                       ('running' if getattr(e, 'js', None) is not None else 'off')),
                ('status', getattr(e, 'status', '') or '-')]
        y = split + 28
        for k, v in rows:
            if y + 16 > y1 - 4:
                break
            ui.text((x0 + 12, y), k + ':', 11, color=theme.MUTED)
            ui.text((x0 + 76, y), gfx.ellipsize(fnt, str(v), x1 - x0 - 86), 11)
            y += 18

    def console_lines(self):
        try:
            from ..web import jsengine
            with open(jsengine.LOG, errors='replace') as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 8000))
                return f.read().splitlines()[-200:]
        except Exception:
            return []

    def draw_code(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.d.rectangle((x0, y0, x1, y1), fill=(16, 18, 22, 255))
        ui.d.rectangle((x0, y0, x1, y0 + 22), fill=(40, 46, 56, 255))
        js = getattr(self.emb, 'js', None) is not None
        ui.text((x0 + 10, y0 + 11), 'Code.Exe.Explore', 11, bold=True, color=(200, 230, 255), anchor='lm')
        ui.text((x0 + 140, y0 + 11), 'JavaScript console' if js else 'page source (scripts are not running here)', 10,
                color=(140, 160, 180), anchor='lm')
        ui.toolbutton((x1 - 26, y0 + 2, x1 - 4, y0 + 20), '×', 'code', size=11, dark=True)
        fnt = gfx.font(11, False, 'mono')
        lh = 15
        avail = (y1 - y0 - 22 - 34) // lh
        if js:
            lines = [l for l in self.console_lines() if not l.startswith('---')][-avail:]
        else:
            lines = (getattr(self.emb, 'source', '') or '').splitlines()[:avail]
        for i, l in enumerate(lines):
            col = (255, 150, 140) if l.startswith('[error') else (200, 225, 210)
            ui.d.text((x0 + 8, y0 + 26 + i * lh), gfx.ellipsize(fnt, l.replace('\t', '  '), x1 - x0 - 16),
                      font=gfx.font_for_obj(fnt, l or 'x'), fill=col + (255,))
        if js:
            ui.text((x0 + 8, y1 - 16), 'js>', 11, color=(120, 200, 255), anchor='lm', kind='mono')
            ui.textbox((x0 + 40, y1 - 28, x1 - 90, y1 - 5), 'code_in', self.code_in, mono=True,
                       placeholder='document.title')
            ui.button((x1 - 82, y1 - 28, x1 - 8, y1 - 5), 'Run', 'code_run', size=11)

    # -- input --------------------------------------------------------------------------
    def in_space(self, x, y):
        return gfx.in_rect(x, y, self.geometry()['space'])

    def local(self, x, y):
        sx0, sy0 = self.geometry()['space'][:2]
        return x - sx0, y - sy0

    def mouse(self, kind, x, y, button=1, clicks=1):
        e = self.emb
        if self.in_space(x, y) or (kind in ('move', 'up') and e.ui.down is not None):
            if kind == 'down':
                self.focus_page = True
                self.ui.focus = None
                self.ui.kfocus = None
            lx, ly = self.local(x, y)
            e.mouse(kind, lx, ly, button, clicks)
            if self.ui.mouse != (-1, -1):
                super().mouse('leave', x, y)
            return
        if kind == 'move' and e.ui.mouse != (-1, -1):
            e.mouse('leave', 0, 0)
        if kind == 'down':
            self.focus_page = False
        super().mouse(kind, x, y, button, clicks)

    def cursor_at(self, x, y):
        if self.in_space(x, y):
            return self.emb.cursor_at(*self.local(x, y))
        return super().cursor_at(x, y)

    def context_menu(self, x, y):
        if self.in_space(x, y):
            return self.emb.context_menu(*self.local(x, y))
        return None

    def on_wheel(self, dy, x, y):
        if self.in_space(x, y):
            self.emb.on_wheel(dy, *self.local(x, y))
        else:
            self.tree_scroll = max(0, self.tree_scroll - dy * 3)
        self.invalidate()

    def key(self, ev):
        if ev['type'] == 'key' and self.focus_page and self.ui.focus is None:
            k = ev['key']
            if ev.get('ctrl') and k == 'l':
                self.focus_page = False
                self.ui.focus = 'addr'
                self.addr['cur'] = len(self.addr['text'])
                self.invalidate()
                return True
            if ev.get('ctrl') and k == 'e':
                self.on_command('code', None)
                return True
            r = self.emb.key(ev)
            self.invalidate()
            return r
        return super().key(ev)

    def wants_caret(self):
        return (self.focus_page and self.emb.wants_caret()) or super().wants_caret()

    def on_command(self, cid, data):
        e = self.emb
        if isinstance(cid, tuple) and cid[0] == 'go':
            e.navigate(cid[1])
            self.focus_page = True
        elif cid == 'back':
            e.back()
        elif cid == 'fwd':
            e.forward()
        elif cid == 'up':
            u = urllib.parse.urlsplit(e.url)
            path = u.path.rstrip('/')
            parent = path.rsplit('/', 1)[0] + '/' if '/' in path else '/'
            e.navigate(urllib.parse.urlunsplit((u.scheme, u.netloc, parent, '', '')))
        elif cid == 'refresh':
            e.reload()
        elif cid == 'stop':
            e.on_command('stop', None)
        elif cid == 'addr' and data == 'enter':
            self.ui.focus = None
            self.focus_page = True
            e.navigate(self.addr['text'])
        elif cid == 'search' and data == 'enter' and self.search['text'].strip():
            self.ui.focus = None
            self.focus_page = True
            e.navigate(SEARCH % urllib.parse.quote_plus(self.search['text'].strip()))
        elif cid == 'code':
            self.code = not self.code
        elif cid in ('code_run',) or (cid == 'code_in' and data == 'enter'):
            src = self.code_in['text'].strip()
            if src and getattr(e, 'js', None) is not None:
                from ..web import jsengine
                jsengine.log('> ' + src)
                e.js_send({'t': 'eval', 'code': src})
                self.code_in = {'text': '', 'cur': 0}
        self.invalidate()

    @property
    def live(self):
        return self.emb.live or self.code

    def on_tick(self, now):
        changed = self.emb.on_tick(now)
        self.sync()
        self.ed_outline_tick(now)
        if self.code and int(now * 2) != int(getattr(self, '_last_code', 0) * 2):
            self._last_code = now
            changed = True
        return changed

    def on_resize(self):
        self.invalidate()

    def on_close(self):
        try:
            self.emb.on_close()
        except Exception:
            pass
        return True

    def on_destroy(self):
        """The window is gone (closed or forced): release the page's Firefox tab and Deno now."""
        try:
            self.emb.on_destroy()
        except Exception:
            pass
