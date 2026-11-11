"""The CEAP look, shared by CEAP apps that aren't file explorers (Control
Panel, CEAP:Web): round nav buttons, an address bar and search on a glass
strip, a tree on the left, the Exploring Space in the middle, Overview and
Metadata on the right, and an optional Code.Exe.Explore strip at the bottom.
"""
from PIL import Image, ImageDraw

from .. import gfx, theme
from ..app import App

TOOL_H, TREE_W, SIDE_W = 40, 210, 250


class CeapFrame(App):
    icon = 'ceap'
    bg = theme.CLIENT_BG
    tree_w, side_w, bottom_h = TREE_W, SIDE_W, 0

    def geometry(self):
        w, h = self.client_size
        b = h - self.bottom_h
        return {'tree': (0, TOOL_H, self.tree_w, b), 'space': (self.tree_w, TOOL_H, w - self.side_w, b),
                'side': (w - self.side_w, TOOL_H, w, b), 'bottom': (0, b, w, h)}

    def nav_button(self, img, ui, cx, cy, cid, glyph, enabled):
        r = 13
        box = (cx - r, cy - r, cx + r, cy + r)
        hot = ui.hot(box) and enabled
        top, bot = (((120, 190, 250), (20, 80, 170)) if hot else ((90, 160, 230), (20, 70, 150))) if enabled else \
            ((215, 220, 228), (175, 180, 190))
        m = Image.new('L', (2 * r, 2 * r), 0)
        ImageDraw.Draw(m).ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
        gfx.paste_masked(img, gfx.vgradient(2 * r, 2 * r, ((0, top), (1, bot))), m, (cx - r, cy - r))
        ui.d.ellipse(box, outline=(30, 60, 110, 255) if enabled else (150, 155, 165, 255))
        ui.text((cx, cy), glyph, 13, bold=True, color=(255, 255, 255), anchor='mm')
        if enabled:
            ui.region(box, cid)

    def draw_toolbar(self, img, ui, w, navs, addr, search, search_hint='Search'):
        tint = self.shell.settings.tint
        img.alpha_composite(gfx.vgradient(w, TOOL_H, ((0, gfx.mix(tint, (255, 255, 255), 0.85)),
                                                      (1, gfx.mix(tint, (255, 255, 255), 0.66)))), (0, 0))
        x = 20
        for cid, glyph, en in navs:
            self.nav_button(img, ui, x, 20, cid, glyph, en)
            x += 32
        if isinstance(addr, dict):
            ui.textbox((x, 8, w - 250, 32), 'addr', addr)
        else:
            ui.rect((x, 8, w - 250, 32), fill=(255, 255, 255), outline=(171, 173, 179))
            ui.text((x + 8, 20), addr, 12, anchor='lm')
        ui.textbox((w - 242, 8, w - 40, 32), 'search', search, placeholder=search_hint)
        ui.icon('ceap', (w - 32, 12), 18)

    def pane(self, img, ui, box, title, right=''):
        x0, y0, x1, y1 = box
        ui.text((x0 + 10, y0 + 12), title, 11, bold=True, color=(30, 57, 91), anchor='lm')
        if right:
            ui.text((x1 - 10, y0 + 12), right, 10, color=theme.MUTED, anchor='rm')

    def side_bg(self, img, ui, box):
        x0, y0, x1, y1 = box
        img.alpha_composite(gfx.vgradient(x1 - x0, y1 - y0, ((0, (250, 252, 255)), (1, (238, 243, 249)))), (x0, y0))
        ui.d.line([(x0, y0), (x0, y1)], fill=(205, 214, 226, 255))

    def tree_bg(self, img, ui, box, title):
        x0, y0, x1, y1 = box
        img.alpha_composite(gfx.vgradient(x1 - x0, y1 - y0, ((0, (246, 249, 253)), (1, (232, 239, 247)))), (x0, y0))
        ui.d.line([(x1 - 1, y0), (x1 - 1, y1)], fill=(205, 214, 226, 255))
        ui.text((x0 + 10, y0 + 12), title, 11, bold=True, color=(30, 57, 91), anchor='lm')
