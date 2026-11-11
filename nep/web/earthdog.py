"""EarthDog: Mozilla Firefox for COS Neptune.

The web engine for anything in Neptune that needs real websites. One
headless Firefox runs in the background (started on first use, stopped a
while after the last page closes). Neptune drives it over WebDriver BiDi,
Firefox's standard remote-control protocol, on a local socket:

    tab = earthdog.Tab(width, height)      # a page; returns at once
    tab.navigate('https://example.com')
    tab.frame, tab.frame_seq               # latest picture of the page (PIL RGB)
    tab.mouse('down', x, y, button) / tab.wheel(...) / tab.key(ev)
    tab.url, tab.title, tab.loading, tab.cursor, tab.hover_link
    tab.events                             # prompts, popups, downloads, errors
    tab.close()

Webinator uses it for http/https pages. Firefox is used as it is: its own
user agent and its usual automation marker, nothing disguised. Its profile
(cookies, logins, cache) lives in .neptune/earthdog/profile.

From a terminal: python3 -m nep.web.earthdog text|html|shot URL [out.png]
(neptune/bin/earthdog does the same).
"""
import atexit
import base64
import collections
import io
import json
import os
import queue
import re
import shutil
import signal
import socket
import struct
import subprocess
import tempfile
import threading
import time

from PIL import Image

from .. import theme

HOME_DIR = os.path.join(theme.STATE, 'earthdog')
PROFILE = os.path.join(HOME_DIR, 'profile')
LOG = os.path.join(HOME_DIR, 'earthdog.log')
LOG_MAX = 512 * 1024
DOWNLOADS = os.path.join(theme.HOME, 'Downloads')
IDLE_QUIT = 120          # seconds with no pages before Firefox is stopped
ORPHAN = 20              # a page nobody has looked at for this long closes itself

PREFS = {
    'browser.shell.checkDefaultBrowser': False,
    'browser.startup.page': 0,
    'browser.startup.homepage': 'about:blank',
    'browser.startup.homepage_override.mstone': 'ignore',
    'startup.homepage_welcome_url': '',
    'startup.homepage_welcome_url.additional': '',
    'browser.aboutwelcome.enabled': False,
    'browser.tabs.warnOnClose': False,
    'browser.sessionstore.resume_from_crash': False,
    'browser.link.open_newwindow': 2,                 # popups and target=_blank: their own window
    'browser.link.open_newwindow.restriction': 0,
    'datareporting.policy.dataSubmissionEnabled': False,
    'datareporting.policy.dataSubmissionPolicyBypassNotification': True,
    'datareporting.healthreport.uploadEnabled': False,
    'toolkit.telemetry.reportingpolicy.firstRun': False,
    'app.update.auto': False,
    'browser.download.folderList': 2,
    'browser.download.dir': DOWNLOADS,
    'browser.download.useDownloadDir': True,
    'browser.download.always_ask_before_handling_new_types': False,
    'browser.download.alwaysOpenPanel': False,
    'remote.prefs.recommended': False,                # an everyday browser, not a test profile
}

KEYS = {'enter': '', 'return': '', 'kp_enter': '', 'backspace': '', 'tab': '',
        'escape': '', 'delete': '', 'insert': '', 'home': '', 'end': '',
        'pageup': '', 'pagedown': '', 'left': '', 'up': '', 'right': '',
        'down': '', 'space': ' '}
KEYS.update({'f%d' % i: chr(0xE031 + i - 1) for i in range(1, 13)})
MODS = (('ctrl', ''), ('alt', ''), ('shift', ''), ('meta', ''))

HOVER_JS = r"""(() => {
  const e = document.elementFromPoint(%f, %f);
  if (!e) return '';
  let c = getComputedStyle(e).cursor;
  const a = e.closest('a[href], area[href]');
  if (c === 'auto') {
    if (a || e.closest('button, [role=button], summary, label, select')) c = 'pointer';
    else if (e.closest('textarea, [contenteditable=""], [contenteditable=true]') ||
             (e.tagName === 'INPUT' && !/^(button|submit|reset|checkbox|radio|image|file|range|color)$/i.test(e.type))) c = 'text';
  }
  return JSON.stringify([c, a ? a.href : '']);
})()"""

INFO_JS = r"""JSON.stringify([document.title, location.href, history.length,
  window.navigation ? navigation.canGoBack : null, window.navigation ? navigation.canGoForward : null])"""

SELECTION_JS = r"""(() => {
  const a = document.activeElement;
  if (a && (a.tagName === 'TEXTAREA' || a.tagName === 'INPUT') && a.selectionStart != null)
    return a.value.substring(a.selectionStart, a.selectionEnd);
  return String(getSelection());
})()"""


def firefox_path():
    for p in (os.environ.get('NEPTUNE_FIREFOX'), shutil.which('firefox'), shutil.which('firefox-esr')):
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def available():
    return firefox_path() is not None


def log(line):
    try:
        os.makedirs(HOME_DIR, exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + '.old')
        with open(LOG, 'a') as f:
            f.write(time.strftime('%H:%M:%S ') + line.rstrip('\n') + '\n')
    except OSError:
        pass


class BidiError(Exception):
    pass


# ---------------------------------------------------------------------------------------------
# a WebSocket client, just enough for a local BiDi connection
# ---------------------------------------------------------------------------------------------
class _Socket:
    def __init__(self, port, path='/session'):
        self.s = socket.create_connection(('127.0.0.1', port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(('GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                        'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n' % (path, port, key)).encode())
        buf = b''
        while b'\r\n\r\n' not in buf:
            d = self.s.recv(4096)
            if not d:
                raise ConnectionError('Firefox closed the connection')
            buf += d
        head, self.buf = buf.split(b'\r\n\r\n', 1)
        if b' 101 ' not in head.split(b'\r\n')[0]:
            raise ConnectionError(head.split(b'\r\n')[0].decode(errors='replace'))
        self.s.settimeout(None)
        self.lock = threading.Lock()

    def send(self, obj, opcode=0x1):
        data = json.dumps(obj).encode() if opcode == 0x1 else obj
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('>Q', n)
        mask = os.urandom(4)
        body = bytes(b ^ mask[i & 3] for i, b in enumerate(data)) if n < 4096 else _mask(data, mask)
        with self.lock:
            self.s.sendall(head + mask + body)

    def _take(self, n):
        while len(self.buf) < n:
            d = self.s.recv(1 << 20)
            if not d:
                raise ConnectionError('closed')
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        msg = b''
        while True:
            a, b = self._take(2)
            n = b & 127
            if n == 126:
                n = struct.unpack('>H', self._take(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._take(8))[0]
            data = self._take(n)
            op = a & 0x0F
            if op == 0x8:
                raise ConnectionError('closed')
            if op == 0x9:
                self.send(data, 0xA)
                continue
            if op == 0xA:
                continue
            msg += data
            if a & 0x80:
                return json.loads(msg)

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def _mask(data, mask):
    import numpy as np
    arr = np.frombuffer(data, dtype=np.uint8)
    m = np.resize(np.frombuffer(mask, dtype=np.uint8), arr.shape)
    return (arr ^ m).tobytes()


# ---------------------------------------------------------------------------------------------
# the Firefox process
# ---------------------------------------------------------------------------------------------
class Engine:
    _inst = None
    _ilock = threading.Lock()

    @classmethod
    def get(cls):
        with cls._ilock:
            if cls._inst is None or not cls._inst.alive:
                cls._inst = Engine(PROFILE)
            return cls._inst

    @classmethod
    def running(cls):
        return cls._inst is not None and cls._inst.alive

    def __init__(self, profile, keep_running=True):
        ff = firefox_path()
        if not ff:
            raise RuntimeError('Firefox is not installed')
        self.profile = profile
        self.keep_running = keep_running
        self.alive = False
        self.tabs = {}
        self.pending = {}
        self.nid = 0
        self.lock = threading.Lock()
        self.unclaimed = {}            # new top-level contexts nobody asked for (popups): ctx -> (t, opener)
        self.spare = set()
        self.creating = 0              # our own browsingContext.create calls still in flight
        self.last_tab_t = time.time()
        self.last_active = None
        os.makedirs(profile, mode=0o700, exist_ok=True)
        os.makedirs(DOWNLOADS, exist_ok=True)
        for stale in ('lock', '.parentlock'):          # left behind if a previous Firefox was killed
            try:
                os.unlink(os.path.join(profile, stale))
            except OSError:
                pass
        with open(os.path.join(profile, 'user.js'), 'w') as f:
            for k, v in PREFS.items():
                f.write('user_pref(%s, %s);\n' % (json.dumps(k), json.dumps(v)))
        env = dict(os.environ)
        env['MOZ_HEADLESS'] = '1'
        env.pop('MOZ_REMOTE_SETTINGS_DEVTOOLS', None)
        log('starting Firefox (%s)' % ff)
        self.p = subprocess.Popen([ff, '--headless', '--no-remote', '--profile', profile,
                                   '--remote-debugging-port', '0', '--width', '1280', '--height', '800'],
                                  stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  env=env, start_new_session=True)
        lines = queue.Queue()

        def drain():
            for raw in self.p.stdout:
                line = raw.decode('utf-8', 'replace')
                lines.put(line)
                if 'JavaScript error' not in line and 'console.' not in line:
                    log('[firefox] ' + line)
            lines.put(None)
        threading.Thread(target=drain, daemon=True).start()
        port, t_end = None, time.time() + 30
        while port is None and time.time() < t_end:
            try:
                line = lines.get(timeout=max(0.1, t_end - time.time()))
            except queue.Empty:
                break
            if line is None:
                break
            m = re.search(r'WebDriver BiDi listening on ws://[\d.]+:(\d+)', line)
            if m:
                port = int(m.group(1))
        if port is None:
            self.kill()
            raise RuntimeError('Firefox did not start (see %s)' % LOG)
        self.ws = _Socket(port)
        self.alive = True
        threading.Thread(target=self._read, daemon=True).start()
        self.call('session.new', {'capabilities': {'alwaysMatch': {
            'unhandledPromptBehavior': {'default': 'ignore'}}}})
        self.call('session.subscribe', {'events': [
            'browsingContext.contextCreated', 'browsingContext.contextDestroyed',
            'browsingContext.navigationStarted', 'browsingContext.domContentLoaded', 'browsingContext.load',
            'browsingContext.fragmentNavigated', 'browsingContext.userPromptOpened']})
        for ev in ('browsingContext.historyUpdated', 'browsingContext.downloadWillBegin',
                   'browsingContext.downloadEnd', 'browsingContext.navigationFailed'):
            try:                                      # newer events; fine if this Firefox lacks them
                self.call('session.subscribe', {'events': [ev]})
            except BidiError:
                pass
        for c in self.call('browsingContext.getTree', {}).get('contexts', []):
            self.spare.add(c['context'])               # Firefox's own first window stays blank
        _engines.add(self)
        threading.Thread(target=self._watch, daemon=True).start()
        log('Firefox ready on port %d (pid %d)' % (port, self.p.pid))

    # -- protocol --------------------------------------------------------------------------
    def _read(self):
        try:
            while True:
                m = self.ws.recv()
                if 'id' in m and m.get('id') is not None:
                    with self.lock:
                        w = self.pending.pop(m['id'], None)
                    if w:
                        w[1]['m'] = m
                        w[0].set()
                elif m.get('type') == 'event':
                    try:
                        self._event(m.get('method', ''), m.get('params') or {})
                    except Exception as e:
                        log('event error: %r' % e)
        except Exception as e:
            log('connection ended: %r' % e)
        finally:
            self.alive = False
            with self.lock:
                waiting, self.pending = list(self.pending.values()), {}
            for ev, box in waiting:
                box['m'] = {'type': 'error', 'error': 'closed', 'message': 'Firefox stopped'}
                ev.set()
            for t in list(self.tabs.values()):
                t._gone('Firefox stopped')
            self.kill()

    def call(self, method, params, timeout=30):
        if not self.alive and method != 'session.new':
            raise BidiError('Firefox is not running')
        ev, box = threading.Event(), {}
        with self.lock:
            self.nid += 1
            i = self.nid
            self.pending[i] = (ev, box)
        try:
            self.ws.send({'id': i, 'method': method, 'params': params})
        except OSError as e:
            with self.lock:
                self.pending.pop(i, None)
            raise BidiError(str(e))
        if not ev.wait(timeout):
            with self.lock:
                self.pending.pop(i, None)
            raise BidiError('%s timed out' % method)
        m = box['m']
        if m.get('type') == 'error':
            raise BidiError('%s: %s' % (m.get('error'), m.get('message', '')))
        return m.get('result') or {}

    def evaluate(self, ctx, expr, timeout=5, sandbox=True):
        target = {'context': ctx}
        if sandbox:
            target['sandbox'] = 'earthdog'
        r = self.call('script.evaluate', {'expression': expr, 'target': target, 'awaitPromise': True,
                                          'resultOwnership': 'none'}, timeout)
        if r.get('type') == 'exception':
            raise BidiError((r.get('exceptionDetails') or {}).get('text', 'script error'))
        return (r.get('result') or {}).get('value')

    def _event(self, method, p):
        ctx = p.get('context')
        tab = self.tabs.get(ctx)
        name = method.split('.', 1)[-1]
        if name == 'contextCreated':
            if p.get('parent') is None and ctx not in self.tabs and ctx not in self.spare:
                self.unclaimed[ctx] = (time.time(), p.get('originalOpener'))
            return
        if name == 'contextDestroyed':
            self.unclaimed.pop(ctx, None)
            if tab:
                tab._gone(None)
            return
        if tab is None:
            if not (name.startswith('download') and self.last_active):
                return
            tab = self.last_active
        if name == 'navigationStarted':
            tab._nav(p.get('url'), True)
        elif name in ('domContentLoaded', 'fragmentNavigated', 'historyUpdated'):
            tab._nav(p.get('url'), None)
        elif name == 'load':
            tab._nav(p.get('url'), False)
        elif name == 'navigationFailed':
            tab._nav(None, False)
        elif name == 'userPromptOpened':
            tab.events.append(('prompt', p))
        elif name == 'downloadWillBegin':
            tab.events.append(('download', p.get('suggestedFilename') or p.get('url', '')))
        elif name == 'downloadEnd':
            tab.events.append(('downloaded', p.get('filepath') or '', p.get('status', 'complete')))

    def _watch(self):
        while self.alive:
            time.sleep(0.3)
            now = time.time()
            for ctx, (t, opener) in list(self.unclaimed.items()):
                if ctx in self.tabs:
                    self.unclaimed.pop(ctx, None)
                elif opener in self.tabs and now - t > 0.3:
                    self.unclaimed.pop(ctx, None)       # a page opened a window: window.open, target=_blank
                    owner = self.tabs[opener]
                    if not owner.closed:
                        owner.events.append(('popup', ctx))
                elif not opener and now - t > 5 and not self.creating:
                    self.unclaimed.pop(ctx, None)       # a window Firefox opened by itself (first-run pages...)
                    log('closing a window nobody asked for')
                    try:
                        self.call('browsingContext.close', {'context': ctx}, 5)
                    except BidiError:
                        pass
            for t in list(self.tabs.values()):
                if t.owned and now - t.touched > ORPHAN:
                    log('closing a page nobody is showing: %s' % t.url)
                    t.close()
            if self.tabs:
                self.last_tab_t = now
            elif self.keep_running and now - self.last_tab_t > IDLE_QUIT:
                log('no pages for a while; stopping Firefox')
                self.quit()
                return

    def quit(self):
        try:
            self.call('browser.close', {}, 5)
        except BidiError:
            pass
        self.alive = False
        self.ws.close()
        threading.Thread(target=self.kill, args=(3,), daemon=True).start()

    def kill(self, grace=0):
        p = getattr(self, 'p', None)
        if p is None or p.poll() is not None:
            return
        if grace:
            try:
                p.wait(grace)
                return
            except subprocess.TimeoutExpired:
                pass
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass


_engines = set()


@atexit.register
def _kill_all():
    for e in list(_engines):
        e.alive = False
        e.kill()


# ---------------------------------------------------------------------------------------------
# one page
# ---------------------------------------------------------------------------------------------
class Tab:
    """A page in Firefox. Every method returns at once; the work happens on the
    tab's own threads. Read the attributes from the UI thread whenever you like."""

    def __init__(self, width, height, context=None, engine=None):
        self.w, self.h = max(50, int(width)), max(50, int(height))
        self.zoom = 1.0
        self.ctx = context
        self.engine = engine
        self.url = ''
        self.title = ''
        self.loading = False
        self.frame = None
        self.frame_seq = 0
        self.cursor = 'arrow'
        self.hover_link = ''
        self.can_back = False
        self.can_fwd = False
        self.events = collections.deque()
        self.closed = False
        self.ready = False
        self.owned = engine is None        # Neptune's pages close themselves when abandoned
        self.touched = time.time()
        self.last_input = 0
        self.last_change = time.time()
        self.info_due = 0
        self._fwd = 0                  # pages ahead of us, counted ourselves (Firefox may not say)
        self._traverse_t = 0
        self._ops = collections.deque()
        self._cv = threading.Condition()
        self._wake = threading.Event()
        self._hover_t = 0
        self._hover_at = None
        threading.Thread(target=self._work, daemon=True).start()

    # -- public, called from the UI thread ----------------------------------------------------
    def touch(self):
        self.touched = time.time()
        if self.engine is not None and self.ready:
            self.engine.last_active = self

    def navigate(self, url):
        self.url = url
        self.loading = True
        self._push(('nav', url))

    def reload(self):
        self.loading = True
        self._push(('reload',))

    def back(self):
        self._push(('traverse', -1))

    def forward(self):
        self._push(('traverse', 1))

    def stop(self):
        self._push(('script', 'window.stop()', False))
        self.loading = False

    def resize(self, width, height, zoom=None):
        width, height = max(50, int(width)), max(50, int(height))
        z = self.zoom if zoom is None else zoom
        if (width, height, z) != (self.w, self.h, self.zoom):
            self.w, self.h, self.zoom = width, height, z
            self._push(('resize',), replace=True)

    def mouse(self, kind, x, y, button=1):
        """kind: move / down / up; x, y in frame pixels; button 1 left, 2 middle, 3 right."""
        x, y = self._css(x, y)
        b = {1: 0, 2: 1, 3: 2}.get(button, 0)
        mv = {'type': 'pointerMove', 'x': x, 'y': y, 'origin': 'viewport', 'duration': 0}
        if kind == 'move':
            self._push(('pointer', [mv]), replace=True)
            self._hover_at = (x, y)
            return
        act = [mv, {'type': 'pointerDown' if kind == 'down' else 'pointerUp', 'button': b}]
        self._push(('pointer', act))

    def wheel(self, dy, x, y, dx=0):
        x, y = self._css(x, y)
        self._push(('wheel', [{'type': 'scroll', 'x': x, 'y': y, 'deltaX': int(dx), 'deltaY': int(dy),
                               'origin': 'viewport', 'duration': 0}]))

    def key(self, ev):
        """A Neptune key event. Returns False for keys EarthDog can't send."""
        ch, k = ev.get('char') or '', (ev.get('key') or '').lower()
        mods = [code for name, code in MODS if ev.get(name)]
        if ch and not ev.get('ctrl') and not ev.get('alt') and k not in KEYS:
            value, mods = ch, []
        elif k in KEYS:
            value = KEYS[k]
            if ch and k == 'space' and not ev.get('ctrl') and not ev.get('alt'):
                mods = []
        elif len(k) == 1:
            value = k
        elif ch:
            value = ch
        else:
            return False
        acts = [{'type': 'keyDown', 'value': m} for m in mods] + \
               [{'type': 'keyDown', 'value': value}, {'type': 'keyUp', 'value': value}] + \
               [{'type': 'keyUp', 'value': m} for m in reversed(mods)]
        self._push(('keys', acts))
        return True

    def insert_text(self, text):
        self._push(('script', 'document.execCommand("insertText", false, %s)' % json.dumps(text), False))

    def handle_prompt(self, accept, text=None):
        self._push(('prompt', accept, text))

    def eval(self, expr, timeout=4, page=False):
        """Run JavaScript now and wait for the answer (for menus and the like). None on failure."""
        if not self.ready or self.engine is None:
            return None
        try:
            return self.engine.evaluate(self.ctx, expr, timeout, sandbox=not page)
        except BidiError as e:
            log('eval failed: %s' % e)
            return None

    def selection(self):
        return self.eval(SELECTION_JS) or ''

    def text(self):
        return self.eval('document.body ? document.body.innerText : ""', 8) or ''

    def html(self):
        return self.eval('document.documentElement ? document.documentElement.outerHTML : ""', 8) or ''

    def find(self, text, backwards=False):
        return bool(self.eval('window.find(%s, false, %s, true)' % (json.dumps(text), 'true' if backwards else 'false'),
                              page=True))

    def recently_changed(self, secs=1.5):
        return time.time() - self.last_change < secs or self.loading

    def close(self):
        if not self.closed:
            self.closed = True
            self._push(('close',))

    # -- internals ----------------------------------------------------------------------------
    def _css(self, x, y):
        return int(x / self.zoom), int(y / self.zoom)

    def _push(self, op, replace=False):
        with self._cv:
            if replace and self._ops and self._ops[-1][0] == op[0]:
                self._ops[-1] = op
            elif op[0] == 'keys' and self._ops and self._ops[-1][0] == 'keys':
                self._ops[-1] = ('keys', self._ops[-1][1] + op[1])
            else:
                self._ops.append(op)
            self._cv.notify()
        if op[0] in ('pointer', 'wheel', 'keys', 'nav', 'reload', 'traverse'):
            self.last_input = time.time()
            self._wake.set()

    def _nav(self, url, loading):
        if loading and time.time() - self._traverse_t > 3:
            self._fwd = 0                    # a new page drops the forward list
            self.can_fwd = False
        if url and (not url.startswith('about:blank') or not self.url):
            self.url = url
        if loading is not None:
            self.loading = loading
        self.info_due = 0
        self.last_change = time.time()
        self._wake.set()

    def _gone(self, why):
        self.closed = True
        self.ready = False
        self.engine and self.engine.tabs.pop(self.ctx, None)
        self.events.append(('closed', why))
        with self._cv:
            self._cv.notify()
        self._wake.set()

    def _viewport(self):
        z = self.zoom
        self.engine.call('browsingContext.setViewport', {
            'context': self.ctx, 'viewport': {'width': max(1, int(self.w / z)), 'height': max(1, int(self.h / z))},
            'devicePixelRatio': z})

    def _work(self):
        try:
            if self.engine is None:
                self.engine = Engine.get()
            eng = self.engine
            if self.ctx is None:
                with eng.lock:
                    eng.creating += 1
                try:
                    self.ctx = eng.call('browsingContext.create', {'type': 'window'})['context']
                    eng.tabs[self.ctx] = self
                finally:
                    with eng.lock:
                        eng.creating -= 1
            eng.tabs[self.ctx] = self
            eng.unclaimed.pop(self.ctx, None)
            self._viewport()
            self.ready = True
            eng.last_active = self
            threading.Thread(target=self._capture, daemon=True).start()
        except Exception as e:
            import traceback
            log('could not open a page: %r\n%s' % (e, ''.join(traceback.format_exc(limit=4))))
            self.closed = True
            self.events.append(('error', str(e)))
            return
        while True:
            with self._cv:
                while not self._ops and not self.closed:
                    self._cv.wait(1.0)
                if not self._ops:
                    break
                op = self._ops.popleft()
            try:
                self._do(eng, op)
            except BidiError as e:
                log('%s failed: %s' % (op[0], e))
            if op[0] == 'close':
                break

    def _do(self, eng, op):
        kind = op[0]
        ctx = self.ctx
        if kind == 'nav':
            eng.call('browsingContext.navigate', {'context': ctx, 'url': op[1], 'wait': 'none'})
        elif kind == 'reload':
            eng.call('browsingContext.reload', {'context': ctx, 'wait': 'none'})
        elif kind == 'traverse':
            try:
                self._traverse_t = time.time()
                eng.call('browsingContext.traverseHistory', {'context': ctx, 'delta': op[1]}, 10)
                self._fwd = max(0, self._fwd - op[1])
                self.can_fwd = self._fwd > 0
            except BidiError:
                pass
            self.info_due = 0
        elif kind == 'resize':
            self._viewport()
        elif kind == 'pointer':
            eng.call('input.performActions', {'context': ctx, 'actions': [
                {'type': 'pointer', 'id': 'mouse', 'parameters': {'pointerType': 'mouse'}, 'actions': op[1]}]})
            if self._hover_at and time.time() - self._hover_t > 0.12:
                self._hover_t = time.time()
                try:
                    r = eng.evaluate(ctx, HOVER_JS % self._hover_at, 2)
                    c, href = json.loads(r) if r else ('', '')
                    self.cursor = {'pointer': 'hand', 'text': 'ibeam', 'vertical-text': 'ibeam',
                                   'wait': 'wait', 'progress': 'wait'}.get(c, 'arrow')
                    self.hover_link = href or ''
                except (BidiError, ValueError):
                    pass
        elif kind == 'wheel':
            eng.call('input.performActions', {'context': ctx, 'actions': [
                {'type': 'wheel', 'id': 'wheel', 'actions': op[1]}]})
        elif kind == 'keys':
            eng.call('input.performActions', {'context': ctx, 'actions': [
                {'type': 'key', 'id': 'keyboard', 'actions': op[1]}]})
        elif kind == 'script':
            eng.evaluate(ctx, op[1], 5, sandbox=op[2])
        elif kind == 'prompt':
            params = {'context': ctx, 'accept': bool(op[1])}
            if op[2] is not None:
                params['userText'] = op[2]
            eng.call('browsingContext.handleUserPrompt', params)
        elif kind == 'close':
            eng.tabs.pop(ctx, None)
            try:
                eng.call('browsingContext.close', {'context': ctx, 'promptUnload': False}, 5)
            except BidiError:
                pass
        self._wake.set()

    def _capture(self):
        eng = self.engine
        last = None
        streak = 0                      # captures in a row that changed: video-like pages use JPEG
        while not self.closed and eng.alive:
            t0 = time.time()
            fmt = {'type': 'image/jpeg', 'quality': 0.85} if streak >= 3 else {'type': 'image/png'}
            try:
                r = eng.call('browsingContext.captureScreenshot', {'context': self.ctx, 'format': fmt}, 10)
                data = r.get('data', '')
                if data != last:
                    im = Image.open(io.BytesIO(base64.b64decode(data)))
                    im.load()
                    self.frame = im.convert('RGB')
                    self.frame_seq += 1
                    if last is not None:
                        self.last_change = time.time()
                    streak += 1
                    last = data
                else:
                    streak = 0
            except BidiError:
                if self.closed:
                    break
                time.sleep(0.3)
            except Exception as e:
                log('frame error: %r' % e)
                time.sleep(0.3)
            now = time.time()
            if now >= self.info_due:
                self.info_due = now + 1.0
                try:
                    title, href, hlen, cb, cf = json.loads(eng.evaluate(self.ctx, INFO_JS, 3) or 'null')
                    self.title = title or ''
                    if href and href != 'about:blank':
                        self.url = href
                    self.can_back = bool(cb) if cb is not None else hlen > 1
                    self.can_fwd = bool(cf) if cf is not None else self._fwd > 0
                except (BidiError, ValueError, TypeError):
                    pass
            busy = now - self.last_input < 3 or self.loading or now - self.last_change < 2
            idle = 1.0 if now - self.touched > 3 else 0.25
            period = 1 / 24 if busy else idle
            self._wake.wait(max(0.0, period - (time.time() - t0)))
            self._wake.clear()


# ---------------------------------------------------------------------------------------------
# command line: python3 -m nep.web.earthdog text|html|shot URL [out.png]
# ---------------------------------------------------------------------------------------------
def _cli(argv):
    if len(argv) < 2 or argv[0] not in ('text', 'html', 'shot', 'title'):
        print('EarthDog: Mozilla Firefox for COS Neptune\n\n'
              'usage: earthdog text URL        print the page as text\n'
              '       earthdog html URL        print the page\'s HTML (after its scripts ran)\n'
              '       earthdog title URL       print the page title\n'
              '       earthdog shot URL [PNG]  save a picture of the page (default earthdog.png)\n\n'
              'Uses a throwaway Firefox profile, so it works while Neptune is running.')
        return 2
    cmd, url = argv[0], argv[1]
    if '://' not in url:
        url = 'https://' + url
    prof = tempfile.mkdtemp(prefix='earthdog-')
    eng = None
    try:
        eng = Engine(prof, keep_running=False)
        tab = Tab(1280, 900, engine=eng)
        t_end = time.time() + 15
        while not tab.ready and not tab.closed and time.time() < t_end:
            time.sleep(0.05)
        if not tab.ready:
            print('earthdog: could not open a page', flush=True)
            return 1
        eng.call('browsingContext.navigate', {'context': tab.ctx, 'url': url, 'wait': 'complete'}, 45)
        time.sleep(1.5)                        # let scripts settle
        if cmd == 'text':
            print(tab.text())
        elif cmd == 'html':
            print(tab.html())
        elif cmd == 'title':
            print(tab.eval('document.title') or '')
        else:
            out = argv[2] if len(argv) > 2 else 'earthdog.png'
            r = eng.call('browsingContext.captureScreenshot', {'context': tab.ctx}, 20)
            with open(out, 'wb') as f:
                f.write(base64.b64decode(r['data']))
            print(out)
        return 0
    except (RuntimeError, BidiError, OSError) as e:
        print('earthdog: %s' % e)
        return 1
    finally:
        if eng is not None:
            eng.alive = False
            eng.kill()
        shutil.rmtree(prof, ignore_errors=True)


if __name__ == '__main__':
    import sys
    sys.exit(_cli(sys.argv[1:]))
