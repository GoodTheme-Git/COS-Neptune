"""Archiver: open, extract and make .zip / .tar.gz / .gz (and friends).

* Archiver           browse inside an archive like a folder (Windows 7's
                     "compressed folder"), open a file straight from it,
                     extract all or some, test it, add files to a .zip
* ExtractDialog      "Extract Compressed Files": pick where, watch progress
* CompressDialog     "Compress": ZIP, TAR.GZ, TAR.XZ, TAR, or GZIP / XZ for
                     one file; fastest to smallest
* ArchiveProgress    the small progress window behind Explorer's quick
                     actions (Extract Here, Send to ZIP, gzip, gunzip)

The work itself is nep/archive.py, on a worker thread.
"""
import os
import shutil
import time

from .. import archive as A
from .. import gfx, icons, theme
from ..app import App
from .dialogs import FileDialog, message

REL = lambda p: p.replace(theme.ROOT, '~/cos/neptune').replace(os.path.expanduser('~'), '~')


def refresh_folder(shell, folder):
    """Refresh every Explorer window showing folder (after we wrote into it)."""
    from .files import Explorer
    try:
        real = os.path.realpath(folder)
    except OSError:
        return
    for w in shell.windows:
        if isinstance(w.app, Explorer) and os.path.realpath(w.app.path) == real:
            try:
                w.app.refresh()
                w.app.invalidate()
            except Exception:
                pass


def open_path(shell, path):
    """Open a file with the Neptune program for it."""
    kind = icons.for_path(os.path.basename(path), False)
    low = path.lower()
    if low.endswith(('.html', '.htm')):
        shell.launch('webinator', url='file://' + path)
        return
    target = {'image': 'photos', 'music': 'druman', 'video': 'druman', 'package': 'installer', 'driver': 'installer',
              'cosapp': 'cosrun', 'archive': 'archiver', 'theme': 'cosplace'}.get(kind)
    if target:
        shell.launch(target, path=path)
    elif kind == 'text' or os.path.getsize(path) < 2_000_000:
        shell.launch('notepad', path=path)
    else:
        shell.launch('files', path=os.path.dirname(path))


def progress_bar(img, ui, box, frac):
    """The Windows 7 green bar with its moving shine."""
    x0, y0, x1, y1 = box
    ui.rect(box, fill=(230, 230, 230), outline=(188, 188, 188))
    fx = x0 + int((x1 - x0 - 2) * gfx.clamp(frac, 0, 1))
    h = y1 - y0 - 2
    if fx > x0 + 1 and h > 0:
        img.alpha_composite(gfx.vgradient(fx - x0 - 1, h, ((0, (120, 225, 120)), (0.5, (40, 180, 50)),
                                                           (1, (20, 140, 30)))), (x0 + 1, y0 + 1))
        sx = x0 + int((time.time() * 240) % max(1, fx - x0 + 80)) - 80
        if sx < fx - 10:
            shine = gfx.hgradient(80, h, ((0, (255, 255, 255, 0)), (0.5, (255, 255, 255, 110)),
                                          (1, (255, 255, 255, 0))))
            lay = shine.crop((max(0, x0 + 1 - sx), 0, min(80, fx - sx), h))
            if lay.size[0] > 0:
                img.alpha_composite(lay, (max(x0 + 1, sx), y0 + 1))


def job_rate(job):
    t = time.time() - job.t0
    if t < 0.5 or not job.done:
        return ''
    rate = job.done / t
    left = (job.total - job.done) / rate if rate and job.total > job.done else 0
    return '%s/s%s' % (A.human(rate), ('  ·  about %d s left' % left) if left >= 2 else '')


# =============================================================================
class Archiver(App):
    title = 'Archiver'
    icon = 'archive'
    size = (820, 540)
    min_size = (520, 340)
    bg = theme.CLIENT_BG

    def __init__(self, shell, path=None, **kw):
        super().__init__(shell, **kw)
        self.path = None
        self.kind = None
        self.entries = []
        self.cwd = ''
        self.sel = None
        self.scroll = 0
        self.error = ''
        self.job = None
        self.job_after = None
        self.last_click = (None, 0)
        if path:
            self.open(path)

    # -- loading -------------------------------------------------------------------
    def open(self, path):
        self.path = os.path.abspath(path)
        self.cwd = ''
        self.sel = None
        self.scroll = 0
        self.error = ''
        try:
            self.kind = A.detect(self.path)
            self.entries = A.list_entries(self.path, self.kind)
        except A.ArchiveError as e:
            self.entries, self.error = [], str(e)
        self.set_title('%s - Archiver' % os.path.basename(self.path))
        self.build()

    def build(self):
        """Turn the flat member list into folders, Explorer style."""
        tree = {'': {'dirs': {}, 'files': []}}
        self.skipped_names = 0

        def folder(p):
            if p not in tree:
                parent, _, name = p.rpartition('/')
                folder(parent)['dirs'][name] = p
                tree[p] = {'dirs': {}, 'files': []}
            return tree[p]
        for e in self.entries:
            rel = A.clean_name(e.name)
            if rel is None:
                self.skipped_names += 1
                continue
            if e.is_dir:
                folder(rel)
                continue
            parent, _, name = rel.rpartition('/')
            folder(parent)['files'].append((name, e, rel))
        self.tree = tree
        self.sizes = {}
        for p in sorted(tree, key=lambda x: -(x.count('/') + 1) if x else 1):   # deepest first, the root last
            node = tree[p]
            s = sum(e.size or 0 for _, e, _ in node['files'])
            known = all(e.csize is not None for _, e, _ in node['files'])
            c = sum(e.csize or 0 for _, e, _ in node['files']) if known else None
            for sub in node['dirs'].values():
                s += self.sizes[sub][0]
                c = None if c is None or self.sizes[sub][1] is None else c + self.sizes[sub][1]
            self.sizes[p] = (s, c)

    def items(self):
        node = self.tree.get(self.cwd, {'dirs': {}, 'files': []})
        out = []
        for name, full in sorted(node['dirs'].items(), key=lambda kv: kv[0].lower()):
            s, c = self.sizes.get(full, (0, 0))
            out.append({'name': name, 'dir': True, 'size': s, 'csize': c, 'mtime': 0, 'path': full})
        for name, e, rel in sorted(node['files'], key=lambda t: t[0].lower()):
            out.append({'name': name, 'dir': False, 'size': e.size, 'csize': e.csize, 'mtime': e.mtime, 'path': rel,
                        'link': e.kind not in ('file', 'dir')})
        return out

    @property
    def can_write(self):
        return self.path and A.inside_home(self.path)

    # -- drawing -------------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        ui.command_bar((0, 0, w, 34))
        x = 6
        sel = self.selected()
        busy = self.job is not None and not self.job.finished
        cmds = [('Extract all', 'extract_all', 'folder', bool(self.entries) and not busy),
                ('Extract selected', 'extract_sel', None, sel is not None and not busy),
                ('Add files...', 'add', None, self.kind == 'zip' and self.can_write and not busy),
                ('Test', 'test', None, bool(self.entries) and not busy),
                ('Open archive...', 'open_dlg', None, not busy),
                ('Open folder', 'folder', None, bool(self.path))]
        for label, cid, ic, en in cmds:
            bw = gfx.text_width(gfx.font(12), label) + (40 if ic else 18)
            ui.toolbutton((x, 5, x + bw, 29), label, cid, icon=ic, enabled=en)
            x += bw + 3
        if not self.path:
            self.draw_empty(img, ui, w, h)
            return
        # breadcrumb
        by = 40
        ui.icon('archive', (10, by), 20)
        x = 36
        crumbs = [(os.path.basename(self.path), '')]
        acc = ''
        for part in [p for p in self.cwd.split('/') if p]:
            acc = (acc + '/' + part) if acc else part
            crumbs.append((part, acc))
        for i, (label, target) in enumerate(crumbs):
            if i:
                ui.text((x, by + 10), '›', 13, color=theme.MUTED, anchor='lm')
                x += 14
            if i == len(crumbs) - 1:
                ui.text((x, by + 10), label, 12, bold=True, anchor='lm')
                x += gfx.text_width(gfx.font(12, True), label) + 8
            else:
                ui.link((x, by + 2), label, ('crumb', target), 12)
                x += gfx.text_width(gfx.font(12), label) + 8
        ui.hline(0, w, by + 26, (220, 225, 232))
        if self.error:
            ui.icon('error', (20, by + 44), 32)
            ui.wrap((62, by + 48), self.error, w - 90, 12)
            return
        self.draw_list(img, ui, (0, by + 27, w, h - 30))
        self.draw_status(img, ui, (0, h - 30, w, h))

    def draw_empty(self, img, ui, w, h):
        cx = w // 2
        ui.icon('archive', (cx - 32, 90), 64)
        ui.text((cx, 176), 'Archiver', 18, color=(30, 57, 91), anchor='mm')
        ui.text((cx, 202), 'Open a .zip, .tar.gz, .gz (or .tar, .tar.xz, .bz2, .xz) to look inside and extract it.', 12,
                color=theme.MUTED, anchor='mm')
        ui.text((cx, 222), 'To make one, right-click files or folders in Explorer and choose Compress.', 12,
                color=theme.MUTED, anchor='mm')
        ui.button((cx - 140, 250, cx - 6, 278), 'Open archive...', 'open_dlg', default=True)
        ui.button((cx + 6, 250, cx + 140, 278), 'Compress files...', 'new')

    def draw_list(self, img, ui, box):
        x0, y0, x1, y1 = box
        cols = [('Name', 0.38), ('Type', 0.17), ('Packed', 0.13), ('Size', 0.13), ('Ratio', 0.08), ('Modified', 0.11)]
        width = x1 - x0 - 20
        hx = x0 + 4
        spans = []
        for name, frac in cols:
            cw = int(width * frac)
            right = name in ('Packed', 'Size', 'Ratio')
            ui.text((hx + cw - 8 if right else hx + 6, y0 + 12), name, 12, color=(76, 96, 122),
                    anchor='rm' if right else 'lm')
            ui.d.line([(hx + cw - 1, y0 + 4), (hx + cw - 1, y0 + 20)], fill=(222, 225, 230, 255))
            spans.append((hx, hx + cw, right))
            hx += cw
        ui.hline(x0, x1, y0 + 24, (213, 213, 213))
        items = self.items()
        rh = 22
        top = y0 + 27
        page = max(1, (y1 - top) // rh)
        self.page = page
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(items) - page))
        fnt = gfx.font(12)
        ext_names = {'zip': 'ZIP', 'gz': 'GZip', 'tgz': 'TAR.GZ'}
        for i in range(self.scroll, min(len(items), self.scroll + page)):
            it = items[i]
            y = top + (i - self.scroll) * rh
            ui.listrow((x0 + 4, y, x1 - 20, y + rh - 1), ('row', i), selected=i == self.sel, data=i)
            if it['dir']:
                kind = 'File folder'
                ic = 'folder'
            else:
                ext = it['name'].rsplit('.', 1)[-1].upper() if '.' in it['name'] else ''
                kind = ('%s file' % ext_names.get(ext.lower(), ext)) if ext else 'File'
                ic = icons.for_path(it['name'], False)
                if it.get('link'):
                    kind = 'Link (not extracted)'
            ratio = ''
            if it['size'] and it['csize'] is not None and not it['dir']:
                ratio = '%d%%' % max(0, round(100 - 100 * it['csize'] / it['size']))
            mt = time.strftime('%Y-%m-%d', time.localtime(it['mtime'])) if it['mtime'] else ''
            vals = [it['name'], kind, A.human(it['csize']) if it['csize'] is not None else '',
                    A.human(it['size']) if it['size'] is not None else '?', ratio, mt]
            for k, (a, b, right) in enumerate(spans):
                if k == 0:
                    ui.icon(ic, (a + 6, y + 3), 16)
                    tx = a + 28
                else:
                    tx = a + 6
                col = theme.MUTED if it.get('link') else theme.CLIENT_TEXT
                txt = gfx.ellipsize(fnt, str(vals[k]), b - tx - 6)
                ui.text((b - 8 if right else tx, y + rh // 2), txt, 12, color=col, anchor='rm' if right else 'lm')
        if not items:
            ui.text(((x0 + x1) // 2, top + 30), 'This folder is empty.', 12, color=theme.MUTED, anchor='mm')
        ui.scrollbar((x1 - 18, top, x1 - 1, y1), 'scroll', self.scroll, page, len(items))

    def draw_status(self, img, ui, box):
        x0, y0, x1, y1 = box
        img.alpha_composite(gfx.vgradient(x1 - x0, y1 - y0, ((0, (241, 245, 251)), (1, (222, 230, 240)))), (x0, y0))
        ui.hline(x0, x1, y0, (190, 200, 214))
        j = self.job
        if j is not None and not j.finished:
            ui.text((x0 + 10, y0 + 15), j.title, 11, anchor='lm')
            tw = gfx.text_width(gfx.font(11), j.title) + 20
            progress_bar(img, ui, (x0 + tw, y0 + 8, x1 - 260, y1 - 8), j.frac)
            ui.text((x1 - 252, y0 + 15), gfx.ellipsize(gfx.font(11), job_rate(j) or j.current, 170), 11,
                    color=theme.MUTED, anchor='lm')
            ui.button((x1 - 74, y0 + 4, x1 - 8, y1 - 4), 'Cancel', 'cancel', size=11)
            return
        total, packed = self.sizes.get('', (0, 0))
        n = len([e for e in self.entries if not e.is_dir])
        parts = ['%d file%s' % (n, '' if n == 1 else 's'), A.NAMES.get(self.kind, '?')]
        if total:
            if packed and self.kind == 'zip':
                parts.append('%s packed into %s (%d%% smaller)' % (A.human(total), A.human(packed),
                                                                  max(0, round(100 - 100 * packed / total))))
            else:
                parts.append('%s unpacked, %s on disk' % (A.human(total), A.human(os.path.getsize(self.path))))
        if self.skipped_names:
            parts.append('%d unsafe name%s hidden' % (self.skipped_names, '' if self.skipped_names == 1 else 's'))
        ui.text((x0 + 10, y0 + 15), '  ·  '.join(parts), 11, color=(40, 50, 60), anchor='lm')

    # -- actions ---------------------------------------------------------------------------
    def selected(self):
        items = self.items() if self.path and not self.error else []
        return items[self.sel] if self.sel is not None and self.sel < len(items) else None

    @property
    def live(self):
        return self.job is not None and not self.job.finished

    def on_tick(self, now):
        j = self.job
        if j is None:
            return False
        if j.finished:
            self.job = None
            after, self.job_after = self.job_after, None
            if after:
                after(j)
            return True
        return int(now * 10) % 2 == 0

    def run(self, title, fn, *a, after=None):
        self.job = A.Job(title).start(fn, *a)
        self.job_after = after
        self.invalidate()

    def on_command(self, cid, data):
        sel = self.selected()
        if isinstance(cid, tuple):
            if cid[0] == 'row':
                self.sel = data
            elif cid[0] == 'crumb':
                self.cwd, self.sel, self.scroll = cid[1], None, 0
        elif cid == 'scroll':
            self.scroll = data
        elif cid == 'extract_all':
            self.shell.launch('extract', path=self.path)
        elif cid == 'extract_sel' and sel:
            self.shell.launch('extract', path=self.path, members=[sel['path'] + ('/' if sel['dir'] else '')])
        elif cid == 'add':
            self.add_dialog()
        elif cid == 'test':
            self.run('Testing', A.test, self.path, after=self.after_test)
        elif cid == 'open_dlg':
            self.open_dialog()
        elif cid == 'new':
            self.shell.open_window(FileDialog(self.shell, 'open', start=os.path.join(theme.HOME, 'Documents'),
                                              callback=lambda p: self.shell.launch('compress', paths=[p])),
                                   parent=self.win)
        elif cid == 'folder' and self.path:
            self.shell.launch('files', path=os.path.dirname(self.path))
        elif cid == 'cancel' and self.job:
            self.job.cancel = True
        self.invalidate()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'row':
            self.sel = data
            it = self.selected()
            if it is None:
                return
            if it['dir']:
                self.cwd, self.sel, self.scroll = it['path'], None, 0
            elif not it.get('link'):
                self.open_member(it)
            self.invalidate()
        else:
            self.on_command(cid, data)

    def open_member(self, it):
        """Open one file from inside the archive (unpacked to a private cache first)."""
        if self.kind in A.SINGLE:
            dest = os.path.join(A.CACHE, '%x' % abs(hash((self.path, os.path.getmtime(self.path)))))
            members = None
        else:
            dest = os.path.join(A.CACHE, '%x' % abs(hash((self.path, it['path']))))
            members = [it['path']]
        shutil.rmtree(dest, ignore_errors=True)

        def done(j):
            if j.error:
                message(self.shell, 'Archiver', 'Could not open %s:\n%s' % (it['name'], j.error), 'error',
                        parent=self.win)
            elif j.result:
                open_path(self.shell, j.result[0])
        self.run('Opening %s' % it['name'], A.extract, self.path, dest, members, 'overwrite', after=done)

    def after_test(self, j):
        if j.error:
            message(self.shell, 'Test', 'The test stopped: %s' % j.error, 'error', parent=self.win)
        elif j.result:
            message(self.shell, 'Test', '%d problem%s found:\n\n%s' % (len(j.result), '' if len(j.result) == 1 else 's',
                                                                      '\n'.join(j.result[:8])), 'error', parent=self.win)
        else:
            message(self.shell, 'Test', 'No errors found in %s. Every file reads back with the right checksum.'
                    % os.path.basename(self.path), 'info', parent=self.win)

    def open_dialog(self):
        start = os.path.dirname(self.path) if self.path else os.path.join(theme.HOME, 'Downloads')
        if not os.path.isdir(start):
            start = theme.HOME
        self.shell.open_window(FileDialog(self.shell, 'open', start=start, exts=A.ARCHIVE_EXTS,
                                          callback=self.open), parent=self.win)

    def add_dialog(self):
        def done(p):
            def after(j):
                if j.error:
                    message(self.shell, 'Add files', j.error, 'error', parent=self.win)
                self.open(self.path)
                if j.skipped:
                    self.shell.notify('Archiver', '%d already in the archive, left alone.' % len(j.skipped), 'info', 4)
            self.run('Adding', A.add_to_zip, self.path, [p], after=after)
        self.shell.open_window(FileDialog(self.shell, 'open', start=os.path.dirname(self.path), callback=done),
                               parent=self.win)

    def on_wheel(self, dy, x, y):
        self.scroll = max(0, self.scroll - dy * 3)
        self.invalidate()

    def on_key(self, ev):
        k = ev['key']
        items = self.items() if self.path and not self.error else []
        if k in ('up', 'down') and items:
            self.sel = gfx.clamp((self.sel if self.sel is not None else -1) + (1 if k == 'down' else -1), 0,
                                 len(items) - 1)
            page = getattr(self, 'page', 10)
            if self.sel < self.scroll:
                self.scroll = self.sel
            elif self.sel >= self.scroll + page:
                self.scroll = self.sel - page + 1
            return True
        if k == 'enter' and self.sel is not None:
            self.on_activate(('row', self.sel), self.sel)
            return True
        if k == 'backspace' and self.cwd:
            self.cwd, self.sel = self.cwd.rpartition('/')[0], None
            return True
        if ev.get('ctrl') and k == 'o':
            self.open_dialog()
            return True
        return False

    def context_menu(self, x, y):
        h = self.ui.hit(x, y)
        if h and isinstance(h[0], tuple) and h[0][0] == 'row':
            self.sel = h[2]
            it = self.selected()
            if it:
                return [{'label': 'Open', 'enabled': not it.get('link'), 'action': lambda: self.on_activate(('row', self.sel), self.sel)},
                        {'label': 'Extract...', 'action': lambda: self.on_command('extract_sel', None)}]
        return [{'label': 'Extract all...', 'enabled': bool(self.entries), 'action': lambda: self.on_command('extract_all', None)},
                {'label': 'Test archive', 'enabled': bool(self.entries), 'action': lambda: self.on_command('test', None)}]

    def on_close(self):
        if self.job is not None and not self.job.finished:
            self.job.cancel = True
        return True


# =============================================================================
class ExtractDialog(App):
    title = 'Extract Compressed Files'
    icon = 'archive'
    size = (560, 330)
    resizable = False
    dialog = True
    bg = theme.CLIENT_BG

    def __init__(self, shell, path=None, members=None, dest=None, **kw):
        super().__init__(shell, **kw)
        self.path = os.path.abspath(path)
        self.members = members
        base = A.default_extract_dir(self.path)
        d = dest or A.unique_path(os.path.join(base, A.strip_ext(os.path.basename(self.path)) or 'extracted'))
        self.dest = {'text': d, 'cur': len(d)}
        self.show = True
        self.page = 'choose'
        self.job = None
        self.title = 'Extract %s' % os.path.basename(self.path)

    def draw(self, img, ui):
        w, h = img.size
        ui.rect((0, 0, w, h), fill=(255, 255, 255))
        ui.icon('archive', (20, 16), 32)
        x0 = 64
        if self.page == 'choose':
            ui.text((x0, 18), 'Select a destination and extract files', 15, color=(0, 51, 153))
            what = 'everything' if not self.members else ', '.join(m.rstrip('/').split('/')[-1] for m in self.members)
            ui.text((x0, 44), 'From %s: %s' % (os.path.basename(self.path), gfx.ellipsize(gfx.font(12), what, 300)), 12,
                    color=theme.MUTED)
            ui.text((x0, 82), 'Files will be extracted to this folder:', 12)
            ui.textbox((x0, 102, w - 24, 128), 'dest', self.dest)
            y = 140
            ui.text((x0, y + 8), 'Quick picks:', 11, color=theme.MUTED, anchor='lm')
            x = x0 + 80
            stem = A.strip_ext(os.path.basename(self.path)) or 'extracted'
            picks = [('Next to the archive', os.path.dirname(self.path)), ('Downloads', os.path.join(theme.HOME, 'Downloads')),
                     ('Documents', os.path.join(theme.HOME, 'Documents'))]
            for label, folder in picks:
                if label.startswith('Next') and not A.inside_home(folder):
                    continue
                ui.link((x, y), label, ('pick', os.path.join(folder, stem)), 11)
                x += gfx.text_width(gfx.font(11), label) + 18
            ui.checkbox((x0, 178), 'Show extracted files when complete', 'show', self.show)
            ui.wrap((x0, 212), 'Neptune only writes inside its home folder (%s). Links and unsafe paths in the archive '
                    'are skipped.' % REL(theme.HOME), w - x0 - 24, 11, color=theme.MUTED)
        elif self.page == 'run':
            j = self.job
            ui.text((x0, 18), 'Extracting...', 15, color=(0, 51, 153))
            ui.text((x0, 50), 'To %s' % gfx.ellipsize(gfx.font(12), REL(self.dest['text']), w - x0 - 50), 12,
                    color=theme.MUTED)
            progress_bar(img, ui, (x0, 84, w - 24, 104), j.frac)
            ui.text((x0, 120), gfx.ellipsize(gfx.font(12), j.current, w - x0 - 30), 12)
            ui.text((x0, 142), '%s of %s   %s' % (A.human(j.done), A.human(j.total), job_rate(j)), 11, color=theme.MUTED)
        else:
            j = self.job
            ok = not j.error
            ui.text((x0, 18), 'Done' if ok else 'Extraction stopped', 15, color=(0, 51, 153) if ok else (170, 30, 20))
            n = len(j.result or [])
            msg = ('%d file%s extracted to %s.' % (n, '' if n == 1 else 's', REL(self.dest['text']))) if ok else j.error
            y = ui.wrap((x0, 52), msg, w - x0 - 24, 12) + 8
            if j.skipped:
                ui.wrap((x0, y), 'Skipped %d unsafe or special entr%s: %s' % (
                    len(j.skipped), 'y' if len(j.skipped) == 1 else 'ies', ', '.join(j.skipped[:4])
                    + (' ...' if len(j.skipped) > 4 else '')), w - x0 - 24, 11, color=(150, 90, 0))
        ui.rect((0, h - 46, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - 46, (223, 223, 223))
        if self.page == 'choose':
            ui.button((w - 182, h - 35, w - 100, h - 11), 'Extract', 'go', default=True,
                      enabled=bool(self.dest['text'].strip()))
            ui.button((w - 92, h - 35, w - 12, h - 11), 'Cancel', 'close')
        elif self.page == 'run':
            ui.button((w - 92, h - 35, w - 12, h - 11), 'Cancel', 'cancel')
        else:
            if not self.job.error:
                ui.button((w - 202, h - 35, w - 100, h - 11), 'Open folder', 'openf')
            ui.button((w - 92, h - 35, w - 12, h - 11), 'Close', 'close', default=True)

    @property
    def live(self):
        return self.page == 'run'

    def on_tick(self, now):
        if self.page == 'run':
            if self.job.finished:
                self.page = 'done'
                dest = self.dest['text']
                refresh_folder(self.shell, os.path.dirname(dest))
                refresh_folder(self.shell, dest)
                if not self.job.error:
                    self.shell.notify('Extracted', '%s -> %s' % (os.path.basename(self.path), REL(dest)), 'info', 4)
                    if self.show and not self.job.skipped:
                        self.close()
                        self.shell.launch('files', path=dest)
                else:
                    self.shell.sound('warn')
            return True
        return False

    def on_command(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'pick':
            self.dest = {'text': A.unique_path(cid[1]), 'cur': len(cid[1])}
        elif cid == 'show':
            self.show = data
        elif cid == 'go' or (cid == 'dest' and data == 'enter'):
            dest = os.path.abspath(os.path.expanduser(self.dest['text'].strip()))
            if not A.inside_home(os.path.dirname(dest) if not os.path.exists(dest) else dest):
                message(self.shell, 'Extract', 'Please pick a folder inside Neptune\'s home folder:\n%s' % theme.HOME,
                        'warning', parent=self.win)
                return
            self.dest['text'] = dest
            self.job = A.Job('Extracting').start(A.extract, self.path, dest, self.members)
            self.page = 'run'
        elif cid == 'cancel' and self.job:
            self.job.cancel = True
        elif cid == 'openf':
            self.close()
            self.shell.launch('files', path=self.dest['text'])
        elif cid == 'close':
            self.close()
        self.invalidate()

    def on_key(self, ev):
        if ev['key'] == 'escape':
            if self.page == 'run':
                self.job.cancel = True
            else:
                self.close()
            return True
        return False

    def on_close(self):
        if self.job is not None and not self.job.finished:
            self.job.cancel = True
        return True


# =============================================================================
FORMATS = [('zip', 'ZIP', 'Works everywhere, Windows and Mac too.'),
           ('tar.gz', 'TAR.GZ', 'The Linux standard. Keeps file permissions.'),
           ('tar.xz', 'TAR.XZ', 'Smallest, but slower to make.'),
           ('tar', 'TAR', 'Bundles files without compressing them.'),
           ('gz', 'GZIP', 'One file, like gzip -k: makes name.gz and keeps the original.'),
           ('xz', 'XZ', 'One file, smaller than gzip.')]
LEVELS = [(0, 'Store'), (1, 'Fastest'), (6, 'Normal'), (9, 'Smallest')]


class CompressDialog(App):
    title = 'Compress'
    icon = 'archive'
    size = (580, 460)
    resizable = False
    dialog = True
    bg = theme.CLIENT_BG

    def __init__(self, shell, paths=None, fmt='zip', **kw):
        super().__init__(shell, **kw)
        self.paths = [os.path.abspath(p) for p in (paths or [])]
        if not self.paths:
            raise ValueError('nothing to compress')
        self.single = len(self.paths) == 1 and os.path.isfile(self.paths[0])
        self.fmt = fmt if (fmt not in A.SINGLE or self.single) else 'zip'
        self.level = 6
        self.out = {'text': A.default_out(self.paths, self.fmt), 'cur': 0}
        self.out['cur'] = len(self.out['text'])
        self.edited = False
        self.page = 'choose'
        self.job = None
        self.src_size = None
        n = len(self.paths)
        self.title = 'Compress %s' % (os.path.basename(self.paths[0]) if n == 1 else '%d items' % n)

    def draw(self, img, ui):
        w, h = img.size
        ui.rect((0, 0, w, h), fill=(255, 255, 255))
        ui.icon('archive', (20, 16), 32)
        x0 = 64
        if self.page == 'choose':
            names = [os.path.basename(p) + ('/' if os.path.isdir(p) else '') for p in self.paths]
            ui.text((x0, 18), 'Compress %s' % (names[0] if len(names) == 1 else '%d items' % len(names)), 15,
                    color=(0, 51, 153))
            ui.text((x0, 42), gfx.ellipsize(gfx.font(11), ', '.join(names), w - x0 - 24), 11, color=theme.MUTED)
            ui.groupbox((x0 - 8, 66, w - 16, 66 + 26 + 22 * len(FORMATS)), 'Format')
            y = 88
            for key, label, desc in FORMATS:
                enabled = key not in A.SINGLE or self.single
                if enabled:
                    ui.radio((x0 + 4, y), label, ('fmt', key), self.fmt == key, data=key)
                else:
                    ui.text((x0 + 23, y + 7), label, 12, color=(170, 170, 170), anchor='lm')
                ui.text((x0 + 92, y + 7), desc if enabled else 'one file only', 11,
                        color=theme.MUTED if enabled else (185, 185, 185), anchor='lm')
                y += 22
            y += 18
            ui.text((x0 - 4, y + 7), 'Compression:', 12, anchor='lm')
            x = x0 + 86
            for lv, label in LEVELS:
                if lv == 0 and self.fmt not in ('zip',):
                    continue
                ui.radio((x, y), label, ('lvl', lv), self.level == lv, data=lv)
                x += gfx.text_width(gfx.font(12), label) + 34
            y += 34
            ui.text((x0 - 4, y), 'Save as:', 12)
            ui.textbox((x0 - 4, y + 18, w - 20, y + 44), 'out', self.out)
        elif self.page == 'run':
            j = self.job
            ui.text((x0, 18), 'Compressing...', 15, color=(0, 51, 153))
            ui.text((x0, 50), REL(self.out['text']), 12, color=theme.MUTED)
            progress_bar(img, ui, (x0, 84, w - 24, 104), j.frac)
            ui.text((x0, 120), gfx.ellipsize(gfx.font(12), j.current, w - x0 - 30), 12)
            ui.text((x0, 142), '%s of %s   %s' % (A.human(j.done), A.human(j.total), job_rate(j)), 11, color=theme.MUTED)
        else:
            j = self.job
            if j.error:
                ui.text((x0, 18), 'Compression stopped', 15, color=(170, 30, 20))
                ui.wrap((x0, 52), j.error, w - x0 - 24, 12)
            else:
                out = j.result
                size = os.path.getsize(out)
                total = j.total if j.total > 1 else size
                pct = max(0, round(100 - 100 * size / total)) if total else 0
                ui.text((x0, 18), 'Done', 15, color=(0, 51, 153))
                ui.wrap((x0, 52), 'Saved %s\n%s became %s (%d%% smaller).' % (REL(out), A.human(total), A.human(size), pct),
                        w - x0 - 24, 12)
        ui.rect((0, h - 46, w, h), fill=(240, 240, 240))
        ui.hline(0, w, h - 46, (223, 223, 223))
        if self.page == 'choose':
            ui.button((w - 182, h - 35, w - 100, h - 11), 'Compress', 'go', default=True,
                      enabled=bool(self.out['text'].strip()))
            ui.button((w - 92, h - 35, w - 12, h - 11), 'Cancel', 'close')
        elif self.page == 'run':
            ui.button((w - 92, h - 35, w - 12, h - 11), 'Cancel', 'cancel')
        else:
            if not self.job.error:
                ui.button((w - 202, h - 35, w - 100, h - 11), 'Show file', 'openf')
            ui.button((w - 92, h - 35, w - 12, h - 11), 'Close', 'close', default=True)

    def set_fmt(self, fmt):
        self.fmt = fmt
        if self.level == 0 and fmt != 'zip':
            self.level = 6
        if not self.edited:
            self.out = {'text': A.default_out(self.paths, fmt, os.path.dirname(self.out['text'])), 'cur': 0}
            self.out['cur'] = len(self.out['text'])

    @property
    def live(self):
        return self.page == 'run'

    def on_tick(self, now):
        if self.page == 'run':
            if self.job.finished:
                self.page = 'done'
                if not self.job.error:
                    refresh_folder(self.shell, os.path.dirname(self.job.result))
                    self.shell.notify('Compressed', os.path.basename(self.job.result), 'info', 3)
                else:
                    self.shell.sound('warn')
            return True
        return False

    def on_text(self, cid, state):
        if cid == 'out':
            self.edited = True

    def on_command(self, cid, data):
        if isinstance(cid, tuple):
            if cid[0] == 'fmt':
                self.set_fmt(cid[1])
            elif cid[0] == 'lvl':
                self.level = cid[1]
        elif cid == 'go' or (cid == 'out' and data == 'enter'):
            out = os.path.abspath(os.path.expanduser(self.out['text'].strip()))
            if not A.inside_home(os.path.dirname(out)):
                message(self.shell, 'Compress', 'Please save inside Neptune\'s home folder:\n%s' % theme.HOME, 'warning',
                        parent=self.win)
                return
            if os.path.exists(out):
                out = A.unique_path(out)
            self.out['text'] = out
            os.makedirs(os.path.dirname(out), exist_ok=True)
            self.job = A.Job('Compressing').start(A.create, self.paths, out, self.fmt, self.level)
            self.page = 'run'
        elif cid == 'cancel' and self.job:
            self.job.cancel = True
        elif cid == 'openf':
            self.close()
            self.shell.launch('files', path=os.path.dirname(self.job.result))
        elif cid == 'close':
            self.close()
        self.invalidate()

    def on_key(self, ev):
        if ev['key'] == 'escape':
            if self.page == 'run':
                self.job.cancel = True
            else:
                self.close()
            return True
        return False

    def on_close(self):
        if self.job is not None and not self.job.finished:
            self.job.cancel = True
        return True


# =============================================================================
class ArchiveProgress(App):
    """A small progress window for Explorer's one-click actions."""
    title = 'Working'
    icon = 'archive'
    size = (440, 170)
    resizable = False
    dialog = True
    bg = theme.CLIENT_BG

    def __init__(self, shell, action=None, path=None, **kw):
        super().__init__(shell, **kw)
        self.action = action
        self.path = os.path.abspath(path)
        folder = os.path.dirname(self.path)
        self.folder = folder if A.inside_home(folder) else os.path.join(theme.HOME, 'Documents')
        self.open_after = None
        name = os.path.basename(self.path)
        if action == 'extract_here':
            entries = A.list_entries(self.path)
            dest, self.open_after = A.smart_dest(self.path, self.folder, entries)
            self.title = 'Extracting %s' % name
            self.job = A.Job(self.title).start(A.extract, self.path, dest)
            self.result_folder = dest
        elif action == 'decompress':
            self.title = 'Decompressing %s' % name
            self.job = A.Job(self.title).start(A.extract, self.path, self.folder)
            self.result_folder = self.folder
        elif action in ('zip', 'gz', 'tar.gz'):
            out = A.default_out([self.path], action, self.folder)
            self.title = 'Compressing %s' % name
            self.job = A.Job(self.title).start(A.create, [self.path], out, action, 6)
            self.result_folder = self.folder
        else:
            raise ValueError('unknown action %r' % action)

    def draw(self, img, ui):
        w, h = img.size
        j = self.job
        ui.icon('archive', (16, 16), 32)
        ui.text((60, 18), gfx.ellipsize(gfx.font(13), self.title, w - 80), 13, color=(0, 51, 153))
        progress_bar(img, ui, (60, 48, w - 16, 66), j.frac)
        ui.text((60, 80), gfx.ellipsize(gfx.font(11), j.current or '...', w - 80), 11)
        ui.text((60, 98), '%s of %s   %s' % (A.human(j.done), A.human(j.total), job_rate(j)), 11, color=theme.MUTED)
        ui.button((w - 92, h - 34, w - 12, h - 10), 'Cancel', 'cancel')

    @property
    def live(self):
        return True

    def on_tick(self, now):
        j = self.job
        if j.finished and not getattr(self, 'reported', False):
            self.reported = True              # ticks keep coming while the window animates closed
            self.close()
            refresh_folder(self.shell, self.result_folder)
            refresh_folder(self.shell, self.folder)
            if j.error:
                message(self.shell, self.title, j.error, 'error' if j.error != 'Cancelled.' else 'info')
            else:
                if isinstance(j.result, str):            # made an archive
                    size = os.path.getsize(j.result)
                    pct = max(0, round(100 - 100 * size / j.total)) if j.total > 1 else 0
                    head, note = 'Compressed', 'Made %s, %d%% smaller.' % (os.path.basename(j.result), pct)
                elif self.action == 'decompress':
                    head = 'Decompressed'
                    note = 'Made %s.' % (os.path.basename(j.result[0]) if j.result else 'nothing')
                else:
                    head = 'Extracted'
                    n = len(j.result or [])
                    where = os.path.basename((self.open_after or self.result_folder).rstrip('/'))
                    note = '%d file%s into %s.' % (n, '' if n == 1 else 's', where)
                if j.skipped:
                    note += ' %d unsafe entr%s skipped.' % (len(j.skipped), 'y' if len(j.skipped) == 1 else 'ies')
                self.shell.notify(head, note, 'info', 4)
        return True

    def on_command(self, cid, data):
        if cid == 'cancel':
            self.job.cancel = True

    def on_close(self):
        if not self.job.finished:
            self.job.cancel = True
        return True
