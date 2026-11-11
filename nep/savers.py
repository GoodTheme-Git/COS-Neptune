"""Neptune screensavers.

The shell starts one after the chosen idle time (CONTROL > Screen Saver...)
and stops it on any key, click or real mouse movement. Each saver is a class
with frame(now) -> RGBA image of the full screen. Heavy ones draw at a
fraction of the screen size and scale up, so they stay smooth on 4K-wide
screens; the shell asks for about 30 frames a second.
"""
import math
import random
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import gfx, wallpaper

FPS = 30


class Saver:
    name = 'Saver'

    def __init__(self, w, h, tint=(38, 150, 160)):
        self.w, self.h = max(16, w), max(16, h)
        self.tint = tuple(tint)
        self.t0 = time.time()
        self.last = self.t0
        self.rng = random.Random(7)

    def frame(self, now):
        dt = min(0.1, max(0.0, now - self.last))
        self.last = now
        return self.draw(now - self.t0, dt)

    def draw(self, t, dt):
        return Image.new('RGBA', (self.w, self.h), (0, 0, 0, 255))

    def scaled(self, img):
        if img.size != (self.w, self.h):
            img = img.resize((self.w, self.h), Image.BILINEAR)
        return img if img.mode == 'RGBA' else img.convert('RGBA')


class Blank(Saver):
    name = 'Blank'

    def draw(self, t, dt):
        if not hasattr(self, '_img'):
            self._img = Image.new('RGBA', (self.w, self.h), (0, 0, 0, 255))
        return self._img


class Bubbles(Saver):
    """Glass bubbles rising through deep blue water, wobbling as they go."""
    name = 'Bubbles'

    def __init__(self, w, h, tint=(38, 150, 160)):
        super().__init__(w, h, tint)
        self.u = self.h / 720.0
        arr = wallpaper._vgrad3(self.w, self.h, (6, 70, 150), (2, 24, 84), (1, 8, 40))
        self.bg = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB').convert('RGBA')
        self.sprites = {}
        n = int(14 + 10 * self.w / self.h)
        self.bubbles = [self.spawn(y=self.rng.uniform(0, self.h)) for _ in range(n)]
        self.logo = wallpaper.bubble_logo(int(110 * self.u), ((44, 100, 128), (12, 32, 132)), (190, 225, 255))
        self.lx, self.ly = self.w * 0.5, self.h * 1.3

    def spawn(self, y=None):
        r = int(self.rng.choice([8, 12, 16, 22, 30, 42, 56]) * self.u)
        return [self.rng.uniform(0, self.w), self.h + r if y is None else y, r, self.rng.uniform(40, 110) * self.u,
                self.rng.uniform(0, 6.28), self.rng.uniform(0.6, 1.6)]

    def sprite(self, r):
        s = self.sprites.get(r)
        if s is None:
            s = Image.new('RGBA', (2 * r + 4, 2 * r + 4), (0, 0, 0, 0))
            d = ImageDraw.Draw(s)
            d.ellipse((2, 2, 2 * r + 1, 2 * r + 1), fill=(120, 200, 255, 38), outline=(200, 240, 255, 170),
                      width=max(1, r // 14))
            d.ellipse((2 + r * 0.42, 2 + r * 0.28, 2 + r * 0.95, 2 + r * 0.62), fill=(255, 255, 255, 150))
            d.ellipse((2 + r * 1.2, 2 + r * 1.35, 2 + r * 1.45, 2 + r * 1.55), fill=(255, 255, 255, 70))
            s = s.filter(ImageFilter.GaussianBlur(max(0.4, r * 0.025)))
            self.sprites[r] = s
        return s

    def draw(self, t, dt):
        img = self.bg.copy()
        # the COS bubble drifts up through the middle every 40 s or so
        self.ly -= 22 * self.u * dt
        if self.ly < -self.logo.size[1]:
            self.ly = self.h + self.logo.size[1]
            self.lx = self.rng.uniform(self.w * 0.3, self.w * 0.7)
        lx = self.lx + math.sin(t * 0.5) * 30 * self.u
        img.alpha_composite(self.logo, (int(lx - self.logo.size[0] / 2), int(self.ly - self.logo.size[1] / 2)))
        for i, b in enumerate(self.bubbles):
            b[1] -= b[3] * dt
            b[4] += dt * b[5]
            if b[1] < -b[2] - 4:
                self.bubbles[i] = b = self.spawn()
            x = b[0] + math.sin(b[4]) * b[2] * 0.6
            gfx.alpha_composite_clip(img, self.sprite(b[2]), int(x - b[2]), int(b[1] - b[2]))
        return img


class BouncingCOS(Saver):
    """The COS bubble bounces round the screen and changes colour at each wall."""
    name = 'Bouncing COS'
    COLORS = [((44, 100, 128), (12, 32, 132)), ((150, 60, 140), (60, 20, 110)), ((30, 140, 90), (8, 70, 50)),
              ((180, 110, 30), (110, 50, 10)), ((160, 40, 50), (80, 10, 20)), ((80, 80, 170), (30, 20, 90))]

    def __init__(self, w, h, tint=(38, 150, 160)):
        super().__init__(w, h, tint)
        self.r = max(10, int(self.h * 0.13))
        self.ci = 0
        self.logos = {}
        self.x, self.y = self.w * 0.3, self.h * 0.4
        sp = self.h * 0.28
        self.vx, self.vy = sp, sp * 0.8
        self.base = Image.new('RGBA', (self.w, self.h), (0, 0, 0, 255))

    def logo(self):
        im = self.logos.get(self.ci)
        if im is None:
            ball = self.COLORS[self.ci % len(self.COLORS)]
            im = self.logos[self.ci] = wallpaper.bubble_logo(self.r, ball, (220, 230, 240), shadow=False)
        return im

    def draw(self, t, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        hit = False
        if self.x - self.r < 0 or self.x + self.r > self.w:
            self.vx = -self.vx
            self.x = gfx.clamp(self.x, self.r, self.w - self.r)
            hit = True
        if self.y - self.r < 0 or self.y + self.r > self.h:
            self.vy = -self.vy
            self.y = gfx.clamp(self.y, self.r, self.h - self.r)
            hit = True
        if hit:
            self.ci = (self.ci + 1) % len(self.COLORS)
        img = self.base.copy()
        lg = self.logo()
        img.alpha_composite(lg, (int(self.x - lg.size[0] / 2), int(self.y - lg.size[1] / 2)))
        return img


class Warp(Saver):
    """Flying through a field of stars."""
    name = 'Warp'

    def __init__(self, w, h, tint=(38, 150, 160)):
        super().__init__(w, h, tint)
        self.s = max(1, int(round(self.w / 1280)))
        self.sw, self.sh = self.w // self.s, self.h // self.s
        n = 900
        rng = np.random.default_rng(5)
        self.x = rng.uniform(-1, 1, n) * (self.sw / self.sh)
        self.y = rng.uniform(-1, 1, n)
        self.z = rng.uniform(0.05, 1, n)
        self.rng = rng

    def draw(self, t, dt):
        speed = 0.35
        self.z -= speed * dt
        dead = self.z <= 0.02
        if dead.any():
            k = int(dead.sum())
            self.x[dead] = self.rng.uniform(-1, 1, k) * (self.sw / self.sh)
            self.y[dead] = self.rng.uniform(-1, 1, k)
            self.z[dead] = 1.0
        img = Image.new('RGB', (self.sw, self.sh), (0, 0, 6))
        d = ImageDraw.Draw(img)
        cx, cy, f = self.sw / 2, self.sh / 2, self.sh * 0.5
        z2 = self.z + speed * 0.06
        for x, y, z, zz in zip(self.x, self.y, self.z, z2):
            px, py = cx + x / z * f, cy + y / z * f
            if not (0 <= px < self.sw and 0 <= py < self.sh):
                continue
            qx, qy = cx + x / zz * f, cy + y / zz * f
            b = int(255 * min(1.0, (1 - z) * 1.4))
            d.line([(qx, qy), (px, py)], fill=(b, b, min(255, b + 30)), width=1 if z > 0.3 else 2)
        return self.scaled(img)


class Aurora(Saver):
    """Neptune's light ribbons, slowly moving."""
    name = 'Aurora'

    def __init__(self, w, h, tint=(38, 150, 160)):
        super().__init__(w, h, tint)
        self.sw = 320
        self.sh = max(40, int(320 * self.h / self.w))
        yy, xx = np.mgrid[0:self.sh, 0:self.sw].astype(np.float32)
        self.xx, self.yy = xx / self.sw, yy / self.sh
        t = self.yy[..., None]
        self.base = np.array((8, 70, 90), np.float32) * (1 - t) + np.array((2, 8, 30), np.float32) * t
        rng = np.random.default_rng(3)
        self.rib = [(rng.uniform(0, 6.28), rng.uniform(0.08, 0.2), rng.uniform(0.4, 0.9), rng.uniform(0.02, 0.06),
                     rng.uniform(0.2, 0.7), rng.uniform(0.1, 0.35)) for _ in range(6)]
        self.cols = [np.array(c, np.float32) for c in ((150, 255, 220), (110, 190, 255), (200, 160, 255))]

    def draw(self, t, dt):
        out = self.base.copy()
        for i, (ph, amp, fr, th, tilt, sp) in enumerate(self.rib):
            path = 0.55 - tilt * self.xx + amp * np.sin(fr * self.xx * 6.283 + ph + t * sp) \
                + 0.03 * np.sin(3.3 * self.xx * 6.283 + ph * 1.7 - t * sp * 1.3)
            d = (self.yy - path) / th
            core = np.exp(-d * d) * (0.6 + 0.4 * math.sin(t * 0.3 + i))
            out += core[..., None] * self.cols[i % 3] * 0.5
        img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), 'RGB')
        return self.scaled(img.filter(ImageFilter.GaussianBlur(1)))


class ChannelRain(Saver):
    """Columns of COS characters falling like rain, in channel teal."""
    name = 'Channel Rain'
    GLYPHS = 'COSNEPTUNE0123456789<>[]{}#*+=/\\|:;$%&@CHANNEL'

    def __init__(self, w, h, tint=(38, 150, 160)):
        super().__init__(w, h, tint)
        self.s = max(1, int(round(self.w / 1600)))
        self.sw, self.sh = self.w // self.s, self.h // self.s
        self.size = max(10, self.sh // 40)
        self.fnt = gfx.font(self.size, True, 'mono')
        self.cw = int(self.fnt.getlength('M')) + 2
        self.cols = []
        for x in range(0, self.sw, self.cw):
            self.cols.append([x, self.rng.uniform(-self.sh, 0), self.rng.uniform(0.25, 0.7) * self.sh,
                              self.rng.randint(8, 30), [self.rng.choice(self.GLYPHS) for _ in range(40)]])
        self.img = Image.new('RGB', (self.sw, self.sh), (0, 0, 0))

    def draw(self, t, dt):
        # fade the old frame instead of clearing, for the trails
        self.img = Image.eval(self.img, lambda v: int(v * 0.86))
        d = ImageDraw.Draw(self.img)
        lh = self.size + 2
        for c in self.cols:
            c[1] += c[2] * dt
            row = int(c[1] // lh)
            if c[1] - c[3] * lh > self.sh:
                c[1] = self.rng.uniform(-self.sh * 0.5, 0)
                c[2] = self.rng.uniform(0.25, 0.7) * self.sh
            if self.rng.random() < 0.08:
                c[4][self.rng.randrange(len(c[4]))] = self.rng.choice(self.GLYPHS)
            y = row * lh
            if 0 <= y < self.sh:
                d.text((c[0], y), c[4][row % len(c[4])], font=self.fnt, fill=(200, 255, 240))
                if y - lh >= 0:
                    d.text((c[0], y - lh), c[4][(row - 1) % len(c[4])], font=self.fnt, fill=(40, 200, 170))
        return self.scaled(self.img)


class Clock(Saver):
    """A big clock that drifts slowly, so nothing burns into the screen."""
    name = 'Clock'

    def __init__(self, w, h, tint=(38, 150, 160)):
        super().__init__(w, h, tint)
        self.fbig = gfx.font(max(12, int(self.h * 0.16)), False, 'light')
        self.fsm = gfx.font(max(8, int(self.h * 0.035)), False, 'light')
        self.base = Image.new('RGBA', (self.w, self.h), (0, 0, 0, 255))
        self.sec = None
        self.cache = None

    def draw(self, t, dt):
        now = time.localtime()
        if self.cache is not None and now.tm_sec == self.sec:
            return self.cache
        self.sec = now.tm_sec
        img = self.base.copy()
        cx = self.w / 2 + math.sin(t / 37.0) * self.w * 0.3
        cy = self.h / 2 + math.cos(t / 29.0) * self.h * 0.25
        hhmm = time.strftime('%H:%M', now)
        gfx.glow_text(img, (cx, cy), hhmm, self.fbig, (235, 250, 255, 255), glow=self.tint, anchor='mm')
        sub = time.strftime('%A, %B ', now) + str(now.tm_mday) + time.strftime('   :%S', now)
        gfx.draw_text(img, (cx, cy + self.fbig.size * 0.75), sub, self.fsm, (140, 190, 210, 255), anchor='mm')
        self.cache = img
        return img


SAVERS = [Bubbles, BouncingCOS, Warp, Aurora, ChannelRain, Clock, Blank]
NAMES = [c.name for c in SAVERS]


def make(name, w, h, tint=(38, 150, 160)):
    for c in SAVERS:
        if c.name == name:
            return c(w, h, tint)
    return Bubbles(w, h, tint)
