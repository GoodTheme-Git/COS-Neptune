"""Pigeon: share a folder over the LAN, read-only, and pull only what changed.

Same protocol as COS v2.4's Pigeon, so a Neptune machine and a v2.4 machine
can sync with each other in either direction:

  GET /<token>/               index page (Neptune shows it in Webinator)
  GET /<token>/manifest.json  every file with size, mtime and SHA-256
  GET /<token>/pull           a small Python client: curl ... | python3 - DEST
  GET /<token>/<path>         one file

Sharing never writes anything and refuses hidden files, caches and backups.
Receiving (the Receive tab) only ever writes inside Neptune's Downloads.
The Chat tab is a just-in-case LAN communicator (see pigeon_chat.py).
"""
import hashlib
import html as _html
import http.server
import json
import os
import platform
import secrets
import socket
import socketserver
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime

from .. import gfx, theme
from ..app import App
from .dialogs import message
from . import pigeon_chat as chat

DEFAULT_PORT = 8420
SKIP_DIRS = {'__pycache__', 'node_modules', 'venv', '.git', 'build', 'dist', '.mypy_cache', '.pytest_cache'}
SKIP_SUFFIX = ('~', '.pyc', '.pyo', '.swp', '.swo', '.tmp', '.temp', '.save', '.orig', '.rej', '.lock')
RECEIVE_ROOT = os.path.join(theme.HOME, 'Downloads', 'Pigeon')
_HASH = {}


def skipped_name(name):
    low = name.lower()
    return low.endswith(SKIP_SUFFIX) or '.bak' in low


def hidden_path(rel):
    parts = [p for p in rel.replace(os.sep, '/').split('/') if p]
    for i, part in enumerate(parts):
        if part.startswith('.') or part in SKIP_DIRS or (i == len(parts) - 1 and skipped_name(part)):
            return True
    return False


def file_sha256(path, size, mtime):
    key = (path, size, int(mtime))
    if key in _HASH:
        return _HASH[key]
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(262144), b''):
                h.update(chunk)
    except OSError:
        return ''
    if len(_HASH) > 4000:
        _HASH.clear()
    _HASH[key] = h.hexdigest()
    return _HASH[key]


def file_manifest(root, max_files=3000, max_mb=512):
    root = os.path.abspath(root)
    out = []
    budget = max_mb * 1_000_000
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in SKIP_DIRS)
        for name in sorted(files):
            if name.startswith('.') or skipped_name(name):
                continue
            full = os.path.join(base, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if not os.path.isfile(full) or os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, '/')
            out.append((rel, st.st_size, st.st_mtime, file_sha256(full, st.st_size, st.st_mtime) if st.st_size <= budget else ''))
            if len(out) >= max_files:
                return out
    return out


def local_addresses():
    addrs = []
    try:
        out = subprocess.run(['ip', '-brief', '-4', 'addr'], capture_output=True, text=True, timeout=4).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[0] != 'lo':
                for tok in parts[2:]:
                    ip = tok.split('/')[0]
                    if ip and not ip.startswith('127.'):
                        addrs.append(ip)
    except Exception:
        pass
    if not addrs:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('10.255.255.255', 1))
            addrs.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    return addrs or ['127.0.0.1']


PULL_CLIENT = r'''#!/usr/bin/env python3
# COS Pigeon -- pull files from a COS machine, transferring only what changed.
# Served by COS %(version)s "Neptune".
# Usage:  curl -fsS %(base)s/pull | python3 - [destination]
import hashlib, json, os, sys, urllib.request

BASE = "%(base)s"

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for c in iter(lambda: f.read(262144), b''):
            h.update(c)
    return h.hexdigest()

def main():
    dest = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
    os.makedirs(dest, exist_ok=True)
    try:
        with urllib.request.urlopen(BASE + '/manifest.json', timeout=20) as r:
            man = json.load(r)
    except Exception as e:
        sys.exit('could not reach ' + BASE + ': ' + str(e))
    files = man['files']
    print(len(files), 'file(s) on', man['host'] + ':' + man['root'])
    new = changed = same = 0
    for item in files:
        rel, size, digest = item['path'], item['size'], item['sha256']
        if rel.startswith('/') or '..' in rel.split('/'):
            continue
        target = os.path.join(dest, rel.replace('/', os.sep))
        if os.path.exists(target):
            try:
                if digest and sha(target) == digest:
                    same += 1
                    continue
            except OSError:
                pass
            changed += 1
            tag = 'changed'
        else:
            new += 1
            tag = 'new'
        os.makedirs(os.path.dirname(target) or '.', exist_ok=True)
        print('  ' + tag.rjust(7) + '  ' + rel + '  (' + str(size) + ' bytes)')
        with urllib.request.urlopen(BASE + '/' + urllib.request.quote(rel), timeout=120) as r, open(target, 'wb') as f:
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
    print('done:', new, 'new,', changed, 'updated,', same, 'already current ->', dest)

if __name__ == '__main__':
    main()
'''


class PigeonHandler(http.server.SimpleHTTPRequestHandler):
    server_version = 'COS-Pigeon/%s' % theme.VERSION
    token = ''
    share_root = '.'
    log_sink = None
    hits = None

    def __init__(self, *a, **kw):
        kw['directory'] = self.share_root
        super().__init__(*a, **kw)

    def log_message(self, fmt, *args):
        if self.log_sink is not None:
            self.log_sink.append((time.time(), self.address_string(), (fmt % args)[:160]))
        if self.hits is not None:
            self.hits[0] += 1

    def _inner(self):
        path = self.path.split('?', 1)[0].split('#', 1)[0]
        if not self.token:
            return path
        prefix = '/' + self.token
        if path == prefix:
            return '/'
        if path.startswith(prefix + '/'):
            return path[len(prefix):]
        return None

    def do_GET(self):
        inner = self._inner()
        if inner is None:
            return self.send_error(404, 'Not found')
        if hidden_path(urllib.parse.unquote(inner)):
            return self.send_error(403, 'Hidden files are not served')
        if inner in ('/', ''):
            return self._write(self.index())
        if inner == '/manifest.json':
            return self._write(json.dumps(self.manifest(), indent=1), 'application/json; charset=utf-8')
        if inner in ('/pull', '/pull.py'):
            return self._write(PULL_CLIENT % {'base': self.base_url(), 'version': theme.VERSION},
                               'text/x-python; charset=utf-8')
        self.path = inner
        return super().do_GET()

    def do_HEAD(self):
        inner = self._inner()
        if inner is None or hidden_path(urllib.parse.unquote(inner)):
            return self.send_error(404, 'Not found')
        if inner in ('/', '', '/manifest.json', '/pull', '/pull.py'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            return
        self.path = inner
        return super().do_HEAD()

    def do_POST(self):
        self.send_error(405, 'This server is read-only')
    do_PUT = do_DELETE = do_PATCH = do_POST

    def list_directory(self, path):
        self.send_error(403, 'Directory listings are off; use the index page')

    def base_url(self):
        host = self.headers.get('Host') or '%s:%d' % (local_addresses()[0], self.server.server_port)
        return 'http://%s' % host + ('/%s' % self.token if self.token else '')

    def _write(self, body, ctype='text/html; charset=utf-8'):
        raw = body.encode('utf-8', 'replace')
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def manifest(self):
        return {'cos': theme.VERSION, 'build': theme.BUILD, 'host': platform.node(),
                'root': os.path.abspath(self.share_root),
                'generated': datetime.now().isoformat(timespec='seconds'),
                'files': [{'path': r, 'size': s, 'mtime': m, 'sha256': d} for r, s, m, d in file_manifest(self.share_root)]}

    def index(self):
        files = file_manifest(self.share_root)
        base = self.base_url()
        total = sum(f[1] for f in files)
        rows = ''.join(
            '<tr><td><a href="%s/%s">%s</a></td><td align="right">%s</td><td>%s</td><td><code>%s</code></td></tr>' % (
                base, urllib.parse.quote(r), _html.escape(r), '{:,}'.format(s),
                datetime.fromtimestamp(m).strftime('%Y-%m-%d %H:%M'), d[:12]) for r, s, m, d in files)
        host = _html.escape(platform.node())
        return ('<!doctype html><html><head><meta charset="utf-8"><title>COS Pigeon on %s</title></head>'
                '<body bgcolor="#f4f8fc"><h1><font color="#1e4e8c">COS Pigeon</font></h1>'
                '<p>%d file(s), %s bytes, shared read-only from <code>%s</code> on <b>%s</b> '
                '(COS %s "Neptune", build %s).</p>'
                '<h2>Sync everything (only what changed moves)</h2>'
                '<pre>curl -fsS %s/pull | python3 - ~/Pigeon</pre>'
                '<p>Or in COS Neptune: open Pigeon, go to <b>Receive</b>, and paste <code>%s</code>.</p>'
                '<h2>Files</h2><table width="100%%" cellpadding="3"><tr bgcolor="#dde8f4"><th align="left">name</th>'
                '<th align="right">bytes</th><th align="left">modified</th><th align="left">sha256</th></tr>%s</table>'
                '<p><a href="%s/manifest.json">manifest.json</a> &middot; <a href="%s/pull">pull client</a></p>'
                '</body></html>') % (host, len(files), '{:,}'.format(total),
                                     _html.escape(os.path.abspath(self.share_root)), host, theme.VERSION, theme.BUILD,
                                     base, base, rows, base, base)


class PigeonServer:
    def __init__(self, root, port=DEFAULT_PORT, use_token=True):
        self.root = os.path.abspath(os.path.expanduser(root))
        self.port = int(port)
        self.token = secrets.token_hex(4) if use_token else ''
        self.httpd = None
        self.thread = None
        self.error = ''
        self.log = deque(maxlen=200)
        self.hits = [0]
        self.started = 0.0

    @property
    def running(self):
        return self.httpd is not None

    def start(self):
        if not os.path.isdir(self.root):
            self.error = 'not a folder: %s' % self.root
            return False
        handler = type('BoundPigeon', (PigeonHandler,), {'token': self.token, 'share_root': self.root,
                                                         'log_sink': self.log, 'hits': self.hits})
        try:
            socketserver.ThreadingTCPServer.allow_reuse_address = True
            self.httpd = socketserver.ThreadingTCPServer(('0.0.0.0', self.port), handler)
            self.httpd.daemon_threads = True
        except OSError as e:
            self.httpd = None
            self.error = 'cannot listen on port %d: %s' % (self.port, e.strerror or e)
            return False
        self.error = ''
        self.started = time.time()
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={'poll_interval': 0.3}, daemon=True,
                                       name='nep-pigeon')
        self.thread.start()
        return True

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
        self.httpd = None

    def urls(self):
        suffix = '/%s' % self.token if self.token else ''
        return ['http://%s:%d%s' % (ip, self.port, suffix) for ip in local_addresses()]


# =============================================================================
class Puller:
    """Receive side: mirror a Pigeon share into a folder, moving only changes."""

    def __init__(self, base, dest):
        self.base = base.rstrip('/')
        self.dest = dest
        self.log = deque(maxlen=300)
        self.done = self.total = 0
        self.counts = {'new': 0, 'changed': 0, 'same': 0}
        self.error = ''
        self.finished = False
        self.cancel = False
        self.host = ''

    def run(self):
        try:
            with urllib.request.urlopen(self.base + '/manifest.json', timeout=20) as r:
                man = json.load(r)
            files = man.get('files', [])
            self.host = '%s:%s' % (man.get('host', '?'), man.get('root', '?'))
            self.total = len(files)
            self.log.append('%d file(s) on %s (COS %s)' % (len(files), self.host, man.get('cos', '?')))
            os.makedirs(self.dest, exist_ok=True)
            root = os.path.realpath(self.dest)
            for item in files:
                if self.cancel:
                    self.log.append('cancelled')
                    break
                rel, size, digest = str(item['path']), item.get('size', 0), item.get('sha256', '')
                target = os.path.realpath(os.path.join(self.dest, rel.replace('/', os.sep)))
                if rel.startswith('/') or '..' in rel.split('/') or not target.startswith(root + os.sep):
                    self.log.append('  skipped unsafe path %s' % rel)
                    self.done += 1
                    continue
                if os.path.exists(target):
                    st = os.stat(target)
                    if digest and file_sha256(target, st.st_size, st.st_mtime) == digest:
                        self.counts['same'] += 1
                        self.done += 1
                        continue
                    tag = 'changed'
                else:
                    tag = 'new'
                self.counts[tag] += 1
                os.makedirs(os.path.dirname(target), exist_ok=True)
                self.log.append('  %7s  %s  (%s bytes)' % (tag, rel, '{:,}'.format(size)))
                with urllib.request.urlopen(self.base + '/' + urllib.parse.quote(rel), timeout=120) as r, \
                        open(target + '.pigeon-part', 'wb') as f:
                    while True:
                        chunk = r.read(262144)
                        if not chunk:
                            break
                        f.write(chunk)
                os.replace(target + '.pigeon-part', target)
                self.done += 1
            self.log.append('done: %(new)d new, %(changed)d updated, %(same)d already current' % self.counts)
        except Exception as e:
            self.error = '%s: %s' % (type(e).__name__, getattr(e, 'reason', e))
            self.log.append('error: ' + self.error)
        self.finished = True


class Pigeon(App):
    title = 'Pigeon'
    icon = 'pigeon'
    size = (660, 560)
    min_size = (540, 460)
    single_instance = True
    bg = theme.CLIENT_BG
    FOLDERS = [('Public', os.path.join(theme.HOME, 'Public')), ('Documents', os.path.join(theme.HOME, 'Documents')),
               ('Pictures', os.path.join(theme.HOME, 'Pictures')), ('Music', os.path.join(theme.HOME, 'Music')),
               ('Downloads', os.path.join(theme.HOME, 'Downloads'))]

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        self.tab = 'share'
        self.folder = self.FOLDERS[0][1]
        self.custom = {'text': '', 'cur': 0}
        self.port = {'text': str(DEFAULT_PORT), 'cur': 4}
        self.use_token = True
        self.server = getattr(shell, 'pigeon_server', None)
        self.src = {'text': '', 'cur': 0}
        self.puller = None
        self.last = 0
        # chat (the hub and the connection outlive this window, like sharing does)
        self.chat_name = {'text': chat.default_name(), 'cur': len(chat.default_name())}
        self.chat_addr = {'text': '', 'cur': 0}
        self.chat_key = {'text': '', 'cur': 0}
        self.host_title = {'text': '', 'cur': 0}
        self.host_key = {'text': '', 'cur': 0}
        self.chat_msg = {'text': '', 'cur': 0}
        self.found = None                     # None = never searched, [] = nothing found
        self.searching = False
        self.chat_scroll = 0
        self._chat_sig = None

    def share_folder(self):
        t = self.custom['text'].strip()
        return os.path.abspath(os.path.expanduser(t)) if t else self.folder

    @property
    def live(self):
        return bool(self.puller and not self.puller.finished)

    @property
    def hub(self):
        return getattr(self.shell, 'pigeon_hub', None)

    @property
    def client(self):
        return getattr(self.shell, 'pigeon_client', None)

    def on_tick(self, now):
        changed = False
        c = self.client
        if c is not None:
            new = c.drain()
            mine = self.chat_name['text'].strip()
            theirs = [m for m in new if m.get('from') != mine]
            if theirs and self.win is not self.shell.active:
                m = theirs[-1]
                self.shell.notify('Pigeon Chat', '<%s> %s' % (m['from'], m['text'][:90]), 'pigeon', 5)
            sig = (len(c.messages), c.ok, c.error, c.title)
            if new or sig != self._chat_sig:
                self._chat_sig = sig
                changed = True
        if now - self.last > 0.5:
            self.last = now
            changed = changed or bool((self.server and self.server.running) or self.puller)
        return changed

    # -- drawing ------------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        img.alpha_composite(gfx.vgradient(w, 70, ((0, (250, 252, 255)), (1, (222, 234, 247)))), (0, 0))
        ui.icon('pigeon', (16, 12), 44)
        ui.text((72, 14), 'Pigeon', 18, color=(30, 57, 91))
        ui.text((72, 42), 'Share a folder on your network, read-only. Only changed files move.', 12, color=theme.MUTED)
        ui.hline(0, w, 69, (190, 205, 222))
        x = 12
        for key, label in (('share', 'Share'), ('receive', 'Receive'), ('chat', 'Chat')):
            tw = gfx.text_width(gfx.font(12), label) + 28
            on = self.tab == key
            box = (x, 78, x + tw, 102)
            if on or ui.hot(box):
                ui._btn_face(box, (((0, (234, 246, 253)), (1, (190, 230, 253))), (60, 127, 177)) if on else
                             (((0, (248, 250, 253)), (1, (226, 236, 247))), (180, 200, 222)))
            ui.text(((box[0] + box[2]) // 2, 90), label, 12, bold=on, anchor='mm')
            ui.region(box, ('tab', key))
            x += tw + 6
        body = (16, 112, w - 16, h - 12)
        {'share': self.draw_share, 'receive': self.draw_receive, 'chat': self.draw_chat}[self.tab](img, ui, body)

    def draw_share(self, img, ui, box):
        x0, y0, x1, y1 = box
        srv = self.server if self.server and self.server.running else None
        if srv is None:
            ui.groupbox((x0, y0, x1, y0 + 150), 'Folder to share')
            yy = y0 + 22
            col = 0
            for label, path in self.FOLDERS:
                cx = x0 + 12 + col * 140
                ui.radio((cx, yy), label, ('folder', path), self.folder == path and not self.custom['text'].strip(),
                         data=path)
                col += 1
                if col == 4:
                    col, yy = 0, yy + 24
            yy += 30
            ui.text((x0 + 12, yy + 10), 'Or a path:', 12, anchor='lm')
            ui.textbox((x0 + 90, yy, x1 - 12, yy + 24), 'custom', self.custom, placeholder='~/Music, /mnt/usb, ...')
            yy = y0 + 162
            ui.groupbox((x0, yy, x1, yy + 90), 'Options')
            ui.text((x0 + 12, yy + 32), 'Port:', 12, anchor='lm')
            ui.textbox((x0 + 60, yy + 20, x0 + 130, yy + 44), 'port', self.port)
            ui.checkbox((x0 + 12, yy + 56), 'Require a secret link (a random token in every address)', 'token',
                        self.use_token)
            folder = self.share_folder()
            ok = os.path.isdir(folder)
            note = ('Anyone on your network who has the link can read every file in %s. Hidden files, caches and '
                    'backups are never shared.' % folder.replace(theme.ROOT, '~/cos/neptune')) if ok else \
                ('%s is not a folder yet. Neptune creates Public for you when you start sharing it.' % folder
                 if folder.startswith(theme.HOME) else '%s is not a folder.' % folder)
            ui.wrap((x0, yy + 104), note, x1 - x0, 11, color=theme.MUTED)
            ui.button((x1 - 160, y1 - 36, x1, y1 - 6), 'Start sharing', 'start', default=True)
            return
        # running
        up = int(time.time() - srv.started)
        ui.icon('pigeon', (x0, y0 + 2), 32)
        ui.text((x0 + 42, y0 + 4), 'Sharing %s' % srv.root.replace(theme.ROOT, '~/cos/neptune'), 13, bold=True)
        ui.text((x0 + 42, y0 + 24), 'Port %d  ·  up %d:%02d  ·  %d request%s' % (
            srv.port, up // 60, up % 60, srv.hits[0], '' if srv.hits[0] == 1 else 's'), 11, color=theme.MUTED)
        yy = y0 + 52
        ui.text((x0, yy), 'Open this address on the other computer:', 12)
        yy += 20
        for i, u in enumerate(srv.urls()[:3]):
            ui.rect((x0, yy, x1 - 150, yy + 24), fill=(246, 248, 251), outline=(200, 208, 220))
            ui.text((x0 + 8, yy + 12), u, 12, kind='mono', anchor='lm')
            ui.button((x1 - 144, yy, x1 - 76, yy + 24), 'Copy', ('copy', u))
            ui.button((x1 - 70, yy, x1, yy + 24), 'Open', ('open', u))
            yy += 30
        cmd = 'curl -fsS %s/pull | python3 - ~/Pigeon' % srv.urls()[0]
        ui.text((x0, yy + 4), 'Or sync from a terminal (copies only what changed):', 12)
        yy += 24
        ui.rect((x0, yy, x1 - 76, yy + 24), fill=(30, 34, 40))
        ui.text((x0 + 8, yy + 12), gfx.ellipsize(gfx.font(11, False, 'mono'), cmd, x1 - x0 - 96), 11, kind='mono',
                color=(180, 255, 190), anchor='lm')
        ui.button((x1 - 70, yy, x1, yy + 24), 'Copy', ('copy', cmd))
        yy += 36
        ui.text((x0, yy), 'Activity', 12, bold=True)
        lb = (x0, yy + 18, x1, y1 - 44)
        ui.rect(lb, fill=(255, 255, 255), outline=(200, 208, 220))
        rows = list(srv.log)[-max(1, (lb[3] - lb[1] - 6) // 17):]
        for i, (t, who, what) in enumerate(rows):
            ui.text((lb[0] + 6, lb[1] + 4 + i * 17), '%s  %-15s %s' % (time.strftime('%H:%M:%S', time.localtime(t)),
                                                                       who, what), 11, kind='mono')
        if not rows:
            ui.text((lb[0] + 8, lb[1] + 8), 'Nobody has connected yet.', 11, color=theme.MUTED)
        ui.button((x1 - 160, y1 - 36, x1, y1 - 6), 'Stop sharing', 'stop', default=True)

    def draw_receive(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.wrap((x0, y0), 'Paste the address another COS computer (Neptune or v2.4) shows in its Pigeon window. Files '
                'land in Downloads/Pigeon, and running it again only fetches what changed.', x1 - x0, 12)
        yy = y0 + 46
        ui.textbox((x0, yy, x1 - 100, yy + 26), 'src', self.src, placeholder='http://192.168.1.20:8420/1a2b3c4d')
        busy = self.puller is not None and not self.puller.finished
        ui.button((x1 - 92, yy, x1, yy + 26), 'Cancel' if busy else 'Sync', 'cancel' if busy else 'pull', default=True,
                  enabled=busy or bool(self.src['text'].strip()))
        yy += 40
        p = self.puller
        if p is None:
            ui.text((x0, yy), 'Destination: %s' % RECEIVE_ROOT.replace(theme.ROOT, '~/cos/neptune'), 11, color=theme.MUTED)
            return
        ui.text((x0, yy), 'Destination: %s' % p.dest.replace(theme.ROOT, '~/cos/neptune'), 11, color=theme.MUTED)
        yy += 22
        bar = (x0, yy, x1, yy + 18)
        ui.rect(bar, fill=(230, 230, 230), outline=(188, 188, 188))
        frac = p.done / max(1, p.total) if p.total else (1.0 if p.finished else 0.0)
        fx = bar[0] + int((bar[2] - bar[0] - 2) * frac)
        if fx > bar[0] + 1:
            img.alpha_composite(gfx.vgradient(fx - bar[0] - 1, 16, ((0, (110, 220, 110)), (1, (20, 140, 30)))),
                                (bar[0] + 1, bar[1] + 1))
        yy += 28
        c = p.counts
        ui.text((x0, yy), '%d of %d files  ·  %d new  ·  %d updated  ·  %d already current' % (
            p.done, p.total, c['new'], c['changed'], c['same']), 12)
        yy += 24
        lb = (x0, yy, x1, y1 - (40 if p.finished and not p.error else 4))
        ui.rect(lb, fill=(30, 34, 40))
        rows = list(p.log)[-max(1, (lb[3] - lb[1] - 6) // 16):]
        for i, line in enumerate(rows):
            ui.text((lb[0] + 6, lb[1] + 4 + i * 16), gfx.ellipsize(gfx.font(11, False, 'mono'), line, lb[2] - lb[0] - 12),
                    11, kind='mono', color=(255, 150, 140) if line.startswith('error') else (200, 230, 210))
        if p.finished and not p.error:
            ui.button((x1 - 140, y1 - 32, x1, y1 - 4), 'Open folder', 'openfolder')

    # -- chat -------------------------------------------------------------------------------
    def draw_chat(self, img, ui, box):
        x0, y0, x1, y1 = box
        c = self.client
        if c is None:
            ui.text((x0, y0 + 10), 'Your name:', 12, anchor='lm')
            ui.textbox((x0 + 78, y0, x0 + 300, y0 + 24), 'chat_name', self.chat_name)
            ui.text((x0 + 312, y0 + 12), 'LAN only: keeps working without the internet.', 11, color=theme.MUTED,
                    anchor='lm')
            yy = y0 + 36
            ui.groupbox((x0, yy, x1, yy + 150), 'Chats on your network')
            ui.button((x1 - 100, yy + 18, x1 - 10, yy + 42), 'Searching...' if self.searching else 'Search',
                      'chat_search', enabled=not self.searching)
            ly = yy + 22
            if self.found is None:
                ui.text((x0 + 12, ly + 6), 'Press Search to look for Pigeon chats on this network.', 12,
                        color=theme.MUTED)
            elif not self.found:
                ui.text((x0 + 12, ly + 6), 'No chats found. Host one below, or join by address.', 12, color=theme.MUTED)
            for h in (self.found or [])[:4]:
                ui.listrow((x0 + 8, ly, x1 - 110, ly + 26), ('chat_pick', h['addr']), kind='item', data=h)
                ui.icon('pigeon', (x0 + 14, ly + 5), 16)
                ui.text((x0 + 38, ly + 13), '%s%s' % (h['title'], '  (password)' if h['locked'] else ''), 12,
                        anchor='lm')
                ui.text((x1 - 120, ly + 13), h['addr'], 11, color=theme.MUTED, anchor='rm')
                ly += 28
            yy += 162
            ui.groupbox((x0, yy, x1, yy + 66), 'Join by address')
            ui.textbox((x0 + 12, yy + 24, x1 - 300, yy + 48), 'chat_addr', self.chat_addr,
                       placeholder='192.168.1.20:8421')
            ui.textbox((x1 - 290, yy + 24, x1 - 110, yy + 48), 'chat_key', self.chat_key, placeholder='password (if any)',
                       password=True)
            ui.button((x1 - 100, yy + 24, x1 - 10, yy + 48), 'Join', 'chat_join', default=True,
                      enabled=bool(self.chat_addr['text'].strip()))
            yy += 78
            ui.groupbox((x0, yy, x1, yy + 66), 'Host a chat on this computer')
            ui.textbox((x0 + 12, yy + 24, x1 - 300, yy + 48), 'host_title', self.host_title,
                       placeholder='Room name (e.g. TCF backup)')
            ui.textbox((x1 - 290, yy + 24, x1 - 110, yy + 48), 'host_key', self.host_key,
                       placeholder='password (optional)', password=True)
            ui.button((x1 - 100, yy + 24, x1 - 10, yy + 48), 'Host', 'chat_host')
            return
        # connected
        hub = self.hub
        dot = (40, 170, 70) if c.ok else (200, 60, 40)
        ui.d.ellipse((x0, y0 + 5, x0 + 10, y0 + 15), fill=dot + (255,))
        ui.text((x0 + 18, y0 + 10), c.title or c.base, 14, bold=True, anchor='lm')
        sub = ('Hosting on this computer' if hub and hub.running else 'Joined %s' % c.base) + \
            ('' if c.ok else '  -  %s' % c.error)
        ui.text((x0 + 18, y0 + 30), gfx.ellipsize(gfx.font(11), sub, x1 - x0 - 200), 11, color=theme.MUTED, anchor='lm')
        ui.button((x1 - 90, y0, x1, y0 + 26), 'Stop' if hub and hub.running else 'Leave', 'chat_leave')
        top = y0 + 48
        if hub and hub.running:
            link = 'http://%s:%d/chat/' % (local_addresses()[0], hub.port)
            ui.text((x0, top + 8), 'Browser link (phones, COS v2.4):', 11, color=theme.MUTED, anchor='lm')
            ui.text((x0 + 196, top + 8), link, 11, kind='mono', anchor='lm')
            ui.button((x1 - 70, top - 2, x1, top + 20), 'Copy', ('copy', link), size=11)
            top += 28
        log = (x0, top, x1, y1 - 40)
        ui.rect(log, fill=(255, 255, 255), outline=(200, 208, 220))
        fnt, fb = gfx.font(12), gfx.font(12, True)
        width = log[2] - log[0] - 24
        lines = []
        for m in c.messages:
            head = '%s  %s' % (time.strftime('%H:%M', time.localtime(m.get('time', 0))), m.get('from', '?'))
            lines.append(('head', head, m.get('from') == self.chat_name['text'].strip()))
            for para in str(m.get('text', '')).split('\n'):
                cur = ''
                for word in para.split(' '):
                    t = (cur + ' ' + word) if cur else word
                    if gfx.text_width(fnt, t) > width and cur:
                        lines.append(('text', cur, False))
                        cur = word
                    else:
                        cur = t
                lines.append(('text', cur, False))
        rows = max(1, (log[3] - log[1] - 8) // 17)
        self.chat_rows_total = len(lines)
        self.chat_scroll = min(self.chat_scroll, max(0, len(lines) - rows))
        start = max(0, len(lines) - rows - self.chat_scroll)
        for i, (kind, text, mine) in enumerate(lines[start:start + rows]):
            yy = log[1] + 4 + i * 17
            if kind == 'head':
                ui.text((log[0] + 8, yy), text, 12, bold=True, color=(30, 110, 60) if mine else (30, 70, 140))
            else:
                ui.text((log[0] + 16, yy), text, 12)
        if not c.messages:
            ui.text(((log[0] + log[2]) // 2, (log[1] + log[3]) // 2), 'No messages yet.', 12, color=theme.MUTED,
                    anchor='mm')
        ui.textbox((x0, y1 - 30, x1 - 84, y1 - 4), 'chat_msg', self.chat_msg,
                   placeholder='Message as %s' % self.chat_name['text'].strip())
        ui.button((x1 - 76, y1 - 30, x1, y1 - 4), 'Send', 'chat_send', default=True)

    def chat_search(self):
        self.searching = True

        def run():
            try:
                self.found = chat.discover()
            finally:
                self.searching = False
                self.invalidate()
        threading.Thread(target=run, daemon=True).start()

    def chat_connect(self, addr, key=''):
        name = self.chat_name['text'].strip() or chat.default_name()
        self.shell.pigeon_client = chat.Client(addr, name, key)
        self.ui.focus = 'chat_msg'
        self.chat_scroll = 0

    def chat_host(self):
        hub = chat.Hub(title=self.host_title['text'].strip(), key=self.host_key['text'])
        if not hub.start():
            message(self.shell, 'Pigeon Chat', 'Could not host a chat:\n%s' % hub.error, 'error', parent=self.win)
            return
        self.shell.pigeon_hub = hub
        self.chat_connect('127.0.0.1:%d' % hub.port, hub.key)
        self.shell.notify('Pigeon Chat', 'Hosting "%s" on port %d.' % (hub.title, hub.port), 'pigeon', 5)

    def chat_leave(self):
        if self.client:
            self.client.stop()
        self.shell.pigeon_client = None
        if self.hub:
            self.hub.stop()
        self.shell.pigeon_hub = None

    def on_wheel(self, dy, x, y):
        if self.tab == 'chat':
            self.chat_scroll = max(0, min(getattr(self, 'chat_rows_total', 0), self.chat_scroll + dy * 3))

    # -- actions --------------------------------------------------------------------------
    def on_command(self, cid, data):
        if self.chat_command(cid, data):
            self.invalidate()
            return
        if isinstance(cid, tuple):
            if cid[0] == 'tab':
                self.tab = cid[1]
            elif cid[0] == 'folder':
                self.folder = cid[1]
                self.custom = {'text': '', 'cur': 0}
            elif cid[0] == 'copy':
                self.shell.clipboard = cid[1]
                self.shell.notify('Pigeon', 'Copied to the clipboard.', 'info', 2)
            elif cid[0] == 'open':
                self.shell.launch('webinator', url=cid[1])
        elif cid == 'token':
            self.use_token = data
        elif cid == 'start':
            self.start()
        elif cid == 'stop':
            self.stop()
        elif cid in ('pull',) or (cid == 'src' and data == 'enter'):
            self.pull()
        elif cid == 'cancel' and self.puller:
            self.puller.cancel = True
        elif cid == 'openfolder' and self.puller:
            self.shell.launch('files', path=self.puller.dest)
        self.invalidate()

    def chat_command(self, cid, data):
        if cid == 'chat_search':
            self.chat_search()
        elif cid == 'chat_join' or (cid in ('chat_addr', 'chat_key') and data == 'enter'):
            if self.chat_addr['text'].strip():
                self.chat_connect(self.chat_addr['text'], self.chat_key['text'])
        elif isinstance(cid, tuple) and cid[0] == 'chat_pick':
            h = data or {}
            self.chat_addr = {'text': cid[1], 'cur': len(cid[1])}
            if h.get('locked'):
                self.ui.focus = 'chat_key'
            else:
                self.chat_connect(cid[1])
        elif cid == 'chat_host' or (cid in ('host_title', 'host_key') and data == 'enter'):
            self.chat_host()
        elif cid == 'chat_leave':
            self.chat_leave()
        elif cid == 'chat_send' or (cid == 'chat_msg' and data == 'enter'):
            text = self.chat_msg['text'].strip()
            if text and self.client:
                self.client.name = self.chat_name['text'].strip() or self.client.name
                self.client.send(text)
                self.chat_msg = {'text': '', 'cur': 0}
                self.chat_scroll = 0
        else:
            return False
        return True

    def start(self):
        folder = self.share_folder()
        if not os.path.isdir(folder) and folder.startswith(theme.HOME):
            os.makedirs(folder, exist_ok=True)
        try:
            port = int(self.port['text'])
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            message(self.shell, 'Pigeon', 'The port must be a number from 1024 to 65535.', 'warning', parent=self.win)
            return
        srv = PigeonServer(folder, port, self.use_token)
        if not srv.start():
            message(self.shell, 'Pigeon', 'Pigeon could not start sharing:\n%s' % srv.error, 'error', parent=self.win)
            return
        self.server = self.shell.pigeon_server = srv
        self.shell.notify('Pigeon', 'Sharing %s at %s' % (os.path.basename(folder) or folder, srv.urls()[0]), 'info', 6)

    def stop(self):
        if self.server:
            self.server.stop()
        self.server = self.shell.pigeon_server = None

    def pull(self):
        src = self.src['text'].strip().rstrip('/')
        if not src:
            return
        if not src.startswith(('http://', 'https://')):
            src = 'http://' + src
        for suffix in ('/manifest.json', '/pull', '/pull.py'):
            if src.endswith(suffix):
                src = src[:-len(suffix)]
        host = urllib.parse.urlparse(src).netloc.split(':')[0] or 'share'
        dest = os.path.join(RECEIVE_ROOT, ''.join(c if c.isalnum() or c in '.-_' else '_' for c in host))
        self.puller = Puller(src, dest)
        threading.Thread(target=self.puller.run, daemon=True).start()

    def on_close(self):
        if self.server and self.server.running:
            def go(ans):
                if ans == 'Stop sharing':
                    self.stop()
                    self.close()
                elif ans == 'Keep sharing':
                    self.shell.close_window(self.win, force=True)
            message(self.shell, 'Pigeon', 'Pigeon is sharing %s. Stop sharing, or keep sharing in the background?'
                    % self.server.root.replace(theme.ROOT, '~/cos/neptune'), 'question',
                    buttons=('Stop sharing', 'Keep sharing', 'Cancel'), callback=go, parent=self.win)
            return False
        return True
