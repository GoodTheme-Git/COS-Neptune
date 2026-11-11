"""Webinator: the COS web browser, back from v2.4 and now graphical.

v2.4's Webinator rendered HTML to text cells. Neptune's has a small layout
engine of its own (nep/web): real fonts, images, tables, lists, links and
forms (GET and POST), plus the simple CSS that decides what is hidden and
what colour things are.

JavaScript runs in a sandbox: each page with scripts gets its own Deno process
(nep/web/jsengine.py) that may use the network and nothing else. The page's
DOM lives there; after every change it comes back as HTML with data-nep ids,
Webinator lays it out again, and clicks and typing go back by those ids. With
JavaScript off (Tools menu) or no Deno installed, pages stay static.

Pages load on a worker thread and images on a small pool, so the window
never freezes. Anything that isn't a web page (a .cip, a song, a zip) is
downloaded into Downloads and handed to the right Neptune program.
"""
import gzip
import html as _html
import io
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageChops

from .. import gfx, icons, theme
from ..app import App
from ..web import dom, earthdog, jsengine
from ..web.layout import Layout
from .dialogs import message

UA = 'Mozilla/5.0 (X11; Linux x86_64) COS-Webinator/%s (Neptune; like Lynx)' % theme.VERSION
HOME_URL = 'about:webinator'
SEARCH = 'https://html.duckduckgo.com/html/?q=%s'
FAV_FILE = os.path.join(theme.STATE, 'favorites.txt')
DOWNLOADS = os.path.join(theme.HOME, 'Downloads')
PAGE_MAX = 8 * 1024 * 1024
IMG_MAX = 6 * 1024 * 1024
TOOL_H = 44
STATUS_H = 22
FIND_H = 30
SB_W = 17
HTML_TYPES = ('text/html', 'application/xhtml', 'text/plain', 'text/xml', 'application/xml', '')

START_PAGE = """<html><head><title>Webinator</title></head><body bgcolor="#f4f8fc">
<center>
<h1><font color="#1e4e8c">Webinator</font></h1>
<p><font color="#5a6a7a">the COS web browser &middot; COS %(version)s "Neptune" &middot; build %(build)s</font></p>
<form action="https://html.duckduckgo.com/html/" method="get">
<input name="q" size="48" placeholder="Search the web"> <input type="submit" value="Search">
</form>
</center>
<h3>Start somewhere</h3>
<ul>
<li><a href="https://en.wikipedia.org/wiki/Special:Random">A random Wikipedia article</a></li>
<li><a href="https://en.wikipedia.org/wiki/Text-based_web_browser">Wikipedia: text-based web browsers</a></li>
<li><a href="https://news.ycombinator.com/">Hacker News</a></li>
<li><a href="https://lite.cnn.com/">CNN Lite</a></li>
<li><a href="https://text.npr.org/">NPR text</a></li>
<li><a href="https://info.cern.ch/hypertext/WWW/TheProject.html">The first web page ever made</a></li>
</ul>
%(favorites)s
<h3>About this browser</h3>
<p>Webinator lays out pages itself: fonts, pictures, tables, lists, links and forms.
JavaScript runs in a sandbox that can reach the network and nothing else, so app-style sites
work, with their colours, themes, flexbox and grid layouts, rounded boxes and colour emoji
(no animations, floats or positioning yet). You can turn JavaScript
off in the Tools menu. Files that are not pages (a <code>.cip</code>, a song,
a zip) go to your Downloads folder and open in the right program.</p>
<p><font color="#5a6a7a">Ctrl+L address &middot; Ctrl+F find &middot; Ctrl+D add to favorites &middot;
Alt+Left/Right back/forward &middot; F5 reload &middot; Ctrl+plus/minus zoom &middot; Ctrl+U page source</font></p>
</body></html>"""


def load_favorites():
    out = []
    try:
        with open(FAV_FILE) as f:
            for line in f:
                if '\t' in line:
                    t, u = line.rstrip('\n').split('\t', 1)
                    out.append((t, u))
    except OSError:
        pass
    return out


def save_favorites(favs):
    os.makedirs(os.path.dirname(FAV_FILE), exist_ok=True)
    with open(FAV_FILE, 'w') as f:
        for t, u in favs:
            f.write('%s\t%s\n' % (t.replace('\t', ' '), u))


def _ctx(insecure):
    if insecure:
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        return c
    return ssl.create_default_context()


def open_url(url, data=None, insecure=False, timeout=20, accept='text/html,application/xhtml+xml,*/*;q=0.8',
             initiator=None, kind='navigate'):
    """initiator: the page that caused this request (None if you asked for it
    yourself); kind: 'navigate' or 'subresource'. Both decide which SameSite
    cookies go with it."""
    req = urllib.request.Request(url, data=data, headers={
        'User-Agent': UA, 'Accept': accept, 'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate'})
    if data is not None:
        req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ctx(insecure)),
                                         jsengine.CookieHandler(initiator, kind))
    return opener.open(req, timeout=timeout)


def read_body(r, limit):
    raw = r.read(limit)
    enc = (r.headers.get('Content-Encoding') or '').lower()
    try:
        if enc == 'gzip':
            raw = gzip.decompress(raw)
        elif enc == 'deflate':
            raw = zlib.decompress(raw, -zlib.MAX_WBITS)
    except Exception:
        pass
    return raw


def decode(raw, ctype):
    charset = None
    if 'charset=' in ctype.lower():
        charset = ctype.lower().split('charset=')[1].split(';')[0].strip().strip('"')
    if not charset:
        head = raw[:2048].decode('ascii', 'ignore').lower()
        i = head.find('charset=')
        if i >= 0:
            charset = head[i + 8:i + 30].strip('"\' ').split('"')[0].split("'")[0].split(';')[0].split('>')[0].strip()
    for cs in (charset, 'utf-8', 'latin-1'):
        if not cs:
            continue
        try:
            return raw.decode(cs)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode('utf-8', 'replace')


def unique_path(folder, name):
    name = name or 'download'
    base, ext = os.path.splitext(name)
    p = os.path.join(folder, name)
    i = 2
    while os.path.exists(p):
        p = os.path.join(folder, '%s (%d)%s' % (base, i, ext))
        i += 1
    return p


class Webinator(App):
    title = 'Webinator'
    icon = 'webinator'
    size = (1020, 720)
    min_size = (480, 320)
    bg = (255, 255, 255, 255)

    def __init__(self, shell, url=None, **kw):
        super().__init__(shell, **kw)
        self.addr = {'text': '', 'cur': 0}
        self.search = {'text': '', 'cur': 0}
        self.url = ''
        self.doc = None
        self.lay = None
        self.bands = {}
        self.scroll = 0
        self.zoom = 1.0
        self.history = []
        self.future = []
        self.visited = set()
        self.images = {}
        self.requested = set()
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.loading = None            # (load id, url) while a page loads
        self.load_id = 0
        self.result = None
        self.status = ''
        self.hover = None
        self.hover_ctl = None
        self.focus_ctl = None
        self.controls = {}
        self.need_relayout = False
        self.last_relayout = 0
        self.last_width = 0
        self.insecure = False
        self.find = None               # {'text', 'cur', 'hits', 'i'}
        self.source = ''
        self.post_data = None
        self.fav = load_favorites()
        self.resized_cache = {}
        self.css_cache = {}            # stylesheet url -> parsed rules (None while loading)
        self.js = None                 # the page's JSEngine while its scripts run
        self.js_hist = (0, 1)          # (index, length) of the page's pushState history
        self.js_seq = 0                # last input event sent to the page
        self.js_dom = None             # newest DOM from the page, not yet laid out
        self.js_dom_t = 0
        self.js_focus = False          # data-nep id the page wants focused (None: blur, False: nothing)
        self.js_last = None            # the DOM message laid out last
        self.js_css_new = False
        self.ed = None                 # the page's EarthDog (Firefox) tab, for http/https pages
        self.ed_seq = -1               # frame of it drawn last
        self.ed_press = False          # a mouse button went down on the page and is still down
        self.ed_popup = False          # this window is a popup the page opened
        self.ed_fails = []             # when Firefox stopped under us (gives up after a few)
        host_kw = getattr(getattr(self, 'host', None), 'kw', None) or {}
        ctx = kw.get('ed_context') or host_kw.get('ed_context')
        if ctx and self.ed_enabled():
            self.ed_attach(ctx)
        else:
            self.navigate(url or HOME_URL)

    # =========================================================================
    # loading
    # =========================================================================
    def resolve(self, text):
        t = text.strip()
        if not t:
            return None
        if t.startswith(('about:', 'http://', 'https://', 'file://', 'webinator:')):
            return t
        if t.startswith(('/', '~')) and os.path.exists(os.path.expanduser(t)):
            return 'file://' + os.path.abspath(os.path.expanduser(t))
        if ' ' in t or ('.' not in t and ':' not in t and t != 'localhost'):
            return SEARCH % urllib.parse.quote_plus(t)
        return 'http://' + t if t.startswith(('localhost', '127.', '192.168.', '10.')) else 'https://' + t

    def navigate(self, url, data=None, push=True, initiator=None):
        """initiator is the page URL when a page caused this (a link, a form, a
        script); None when you did (address bar, favourites, Back, Reload)."""
        url = self.resolve(url) if url else None
        if not url:
            return
        if url == 'webinator:insecure':
            self.insecure = True
            url, push = self.url_retry, False
        if url == 'webinator:retry':
            url, push = self.url_retry, False
        if url.startswith(('http://', 'https://')) and data is None and self.ed_enabled():
            self.ed_go(url, push)
            return
        if self.ed is not None:
            self.ed_leave()
        frag = urllib.parse.urldefrag(url)
        if self.doc and frag.url == urllib.parse.urldefrag(self.url).url and frag.fragment and data is None:
            if push:
                self.history.append((self.url, self.scroll))
                self.future.clear()
            self.url = url
            self.addr = {'text': url, 'cur': len(url)}
            self.jump(frag.fragment)
            return
        self.stop_js()
        self.load_id += 1
        lid = self.load_id
        self.loading = (lid, url)
        self.status = 'Connecting to %s...' % (urllib.parse.urlparse(url).netloc or url)
        self.addr = {'text': url, 'cur': len(url)}
        self.push = push
        self.post_data = data
        self.nav_initiator = initiator if initiator and initiator.startswith('http') else None
        threading.Thread(target=self._load, args=(lid, url, data), daemon=True).start()
        self.invalidate()

    def _load(self, lid, url, data):
        res = {'id': lid, 'url': url, 'kind': 'page'}
        try:
            if url.startswith('about:'):
                res['html'] = self.about(url)
            elif url.startswith('file://'):
                path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
                if os.path.isdir(path):
                    res['html'] = self.dir_listing(path)
                elif path.lower().endswith(('.html', '.htm', '.txt', '.xml')) or os.path.getsize(path) < 2_000_000 \
                        and not icons.for_path(path, False) in ('image', 'music', 'video', 'package', 'driver'):
                    with open(path, 'rb') as f:
                        raw = f.read(PAGE_MAX)
                    res['html'] = self.wrap_text(decode(raw, '')) if path.lower().endswith('.txt') else decode(raw, '')
                else:
                    res['kind'] = 'open'
                    res['path'] = path
            else:
                with open_url(url, data, self.insecure, initiator=getattr(self, 'nav_initiator', None)) as r:
                    res['url'] = r.geturl()
                    ctype = (r.headers.get('Content-Type') or '').lower()
                    main = ctype.split(';')[0].strip()
                    disp = r.headers.get('Content-Disposition') or ''
                    if main.startswith('image/') and 'svg' not in main:
                        raw = read_body(r, IMG_MAX)
                        im = Image.open(io.BytesIO(raw))
                        im.load()
                        self.images[res['url']] = im.convert('RGBA')
                        res['html'] = '<html><head><title>%s</title></head><body bgcolor="#1e1e1e"><center>' \
                                      '<img src="%s"></center></body></html>' % (
                                          _html.escape(os.path.basename(urllib.parse.urlparse(res['url']).path)),
                                          _html.escape(res['url']))
                    elif (main in HTML_TYPES or main.endswith('+xml')) and 'attachment' not in disp:
                        raw = read_body(r, PAGE_MAX)
                        text = decode(raw, ctype)
                        res['html'] = self.wrap_text(text) if main == 'text/plain' else text
                    else:
                        res['kind'] = 'download'
                        res['path'] = self.download(r, res['url'], disp, lid)
            if 'html' in res:
                res['source'] = res['html']
                res['js'] = res['url'].startswith(('http://', 'https://')) and \
                    re.search(r'<script', res['html'], re.I) is not None
                res['doc'] = dom.parse(res['html'], scripting=res['js'] and self.js_allowed())
                self.load_stylesheets(res['doc'], res['url'], lid)
        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = decode(read_body(e, PAGE_MAX), e.headers.get('Content-Type') or '')
            except Exception:
                pass
            own = body.strip().startswith('<') and len(body) > 200
            html = body if own else self.error_page(
                'HTTP %d %s' % (e.code, e.reason), 'The server answered, but not with the page.', url)
            # the site's own error page may need its scripts (bot checks, app "not found" pages)
            res['js'] = own and url.startswith(('http://', 'https://')) and re.search(r'<script', body, re.I) is not None
            res.update(html=html, source=html, doc=dom.parse(html, scripting=res['js'] and self.js_allowed()),
                       error='HTTP %d' % e.code)
        except ssl.SSLCertVerificationError as e:
            html = self.error_page('There is a problem with this website\'s security certificate', str(e), url,
                                   insecure=True)
            res.update(html=html, source=html, doc=dom.parse(html), error='certificate')
        except Exception as e:
            reason = getattr(e, 'reason', None) or e
            html = self.error_page('Webinator cannot display the webpage', '%s: %s' % (type(e).__name__, reason), url)
            res.update(html=html, source=html, doc=dom.parse(html), error=type(e).__name__)
        self.result = res

    def load_stylesheets(self, doc, page_url, lid):
        """Fetch the page's linked stylesheets (up to 12, from any site, in parallel,
        following @import one level) and apply them."""
        if not doc.stylesheets or not page_url.startswith('http'):
            return
        self.status = 'Loading styles...'
        urls = []
        for href in doc.stylesheets[:12]:
            u = urllib.parse.urljoin(page_url, href)
            if u not in urls:
                urls.append(u)
        texts = self._fetch_css_texts(urls, lid)
        if self.load_id != lid:
            return
        added = False
        for u in urls:
            css = texts.get(u)
            if not css:
                continue
            imports = [urllib.parse.urljoin(u, m) for m in re.findall(
                r'@import\s+(?:url\()?["\']?([^"\')\s;]+)', css)[:6]]
            if imports:
                more = self._fetch_css_texts(imports, lid)
                css = ''.join(more.get(i, '') for i in imports) + css
            rules = dom.parse_stylesheet(css)
            self.css_cache[u] = rules
            doc.rules += rules
            added = True
        if added:
            dom.compute_css(doc)

    def _fetch_css_texts(self, urls, lid):
        def one(u):
            try:
                with open_url(u, None, self.insecure, timeout=8, accept='text/css,*/*;q=0.1',
                              initiator=self.url, kind='subresource') as r:
                    return u, decode(read_body(r, 3_000_000), r.headers.get('Content-Type') or '')
            except Exception:
                return u, ''
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=6) as ex:
            return dict(ex.map(one, urls))

    def download(self, r, url, disp, lid):
        os.makedirs(DOWNLOADS, exist_ok=True)
        name = ''
        if 'filename=' in disp:
            name = disp.split('filename=')[-1].strip('"\'; ')
        name = os.path.basename(name or urllib.parse.unquote(urllib.parse.urlparse(url).path)) or 'download'
        path = unique_path(DOWNLOADS, name)
        total = int(r.headers.get('Content-Length') or 0)
        got = 0
        with open(path + '.part', 'wb') as f:
            while True:
                if self.load_id != lid:
                    raise RuntimeError('download cancelled')
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                self.status = 'Downloading %s: %s%s' % (name, _size(got), (' of %s' % _size(total)) if total else '')
        os.replace(path + '.part', path)
        return path

    def wrap_text(self, text):
        return '<html><body><pre>%s</pre></body></html>' % _html.escape(text)

    def about(self, url):
        if url == 'about:blank':
            return '<html><head><title>Blank</title></head><body></body></html>'
        favs = ''
        if self.fav:
            favs = '<h3>Favorites</h3><ul>%s</ul>' % ''.join(
                '<li><a href="%s">%s</a></li>' % (_html.escape(u), _html.escape(t)) for t, u in self.fav)
        return START_PAGE % {'version': theme.VERSION, 'build': theme.BUILD, 'favorites': favs}

    def dir_listing(self, path):
        rows = []
        for n in sorted(os.listdir(path), key=str.lower):
            if n.startswith('.'):
                continue
            p = os.path.join(path, n)
            rows.append('<li><a href="%s">%s%s</a></li>' % (_html.escape('file://' + urllib.parse.quote(p)),
                                                           _html.escape(n), '/' if os.path.isdir(p) else ''))
        return '<html><head><title>%s</title></head><body><h2>Index of %s</h2><ul>%s</ul></body></html>' % (
            _html.escape(path), _html.escape(path), ''.join(rows))

    def error_page(self, title, detail, url, insecure=False):
        self.url_retry = url
        extra = ('<p><a href="webinator:insecure">Continue to this website (not recommended)</a></p>'
                 if insecure else '')
        return ('<html><head><title>%s</title></head><body bgcolor="#ffffff"><table width="100%%"><tr>'
                '<td width="64"><font size="7" color="#c0392b">!</font></td><td><h2><font color="#1e4e8c">%s</font>'
                '</h2></td></tr></table><p>%s</p><p><font color="#666666">%s</font></p><ul>'
                '<li>Check the address for typing mistakes.</li><li>Make sure this computer is online.</li>'
                '<li>If the site needs JavaScript, check it is on in the Tools menu.</li></ul>%s'
                '<p><a href="webinator:retry">Try again</a></p></body></html>') % (
            _html.escape(title), _html.escape(title), _html.escape(url), _html.escape(detail), extra)

    def finish_load(self, res):
        if res['id'] != self.load_id:
            return
        self.loading = None
        if res['kind'] == 'download':
            self.status = 'Downloaded %s' % os.path.basename(res['path'])
            self.after_download(res['path'])
            self.addr = {'text': self.url, 'cur': len(self.url)}
            return
        if res['kind'] == 'open':
            self.addr = {'text': self.url, 'cur': len(self.url)}
            self.open_file(res['path'])
            return
        if self.push and self.url and self.url != res['url']:
            self.history.append((self.url, self.scroll))
            del self.history[:-100]
            self.future.clear()
        self.url = res['url']
        self.visited.add(self.url)
        self.addr = {'text': self.url, 'cur': len(self.url)}
        self.doc = res['doc']
        self.source = res.get('source', '')
        self.base = urllib.parse.urljoin(self.url, self.doc.base) if self.doc.base else self.url
        self.controls = {}
        self.focus_ctl = None
        self.hover = None
        self.scroll = getattr(self, 'restore_scroll', 0) or 0
        self.restore_scroll = 0
        self.relayout()
        t = self.doc.title or urllib.parse.urlparse(self.url).netloc or self.url
        self.set_title('%s - Webinator' % t if self.url != HOME_URL else 'Webinator')
        self.status = res.get('error') and ('Error: %s' % res['error']) or 'Done'
        frag = urllib.parse.urldefrag(self.url).fragment
        if frag:
            self.jump(frag)
        if res.get('error'):
            self.shell.sound('warn')
        if res.get('js') and self.js_allowed():
            self.start_js(res)
        if self.doc.refresh and not res.get('error'):
            parts = self.doc.refresh.split('url=', 1) if 'url=' in self.doc.refresh.lower() else None
            try:
                delay = float(self.doc.refresh.split(';')[0])
            except ValueError:
                delay = 0
            if parts and delay <= 3:
                target = self.doc.refresh[self.doc.refresh.lower().find('url=') + 4:].strip('\'" ')
                self.navigate(urllib.parse.urljoin(self.url, target), push=False, initiator=self.url)

    def after_download(self, path):
        name = os.path.basename(path)
        kind = icons.for_path(name, False)
        if kind in ('package', 'driver'):
            def go(ans):
                if ans == 'Install':
                    self.shell.launch('installer', path=path)
            message(self.shell, 'Download complete', '%s was saved in Downloads.\n\nIt is a COS package. Install it now?'
                    % name, 'question', buttons=('Install', 'Not now'), callback=go, parent=self.win)
            return
        self.shell.notify('Download complete', '%s was saved in Downloads.' % name, 'info', 6)
        if kind in ('music', 'video', 'image', 'archive'):
            self.open_file(path)

    def open_file(self, path):
        kind = icons.for_path(path, False)
        launch = {'image': 'photos', 'music': 'druman', 'video': 'druman', 'package': 'installer',
                  'driver': 'installer', 'cosapp': 'cosrun', 'archive': 'archiver'}.get(kind)
        if launch:
            self.shell.launch(launch, path=path)
        else:
            self.shell.launch('files', path=os.path.dirname(path))

    # =========================================================================
    # layout and images
    # =========================================================================
    def viewport(self):
        w, h = self.client_size
        bottom = h - STATUS_H - (FIND_H if self.find is not None else 0)
        return (0, TOOL_H, w - SB_W, bottom)

    def relayout(self):
        if self.doc is None or self.ed is not None:
            return
        vx0, vy0, vx1, vy1 = self.viewport()
        self.last_width = vx1 - vx0
        self.lay = Layout(self.doc, self.last_width, getattr(self, 'base', self.url), self.images, self.zoom,
                          self.visited, self.controls, rich=self.js is not None).run()
        bands = {}
        for i, it in enumerate(self.lay.items):
            y0, y1 = self.item_span(it)
            for b in range(int(y0) // 256, int(y1) // 256 + 1):
                bands.setdefault(b, []).append(i)
        self.bands = bands
        self.last_relayout = time.time()
        self.need_relayout = False
        for url in self.lay.want - self.requested:
            if len(self.requested) > 300:
                break
            self.requested.add(url)
            self.pool.submit(self._fetch_image, url, self.load_id)
        self.clamp_scroll()
        if self.find:
            self.do_find(keep=True)

    @staticmethod
    def item_span(it):
        k = it[0]
        if k == 't':
            return it[2] - it[4].size * 1.2, it[2] + it[4].size * 0.4
        if k in ('r', 'o', 'b'):
            return it[2], it[4]
        if k == 'i':
            return it[2], it[2] + it[4]
        c = it[1]
        return c.y, c.y + c.h

    def _fetch_image(self, url, lid):
        im = None
        try:
            if url.startswith('file://'):
                im = Image.open(urllib.parse.unquote(urllib.parse.urlparse(url).path))
            else:
                with open_url(url, None, self.insecure, timeout=15, accept='image/png,image/jpeg,image/gif,image/*',
                              initiator=self.url, kind='subresource') as r:
                    ctype = (r.headers.get('Content-Type') or '').lower()
                    if 'svg' in ctype:
                        raise ValueError('svg')
                    im = Image.open(io.BytesIO(read_body(r, IMG_MAX)))
            im.load()
            if max(im.size) > 2000:
                im.thumbnail((2000, 2000))
            im = im.convert('RGBA')
        except Exception:
            im = None
        self.images[url] = im
        if lid == self.load_id and im is not None:
            self.need_relayout = True

    def clamp_scroll(self):
        if not self.lay:
            return
        vh = self.viewport()[3] - self.viewport()[1]
        self.scroll = int(gfx.clamp(self.scroll, 0, max(0, self.lay.height - vh)))

    def jump(self, frag):
        if self.lay and frag in self.lay.anchors:
            self.scroll = int(self.lay.anchors[frag]) - 8
            self.clamp_scroll()
            self.invalidate()

    @property
    def live(self):
        return self.loading is not None or (self.ed is not None and self.ed.recently_changed())

    def on_tick(self, now):
        changed = False
        if self.ed is not None:
            changed = self.ed_tick(now)
        if self.result is not None:
            res, self.result = self.result, None
            self.finish_load(res)
            changed = True
        if self.js is not None and self.pump_js(now):
            changed = True
        if self.need_relayout and now - self.last_relayout > 0.35:
            self.relayout()
            changed = True
        if self.loading is not None:
            changed = True
        if self.win and self.ed is None and self.viewport()[2] != self.last_width and self.doc is not None:
            self.relayout()
            self.js_send({'t': 'resize', 'width': self.last_width, 'height': self.viewport()[3] - self.viewport()[1]})
            changed = True
        return changed

    def on_resize(self):
        if self.doc is not None:
            self.relayout()

    def on_destroy(self):
        self.stop_js()
        self.ed_leave()
        self.load_id += 1

    def on_close(self):
        self.stop_js()
        self.load_id += 1
        self.pool.shutdown(wait=False, cancel_futures=True)
        return True

    # =========================================================================
    # JavaScript
    # =========================================================================
    def js_allowed(self):
        return bool(self.shell.settings.get('webinator_js', True)) and jsengine.available()

    def toggle_js(self):
        self.shell.settings['webinator_js'] = not self.shell.settings.get('webinator_js', True)
        try:
            self.shell.settings.save()
        except Exception:
            pass
        self.reload()

    def start_js(self, res):
        vx0, vy0, vx1, vy1 = self.viewport()
        try:
            self.js = jsengine.JSEngine(self.url, res['html'], vx1 - vx0, vy1 - vy0, self.insecure)
        except Exception as e:
            self.js = None
            self.status = 'JavaScript could not start: %s' % e
            return
        self.js_hist = (0, 1)
        self.js_seq = 0
        self.js_dom = self.js_last = None
        self.js_focus = False
        self.status = 'Running scripts...'

    def stop_js(self):
        if self.js is not None:
            self.js.close()
            self.js = None
        self.js_dom = None
        self.js_hist = (0, 1)

    def js_send(self, msg):
        if self.js is not None:
            self.js.send(msg)

    @staticmethod
    def nep_id(node):
        v = node.get('data-nep') if node is not None else None
        return int(v) if v and v.isdigit() else None

    def pump_js(self, now):
        """Handle what the page's scripts sent. True if the window needs a repaint."""
        changed = False
        for m in self.js.poll():
            t = m.get('t')
            if t == 'dom':
                self.js_dom = m
            elif t == 'url':
                self.js_url(m)
                changed = True
            elif t == 'navigate':
                url = m.get('url') or self.url
                if m.get('target') == '_blank':
                    self.shell.launch('webinator', url=url)
                    continue
                if m.get('method') == 'post':
                    self.navigate(url, data=(m.get('body') or '').encode(), initiator=self.url)
                elif m.get('reload'):
                    self.reload()
                else:
                    self.navigate(url, push=not m.get('replace'), initiator=self.url)
                return True                       # the page (and its engine) is gone
            elif t == 'open':
                if m.get('url'):
                    self.shell.launch('webinator', url=m['url'])
            elif t == 'focus':
                self.js_focus = m.get('id')
                self.apply_js_focus(final=False)
                changed = True
            elif t == 'alert':
                host = urllib.parse.urlparse(self.url).netloc or 'This page'
                message(self.shell, '%s says' % host, m.get('text', '') or ' ', 'info', parent=self.win)
            elif t == 'error':
                self.status = 'Script error: %s' % (m.get('text') or '').strip().split('\n')[0][:200]
                changed = True
            elif t == 'ready':
                if not self.loading:
                    self.status = 'Done'
                changed = True
            elif t == 'exit':
                self.js.close()
                self.js = None
                self.status = 'The page\'s scripts stopped'
                return True
        if self.js_css_new and self.js_last is not None and self.js_dom is None:
            self.js_dom = self.js_last
        self.js_css_new = False
        if self.js_dom is not None and (now - self.js_dom_t > 0.08 or self.js_dom.get('ack', 0) >= self.js_seq > 0):
            m, self.js_dom = self.js_dom, None
            self.js_dom_t = now
            self.apply_js_dom(m)
            changed = True
        return changed

    def js_url(self, m):
        url = m.get('url') or self.url
        self.url = url
        self.visited.add(url)
        if self.ui.focus != 'addr':
            self.addr = {'text': url, 'cur': len(url)}
        self.js_hist = (int(m.get('index', 0)), int(m.get('length', 1)))

    def apply_js_dom(self, m):
        doc = dom.parse(m.get('html', ''))
        extra = []
        host = urllib.parse.urlparse(self.url).netloc.split('.')[-2:]
        for href in doc.stylesheets[:6]:
            u = urllib.parse.urljoin(self.url, href)
            if u in self.css_cache:
                extra += self.css_cache[u] or []
            elif urllib.parse.urlparse(u).netloc.split('.')[-2:] == host:
                self.css_cache[u] = None
                self.pool.submit(self._fetch_css, u, self.load_id)
        if extra:
            doc.rules = extra + doc.rules
            dom.compute_css(doc)
        old = self.focus_ctl
        old_id = old.node.get('data-nep') if old is not None else None
        keep_local = old is not None and m.get('ack', 0) < self.js_seq
        self.js_last = m
        self.doc = doc
        self.base = urllib.parse.urljoin(self.url, doc.base) if doc.base else self.url
        self.controls = {}
        self.hover_ctl = None
        self.focus_ctl = None
        self.relayout()
        if old_id:
            for c in self.lay.controls:
                if c.node.get('data-nep') == old_id:
                    if keep_local:
                        c.value = old.value
                    c.cur = min(old.cur, len(c.value))
                    self.focus_ctl = c
                    break
        self.apply_js_focus(final=True)
        t = m.get('title') or doc.title or urllib.parse.urlparse(self.url).netloc or self.url
        self.set_title('%s - Webinator' % t)

    def apply_js_focus(self, final):
        """Follow the page moving focus (autofocus, el.focus(), blur)."""
        fid = self.js_focus
        if fid is False:
            return
        if fid is None:
            self.focus_ctl = None
            self.js_focus = False
            return
        for c in (self.lay.controls if self.lay else []):
            if c.node.get('data-nep') == str(fid):
                if c.kind in ('text', 'password', 'textarea'):
                    if c is not self.focus_ctl:
                        c.cur = len(c.value)
                    self.focus_ctl = c
                    self.ui.focus = None
                else:
                    self.focus_ctl = None
                self.js_focus = False
                return
        if final:
            # focus went to something that isn't a text box (a link, a div)
            self.focus_ctl = None
            self.js_focus = False

    def _fetch_css(self, url, lid):
        rules = []
        try:
            with open_url(url, None, self.insecure, timeout=8, accept='text/css,*/*;q=0.1',
                              initiator=self.url, kind='subresource') as r:
                rules = dom.parse_stylesheet(decode(read_body(r, 1_500_000), r.headers.get('Content-Type') or ''))
        except Exception:
            pass
        self.css_cache[url] = rules
        if lid == self.load_id and rules:
            self.js_css_new = True

    def js_key(self, c, key, ev):
        self.js_seq += 1
        self.js_send({'t': 'key', 'id': self.nep_id(c.node) if c is not None else None, 'key': key,
                      'shift': bool(ev.get('shift')), 'ctrl': bool(ev.get('ctrl')), 'alt': bool(ev.get('alt')),
                      'seq': self.js_seq})

    def can_back(self):
        return bool(self.history) or (self.js is not None and self.js_hist[0] > 0) or \
            (self.ed is not None and self.ed.can_back)

    def can_forward(self):
        return bool(self.future) or (self.js is not None and self.js_hist[0] < self.js_hist[1] - 1) or \
            (self.ed is not None and self.ed.can_fwd)

    # =========================================================================
    # painting
    # =========================================================================
    # =========================================================================
    # EarthDog: http/https pages are shown by Firefox (nep/web/earthdog.py)
    # =========================================================================
    def ed_enabled(self):
        if self.shell.settings.get('webinator_engine', 'earthdog') != 'earthdog' or not earthdog.available():
            return False
        now = time.time()
        return len([t for t in self.ed_fails if now - t < 120]) < 3

    def toggle_ed(self):
        on = self.shell.settings.get('webinator_engine', 'earthdog') == 'earthdog'
        self.shell.settings['webinator_engine'] = 'builtin' if on else 'earthdog'
        try:
            self.shell.settings.save()
        except Exception:
            pass
        if self.url.startswith(('http://', 'https://')):
            if self.ed is not None:
                self.ed_leave()
            self.navigate(self.url, push=False)

    def ed_rect(self):
        vx0, vy0, vx1, vy1 = self.viewport()
        return (vx0, vy0, vx1 + SB_W, vy1)         # Firefox draws its own scrollbars

    def ed_size(self):
        r = self.ed_rect()
        return max(50, r[2] - r[0]), max(50, r[3] - r[1])

    def _ed_page(self):
        self.stop_js()
        self.load_id += 1
        self.loading = None
        self.doc = dom.Document()
        self.lay = None
        self.bands = {}
        self.controls = {}
        self.focus_ctl = self.hover_ctl = None
        self.hover = None
        self.source = ''
        self.scroll = 0
        self.ed_seq = -1

    def ed_go(self, url, push=True):
        if self.ed is None:
            if push and self.url and self.url != url:
                self.history.append((self.url, self.scroll))     # Back from Firefox's first page returns here
                del self.history[:-100]
                self.future.clear()
            self._ed_page()
            self.ed = earthdog.Tab(*self.ed_size())
            if self.zoom != 1.0:
                self.ed.resize(*self.ed_size(), zoom=self.zoom)
        self.ed.navigate(url)
        self.url = url
        self.visited.add(url)
        self.addr = {'text': url, 'cur': len(url)}
        self.status = 'Connecting to %s...' % (urllib.parse.urlparse(url).netloc or url)
        self.invalidate()

    def ed_attach(self, ctx):
        """Show a window the page opened (window.open, target=_blank)."""
        self._ed_page()
        self.ed_popup = True
        self.ed = earthdog.Tab(*self.ed_size(), context=ctx)
        self.status = 'Opening...'

    def ed_leave(self):
        if self.ed is not None:
            self.ed.close()
            self.ed = None
            self.ed_press = False
            if self.ui.down and self.ui.down[0] == 'page':
                self.ui.down = None

    def ed_tick(self, now):
        t = self.ed
        t.touch()
        changed = False
        while t.events and self.ed is t:
            changed = True
            self.ed_event(t, t.events.popleft())
        if self.ed is not t:
            return True
        if t.url and t.url != self.url:
            self.url = t.url
            self.visited.add(t.url)
            if self.ui.focus != 'addr':
                self.addr = {'text': t.url, 'cur': len(t.url)}
            changed = True
        if t.title != self.doc.title:
            self.doc.title = t.title
            host = urllib.parse.urlparse(self.url).netloc or self.url
            self.set_title('%s - Webinator' % (t.title or host))
            changed = True
        if not t.ready:
            status = 'Starting EarthDog...' if not t.closed else self.status
        elif t.loading:
            status = 'Loading %s...' % (urllib.parse.urlparse(self.url).netloc or self.url)
        else:
            status = 'Done'
        hover = t.hover_link or None
        if status != self.status or hover != self.hover:
            self.status, self.hover = status, hover
            changed = True
        if t.ready and (t.w, t.h) != self.ed_size():
            t.resize(*self.ed_size())
        if t.frame_seq != self.ed_seq:
            changed = True
        return changed

    def ed_event(self, t, ev):
        kind = ev[0]
        if kind == 'prompt':
            p = ev[1]
            typ, text = p.get('type', 'alert'), p.get('message', '') or ''
            host = urllib.parse.urlparse(self.url).netloc or 'This page'
            if typ == 'beforeunload':
                t.handle_prompt(True)
            elif typ == 'alert':
                message(self.shell, host, text, 'info', callback=lambda a: t.handle_prompt(True), parent=self.win)
            else:
                default = p.get('defaultValue')
                message(self.shell, host, text, 'question', buttons=('OK', 'Cancel'),
                        callback=lambda a: t.handle_prompt(a == 'OK', default if typ == 'prompt' else None),
                        parent=self.win)
        elif kind == 'popup':
            self.shell.launch('webinator', ed_context=ev[1])
        elif kind == 'download':
            self.shell.notify('Webinator', 'Downloading %s...' % os.path.basename(ev[1] or 'a file'), 'info', 3)
        elif kind == 'downloaded':
            path, status = ev[1], ev[2]
            if status == 'complete' and path and os.path.exists(path):
                self.after_download(path)
            elif status != 'complete':
                self.shell.notify('Webinator', 'A download did not finish.', 'warning', 4)
        elif kind == 'closed':
            self.ed = None
            if ev[1] is None and self.ed_popup:
                self.close()                     # the page closed its own popup (sign-in done, etc.)
                return
            self.ed_fails.append(time.time())
            self.status = 'EarthDog stopped: %s' % (ev[1] or 'the page closed')
            if self.url.startswith(('http://', 'https://')):
                self.navigate(self.url, push=False)   # new Firefox, or the built-in engine after repeated failures
        elif kind == 'error':
            self.ed = None
            self.ed_fails += [time.time()] * 3   # Firefox won't start: built-in engine for a while
            self.shell.notify('Webinator', 'EarthDog (Firefox) could not start, so the built-in engine is used. '
                              '%s' % ev[1], 'warning', 6)
            self.navigate(self.url, push=False)

    def ed_draw(self, img, ui):
        r = self.ed_rect()
        x0, y0, x1, y1 = r
        if x1 <= x0 or y1 <= y0:
            return
        fr = self.ed.frame
        self.ed_seq = self.ed.frame_seq
        ui.rect(r, fill=(255, 255, 255))
        if fr is not None:
            w, h = min(fr.size[0], x1 - x0), min(fr.size[1], y1 - y0)
            img.paste(fr.crop((0, 0, w, h)) if (w, h) != fr.size else fr, (x0, y0))
        else:
            ui.icon('webinator', ((x0 + x1) // 2 - 24, (y0 + y1) // 2 - 40), 48)
            ui.text(((x0 + x1) // 2, (y0 + y1) // 2 + 24), self.status or 'Starting EarthDog...', 12,
                    color=theme.MUTED, anchor='mm')
        ui.region(r, 'page', 'page')

    def ed_mouse(self, kind, x, y, button, clicks):
        r = self.ed_rect()
        inside = gfx.in_rect(x, y, r)
        px, py = min(max(x, r[0]), r[2] - 1) - r[0], min(max(y, r[1]), r[3] - 1) - r[1]
        if kind == 'down' and inside:
            self.ui.mouse = (x, y)
            self.ui.focus = self.ui.kfocus = None
            if button == 3:
                return                           # Neptune's own menu (context_menu)
            self.ed_press = True
            self.ui.down = ('page', 'page', None, r)
            self.ed.mouse('down', px, py, button)
            self.invalidate()
            return
        if kind == 'up' and self.ed_press:
            self.ed_press = False
            self.ui.down = None
            self.ed.mouse('up', px, py, button)
            self.invalidate()
            return
        if kind == 'move' and (inside or self.ed_press):
            self.ed.mouse('move', px, py)
            if self.ed_press:
                return
        App.mouse(self, kind, x, y, button, clicks)

    def ed_key(self, ev):
        k, ctrl = ev['key'], ev.get('ctrl')
        t = self.ed
        if k == 'escape' and self.find is not None:
            self.on_command('find_close', None)
            return True
        if k == 'escape' and t.loading:
            t.stop()
            return True
        if ctrl and k in ('c', 'x'):
            sel = t.selection()
            if sel:
                self.shell.clipboard = sel
            if k == 'x':
                t.key(ev)
            return True
        if ctrl and k == 'v':
            if self.shell.clipboard:
                t.insert_text(self.shell.clipboard)
            return True
        return t.key(ev)

    def ed_menu(self, x, y):
        t = self.ed
        link = t.hover_link if gfx.in_rect(x, y, self.ed_rect()) else ''
        items = []
        if link:
            items += [{'label': 'Open Link in New Window', 'action': lambda: self.shell.launch('webinator', url=link)},
                      {'label': 'Copy Link Address', 'action': lambda: setattr(self.shell, 'clipboard', link)},
                      {'sep': True}]
        sel = t.selection()
        items += [{'label': 'Back', 'enabled': self.can_back(), 'action': self.back},
                  {'label': 'Forward', 'enabled': self.can_forward(), 'action': self.forward},
                  {'label': 'Reload', 'action': self.reload},
                  {'sep': True},
                  {'label': 'Copy', 'enabled': bool(sel), 'action': lambda: setattr(self.shell, 'clipboard', sel)},
                  {'label': 'Paste', 'enabled': bool(self.shell.clipboard),
                   'action': lambda: t.insert_text(self.shell.clipboard)},
                  {'sep': True},
                  {'label': 'Add to Favorites', 'action': self.add_favorite},
                  {'label': 'Copy Page Text', 'action': lambda: setattr(self.shell, 'clipboard', self.page_text())},
                  {'label': 'View Source', 'action': self.view_source}]
        return items

    def draw(self, img, ui):
        w, h = img.size
        self.draw_page(img, ui)
        self.draw_toolbar(img, ui, w)
        if self.find is not None:
            self.draw_findbar(img, ui, w, h)
        self.draw_status(img, ui, w, h)

    def draw_toolbar(self, img, ui, w):
        tint = self.shell.settings.tint
        img.alpha_composite(gfx.vgradient(w, TOOL_H, ((0, gfx.mix(tint, (255, 255, 255), 0.82)),
                                                      (1, gfx.mix(tint, (255, 255, 255), 0.62)))), (0, 0))
        ui.hline(0, w, TOOL_H - 1, gfx.mix(tint, (0, 0, 0), 0.25)[:3])
        # back (big) and forward (small) round buttons, IE8 style
        self.round_arrow(img, ui, (24, 22), 15, 'back', -1, self.can_back())
        self.round_arrow(img, ui, (55, 22), 11, 'fwd', 1, self.can_forward())
        # address box
        ax0, ax1 = 74, w - 260
        box = (ax0, 10, ax1, 34)
        ui.icon('webinator' if not self.url.startswith('https') else 'lock', (ax0 + 4, 14), 16)
        ui.textbox((ax0 + 22, 10, ax1 - 52, 34), 'addr', self.addr)
        ui.rect((ax0, 10, ax0 + 22, 34), fill=(255, 255, 255))
        ui.rect((ax0, 10, ax0 + 22, 34), outline=(171, 173, 179))
        ui.icon('lock' if self.url.startswith('https') else 'webinator', (ax0 + 3, 14), 16)
        if self.loading:
            ui.toolbutton((ax1 - 52, 10, ax1 - 26, 34), '×', 'stop', size=14)
            # progress sweep under the address text
            t = (time.time() * 0.8) % 1.0
            bw = ax1 - ax0 - 60
            sx = ax0 + 22 + int(t * bw)
            ui.d.rectangle((max(ax0 + 22, sx - 40), 31, min(ax1 - 52, sx), 33), fill=(70, 160, 230, 255))
        else:
            ui.toolbutton((ax1 - 52, 10, ax1 - 26, 34), '⟳', 'reload', size=14)
        ui.toolbutton((ax1 - 26, 10, ax1, 34), '⌂', 'home', size=15)
        # search box
        ui.textbox((w - 252, 10, w - 70, 34), 'search', self.search, placeholder='Search DuckDuckGo')
        ui.toolbutton((w - 66, 8, w - 38, 36), '★', 'fav', size=16, toggled=any(u == self.url for _, u in self.fav))
        ui.toolbutton((w - 34, 8, w - 6, 36), '☰', 'menu', size=14)

    def round_arrow(self, img, ui, c, r, cid, direction, enabled):
        cx, cy = c
        box = (cx - r, cy - r, cx + r, cy + r)
        hot = ui.hot(box) and enabled
        if enabled:
            top, bot = ((120, 190, 250), (20, 80, 170)) if hot else ((90, 160, 230), (20, 70, 150))
        else:
            top, bot = (215, 220, 228), (175, 180, 190)
        from PIL import ImageDraw
        m = Image.new('L', (r * 2, r * 2), 0)
        ImageDraw.Draw(m).ellipse((0, 0, r * 2 - 1, r * 2 - 1), fill=255)
        gfx.paste_masked(img, gfx.vgradient(r * 2, r * 2, ((0, top), (1, bot))), m, (cx - r, cy - r))
        ui.d.ellipse(box, outline=(30, 60, 110, 255) if enabled else (150, 155, 165, 255))
        s = r * 0.5
        col = (255, 255, 255, 255)
        ui.d.line([(cx - s * direction, cy), (cx + s * direction, cy)], fill=col, width=max(2, r // 5))
        ui.d.line([(cx, cy - s), (cx + s * direction, cy), (cx, cy + s)], fill=col, width=max(2, r // 5))
        if enabled:
            ui.region(box, cid)

    def draw_findbar(self, img, ui, w, h):
        y0 = h - STATUS_H - FIND_H
        ui.rect((0, y0, w, y0 + FIND_H), fill=(240, 244, 249))
        ui.hline(0, w, y0, (200, 210, 222))
        ui.text((10, y0 + 15), 'Find:', 12, anchor='lm')
        ui.textbox((48, y0 + 4, 300, y0 + FIND_H - 4), 'find', self.find)
        hits = self.find.get('hits', [])
        ui.button((308, y0 + 4, 368, y0 + FIND_H - 4), 'Previous', 'find_prev', enabled=bool(hits))
        ui.button((372, y0 + 4, 432, y0 + FIND_H - 4), 'Next', 'find_next', enabled=bool(hits))
        msg = ('%d of %d' % (self.find['i'] + 1, len(hits))) if hits else ('No matches' if self.find['text'] else '')
        if self.ed is not None and hits:
            msg = 'Found'
        ui.text((444, y0 + 15), msg, 12, color=theme.MUTED, anchor='lm')
        ui.toolbutton((w - 30, y0 + 4, w - 6, y0 + FIND_H - 4), '×', 'find_close', size=14)

    def draw_status(self, img, ui, w, h):
        y0 = h - STATUS_H
        img.alpha_composite(gfx.vgradient(w, STATUS_H, ((0, (241, 245, 251)), (1, (222, 230, 240)))), (0, y0))
        ui.hline(0, w, y0, (190, 200, 214))
        txt = self.hover or self.status
        ui.text((8, y0 + 11), gfx.ellipsize(gfx.font(11), txt, w - 220), 11, color=(40, 50, 60), anchor='lm')
        sec = 'Secure (https)' if self.url.startswith('https') and not self.insecure else (
            'Not secure' if self.url.startswith('http') else 'Local')
        ui.text((w - 70, y0 + 11), sec, 11, color=theme.MUTED, anchor='rm')
        ui.toolbutton((w - 62, y0 + 1, w - 4, y0 + STATUS_H - 1), '%d%%' % round(self.zoom * 100), 'zoom', size=11)

    def draw_page(self, img, ui):
        if self.ed is not None:
            return self.ed_draw(img, ui)
        vx0, vy0, vx1, vy1 = self.viewport()
        vw, vh = vx1 - vx0, vy1 - vy0
        if vw <= 0 or vh <= 0:
            return
        lay = self.lay
        bg = lay.bg if lay else (255, 255, 255)
        page = Image.new('RGBA', (vw, vh), bg + (255,))
        from PIL import ImageDraw
        d = ImageDraw.Draw(page)
        if lay:
            s = self.scroll
            idx = set()
            for b in range(s // 256, (s + vh) // 256 + 1):
                idx.update(self.bands.get(b, ()))
            hits = self.find.get('hits', []) if self.find else []
            cur_hit = hits[self.find['i']] if hits else None
            hit_items = {}
            for n, (ii, a, b2) in enumerate(hits):
                hit_items.setdefault(ii, []).append((a, b2, n == self.find['i']))
            for i in sorted(idx):
                it = lay.items[i]
                k = it[0]
                if k == 't':
                    _, x, base, text, fnt, col, under, strike, link = it
                    y = base - s
                    if i in hit_items:
                        fa, fd = fnt.getmetrics()
                        for a, b2, cur in hit_items[i]:
                            hx0 = x + fnt.getlength(text[:a])
                            hx1 = x + fnt.getlength(text[:b2])
                            d.rectangle((hx0, y - fa, hx1, y + fd), fill=(255, 150, 50, 255) if cur else (255, 235, 90, 255))
                    if link and link == self.hover:
                        col = (0, 51, 153)
                    d.text((x, y), text, font=gfx.font_for_obj(fnt, text), fill=tuple(col) + (255,), anchor='ls')
                    if under or (link and link == self.hover):
                        tw = fnt.getlength(text)
                        d.line([(x, y + 2), (x + tw, y + 2)], fill=tuple(col) + (255,))
                    if strike:
                        tw = fnt.getlength(text)
                        d.line([(x, y - fnt.size * 0.3), (x + tw, y - fnt.size * 0.3)], fill=tuple(col) + (255,))
                elif k == 'b':
                    self.draw_box(page, d, it, s)
                elif k == 'r':
                    d.rectangle((it[1], it[2] - s, it[3] - 1, it[4] - s - 1), fill=tuple(it[5]) + (255,))
                elif k == 'o':
                    d.rectangle((it[1], it[2] - s, it[3] - 1, it[4] - s - 1), outline=tuple(it[5]) + (255,))
                elif k == 'i':
                    _, x, y, iw, ih, url, link, alt = it[:8]
                    radius = it[8] if len(it) > 8 else 0
                    src = self.images.get(url)
                    if src is None:
                        d.rectangle((x, y - s, x + iw - 1, y - s + ih - 1), fill=(236, 239, 243, 255),
                                    outline=(200, 205, 212, 255))
                        continue
                    key = (url, iw, ih, radius)
                    im = self.resized_cache.get(key)
                    if im is None:
                        im = src if src.size == (iw, ih) else src.resize((max(1, iw), max(1, ih)), Image.LANCZOS)
                        if radius:
                            im = im.convert('RGBA')
                            im.putalpha(ImageChops.multiply(im.getchannel('A'), gfx.rounded_mask(
                                iw, ih, int(min(radius, iw / 2, ih / 2)))))
                        if len(self.resized_cache) > 200:
                            self.resized_cache.clear()
                        self.resized_cache[key] = im
                    gfx.alpha_composite_clip(page, im, int(x), int(y - s))
                    if link and link == self.hover:
                        d.rectangle((x, y - s, x + iw - 1, y - s + ih - 1), outline=(0, 102, 204, 255), width=2)
                elif k == 'c':
                    self.draw_ctl(page, d, it[1], s)
        img.paste(page, (vx0, vy0))
        total = lay.height if lay else vh
        ui.scrollbar((vx1, vy0, vx1 + SB_W, vy1), 'vscroll', self.scroll, vh, total)
        ui.region((vx0, vy0, vx1, vy1), 'page', 'page')

    @staticmethod
    def draw_box(page, d, it, s):
        """A CSS box: background, borders (per side when square), rounded corners."""
        _k, x0, y0, x1, y1, fill, bcol, bw, radius = it[:9]
        y0, y1 = y0 - s, y1 - s
        if x1 - x0 < 1 or y1 - y0 < 1 or y1 < 0 or y0 > page.size[1]:
            return
        r = int(min(radius or 0, (x1 - x0) / 2, (y1 - y0) / 2))
        f = tuple(fill) + (255,) if fill else None
        line = tuple(bcol) + (255,) if bcol else None
        top, right, bottom, left = bw
        uniform = top == right == bottom == left
        if r > 0:
            d.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=r, fill=f,
                                outline=line if (line and uniform and top) else None, width=max(1, int(top or 1)))
            if line and not uniform:
                if bottom:
                    d.line([(x0 + r, y1 - 1), (x1 - r, y1 - 1)], fill=line, width=int(bottom))
                if top:
                    d.line([(x0 + r, y0), (x1 - r, y0)], fill=line, width=int(top))
            return
        if f:
            d.rectangle((x0, y0, x1 - 1, y1 - 1), fill=f)
        if line:
            if top:
                d.rectangle((x0, y0, x1 - 1, y0 + top - 1), fill=line)
            if bottom:
                d.rectangle((x0, y1 - bottom, x1 - 1, y1 - 1), fill=line)
            if left:
                d.rectangle((x0, y0, x0 + left - 1, y1 - 1), fill=line)
            if right:
                d.rectangle((x1 - right, y0, x1 - 1, y1 - 1), fill=line)

    def draw_ctl(self, page, d, c, s):
        x, y, w, h = c.x, c.y - s, c.w, c.h
        focused = c is self.focus_ctl
        hot = c is self.hover_ctl
        fnt = c.font
        css = getattr(c, 'css', None)
        if css and c.kind in ('text', 'password', 'textarea', 'select'):
            return self.draw_css_ctl(d, c, x, y, w, h, focused, css)
        if c.kind in ('text', 'password', 'textarea'):
            d.rectangle((x, y, x + w - 1, y + h - 1), fill=(255, 255, 255, 255),
                        outline=(61, 123, 173, 255) if focused else ((126, 180, 234, 255) if hot else (171, 173, 179, 255)))
            txt = c.value if c.kind != 'password' else '•' * len(c.value)
            if not txt and getattr(c, 'placeholder', '') and not focused:
                d.text((x + 5, y + h // 2), c.placeholder, font=fnt, fill=(140, 145, 150, 255), anchor='lm')
            elif c.kind == 'textarea':
                lines = txt.split('\n')
                lh = int(fnt.size * 1.3)
                for i, ln in enumerate(lines[: max(1, (h - 6) // lh)]):
                    d.text((x + 4, y + 4 + i * lh), ln, font=gfx.font_for_obj(fnt, ln), fill=(0, 0, 0, 255))
            else:
                off = 0
                while fnt.getlength(txt[off:c.cur]) > w - 12 and off < c.cur:
                    off += 1
                vis = txt[off:]
                d.text((x + 5, y + h // 2), vis, font=gfx.font_for_obj(fnt, vis or 'x'), fill=(0, 0, 0, 255), anchor='lm')
                if focused and self.shell.caret_on:
                    cx = x + 5 + fnt.getlength(txt[off:c.cur])
                    d.line([(cx, y + 4), (cx, y + h - 5)], fill=(0, 0, 0, 255))
        elif c.kind == 'checkbox':
            d.rectangle((x, y, x + w - 1, y + h - 1), fill=(250, 250, 252, 255),
                        outline=(60, 127, 177, 255) if hot else (51, 51, 51, 255))
            if c.checked:
                d.line([(x + 3, y + h * 0.5), (x + w * 0.42, y + h - 4), (x + w - 3, y + 3)], fill=(30, 60, 110, 255), width=2)
        elif c.kind == 'radio':
            d.ellipse((x, y, x + w - 1, y + h - 1), fill=(250, 250, 252, 255),
                      outline=(60, 127, 177, 255) if hot else (51, 51, 51, 255))
            if c.checked:
                d.ellipse((x + w * 0.28, y + h * 0.28, x + w * 0.72, y + h * 0.72), fill=(40, 110, 190, 255))
        else:
            from ..ui import BTN_NORMAL, BTN_HOVER
            stops, border = BTN_HOVER if hot else BTN_NORMAL
            page.alpha_composite(gfx.vgradient(max(1, w), max(1, h), stops), (int(x), int(y))) if y + h > 0 else None
            d.rectangle((x, y, x + w - 1, y + h - 1), outline=border + (255,))
            label = c.display() if c.kind == 'select' else c.label
            if c.kind == 'select':
                d.text((x + 6, y + h // 2), label, font=gfx.font_for_obj(fnt, label or 'x'), fill=(0, 0, 0, 255), anchor='lm')
                ax = x + w - 12
                d.polygon([(ax - 4, y + h / 2 - 2), (ax + 4, y + h / 2 - 2), (ax, y + h / 2 + 3)], fill=(40, 40, 40, 255))
            else:
                d.text((x + w // 2, y + h // 2), label, font=gfx.font_for_obj(fnt, label or 'x'), fill=(0, 0, 0, 255), anchor='mm')

    def draw_css_ctl(self, d, c, x, y, w, h, focused, css):
        """A text field / select the page styled with CSS (dark themes and so on)."""
        fnt = c.font
        bg = css.get('bg') or (255, 255, 255)
        fg = css.get('fg') or ((230, 230, 235) if sum(bg) < 300 else (20, 20, 20))
        border = css.get('border') or ((90, 140, 230) if focused else tuple(max(0, v - 30) for v in bg))
        if focused:
            border = tuple(min(255, int(v * 0.6 + q * 0.4)) for v, q in zip(border, (88, 101, 242)))
        r = int(min(css.get('radius') or 0, h / 2))
        d.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius=r, fill=tuple(bg) + (255,),
                            outline=tuple(border) + (255,), width=max(1, int(css.get('bw') or 1)))
        if c.kind == 'select':
            label = c.display()
            d.text((x + 8, y + h // 2), label, font=gfx.font_for_obj(fnt, label or 'x'), fill=tuple(fg) + (255,),
                   anchor='lm')
            ax = x + w - 12
            d.polygon([(ax - 4, y + h / 2 - 2), (ax + 4, y + h / 2 - 2), (ax, y + h / 2 + 3)], fill=tuple(fg) + (255,))
            return
        txt = c.value if c.kind != 'password' else '•' * len(c.value)
        dim = tuple(int(v * 0.55 + b * 0.45) for v, b in zip(fg, bg)) + (255,)
        if not txt and getattr(c, 'placeholder', '') and not focused:
            d.text((x + 8, y + h // 2), c.placeholder, font=gfx.font_for_obj(fnt, c.placeholder), fill=dim,
                   anchor='lm')
            return
        if c.kind == 'textarea':
            lh = int(fnt.size * 1.3)
            for i, ln in enumerate(txt.split('\n')[: max(1, (h - 6) // lh)]):
                d.text((x + 6, y + 5 + i * lh), ln, font=gfx.font_for_obj(fnt, ln), fill=tuple(fg) + (255,))
            return
        off = 0
        while fnt.getlength(txt[off:c.cur]) > w - 16 and off < c.cur:
            off += 1
        vis = txt[off:]
        d.text((x + 8, y + h // 2), vis, font=gfx.font_for_obj(fnt, vis or 'x'), fill=tuple(fg) + (255,), anchor='lm')
        if focused and self.shell.caret_on:
            cx = x + 8 + fnt.getlength(txt[off:c.cur])
            d.line([(cx, y + 5), (cx, y + h - 6)], fill=tuple(fg) + (255,))

    # =========================================================================
    # hit testing and input
    # =========================================================================
    def page_point(self, x, y):
        vx0, vy0, vx1, vy1 = self.viewport()
        if not (vx0 <= x < vx1 and vy0 <= y < vy1):
            return None
        return x - vx0, y - vy0 + self.scroll

    def link_at(self, px, py):
        if not self.lay:
            return None
        for x0, y0, x1, y1, link in reversed(self.lay.regions):
            if x0 <= px <= x1 and y0 <= py <= y1:
                return link
        return None

    def ctl_at(self, px, py):
        if not self.lay:
            return None
        for c in self.lay.controls:
            if c.x <= px <= c.x + c.w and c.y <= py <= c.y + c.h:
                return c
        return None

    def mouse(self, kind, x, y, button=1, clicks=1):
        if self.ed is not None:
            return self.ed_mouse(kind, x, y, button, clicks)
        if kind == 'move':
            pp = self.page_point(x, y)
            link = ctl = None
            if pp and self.ui.down is None:
                link = self.link_at(*pp)
                ctl = self.ctl_at(*pp)
            if link != self.hover or ctl is not self.hover_ctl:
                self.hover, self.hover_ctl = link, ctl
                self.invalidate()
        elif kind == 'leave' and (self.hover or self.hover_ctl):
            self.hover = self.hover_ctl = None
            self.invalidate()
        super().mouse(kind, x, y, button, clicks)

    def cursor_at(self, x, y):
        if self.ed is not None:
            return self.ed.cursor if gfx.in_rect(x, y, self.ed_rect()) else App.cursor_at(self, x, y)
        if self.hover:
            return 'hand'
        if self.hover_ctl is not None:
            return 'ibeam' if self.hover_ctl.kind in ('text', 'password', 'textarea') else 'hand'
        return super().cursor_at(x, y)

    def on_down(self, cid, kind, data, x, y, button):
        if cid != 'page':
            if cid not in ('addr', 'search', 'find'):
                pass
            self.focus_ctl = None if cid in ('addr', 'search', 'find') else self.focus_ctl
            return
        pp = self.page_point(x, y)
        if not pp:
            return
        self.ui.focus = None
        ctl = self.ctl_at(*pp)
        if self.js is not None:
            self.js_down(pp, ctl, x, y, button)
            return
        if ctl is not None:
            self.click_ctl(ctl, x, y)
            return
        self.focus_ctl = None
        link = self.link_at(*pp)
        if link:
            if button == 2 or self.shell_ctrl():
                self.shell.launch('webinator', url=link)
            elif button == 1:
                self.follow(link)

    def js_down(self, pp, ctl, x, y, button):
        """A click on a page that runs scripts: the page decides what it does."""
        node = ctl.node if ctl is not None else (self.lay.el_at(*pp) if self.lay else None)
        link = self.link_at(*pp)
        if link and (button == 2 or self.shell_ctrl()):
            self.shell.launch('webinator', url=link)
            return
        if ctl is not None and ctl.kind in ('text', 'password', 'textarea'):
            if ctl is not self.focus_ctl:
                ctl.cur = len(ctl.value)
            self.focus_ctl = ctl
        else:
            self.focus_ctl = None
        if ctl is not None and ctl.kind == 'select':
            self.click_ctl(ctl, x, y)
        nid = self.nep_id(node)
        if nid is not None:
            self.js_send({'t': 'click', 'id': nid, 'button': button, 'x': pp[0], 'y': pp[1] - self.scroll,
                          'ctrl': self.shell_ctrl()})
        elif link:
            self.follow(link)
        self.invalidate()

    def shell_ctrl(self):
        return False

    def follow(self, link):
        if link.startswith('mailto:'):
            self.shell.notify('Webinator', 'Webinator has no mail program for %s.' % link[7:], 'info', 4)
            return
        self.navigate(link, initiator=self.url)

    def click_ctl(self, c, x, y):
        if c.kind in ('text', 'password', 'textarea'):
            self.focus_ctl = c
            c.cur = len(c.value)
        elif c.kind == 'checkbox':
            c.checked = not c.checked
        elif c.kind == 'radio':
            for o in self.controls.values():
                if o.kind == 'radio' and o.name == c.name and o.form is c.form:
                    o.checked = False
            c.checked = True
        elif c.kind == 'select':
            cx0, cy0 = self.win.client_rect()[:2]
            vx0, vy0 = self.viewport()[:2]
            items = [{'label': t or '(blank)', 'checked': v == c.value,
                      'action': (lambda v=v: self.set_select(c, v))} for v, t in c.options[:40]]
            if items:
                self.shell.popup(items, cx0 + vx0 + c.x, cy0 + vy0 + c.y - self.scroll + c.h, min_w=c.w)
        elif c.kind == 'submit':
            self.submit(c.form, c)
        self.invalidate()

    def set_select(self, c, v):
        c.value = v
        if self.js is not None and self.nep_id(c.node) is not None:
            self.js_send({'t': 'change', 'id': self.nep_id(c.node), 'value': v})
        self.invalidate()

    def submit(self, form, submitter=None):
        if form is None or self.js is not None:
            return
        params = []
        for n in form.node.iter():
            if n.tag not in ('input', 'select', 'textarea', 'button') or not n.get('name') or 'disabled' in n.attrs:
                continue
            c = self.controls.get(n.nid)
            t = n.get('type', 'text').lower() if n.tag == 'input' else n.tag
            if t in ('submit', 'image', 'button') or n.tag == 'button':
                if c is not None and c is submitter and n.get('name'):
                    params.append((n.get('name'), n.get('value', '')))
                continue
            if t in ('checkbox', 'radio'):
                on = c.checked if c is not None else 'checked' in n.attrs
                if on:
                    params.append((n.get('name'), n.get('value', 'on')))
                continue
            if t in ('reset', 'file'):
                continue
            params.append((n.get('name'), c.value if c is not None else n.get('value', '')))
        action = urllib.parse.urljoin(getattr(self, 'base', self.url), form.action or self.url)
        body = urllib.parse.urlencode(params)
        if form.method == 'post':
            self.navigate(action, data=body.encode(), initiator=self.url)
        else:
            base = action.split('?', 1)[0].split('#', 1)[0]
            self.navigate(base + ('?' + body if body else ''), initiator=self.url)

    def on_command(self, cid, data):
        if cid == 'back':
            self.back()
        elif cid == 'fwd':
            self.forward()
        elif cid == 'reload':
            self.reload()
        elif cid == 'stop':
            if self.ed is not None:
                self.ed.stop()
            self.load_id += 1
            self.loading = None
            self.status = 'Stopped'
        elif cid == 'home':
            self.navigate(HOME_URL)
        elif cid == 'addr' and data == 'enter':
            self.ui.focus = None
            self.navigate(self.addr['text'])
        elif cid == 'search' and data == 'enter' and self.search['text'].strip():
            self.ui.focus = None
            self.navigate(SEARCH % urllib.parse.quote_plus(self.search['text'].strip()))
        elif cid == 'vscroll':
            self.scroll = data
        elif cid == 'fav':
            self.fav_menu()
        elif cid == 'menu':
            self.tools_menu()
        elif cid == 'zoom':
            self.set_zoom(1.0 if self.zoom != 1.0 else 1.25)
        elif cid == 'find' and data == 'enter':
            self.find_step(1)
        elif cid == 'find_next':
            self.find_step(1)
        elif cid == 'find_prev':
            self.find_step(-1)
        elif cid == 'find_close':
            self.find = None
            self.ui.focus = None
            self.relayout()
        self.invalidate()

    def on_text(self, cid, state):
        if cid == 'find':
            self.do_find()

    def screen_menu_pos(self, x, y):
        cx0, cy0 = self.win.client_rect()[:2]
        return cx0 + x, cy0 + y

    def fav_menu(self):
        w, _ = self.client_size
        sx, sy = self.screen_menu_pos(w - 66, 36)
        items = [{'label': 'Add to Favorites', 'icon': 'webinator', 'action': self.add_favorite, 'shortcut': 'Ctrl+D'}]
        if any(u == self.url for _, u in self.fav):
            items.append({'label': 'Remove this page', 'action': self.remove_favorite})
        if self.fav:
            items.append({'sep': True})
            items += [{'label': gfx.ellipsize(gfx.font(12), t, 300), 'action': (lambda u=u: self.navigate(u))}
                      for t, u in self.fav[:30]]
        self.shell.popup(items, sx - 200, sy, min_w=230)

    def tools_menu(self):
        w, _ = self.client_size
        sx, sy = self.screen_menu_pos(w - 34, 36)
        items = [
            {'label': 'New Window', 'action': lambda: self.shell.launch('webinator'), 'shortcut': 'Ctrl+N'},
            {'label': 'Find on Page...', 'action': self.open_find, 'shortcut': 'Ctrl+F'},
            {'label': 'View Source', 'action': self.view_source, 'shortcut': 'Ctrl+U'},
            {'label': 'Save Page As Text', 'action': self.save_text},
            {'sep': True},
            {'label': 'Zoom In', 'action': lambda: self.set_zoom(self.zoom + 0.1), 'shortcut': 'Ctrl++'},
            {'label': 'Zoom Out', 'action': lambda: self.set_zoom(self.zoom - 0.1), 'shortcut': 'Ctrl+-'},
            {'label': 'Actual Size', 'action': lambda: self.set_zoom(1.0), 'shortcut': 'Ctrl+0'},
            {'sep': True},
            {'label': 'Downloads Folder', 'icon': 'folder', 'action': self.open_downloads},
            {'label': 'Allow Insecure Certificates', 'checked': self.insecure, 'action': self.toggle_insecure},
            {'label': 'Enable JavaScript', 'checked': self.js_allowed(), 'enabled': jsengine.available(),
             'action': self.toggle_js},
            {'label': 'Use EarthDog (Firefox)', 'checked': self.ed_enabled(), 'enabled': earthdog.available(),
             'action': self.toggle_ed},
        ]
        self.shell.popup(items, sx - 200, sy, min_w=230)

    def toggle_insecure(self):
        self.insecure = not self.insecure

    def open_downloads(self):
        os.makedirs(DOWNLOADS, exist_ok=True)
        self.shell.launch('files', path=DOWNLOADS)

    def add_favorite(self):
        if not self.url or any(u == self.url for _, u in self.fav):
            return
        title = (self.doc.title if self.doc and self.doc.title else self.url)[:120]
        self.fav.append((title, self.url))
        save_favorites(self.fav)
        self.shell.notify('Webinator', 'Added "%s" to Favorites.' % gfx.ellipsize(gfx.font(12), title, 260), 'info', 3)
        self.invalidate()

    def remove_favorite(self):
        self.fav = [(t, u) for t, u in self.fav if u != self.url]
        save_favorites(self.fav)
        self.invalidate()

    def set_zoom(self, z):
        z = round(gfx.clamp(z, 0.5, 2.5), 2)
        if self.ed is not None:
            self.zoom = z
            self.ed.resize(*self.ed_size(), zoom=z)
            self.invalidate()
            return
        if z != self.zoom:
            frac = self.scroll / max(1, self.lay.height) if self.lay else 0
            self.zoom = z
            self.resized_cache.clear()
            self.relayout()
            if self.lay:
                self.scroll = int(frac * self.lay.height)
                self.clamp_scroll()
            self.invalidate()

    def view_source(self):
        if self.ed is not None:
            self.source = self.ed.html()
        p = os.path.join(theme.STATE, 'web', 'page-source.html')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w') as f:
            f.write(self.source or '')
        self.shell.launch('notepad', path=p)

    def page_text(self):
        if self.ed is not None:
            return self.ed.text()
        if not self.lay:
            return ''
        lines = {}
        for it in self.lay.items:
            if it[0] == 't':
                lines.setdefault(int(it[2]), []).append((it[1], it[3]))
        return '\n'.join(''.join(t for _, t in sorted(v)) for _, v in sorted(lines.items()))

    def save_text(self):
        os.makedirs(DOWNLOADS, exist_ok=True)
        name = ''.join(c for c in (self.doc.title if self.doc else 'page')[:60] if c.isalnum() or c in ' -_') or 'page'
        p = unique_path(DOWNLOADS, name.strip() + '.txt')
        with open(p, 'w') as f:
            f.write(self.page_text())
        self.shell.notify('Webinator', 'Saved %s in Downloads.' % os.path.basename(p), 'info', 4)

    def back(self):
        if self.ed is not None and self.ed.can_back:
            self.ed.back()
            return
        if self.js is not None and self.js_hist[0] > 0:
            self.js_send({'t': 'history', 'delta': -1})
            return
        if self.history:
            url, sc = self.history.pop()
            self.future.append((self.url, self.scroll))
            self.restore_scroll = sc
            self.navigate(url, push=False)

    def forward(self):
        if self.ed is not None and self.ed.can_fwd:
            self.ed.forward()
            return
        if self.js is not None and self.js_hist[0] < self.js_hist[1] - 1:
            self.js_send({'t': 'history', 'delta': 1})
            return
        if self.future:
            url, sc = self.future.pop()
            self.history.append((self.url, self.scroll))
            self.restore_scroll = sc
            self.navigate(url, push=False)

    def reload(self):
        if self.ed is not None:
            self.ed.reload()
            return
        self.restore_scroll = self.scroll
        self.requested.clear()
        for k in [k for k, v in self.images.items() if v is None]:
            del self.images[k]
        self.navigate(self.url, data=self.post_data, push=False)

    # -- find ---------------------------------------------------------------------------------
    def open_find(self):
        if self.find is None:
            self.find = {'text': '', 'cur': 0, 'hits': [], 'i': 0}
            self.relayout()
        self.ui.focus = 'find'
        self.invalidate()

    def do_find(self, keep=False):
        if self.find and self.ed is not None:
            q = self.find['text']
            self.find['hits'] = [0] if q and self.ed.find(q) else []
            self.find['i'] = 0
            return
        if not self.find or not self.lay:
            return
        q = self.find['text'].lower()
        hits = []
        if q:
            for i, it in enumerate(self.lay.items):
                if it[0] == 't':
                    low = it[3].lower()
                    j = low.find(q)
                    while j >= 0:
                        hits.append((i, j, j + len(q)))
                        j = low.find(q, j + 1)
        self.find['hits'] = hits
        if not keep:
            self.find['i'] = 0
            self.reveal_hit()
        else:
            self.find['i'] = min(self.find['i'], max(0, len(hits) - 1))

    def find_step(self, d):
        if self.find and self.ed is not None:
            if self.find['text']:
                self.find['hits'] = [0] if self.ed.find(self.find['text'], backwards=d < 0) else []
            return
        hits = self.find.get('hits', []) if self.find else []
        if hits:
            self.find['i'] = (self.find['i'] + d) % len(hits)
            self.reveal_hit()

    def reveal_hit(self):
        hits = self.find.get('hits', [])
        if not hits:
            return
        it = self.lay.items[hits[self.find['i']][0]]
        vh = self.viewport()[3] - self.viewport()[1]
        y = it[2]
        if not (self.scroll + 30 < y < self.scroll + vh - 30):
            self.scroll = int(y - vh / 3)
            self.clamp_scroll()

    # -- keyboard ----------------------------------------------------------------------------
    def key(self, ev):
        if ev['type'] != 'key':
            return False
        k = ev['key']
        ctrl, alt = ev.get('ctrl'), ev.get('alt')
        # browser shortcuts work everywhere
        if ctrl and k == 'l':
            self.ui.focus = 'addr'
            self.addr['cur'] = len(self.addr['text'])
            self.invalidate()
            return True
        if ctrl and k == 'f':
            self.open_find()
            return True
        if ctrl and k == 'd':
            self.add_favorite()
            return True
        if ctrl and k == 'n':
            self.shell.launch('webinator')
            return True
        if ctrl and k == 'u':
            self.view_source()
            return True
        if ctrl and k in ('=', '+', 'plus', 'equal'):
            self.set_zoom(self.zoom + 0.1)
            return True
        if ctrl and k in ('-', 'minus'):
            self.set_zoom(self.zoom - 0.1)
            return True
        if ctrl and k == '0':
            self.set_zoom(1.0)
            return True
        if k == 'f5' or (ctrl and k == 'r'):
            self.reload()
            return True
        if alt and k == 'left':
            self.back()
            return True
        if alt and k == 'right':
            self.forward()
            return True
        if alt and k == 'home':
            self.navigate(HOME_URL)
            return True
        if self.ed is not None and self.ui.focus is None:
            return self.ed_key(ev)
        if k == 'escape':
            if self.loading:
                self.on_command('stop', None)
            elif self.find is not None:
                self.on_command('find_close', None)
            else:
                if self.js is not None:
                    self.js_key(self.focus_ctl, 'Escape', ev)
                self.focus_ctl = None
                self.ui.focus = None
            self.invalidate()
            return True
        if self.ui.focus is not None:
            return super().key(ev)
        if self.focus_ctl is not None:
            return self.ctl_key(self.focus_ctl, ev)
        vh = self.viewport()[3] - self.viewport()[1]
        step = {'down': 48, 'up': -48, 'pagedown': vh - 40, 'pageup': -(vh - 40), 'space': vh - 40}.get(k)
        if k == 'space' and ev.get('shift'):
            step = -(vh - 40)
        if step is not None:
            self.scroll += step
        elif k == 'home':
            self.scroll = 0
        elif k == 'end':
            self.scroll = 10 ** 9
        elif k == 'backspace':
            self.back()
        elif k == 'tab':
            ctls = [c for c in (self.lay.controls if self.lay else []) if c.kind in ('text', 'password', 'textarea')]
            if ctls:
                self.focus_ctl = ctls[0]
        else:
            return False
        self.clamp_scroll()
        self.invalidate()
        return True

    def ctl_key(self, c, ev):
        k, ch = ev['key'], ev.get('char', '')
        v, cur = c.value, min(c.cur, len(c.value))
        js = self.js is not None and self.nep_id(c.node) is not None
        jkey, paste = None, False
        if k == 'enter':
            if c.kind == 'textarea':
                v, cur = v[:cur] + '\n' + v[cur:], cur + 1
                jkey = 'Enter'
            elif js:
                self.js_key(c, 'Enter', ev)
                return True
            else:
                self.submit(c.form)
                return True
        elif ch and not ev.get('ctrl'):
            v, cur = v[:cur] + ch + v[cur:], cur + 1
            jkey = ch
        elif k == 'backspace':
            if cur:
                v, cur = v[:cur - 1] + v[cur:], cur - 1
            jkey = 'Backspace'
        elif k == 'delete':
            v = v[:cur] + v[cur + 1:]
            jkey = 'Delete'
        elif k in ('up', 'down') and js:
            self.js_key(c, 'ArrowUp' if k == 'up' else 'ArrowDown', ev)
            return True
        elif k == 'left':
            cur = max(0, cur - 1)
        elif k == 'right':
            cur = min(len(v), cur + 1)
        elif k == 'home':
            cur = 0
        elif k == 'end':
            cur = len(v)
        elif k == 'tab':
            ctls = [x for x in self.lay.controls if x.kind in ('text', 'password', 'textarea')]
            if c in ctls and len(ctls) > 1:
                self.focus_ctl = ctls[(ctls.index(c) + 1) % len(ctls)]
                if js:
                    self.js_send({'t': 'focus', 'id': self.nep_id(self.focus_ctl.node)})
        elif ev.get('ctrl') and k == 'v':
            text = getattr(self.shell, 'clipboard', '') or ''
            v, cur = v[:cur] + text + v[cur:], cur + len(text)
            paste = True
        else:
            return False
        if js and v != c.value:
            self.js_seq += 1
            self.js_send({'t': 'input', 'id': self.nep_id(c.node), 'value': v, 'cur': cur, 'key': jkey,
                          'char': ch if jkey == ch and ch else None, 'paste': paste,
                          'shift': bool(ev.get('shift')), 'seq': self.js_seq})
        c.value, c.cur = v, cur
        self.invalidate()
        return True

    def wants_caret(self):
        return self.ui.focus is not None or self.focus_ctl is not None

    def on_wheel(self, dy, x, y):
        if self.ed is not None:
            r = self.ed_rect()
            self.ed.wheel(-dy * 100, x - r[0], y - r[1])
            return
        self.scroll -= dy * 60
        self.clamp_scroll()
        self.invalidate()

    def context_menu(self, x, y):
        if self.ed is not None:
            return self.ed_menu(x, y)
        pp = self.page_point(x, y)
        link = self.link_at(*pp) if pp else None
        items = []
        if link:
            items += [{'label': 'Open Link', 'action': lambda: self.follow(link)},
                      {'label': 'Open Link in New Window', 'action': lambda: self.shell.launch('webinator', url=link)},
                      {'label': 'Copy Link Address', 'action': lambda: setattr(self.shell, 'clipboard', link)},
                      {'sep': True}]
        items += [{'label': 'Back', 'enabled': self.can_back(), 'action': self.back},
                  {'label': 'Forward', 'enabled': self.can_forward(), 'action': self.forward},
                  {'label': 'Reload', 'action': self.reload},
                  {'sep': True},
                  {'label': 'Add to Favorites', 'action': self.add_favorite},
                  {'label': 'Copy Page Text', 'action': lambda: setattr(self.shell, 'clipboard', self.page_text())},
                  {'label': 'View Source', 'action': self.view_source}]
        return items


def _size(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024.0
