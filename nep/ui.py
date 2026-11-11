"""Immediate-mode widgets for window contents, drawn in the Windows 7 Aero
style: glossy grey buttons that light up blue, soft-blue selection, etc.

An app's draw() calls ui.button(...), ui.textbox(...) ...; each call draws the
widget and registers a hit region. Mouse/keyboard routing back to the app is
done by App (apps.py) using those regions.
"""
import time

from PIL import ImageDraw

from . import gfx, icons, theme

F = lambda size=12, bold=False: gfx.font(size, bold)

BTN_NORMAL = (((0, (242, 242, 242)), (0.5, (235, 235, 235)), (0.51, (221, 221, 221)), (1, (207, 207, 207))), (112, 112, 112))
BTN_HOVER = (((0, (234, 246, 253)), (0.5, (217, 240, 252)), (0.51, (190, 230, 253)), (1, (167, 217, 245))), (60, 127, 177))
BTN_PRESS = (((0, (229, 244, 252)), (0.5, (196, 229, 246)), (0.51, (152, 209, 239)), (1, (104, 179, 219))), (44, 98, 139))
BTN_DISABLED = (((0, (244, 244, 244)), (1, (244, 244, 244))), (173, 178, 181))


class UI:
    def __init__(self):
        self.mouse = (-1, -1)
        self.regions = []
        self.down = None          # (cid, kind, data) pressed
        self.focus = None         # cid of focused textbox
        self.kfocus = None        # cid of the control Tab moved to (buttons, textboxes)
        self.caret_on = True
        self.img = None
        self.d = None
        self.textboxes = []

    # -- region bookkeeping ---------------------------------------------------
    def begin(self, img):
        self.img = img
        self.d = ImageDraw.Draw(img)
        self.regions = []
        self.textboxes = []

    def region(self, rect, cid, kind='button', data=None):
        self.regions.append((tuple(int(v) for v in rect), cid, kind, data))

    def hit(self, x, y):
        for rect, cid, kind, data in reversed(self.regions):
            if gfx.in_rect(x, y, rect):
                return (cid, kind, data, rect)
        return None

    def hot(self, rect):
        return gfx.in_rect(self.mouse[0], self.mouse[1], rect)

    def pressed(self, cid):
        return self.down is not None and self.down[0] == cid

    # -- drawing helpers --------------------------------------------------------
    def text(self, xy, s, size=12, bold=False, color=theme.CLIENT_TEXT, anchor='la', kind='sans'):
        self.d.text((int(xy[0]), int(xy[1])), s, font=gfx.font_for(s, size, bold, kind), fill=gfx.rgba(color),
                    anchor=anchor)

    def wrap(self, xy, s, width, size=12, color=theme.CLIENT_TEXT, bold=False, line_h=None, kind='sans'):
        """Draw word-wrapped text; returns the y after the last line."""
        fnt = gfx.font(size, bold, kind)
        line_h = line_h or int(size * 1.45)
        x, y = xy
        for para in s.split('\n'):
            words = para.split(' ')
            line = ''
            for w in words:
                t = (line + ' ' + w) if line else w
                if gfx.text_width(fnt, t) > width and line:
                    self.d.text((x, y), line, font=fnt, fill=gfx.rgba(color))
                    y += line_h
                    line = w
                else:
                    line = t
            self.d.text((x, y), line, font=fnt, fill=gfx.rgba(color))
            y += line_h
        return y

    def rect(self, box, fill=None, outline=None):
        self.d.rectangle([int(v) for v in (box[0], box[1], box[2] - 1, box[3] - 1)],
                         fill=gfx.rgba(fill) if fill else None,
                         outline=gfx.rgba(outline) if outline else None)

    def hline(self, x0, x1, y, color=(220, 225, 230)):
        self.d.line([(x0, y), (x1, y)], fill=gfx.rgba(color))

    def icon(self, name, xy, size=32):
        self.img.alpha_composite(icons.get(name, size), (int(xy[0]), int(xy[1])))

    def panel_gradient(self, box, top=(250, 252, 254), bottom=(232, 238, 245)):
        x0, y0, x1, y1 = [int(v) for v in box]
        self.img.alpha_composite(gfx.vgradient(x1 - x0, y1 - y0, ((0, top), (1, bottom))), (x0, y0))

    # -- widgets ----------------------------------------------------------------
    def _btn_face(self, box, style, r=3):
        stops, border = style
        x0, y0, x1, y1 = [int(v) for v in box]
        w, h = x1 - x0, y1 - y0
        gfx.round_rect(self.img, box, r, fill=gfx.vgradient(w, h, stops))
        gfx.round_rect(self.img, box, r, outline=border + (255,))
        if w > 4 and h > 4:
            gfx.round_rect(self.img, (x0 + 1, y0 + 1, x1 - 1, y1 - 1), max(0, r - 1), outline=(255, 255, 255, 150))

    def button(self, box, label, cid, default=False, enabled=True, icon=None, size=12, data=None):
        box = tuple(int(v) for v in box)
        if not enabled:
            style = BTN_DISABLED
        elif self.pressed(cid) and self.hot(box):
            style = BTN_PRESS
        elif self.hot(box):
            style = BTN_HOVER
        else:
            style = BTN_NORMAL
        self._btn_face(box, style)
        if default and enabled and style is BTN_NORMAL:
            gfx.round_rect(self.img, box, 3, outline=(60, 127, 177, 255))
            gfx.round_rect(self.img, (box[0] + 1, box[1] + 1, box[2] - 1, box[3] - 1), 2, outline=(140, 210, 245, 255))
        cx = (box[0] + box[2]) // 2
        cy = (box[1] + box[3]) // 2
        col = (20, 20, 20) if enabled else (160, 160, 160)
        if icon:
            isz = min(24, box[3] - box[1] - 6)
            if label:
                tw = gfx.text_width(F(size), label) + isz + 6
                ix = cx - tw // 2
                self.icon(icon, (ix, cy - isz // 2), isz)
                self.text((ix + isz + 6, cy), label, size, color=col, anchor='lm')
            else:
                self.icon(icon, (cx - isz // 2, cy - isz // 2), isz)
        else:
            self.text((cx, cy), label, size, color=col, anchor='mm')
        if enabled:
            self.region(box, cid, 'button', data)

    def toolbutton(self, box, label, cid, icon=None, toggled=False, size=12, data=None, enabled=True, dark=False):
        """Flat until hovered (Explorer command bar style)."""
        box = tuple(int(v) for v in box)
        hot = self.hot(box) and enabled
        if toggled or (hot and self.pressed(cid)):
            self._btn_face(box, BTN_PRESS)
        elif hot:
            self._btn_face(box, BTN_HOVER)
        cy = (box[1] + box[3]) // 2
        x = box[0] + 6
        col = (150, 150, 150) if not enabled else ((255, 255, 255) if dark and not hot else (20, 30, 40))
        if icon:
            isz = min(20, box[3] - box[1] - 4)
            self.icon(icon, (x if label else (box[0] + box[2] - isz) // 2, cy - isz // 2), isz)
            x += isz + 5
        if label:
            if icon:
                self.text((x, cy), label, size, color=col, anchor='lm')
            else:
                self.text(((box[0] + box[2]) // 2, cy), label, size, color=col, anchor='mm')
        if enabled:
            self.region(box, cid, 'button', data)

    def textbox(self, box, cid, state, placeholder='', size=12, mono=False, password=False):
        """Single-line text field. state: dict(text=..., cur=int)."""
        box = tuple(int(v) for v in box)
        focused = self.focus == cid
        hot = self.hot(box)
        x0, y0, x1, y1 = box
        self.rect(box, fill=(255, 255, 255))
        border = (61, 123, 173) if focused else ((126, 180, 234) if hot else (171, 173, 179))
        self.rect(box, outline=border)
        self.hline(x0 + 1, x1 - 2, y0 + 1, (226, 227, 234) if not focused else (180, 210, 235))
        fnt = gfx.font(size, False, 'mono' if mono else 'sans')
        txt = state.get('text', '')
        shown = ('•' * len(txt)) if password else txt
        cur = min(state.get('cur', len(txt)), len(txt))
        # scroll so the caret stays visible
        avail = x1 - x0 - 10
        off = 0
        while gfx.text_width(fnt, shown[off:cur]) > avail and off < cur:
            off += 1
        vis = shown[off:]
        cy = (y0 + y1) // 2
        if not txt and placeholder and not focused:
            self.d.text((x0 + 5, cy), placeholder, font=fnt, fill=(140, 145, 150, 255), anchor='lm')
        else:
            self.d.text((x0 + 5, cy), vis, font=fnt, fill=(0, 0, 0, 255), anchor='lm')
        if focused and self.caret_on:
            cx = x0 + 5 + gfx.text_width(fnt, shown[off:cur])
            self.d.line([(cx, y0 + 4), (cx, y1 - 5)], fill=(0, 0, 0, 255))
        self.region(box, cid, 'textbox', state)
        self.textboxes.append(cid)

    def checkbox(self, xy, label, cid, checked, size=12):
        x, y = int(xy[0]), int(xy[1])
        tw = gfx.text_width(F(size), label)
        box = (x, y, x + 18 + tw + 4, y + 16)
        hot = self.hot(box)
        b = (x, y + 1, x + 13, y + 14)
        self.img.alpha_composite(gfx.vgradient(13, 13, ((0, (220, 222, 226) if not hot else (200, 230, 250)), (1, (250, 250, 252)))), (x, y + 1))
        self.rect(b, outline=(51, 51, 51) if not hot else (60, 127, 177))
        if checked:
            self.d.line([(x + 3, y + 7), (x + 5, y + 10), (x + 10, y + 4)], fill=(30, 60, 110, 255), width=2)
        self.text((x + 19, y + 7), label, size, anchor='lm')
        self.region(box, cid, 'button', not checked)

    def radio(self, xy, label, cid, on, size=12, data=None):
        x, y = int(xy[0]), int(xy[1])
        tw = gfx.text_width(F(size), label)
        box = (x, y, x + 18 + tw + 4, y + 16)
        hot = self.hot(box)
        self.d.ellipse((x, y + 1, x + 13, y + 14), fill=(245, 246, 248, 255),
                       outline=(60, 127, 177, 255) if hot else (51, 51, 51, 255))
        if on:
            self.d.ellipse((x + 3, y + 4, x + 10, y + 11), fill=(40, 110, 190, 255))
        self.text((x + 19, y + 7), label, size, anchor='lm')
        self.region(box, cid, 'button', data)

    def slider(self, box, cid, value):
        box = tuple(int(v) for v in box)
        x0, y0, x1, y1 = box
        cy = (y0 + y1) // 2
        self.rect((x0, cy - 2, x1, cy + 2), fill=(231, 234, 234), outline=(176, 176, 176))
        tx = int(x0 + (x1 - x0) * gfx.clamp(value, 0, 1))
        style = BTN_PRESS if self.pressed(cid) else (BTN_HOVER if self.hot(box) else BTN_NORMAL)
        self._btn_face((tx - 5, y0, tx + 6, y1), style, r=2)
        self.region(box, cid, 'slider', None)

    def listrow(self, box, cid, selected=False, data=None, kind='row'):
        """Win7 selection highlight; returns True if hot."""
        box = tuple(int(v) for v in box)
        hot = self.hot(box)
        if selected or hot:
            top, bot, border = ((220, 235, 252), (193, 219, 252), (125, 162, 206)) if selected else \
                               ((250, 251, 253), (235, 243, 253), (184, 214, 251))
            x0, y0, x1, y1 = box
            gfx.round_rect(self.img, box, 3, fill=gfx.vgradient(x1 - x0, y1 - y0, ((0, top), (1, bot))))
            gfx.round_rect(self.img, box, 3, outline=border + (255,))
        self.region(box, cid, kind, data)
        return hot

    def link(self, xy, label, cid, size=12, data=None):
        fnt = F(size)
        x, y = int(xy[0]), int(xy[1])
        w = gfx.text_width(fnt, label)
        box = (x, y, x + w, y + size + 4)
        hot = self.hot(box)
        col = (51, 153, 255) if hot else theme.LINK
        self.text((x, y), label, size, color=col)
        if hot:
            self.hline(x, x + w, y + size + 2, col)
        self.region(box, cid, 'button', data)

    def scrollbar(self, box, cid, pos, page, total):
        """Vertical scrollbar. pos/page/total in rows. Registers 'scroll' region."""
        box = tuple(int(v) for v in box)
        x0, y0, x1, y1 = box
        self.img.alpha_composite(gfx.hgradient(x1 - x0, y1 - y0, ((0, (232, 232, 236)), (1, (245, 245, 247)))), (x0, y0))
        if total <= page:
            return
        h = y1 - y0
        th = max(24, int(h * page / total))
        ty = y0 + int((h - th) * pos / max(1, total - page))
        tb = (x0 + 2, ty, x1 - 1, ty + th)
        style = BTN_HOVER if self.hot(tb) else BTN_NORMAL
        stops, border = style
        self.img.alpha_composite(gfx.hgradient(tb[2] - tb[0], th, stops), (tb[0], tb[1]))
        gfx.round_rect(self.img, tb, 2, outline=border + (255,))
        self.region(box, cid, 'scroll', (pos, page, total, y0, y1))

    def groupbox(self, box, title=''):
        x0, y0, x1, y1 = [int(v) for v in box]
        gfx.round_rect(self.img, (x0, y0 + 7, x1, y1), 3, outline=(213, 223, 229, 255))
        if title:
            fnt = F(12)
            w = gfx.text_width(fnt, title)
            self.rect((x0 + 8, y0, x0 + 14 + w, y0 + 15), fill=self.img.getpixel((x0 + 2, y0 + 12))[:3])
            self.text((x0 + 11, y0), title, 12, color=(0, 51, 153))

    def command_bar(self, box):
        """Explorer-style toolbar strip (light blue gradient)."""
        x0, y0, x1, y1 = [int(v) for v in box]
        self.img.alpha_composite(gfx.vgradient(x1 - x0, y1 - y0, ((0, (245, 250, 254)), (0.5, (231, 241, 252)),
                                                                  (0.51, (220, 233, 248)), (1, (214, 228, 245)))), (x0, y0))
        self.hline(x0, x1, y1 - 1, (160, 175, 195))
