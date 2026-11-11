#!/usr/bin/env python3
"""COS Neptune Mobile, Python edition: the phone shell in a window on your PC.

    python3 neptune_mobile.py                # a phone-sized window
    python3 neptune_mobile.py --scale 0.8    # smaller (or 1.25 bigger)
    python3 neptune_mobile.py --shot out.png [--steps steps.json]   # render headless, for testing

The mouse is your finger: click to tap, drag to swipe, hold to long-press.
Esc is Back, Ctrl+Q quits, F5 saves a screenshot into home/Pictures.
Built on Neptune's own toolkit (../../nep): glass, wallpapers and icons match the desktop.
Your files and settings live in mobile/python/home/ ($NEPTUNE_MOBILE_HOME to move them).
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nm import ui                      # noqa: E402  (puts ~/cos/neptune on sys.path)
from nm.shell import Shell, FULLNAME   # noqa: E402
from nm import apps                    # noqa: E402

FPS = 30


def make_shell():
    sh = Shell()
    apps.register_all(sh)
    return sh


def headless(args):
    """Run the shell without a window. steps: a JSON list of
    ["wait", secs] ["tap", x, y] ["hold", x, y] ["drag", x0, y0, x1, y1] ["key", name, char]
    ["open", app] ["shot", "file.png"]."""
    sh = make_shell()
    steps = json.load(open(args.steps)) if args.steps else [['wait', 3.0]]
    t = time.time()

    def run(secs):
        end = time.time() + secs
        last = time.time()
        while time.time() < end:
            now = time.time()
            sh.tick(now - last)
            sh.check_hold()
            last = now
            time.sleep(0.03)
        sh.render()

    for st in steps:
        op = st[0]
        if op == 'wait':
            run(st[1])
        elif op == 'tap':
            sh.render()
            sh.pointer('down', st[1], st[2])
            sh.pointer('up', st[1], st[2])
            run(0.1)
        elif op == 'hold':
            sh.render()
            sh.pointer('down', st[1], st[2])
            run(0.7)
            sh.pointer('up', st[1], st[2])
            run(0.1)
        elif op == 'drag':
            sh.render()
            x0, y0, x1, y1 = st[1:5]
            sh.pointer('down', x0, y0)
            for i in range(1, 11):
                sh.pointer('move', x0 + (x1 - x0) * i / 10, y0 + (y1 - y0) * i / 10)
            sh.pointer('up', x1, y1)
            run(0.4)
        elif op == 'key':
            sh.key({'key': st[1], 'char': st[2] if len(st) > 2 else ''})
            run(0.05)
        elif op == 'type':
            for ch in st[1]:
                sh.key({'key': ch, 'char': ch})
            run(0.05)
        elif op == 'open':
            sh.open(st[1], **(st[2] if len(st) > 2 else {}))
            run(0.4)
        elif op == 'shot':
            sh.render().convert('RGB').save(st[1])
    sh.render().convert('RGB').save(args.shot)
    print('rendered %s in %.1fs' % (args.shot, time.time() - t))


def window(args):
    os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
    import warnings
    warnings.filterwarnings('ignore', category=RuntimeWarning)
    import pygame
    pygame.display.init()
    pygame.font.init()
    scale = args.scale
    if not scale:
        info = pygame.display.Info()
        scale = min(1.25, max(0.5, (info.current_h - 90) / ui.H)) if info.current_h else 1.0
        scale = min(scale, 1.0) if info.current_h < 1300 else scale
    size = (int(ui.W * scale), int(ui.H * scale))
    screen = pygame.display.set_mode(size)
    pygame.display.set_caption(FULLNAME)
    try:
        ic = ui.logo(28)
        pygame.display.set_icon(pygame.image.fromstring(ic.tobytes(), ic.size, 'RGBA'))
    except Exception:
        pass
    pygame.key.set_repeat(400, 35)
    sh = make_shell()
    clock = pygame.time.Clock()
    last = time.time()
    special = {pygame.K_RETURN: 'enter', pygame.K_KP_ENTER: 'enter', pygame.K_BACKSPACE: 'backspace',
               pygame.K_TAB: 'tab', pygame.K_ESCAPE: 'escape', pygame.K_DELETE: 'delete', pygame.K_LEFT: 'left',
               pygame.K_RIGHT: 'right', pygame.K_UP: 'up', pygame.K_DOWN: 'down', pygame.K_HOME: 'home',
               pygame.K_END: 'end', pygame.K_F5: 'f5', pygame.K_SPACE: 'space'}

    def pos(e):
        return e.pos[0] / scale, e.pos[1] / scale

    while not sh.quit:
        for e in pygame.event.get():
            if e.type == pygame.QUIT:
                sh.quit = True
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                sh.pointer('down', *pos(e))
            elif e.type == pygame.MOUSEMOTION and e.buttons[0]:
                sh.pointer('move', *pos(e))
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
                sh.pointer('up', *pos(e))
            elif e.type == pygame.MOUSEBUTTONUP and e.button == 3:
                sh.back()                                   # right-click = Back
            elif e.type == pygame.MOUSEWHEEL:
                sh.wheel(e.y)
            elif e.type == pygame.KEYDOWN:
                ch = e.unicode if e.unicode and ord(e.unicode[0]) >= 32 and ord(e.unicode[0]) != 127 else ''
                name = special.get(e.key) or pygame.key.name(e.key)
                sh.key({'key': name, 'char': ch, 'ctrl': bool(e.mod & pygame.KMOD_CTRL),
                        'alt': bool(e.mod & pygame.KMOD_ALT), 'shift': bool(e.mod & pygame.KMOD_SHIFT)})
            elif e.type in (pygame.WINDOWFOCUSGAINED, pygame.VIDEOEXPOSE) if hasattr(pygame, 'WINDOWFOCUSGAINED') else False:
                sh.dirty = True
        now = time.time()
        sh.tick(now - last)
        sh.check_hold()
        last = now
        if sh.dirty:
            img = sh.render()
            surf = pygame.image.frombuffer(img.tobytes(), img.size, 'RGBA')
            if scale != 1:
                surf = pygame.transform.smoothscale(surf, size)
            screen.blit(surf, (0, 0))
            pygame.display.flip()
        clock.tick(FPS)
    pygame.quit()


def main():
    ap = argparse.ArgumentParser(description=FULLNAME)
    ap.add_argument('--scale', type=float, default=0, help='window size multiplier (default: fit the screen)')
    ap.add_argument('--shot', metavar='PNG', help='render without a window and save a picture')
    ap.add_argument('--steps', metavar='JSON', help='with --shot: actions to perform first')
    args = ap.parse_args()
    if args.shot:
        headless(args)
    else:
        window(args)


if __name__ == '__main__':
    main()
