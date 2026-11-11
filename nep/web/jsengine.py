"""Page JavaScript for Webinator, run by Deno in a sandbox.

One Deno process per page runs nep/web/jsrt/runtime.js, which gives page
scripts a small browser (DOM, events, history, storage, fetch, WebSocket).
The process may use the network and nothing else: no files, no programs, no
environment. Webinator and the runtime talk in JSON lines over stdin/stdout
(the message list is at the top of runtime.js).

Deno can't keep a site's localStorage itself (that would need file access),
so this side keeps it, one file per site in .neptune/web/storage. Cookies live
in Webinator's one cookie jar (nep/web/cookies.py) with their full rules.

Cookies and scripts: the runtime only ever sees the cookies document.cookie
may show (no HttpOnly ones). A script request that should carry cookies
(same-origin, or credentials: 'include') is sent here as a 'fetch' message
and made by Python with the jar, so HttpOnly values never enter the Deno
process at all. Requests without cookies go straight from Deno.
Deno's module cache lives in neptune/cache/deno.

If Deno isn't installed, available() is False and Webinator stays static.
"""
import atexit
import base64
import json
import os
import weakref
import queue
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import theme
from . import cookies

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.join(HERE, 'jsrt', 'runtime.js')
NEPTUNE = os.path.dirname(os.path.dirname(HERE))
DENO_DIR = os.path.join(NEPTUNE, 'cache', 'deno')
STORE_DIR = os.path.join(theme.STATE, 'web', 'storage')
LOG = os.path.join(theme.STATE, 'web', 'js-console.log')
LOG_MAX = 512 * 1024
_engines = weakref.WeakSet()
STORE_MAX = 5 * 1024 * 1024


def deno_path():
    for p in (os.environ.get('NEPTUNE_DENO'), shutil.which('deno'), os.path.expanduser('~/.deno/bin/deno')):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def available():
    return deno_path() is not None and os.path.isfile(RUNTIME)


def origin_of(url):
    u = urllib.parse.urlsplit(url)
    return '%s://%s' % (u.scheme, u.netloc)


def _store_file(origin):
    safe = re.sub(r'[^A-Za-z0-9.-]+', '_', origin)
    return os.path.join(STORE_DIR, safe + '.json')


def load_store(origin):
    try:
        with open(_store_file(origin)) as f:
            d = json.load(f)
        return {'local': dict(d.get('local') or {})}
    except (OSError, ValueError):
        return {'local': {}}


def save_store(origin, store):
    os.makedirs(STORE_DIR, exist_ok=True)
    p = _store_file(origin)
    tmp = p + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)     # it can hold login tokens
    with os.fdopen(fd, 'w') as f:
        json.dump(store, f)
    os.replace(tmp, p)


UA = 'Mozilla/5.0 (X11; Linux x86_64) COS-Webinator/%s (Neptune)' % theme.VERSION
FETCH_MAX = 32 * 1024 * 1024
# headers a page script may not set (the browser owns them)
FORBIDDEN = {'cookie', 'cookie2', 'host', 'content-length', 'connection', 'transfer-encoding', 'keep-alive', 'te',
             'trailer', 'upgrade', 'accept-encoding', 'expect', 'via', 'proxy-authorization', 'origin', 'referer'}
_fetch_slots = threading.BoundedSemaphore(6)


class CookieHandler(urllib.request.BaseHandler):
    """urllib handler that sends and keeps cookies through Webinator's jar,
    with SameSite judged against the page that caused the request. Runs for
    every hop of a redirect."""
    handler_order = 400

    def __init__(self, initiator=None, kind='navigate'):
        self.initiator = initiator
        self.kind = kind

    def http_request(self, req):
        if not req.has_header('Cookie'):
            hdr = cookies.jar().header_for(req.full_url, self.initiator, self.kind, req.get_method())
            if hdr:
                req.add_unredirected_header('Cookie', hdr)
        return req

    def http_response(self, req, resp):
        cookies.jar().set_from_headers(resp.headers, req.full_url)
        return resp

    https_request = http_request
    https_response = http_response


def log(line):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + '.old')
        with open(LOG, 'a') as f:
            f.write(line.rstrip('\n') + '\n')
    except OSError:
        pass


class JSEngine:
    """Runs one page's scripts. send() events in, poll() messages out."""

    def __init__(self, url, html, width, height, insecure=False):
        self.url = url
        self.origin = origin_of(url)
        self.q = queue.Queue()
        self.alive = True
        self.store = load_store(self.origin)
        self.store_dirty = False
        self.store_t = 0
        self.lock = threading.Lock()
        deno = deno_path()
        if deno is None:
            raise RuntimeError('Deno is not installed')
        os.makedirs(DENO_DIR, exist_ok=True)
        cmd = [deno, 'run', '--quiet', '--no-prompt', '--no-config', '--reload',
               '--allow-net', '--allow-import', '--v8-flags=--max-old-space-size=768']
        if insecure:
            cmd.append('--unsafely-ignore-certificate-errors')
        cmd.append(RUNTIME)
        env = {'DENO_DIR': DENO_DIR, 'PATH': '/usr/bin:/bin', 'NO_COLOR': '1', 'DENO_NO_UPDATE_CHECK': '1',
               'HOME': DENO_DIR}
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env, cwd=DENO_DIR, start_new_session=True)
        _engines.add(self)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()
        log('--- %s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), url))
        self.insecure = insecure
        vis, hidden = cookies.jar().script_view(url)
        self.send({'t': 'load', 'url': url, 'html': html, 'width': width, 'height': height,
                   'storage': self.store['local'], 'cookies': vis, 'httponly': hidden})

    @property
    def pid(self):
        return self.p.pid

    def _read(self):
        try:
            for line in self.p.stdout:
                try:
                    self.q.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.alive = False
            self.q.put({'t': 'exit', 'code': self.p.poll()})

    def _read_err(self):
        for line in self.p.stderr:
            log('[deno] ' + line.decode('utf-8', 'replace'))

    def send(self, msg):
        if not self.alive:
            return
        data = (json.dumps(msg) + '\n').encode()
        try:
            with self.lock:
                self.p.stdin.write(data)
                self.p.stdin.flush()
        except (OSError, ValueError):
            self.alive = False

    def poll(self):
        """Messages since the last poll. Storage and console messages are handled here."""
        out = []
        while True:
            try:
                m = self.q.get_nowait()
            except queue.Empty:
                break
            t = m.get('t')
            if t == 'storage':
                self._storage(m)
            elif t == 'setcookie':
                cookies.jar().set_cookie(str(m.get('text', '')), self.url, from_script=True)
                self.push_cookies()
            elif t == 'fetch':
                threading.Thread(target=self._fetch, args=(m,), daemon=True).start()
            elif t in ('console', 'error'):
                log('[%s] %s' % (m.get('level', 'error'), m.get('text', '')))
                if t == 'error':
                    out.append(m)
            else:
                out.append(m)
        if self.store_dirty and time.time() - self.store_t > 0.5:
            self.flush_store()
        return out

    def push_cookies(self):
        """Tell the runtime what document.cookie shows now (never HttpOnly values)."""
        vis, hidden = cookies.jar().script_view(self.url)
        self.send({'t': 'cookies', 'cookies': vis, 'httponly': hidden})

    def _fetch(self, m):
        """Make a script's cookie-carrying request here, so HttpOnly cookies are
        sent and received without the page's scripts ever holding them."""
        rid = m.get('id')
        url = str(m.get('url', ''))
        out = {'t': 'fetchres', 'id': rid}
        try:
            if urllib.parse.urlsplit(url).scheme not in ('http', 'https'):
                raise ValueError('only http and https')
            body = base64.b64decode(m['body']) if m.get('body') else None
            method = str(m.get('method') or 'GET').upper()
            req = urllib.request.Request(url, data=body, method=method)
            req.add_header('User-Agent', UA)
            for k, v in m.get('headers') or []:
                if str(k).lower() not in FORBIDDEN and not str(k).lower().startswith(('sec-', 'proxy-')):
                    req.add_header(str(k), str(v))
            req.add_header('Origin', self.origin)
            req.add_header('Referer', self.url)
            import ssl
            ctx = ssl.create_default_context()
            if self.insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                                 CookieHandler(self.url, 'subresource'))
            with _fetch_slots:
                try:
                    resp = opener.open(req, timeout=60)
                except urllib.error.HTTPError as e:
                    resp = e                 # still a response, with a status and body
                with resp:
                    data = resp.read(FETCH_MAX)
                    out.update(status=resp.status if hasattr(resp, 'status') else resp.code,
                               statusText=getattr(resp, 'reason', '') or '', url=resp.geturl(),
                               headers=[[k, v] for k, v in resp.headers.items() if k.lower() != 'set-cookie'],
                               body=base64.b64encode(data).decode('ascii'))
        except Exception as e:
            out['error'] = '%s: %s' % (type(e).__name__, getattr(e, 'reason', e))
        self.send(out)
        self.push_cookies()

    def _storage(self, m):
        if m.get('area') != 'local':
            return                           # cookies go through the jar now
        d = self.store['local']
        if m.get('clear'):
            d.clear()
        elif m.get('value') is None:
            d.pop(m.get('key'), None)
        else:
            d[str(m.get('key'))] = str(m.get('value'))
        if sum(len(k) + len(v) for k, v in d.items()) > STORE_MAX:
            d.pop(str(m.get('key')), None)
        self.store_dirty = True
        self.store_t = time.time()

    def flush_store(self):
        if self.store_dirty:
            self.store_dirty = False
            try:
                save_store(self.origin, self.store)
            except OSError:
                pass

    def kill(self):
        self.flush_store()
        if self.p.poll() is None:
            self.p.kill()

    def close(self):
        self.flush_store()
        self.alive = False
        p = self.p
        if p.poll() is not None:
            return
        try:
            p.stdin.close()
        except OSError:
            pass

        def reap():
            try:
                p.wait(1.0)
            except subprocess.TimeoutExpired:
                p.kill()
                try:
                    p.wait(2.0)
                except subprocess.TimeoutExpired:
                    pass
        threading.Thread(target=reap, daemon=True).start()


@atexit.register
def _kill_all():
    for e in list(_engines):
        try:
            e.kill()
        except Exception:
            pass
