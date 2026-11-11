"""Lay a parsed page out into a display list Webinator can paint.

Normal flow only: blocks stack, inline content wraps into lines (with real
font metrics and baselines), lists get markers, tables use the automatic
table width algorithm (min/max content per column), images keep their
aspect ratio, and form controls sit inline. No floats, no positioning.

Display items (all coordinates in page pixels):
  ('t', x, baseline, text, font, color, underline, strike, link)
  ('r', x0, y0, x1, y1, color)          filled rectangle
  ('o', x0, y0, x1, y1, color)          outline
  ('i', x, y, w, h, src, link, alt)     image
  ('c', ctl)                            form control (ctl has x, y, w, h)
"""
import base64
import io
import re
import urllib.parse

from PIL import Image

from .. import gfx
from .dom import parse_color
from .cssbox import CSSBoxMixin, FloatMixin, color_of, emoji_image, is_emoji_text, split_emoji

# CSS properties that make an element worth laying out as a real box
BOX_PROPS = ('margin', 'margin-top', 'margin-bottom', 'margin-left', 'margin-right', 'padding', 'padding-top',
             'padding-bottom', 'padding-left', 'padding-right', 'border', 'border-top', 'border-bottom', 'border-left',
             'border-right', 'border-width', 'border-radius', 'background', 'background-color', 'width', 'max-width',
             'min-width', 'height', 'min-height', 'padding-inline', 'padding-block', 'margin-inline', 'margin-block')
BLOCK = {'address', 'article', 'aside', 'blockquote', 'center', 'dd', 'details', 'dialog', 'div', 'dl', 'dt',
         'fieldset', 'figcaption', 'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hgroup',
         'hr', 'li', 'main', 'menu', 'nav', 'ol', 'p', 'pre', 'section', 'summary', 'table', 'ul', 'caption',
         'legend', 'body', 'listing', 'xmp', 'plaintext', 'noscript', 'tr', 'tbody', 'thead', 'tfoot'}
HEAD_SIZE = {'h1': 2.0, 'h2': 1.5, 'h3': 1.25, 'h4': 1.08, 'h5': 0.95, 'h6': 0.85}
MARGIN = {'p': 0.85, 'h1': 0.6, 'h2': 0.7, 'h3': 0.8, 'h4': 0.9, 'h5': 0.9, 'h6': 0.9, 'ul': 0.6, 'ol': 0.6,
          'dl': 0.6, 'blockquote': 0.8, 'pre': 0.7, 'figure': 0.8, 'table': 0.3, 'form': 0.2, 'hr': 0.4,
          'fieldset': 0.5, 'address': 0.6, 'details': 0.4}
LINK_COLOR = (0, 102, 204)
VISITED_COLOR = (110, 50, 160)
FONT_SIZE_ATTR = {'1': 0.63, '2': 0.82, '3': 1.0, '4': 1.13, '5': 1.5, '6': 2.0, '7': 3.0}
WS = re.compile(r'[ \t\n\r\f]+')


class Style:
    __slots__ = ('size', 'bold', 'italic', 'mono', 'serif', 'color', 'link', 'under', 'strike', 'pre', 'align',
                 'nowrap', 'bg', 'hidden', 'el', 'tt', 'ellip', 'nolist')

    def __init__(self, size):
        self.size = size
        self.bold = self.italic = self.mono = self.serif = False
        self.color = (0, 0, 0)
        self.link = None
        self.under = self.strike = self.pre = self.nowrap = self.hidden = False
        self.align = 'left'
        self.bg = None
        self.el = None                   # the element this style belongs to (for hit testing)
        self.tt = None                   # text-transform
        self.ellip = False               # text-overflow: ellipsis on a nowrap line
        self.nolist = False              # list-style: none (inherited, like in CSS)

    def copy(self):
        s = Style.__new__(Style)
        for k in self.__slots__:
            setattr(s, k, getattr(self, k))
        s.bg = None
        return s

    def font(self):
        size = max(7, int(round(self.size)))
        if self.mono:
            return gfx.font(size, self.bold, 'mono')
        if self.serif:
            return gfx.font(size, self.bold, 'serif-italic' if self.italic else 'serif')
        return gfx.font(size, self.bold, 'italic' if self.italic else 'sans')


class Ctl:
    """A form control. Kept across relayouts (keyed by node) so typing survives."""

    def __init__(self, node, kind):
        self.node = node
        self.kind = kind                 # text password checkbox radio submit select textarea button
        self.form = node.form
        self.name = node.get('name', '')
        self.value = node.get('value', '')
        self.checked = 'checked' in node.attrs
        self.options = []
        self.label = ''
        self.cur = len(self.value)
        self.x = self.y = self.w = self.h = 0
        if kind == 'textarea':
            self.value = node.text_content()
        if kind == 'select':
            for o in node.iter():
                if o.tag == 'option':
                    v = o.get('value', o.text_content().strip())
                    self.options.append((v, ' '.join(o.text_content().split())))
                    if 'selected' in o.attrs:
                        self.value = v
            if self.options and not any(v == self.value for v, _ in self.options):
                self.value = self.options[0][0]

    def display(self):
        if self.kind == 'select':
            return next((t for v, t in self.options if v == self.value), '')
        return self.value


def _int(v, default=None):
    try:
        return int(str(v).strip().rstrip('px').split('.')[0])
    except (ValueError, TypeError):
        return default


class Layout(FloatMixin, CSSBoxMixin):
    def __init__(self, doc, width, base, images, zoom=1.0, visited=(), controls=None, rich=False):
        self.doc = doc
        self.rich = rich                 # the page runs JavaScript: style its buttons as boxes
        self.W = max(200, width)
        self.base = base
        self.images = images             # url -> PIL image (or None while loading / failed)
        self.zoom = zoom
        self.visited = visited
        self.ctl_store = controls if controls is not None else {}
        self.items = []
        self.regions = []                # (x0, y0, x1, y1, link)
        self.boxes = []                  # (x0, y0, x1, y1, node): what element is where
        self.controls = []
        self.anchors = {}
        self.want = set()                # image urls the page needs
        self.links = []
        self.atoms = []
        self.y = 0
        self.fx = 0
        self.fw = self.W
        self.align = 'left'
        self.pending_margin = 0
        self.marker = None
        self.list_stack = []
        self._wcache = {}
        self._mcache = {}
        self.img_radius = {}
        self.floats = []                 # (x0, y0, x1, y1, side) of floated boxes
        self.line_lo = 0.0               # left float space for the line being placed
        self.line_w = None
        root = doc.root
        self.bg = parse_color(root.get('bgcolor')) or (255, 255, 255)
        if root.css:
            self.bg = color_of(root.css.get('background-color') or root.css.get('background', '')) or self.bg
        self.base_style = Style(15 * zoom)
        self.base_style.el = root
        self.base_style.color = parse_color(root.get('text')) or (0, 0, 0)
        if root.css and parse_color(root.css.get('color')):
            self.base_style.color = parse_color(root.css.get('color'))
        self.link_color = parse_color(root.get('link')) or LINK_COLOR

    # -- helpers -------------------------------------------------------------------
    def url(self, href):
        return urllib.parse.urljoin(self.base, (href or '').strip())

    def width_of(self, font, text):
        k = (id(font), text)
        w = self._wcache.get(k)
        if w is None:
            if is_emoji_text(text):
                w = sum(font.size * 1.18 if emo else font.getlength(t) for emo, t in split_emoji(text))
            else:
                w = font.getlength(text)
            self._wcache[k] = w
        return w

    def style_for(self, node, parent):
        st = parent.copy()
        st.el = node
        t = node.tag
        if t in HEAD_SIZE:
            st.size = self.base_style.size * HEAD_SIZE[t]
            st.bold = True
        elif t in ('b', 'strong', 'th', 'dt', 'summary', 'legend'):
            st.bold = True
        elif t in ('i', 'em', 'cite', 'var', 'dfn', 'address'):
            st.italic = True
        elif t in ('u', 'ins'):
            st.under = True
        elif t in ('s', 'strike', 'del'):
            st.strike = True
        elif t in ('code', 'kbd', 'samp', 'tt'):
            st.mono = True
            st.size = parent.size * 0.92
        elif t in ('pre', 'listing', 'xmp', 'plaintext'):
            st.mono, st.pre = True, True
            st.size = parent.size * 0.9
        elif t == 'small' or t in ('sub', 'sup'):
            st.size = parent.size * 0.83
        elif t == 'big':
            st.size = parent.size * 1.17
        elif t == 'center':
            st.align = 'center'
        elif t == 'mark':
            st.bg = (255, 240, 120)
        elif t == 'a' and node.get('href') is not None and not node.get('href', '').startswith('javascript:'):
            st.link = self.url(node.get('href'))
            st.color = VISITED_COLOR if st.link in self.visited else self.link_color
            st.under = True
        elif t == 'font':
            c = parse_color(node.get('color'))
            if c:
                st.color = c
            sz = node.get('size', '').strip()
            if sz in FONT_SIZE_ATTR:
                st.size = self.base_style.size * FONT_SIZE_ATTR[sz]
            elif sz[:1] in '+-' and sz[1:].isdigit():
                st.size = self.base_style.size * FONT_SIZE_ATTR.get(str(max(1, min(7, 3 + int(sz)))), 1.0)
            face = node.get('face', '').lower()
            if 'courier' in face or 'mono' in face:
                st.mono = True
            elif 'times' in face or 'serif' in face and 'sans' not in face:
                st.serif = True
        if t in ('td', 'th') and node.get('nowrap') is not None:
            st.nowrap = True
        al = node.get('align', '').lower()
        if al in ('left', 'right', 'center', 'justify') and t not in ('img', 'table', 'input'):
            st.align = 'left' if al == 'justify' else al
        elif t == 'th' and not al:
            st.align = 'center'
        bg = parse_color(node.get('bgcolor'))
        if bg:
            st.bg = bg
        if 'hidden' in node.attrs and t != 'input':
            st.hidden = True
        css = node.css
        if css:
            if css.get('display', '').strip() == 'none' or css.get('visibility', '').strip() == 'hidden' or \
                    self.visually_hidden(css, st):
                st.hidden = True
            ls = (css.get('list-style') or css.get('list-style-type') or '').strip()
            if ls:
                st.nolist = 'none' in ls.split()
            font = css.get('font', '').strip()
            if font and font not in ('inherit', 'initial'):
                self.font_shorthand(font, st, parent)
            c = parse_color(css.get('color'))
            if c and not st.link:
                st.color = c
            b = css.get('background-color') or css.get('background', '')
            if b:
                bc = parse_color(b.split(' ')[0] if not b.startswith('rgb') else b)
                if bc and bc != (255, 255, 255):
                    st.bg = bc
            fw = css.get('font-weight', '')
            if fw in ('bold', 'bolder') or (fw.isdigit() and int(fw) >= 600):
                st.bold = True
            elif fw in ('normal', '400', 'lighter') and t not in HEAD_SIZE:
                st.bold = False
            if css.get('font-style') == 'italic':
                st.italic = True
            ta = css.get('text-align', '')
            if ta in ('left', 'right', 'center'):
                st.align = ta
            td = css.get('text-decoration', '')
            if 'none' in td:
                st.under = False
            elif 'underline' in td:
                st.under = True
            if 'line-through' in td:
                st.strike = True
            ff = css.get('font-family', '').lower()
            if 'monospace' in ff or 'courier' in ff:
                st.mono = True
            fs = css.get('font-size', '')
            m = re.match(r'([\d.]+)(px|em|rem|%|pt)?$', fs)
            if m:
                v = float(m.group(1))
                unit = m.group(2) or 'px'
                st.size = {'px': v * self.zoom, 'pt': v * 1.33 * self.zoom, 'em': parent.size * v,
                           'rem': self.base_style.size * v, '%': parent.size * v / 100}[unit]
                st.size = max(8, min(st.size, 80 * self.zoom))
            tt = css.get('text-transform', '').strip()
            if tt in ('uppercase', 'lowercase', 'capitalize'):
                st.tt = tt
            elif tt == 'none':
                st.tt = None
            if css.get('text-overflow', '').strip() == 'ellipsis':
                st.ellip = True
            ws = css.get('white-space', '')
            if ws.startswith('pre'):
                st.pre = True
            elif ws == 'nowrap':
                st.nowrap = True
        if st.ellip and not st.nowrap and css and css.get('overflow', '') == 'hidden':
            st.nowrap = True
        return st

    def visually_hidden(self, css, st):
        """The usual ways pages hide things from sight while keeping them for screen readers
        (skip links, menu checkboxes, off-screen panels)."""
        if css.get('opacity', '').strip() in ('0', '0.0', '0%'):
            return True
        clip = css.get('clip', '').replace(' ', '')
        if clip.startswith(('rect(0', 'rect(1px', 'rect(0px')):
            return True
        cp = css.get('clip-path', '').replace(' ', '')
        if cp.startswith(('inset(50%', 'inset(100%', 'circle(0', 'polygon(0000')):
            return True
        pos = css.get('position', '').strip()
        if pos in ('absolute', 'fixed'):
            for side in ('left', 'top', 'right'):
                v = self.length(css.get(side), st.size, self.W)
                if v is not None and v < -400:
                    return True
            w = self.length(css.get('width'), st.size, 0)
            h = self.length(css.get('height'), st.size, 0)
            if w is not None and h is not None and w <= 1.5 and h <= 1.5:
                return True
            tr = css.get('transform', '')
            if 'translate' in tr and ('-100%' in tr or '-9999' in tr):
                return True
        ti = self.length(css.get('text-indent'), st.size, 0)
        if ti is not None and ti < -400:
            return True
        return False

    def font_shorthand(self, font, st, parent):
        """font: [italic] [bold|600] <size>[/<line-height>] <family>"""
        toks = font.replace(',', ' , ').split()
        for i, tok in enumerate(toks):
            low = tok.lower()
            if low == 'italic' or low == 'oblique':
                st.italic = True
            elif low in ('bold', 'bolder') or (low.isdigit() and int(low) >= 600):
                st.bold = True
            elif low in ('normal', 'lighter') or (low.isdigit() and int(low) < 600):
                pass
            else:
                size = low.split('/')[0]
                m = re.match(r'([\d.]+)(px|em|rem|%|pt)$', size)
                if m:
                    v, unit = float(m.group(1)), m.group(2)
                    st.size = max(8, min(80 * self.zoom, {'px': v * self.zoom, 'pt': v * 1.33 * self.zoom,
                                                          'em': parent.size * v, 'rem': 16 * self.zoom * v,
                                                          '%': parent.size * v / 100}[unit]))
                    fam = ' '.join(toks[i + 1:]).lower()
                    if 'mono' in fam or 'courier' in fam:
                        st.mono = True
                    elif ('serif' in fam and 'sans' not in fam) or 'georgia' in fam or 'times' in fam:
                        st.serif = True
                    break

    def is_block(self, node):
        if node.css and node.css.get('display'):
            d = node.css['display'].strip()
            if d in ('block', 'flex', 'grid', 'list-item', 'table'):
                return True
            if d.startswith('inline'):
                return False
        return node.tag in BLOCK

    # -- running ------------------------------------------------------------------------
    def run(self):
        st = self.base_style
        self.fx, self.fw = 16, self.W - 32
        self.y = 12
        rc = self.doc.root.css or {}
        if any(k in rc for k in BOX_PROPS):
            # <html>/<body> styled by the page: its margin, max-width, padding, alignment
            box = self.box_of(self.doc.root, st, self.W)
            m, pad = box['margin'], box['padding']
            has_margin = any(k.startswith('margin') for k in rc)
            ml = m[3] if isinstance(m[3], (int, float)) else (0 if has_margin else 16)
            mr = m[1] if isinstance(m[1], (int, float)) else (0 if has_margin else 16)
            self.fx, self.fw = ml, self.W - ml - mr
            w = box['width']
            if box['max-width'] is not None:
                w = min(w if w is not None else self.fw, box['max-width'])
            if w is not None and w < self.fw:
                if m[3] == 'auto' and m[1] == 'auto':
                    self.fx += (self.fw - w) / 2
                self.fw = w
            self.fx += pad[3]
            self.fw -= pad[3] + pad[1]
            top = m[0] if isinstance(m[0], (int, float)) else (0 if has_margin else 12)
            self.y = top + pad[0]
        ta = rc.get('text-align', '').strip()
        if ta in ('left', 'right', 'center'):
            st.align = self.align = ta
        self.walk(self.doc.root, st)
        self.flush()
        fb = self.float_bottom()
        self.height = int(max(self.y, fb or 0) + 24)
        return self

    def ensure_margin(self):
        if self.pending_margin:
            self.y += self.pending_margin
            self.pending_margin = 0

    def add_margin(self, m):
        self.pending_margin = max(self.pending_margin, m)

    def anchor(self, node):
        for k in ('id', 'name'):
            v = node.get(k)
            if v and v not in self.anchors:
                self.anchors[v] = self.y + self.pending_margin

    def walk(self, node, st):
        for ch in node.children:
            if ch.tag == '#text':
                self.text(ch.text, st)
                continue
            if ch.tag == 'head' or ch.tag == 'option':
                continue
            cst = self.style_for(ch, st)
            if cst.hidden:
                continue
            self.anchor(ch)
            t = ch.tag
            side = self.float_side(ch) if ch.css else None
            if side:
                self.float_box(ch, cst, side)
                continue
            if t in ('br',):
                self.atoms.append(('br', cst))
            elif t == 'img':
                self.image(ch, cst)
            elif t not in ('input', 'select', 'textarea') and self.wants_inline_box(ch, cst):
                self.inline_box(ch, cst)
            elif t in ('input', 'select', 'textarea', 'button'):
                self.control(ch, cst)
            elif t == 'wbr':
                pass
            elif self.is_block(ch):
                self.flush()
                if ch.css and 'clear' in ch.css:
                    self.clear_floats(ch.css)
                self.block(ch, cst)
            else:
                if cst.bg and not st.bg:
                    pass
                self.walk(ch, cst)

    def block(self, node, st, flex_item=False):
        t = node.tag
        if t == 'table':
            return self.table(node, st)
        css = node.css
        if css and t != 'hr' and (any(k in css for k in BOX_PROPS) or
                                  css.get('display', '').strip() in ('flex', 'inline-flex', 'grid', 'inline-grid')):
            box = self.box_of(node, st)
            mt, mb = box['margin'][0], box['margin'][2]
            dflt = 0 if flex_item else MARGIN.get(t, 0) * st.size
            mt = mt if isinstance(mt, (int, float)) else dflt
            mb = mb if isinstance(mb, (int, float)) else dflt
            return self.box_block(node, st, box, mt, mb)
        if t == 'hr':
            self.add_margin(st.size * 0.5)
            self.ensure_margin()
            self.items.append(('r', self.fx, self.y, self.fx + self.fw, self.y + 1, (190, 195, 200)))
            self.items.append(('r', self.fx, self.y + 1, self.fx + self.fw, self.y + 2, (240, 242, 245)))
            self.y += 2
            self.add_margin(st.size * 0.5)
            return
        m = MARGIN.get(t, 0) * st.size
        if t in ('ul', 'ol', 'dl', 'menu') and self.list_stack:
            m = 0
        self.add_margin(m)
        save = (self.fx, self.fw, self.align)
        self.align = st.align
        if t in ('ul', 'ol', 'menu'):
            ind = int(st.size * 1.9)
            self.fx += ind
            self.fw -= ind
            start = _int(node.get('start'), 1)
            self.list_stack.append([t, start, node.get('type', '')])
        elif t == 'blockquote':
            ind = int(st.size * 2.2)
            self.fx += ind
            self.fw -= ind * 2
        elif t == 'dd':
            ind = int(st.size * 2.2)
            self.fx += ind
            self.fw -= ind
        elif t == 'li' and not st.nolist:
            self.marker = (self.list_marker(node), st)
        bg_i = None
        pad = 0
        box_y0 = self.y + self.pending_margin
        if t == 'pre' or st.bg or t == 'fieldset':
            self.ensure_margin()
            bg_i = len(self.items)
            y0 = self.y
            pad = 8 if t == 'pre' or t == 'fieldset' else 3
            self.fx += pad
            self.fw -= pad * 2
            self.y += pad
        self.walk(node, st)
        self.flush()
        self.marker = None
        self.boxes.append((save[0], box_y0, save[0] + save[1], self.y, node))
        if bg_i is not None:
            self.y += pad
            col = st.bg or (246, 248, 250)
            self.items.insert(bg_i, ('r', save[0], y0, save[0] + save[1], self.y, col))
            if t in ('pre', 'fieldset'):
                self.items.insert(bg_i + 1, ('o', save[0], y0, save[0] + save[1], self.y, (215, 220, 226)))
        if t in ('ul', 'ol', 'menu'):
            self.list_stack.pop()
        self.fx, self.fw, self.align = save
        self.add_margin(m)

    def list_marker(self, node):
        if not self.list_stack:
            return '•'
        top = self.list_stack[-1]
        if top[0] == 'ol':
            n = top[1]
            top[1] += 1
            if _int(node.get('value')) is not None:
                n = _int(node.get('value'))
                top[1] = n + 1
            kind = top[2]
            if kind == 'a':
                return '%s.' % chr(96 + ((n - 1) % 26) + 1)
            if kind == 'A':
                return '%s.' % chr(64 + ((n - 1) % 26) + 1)
            if kind in ('i', 'I'):
                r = _roman(n)
                return (r.lower() if kind == 'i' else r) + '.'
            return '%d.' % n
        depth = sum(1 for e in self.list_stack if e[0] != 'ol')
        return ['•', '◦', '▪'][(depth - 1) % 3]

    # -- inline atoms ---------------------------------------------------------------------
    def text(self, text, st):
        if st.tt == 'uppercase':
            text = text.upper()
        elif st.tt == 'lowercase':
            text = text.lower()
        elif st.tt == 'capitalize':
            text = text.title()
        if st.pre:
            parts = text.replace('\t', '    ').split('\n')
            for i, part in enumerate(parts):
                if i:
                    self.atoms.append(('br', st))
                if part:
                    self.atoms.append(('w', part, st))
            return
        words = WS.split(text)
        if text[:1].isspace() if text else False:
            self.atoms.append(('s', st))
        for i, w in enumerate(words):
            if not w:
                continue
            if i and self.atoms and self.atoms[-1][0] != 's':
                self.atoms.append(('s', st))
            self.atoms.append(('w', w, st))
        if text[-1:].isspace() and words and words[-1] == '':
            self.atoms.append(('s', st))

    def image(self, node, st):
        src = node.get('src') or node.get('data-src') or ''
        if not src:
            return
        url = src if src.startswith('data:') else self.url(src)
        w, h = _int(node.get('width')), _int(node.get('height'))
        cap = None
        if node.css:
            cw = self.length(node.css.get('width'), st.size, self.fw)
            ch = self.length(node.css.get('height'), st.size, 0) if '%' not in node.css.get('height', '') else None
            w = int(cw / self.zoom) if cw else w
            h = int(ch / self.zoom) if ch else h
            mw = self.length(node.css.get('max-width'), st.size, self.fw)
            if mw:
                cap = mw / self.zoom
        img = self.images.get(url)
        if url.startswith('data:') and url not in self.images:
            img = self.images[url] = decode_data_url(url)
        if img is None and url not in self.images:
            self.want.add(url)
        if img is not None:
            iw, ih = img.size
            if w and not h:
                h = max(1, int(ih * w / max(1, iw)))
            elif h and not w:
                w = max(1, int(iw * h / max(1, ih)))
            elif not w and not h:
                w, h = iw, ih
        if w and h and cap and w > cap:
            w, h = int(cap), max(1, int(h * cap / w))
        if not w or not h:
            alt = node.get('alt', '').strip()
            if alt and img is None:
                ast = st.copy()
                ast.color = (120, 120, 120)
                self.atoms.append(('w', '[%s]' % alt, ast))
            return
        w, h = int(w * self.zoom), int(h * self.zoom)
        self.atoms.append(('img', w, h, url, st.link, node.get('alt', ''), node))
        r = (node.css or {}).get('border-radius', '').split()
        if r:
            self.img_radius[id(node)] = 999 if r[0].endswith('%') else (self.length(r[0], st.size, 0) or 0)

    def control(self, node, st, force_w=None):
        t = node.tag
        if t == 'input':
            kind = node.get('type', 'text').lower()
            if kind == 'hidden':
                return
            kind = {'search': 'text', 'email': 'text', 'url': 'text', 'number': 'text', 'tel': 'text',
                    'image': 'submit', 'reset': 'button', 'date': 'text'}.get(kind, kind)
            if kind not in ('text', 'password', 'checkbox', 'radio', 'submit', 'button', 'file'):
                kind = 'text'
            if kind == 'file':
                kind = 'button'
        elif t == 'button':
            kind = 'submit' if node.get('type', 'submit').lower() == 'submit' else 'button'
        else:
            kind = t
        key = node.get('data-nep') or node.nid
        ctl = self.ctl_store.get(key)
        if ctl is None:
            ctl = self.ctl_store[key] = Ctl(node, kind)
            if t == 'button':
                ctl.label = ' '.join(node.text_content().split()) or node.get('aria-label') or node.get('title') or \
                    ('Submit' if kind == 'submit' and not node.get('data-nep') else '•')
            elif kind == 'submit':
                ctl.label = node.get('value') or node.get('alt') or 'Submit'
            elif kind == 'button':
                ctl.label = node.get('value') or 'Button'
            ctl.placeholder = node.get('placeholder', '')
        fs = self.base_style.size
        fnt = gfx.font(max(8, int(fs * 0.87)))
        css = node.css or {}
        ctl.css = None
        if css:
            bw, bc = self._border(css, st.size)
            r = css.get('border-radius', '').split()
            ctl.css = {'bg': color_of(css.get('background-color') or css.get('background')),
                       'fg': parse_color(css.get('color')) or (st.color if st.color != (0, 0, 0) else None),
                       'border': bc if any(bw) else None, 'bw': max(bw) if any(bw) else 0,
                       'radius': (self.length(r[0], st.size, 0) or 0) if r else 0}
            if not any(ctl.css.values()):
                ctl.css = None
            if css.get('font-size'):
                fnt = gfx.font(max(8, int(st.size)))
        cw = force_w if force_w is not None else self.length(css.get('width'), st.size, self.fw)
        if kind in ('text', 'password'):
            ctl.w = int(min(self.fw, max(60, (_int(node.get('size'), 22) * fs * 0.55))))
            ctl.h = int(fs * 1.75)
        elif kind in ('checkbox', 'radio'):
            ctl.w = ctl.h = int(fs * 0.95)
        elif kind in ('submit', 'button'):
            ctl.w = int(self.width_of(fnt, ctl.label) + fs * 1.6)
            ctl.h = int(fs * 1.8)
        elif kind == 'select':
            longest = max([self.width_of(fnt, tx) for _, tx in ctl.options] + [40])
            ctl.w = int(min(self.fw, longest + fs * 2.4))
            ctl.h = int(fs * 1.75)
        elif kind == 'textarea':
            ctl.w = int(min(self.fw, _int(node.get('cols'), 40) * fs * 0.6))
            ctl.h = int(_int(node.get('rows'), 3) * fs * 1.3 + 8)
        if cw is not None and kind in ('text', 'password', 'select', 'textarea', 'submit', 'button'):
            ctl.w = int(max(20, min(self.fw, cw) - 4))
        h = self.length(css.get('height'), st.size, 0) if css else None
        if h and kind in ('text', 'password', 'select', 'submit', 'button'):
            ctl.h = int(max(fs, h))
        ctl.font = fnt
        self.atoms.append(('ctl', ctl))

    # -- line building ----------------------------------------------------------------------
    def flush(self):
        atoms, self.atoms = self.atoms, []
        if not atoms:
            return
        if all(a[0] == 's' for a in atoms):
            return
        line = []          # (atom, width)
        cx = 0
        space = None
        clipped = None     # style whose text was cut off with an ellipsis
        lh = self.base_style.size * 1.4

        def line_room():
            # width of the next line, shortened by floats beside it (moving below them if too narrow)
            if not self.floats:
                self.line_lo, self.line_w = 0.0, None
                return self.fw
            for _ in range(20):
                y = self.y + self.pending_margin
                lo, ro = self.float_offsets(y, lh)
                if self.fw - lo - ro >= min(80, self.fw * 0.5) or (not lo and not ro):
                    break
                below = [f[3] for f in self.floats if f[1] <= y < f[3]]
                if not below:
                    break
                self.ensure_margin()
                self.y = min(below)
            self.line_lo, self.line_w = lo, self.fw - lo - ro
            return self.line_w
        W = line_room()
        for a in atoms:
            k = a[0]
            if clipped is not None and k in ('w', 's') and a[-1] is clipped:
                continue
            if k == 's':
                if line:
                    space = a[1]
                continue
            if k == 'br':
                self.place_line(line, a[1])
                line, cx, space = [], 0, None
                W = line_room()
                continue
            if k == 'w':
                fnt = a[2].font()
                w = self.width_of(fnt, a[1])
            elif k == 'img':
                w = a[1]
            elif k == 'box':
                w = a[1]
            else:
                w = a[1].w + 4
            sw = self.width_of(space.font(), ' ') if space is not None and line else 0
            nowrap = k == 'w' and (a[2].pre or a[2].nowrap)
            if k == 'w' and a[2].ellip and cx + sw + w > W:
                txt, st = a[1], a[2]
                room = W - cx - sw - self.width_of(fnt, '\u2026')
                while txt and self.width_of(fnt, txt) > room:
                    txt = txt[:-1]
                a = ('w', txt.rstrip() + '\u2026', st)
                w = self.width_of(fnt, a[1])
                clipped = st
            if line and cx + sw + w > W and not nowrap:
                self.place_line(line)
                line, cx, sw = [], 0, 0
                W = line_room()
            if line and sw:
                line.append((('sp', space), sw))
                cx += sw
            space = None
            if k == 'w' and w > W and not a[2].pre:
                # a word wider than the line: break it by characters
                txt, st = a[1], a[2]
                while txt:
                    n = len(txt)
                    while n > 1 and self.width_of(fnt, txt[:n]) > W - cx:
                        n -= 1
                    piece = txt[:n]
                    line.append((('w', piece, st), self.width_of(fnt, piece)))
                    txt = txt[n:]
                    if txt:
                        self.place_line(line)
                        line, cx = [], 0
                        W = line_room()
                    else:
                        cx += self.width_of(fnt, piece)
                continue
            line.append((a, w))
            cx += w
        if line:
            self.place_line(line)

    def place_line(self, line, br_style=None):
        self.ensure_margin()
        while line and line[-1][0][0] == 'sp':
            line.pop()
        if not line:
            st = br_style or self.base_style
            self.y += int(st.size * 1.35)
            return
        asc = desc = 0
        for a, w in line:
            k = a[0]
            if k in ('w', 'sp'):
                st = a[2] if k == 'w' else a[1]
                fa, fd = st.font().getmetrics()
                lead = st.size * 0.18
                asc = max(asc, fa + lead)
                desc = max(desc, fd + lead)
            elif k == 'img':
                asc = max(asc, a[2])
            elif k == 'box':
                ba, bd = self.box_metrics(a)
                asc = max(asc, ba)
                desc = max(desc, bd)
            elif k == 'ctl':
                asc = max(asc, a[1].h - 3)
                desc = max(desc, 5)
        total = sum(w for _, w in line)
        off = 0
        room = self.line_w if self.line_w is not None else self.fw
        if self.align == 'center':
            off = max(0, (room - total) / 2)
        elif self.align == 'right':
            off = max(0, room - total)
        base = self.y + asc
        x = self.fx + (self.line_lo if self.line_w is not None else 0) + off
        if self.marker:
            mtext, mst = self.marker
            fnt = mst.font()
            mw = self.width_of(fnt, mtext)
            self.items.append(('t', self.fx - mw - mst.size * 0.45, base, mtext, fnt, mst.color, False, False, None))
            self.marker = None
        # merge neighbouring words of the same style into one run
        run = None
        for a, w in line:
            k = a[0]
            if k in ('w', 'sp'):
                st = a[2] if k == 'w' else a[1]
                txt = a[1] if k == 'w' else ' '
                if run and run[1] is st:
                    run[2] += txt
                    run[3] += w
                else:
                    self._emit_run(run, base)
                    run = [x, st, txt, w]
            else:
                self._emit_run(run, base)
                run = None
                if k == 'box':
                    self.place_box(a, x, base)
                elif k == 'img':
                    _k, iw, ih, url, link, alt, inode = a
                    y0 = base - ih
                    self.items.append(('i', int(x), int(y0), iw, ih, url, link, alt,
                                       self.img_radius.get(id(inode), 0)))
                    self.boxes.append((x, y0, x + iw, base, inode))
                    if link:
                        self.regions.append((x, y0, x + iw, base, link))
                else:
                    ctl = a[1]
                    ctl.x, ctl.y = int(x + 2), int(base - ctl.h + 3)
                    self.items.append(('c', ctl))
                    self.controls.append(ctl)
            x += w
        self._emit_run(run, base)
        self.y = base + desc

    def _emit_run(self, run, base):
        if not run:
            return
        x, st, txt, w = run
        fnt = st.font()
        if st.bg:
            fa, fd = fnt.getmetrics()
            self.items.append(('r', x, base - fa, x + w, base + fd, st.bg))
        if is_emoji_text(txt):
            self._emit_emoji_run(x, base, txt, fnt, st)
        else:
            self.items.append(('t', x, base, txt, fnt, st.color, st.under, st.strike, st.link))
        fa, fd = fnt.getmetrics()
        if st.el is not None:
            self.boxes.append((x, base - fa, x + w, base + fd, st.el))
        if st.link:
            self.regions.append((x, base - fa, x + w, base + fd, st.link))

    def _emit_emoji_run(self, x, base, txt, fnt, st):
        fa, fd = fnt.getmetrics()
        px = max(8, int(fnt.size * 1.1))
        for emo, part in split_emoji(txt):
            if not emo:
                if part:
                    self.items.append(('t', x, base, part, fnt, st.color, st.under, st.strike, st.link))
                x += self.width_of(fnt, part)
                continue
            img = emoji_image(part, px)
            w = fnt.size * 1.18
            if img is None:
                self.items.append(('t', x, base, part, fnt, st.color, False, False, st.link))
            else:
                url = 'emoji:%s:%d' % (part, px)
                self.images[url] = img
                y0 = int(base - fa + (fa + fd - img.size[1]) / 2)
                self.items.append(('i', int(x + (w - img.size[0]) / 2), y0, img.size[0], img.size[1], url, st.link, part))
            x += w

    # -- tables ----------------------------------------------------------------------------
    def rows_of(self, table):
        rows = []
        caption = None

        def scan(n):
            nonlocal caption
            for c in n.children:
                if c.tag == 'tr':
                    rows.append((c, [d for d in c.children if d.tag in ('td', 'th')]))
                elif c.tag in ('tbody', 'thead', 'tfoot'):
                    scan(c)
                elif c.tag == 'caption':
                    caption = c
                elif c.tag in ('td', 'th'):
                    rows.append((n, [c]))
        scan(table)
        return rows, caption

    def measure(self, node, st):
        key = (node.nid, round(st.size, 1), st.bold, st.mono)
        if key in self._mcache:
            return self._mcache[key]
        state = {'mn': 0.0, 'mx': 0.0, 'line': 0.0}

        def br():
            state['mx'] = max(state['mx'], state['line'])
            state['line'] = 0.0

        def word(w):
            state['mn'] = max(state['mn'], w)
            state['line'] += w

        def go(n, s, indent=0):
            for c in n.children:
                if c.tag == '#text':
                    fnt = s.font()
                    if s.tt:
                        c = type('T', (), {'text': c.text.upper() if s.tt == 'uppercase' else
                                           (c.text.lower() if s.tt == 'lowercase' else c.text.title())})()
                    if s.pre:
                        for i, part in enumerate(c.text.replace('\t', '    ').split('\n')):
                            if i:
                                br()
                            word(self.width_of(fnt, part) + indent)
                        continue
                    sw = self.width_of(fnt, ' ')
                    ws = [x for x in WS.split(c.text) if x]
                    if s.nowrap:
                        word(sum(self.width_of(fnt, x) for x in ws) + sw * max(0, len(ws) - 1))
                        continue
                    for x in ws:
                        word(self.width_of(fnt, x))
                        state['line'] += sw
                    continue
                cs = self.style_for(c, s)
                if cs.hidden or c.tag in ('head', 'option'):
                    continue
                if c.tag == 'br':
                    br()
                elif c.tag == 'img':
                    w = self.length((c.css or {}).get('width'), cs.size, 0) if (c.css or {}).get('width', '').strip() \
                        not in ('', 'auto') and '%' not in (c.css or {}).get('width', '') else None
                    w = w / self.zoom if w else _int(c.get('width'))
                    src = c.get('src') or ''
                    im = self.images.get(src if src.startswith('data:') else self.url(src))
                    if w is None and im is not None:
                        w = im.size[0]
                    word((w or 0) * self.zoom)
                elif c.tag not in ('input', 'select', 'textarea') and self.wants_inline_box(c, cs):
                    m1, m2 = self._measure_box(c, cs)
                    state['mn'] = max(state['mn'], m1)
                    state['line'] += m2
                elif c.tag in ('input', 'select', 'textarea', 'button'):
                    m1, m2 = self.content_size(c, cs)
                    word(m2)
                elif self.is_block(c) and (c.css or {}).get('display', '').strip() in ('flex', 'grid') or \
                        (self.is_block(c) and c.css and any(k in c.css for k in BOX_PROPS)):
                    br()
                    m1, m2 = self._measure_box(c, cs)
                    state['mn'] = max(state['mn'], m1 + indent)
                    state['mx'] = max(state['mx'], m2 + indent)
                    br()
                elif c.tag == 'table':
                    br()
                    rows, _cap = self.rows_of(c)
                    tmn = tmx = 0
                    for _tr, cells in rows:
                        a = b = 0
                        for cell in cells:
                            m1, m2 = self.measure(cell, self.style_for(cell, cs))
                            a += m1 + 6
                            b += m2 + 6
                        tmn, tmx = max(tmn, a), max(tmx, b)
                    tw = _int(c.get('width')) if '%' not in (c.get('width') or '') else None
                    if tw:
                        tmn = max(tmn, tw * self.zoom)
                        tmx = max(tmx, tw * self.zoom)
                    state['mn'] = max(state['mn'], tmn + indent)
                    state['mx'] = max(state['mx'], tmx + indent)
                elif self.is_block(c):
                    br()
                    ind = indent + (st.size * 2 if c.tag in ('ul', 'ol', 'blockquote', 'dd') else 0)
                    go(c, cs, ind)
                    br()
                else:
                    go(c, cs, indent)
        go(node, st)
        br()
        res = (state['mn'], state['mx'])
        self._mcache[key] = res
        return res

    def _measure_box(self, c, cs):
        """(min, max) width of an element counting its padding/border, flex rows side by side."""
        key = ('box', c.nid, round(cs.size, 1), cs.bold)
        if key in self._mcache:
            return self._mcache[key]
        self._mcache[key] = (0.0, 0.0)              # guard against loops
        css = c.css or {}
        box = self.box_of(c, cs, 0) or {'padding': [0] * 4, 'border': [0] * 4, 'width': None, 'margin': [0] * 4}
        horiz = sum(box['padding'][i] + box['border'][i] for i in (1, 3)) + \
            sum(m for m in (box['margin'][1], box['margin'][3]) if isinstance(m, (int, float)))
        disp = css.get('display', '').strip()
        direction = css.get('flex-direction', 'row')
        if disp in ('flex', 'inline-flex') and not direction.startswith('column'):
            kids = self.flex_children(c, cs)
            _rg, cg = self._gaps(css, cs.size, 0)
            sizes = [self.flex_props(k, ks, 0)[::3] for k, ks in kids]       # (basis, minw)
            m2 = sum(b for b, _m in sizes) + cg * max(0, len(sizes) - 1)
            m1 = max([m for _b, m in sizes] + [0]) if 'wrap' in css.get('flex-wrap', '') else \
                sum(m for _b, m in sizes) + cg * max(0, len(sizes) - 1)
        else:
            m1, m2 = self.measure(c, cs)
        if box.get('width') is not None and box['width'] > 0:
            m1 = m2 = box['width'] - horiz
        res = (m1 + horiz, m2 + horiz)
        self._mcache[key] = res
        return res

    def table(self, node, st):
        rows, caption = self.rows_of(node)
        if not rows:
            return
        self.add_margin(MARGIN['table'] * st.size)
        self.ensure_margin()
        border = _int(node.get('border'), 0) or 0
        if border == 0 and node.get('border') == '':
            border = 1
        pad = _int(node.get('cellpadding'), 2 if border else 1)
        spc = _int(node.get('cellspacing'), 2)
        pad = int(pad * self.zoom)
        spc = int(spc * self.zoom)
        # cells -> grid columns
        grid = []
        ncols = 0
        for tr, cells in rows:
            r = []
            for c in cells:
                span = max(1, min(50, _int(c.get('colspan'), 1) or 1))
                r.append((c, span))
            grid.append((tr, r))
            ncols = max(ncols, sum(s for _, s in r))
        mins = [0.0] * ncols
        maxs = [0.0] * ncols
        fixed = [None] * ncols
        avail = self.fw - (ncols + 1) * spc
        wattr = node.get('width', '')
        if wattr.endswith('%'):
            tw = self.fw * min(100, _int(wattr[:-1], 100)) / 100.0
        else:
            tw = _int(wattr)
            tw = min(self.fw, tw * self.zoom) if tw else None
        if node.css and node.css.get('width', '').strip() == '100%':
            tw = self.fw
        tr_styles = {}
        tst = st.copy()
        tst.align = 'left'           # the classic quirk: tables don't inherit text-align (e.g. from <center>)
        for tr, r in grid:
            trs = self.style_for(tr, tst) if tr.tag == 'tr' else tst
            tr_styles[tr.nid] = trs
            col = 0
            for c, span in r:
                cs = self.style_for(c, trs)
                if cs.hidden:
                    col += span
                    continue
                mn, mx = self.measure(c, cs)
                mn += 2 * pad
                mx += 2 * pad
                cw = c.get('width', '')
                if span == 1 and col < ncols:
                    mins[col] = max(mins[col], mn)
                    maxs[col] = max(maxs[col], mx)
                    if cw and not cw.endswith('%') and _int(cw):
                        fixed[col] = max(fixed[col] or 0, _int(cw) * self.zoom)
                    elif cw.endswith('%') and _int(cw[:-1]):
                        fixed[col] = max(fixed[col] or 0, (tw or avail) * _int(cw[:-1]) / 100.0)
                col += span
        for tr, r in grid:
            col = 0
            for c, span in r:
                if span > 1 and col < ncols:
                    cs = self.style_for(c, tr_styles[tr.nid])
                    mn, mx = self.measure(c, cs)
                    have_mn = sum(mins[col:col + span])
                    have_mx = sum(maxs[col:col + span])
                    n = len(mins[col:col + span]) or 1
                    if mn + 2 * pad > have_mn:
                        for i in range(col, min(ncols, col + span)):
                            mins[i] += (mn + 2 * pad - have_mn) / n
                    if mx + 2 * pad > have_mx:
                        for i in range(col, min(ncols, col + span)):
                            maxs[i] += (mx + 2 * pad - have_mx) / n
                col += span
        for i in range(ncols):
            if fixed[i]:
                mins[i] = max(mins[i], min(fixed[i], avail))
                maxs[i] = max(mins[i], fixed[i])
            maxs[i] = max(maxs[i], mins[i])
        target = (tw - (ncols + 1) * spc) if tw else None
        smin, smax = sum(mins), sum(maxs)
        room = max(10, avail if target is None else min(avail, target))
        if smax <= room:
            if target:
                extra = target - smax
                flex = [i for i in range(ncols) if not fixed[i]] or list(range(ncols))
                tot = sum(maxs[i] for i in flex) or 1
                colw = [maxs[i] + (extra * maxs[i] / tot if i in flex else 0) for i in range(ncols)]
            else:
                colw = list(maxs)
        elif smin <= room:
            extra = room - smin
            d = [maxs[i] - mins[i] for i in range(ncols)]
            tot = sum(d) or 1
            colw = [mins[i] + extra * d[i] / tot for i in range(ncols)]
        else:
            colw = [m * room / (smin or 1) for m in mins]
        total = sum(colw) + (ncols + 1) * spc
        x0 = self.fx
        if node.get('align', '').lower() == 'center' or self.align == 'center' or \
                (node.css and 'auto' in node.css.get('margin', '')):
            x0 = self.fx + max(0, (self.fw - total) / 2)
        elif node.get('align', '').lower() == 'right':
            x0 = self.fx + max(0, self.fw - total)
        save = (self.fx, self.fw, self.align, self.list_stack, self.marker)
        self.marker = None
        t0 = len(self.items)
        top = self.y
        backs = []
        if caption is not None:
            self.fx, self.fw, self.align = x0, total, 'center'
            self.walk(caption, self.style_for(caption, st))
            self.flush()
            top = self.y
        tbg = st.bg
        y = top + spc
        for tr, r in grid:
            trs = tr_styles[tr.nid]
            cells = []
            col = 0
            cx = x0 + spc
            for c, span in r:
                if col >= ncols:
                    break
                cw = sum(colw[col:col + span]) + spc * (span - 1)
                cs = self.style_for(c, trs)
                if cs.hidden:
                    cx += cw + spc
                    col += span
                    continue
                self.fx, self.fw = cx + pad, max(10, cw - 2 * pad)
                self.align = cs.align
                self.list_stack = []
                self.y = y + pad
                self.pending_margin = 0
                i0, g0, c0, b0 = len(self.items), len(self.regions), len(self.controls), len(self.boxes)
                self.anchor(c)
                self.walk(c, cs)
                self.flush()
                h = self.y - y + pad
                va = (c.get('valign') or tr.get('valign') or '').lower()
                if c.css and c.css.get('vertical-align'):
                    va = c.css['vertical-align']
                cells.append((c, cs, cx, cw, h, i0, g0, c0, va, b0))
                cx += cw + spc
                col += span
            row_h = max([cl[4] for cl in cells] + [0])
            ends = [(cl[5], cl[6], cl[7], cl[9]) for cl in cells[1:]] + \
                [(len(self.items), len(self.regions), len(self.controls), len(self.boxes))]
            for cl, (i1, g1, c1, b1) in zip(cells, ends):
                c, cs, cx, cw, h, i0, g0, c0, va, b0 = cl
                dy = 0 if va == 'top' else ((row_h - h) if va == 'bottom' else (row_h - h) // 2)
                if va not in ('top', 'bottom', 'middle', 'center', '') or va == '':
                    dy = (row_h - h) // 2
                if dy:
                    self.shift(i0, i1, g0, g1, c0, c1, dy)
                    for bi in range(b0, b1):
                        bx = self.boxes[bi]
                        self.boxes[bi] = (bx[0], bx[1] + dy, bx[2], bx[3] + dy, bx[4])
                self.boxes.append((cx, y, cx + cw, y + row_h, c))
                bg = cs.bg or (trs.bg if tr.tag == 'tr' else None)
                if bg:
                    backs.append(('r', cx, y, cx + cw, y + row_h, bg))
                if border:
                    backs.append(('o', cx, y, cx + cw, y + row_h, (160, 160, 160)))
            y += row_h + spc
        if tbg:
            backs.insert(0, ('r', x0, top, x0 + total, y, tbg))
        if border:
            backs.append(('o', x0, top, x0 + total, y, (120, 120, 120)))
        self.items[t0:t0] = [b for b in backs if b[0] == 'r'] + [b for b in backs if b[0] == 'o']
        self.fx, self.fw, self.align, self.list_stack, self.marker = save
        self.y = y
        self.pending_margin = 0
        self.add_margin(MARGIN['table'] * st.size)

    def el_at(self, px, py):
        """The innermost element at a page point that has a data-nep id (pages run by JavaScript)."""
        best, area = None, None
        for x0, y0, x1, y1, node in self.boxes:
            if x0 <= px <= x1 and y0 <= py <= y1:
                a = (x1 - x0) * (y1 - y0)
                if area is None or a <= area:
                    best, area = node, a
        while best is not None and not best.get('data-nep'):
            best = best.parent
        return best

    def shift(self, i0, i1, g0, g1, c0, c1, dy):
        for i in range(i0, i1):
            it = self.items[i]
            k = it[0]
            if k == 't':
                self.items[i] = (k, it[1], it[2] + dy) + it[3:]
            elif k in ('r', 'o', 'b'):
                self.items[i] = (k, it[1], it[2] + dy, it[3], it[4] + dy) + it[5:]
            elif k == 'i':
                self.items[i] = (k, it[1], it[2] + dy) + it[3:]
            elif k == 'c':
                it[1].y += dy
        for i in range(g0, g1):
            r = self.regions[i]
            self.regions[i] = (r[0], r[1] + dy, r[2], r[3] + dy, r[4])


def _roman(n):
    vals = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'),
            (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]
    out = ''
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out or 'I'


def decode_data_url(url):
    try:
        head, data = url.split(',', 1)
        raw = base64.b64decode(data) if ';base64' in head else urllib.parse.unquote_to_bytes(data)
        im = Image.open(io.BytesIO(raw))
        im.load()
        return im.convert('RGBA')
    except Exception:
        return None
