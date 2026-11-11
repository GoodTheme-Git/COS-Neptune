"""The `cos` API of COS v2.4 "Druman", re-implemented for compatibility mode.

A .cosapp sees exactly the names v2.4 gave it (see cosapp_api() in
~/cos/cos.py): Window, ScrollListWindow, Canvas, the dialog windows, cp() and
the CP_* colour pairs, and the small helpers. Drawing goes into a Grid of
character cells instead of a curses screen; host.py turns the grid into
pixels inside an Aero window.

None of ~/cos/cos.py is imported or copied: this is a clean re-implementation
of the documented app-facing surface, so Neptune runs old apps without
needing the old system installed.
"""
import ast
import math
import operator
import os
import shutil
import subprocess
import threading
import time
import types

from .cursesshim import shim as curses, A_BOLD, A_DIM, A_REVERSE

VERSION_24 = '2.4'
CODENAME_24 = 'Druman'

(CP_MENUBAR, CP_MENU_SEL, CP_DESKTOP, CP_WINDOW, CP_WIN_TITLE, CP_STATUS, CP_CURSOR, CP_TEXT, CP_BRIGHT, CP_SHADOW,
 CP_INPUT, CP_WARN, CP_GREEN, CP_TITLE_IDLE, CP_ICON, CP_TASKBAR, CP_TASK_ON, CP_OUTLINE, CP_METER, CP_ACCENT,
 CP_SCROLL, CP_SEL) = range(1, 23)

# The seven v2.4 colour schemes: name, menu, desktop, window, title (fg, bg), accent
THEMES = [
    ('COS Classic (Blue/Cyan)', (0, 6), (6, 4), (7, 0), (0, 7), 3),
    ('Night Mode (Black/Green)', (2, 0), (2, 0), (2, 0), (0, 2), 2),
    ('Retro Amber (Amber/Black)', (0, 3), (3, 0), (3, 0), (0, 3), 3),
    ('High Contrast (Mono)', (0, 7), (7, 0), (7, 0), (0, 7), 7),
    ('TCF Blue (Blue/White)', (7, 4), (4, 7), (7, 4), (4, 7), 6),
    ('Magenta Dream', (0, 5), (5, 0), (7, 0), (0, 5), 5),
    ('Deep Sea (Cyan/Blue)', (7, 4), (4, 0), (6, 4), (4, 6), 6),
]


def pair_table(idx):
    """colour pair number -> (fg, bg), the way v2.4's apply_theme set them."""
    _, (mf, mb), (df, db), (wf, wb), (tf, tb), ac = THEMES[idx % len(THEMES)]
    sh = db if db != wb else 0
    return {
        CP_MENUBAR: (mf, mb), CP_MENU_SEL: (tb, tf), CP_DESKTOP: (df, db), CP_WINDOW: (wf, wb),
        CP_WIN_TITLE: (tf, tb), CP_STATUS: (mf, mb), CP_CURSOR: (0, 3), CP_TEXT: (wf, wb), CP_BRIGHT: (ac, wb),
        CP_SHADOW: (sh, wb), CP_INPUT: (wb, wf), CP_WARN: (1, wb), CP_GREEN: (2, wb), CP_TITLE_IDLE: (wf, sh),
        CP_ICON: (ac, db), CP_TASKBAR: (mf, mb), CP_TASK_ON: (mb, mf), CP_OUTLINE: (7, db), CP_METER: (ac, wb),
        CP_ACCENT: (ac, db), CP_SCROLL: (wf, wb), CP_SEL: (tb, tf), 0: (7, 0),
    }


def cp(n, bold=False, dim=False, rev=False):
    a = curses.color_pair(n)
    if bold:
        a |= A_BOLD
    if dim:
        a |= A_DIM
    if rev:
        a |= A_REVERSE
    return a


class CosFatal(Exception):
    """Raised by Window.bsod() / error(fatal=True): the app stops on purpose."""

    def __init__(self, reason='', title='Fatal error'):
        super().__init__(reason)
        self.reason, self.title = reason, title


# =============================================================================
# cells
# =============================================================================
class Grid:
    """What a curses screen was: rows of [char, attr] cells."""

    def __init__(self, w, h, attr=0):
        self.w, self.h = max(0, w), max(0, h)
        self.cells = [[[' ', attr] for _ in range(self.w)] for _ in range(self.h)]

    def set(self, y, x, ch, attr):
        if 0 <= y < self.h and 0 <= x < self.w:
            c = self.cells[y][x]
            c[0] = ch if isinstance(ch, str) else (chr(ch) if isinstance(ch, int) and 0 < ch < 0x110000 else ' ')
            c[1] = attr

    def snapshot(self):
        return tuple(tuple((c[0], c[1]) for c in row) for row in self.cells)


def _safestr(scr, y, x, s, attr=0):
    for i, ch in enumerate(s):
        scr.set(y, x + i, ch, attr)


def _safech(scr, y, x, c, attr=0):
    scr.set(y, x, c, attr)


class Canvas:
    """A clipped drawing region. All coordinates are region-relative."""
    __slots__ = ('scr', 'y0', 'x0', 'h', 'w')

    def __init__(self, scr, y0, x0, h, w):
        self.scr, self.y0, self.x0 = scr, y0, x0
        self.h, self.w = max(0, h), max(0, w)

    def put(self, row, col, s, attr=0):
        if row < 0 or row >= self.h or self.w <= 0:
            return
        s = str(s).replace('\t', '    ')
        if col < 0:
            s = s[-col:]
            col = 0
        if col >= self.w or not s:
            return
        _safestr(self.scr, self.y0 + row, self.x0 + col, s[:self.w - col], attr)

    def ch(self, row, col, c, attr=0):
        if 0 <= row < self.h and 0 <= col < self.w:
            _safech(self.scr, self.y0 + row, self.x0 + col, c, attr)

    def fill(self, attr=0, c=' '):
        for r in range(self.h):
            self.put(r, 0, c * self.w, attr)

    def hline(self, row, col, n, c=None, attr=0):
        c = curses.ACS_HLINE if c is None else c
        for i in range(n):
            self.ch(row, col + i, c, attr)

    def vline(self, row, col, n, c=None, attr=0):
        c = curses.ACS_VLINE if c is None else c
        for i in range(n):
            self.ch(row + i, col, c, attr)

    def center(self, row, s, attr=0):
        self.put(row, max(0, (self.w - len(str(s))) // 2), s, attr)

    def sub(self, row, col, h, w):
        return Canvas(self.scr, self.y0 + row, self.x0 + col, h, w)

    def bar(self, row, col, width, frac, attr_on, attr_off, c_on=None, c_off=None):
        c_on = curses.ACS_CKBOARD if c_on is None else c_on
        c_off = '-' if c_off is None else c_off
        filled = int(round(max(0.0, min(1.0, frac)) * width))
        for i in range(width):
            self.ch(row, col + i, c_on if i < filled else c_off, attr_on if i < filled else attr_off)

    def scrollbar(self, row, col, height, total, visible, offset, attr):
        if height < 3:
            return
        self.ch(row, col, curses.ACS_UARROW, attr)
        self.ch(row + height - 1, col, curses.ACS_DARROW, attr)
        track = height - 2
        if total <= visible or total <= 0:
            for i in range(track):
                self.ch(row + 1 + i, col, curses.ACS_CKBOARD, attr | A_DIM)
            return
        thumb = max(1, int(track * visible / total))
        span = track - thumb
        pos = 0 if total == visible else int(round(span * offset / max(1, total - visible)))
        for i in range(track):
            inside = pos <= i < pos + thumb
            self.ch(row + 1 + i, col, curses.ACS_CKBOARD if inside else ' ', attr | (A_BOLD if inside else A_DIM))


def draw_box(scr, y, x, h, w, attr, fillattr=None, fill=' '):
    if h < 2 or w < 2:
        return
    if fillattr is not None:
        for r in range(1, h - 1):
            _safestr(scr, y + r, x + 1, fill * (w - 2), fillattr)
    for (yy, xx, c) in ((y, x, curses.ACS_ULCORNER), (y, x + w - 1, curses.ACS_URCORNER),
                        (y + h - 1, x, curses.ACS_LLCORNER), (y + h - 1, x + w - 1, curses.ACS_LRCORNER)):
        _safech(scr, yy, xx, c, attr)
    for i in range(1, w - 1):
        _safech(scr, y, x + i, curses.ACS_HLINE, attr)
        _safech(scr, y + h - 1, x + i, curses.ACS_HLINE, attr)
    for i in range(1, h - 1):
        _safech(scr, y + i, x, curses.ACS_VLINE, attr)
        _safech(scr, y + i, x + w - 1, curses.ACS_VLINE, attr)


def draw_outline(scr, y, x, h, w, attr):
    if h < 2 or w < 2:
        return
    for i in range(w):
        _safech(scr, y, x + i, curses.ACS_CKBOARD, attr)
        _safech(scr, y + h - 1, x + i, curses.ACS_CKBOARD, attr)
    for i in range(1, h - 1):
        _safech(scr, y + i, x, curses.ACS_CKBOARD, attr)
        _safech(scr, y + i, x + w - 1, curses.ACS_CKBOARD, attr)


# =============================================================================
# windows
# =============================================================================
class Window:
    icon = '#'
    resizable = True
    claims_ctrl = frozenset()
    min_w, min_h = 26, 7
    want_cursor = False

    def __init__(self, title, w=50, h=16, x=None, y=None):
        self.title = title
        self.w, self.h = w, h
        self.x = 2 if x is None else x
        self.y = 2 if y is None else y
        self.closed = False
        self.maximized = False
        self._restore = None
        self.wm = None
        self.scroll = 0
        self.caret = None

    @property
    def cw(self):
        return max(0, self.w - 2)

    @property
    def chh(self):
        return max(0, self.h - 2)

    def client_canvas(self, scr):
        return Canvas(scr, 0, 0, self.chh, self.cw)

    # overridables
    def draw_client(self, c, active):
        c.put(0, 1, '(empty window)', cp(CP_TEXT))

    def status(self):
        return ''

    def on_key(self, key):
        return False

    def on_click(self, row, col, btn):
        return False

    def on_scroll(self, n):
        return False

    def pixel_rects(self, fw, fh):
        return []

    def tick(self):
        pass

    def on_close(self):
        pass

    def on_resize(self):
        pass

    # helpers
    def notify(self, msg, sound='notify'):
        if self.wm:
            self.wm.notify(msg, sound)

    def notice(self, title, body, kind='info', on_ok=None):
        return self.wm.open(NoticeWindow(title, body, kind, on_ok)) if self.wm else None

    def warn(self, title, body, on_ok=None):
        return self.notice(title, body, 'warn', on_ok)

    def error(self, title, body, on_ok=None, fatal=False):
        if fatal:
            raise CosFatal(body if isinstance(body, str) else '\n'.join(body), title)
        return self.notice(title, body, 'error', on_ok)

    def success(self, title, body, on_ok=None):
        return self.notice(title, body, 'ok', on_ok)

    def ask(self, title, body, on_yes=None, kind=None, yes='Yes', no='No', danger=False, on_no=None):
        return self.wm.open(ConfirmWindow(title, body, on_yes, danger, kind, yes, no, on_no)) if self.wm else None

    def confirm(self, title, body, on_yes, danger=False):
        return self.ask(title, body, on_yes, danger=danger)

    def prompt(self, title, label, default='', on_ok=None):
        return self.wm.open(PromptWindow(title, label, default, on_ok)) if self.wm else None

    def progress(self, title, label='Working...', total=0, worker=None, cancellable=True, on_done=None):
        return self.wm.open(ProgressWindow(title, label, total, worker, cancellable, on_done)) if self.wm else None

    def text(self, title, lines, w=None):
        return self.wm.open(TextWindow(title, lines, w)) if self.wm else None

    def play(self, name):
        if self.wm:
            self.wm.sound.play(name)

    def bsod(self, reason='', title='Fatal error'):
        raise CosFatal(reason, title)

    def toggle_max(self, lay=None):
        if self.wm:
            self.wm.toggle_max(self)


class ScrollListWindow(Window):
    def __init__(self, title, w=56, h=18, x=None, y=None):
        super().__init__(title, w, h, x, y)
        self.sel = 0

    def rows(self):
        return []

    def row_attr(self, i, item, selected, active):
        if selected:
            return cp(CP_SEL, bold=True) if active else cp(CP_MENU_SEL)
        return cp(CP_TEXT)

    def row_text(self, i, item):
        return str(item)

    def on_activate(self, i, item):
        pass

    def visible_rows(self):
        return self.chh - self.header_rows() - self.footer_rows()

    def header_rows(self):
        return 0

    def footer_rows(self):
        return 0

    def draw_header(self, c, active):
        pass

    def draw_footer(self, c, active, top):
        pass

    def draw_client(self, c, active):
        c.fill(cp(CP_WINDOW))
        items = self.rows()
        hr = self.header_rows()
        vis = max(1, self.visible_rows())
        self.sel = max(0, min(self.sel, max(0, len(items) - 1)))
        if self.sel < self.scroll:
            self.scroll = self.sel
        if self.sel >= self.scroll + vis:
            self.scroll = self.sel - vis + 1
        self.scroll = max(0, min(self.scroll, max(0, len(items) - vis)))
        self.draw_header(c, active)
        need_sb = len(items) > vis
        body_w = c.w - (1 if need_sb else 0)
        for i in range(vis):
            ri = self.scroll + i
            if ri >= len(items):
                break
            txt = self.row_text(ri, items[ri])
            a = self.row_attr(ri, items[ri], ri == self.sel, active)
            c.put(hr + i, 0, ('%-*s' % (max(0, body_w), txt))[:max(0, body_w)], a)
        if need_sb:
            c.scrollbar(hr, c.w - 1, vis, len(items), vis, self.scroll, cp(CP_SCROLL))
        self.draw_footer(c, active, hr + vis)

    def on_key(self, key):
        items = self.rows()
        vis = max(1, self.visible_rows())
        if key in (curses.KEY_UP, ord('k')):
            self.sel = max(0, self.sel - 1)
            return True
        if key in (curses.KEY_DOWN, ord('j')):
            self.sel = min(len(items) - 1, self.sel + 1)
            return True
        if key == curses.KEY_PPAGE:
            self.sel = max(0, self.sel - vis)
            return True
        if key == curses.KEY_NPAGE:
            self.sel = min(len(items) - 1, self.sel + vis)
            return True
        if key == curses.KEY_HOME:
            self.sel = 0
            return True
        if key == curses.KEY_END:
            self.sel = max(0, len(items) - 1)
            return True
        if key in (curses.KEY_ENTER, 10, 13):
            if items:
                self.on_activate(self.sel, items[self.sel])
            return True
        return False

    def on_scroll(self, n):
        items = self.rows()
        vis = max(1, self.visible_rows())
        self.scroll = max(0, min(self.scroll + n * 2, max(0, len(items) - vis)))
        self.sel = max(self.scroll, min(self.sel, self.scroll + vis - 1))
        return True

    def on_click(self, row, col, btn):
        hr = self.header_rows()
        items = self.rows()
        idx = self.scroll + (row - hr)
        if hr <= row and 0 <= idx < len(items):
            double = self.wm and self.wm.was_double(self, idx)
            self.sel = idx
            if double or btn == 3:
                self.on_activate(idx, items[idx])
            return True
        return False


# -- dialogs --------------------------------------------------------------------
def _lines(body):
    if isinstance(body, (list, tuple)):
        out = []
        for b in body:
            out += str(b).split('\n')
        return out
    return str(body).split('\n')


KIND_TAG = {'info': ('[i]', CP_BRIGHT), 'warn': ('[!]', CP_WARN), 'error': ('[X]', CP_WARN), 'ok': ('[v]', CP_GREEN),
            'question': ('[?]', CP_BRIGHT), None: ('[?]', CP_BRIGHT)}


def _buttons(c, row, labels, sel):
    total = sum(len(l) + 4 for l in labels) + 2 * (len(labels) - 1)
    col = max(0, (c.w - total) // 2)
    spans = []
    for i, l in enumerate(labels):
        txt = '[ %s ]' % l
        c.put(row, col, txt, cp(CP_SEL, bold=True) if i == sel else cp(CP_TEXT))
        spans.append((col, col + len(txt)))
        col += len(txt) + 2
    return spans


class NoticeWindow(Window):
    resizable = False
    dialog = True

    def __init__(self, title, body, kind='info', on_ok=None):
        self.body = _lines(body)
        self.kind = kind if kind in KIND_TAG else 'info'
        self.on_ok = on_ok
        w = max(34, min(76, max(max(len(l) for l in self.body) + 10, len(str(title)) + 10)))
        super().__init__(title, w, len(self.body) + 7)
        self.spans = []

    def draw_client(self, c, active):
        c.fill(cp(CP_WINDOW))
        tag, pair = KIND_TAG[self.kind]
        c.put(1, 2, tag, cp(pair, bold=True))
        for i, l in enumerate(self.body):
            c.put(1 + i, 7, l, cp(CP_TEXT))
        self.spans = _buttons(c, c.h - 2, ['OK'], 0)

    def _ok(self):
        self.wm.close(self)
        if self.on_ok:
            self.on_ok()

    def on_key(self, key):
        if key in (10, 13, 27, ord(' '), curses.KEY_ENTER, ord('o'), ord('O')):
            self._ok()
            return True
        return False

    def on_click(self, row, col, btn):
        if row == self.chh - 2 and any(a <= col < b for a, b in self.spans):
            self._ok()
        return True


class ConfirmWindow(Window):
    resizable = False
    dialog = True

    def __init__(self, title, body, on_yes=None, danger=False, kind=None, yes='Yes', no='No', on_no=None):
        self.body = _lines(body)
        self.on_yes, self.on_no = on_yes, on_no
        self.kind = kind if kind in KIND_TAG else ('warn' if danger else 'question')
        self.labels = [str(yes), str(no)]
        self.sel = 1 if danger else 0
        w = max(36, min(76, max(max(len(l) for l in self.body) + 10, len(str(title)) + 10)))
        super().__init__(title, w, len(self.body) + 7)
        self.spans = []

    def draw_client(self, c, active):
        c.fill(cp(CP_WINDOW))
        tag, pair = KIND_TAG[self.kind]
        c.put(1, 2, tag, cp(pair, bold=True))
        for i, l in enumerate(self.body):
            c.put(1 + i, 7, l, cp(CP_TEXT))
        self.spans = _buttons(c, c.h - 2, self.labels, self.sel)

    def _answer(self, yes):
        self.wm.close(self)
        cb = self.on_yes if yes else self.on_no
        if cb:
            cb()

    def on_key(self, key):
        if key in (curses.KEY_LEFT, curses.KEY_RIGHT, 9, curses.KEY_BTAB):
            self.sel = 1 - self.sel
        elif key in (10, 13, curses.KEY_ENTER, ord(' ')):
            self._answer(self.sel == 0)
        elif key == 27:
            self._answer(False)
        elif key in (ord(self.labels[0][:1].lower()), ord(self.labels[0][:1].upper())):
            self._answer(True)
        elif key in (ord(self.labels[1][:1].lower()), ord(self.labels[1][:1].upper())):
            self._answer(False)
        else:
            return False
        return True

    def on_click(self, row, col, btn):
        if row == self.chh - 2:
            for i, (a, b) in enumerate(self.spans):
                if a <= col < b:
                    self._answer(i == 0)
        return True


class PromptWindow(Window):
    resizable = False
    dialog = True
    want_cursor = True

    def __init__(self, title, label, default='', on_ok=None):
        self.label = str(label)
        self.value = str(default)
        self.on_ok = on_ok
        super().__init__(title, max(44, min(76, len(self.label) + 8)), 8)

    def draw_client(self, c, active):
        c.fill(cp(CP_WINDOW))
        c.put(1, 2, self.label, cp(CP_TEXT))
        fw = c.w - 4
        shown = self.value[-(fw - 1):]
        c.put(2, 2, ' ' * fw, cp(CP_INPUT))
        c.put(2, 2, shown, cp(CP_INPUT))
        self.caret = (2, 2 + len(shown))
        c.put(4, 2, 'ENTER ok   ESC cancel', cp(CP_SHADOW))

    def on_key(self, key):
        if key in (10, 13, curses.KEY_ENTER):
            self.wm.close(self)
            if self.on_ok:
                self.on_ok(self.value)
        elif key == 27:
            self.wm.close(self)
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            self.value = self.value[:-1]
        elif 32 <= key < 0x110000 and key not in range(256, 512):
            self.value += chr(key)
        else:
            return False
        return True


class TextWindow(Window):
    def __init__(self, title, lines, w=None):
        self.lines = _lines(lines)
        w = w or max(40, min(90, max([len(l) for l in self.lines] + [20]) + 4))
        super().__init__(title, w, min(30, len(self.lines) + 4))

    def status(self):
        return 'arrows scroll   Q close'

    def draw_client(self, c, active):
        c.fill(cp(CP_WINDOW))
        self.scroll = max(0, min(self.scroll, max(0, len(self.lines) - c.h)))
        for i in range(c.h):
            if self.scroll + i < len(self.lines):
                c.put(i, 1, self.lines[self.scroll + i], cp(CP_TEXT))
        if len(self.lines) > c.h:
            c.scrollbar(0, c.w - 1, c.h, len(self.lines), c.h, self.scroll, cp(CP_SCROLL))

    def on_key(self, key):
        if key in (curses.KEY_UP, ord('k')):
            self.scroll -= 1
        elif key in (curses.KEY_DOWN, ord('j')):
            self.scroll += 1
        elif key == curses.KEY_PPAGE:
            self.scroll -= self.chh
        elif key in (curses.KEY_NPAGE, ord(' ')):
            self.scroll += self.chh
        elif key == 27:
            self.wm.close(self)
        else:
            return False
        self.scroll = max(0, self.scroll)
        return True

    def on_scroll(self, n):
        self.scroll = max(0, self.scroll + n * 2)
        return True


class Progress:
    """The handle a worker thread uses to report progress."""

    def __init__(self, win):
        self._win = win

    @property
    def cancelled(self):
        return self._win.cancelled

    def set(self, done, label=None):
        self._win.set(done, label)

    def advance(self, n=1, label=None):
        self._win.advance(n, label)

    def label(self, text):
        self._win.set(None, text)

    def total(self, n):
        with self._win.lock:
            self._win.total_units = max(0, int(n))


class ProgressWindow(Window):
    resizable = False
    dialog = True

    def __init__(self, title, label='Working...', total=0, worker=None, cancellable=True, on_done=None):
        super().__init__(title, 54, 9)
        self.label_text = str(label)
        self.total_units = max(0, int(total or 0))
        self.done_units = 0
        self.worker = worker
        self.cancellable = cancellable
        self.on_done = on_done
        self.cancelled = False
        self.finished = False
        self.result = None
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.thread = None

    def start(self):
        if self.worker and self.thread is None:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    def _run(self):
        try:
            self.result = self.worker(Progress(self))
        except Exception as e:
            self.result = e
        self.finished = True

    def set(self, done, label=None):
        with self.lock:
            if done is not None:
                self.done_units = done
            if label is not None:
                self.label_text = str(label)

    def advance(self, n=1, label=None):
        with self.lock:
            self.done_units += n
            if label is not None:
                self.label_text = str(label)

    def tick(self):
        self.start()
        if self.finished and not self.closed:
            self.wm.close(self)
            if self.on_done:
                self.on_done(self.result)

    def draw_client(self, c, active):
        c.fill(cp(CP_WINDOW))
        with self.lock:
            lab, done, tot = self.label_text, self.done_units, self.total_units
        c.put(1, 2, lab[:c.w - 4], cp(CP_TEXT))
        bw = c.w - 4
        if tot:
            c.bar(3, 2, bw, done / max(1, tot), cp(CP_METER, bold=True), cp(CP_SHADOW))
            c.put(4, 2, '%d%%' % int(100 * done / max(1, tot)), cp(CP_SHADOW))
        else:
            pos = int((time.time() - self.t0) * 12) % (bw + 6) - 6
            for i in range(bw):
                c.ch(3, 2 + i, curses.ACS_CKBOARD if pos <= i < pos + 6 else '-',
                     cp(CP_METER, bold=True) if pos <= i < pos + 6 else cp(CP_SHADOW))
        if self.cancellable:
            c.put(c.h - 1, 2, 'ESC cancel', cp(CP_SHADOW))

    def on_key(self, key):
        if key == 27 and self.cancellable:
            self.cancelled = True
            return True
        return False


# =============================================================================
# helpers apps were given
# =============================================================================
RAMP = ' .:-=+*#%@'


def human_size(n):
    n = float(n or 0)
    for unit in ('B', 'K', 'M', 'G', 'T'):
        if n < 1024 or unit == 'T':
            return ('%d%s' % (n, unit)) if unit == 'B' else ('%.1f%s' % (n, unit))
        n /= 1024


def human_time(sec):
    sec = int(max(0, sec or 0))
    if sec >= 86400:
        return '%dd %02dh' % (sec // 86400, sec // 3600 % 24)
    if sec >= 3600:
        return '%dh %02dm' % (sec // 3600, sec // 60 % 60)
    return '%dm %02ds' % (sec // 60, sec % 60)


def run_cmd(cmd, timeout=5):
    try:
        r = subprocess.run(cmd if isinstance(cmd, list) else cmd, shell=isinstance(cmd, str), capture_output=True,
                           text=True, timeout=timeout)
        return r.stdout
    except Exception:
        return ''


def wrap_text(text, width):
    out = []
    for para in str(text).split('\n'):
        line = ''
        for word in para.split(' '):
            if len(line) + len(word) + (1 if line else 0) > width and line:
                out.append(line)
                line = word
            else:
                line = (line + ' ' + word) if line else word
        out.append(line)
    return out


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.USub: operator.neg,
        ast.UAdd: operator.pos}
_FUNCS = {k: getattr(math, k) for k in ('sqrt', 'sin', 'cos', 'tan', 'log', 'log10', 'exp', 'floor', 'ceil', 'fabs')}
_FUNCS.update(abs=abs, round=round, pi=math.pi, e=math.e)


def eval_math(expr):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in _FUNCS and not callable(_FUNCS[n.id]):
            return _FUNCS[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and callable(_FUNCS.get(n.func.id)):
            return _FUNCS[n.func.id](*[ev(a) for a in n.args])
        raise ValueError('unsupported expression')
    return ev(ast.parse(str(expr).replace('^', '**'), mode='eval'))


def meminfo():
    out = {}
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':', 1)
                out[k] = int(v.split()[0]) * 1024
    except OSError:
        pass
    return out


def loadavg():
    try:
        return os.getloadavg()
    except OSError:
        return (0.0, 0.0, 0.0)


def uptime_seconds():
    try:
        with open('/proc/uptime') as f:
            return float(f.read().split()[0])
    except OSError:
        return 0.0


def battery_info():
    return None


def local_addresses():
    return []


def media_attr(fg, bg, bold=False):
    return cp(CP_TEXT, bold=bold)


def _not_here(name):
    def f(*a, **k):
        raise NotImplementedError('cos.%s is not available in compatibility mode' % name)
    return f


def make_api(version, home, state, app_dir, sound_dir):
    """The `cos` namespace for one app, like v2.4's cosapp_api()."""
    api = types.SimpleNamespace(
        VERSION=version, CODENAME=CODENAME_24 if version == '2.4' else 'Classic',
        HOME=home, APP_DIR=app_dir, SOUND_DIR=sound_dir, state=state,
        Window=Window, ScrollListWindow=ScrollListWindow, Canvas=Canvas,
        NoticeWindow=NoticeWindow, ProgressWindow=ProgressWindow, ConfirmWindow=ConfirmWindow,
        PromptWindow=PromptWindow, TextWindow=TextWindow,
        cp=cp, media_attr=media_attr, draw_box=draw_box, draw_outline=draw_outline,
        cell_art=_not_here('cell_art'), fit_cells=_not_here('fit_cells'), decode_still=_not_here('decode_still'),
        probe_media=_not_here('probe_media'), file_manifest=_not_here('file_manifest'),
        RAMP=RAMP, human_size=human_size, human_time=human_time, run_cmd=run_cmd, wrap_text=wrap_text,
        which=shutil.which, eval_math=eval_math, meminfo=meminfo, loadavg=loadavg, uptime_seconds=uptime_seconds,
        battery_info=battery_info, local_addresses=local_addresses, curses=curses, CosFatal=CosFatal,
    )
    for k, v in globals().items():
        if k.startswith('CP_'):
            setattr(api, k, v)
    return api
