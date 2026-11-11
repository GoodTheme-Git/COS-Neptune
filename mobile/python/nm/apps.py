"""The phone's apps. Each is a shell.App subclass; register_all() adds them in home-screen order."""
import ast
import importlib.util
import math
import operator
import os
import platform
import random
import re
import shutil
import socket
import threading
import time

from PIL import Image, ImageDraw

from . import ui
from .ui import W, H, gfx, button, text, rrect, INK, DIM, LINE, WHITE
from .shell import App, HOME, FULLNAME, VERSION

ROW = 56


def toolbar(img, hits, box, items):
    """A strip of buttons along the top of the client area. items: (label, fn[, kind]). Returns its bottom."""
    x0, y0, x1, _ = box
    ImageDraw.Draw(img).rectangle((x0, y0, x1, y0 + 48), fill=(232, 238, 245, 255))
    ImageDraw.Draw(img).line((x0, y0 + 48, x1, y0 + 48), fill=LINE)
    x = x0 + 8
    for it in items:
        label, fn = it[0], it[1]
        kind = it[2] if len(it) > 2 else 'normal'
        w = max(44, ui.text_w(label, 14) + 24)
        button(img, hits, (x, y0 + 7, x + w, y0 + 41), label, fn, kind, size=14, r=8)
        x += w + 6
    return y0 + 49


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' if unit == 'B' else '%.1f %s') % (n, unit)
        n /= 1024.0


class TextBox:
    """One editable text value with a cursor, driven by key events."""

    def __init__(self, value='', multiline=False):
        self.value = value
        self.pos = len(value)
        self.multiline = multiline

    def key(self, ev):
        k, ch = ev.get('key'), ev.get('char', '')
        v, p = self.value, self.pos
        if k == 'backspace' and p > 0:
            self.value, self.pos = v[:p - 1] + v[p:], p - 1
        elif k == 'delete' and p < len(v):
            self.value = v[:p] + v[p + 1:]
        elif k == 'left':
            self.pos = max(0, p - 1)
        elif k == 'right':
            self.pos = min(len(v), p + 1)
        elif k == 'home':
            self.pos = v.rfind('\n', 0, p) + 1 if self.multiline else 0
        elif k == 'end':
            e = v.find('\n', p)
            self.pos = (len(v) if e < 0 else e) if self.multiline else len(v)
        elif k in ('up', 'down') and self.multiline:
            start = v.rfind('\n', 0, p) + 1
            col = p - start
            if k == 'up' and start > 0:
                ps = v.rfind('\n', 0, start - 1) + 1
                self.pos = min(ps + col, start - 1)
            elif k == 'down':
                e = v.find('\n', p)
                if e >= 0:
                    ne = v.find('\n', e + 1)
                    self.pos = min(e + 1 + col, len(v) if ne < 0 else ne)
        elif k == 'enter' and self.multiline:
            self.value, self.pos = v[:p] + '\n' + v[p:], p + 1
        elif k == 'tab' and self.multiline:
            self.value, self.pos = v[:p] + '    ' + v[p:], p + 4
        elif ev.get('ctrl') and k == 'a':
            self.pos = len(v)
        elif ch and not ev.get('ctrl'):
            self.value, self.pos = v[:p] + ch + v[p:], p + len(ch)
        else:
            return False
        return True

    def caret_text(self):
        on = int(time.time() * 2) % 2 == 0
        return self.value[:self.pos] + ('|' if on else ' ') + self.value[self.pos:]


def input_bar(img, hits, box, tb, placeholder, send=None, send_label='Send'):
    """A text field along the bottom of the client area with a Send button. Returns its top."""
    x0, _, x1, y1 = box
    top = y1 - 58
    ImageDraw.Draw(img).rectangle((x0, top, x1, y1), fill=(232, 238, 245, 255))
    ImageDraw.Draw(img).line((x0, top, x1, top), fill=LINE)
    bw = 76 if send else 0
    rrect(img, (x0 + 8, top + 9, x1 - 14 - bw, y1 - 9), 20, fill=WHITE, outline=(150, 175, 200, 255))
    fw = x1 - x0 - 44 - bw
    if tb.value:
        shown = tb.caret_text().replace('\n', ' ')
        f = ui.font(15)
        while gfx.text_width(f, shown) > fw and len(shown) > 1:
            shown = shown[1:]
        text(img, (x0 + 22, top + 29), shown, 15, INK, anchor='lm')
    else:
        text(img, (x0 + 22, top + 29), ('|' if int(time.time() * 2) % 2 == 0 else '') + placeholder, 15, DIM, anchor='lm')
    if send:
        button(img, hits, (x1 - 8 - bw, top + 9, x1 - 8, y1 - 9), send_label, send, 'primary', size=14, r=20)
    return top


# ================================================================ Files

class Files(App):
    id, name, icon = 'files', 'Files', 'folder'
    c1, c2 = '#d9a634', '#8a5a0c'

    def __init__(self, shell, path=None, **a):
        super().__init__(shell)
        self.cwd = path or HOME

    def rel(self, p):
        r = os.path.relpath(p, HOME)
        return '/' if r == '.' else '/' + r

    def entries(self):
        try:
            names = [n for n in os.listdir(self.cwd) if not n.startswith('.')]
        except OSError:
            return []
        full = [(n, os.path.join(self.cwd, n)) for n in names]
        return sorted(full, key=lambda e: (not os.path.isdir(e[1]), e[0].lower()))

    def draw(self, img, box, hits):
        self.title = 'Files ' + self.rel(self.cwd)
        y = toolbar(img, hits, box, [('↑ Up', self.up), ('+ Folder', self.new_folder), ('+ File', self.new_file)])
        bar = img.crop((0, box[1], W, y))
        ents = self.entries()
        self.max_scroll = max(0, len(ents) * ROW - (box[3] - y) + 10)
        yy = y - self.scroll
        if not ents:
            text(img, (W // 2, y + 60), 'This folder is empty', 15, DIM, anchor='mm')
        for n, p in ents:
            if yy > box[3]:
                break
            if yy + ROW > y:
                isdir = os.path.isdir(p)
                img.alpha_composite(ui.icons.get(ui.icons.for_path(p, isdir), 34), (14, yy + 11))
                text(img, (60, yy + 20), n, 15, INK, anchor='lm', width=W - 90)
                try:
                    st = os.stat(p)
                    info = time.strftime('%b %-d %H:%M', time.localtime(st.st_mtime)) + ('' if isdir else '  ' + human(st.st_size))
                except OSError:
                    info = ''
                text(img, (60, yy + 40), 'Folder' if isdir else info, 12, DIM, anchor='lm')
                ImageDraw.Draw(img).line((60, yy + ROW - 1, W, yy + ROW - 1), fill=LINE)
                hits.add((0, max(y, yy), W, yy + ROW), lambda p=p: self.activate(p), hold=lambda p=p: self.menu(p))
            yy += ROW
        img.paste(bar, (0, box[1]))

    def up(self):
        if os.path.abspath(self.cwd) != os.path.abspath(HOME):
            self.cwd = os.path.dirname(self.cwd)
            self.scroll = 0

    def back(self):
        if os.path.abspath(self.cwd) != os.path.abspath(HOME):
            self.up()
            return True
        return False

    def activate(self, p):
        if os.path.isdir(p):
            self.cwd, self.scroll = p, 0
        else:
            open_file(self.shell, p)

    def menu(self, p):
        name = os.path.basename(p)

        def pick(i):
            if i == 0:
                self.activate(p)
            elif i == 1:
                self.shell.prompt('Rename', 'New name for %s' % name, lambda n: self.rename(p, n), name)
            elif i == 2:
                self.shell.confirm('Delete', 'Delete %s?' % name, lambda: self.delete(p), 'Delete', danger=True)
        self.shell.choose(name, ['Open', 'Rename', 'Delete'], pick)

    def rename(self, p, n):
        n = n.strip().replace('/', '-')
        if n:
            os.rename(p, os.path.join(os.path.dirname(p), n))

    def delete(self, p):
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
        self.shell.toast('Deleted')

    def new_folder(self):
        self.shell.prompt('New Folder', 'Folder name', lambda n: n.strip() and os.makedirs(
            os.path.join(self.cwd, n.strip().replace('/', '-')), exist_ok=True), 'New Folder')

    def new_file(self):
        def make(n):
            n = n.strip().replace('/', '-')
            if n:
                p = os.path.join(self.cwd, n)
                open(p, 'a').close()
                self.shell.open('notepad', path=p)
        self.shell.prompt('New File', 'File name', make, 'Untitled.txt')


def open_file(shell, p):
    ext = os.path.splitext(p)[1].lower()
    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
        shell.open('photos', path=p)
    elif ext in ('.mp3', '.ogg', '.wav', '.flac'):
        shell.open('druman', path=p)
    else:
        shell.open('notepad', path=p)


# ================================================================ Notepad

class Notepad(App):
    id, name, icon = 'notepad', 'Notepad', 'notepad'
    c1, c2 = '#4b8fd6', '#123f8a'

    def __init__(self, shell, path=None, **a):
        super().__init__(shell)
        self.path = None
        self.tb = TextBox('', multiline=True)
        self.saved = ''
        if path:
            self.load(path)

    def args(self, path=None, **a):
        if path and path != self.path:
            self.load(path)

    def load(self, path):
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                data = f.read(400_000)
        except OSError as e:
            return self.shell.toast("Can't open: %s" % e)
        self.path, self.tb, self.saved, self.scroll = path, TextBox(data, True), data, 0
        self.tb.pos = 0

    def draw(self, img, box, hits):
        name = os.path.basename(self.path) if self.path else 'Untitled'
        self.title = name + (' *' if self.tb.value != self.saved else '')
        y = toolbar(img, hits, box, [('New', self.new), ('Open', self.open_doc), ('Save', self.save, 'primary')])
        bar = img.crop((0, box[1], W, y))
        lines = []
        caret = self.tb.caret_text()
        for ln in ui.wrap(caret, 15, W - 32, kind='mono'):
            lines.append(ln)
        lh = 21
        self.max_scroll = max(0, len(lines) * lh - (box[3] - y) + 30)
        # keep the cursor in view
        before = ui.wrap(self.tb.value[:self.tb.pos], 15, W - 32, kind='mono')
        cy = (len(before) - 1) * lh
        view = box[3] - y - 30
        if cy - self.scroll > view:
            self.scroll = cy - view
        elif cy < self.scroll:
            self.scroll = max(0, cy)
        yy = y + 12 - self.scroll
        for ln in lines:
            if yy > box[3]:
                break
            if yy > y - lh:
                text(img, (16, yy), ln, 15, INK, kind='mono')
            yy += lh
        img.paste(bar, (0, box[1]))
        hits.add((0, y, W, box[3]), lambda: None)
        text(img, (W - 12, box[3] - 14), '%d chars' % len(self.tb.value), 11, DIM, anchor='rm')

    def key(self, ev):
        if ev.get('ctrl') and ev.get('key') == 's':
            self.save()
            return True
        return self.tb.key(ev)

    def new(self):
        self.path, self.tb, self.saved, self.scroll = None, TextBox('', True), '', 0

    def open_doc(self):
        docs = os.path.join(HOME, 'Documents')
        names = sorted(n for n in os.listdir(docs) if not n.startswith('.') and os.path.isfile(os.path.join(docs, n)))
        if not names:
            return self.shell.toast('No documents yet')
        self.shell.choose('Open from Documents', names, lambda i: self.load(os.path.join(docs, names[i])))

    def save(self):
        if not self.path:
            def named(n):
                n = n.strip().replace('/', '-')
                if n:
                    self.path = os.path.join(HOME, 'Documents', n if '.' in n else n + '.txt')
                    self.save()
            return self.shell.prompt('Save As', 'Save in Documents as', named, 'Untitled.txt')
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(self.tb.value)
        self.saved = self.tb.value
        self.shell.toast('Saved %s' % os.path.basename(self.path))

    def back(self):
        if self.tb.value != self.saved:
            self.shell.confirm('Notepad', 'Close without saving?', lambda: self.shell.close('notepad'), 'Discard', True)
            return True
        return False


# ================================================================ Calculator

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}


def safe_eval(expr):
    """Arithmetic only: numbers, + - * / % ** and brackets, sqrt/sin/cos/tan/log/pi/e."""
    funcs = {'sqrt': math.sqrt, 'sin': math.sin, 'cos': math.cos, 'tan': math.tan, 'log': math.log10,
             'ln': math.log, 'abs': abs}
    consts = {'pi': math.pi, 'e': math.e}

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 1000:
                raise ValueError('too big')
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Name) and n.id in consts:
            return consts[n.id]
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs and len(n.args) == 1:
            return funcs[n.func.id](ev(n.args[0]))
        raise ValueError('not arithmetic')
    expr = expr.replace('×', '*').replace('÷', '/').replace('^', '**').replace('−', '-')
    return ev(ast.parse(expr, mode='eval'))


def fmt_num(v):
    if isinstance(v, float):
        if v != v or v in (float('inf'), float('-inf')):
            return 'Error'
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return ('%.10g' % v)
    return str(v)


class Calc(App):
    id, name, icon = 'calc', 'Calculator', 'calc'
    c1, c2 = '#5a6878', '#1d2630'
    dark = True
    KEYS = ['C', '(', ')', '÷', '7', '8', '9', '×', '4', '5', '6', '−', '1', '2', '3', '+',
            '⌫', '0', '.', '=']

    def __init__(self, shell, **a):
        super().__init__(shell)
        self.expr, self.result, self.history = '', '', []

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        text(img, (x1 - 20, y0 + 40), self.history[-1] if self.history else '', 14, (150, 165, 180, 255), anchor='rm',
             width=W - 40)
        shown = self.expr or '0'
        size = 46 if len(shown) < 12 else 30
        text(img, (x1 - 20, y0 + 100), shown, size, WHITE, kind='light', anchor='rm', width=W - 40)
        text(img, (x1 - 20, y0 + 150), self.result, 20, (120, 210, 230, 255), anchor='rm', width=W - 40)
        top = y0 + 190
        bw = (W - 20 - 30) // 4
        bh = min(78, (y1 - top - 14 - 40) // 5)
        for i, k in enumerate(self.KEYS):
            r, c = divmod(i, 4)
            bx, by = 10 + c * (bw + 10), top + r * (bh + 10)
            kind = 'primary' if k == '=' else ('danger' if k == 'C' else ('normal' if k.isdigit() or k == '.' else 'dark'))
            button(img, hits, (bx, by, bx + bw, by + bh), k, lambda k=k: self.press(k), kind, size=24, r=bh // 2)

    def press(self, k):
        if k == 'C':
            self.expr, self.result = '', ''
        elif k == '⌫':
            self.expr = self.expr[:-1]
        elif k == '=':
            try:
                v = fmt_num(safe_eval(self.expr))
                self.history.append(self.expr + ' =')
                self.expr, self.result = v, ''
            except Exception:
                self.result = 'Error'
            return
        else:
            self.expr += k
        try:
            self.result = '= ' + fmt_num(safe_eval(self.expr)) if self.expr else ''
        except Exception:
            self.result = ''

    def key(self, ev):
        k, ch = ev.get('key'), ev.get('char', '')
        m = {'*': '×', '/': '÷', '-': '−', 'x': '×'}
        if k == 'enter' or ch == '=':
            self.press('=')
        elif k == 'backspace':
            self.press('⌫')
        elif k == 'delete' or ch in ('c', 'C'):
            self.press('C')
        elif ch and (ch.isdigit() or ch in '.()+'):
            self.press(ch)
        elif ch in m:
            self.press(m[ch])
        else:
            return False
        return True


# ================================================================ Clock

class Clock(App):
    id, name, icon = 'clock', 'Clock', 'clock'
    c1, c2 = '#3a4f6a', '#0b1830'
    dark = True

    def __init__(self, shell, **a):
        super().__init__(shell)
        self.mode = 'clock'
        self.sw_run, self.sw_acc, self.sw_start, self.laps = False, 0.0, 0.0, []
        self.t_end, self.t_len = None, 300

    def sw(self):
        return self.sw_acc + (time.time() - self.sw_start if self.sw_run else 0)

    @staticmethod
    def fmt(secs, cs=False):
        secs = max(0.0, secs)
        h, rem = divmod(int(secs), 3600)
        m, s = divmod(rem, 60)
        out = ('%d:%02d:%02d' % (h, m, s)) if h else ('%02d:%02d' % (m, s))
        return out + ('.%02d' % int((secs % 1) * 100) if cs else '')

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        tabs = [('clock', 'Clock'), ('stopwatch', 'Stopwatch'), ('timer', 'Timer')]
        tw = (W - 40) // 3
        for i, (k, label) in enumerate(tabs):
            button(img, hits, (14 + i * (tw + 6), y0 + 12, 14 + i * (tw + 6) + tw, y0 + 48), label,
                   lambda k=k: setattr(self, 'mode', k), 'primary' if k == self.mode else 'dark', size=14, r=18)
        cy = y0 + 220
        d = ImageDraw.Draw(img)
        if self.mode == 'clock':
            r = 130
            cx = W // 2
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(40, 50, 66, 255), outline=(120, 200, 220, 255), width=3)
            for i in range(60):
                a, b = ui.angle_xy(cx, cy, r - 8, i / 60), ui.angle_xy(cx, cy, r - (20 if i % 5 == 0 else 13), i / 60)
                d.line((a, b), fill=(220, 235, 245, 255), width=3 if i % 5 == 0 else 1)
            t = time.localtime()
            frac = time.time() % 1
            d.line(((cx, cy), ui.angle_xy(cx, cy, r * 0.5, ((t.tm_hour % 12) + t.tm_min / 60) / 12)), fill=WHITE, width=7)
            d.line(((cx, cy), ui.angle_xy(cx, cy, r * 0.75, (t.tm_min + t.tm_sec / 60) / 60)), fill=WHITE, width=4)
            d.line(((cx, cy), ui.angle_xy(cx, cy, r * 0.85, (t.tm_sec + frac) / 60)), fill=(250, 90, 70, 255), width=2)
            d.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), fill=(250, 90, 70, 255))
            text(img, (W // 2, cy + r + 50), self.shell.clock_text(secs=True), 40, kind='light', anchor='mm')
            text(img, (W // 2, cy + r + 92), time.strftime('%A, %B %-d %Y'), 16, (170, 190, 205, 255), anchor='mm')
        elif self.mode == 'stopwatch':
            text(img, (W // 2, cy - 60), self.fmt(self.sw(), True), 60, kind='light', anchor='mm')
            bw = 150
            button(img, hits, (W // 2 - bw - 8, cy, W // 2 - 8, cy + 54), 'Stop' if self.sw_run else 'Start',
                   self.sw_toggle, 'danger' if self.sw_run else 'primary', size=17, r=27)
            button(img, hits, (W // 2 + 8, cy, W // 2 + 8 + bw, cy + 54), 'Lap' if self.sw_run else 'Reset',
                   self.sw_lap, 'dark', size=17, r=27)
            for i, lap in enumerate(reversed(self.laps[-8:])):
                n = len(self.laps) - i
                yy = cy + 90 + i * 34
                text(img, (40, yy), 'Lap %d' % n, 16, (170, 190, 205, 255), anchor='lm')
                text(img, (W - 40, yy), self.fmt(lap, True), 16, WHITE, kind='mono', anchor='rm')
        else:
            left = (self.t_end - time.time()) if self.t_end else self.t_len
            text(img, (W // 2, cy - 60), self.fmt(left), 64, kind='light', anchor='mm')
            if not self.t_end:
                for i, mins in enumerate((1, 3, 5, 10, 15, 30)):
                    r, c = divmod(i, 3)
                    bx = 30 + c * 122
                    button(img, hits, (bx, cy + r * 56, bx + 110, cy + r * 56 + 46), '%d min' % mins,
                           lambda m=mins: setattr(self, 't_len', m * 60), 'primary' if self.t_len == mins * 60 else 'dark',
                           size=15, r=23)
            button(img, hits, (W // 2 - 90, cy + 130, W // 2 + 90, cy + 186), 'Cancel' if self.t_end else 'Start',
                   self.t_toggle, 'danger' if self.t_end else 'primary', size=17, r=28)

    def sw_toggle(self):
        if self.sw_run:
            self.sw_acc += time.time() - self.sw_start
        else:
            self.sw_start = time.time()
        self.sw_run = not self.sw_run

    def sw_lap(self):
        if self.sw_run:
            self.laps.append(self.sw())
        else:
            self.sw_acc, self.laps = 0.0, []

    def t_toggle(self):
        self.t_end = None if self.t_end else time.time() + self.t_len

    def tick(self, dt):
        if self.t_end and time.time() >= self.t_end:
            self.t_end = None
            self.shell.notify("Time's up!", 'Your %s timer finished.' % self.fmt(self.t_len), app='clock')
        return True


# ================================================================ IBBS

IBBS_PY = os.path.expanduser(os.environ.get('IBBS_PY', '~/ssh-crap/ibbs/ibbs-new.py'))
_engine = None
_engine_lock = threading.Lock()


def ibbs_engine():
    """ibbs-new.py, imported once. Neptune's own IBBS package copy works too."""
    global _engine
    with _engine_lock:
        if _engine is None:
            for p in (IBBS_PY, os.path.join(ui.NEPTUNE, 'home', '.neptune', 'packages', 'IBBS', 'files', 'ibbs.py')):
                if os.path.isfile(p):
                    spec = importlib.util.spec_from_file_location('nm_tcf_ibbs', p)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                    _engine = mod
                    break
            else:
                raise RuntimeError("can't find ibbs-new.py (set IBBS_PY)")
    return _engine


def my_hosts():
    names = {'localhost', '127.0.0.1', socket.gethostname()}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        names.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return names


class Board:
    """Talks to the board in settings: a server host:port, or 'local' for this computer's
    board files. If a server on this computer is down, it falls back to the files."""
    lock = threading.Lock()

    def __init__(self, shell):
        self.shell = shell
        self.client = shell.settings.get('ibbs_client') or ''.join(random.choice('0123456789abcdef') for _ in range(12))
        if not shell.settings.get('ibbs_client'):
            shell.set('ibbs_client', self.client)
        self.where = ''

    def name(self):
        s = self.shell.settings
        return s.get('ibbs_name') or ((s.get('user') or 'mobile').lower().replace(' ', '') + '@neptune-mobile-py')

    def call(self, fn, *a):
        eng = ibbs_engine()
        server = (self.shell.settings.get('ibbs_server') or '').strip()
        server = re.sub(r'^https?://', '', server).rstrip('/')
        local = server in ('', 'local')
        with Board.lock:                     # the engine keeps its server in module globals
            if not local:
                eng.configure(server=server, token=self.shell.settings.get('ibbs_token') or '')
                try:
                    out = fn(eng, *a)
                    self.where = server
                    return out
                except (ConnectionError, OSError) as e:
                    if server.rsplit(':', 1)[0] not in my_hosts():
                        raise ConnectionError(str(e))
            eng.configure(server='')
            self.where = 'this computer (local board)'
            return fn(eng, *a)

    def rooms(self):
        return self.call(lambda e: e.list_rooms())

    def end(self, room):
        return self.call(lambda e: e.remote('GET', '/room/%s?since=end' % e.clean_room(room))['offset']
                         if e.SERVER else e.end_offset(room))

    def read(self, room, offset):
        return self.call(lambda e: e.follow(room, offset))

    def post(self, room, txt):
        return self.call(lambda e: e.post(room, self.name(), txt, 'neptune-mobile-py', self.client))

    def mine(self, m):
        return m.get('client') == self.client or (m.get('from') or '').lower() == self.name().lower()


class IBBSWatcher:
    """Background: new messages in the watched room become notifications and a badge."""

    def __init__(self, shell):
        self.shell = shell
        self.board = Board(shell)
        self.offset = None
        self.room = None
        self.next = 0
        self.busy = False
        self.fails = 0
        shell.on('tick', self.tick)

    def tick(self, dt):
        s = self.shell
        if not s.settings.get('ibbs_notify') or self.busy or time.time() < self.next or s.current == 'ibbs':
            return
        self.busy = True
        threading.Thread(target=self.poll, daemon=True).start()

    def poll(self):
        s = self.shell
        room = s.settings.get('ibbs_room') or 'general'
        try:
            if self.offset is None or room != self.room:
                self.offset, self.room = self.board.end(room), room
            else:
                msgs, self.offset = self.board.read(room, self.offset)
                fresh = [m for m in msgs if not self.board.mine(m)]
                if fresh:
                    s.badge('ibbs', s.badges.get('ibbs', 0) + len(fresh))
                    m = fresh[-1]
                    s.notify('#%s · %s' % (room, m.get('from', '?')), str(m.get('text', ''))[:120], app='ibbs')
            self.fails = 0
            self.next = time.time() + 6
        except Exception:
            self.fails += 1
            self.next = time.time() + min(120, 6 * 2 ** self.fails)        # board away: back off
        self.busy = False


class IBBS(App):
    id, name, icon = 'ibbs', 'IBBS', 'bbs'
    c1, c2 = '#e0a030', '#8a3a0a'

    def __init__(self, shell, **a):
        super().__init__(shell)
        self.board = Board(shell)
        self.room = shell.settings.get('ibbs_room') or 'general'
        self.msgs, self.offset, self.status_line = [], 0, 'Connecting...'
        self.tb = TextBox('')
        self.stick = True
        self.alive = True
        self.next, self.fails = 0, 0
        self.lock = threading.Lock()
        threading.Thread(target=self.loop, daemon=True).start()

    def closing(self):
        self.alive = False

    def loop(self):
        while self.alive:
            if time.time() >= self.next:
                self.poll()
            time.sleep(0.25)

    def poll(self):
        room = self.room
        try:
            msgs, off = self.board.read(room, self.offset)
            with self.lock:
                if room == self.room:
                    if self.offset == 0:
                        self.msgs = msgs[-200:]
                    else:
                        self.msgs = (self.msgs + msgs)[-300:]
                    self.offset = off
            if msgs:
                self.shell.dirty = True
            self.status_line = 'On %s as %s' % (self.board.where, self.board.name())
            self.fails = 0
            self.next = time.time() + 2
        except Exception as e:
            self.fails += 1
            self.status_line = '⚠ %s' % e
            self.next = time.time() + min(30, 2 * 2 ** self.fails)
            self.shell.dirty = True

    def draw(self, img, box, hits):
        self.title = 'IBBS · #%s' % self.room
        y = toolbar(img, hits, box, [('#' + self.room, self.pick_room), ('Name', self.set_name),
                                     ('⚙', lambda: self.shell.open('settings', page='ibbs'))])
        ImageDraw.Draw(img).rectangle((0, y, W, y + 22), fill=(250, 240, 220, 255))
        text(img, (10, y + 11), self.status_line, 11, (110, 80, 30, 255), anchor='lm', width=W - 20)
        y += 23
        bottom = input_bar(img, hits, box, self.tb, 'Message #%s' % self.room, self.send)
        top_strip, bottom_strip = img.crop((0, box[1], W, y)), img.crop((0, bottom, W, box[3]))
        with self.lock:
            msgs = list(self.msgs)
        # lay out bubbles bottom-up
        blocks = []
        for m in msgs:
            mine = self.board.mine(m)
            body = ui.wrap(str(m.get('text', '')), 14, W - 120)
            blocks.append((m, mine, body, 30 + 18 * len(body)))
        total = sum(b[3] + 8 for b in blocks)
        view = bottom - y - 8
        self.max_scroll = max(0, total - view)
        if self.stick:
            self.scroll = self.max_scroll
        yy = bottom - 8 - total + (self.max_scroll - self.scroll)
        if not msgs:
            text(img, (W // 2, (y + bottom) // 2), 'No messages in #%s yet. Say hi!' % self.room, 14, DIM, anchor='mm')
        for m, mine, body, h in blocks:
            if yy + h >= y and yy <= bottom:
                bw = max(ui.text_w(ln, 14) for ln in body) if body else 40
                bw = max(bw, ui.text_w(m.get('from', '?'), 11, True) + 70) + 24
                bx = W - 12 - bw if mine else 12
                fill = (210, 236, 255, 255) if mine else WHITE
                rrect(img, (bx, yy, bx + bw, yy + h), 14, fill=fill, outline=(190, 205, 220, 255))
                text(img, (bx + 12, yy + 13), m.get('from', '?'), 11, (150, 80, 10, 255), bold=True, anchor='lm',
                     width=bw - 70)
                stamp = time.strftime('%H:%M', time.localtime(m.get('time', 0)))
                text(img, (bx + bw - 10, yy + 13), stamp, 10, DIM, anchor='rm')
                for i, ln in enumerate(body):
                    text(img, (bx + 12, yy + 32 + i * 18), ln, 14, INK, anchor='lm')
            yy += h + 8
        img.paste(top_strip, (0, box[1]))        # messages scroll under the bars, not over them
        img.paste(bottom_strip, (0, bottom))
        hits.add((0, y, W, bottom), lambda: None)

    def drag(self, dx, dy, x, y):
        changed = super().drag(dx, dy, x, y)
        self.stick = self.scroll >= self.max_scroll - 2
        return changed

    def key(self, ev):
        if ev.get('key') == 'enter':
            self.send()
            return True
        return self.tb.key(ev)

    def send(self):
        t = self.tb.value.strip()
        if not t:
            return
        self.tb = TextBox('')

        def go():
            try:
                self.board.post(self.room, t)
                self.next = 0
                self.stick = True
            except Exception as e:
                self.tb = TextBox(t)
                self.shell.toast('Not sent: %s' % e)
        threading.Thread(target=go, daemon=True).start()

    def switch(self, room):
        room = re.sub(r'[^a-z0-9_-]+', '-', room.strip().lower()).strip('-') or 'general'
        with self.lock:
            self.room, self.msgs, self.offset, self.stick, self.next = room, [], 0, True, 0
        self.shell.set('ibbs_room', room)

    def pick_room(self):
        try:
            rooms = self.board.rooms()
        except Exception as e:
            return self.shell.toast('Rooms: %s' % e)
        opts = ['#' + r for r in rooms] + ['New room...']

        def pick(i):
            if i < len(rooms):
                self.switch(rooms[i])
            else:
                self.shell.prompt('New Room', 'Room name (letters, numbers, - and _)', self.switch, '')
        self.shell.choose('Rooms', opts, pick)

    def set_name(self):
        self.shell.prompt('IBBS Name', 'Your name on the board', lambda n: n.strip() and self.shell.set('ibbs_name', n.strip()),
                          self.board.name())


# ================================================================ Terminal

class Terminal(App):
    id, name, icon = 'terminal', 'Terminal', 'terminal'
    c1, c2 = '#3a3a3a', '#000000'
    dark = True

    def __init__(self, shell, **a):
        super().__init__(shell)
        self.cwd = HOME
        self.lines = ['%s %s Terminal' % (FULLNAME, VERSION), 'Type "help" for commands.', '']
        self.tb = TextBox('')
        self.hist, self.hpos = [], 0
        self.stick = True

    def prompt_str(self):
        return 'C:%s> ' % (('\\' + os.path.relpath(self.cwd, HOME).replace('/', '\\')) if self.cwd != HOME else '\\')

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        ImageDraw.Draw(img).rectangle(box, fill=(8, 12, 10, 255))
        wrapped = []
        for ln in self.lines[-400:]:
            wrapped.extend(ui.wrap(ln, 13, W - 20, kind='mono') or [''])
        wrapped.extend(ui.wrap(self.prompt_str() + self.tb.caret_text(), 13, W - 20, kind='mono'))
        lh = 18
        total = len(wrapped) * lh
        view = y1 - y0 - 16
        self.max_scroll = max(0, total - view)
        if self.stick:
            self.scroll = self.max_scroll
        yy = y0 + 8 - self.scroll
        for ln in wrapped:
            if yy > y1:
                break
            if yy > y0 - lh:
                text(img, (10, yy), ln, 13, (150, 255, 160, 255), kind='mono')
            yy += lh
        hits.add(box, lambda: None)

    def drag(self, dx, dy, x, y):
        c = super().drag(dx, dy, x, y)
        self.stick = self.scroll >= self.max_scroll - 2
        return c

    def key(self, ev):
        k = ev.get('key')
        self.stick = True
        if k == 'enter':
            cmd = self.tb.value
            self.lines.append(self.prompt_str() + cmd)
            self.tb = TextBox('')
            if cmd.strip():
                self.hist.append(cmd)
            self.hpos = len(self.hist)
            try:
                self.run(cmd.strip())
            except Exception as e:
                self.lines.append('error: %s' % e)
            return True
        if k == 'up' and self.hist:
            self.hpos = max(0, self.hpos - 1)
            self.tb = TextBox(self.hist[self.hpos])
            return True
        if k == 'down' and self.hist:
            self.hpos = min(len(self.hist), self.hpos + 1)
            self.tb = TextBox(self.hist[self.hpos] if self.hpos < len(self.hist) else '')
            return True
        if ev.get('ctrl') and k == 'l':
            self.lines = []
            return True
        return self.tb.key(ev)

    def path(self, p):
        p = p.replace('\\', '/')
        full = os.path.normpath(os.path.join(HOME, p.lstrip('/')) if p.startswith('/') else os.path.join(self.cwd, p))
        if not (full + '/').startswith(os.path.abspath(HOME) + '/'):
            raise ValueError('that is outside your home')
        return full

    def run(self, cmd):
        out = self.lines.append
        if not cmd:
            return
        parts = cmd.split()
        c, args = parts[0].lower(), parts[1:]
        rest = cmd[len(parts[0]):].strip()
        if c == 'help':
            for ln in ('help  ls [dir]  cd <dir>  pwd  cat <file>  echo <text> [> file]', 'mkdir <dir>  touch <file>  rm <path>  mv <a> <b>',
                       'edit <file>  open <app>  apps  date  ver  whoami  calc <expr>', 'ibbs [room]  ibbs say <text>  neofetch  clear  exit'):
                out(ln)
        elif c in ('ls', 'dir'):
            d = self.path(args[0]) if args else self.cwd
            for n in sorted(os.listdir(d)):
                if not n.startswith('.'):
                    out(('%s/' % n) if os.path.isdir(os.path.join(d, n)) else n)
        elif c == 'cd':
            d = self.path(args[0]) if args else HOME
            if not os.path.isdir(d):
                raise ValueError('no such folder')
            self.cwd = d
        elif c == 'pwd':
            out(self.prompt_str()[:-2])
        elif c in ('cat', 'type'):
            with open(self.path(rest), encoding='utf-8', errors='replace') as f:
                self.lines.extend(f.read(20000).split('\n'))
        elif c == 'echo':
            if '>' in rest:
                t, fn = rest.split('>', 1)
                with open(self.path(fn.strip()), 'a' if rest.count('>') > 1 else 'w') as f:
                    f.write(t.strip().rstrip('>').strip() + '\n')
            else:
                out(rest)
        elif c in ('mkdir', 'md'):
            os.makedirs(self.path(rest), exist_ok=True)
        elif c == 'touch':
            open(self.path(rest), 'a').close()
        elif c in ('rm', 'del'):
            p = self.path(rest)
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        elif c in ('mv', 'move', 'ren'):
            os.rename(self.path(args[0]), self.path(args[1]))
        elif c == 'edit':
            self.shell.open('notepad', path=self.path(rest))
        elif c == 'open':
            self.shell.open(rest.lower())
        elif c == 'apps':
            out('  '.join(self.shell.order))
        elif c == 'date':
            out(time.strftime('%A %d %B %Y  %H:%M:%S'))
        elif c == 'ver':
            out('%s %s  Build %s' % (FULLNAME, VERSION, self.shell.build))
        elif c == 'whoami':
            out(self.shell.settings.get('user') or 'User')
        elif c == 'calc':
            out(fmt_num(safe_eval(rest)))
        elif c in ('clear', 'cls'):
            self.lines = []
        elif c == 'exit':
            self.shell.close('terminal')
        elif c == 'ibbs':
            b = Board(self.shell)
            if args and args[0] == 'say':
                b.post(self.shell.settings.get('ibbs_room') or 'general', rest[3:].strip())
                out('sent')
            else:
                room = args[0] if args else (self.shell.settings.get('ibbs_room') or 'general')
                msgs, _ = b.read(room, 0)
                for m in msgs[-15:]:
                    out('<%s> %s' % (m.get('from', '?'), m.get('text', '')))
                out('-- #%s on %s' % (room, b.where))
        elif c == 'neofetch':
            for ln in ('   .-""-.    %s' % FULLNAME, '  / COS  \\   Version %s' % VERSION,
                       ' |  (o)   |  Build %s' % self.shell.build, '  \\      /   Python %s' % platform.python_version(),
                       "   '-..-'    Host %s" % socket.gethostname(), '             Apps %d' % len(self.shell.apps)):
                out(ln)
        else:
            out("'%s' is not a command. Try help." % c)


# ================================================================ Bubbles game

class Bubbles(App):
    id, name, icon = 'bubbles', 'Bubbles', 'saver'
    c1, c2 = '#30c0f0', '#0a4aa0'
    dark = True

    def __init__(self, shell, **a):
        super().__init__(shell)
        self.reset()
        self.best = shell.settings.get('bubbles_best', 0)

    def reset(self):
        self.bubs, self.pops, self.score, self.missed, self.spawn, self.over = [], [], 0, 0, 0, False

    def tick(self, dt):
        if self.over:
            return False
        self.spawn -= dt
        if self.spawn <= 0:
            r = random.uniform(18, 38)
            self.bubs.append([random.uniform(r, W - r), H, r, random.uniform(70, 140) + self.score * 2,
                              random.uniform(0, 6.28), random.choice([(120, 210, 255), (160, 255, 200), (255, 180, 230), (255, 230, 140)])])
            self.spawn = max(0.25, 0.9 - self.score * 0.01)
        for b in self.bubs:
            b[1] -= b[3] * dt
            b[4] += dt * 2
        gone = [b for b in self.bubs if b[1] < 150]
        if gone:
            self.missed += len(gone)
            self.bubs = [b for b in self.bubs if b[1] >= 150]
            if self.missed >= 10:
                self.over = True
                if self.score > self.best:
                    self.best = self.score
                    self.shell.set('bubbles_best', self.best)
        self.pops = [p for p in self.pops if time.time() - p[3] < 0.3]
        return True

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        img.alpha_composite(ui.vgrad(W, y1 - y0, (10, 60, 120, 255), (2, 14, 40, 255)), (0, y0))
        lay = ui.Layer(img)
        d = lay.__enter__()
        for b in self.bubs:
            x, y, r = b[0] + math.sin(b[4]) * 6, b[1], b[2]
            if y - r > y1 or y + r < y0:
                continue
            c = b[5]
            d.ellipse((x - r, y - r, x + r, y + r), fill=c + (70,), outline=c + (230,), width=2)
            d.ellipse((x - r * 0.5, y - r * 0.6, x - r * 0.05, y - r * 0.2), fill=(255, 255, 255, 170))
            hits.add((x - r - 6, y - r - 6, x + r + 6, y + r + 6), lambda b=b: self.pop(b))
        for x, y, r, t in self.pops:
            k = (time.time() - t) / 0.3
            d.ellipse((x - r * (1 + k), y - r * (1 + k), x + r * (1 + k), y + r * (1 + k)), outline=(255, 255, 255, int(255 * (1 - k))), width=2)
        lay.__exit__()
        text(img, (16, y0 + 22), 'Score %d' % self.score, 18, bold=True, anchor='lm', shadow=True)
        text(img, (W - 16, y0 + 22), 'Missed %d/10   Best %d' % (self.missed, self.best), 13, anchor='rm', shadow=True)
        if self.over:
            ui.glass(img, (40, y0 + 200, W - 40, y0 + 380), self.shell.tint, 0.5, r=20)
            text(img, (W // 2, y0 + 240), 'Game Over', 28, kind='serif', anchor='mm', shadow=True)
            text(img, (W // 2, y0 + 282), 'You popped %d bubbles' % self.score, 16, anchor='mm', shadow=True)
            button(img, hits, (W // 2 - 80, y0 + 310, W // 2 + 80, y0 + 356), 'Play Again', self.reset, 'primary', r=23)

    def pop(self, b):
        if b in self.bubs:
            self.bubs.remove(b)
            self.pops.append((b[0], b[1], b[2], time.time()))
            self.score += 1


# ================================================================ Paint

class Paint(App):
    id, name, icon = 'paint', 'Paint', 'paint'
    c1, c2 = '#e05a8a', '#7a1a5a'
    COLORS = [(20, 20, 20), (230, 40, 40), (250, 150, 30), (250, 220, 40), (60, 180, 70), (40, 140, 230),
              (130, 70, 200), (255, 255, 255)]

    def __init__(self, shell, **a):
        super().__init__(shell)
        self.canvas = None
        self.color, self.size, self.last = self.COLORS[0], 6, None

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        y = toolbar(img, hits, box, [('Clear', self.clear), ('Size %d' % self.size, self.cycle), ('Save', self.save, 'primary')])
        pal_y = y1 - 54
        if self.canvas is None or self.canvas.size != (W, pal_y - y):
            self.canvas = Image.new('RGBA', (W, pal_y - y), (255, 255, 255, 255))
        self.cbox = (0, y, W, pal_y)
        img.alpha_composite(self.canvas, (0, y))
        hits.add(self.cbox, self.dot)
        ImageDraw.Draw(img).rectangle((0, pal_y, W, y1), fill=(232, 238, 245, 255))
        for i, c in enumerate(self.COLORS):
            cx = 30 + i * 50
            sel = c == self.color
            ImageDraw.Draw(img).ellipse((cx - 17, pal_y + 10, cx + 17, pal_y + 44), fill=c + (255,),
                                        outline=(40, 120, 220, 255) if sel else (120, 130, 140, 255), width=4 if sel else 1)
            hits.add((cx - 24, pal_y, cx + 24, y1), lambda c=c: setattr(self, 'color', c))

    def to_canvas(self, x, y):
        return x, y - self.cbox[1]

    def dot(self):
        p = self.shell.ptr_last
        if p:
            cx, cy = self.to_canvas(*p)
            ImageDraw.Draw(self.canvas).ellipse((cx - self.size / 2, cy - self.size / 2, cx + self.size / 2, cy + self.size / 2),
                                                fill=self.color + (255,))

    def drag(self, dx, dy, x, y):
        if not hasattr(self, 'cbox') or not (self.cbox[1] <= y < self.cbox[3]):
            self.last = None
            return False
        cx, cy = self.to_canvas(x, y)
        a = self.last or (cx - dx, cy - dy)
        d = ImageDraw.Draw(self.canvas)
        d.line((a, (cx, cy)), fill=self.color + (255,), width=self.size)
        d.ellipse((cx - self.size / 2, cy - self.size / 2, cx + self.size / 2, cy + self.size / 2), fill=self.color + (255,))
        self.last = (cx, cy)
        return True

    def release(self, x, y):
        self.last = None

    def clear(self):
        self.canvas = None

    def cycle(self):
        sizes = [2, 4, 6, 10, 16, 24]
        self.size = sizes[(sizes.index(self.size) + 1) % len(sizes)] if self.size in sizes else 6

    def save(self):
        fn = os.path.join(HOME, 'Pictures', time.strftime('Painting %Y-%m-%d %H.%M.%S.png'))
        self.canvas.convert('RGB').save(fn)
        self.shell.toast('Saved to Pictures')


# ================================================================ Photos

class Photos(App):
    id, name, icon = 'photos', 'Photos', 'pictures'
    c1, c2 = '#40b080', '#0a5a3a'
    EXT = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')

    def __init__(self, shell, path=None, **a):
        super().__init__(shell)
        self.view = path
        self.thumbs = {}

    def args(self, path=None, **a):
        if path:
            self.view = path

    def pics(self):
        d = os.path.join(HOME, 'Pictures')
        return sorted((os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith(self.EXT)),
                      key=os.path.getmtime, reverse=True)

    def thumb(self, p, s):
        key = (p, s, os.path.getmtime(p))
        if key not in self.thumbs:
            try:
                im = Image.open(p).convert('RGBA')
                k = max(s / im.width, s / im.height)
                im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.BILINEAR)
                l, t = (im.width - s) // 2, (im.height - s) // 2
                self.thumbs[key] = im.crop((l, t, l + s, t + s))
            except Exception:
                self.thumbs[key] = ui.icons.get('image', s)
        return self.thumbs[key]

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        if self.view:
            self.title = os.path.basename(self.view)
            ImageDraw.Draw(img).rectangle(box, fill=(10, 10, 12, 255))
            try:
                im = Image.open(self.view).convert('RGBA')
                k = min((W - 10) / im.width, (y1 - y0 - 70) / im.height, 4)
                im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
                img.alpha_composite(im, ((W - im.width) // 2, y0 + (y1 - y0 - 60 - im.height) // 2))
            except Exception as e:
                text(img, (W // 2, (y0 + y1) // 2), "Can't show this picture: %s" % e, 14, anchor='mm', width=W - 30)
            button(img, hits, (14, y1 - 52, 120, y1 - 12), '← All', lambda: setattr(self, 'view', None), 'dark', r=20)
            button(img, hits, (W - 160, y1 - 52, W - 14, y1 - 12), 'Wallpaper', self.as_wall, 'primary', r=20)
            return
        self.title = 'Photos'
        pics = self.pics()
        s = (W - 8) // 3
        if not pics:
            text(img, (W // 2, y0 + 80), 'No pictures yet', 16, DIM, anchor='mm')
            text(img, (W // 2, y0 + 106), 'Paint, and F5 screenshots, save here.', 13, DIM, anchor='mm')
        rows = (len(pics) + 2) // 3
        self.max_scroll = max(0, rows * (s + 2) - (y1 - y0))
        for i, p in enumerate(pics):
            r, c = divmod(i, 3)
            px, py = 2 + c * (s + 2), y0 + 2 + r * (s + 2) - self.scroll
            if py > y1 or py + s < y0:
                continue
            gfx.alpha_composite_clip(img, self.thumb(p, s), px, py)
            hits.add((px, py, px + s, py + s), lambda p=p: setattr(self, 'view', p))

    def as_wall(self):
        self.shell.toast('Picture wallpapers come in the next build; try Settings > Background')

    def back(self):
        if self.view:
            self.view = None
            return True
        return False


# ================================================================ Druman (music)

class Druman(App):
    id, name, icon = 'druman', 'Druman', 'druman'
    c1, c2 = '#7a4ad0', '#2a0a6a'
    dark = True
    EXT = ('.mp3', '.ogg', '.wav', '.flac')

    def __init__(self, shell, path=None, **a):
        super().__init__(shell)
        self.track, self.playing, self.paused = None, False, False
        if path:
            self.play(path)

    def args(self, path=None, **a):
        if path:
            self.play(path)

    def songs(self):
        d = os.path.join(HOME, 'Music')
        return sorted(os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith(self.EXT))

    def mixer(self):
        import pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        return pygame.mixer

    def play(self, p):
        try:
            m = self.mixer()
            m.music.load(p)
            m.music.set_volume(self.shell.settings.get('volume', 80) / 100.0)
            m.music.play()
            self.track, self.playing, self.paused = p, True, False
        except Exception as e:
            self.shell.toast("Can't play: %s" % e)

    def toggle(self):
        m = self.mixer()
        if not self.track:
            s = self.songs()
            return self.play(s[0]) if s else self.shell.toast('Put music in Music/ first')
        if self.paused:
            m.music.unpause()
        else:
            m.music.pause()
        self.paused = not self.paused

    def closing(self):
        try:
            self.mixer().music.stop()
        except Exception:
            pass

    def tick(self, dt):
        return self.playing and not self.paused

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        img.alpha_composite(ui.vgrad(W, y1 - y0, (60, 30, 110, 255), (10, 6, 30, 255)), (0, y0))
        # the visualiser: a fake spectrum that moves while music plays
        t = time.time()
        lay = ui.Layer(img)
        d = lay.__enter__()
        for i in range(24):
            amp = (0.3 + 0.7 * abs(math.sin(t * (1.3 + i * 0.17) + i))) if (self.playing and not self.paused) else 0.08
            h = int(amp * 120)
            x = 20 + i * 16
            d.rectangle((x, y0 + 170 - h, x + 11, y0 + 170), fill=(160 + i * 4, 120, 255, 230))
        lay.__exit__()
        name = os.path.basename(self.track) if self.track else 'Nothing playing'
        text(img, (W // 2, y0 + 200), name, 16, bold=True, anchor='mm', width=W - 40)
        button(img, hits, (W // 2 - 60, y0 + 222, W // 2 + 60, y0 + 270),
               '⏸ Pause' if (self.playing and not self.paused) else '▶ Play', self.toggle, 'primary', r=24)
        y = y0 + 290
        songs = self.songs()
        if not songs:
            text(img, (W // 2, y + 30), 'No music yet: copy songs into %s' % os.path.join('home', 'Music'), 13,
                 (190, 180, 220, 255), anchor='mm', width=W - 30)
        for p in songs[:9]:
            on = p == self.track
            rrect(img, (12, y, W - 12, y + 42), 10, fill=(255, 255, 255, 50 if on else 18))
            text(img, (26, y + 21), ('♫ ' if on else '') + os.path.basename(p), 14, anchor='lm', width=W - 60)
            hits.add((12, y, W - 12, y + 42), lambda p=p: self.play(p))
            y += 48


# ================================================================ Task Manager

class TaskMgr(App):
    id, name, icon = 'taskmgr', 'Task Manager', 'taskmgr'
    c1, c2 = '#30a050', '#0a4a1a'

    def __init__(self, shell, **a):
        super().__init__(shell)
        self.cpu = []
        self.last = (time.time(), time.process_time())

    def tick(self, dt):
        now, pt = time.time(), time.process_time()
        if now - self.last[0] >= 1:
            self.cpu.append(min(100, 100 * (pt - self.last[1]) / (now - self.last[0])))
            self.cpu = self.cpu[-60:]
            self.last = (now, pt)
            return True
        return False

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        text(img, (16, y0 + 22), 'RUNNING APPS', 12, DIM, bold=True, anchor='lm')
        y = y0 + 38
        for a in reversed(self.shell.stack):
            cls = self.shell.apps[a]
            img.alpha_composite(ui.bubble_icon(cls.icon, 34, cls.c1, cls.c2), (14, y + 6))
            text(img, (58, y + 23), cls.name, 15, INK, anchor='lm')
            if a != 'taskmgr':
                button(img, hits, (W - 110, y + 8, W - 14, y + 40), 'End Task', lambda a=a: self.shell.close(a), 'danger',
                       size=13, r=8)
            y += 48
        y += 10
        text(img, (16, y), 'PERFORMANCE (this shell)', 12, DIM, bold=True, anchor='lm')
        gy0, gy1 = y + 14, y + 134
        rrect(img, (14, gy0, W - 14, gy1), 8, fill=(10, 30, 20, 255))
        d = ImageDraw.Draw(img)
        for i in range(1, 4):
            d.line((14, gy0 + i * 30, W - 14, gy0 + i * 30), fill=(30, 80, 50, 255))
        pts = [(W - 14 - (len(self.cpu) - 1 - i) * ((W - 28) / 59), gy1 - v / 100 * (gy1 - gy0)) for i, v in enumerate(self.cpu)]
        if len(pts) > 1:
            d.line(pts, fill=(80, 255, 120, 255), width=2)
        text(img, (20, gy0 + 12), 'CPU %d%%' % (self.cpu[-1] if self.cpu else 0), 12, (120, 255, 150, 255), anchor='lm')
        try:
            import resource
            mem = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            text(img, (16, gy1 + 22), 'Memory (peak) %.0f MB    Threads %d' % (mem, threading.active_count()), 13, INK, anchor='lm')
        except Exception:
            pass


# ================================================================ Settings

class Settings(App):
    id, name, icon = 'settings', 'Settings', 'settings'
    c1, c2 = '#7a8a9a', '#2a3a4a'
    PAGES = [('personalize', 'Personalize', 'personalize', 'Glass colour and transparency'),
             ('background', 'Background', 'image', 'Auroras and COS bubbles'),
             ('lock', 'Lock Screen', 'lock', 'PIN and locking at start'),
             ('sounds', 'Sounds', 'music', 'Sound effects and volume'),
             ('time', 'Date and Time', 'clock', '12 or 24 hours'),
             ('ibbs', 'IBBS', 'bbs', 'Board, name and alerts'),
             ('about', 'About and Reset', 'info', 'Version, build, start over')]

    def __init__(self, shell, page=None, **a):
        super().__init__(shell)
        self.page = page

    def args(self, page=None, **a):
        if page:
            self.page = page

    def back(self):
        if self.page:
            self.page, self.scroll = None, 0
            return True
        return False

    def row(self, img, hits, y, label, value=None, fn=None, sub=None):
        text(img, (18, y + (18 if sub else 26)), label, 15, INK, anchor='lm', width=W - 150)
        if sub:
            text(img, (18, y + 38), sub, 12, DIM, anchor='lm', width=W - 150)
        if value is not None:
            text(img, (W - 34 if fn else W - 18, y + 26), str(value), 14, DIM, anchor='rm', width=150)
        if fn:
            text(img, (W - 16, y + 26), '›', 22, DIM, anchor='rm')
            hits.add((0, y, W, y + 52), fn)
        ImageDraw.Draw(img).line((18, y + 52, W, y + 52), fill=LINE)
        return y + 53

    def switch(self, img, hits, y, label, key, sub=None):
        on = bool(self.shell.settings.get(key))
        text(img, (18, y + (18 if sub else 26)), label, 15, INK, anchor='lm', width=W - 100)
        if sub:
            text(img, (18, y + 38), sub, 12, DIM, anchor='lm', width=W - 100)
        ui.toggle(img, hits, (0, y, W - 16, y + 52), on, lambda: self.shell.set(key, not on))
        ImageDraw.Draw(img).line((18, y + 52, W, y + 52), fill=LINE)
        return y + 53

    def head(self, img, y, label):
        text(img, (18, y + 16), label.upper(), 12, (60, 110, 160, 255), bold=True, anchor='lm')
        return y + 30

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        s = self.shell.settings
        y = y0 - self.scroll
        p = self.page
        self.title = dict((k, n) for k, n, _, _ in self.PAGES).get(p, 'Settings')
        start = y
        if not p:
            for k, n, ic, sub in self.PAGES:
                img.alpha_composite(ui.icons.get(ic, 30), (16, y + 11))
                text(img, (60, y + 18), n, 16, INK, anchor='lm')
                text(img, (60, y + 38), sub, 12, DIM, anchor='lm')
                text(img, (W - 16, y + 27), '›', 22, DIM, anchor='rm')
                hits.add((0, max(y, y0), W, y + 56), lambda k=k: self.go(k))
                ImageDraw.Draw(img).line((60, y + 56, W, y + 56), fill=LINE)
                y += 57
        elif p == 'personalize':
            y = self.head(img, y + 4, 'Glass colour')
            sw = (W - 36 - 7 * 8) // 8
            for i, (n, rgb) in enumerate(ui.GLASS):
                r, c = divmod(i, 8)
                bx, by = 18 + c * (sw + 8), y + 6 + r * (sw + 22)
                ui.glass(img, (bx, by, bx + sw, by + sw), rgb, 0.75, r=10, blur=False)
                if n == s['glass']:
                    rrect(img, (bx - 3, by - 3, bx + sw + 3, by + sw + 3), 12, outline=(30, 110, 220, 255), width=3)
                text(img, (bx + sw // 2, by + sw + 9), n, 9, DIM, anchor='mm', width=sw + 8)
                hits.add((bx, by, bx + sw, by + sw + 18), lambda n=n: self.shell.set('glass', n))
            y += 2 * (sw + 22) + 16
            y = self.head(img, y, 'Transparency')
            levels = [('Clear', 0.2), ('Light', 0.32), ('Normal', 0.42), ('Rich', 0.58), ('Solid', 0.8)]
            bw = (W - 36 - 4 * 6) // 5
            for i, (n, v) in enumerate(levels):
                bx = 18 + i * (bw + 6)
                button(img, hits, (bx, y + 4, bx + bw, y + 40), n, lambda v=v: self.shell.set('intensity', v),
                       'primary' if abs(s['intensity'] - v) < 0.01 else 'normal', size=13, r=8)
            y += 56
            y = self.head(img, y, 'Preview')
            ui.glass(img, (18, y + 6, W - 18, y + 76), self.shell.tint, s['intensity'], r=14)
            text(img, (W // 2, y + 41), 'This is your glass.', 15, anchor='mm', shadow=True)
            y += 90
            y = self.switch(img, hits, y, 'Animations', 'animations', 'Apps zoom open and closed')
        elif p == 'background':
            tw, th = 84, 150
            cols = 4
            gap = (W - cols * tw) // (cols + 1)
            for i, n in enumerate(ui.WALLPAPERS):
                r, c = divmod(i, cols)
                bx, by = gap + c * (tw + gap), y + 14 + r * (th + 34)
                if by > y1 or by + th < y0 - 40:
                    continue
                th_img = ui.wall_thumb(n, tw, th).copy()
                th_img.putalpha(gfx.rounded_mask(tw, th, 8))
                gfx.alpha_composite_clip(img, th_img, bx, by)
                if n == s['wallpaper']:
                    rrect(img, (bx - 3, by - 3, bx + tw + 3, by + th + 3), 10, outline=(30, 110, 220, 255), width=3)
                text(img, (bx + tw // 2, by + th + 12), n, 10, DIM, anchor='mm', width=tw + gap - 4)
                hits.add((bx, by, bx + tw, by + th + 24), lambda n=n: self.pick_wall(n))
            y += 14 + ((len(ui.WALLPAPERS) + cols - 1) // cols) * (th + 34)
        elif p == 'lock':
            y = self.switch(img, hits, y, 'Lock when Neptune starts', 'lock_on_start')
            y = self.row(img, hits, y, 'PIN', 'On' if s.get('pin') else 'Off', self.set_pin, 'Numbers only, 4 to 8 digits')
            if s.get('pin'):
                y = self.row(img, hits, y, 'Remove PIN', None, lambda: self.shell.set('pin', ''))
            y = self.row(img, hits, y, 'Lock now', None, self.shell.lock)
            text(img, (18, y + 20), 'The PIN locks the look, not real security.', 12, DIM, anchor='lm')
            y += 40
        elif p == 'sounds':
            y = self.switch(img, hits, y, 'Sound effects', 'sounds', 'Boot, notifications, warnings')
            text(img, (18, y + 26), 'Volume', 15, INK, anchor='lm')
            vol = s.get('volume', 80)
            button(img, hits, (W - 160, y + 10, W - 120, y + 42), '−', lambda: self.shell.set('volume', max(0, vol - 10)), size=18)
            text(img, (W - 90, y + 26), '%d%%' % vol, 15, INK, anchor='mm')
            button(img, hits, (W - 58, y + 10, W - 18, y + 42), '+', lambda: self.shell.set('volume', min(100, vol + 10)), size=18)
            y += 53
            y = self.row(img, hits, y, 'Play a test sound', None, lambda: self.shell.sound('notify'))
        elif p == 'time':
            y = self.switch(img, hits, y, '24-hour clock', 'clock24')
            y = self.row(img, hits, y, 'Now', self.shell.clock_text(secs=True))
            y = self.row(img, hits, y, 'Time zone', time.strftime('%Z'))
            y = self.switch(img, hits, y, 'Boot screen', 'boot_screen', 'Show the COS orb when starting')
        elif p == 'ibbs':
            y = self.row(img, hits, y, 'Board', s.get('ibbs_server') or 'local', self.edit('ibbs_server', 'Board server (host:port) or "local"'),
                         '"local" = this computer\'s board files')
            y = self.row(img, hits, y, 'Your name', s.get('ibbs_name') or Board(self.shell).name(),
                         self.edit('ibbs_name', 'Your name on the board'))
            y = self.row(img, hits, y, 'Token', '••••' if s.get('ibbs_token') else 'none',
                         self.edit('ibbs_token', 'Shared password (IBBS_TOKEN), if the board has one', secret=True))
            y = self.row(img, hits, y, 'Room to watch', '#' + (s.get('ibbs_room') or 'general'),
                         self.edit('ibbs_room', 'Room name'))
            y = self.switch(img, hits, y, 'Notify me about new messages', 'ibbs_notify')
            y = self.row(img, hits, y, 'Use the local board', None, lambda: self.shell.set('ibbs_server', 'local'))
        elif p == 'about':
            lg = ui.logo(44)
            img.alpha_composite(lg, (W // 2 - lg.size[0] // 2, y + 6))
            y += lg.size[1] + 6
            text(img, (W // 2, y + 6), FULLNAME, 22, INK, kind='serif', anchor='mm')
            y += 30
            y = self.row(img, hits, y, 'Version', VERSION)
            y = self.row(img, hits, y, 'Build', self.shell.build)
            y = self.row(img, hits, y, 'Python', platform.python_version())
            y = self.row(img, hits, y, 'Your files', os.path.relpath(HOME, ui.MOBILE))
            y = self.row(img, hits, y, 'Run setup again', None, self.shell.oobe)
            y = self.row(img, hits, y, 'Reset all settings', None, lambda: self.shell.confirm(
                'Reset', 'Put every setting back to how it came? Your files stay.', self.reset, 'Reset', True))
        self.max_scroll = max(0, (y + self.scroll - y0) - (y1 - y0) + 20)

    def go(self, k):
        self.page, self.scroll = k, 0

    def pick_wall(self, n):
        self.shell.set('wallpaper', n)
        self.shell.blur_cache = None

    def edit(self, key, msg, secret=False):
        return lambda: self.shell.prompt(self.title, msg, lambda v: self.shell.set(key, v.strip()),
                                         '' if secret else self.shell.settings.get(key, ''), secret)

    def set_pin(self):
        def done(v):
            v = v.strip()
            if not (v.isdigit() and 4 <= len(v) <= 8):
                return self.shell.toast('A PIN is 4 to 8 numbers')
            self.shell.set('pin', v)
            self.shell.toast('PIN set')
        self.shell.prompt('PIN', 'New PIN (4 to 8 numbers)', done, '', True)

    def reset(self):
        from .shell import DEFAULTS
        keep = {'oobe': True, 'user': self.shell.settings.get('user')}
        for k, v in DEFAULTS.items():
            self.shell.set(k, keep.get(k, v))
        self.shell.toast('Settings reset')


# ================================================================ Help & About

class Help(App):
    id, name, icon = 'help', 'Help', 'help'
    c1, c2 = '#4ab0d0', '#0a4a7a'
    TOPICS = [
        ('Getting around', 'The bar at the bottom is your nav bar: ◁ is Back (Esc does the same), the COS orb is '
                           'Home and □ shows Recent Apps. Hold the orb for Lock, Restart and Shut Down.'),
        ('Your finger is the mouse', 'Click to tap, drag to scroll or swipe, and hold the button to long-press. '
                                     'The scroll wheel scrolls too.'),
        ('Notifications', 'Drag down from the very top of the screen (or anywhere on an empty home screen) to pull '
                          'down the shade: quick toggles, brightness and your notifications.'),
        ('Recent Apps', 'Swipe a card sideways to find an app, tap it to switch, flick it up or press ✕ to close it.'),
        ('Home screen', 'Hold an app icon to add it to the dock or take it off.'),
        ('IBBS', 'Chat with the TCF bulletin board. With the board server down, Neptune Mobile uses this '
                 'computer\'s board files instead, so chat still works here.'),
        ('Keys', 'Esc Back · Alt+Home Home · F5 screenshot to Pictures · Ctrl+Q quit. '
                 'Type straight into Notepad, Terminal, IBBS and Calculator.'),
    ]

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        y = y0 + 10 - self.scroll
        for title, body in self.TOPICS:
            lines = ui.wrap(body, 14, W - 60)
            h = 44 + 19 * len(lines)
            rrect(img, (12, y, W - 12, y + h), 12, fill=WHITE, outline=LINE)
            text(img, (28, y + 22), title, 16, (20, 80, 140, 255), bold=True, anchor='lm')
            for i, ln in enumerate(lines):
                text(img, (28, y + 46 + i * 19), ln, 14, INK, anchor='lm')
            y += h + 10
        self.max_scroll = max(0, y + self.scroll - y1 + 10)


class About(App):
    id, name, icon = 'about', 'About', 'info'
    c1, c2 = '#2c6480', '#0c2084'

    def draw(self, img, box, hits):
        x0, y0, x1, y1 = box
        img.alpha_composite(ui.wall(self.shell.settings['wallpaper']).crop(box), (0, y0))
        lg = ui.logo(80)
        img.alpha_composite(lg, (W // 2 - lg.size[0] // 2, y0 + 30))
        y = y0 + 40 + lg.size[1]
        text(img, (W // 2, y), 'COS Neptune', 34, kind='serif', anchor='mm', shadow=True)
        text(img, (W // 2, y + 38), 'MOBILE · PYTHON EDITION', 13, (190, 240, 245, 255), bold=True, anchor='mm')
        ui.glass(img, (24, y + 70, W - 24, y + 230), self.shell.tint, self.shell.settings['intensity'], r=16)
        rows = [('Version', VERSION), ('Build', self.shell.build), ('Owner', self.shell.settings.get('user') or 'User'),
                ('Python', platform.python_version()), ('Drawn with', 'nep.gfx (PIL) + pygame')]
        for i, (k, v) in enumerate(rows):
            text(img, (44, y + 98 + i * 28), k, 14, (210, 230, 240, 255), anchor='lm')
            text(img, (W - 44, y + 98 + i * 28), v, 14, anchor='rm', shadow=True)
        text(img, (W // 2, y1 - 30), 'Also on the web and Android: ~/cos/neptune/mobile', 11, (210, 225, 235, 255),
             anchor='mm', shadow=True)


ALL = [Files, Notepad, Calc, Clock, IBBS, Paint, Photos, Druman, Terminal, Bubbles, TaskMgr, Settings, Help, About]


def register_all(shell):
    for cls in ALL:
        shell.register(cls)
    shell.ibbs_watcher = IBBSWatcher(shell)
