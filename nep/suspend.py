"""GDR save-state: hand the whole screen to another program, then come back.

(Contributed by the LEIGON session on brainpad; merged 2026-11-08.)

Under GDR Neptune is DRM master and draws every pixel itself, so a program
that needs its own display server (LEIGON runs Chrome inside a private X
server) cannot share the screen. Instead Neptune:

  1. snapshots itself: a full-screen PNG of exactly what was on screen, and
     state.json with every window (which app, how it was launched, where it
     was, its size, maximised/minimised, z-order, which one had focus);
  2. lets go of the display, the input devices and the console;
  3. runs the program and waits for it to finish;
  4. takes the screen back, puts the saved pixels up at once, and carries on.

Neptune's process stays alive the whole time, so every open app comes back
exactly as it was: documents, scroll positions, terminals, everything in
memory. The files on disk are the safety net: if Neptune is killed while the
program runs, the next start finds state.json and reopens the same windows in
the same places.
"""
import json
import os
import time

from . import theme

DIR = os.path.join(theme.STATE, 'suspend')
STATE_FILE = os.path.join(DIR, 'state.json')
SCREEN_FILE = os.path.join(DIR, 'screen.png')


def _json_safe(kw):
    out = {}
    for k, v in (kw or {}).items():
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            pass
    return out


def snapshot(shell, image, reason):
    os.makedirs(DIR, exist_ok=True)
    wins = []
    for w in shell.windows:
        if w.closing:
            continue
        launch = getattr(w.app, '_launch', None)
        wins.append({
            'app': launch[0] if launch else None,
            'kw': _json_safe(launch[1]) if launch else {},
            'title': getattr(w.app, 'title', ''),
            'rect': [w.x, w.y, w.w, w.h],
            'state': w.state,
            'restore_rect': list(w.restore_rect) if w.restore_rect else None,
            'minimized': bool(w.minimized),
            'active': w is shell.active,
            'dialog': bool(getattr(w.app, 'dialog', False)),
        })
    data = {'pending': True, 'reason': reason, 'time': time.time(), 'size': [shell.W, shell.H], 'windows': wins}
    os.chmod(DIR, 0o700)                   # a picture of the screen can show anything that was open
    try:
        image.convert('RGB').save(SCREEN_FILE)
        os.chmod(SCREEN_FILE, 0o600)
    except Exception:
        pass
    tmp = STATE_FILE + '.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, STATE_FILE)
    return data


def clear():
    for p in (STATE_FILE, SCREEN_FILE):
        try:
            os.remove(p)
        except OSError:
            pass


def load_pending():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
        return d if d.get('pending') else None
    except (OSError, ValueError):
        return None


def restore_windows(shell, data):
    """Reopen the windows a snapshot recorded (used after Neptune was killed mid-suspend)."""
    opened = 0
    active = None
    for rec in data.get('windows', []):
        name = rec.get('app')
        if not name or rec.get('dialog') or name not in shell.registry:
            continue
        try:
            app = shell.launch(name, **rec.get('kw', {}))
        except Exception:
            app = None
        if app is None or app.win is None:
            continue
        w = app.win
        x, y, ww, hh = rec.get('rect', [w.x, w.y, w.w, w.h])
        w.x, w.y, w.w, w.h = int(x), int(y), int(ww), int(hh)
        w.state = rec.get('state', 'normal')
        rr = rec.get('restore_rect')
        w.restore_rect = tuple(rr) if rr else None
        w.anim = None
        w.content_dirty = w.frame_dirty = True
        if rec.get('minimized'):
            w.minimized = True
        if rec.get('active'):
            active = w
        opened += 1
    if active is not None and not active.minimized:
        shell.activate(active)
    shell.dirty = True
    clear()
    return opened
