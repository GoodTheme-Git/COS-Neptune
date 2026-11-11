#!/usr/bin/env python3
"""Draw the Neptune Mobile app icons with Neptune's own COS bubble (nep/wallpaper.py)."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))          # ~/cos/neptune
from PIL import Image
from nep import wallpaper

OUT = os.path.join(os.path.dirname(HERE), 'web', 'icons')
os.makedirs(OUT, exist_ok=True)
st = wallpaper._BUBBLE_STYLE['COS Bubble']
for size, maskable in ((192, False), (512, False), (512, True), (48, False), (72, False), (96, False), (144, False)):
    bg = Image.fromarray(wallpaper._vgrad3(size, size, *st['bg']).clip(0, 255).astype('uint8'), 'RGB').convert('RGBA')
    r = int(size * (0.30 if maskable else 0.36))
    logo = wallpaper.bubble_logo(r, st['ball'], st['rim'])
    bg.alpha_composite(logo, ((size - logo.size[0]) // 2, (size - logo.size[1]) // 2))
    name = 'icon-%d%s.png' % (size, '-maskable' if maskable else '')
    bg.convert('RGB').save(os.path.join(OUT, name))
    print(name)
