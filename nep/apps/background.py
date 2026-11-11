"""Desktop Background: choose a Neptune wallpaper, an original Nostalgia design,
a solid colour, a gradient, a pattern, your own text, or your own picture.

Changes show on the desktop straight away; Cancel (or closing the window)
puts the old background back, Save changes keeps the new one.
"""
import json
import os

from .. import gfx, icons, theme, wallpaper
from ..app import App
from .dialogs import FileDialog, message

KINDS = [('preset', 'Neptune'), ('bubble', 'Bubbles'), ('nostalgia', 'Nostalgia'), ('solid', 'Solid color'), ('gradient', 'Gradient'),
         ('pattern', 'Pattern'), ('text', 'Text'), ('image', 'Picture')]
SWATCHES = ['#000000', '#3c3c3c', '#7f7f7f', '#c3c3c3', '#ffffff', '#0e2a50', '#1e4e8c', '#3a7bd5',
            '#0c787d', '#008080', '#2e7d32', '#9ccc65', '#f9a825', '#ef6c00', '#c62828', '#ad1457',
            '#6a1b9a', '#3a0a5a', '#2b1055', '#ff7a59', '#8d6e63', '#3e2723', '#e6f6ff', '#102a4a']
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
DEFAULTS = {
    'solid': {'kind': 'solid', 'color': '#1e4e8c'},
    'gradient': {'kind': 'gradient', 'colors': ['#0c787d', '#081e5a'], 'style': 'vertical'},
    'pattern': {'kind': 'pattern', 'name': 'Hexagons', 'fg': '#3a7bd5', 'bg': '#0e2a50', 'scale': 1.0},
    'text': {'kind': 'text', 'text': 'COS Neptune', 'fg': '#e6f6ff', 'bg': '#102a4a', 'bg2': '#081e5a',
             'layout': 'center', 'font': 'serif', 'size': 64},
    'nostalgia': {'kind': 'nostalgia', 'name': 'Rolling Hills'},
    'bubble': {'kind': 'bubble', 'name': 'COS Bubble'},
    'preset': {'kind': 'preset', 'name': 'Neptune Aurora'},
    'image': {'kind': 'image', 'path': '', 'fit': 'fill', 'bg': '#000000'},
}


class Background(App):
    title = 'Desktop Background'
    icon = 'pictures'
    size = (820, 600)
    min_size = (720, 560)
    single_instance = True

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        self.saved = shell.settings.get('wall')
        self.saved_name = shell.settings.get('wallpaper')
        spec = dict(shell.wall_spec())
        self.spec = spec
        self.kind = spec.get('kind', 'preset')
        self.drafts = {k: dict(v) for k, v in DEFAULTS.items()}
        self.drafts[self.kind] = dict(spec)
        self.thumbs = {}
        self.hex = {}
        self.text = {'text': spec.get('text', DEFAULTS['text']['text']).replace('\n', ' | '), 'cur': 0}
        self.pictures = []
        self.scan_pictures()

    # -- helpers -----------------------------------------------------------------------
    def thumb(self, spec, w, h):
        key = (json.dumps(spec, sort_keys=True), w, h)
        im = self.thumbs.get(key)
        if im is None:
            try:
                im = wallpaper.build(spec, w, h)
            except Exception:
                im = gfx.new(w, h, (60, 60, 60))
            if len(self.thumbs) > 200:
                self.thumbs.clear()
            self.thumbs[key] = im
        return im

    def scan_pictures(self):
        found = []
        for d in (wallpaper.WALL_DIR, os.path.join(theme.HOME, 'Pictures'), os.path.join(theme.HOME, 'Downloads')):
            try:
                for n in sorted(os.listdir(d)):
                    if n.lower().endswith(IMAGE_EXTS):
                        found.append(os.path.join(d, n))
            except OSError:
                pass
        self.pictures = found[:24]

    def apply(self, spec):
        self.spec = dict(spec)
        self.drafts[self.spec['kind']] = dict(spec)
        self.shell.settings['wall'] = dict(spec)
        if spec['kind'] == 'preset':
            self.shell.settings['wallpaper'] = spec.get('name', 'Neptune Aurora')
        self.shell.desk_dirty = True
        self.shell.dirty = True

    def update(self, **kw):
        s = dict(self.spec if self.spec.get('kind') == self.kind else self.drafts[self.kind])
        s.update(kw)
        s['kind'] = self.kind
        self.apply(s)

    # -- drawing ---------------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        ui.command_bar((0, 0, w, 32))
        ui.text((12, 16), 'Control Panel  ▸  Personalization  ▸  Desktop Background', 12, color=(30, 50, 80),
                anchor='lm')
        ui.text((20, 46), 'Choose your desktop background', 16, color=(0, 51, 153), kind='light')
        # kind tabs
        x = 20
        for k, label in KINDS:
            tw = gfx.text_width(gfx.font(12), label) + 24
            ui.toolbutton((x, 78, x + tw, 102), label, ('kind', k), toggled=self.kind == k)
            x += tw + 4
        ui.hline(20, w - 20, 108, (220, 228, 235))
        body = (20, 118, w - 300, h - 56)
        getattr(self, 'draw_' + self.kind)(img, ui, body)
        # preview
        px0, py0 = w - 280, 118
        pw, ph = 260, 146
        ui.text((px0, py0 - 2), 'Preview', 12, True, color=(30, 60, 110))
        frame = (px0, py0 + 18, px0 + pw, py0 + 18 + ph)
        gfx.round_rect(img, (frame[0] - 6, frame[1] - 6, frame[2] + 6, frame[3] + 22), 6,
                       fill=gfx.vgradient(pw + 12, ph + 28, ((0, (70, 75, 85)), (1, (25, 28, 34)))))
        img.alpha_composite(self.thumb(self.spec, pw, ph), (frame[0], frame[1]))
        # tiny taskbar so the preview reads as a desktop
        img.alpha_composite(gfx.vgradient(pw, 10, ((0, (30, 60, 70, 220)), (1, (10, 25, 35, 230)))), (frame[0], frame[3] - 10))
        img.alpha_composite(icons.get('neptune', 8), (frame[0] + 2, frame[3] - 9))
        ui.wrap((px0, frame[3] + 30), self.describe(), pw, 11, color=theme.MUTED)
        # buttons
        ui.rect((0, h - 44, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - 44, (223, 223, 223))
        ui.button((w - 196, h - 34, w - 104, h - 10), 'Save changes', 'save', default=True)
        ui.button((w - 96, h - 34, w - 12, h - 10), 'Cancel', 'cancel')

    def describe(self):
        s = self.spec
        k = s.get('kind')
        if k in ('preset', 'nostalgia', 'bubble'):
            return s.get('name', '')
        if k == 'solid':
            return 'Solid color %s' % s.get('color')
        if k == 'gradient':
            return '%s gradient, %s to %s' % (s.get('style', 'vertical').title(), *s.get('colors', ['?', '?'])[:2])
        if k == 'pattern':
            return '%s pattern' % s.get('name')
        if k == 'text':
            return 'Text: "%s"' % s.get('text', '').replace('\n', ' / ')
        if k == 'image':
            return os.path.basename(s.get('path', '')) or 'No picture chosen yet'
        return ''

    def grid(self, img, ui, box, specs, labels, current, cid):
        x0, y0, x1, y1 = box
        tw, th = 148, 84
        per = max(1, (x1 - x0) // (tw + 14))
        for i, (spec, label) in enumerate(zip(specs, labels)):
            cx = x0 + (i % per) * (tw + 14)
            cy = y0 + (i // per) * (th + 30)
            if cy + th > y1:
                break
            r = (cx, cy, cx + tw, cy + th)
            sel = label == current
            if sel or ui.hot(r):
                gfx.round_rect(img, (r[0] - 4, r[1] - 4, r[2] + 4, r[3] + 22), 4,
                               fill=(220, 235, 252, 255) if sel else (240, 246, 252, 255),
                               outline=(60, 127, 177, 255) if sel else (170, 200, 230, 255))
            img.alpha_composite(self.thumb(spec, tw, th), (cx, cy))
            ui.rect(r, outline=(90, 100, 110))
            ui.text(((r[0] + r[2]) // 2, r[3] + 9), label, 10, color=(60, 70, 80), anchor='mm')
            ui.region(r, (cid, label))

    def swatches(self, img, ui, x, y, cid, current, label):
        ui.text((x, y + 9), label, 12, anchor='lm')
        x += 96
        for i, c in enumerate(SWATCHES):
            r = (x + (i % 12) * 22, y + (i // 12) * 22, x + (i % 12) * 22 + 18, y + (i // 12) * 22 + 18)
            on = (current or '').lower() == c
            ui.rect(r, fill=wallpaper._rgb(c), outline=(40, 110, 200) if on or ui.hot(r) else (130, 130, 130))
            if on:
                ui.rect((r[0] - 2, r[1] - 2, r[2] + 2, r[3] + 2), outline=(40, 110, 200))
            ui.region(r, (cid, c))
        st = self.hex.setdefault(cid, {'text': current or '', 'cur': len(current or '')})
        if ui.focus != 'hex_' + cid:
            st['text'], st['cur'] = current or '', len(current or '')
        ui.textbox((x + 12 * 22 + 8, y, x + 12 * 22 + 84, y + 22), 'hex_' + cid, st)
        return y + 48

    def draw_preset(self, img, ui, box):
        names = theme.WALLPAPERS
        self.grid(img, ui, box, [{'kind': 'preset', 'name': n} for n in names], names,
                  self.spec.get('name') if self.spec.get('kind') == 'preset' else None, 'preset')

    def draw_bubble(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.text((x0, y0), 'The COS 3.1 bubble look, drawn sharp at any screen size.', 11, color=theme.MUTED)
        names = wallpaper.BUBBLES
        self.grid(img, ui, (x0, y0 + 22, x1, y1), [{'kind': 'bubble', 'name': n} for n in names], names,
                  self.spec.get('name') if self.spec.get('kind') == 'bubble' else None, 'bubble')

    def draw_nostalgia(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.text((x0, y0), 'Original designs in the spirit of 90s and 2000s desktops.', 11, color=theme.MUTED)
        names = wallpaper.NOSTALGIA
        self.grid(img, ui, (x0, y0 + 22, x1, y1), [{'kind': 'nostalgia', 'name': n} for n in names], names,
                  self.spec.get('name') if self.spec.get('kind') == 'nostalgia' else None, 'nostalgia')

    def draw_solid(self, img, ui, box):
        x0, y0, x1, y1 = box
        s = self.drafts['solid'] if self.spec.get('kind') != 'solid' else self.spec
        self.swatches(img, ui, x0, y0 + 6, 'color', s.get('color'), 'Color:')

    def draw_gradient(self, img, ui, box):
        x0, y0, x1, y1 = box
        s = self.spec if self.spec.get('kind') == 'gradient' else self.drafts['gradient']
        cols = s.get('colors', ['#0c787d', '#081e5a'])
        y = self.swatches(img, ui, x0, y0 + 6, 'g1', cols[0], 'From:')
        y = self.swatches(img, ui, x0, y, 'g2', cols[-1], 'To:')
        ui.text((x0, y + 8), 'Direction:', 12, anchor='lm')
        xx = x0 + 96
        for st in wallpaper.GRADIENT_STYLES:
            ui.radio((xx, y), st.title(), ('gstyle', st), s.get('style', 'vertical') == st, data=st)
            xx += 100
        y += 40
        ui.text((x0, y), 'Ideas:', 12, color=theme.MUTED)
        ideas = [('Neptune', ['#0c787d', '#081e5a']), ('Sunset', ['#ff7a59', '#2b1055']), ('Mint', ['#9be7c4', '#1e6f5c']),
                 ('Ember', ['#f9a825', '#6a1b1a']), ('Steel', ['#c3cfe2', '#3a4a5c']), ('Grape', ['#ad1457', '#1a0033'])]
        xx = x0 + 96
        for name, c in ideas:
            spec = {'kind': 'gradient', 'colors': c, 'style': s.get('style', 'vertical')}
            r = (xx, y - 4, xx + 64, y + 32)
            img.alpha_composite(self.thumb(spec, 64, 36), (r[0], r[1]))
            ui.rect(r, outline=(40, 110, 200) if ui.hot(r) else (130, 130, 130))
            ui.text(((r[0] + r[2]) // 2, r[3] + 8), name, 10, color=(60, 70, 80), anchor='mm')
            ui.region(r, ('gidea', name), data=c)
            xx += 74

    def draw_pattern(self, img, ui, box):
        x0, y0, x1, y1 = box
        s = self.spec if self.spec.get('kind') == 'pattern' else self.drafts['pattern']
        y = self.swatches(img, ui, x0, y0 + 6, 'pfg', s.get('fg'), 'Foreground:')
        y = self.swatches(img, ui, x0, y, 'pbg', s.get('bg'), 'Background:')
        ui.text((x0, y + 8), 'Size:', 12, anchor='lm')
        ui.slider((x0 + 96, y, x0 + 300, y + 16), 'pscale', (float(s.get('scale', 1.0)) - 0.4) / 2.6)
        y += 30
        names = wallpaper.PATTERNS
        specs = [dict(s, name=n) for n in names]
        tw = 96
        per = max(1, (x1 - x0) // (tw + 10))
        for i, (spec, n) in enumerate(zip(specs, names)):
            cx, cy = x0 + (i % per) * (tw + 10), y + (i // per) * (54 + 24)
            if cy + 54 > y1:
                break
            r = (cx, cy, cx + tw, cy + 54)
            sel = s.get('name') == n
            if sel or ui.hot(r):
                ui.rect((r[0] - 3, r[1] - 3, r[2] + 3, r[3] + 18), fill=(220, 235, 252) if sel else (240, 246, 252),
                        outline=(60, 127, 177) if sel else (170, 200, 230))
            img.alpha_composite(self.thumb(spec, tw, 54), (cx, cy))
            ui.rect(r, outline=(90, 100, 110))
            ui.text(((r[0] + r[2]) // 2, r[3] + 8), n, 10, color=(60, 70, 80), anchor='mm')
            ui.region(r, ('pname', n))

    def draw_text(self, img, ui, box):
        x0, y0, x1, y1 = box
        s = self.spec if self.spec.get('kind') == 'text' else self.drafts['text']
        ui.text((x0, y0 + 16), 'Text:', 12, anchor='lm')
        ui.textbox((x0 + 96, y0 + 4, x1, y0 + 28), 'text', self.text, placeholder='Your text (use | for a new line)')
        ui.button((x1 - 70, y0 + 34, x1, y0 + 56), 'Apply', 'text_apply', size=11)
        y = y0 + 64
        y = self.swatches(img, ui, x0, y, 'tfg', s.get('fg'), 'Text color:')
        y = self.swatches(img, ui, x0, y, 'tbg', s.get('bg'), 'Top:')
        y = self.swatches(img, ui, x0, y, 'tbg2', s.get('bg2') or s.get('bg'), 'Bottom:')
        ui.text((x0, y + 8), 'Layout:', 12, anchor='lm')
        xx = x0 + 96
        for k, label in (('center', 'Centered'), ('tile', 'Tiled'), ('corner', 'Corner')):
            ui.radio((xx, y), label, ('tlayout', k), s.get('layout', 'center') == k, data=k)
            xx += 100
        y += 28
        ui.text((x0, y + 8), 'Font:', 12, anchor='lm')
        xx = x0 + 96
        for k, label in (('serif', 'Serif'), ('sans', 'Sans'), ('mono', 'Mono'), ('light', 'Light')):
            ui.radio((xx, y), label, ('tfont', k), s.get('font', 'serif') == k, data=k)
            xx += 80
        y += 28
        ui.text((x0, y + 8), 'Size:', 12, anchor='lm')
        ui.slider((x0 + 96, y, x0 + 300, y + 16), 'tsize', (float(s.get('size', 64)) - 16) / 144)

    def draw_image(self, img, ui, box):
        x0, y0, x1, y1 = box
        s = self.spec if self.spec.get('kind') == 'image' else self.drafts['image']
        ui.button((x0, y0, x0 + 150, y0 + 26), 'Browse...', 'browse', default=True, icon='pictures')
        ui.text((x0 + 162, y0 + 13), 'Pick any picture. Neptune keeps its own copy.', 11, color=theme.MUTED,
                anchor='lm')
        y = y0 + 40
        ui.text((x0, y + 8), 'Fit:', 12, anchor='lm')
        xx = x0 + 60
        for f in wallpaper.FITS:
            ui.radio((xx, y), f.title(), ('fit', f), s.get('fit', 'fill') == f, data=f)
            xx += 84
        y += 34
        if not self.pictures:
            ui.text((x0, y + 4), 'No pictures in Pictures or Downloads yet. Use Browse...', 12, color=theme.MUTED)
            return
        tw, th = 96, 60
        per = max(1, (x1 - x0) // (tw + 10))
        for i, p in enumerate(self.pictures):
            cx, cy = x0 + (i % per) * (tw + 10), y + (i // per) * (th + 22)
            if cy + th > y1:
                break
            r = (cx, cy, cx + tw, cy + th)
            sel = os.path.realpath(s.get('path', '') or '') == os.path.realpath(p)
            if sel or ui.hot(r):
                ui.rect((r[0] - 3, r[1] - 3, r[2] + 3, r[3] + 18), fill=(220, 235, 252) if sel else (240, 246, 252),
                        outline=(60, 127, 177) if sel else (170, 200, 230))
            img.alpha_composite(self.thumb({'kind': 'image', 'path': p, 'fit': 'fill'}, tw, th), (cx, cy))
            ui.rect(r, outline=(90, 100, 110))
            ui.text(((r[0] + r[2]) // 2, r[3] + 8), gfx.ellipsize(gfx.font(10), os.path.basename(p), tw), 10,
                    color=(60, 70, 80), anchor='mm')
            ui.region(r, ('pic', p))

    # -- input ---------------------------------------------------------------------------------
    def on_command(self, cid, data):
        if isinstance(cid, tuple):
            k, v = cid
            if k == 'kind':
                self.kind = v
                if self.spec.get('kind') != v:
                    d = dict(self.drafts[v])
                    if v != 'image' or d.get('path'):
                        self.apply(d)
            elif k in ('preset', 'nostalgia', 'bubble'):
                self.kind = k
                self.apply({'kind': k, 'name': v})
            elif k == 'color':
                self.update(color=v)
            elif k in ('g1', 'g2'):
                cols = list((self.spec if self.spec.get('kind') == 'gradient' else self.drafts['gradient'])['colors'])
                cols[0 if k == 'g1' else -1] = v
                self.update(colors=cols)
            elif k == 'gstyle':
                self.update(style=v)
            elif k == 'gidea':
                self.update(colors=list(data))
            elif k == 'pfg':
                self.update(fg=v)
            elif k == 'pbg':
                self.update(bg=v)
            elif k == 'pname':
                self.update(name=v)
            elif k == 'tfg':
                self.update(fg=v)
            elif k == 'tbg':
                self.update(bg=v)
            elif k == 'tbg2':
                self.update(bg2=v)
            elif k == 'tlayout':
                self.update(layout=v)
            elif k == 'tfont':
                self.update(font=v)
            elif k == 'fit':
                self.update(fit=v)
            elif k == 'pic':
                self.use_picture(v)
            return
        if isinstance(cid, str) and cid.startswith('hex_') and data == 'enter':
            self.hex_entered(cid[4:])
        elif cid in ('text_apply',) or (cid == 'text' and data == 'enter'):
            self.update(text=self.text['text'].replace(' | ', '\n').replace('|', '\n').strip() or 'COS Neptune')
        elif cid == 'browse':
            self.shell.open_window(FileDialog(self.shell, 'open', start=os.path.join(theme.HOME, 'Pictures'),
                                              exts=IMAGE_EXTS, callback=self.use_picture), parent=self.win)
        elif cid == 'save':
            self.shell.settings.save()
            self.saved = self.shell.settings.get('wall')
            self.saved_name = self.shell.settings.get('wallpaper')
            self.close()
        elif cid == 'cancel':
            self.close()

    def hex_entered(self, which):
        t = self.hex.get(which, {}).get('text', '').strip()
        if not t.startswith('#'):
            t = '#' + t
        if len(t) not in (4, 7) or any(c not in '0123456789abcdefABCDEF' for c in t[1:]):
            message(self.shell, 'Desktop Background', 'Colors look like #1e4e8c.', 'warning', parent=self.win)
            return
        self.ui.focus = None
        self.on_command((which, t.lower()), None)

    def use_picture(self, path):
        try:
            dest = wallpaper.import_image(path)
        except Exception as e:
            message(self.shell, 'Desktop Background', "That doesn't look like a picture Neptune can open.\n%s" % e,
                    'warning', parent=self.win)
            return
        self.kind = 'image'
        self.update(path=dest)
        self.scan_pictures()

    def on_slider(self, cid, value):
        if cid == 'pscale':
            self.update(scale=round(0.4 + value * 2.6, 2))
        elif cid == 'tsize':
            self.update(size=int(16 + value * 144))

    def on_close(self):
        s = self.shell.settings
        if s.get('wall') != self.saved or s.get('wallpaper') != self.saved_name:
            s['wall'] = self.saved
            s['wallpaper'] = self.saved_name
            self.shell.desk_dirty = True
            self.shell.dirty = True
        return True
