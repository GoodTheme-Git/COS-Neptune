"""CEAP: the COS Explorer Application Platform (say it "cheap").

Two halves, from good's concept sheet (Documents/ceap_concept.png):

1. CEAP:Main, an explorer window: Back / Forward / Up / Refresh, an address
   bar and search, a Directory tree, the Exploring Space in three modes
   (Icon.Arrange: drag icons anywhere, Icon.Standard: a fixed grid,
   Text.Display: a numbered list), Overview (Text.over / Dir.over) and
   Metadata panes, and Code.Exe.Explore, a terminal docked at the bottom
   that follows the folder you're in.

2. CEAP apps: plain-text .ceap files (type "CEAPApp") built from those
   windows, no Python needed. A whole app can be this:

       app "Hello"
       win.CEAP:Note "Hello"
           text "Hello!"
           input name "Text input..."
           button "Hi" -> say "Hi, {name}!"
           button "Good day." -> text "Good day, {name}."
           button "Hello" -> notify "Hello from CEAP"

   Apps in home/.neptune/ceap/ show up in APPS; any .ceap opens from
   Explorer. The language is described in LANGUAGE below (and in Help).
"""
import json
import os
import shlex
import shutil
import time

from PIL import Image

from .. import gfx, icons, theme
from ..app import App
from .dialogs import message
from .files import Explorer, _human, _kind, inside_home
from .text import Terminal

CEAP_DIR = os.path.join(theme.STATE, 'ceap')
LAYOUT_FILE = os.path.join(CEAP_DIR, 'arrange.json')
EXT = '.ceap'
MODES = [('arrange', 'Icon.Arrange'), ('standard', 'Icon.Standard'), ('text', 'Text.Display')]

# -- the CEAP log --------------------------------------------------------------------
# Everything CEAP apps do is written here: windows opening, buttons, each action
# and what it did, variables changing, mistakes, and the app's own `log` lines.
# The App Maker shows it live; it is also appended to home/.neptune/ceap/ceap.log.
import collections
LOG_FILE = os.path.join(theme.STATE, 'ceap', 'ceap.log')
LOG = collections.deque(maxlen=2000)        # (time, app name, level, text)
LOG_MAX_BYTES = 512 * 1024


def log(app, level, text):
    """level: info, action, var, user (from the app's own log action), warn, error."""
    t = time.time()
    LOG.append((t, str(app), level, str(text)))
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX_BYTES:
            os.replace(LOG_FILE, LOG_FILE + '.old')
        with open(LOG_FILE, 'a') as f:
            f.write('%s  %-6s  %s: %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t)), level, app, text))
    except OSError:
        pass


LANGUAGE = """CEAP apps are text files ending in .ceap. One statement per line; # starts a comment.

App lines (once, at the top):
  app "Name"            the app's name (APPS menu, title bars)
  icon <name>           a Neptune icon: ceap, notepad, folder, music, calc, ...
  version 1.0   author "you"   description "What it does"

Windows (each starts a block; indent what belongs to it):
  win.CEAP:Note "Title"         a small window with text, inputs and buttons
      text "Hello, {name}!"     a line of text; {var}, {date}, {time}, {user} fill in
      input <var> "hint"        a text box; what you type goes in {var}
      button "Label" -> <actions>
      size <w> <h>              optional
  win.CEAP:Main "Title"         a CEAP explorer
      root <folder>             where it starts (~ and Neptune paths work)
      view arrange | standard | text
      terminal on | off

Actions (after ->, separated by ;):
  say "text"          pop up a message          text "text"     change the window's text
  set <var> "value"   change a variable          notify "text"   a notification balloon
  open <app>          open a Neptune program     open file "path"   open url "https://..."
  explore "folder"    open a CEAP:Main there     run "command"   run it in a Terminal window
  sound notify|warn   play a sound               close           close this window
  log "text"          write a line to the CEAP log (App Maker > Log, home/.neptune/ceap/ceap.log)
"""

TEMPLATE = '''# A CEAP app. Save it and CEAP picks the change up by itself.
app "%(name)s"
icon ceap
version 1.0
author "%(author)s"
description "A new CEAP app."

win.CEAP:Note "%(name)s"
    text "Hello from %(name)s!"
    input name "Your name..."
    button "Hi" -> say "Hi, {name}!"
    button "What time is it?" -> text "It is {time} on {date}."
    button "Explore" -> explore "~/cos/neptune/home"
'''


# =============================================================================
# the language
# =============================================================================
class CeapError(Exception):
    def __init__(self, line, msg):
        super().__init__('line %d: %s' % (line, msg) if line else msg)
        self.line = line


class CeapApp:
    """A parsed .ceap file."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.stem = os.path.splitext(os.path.basename(path))[0]
        self.meta = {'app': self.stem, 'icon': 'ceap', 'version': '', 'author': '', 'description': ''}
        self.windows = []
        self.error = ''
        self.mtime = 0
        try:
            self.mtime = os.path.getmtime(self.path)
            with open(self.path, encoding='utf-8', errors='replace') as f:
                self.parse(f.read())
        except CeapError as e:
            self.error = str(e)
        except OSError as e:
            self.error = 'cannot read: %s' % e.strerror

    @property
    def name(self):
        return str(self.meta.get('app') or self.stem)

    @staticmethod
    def split(line, n):
        lex = shlex.shlex(line, posix=True, punctuation_chars=';')
        lex.whitespace_split = True
        lex.commenters = ''
        try:
            return list(lex)
        except ValueError as e:
            raise CeapError(n, 'unfinished quote (%s)' % e)

    def parse(self, src):
        cur = None
        for n, raw in enumerate(src.splitlines(), 1):
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            toks = self.split(stripped, n)
            word = toks[0]
            indented = raw[:1] in (' ', '\t')
            if word.startswith('win.CEAP:'):
                kind = word.split(':', 1)[1].lower()
                if kind not in ('note', 'main'):
                    raise CeapError(n, 'unknown window %s (use win.CEAP:Note or win.CEAP:Main)' % word)
                cur = {'kind': kind, 'title': toks[1] if len(toks) > 1 else self.name, 'items': [], 'line': n,
                       'root': theme.HOME, 'view': 'standard', 'terminal': False, 'size': None}
                self.windows.append(cur)
                continue
            if not indented or cur is None:
                if word in ('app', 'icon', 'version', 'author', 'description'):
                    if len(toks) < 2:
                        raise CeapError(n, '%s needs a value' % word)
                    self.meta[word] = ' '.join(toks[1:])
                    continue
                if cur is None:
                    raise CeapError(n, 'unknown statement "%s" (did you mean to put it in a window?)' % word)
            self.window_stmt(cur, word, toks, n)
        if not self.windows:
            raise CeapError(0, 'no windows: add a win.CEAP:Note or win.CEAP:Main block')

    def window_stmt(self, w, word, toks, n):
        if word == 'size':
            try:
                w['size'] = (max(200, int(toks[1])), max(120, int(toks[2])))
            except (IndexError, ValueError):
                raise CeapError(n, 'size needs two numbers: size 360 200')
            return
        if w['kind'] == 'main':
            if word == 'root' and len(toks) > 1:
                w['root'] = resolve_path(' '.join(toks[1:]))
            elif word == 'view' and len(toks) > 1 and toks[1] in dict(MODES):
                w['view'] = toks[1]
            elif word == 'terminal' and len(toks) > 1:
                w['terminal'] = toks[1].lower() in ('on', 'yes', 'true', '1')
            else:
                raise CeapError(n, 'win.CEAP:Main understands root, view, terminal and size, not "%s"' % word)
            return
        if word == 'text':
            w['items'].append(('text', ' '.join(toks[1:])))
        elif word == 'input':
            if len(toks) < 2 or not toks[1].isidentifier():
                raise CeapError(n, 'input needs a variable name: input name "hint"')
            w['items'].append(('input', toks[1], ' '.join(toks[2:])))
        elif word == 'button':
            if '->' not in toks or toks.index('->') < 2:
                raise CeapError(n, 'button needs a label and actions: button "Hi" -> say "Hi!"')
            i = toks.index('->')
            label = ' '.join(toks[1:i])
            w['items'].append(('button', label, self.actions(toks[i + 1:], n)))
        else:
            raise CeapError(n, 'win.CEAP:Note understands text, input, button and size, not "%s"' % word)

    VERBS = {'say': 1, 'text': 1, 'set': 2, 'notify': 1, 'open': 1, 'explore': 1, 'run': 1, 'sound': 1, 'close': 0,
             'log': 1}

    def actions(self, toks, n):
        out, cur = [], []
        for t in toks + [';']:
            if t == ';':
                if cur:
                    verb = cur[0]
                    if verb not in self.VERBS:
                        raise CeapError(n, 'unknown action "%s" (try say, text, set, notify, open, explore, run, '
                                           'sound, close)' % verb)
                    if len(cur) - 1 < self.VERBS[verb]:
                        raise CeapError(n, '%s needs %d value%s' % (verb, self.VERBS[verb],
                                                                   '' if self.VERBS[verb] == 1 else 's'))
                    out.append(cur)
                cur = []
            else:
                cur.append(t)
        if not out:
            raise CeapError(n, 'a button needs at least one action after ->')
        return out


def resolve_path(p):
    p = os.path.expanduser(p.strip())
    if not os.path.isabs(p):
        p = os.path.join(theme.HOME, p)
    return os.path.abspath(p)


def fill(text, vars_):
    out = str(text)
    now = time.localtime()
    env = {'date': time.strftime('%m/%d/%Y', now), 'time': time.strftime('%I:%M:%S %p', now),
           'user': os.environ.get('USER', 'you')}
    env.update(vars_)
    for k, v in env.items():
        out = out.replace('{%s}' % k, str(v))
    return out


def build_windows(shell, path):
    """The (not yet opened) windows a .ceap file describes, or an error box."""
    from .dialogs import MessageBox
    app = CeapApp(path)
    if app.error:
        log(app.name, 'error', 'could not start: %s' % app.error)
        return [MessageBox(shell, title='CEAP app error', text='%s\n\n%s' % (os.path.basename(path), app.error),
                           kind='error')]
    state = {'vars': {}}                    # an app's windows share their variables
    log(app.name, 'info', 'started from %s (%d window%s)' % (os.path.basename(path), len(app.windows),
                                                             '' if len(app.windows) == 1 else 's'))
    return [CeapNote(shell, app=app, spec=w, state=state) if w['kind'] == 'note' else
            CeapMain(shell, path=w['root'], view=w['view'], terminal=w['terminal'], title=w['title'], size=w['size'])
            for w in app.windows]


def run_app(shell, path):
    """Open every window a .ceap file describes. Returns the first one."""
    wins = build_windows(shell, path)
    for w in wins:
        shell.open_window(w, center=getattr(w, 'dialog', False))
    return wins[0] if wins else None


# =============================================================================
# win.CEAP:Note
# =============================================================================
class CeapNote(App):
    title = 'CEAP:Note'
    icon = 'ceap'
    bg = theme.CLIENT_BG
    resizable = False

    def __init__(self, shell, app=None, spec=None, state=None, **kw):
        super().__init__(shell, **kw)
        self.app, self.spec = app, spec
        self.state = state if state is not None else {'vars': {}}
        self.title = spec['title']
        self.icon = app.meta.get('icon', 'ceap') if app else 'ceap'
        self.lines = [it[1] for it in spec['items'] if it[0] == 'text']
        self.override = None
        self.inputs = {}
        n_in = 0
        for it in spec['items']:
            if it[0] == 'input':
                self.inputs[it[1]] = {'text': '', 'cur': 0}
                self.state['vars'].setdefault(it[1], '')
                n_in += 1
        n_btn = sum(1 for it in spec['items'] if it[0] == 'button')
        h = 52 + 30 * max(1, len(self.lines)) + 36 * n_in + (44 if n_btn else 0) + theme.TITLE_H + theme.BORDER
        self.size = spec['size'] or (380, max(170, h))
        if self.inputs:
            self.ui.focus = ('in', next(iter(self.inputs)))

    def draw(self, img, ui):
        w, h = img.size
        ui.rect((0, 0, w, h), fill=(255, 255, 255))
        v = self.state['vars']
        for k, st in self.inputs.items():
            v[k] = st['text']
        y = 18
        lines = [self.override] if self.override is not None else self.lines
        for ln in lines:
            y = ui.wrap((20, y), fill(ln, v), w - 40, 15, color=(20, 40, 70)) + 6
        y += 4
        for it in self.spec['items']:
            if it[0] == 'input':
                ui.textbox((20, y, w - 20, y + 28), ('in', it[1]), self.inputs[it[1]], placeholder=fill(it[2], v),
                           size=13)
                y += 36
        btns = [it for it in self.spec['items'] if it[0] == 'button']
        if btns:
            by = h - 44
            ui.hline(0, w, by - 8, (232, 236, 240))
            widths = [max(64, gfx.text_width(gfx.font(12), fill(b[1], v)) + 24) for b in btns]
            x = max(12, (w - sum(widths) - 8 * (len(btns) - 1)) // 2)
            for i, (b, bw) in enumerate(zip(btns, widths)):
                ui.button((x, by, x + bw, by + 28), fill(b[1], v), ('btn', i), default=i == 0)
                x += bw + 8

    def on_command(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'btn':
            btns = [it for it in self.spec['items'] if it[0] == 'button']
            self.do(btns[cid[1]][2], label=btns[cid[1]][1])
        elif isinstance(cid, tuple) and cid[0] == 'in' and data == 'enter':
            btns = [it for it in self.spec['items'] if it[0] == 'button']
            if btns:
                self.do(btns[0][2], label=btns[0][1] + " (Enter)")
        self.invalidate()

    @property
    def app_name(self):
        return self.app.name if self.app else 'CEAP'

    def do(self, actions, label=None):
        sh = self.shell
        v = self.state['vars']
        for st, box in self.inputs.items():
            v[st] = box['text']
        if label is not None:
            typed = ', '.join('%s=%r' % (k, v.get(k, '')) for k in self.inputs) or 'no inputs'
            log(self.app_name, 'info', 'button "%s" clicked (%s)' % (fill(label, v), typed))
        for a in actions:
            verb, args = a[0], [fill(x, v) for x in a[1:]]
            log(self.app_name, 'action', ' '.join([verb] + [repr(x) for x in args]))
            try:
                if verb == 'say':
                    message(sh, self.app.name if self.app else 'CEAP', args[0], 'info', parent=self.win)
                elif verb == 'text':
                    self.override = a[1]          # filled in when drawn, so {vars} stay live
                elif verb == 'set':
                    log(self.app_name, 'var', '%s: %r -> %r' % (a[1], v.get(a[1], ''), args[1]))
                    v[a[1]] = args[1]
                    if a[1] in self.inputs:
                        self.inputs[a[1]] = {'text': args[1], 'cur': len(args[1])}
                elif verb == 'notify':
                    sh.notify(self.app.name if self.app else 'CEAP', args[0], 'info', 4)
                elif verb == 'open':
                    if args[0] == 'file' and len(args) > 1:
                        from .archiver import open_path
                        open_path(sh, resolve_path(args[1]))
                    elif args[0] == 'url' and len(args) > 1:
                        sh.launch('webinator', url=args[1])
                    elif args[0] in sh.registry:
                        sh.launch(args[0])
                    else:
                        log(self.app_name, 'warn', 'there is no Neptune program called "%s"' % args[0])
                        message(sh, 'CEAP', 'There is no Neptune program called "%s".' % args[0], 'warning',
                                parent=self.win)
                elif verb == 'explore':
                    sh.launch('ceap', path=resolve_path(args[0]))
                elif verb == 'run':
                    sh.launch('terminal', command=args[0], cwd=theme.HOME)
                elif verb == 'sound':
                    sh.sound('warn' if args[0] == 'warn' else 'notify')
                elif verb == 'log':
                    log(self.app_name, 'user', args[0])
                elif verb == 'close':
                    log(self.app_name, 'info', 'window "%s" closed by its button' % self.title)
                    self.close()
                    return
            except Exception as e:
                log(self.app_name, 'error', 'the "%s" action failed: %s' % (verb, e))
                message(sh, 'CEAP', 'The "%s" action failed: %s' % (verb, e), 'error', parent=self.win)
                return


# =============================================================================
# Code.Exe.Explore: Neptune's terminal, living inside CEAP:Main
# =============================================================================
class _DockedTerminal(Terminal):
    """A Terminal drawn into part of another window instead of its own."""

    def __init__(self, shell, host, cwd):
        self.host = host
        self.focused = False
        super().__init__(shell, cwd=cwd)

    @property
    def client_size(self):
        return self.size

    def invalidate(self):
        self.host.invalidate()

    def close(self):
        self.exited = True                 # the shell inside ended; the pane offers a restart

    def draw(self, img, ui):
        real = self.win
        self.win = self.shell.active if self.focused else object()   # caret only while the pane has focus
        try:
            super().draw(img, ui)
        finally:
            self.win = real


# =============================================================================
# win.CEAP:Main
# =============================================================================
TOOL_H, TABS_H, TREE_W, SIDE_W, TERM_H = 40, 30, 210, 240, 190


class CeapMain(Explorer):
    title = 'CEAP:Main'
    icon = 'ceap'
    size = (1120, 700)
    min_size = (760, 460)

    def __init__(self, shell, path=None, view='standard', terminal=False, title=None, size=None, **kw):
        self.mode = view if view in dict(MODES) else 'standard'
        self.fixed_title = title
        self.tree_open = set()
        self.tree_scroll = 0
        self.tree_cache = {}
        self.layouts = self.load_layouts()
        self.drag = None
        self.term = None
        self.follow = True
        self.ov_cache = {}
        if size:
            self.size = size
        super().__init__(shell, path=path or theme.HOME, **kw)
        self.view = 'icons'
        if terminal:
            self.toggle_terminal()

    # -- navigation ----------------------------------------------------------------
    def go(self, path, record=True):
        old = self.path
        super().go(path, record)
        if self.path != old:
            self.expand_to(self.path)
            if self.fixed_title:
                self.set_title(self.fixed_title)
            else:
                self.set_title('CEAP:Main - %s' % self.title)
            if self.term is not None and self.follow and not self.term.exited:
                self.term.write(' cd -- %s\r' % shlex.quote(self.path))

    ROOTS = [('Neptune Home', theme.HOME, 'documents'), ('User Home', os.path.expanduser('~'), 'folder'),
             ('COS', os.path.expanduser('~/cos'), 'folder'), ('Computer', '/', 'computer')]

    def subdirs(self, path):
        hit = self.tree_cache.get(path)
        if hit is not None and time.time() - hit[0] < 5:
            return hit[1]
        try:
            ds = sorted((n for n in os.listdir(path) if not n.startswith('.') and
                         os.path.isdir(os.path.join(path, n))), key=str.lower)[:300]
        except OSError:
            ds = []
        self.tree_cache[path] = (time.time(), ds)
        return ds

    def expand_to(self, path):
        for _label, root, _ic in self.ROOTS:
            r = os.path.realpath(root)
            p = os.path.realpath(path)
            if p == r or p.startswith(r.rstrip('/') + '/'):
                self.tree_open.add(r)
                cur = r
                for part in os.path.relpath(p, r).split(os.sep):
                    if part in ('.', ''):
                        break
                    cur = os.path.join(cur, part)
                    self.tree_open.add(cur)
                return

    def tree_rows(self):
        rows = []

        def walk(path, depth):
            for n in self.subdirs(path):
                full = os.path.join(path, n)
                rows.append((depth, n, full, 'folder'))
                if full in self.tree_open:
                    walk(full, depth + 1)
                if len(rows) > 2000:
                    return
        for label, root, ic in self.ROOTS:
            r = os.path.realpath(root)
            if not os.path.isdir(r):
                continue
            rows.append((0, label, r, ic))
            if r in self.tree_open:
                walk(r, 1)
        return rows

    # -- arrange layouts (Icon.Arrange remembers where you put things) ------------------
    def load_layouts(self):
        try:
            with open(LAYOUT_FILE) as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_layouts(self):
        os.makedirs(CEAP_DIR, exist_ok=True)
        tmp = LAYOUT_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(self.layouts, f)
        os.replace(tmp, LAYOUT_FILE)

    def arrange_positions(self, w):
        lay = self.layouts.setdefault(self.path, {})
        cw, ch = 96, 90
        cols = max(1, (w - 16) // cw)
        taken = {(round(x / cw), round(y / ch)) for x, y in lay.values()}
        pos = []
        slot = 0
        for n, *_ in self.items:
            if n in lay:
                pos.append(tuple(lay[n]))
                continue
            while (slot % cols, slot // cols) in taken:
                slot += 1
            p = (8 + (slot % cols) * cw, 8 + (slot // cols) * ch)
            taken.add((slot % cols, slot // cols))
            pos.append(p)
            slot += 1
        return pos

    # -- drawing ------------------------------------------------------------------------
    def geometry(self):
        w, h = self.client_size
        bottom = h - (TERM_H if self.term is not None else 0)
        return {'tree': (0, TOOL_H + TABS_H, TREE_W, bottom),
                'space': (TREE_W, TOOL_H + TABS_H, w - SIDE_W, bottom),
                'side': (w - SIDE_W, TOOL_H + TABS_H, w, bottom),
                'term': (0, bottom, w, h)}

    def draw(self, img, ui):
        w, h = img.size
        g = self.geometry()
        self.draw_toolbar(img, ui, w)
        self.draw_tabs(img, ui, w)
        self.draw_tree(img, ui, g['tree'])
        self.draw_space(img, ui, g['space'])
        self.draw_side(img, ui, g['side'])
        if self.term is not None:
            self.draw_term(img, ui, g['term'])

    def nav_button(self, img, ui, cx, cy, cid, glyph, enabled):
        r = 13
        box = (cx - r, cy - r, cx + r, cy + r)
        hot = ui.hot(box) and enabled
        top, bot = (((120, 190, 250), (20, 80, 170)) if hot else ((90, 160, 230), (20, 70, 150))) if enabled else \
            ((215, 220, 228), (175, 180, 190))
        from PIL import ImageDraw
        m = Image.new('L', (2 * r, 2 * r), 0)
        ImageDraw.Draw(m).ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
        gfx.paste_masked(img, gfx.vgradient(2 * r, 2 * r, ((0, top), (1, bot))), m, (cx - r, cy - r))
        ui.d.ellipse(box, outline=(30, 60, 110, 255) if enabled else (150, 155, 165, 255))
        ui.text((cx, cy), glyph, 13, bold=True, color=(255, 255, 255), anchor='mm')
        if enabled:
            ui.region(box, cid)

    def draw_toolbar(self, img, ui, w):
        tint = self.shell.settings.tint
        img.alpha_composite(gfx.vgradient(w, TOOL_H, ((0, gfx.mix(tint, (255, 255, 255), 0.85)),
                                                      (1, gfx.mix(tint, (255, 255, 255), 0.66)))), (0, 0))
        x = 20
        for cid, glyph, en in (('back', '←', bool(self.history)), ('fwd', '→', bool(self.future)),
                               ('up', '↑', self.path != '/'), ('refresh', '⟳', True)):
            self.nav_button(img, ui, x, 20, cid, glyph, en)
            x += 32
        ui.textbox((x, 8, w - 250, 32), 'addr', self.addr)
        ui.textbox((w - 242, 8, w - 40, 32), 'search', self.search, placeholder='Search %s' % (
            os.path.basename(self.path.rstrip('/')) or 'Computer'))
        ui.icon('ceap', (w - 32, 12), 18)

    def draw_tabs(self, img, ui, w):
        y0 = TOOL_H
        ui.command_bar((0, y0, w, y0 + TABS_H))
        x = 8
        for key, label in MODES:
            bw = gfx.text_width(gfx.font(12), label) + 20
            ui.toolbutton((x, y0 + 3, x + bw, y0 + TABS_H - 3), label, ('mode', key), toggled=self.mode == key)
            x += bw + 4
        x += 10
        bw = gfx.text_width(gfx.font(12), 'Code.Exe.Explore') + 20
        ui.toolbutton((x, y0 + 3, x + bw, y0 + TABS_H - 3), 'Code.Exe.Explore', 'term', toggled=self.term is not None)
        x += bw + 4
        ui.toolbutton((x, y0 + 3, x + 70, y0 + TABS_H - 3), '▾ Menu', 'menu')
        n = len(self.items)
        ui.text((w - 10, y0 + TABS_H // 2), '%d item%s' % (n, '' if n == 1 else 's'), 11, color=theme.MUTED,
                anchor='rm')

    def draw_tree(self, img, ui, box):
        x0, y0, x1, y1 = box
        img.alpha_composite(gfx.vgradient(x1 - x0, y1 - y0, ((0, (246, 249, 253)), (1, (232, 239, 247)))), (x0, y0))
        ui.d.line([(x1 - 1, y0), (x1 - 1, y1)], fill=(205, 214, 226, 255))
        ui.text((x0 + 10, y0 + 12), 'Directory tree', 11, bold=True, color=(30, 57, 91), anchor='lm')
        rows = self.tree_rows()
        rh = 21
        top = y0 + 26
        page = max(1, (y1 - top) // rh)
        self.tree_page = page
        self.tree_scroll = gfx.clamp(self.tree_scroll, 0, max(0, len(rows) - page))
        cur = os.path.realpath(self.path)
        fnt = gfx.font(12)
        for i, (depth, name, full, ic) in enumerate(rows[self.tree_scroll:self.tree_scroll + page]):
            y = top + i * rh
            ix = x0 + 8 + depth * 14
            r = (x0 + 2, y, x1 - 4, y + rh - 1)
            ui.listrow(r, ('tree', full), selected=full == cur, data=full)
            has_kids = bool(self.subdirs(full)) if depth < 12 else False
            if has_kids:
                tri = [(ix, y + 7), (ix + 6, y + 11), (ix, y + 15)] if full not in self.tree_open else \
                    [(ix - 1, y + 8), (ix + 7, y + 8), (ix + 3, y + 14)]
                ui.d.polygon(tri, fill=(90, 100, 115, 255))
                ui.region((ix - 3, y, ix + 9, y + rh), ('twist', full))
            ui.icon(ic, (ix + 12, y + 2), 16)
            ui.text((ix + 32, y + rh // 2), gfx.ellipsize(fnt, name, x1 - ix - 40), 12, anchor='lm')
        ui.scrollbar((x1 - 14, top, x1 - 2, y1), 'tsb', self.tree_scroll, page, len(rows))

    def item_icon(self, n, isdir):
        if isdir:
            return 'folder'
        if n.lower().endswith(EXT):
            return 'ceap'
        return icons.for_path(n, False)

    def draw_space(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.rect(box, fill=(255, 255, 255))
        ui.region(box, 'blank', 'item')
        if getattr(self, 'error', None):
            ui.text((x0 + 20, y0 + 30), 'Access denied: %s' % self.error, 12, color=(160, 40, 30))
            return
        if not self.items:
            ui.text(((x0 + x1) // 2, y0 + 50), 'This folder is empty.' if not self.search['text'] else
                    'Nothing matches "%s".' % self.search['text'], 12, color=theme.MUTED, anchor='mm')
        getattr(self, 'space_' + self.mode)(img, ui, box)

    def menu_chip(self, ui, x, y, idx):
        box = (x, y, x + 58, y + 18)
        ui.toolbutton(box, '▾ Menu', ('chip', idx), size=10)

    def label_lines(self, n, width):
        fnt = gfx.font(11)
        if gfx.text_width(fnt, n) <= width:
            return [n]
        a = n
        while a and gfx.text_width(fnt, a) > width:
            a = a[:-1]
        b = gfx.ellipsize(fnt, n[len(a):], width)
        return [a, b]

    def draw_icon_cell(self, img, ui, x, y, idx, cw=92):
        n, isdir, size, mt = self.items[idx]
        r = (x, y, x + cw - 4, y + 84)
        ui.listrow(r, ('item', idx), self.sel == idx, data=idx, kind='item')
        p = os.path.join(self.path, n)
        ic = self.item_icon(n, isdir)
        if ic == 'image':
            th = self.thumb(p)
            if th is not None:
                img.alpha_composite(th, (x + (cw - 4 - th.size[0]) // 2, y + 6 + (48 - th.size[1]) // 2))
            else:
                ui.icon(ic, (x + (cw - 52) // 2, y + 6), 48)
        else:
            ui.icon(ic, (x + (cw - 52) // 2, y + 6), 48)
        if self.rename and self.rename[0] == idx:
            ui.textbox((x - 6, y + 58, x + cw + 2, y + 80), 'rename', self.rename[1], size=11)
            return
        for k, ln in enumerate(self.label_lines(n, cw - 10)):
            ui.text((x + (cw - 4) // 2, y + 64 + k * 13), ln, 11, anchor='mm')

    def space_standard(self, img, ui, box):
        x0, y0, x1, y1 = box
        cw, ch = 96, 92
        cols = max(1, (x1 - x0 - 30) // cw)
        self.per_row = cols
        rows_vis = max(1, (y1 - y0 - 8) // ch)
        total_rows = -(-len(self.items) // cols)
        self.scroll = gfx.clamp(self.scroll, 0, max(0, total_rows - rows_vis))
        for r in range(self.scroll, min(total_rows, self.scroll + rows_vis + 1)):
            for c in range(cols):
                idx = r * cols + c
                if idx >= len(self.items):
                    break
                self.draw_icon_cell(img, ui, x0 + 10 + c * cw, y0 + 8 + (r - self.scroll) * ch, idx)
        if self.sel is not None and self.sel < len(self.items):
            r, c = divmod(self.sel, cols)
            if self.scroll <= r < self.scroll + rows_vis:
                self.menu_chip(ui, x0 + 10 + c * cw + 18, y0 + 8 + (r - self.scroll) * ch + 86, self.sel)
        ui.scrollbar((x1 - 16, y0, x1, y1), 'ssb', self.scroll, rows_vis, total_rows)

    def space_arrange(self, img, ui, box):
        x0, y0, x1, y1 = box
        pos = self.arrange_positions(x1 - x0)
        self._arrange_pos = pos
        sy = self.scroll * 30
        maxy = max([p[1] for p in pos] + [0]) + 100
        for idx, (px, py) in enumerate(pos):
            if self.drag and self.drag['idx'] == idx:
                continue
            x, y = x0 + px, y0 + py - sy
            if y + 84 < y0 or y > y1:
                continue
            self.draw_icon_cell(img, ui, x, y, idx)
        if self.drag:
            idx = self.drag['idx']
            self.draw_icon_cell(img, ui, self.drag['x'], self.drag['y'], idx)
        elif self.sel is not None and self.sel < len(pos):
            px, py = pos[self.sel]
            self.menu_chip(ui, x0 + px + 96, y0 + py - sy + 4, self.sel)
        ui.text((x1 - 22, y1 - 12), 'drag icons anywhere; CEAP remembers', 10, color=(170, 175, 185), anchor='rm')
        ui.scrollbar((x1 - 16, y0, x1, y1), 'ssb', self.scroll, max(1, (y1 - y0) // 30), max(1, maxy // 30))

    def space_text(self, img, ui, box):
        x0, y0, x1, y1 = box
        rh = 22
        page = max(1, (y1 - y0 - 8) // rh)
        self.per_row = 1
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(self.items) - page))
        fnt = gfx.font(12, False, 'mono')
        width = len(str(len(self.items)))
        for i in range(self.scroll, min(len(self.items), self.scroll + page)):
            n, isdir, size, mt = self.items[i]
            y = y0 + 6 + (i - self.scroll) * rh
            ui.listrow((x0 + 6, y, x1 - 20, y + rh - 1), ('item', i), self.sel == i, data=i, kind='item')
            ui.text((x0 + 14, y + rh // 2), '%*d.' % (width, i + 1), 12, color=theme.MUTED, anchor='lm', kind='mono')
            nx = x0 + 22 + (width + 1) * 8
            if self.rename and self.rename[0] == i:
                ui.textbox((nx, y + 1, x1 - 260, y + rh - 2), 'rename', self.rename[1])
            else:
                label = n + ('/' if isdir else '')
                ui.text((nx, y + rh // 2), gfx.ellipsize(fnt, label, x1 - nx - 280), 12, anchor='lm', kind='mono')
            kind = 'CEAPApp' if n.lower().endswith(EXT) else _kind(n, isdir)
            ui.text((x1 - 250, y + rh // 2), gfx.ellipsize(gfx.font(11), kind, 120), 11, color=theme.MUTED, anchor='lm')
            ui.text((x1 - 28, y + rh // 2), '' if isdir else _human(size), 11, color=theme.MUTED, anchor='rm')
        ui.scrollbar((x1 - 16, y0, x1, y1), 'ssb', self.scroll, page, len(self.items))

    # -- Overview and Metadata ---------------------------------------------------------------
    def selected_path(self):
        if self.sel is not None and self.sel < len(self.items):
            n, isdir, size, mt = self.items[self.sel]
            return os.path.join(self.path, n), isdir, size, mt
        try:
            st = os.stat(self.path)
            return self.path, True, 0, st.st_mtime
        except OSError:
            return self.path, True, 0, 0

    def overview(self, p, isdir):
        key = (p, os.path.getmtime(p) if os.path.exists(p) else 0)
        if key in self.ov_cache:
            return self.ov_cache[key]
        kind, data = 'icon', None
        name = os.path.basename(p)
        try:
            if isdir:
                names = sorted((n for n in os.listdir(p) if not n.startswith('.')), key=str.lower)
                kind, data = 'dir', (len(names), [(n, os.path.isdir(os.path.join(p, n))) for n in names[:14]])
            elif name.lower().endswith(EXT):
                kind, data = 'ceap', CeapApp(p)
            elif icons.for_path(name, False) == 'image':
                im = Image.open(p)
                im.thumbnail((SIDE_W - 30, 150))
                kind, data = 'image', im.convert('RGBA')
            elif icons.for_path(name, False) == 'archive':
                from .. import archive as A
                ents = A.list_entries(p)
                kind, data = 'dir', (len(ents), [(e.name.rstrip('/'), e.is_dir) for e in ents[:14]])
            elif os.path.getsize(p) < 1_000_000:
                with open(p, 'rb') as f:
                    raw = f.read(4096)
                if b'\x00' not in raw:
                    kind, data = 'text', raw.decode('utf-8', 'replace').splitlines()[:40]
        except Exception:
            kind, data = 'icon', None
        if len(self.ov_cache) > 64:
            self.ov_cache.clear()
        self.ov_cache[key] = (kind, data)
        return kind, data

    def draw_side(self, img, ui, box):
        x0, y0, x1, y1 = box
        img.alpha_composite(gfx.vgradient(x1 - x0, y1 - y0, ((0, (250, 252, 255)), (1, (238, 243, 249)))), (x0, y0))
        ui.d.line([(x0, y0), (x0, y1)], fill=(205, 214, 226, 255))
        p, isdir, size, mt = self.selected_path()
        name = os.path.basename(p.rstrip('/')) or '/'
        split = y0 + int((y1 - y0) * 0.58)
        kind, data = self.overview(p, isdir)
        label = {'dir': 'Dir.over', 'text': 'Text.over', 'image': 'Image.over', 'ceap': 'CEAP.over'}.get(kind, 'Overview')
        ui.text((x0 + 10, y0 + 12), 'Overview', 11, bold=True, color=(30, 57, 91), anchor='lm')
        ui.text((x1 - 10, y0 + 12), label, 10, color=theme.MUTED, anchor='rm')
        oy = y0 + 26
        inner = (x0 + 8, oy, x1 - 8, split - 8)
        ui.rect(inner, fill=(255, 255, 255), outline=(215, 222, 232))
        ix0, iy0, ix1, iy1 = inner
        if kind == 'dir':
            count, rows = data
            y = iy0 + 6
            for n, d in rows:
                if y + 18 > iy1 - 16:
                    break
                ui.icon('folder' if d else self.item_icon(n, False), (ix0 + 6, y), 16)
                ui.text((ix0 + 26, y + 8), gfx.ellipsize(gfx.font(11), n, ix1 - ix0 - 34), 11, anchor='lm')
                y += 19
            ui.text((ix0 + 6, iy1 - 10), '%d item%s' % (count, '' if count == 1 else 's'), 10, color=theme.MUTED,
                    anchor='lm')
        elif kind == 'text':
            y = iy0 + 5
            fnt = gfx.font(10, False, 'mono')
            for ln in data:
                if y + 13 > iy1:
                    break
                ui.d.text((ix0 + 6, y), gfx.ellipsize(fnt, ln.replace('\t', '    '), ix1 - ix0 - 12),
                          font=gfx.font_for_obj(fnt, ln or 'x'), fill=(40, 50, 60, 255))
                y += 13
        elif kind == 'image':
            img.alpha_composite(data, ((ix0 + ix1 - data.size[0]) // 2, (iy0 + iy1 - data.size[1]) // 2))
        elif kind == 'ceap':
            app = data
            ui.icon(app.meta.get('icon', 'ceap') if not app.error else 'error', (ix0 + 8, iy0 + 8), 32)
            ui.text((ix0 + 48, iy0 + 12), gfx.ellipsize(gfx.font(12, True), app.name, ix1 - ix0 - 56), 12, bold=True)
            ui.text((ix0 + 48, iy0 + 30), ('v%s  ' % app.meta['version'] if app.meta.get('version') else '') +
                    ('%d window%s' % (len(app.windows), '' if len(app.windows) == 1 else 's')), 11, color=theme.MUTED)
            yy = ui.wrap((ix0 + 8, iy0 + 52), app.error or app.meta.get('description') or 'A CEAP app.',
                         ix1 - ix0 - 16, 11, color=(170, 30, 20) if app.error else theme.CLIENT_TEXT)
            if not app.error:
                ui.button((ix0 + 8, min(iy1 - 30, yy + 6), ix0 + 90, min(iy1 - 6, yy + 30)), 'Run', ('run', p),
                          default=True)
            ui.button((ix0 + 96, min(iy1 - 30, yy + 6), ix0 + 170, min(iy1 - 6, yy + 30)), 'Edit', ('edit', p))
        else:
            ic = 'folder' if isdir else self.item_icon(name, False)
            ui.icon(ic, ((ix0 + ix1) // 2 - 32, (iy0 + iy1) // 2 - 40), 64)
            ui.text(((ix0 + ix1) // 2, (iy0 + iy1) // 2 + 36), 'No preview', 11, color=theme.MUTED, anchor='mm')
        # Metadata
        ui.text((x0 + 10, split + 6), 'Metadata', 11, bold=True, color=(30, 57, 91), anchor='lm')
        my = split + 20
        ic = 'folder' if isdir else self.item_icon(name, False)
        ui.icon(ic, (x0 + 10, my), 32)
        ui.text((x0 + 50, my + 4), gfx.ellipsize(gfx.font(12, True), name, x1 - x0 - 60), 12, bold=True)
        kind_name = 'CEAPApp' if name.lower().endswith(EXT) else _kind(name, isdir)
        ui.text((x0 + 50, my + 22), gfx.ellipsize(gfx.font(11), kind_name, x1 - x0 - 60), 11, color=theme.MUTED)
        lt = time.localtime(mt) if mt else None
        rows = [('date', time.strftime('%m/%d/%Y', lt) if lt else '?'),
                ('time', time.strftime('%I:%M:%S %p', lt) if lt else '?'),
                ('type', kind_name),
                ('size', '-' if isdir else _human(size))]
        y = my + 44
        for k, v in rows:
            if y + 16 > y1 - 34:
                break
            ui.text((x0 + 12, y), k + ':', 11, color=theme.MUTED)
            ui.text((x0 + 62, y), gfx.ellipsize(gfx.font(11), v, x1 - x0 - 72), 11)
            y += 17
        ui.button((x0 + 10, y1 - 30, x0 + 90, y1 - 8), 'More...', 'more', size=11)

    def draw_term(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.d.rectangle((x0, y0, x1, y0 + 22), fill=(40, 46, 56, 255))
        ui.text((x0 + 10, y0 + 11), 'Code.Exe.Explore', 11, bold=True, color=(200, 230, 255), anchor='lm')
        ui.text((x0 + 130, y0 + 11), REL(self.path), 10, color=(140, 160, 180), anchor='lm')
        ui.checkbox((x1 - 190, y0 + 3), '', 'follow', self.follow, size=10)
        ui.text((x1 - 170, y0 + 11), 'follow folder', 10, color=(200, 210, 220), anchor='lm')
        ui.toolbutton((x1 - 80, y0 + 2, x1 - 30, y0 + 20), 'here', 'cdhere', size=10, dark=True)
        ui.toolbutton((x1 - 26, y0 + 2, x1 - 4, y0 + 20), '×', 'term', size=11, dark=True)
        t = self.term
        tw, th = x1 - x0, y1 - y0 - 22
        if (tw, th) != t.size:
            t.size = (tw, th)
            t.on_resize()
        sub = Image.new('RGBA', (tw, th), (12, 12, 12, 255))
        from ..ui import UI
        tui = UI()
        tui.begin(sub)
        t.draw(sub, tui)
        if t.exited:
            tui.text((tw // 2, th // 2), 'The shell ended. Click here to start a new one.', 12, color=(200, 200, 200),
                     anchor='mm')
        img.alpha_composite(sub, (x0, y0 + 22))
        ui.region((x0, y0 + 22, x1, y1), 'termarea', 'item')

    # -- input -------------------------------------------------------------------------
    def toggle_terminal(self):
        if self.term is None:
            self.term = _DockedTerminal(self.shell, self, self.path)
            self.term.focused = True
        else:
            self.term.on_close()
            self.term = None
        self.invalidate()

    def on_command(self, cid, data):
        if isinstance(cid, tuple):
            k = cid[0]
            if k == 'mode':
                self.mode = cid[1]
                self.scroll = 0
            elif k == 'tree':
                self.go(cid[1])
            elif k == 'twist':
                self.tree_open ^= {cid[1]}
            elif k == 'chip':
                self.sel = cid[1]
                menu = self.context_for(cid[1])
                if menu:
                    self.shell.popup(menu, *self.screen_pos(*self.ui.mouse))
            elif k == 'run':
                run_app(self.shell, cid[1])
            elif k == 'edit':
                self.shell.launch('notepad', path=cid[1])
            else:
                if k == 'item' and self.term is not None:
                    self.term.focused = False
                super().on_command(cid, data)
            self.invalidate()
            return
        if cid == 'refresh':
            self.tree_cache.clear()
            self.ov_cache.clear()
            self.refresh()
        elif cid == 'term':
            self.toggle_terminal()
        elif cid == 'menu':
            self.shell.popup(self.space_menu(), *self.screen_pos(*self.ui.mouse))
        elif cid == 'more':
            p, isdir, size, mt = self.selected_path()
            if self.sel is not None:
                self.properties()
            else:
                message(self.shell, '%s Properties' % (os.path.basename(p) or '/'), 'Location: %s\nType: File folder\n'
                        'Items: %d' % (p, len(self.items)), 'info', parent=self.win)
        elif cid == 'tsb':
            self.tree_scroll = data
        elif cid == 'ssb':
            self.scroll = data
        elif cid == 'follow':
            self.follow = data
        elif cid == 'cdhere' and self.term is not None:
            self.term.write(' cd -- %s\r' % shlex.quote(self.path))
        elif cid == 'termarea' and self.term is not None:
            if self.term.exited:
                self.term.on_close()
                self.term = _DockedTerminal(self.shell, self, self.path)
            self.term.focused = True
            self.ui.focus = None
        elif cid == 'blank':
            if self.term is not None:
                self.term.focused = False
            super().on_command(cid, data)
        else:
            super().on_command(cid, data)
        self.invalidate()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'item':
            n = self.items[cid[1]][0]
            if n.lower().endswith(EXT) and not self.items[cid[1]][1]:
                run_app(self.shell, os.path.join(self.path, n))
                return
            self.open_item(cid[1])
        elif isinstance(cid, tuple) and cid[0] == 'tree':
            self.go(cid[1])
            self.tree_open.add(cid[1])
        else:
            self.on_command(cid, data)

    def context_for(self, idx):
        n, isdir = self.items[idx][0], self.items[idx][1]
        p = os.path.join(self.path, n)
        base = Explorer.context_menu.__get__(self)(*self._item_point(idx)) or []
        if n.lower().endswith(EXT) and not isdir:
            base = [{'label': 'Run CEAP app', 'icon': 'ceap', 'action': lambda: run_app(self.shell, p)},
                    {'label': 'Edit', 'icon': 'notepad', 'action': lambda: self.shell.launch('notepad', path=p)},
                    {'sep': True}] + base
        return base

    def _item_point(self, idx):
        for rect, cid, kind, data in self.ui.regions:
            if cid == ('item', idx):
                return (rect[0] + 2, rect[1] + 2)
        return (0, 0)

    def context_menu(self, x, y):
        h = self.ui.hit(x, y)
        if h and isinstance(h[0], tuple) and h[0][0] == 'item':
            self.sel = h[0][1]
            return self.context_for(h[0][1])
        if h and h[0] in ('blank',):
            return self.space_menu()
        if h and h[0] == 'termarea' and self.term is not None:
            return self.term.context_menu(0, 0)
        return None

    def space_menu(self):
        here = inside_home(self.path)
        return [{'label': 'View', 'sub': [{'label': label, 'checked': self.mode == k,
                                           'action': (lambda k=k: self.set_mode(k))} for k, label in MODES]},
                {'label': 'Refresh', 'action': lambda: self.on_command('refresh', None)},
                {'label': 'Show hidden files', 'checked': self.show_hidden, 'action': self.toggle_hidden},
                {'sep': True},
                {'label': 'New Folder', 'enabled': here, 'action': lambda: self.make_new(True)},
                {'label': 'New Text Document', 'enabled': here, 'action': lambda: self.make_new(False)},
                {'label': 'New CEAP App', 'icon': 'ceap', 'enabled': here, 'action': self.new_ceap},
                {'label': 'CEAP App Maker', 'icon': 'ceap', 'action': lambda: self.shell.launch('ceapmaker')},
                {'sep': True},
                {'label': 'Reset icon positions', 'enabled': self.mode == 'arrange', 'action': self.reset_arrange},
                {'label': 'Code.Exe.Explore', 'checked': self.term is not None, 'action': self.toggle_terminal},
                {'label': 'CEAP language help', 'icon': 'help', 'action': lambda: self.shell.launch('help', page='ceap')}]

    def set_mode(self, k):
        self.mode = k
        self.scroll = 0
        self.invalidate()

    def reset_arrange(self):
        self.layouts.pop(self.path, None)
        self.save_layouts()
        self.invalidate()

    def new_ceap(self):
        if not self.guard():
            return
        base = os.path.join(self.path, 'New App%s' % EXT)
        i = 2
        while os.path.exists(base):
            base = os.path.join(self.path, 'New App (%d)%s' % (i, EXT))
            i += 1
        name = os.path.splitext(os.path.basename(base))[0]
        with open(base, 'w') as f:
            f.write(TEMPLATE % {'name': name, 'author': os.environ.get('USER', 'me')})
        self.refresh()
        self.shell.launch('notepad', path=base)
        self.invalidate()

    # dragging in Icon.Arrange
    def on_down(self, cid, kind, data, x, y, button):
        if self.mode == 'arrange' and isinstance(cid, tuple) and cid[0] == 'item' and button == 1:
            pos = getattr(self, '_arrange_pos', None)
            if pos and cid[1] < len(pos):
                g = self.geometry()['space']
                px, py = pos[cid[1]]
                sx, sy = g[0] + px, g[1] + py - self.scroll * 30
                self.drag = {'idx': cid[1], 'dx': x - sx, 'dy': y - sy, 'x': sx, 'y': sy, 'moved': False}

    def on_drag(self, cid, x, y):
        if self.drag:
            self.drag['x'], self.drag['y'] = x - self.drag['dx'], y - self.drag['dy']
            self.drag['moved'] = True
            self.invalidate()

    def mouse(self, kind, x, y, button=1, clicks=1):
        super().mouse(kind, x, y, button, clicks)
        if kind == 'up' and self.drag:
            d, self.drag = self.drag, None
            if d['moved']:
                g = self.geometry()['space']
                nx = int(gfx.clamp(d['x'] - g[0], 0, g[2] - g[0] - 92))
                ny = int(max(0, d['y'] - g[1] + self.scroll * 30))
                self.layouts.setdefault(self.path, {})[self.items[d['idx']][0]] = [nx, ny]
                try:
                    self.save_layouts()
                except OSError:
                    pass
            self.invalidate()

    def cursor_at(self, x, y):
        h = self.ui.hit(x, y)
        if h and h[0] == 'termarea':
            return 'ibeam'
        return super().cursor_at(x, y)

    def on_wheel(self, dy, x, y):
        g = self.geometry()
        if gfx.in_rect(x, y, g['tree']):
            self.tree_scroll = max(0, self.tree_scroll - dy * 3)
        elif self.term is not None and gfx.in_rect(x, y, g['term']):
            self.term.on_wheel(dy, x, y)
        else:
            self.scroll = max(0, self.scroll - dy * (1 if self.mode != 'text' else 3))
        self.invalidate()

    def key(self, ev):
        if self.term is not None and self.term.focused and ev['type'] == 'key' and self.ui.focus is None \
                and not self.rename:
            if ev.get('ctrl') and ev.get('shift') and ev['key'] == 'c':
                return False
            self.term.key(ev)
            self.invalidate()
            return True
        if ev['type'] == 'key' and ev['key'] == 'f5':
            self.on_command('refresh', None)
            return True
        return super().key(ev)

    def wants_caret(self):
        return (self.term is not None and self.term.focused) or super().wants_caret()

    def on_tick(self, now):
        changed = False
        if self.term is not None and not self.term.exited:
            if self.term.on_tick(now):
                changed = True
            if self.term.focused and self.term.ui.caret_on != self.shell.caret_on:
                self.term.ui.caret_on = self.shell.caret_on
                changed = True
        return changed

    def on_close(self):
        if self.term is not None:
            self.term.on_close()
            self.term = None
        return True


REL = lambda p: p.replace(theme.ROOT, '~/cos/neptune').replace(os.path.expanduser('~'), '~', 1)


# =============================================================================
# CEAP apps in the APPS menu
# =============================================================================
SAMPLE = '''# Hello.ceap: the CEAP:Note from the CEAP concept sheet.
app "Hello"
icon ceap
version 1.0
author "TCF"
description "Says hello. Open this file in Notepad to see how a CEAP app is written."

win.CEAP:Note "CEAP:Note"
    text "Hello!"
    input name "Text input..."
    button "Hi" -> say "Hi, {name}!"
    button "Good day." -> text "Good day to you, {name}."
    button "Hello" -> notify "Hello from CEAP, {name}!" ; sound notify
'''


class CeapRegistry:
    """Keeps .ceap apps from home/.neptune/ceap in Neptune's registry, live."""

    def __init__(self, shell):
        self.shell = shell
        self.apps = {}
        self.last = 0
        os.makedirs(CEAP_DIR, exist_ok=True)
        sample = os.path.join(CEAP_DIR, 'Hello.ceap')
        marker = os.path.join(CEAP_DIR, '.sample-installed')
        if not os.path.exists(marker):
            if not os.path.exists(sample):
                with open(sample, 'w') as f:
                    f.write(SAMPLE)
            open(marker, 'w').close()
        self.scan()

    def scan(self):
        self.last = time.time()
        reg = self.shell.registry
        seen = set()
        try:
            names = sorted(n for n in os.listdir(CEAP_DIR) if n.endswith(EXT) and not n.startswith('.'))
        except OSError:
            names = []
        for n in names:
            p = os.path.join(CEAP_DIR, n)
            seen.add(p)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            old = self.apps.get(p)
            if old is not None and old.mtime == mt:
                continue
            app = CeapApp(p)
            self.apps[p] = app
            key = 'ceapapp:' + app.stem
            if app.error:
                reg.pop(key, None)
            else:
                reg[key] = (_Opener(app), app.name, app.meta.get('icon', 'ceap'))
        for p in list(self.apps):
            if p not in seen:
                reg.pop('ceapapp:' + self.apps.pop(p).stem, None)

    def tick(self, now):
        if now - self.last > 2:
            self.scan()


def _Opener(app):
    """A registry entry for one CEAP app: calling it builds the app's windows,
    opens all but the first, and hands the first to the shell to open."""
    def make(shell, **kw):
        wins = build_windows(shell, app.path)
        for w in wins[1:]:
            shell.open_window(w)
        return wins[0]
    make.single_instance = False
    make.title = app.name
    return make
