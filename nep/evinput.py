"""Raw evdev input for GDR mode: keyboards, mice and touchpads.

The user must be able to read /dev/input/event* (the 'input' group). Events
come out in the same dict shape the pygame window backend produces, so the
shell cannot tell where they came from.
"""
import fcntl
import os
import select
import struct
import time

EVENT_FMT = 'llHHi'
EVENT_SIZE = struct.calcsize(EVENT_FMT)
EV_SYN, EV_KEY, EV_REL, EV_ABS = 0, 1, 2, 3
REL_X, REL_Y, REL_WHEEL = 0, 1, 8
ABS_X, ABS_Y = 0, 1
ABS_MT_POSITION_X = 0x35
BTN_LEFT, BTN_RIGHT, BTN_MIDDLE = 0x110, 0x111, 0x112
BTN_TOUCH = 0x14a
BTN_TOOL_FINGER, BTN_TOOL_DOUBLETAP, BTN_TOOL_TRIPLETAP = 0x145, 0x14d, 0x14e
KEY_A = 30

# US layout: code -> (normal, shifted)
_CHARS = {
    2: ('1', '!'), 3: ('2', '@'), 4: ('3', '#'), 5: ('4', '$'), 6: ('5', '%'), 7: ('6', '^'),
    8: ('7', '&'), 9: ('8', '*'), 10: ('9', '('), 11: ('0', ')'), 12: ('-', '_'), 13: ('=', '+'),
    16: ('q', 'Q'), 17: ('w', 'W'), 18: ('e', 'E'), 19: ('r', 'R'), 20: ('t', 'T'), 21: ('y', 'Y'),
    22: ('u', 'U'), 23: ('i', 'I'), 24: ('o', 'O'), 25: ('p', 'P'), 26: ('[', '{'), 27: (']', '}'),
    30: ('a', 'A'), 31: ('s', 'S'), 32: ('d', 'D'), 33: ('f', 'F'), 34: ('g', 'G'), 35: ('h', 'H'),
    36: ('j', 'J'), 37: ('k', 'K'), 38: ('l', 'L'), 39: (';', ':'), 40: ("'", '"'), 41: ('`', '~'),
    43: ('\\', '|'), 44: ('z', 'Z'), 45: ('x', 'X'), 46: ('c', 'C'), 47: ('v', 'V'), 48: ('b', 'B'),
    49: ('n', 'N'), 50: ('m', 'M'), 51: (',', '<'), 52: ('.', '>'), 53: ('/', '?'), 57: (' ', ' '),
    55: ('*', '*'), 74: ('-', '-'), 78: ('+', '+'), 98: ('/', '/'),
    71: ('7', '7'), 72: ('8', '8'), 73: ('9', '9'), 75: ('4', '4'), 76: ('5', '5'), 77: ('6', '6'),
    79: ('1', '1'), 80: ('2', '2'), 81: ('3', '3'), 82: ('0', '0'), 83: ('.', '.'),
}
_NAMES = {
    1: 'escape', 14: 'backspace', 15: 'tab', 28: 'enter', 96: 'enter', 57: 'space',
    29: 'ctrl', 97: 'ctrl', 42: 'shift', 54: 'shift', 56: 'alt', 100: 'alt', 125: 'super', 126: 'super',
    58: 'capslock', 59: 'f1', 60: 'f2', 61: 'f3', 62: 'f4', 63: 'f5', 64: 'f6', 65: 'f7', 66: 'f8',
    67: 'f9', 68: 'f10', 87: 'f11', 88: 'f12', 102: 'home', 103: 'up', 104: 'pageup', 105: 'left',
    106: 'right', 107: 'end', 108: 'down', 109: 'pagedown', 110: 'insert', 111: 'delete',
    127: 'menu', 99: 'printscreen',
}


def _bitmask(s):
    v = 0
    for part in s.split():
        v = (v << 64) | int(part, 16)
    return v


def _has(mask, bit):
    return bool((mask >> bit) & 1)


def scan():
    """[(path, kind)] for keyboards, mice and touchpads."""
    out = []
    try:
        blocks = open('/proc/bus/input/devices').read().split('\n\n')
    except OSError:
        return out
    for blk in blocks:
        name, handlers, ev, key, abs_, rel = '', [], 0, 0, 0, 0
        for line in blk.splitlines():
            line = line.strip()
            if line.startswith('N: Name='):
                name = line[8:].strip('"')
            elif line.startswith('H: Handlers='):
                handlers = line[12:].split()
            elif line.startswith('B: EV='):
                ev = _bitmask(line[6:])
            elif line.startswith('B: KEY='):
                key = _bitmask(line[7:])
            elif line.startswith('B: ABS='):
                abs_ = _bitmask(line[7:])
            elif line.startswith('B: REL='):
                rel = _bitmask(line[7:])
        evn = next((h for h in handlers if h.startswith('event')), None)
        if not evn:
            continue
        path = '/dev/input/' + evn
        if _has(abs_, ABS_X) and _has(key, BTN_TOUCH):
            out.append((path, 'touchpad', name))
        elif _has(rel, REL_X) and _has(rel, REL_Y):
            out.append((path, 'mouse', name))
        elif _has(key, KEY_A) and _has(key, 28) and _has(key, 44):
            out.append((path, 'keyboard', name))
    return out


def _absinfo(fd, code):
    req = (2 << 30) | (24 << 16) | (ord('E') << 8) | (0x40 + code)
    buf = bytearray(24)
    try:
        fcntl.ioctl(fd, req, buf, True)
        return struct.unpack('6i', bytes(buf))   # value, min, max, fuzz, flat, resolution
    except OSError:
        return None


class Device:
    def __init__(self, path, kind, name):
        self.path, self.kind, self.name = path, kind, name
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        self.buf = b''
        # touchpad state
        self.ax = _absinfo(self.fd, ABS_X) if kind == 'touchpad' else None
        self.ay = _absinfo(self.fd, ABS_Y) if kind == 'touchpad' else None
        self.touch = False
        self.fingers = 1
        self.last = None
        self.cur = [None, None]
        self.t0 = 0
        self.travel = 0.0
        self.max_fingers = 0
        self.scroll_acc = 0.0
        self.rel = [0, 0, 0]


# Live options the shell keeps in step with Settings (pointer speed, tap to click).
OPTIONS = {'speed': None, 'tap': True}


class EvInput:
    def __init__(self, width, height, speed=1.0):
        self.W, self.H = width, height
        self.x, self.y = width // 2, height // 2
        self.speed = speed
        self.devices = []
        self.errors = []
        self.mods = {'ctrl': False, 'alt': False, 'shift': False, 'super': False}
        self.caps = False
        self.last_scan = 0.0
        self.rescan()

    def rescan(self):
        """Open devices that appeared since last time (plug-and-play). Called every
        couple of seconds from poll(); unplugged ones are dropped as they fail."""
        self.last_scan = time.time()
        have = {d.path for d in self.devices}
        added = []
        for path, kind, name in scan():
            if path in have:
                continue
            try:
                self.devices.append(Device(path, kind, name))
                added.append(path)
            except OSError as e:
                msg = '%s: %s' % (path, e.strerror)
                if msg not in self.errors:
                    self.errors.append(msg)
        return added

    def drop(self, d):
        try:
            os.close(d.fd)
        except OSError:
            pass
        if d in self.devices:
            self.devices.remove(d)
        if d.kind == 'keyboard':           # a key held while unplugging must not stay held
            for k in self.mods:
                self.mods[k] = False

    @property
    def has_keyboard(self):
        return any(d.kind == 'keyboard' for d in self.devices)

    def fileno_list(self):
        return [d.fd for d in self.devices]

    def poll(self, timeout):
        if OPTIONS.get('speed'):
            self.speed = OPTIONS['speed']
        if time.time() - self.last_scan > 2.0:
            self.rescan()
        if not self.devices:
            time.sleep(timeout)
            return []
        try:
            r, _, _ = select.select(self.fileno_list(), [], [], max(0, timeout))
        except (OSError, ValueError):
            return []
        out = []
        for d in self.devices:
            if d.fd not in r:
                continue
            try:
                data = os.read(d.fd, EVENT_SIZE * 64)
            except BlockingIOError:
                continue
            except OSError:
                self.drop(d)                   # unplugged (ENODEV): forget it; rescan() finds it again
                continue
            if not data:
                self.drop(d)
                continue
            d.buf += data
            while len(d.buf) >= EVENT_SIZE:
                _s, _us, typ, code, val = struct.unpack(EVENT_FMT, d.buf[:EVENT_SIZE])
                d.buf = d.buf[EVENT_SIZE:]
                self._event(d, typ, code, val, out)
        return out

    def _move(self, dx, dy, out):
        nx = min(self.W - 1, max(0, self.x + dx))
        ny = min(self.H - 1, max(0, self.y + dy))
        if (int(nx), int(ny)) != (int(self.x), int(self.y)):
            self.x, self.y = nx, ny
            out.append({'type': 'move', 'x': int(self.x), 'y': int(self.y)})
        else:
            self.x, self.y = nx, ny

    def _button(self, btn, down, out):
        out.append({'type': 'down' if down else 'up', 'x': int(self.x), 'y': int(self.y), 'button': btn})

    def _event(self, d, typ, code, val, out):
        if typ == EV_KEY:
            if code in (BTN_LEFT, BTN_RIGHT, BTN_MIDDLE):
                btn = {BTN_LEFT: 1, BTN_MIDDLE: 2, BTN_RIGHT: 3}[code]
                if d.kind == 'touchpad' and code == BTN_LEFT and d.fingers >= 2:
                    btn = 3     # clickpad: two-finger press is a right click
                if d.kind == 'touchpad':
                    d.travel = 999   # a physical click is not also a tap
                if val in (0, 1):
                    self._button(btn, val == 1, out)
                return
            if d.kind == 'touchpad':
                if code == BTN_TOUCH:
                    if val:
                        d.touch, d.t0, d.travel, d.last, d.max_fingers = True, time.time(), 0.0, None, d.fingers
                    else:
                        d.touch = False
                        dt = time.time() - d.t0
                        if dt < 0.2 and d.travel < 12 and OPTIONS.get('tap', True):
                            btn = 3 if d.max_fingers >= 2 else 1
                            self._button(btn, True, out)
                            self._button(btn, False, out)
                        d.last = None
                elif code == BTN_TOOL_FINGER and val:
                    d.fingers = 1
                elif code == BTN_TOOL_DOUBLETAP:
                    d.fingers = 2 if val else 1
                    d.last = None
                elif code == BTN_TOOL_TRIPLETAP:
                    d.fingers = 3 if val else 1
                d.max_fingers = max(d.max_fingers, d.fingers if d.touch else 0)
                return
            self._key(code, val, out)
        elif typ == EV_REL:
            if code == REL_X:
                d.rel[0] += val
            elif code == REL_Y:
                d.rel[1] += val
            elif code == REL_WHEEL:
                out.append({'type': 'wheel', 'dy': val})
        elif typ == EV_ABS and d.kind == 'touchpad':
            if code == ABS_X:
                d.cur[0] = val
            elif code == ABS_Y:
                d.cur[1] = val
        elif typ == EV_SYN:
            if d.rel[0] or d.rel[1]:
                f = self.speed * (1.0 + min(2.0, (abs(d.rel[0]) + abs(d.rel[1])) / 12.0))
                self._move(d.rel[0] * f, d.rel[1] * f, out)
                d.rel = [0, 0, 0]
            if d.kind == 'touchpad' and d.touch and None not in d.cur:
                if d.last is not None:
                    rx = (d.ax[2] - d.ax[1]) if d.ax else 1000
                    sc = self.W / max(1, rx) * 1.6 * self.speed
                    dx, dy = (d.cur[0] - d.last[0]) * sc, (d.cur[1] - d.last[1]) * sc
                    d.travel += abs(dx) + abs(dy)
                    if d.fingers >= 2:
                        d.scroll_acc += dy
                        while abs(d.scroll_acc) >= 28:
                            step = 1 if d.scroll_acc > 0 else -1
                            out.append({'type': 'wheel', 'dy': step})   # natural-ish
                            d.scroll_acc -= 28 * step
                    else:
                        self._move(dx, dy, out)
                d.last = tuple(d.cur)

    def _key(self, code, val, out):
        name = _NAMES.get(code)
        if name in ('ctrl', 'alt', 'shift', 'super'):
            self.mods[name] = val != 0
        if code == 58 and val == 1:
            self.caps = not self.caps
        if val == 0:
            out.append(dict(type='keyup', key=name or '?', char='', **self.mods))
            return
        ch = ''
        if code in _CHARS:
            lo, hi = _CHARS[code]
            shifted = self.mods['shift']
            if lo.isalpha() and self.caps:
                shifted = not shifted
            ch = hi if shifted else lo
            if not name:
                name = lo if lo != ' ' else 'space'
        if self.mods['ctrl'] or self.mods['alt'] or self.mods['super']:
            ch = '' if name != 'space' else ch
            if self.mods['ctrl'] or self.mods['super']:
                ch = ''
        out.append(dict(type='key', key=name or '?', char=ch, **self.mods))

    def close(self):
        for d in self.devices:
            try:
                os.close(d.fd)
            except OSError:
                pass
        self.devices = []
