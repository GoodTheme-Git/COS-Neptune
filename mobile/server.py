#!/usr/bin/env python3
"""COS Neptune Mobile server: serves the phone shell (web/) on the LAN and relays IBBS.

    python3 server.py                 # http://0.0.0.0:8620, board 192.168.1.115:8619
    python3 server.py --port 9000 --board 192.168.1.115:8619
    python3 server.py --local         # only this computer (127.0.0.1)
    python3 server.py --board local   # read/write this computer's board files directly

Open the printed address on your phone. A TCF_IBBS board sends no CORS headers, so the
page can't call it directly. Instead it asks /api/ibbs/<endpoint> and this server
forwards the request to the board named in the X-IBBS-Server header (or --board),
passing X-IBBS-Token along. Only the board's own read/post endpoints are relayed:
/rooms, /room/<name>, /files and /file/<name>.

When the board is "local", or the board server is on this computer and isn't running,
the relay reads and writes the board files (~/.tcf_ibbs, or $IBBS_DIR) itself through
ibbs-new.py ($IBBS_PY, default ~/ssh-crap/ibbs/ibbs-new.py), so chat keeps working.

Phones: open the address with http:// in front. Recent Chrome for Android tries
https:// first; this server closes those TLS connections straight away so Chrome
falls back to http:// at once instead of retrying.
"""
import argparse
import http.server
import importlib.util
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, 'web')
BOARD = os.environ.get('IBBS_SERVER', '192.168.1.115:8619')
TOKEN = os.environ.get('IBBS_TOKEN', '')
HOSTPORT = re.compile(r'^[A-Za-z0-9.\-]{1,253}:\d{1,5}$')
RELAYED = re.compile(r'^/(rooms|files|room/[A-Za-z0-9_-]+|file/[^/]+)$')
IBBS_PY = os.path.expanduser(os.environ.get('IBBS_PY', '~/ssh-crap/ibbs/ibbs-new.py'))
mimetypes.add_type('application/manifest+json', '.webmanifest')
mimetypes.add_type('text/javascript', '.js')


_ibbs = None
_ibbs_lock = threading.Lock()


def local_ibbs():
    """The ibbs-new.py module, pointed at this computer's board directory."""
    global _ibbs
    with _ibbs_lock:
        if _ibbs is None:
            spec = importlib.util.spec_from_file_location('tcf_ibbs', IBBS_PY)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.configure(server='')
            _ibbs = mod
    return _ibbs


def my_addresses():
    names = {'localhost', '127.0.0.1', '0.0.0.0', socket.gethostname(), lan_ip()}
    try:
        names.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return names


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = 'NeptuneMobile/3.1'

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def handle(self):
        # A TLS ClientHello starts with 0x16 0x03. Chrome sends one when it tries https://
        # first; answering it with a plain-text 400 makes some builds retry. Hang up instead.
        try:
            self.connection.settimeout(15)
            first = self.connection.recv(2, socket.MSG_PEEK)
        except OSError:
            return
        if first[:1] == b'\x16':
            self.close_connection = True
            return
        super().handle()

    def log_message(self, fmt, *args):
        if not self.path.startswith('/api/ibbs/room/'):      # the chat polls every 2 s; keep quiet
            sys.stderr.write('%s  %s\n' % (self.address_string(), fmt % args))

    def end_headers(self):
        if self.path.endswith(('.html', '.js', '.css', '/', '.webmanifest')) or self.path.startswith('/api/'):
            self.send_header('Cache-Control', 'no-cache')
        super().end_headers()

    def json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split('?')[0] == '/api/ping':
            return self.json(200, {'neptune': 'mobile', 'version': '3.1', 'board': BOARD})
        if self.path.startswith('/api/ibbs/'):
            return self.relay('GET')
        if self.path.split('?')[0] in ('/apk', '/NeptuneMobile.apk'):
            return self.send_apk()
        return super().do_GET()

    def send_apk(self):
        apk = os.path.join(HERE, 'dist', 'NeptuneMobile.apk')
        if not os.path.isfile(apk):
            return self.json(404, {'error': 'no APK built yet: run android/build.sh'})
        with open(apk, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', 'application/vnd.android.package-archive')
        self.send_header('Content-Disposition', 'attachment; filename="NeptuneMobile.apk"')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.startswith('/api/ibbs/'):
            return self.relay('POST')
        self.json(404, {'error': 'no such endpoint'})

    def relay(self, method):
        url = urllib.parse.urlsplit(self.path)
        path = url.path[len('/api/ibbs'):]
        query = urllib.parse.parse_qs(url.query)
        if not RELAYED.match(path):
            return self.json(404, {'error': 'IBBS relay: not a board endpoint'})
        board = (self.headers.get('X-IBBS-Server') or query.pop('server', [None])[0] or BOARD).strip()
        board = re.sub(r'^https?://', '', board).rstrip('/')
        local = board.lower() == 'local'
        if not local and not HOSTPORT.match(board):
            return self.json(400, {'error': 'IBBS relay: board must be host:port or local'})
        data = None
        if method == 'POST':
            n = int(self.headers.get('Content-Length') or 0)
            if n > 64 * 1024:
                return self.json(413, {'error': 'IBBS relay: message too big'})
            data = self.rfile.read(n)
        if local:
            return self.local_board(method, path, query, data)
        token = self.headers.get('X-IBBS-Token') or TOKEN
        qs = urllib.parse.urlencode({k: v[0] for k, v in query.items()})
        target = 'http://%s%s%s' % (board, path, '?' + qs if qs else '')
        req = urllib.request.Request(target, data=data, method=method)
        req.add_header('Content-Type', 'application/json')
        if token:
            req.add_header('X-IBBS-Token', token)
        try:
            resp = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as e:
            body = e.read()
            self.send_response(e.code)
            self.send_header('Content-Type', e.headers.get('Content-Type', 'application/json'))
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        except (urllib.error.URLError, OSError) as e:
            if board.rsplit(':', 1)[0] in my_addresses() and os.path.isfile(IBBS_PY):
                return self.local_board(method, path, query, data)
            return self.json(502, {'error': "can't reach board %s: %s" % (board, getattr(e, 'reason', e))})
        with resp:
            self.send_response(resp.status)
            for h in ('Content-Type', 'Content-Length', 'X-IBBS-Version', 'X-IBBS-SHA256'):
                if resp.headers.get(h):
                    self.send_header(h, resp.headers[h])
            if path.startswith('/file/'):
                name = urllib.parse.unquote(path[6:]).replace('"', '')
                self.send_header('Content-Disposition', 'attachment; filename="%s"' % name)
            self.end_headers()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)


    def local_board(self, method, path, query, data):
        try:
            ib = local_ibbs()
        except Exception as e:                       # missing or broken ibbs-new.py
            return self.json(502, {'error': "no board server and can't load %s: %s" % (IBBS_PY, e)})
        parts = path.strip('/').split('/')
        try:
            if method == 'POST':
                if parts[0] != 'room':
                    return self.json(404, {'error': 'local board: only room posts'})
                body = json.loads(data or b'{}')
                text = str(body.get('text') or '').strip()
                if not text:
                    raise ValueError('empty message')
                return self.json(200, ib.post(parts[1], str(body.get('from') or 'anonymous'), text,
                                              str(body.get('host') or '?'), str(body.get('client') or '?')))
            if parts == ['rooms']:
                return self.json(200, {'rooms': ib.list_rooms()})
            if parts == ['files']:
                return self.json(200, {'files': ib.list_files()})
            if parts[0] == 'room':
                since = query.get('since', ['0'])[0]
                if since == 'end':
                    return self.json(200, {'messages': [], 'offset': ib.end_offset(parts[1])})
                msgs, offset = ib.follow(parts[1], max(0, int(since)))
                return self.json(200, {'messages': msgs, 'offset': offset})
            if parts[0] == 'file':
                name = urllib.parse.unquote(parts[1])
                fpath, info = ib.file_info(name)
                with open(fpath, 'rb') as f:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Length', str(os.fstat(f.fileno()).st_size))
                    self.send_header('X-IBBS-SHA256', info.get('sha256', ''))
                    self.send_header('Content-Disposition', 'attachment; filename="%s"' % name.replace('"', ''))
                    self.end_headers()
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
        except FileNotFoundError:
            return self.json(404, {'error': 'no such file on the board'})
        except (ValueError, KeyError, TypeError, IndexError) as e:
            return self.json(400, {'error': 'local board: %s' % e})
        return self.json(404, {'error': 'no such endpoint'})


class Server(http.server.ThreadingHTTPServer):
    request_queue_size = 128     # phones open several connections at once
    daemon_threads = True


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))
        return s.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        s.close()


def main():
    global BOARD
    ap = argparse.ArgumentParser(description='COS Neptune Mobile server')
    ap.add_argument('--port', type=int, default=8620)
    ap.add_argument('--board', default=BOARD, help='default IBBS board host:port (%(default)s)')
    ap.add_argument('--local', action='store_true', help='listen on 127.0.0.1 only')
    a = ap.parse_args()
    BOARD = a.board
    host = '127.0.0.1' if a.local else '0.0.0.0'
    srv = Server((host, a.port), Handler)
    print('COS Neptune Mobile')
    print('  on this computer: http://localhost:%d/' % a.port)
    if not a.local:
        print('  on your phone:    http://%s:%d/   (same Wi-Fi)' % (lan_ip(), a.port))
        if os.path.isfile(os.path.join(HERE, 'dist', 'NeptuneMobile.apk')):
            print('  Android app:      http://%s:%d/apk' % (lan_ip(), a.port))
    print('  IBBS board:       %s%s' % (BOARD, '' if BOARD == 'local' else '  (falls back to the local board files if it is down)'))
    print('Ctrl+C stops it.')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print()


if __name__ == '__main__':
    main()
