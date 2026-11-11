"""Neptune's fun stuff.

* Bubbo, the desktop pet: a little glass COS bubble who wanders along the top
  of the taskbar, blinks, falls asleep when nothing happens, bounces when you
  click him, and says things. (Control Panel > Fun > Desktop pet.)
* Tip of the day: a useful tip, or a joke, when Neptune starts.
* The Konami code (up up down down left right left right B A) does something.
"""
import math
import random
import time

from PIL import Image, ImageDraw, ImageFilter

from . import gfx

LINES = [
    'Hi! I\'m Bubbo.', 'Have you tried turning the glass up?', 'Ctrl+Shift+Alt+Q is the big red door.',
    'I live here now.', 'Zip it! (Right-click > Send to.)', 'CEAP sounds like cheap. CEAP is free.',
    'Tab, Tab, Enter. You don\'t even need a mouse.', 'I\'m 100% bubble.', 'Did you know COS 2.4 is in here too?',
    'Pigeon delivers. Mostly.', 'Bloop.', 'I saw that window you minimized.', 'Unplug something. I dare you.',
    'Druman: drum + man.', 'Every build has a number. This one is special.', 'Wheee!', 'Skill issue? Never heard of it.',
]
TIPS = [
    'Tab and Shift+Tab move between buttons and boxes in any window. Enter or Space presses them.',
    'Right-click any file or folder: Send to > Compressed (zipped) folder makes a .zip in one click.',
    'Ctrl+Shift+Esc opens Task Manager from anywhere.',
    'Super+L locks the screen. CONTROL > Screen Saver... can show the lock screen after the screen saver too.',
    'Make your own app without code: APPS > CEAP App Maker.',
    'File Explorer\'s Code.Exe.Explore is a terminal that follows whatever folder you are browsing.',
    'In Icon.Arrange you can drag icons anywhere, and CEAP remembers where you put them.',
    'Plug a gamepad in: if you installed the Gamepad driver, it starts by itself.',
    'COS v2.4 Mode runs the real COS v2.4 in a window, mouse and all.',
    'Control Panel has a search box. It finds every setting from COS v2.4 and Neptune.',
    'Ctrl+Shift+Alt+Q asks whether to leave COS Neptune right now, even from the lock screen.',
    'CEAP:Web keeps your history in the tree on the left. Ctrl+E opens its JavaScript console.',
    'Why did the window go to therapy? It had too many panes.',
    'There are 10 kinds of people: those who use Text.Display, and those who use Icon.Arrange.',
    'Bubbo is not a screensaver. Bubbo is a lifestyle.',
    'Up, up, down, down... you know the rest.',
]
KONAMI = ['up', 'up', 'down', 'down', 'left', 'right', 'left', 'right', 'b', 'a']


def tip_of_the_day():
    return TIPS[time.localtime().tm_yday % len(TIPS)]


class Bubbo:
    SIZE = 34

    def __init__(self, shell):
        self.sh = shell
        self.x = shell.W * 0.35
        self.dir = 1
        self.t_last = time.time()
        self.say = None              # (text, until)
        self.next_say = time.time() + 25
        self.jump = 0.0              # time a click bounce started
        self.blink = 0.0
        self.mode = 'walk'           # walk | rest
        self.mode_until = time.time() + 8
        self.sprites = {}
        self.party_until = 0
        self.rng = random.Random()

    def sprite(self, squash, eyes):
        key = (round(squash, 2), eyes)
        if key in self.sprites:
            return self.sprites[key]
        n = self.SIZE
        S = 4
        im = Image.new('RGBA', (n * S, n * S), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        w = n * S * (1 + squash * 0.18)
        h = n * S * (1 - squash * 0.18)
        cx, by = n * S / 2, n * S
        box = (cx - w / 2 + 4, by - h + 4, cx + w / 2 - 4, by - 4)
        body = gfx.vgradient(int(box[2] - box[0]), int(box[3] - box[1]), ((0, (60, 120, 160)), (1, (14, 36, 140))))
        m = Image.new('L', body.size, 0)
        ImageDraw.Draw(m).ellipse((0, 0, body.size[0] - 1, body.size[1] - 1), fill=255)
        gfx.paste_masked(im, body, m, (int(box[0]), int(box[1])))
        d.ellipse(box, outline=(220, 210, 170, 255), width=S)
        bw, bh = box[2] - box[0], box[3] - box[1]
        d.ellipse((box[0] + bw * 0.2, box[1] + bh * 0.08, box[0] + bw * 0.62, box[1] + bh * 0.34), fill=(255, 255, 255, 90))
        ey = box[1] + bh * 0.48
        for ex in (cx - bw * 0.17, cx + bw * 0.17):
            if eyes == 'open':
                d.ellipse((ex - bw * 0.08, ey - bh * 0.11, ex + bw * 0.08, ey + bh * 0.11), fill=(255, 255, 255, 255))
                d.ellipse((ex - bw * 0.035 + self.dir * bw * 0.02, ey - bh * 0.04, ex + bw * 0.035 + self.dir * bw * 0.02,
                           ey + bh * 0.06), fill=(10, 20, 40, 255))
            else:
                d.line((ex - bw * 0.07, ey, ex + bw * 0.07, ey), fill=(255, 255, 255, 255), width=S)
        d.arc((cx - bw * 0.12, ey + bh * 0.06, cx + bw * 0.12, ey + bh * 0.24), 20, 160, fill=(255, 255, 255, 230), width=S)
        out = im.resize((n, n), Image.LANCZOS)
        self.sprites[key] = out
        return out

    def rect(self):
        from .theme import TASKBAR_H
        n = self.SIZE
        y = self.sh.H - TASKBAR_H - n + 2 - int(self.hop() * 22)
        return (int(self.x - n / 2), y, int(self.x + n / 2), y + n)

    def hop(self):
        t = time.time() - self.jump
        if t < 0.6:
            return math.sin(t / 0.6 * math.pi)
        if self.party_until > time.time():
            return abs(math.sin(time.time() * 6)) * 0.6
        return 0.0

    def tick(self, now):
        dt = min(0.2, now - self.t_last)
        self.t_last = now
        idle = now - self.sh.last_input
        if idle > 90:
            self.mode = 'sleep'
        elif self.mode == 'sleep':
            self.mode = 'walk'
            self.speak('Oh! You\'re back.')
        if self.mode != 'sleep' and now > self.mode_until:
            self.mode = 'rest' if self.mode == 'walk' else 'walk'
            self.mode_until = now + (self.rng.uniform(3, 7) if self.mode == 'rest' else self.rng.uniform(6, 14))
            if self.mode == 'walk' and self.rng.random() < 0.4:
                self.dir = -self.dir
        moved = False
        if self.mode == 'walk' or self.party_until > now:
            self.x += self.dir * 38 * dt * (2.2 if self.party_until > now else 1)
            lo, hi = 80, self.sh.W - 140
            if self.x < lo or self.x > hi:
                self.x = gfx.clamp(self.x, lo, hi)
                self.dir = -self.dir
            moved = True
        if now > self.next_say and self.mode != 'sleep':
            self.speak(self.rng.choice(LINES))
        if now > self.blink + 4 and self.rng.random() < 0.02:
            self.blink = now
        return moved or self.say is not None or self.hop() > 0 or (now - self.blink) < 0.2

    def speak(self, text, dur=4.5):
        self.say = (text, time.time() + dur)
        self.next_say = time.time() + self.rng.uniform(35, 80)

    def click(self):
        self.jump = time.time()
        self.speak(self.rng.choice(['Boing!', 'Hey, that tickles.', 'Bloop!', 'Wheee!', 'I\'m awake, I\'m awake!',
                                    'Again! Again!']), 2.5)
        self.mode = 'walk'
        self.sh.last_input = time.time()

    def party(self):
        self.party_until = time.time() + 12
        self.speak('PARTY MODE! 30 lives granted.', 6)

    def draw(self, canvas, now):
        if self.say and now > self.say[1]:
            self.say = None
        squash = 0.0
        if self.mode == 'walk' or self.party_until > now:
            squash = 0.5 + 0.5 * math.sin(now * 9)
        eyes = 'closed' if self.mode == 'sleep' or (now - self.blink) < 0.15 else 'open'
        im = self.sprite(squash * 0.6, eyes)
        x0, y0, x1, y1 = self.rect()
        sh = Image.new('RGBA', (self.SIZE, 8), (0, 0, 0, 0))
        ImageDraw.Draw(sh).ellipse((4, 1, self.SIZE - 4, 7), fill=(0, 0, 0, int(90 * (1 - self.hop() * 0.6))))
        from .theme import TASKBAR_H
        gfx.alpha_composite_clip(canvas, sh, x0, self.sh.H - TASKBAR_H - 5)
        gfx.alpha_composite_clip(canvas, im, x0, y0)
        if self.mode == 'sleep':
            z = int(now * 1.5) % 3 + 1
            ImageDraw.Draw(canvas).text((x1 - 2, y0 - 6), 'z' * z, font=gfx.font(12, True), fill=(220, 235, 255, 230))
        if self.say:
            text = self.say[0]
            f = gfx.font(12)
            tw = gfx.text_width(f, text)
            bw, bh = tw + 20, 26
            bx = int(gfx.clamp(self.x - bw / 2, 6, self.sh.W - bw - 6))
            by = y0 - bh - 10
            gfx.round_rect(canvas, (bx, by, bx + bw, by + bh), 9, fill=(255, 255, 255, 240), outline=(90, 120, 150, 255))
            d = ImageDraw.Draw(canvas)
            tx = int(gfx.clamp(self.x, bx + 12, bx + bw - 12))
            d.polygon([(tx - 6, by + bh - 1), (tx + 6, by + bh - 1), (tx, by + bh + 7)], fill=(255, 255, 255, 240))
            d.text((bx + 10, by + bh // 2), text, font=f, fill=(20, 30, 45, 255), anchor='lm')


class Confetti:
    """A burst of COS-coloured confetti over everything, for a few seconds."""

    def __init__(self, W, H):
        self.W, self.H = W, H
        rng = random.Random()
        cols = [(52, 195, 208), (47, 111, 255), (255, 210, 90), (255, 120, 160), (140, 230, 120), (255, 255, 255)]
        self.parts = [[rng.uniform(0, W), rng.uniform(-H * 0.6, 0), rng.uniform(-40, 40), rng.uniform(120, 260),
                       rng.uniform(0, 6.28), rng.choice(cols), rng.uniform(5, 10)] for _ in range(int(W / 6))]
        self.t0 = self.last = time.time()

    @property
    def done(self):
        return time.time() - self.t0 > 7

    def draw(self, canvas, now):
        dt = min(0.1, now - self.last)
        self.last = now
        d = ImageDraw.Draw(canvas)
        for p in self.parts:
            p[0] += p[2] * dt + math.sin(now * 3 + p[4]) * 20 * dt
            p[1] += p[3] * dt
            p[4] += dt * 5
            if 0 <= p[1] < self.H:
                s = p[6]
                w = abs(math.cos(p[4])) * s + 1
                d.rectangle((p[0] - w / 2, p[1] - s / 3, p[0] + w / 2, p[1] + s / 3), fill=p[5] + (255,))
