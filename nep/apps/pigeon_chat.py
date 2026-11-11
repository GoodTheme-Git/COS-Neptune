"""Pigeon Chat: a just-in-case LAN communicator.

One computer hosts a chat hub; everyone else on the network joins it. Nothing
outside the local network is involved, so it keeps working when TCFNet, the
internet or the IBBS server are down.

  GET  /chat/messages?since=N   JSON: messages with id > N
  POST /chat/messages           JSON {"from", "host", "text"} -> stored message
  GET  /chat/                   a plain HTML chat page with a form (no
                                JavaScript), for phones, COS v2.4 and anything
                                else with a browser
  POST /chat/                   that form

A hub may have a room password: JSON clients send it as X-Pigeon-Key, the HTML
page as a form field. Hubs answer a UDP broadcast on the same port, so joining
is one click; the answer never includes the password.
"""
import html as _html
import http.server
import json
import os
import platform
import socket
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

from .. import theme

CHAT_PORT = 8421
PROBE = b'PIGEON-CHAT?'
MAX_TEXT = 2000
KEEP = 1000
LOG_FILE = os.path.join(theme.STATE, 'pigeon-chat.jsonl')


def default_name():
    return '%s@%s' % (os.environ.get('USER', 'neptune'), platform.node().split('.')[0])


# =============================================================================
# hub
# =============================================================================
class Hub:
    def __init__(self, port=CHAT_PORT, title='', key=''):
        self.port = int(port)
        self.title = title or ('%s chat' % platform.node().split('.')[0])
        self.key = key
        self.messages = deque(maxlen=KEEP)
        self.next_id = 1
        self.lock = threading.Lock()
        self.httpd = None
        self.udp = None
        self.error = ''
        self.started = 0
        self._load()

    # -- storage -------------------------------------------------------------------
    def _load(self):
        try:
            with open(LOG_FILE, encoding='utf-8') as f:
                for line in f:
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    self.messages.append(m)
                    self.next_id = max(self.next_id, int(m.get('id', 0)) + 1)
        except OSError:
            pass

    def add(self, sender, host, text):
        text = str(text).strip()[:MAX_TEXT]
        sender = (str(sender).strip() or 'anonymous')[:60]
        if not text:
            raise ValueError('empty message')
        with self.lock:
            m = {'id': self.next_id, 'time': time.time(), 'from': sender, 'host': str(host)[:60], 'text': text}
            self.next_id += 1
            self.messages.append(m)
            try:
                os.makedirs(theme.STATE, exist_ok=True)
                with open(LOG_FILE, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(m) + '\n')
            except OSError:
                pass
        return m

    def since(self, n):
        with self.lock:
            return [m for m in self.messages if m['id'] > n]

    # -- lifecycle --------------------------------------------------------------------
    @property
    def running(self):
        return self.httpd is not None

    def start(self):
        hub = self
        handler = type('BoundChat', (ChatHandler,), {'hub': hub})
        try:
            socketserver.ThreadingTCPServer.allow_reuse_address = True
            self.httpd = socketserver.ThreadingTCPServer(('0.0.0.0', self.port), handler)
            self.httpd.daemon_threads = True
        except OSError as e:
            self.httpd = None
            self.error = 'cannot listen on port %d: %s' % (self.port, e.strerror or e)
            return False
        threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.3}, daemon=True,
                         name='pigeon-chat').start()
        try:
            self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.udp.bind(('0.0.0.0', self.port))
            threading.Thread(target=self._answer_probes, daemon=True, name='pigeon-chat-udp').start()
        except OSError:
            self.udp = None            # discovery is a convenience; chat still works by address
        self.started = time.time()
        self.error = ''
        return True

    def _answer_probes(self):
        while self.udp is not None:
            try:
                data, addr = self.udp.recvfrom(256)
            except OSError:
                return
            if data.strip() == PROBE:
                reply = json.dumps({'pigeon_chat': 1, 'title': self.title, 'port': self.port,
                                    'host': platform.node(), 'locked': bool(self.key)}).encode()
                try:
                    self.udp.sendto(reply, addr)
                except OSError:
                    pass

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
        self.httpd = None
        if self.udp:
            try:
                self.udp.close()
            except OSError:
                pass
        self.udp = None


class ChatHandler(http.server.BaseHTTPRequestHandler):
    server_version = 'COS-Pigeon-Chat/%s' % theme.VERSION
    hub = None

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=()):
        raw = body.encode('utf-8', 'replace') if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    def _key_ok(self, given):
        return not self.hub.key or given == self.hub.key

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(url.query)
        if url.path == '/chat/messages':
            if not self._key_ok(self.headers.get('X-Pigeon-Key', '')):
                return self._json(403, {'error': 'wrong room password'})
            try:
                n = int(q.get('since', ['0'])[0])
            except ValueError:
                n = 0
            return self._json(200, {'title': self.hub.title, 'messages': self.hub.since(n)[-300:]})
        if url.path in ('/chat', '/chat/', '/'):
            return self._send(200, self.page(q.get('key', [''])[0], q.get('name', [''])[0]),
                              'text/html; charset=utf-8')
        return self._json(404, {'error': 'not found'})

    def do_POST(self):
        url = urllib.parse.urlsplit(self.path)
        try:
            length = min(int(self.headers.get('Content-Length', 0)), 64 * 1024)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length else b''
        if url.path == '/chat/messages':
            if not self._key_ok(self.headers.get('X-Pigeon-Key', '')):
                return self._json(403, {'error': 'wrong room password'})
            try:
                body = json.loads(raw or b'{}')
                return self._json(200, self.hub.add(body.get('from', ''), body.get('host', ''), body.get('text', '')))
            except (ValueError, AttributeError) as e:
                return self._json(400, {'error': str(e)})
        if url.path in ('/chat', '/chat/', '/'):
            form = urllib.parse.parse_qs(raw.decode('utf-8', 'replace'))
            key = form.get('key', [''])[0]
            name = form.get('name', [''])[0]
            if self._key_ok(key) and form.get('text', [''])[0].strip():
                try:
                    self.hub.add(name or 'web', self.client_address[0], form['text'][0])
                except ValueError:
                    pass
            back = '/chat/?' + urllib.parse.urlencode({'key': key, 'name': name})
            return self._send(303, '', 'text/plain', extra=(('Location', back),))
        return self._json(404, {'error': 'not found'})

    def page(self, key, name):
        e = _html.escape
        if not self._key_ok(key):
            body = ('<p>This room has a password.</p><form method="get" action="/chat/">'
                    'Password <input type="password" name="key"> Name <input name="name" value="%s"> '
                    '<input type="submit" value="Enter"></form>' % e(name))
        else:
            rows = ''.join('<tr><td valign="top"><font color="#667788">%s</font></td><td valign="top"><b>%s</b></td>'
                           '<td>%s</td></tr>' % (time.strftime('%H:%M', time.localtime(m['time'])), e(m['from']),
                                                 e(m['text']).replace('\n', '<br>'))
                           for m in list(self.hub.messages)[-60:])
            body = ('<table cellpadding="3" width="100%%">%s</table><hr>'
                    '<form method="post" action="/chat/"><input type="hidden" name="key" value="%s">'
                    'Name <input name="name" size="14" value="%s"> '
                    '<input name="text" size="50" autofocus> <input type="submit" value="Send"> '
                    '<a href="/chat/?key=%s&name=%s">refresh</a></form>') % (
                rows or '<tr><td>No messages yet.</td></tr>', e(key), e(name), e(urllib.parse.quote(key)),
                e(urllib.parse.quote(name)))
        return ('<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="8">'
                '<title>%s - Pigeon Chat</title></head><body bgcolor="#f4f8fc">'
                '<h2><font color="#1e4e8c">%s</font></h2><p><small>Pigeon Chat on %s (COS Neptune). LAN only.</small>'
                '</p>%s</body></html>') % (e(self.hub.title), e(self.hub.title), e(platform.node()), body)


# =============================================================================
# client
# =============================================================================
def discover(port=CHAT_PORT, wait=0.8):
    """[{'title', 'host', 'addr', 'port', 'locked'}] for hubs answering on the LAN."""
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.2)
        for target in ('255.255.255.255', '127.0.0.1'):
            try:
                s.sendto(PROBE, (target, port))
            except OSError:
                pass
        end = time.time() + wait
        while time.time() < end:
            try:
                data, addr = s.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                info = json.loads(data)
            except ValueError:
                continue
            if info.get('pigeon_chat'):
                key = (info.get('host'), info.get('port'))
                if key not in found or addr[0] != '127.0.0.1':
                    found[key] = {'title': info.get('title', '?'), 'host': info.get('host', '?'),
                                  'addr': '%s:%d' % (addr[0], int(info.get('port', port))),
                                  'locked': bool(info.get('locked'))}
    finally:
        s.close()
    return sorted(found.values(), key=lambda h: h['title'].lower())


class Client:
    """Polls one hub in the background; the app drains .inbox on its ticks."""

    def __init__(self, addr, name, key=''):
        addr = addr.strip().rstrip('/')
        if '://' not in addr:
            addr = 'http://' + addr
        for suffix in ('/chat/messages', '/chat'):
            if addr.endswith(suffix):
                addr = addr[:-len(suffix)]
        self.base = addr
        self.name = name
        self.key = key
        self.title = ''
        self.messages = []
        self.last_id = 0
        self.ok = False
        self.error = ''
        self.inbox = []
        self.lock = threading.Lock()
        self.stopped = False
        threading.Thread(target=self._poll, daemon=True, name='pigeon-chat-client').start()

    def _req(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header('Content-Type', 'application/json')
        if self.key:
            req.add_header('X-Pigeon-Key', self.key)
        try:
            with urllib.request.urlopen(req, timeout=6) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get('error', '')
            except ValueError:
                msg = ''
            raise ConnectionError(msg or 'hub said %d' % e.code)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ConnectionError('cannot reach %s (%s)' % (self.base, getattr(e, 'reason', e)))

    def _poll(self):
        while not self.stopped:
            try:
                got = self._req('GET', '/chat/messages?since=%d' % self.last_id)
                new = got.get('messages', [])
                with self.lock:
                    self.title = got.get('title', '')
                    if new:
                        self.last_id = max(m['id'] for m in new)
                        self.messages = (self.messages + new)[-KEEP:]
                        self.inbox.extend(new)
                    self.ok, self.error = True, ''
                time.sleep(1.0)
            except ConnectionError as e:
                with self.lock:
                    self.ok, self.error = False, str(e)
                time.sleep(3.0)

    def send(self, text, done=None):
        def run():
            try:
                self._req('POST', '/chat/messages', {'from': self.name, 'host': platform.node(), 'text': text})
            except ConnectionError as e:
                with self.lock:
                    self.error = str(e)
        threading.Thread(target=run, daemon=True).start()

    def drain(self):
        with self.lock:
            items, self.inbox = self.inbox, []
        return items

    def stop(self):
        self.stopped = True
