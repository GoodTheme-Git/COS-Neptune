"""The COS Place: a peer-to-peer web of every COS machine that joins.

(Neptune's copy of COS Aurora's reference implementation, written by the Aurora
session; protocol v1, docs in ~/cos/codename_aurora/docs/COS_PLACE.md. Neptune
changes: the software name, and falling back to another port when 8643 is taken,
e.g. by COS Aurora running on the same computer.)

There is no server. Every machine that joins runs a small node:

* it serves its "shelf" (everything it published or downloaded) over HTTP
  on TCP 8643, so anything one machine has, every machine can get;
* it finds other nodes by UDP broadcast on 8644 (the LAN), by addresses the
  user typed in, and by asking every node it knows for the nodes *they* know
  (peer exchange), so the web reaches past one network;
* items are named by the sha256 of their bytes, so a copy from anyone is as
  good as the original: the hash proves it.

Items are themes, pictures, videos, music and apps -- 2000s-internet stuff.
Every item is checked to really be what it says (a theme is a clean .ctheme,
a picture opens as a picture, a video starts like a video...). Nothing from
The COS Place is ever run by it: an app only lands in Downloads, and
installing it is the user's call, with a warning.

Downloading something puts it on your shelf, which means you share it too.
The more people have a thing, the harder it is to lose: an item only one
node holds is shown as "endangered", and Rescue keeps a copy of all of them.

The wire protocol is written up in docs/COS_PLACE.md for other COS releases
(Neptune, Mobile) that want to join. Clients skip kinds they don't know, so
more kinds can be added later.
"""
import hashlib
import json
import os
import queue
import re
import socket
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import theme

PROTO = 1
PORT = 8643
DISCOVERY_PORT = 8644
BROADCAST_EVERY = 60
TIMEOUT = 3.0
MAX_PEERS = 256
MAX_ITEMS = 2000
MAX_CRAWL = 64
DIR = os.path.join(theme.STATE, 'cosplace')
STORE = os.path.join(DIR, 'store')
SHELF = os.path.join(DIR, 'shelf.json')
PEERS = os.path.join(DIR, 'peers.json')
NODE = os.path.join(DIR, 'node.json')
SHA_RE = re.compile(r'^[0-9a-f]{64}$')
MB = 1024 * 1024
KINDS = {
    'theme': {'exts': ['.ctheme'], 'max': 24 * MB, 'label': 'Theme'},
    'image': {'exts': ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'], 'max': 32 * MB, 'label': 'Picture'},
    'video': {'exts': ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.ogv'], 'max': 1024 * MB, 'label': 'Video'},
    'music': {'exts': ['.mp3', '.ogg', '.oga', '.wav', '.flac', '.m4a', '.opus'], 'max': 256 * MB, 'label': 'Music'},
    'app': {'exts': ['.nepapp', '.ceap', '.cip', '.cosapp'], 'max': 64 * MB, 'label': 'App'},
}
DOWNLOADS = os.path.join(theme.HOME, 'Downloads', 'COS Place')
CHUNK = 1 << 16


def kind_for(path):
    ext = os.path.splitext(path)[1].lower()
    for k, v in KINDS.items():
        if ext in v['exts']:
            return k, ext
    return None, ext


class ItemError(Exception):
    pass


def validate(path, kind, ext):
    """Check that the file really is a <kind>. Raises ItemError."""
    size = os.path.getsize(path)
    if kind not in KINDS or ext not in KINDS[kind]['exts']:
        raise ItemError('COS Place does not carry %s files' % (ext or 'these'))
    if not 0 < size <= KINDS[kind]['max']:
        raise ItemError('too big for The COS Place (%d MB max for a %s)' % (KINDS[kind]['max'] // MB,
                                                                          KINDS[kind]['label'].lower()))
    with open(path, 'rb') as f:
        head = f.read(64)
    if kind == 'theme':
        from . import themes
        with open(path, 'rb') as f:
            try:
                themes.read_ctheme(f.read())
            except themes.ThemeError as e:
                raise ItemError(str(e))
    elif kind == 'image':
        from PIL import Image
        try:
            with Image.open(path) as im:
                if im.format not in ('PNG', 'JPEG', 'GIF', 'WEBP', 'BMP'):
                    raise ItemError('not a picture COS can show')
                im.verify()
        except ItemError:
            raise
        except Exception:
            raise ItemError('not a picture COS can show')
    elif kind == 'video':
        ok = (head[4:8] == b'ftyp' or head[:4] == b'\x1a\x45\xdf\xa3' or
              (head[:4] == b'RIFF' and head[8:12] == b'AVI ') or head[:4] == b'OggS')
        if not ok:
            raise ItemError('does not look like a video')
    elif kind == 'music':
        ok = (head[:3] == b'ID3' or (head[0:1] == b'\xff' and (head[1] & 0xe0) == 0xe0) or head[:4] == b'OggS'
              or (head[:4] == b'RIFF' and head[8:12] == b'WAVE') or head[:4] == b'fLaC' or head[4:8] == b'ftyp')
        if not ok:
            raise ItemError('does not look like music')
    elif kind == 'app':
        if ext == '.cip':
            import zipfile
            if not zipfile.is_zipfile(path):
                raise ItemError('not a COS package')
        else:
            with open(path, 'rb') as f:
                try:
                    f.read().decode('utf-8')
                except UnicodeDecodeError:
                    raise ItemError('an app file should be text')


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(CHUNK), b''):
            h.update(chunk)
    return h.hexdigest()
SOFTWARE = '%s (COS Neptune)' % theme.FULLNAME


def _txt(v, n):
    s = str(v or '')
    return ''.join(c for c in s if c >= ' ')[:n]


def clean_item(it):
    """An item description from the network, trimmed to what we use."""
    if not isinstance(it, dict):
        return None
    sha = str(it.get('sha256', '')).lower()
    if not SHA_RE.match(sha) or it.get('kind') not in KINDS:
        return None
    ext = str(it.get('ext') or KINDS[it['kind']]['exts'][0]).lower()
    if ext not in KINDS[it['kind']]['exts']:
        return None
    try:
        size = int(it.get('size', 0))
    except (TypeError, ValueError):
        return None
    if not 0 < size <= KINDS[it['kind']]['max']:
        return None
    return {'sha256': sha, 'kind': it['kind'], 'ext': ext, 'size': size,
            'name': _txt(it.get('name'), 48) or 'Untitled', 'author': _txt(it.get('author'), 48),
            'description': _txt(it.get('description'), 400), 'published': _txt(it.get('published'), 32),
            'origin': _txt(it.get('origin'), 64)}


def _peer_addr(s):
    """'host:port' -> normalised string, or None."""
    s = str(s or '').strip()
    if not s or len(s) > 255:
        return None
    if ':' not in s:
        s += ':%d' % PORT
    host, _, port = s.rpartition(':')
    try:
        port = int(port)
    except ValueError:
        return None
    if not host or not 0 < port < 65536 or not re.match(r'^[A-Za-z0-9.\-]+$', host):
        return None
    return '%s:%d' % (host.lower(), port)


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


class Node:
    """This computer's place in The COS Place."""

    def __init__(self, settings):
        self.settings = settings
        os.makedirs(STORE, exist_ok=True)
        meta = _load(NODE, {})
        if not isinstance(meta, dict) or not re.match(r'^[0-9a-f]{16}$', str(meta.get('node', ''))):
            meta = {'node': uuid.uuid4().hex[:16]}
            _save(NODE, meta)
        self.id = meta['node']
        self.lock = threading.RLock()
        shelf = _load(SHELF, [])
        self.shelf = {}
        for it in shelf if isinstance(shelf, list) else []:
            c = clean_item(it)
            if c and os.path.isfile(self.blob(c['sha256'])):
                c['added'] = _txt(it.get('added'), 32)
                c['mine'] = bool(it.get('mine'))
                self.shelf[c['sha256']] = c
        peers = _load(PEERS, {})
        self.peers = {}                  # addr -> {'node', 'name', 'software', 'seen', 'items', 'ok'}
        for a, p in (peers.items() if isinstance(peers, dict) else []):
            a = _peer_addr(a)
            if a and isinstance(p, dict):
                self.peers[a] = {'node': _txt(p.get('node'), 16), 'name': _txt(p.get('name'), 48),
                                 'software': _txt(p.get('software'), 64), 'seen': float(p.get('seen') or 0),
                                 'items': int(p.get('items') or 0), 'ok': False}
        self.network = {}                # sha -> {'item': meta, 'holders': [addr...]}
        self.httpd = None
        self.udp = None
        self.running = False
        self.port = int(os.environ.get('COSPLACE_PORT') or PORT)
        self.crawling = False
        self.last_crawl = 0.0
        self.status = 'Not connected'
        self.events = queue.Queue()      # (kind, text) for the shell to show as notifications

    # -- local shelf ------------------------------------------------------------------------
    @staticmethod
    def blob(sha):
        return os.path.join(STORE, sha + '.blob')

    def name(self):
        return _txt(self.settings.get('computer_name') or socket.gethostname().split('.')[0], 48)

    def save_shelf(self):
        with self.lock:
            _save(SHELF, list(self.shelf.values()))

    def save_peers(self):
        with self.lock:
            _save(PEERS, {a: {k: v for k, v in p.items() if k != 'ok'} for a, p in self.peers.items()})

    def put_file(self, path, mine=False, name=None, author=None, description=None, kind=None):
        """Check a file and put it on the shelf. Returns the item."""
        k, ext = kind_for(path)
        kind = kind or k
        validate(path, kind, ext)
        tmeta = {}
        if kind == 'theme':
            from . import themes
            with open(path, 'rb') as f:
                tmeta, _files = themes.read_ctheme(f.read())
        sha = sha256_file(path)
        blob = self.blob(sha)
        if not os.path.isfile(blob):
            tmp = blob + '.tmp'
            with open(path, 'rb') as src, open(tmp, 'wb') as dst:
                for chunk in iter(lambda: src.read(CHUNK), b''):
                    dst.write(chunk)
            os.replace(tmp, blob)
        base = os.path.splitext(os.path.basename(path))[0]
        it = clean_item({'sha256': sha, 'kind': kind, 'ext': ext, 'size': os.path.getsize(blob),
                         'name': name or tmeta.get('name') or base,
                         'author': author if author is not None else tmeta.get('author', ''),
                         'description': description if description is not None else tmeta.get('description', ''),
                         'published': tmeta.get('created') or time.strftime('%Y-%m-%d %H:%M:%S'),
                         'origin': self.name() if mine else ''})
        with self.lock:
            old = self.shelf.get(sha)
            it['mine'] = bool(old and old.get('mine')) or mine
            if old and old.get('origin'):
                it['origin'] = old['origin']
            it['added'] = (old or {}).get('added') or time.strftime('%Y-%m-%d %H:%M:%S')
            self.shelf[sha] = it
            self.save_shelf()
            ent = self.network.setdefault(sha, {'item': it, 'holders': []})
            if 'self' not in ent['holders']:
                ent['holders'].insert(0, 'self')
        return it

    def publish_file(self, path, **kw):
        return self.put_file(path, mine=True, **kw)

    def save_to_downloads(self, sha):
        """Copy a shelf item into Downloads/COS Place under its own name. Returns the path."""
        it = self.shelf[sha]
        os.makedirs(DOWNLOADS, exist_ok=True)
        safe = ''.join(c if c.isalnum() or c in ' -_.()' else '_' for c in it['name']).strip(' .') or 'item'
        safe = safe[:60]
        if safe.lower().endswith(it['ext']):
            safe = safe[:-len(it['ext'])]
        dst = os.path.join(DOWNLOADS, safe + it['ext'])
        n = 2
        while os.path.exists(dst):
            if sha256_file(dst) == sha:
                return dst
            dst = os.path.join(DOWNLOADS, '%s (%d)%s' % (safe, n, it['ext']))
            n += 1
        with open(self.blob(sha), 'rb') as src, open(dst + '.part', 'wb') as out:
            for chunk in iter(lambda: src.read(CHUNK), b''):
                out.write(chunk)
        os.replace(dst + '.part', dst)
        return dst

    def remove(self, sha):
        with self.lock:
            self.shelf.pop(sha, None)
            self.save_shelf()
            ent = self.network.get(sha)
            if ent and 'self' in ent['holders']:
                ent['holders'].remove('self')
                if not ent['holders']:
                    self.network.pop(sha, None)
        try:
            os.remove(self.blob(sha))
        except OSError:
            pass


    # -- serving ---------------------------------------------------------------------------
    def hello_doc(self):
        return {'cosplace': PROTO, 'node': self.id, 'name': self.name(), 'software': SOFTWARE,
                'port': self.port, 'items': len(self.shelf)}

    def catalog_doc(self):
        with self.lock:
            items = [{k: v for k, v in it.items() if k not in ('mine', 'added')} for it in self.shelf.values()]
        return {'cosplace': PROTO, 'node': self.id, 'items': items[:MAX_ITEMS]}

    def peers_doc(self):
        now = time.time()
        with self.lock:
            live = [a for a, p in self.peers.items() if now - p.get('seen', 0) < 86400]
        return {'cosplace': PROTO, 'peers': live[:64]}

    def learn(self, addr, info=None, seen=True):
        """Remember a peer. info is its hello document."""
        addr = _peer_addr(addr)
        if not addr:
            return None
        if info and info.get('node') == self.id:
            return None                  # that's us
        with self.lock:
            p = self.peers.get(addr)
            if p is None:
                if len(self.peers) >= MAX_PEERS:
                    old = min(self.peers, key=lambda a: self.peers[a].get('seen', 0))
                    self.peers.pop(old)
                p = self.peers[addr] = {'node': '', 'name': '', 'software': '', 'seen': 0.0, 'items': 0,
                                        'ok': False}
            if info:
                p['node'] = _txt(info.get('node'), 16)
                p['name'] = _txt(info.get('name'), 48)
                p['software'] = _txt(info.get('software'), 64)
                try:
                    p['items'] = max(0, int(info.get('items') or 0))
                except (TypeError, ValueError):
                    p['items'] = 0
            if seen:
                p['seen'] = time.time()
                p['ok'] = True
        return addr

    def _handler(self):
        node = self

        class H(BaseHTTPRequestHandler):
            server_version = 'COSPlace/%d' % PROTO
            timeout = 15

            def log_message(self, *a):
                pass

            def _json(self, doc, code=200):
                raw = json.dumps(doc).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                path = self.path.split('?', 1)[0]
                if path == '/cosplace/v1/hello':
                    return self._json(node.hello_doc())
                if path == '/cosplace/v1/catalog':
                    return self._json(node.catalog_doc())
                if path == '/cosplace/v1/peers':
                    return self._json(node.peers_doc())
                if path == '/cosplace/v1/sites' or path == '/cities' or path.startswith('/cities/'):
                    from . import cities       # COS Cities home pages (a Neptune extension; others 404)
                    try:
                        return cities.handle_get(self, node, path)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                m = re.match(r'^/cosplace/v1/item/([0-9a-f]{64})$', path)
                if m and m.group(1) in node.shelf:
                    try:
                        f = open(node.blob(m.group(1)), 'rb')
                    except OSError:
                        return self._json({'error': 'gone'}, 404)
                    with f:
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/octet-stream')
                        self.send_header('Content-Length', str(os.fstat(f.fileno()).st_size))
                        self.end_headers()
                        try:
                            for chunk in iter(lambda: f.read(CHUNK), b''):
                                self.wfile.write(chunk)
                        except (BrokenPipeError, ConnectionResetError):
                            pass
                    return
                if path in ('/', '/cosplace', '/cosplace/'):
                    raw = ('<html><body><h1>The COS Place</h1><p>%s is part of The COS Place, the '
                           'peer-to-peer web of COS computers, sharing %d item(s). Open The COS Place in '
                           'COS to look around.</p><p><a href="/cities/">COS Cities home pages</a></p></body></html>'
                           % (node.name(), len(node.shelf))).encode('utf-8')
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/html; charset=utf-8')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                self._json({'error': 'not found'}, 404)

            def do_POST(self):
                if self.path.startswith('/cities/'):
                    from . import cities
                    return cities.handle_post(self, node, self.path.split('?', 1)[0])
                if self.path.split('?', 1)[0] != '/cosplace/v1/hello':
                    return self._json({'error': 'not found'}, 404)
                try:
                    n = min(int(self.headers.get('Content-Length') or 0), 4096)
                    body = json.loads(self.rfile.read(n).decode('utf-8') or '{}')
                    port = int(body.get('port') or PORT)
                except (ValueError, UnicodeDecodeError, AttributeError):
                    return self._json({'error': 'bad request'}, 400)
                # the caller told us its port; the address is whoever connected. Mark it seen
                # only once we have reached it ourselves (next crawl), so nobody can fake peers.
                node.learn('%s:%d' % (self.client_address[0], port), None, seen=False)
                self._json(node.hello_doc())

        return H

    def start(self):
        if self.running:
            return True
        err = None
        for port in [self.port] + [p for p in range(PORT + 2, PORT + 10) if p != self.port]:
            try:
                self.httpd = ThreadingHTTPServer(('0.0.0.0', port), self._handler())
                self.port = port         # announced in hello and broadcasts, so others still find us
                break
            except OSError as e:
                err = err or e
        else:
            self.status = 'Could not open port %d: %s' % (self.port, err.strerror or err)
            return False
        self.httpd.daemon_threads = True
        self.running = True
        threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.5}, daemon=True,
                         name='cosplace-http').start()
        threading.Thread(target=self._discovery, daemon=True, name='cosplace-udp').start()
        for a in self.settings.get('cosplace_peers') or []:
            self.learn(a, seen=False)
        self.status = 'Joined: sharing %d item(s) on port %d' % (len(self.shelf), self.port)
        self.crawl_async()
        return True

    def stop(self):
        self.running = False
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
            self.httpd = None
        if self.udp is not None:
            try:
                self.udp.close()
            except OSError:
                pass
            self.udp = None
        self.status = 'Not connected'

    # -- discovery (UDP broadcast) --------------------------------------------------------------
    def _discovery(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind(('', DISCOVERY_PORT))
            s.settimeout(1.0)
        except OSError:
            return                        # someone else has the port; manual peers + exchange still work
        self.udp = s
        last = 0.0
        while self.running:
            now = time.time()
            if now - last > BROADCAST_EVERY:
                last = now
                msg = json.dumps({'cosplace': PROTO, 'node': self.id, 'port': self.port, 'name': self.name()}).encode()
                try:
                    s.sendto(msg, ('<broadcast>', DISCOVERY_PORT))
                except OSError:
                    pass
            try:
                data, (ip, _p) = s.recvfrom(2048)
            except (socket.timeout, OSError):
                continue
            try:
                doc = json.loads(data.decode('utf-8'))
                if doc.get('cosplace') and doc.get('node') != self.id:
                    addr = self.learn('%s:%d' % (ip, int(doc.get('port') or PORT)), None, seen=False)
                    if addr and addr not in self.network_holders():
                        self.crawl_async()
            except (ValueError, UnicodeDecodeError, AttributeError, TypeError):
                pass

    def network_holders(self):
        out = set()
        for ent in self.network.values():
            out.update(ent['holders'])
        return out

    # -- crawling the web ------------------------------------------------------------------------
    def _get(self, addr, path, limit=1 << 20):
        req = urllib.request.Request('http://%s%s' % (addr, path), headers={'User-Agent': 'COSPlace/%d' % PROTO})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError('too big')
        return data

    def _visit(self, addr):
        """Hello + catalog + peers from one node. Returns (addr, hello, items, peers) or None."""
        try:
            hello = json.loads(self._get(addr, '/cosplace/v1/hello'))
            if not isinstance(hello, dict) or not hello.get('cosplace'):
                return None
            if hello.get('node') == self.id:
                return (addr, 'self', [], [])
            cat = json.loads(self._get(addr, '/cosplace/v1/catalog', 4 << 20))
            peers = json.loads(self._get(addr, '/cosplace/v1/peers'))
        except Exception:
            return None
        items = [c for c in (clean_item(i) for i in (cat.get('items') or [])[:MAX_ITEMS]) if c]
        plist = [p for p in (_peer_addr(a) for a in (peers.get('peers') or [])[:64]) if p]
        if self.running:
            try:                          # tell it about us so it can find us back
                body = json.dumps({'cosplace': PROTO, 'node': self.id, 'port': self.port, 'name': self.name()}).encode()
                req = urllib.request.Request('http://%s/cosplace/v1/hello' % addr, data=body,
                                             headers={'Content-Type': 'application/json'})
                urllib.request.urlopen(req, timeout=TIMEOUT).read(4096)
            except Exception:
                pass
        return (addr, hello, items, plist)

    def crawl(self):
        """Walk the web: every known peer, then the peers they know, up to MAX_CRAWL nodes."""
        with self.lock:
            if self.crawling:
                return
            self.crawling = True
            todo = list(self.peers)
        self.status = 'Looking around The COS Place...'
        seen = set()
        network = {}
        with self.lock:
            for sha, it in self.shelf.items():
                network[sha] = {'item': it, 'holders': ['self']}
            for p in self.peers.values():
                p['ok'] = False
        alive = 0
        nodes = {}                        # node id -> the address we count it under
        try:
            with ThreadPoolExecutor(8) as pool:
                while todo and len(seen) < MAX_CRAWL:
                    batch = [a for a in todo if a not in seen][:MAX_CRAWL - len(seen)]
                    todo = []
                    seen.update(batch)
                    for res in pool.map(self._visit, batch):
                        if res and res[1] == 'self':
                            with self.lock:       # one of our own addresses: don't list it as a computer
                                self.peers.pop(res[0], None)
                            continue
                        if not res:
                            continue
                        addr, hello, items, plist = res
                        self.learn(addr, hello)
                        nid = hello.get('node') or addr
                        if nodes.setdefault(nid, addr) != addr:
                            continue              # the same computer under another address: count it once
                        alive += 1
                        for it in items:
                            ent = network.setdefault(it['sha256'], {'item': it, 'holders': []})
                            if addr not in ent['holders']:
                                ent['holders'].append(addr)
                        for p in plist:
                            if p not in seen:
                                self.learn(p, seen=False)
                                todo.append(p)
        finally:
            with self.lock:
                self.network = network
                self.crawling = False
                self.last_crawl = time.time()
            self.save_peers()
        self.status = ('Connected to %d computer%s, %d item%s on the web'
                       % (alive, '' if alive == 1 else 's', len(network), '' if len(network) == 1 else 's'))
        self.events.put(('crawl', self.status))

    def crawl_async(self):
        threading.Thread(target=self.crawl, daemon=True, name='cosplace-crawl').start()

    # -- getting things --------------------------------------------------------------------------
    def _download(self, addr, sha, limit, progress=None):
        """Stream an item into a temp file, hashing as it comes. Returns the temp path."""
        req = urllib.request.Request('http://%s/cosplace/v1/item/%s' % (addr, sha),
                                     headers={'User-Agent': 'COSPlace/%d' % PROTO})
        tmp = os.path.join(STORE, '%s.%s.part' % (sha, uuid.uuid4().hex[:6]))
        h = hashlib.sha256()
        got = 0
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT * 4) as r, open(tmp, 'wb') as out:
                for chunk in iter(lambda: r.read(CHUNK), b''):
                    got += len(chunk)
                    if got > limit:
                        raise ItemError('sent more than the item is supposed to be')
                    h.update(chunk)
                    out.write(chunk)
                    if progress:
                        progress(got)
            if h.hexdigest() != sha:
                raise ItemError('sent a copy that does not match its hash')
            return tmp
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def fetch(self, sha, progress=None):
        """Get an item from whoever has it, check its hash and contents, and keep it
        on the shelf (so this computer shares it too). Returns the item."""
        if sha in self.shelf:
            return self.shelf[sha]
        ent = self.network.get(sha)
        if not ent:
            raise LookupError('nobody on The COS Place has that right now')
        it = ent['item']
        errors = []
        for addr in [h for h in ent['holders'] if h != 'self']:
            try:
                tmp = self._download(addr, sha, min(it['size'], KINDS[it['kind']]['max']), progress)
            except Exception as e:
                errors.append('%s: %s' % (addr, e))
                continue
            try:
                named = tmp + it['ext']
                os.replace(tmp, named)
                got = self.put_file(named, name=it['name'], author=it['author'], description=it['description'],
                                    kind=it['kind'])
            except Exception as e:
                errors.append('%s: %s' % (addr, e))
                continue
            finally:
                for p in (tmp, tmp + it['ext']):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
            with self.lock:
                got['origin'] = it.get('origin') or got.get('origin')
                got['published'] = it.get('published') or got.get('published')
                self.save_shelf()
            return got
        raise IOError('could not get it from anyone: ' + ('; '.join(errors) or 'no holders'))

    def endangered(self):
        """Items on the web that only one computer holds (and it isn't us)."""
        return [e for e in self.network.values() if len(e['holders']) == 1 and e['holders'][0] != 'self']


_node = None


def get(settings):
    global _node
    if _node is None:
        _node = Node(settings)
    return _node


def running():
    return _node is not None and _node.running
