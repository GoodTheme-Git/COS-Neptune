"""Base class for Neptune applications."""
import time

from . import gfx, theme
from .ui import UI


class App:
    title = 'Application'
    icon = 'file'
    size = (520, 380)
    min_size = (220, 140)
    resizable = True
    bg = theme.CLIENT_BG
    single_instance = False
    show_in_taskbar = True
    dialog = False          # dialogs: no min/max, not resizable
    dark_client = False

    def __init__(self, shell, **kw):
        self.shell = shell
        self.win = None
        self.ui = UI()
        self.kw = kw
        self._wheel_target = None

    # -- helpers ------------------------------------------------------------------
    def invalidate(self):
        if self.win is not None:
            self.win.content_dirty = True
        self.shell.dirty = True

    def close(self):
        if self.win is not None:
            self.shell.close_window(self.win)

    def set_title(self, t):
        self.title = t
        if self.win is not None:
            self.win.frame_dirty = True
        self.shell.dirty = True

    @property
    def client_size(self):
        return self.win.client_size if self.win else self.size

    # -- overridables ----------------------------------------------------------------
    def draw(self, img, ui):
        pass

    def on_command(self, cid, data):
        pass

    def on_activate(self, cid, data):
        """Double click on a region."""
        self.on_command(cid, data)

    def on_down(self, cid, kind, data, x, y, button):
        pass

    def on_drag(self, cid, x, y):
        pass

    def on_slider(self, cid, value):
        pass

    def on_text(self, cid, state):
        """A textbox changed."""
        pass

    def on_key(self, ev):
        return False

    def on_wheel(self, dy, x, y):
        pass

    def on_tick(self, now):
        return False

    def on_close(self):
        return True

    def on_resize(self):
        pass

    def on_focus(self, focused):
        pass

    def context_menu(self, x, y):
        return None

    def wants_caret(self):
        return self.ui.focus is not None

    def cursor_at(self, x, y):
        h = self.ui.hit(x, y)
        if h and h[1] == 'textbox':
            return 'ibeam'
        return 'arrow'

    # -- routing (called by the shell; x, y are client coordinates) ------------------
    def mouse(self, kind, x, y, button=1, clicks=1):
        ui = self.ui
        if kind == 'move':
            old = ui.mouse
            ui.mouse = (x, y)
            if ui.down is not None:
                cid, k, data, rect = ui.down
                if k == 'slider':
                    v = gfx.clamp((x - rect[0]) / max(1, rect[2] - rect[0]), 0, 1)
                    self.on_slider(cid, v)
                    self.invalidate()
                elif k == 'scroll':
                    self._scroll_drag(cid, data, y)
                else:
                    self.on_drag(cid, x, y)
            h0 = ui.hit(*old) if old[0] >= 0 else None
            h1 = ui.hit(x, y)
            if (h0 and h0[:2]) != (h1 and h1[:2]) or ui.down is not None:
                self.invalidate()
            return
        if kind == 'leave':
            if ui.mouse != (-1, -1):
                ui.mouse = (-1, -1)
                self.invalidate()
            return
        if kind == 'down':
            ui.mouse = (x, y)
            h = ui.hit(x, y)
            ui.down = h
            ui.kfocus = h[0] if (h and h[1] == 'textbox') else None
            if h:
                cid, k, data, rect = h
                if k == 'textbox':
                    if ui.focus != cid:
                        ui.focus = cid
                    self._place_caret(data, rect, x)
                elif k == 'slider':
                    v = gfx.clamp((x - rect[0]) / max(1, rect[2] - rect[0]), 0, 1)
                    self.on_slider(cid, v)
                elif k == 'scroll':
                    self._scroll_drag(cid, data, y)
                self.on_down(cid, k, data, x, y, button)
                if clicks >= 2 and k not in ('textbox', 'slider', 'scroll'):
                    self.on_activate(cid, data)
                    ui.down = None
            else:
                self.on_down(None, None, None, x, y, button)
            self.invalidate()
            return
        if kind == 'up':
            h = ui.hit(x, y)
            d = ui.down
            ui.down = None
            if d and h and d[0] == h[0] and d[1] in ('button', 'row', 'item'):
                self.on_command(h[0], h[2])
            self.invalidate()

    def _scroll_drag(self, cid, data, y):
        pos, page, total, y0, y1 = data
        frac = gfx.clamp((y - y0) / max(1, y1 - y0), 0, 1)
        self.on_command(cid, int(frac * max(0, total - page)))

    def _place_caret(self, state, rect, x):
        fnt = gfx.font(12)
        txt = state.get('text', '')
        rel = x - rect[0] - 5
        best = len(txt)
        for i in range(len(txt) + 1):
            if gfx.text_width(fnt, txt[:i]) >= rel:
                best = i
                break
        state['cur'] = best

    # -- keyboard focus: Tab / Shift+Tab walk the controls, Enter / Space press them ----------
    FOCUS_KINDS = ('button', 'textbox')

    def focusables(self):
        seen, out = set(), []
        for rect, cid, kind, data in self.ui.regions:
            if kind in self.FOCUS_KINDS and cid not in seen and rect[2] - rect[0] > 2 and rect[3] - rect[1] > 2:
                seen.add(cid)
                out.append((rect, cid, kind, data))
        # reading order, whatever order things were drawn in: rows top to bottom, then left to right
        out.sort(key=lambda it: (round((it[0][1] + it[0][3]) / 2 / 18), it[0][0]))
        return out

    def focus_step(self, d, start=None):
        items = self.focusables()
        if not items:
            return False
        cur = start if start is not None else self.ui.kfocus
        ids = [cid for _r, cid, _k, _d in items]
        i = ids.index(cur) if cur in ids else (-1 if d > 0 else 0)
        rect, cid, kind, data = items[(i + d) % len(items)]
        self.ui.kfocus = cid
        self.ui.focus = cid if kind == 'textbox' else None
        if kind == 'textbox' and isinstance(data, dict):
            data['cur'] = len(data.get('text', ''))
        self.invalidate()
        return True

    def focus_press(self):
        for rect, cid, kind, data in self.focusables():
            if cid == self.ui.kfocus and kind == 'button':
                self.on_command(cid, data)
                self.invalidate()
                return True
        return False

    def draw_focus(self, img):
        """A dotted ring round the control that has keyboard focus (buttons only;
        a focused textbox already shows its caret)."""
        kf = self.ui.kfocus
        if kf is None or kf == self.ui.focus:
            return
        for rect, cid, kind, data in self.ui.regions:
            if cid == kf and kind == 'button':
                from PIL import ImageDraw
                d = ImageDraw.Draw(img)
                x0, y0, x1, y1 = rect[0] + 2, rect[1] + 2, rect[2] - 3, rect[3] - 3
                for x in range(x0, x1, 2):
                    d.point((x, y0), fill=(20, 20, 20, 255))
                    d.point((x, y1), fill=(20, 20, 20, 255))
                for y in range(y0, y1, 2):
                    d.point((x0, y), fill=(20, 20, 20, 255))
                    d.point((x1, y), fill=(20, 20, 20, 255))
                return

    def key(self, ev):
        ui = self.ui
        if ev['type'] == 'key' and ui.kfocus is not None and ui.focus is None and ev['key'] in ('enter', 'space'):
            if self.focus_press():
                return True
        if ui.focus is not None and ev['type'] == 'key':
            state = None
            for rect, cid, kind, data in ui.regions:
                if cid == ui.focus and kind == 'textbox':
                    state = data
            if state is not None:
                k = ev['key']
                t = state.get('text', '')
                c = min(state.get('cur', len(t)), len(t))
                handled = True
                if ev.get('char') and not ev.get('ctrl'):
                    t = t[:c] + ev['char'] + t[c:]
                    c += 1
                elif k == 'backspace':
                    if c > 0:
                        t = t[:c - 1] + t[c:]
                        c -= 1
                elif k == 'delete':
                    t = t[:c] + t[c + 1:]
                elif k == 'left':
                    c = max(0, c - 1)
                elif k == 'right':
                    c = min(len(t), c + 1)
                elif k == 'home':
                    c = 0
                elif k == 'end':
                    c = len(t)
                elif k == 'enter':
                    self.on_command(ui.focus, 'enter')
                elif k == 'tab':
                    self.focus_step(-1 if ev.get('shift') else 1, start=ui.focus)
                elif k == 'escape':
                    handled = self.on_key(ev)
                else:
                    handled = self.on_key(ev)
                if t != state.get('text', '') or c != state.get('cur'):
                    changed = t != state.get('text', '')
                    state['text'], state['cur'] = t, c
                    if changed:
                        self.on_text(ui.focus, state)
                self.invalidate()
                return handled
        r = self.on_key(ev)
        if not r and ev['type'] == 'key' and ev['key'] == 'tab' and not ev.get('ctrl') and not ev.get('alt'):
            r = self.focus_step(-1 if ev.get('shift') else 1)
        if r:
            self.invalidate()
        return r
