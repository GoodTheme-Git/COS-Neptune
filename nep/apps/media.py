"""Calculator, Paint and Personalize."""
import math
import os
import time

from PIL import Image, ImageDraw

from .. import gfx, icons, theme, wallpaper
from ..app import App
from .dialogs import FileDialog, message


# ---------------------------------------------------------------------------
class Calculator(App):
    title = 'Calculator'
    icon = 'calc'
    size = (262, 348)
    resizable = False
    bg = (217, 228, 241, 255)
    KEYS = [['MC', 'MR', 'MS', 'M+', 'M-'],
            ['←', 'CE', 'C', '±', '√'],
            ['7', '8', '9', '/', '%'],
            ['4', '5', '6', '*', '1/x'],
            ['1', '2', '3', '-', '='],
            ['0', None, '.', '+', None]]

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        self.display = '0'
        self.acc = None
        self.op = None
        self.fresh = True
        self.mem = 0.0
        self.expr = ''

    def draw(self, img, ui):
        w, h = img.size
        img.alpha_composite(gfx.vgradient(w, h, ((0, (230, 238, 248)), (1, (205, 218, 235)))), (0, 0))
        lcd = (10, 10, w - 10, 70)
        gfx.round_rect(img, lcd, 3, fill=gfx.vgradient(lcd[2] - lcd[0], 60, ((0, (236, 244, 252)), (1, (220, 232, 246)))),
                       outline=(142, 156, 173, 255))
        ui.text((lcd[2] - 8, 22), self.expr, 11, color=(100, 110, 120), anchor='rm')
        disp = self.display
        size = 24 if len(disp) < 14 else 16
        ui.text((lcd[2] - 8, 50), disp, size, color=(0, 0, 0), anchor='rm')
        if self.mem:
            ui.text((lcd[0] + 8, 50), 'M', 12, color=(60, 60, 60), anchor='lm')
        bw, bh, gap = 40, 31, 6
        x0, y0 = 12, 82
        for r, row in enumerate(self.KEYS):
            for c, k in enumerate(row):
                if k is None:
                    continue
                x = x0 + c * (bw + gap)
                y = y0 + r * (bh + gap)
                ww, hh = bw, bh
                if k == '0':
                    ww = bw * 2 + gap
                if k == '=':
                    hh = bh * 2 + gap
                ui.button((x, y, x + ww, y + hh), k, ('k', k), size=12 if len(k) < 3 else 10)

    def num(self):
        try:
            return float(self.display)
        except ValueError:
            return 0.0

    def show(self, v):
        if isinstance(v, str):
            self.display = v
            return
        if v != v or v in (float('inf'), float('-inf')):
            self.display = 'Cannot divide by zero'
            return
        if abs(v) >= 1e16 or (abs(v) < 1e-9 and v != 0):
            self.display = '%.10g' % v
        else:
            s = ('%.12f' % v).rstrip('0').rstrip('.')
            self.display = s if s not in ('-0', '') else '0'

    def compute(self):
        if self.op is None or self.acc is None:
            return self.num()
        a, b = self.acc, self.num()
        try:
            return {'+': a + b, '-': a - b, '*': a * b, '/': a / b}[self.op]
        except ZeroDivisionError:
            return float('inf')

    def press(self, k):
        if k.isdigit() or k == '.':
            numeric = self.display.replace('-', '', 1).replace('.', '', 1).isdigit()
            if self.fresh or not numeric or self.display == '0':
                self.display = '0.' if k == '.' else k
                self.fresh = False
            else:
                if k == '.' and '.' in self.display:
                    return
                self.display += k
        elif k in '+-*/':
            if not self.fresh and self.op is not None:
                self.show(self.compute())
            self.acc = self.num()
            self.op = k
            self.expr = '%s %s' % (self.display, {'*': '×', '/': '÷'}.get(k, k))
            self.fresh = True
        elif k == '=':
            if self.op is not None:
                self.expr = ''
                self.show(self.compute())
                self.acc, self.op = None, None
                self.fresh = True
        elif k == 'C':
            self.display, self.acc, self.op, self.fresh, self.expr = '0', None, None, True, ''
        elif k == 'CE':
            self.display, self.fresh = '0', True
        elif k == '←':
            if not self.fresh:
                self.display = self.display[:-1] or '0'
        elif k == '±':
            self.show(-self.num())
        elif k == '√':
            self.show(math.sqrt(self.num()) if self.num() >= 0 else float('nan'))
            self.fresh = True
        elif k == '%':
            if self.acc is not None:
                self.show(self.acc * self.num() / 100)
        elif k == '1/x':
            self.show(1 / self.num() if self.num() else float('inf'))
            self.fresh = True
        elif k == 'MC':
            self.mem = 0.0
        elif k == 'MR':
            self.show(self.mem)
            self.fresh = True
        elif k == 'MS':
            self.mem = self.num()
        elif k == 'M+':
            self.mem += self.num()
        elif k == 'M-':
            self.mem -= self.num()

    def on_command(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'k':
            self.press(cid[1])

    def on_key(self, ev):
        ch = ev.get('char', '')
        k = ev['key']
        if ch and ch in '0123456789.+-*/%':
            self.press(ch)
            return True
        if ch == '=' or k == 'enter':
            self.press('=')
            return True
        if k == 'backspace':
            self.press('←')
            return True
        if k == 'escape':
            self.press('C')
            return True
        if k == 'delete':
            self.press('CE')
            return True
        return False


# ---------------------------------------------------------------------------
PALETTE = [(0, 0, 0), (127, 127, 127), (136, 0, 21), (237, 28, 36), (255, 127, 39), (255, 242, 0),
           (34, 177, 76), (0, 162, 232), (63, 72, 204), (163, 73, 164),
           (255, 255, 255), (195, 195, 195), (185, 122, 87), (255, 174, 201), (255, 201, 14), (239, 228, 176),
           (181, 230, 29), (153, 217, 234), (112, 146, 190), (200, 191, 231)]


class Paint(App):
    title = 'Untitled - Paint'
    icon = 'paint'
    size = (820, 580)
    min_size = (480, 320)
    bg = (207, 219, 235, 255)
    TOOLS = [('pencil', 'Pencil'), ('brush', 'Brush'), ('eraser', 'Eraser'), ('fill', 'Fill'),
             ('line', 'Line'), ('rect', 'Rectangle'), ('ellipse', 'Ellipse'), ('picker', 'Picker')]

    def __init__(self, shell, path=None, **kw):
        super().__init__(shell, **kw)
        self.canvas = Image.new('RGBA', (640, 400), (255, 255, 255, 255))
        self.tool = 'brush'
        self.color = (0, 0, 0)
        self.color2 = (255, 255, 255)
        self.width = 4
        self.last = None
        self.start = None
        self.preview = None
        self.path = None
        self.modified = False
        self.undo = []
        if path:
            try:
                self.canvas = Image.open(path).convert('RGBA')
                self.path = path
            except Exception:
                pass
        self.update_title()

    RIBBON = 92

    def update_title(self):
        self.set_title('%s%s - Paint' % ('*' if self.modified else '',
                                          os.path.basename(self.path) if self.path else 'Untitled'))

    def canvas_origin(self):
        return (10, self.RIBBON + 10)

    def draw(self, img, ui):
        w, h = img.size
        # ribbon
        img.alpha_composite(gfx.vgradient(w, self.RIBBON, ((0, (252, 253, 255)), (0.75, (231, 239, 249)),
                                                           (1, (219, 230, 244)))), (0, 0))
        ui.hline(0, w, self.RIBBON - 1, (160, 178, 200))
        ui.button((8, 8, 70, 30), 'Save', 'save', icon='image', size=11)
        ui.button((8, 34, 70, 56), 'New', 'new', size=11)
        ui.button((8, 60, 70, 82), 'Undo', 'undo', size=11, enabled=bool(self.undo))
        ui.d.line([(80, 8), (80, 82)], fill=(200, 210, 225, 255))
        x = 90
        for i, (key, label) in enumerate(self.TOOLS):
            bx = x + (i % 4) * 62
            by = 8 + (i // 4) * 30
            ui.toolbutton((bx, by, bx + 60, by + 26), label, ('tool', key), toggled=self.tool == key, size=11)
        x = 90 + 4 * 62 + 10
        ui.d.line([(x - 6, 8), (x - 6, 82)], fill=(200, 210, 225, 255))
        ui.text((x, 14), 'Size', 11, color=(60, 70, 90))
        for i, s in enumerate((1, 3, 6, 12)):
            r = (x + i * 26, 30, x + i * 26 + 24, 54)
            ui.toolbutton(r, '', ('size', s), toggled=self.width == s)
            cy = 42
            ui.d.ellipse((r[0] + 12 - s / 2 - 0.5, cy - s / 2, r[0] + 12 + s / 2, cy + s / 2), fill=(30, 30, 30, 255))
        x += 4 * 26 + 12
        ui.d.line([(x - 6, 8), (x - 6, 82)], fill=(200, 210, 225, 255))
        # current colours
        for j, (c, lab) in enumerate(((self.color, 'Color 1'), (self.color2, 'Color 2'))):
            r = (x + j * 44, 10, x + j * 44 + 36, 46)
            ui.rect(r, fill=c, outline=(80, 80, 80))
            ui.text(((r[0] + r[2]) // 2, 60), lab, 10, color=(60, 70, 90), anchor='mm')
        x += 96
        for i, c in enumerate(PALETTE):
            r = (x + (i % 10) * 22, 12 + (i // 10) * 22, x + (i % 10) * 22 + 19, 12 + (i // 10) * 22 + 19)
            ui.rect(r, fill=c, outline=(130, 130, 130) if not ui.hot(r) else (40, 110, 200))
            ui.region(r, ('color', i))
        # canvas
        ox, oy = self.canvas_origin()
        cw, ch = self.canvas.size
        sh, pad = gfx.shadow(cw, ch, 0, 5, 90)
        gfx.alpha_composite_clip(img, sh, ox - pad + 2, oy - pad + 2)
        gfx.alpha_composite_clip(img, self.canvas, ox, oy)
        if self.preview is not None:
            gfx.alpha_composite_clip(img, self.preview, ox, oy)
        ui.region((ox, oy, ox + cw, oy + ch), 'canvas')
        ui.rect((0, h - 22, w, h), fill=(231, 239, 249))
        ui.hline(0, w, h - 22, (190, 200, 215))
        mx, my = ui.mouse
        if ox <= mx < ox + cw and oy <= my < oy + ch:
            ui.text((10, h - 11), '%d, %dpx' % (mx - ox, my - oy), 11, color=(60, 70, 90), anchor='lm')
        ui.text((140, h - 11), '%d × %dpx' % (cw, ch), 11, color=(60, 70, 90), anchor='lm')

    def cursor_at(self, x, y):
        h = self.ui.hit(x, y)
        if h and h[0] == 'canvas':
            return 'ibeam' if self.tool == 'picker' else 'move' if self.tool == 'fill' else 'arrow'
        return 'arrow'

    def _pt(self, x, y):
        ox, oy = self.canvas_origin()
        return (x - ox, y - oy)

    def push_undo(self):
        self.undo.append(self.canvas.copy())
        self.undo = self.undo[-20:]

    def on_down(self, cid, kind, data, x, y, button):
        if cid != 'canvas':
            return
        p = self._pt(x, y)
        col = self.color if button == 1 else self.color2
        self.draw_col = col
        if self.tool == 'picker':
            c = self.canvas.getpixel(p)[:3]
            if button == 1:
                self.color = c
            else:
                self.color2 = c
            return
        self.push_undo()
        if self.tool == 'fill':
            ImageDraw.floodfill(self.canvas, p, col + (255,), thresh=30)
            self.dirty_doc()
            return
        self.last = p
        self.start = p
        if self.tool in ('pencil', 'brush', 'eraser'):
            self.stroke(p, p)

    def dirty_doc(self):
        if not self.modified:
            self.modified = True
            self.update_title()

    def stroke(self, a, b):
        d = ImageDraw.Draw(self.canvas)
        col = (255, 255, 255, 255) if self.tool == 'eraser' else self.draw_col + (255,)
        wd = 1 if self.tool == 'pencil' else (self.width * (3 if self.tool == 'eraser' else 1))
        d.line([a, b], fill=col, width=wd)
        if wd > 2:
            r = wd / 2
            d.ellipse((b[0] - r, b[1] - r, b[0] + r, b[1] + r), fill=col)
        self.dirty_doc()

    def on_drag(self, cid, x, y):
        if self.start is None:
            return
        p = self._pt(x, y)
        if self.tool in ('pencil', 'brush', 'eraser'):
            self.stroke(self.last, p)
            self.last = p
        elif self.tool in ('line', 'rect', 'ellipse'):
            self.preview = Image.new('RGBA', self.canvas.size, (0, 0, 0, 0))
            self.shape(ImageDraw.Draw(self.preview), self.start, p)

    def shape(self, d, a, b):
        col = self.draw_col + (255,)
        x0, y0 = min(a[0], b[0]), min(a[1], b[1])
        x1, y1 = max(a[0], b[0]), max(a[1], b[1])
        if self.tool == 'line':
            d.line([a, b], fill=col, width=self.width)
        elif self.tool == 'rect':
            d.rectangle((x0, y0, x1, y1), outline=col, width=self.width)
        elif self.tool == 'ellipse':
            d.ellipse((x0, y0, x1, y1), outline=col, width=self.width)

    def mouse(self, kind, x, y, button=1, clicks=1):
        if kind == 'up' and self.start is not None:
            if self.tool in ('line', 'rect', 'ellipse'):
                self.shape(ImageDraw.Draw(self.canvas), self.start, self._pt(x, y))
                self.dirty_doc()
            self.start = None
            self.preview = None
        super().mouse(kind, x, y, button, clicks)

    def on_command(self, cid, data):
        if isinstance(cid, tuple):
            if cid[0] == 'tool':
                self.tool = cid[1]
            elif cid[0] == 'size':
                self.width = cid[1]
            elif cid[0] == 'color':
                self.color = PALETTE[cid[1]]
        elif cid == 'undo' and self.undo:
            self.canvas = self.undo.pop()
        elif cid == 'new':
            self.push_undo()
            self.canvas = Image.new('RGBA', self.canvas.size, (255, 255, 255, 255))
            self.path = None
            self.modified = False
            self.update_title()
        elif cid == 'save':
            self.save()

    def save(self):
        def done(p):
            if not p.lower().endswith('.png'):
                p += '.png'
            try:
                self.canvas.convert('RGB').save(p)
                self.path = p
                self.modified = False
                self.update_title()
                self.shell.notify('Paint', 'Saved %s' % os.path.basename(p), 'image', 3)
            except OSError as e:
                message(self.shell, 'Paint', str(e), 'error', parent=self.win)
        name = os.path.basename(self.path) if self.path else 'Untitled.png'
        self.shell.open_window(FileDialog(self.shell, 'save', start=os.path.join(theme.HOME, 'Pictures'),
                                          filename=name, callback=done), parent=self.win)

    def on_key(self, ev):
        if ev.get('ctrl') and ev['key'] == 'z':
            self.on_command('undo', None)
            return True
        if ev.get('ctrl') and ev['key'] == 's':
            self.save()
            return True
        return False


# ---------------------------------------------------------------------------
class Personalize(App):
    title = 'Personalization'
    icon = 'personalize'
    size = (720, 560)
    single_instance = True

    def __init__(self, shell, **kw):
        super().__init__(shell, **kw)
        self.saved = dict(shell.settings)
        self._thumbs = {}

    def thumb(self, name):
        if name not in self._thumbs:
            self._thumbs[name] = wallpaper.render(name, 136, 77).convert('RGBA')
        return self._thumbs[name]

    def draw(self, img, ui):
        w, h = img.size
        s = self.shell.settings
        ui.command_bar((0, 0, w, 32))
        ui.text((12, 16), 'Control Panel  ▸  Appearance and Personalization  ▸  Personalization', 12,
                color=(30, 50, 80), anchor='lm')
        ui.text((24, 46), 'Change the color of your window borders, Start menu, and taskbar', 16,
                color=(0, 51, 153), kind='light')
        # colour swatches
        y = 84
        sw = 50
        per = min(len(theme.GLASS_COLORS), (w - 48) // (sw + 8))
        for i, (name, rgb) in enumerate(theme.GLASS_COLORS):
            cx = 24 + (i % per) * (sw + 8)
            cy = y + (i // per) * (sw + 26)
            r = (cx, cy, cx + sw, cy + sw)
            sel = s['glass_color'] == name
            hot = ui.hot(r)
            if sel or hot:
                gfx.round_rect(img, (r[0] - 4, r[1] - 4, r[2] + 4, r[3] + 4), 5,
                               fill=(220, 235, 252, 255) if sel else (240, 246, 252, 255),
                               outline=(60, 127, 177, 255) if sel else (170, 200, 230, 255))
            face = gfx.vgradient(sw, sw, ((0, gfx.mix(rgb, (255, 255, 255), 0.45)), (0.5, rgb),
                                          (0.51, gfx.mix(rgb, (0, 0, 0), 0.15)), (1, gfx.mix(rgb, (255, 255, 255), 0.2))))
            gfx.round_rect(img, r, 4, fill=face, outline=(60, 60, 70, 255))
            gfx.round_rect(img, (r[0] + 1, r[1] + 1, r[2] - 1, r[3] - 1), 3, outline=(255, 255, 255, 120))
            ui.text(((r[0] + r[2]) // 2, r[3] + 10), name, 10, color=(60, 70, 80), anchor='mm')
            ui.region(r, ('color', name))
        rows = (len(theme.GLASS_COLORS) + per - 1) // per
        y += rows * (sw + 26) + 6
        aero = s.get('aero', True)
        ui.radio((24, y - 4), 'Neptune Aero', ('look', 'aero'), aero, data='aero')
        ui.radio((150, y - 4), 'Neptune Basic (faster, for slower computers)', ('look', 'basic'), not aero, data='basic')
        if aero:
            ui.checkbox((24, y + 18), 'Enable transparency', 'transparency', s['transparency'])
            ui.text((24, y + 50), 'Color intensity:', 12, anchor='lm')
            ui.slider((130, y + 42, 380, y + 58), 'intensity', (s['intensity'] - 0.1) / 0.8)
        else:
            ui.wrap((24, y + 20), 'No glass, blur or shadows: flat window frames that are quick to draw.', 380, 11,
                    color=theme.MUTED)
        # live glass preview
        pv = (420, y - 6, w - 24, y + 56)
        pw, ph = pv[2] - pv[0], pv[3] - pv[1]
        bgp = self.shell.wall().crop((500, 200, 500 + pw, 200 + ph))
        img.alpha_composite(bgp, (pv[0], pv[1]))
        gfx.glass_panel(img, pv, 6, tuple(s['glass_rgb']), s['intensity'], transparency=s['transparency'])
        gfx.glow_text(img, (pv[0] + 12, pv[1] + 16), 'Preview', gfx.font(12), anchor='lm')
        y += 78
        ui.hline(24, w - 24, y, (220, 228, 235))
        y += 10
        ui.text((24, y), 'Desktop Background', 13, True, color=(30, 60, 110))
        y += 24
        r = (24, y, 24 + 124, y + 70)
        img.alpha_composite(self.shell.wall().resize((124, 70)), (r[0], r[1]))
        ui.rect(r, outline=(90, 100, 110))
        ui.wrap((164, y + 2), 'Neptune wallpapers, original Nostalgia designs, solid colors, gradients, patterns, '
                'your own text or your own picture.', w - 200, 12, color=(60, 70, 80))
        ui.button((164, y + 42, 384, y + 68), 'Change desktop background...', 'background', icon='pictures')
        y += 92
        ui.checkbox((24, y), 'Window animations', 'animations', s['animations'])
        ui.checkbox((200, y), 'Sounds', 'sounds', s['sounds'])
        ui.checkbox((320, y), 'Clock gadget', 'show_gadget', s['show_gadget'])
        ui.checkbox((460, y), 'Boot screen', 'boot_screen', s['boot_screen'])
        ui.rect((0, h - 44, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - 44, (223, 223, 223))
        ui.button((w - 196, h - 34, w - 104, h - 10), 'Save changes', 'save', default=True)
        ui.button((w - 96, h - 34, w - 12, h - 10), 'Cancel', 'cancel')

    def on_command(self, cid, data):
        s = self.shell.settings
        if isinstance(cid, tuple) and cid[0] == 'color':
            s['glass_color'] = cid[1]
            s['glass_rgb'] = list(dict(theme.GLASS_COLORS)[cid[1]])
        elif isinstance(cid, tuple) and cid[0] == 'look':
            s['aero'] = cid[1] == 'aero'
            s['animations'] = s['aero']          # Basic is for slow machines: animations off too
        elif cid == 'background':
            self.shell.launch('background')
            return
        elif cid in ('transparency', 'animations', 'sounds', 'show_gadget', 'boot_screen'):
            s[cid] = bool(data)
        elif cid == 'save':
            s.save()
            self.saved = dict(s)
            self.close()
        elif cid == 'cancel':
            self.close()
            return
        self.shell.apply_settings()

    def on_slider(self, cid, value):
        if cid == 'intensity':
            self.shell.settings['intensity'] = round(0.1 + value * 0.8, 3)
            self.shell.apply_settings()

    MINE = ('glass_color', 'glass_rgb', 'intensity', 'transparency', 'aero', 'animations', 'sounds', 'show_gadget',
            'boot_screen')

    def on_close(self):
        # undo only what this window changes (the Desktop Background window saves its own)
        s = self.shell.settings
        if any(s.get(k) != self.saved.get(k) for k in self.MINE):
            for k in self.MINE:
                s[k] = self.saved.get(k, s.get(k))
            self.shell.apply_settings()
        return True
