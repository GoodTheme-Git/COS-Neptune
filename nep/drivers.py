"""Device detection and .cdi drivers for Neptune.

Devices come from the same world-readable places COS v2.4 used:
/sys/bus/usb, /sys/bus/pci and /proc/bus/input/devices. Installed drivers
(home/.neptune/drivers, from .cdi packages) are matched against them with
v2.4's rules and each match runs on its own thread.

The `api` a driver gets is call-for-call the v2.4 DriverAPI, so v2.4 drivers
run unchanged. What a driver does arrives in Neptune as ordinary input:
api.key(10) is a press of Enter, api.pointer_move() moves Neptune's pointer,
api.click() clicks wherever the pointer is.
"""
import collections
import fcntl
import fnmatch
import os
import select
import struct
import threading
import time

from . import packages
from .compat.cursesshim import shim as curses

EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
BTN_LEFT, BTN_TOUCH = 0x110, 0x14a
EVENT_FMT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FMT)
ABSINFO_FMT = 'iiiiii'
ABSINFO_SIZE = struct.calcsize(ABSINFO_FMT)

INPUT_CAPS = {'gamepad': 0x130, 'joystick': 0x120, 'mouse': BTN_LEFT, 'keyboard': 30, 'touchpad': BTN_TOUCH}

PCI_CLASS_NAMES = {'00': 'Device', '01': 'Storage controller', '02': 'Network controller', '03': 'Display adapter',
                   '04': 'Multimedia controller', '05': 'Memory controller', '06': 'Bridge',
                   '07': 'Communication controller', '08': 'System peripheral', '09': 'Input controller',
                   '0c': 'Serial bus controller', '0d': 'Wireless controller'}

# Device Manager categories: (key, title, icon)
CATEGORIES = [('display', 'Display adapters', 'computer'), ('sound', 'Sound, video and game controllers', 'music'),
              ('gamepad', 'Game controllers', 'gamepad'), ('keyboard', 'Keyboards', 'keyboard'),
              ('mouse', 'Mice and other pointing devices', 'mouse'), ('network', 'Network adapters', 'network'),
              ('storage', 'Storage controllers', 'drive'), ('usb', 'Universal Serial Bus devices', 'usb'),
              ('input', 'Human Interface Devices', 'keyboard'), ('system', 'System devices', 'settings')]

CELL_PX = (9, 18)        # one v2.4 "character cell" of pointer travel, in Neptune pixels


def _sysread(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ''


def _bitmask(s):
    v = 0
    for word in s.split():
        v = (v << 64) | int(word, 16)
    return v


def _kernel_driver(sysdir):
    try:
        return os.path.basename(os.readlink(os.path.join(sysdir, 'driver')))
    except OSError:
        return ''


def category(dev):
    bus = dev['bus']
    if bus == 'pci':
        return {'03': 'display', '04': 'sound', '02': 'network', '0d': 'network', '01': 'storage',
                '0c': 'usb'}.get(dev.get('class', '')[:2], 'system')
    if bus == 'usb':
        return 'usb'
    caps = dev.get('caps', [])
    for c in ('gamepad', 'joystick'):
        if c in caps:
            return 'gamepad'
    if 'touchpad' in caps or 'mouse' in caps:
        return 'mouse'
    if 'keyboard' in caps:
        return 'keyboard'
    return 'input'


def scan_devices():
    out = []
    usb = '/sys/bus/usb/devices'
    try:
        for n in sorted(os.listdir(usb)):
            if ':' in n:
                continue
            d = os.path.join(usb, n)
            vid = _sysread(os.path.join(d, 'idVendor'))
            if not vid:
                continue
            name = ' '.join(x for x in (_sysread(os.path.join(d, 'manufacturer')),
                                        _sysread(os.path.join(d, 'product'))) if x)
            out.append({'bus': 'usb', 'id': 'usb:' + n, 'vendor': vid, 'product': _sysread(os.path.join(d, 'idProduct')),
                        'name': name or 'USB device %s' % n, 'class': _sysread(os.path.join(d, 'bDeviceClass')),
                        'sys': d, 'kernel': _kernel_driver(d)})
    except OSError:
        pass
    pci = '/sys/bus/pci/devices'
    try:
        for n in sorted(os.listdir(pci)):
            d = os.path.join(pci, n)
            cls = _sysread(os.path.join(d, 'class'))
            cls = cls[2:6] if cls.startswith('0x') else cls
            out.append({'bus': 'pci', 'id': 'pci:' + n, 'vendor': _sysread(os.path.join(d, 'vendor'))[2:],
                        'device': _sysread(os.path.join(d, 'device'))[2:], 'class': cls,
                        'name': PCI_CLASS_NAMES.get(cls[:2], 'PCI device') + ' ' + n, 'sys': d,
                        'kernel': _kernel_driver(d)})
    except OSError:
        pass
    try:
        with open('/proc/bus/input/devices') as f:
            blocks = f.read().split('\n\n')
    except OSError:
        blocks = []
    for blk in blocks:
        d = {'bus': 'input', 'name': '?', 'handlers': [], 'key': 0, 'vendor': '', 'product': '', 'phys': ''}
        for line in blk.splitlines():
            line = line.strip()
            if line.startswith('I: '):
                for kv in line[3:].split():
                    k, _, v = kv.partition('=')
                    if k in ('Vendor', 'Product'):
                        d[k.lower()] = v.lower()
            elif line.startswith('N: Name='):
                d['name'] = line[8:].strip().strip('"')
            elif line.startswith('P: Phys='):
                d['phys'] = line[8:].strip()
            elif line.startswith('H: Handlers='):
                d['handlers'] = line[12:].split()
            elif line.startswith('B: KEY='):
                d['key'] = _bitmask(line[7:])
        ev = next((h for h in d['handlers'] if h.startswith('event')), None)
        if not ev:
            continue
        d['path'] = '/dev/input/' + ev
        d['id'] = 'input:' + ev
        d['caps'] = [c for c, code in INPUT_CAPS.items() if d['key'] >> code & 1]
        d['readable'] = os.access(d['path'], os.R_OK)
        del d['key']
        out.append(d)
    for d in out:
        d['category'] = category(d)
    return out


def device_matches(rule, dev):
    if rule.get('bus') != dev.get('bus'):
        return False
    for k, want in rule.items():
        if k == 'bus':
            continue
        if k == 'capability':
            if want not in dev.get('caps', []):
                return False
            continue
        have, want = str(dev.get(k, '')).lower(), str(want).lower()
        if k == 'class':
            if not have.startswith(want):
                return False
        elif not fnmatch.fnmatch(have, want):
            return False
    return True


# =============================================================================
# the api a driver gets
# =============================================================================
class DriverAPI:
    EV_KEY, EV_REL, EV_ABS, EV_SYN = EV_KEY, EV_REL, EV_ABS, EV_SYN

    def __init__(self, host, device):
        self._host = host
        self.device = device
        self.state = dict(host.shell.settings)
        self.stopping = threading.Event()
        self.curses = curses
        self.status = 'starting'
        self._frac = [0.0, 0.0]

    def key(self, code):
        if isinstance(code, str):
            for ch in code:
                self._host.post(('key', ord(ch)))
        else:
            self._host.post(('key', int(code)))

    def pointer_move(self, dx, dy):
        self._frac[0] += dx * CELL_PX[0]
        self._frac[1] += dy * CELL_PX[1]
        ix, iy = int(self._frac[0]), int(self._frac[1])
        if ix or iy:
            self._frac[0] -= ix
            self._frac[1] -= iy
            self._host.post(('move', ix, iy))

    def pointer_button(self, btn, down):
        self._host.post(('button', int(btn), bool(down)))

    def click(self, btn=1):
        self._host.post(('button', int(btn), True))
        self._host.post(('button', int(btn), False))

    def scroll(self, n):
        self._host.post(('scroll', int(n)))

    def notify(self, msg, kind=None):
        self._host.post(('notify', str(msg), kind, self.device.get('name', '')))

    def set_status(self, text):
        self.status = str(text)

    def evdev(self, path=None, grab=False, tick=0.25):
        path = path or self.device.get('path')
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        try:
            if grab:
                try:
                    fcntl.ioctl(fd, 0x40044590, 1)
                except OSError:
                    pass
            buf = b''
            while not self.stopping.is_set():
                r, _w, _x = select.select([fd], [], [], tick)
                if not r:
                    yield None
                    continue
                try:
                    chunk = os.read(fd, EVENT_SIZE * 64)
                except BlockingIOError:
                    continue
                if not chunk:
                    return
                buf += chunk
                while len(buf) >= EVENT_SIZE:
                    pkt, buf = buf[:EVENT_SIZE], buf[EVENT_SIZE:]
                    _s, _us, etype, code, value = struct.unpack(EVENT_FMT, pkt)
                    yield etype, code, value
        finally:
            os.close(fd)

    def absinfo(self, code, path=None):
        try:
            fd = os.open(path or self.device.get('path'), os.O_RDONLY)
        except OSError:
            return None
        try:
            buf = bytearray(ABSINFO_SIZE)
            fcntl.ioctl(fd, 0x80184540 + code, buf, True)
            _v, lo, hi, _f, _fl, _r = struct.unpack(ABSINFO_FMT, bytes(buf))
            return (lo, hi) if hi > lo else None
        except OSError:
            return None
        finally:
            os.close(fd)


class DriverInstance:
    def __init__(self, host, rec, device):
        self.host = host
        self.rec = rec
        self.device = device
        self.api = DriverAPI(host, device)
        self.thread = None
        self.error = ''

    @property
    def label(self):
        return '%s %s' % (self.rec.get('title') or self.rec['name'], self.rec.get('version', ''))

    @property
    def status(self):
        if self.error:
            return 'error: ' + self.error
        if self.thread and self.thread.is_alive():
            return self.api.status if self.api.status != 'starting' else 'running'
        return 'stopped'

    def start(self):
        d = self.rec['driver']
        path = os.path.join(packages.DRIVER_DIR, self.rec['name'], d['module'])
        try:
            with open(path, encoding='utf-8') as f:
                src = f.read()
            ns = {'__name__': 'cosdriver_' + self.rec['name'].replace('-', '_').replace('.', '_'), '__file__': path,
                  'curses': curses}
            exec(compile(src, path, 'exec'), ns)
            cls = ns.get(d.get('class', 'Driver'))
            if not isinstance(cls, type):
                raise TypeError('no class %s in %s' % (d.get('class', 'Driver'), d['module']))
            obj = cls(self.api, self.device)
        except Exception as e:
            self.error = '%s: %s' % (type(e).__name__, e)
            return False
        self.thread = threading.Thread(target=self._run, args=(obj,), daemon=True,
                                       name='nep-driver-' + self.rec['name'])
        self.thread.start()
        return True

    def _run(self, obj):
        try:
            obj.run()
        except Exception as e:
            self.error = '%s: %s' % (type(e).__name__, e)
            self.host.post(('notify', 'Driver stopped: %s' % self.error, 'error', self.label))

    def stop(self):
        self.api.stopping.set()
        if self.thread:
            self.thread.join(timeout=2.0)


class DriverHost:
    """Matches installed .cdi drivers to devices, runs them, and turns what
    they do into Neptune input."""

    def __init__(self, shell, start=True):
        self.shell = shell
        self.queue = collections.deque(maxlen=2048)
        self.instances = []
        self.devices = []
        self.disabled = bool(os.environ.get('NEPTUNE_NO_DRIVERS') or os.environ.get('COS_NO_DRIVERS'))
        self.buttons_down = set()
        self.started = start
        self.last_watch = time.time()
        if start:
            self.rescan()

    # -- plug-and-play -----------------------------------------------------------------
    def tick(self, now):
        """Every few seconds: notice devices plugged in or pulled out, start drivers
        for the new ones, stop drivers whose device left, and say so."""
        if not self.started or now - self.last_watch < 2.5:
            return
        self.last_watch = now
        try:
            fresh = scan_devices()
        except Exception:
            return
        old = {d['id']: d for d in self.devices}
        new = {d['id']: d for d in fresh}
        added = [new[i] for i in new if i not in old]
        removed = [old[i] for i in old if i not in new]
        if not added and not removed:
            return
        self.devices = fresh
        for inst in list(self.instances):
            if inst.device['id'] not in new:
                inst.stop()
                self.instances.remove(inst)
        if added and not self.off:
            claimed = {i.device['id'] for i in self.instances}
            for rec in sorted(packages.installed_packages('cdi'), key=lambda r: r['name'].lower()):
                for dev in added:
                    if dev['id'] in claimed:
                        continue
                    if any(device_matches(rule, dev) for rule in rec.get('driver', {}).get('match', [])):
                        inst = DriverInstance(self, rec, dev)
                        claimed.add(dev['id'])
                        self.instances.append(inst)
                        inst.start()
        self.announce(added, True)
        self.announce(removed, False)
        for w in self.shell.windows:            # keep an open Device Manager current
            if hasattr(w.app, 'devices') and w.app.__class__.__name__ == 'DeviceManager':
                w.app.devices = self.devices
                w.app.invalidate()

    def announce(self, devs, plugged):
        """One balloon per physical thing: USB devices by name; input-only devices
        (Bluetooth, built-in) when no USB device explains them."""
        usb = [d for d in devs if d['bus'] == 'usb' and d.get('class') != '09']   # not the hubs
        shown = usb or [d for d in devs if d['bus'] == 'input' and d['category'] in ('keyboard', 'mouse', 'gamepad')]
        names = []
        for d in shown:
            n = d.get('name') or d['id']
            if n not in names:
                names.append(n)
        for n in names[:3]:
            drv = [i.label.strip() for i in self.instances if i.device.get('name') == n]
            self.shell.notify('Device connected' if plugged else 'Device disconnected',
                              n + ('  ·  driver: %s' % drv[0] if plugged and drv else ''), 'info', 4)

    def post(self, item):
        self.queue.append(item)

    def stop_all(self):
        for inst in self.instances:
            inst.stop()
        self.instances = []

    @property
    def off(self):
        """Safe mode: the environment, or Control Panel > Devices > Safe mode."""
        return self.disabled or bool(self.shell.settings.get('drivers_off'))

    def rescan(self):
        self.stop_all()
        self.devices = scan_devices()
        if self.off:
            return
        claimed = set()
        for rec in sorted(packages.installed_packages('cdi'), key=lambda r: r['name'].lower()):
            for dev in self.devices:
                if dev['id'] in claimed:
                    continue
                if any(device_matches(rule, dev) for rule in rec.get('driver', {}).get('match', [])):
                    inst = DriverInstance(self, rec, dev)
                    claimed.add(dev['id'])
                    self.instances.append(inst)
                    inst.start()

    def driver_for(self, dev):
        for inst in self.instances:
            if inst.device['id'] == dev['id']:
                return inst
        return None

    # -- draining into the shell ----------------------------------------------------
    NAMED = {10: 'enter', 13: 'enter', 27: 'escape', 9: 'tab', 127: 'backspace', 8: 'backspace'}

    def key_event(self, code):
        c = curses
        named = {c.KEY_UP: 'up', c.KEY_DOWN: 'down', c.KEY_LEFT: 'left', c.KEY_RIGHT: 'right', c.KEY_HOME: 'home',
                 c.KEY_END: 'end', c.KEY_PPAGE: 'pageup', c.KEY_NPAGE: 'pagedown', c.KEY_DC: 'delete',
                 c.KEY_IC: 'insert', c.KEY_BACKSPACE: 'backspace', c.KEY_ENTER: 'enter', c.KEY_BTAB: 'tab'}
        named.update(self.NAMED)
        if code == 6:                       # v2.4's Ctrl+F "open the menus" -> Neptune's menu key
            return {'type': 'key', 'key': 'f10', 'char': ''}
        if code in named:
            return {'type': 'key', 'key': named[code], 'char': '', 'shift': code == c.KEY_BTAB}
        if c.KEY_F0 < code <= c.KEY_F0 + 12:
            return {'type': 'key', 'key': 'f%d' % (code - c.KEY_F0), 'char': ''}
        if code == 32:
            return {'type': 'key', 'key': 'space', 'char': ' '}
        if 1 <= code <= 26:
            return {'type': 'key', 'key': chr(code + 96), 'char': '', 'ctrl': True}
        if 32 < code < 0x110000 and code not in range(256, 600):
            ch = chr(code)
            return {'type': 'key', 'key': ch.lower(), 'char': ch, 'shift': ch.isupper()}
        return None

    def drain(self):
        sh = self.shell
        n = 0
        while self.queue and n < 256:
            item = self.queue.popleft()
            n += 1
            kind = item[0]
            try:
                if kind == 'key':
                    ev = self.key_event(item[1])
                    if ev:
                        sh.handle(ev)
                elif kind == 'move':
                    mx, my = sh.mouse
                    x = max(0, min(sh.W - 1, mx + item[1]))
                    y = max(0, min(sh.H - 1, my + item[2]))
                    sh.handle({'type': 'move', 'x': x, 'y': y})
                elif kind == 'button':
                    mx, my = sh.mouse
                    sh.handle({'type': 'down' if item[2] else 'up', 'x': mx, 'y': my, 'button': item[1]})
                elif kind == 'scroll':
                    sh.handle({'type': 'wheel', 'dy': -item[1]})
                elif kind == 'notify':
                    _k, msg, level, who = item
                    sh.notify(who or 'Driver', msg, {'warn': 'warning', 'error': 'error'}.get(level, 'info'), 6)
            except Exception:
                pass
