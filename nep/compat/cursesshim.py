"""A stand-in for the curses module, for COS 2.x programs.

Old apps were written against real curses on a text console. Neptune has no
console, so they get this instead: the same key codes and attribute bits
(copied from the real module, which defines them without a terminal), colour
pairs that resolve through the app's compatibility colour scheme, and the
ACS line-drawing names as Unicode box characters.
"""
import types

try:
    import curses as _real
except ImportError:          # pragma: no cover
    _real = None

shim = types.ModuleType('curses')
shim.__doc__ = 'COS compatibility curses (Neptune)'

if _real is not None:
    for _k in dir(_real):
        if _k.isupper() and isinstance(getattr(_real, _k), int):
            setattr(shim, _k, getattr(_real, _k))
    shim.error = _real.error
else:
    shim.error = Exception

# attribute bits, in case the real module was missing any
for _k, _v in (('A_NORMAL', 0), ('A_STANDOUT', 1 << 16), ('A_UNDERLINE', 1 << 17), ('A_REVERSE', 1 << 18),
               ('A_BLINK', 1 << 19), ('A_DIM', 1 << 20), ('A_BOLD', 1 << 21), ('A_COLOR', 0xff00),
               ('KEY_UP', 259), ('KEY_DOWN', 258), ('KEY_LEFT', 260), ('KEY_RIGHT', 261), ('KEY_HOME', 262),
               ('KEY_BACKSPACE', 263), ('KEY_F0', 264), ('KEY_DC', 330), ('KEY_IC', 331), ('KEY_NPAGE', 338),
               ('KEY_PPAGE', 339), ('KEY_ENTER', 343), ('KEY_END', 360), ('KEY_RESIZE', 410), ('KEY_MOUSE', 409),
               ('KEY_BTAB', 353), ('COLOR_BLACK', 0), ('COLOR_RED', 1), ('COLOR_GREEN', 2), ('COLOR_YELLOW', 3),
               ('COLOR_BLUE', 4), ('COLOR_MAGENTA', 5), ('COLOR_CYAN', 6), ('COLOR_WHITE', 7)):
    if not hasattr(shim, _k):
        setattr(shim, _k, _v)

A_BOLD, A_DIM, A_REVERSE, A_UNDERLINE, A_STANDOUT = shim.A_BOLD, shim.A_DIM, shim.A_REVERSE, shim.A_UNDERLINE, shim.A_STANDOUT

ACS = {
    'ACS_HLINE': '─', 'ACS_VLINE': '│', 'ACS_ULCORNER': '┌', 'ACS_URCORNER': '┐', 'ACS_LLCORNER': '└',
    'ACS_LRCORNER': '┘', 'ACS_LTEE': '├', 'ACS_RTEE': '┤', 'ACS_TTEE': '┬', 'ACS_BTEE': '┴', 'ACS_PLUS': '┼',
    'ACS_CKBOARD': '▒', 'ACS_BOARD': '▒', 'ACS_BLOCK': '█', 'ACS_DIAMOND': '◆', 'ACS_DEGREE': '°',
    'ACS_PLMINUS': '±', 'ACS_BULLET': '·', 'ACS_UARROW': '↑', 'ACS_DARROW': '↓', 'ACS_LARROW': '←',
    'ACS_RARROW': '→', 'ACS_LEQUAL': '≤', 'ACS_GEQUAL': '≥', 'ACS_PI': 'π', 'ACS_NEQUAL': '≠',
    'ACS_STERLING': '£', 'ACS_S1': '⎺', 'ACS_S3': '⎻', 'ACS_S7': '⎼', 'ACS_S9': '⎽', 'ACS_LANTERN': '§',
}
for _k, _v in ACS.items():
    setattr(shim, _k, _v)


def color_pair(n):
    return (int(n) & 0xff) << 8


def pair_number(attr):
    return (int(attr) & 0xff00) >> 8


def KEY_F(n):
    return shim.KEY_F0 + n


_noop = lambda *a, **k: None
shim.color_pair = color_pair
shim.pair_number = pair_number
shim.KEY_F = KEY_F
shim.has_colors = lambda: True
shim.can_change_color = lambda: False
shim.COLORS = 8
shim.COLOR_PAIRS = 64
for _f in ('start_color', 'use_default_colors', 'curs_set', 'beep', 'flash', 'napms', 'noecho', 'echo',
           'cbreak', 'nocbreak', 'raw', 'noraw', 'mousemask', 'mouseinterval', 'doupdate'):
    setattr(shim, _f, _noop)
shim.init_pair = _noop          # colour schemes come from the compatibility settings


def _unsupported(*a, **k):
    raise shim.error('not available in COS compatibility mode')


for _f in ('initscr', 'newwin', 'newpad', 'wrapper', 'endwin', 'getmouse'):
    setattr(shim, _f, _unsupported)
