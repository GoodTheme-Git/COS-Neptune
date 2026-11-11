"""Neptune display backends.

  GDR (Graphics Desktop Rendering)  -- Neptune owns the screen: console in
        KD_GRAPHICS, DRM master on the card, a dumb buffer at the panel's
        preferred mode. Falls back to /dev/fb0 for chips with no KMS driver.
        Same lineage as COS v2.4's DrmDisplay/FbDisplay, but here it is the
        only renderer: there is no curses underneath, every pixel is ours.
  window -- a pygame window, for running Neptune inside an X/Wayland session.
  headless -- renders into memory (screenshots, tests).

Every backend exposes: width, height, present(img, rect), close(), and
poll(timeout) -> list of input events (window backend only; GDR reads evdev).
"""
import atexit
import ctypes
import fcntl
import mmap
import os
import signal
import struct
import sys
import termios

import numpy as np

# -- console ------------------------------------------------------------------
KDSETMODE = 0x4B3A
KDGETMODE = 0x4B3B
KD_TEXT = 0
KD_GRAPHICS = 1


def _console_fd():
    cands = ['/dev/tty']
    vt = os.environ.get('XDG_VTNR', '')
    if vt.isdigit():
        cands.append('/dev/tty%s' % vt)
    for dev in cands:
        try:
            return os.open(dev, os.O_RDWR | os.O_NOCTTY)
        except OSError:
            continue
    return None


def set_console_graphics(on):
    fd = _console_fd()
    if fd is None:
        return False
    try:
        fcntl.ioctl(fd, KDSETMODE, KD_GRAPHICS if on else KD_TEXT)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def on_real_vt():
    """True when stdin is a Linux virtual console (not a pty / X terminal)."""
    try:
        name = os.ttyname(sys.stdin.fileno())
    except OSError:
        return False
    return name.startswith('/dev/tty') and name[8:].isdigit()


class Console:
    """Takes the VT for GDR: KD_GRAPHICS + raw, silent keyboard. Paranoid
    about giving it back -- exit, atexit and the fatal signals all restore."""

    SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT)

    def __init__(self):
        self.graphics = False
        self.saved_tc = None
        self.restored = False

    def take(self):
        atexit.register(self.restore)
        for sig in self.SIGNALS:
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass
        if sys.stdin.isatty():
            try:
                fd = sys.stdin.fileno()
                self.saved_tc = termios.tcgetattr(fd)
                new = termios.tcgetattr(fd)
                new[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG)
                termios.tcsetattr(fd, termios.TCSANOW, new)
            except termios.error:
                self.saved_tc = None
        self.graphics = set_console_graphics(True)
        return self.graphics

    def _on_signal(self, signum, frame):
        self.restore()
        raise SystemExit(128 + signum)

    def restore(self, *_a):
        if self.restored:
            return
        self.restored = True
        if self.graphics:
            set_console_graphics(False)
        if self.saved_tc is not None:
            try:
                fd = sys.stdin.fileno()
                termios.tcflush(fd, termios.TCIFLUSH)   # don't dump our keys into the shell
                termios.tcsetattr(fd, termios.TCSANOW, self.saved_tc)
            except (termios.error, OSError, ValueError):
                pass


# -- DRM / KMS ----------------------------------------------------------------
DRM_IOCTL_BASE = 0x64


def _drm_iowr(nr, size):
    return (3 << 30) | (size << 16) | (DRM_IOCTL_BASE << 8) | nr


def _drm_io(nr):
    return (DRM_IOCTL_BASE << 8) | nr


DRM_SET_MASTER = _drm_io(0x1e)
DRM_DROP_MASTER = _drm_io(0x1f)
DRM_GET_CAP = _drm_iowr(0x0c, 16)
DRM_MODE_RES = _drm_iowr(0xA0, 64)
DRM_MODE_GETCRTC = _drm_iowr(0xA1, 104)
DRM_MODE_SETCRTC = _drm_iowr(0xA2, 104)
DRM_MODE_GETCONN = _drm_iowr(0xA7, 80)
DRM_MODE_ADDFB = _drm_iowr(0xAE, 28)
DRM_MODE_RMFB = _drm_iowr(0xAF, 4)
DRM_MODE_CREATE_DUMB = _drm_iowr(0xB2, 32)
DRM_MODE_MAP_DUMB = _drm_iowr(0xB3, 16)
DRM_MODE_DESTROY_DUMB = _drm_iowr(0xB4, 4)
DRM_CAP_DUMB_BUFFER = 1
DRM_MODE_CONNECTED = 1
DRM_MODE_TYPE_PREFERRED = 0x8
MODEINFO_FMT = '<I10H3I32s'
MODEINFO_SIZE = 68


class _Mapped:
    """Shared presentation code for anything that is an mmap'd XRGB buffer."""
    kind = 'gdr'

    def _view(self):
        if getattr(self, '_arr', None) is None:
            # DRM dumb buffers are padded to whole pages, so use only height * pitch bytes of the mapping
            self._arr = np.frombuffer(self.mm, dtype=np.uint8, count=self.height * self.pitch).reshape(self.height, self.pitch)
        return self._arr

    def present(self, img, rect=None):
        W, H = self.width, self.height
        if rect is None:
            rect = (0, 0, W, H)
        x0, y0, x1, y1 = (max(0, int(rect[0])), max(0, int(rect[1])),
                          min(W, int(rect[2])), min(H, int(rect[3])))
        if x1 <= x0 or y1 <= y0:
            return
        data = img.crop((x0, y0, x1, y1)).tobytes('raw', 'BGRA')
        src = np.frombuffer(data, dtype=np.uint8).reshape(y1 - y0, (x1 - x0) * 4)
        self._view()[y0:y1, x0 * 4:x1 * 4] = src

    def poll(self, timeout):
        return []


class DrmDisplay(_Mapped):
    CONN_TYPES = {1: 'VGA', 2: 'DVI-I', 3: 'DVI-D', 7: 'LVDS', 10: 'DP', 11: 'HDMI-A',
                  14: 'eDP', 15: 'Virtual', 16: 'DSI'}

    def __init__(self, want=None):
        self.want = want            # (w, h, refresh or 0) or None for the panel's preferred mode
        self.fd = -1
        self.width = self.height = self.pitch = self.size = 0
        self.handle = self.fb_id = self.crtc_id = self.conn_id = 0
        self.mode = b''
        self.name = ''
        self.mm = None
        self.master = False
        self.saved_crtc = None
        self.error = ''
        self._arr = None

    @staticmethod
    def candidates():
        try:
            return ['/dev/dri/' + n for n in sorted(os.listdir('/dev/dri')) if n.startswith('card')]
        except OSError:
            return []

    def _resources(self):
        buf = bytearray(64)
        fcntl.ioctl(self.fd, DRM_MODE_RES, buf, True)
        f = struct.unpack('QQQQ8I', bytes(buf))
        ncrtc, nconn = f[5], f[6]
        crtcs = (ctypes.c_uint32 * max(1, ncrtc))()
        conns = (ctypes.c_uint32 * max(1, nconn))()
        buf = bytearray(struct.pack('QQQQ8I', 0, ctypes.addressof(crtcs), ctypes.addressof(conns), 0,
                                    0, ncrtc, nconn, 0, 0, 0, 0, 0))
        fcntl.ioctl(self.fd, DRM_MODE_RES, buf, True)
        return list(crtcs)[:ncrtc], list(conns)[:nconn]

    def _connector(self, cid):
        buf = bytearray(struct.pack('<QQQQ12I', 0, 0, 0, 0, 0, 0, 0, 0, cid, 0, 0, 0, 0, 0, 0, 0))
        fcntl.ioctl(self.fd, DRM_MODE_GETCONN, buf, True)
        v = struct.unpack('<QQQQ12I', bytes(buf))
        nmodes = v[4]
        if not nmodes:
            return None
        raw = bytearray(MODEINFO_SIZE * nmodes)
        cbuf = (ctypes.c_char * len(raw)).from_buffer(raw)
        buf = bytearray(struct.pack('<QQQQ12I', 0, ctypes.addressof(cbuf), 0, 0,
                                    nmodes, 0, 0, 0, cid, 0, 0, 0, 0, 0, 0, 0))
        fcntl.ioctl(self.fd, DRM_MODE_GETCONN, buf, True)
        v = struct.unpack('<QQQQ12I', bytes(buf))
        if v[11] != DRM_MODE_CONNECTED:
            return None
        # drm_mode_modeinfo: clock, hdisplay, hsync_start, hsync_end, htotal, hskew,
        # vdisplay, vsync_start, vsync_end, vtotal, vscan, vrefresh, flags, type, name
        # (COS v2.4 read flags as type and type as refresh -- fixed here)
        best = None
        want = self.want
        for i in range(min(v[4], nmodes)):
            blob = bytes(raw[i * MODEINFO_SIZE:(i + 1) * MODEINFO_SIZE])
            m = struct.unpack(MODEINFO_FMT, blob)
            w, h, hz, typ = m[1], m[6], m[11], m[13]
            if want:
                match = (w, h) == want[:2] and (not want[2] or hz == want[2])
                score = (1 if match else 0, 1 if (w, h) == want[:2] else 0, hz if match else 0,
                         1 if typ & DRM_MODE_TYPE_PREFERRED else 0)
            else:
                score = (1 if typ & DRM_MODE_TYPE_PREFERRED else 0, w * h, hz)
            if best is None or score > best[0]:
                best = (score, blob, m)
        if best is None:
            return None
        _s, blob, m = best
        if want and (m[1], m[6]) != want[:2]:
            self.error = 'mode %dx%d not offered; using %dx%d' % (want[0], want[1], m[1], m[6])
        return {'id': cid, 'blob': blob, 'w': m[1], 'h': m[6], 'refresh': m[11],
                'name': '%s-%d' % (self.CONN_TYPES.get(v[9], '?'), v[10])}

    def _crtc_for(self):
        crtcs, _ = self._resources()
        for crid in crtcs:
            buf = bytearray(104)
            struct.pack_into('<QIIIIIII', buf, 0, 0, 0, crid, 0, 0, 0, 0, 0)
            try:
                fcntl.ioctl(self.fd, DRM_MODE_GETCRTC, buf, True)
            except OSError:
                continue
            v = struct.unpack('<QIIIIIII', bytes(buf[:36]))
            if v[7]:
                return crid, bytes(buf)
        return (crtcs[0], None) if crtcs else (0, None)

    def open(self):
        for path in self.candidates():
            try:
                fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
            except OSError as e:
                self.error = '%s: %s' % (path, e.strerror)
                continue
            self.fd = fd
            try:
                capbuf = bytearray(struct.pack('QQ', DRM_CAP_DUMB_BUFFER, 0))
                fcntl.ioctl(fd, DRM_GET_CAP, capbuf, True)
                if not struct.unpack('QQ', bytes(capbuf))[1]:
                    raise OSError('no dumb buffers')
                conn = None
                for cid in self._resources()[1]:
                    try:
                        conn = self._connector(cid)
                    except OSError:
                        conn = None
                    if conn:
                        break
                if not conn:
                    raise OSError('nothing connected')
                self.conn_id = conn['id']
                self.width, self.height = conn['w'], conn['h']
                self.mode = conn['blob']
                self.name = '%s %s %dx%d@%d' % (os.path.basename(path), conn['name'],
                                                conn['w'], conn['h'], conn['refresh'])
                self.crtc_id, self.saved_crtc = self._crtc_for()
                if not self.crtc_id:
                    raise OSError('no CRTC')
                fcntl.ioctl(fd, DRM_SET_MASTER, 0)
                self.master = True
                self._create_buffer()
                self._show()
                return True
            except OSError as e:
                self.error = '%s: %s' % (path, e)
                self.close()
        return False

    def _create_buffer(self):
        buf = bytearray(struct.pack('<IIIIIIQ', self.height, self.width, 32, 0, 0, 0, 0))
        fcntl.ioctl(self.fd, DRM_MODE_CREATE_DUMB, buf, True)
        v = struct.unpack('<IIIIIIQ', bytes(buf))
        self.handle, self.pitch, self.size = v[4], v[5], v[6]
        buf = bytearray(struct.pack('<IIQ', self.handle, 0, 0))
        fcntl.ioctl(self.fd, DRM_MODE_MAP_DUMB, buf, True)
        offset = struct.unpack('<IIQ', bytes(buf))[2]
        self.mm = mmap.mmap(self.fd, self.size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                            offset=offset)
        buf = bytearray(struct.pack('<7I', 0, self.width, self.height, self.pitch, 32, 24, self.handle))
        fcntl.ioctl(self.fd, DRM_MODE_ADDFB, buf, True)
        self.fb_id = struct.unpack('<7I', bytes(buf))[0]

    def _show(self):
        conns = (ctypes.c_uint32 * 1)(self.conn_id)
        buf = bytearray(104)
        struct.pack_into('<QIIIIIII', buf, 0, ctypes.addressof(conns), 1, self.crtc_id, self.fb_id,
                         0, 0, 0, 1)
        buf[36:104] = self.mode
        fcntl.ioctl(self.fd, DRM_MODE_SETCRTC, buf, True)

    def close(self):
        if self.fd < 0:
            return
        try:
            if self.saved_crtc is not None and self.master:
                v = struct.unpack('<QIIIIIII', bytes(self.saved_crtc[:36]))
                conns = (ctypes.c_uint32 * 1)(self.conn_id)
                buf = bytearray(self.saved_crtc)
                struct.pack_into('<QIIIIIII', buf, 0, ctypes.addressof(conns), 1,
                                 v[2], v[3], v[4], v[5], v[6], v[7])
                fcntl.ioctl(self.fd, DRM_MODE_SETCRTC, buf, True)
        except OSError:
            pass
        self._arr = None
        try:
            if self.mm:
                self.mm.close()
        except Exception:
            pass
        self.mm = None
        for call, arg in ((DRM_MODE_RMFB, self.fb_id), (DRM_MODE_DESTROY_DUMB, self.handle)):
            if arg:
                try:
                    fcntl.ioctl(self.fd, call, struct.pack('<I', arg), True)
                except OSError:
                    pass
        self.fb_id = self.handle = 0
        if self.master:
            try:
                fcntl.ioctl(self.fd, DRM_DROP_MASTER, 0)
            except OSError:
                pass
            self.master = False
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = -1


# -- fbdev fallback -------------------------------------------------------------
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602
FBIOPAN_DISPLAY = 0x4606


class _FbBitfield(ctypes.Structure):
    _fields_ = [('offset', ctypes.c_uint32), ('length', ctypes.c_uint32), ('msb_right', ctypes.c_uint32)]


class _FbVar(ctypes.Structure):
    _fields_ = ([(n, ctypes.c_uint32) for n in (
        'xres', 'yres', 'xres_virtual', 'yres_virtual', 'xoffset', 'yoffset',
        'bits_per_pixel', 'grayscale')]
        + [(n, _FbBitfield) for n in ('red', 'green', 'blue', 'transp')]
        + [(n, ctypes.c_uint32) for n in (
            'nonstd', 'activate', 'height', 'width', 'accel_flags', 'pixclock',
            'left_margin', 'right_margin', 'upper_margin', 'lower_margin',
            'hsync_len', 'vsync_len', 'sync', 'vmode', 'rotate', 'colorspace')]
        + [('reserved', ctypes.c_uint32 * 4)])


class _FbFix(ctypes.Structure):
    _fields_ = [('id', ctypes.c_char * 16), ('smem_start', ctypes.c_ulong),
                ('smem_len', ctypes.c_uint32), ('type', ctypes.c_uint32),
                ('type_aux', ctypes.c_uint32), ('visual', ctypes.c_uint32),
                ('xpanstep', ctypes.c_uint16), ('ypanstep', ctypes.c_uint16),
                ('ywrapstep', ctypes.c_uint16), ('line_length', ctypes.c_uint32),
                ('mmio_start', ctypes.c_ulong), ('mmio_len', ctypes.c_uint32),
                ('accel', ctypes.c_uint32), ('capabilities', ctypes.c_uint16),
                ('reserved', ctypes.c_uint16 * 2)]


class FbDisplay(_Mapped):
    def __init__(self):
        self.fd = -1
        self.width = self.height = self.pitch = 0
        self.mm = None
        self.name = ''
        self.error = ''
        self._arr = None

    def open(self):
        for path in ('/dev/fb0', '/dev/fb1'):
            if not os.path.exists(path):
                continue
            try:
                fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
            except OSError as e:
                self.error = '%s: %s' % (path, e.strerror)
                continue
            try:
                var, fix = _FbVar(), _FbFix()
                fcntl.ioctl(fd, FBIOGET_VSCREENINFO, var, True)
                fcntl.ioctl(fd, FBIOGET_FSCREENINFO, fix, True)
                if not (var.bits_per_pixel == 32 and var.red.offset == 16 and var.blue.offset == 0):
                    raise OSError('%d-bit framebuffer; Neptune needs 32-bit XRGB' % var.bits_per_pixel)
                if var.xoffset or var.yoffset:
                    pan = _FbVar.from_buffer_copy(var)
                    pan.xoffset = pan.yoffset = 0
                    try:
                        fcntl.ioctl(fd, FBIOPAN_DISPLAY, pan, True)
                    except OSError:
                        pass
                self.fd = fd
                self.width, self.height, self.pitch = var.xres, var.yres, fix.line_length
                self.mm = mmap.mmap(fd, self.pitch * self.height, mmap.MAP_SHARED,
                                    mmap.PROT_READ | mmap.PROT_WRITE)
                self.name = '%s %s %dx%d' % (os.path.basename(path), fix.id.decode('ascii', 'replace'),
                                             self.width, self.height)
                return True
            except OSError as e:
                self.error = '%s: %s' % (path, e)
                os.close(fd)
                self.fd = -1
        return False

    def close(self):
        self._arr = None
        try:
            if self.mm:
                self.mm.close()
        except Exception:
            pass
        self.mm = None
        if self.fd >= 0:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1


# -- pygame window -------------------------------------------------------------
class WindowDisplay:
    kind = 'window'

    def __init__(self, size=(1366, 768), fullscreen=False):
        os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
        import warnings
        warnings.filterwarnings('ignore', category=RuntimeWarning)
        import pygame
        self.pg = pygame
        pygame.display.init()
        pygame.display.set_caption('COS Neptune')
        flags = 0
        if fullscreen:
            info = pygame.display.Info()
            size = (info.current_w, info.current_h)
            flags = pygame.FULLSCREEN
        self.screen = pygame.display.set_mode(size, flags)
        pygame.mouse.set_visible(False)
        pygame.key.set_repeat(400, 35)
        self.width, self.height = size
        self.name = 'window %dx%d' % size
        self.error = ''
        try:
            from PIL import Image
            ic = __import__('nep.icons', fromlist=['get']).get('neptune', 32)
            surf = pygame.image.fromstring(ic.tobytes(), ic.size, 'RGBA')
            pygame.display.set_icon(surf)
        except Exception:
            pass

    def present(self, img, rect=None):
        pg = self.pg
        if rect is None:
            rect = (0, 0, self.width, self.height)
        x0, y0, x1, y1 = (max(0, int(rect[0])), max(0, int(rect[1])),
                          min(self.width, int(rect[2])), min(self.height, int(rect[3])))
        if x1 <= x0 or y1 <= y0:
            return
        crop = img.crop((x0, y0, x1, y1))
        surf = pg.image.frombuffer(crop.tobytes(), crop.size, 'RGBA')
        self.screen.blit(surf, (x0, y0))
        pg.display.update(pg.Rect(x0, y0, x1 - x0, y1 - y0))

    KEYMAP = None

    def poll(self, timeout):
        pg = self.pg
        evs = []
        if timeout > 0:
            ev = pg.event.wait(int(timeout * 1000))
            raw = [ev] + pg.event.get()
        else:
            raw = pg.event.get()
        mods_now = pg.key.get_mods()
        for e in raw:
            if e.type == pg.NOEVENT:
                continue
            if e.type == pg.QUIT:
                evs.append({'type': 'quit'})
            elif e.type == pg.MOUSEMOTION:
                evs.append({'type': 'move', 'x': e.pos[0], 'y': e.pos[1]})
            elif e.type == pg.MOUSEBUTTONDOWN and e.button in (1, 2, 3):
                evs.append({'type': 'down', 'x': e.pos[0], 'y': e.pos[1], 'button': e.button})
            elif e.type == pg.MOUSEBUTTONUP and e.button in (1, 2, 3):
                evs.append({'type': 'up', 'x': e.pos[0], 'y': e.pos[1], 'button': e.button})
            elif e.type == pg.MOUSEWHEEL:
                evs.append({'type': 'wheel', 'dy': e.y})
            elif e.type in (pg.KEYDOWN, pg.KEYUP):
                name = self._keyname(e.key)
                ch = e.unicode if e.type == pg.KEYDOWN else ''
                if ch and (ord(ch) < 32 or ord(ch) == 127):
                    ch = ''
                evs.append({'type': 'key' if e.type == pg.KEYDOWN else 'keyup', 'key': name, 'char': ch,
                            'ctrl': bool(e.mod & pg.KMOD_CTRL), 'alt': bool(e.mod & pg.KMOD_ALT),
                            'shift': bool(e.mod & pg.KMOD_SHIFT), 'super': bool(e.mod & pg.KMOD_GUI)})
            elif e.type == pg.WINDOWLEAVE if hasattr(pg, 'WINDOWLEAVE') else False:
                evs.append({'type': 'move', 'x': -100, 'y': -100})
        return evs

    def _keyname(self, k):
        pg = self.pg
        special = {pg.K_RETURN: 'enter', pg.K_KP_ENTER: 'enter', pg.K_BACKSPACE: 'backspace',
                   pg.K_TAB: 'tab', pg.K_ESCAPE: 'escape', pg.K_DELETE: 'delete',
                   pg.K_LEFT: 'left', pg.K_RIGHT: 'right', pg.K_UP: 'up', pg.K_DOWN: 'down',
                   pg.K_HOME: 'home', pg.K_END: 'end', pg.K_PAGEUP: 'pageup', pg.K_PAGEDOWN: 'pagedown',
                   pg.K_LSUPER: 'super', pg.K_RSUPER: 'super', pg.K_F4: 'f4', pg.K_F1: 'f1',
                   pg.K_F2: 'f2', pg.K_F5: 'f5', pg.K_LALT: 'alt', pg.K_RALT: 'alt',
                   pg.K_LCTRL: 'ctrl', pg.K_RCTRL: 'ctrl', pg.K_LSHIFT: 'shift', pg.K_RSHIFT: 'shift',
                   pg.K_SPACE: 'space', pg.K_PRINT: 'printscreen', pg.K_SYSREQ: 'printscreen'}
        if k in special:
            return special[k]
        try:
            return pg.key.name(k)
        except Exception:
            return '?'

    def close(self):
        try:
            self.pg.display.quit()
        except Exception:
            pass


class HeadlessDisplay:
    kind = 'headless'

    def __init__(self, size=(1366, 768)):
        self.width, self.height = size
        self.name = 'headless %dx%d' % size
        self.frame = None
        self.error = ''

    def present(self, img, rect=None):
        self.frame = img

    def poll(self, timeout):
        return []

    def close(self):
        pass
