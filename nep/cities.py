"""COS Cities: free home pages on The COS Place, the old-web way.

Every COS computer gets one home page at an address in a neighborhood, like
/cities/Trench/4021/. The page is the folder home/Sites (index.html plus
pictures and sounds); this computer's COS Place node serves it over HTTP, so
other computers open http://<computer>:8643/cities/Trench/4021/ in Webinator.

Server-side includes give pages the classic furniture:
    <!--#counter-->     visitor counter (one count per visitor per hour)
    <!--#guestbook-->   signed entries plus a form to sign it
    <!--#webring-->     previous / random / next page in the same neighborhood
    <!--#updated-->     when the page was last changed
Clip art is generated and served at /cities/_art/<name>.gif.

Pages are HTML, pictures and sounds only: scripts are not served, <script>
blocks and on...= handlers are stripped, and pages carry a CSP header
that forbids scripts and outside connections. Guestbook entries are escaped,
size-limited and rate-limited per address.

The directory (/cities/) lists every page this computer knows about on The
COS Place, gathered from /cosplace/v1/sites on each computer it has reached.
Nodes that don't have COS Cities (COS Aurora, for now) just answer 404.
"""
import html as _html
import io
import json
import os
import random
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import theme

SITE_DIR = os.path.join(theme.HOME, 'Sites')
STATE = os.path.join(theme.STATE, 'cities')
SITE_FILE = os.path.join(STATE, 'site.json')
COUNTER_FILE = os.path.join(STATE, 'counter.json')
GUESTBOOK_FILE = os.path.join(STATE, 'guestbook.json')
PREVIEW_DIR = os.path.join(STATE, 'preview')
ART_DIR = os.path.join(STATE, 'art')

NEIGHBORHOODS = [
    ('Trench', 'The deep sea, oceans and everything Neptune'),
    ('Nebula', 'Space, science fiction and the stars'),
    ('Arcade', 'Games, high scores and cheat codes'),
    ('Garage', 'Computers, gadgets and building things'),
    ('BubblePark', 'Kids, pets and families'),
    ('Studio', 'Art, music and making things'),
    ('Lighthouse', 'Writing, poems and stories'),
    ('Pitstop', 'Cars, sports and going fast'),
]
NEIGHBORHOOD = dict(NEIGHBORHOODS)

TEMPLATES = {
    'starfield': {'label': 'Starfield', 'bg': '#000010', 'text': '#e0e0ff', 'link': '#66ffff', 'vlink': '#ff66ff',
                  'head': '#ffff66', 'tile': 'stars'},
    'sea': {'label': 'Under the Sea', 'bg': '#003366', 'text': '#ffffff', 'link': '#99ffcc', 'vlink': '#ccccff',
            'head': '#66ffff', 'tile': 'bubbles'},
    'cafe': {'label': 'Cyber Cafe', 'bg': '#001100', 'text': '#33ff33', 'link': '#ffff00', 'vlink': '#99cc00',
             'head': '#66ff66', 'tile': 'grid'},
    'shrine': {'label': 'Fan Shrine', 'bg': '#ffccee', 'text': '#660033', 'link': '#cc0066', 'vlink': '#993399',
               'head': '#ff0099', 'tile': 'hearts'},
    'plain': {'label': 'Plain Paper', 'bg': '#ffffff', 'text': '#000000', 'link': '#0000ee', 'vlink': '#551a8b',
              'head': '#000080', 'tile': ''},
}

SERVE_TYPES = {'.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8', '.css': 'text/css',
               '.txt': 'text/plain; charset=utf-8', '.png': 'image/png', '.gif': 'image/gif', '.jpg': 'image/jpeg',
               '.jpeg': 'image/jpeg', '.bmp': 'image/bmp', '.webp': 'image/webp', '.ico': 'image/x-icon',
               '.mid': 'audio/midi', '.midi': 'audio/midi', '.wav': 'audio/wav', '.mp3': 'audio/mpeg',
               '.ogg': 'audio/ogg'}
MAX_FILE = 8 * 1024 * 1024
CSP = "default-src 'self' data:; img-src * data:; media-src *; style-src 'self' 'unsafe-inline'; script-src 'none'; " \
      "connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'self'"
GB_MAX = 300
GB_GAP = 30                  # seconds between entries from one address

_lock = threading.RLock()
_gb_last = {}
_dir_cache = {'t': 0, 'sites': [], 'busy': False}


def _txt(v, n):
    s = v if isinstance(v, str) else ''
    s = ''.join(c for c in s if c.isprintable())
    return s.strip()[:n]


def _load(path, default):
    try:
        with open(path) as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


# -- this computer's site ----------------------------------------------------------------------
def site():
    """The settings for this computer's page; makes up an address the first time."""
    with _lock:
        s = _load(SITE_FILE, {})
        changed = False
        if s.get('neighborhood') not in NEIGHBORHOOD:
            s['neighborhood'] = 'Trench'
            changed = True
        if not isinstance(s.get('number'), int) or not 1000 <= s['number'] <= 9999:
            s['number'] = random.randint(1000, 9999)
            changed = True
        for k, v in (('title', ''), ('tagline', ''), ('about', ''), ('owner', ''), ('template', 'starfield'),
                     ('links', ['', '', '', '']), ('marquee', ''), ('counter', True), ('guestbook', True),
                     ('webring', True), ('construction', True), ('published', False), ('generated', 0)):
            if k not in s:
                s[k] = v
                changed = True
        if changed:
            _save(SITE_FILE, s)
        return s


def save_site(s):
    with _lock:
        _save(SITE_FILE, s)


def address(s=None):
    s = s or site()
    return '/cities/%s/%d/' % (s['neighborhood'], s['number'])


def index_path():
    return os.path.join(SITE_DIR, 'index.html')


def hand_edited(s=None):
    """True when index.html was changed after COS Cities last wrote it (so don't overwrite it silently)."""
    s = s or site()
    try:
        return os.path.getmtime(index_path()) > s.get('generated', 0) + 2
    except OSError:
        return False


def _link_parts(line):
    """'My friend http://x/' -> ('My friend', 'http://x/'). Only http(s) and /cities/ links."""
    words = line.split()
    url = next((w for w in reversed(words) if w.startswith(('http://', 'https://', '/cities/'))), '')
    title = ' '.join(w for w in words if w != url).strip() or url
    return title, url


def build_page(s):
    t = TEMPLATES.get(s.get('template'), TEMPLATES['starfield'])
    e = _html.escape
    title = s.get('title') or 'My Home Page'
    nb = s['neighborhood']
    parts = ['<html><head><title>%s</title>' % e(title),
             '<meta name="generator" content="COS Cities Page Builder"></head>',
             '<body bgcolor="%s" text="%s" link="%s" vlink="%s"%s>' % (
                 t['bg'], t['text'], t['link'], t['vlink'],
                 ' background="/cities/_art/%s.gif"' % t['tile'] if t['tile'] else ''),
             '<center>']
    if s.get('construction'):
        parts.append('<img src="/cities/_art/construction.gif" width="200" height="50" alt="Under construction"><br>')
    parts.append('<h1><font color="%s" face="Comic Sans MS, Arial">%s</font></h1>' % (t['head'], e(title)))
    if s.get('tagline'):
        parts.append('<p><i>%s</i></p>' % e(s['tagline']))
    if s.get('marquee'):
        parts.append('<marquee width="80%%" scrollamount="4"><b>%s</b></marquee>' % e(s['marquee']))
    parts.append('<img src="/cities/_art/rainbow.gif" width="460" height="8" alt="----"><br>')
    parts.append('</center>')
    if s.get('about'):
        parts.append('<table width="80%%" align="center"><tr><td><h2><font color="%s">About me</font></h2>'
                     '<p>%s</p></td></tr></table>' % (t['head'], e(s['about'])))
    links = [_link_parts(l) for l in s.get('links') or [] if l.strip()]
    links = [(a, b) for a, b in links if b]
    if links:
        parts.append('<table width="80%%" align="center"><tr><td><h2><font color="%s">Cool links</font></h2><ul>'
                     % t['head'])
        for a, b in links:
            parts.append('<li><a href="%s">%s</a> <img src="/cities/_art/new.gif" width="32" height="16" alt="new">'
                         % (e(b), e(a)))
        parts.append('</ul></td></tr></table>')
    parts.append('<center><img src="/cities/_art/rainbow.gif" width="460" height="8" alt="----"><br>')
    if s.get('counter'):
        parts.append('<p>You are visitor number <!--#counter--></p>')
    if s.get('guestbook'):
        parts.append('<h2><font color="%s">Guestbook</font></h2><!--#guestbook-->' % t['head'])
    if s.get('webring'):
        parts.append('<p><!--#webring--></p>')
    parts.append('<p><font size="2">Made with COS Cities on %s &middot; %s/%d &middot; updated <!--#updated-->'
                 '</font></p>' % (e(theme.FULLNAME), e(nb), s['number']))
    parts.append('<p><a href="/cities/"><img src="/cities/_art/cities.gif" width="88" height="31" '
                 'alt="COS Cities" border="0"></a></p></center></body></html>')
    return '\n'.join(parts)


def write_page(s=None):
    s = s or site()
    os.makedirs(SITE_DIR, exist_ok=True)
    with open(index_path(), 'w') as f:
        f.write(build_page(s))
    s['generated'] = time.time()
    save_site(s)
    return index_path()


def has_page():
    return os.path.isfile(index_path())


def site_doc(node):
    """What /cosplace/v1/sites says about this computer's page."""
    s = site()
    if not s.get('published') or not has_page():
        return {'cosplace': 1, 'sites': []}
    return {'cosplace': 1, 'sites': [{
        'neighborhood': s['neighborhood'], 'number': s['number'], 'title': _txt(s.get('title'), 80) or 'Untitled',
        'tagline': _txt(s.get('tagline'), 120), 'owner': _txt(s.get('owner'), 48),
        'updated': os.path.getmtime(index_path()), 'visits': counter_value()}]}


# -- counter and guestbook --------------------------------------------------------------------------
def counter_value():
    return int(_load(COUNTER_FILE, {}).get('count', 0))


def count_visit(ip):
    with _lock:
        c = _load(COUNTER_FILE, {})
        seen = c.get('seen') if isinstance(c.get('seen'), dict) else {}
        now = time.time()
        seen = {k: v for k, v in seen.items() if now - v < 3600}
        if ip not in seen:
            c['count'] = int(c.get('count', 0)) + 1
        seen[ip] = now
        c['seen'] = seen
        _save(COUNTER_FILE, c)
        return c['count']


def guestbook():
    return _load(GUESTBOOK_FILE, [])


def sign(ip, name, message, homepage=''):
    name, message = _txt(name, 40), _txt(message, 500)
    homepage = _txt(homepage, 200)
    if not homepage.startswith(('http://', 'https://')):
        homepage = ''
    if not name or not message:
        raise ValueError('a guestbook entry needs a name and a message')
    now = time.time()
    with _lock:
        if now - _gb_last.get(ip, 0) < GB_GAP:
            raise ValueError('slow down: one entry every %d seconds' % GB_GAP)
        _gb_last[ip] = now
        gb = guestbook()
        gb.insert(0, {'name': name, 'message': message, 'homepage': homepage, 'when': now, 'from': ip})
        _save(GUESTBOOK_FILE, gb[:GB_MAX])


def delete_entry(i):
    with _lock:
        gb = guestbook()
        if 0 <= i < len(gb):
            gb.pop(i)
            _save(GUESTBOOK_FILE, gb)


# -- includes ------------------------------------------------------------------------------------------
def counter_html(n):
    digits = '%06d' % n
    cells = ''.join('<td bgcolor="#000000"><font face="Courier New" color="#00ff00"><b>%s</b></font></td>' % d
                    for d in digits)
    return '<table border="1" cellpadding="2" cellspacing="0" align="center" style="display:inline-table"><tr>%s</tr></table>' % cells


def guestbook_html(action):
    e = _html.escape
    rows = []
    for g in guestbook()[:50]:
        who = e(g['name'])
        if g.get('homepage'):
            who = '<a href="%s">%s</a>' % (e(g['homepage']), who)
        rows.append('<tr><td><b>%s</b> <font size="1">%s</font><br>%s</td></tr>' % (
            who, time.strftime('%m/%d/%Y %I:%M %p', time.localtime(g.get('when', 0))), e(g['message'])))
    form = ('<form method="post" action="%s"><table><tr><td>Name:</td><td><input name="name" size="30" maxlength="40">'
            '</td></tr><tr><td>Homepage:</td><td><input name="homepage" size="30" value="http://"></td></tr>'
            '<tr><td>Message:</td><td><textarea name="message" rows="3" cols="34"></textarea></td></tr></table>'
            '<input type="submit" value="Sign my guestbook!"></form>') % e(action)
    body = ''.join(rows) or '<tr><td><i>Nobody has signed yet. Be the first!</i></td></tr>'
    return '<table width="80%%" border="1" cellpadding="6" cellspacing="0" align="center">%s</table>%s' % (body, form)


def webring_html(s, sites):
    nb = s['neighborhood']
    ring = sorted({(x['number'], x['url']) for x in sites if x['neighborhood'] == nb} |
                  {(s['number'], address(s))})
    i = [n for n, _ in ring].index(s['number'])
    prev, nxt = ring[i - 1][1], ring[(i + 1) % len(ring)][1]
    rnd = random.choice(ring)[1]
    e = _html.escape
    return ('<table border="2" cellpadding="4" align="center"><tr><td align="center"><font size="2">'
            '<a href="%s">&laquo; Prev</a> | <b>%s Ring</b> (%d page%s) | <a href="%s">Random</a> | '
            '<a href="%s">Next &raquo;</a></font></td></tr></table>') % (
        e(prev), e(nb), len(ring), '' if len(ring) == 1 else 's', e(rnd), e(nxt))


_SCRIPT = re.compile(r'<script\b.*?(</script\s*>|$)', re.I | re.S)
_ON = re.compile(r'\son[a-z]+\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)', re.I)
_JSURL = re.compile(r'(href|src|action)\s*=\s*(["\']?)\s*javascript:', re.I)


def clean_html(text):
    text = _SCRIPT.sub('', text)
    text = _ON.sub('', text)
    return _JSURL.sub(r'\1=\2#', text)


def render(s, raw, sites, guest_action, count=None):
    text = clean_html(raw)
    if '<!--#counter-->' in text:
        text = text.replace('<!--#counter-->', counter_html(counter_value() if count is None else count))
    if '<!--#guestbook-->' in text:
        text = text.replace('<!--#guestbook-->', guestbook_html(guest_action))
    if '<!--#webring-->' in text:
        text = text.replace('<!--#webring-->', webring_html(s, sites))
    if '<!--#updated-->' in text:
        try:
            when = time.strftime('%B %d, %Y', time.localtime(os.path.getmtime(index_path())))
        except OSError:
            when = 'today'
        text = text.replace('<!--#updated-->', when)
    return text


def preview():
    """Write a preview of the page with file:// links, for Webinator when not joined. Returns its path."""
    s = site()
    if not has_page():
        write_page(s)
    with open(index_path(), encoding='utf-8', errors='replace') as f:
        raw = f.read()
    text = render(s, raw, directory(None, wait=False), '#')
    ensure_art()
    text = text.replace('/cities/_art/', 'file://' + ART_DIR + '/')
    base = 'file://' + SITE_DIR + '/'
    text = re.sub(r'(href|src|background)="(?!https?:|file:|/|#|mailto:)', r'\1="' + base, text)
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    p = os.path.join(PREVIEW_DIR, 'index.html')
    with open(p, 'w') as f:
        f.write(text)
    return p


# -- clip art -----------------------------------------------------------------------------------------
ART = ('construction', 'new', 'rainbow', 'cities', 'stars', 'bubbles', 'grid', 'hearts', 'email')


def _art(name):
    from PIL import Image, ImageDraw
    from . import gfx
    out = io.BytesIO()
    if name == 'construction':
        frames = []
        for k in range(8):
            im = Image.new('RGB', (200, 50), (255, 210, 0))
            d = ImageDraw.Draw(im)
            for x in range(-60, 260, 24):
                d.polygon([(x + k * 3, 0), (x + 12 + k * 3, 0), (x - 13 + k * 3, 50), (x - 25 + k * 3, 50)], fill=(0, 0, 0))
            d.rectangle((14, 11, 186, 39), fill=(255, 210, 0), outline=(0, 0, 0), width=2)
            d.text((100, 25), 'UNDER CONSTRUCTION', font=gfx.font(13, True), fill=(0, 0, 0), anchor='mm')
            if k % 4 < 2:
                d.ellipse((4, 4, 12, 12), fill=(255, 60, 0))
                d.ellipse((188, 4, 196, 12), fill=(255, 60, 0))
            frames.append(im.convert('P', palette=Image.ADAPTIVE, colors=16))
        frames[0].save(out, 'GIF', save_all=True, append_images=frames[1:], duration=120, loop=0)
    elif name == 'new':
        frames = []
        for col in ((255, 0, 0), (255, 220, 0)):
            im = Image.new('RGB', (32, 16), col)
            ImageDraw.Draw(im).text((16, 8), 'NEW!', font=gfx.font(10, True),
                                    fill=(255, 255, 255) if col[1] == 0 else (200, 0, 0), anchor='mm')
            frames.append(im.convert('P', palette=Image.ADAPTIVE, colors=8))
        frames[0].save(out, 'GIF', save_all=True, append_images=frames[1:], duration=400, loop=0)
    elif name == 'rainbow':
        im = Image.new('RGB', (460, 8))
        d = ImageDraw.Draw(im)
        import colorsys
        for x in range(460):
            r, g, b = colorsys.hsv_to_rgb(x / 460.0, 1, 1)
            d.line([(x, 0), (x, 7)], fill=(int(r * 255), int(g * 255), int(b * 255)))
        im.save(out, 'GIF')
    elif name == 'cities':
        im = Image.new('RGB', (88, 31), (0, 0, 102))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 87, 30), outline=(255, 255, 255))
        for i, (x, h) in enumerate(((6, 14), (14, 20), (22, 11), (28, 17))):
            d.rectangle((x, 28 - h, x + 6, 28), fill=(80, 160, 255))
            for y in range(28 - h + 2, 27, 4):
                d.point((x + 2, y), fill=(255, 255, 120))
                d.point((x + 4, y), fill=(255, 255, 120))
        d.text((60, 10), 'COS', font=gfx.font(10, True), fill=(255, 255, 0), anchor='mm')
        d.text((60, 21), 'Cities', font=gfx.font(10, True), fill=(255, 255, 255), anchor='mm')
        im.save(out, 'GIF')
    elif name == 'email':
        im = Image.new('RGB', (40, 28), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.rectangle((2, 4, 37, 25), fill=(250, 250, 230), outline=(0, 0, 0))
        d.line([(2, 4), (20, 17), (37, 4)], fill=(0, 0, 0))
        im.save(out, 'GIF')
    else:                                       # background tiles
        rnd = random.Random(name)
        if name == 'stars':
            im = Image.new('RGB', (128, 128), (0, 0, 16))
            d = ImageDraw.Draw(im)
            for _ in range(40):
                x, y = rnd.randrange(128), rnd.randrange(128)
                c = rnd.choice(((255, 255, 255), (180, 180, 255), (255, 255, 160)))
                d.point((x, y), fill=c)
                if rnd.random() < 0.15:
                    d.line([(x - 1, y), (x + 1, y)], fill=c)
                    d.line([(x, y - 1), (x, y + 1)], fill=c)
        elif name == 'bubbles':
            im = Image.new('RGB', (128, 128), (0, 51, 102))
            d = ImageDraw.Draw(im)
            for _ in range(10):
                x, y, r = rnd.randrange(128), rnd.randrange(128), rnd.randrange(3, 10)
                d.ellipse((x - r, y - r, x + r, y + r), outline=(120, 200, 255))
                d.point((x - r // 2, y - r // 2), fill=(255, 255, 255))
        elif name == 'grid':
            im = Image.new('RGB', (32, 32), (0, 17, 0))
            d = ImageDraw.Draw(im)
            d.line([(0, 0), (31, 0)], fill=(0, 70, 0))
            d.line([(0, 0), (0, 31)], fill=(0, 70, 0))
        else:                                   # hearts
            im = Image.new('RGB', (64, 64), (255, 204, 238))
            d = ImageDraw.Draw(im)
            for (x, y) in ((16, 16), (48, 48)):
                d.ellipse((x - 6, y - 5, x, y + 1), fill=(255, 170, 210))
                d.ellipse((x, y - 5, x + 6, y + 1), fill=(255, 170, 210))
                d.polygon([(x - 6, y - 1), (x + 6, y - 1), (x, y + 7)], fill=(255, 170, 210))
        im.save(out, 'GIF')
    return out.getvalue()


def art(name):
    if name not in ART:
        return None
    p = os.path.join(ART_DIR, name + '.gif')
    if not os.path.isfile(p):
        os.makedirs(ART_DIR, exist_ok=True)
        data = _art(name)
        with open(p + '.tmp', 'wb') as f:
            f.write(data)
        os.replace(p + '.tmp', p)
    with open(p, 'rb') as f:
        return f.read()


def ensure_art():
    for n in ART:
        art(n)


# -- the directory --------------------------------------------------------------------------------------
def _clean_site(x, addr):
    try:
        nb = x.get('neighborhood')
        num = int(x.get('number'))
        if nb not in NEIGHBORHOOD or not 1000 <= num <= 9999:
            return None
        return {'neighborhood': nb, 'number': num, 'title': _txt(x.get('title'), 80) or 'Untitled',
                'tagline': _txt(x.get('tagline'), 120), 'owner': _txt(x.get('owner'), 48),
                'updated': float(x.get('updated') or 0), 'visits': max(0, int(x.get('visits') or 0)),
                'computer': addr, 'url': 'http://%s/cities/%s/%d/' % (addr, nb, num)}
    except (TypeError, ValueError, AttributeError):
        return None


def _fetch_sites(addr):
    try:
        req = urllib.request.Request('http://%s/cosplace/v1/sites' % addr, headers={'User-Agent': 'COSCities/1'})
        with urllib.request.urlopen(req, timeout=3) as r:
            doc = json.loads(r.read(256 * 1024))
        out = [_clean_site(x, addr) for x in (doc.get('sites') or [])[:4]]
        return [x for x in out if x]
    except Exception:
        return []


def _refresh(node):
    try:
        with node.lock:
            addrs = [a for a, p in node.peers.items() if p.get('ok')]
        found = []
        with ThreadPoolExecutor(8) as pool:
            for res in pool.map(_fetch_sites, addrs[:64]):
                found.extend(res)
        _dir_cache['sites'] = found
        _dir_cache['t'] = time.time()
    finally:
        _dir_cache['busy'] = False


def directory(node, wait=False):
    """Pages on other computers this one knows about (cached for a minute)."""
    if node is not None and node.running and time.time() - _dir_cache['t'] > 60 and not _dir_cache['busy']:
        _dir_cache['busy'] = True
        if wait:
            _refresh(node)
        else:
            threading.Thread(target=_refresh, args=(node,), daemon=True).start()
    return list(_dir_cache['sites'])


def own_entry(node):
    d = site_doc(node)['sites']
    if not d:
        return None
    x = dict(d[0])
    x['computer'] = 'this computer'
    x['url'] = address()
    return x


def directory_html(node):
    e = _html.escape
    sites = directory(node)
    me = own_entry(node)
    if me:
        sites = [me] + sites
    rows = ['<html><head><title>COS Cities directory</title></head><body bgcolor="#ffffee">',
            '<center><img src="/cities/_art/cities.gif" width="88" height="31" alt="COS Cities">',
            '<h1><font color="#000066">COS Cities</font></h1><p>Free home pages for every COS computer on '
            'The COS Place. This is the directory as %s sees it.</p></center>' % e(node.name())]
    for nb, desc in NEIGHBORHOODS:
        here = sorted((x for x in sites if x['neighborhood'] == nb), key=lambda x: x['number'])
        rows.append('<h2><font color="#990000">%s</font></h2><p><i>%s</i></p>' % (e(nb), e(desc)))
        if not here:
            rows.append('<p><font color="#888888">No pages here yet.</font></p>')
            continue
        rows.append('<ul>')
        for x in here:
            rows.append('<li><a href="%s"><b>%s</b></a> &middot; %s/%d%s%s</li>' % (
                e(x['url']), e(x['title']), e(nb), x['number'],
                (' &middot; by %s' % e(x['owner'])) if x['owner'] else '',
                (' &mdash; <i>%s</i>' % e(x['tagline'])) if x['tagline'] else ''))
        rows.append('</ul>')
    rows.append('<hr><p><font size="2">Make your own page with COS Cities in COS Neptune.</font></p></body></html>')
    return '\n'.join(rows)


# -- HTTP (called from the COS Place node's request handler) ------------------------------------------
def _send(h, code, body, ctype='text/html; charset=utf-8', extra=()):
    if isinstance(body, str):
        body = body.encode('utf-8')
    h.send_response(code)
    h.send_header('Content-Type', ctype)
    h.send_header('Content-Length', str(len(body)))
    h.send_header('Content-Security-Policy', CSP)
    h.send_header('X-Content-Type-Options', 'nosniff')
    for k, v in extra:
        h.send_header(k, v)
    h.end_headers()
    if h.command != 'HEAD':
        h.wfile.write(body)


def _not_found(h, what='That page'):
    _send(h, 404, '<html><head><title>Not found</title></head><body bgcolor="#ffffff"><h1>404</h1><p>%s is not '
                  'on this computer. Try the <a href="/cities/">COS Cities directory</a>.</p></body></html>'
          % _html.escape(what))


def handle_get(h, node, path):
    if path in ('/cosplace/v1/sites',):
        return h._json(site_doc(node))
    if path in ('/cities', '/cities/'):
        return _send(h, 200, directory_html(node))
    m = re.match(r'^/cities/_art/([a-z]+)\.gif$', path)
    if m:
        data = art(m.group(1))
        return _send(h, 200, data, 'image/gif', (('Cache-Control', 'max-age=86400'),)) if data else _not_found(h)
    s = site()
    base = address(s)
    if path == base.rstrip('/'):
        return _send(h, 301, '', extra=(('Location', base),))
    if not path.startswith(base) or not s.get('published'):
        return _not_found(h, urllib.parse.unquote(path))
    rel = urllib.parse.unquote(path[len(base):]) or 'index.html'
    if rel.endswith('/'):
        rel += 'index.html'
    parts = rel.split('/')
    if any(p in ('', '.', '..') or p.startswith('.') for p in parts):
        return _not_found(h, rel)
    full = os.path.join(SITE_DIR, *parts)
    root = os.path.realpath(SITE_DIR)
    real = os.path.realpath(full)
    ext = os.path.splitext(real)[1].lower()
    if not real.startswith(root + os.sep) or not os.path.isfile(real) or ext not in SERVE_TYPES:
        return _not_found(h, rel)
    if os.path.getsize(real) > MAX_FILE:
        return _send(h, 413, 'Too big')
    with open(real, 'rb') as f:
        data = f.read()
    if ext in ('.html', '.htm'):
        count = count_visit(h.client_address[0]) if rel == 'index.html' else None
        sites = directory(node)
        data = render(s, data.decode('utf-8', 'replace'), sites, base + '_guestbook', count)
    return _send(h, 200, data, SERVE_TYPES[ext])


def handle_post(h, node, path):
    s = site()
    base = address(s)
    if path != base + '_guestbook' or not s.get('published'):
        return _not_found(h, path)
    try:
        n = min(int(h.headers.get('Content-Length') or 0), 8192)
        form = urllib.parse.parse_qs(h.rfile.read(n).decode('utf-8', 'replace'))
        sign(h.client_address[0], (form.get('name') or [''])[0], (form.get('message') or [''])[0],
             (form.get('homepage') or [''])[0])
    except ValueError as e:
        return _send(h, 400, '<html><body bgcolor="#ffffff"><h2>Could not sign the guestbook</h2><p>%s</p>'
                             '<p><a href="%s">Back</a></p></body></html>' % (_html.escape(str(e)), _html.escape(base)))
    _send(h, 303, '', extra=(('Location', base),))
