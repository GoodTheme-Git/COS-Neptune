"""Notepad and Terminal."""
import errno
import fcntl
import os
import pty
import re
import select
import signal
import struct
import termios

from PIL import ImageDraw

from .. import gfx, theme
from ..app import App
from .dialogs import FileDialog, message


class MenuStrip:
    """Classic menu strip inside a window's client area."""
    H = 22

    def __init__(self, app, menus):
        self.app = app
        self.menus = menus      # [(label, items_fn)]

    def draw(self, img, ui):
        w = img.size[0]
        img.alpha_composite(gfx.vgradient(w, self.H, ((0, (252, 253, 255)), (1, (230, 237, 246)))), (0, 0))
        ui.hline(0, w, self.H - 1, (182, 188, 204))
        x = 4
        fnt = gfx.font(12)
        for label, _fn in self.menus:
            tw = gfx.text_width(fnt, label) + 16
            r = (x, 1, x + tw, self.H - 2)
            if ui.hot(r):
                gfx.round_rect(img, r, 3, fill=gfx.vgradient(tw, r[3] - r[1], ((0, (250, 251, 253)), (1, (230, 240, 252)))),
                               outline=(168, 202, 240, 255))
            ui.text((x + 8, self.H // 2), label, 12, anchor='lm')
            ui.region(r, ('menu', label))
            x += tw

    def open(self, label, rect):
        for l, fn in self.menus:
            if l == label:
                cx, cy, _, _ = self.app.win.client_rect()
                self.app.shell.popup(fn(), cx + rect[0], cy + rect[3] + 1, min_w=160)


# ---------------------------------------------------------------------------
class Notepad(App):
    title = 'Untitled - Notepad'
    icon = 'notepad'
    size = (640, 460)

    def __init__(self, shell, path=None, **kw):
        super().__init__(shell, **kw)
        self.lines = ['']
        self.row = self.col = 0
        self.top = 0
        self.left = 0
        self.path = None
        self.modified = False
        self.wrap = False
        self.font_size = 13
        self.anchor = None      # selection anchor (row, col)
        self.menu = MenuStrip(self, [('File', self.file_menu), ('Edit', self.edit_menu),
                                     ('Format', self.format_menu), ('Help', self.help_menu)])
        if path:
            self.load(path)
        self.update_title()

    # -- file ops ------------------------------------------------------------------
    def load(self, path):
        try:
            with open(path, 'r', errors='replace') as f:
                data = f.read(4 * 1024 * 1024)
        except OSError as e:
            message(self.shell, 'Notepad', 'Cannot open %s\n%s' % (path, e.strerror), 'error')
            return
        self.lines = data.replace('\r\n', '\n').replace('\t', '    ').split('\n') or ['']
        self.path = path
        self.row = self.col = self.top = 0
        self.modified = False
        self.update_title()

    def save(self, then=None):
        if not self.path or not os.path.realpath(self.path).startswith(os.path.realpath(theme.HOME)):
            self.save_as(then)
            return
        try:
            with open(self.path, 'w') as f:
                f.write('\n'.join(self.lines))
            self.modified = False
            self.update_title()
            if then:
                then()
        except OSError as e:
            message(self.shell, 'Notepad', 'Cannot save %s\n%s' % (self.path, e.strerror), 'error', parent=self.win)

    def save_as(self, then=None):
        def done(p):
            self.path = p
            self.save(then)
        name = os.path.basename(self.path) if self.path else 'Untitled.txt'
        self.shell.open_window(FileDialog(self.shell, 'save', filename=name, callback=done), parent=self.win)

    def open_dialog(self):
        def done(p):
            self.load(p)
            self.invalidate()
        self.shell.open_window(FileDialog(self.shell, 'open', callback=done), parent=self.win)

    def update_title(self):
        name = os.path.basename(self.path) if self.path else 'Untitled'
        self.set_title('%s%s - Notepad' % ('*' if self.modified else '', name))

    def on_close(self):
        if not self.modified:
            return True

        def answer(r):
            if r == 'Save':
                self.save(then=lambda: self.shell.close_window(self.win, force=True))
            elif r == "Don't Save":
                self.shell.close_window(self.win, force=True)
        message(self.shell, 'Notepad', 'Do you want to save changes to %s?' %
                (os.path.basename(self.path) if self.path else 'Untitled'), 'warning',
                ('Save', "Don't Save", 'Cancel'), answer, parent=self.win)
        return False

    # -- menus ----------------------------------------------------------------------
    def file_menu(self):
        return [{'label': 'New', 'shortcut': 'Ctrl+N', 'action': lambda: self.shell.launch('notepad')},
                {'label': 'Open...', 'shortcut': 'Ctrl+O', 'action': self.open_dialog},
                {'label': 'Save', 'shortcut': 'Ctrl+S', 'action': self.save},
                {'label': 'Save As...', 'action': self.save_as},
                {'sep': True},
                {'label': 'Exit', 'action': self.close}]

    def edit_menu(self):
        return [{'label': 'Select All', 'shortcut': 'Ctrl+A', 'action': self.select_all},
                {'label': 'Copy', 'shortcut': 'Ctrl+C', 'action': self.copy},
                {'label': 'Cut', 'shortcut': 'Ctrl+X', 'action': self.cut},
                {'label': 'Paste', 'shortcut': 'Ctrl+V', 'action': self.paste},
                {'sep': True},
                {'label': 'Time/Date', 'shortcut': 'F5', 'action': self.insert_time}]

    def format_menu(self):
        return [{'label': 'Word Wrap', 'checked': self.wrap, 'action': self.toggle_wrap},
                {'label': 'Larger Text', 'action': lambda: self.set_font(self.font_size + 1)},
                {'label': 'Smaller Text', 'action': lambda: self.set_font(self.font_size - 1)}]

    def help_menu(self):
        return [{'label': 'Neptune Help', 'action': lambda: self.shell.launch('help')},
                {'label': 'About Notepad', 'action': lambda: message(self.shell, 'About Notepad',
                                                                     'Notepad for COS Neptune.', 'info', parent=self.win)}]

    def toggle_wrap(self):
        self.wrap = not self.wrap
        self.invalidate()

    def set_font(self, s):
        self.font_size = int(gfx.clamp(s, 9, 28))
        self.invalidate()

    def insert_time(self):
        import time
        self.insert(time.strftime('%I:%M %p %m/%d/%Y'))

    # -- selection / clipboard ---------------------------------------------------------
    def sel_range(self):
        if self.anchor is None or self.anchor == (self.row, self.col):
            return None
        a, b = self.anchor, (self.row, self.col)
        return (a, b) if a < b else (b, a)

    def sel_text(self):
        r = self.sel_range()
        if not r:
            return ''
        (r0, c0), (r1, c1) = r
        if r0 == r1:
            return self.lines[r0][c0:c1]
        out = [self.lines[r0][c0:]] + self.lines[r0 + 1:r1] + [self.lines[r1][:c1]]
        return '\n'.join(out)

    def delete_sel(self):
        r = self.sel_range()
        if not r:
            self.anchor = None
            return False
        (r0, c0), (r1, c1) = r
        self.lines[r0:r1 + 1] = [self.lines[r0][:c0] + self.lines[r1][c1:]]
        self.row, self.col = r0, c0
        self.anchor = None
        self.modified = True
        return True

    def select_all(self):
        self.anchor = (0, 0)
        self.row = len(self.lines) - 1
        self.col = len(self.lines[-1])
        self.invalidate()

    def copy(self):
        t = self.sel_text()
        if t:
            self.shell.clipboard = t

    def cut(self):
        self.copy()
        self.delete_sel()
        self.update_title()
        self.invalidate()

    def paste(self):
        t = getattr(self.shell, 'clipboard', '')
        if t:
            self.insert(t)

    def insert(self, text):
        self.delete_sel()
        line = self.lines[self.row]
        before, after = line[:self.col], line[self.col:]
        parts = text.split('\n')
        if len(parts) == 1:
            self.lines[self.row] = before + text + after
            self.col += len(text)
        else:
            new = [before + parts[0]] + parts[1:-1] + [parts[-1] + after]
            self.lines[self.row:self.row + 1] = new
            self.row += len(parts) - 1
            self.col = len(parts[-1])
        self.modified = True
        self.update_title()
        self.invalidate()

    # -- geometry ---------------------------------------------------------------------
    def metrics(self):
        fnt = gfx.font(self.font_size, False, 'mono')
        cw = max(1, fnt.getlength('M'))
        lh = int(self.font_size * 1.45)
        return fnt, cw, lh

    def visual_lines(self, cols):
        """[(row, start_col, text)] for drawing with optional wrap."""
        out = []
        for r, ln in enumerate(self.lines):
            if not self.wrap or len(ln) <= cols:
                out.append((r, 0, ln))
            else:
                for s in range(0, max(1, len(ln)), cols):
                    out.append((r, s, ln[s:s + cols]))
        return out

    def draw(self, img, ui):
        w, h = img.size
        self.menu.draw(img, ui)
        fnt, cw, lh = self.metrics()
        top = MenuStrip.H
        status_h = 22
        text_h = h - top - status_h
        rows = max(1, text_h // lh)
        cols = max(10, int((w - 24) / cw))
        self.rows, self.cols = rows, cols
        vis = self.visual_lines(cols)
        # caret's visual line
        cur_v = 0
        for i, (r, s, t) in enumerate(vis):
            if r == self.row and s <= self.col and (self.col < s + cols or not self.wrap or s + cols >= len(self.lines[r])):
                cur_v = i
                if not self.wrap or self.col <= s + len(t):
                    break
        if cur_v < self.top:
            self.top = cur_v
        if cur_v >= self.top + rows:
            self.top = cur_v - rows + 1
        if not self.wrap:
            vis_cols = int((w - 24) / cw)
            if self.col < self.left:
                self.left = self.col
            if self.col >= self.left + vis_cols:
                self.left = self.col - vis_cols + 1
        else:
            self.left = 0
        d = ui.d
        sel = self.sel_range()
        for i in range(rows):
            vi = self.top + i
            if vi >= len(vis):
                break
            r, s, t = vis[vi]
            y = top + 3 + i * lh
            if sel:
                (r0, c0), (r1, c1) = sel
                if r0 <= r <= r1:
                    a = c0 if r == r0 else 0
                    b = c1 if r == r1 else len(self.lines[r]) + 1
                    a, b = max(a, s + self.left), min(b, s + len(t) + 1)
                    if b > a:
                        x0 = 4 + (a - s - self.left) * cw
                        x1 = 4 + (b - s - self.left) * cw
                        d.rectangle((x0, y, x1, y + lh - 1), fill=(51, 153, 255, 255))
            d.text((4, y), t[self.left:], font=fnt, fill=(0, 0, 0, 255))
            if vi == cur_v and self.ui.caret_on and self.win is self.shell.active:
                cx = 4 + (self.col - s - self.left) * cw
                d.line([(cx, y), (cx, y + lh - 2)], fill=(0, 0, 0, 255), width=1)
        ui.region((0, top, w - 17, top + text_h), 'text', 'textarea')
        ui.scrollbar((w - 17, top, w, top + text_h), 'sb', self.top, rows, len(vis))
        ui.rect((0, h - status_h, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - status_h, (215, 215, 215))
        ui.text((w - 140, h - status_h // 2), 'Ln %d, Col %d' % (self.row + 1, self.col + 1), 11, anchor='lm',
                color=(60, 60, 60))
        ui.text((10, h - status_h // 2), 'UTF-8   %d lines' % len(self.lines), 11, anchor='lm', color=(110, 110, 110))

    def cursor_at(self, x, y):
        h = self.ui.hit(x, y)
        return 'ibeam' if h and h[0] == 'text' else 'arrow'

    def pos_from_xy(self, x, y):
        fnt, cw, lh = self.metrics()
        vis = self.visual_lines(getattr(self, 'cols', 80))
        vi = self.top + max(0, int((y - MenuStrip.H - 3) // lh))
        if vi >= len(vis):
            return len(self.lines) - 1, len(self.lines[-1])
        r, s, t = vis[vi]
        c = s + self.left + max(0, int(round((x - 4) / cw)))
        return r, min(c, s + len(t)) if self.wrap else min(c, len(self.lines[r]))

    def on_down(self, cid, kind, data, x, y, button):
        if cid == 'text' and button == 1:
            self.row, self.col = self.pos_from_xy(x, y)
            self.anchor = (self.row, self.col)
            self.selecting = True
        elif isinstance(cid, tuple) and cid[0] == 'menu':
            h = self.ui.hit(x, y)
            self.ui.down = None
            self.menu.open(cid[1], h[3])

    def on_drag(self, cid, x, y):
        if cid == 'text':
            self.row, self.col = self.pos_from_xy(x, y)

    def on_activate(self, cid, data):
        if cid == 'text':
            ln = self.lines[self.row]
            a = b = self.col
            while a > 0 and (ln[a - 1].isalnum() or ln[a - 1] == '_'):
                a -= 1
            while b < len(ln) and (ln[b].isalnum() or ln[b] == '_'):
                b += 1
            self.anchor = (self.row, a)
            self.col = b

    def on_command(self, cid, data):
        if cid == 'sb':
            self.top = data
            fnt, cw, lh = self.metrics()

    def context_menu(self, x, y):
        return self.edit_menu()

    def on_wheel(self, dy, x, y):
        vis = len(self.visual_lines(getattr(self, 'cols', 80)))
        self.top = int(gfx.clamp(self.top - dy * 3, 0, max(0, vis - getattr(self, 'rows', 10))))
        # keep the caret on screen without fighting the wheel
        if self.row < self.top:
            self.row = self.top
            self.col = min(self.col, len(self.lines[self.row]))
        elif self.row >= self.top + getattr(self, 'rows', 10):
            self.row = min(len(self.lines) - 1, self.top + self.rows - 1)
            self.col = min(self.col, len(self.lines[self.row]))

    def on_key(self, ev):
        k = ev['key']
        ctrl = ev.get('ctrl')
        shift = ev.get('shift')
        if ctrl:
            act = {'s': self.save, 'o': self.open_dialog, 'n': lambda: self.shell.launch('notepad'),
                   'a': self.select_all, 'c': self.copy, 'x': self.cut, 'v': self.paste}.get(k)
            if act:
                act()
                return True
        moves = ('left', 'right', 'up', 'down', 'home', 'end', 'pageup', 'pagedown')
        if k in moves:
            if shift and self.anchor is None:
                self.anchor = (self.row, self.col)
            elif not shift:
                self.anchor = None
            if k == 'left':
                if self.col > 0:
                    self.col -= 1
                elif self.row > 0:
                    self.row -= 1
                    self.col = len(self.lines[self.row])
            elif k == 'right':
                if self.col < len(self.lines[self.row]):
                    self.col += 1
                elif self.row < len(self.lines) - 1:
                    self.row += 1
                    self.col = 0
            elif k in ('up', 'pageup'):
                self.row = max(0, self.row - (1 if k == 'up' else getattr(self, 'rows', 10)))
            elif k in ('down', 'pagedown'):
                self.row = min(len(self.lines) - 1, self.row + (1 if k == 'down' else getattr(self, 'rows', 10)))
            elif k == 'home':
                self.col = 0
                if ctrl:
                    self.row = 0
            elif k == 'end':
                if ctrl:
                    self.row = len(self.lines) - 1
                self.col = len(self.lines[self.row])
            self.col = min(self.col, len(self.lines[self.row]))
            return True
        if k == 'f5':
            self.insert_time()
            return True
        if k == 'enter':
            self.insert('\n')
            return True
        if k == 'tab':
            self.insert('    ')
            return True
        if k == 'backspace':
            if not self.delete_sel():
                if self.col > 0:
                    ln = self.lines[self.row]
                    self.lines[self.row] = ln[:self.col - 1] + ln[self.col:]
                    self.col -= 1
                elif self.row > 0:
                    prev = self.lines[self.row - 1]
                    self.lines[self.row - 1] = prev + self.lines[self.row]
                    del self.lines[self.row]
                    self.row -= 1
                    self.col = len(prev)
            self.modified = True
            self.update_title()
            return True
        if k == 'delete':
            if not self.delete_sel():
                ln = self.lines[self.row]
                if self.col < len(ln):
                    self.lines[self.row] = ln[:self.col] + ln[self.col + 1:]
                elif self.row < len(self.lines) - 1:
                    self.lines[self.row] = ln + self.lines[self.row + 1]
                    del self.lines[self.row + 1]
            self.modified = True
            self.update_title()
            return True
        if ev.get('char') and not ctrl:
            self.insert(ev['char'])
            return True
        return False

    def wants_caret(self):
        return True


# ---------------------------------------------------------------------------
ANSI = [(12, 12, 12), (197, 15, 31), (19, 161, 14), (193, 156, 0), (0, 85, 218), (136, 23, 152),
        (58, 150, 221), (204, 204, 204), (118, 118, 118), (231, 72, 86), (22, 198, 12), (249, 241, 165),
        (59, 120, 255), (180, 0, 158), (97, 214, 214), (242, 242, 242)]
CSI_RE = re.compile(r'\x1b\[([?>=]?)([0-9;]*)([@-~])')


def _xterm256(n):
    if n < 16:
        return ANSI[n]
    if n < 232:
        n -= 16
        return tuple((v * 40 + 55) if v else 0 for v in (n // 36, (n // 6) % 6, n % 6))
    g = 8 + (n - 232) * 10
    return (g, g, g)


# VT100 "special graphics" (ESC ( 0): what curses ACS_* line drawing sends
DEC_GRAPHICS = dict(zip('`abcdefghijklmnopqrstuvwxyz{|}~',
                        '◆▒␉␌␍␊°±␤␋┘┐┌└┼⎺⎻─⎼⎽├┤┴┬│≤≥π≠£·'))


class TermScreen:
    """A small VT100/xterm subset: enough for a shell, ls --color, less, top,
    and curses programs that draw boxes with the DEC line-drawing set."""

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.fg, self.bg, self.bold = 7, None, False
        self.buf = [self._blank_line() for _ in range(rows)]
        self.scrollback = []
        self.cx = self.cy = 0
        self.saved = (0, 0)
        self.pending = ''
        self.top, self.bottom = 0, rows - 1
        self.alt = None
        self.cursor_visible = True
        self.g = ['B', 'B']          # G0 / G1 character sets
        self.mouse_mode = 0          # xterm mouse reporting: 0 off, 9 / 1000 / 1002 / 1003
        self.mouse_sgr = False       # 1006: SGR coordinates (no 223-column limit)
        self.shift = 0               # 0 = G0 active, 1 = G1 (after SO)

    def _blank_line(self):
        return [(' ', 7, None, False) for _ in range(self.cols)]

    def resize(self, cols, rows):
        if (cols, rows) == (self.cols, self.rows):
            return
        new = []
        for ln in self.buf:
            ln = ln[:cols] + [(' ', 7, None, False)] * max(0, cols - len(ln))
            new.append(ln)
        while len(new) < rows:
            new.append([(' ', 7, None, False)] * cols)
        while len(new) > rows:
            # drop empty lines below the cursor first, like a real terminal; only scroll
            # the top away when that is the only way to keep the cursor on screen
            if len(new) - 1 > self.cy and not ''.join(c[0] for c in new[-1]).strip():
                new.pop()
            else:
                self.scrollback.append(new.pop(0))
                self.cy = max(0, self.cy - 1)
        self.buf = new
        self.cols, self.rows = cols, rows
        self.top, self.bottom = 0, rows - 1
        self.cx = min(self.cx, cols - 1)
        self.cy = min(self.cy, rows - 1)

    def scroll_up(self):
        line = self.buf.pop(self.top)
        if self.top == 0 and self.alt is None:
            self.scrollback.append(line)
            self.scrollback = self.scrollback[-1000:]
        self.buf.insert(self.bottom, self._blank_line())

    def newline(self):
        if self.cy == self.bottom:
            self.scroll_up()
        else:
            self.cy = min(self.rows - 1, self.cy + 1)

    def put(self, ch):
        if self.cx >= self.cols:
            self.cx = 0
            self.newline()
        if self.g[self.shift] == '0':
            ch = DEC_GRAPHICS.get(ch, ch)
        self.buf[self.cy][self.cx] = (ch, self.fg, self.bg, self.bold)
        self.cx += 1

    def feed(self, data):
        s = self.pending + data
        self.pending = ''
        i = 0
        n = len(s)
        while i < n:
            ch = s[i]
            if ch == '\x1b':
                if i + 1 >= n:
                    self.pending = s[i:]
                    return
                nx = s[i + 1]
                if nx == '[':
                    m = CSI_RE.match(s, i)
                    if not m:
                        if n - i < 32:
                            self.pending = s[i:]
                            return
                        i += 2
                        continue
                    self.csi(m.group(1), m.group(2), m.group(3))
                    i = m.end()
                    continue
                if nx == ']':
                    end = s.find('\x07', i)
                    end2 = s.find('\x1b\\', i)
                    ends = [e for e in (end, end2) if e >= 0]
                    if not ends:
                        self.pending = s[i:] if n - i < 512 else ''
                        return
                    e = min(ends)
                    i = e + (1 if e == end else 2)
                    continue
                if nx in '()':
                    if i + 2 >= n:
                        self.pending = s[i:]
                        return
                    self.g[0 if nx == '(' else 1] = s[i + 2]
                    i += 3
                    continue
                if nx == '7':
                    self.saved = (self.cx, self.cy)
                elif nx == '8':
                    self.cx, self.cy = self.saved
                elif nx == 'M':
                    if self.cy == self.top:
                        self.buf.pop(self.bottom)
                        self.buf.insert(self.top, self._blank_line())
                    else:
                        self.cy = max(0, self.cy - 1)
                elif nx == 'c':
                    self.__init__(self.cols, self.rows)
                i += 2
                continue
            if ch == '\r':
                self.cx = 0
            elif ch == '\n':
                self.newline()
            elif ch == '\b':
                self.cx = max(0, self.cx - 1)
            elif ch == '\t':
                self.cx = min(self.cols - 1, (self.cx // 8 + 1) * 8)
            elif ch == '\x07':
                pass
            elif ch == '\x0e':
                self.shift = 1
            elif ch == '\x0f':
                self.shift = 0
            elif ord(ch) >= 32:
                self.put(ch)
            i += 1

    def csi(self, priv, params, cmd):
        ps = [int(p) if p.isdigit() else 0 for p in params.split(';')] if params else []
        p1 = ps[0] if ps else 0
        if priv == '?':
            if cmd in 'hl':
                on = cmd == 'h'
                for p in ps:
                    if p == 25:
                        self.cursor_visible = on
                    elif p in (9, 1000, 1002, 1003):
                        self.mouse_mode = p if on else (0 if self.mouse_mode == p else self.mouse_mode)
                    elif p == 1006:
                        self.mouse_sgr = on
                    elif p in (1049, 47, 1047):
                        if on and self.alt is None:
                            self.alt = (self.buf, self.cx, self.cy)
                            self.buf = [self._blank_line() for _ in range(self.rows)]
                            self.cx = self.cy = 0
                        elif not on and self.alt is not None:
                            self.buf, self.cx, self.cy = self.alt
                            self.alt = None
            return
        if cmd == 'm':
            if not ps:
                ps = [0]
            j = 0
            while j < len(ps):
                p = ps[j]
                if p == 0:
                    self.fg, self.bg, self.bold = 7, None, False
                elif p == 1:
                    self.bold = True
                elif p == 22:
                    self.bold = False
                elif p == 7:
                    self.fg, self.bg = (self.bg if self.bg is not None else 0), self.fg
                elif 30 <= p <= 37:
                    self.fg = p - 30
                elif p == 39:
                    self.fg = 7
                elif 40 <= p <= 47:
                    self.bg = p - 40
                elif p == 49:
                    self.bg = None
                elif 90 <= p <= 97:
                    self.fg = p - 90 + 8
                elif 100 <= p <= 107:
                    self.bg = p - 100 + 8
                elif p in (38, 48) and j + 2 < len(ps) and ps[j + 1] == 5:
                    col = _xterm256(ps[j + 2])
                    if p == 38:
                        self.fg = col
                    else:
                        self.bg = col
                    j += 2
                elif p in (38, 48) and j + 4 < len(ps) and ps[j + 1] == 2:
                    col = tuple(ps[j + 2:j + 5])
                    if p == 38:
                        self.fg = col
                    else:
                        self.bg = col
                    j += 4
                j += 1
        elif cmd in 'HF' or cmd == 'f':
            r = (ps[0] if len(ps) > 0 and ps[0] else 1) - 1
            c = (ps[1] if len(ps) > 1 and ps[1] else 1) - 1
            self.cy, self.cx = min(self.rows - 1, max(0, r)), min(self.cols - 1, max(0, c))
        elif cmd == 'A':
            self.cy = max(0, self.cy - (p1 or 1))
        elif cmd == 'B':
            self.cy = min(self.rows - 1, self.cy + (p1 or 1))
        elif cmd == 'C':
            self.cx = min(self.cols - 1, self.cx + (p1 or 1))
        elif cmd == 'D':
            self.cx = max(0, self.cx - (p1 or 1))
        elif cmd == 'G':
            self.cx = min(self.cols - 1, max(0, (p1 or 1) - 1))
        elif cmd == 'd':
            self.cy = min(self.rows - 1, max(0, (p1 or 1) - 1))
        elif cmd == 'K':
            ln = self.buf[self.cy]
            rng = {0: range(self.cx, self.cols), 1: range(0, self.cx + 1), 2: range(self.cols)}.get(p1, range(0))
            for x in rng:
                ln[x] = (' ', 7, self.bg, False)
        elif cmd == 'J':
            if p1 == 2 or p1 == 3:
                self.buf = [self._blank_line() for _ in range(self.rows)]
            elif p1 == 0:
                for x in range(self.cx, self.cols):
                    self.buf[self.cy][x] = (' ', 7, None, False)
                for y in range(self.cy + 1, self.rows):
                    self.buf[y] = self._blank_line()
            elif p1 == 1:
                for y in range(0, self.cy):
                    self.buf[y] = self._blank_line()
        elif cmd == 'P':
            ln = self.buf[self.cy]
            k = p1 or 1
            del ln[self.cx:self.cx + k]
            ln.extend([(' ', 7, None, False)] * k)
        elif cmd == '@':
            ln = self.buf[self.cy]
            k = p1 or 1
            for _ in range(k):
                ln.insert(self.cx, (' ', 7, None, False))
            del ln[self.cols:]
        elif cmd == 'X':
            for x in range(self.cx, min(self.cols, self.cx + (p1 or 1))):
                self.buf[self.cy][x] = (' ', 7, None, False)
        elif cmd == 'L':
            for _ in range(p1 or 1):
                self.buf.pop(self.bottom)
                self.buf.insert(self.cy, self._blank_line())
        elif cmd == 'M':
            for _ in range(p1 or 1):
                self.buf.pop(self.cy)
                self.buf.insert(self.bottom, self._blank_line())
        elif cmd == 'r':
            t = (ps[0] if len(ps) > 0 and ps[0] else 1) - 1
            b = (ps[1] if len(ps) > 1 and ps[1] else self.rows) - 1
            self.top, self.bottom = max(0, t), min(self.rows - 1, b)
            self.cx = self.cy = 0
        elif cmd == 's':
            self.saved = (self.cx, self.cy)
        elif cmd == 'u':
            self.cx, self.cy = self.saved


class Terminal(App):
    title = 'Terminal'
    icon = 'terminal'
    size = (700, 440)
    bg = (12, 12, 12, 255)
    dark_client = True
    FONT = 13

    def __init__(self, shell, command=None, cwd=None, **kw):
        super().__init__(shell, **kw)
        fnt = gfx.font(self.FONT, False, 'mono')
        self.cw = fnt.getlength('M')
        self.lh = int(self.FONT * 1.3)
        cols, rows = self._dims(self.size[0] - 2 * 7 - 14, self.size[1] - 30 - 7)
        self.screen = TermScreen(cols, rows)
        self.view = 0
        self.exited = False
        env = dict(os.environ)
        env['TERM'] = 'xterm-256color'
        env['COLORTERM'] = ''
        env['COS_NEPTUNE'] = '1'
        bindir = os.path.join(theme.ROOT, 'bin')          # Neptune's own commands (earthdog)
        if os.path.isdir(bindir) and bindir not in env.get('PATH', '').split(':'):
            env['PATH'] = bindir + ':' + env.get('PATH', '/usr/bin:/bin')
        sh = env.get('SHELL', '/bin/bash')
        argv = [sh] if not command else [sh, '-c', command + '; echo; echo "[process finished - press a key]"; read -n1']
        env['PWD'] = cwd or theme.HOME
        pid, fd = pty.fork()
        if pid == 0:
            try:
                os.chdir(cwd or theme.HOME)
            except OSError:
                pass
            try:
                os.execvpe(argv[0], argv, env)
            finally:
                os._exit(127)
        self.pid, self.fd = pid, fd
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        self._winsz(cols, rows)
        if command:
            if type(self).title == 'Terminal':
                self.title = 'Terminal - %s' % command[:40]

    def _dims(self, w, h):
        return max(20, int(w / self.cw)), max(5, int(h / self.lh))

    def _winsz(self, cols, rows):
        try:
            fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
        except OSError:
            pass

    def on_resize(self):
        w, h = self.client_size
        cols, rows = self._dims(w - 18, h - 6)
        if (cols, rows) != (self.screen.cols, self.screen.rows):
            self.screen.resize(cols, rows)
            self._winsz(cols, rows)

    def on_tick(self, now):
        if self.exited:
            return False
        got = False
        for _ in range(16):
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            except OSError:
                data = b''
            if not data:
                self.exited = True
                self.close()
                return False
            self.screen.feed(data.decode('utf-8', 'replace'))
            got = True
        if got:
            self.view = 0
        return got

    def write(self, s):
        try:
            os.write(self.fd, s.encode())
        except OSError:
            pass

    def on_key(self, ev):
        k = ev['key']
        seq = {'enter': '\r', 'backspace': '\x7f', 'tab': '\t', 'escape': '\x1b', 'up': '\x1b[A',
               'down': '\x1b[B', 'right': '\x1b[C', 'left': '\x1b[D', 'home': '\x1b[H', 'end': '\x1b[F',
               'delete': '\x1b[3~', 'pageup': '\x1b[5~', 'pagedown': '\x1b[6~', 'insert': '\x1b[2~'}.get(k)
        if ev.get('ctrl') and ev.get('shift') and k == 'v':
            self.write(getattr(self.shell, 'clipboard', ''))
            return True
        if ev.get('ctrl') and len(k) == 1 and k.isalpha():
            self.write(chr(ord(k.lower()) - 96))
            return True
        if ev.get('ctrl') and k in ('[', 'space'):
            self.write('\x1b' if k == '[' else '\x00')
            return True
        if seq:
            self.write(seq)
            return True
        if ev.get('char'):
            self.write(('\x1b' if ev.get('alt') else '') + ev['char'])
            return True
        return False

    def on_wheel(self, dy, x, y):
        if self.screen.mouse_mode:
            for _ in range(min(5, abs(dy))):
                self.mouse_report(64 if dy > 0 else 65, x, y, True)
            return
        self.view = int(gfx.clamp(self.view + dy * 3, 0, len(self.screen.scrollback)))

    # -- xterm mouse reporting: programs that ask (cos.py, mc, htop, vim) get the mouse --
    def cell_at(self, x, y):
        return (max(0, min(self.screen.cols - 1, int((x - 4) // self.cw))),
                max(0, min(self.screen.rows - 1, int((y - 3) // self.lh))))

    def mouse_report(self, code, x, y, press):
        col, row = self.cell_at(x, y)
        if self.screen.mouse_sgr:
            self.write('\x1b[<%d;%d;%d%s' % (code, col + 1, row + 1, 'M' if press else 'm'))
        else:
            if not press:
                code = 3                       # X10 has no per-button release
            if col > 222 or row > 222:
                return
            self.write('\x1b[M' + chr(32 + code) + chr(33 + col) + chr(33 + row))

    def mouse(self, kind, x, y, button=1, clicks=1):
        mode = self.screen.mouse_mode
        if not mode or self.view:
            return super().mouse(kind, x, y, button, clicks)
        btn = {1: 0, 2: 1, 3: 2}.get(button, 0)
        if kind == 'down':
            self._held = btn
            self.mouse_report(btn, x, y, True)
        elif kind == 'up':
            self.mouse_report(getattr(self, '_held', btn), x, y, False)
            self._held = None
        elif kind == 'move':
            cell = self.cell_at(x, y)
            if cell == getattr(self, '_last_cell', None):
                return
            self._last_cell = cell
            held = getattr(self, '_held', None)
            if mode == 1003 or (mode == 1002 and held is not None):
                self.mouse_report(32 + (held if held is not None else 3), x, y, True)

    def cursor_at(self, x, y):
        return 'arrow' if self.screen.mouse_mode else 'ibeam'

    def on_close(self):
        try:
            os.kill(self.pid, signal.SIGHUP)
        except OSError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            os.waitpid(self.pid, os.WNOHANG)
        except OSError:
            pass
        return True

    def wants_caret(self):
        return True

    def draw(self, img, ui):
        w, h = img.size
        sc = self.screen
        fnt = gfx.font(self.FONT, False, 'mono')
        fntb = gfx.font(self.FONT, True, 'mono')
        d = ImageDraw.Draw(img)
        lines = sc.buf
        if self.view:
            sb = sc.scrollback
            lines = (sb[len(sb) - self.view:] + sc.buf)[:sc.rows]
        for y, ln in enumerate(lines):
            py = 3 + y * self.lh
            # batch runs of identical attributes
            x = 0
            n = len(ln)
            while x < n:
                ch, fg, bg, bold = ln[x]
                j = x + 1
                while j < n and ln[j][1:] == (fg, bg, bold):
                    j += 1
                run = ''.join(c[0] for c in ln[x:j])
                px = 4 + x * self.cw
                if bg is not None:
                    col = ANSI[bg] if isinstance(bg, int) else bg
                    d.rectangle((px, py, 4 + j * self.cw - 1, py + self.lh - 1), fill=col + (255,))
                if run.strip():
                    if isinstance(fg, int):
                        col = ANSI[fg + 8 if bold and fg < 8 else fg]
                    else:
                        col = fg
                    d.text((px, py), run, font=fntb if bold else fnt, fill=tuple(col) + (255,))
                x = j
        if sc.cursor_visible and not self.view and self.win is self.shell.active and self.ui.caret_on:
            px = 4 + sc.cx * self.cw
            py = 3 + sc.cy * self.lh
            d.rectangle((px, py + self.lh - 3, px + self.cw - 1, py + self.lh - 1), fill=(230, 230, 230, 255))
        if self.view:
            ui.text((w - 20, 6), 'scrollback -%d' % self.view, 11, color=(120, 200, 255), anchor='ra')
        ui.region((0, 0, w, h), 'term')

    def context_menu(self, x, y):
        return [{'label': 'Paste', 'shortcut': 'Ctrl+Shift+V',
                 'action': lambda: self.write(getattr(self.shell, 'clipboard', ''))},
                {'sep': True},
                {'label': 'New Terminal', 'action': lambda: self.shell.launch('terminal')}]
