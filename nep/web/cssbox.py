"""CSS boxes for Webinator: the box model, flexbox, simple grids, inline-blocks
and colour emoji. Mixed into Layout (layout.py), which keeps doing normal flow.

Display list additions:
  ('b', x0, y0, x1, y1, fill, border_color, (top, right, bottom, left), radius)
Emoji are ordinary image items whose url is 'emoji:<text>:<px>'.

Coverage is what app-style pages actually use: lengths in px/em/rem/%/vw/vh
and calc(); margin/padding/border/border-radius/background(-color);
width/min-width/max-width/height/min-height; display flex/inline-flex/grid/
inline-block/none; flex-direction, flex-wrap, gap, flex (grow/shrink/basis),
justify-content, align-items/align-self; grid-template-columns with px, fr,
repeat(N, ...) and repeat(auto-fill, minmax(...)). No floats, no positioning.
"""
import ast
import functools
import operator
import os
import re

from PIL import Image, ImageDraw, ImageFont

from .dom import parse_color

_NUM = re.compile(r'^(-?[\d.]+)(px|em|rem|%|vw|vh|pt|fr)?$')
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.USub: operator.neg, ast.UAdd: operator.pos}


def _arith(expr):
    """Evaluate + - * / ( ) on plain numbers, nothing else."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError('not arithmetic')
    return ev(ast.parse(expr, mode='eval'))


def color_of(value):
    """First usable colour in a background / border value (gradients give their first stop)."""
    if not value:
        return None
    v = value.strip()
    if v in ('none', 'transparent', '0', '0 0', 'inherit', 'initial', 'unset', 'currentcolor'):
        return None
    c = parse_color(v)
    if c is not None:
        return c
    for tok in re.findall(r'#[0-9a-fA-F]{3,8}|rgba?\([^)]*\)|hsla?\([^)]*\)|[a-zA-Z]+', v):
        c = parse_color(tok)
        if c is not None:
            return c
    return None


# -- emoji --------------------------------------------------------------------------
EMOJI_FONT = '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf'
_EMOJI_RE = re.compile(
    '(?:[\U0001F1E6-\U0001F1FF]{2})'                                   # flags
    '|(?:[\U0001F300-\U0001FAFF☀-➿⬀-⯿⌀-⏿←-⇿⤴⤵'
    '〰〽㊗㊙©®‼⁉™ℹ]'
    '[️\U0001F3FB-\U0001F3FF]*(?:‍[\U0001F300-\U0001FAFF☀-➿][️]?)*)')
# symbols the UI font already draws well; leave them as text
_TEXT_SYMBOLS = set('←↑→↓↔↕↩↪•▲▼◀▶✓✕'
                    '✖…©®™')


def is_emoji_text(s):
    return bool(_EMOJI_RE.search(s))


def split_emoji(s):
    """[(is_emoji, text)] runs."""
    out, pos = [], 0
    for m in _EMOJI_RE.finditer(s):
        if m.group(0) in _TEXT_SYMBOLS:
            continue
        if m.start() > pos:
            out.append((False, s[pos:m.start()]))
        out.append((True, m.group(0)))
        pos = m.end()
    if pos < len(s):
        out.append((False, s[pos:]))
    return out


@functools.lru_cache(maxsize=1)
def _emoji_font():
    try:
        return ImageFont.truetype(EMOJI_FONT, 109) if os.path.isfile(EMOJI_FONT) else None
    except OSError:
        return None


@functools.lru_cache(maxsize=512)
def emoji_image(text, px):
    """A colour emoji scaled to px tall, or None when the emoji font is missing."""
    f = _emoji_font()
    if f is None:
        return None
    text = text.replace('️', '')
    try:
        w = int(f.getlength(text)) + 8
        big = Image.new('RGBA', (max(w, 140), 140), (0, 0, 0, 0))
        ImageDraw.Draw(big).text((4, 4), text, font=f, embedded_color=True)
    except Exception:
        return None
    box = big.getbbox()
    if not box:
        return None
    big = big.crop(box)
    s = px / float(max(1, big.size[1]))
    return big.resize((max(1, int(big.size[0] * s)), max(1, int(px))), Image.LANCZOS)


class CSSBoxMixin:
    rich = False          # set by Layout: the page runs JavaScript (style buttons as boxes)

    # -- lengths ------------------------------------------------------------------------
    def length(self, v, em, ref, auto=None):
        """CSS length -> px (float). ref is what % is relative to. 'auto' -> auto."""
        if v is None:
            return None
        v = str(v).strip().lower()
        if not v:
            return None
        if v == 'auto':
            return auto
        if v in ('0', 'none', 'initial', 'unset', 'inherit', 'normal'):
            return 0.0 if v == '0' else None
        if v.startswith('calc(') and v.endswith(')'):
            inner = v[5:-1]

            def unit(m):
                r = self.length(m.group(0), em, ref)
                return repr(r if r is not None else 0.0)
            try:
                return float(_arith(re.sub(r'-?[\d.]+(?:px|em|rem|%|vw|vh|pt)', unit, inner)))
            except Exception:
                return None
        if v.startswith(('min(', 'max(', 'clamp(')):
            parts = [self.length(p, em, ref) for p in v[v.index('(') + 1:-1].split(',')]
            parts = [p for p in parts if p is not None]
            if not parts:
                return None
            if v.startswith('min('):
                return min(parts)
            if v.startswith('max('):
                return max(parts)
            return sorted(parts)[1] if len(parts) == 3 else parts[0]
        m = _NUM.match(v)
        if not m:
            return None
        n = float(m.group(1))
        u = m.group(2) or 'px'
        z = self.zoom
        return {'px': n * z, 'pt': n * 1.333 * z, 'em': n * em, 'rem': n * 16 * z,
                '%': (ref or 0) * n / 100.0, 'vw': self.W * n / 100.0, 'vh': self.W * 0.5625 * n / 100.0,
                'fr': n}[u]

    def _four(self, css, prop, em, ref):
        vals = [None, None, None, None]
        short = css.get(prop)
        if short:
            toks = short.split()
            if 1 <= len(toks) <= 4:
                t = (toks * 4)[:4] if len(toks) == 1 else \
                    [toks[0], toks[1], toks[0], toks[1]] if len(toks) == 2 else \
                    [toks[0], toks[1], toks[2], toks[1]] if len(toks) == 3 else toks
                vals = [self.length(x, em, ref, auto='auto') for x in t]
        for i, side in enumerate(('top', 'right', 'bottom', 'left')):
            v = css.get('%s-%s' % (prop, side))
            if v is not None:
                vals[i] = self.length(v, em, ref, auto='auto')
        for i, (a, b) in enumerate((('block-start', 'top'), ('inline-end', 'right'), ('block-end', 'bottom'),
                                    ('inline-start', 'left'))):
            pass
        for prop2, idx in (('%s-inline' % prop, (1, 3)), ('%s-block' % prop, (0, 2))):
            v = css.get(prop2)
            if v:
                t = v.split()
                a = self.length(t[0], em, ref, auto='auto')
                b = self.length(t[-1], em, ref, auto='auto')
                vals[idx[0]], vals[idx[1]] = a, b
        return vals

    def _border(self, css, em):
        widths, colors = [0, 0, 0, 0], [None, None, None, None]

        def parse(v):
            if not v or v.strip() in ('none', '0', 'hidden'):
                return 0, None
            w, c = None, None
            for tok in re.findall(r'rgba?\([^)]*\)|hsla?\([^)]*\)|\S+', v):
                if tok in ('solid', 'dashed', 'dotted', 'double', 'groove', 'ridge', 'inset', 'outset'):
                    if w is None:
                        w = 1.0
                    continue
                ln = self.length(tok, em, 0)
                if ln is not None and w in (None, 1.0) and not tok.startswith(('#', 'rgb')):
                    w = ln
                    continue
                cc = parse_color(tok)
                if cc is not None:
                    c = cc
            if w is None:
                w = 1.0 if c is not None else 0
            return w, c
        if 'border' in css:
            w, c = parse(css['border'])
            widths, colors = [w] * 4, [c] * 4
        for i, side in enumerate(('top', 'right', 'bottom', 'left')):
            v = css.get('border-' + side)
            if v is not None:
                widths[i], colors[i] = parse(v)
        if 'border-width' in css:
            t = [self.length(x, em, 0) or 0 for x in css['border-width'].split()]
            if t:
                widths = (t * 4)[:4] if len(t) == 1 else [t[0], t[1 % len(t)], t[2 % len(t)], t[3 % len(t)]]
        if 'border-color' in css:
            c = color_of(css['border-color'])
            colors = [c] * 4
        if css.get('border-style', '').strip() == 'none':
            widths = [0, 0, 0, 0]
        color = next((c for c, w in zip(colors, widths) if c and w), None)
        widths = [int(round(w)) if (w and (colors[i] or color)) else 0 for i, w in enumerate(widths)]
        return widths, color

    def box_of(self, node, st, ref=None):
        """Box-model values for an element, or None when it has no CSS that matters here."""
        css = node.css
        if not css:
            return None
        ref = self.fw if ref is None else ref
        em = st.size
        b = {}
        b['margin'] = self._four(css, 'margin', em, ref)
        b['padding'] = [max(0.0, x) if isinstance(x, (int, float)) else 0.0 for x in self._four(css, 'padding', em, ref)]
        b['border'], b['border_color'] = self._border(css, em)
        r = css.get('border-radius', '').split('/')[0].split()
        rad = self.length(r[0], em, 0) if r else 0
        if r and r[0].endswith('%'):
            rad = 999
        b['radius'] = rad or 0
        bg = css.get('background-color') or css.get('background')
        b['bg'] = color_of(bg)
        if any(k in css for k in ('mask-image', '-webkit-mask-image', 'mask', '-webkit-mask')):
            b['bg'] = None              # an icon painted through a mask: never a solid square
        for k in ('width', 'min-width', 'max-width', 'height', 'min-height', 'flex-basis'):
            v = css.get(k)
            b[k] = self.length(v, em, ref) if v not in (None, 'auto') else None
        if css.get('max-width') in ('none',):
            b['max-width'] = None
        b['display'] = css.get('display', '').strip()
        return b

    # -- bookkeeping for moving laid-out content -------------------------------------------
    def mark(self):
        return (len(self.items), len(self.regions), len(self.boxes), len(self.controls))

    def translate(self, m0, m1, dx, dy):
        dx, dy = int(dx), int(dy)
        if not dx and not dy:
            return
        for i in range(m0[0], m1[0]):
            self.items[i] = _moved(self.items[i], dx, dy)
        for i in range(m0[1], m1[1]):
            r = self.regions[i]
            self.regions[i] = (r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy, r[4])
        for i in range(m0[2], m1[2]):
            r = self.boxes[i]
            self.boxes[i] = (r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy, r[4])
        for i in range(m0[3], m1[3]):
            c = self.controls[i]
            c.x += dx
            c.y += dy

    # -- boxes ---------------------------------------------------------------------------
    def box_block(self, node, st, box, m_top, m_bottom):
        """Lay a block out with its margins, borders, padding, background and size,
        then its children (flow, flex or grid). Returns True if handled."""
        t = node.tag
        mt, mr, mb, ml = box['margin']
        self.add_margin(m_top)
        save = (self.fx, self.fw, self.align)
        self.align = st.align
        lft = ml if isinstance(ml, (int, float)) else 0
        rgt = mr if isinstance(mr, (int, float)) else 0
        self.fx += lft
        self.fw -= lft + rgt
        avail = self.fw
        w = box['width']
        if box['max-width'] is not None:
            w = min(w if w is not None else avail, box['max-width'])
        if box['min-width'] is not None and w is not None:
            w = max(w, box['min-width'])
        if w is not None and 0 < w < avail:
            if ml == 'auto' and mr == 'auto':
                self.fx += (avail - w) / 2
            elif ml == 'auto':
                self.fx += avail - w
            self.fw = w
        if t in ('ul', 'ol', 'menu'):
            pl = box['padding'][3]
            if not pl and 'padding' not in (node.css or {}) and 'padding-left' not in (node.css or {}):
                ind = int(st.size * 1.9)
                self.fx += ind
                self.fw -= ind
            self.list_stack.append([t, int(node.get('start') or 1) if (node.get('start') or '').isdigit() else 1,
                                    node.get('type', '')])
        elif t == 'li':
            if not st.nolist:
                self.marker = (self.list_marker(node), st)
        self.ensure_margin()
        bw = box['border']
        pad = box['padding']
        draws = box['bg'] is not None or any(bw)
        bi = len(self.items)
        x0, y0, x1 = self.fx, self.y, self.fx + self.fw
        self.fx += bw[3] + pad[3]
        self.fw -= bw[3] + pad[3] + bw[1] + pad[1]
        self.fw = max(10, self.fw)
        self.y += bw[0] + pad[0]
        inner = st.copy()
        inner.bg = None
        inner.el = node
        disp = box['display']
        if disp in ('flex', 'inline-flex'):
            self.flex(node, inner)
        elif disp in ('grid', 'inline-grid'):
            self.grid(node, inner)
        else:
            self.walk(node, inner)
            self.flush()
        self.marker = None
        self.y += pad[2] + bw[2]
        if box['height'] is not None and box['height'] > self.y - y0:
            self.y = y0 + box['height']
        if box['min-height'] is not None and box['min-height'] > self.y - y0:
            self.y = y0 + box['min-height']
        if draws:
            self.items.insert(bi, ('b', x0, y0, x1, self.y, box['bg'], box['border_color'], tuple(bw),
                                   box['radius'], node))
        self.boxes.append((x0, y0, x1, self.y, node))
        if t in ('ul', 'ol', 'menu'):
            self.list_stack.pop()
        self.fx, self.fw, self.align = save
        self.add_margin(m_bottom)
        return True

    # -- flex ----------------------------------------------------------------------------
    def _gaps(self, css, em, ref):
        g = (css.get('gap') or '').split()
        rg = self.length(g[0], em, ref) if g else None
        cg = self.length(g[-1], em, ref) if g else None
        if css.get('row-gap'):
            rg = self.length(css['row-gap'], em, ref)
        if css.get('column-gap'):
            cg = self.length(css['column-gap'], em, ref)
        return rg or 0.0, cg or 0.0

    def flex_children(self, node, st):
        out = []
        for ch in node.children:
            if ch.tag == '#text':
                if ch.text.strip():
                    out.append((ch, st))
                continue
            if ch.tag in ('head', 'option', 'br', 'wbr'):
                continue
            cst = self.style_for(ch, st)
            if cst.hidden:
                continue
            pos = (ch.css or {}).get('position', '')
            if pos in ('fixed', 'absolute') and not ch.children:
                continue
            out.append((ch, cst))
        order = [(int(((c.css or {}).get('order') or '0').strip() or 0) if c.tag != '#text' and
                  ((c.css or {}).get('order') or '0').strip().lstrip('-').isdigit() else 0, i, (c, s))
                 for i, (c, s) in enumerate(out)]
        return [x[2] for x in sorted(order)]

    def flex_props(self, ch, cst, W):
        """(basis, grow, shrink, min_width) for one flex item."""
        if ch.tag == '#text':
            fnt = cst.font()
            txt = {'uppercase': str.upper, 'lowercase': str.lower, 'capitalize': str.title}.get(cst.tt, str)(ch.text)
            words = txt.split()
            mx = self.width_of(fnt, ' '.join(words))
            mn = max([self.width_of(fnt, w) for w in words] or [0])
            return mx + 1, 0.0, 1.0, mn
        css = ch.css or {}
        grow, shrink, basis = 0.0, 1.0, None
        f = css.get('flex', '').strip()
        if f:
            toks = f.split()
            if toks[0] == 'none':
                grow, shrink = 0.0, 0.0
            elif toks[0] == 'auto':
                grow, shrink = 1.0, 1.0
            else:
                nums = [t for t in toks if re.match(r'^[\d.]+$', t)]
                if nums:
                    grow = float(nums[0])
                    if len(nums) > 1:
                        shrink = float(nums[1])
                    lens = [t for t in toks if not re.match(r'^[\d.]+$', t)]
                    basis = self.length(lens[0], cst.size, W) if lens else 0.0
                else:
                    basis = self.length(toks[0], cst.size, W)
        if css.get('flex-grow'):
            try:
                grow = float(css['flex-grow'])
            except ValueError:
                pass
        if css.get('flex-shrink'):
            try:
                shrink = float(css['flex-shrink'])
            except ValueError:
                pass
        box = self.box_of(ch, cst, W) or {'margin': [0] * 4, 'padding': [0] * 4, 'border': [0] * 4,
                                          'width': None, 'min-width': None, 'max-width': None, 'flex-basis': None}
        if box.get('flex-basis') is not None:
            basis = box['flex-basis']
        horiz = sum(box['padding'][i] + box['border'][i] for i in (1, 3))
        margins = sum(m for m in (box['margin'][1], box['margin'][3]) if isinstance(m, (int, float)))
        mn, mx = self.content_size(ch, cst)
        if box['width'] is not None and basis is None:
            basis = box['width']
        if basis is None:
            basis = mx + horiz
        minw = box['min-width'] if box['min-width'] is not None else (
            0.0 if css.get('min-width', '').strip() in ('0', '0px') else min(mn + horiz, basis))
        if box['max-width'] is not None:
            basis = min(basis, box['max-width'])
        return basis + margins, grow, shrink, minw + margins

    def content_size(self, ch, cst):
        """(min, max) content width of an element, controls and inline boxes included."""
        if ch.tag in ('input', 'select', 'textarea'):
            w = self.length((ch.css or {}).get('width'), cst.size, self.fw)
            base = w if w is not None else cst.size * 12
            return min(base, cst.size * 6), base
        if ch.tag == 'img':
            w = self.length((ch.css or {}).get('width') or ch.get('width'), cst.size, self.fw) or cst.size * 2
            return w, w
        css = ch.css or {}
        if css.get('display', '').strip() in ('flex', 'inline-flex') and \
                not css.get('flex-direction', 'row').startswith('column'):
            # a nested flex row: its items side by side, gaps included
            key = ('flexrow', ch.nid, round(cst.size, 1))
            if key not in self._mcache:
                self._mcache[key] = (0.0, 0.0)
                kids = self.flex_children(ch, cst)
                _rg, cg = self._gaps(css, cst.size, 0)
                sizes = [self.flex_props(k, ks, 0) for k, ks in kids]
                gaps = cg * max(0, len(sizes) - 1)
                mx = sum(p[0] for p in sizes) + gaps
                mn = (max([p[3] for p in sizes] + [0]) if 'wrap' in css.get('flex-wrap', '')
                      else sum(p[3] for p in sizes) + gaps)
                self._mcache[key] = (mn, mx)
            return self._mcache[key]
        return self.measure(ch, cst)

    def flex(self, node, st):
        css = node.css or {}
        direction = css.get('flex-direction', '') or css.get('flex-flow', '').split(' ')[0] or 'row'
        wrap = 'wrap' in (css.get('flex-wrap', '') + ' ' + css.get('flex-flow', '')) and \
            'nowrap' not in css.get('flex-wrap', '')
        rg, cg = self._gaps(css, st.size, self.fw)
        items = self.flex_children(node, st)
        if not items:
            return
        if direction.startswith('column'):
            return self.flex_column(node, st, items, rg)
        W = self.fw
        props = [self.flex_props(ch, cst, W) for ch, cst in items]
        lines, cur, used = [], [], 0.0
        for it, p in zip(items, props):
            need = p[0] + (cg if cur else 0)
            if wrap and cur and used + need > W:
                lines.append(cur)
                cur, used = [], 0.0
                need = p[0]
            cur.append((it, p))
            used += need
        if cur:
            lines.append(cur)
        justify = css.get('justify-content', 'flex-start').strip()
        align = css.get('align-items', 'stretch').strip()
        x_left = self.fx
        for li, line in enumerate(lines):
            n = len(line)
            widths = [p[0] for _it, p in line]
            free = W - sum(widths) - cg * (n - 1)
            grow = sum(p[1] for _it, p in line)
            if free > 0 and grow > 0:
                widths = [w + free * p[1] / grow for w, (_it, p) in zip(widths, line)]
                free = 0
            elif free < 0:
                for _ in range(3):                          # shrink, respecting min-content
                    shrinkable = [(i, (w - p[3]) * p[2]) for i, (w, (_it, p)) in enumerate(zip(widths, line))
                                  if w > p[3] and p[2] > 0]
                    total = sum(s for _i, s in shrinkable)
                    if total <= 0 or free >= -0.5:
                        break
                    for i, s in shrinkable:
                        widths[i] = max(line[i][1][3], widths[i] + free * s / total)
                    free = W - sum(widths) - cg * (n - 1)
            gap = cg
            x = x_left
            if free > 0:
                if justify in ('flex-end', 'end', 'right'):
                    x += free
                elif justify == 'center':
                    x += free / 2
                elif justify == 'space-between' and n > 1:
                    gap = cg + free / (n - 1)
                elif justify == 'space-around':
                    gap = cg + free / n
                    x += free / n / 2
                elif justify == 'space-evenly':
                    gap = cg + free / (n + 1)
                    x += free / (n + 1)
            top = self.y
            placed = []
            for ((ch, cst), _p), w in zip(line, widths):
                m0 = self.mark()
                h = self.flex_item(ch, cst, x, top, max(1, w))
                placed.append((ch, m0, self.mark(), h))
                x += w + gap
            rowh = max([p[3] for p in placed] + [0])
            for ch, m0, m1, h in placed:
                a = align
                if ch.tag != '#text':
                    a = (ch.css or {}).get('align-self', '').strip() or align
                if a in ('center',):
                    self.translate(m0, m1, 0, (rowh - h) / 2)
                elif a in ('flex-end', 'end', 'baseline-end'):
                    self.translate(m0, m1, 0, rowh - h)
                elif a in ('stretch', 'normal') and ch.tag != '#text':
                    self._stretch(ch, m0, m1, top + rowh)
            self.y = top + rowh + (rg if li < len(lines) - 1 else 0)
        self.fx, self.fw = x_left, W

    def _stretch(self, node, m0, m1, bottom):
        for i in range(m0[0], m1[0]):
            it = self.items[i]
            if it[0] == 'b' and it[9] is node:
                if it[4] < bottom:
                    self.items[i] = it[:4] + (bottom,) + it[5:]
                return

    def flex_item(self, ch, cst, x, top, w):
        """Lay one flex/grid item out in [x, x+w) from y=top. Returns its height."""
        save = (self.fx, self.fw, self.align, self.atoms, self.pending_margin, self.marker)
        self.atoms = []
        self.fx, self.fw, self.y, self.pending_margin, self.marker = x, w, top, 0, None
        if ch.tag == '#text':
            self.text(ch.text, cst)
            self.flush()
        elif ch.tag in ('input', 'select', 'textarea') or (ch.tag == 'button' and not self.rich):
            self.control(ch, cst, force_w=w)
            self.flush()
        elif ch.tag == 'img':
            self.image(ch, cst)
            self.flush()
        else:
            self.block(ch, cst, flex_item=True)
            self.flush()
        h = self.y - top
        self.fx, self.fw, self.align, self.atoms, self.pending_margin, self.marker = save
        return h

    def flex_column(self, node, st, items, rg):
        css = node.css or {}
        align = css.get('align-items', 'stretch').strip()
        W, x0 = self.fw, self.fx
        first = True
        for ch, cst in items:
            if not first:
                self.y += rg
            first = False
            a = (ch.css or {}).get('align-self', '').strip() if ch.tag != '#text' else ''
            a = a or align
            w = W
            x = x0
            if a in ('center', 'flex-end', 'end', 'flex-start', 'start'):
                basis = self.flex_props(ch, cst, W)[0]
                bw = (self.box_of(ch, cst, W) or {}).get('width') if ch.tag != '#text' else None
                w = min(W, bw if bw is not None else basis)
                if a == 'center':
                    x = x0 + (W - w) / 2
                elif a in ('flex-end', 'end'):
                    x = x0 + W - w
            top = self.y
            h = self.flex_item(ch, cst, x, top, max(1, w))
            self.y = top + h
        self.fx, self.fw = x0, W

    # -- grid ------------------------------------------------------------------------------
    def grid(self, node, st):
        css = node.css or {}
        items = self.flex_children(node, st)
        if not items:
            return
        rg, cg = self._gaps(css, st.size, self.fw)
        W, x0 = self.fw, self.fx
        cols = self.grid_columns(css.get('grid-template-columns', ''), W, cg, st.size)
        n = len(cols)
        align = css.get('align-items', 'stretch').strip()
        for r in range(0, len(items), n):
            row = items[r:r + n]
            top = self.y
            placed = []
            x = x0
            for (ch, cst), w in zip(row, cols):
                m0 = self.mark()
                h = self.flex_item(ch, cst, x, top, max(1, w))
                placed.append((ch, m0, self.mark(), h))
                x += w + cg
            rowh = max([p[3] for p in placed] + [0])
            for ch, m0, m1, h in placed:
                if align == 'center':
                    self.translate(m0, m1, 0, (rowh - h) / 2)
                elif align in ('stretch', 'normal') and ch.tag != '#text':
                    self._stretch(ch, m0, m1, top + rowh)
            self.y = top + rowh + (rg if r + n < len(items) else 0)
        self.fx, self.fw = x0, W

    def grid_columns(self, tmpl, W, cg, em):
        tmpl = tmpl.strip()
        if not tmpl or tmpl == 'none':
            return [W]
        m = re.match(r'repeat\(\s*(auto-fill|auto-fit|\d+)\s*,\s*(.+)\)$', tmpl)
        if m:
            what, track = m.group(1), m.group(2).strip()
            if what.isdigit():
                tracks = [track] * int(what)
            else:
                mm = re.match(r'minmax\(\s*([^,]+),', track)
                mn = self.length(mm.group(1) if mm else track, em, W) or 200
                k = max(1, int((W + cg) // (mn + cg)))
                tracks = ['1fr'] * k
        else:
            tracks = re.findall(r'minmax\([^)]*\)|repeat\([^)]*\)|\S+', tmpl)
        fixed, frs, out = 0.0, 0.0, []
        for t in tracks:
            if t.startswith('minmax('):
                t = t[7:-1].split(',')[-1].strip()
            if t.endswith('fr'):
                v = self.length(t, em, W) or 1
                out.append(('fr', v))
                frs += v
            else:
                v = self.length(t, em, W)
                v = v if v is not None else W / max(1, len(tracks))
                out.append(('px', v))
                fixed += v
        rest = max(0.0, W - fixed - cg * (len(out) - 1))
        return [v if k == 'px' else rest * v / max(frs, 1e-6) for k, v in out]

    # -- inline-blocks (styled buttons, badges, inline-flex) -------------------------------
    def wants_inline_box(self, node, st):
        css = node.css or {}
        d = css.get('display', '').strip()
        if d in ('inline-block', 'inline-flex', 'inline-grid'):
            return True
        if node.tag == 'button' and self.rich and d != 'none':
            return True
        return False

    def inline_box(self, node, st):
        box = self.box_of(node, st) or {'margin': [0] * 4, 'padding': [0] * 4, 'border': [0] * 4, 'width': None,
                                        'max-width': None, 'min-width': None, 'height': None, 'min-height': None,
                                        'bg': None, 'border_color': None, 'radius': 0, 'display': '',
                                        'flex-basis': None}
        horiz = sum(box['padding'][i] + box['border'][i] for i in (1, 3))
        mn, mx = self.measure(node, st)
        w = box['width'] if box['width'] is not None else mx + horiz + 1
        if box['min-width'] is not None:
            w = max(w, box['min-width'])
        if box['max-width'] is not None:
            w = min(w, box['max-width'])
        w = max(4, min(self.fw, w))
        ml = box['margin'][3] if isinstance(box['margin'][3], (int, float)) else 0
        mr = box['margin'][1] if isinstance(box['margin'][1], (int, float)) else 0
        saved = (self.atoms, self.items, self.regions, self.boxes, self.controls, self.y, self.fx, self.fw,
                 self.pending_margin, self.align, self.marker, self.list_stack, self.floats)
        self.atoms, self.items, self.regions, self.boxes, self.controls = [], [], [], [], []
        self.y, self.fx, self.fw, self.pending_margin, self.marker, self.list_stack = 0, 0, w, 0, None, []
        self.floats = []
        try:
            disp = box['display']
            if node.tag == 'button' and disp not in ('flex', 'inline-flex', 'grid', 'inline-grid'):
                box = dict(box, display='')
                if st.align == 'left':
                    st = st.copy()
                    st.align = 'center'
                    st.el = node
            self.box_block(node, st, box, 0, 0)
            h = self.y
            group = (self.items, self.regions, self.boxes, self.controls)
        finally:
            (self.atoms, self.items, self.regions, self.boxes, self.controls, self.y, self.fx, self.fw,
             self.pending_margin, self.align, self.marker, self.list_stack, self.floats) = saved
        self.atoms.append(('box', w + ml + mr, max(1, h), group, ml))

    def place_box(self, atom, x, base):
        _k, w, h, group, ml = atom
        desc = min(h * 0.28, 6)
        dx, dy = x + ml, base + desc - h
        items, regions, boxes, controls = group
        self.items.extend(_moved(it, int(dx), int(dy)) for it in items)
        self.regions.extend((r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy, r[4]) for r in regions)
        self.boxes.extend((r[0] + dx, r[1] + dy, r[2] + dx, r[3] + dy, r[4]) for r in boxes)
        for c in controls:
            c.x += int(dx)
            c.y += int(dy)
            self.controls.append(c)

    @staticmethod
    def box_metrics(atom):
        h = atom[2]
        desc = min(h * 0.28, 6)
        return h - desc, desc


def _moved(it, dx, dy):
    k = it[0]
    if k == 't':
        return (k, it[1] + dx, it[2] + dy) + it[3:]
    if k in ('r', 'o', 'b'):
        return (k, it[1] + dx, it[2] + dy, it[3] + dx, it[4] + dy) + it[5:]
    if k == 'i':
        return (k, it[1] + dx, it[2] + dy) + it[3:]
    return it          # 'c' items move through their control object


class FloatMixin:
    """CSS floats: boxes pushed to the left/right edge with line boxes flowing around them."""

    def float_side(self, node):
        css = node.css or {}
        f = css.get('float', '').strip()
        if f in ('left', 'right', 'inline-start', 'inline-end'):
            pos = css.get('position', '').strip()
            if pos in ('absolute', 'fixed'):
                return None
            return 'left' if f in ('left', 'inline-start') else 'right'
        return None

    def float_offsets(self, y, h):
        """(left, right) space taken by floats over [y, y+h) within the current column."""
        lo = ro = 0.0
        x0, x1 = self.fx, self.fx + self.fw
        for fx0, fy0, fx1, fy1, side in self.floats:
            if fy1 <= y or fy0 >= y + h or fx1 <= x0 or fx0 >= x1:
                continue
            if side == 'left':
                lo = max(lo, fx1 - x0)
            else:
                ro = max(ro, x1 - fx0)
        return lo, ro

    def float_bottom(self, side='both'):
        ys = [f[3] for f in self.floats if side == 'both' or f[4] == side]
        return max(ys) if ys else None

    def clear_floats(self, css):
        c = (css or {}).get('clear', '').strip()
        if c in ('left', 'right', 'both', 'inline-start', 'inline-end'):
            side = {'inline-start': 'left', 'inline-end': 'right'}.get(c, c)
            b = self.float_bottom(side)
            if b is not None:
                self.ensure_margin()
                self.y = max(self.y, b)

    def float_box(self, node, st, side):
        self.flush()
        self.ensure_margin()
        self.clear_floats(node.css)
        box = self.box_of(node, st) or {'margin': [0] * 4, 'padding': [0] * 4, 'border': [0] * 4, 'width': None,
                                        'max-width': None, 'min-width': None, 'height': None, 'min-height': None,
                                        'bg': None, 'border_color': None, 'radius': 0, 'display': '',
                                        'flex-basis': None}
        mt, mr, mb, ml = [m if isinstance(m, (int, float)) else 0 for m in box['margin']]
        horiz = sum(box['padding'][i] + box['border'][i] for i in (1, 3))
        if node.tag == 'table' and box['width'] is None:
            mn, mx = self.measure_table(node, st)
            w = min(mx, self.fw * 0.5)
        elif node.tag == 'img':
            w = None
        elif box['width'] is not None:
            w = box['width']
        else:
            mn, mx = self.measure(node, st)
            w = min(mx + horiz + 1, self.fw * 0.6)
        if w is not None and box['max-width'] is not None:
            w = min(w, box['max-width'])
        # lay it out on its own
        saved = (self.atoms, self.items, self.regions, self.boxes, self.controls, self.y, self.fx, self.fw,
                 self.pending_margin, self.align, self.marker, self.list_stack, self.floats)
        self.atoms, self.items, self.regions, self.boxes, self.controls = [], [], [], [], []
        self.y, self.fx, self.pending_margin, self.marker, self.list_stack, self.floats = 0, 0, 0, None, [], []
        self.fw = w if w is not None else saved[7] * 0.6
        try:
            if node.tag == 'img':
                self.image(node, st)
                self.flush()
                w = max([it[1] + it[3] for it in self.items if it[0] == 'i'] + [1])
            elif node.tag == 'table':
                self.table(node, st)
            else:
                nb = dict(box)
                nb['margin'] = [0, 0, 0, 0]
                nb['width'] = None
                nb['max-width'] = None
                self.box_block(node, st, nb, 0, 0)
            self.flush()
            h = self.y + self.pending_margin
            group = (self.items, self.regions, self.boxes, self.controls)
            if node.tag == 'table':
                w = max([it[3] for it in self.items if it[0] in ('r', 'o', 'b')] + [w or 1])
        finally:
            (self.atoms, self.items, self.regions, self.boxes, self.controls, self.y, self.fx, self.fw,
             self.pending_margin, self.align, self.marker, self.list_stack, self.floats) = saved
        w = min(w, self.fw)
        # find a spot: beside existing floats if there is room, else below them
        y = self.y + mt
        for _ in range(20):
            lo, ro = self.float_offsets(y, h)
            if lo + ro + w + ml + mr <= self.fw or (not lo and not ro):
                break
            nxt = [f[3] for f in self.floats if f[1] <= y < f[3]]
            if not nxt:
                break
            y = min(nxt)
        if side == 'right':
            x = self.fx + self.fw - ro - mr - w
        else:
            x = self.fx + lo + ml
        self.place_box(('box', w, h, group, 0), x, y + h - min(h * 0.28, 6))
        self.floats.append((x - ml, y - mt, x + w + mr, y + h + mb, side))
        self.boxes.append((x, y, x + w, y + h, node))

    def measure_table(self, node, st):
        rows, _cap = self.rows_of(node)
        mn = mx = 0
        for _tr, cells in rows:
            a = b = 0
            for cell in cells:
                m1, m2 = self.measure(cell, self.style_for(cell, st))
                a += m1 + 8
                b += m2 + 8
            mn, mx = max(mn, a), max(mx, b)
        return mn, mx
