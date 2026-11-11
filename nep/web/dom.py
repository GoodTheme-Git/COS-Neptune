"""HTML to a small DOM, plus just enough CSS to look right.

The parser is forgiving the way browsers are: it closes <p>, <li>, <td> and
friends by itself, ignores stray end tags, and drops <script>/<style>
content. CSS support is deliberately small: inline style="" attributes, and
<style> rules whose selectors are a single tag, .class, #id or tag.class
(no combinators, no @media). That is enough to hide menus a page hides,
and to pick up colours, weights and alignment.
"""
import re
from html.parser import HTMLParser

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track',
        'wbr', 'keygen'}
SKIP = {'script', 'style', 'template', 'svg', 'math', 'iframe', 'object', 'canvas', 'video', 'audio', 'select-hide'}
CLOSES_P = {'p', 'div', 'ul', 'ol', 'table', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'blockquote', 'hr', 'form',
            'dl', 'section', 'article', 'header', 'footer', 'nav', 'aside', 'main', 'figure', 'address', 'fieldset'}

NAMED_COLORS = {
    'black': (0, 0, 0), 'white': (255, 255, 255), 'red': (255, 0, 0), 'green': (0, 128, 0), 'blue': (0, 0, 255),
    'yellow': (255, 255, 0), 'orange': (255, 165, 0), 'purple': (128, 0, 128), 'gray': (128, 128, 128),
    'grey': (128, 128, 128), 'silver': (192, 192, 192), 'maroon': (128, 0, 0), 'navy': (0, 0, 128),
    'teal': (0, 128, 128), 'olive': (128, 128, 0), 'lime': (0, 255, 0), 'aqua': (0, 255, 255),
    'cyan': (0, 255, 255), 'fuchsia': (255, 0, 255), 'magenta': (255, 0, 255), 'brown': (165, 42, 42),
    'pink': (255, 192, 203), 'gold': (255, 215, 0), 'darkgray': (169, 169, 169), 'darkgrey': (169, 169, 169),
    'lightgray': (211, 211, 211), 'lightgrey': (211, 211, 211), 'darkblue': (0, 0, 139), 'darkred': (139, 0, 0),
    'darkgreen': (0, 100, 0), 'whitesmoke': (245, 245, 245), 'gainsboro': (220, 220, 220),
    'beige': (245, 245, 220), 'ivory': (255, 255, 240), 'lightyellow': (255, 255, 224),
    'lightblue': (173, 216, 230), 'steelblue': (70, 130, 180), 'dimgray': (105, 105, 105),
    'transparent': None,
}


def parse_color(v):
    if not v:
        return None
    v = v.strip().lower()
    if v in NAMED_COLORS:
        return NAMED_COLORS[v]
    m = re.match(r'#?([0-9a-f]{3}|[0-9a-f]{6})$', v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = ''.join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    m = re.match(r'rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)(?:[,\s/]+([\d.]+%?))?\s*\)', v)
    if m:
        if m.group(4):
            a = m.group(4)
            alpha = float(a[:-1]) / 100 if a.endswith('%') else float(a)
            if alpha < 0.2:
                return None
        return tuple(min(255, int(x)) for x in m.groups()[:3])
    return None


def parse_decls(text):
    out = {}
    for part in (text or '').split(';'):
        if ':' in part:
            k, v = part.split(':', 1)
            out[k.strip().lower()] = v.strip().replace('!important', '').strip()
    return out


class Node:
    __slots__ = ('tag', 'attrs', 'children', 'parent', 'text', 'css', 'form', 'nid', 'html_attrs', 'vars')
    _n = 0

    def __init__(self, tag, attrs=None, parent=None, text=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.parent = parent
        self.text = text
        self.css = None
        self.form = None
        self.html_attrs = {}
        self.vars = None
        Node._n += 1
        self.nid = Node._n

    def get(self, k, d=None):
        return self.attrs.get(k, d)

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()

    def text_content(self):
        if self.tag == '#text':
            return self.text
        return ''.join(c.text_content() for c in self.children)


class Form:
    def __init__(self, node, base):
        self.node = node
        self.action = node.get('action') or ''
        self.method = (node.get('method') or 'get').lower()
        self.controls = []


class Document:
    def __init__(self):
        self.root = Node('#document')
        self.title = ''
        self.base = None
        self.forms = []
        self.rules = []          # (kind, value, tag, decls)
        self.charset = None
        self.refresh = None
        self.stylesheets = []


class Builder(HTMLParser):
    def __init__(self, scripting=False):
        super().__init__(convert_charrefs=True)
        self.scripting = scripting      # page scripts will run: <noscript> is ignored, as in a browser
        self.doc = Document()
        self.stack = [self.doc.root]
        self.skip = 0
        self.skip_tag = None
        self.in_title = False
        self.in_style = False
        self.style_buf = []
        self.form_stack = []

    @property
    def cur(self):
        return self.stack[-1]

    def _close_until(self, names, stop=()):
        for i in range(len(self.stack) - 1, 0, -1):
            t = self.stack[i].tag
            if t in stop:
                return
            if t in names:
                del self.stack[i:]
                return

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v if v is not None else '') for k, v in attrs}
        if self.skip:
            if tag == self.skip_tag:
                self.skip += 1
            return
        if tag in SKIP or (tag == 'noscript' and self.scripting):
            if tag == 'style':
                self.in_style = True
                self.style_buf = []
            self.skip = 1
            self.skip_tag = tag
            return
        if tag == 'title':
            self.in_title = True
            return
        if tag == 'base' and a.get('href') and not self.doc.base:
            self.doc.base = a['href']
        if tag == 'meta':
            if a.get('http-equiv', '').lower() == 'refresh':
                self.doc.refresh = a.get('content', '')
            return
        if tag == 'link':
            if 'stylesheet' in a.get('rel', '').lower() and a.get('href') and \
                    a.get('media', 'all').lower() in ('all', 'screen', '') :
                self.doc.stylesheets.append(a['href'])
            return
        if tag == 'html':
            self.doc.root.html_attrs.update(a)
            return
        if tag in ('head', 'noscript'):
            return
        if tag == 'body':
            self.doc.root.attrs.update(a)
            return
        if tag in CLOSES_P:
            self._close_until({'p'}, stop={'td', 'th', 'li', 'div', 'blockquote', 'table'})
        if tag == 'li':
            self._close_until({'li'}, stop={'ul', 'ol', 'menu'})
        elif tag in ('dt', 'dd'):
            self._close_until({'dt', 'dd'}, stop={'dl'})
        elif tag == 'tr':
            self._close_until({'tr', 'td', 'th'}, stop={'table', 'tbody', 'thead', 'tfoot'})
        elif tag in ('td', 'th'):
            self._close_until({'td', 'th'}, stop={'tr', 'table'})
        elif tag in ('tbody', 'thead', 'tfoot'):
            self._close_until({'tbody', 'thead', 'tfoot', 'tr', 'td', 'th'}, stop={'table'})
        elif tag == 'option':
            self._close_until({'option'}, stop={'select'})
        elif tag == 'p':
            self._close_until({'p'}, stop={'td', 'th', 'li', 'div', 'blockquote'})
        node = Node(tag, a, self.cur)
        if self.form_stack:
            node.form = self.form_stack[-1]
        if tag == 'form':
            f = Form(node, self.doc.base)
            node.form = f
            self.doc.forms.append(f)
            self.form_stack.append(f)
        self.cur.children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        tag = tag.lower()
        if tag not in VOID and not self.skip and len(self.stack) > 1 and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.skip:
            if tag == self.skip_tag:
                self.skip -= 1
                if self.skip == 0:
                    if self.in_style:
                        self.in_style = False
                        self.doc.rules += parse_stylesheet(''.join(self.style_buf))
                    self.skip_tag = None
            return
        if tag == 'title':
            self.in_title = False
            return
        if tag == 'form' and self.form_stack:
            self.form_stack.pop()
        if tag == 'p' and not any(n.tag == 'p' for n in self.stack[1:]):
            node = Node('p', {}, self.cur)          # a stray </p> makes an empty paragraph
            self.cur.children.append(node)
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
            if self.stack[i].tag in ('table', 'td', 'th') and tag not in ('table', 'tr', 'tbody', 'thead', 'tfoot'):
                return

    def handle_data(self, data):
        if self.skip:
            if self.in_style:
                self.style_buf.append(data)
            return
        if self.in_title:
            self.doc.title += data
            return
        if not data:
            return
        self.cur.children.append(Node('#text', None, self.cur, data))


# -- CSS: selectors, cascade, custom properties --------------------------------------
# Supported: type, .class, #id, *, [attr], [attr=v], [attr~=v], [attr^=v], [attr$=v], [attr*=v],
# :root, :first-child, :last-child, :only-child, :not(<simple>), descendant ' ' and child '>'.
# Rules that need state Webinator does not track (:hover, :focus, ::before, '+', '~', ...)
# are skipped rather than misapplied. <html> and <body> are both the document root node.
DYNAMIC_PSEUDO = ('hover', 'focus', 'active', 'visited', 'focus-within', 'focus-visible', 'checked', 'disabled',
                  'enabled', 'placeholder', 'selection', 'target', 'empty', 'invalid', 'valid', 'required',
                  'nth-child', 'nth-of-type', 'nth-last-child', 'is', 'where', 'has', 'link', 'any-link',
                  'placeholder-shown', 'read-only', 'autofill', 'indeterminate', 'default', 'scope', 'host')
DROP_PROPS = ('transition', 'animation', 'cursor', 'box-shadow', 'text-shadow', 'will-change', 'outline',
              'user-select', '-webkit-user-select', 'app-region', '-webkit-app-region', 'filter', 'backdrop-filter',
              'pointer-events', 'resize', 'scrollbar-width', 'content', 'transform', 'transition-property',
              '-webkit-font-smoothing', 'appearance', '-webkit-appearance', 'touch-action', 'caret-color',
              'object-fit', 'animation-name', 'outline-offset', 'letter-spacing', 'word-break', 'overflow-wrap')
INHERITED = ('color', 'font-size', 'font-weight', 'font-style', 'font-family', 'text-align', 'line-height',
             'white-space', 'visibility', 'text-transform')
_ATTR = re.compile(r'\[\s*([\w-]+)\s*(?:([~^$*|]?=)\s*("([^"]*)"|\'([^\']*)\'|[^\]\s]+)\s*)?\]')
_TOKEN = re.compile(r'([.#][\w-]+|\[[^\]]*\]|::?[\w-]+(?:\([^)]*\))?|\*|[a-zA-Z][\w-]*)')


class Compound:
    __slots__ = ('tag', 'ident', 'classes', 'attrs', 'pseudo', 'neg', 'root')

    def __init__(self):
        self.tag, self.ident, self.classes, self.attrs, self.pseudo, self.neg, self.root = '', '', frozenset(), (), (), (), False


def parse_compound(text):
    pos, c = 0, Compound()
    classes, attrs, pseudo, neg = set(), [], [], []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            return None
        tok = m.group(1)
        pos = m.end()
        if tok == '*':
            continue
        if tok[0] == '.':
            classes.add(tok[1:].lower())
        elif tok[0] == '#':
            c.ident = tok[1:].lower()
        elif tok[0] == '[':
            a = _ATTR.match(tok)
            if not a:
                return None
            val = a.group(4) if a.group(4) is not None else (a.group(5) if a.group(5) is not None else a.group(3))
            attrs.append((a.group(1).lower(), a.group(2), val))
        elif tok.startswith('::'):
            return None                                   # pseudo-elements: nothing to draw them on
        elif tok[0] == ':':
            name = tok[1:].split('(')[0].lower()
            if name == 'root':
                c.root = True
            elif name in ('first-child', 'last-child', 'only-child'):
                pseudo.append(name)
            elif name == 'not':
                inner = parse_compound(tok[5:-1].strip())
                if inner is None:
                    return None
                neg.append(inner)
            else:
                return None                               # :hover and friends
        else:
            c.tag = tok.lower()
    c.classes, c.attrs, c.pseudo, c.neg = frozenset(classes), tuple(attrs), tuple(pseudo), tuple(neg)
    return c


def parse_selector(sel):
    """'div.a > ul .b' -> [(None, c1), ('>', c2), (' ', c3)], or None if unsupported."""
    sel = sel.strip()
    if not sel or any(ch in sel for ch in '+~@'):
        return None
    sel = re.sub(r'\s*>\s*', ' > ', sel)
    parts, depth, cur = [], 0, ''
    for ch in sel:                                         # split on spaces outside [] and ()
        if ch in '[(':
            depth += 1
        elif ch in '])':
            depth -= 1
        if ch == ' ' and depth == 0:
            if cur:
                parts.append(cur)
            cur = ''
        else:
            cur += ch
    if cur:
        parts.append(cur)
    out, comb = [], None
    for p in parts:
        if p == '>':
            comb = '>'
            continue
        c = parse_compound(p)
        if c is None:
            return None
        out.append((comb if out else None, c))
        comb = ' '
    return out or None


def specificity(chain):
    a = b = c = 0
    for _comb, cp in chain:
        a += bool(cp.ident)
        b += len(cp.classes) + len(cp.attrs) + len(cp.pseudo) + cp.root + len(cp.neg)
        c += bool(cp.tag)
    return (a, b, c)


def _split_rules(css):
    """Yield (selector, body) pairs, descending into @media blocks that apply to a desktop screen."""
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    i, n = 0, len(css)
    while i < n:
        j = css.find('{', i)
        if j < 0:
            return
        sel = css[i:j].strip().lstrip('}').strip()
        if sel.startswith('@'):
            depth, k = 1, j + 1
            while k < n and depth:
                if css[k] == '{':
                    depth += 1
                elif css[k] == '}':
                    depth -= 1
                k += 1
            low = sel.lower()
            if low.startswith('@media') and _media_ok(low):
                yield from _split_rules(css[j + 1:k - 1])
            i = k
            continue
        k = css.find('}', j)
        if k < 0:
            return
        yield sel, css[j + 1:k]
        i = k + 1


def _media_ok(q):
    """A desktop-sized screen: honour min-width, skip max-width / small-screen queries and print."""
    if 'print' in q and 'screen' not in q:
        return False
    if re.search(r'max-width|width\s*<=?|<=?\s*width|orientation:\s*portrait|hover:\s*none|pointer:\s*coarse', q):
        return False
    m = re.search(r'min-width:\s*(\d+)', q)
    if m and int(m.group(1)) > 1280:
        return False
    if 'prefers-color-scheme: dark' in q or 'prefers-reduced-motion' in q:
        return False
    return True


def parse_stylesheet(css):
    rules = []
    for sel, body in _split_rules(css):
        decls = {p: v for p, v in parse_decls(body).items() if p not in DROP_PROPS and not p.startswith('transition')}
        if not decls or not sel:
            continue
        depth, cur, sels = 0, '', []
        for ch in sel:
            if ch in '([':
                depth += 1
            elif ch in ')]':
                depth -= 1
            if ch == ',' and depth == 0:
                sels.append(cur)
                cur = ''
            else:
                cur += ch
        sels.append(cur)
        for one in sels:
            chain = parse_selector(one)
            if chain:
                rules.append((chain, decls, specificity(chain)))
    return rules


def _attrs_for(node, which):
    if node.tag == '#document':
        return node.html_attrs if which == 'html' else node.attrs
    return node.attrs


def _compound_ok(node, c):
    if node.tag == '#document':
        if c.tag and c.tag not in ('html', 'body'):
            return False
        which = 'html' if (c.root or c.tag == 'html') else 'body'
        if c.root and c.tag == 'body':
            return False
        attrs = _attrs_for(node, which)
    else:
        if c.root or (c.tag and node.tag != c.tag):
            return False
        attrs = node.attrs
    if c.ident and (attrs.get('id') or '').lower() != c.ident:
        return False
    if c.classes and not c.classes <= set((attrs.get('class') or '').lower().split()):
        return False
    for name, op, val in c.attrs:
        have = attrs.get(name)
        if have is None:
            return False
        if op is None:
            continue
        if op == '=' and have != val:
            return False
        if op == '~=' and val not in have.split():
            return False
        if op == '^=' and not have.startswith(val):
            return False
        if op == '$=' and not have.endswith(val):
            return False
        if op == '*=' and val not in have:
            return False
        if op == '|=' and not (have == val or have.startswith(val + '-')):
            return False
    if c.pseudo and node.parent is not None:
        sibs = [s for s in node.parent.children if s.tag != '#text']
        for p in c.pseudo:
            if p == 'first-child' and sibs[:1] != [node]:
                return False
            if p == 'last-child' and sibs[-1:] != [node]:
                return False
            if p == 'only-child' and sibs != [node]:
                return False
    for inner in c.neg:
        if _compound_ok(node, inner):
            return False
    return True


def _chain_ok(node, chain):
    """Match a selector chain right to left, walking up the parents."""
    if not _compound_ok(node, chain[-1][1]):
        return False
    cur = node
    for idx in range(len(chain) - 1, 0, -1):
        comb = chain[idx][0]
        want = chain[idx - 1][1]
        if comb == '>':
            cur = cur.parent
            if cur is None or not _compound_ok(cur, want):
                return False
        else:
            cur = cur.parent
            while cur is not None and not _compound_ok(cur, want):
                cur = cur.parent
            if cur is None:
                return False
    return True


_VAR = re.compile(r'var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\))?[^()]*))?\)')


def resolve_vars(value, env, depth=0):
    if 'var(' not in value or depth > 8:
        return value
    def sub(m):
        v = env.get(m.group(1))
        if v is None:
            v = (m.group(2) or '').strip()
        return resolve_vars(v, env, depth + 1)
    return _VAR.sub(sub, value)


def compute_css(doc):
    """Cascade: matching rules by (specificity, order), then inline style; then
    resolve custom properties (var()) with inheritance down the tree."""
    by_id, by_class, by_tag, anyrule = {}, {}, {}, []
    for order, rule in enumerate(doc.rules):
        if len(rule) != 3:
            continue
        chain, decls, spec = rule
        last = chain[-1][1]
        entry = (spec, order, chain, decls)
        if last.ident:
            by_id.setdefault(last.ident, []).append(entry)
        elif last.classes:
            by_class.setdefault(min(last.classes), []).append(entry)
        elif last.tag and not last.root:
            by_tag.setdefault(last.tag, []).append(entry)
        else:
            anyrule.append(entry)

    def cascade(node):
        if node.tag == '#document':
            cands = by_tag.get('html', []) + by_tag.get('body', []) + list(anyrule)
            ids = [(node.html_attrs.get('id') or '').lower(), (node.get('id') or '').lower()]
            classes = set((node.html_attrs.get('class') or '').lower().split()) | \
                set((node.get('class') or '').lower().split())
        else:
            cands = by_tag.get(node.tag, []) + anyrule
            ids = [(node.get('id') or '').lower()]
            classes = set((node.get('class') or '').lower().split())
        for i in ids:
            if i:
                cands = cands + by_id.get(i, [])
        for cl in classes:
            cands = cands + by_class.get(cl, [])
        decls = {}
        if cands:
            for _s, _o, chain, d in sorted(cands, key=lambda e: (e[0], e[1])):
                if _chain_ok(node, chain):
                    decls.update(d)
        if node.get('style'):
            decls.update(parse_decls(node.get('style')))
        return decls

    def walk(node, env):
        if node.tag == '#text':
            return
        decls = cascade(node)
        own = {k: v for k, v in decls.items() if k.startswith('--')}
        if own:
            env = dict(env)
            for k, v in own.items():
                env[k] = resolve_vars(v, env)
        css = {}
        for k, v in decls.items():
            if k.startswith('--'):
                continue
            if 'var(' in v:
                v = resolve_vars(v, env).strip()
                if not v:
                    continue
            css[k] = v
        node.css = css or None
        node.vars = env
        for ch in node.children:
            walk(ch, env)

    walk(doc.root, {})


def parse(html, scripting=False):
    b = Builder(scripting)
    try:
        b.feed(html)
        b.close()
    except Exception:
        pass
    doc = b.doc
    doc.title = ' '.join(doc.title.split())
    compute_css(doc)
    return doc
