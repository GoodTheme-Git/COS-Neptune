#!/usr/bin/env python3
"""COS Neptune -- COS 3.0, the Aero release, rendered entirely through GDR.

    python3 neptune.py              # GDR on a real console, a window under X/Wayland
    python3 neptune.py --gdr        # insist on Graphics Desktop Rendering (DRM/KMS, then fbdev)
    python3 neptune.py --fb         # GDR through /dev/fb0 only
    python3 neptune.py --window [--size 1366x768] [--fullscreen]
    python3 neptune.py --shot out.png   # render the desktop once, headless

Emergency exit: Ctrl+Alt+Backspace. See home/.neptune/ for settings and crash.log.
"""
import argparse
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from nep import theme                       # noqa: E402
from nep import display as dsp              # noqa: E402

GDR_MARKER = os.path.join(theme.STATE, 'gdr-attempt')
WATCHDOG_SECONDS = 20


def log(msg):
    try:
        os.makedirs(theme.STATE, exist_ok=True)
        with open(os.path.join(theme.STATE, 'neptune.log'), 'a') as f:
            f.write('%s %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg))
    except OSError:
        pass


def parse():
    ap = argparse.ArgumentParser(description='COS Neptune')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--gdr', action='store_true', help='Graphics Desktop Rendering on this console (DRM, then fbdev)')
    g.add_argument('--fb', action='store_true', help='GDR through /dev/fb0 only')
    g.add_argument('--window', action='store_true', help='run in a window under X / Wayland')
    g.add_argument('--shot', metavar='PNG', help='render one desktop frame to a PNG and exit')
    ap.add_argument('--size', default='1366x768', help='window / headless size, e.g. 1366x768')
    ap.add_argument('--fullscreen', action='store_true')
    ap.add_argument('--no-boot', action='store_true', help='skip the boot screen')
    ap.add_argument('--oobe', action='store_true', help='run Set Up COS Neptune (the first-run setup) now')
    ap.add_argument('--retry-gdr', action='store_true', help='ignore a failed previous GDR attempt')
    ap.add_argument('--mode', help='GDR display mode, e.g. 2560x1440 or 3840x1080@60 (default: the panel\'s preferred mode)')
    return ap.parse_args()


def size_arg(s):
    try:
        w, h = s.lower().split('x')
        return max(640, int(w)), max(480, int(h))
    except ValueError:
        return 1366, 768


class Watchdog:
    """Gives the console back if nobody confirms they can see the desktop."""

    def __init__(self, seconds, display, console):
        self.ev = threading.Event()
        self.expired = False
        self.display, self.console = display, console
        self.t = threading.Thread(target=self._run, args=(seconds,), daemon=True, name='neptune-watchdog')
        self.t.start()

    def confirm(self):
        self.ev.set()
        try:
            os.remove(GDR_MARKER)
        except OSError:
            pass
        log('GDR confirmed')

    def _run(self, seconds):
        if self.ev.wait(seconds):
            return
        self.expired = True                 # ask the main loop to leave politely
        log('GDR watchdog expired; restoring console')
        if self.ev.wait(2.5):
            return
        # main loop is wedged: take the screen back from this thread
        try:
            self.display.close()
        except Exception:
            pass
        self.console.restore()
        sys.stderr.write('\nNeptune: no input within %ds of GDR starting, console restored.\n' % seconds)
        os._exit(3)


def mode_arg(s):
    if not s:
        return None
    try:
        res, _, hz = s.lower().partition('@')
        w, h = res.split('x')
        return int(w), int(h), int(hz or 0)
    except ValueError:
        return None


def open_gdr(prefer_fb=False, retry=False, want=None):
    """-> (display, note). Tries DRM/KMS, then fbdev; honours the attempt marker."""
    last = None
    if os.path.exists(GDR_MARKER) and not retry:
        try:
            last = open(GDR_MARKER).read().strip()
        except OSError:
            last = '?'
    order = ['fb'] if prefer_fb else ['drm', 'fb']
    note = ''
    if last:
        if last.startswith('drm') and 'fb' in order:
            order = ['fb']
            note = 'Last GDR start through DRM did not come back, so Neptune is using /dev/fb0.'
        elif last.startswith('fb'):
            return None, ('The last GDR start (%s) never got confirmed. Fix the cause, then run with --retry-gdr '
                          '(or delete %s).' % (last, GDR_MARKER))
    errors = []
    for kind in order:
        d = dsp.DrmDisplay(want) if kind == 'drm' else dsp.FbDisplay()
        os.makedirs(theme.STATE, exist_ok=True)
        with open(GDR_MARKER, 'w') as f:
            f.write('%s pid %d %s' % (kind, os.getpid(), time.strftime('%H:%M:%S')))
        if d.open():
            return d, note
        errors.append(d.error)
        try:
            os.remove(GDR_MARKER)
        except OSError:
            pass
    return None, 'GDR could not start: ' + '; '.join(e for e in errors if e)


def gdr_suspend(shell, req, display, inp, console, settings, watchdog=None):
    """Save-state Neptune, give the screen to req['cmd'] until it exits, then take it back.
    Returns (display, inp, console, ok). See nep/suspend.py. (From the LEIGON session on brainpad.)"""
    from nep import suspend
    from nep.evinput import EvInput
    if watchdog is not None:
        watchdog.confirm()             # launching something means the screen works; never let it fire mid-game
    if shell.phase == 'saver':
        shell.stop_saver()
    shell.dirty = True
    fr = shell.frame(time.time())
    snap = (fr[0] if fr else shell.out).copy()
    suspend.snapshot(shell, snap, req.get('title', 'a program'))
    size = (display.width, display.height)
    was_fb = isinstance(display, dsp.FbDisplay)
    log('GDR suspend for %s: %s' % (req.get('title'), ' '.join(map(str, req['cmd']))))
    if inp is not None:
        inp.close()
    display.close()
    console.restore()
    rc = -1
    t0 = time.time()
    logf = None
    try:
        logf = open(req['log'], 'a') if req.get('log') else None
        rc = subprocess.call(req['cmd'], stdout=logf or subprocess.DEVNULL, stderr=logf or subprocess.DEVNULL,
                             env=req.get('env'), cwd=req.get('cwd'))
    except Exception as e:
        log('suspended program failed to start: %s' % e)
    finally:
        if logf:
            logf.close()
    log('suspended program ended (rc %s) after %ds; taking the screen back' % (rc, time.time() - t0))
    console = dsp.Console()
    console.take()
    new, note = open_gdr(prefer_fb=was_fb, retry=True, want=(size[0], size[1], 0))
    if new is None and not was_fb:
        new, note = open_gdr(prefer_fb=True, retry=True)
    try:
        os.remove(GDR_MARKER)
    except OSError:
        pass
    if new is None:
        console.restore()
        log('could not take the screen back: %s' % note)
        print('Neptune: could not take the screen back after %s (%s). Your windows are saved; start Neptune '
              'again and it reopens them.' % (req.get('title'), note))
        return display, None, console, False
    if (new.width, new.height) != size:
        log('screen came back at %dx%d instead of %dx%d' % (new.width, new.height, size[0], size[1]))
    inp = EvInput(new.width, new.height, settings.get('cursor_speed', 1.0))
    shell.input_info = ['%s: %s (%s)' % (d.kind, d.name, d.path) for d in inp.devices] + inp.errors
    if (new.width, new.height) == size:
        new.present(snap, None)          # the exact pixels from before, straight away
    shell.display_name = new.name
    shell.last_input = time.time()       # coming back is activity: no screensaver straight away
    shell.dirty = True
    shell.desk_dirty = True
    for w in shell.windows:
        w.content_dirty = w.frame_dirty = True
    suspend.clear()
    cb = req.get('done')
    if cb:
        try:
            cb(rc)
        except Exception:
            pass
    return new, inp, console, True


def coalesce(evs):
    out = []
    for e in evs:
        if e['type'] == 'move' and out and out[-1]['type'] == 'move':
            out[-1] = e
        else:
            out.append(e)
    return out


def main():
    args = parse()
    theme.ensure_dirs()
    from nep.theme import Settings
    from nep.shell import Shell
    from nep.apps import REGISTRY

    settings = Settings()
    console = None
    watchdog = None
    inp = None
    note = ''

    if args.shot:
        W, H = size_arg(args.size)
        display = dsp.HeadlessDisplay((W, H))
        mode = 'headless'
    elif args.window or (not args.gdr and not args.fb and not dsp.on_real_vt()):
        if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')) and not args.window:
            print('Neptune: not on a real console and no X/Wayland display. Run it from a VT (Ctrl+Alt+F3) for '
                  'GDR, or inside a desktop session for a window.')
            return 1
        display = dsp.WindowDisplay(size_arg(args.size), args.fullscreen)
        mode = 'window'
    else:
        from nep.evinput import EvInput
        console = dsp.Console()
        console.take()
        display, note = open_gdr(prefer_fb=args.fb, retry=args.retry_gdr,
                                 want=mode_arg(args.mode or settings.get('gdr_mode')))
        if display is None:
            console.restore()
            print('Neptune: %s' % note)
            print('Tip: inside a desktop session run  python3 neptune.py --window')
            return 2
        mode = 'gdr'
        inp = EvInput(display.width, display.height, settings.get('cursor_speed', 1.0))
        if not inp.has_keyboard:
            log('no readable keyboard: %s' % inp.errors)
        watchdog = Watchdog(WATCHDOG_SECONDS, display, console)
        log('GDR on %s' % display.name)

    shell = Shell(display.width, display.height, settings, backend=mode, registry=REGISTRY,
                  boot=not (args.no_boot or args.shot), oobe=True if args.oobe else None)
    shell.display_name = display.name
    shell.can_suspend = mode == 'gdr'           # apps may hand the whole screen to a program (nep/suspend.py)
    if inp is not None:
        shell.input_info = ['%s: %s (%s)' % (d.kind, d.name, d.path) for d in inp.devices] + inp.errors
    if watchdog is not None:
        shell.gdr_confirm = watchdog.confirm
        if note:
            shell.notify('Graphics Desktop Rendering', note, 'warning', 10)
        shell.notify('Graphics Desktop Rendering', 'Press a key or click to keep this display. Neptune gives the '
                     'console back in %d seconds otherwise.' % WATCHDOG_SECONDS, 'info', 12)
    if args.shot:
        shell.fade_in_t = None          # capture the settled desktop, not the first frame of the fade-in
        shell.desk_dirty = True
        shell.tick(time.time())
        shell.dirty = True
        img, _ = shell.frame(time.time())
        img.convert('RGB').save(args.shot)
        print('wrote', args.shot)
        return 0

    code = 'exit'
    from nep import suspend
    pending = suspend.load_pending()            # Neptune was killed while another program had the screen
    try:
        poll = inp.poll if inp is not None else display.poll
        while shell.exit_code is None:
            if watchdog is not None and watchdog.expired:
                code = 'watchdog'
                break
            now = time.time()
            shell.tick(now)
            fr = shell.frame(now)
            if fr is not None:
                display.present(*fr)
            timeout = 1 / 60.0 if shell.busy() else 0.06
            for e in coalesce(poll(timeout)):
                shell.handle(e)
            if pending is not None and shell.phase == 'desktop':
                n = suspend.restore_windows(shell, pending)
                shell.notify('Welcome back', 'Neptune reopened %d window%s from before %s.'
                             % (n, '' if n == 1 else 's', pending.get('reason', 'it stopped')), 'info', 8)
                pending = None
            req = getattr(shell, 'suspend_request', None)
            if req is not None:
                shell.suspend_request = None
                if mode == 'gdr' and console is not None:
                    display, inp, console, ok = gdr_suspend(shell, req, display, inp, console, settings, watchdog)
                    if not ok:
                        code = 'exit'
                        break
                    poll = inp.poll if inp is not None else display.poll
                else:
                    shell.notify(req.get('title', 'Program'), 'Handing the whole screen to a program only works '
                                 'in GDR (full-screen) mode.', 'warning', 6)
                    cb = req.get('done')
                    if cb:
                        try:
                            cb(-1)
                        except Exception:
                            pass
        else:
            code = shell.exit_code
    except KeyboardInterrupt:
        code = 'exit'
    except Exception:
        import traceback
        log('FATAL\n' + traceback.format_exc())
        if console is not None:
            display.close()
            console.restore()
            console = None
        raise
    finally:
        settings.save()
        if inp is not None:
            inp.close()
        display.close()
        if console is not None:
            console.restore()
        if watchdog is not None:
            watchdog.ev.set()

    log('exit: %s' % code)
    if code == 'restart':
        os.execv(sys.executable, [sys.executable] + sys.argv)
    if code == 'watchdog':
        print('Neptune: nobody confirmed the GDR display within %d seconds; console restored.' % WATCHDOG_SECONDS)
        return 3
    if code in ('poweroff', 'reboot') and mode == 'gdr':
        subprocess.call(['systemctl', code])
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
