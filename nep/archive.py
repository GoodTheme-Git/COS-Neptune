"""Zip, unzip, gzip, gunzip (and tar, bzip2, xz) for Neptune.

Everything here uses Python's own zipfile/tarfile/gzip/bz2/lzma, so it
works on any COS machine without extra programs.

Reading:   .zip  .tar  .tar.gz/.tgz  .tar.bz2/.tbz2  .tar.xz/.txz
           .gz  .bz2  .xz   (a single compressed file)
Writing:   the same formats.

Extracting is careful, because an archive is a file from somebody else:
* no entry may land outside the destination ("zip slip": absolute paths
  and ".." are refused), checked again on the real path after the folder
  exists, so a pre-existing symlink can't redirect it either;
* links, devices and other special entries are skipped, never created;
* a size cap and a free-disk check stop "zip bombs" (a tiny file that
  inflates to fill the disk), counting bytes actually written, not what
  the archive claims;
* files come out as ordinary files with sane permissions (rw-r--r--, plus
  the execute bit when the archive had it).
Like all of Neptune, it only writes inside ~/cos/neptune/home.

Long operations run as a Job on a worker thread; the UI polls job.done /
job.total and can set job.cancel.
"""
import bz2
import gzip
import io
import lzma
import os
import shutil
import stat
import struct
import tarfile
import threading
import time
import zipfile
import zlib

from . import theme

CHUNK = 1 << 20
MAX_ENTRIES = 100_000
MAX_TOTAL = 20 * 1024 ** 3          # never unpack more than 20 GB in one go
DISK_MARGIN = 512 * 1024 ** 2       # always leave this much free
CACHE = os.path.join(theme.STATE, 'archiver-cache')   # inside home: Neptune only writes there

# kind -> name endings, longest first where it matters
EXTS = [('tar.gz', ('.tar.gz', '.tgz')), ('tar.bz2', ('.tar.bz2', '.tbz2', '.tbz')),
        ('tar.xz', ('.tar.xz', '.txz')), ('zip', ('.zip',)), ('tar', ('.tar',)),
        ('gz', ('.gz',)), ('bz2', ('.bz2',)), ('xz', ('.xz',))]
NAMES = {'zip': 'ZIP archive', 'tar': 'TAR archive', 'tar.gz': 'TAR.GZ archive', 'tar.bz2': 'TAR.BZ2 archive',
         'tar.xz': 'TAR.XZ archive', 'gz': 'GZip compressed file', 'bz2': 'BZip2 compressed file',
         'xz': 'XZ compressed file'}
SINGLE = ('gz', 'bz2', 'xz')
OPENERS = {'gz': gzip.open, 'bz2': bz2.open, 'xz': lzma.open}
ARCHIVE_EXTS = tuple(e for _, es in EXTS for e in es)


class ArchiveError(Exception):
    pass


class Cancelled(Exception):
    pass


def is_archive_name(name):
    return name.lower().endswith(ARCHIVE_EXTS)


def strip_ext(name):
    low = name.lower()
    for _, es in EXTS:
        for e in es:
            if low.endswith(e):
                return name[:-len(e)]
    return os.path.splitext(name)[0]


def inside_home(path):
    home = os.path.realpath(theme.HOME)
    p = os.path.realpath(path)
    return p == home or p.startswith(home + os.sep)


def _need_home(path):
    if not inside_home(path):
        raise ArchiveError('Neptune only writes inside its home folder (%s).' % theme.HOME)


def unique_path(path):
    if not os.path.exists(path):
        return path
    folder, name = os.path.split(path)
    low = name.lower()
    ext = ''
    for _, es in EXTS:
        for e in es:
            if low.endswith(e) and len(e) > len(ext):
                ext = name[-len(e):]
    if not ext:
        name, ext = os.path.splitext(name)
    else:
        name = name[:-len(ext)]
    i = 2
    while True:
        p = os.path.join(folder, '%s (%d)%s' % (name, i, ext))
        if not os.path.exists(p):
            return p
        i += 1


def human(n):
    if n is None:
        return '?'
    for unit in ('bytes', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024 or unit == 'TB':
            return ('%d %s' % (n, unit)) if unit == 'bytes' else ('%.1f %s' % (n, unit))
        n /= 1024.0


# =============================================================================
# what is it?
# =============================================================================
def detect(path):
    """The archive kind ('zip', 'tar.gz', 'gz', ...) or None. Looks at the
    first bytes, not just the name, so a renamed archive still opens."""
    try:
        with open(path, 'rb') as f:
            head = f.read(512)
    except OSError:
        return None
    low = path.lower()
    by_name = next((k for k, es in EXTS if low.endswith(es)), None)
    if head.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')):
        return 'zip'
    for magic, single, tar in ((b'\x1f\x8b', 'gz', 'tar.gz'), (b'BZh', 'bz2', 'tar.bz2'),
                               (b'\xfd7zXZ\x00', 'xz', 'tar.xz')):
        if head.startswith(magic):
            if by_name == tar:
                return tar
            try:
                with tarfile.open(path, 'r:' + single) as tf:
                    if tf.next() is not None:
                        return tar
            except (tarfile.TarError, OSError, EOFError, zlib.error, lzma.LZMAError, ValueError):
                pass
            return single
    if len(head) >= 262 and head[257:262] == b'ustar':
        return 'tar'
    if by_name == 'tar' and tarfile.is_tarfile(path):
        return 'tar'
    return None


class Entry:
    __slots__ = ('name', 'is_dir', 'size', 'csize', 'mtime', 'kind')

    def __init__(self, name, is_dir, size, csize, mtime, kind='file'):
        self.name, self.is_dir, self.size, self.csize, self.mtime, self.kind = name, is_dir, size, csize, mtime, kind


def clean_name(name):
    """An archive member name as a safe relative path, or None if it would
    escape the destination (absolute, drive letter, '..')."""
    n = name.replace('\\', '/')
    if n.startswith('/') or (len(n) > 1 and n[1] == ':') or '\x00' in n:
        return None
    parts = [p for p in n.split('/') if p not in ('', '.')]
    if not parts or any(p == '..' for p in parts):
        return None
    return '/'.join(parts)


def _gzip_info(path):
    """(original name from the gzip header or None, size mod 4 GB)."""
    name = None
    size = None
    try:
        with open(path, 'rb') as f:
            hdr = f.read(10)
            if len(hdr) == 10 and hdr[3] & 0x08:          # FNAME
                if hdr[3] & 0x04:                         # FEXTRA
                    xlen = struct.unpack('<H', f.read(2))[0]
                    f.read(xlen)
                raw = b''
                while len(raw) < 1024:
                    c = f.read(1)
                    if not c or c == b'\x00':
                        break
                    raw += c
                name = os.path.basename(raw.decode('latin-1')) or None
            f.seek(-4, os.SEEK_END)
            size = struct.unpack('<I', f.read(4))[0]
    except (OSError, struct.error):
        pass
    return name, size


def single_name(path, kind):
    if kind == 'gz':
        n, _ = _gzip_info(path)
        if n and clean_name(n) and '/' not in clean_name(n):
            return n
    base = os.path.basename(path)
    for e in ('.' + kind, '.tgz', '.txz', '.tbz2'):
        if base.lower().endswith(e):
            return base[:-len(e)] or 'file'
    return base + '.out'


def list_entries(path, kind=None):
    kind = kind or detect(path)
    if kind is None:
        raise ArchiveError('%s is not an archive Neptune can open.' % os.path.basename(path))
    out = []
    try:
        if kind == 'zip':
            with zipfile.ZipFile(path) as zf:
                for i in zf.infolist():
                    mode = (i.external_attr >> 16) & 0xFFFF
                    k = 'link' if stat.S_ISLNK(mode) else ('dir' if i.is_dir() else 'file')
                    out.append(Entry(i.filename, i.is_dir(), i.file_size, i.compress_size,
                                     time.mktime(i.date_time + (0, 0, -1)) if i.date_time[0] >= 1980 else 0, k))
        elif kind.startswith('tar'):
            mode = 'r:' + (kind.split('.')[1] if '.' in kind else '')
            with tarfile.open(path, mode) as tf:
                for m in tf:
                    k = 'dir' if m.isdir() else ('file' if m.isreg() else ('link' if m.issym() or m.islnk() else 'other'))
                    out.append(Entry(m.name, m.isdir(), m.size if m.isreg() else 0, None, m.mtime, k))
                    if len(out) > MAX_ENTRIES:
                        break
        else:
            size = _gzip_info(path)[1] if kind == 'gz' else None
            st = os.stat(path)
            out.append(Entry(single_name(path, kind), False, size, st.st_size, st.st_mtime))
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, zlib.error, lzma.LZMAError, OSError) as e:
        raise ArchiveError('%s looks damaged: %s' % (os.path.basename(path), e))
    return out


# =============================================================================
# jobs
# =============================================================================
class Job:
    """A long operation on a worker thread. Poll done/total/current; set cancel."""

    def __init__(self, title):
        self.title = title
        self.total = 0
        self.done = 0
        self.current = ''
        self.cancel = False
        self.error = ''
        self.result = None
        self.skipped = []
        self.finished = False
        self.t0 = time.time()

    def start(self, fn, *a, **kw):
        def run():
            try:
                self.result = fn(self, *a, **kw)
            except Cancelled:
                self.error = 'Cancelled.'
            except ArchiveError as e:
                self.error = str(e)
            except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError, zlib.error, lzma.LZMAError) as e:
                self.error = '%s: %s' % (type(e).__name__, e)
            self.finished = True
        threading.Thread(target=run, daemon=True, name='nep-archive').start()
        return self

    @property
    def frac(self):
        return min(1.0, self.done / self.total) if self.total else (1.0 if self.finished else 0.0)

    def tick(self, n=0):
        if self.cancel:
            raise Cancelled()
        self.done += n


def _copy(src, dst, job, limit=None, written=None):
    """Stream src -> dst with progress, cancel, and an optional byte cap."""
    while True:
        if job is not None and job.cancel:
            raise Cancelled()
        buf = src.read(CHUNK)
        if not buf:
            return
        if written is not None:
            written[0] += len(buf)
            if limit is not None and written[0] > limit:
                raise ArchiveError('This archive unpacks to more than %s, which is more than Neptune will '
                                   'write (or more than the free disk space). It may be a "zip bomb".' % human(limit))
        dst.write(buf)
        if job is not None:
            job.done += len(buf)


def _limit_for(dest):
    try:
        free = shutil.disk_usage(dest).free - DISK_MARGIN
    except OSError:
        free = MAX_TOTAL
    return max(0, min(MAX_TOTAL, free))


def _target(dest_real, rel):
    """Real path for rel under dest, or None if it would land outside."""
    p = os.path.realpath(os.path.join(dest_real, *rel.split('/')))
    if p != dest_real and not p.startswith(dest_real + os.sep):
        return None
    return p


def _resolve_clash(path, policy):
    if not os.path.exists(path):
        return path
    if policy == 'overwrite' and os.path.isfile(path):
        return path
    if policy == 'skip':
        return None
    return unique_path(path)


def _write_file(job, src, target, mode_bits, mtime, limit, written, policy):
    target = _resolve_clash(target, policy)
    if target is None:
        return None
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + '.nep-part'
    try:
        with open(tmp, 'wb') as out:
            _copy(src, out, job, limit, written)
        os.chmod(tmp, 0o755 if mode_bits & 0o111 else 0o644)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    if mtime:
        try:
            os.utime(target, (mtime, mtime))
        except (OSError, OverflowError, ValueError):
            pass
    return target


def extract(job, path, dest, members=None, policy='rename', kind=None):
    """Extract path into dest (a folder, created if needed). members: optional
    list of member names (or folder prefixes ending in '/') to take. Returns
    the list of files written."""
    kind = kind or detect(path)
    if kind is None:
        raise ArchiveError('%s is not an archive Neptune can open.' % os.path.basename(path))
    _need_home(dest)
    os.makedirs(dest, exist_ok=True)
    dest_real = os.path.realpath(dest)
    limit = _limit_for(dest_real)
    written = [0]
    out = []

    def wanted(name):
        if not members:
            return True
        return any(name == m or (m.endswith('/') and name.startswith(m)) for m in members)

    if kind == 'zip':
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES:
                raise ArchiveError('This archive has %d entries, more than Neptune will unpack.' % len(infos))
            infos = [i for i in infos if wanted(i.filename)]
            job.total = sum(i.file_size for i in infos if not i.is_dir()) or 1
            for i in infos:
                job.tick()
                rel = clean_name(i.filename)
                mode = (i.external_attr >> 16) & 0xFFFF
                ftype = stat.S_IFMT(mode)        # 0 when the zip stores no type (Windows tools, many others)
                if rel is None or (ftype and ftype not in (stat.S_IFREG, stat.S_IFDIR)):
                    job.skipped.append(i.filename)
                    continue
                target = _target(dest_real, rel)
                if target is None:
                    job.skipped.append(i.filename)
                    continue
                if i.is_dir():
                    os.makedirs(target, exist_ok=True)
                    continue
                job.current = rel
                mt = time.mktime(i.date_time + (0, 0, -1)) if i.date_time[0] >= 1980 else 0
                with zf.open(i) as src:
                    w = _write_file(job, src, target, mode, mt, limit, written, policy)
                if w:
                    out.append(w)
    elif kind.startswith('tar'):
        mode = 'r:' + (kind.split('.')[1] if '.' in kind else '')
        with tarfile.open(path, mode) as tf:
            members_all = []
            for m in tf:
                members_all.append(m)
                if len(members_all) > MAX_ENTRIES:
                    raise ArchiveError('This archive has more than %d entries, more than Neptune will unpack.'
                                       % MAX_ENTRIES)
            ms = [m for m in members_all if wanted(m.name)]
            job.total = sum(m.size for m in ms if m.isreg()) or 1
            for m in ms:
                job.tick()
                rel = clean_name(m.name)
                if rel is None or not (m.isreg() or m.isdir()):
                    job.skipped.append(m.name)          # links, devices, fifos: never created
                    continue
                target = _target(dest_real, rel)
                if target is None:
                    job.skipped.append(m.name)
                    continue
                if m.isdir():
                    os.makedirs(target, exist_ok=True)
                    continue
                job.current = rel
                src = tf.extractfile(m)
                if src is None:
                    job.skipped.append(m.name)
                    continue
                with src:
                    w = _write_file(job, src, target, m.mode, m.mtime, limit, written, policy)
                if w:
                    out.append(w)
    else:
        name = single_name(path, kind)
        size = _gzip_info(path)[1] if kind == 'gz' else None
        job.total = size or os.path.getsize(path) * 4 or 1
        job.current = name
        target = _target(dest_real, name)
        if target is None:
            raise ArchiveError('The name stored in this file is not safe to use.')
        with OPENERS[kind](path, 'rb') as src:
            w = _write_file(job, src, target, 0o644, os.path.getmtime(path), limit, written, policy)
        if w:
            out.append(w)
    job.done = job.total
    return out


def smart_dest(path, folder, entries):
    """Where 'Extract Here' should go: straight into folder when everything in
    the archive sits under one top folder (or it is one file), otherwise into a
    new folder named after the archive. Returns (dest, opened_path)."""
    tops = set()
    for e in entries:
        rel = clean_name(e.name)
        if rel:
            tops.add(rel.split('/')[0])
    names = {clean_name(e.name) for e in entries if clean_name(e.name)}
    if len(tops) == 1:
        top = next(iter(tops))
        single_file = names == {top}
        if not os.path.exists(os.path.join(folder, top)):
            return folder, os.path.join(folder, top) if not single_file else folder
    dest = unique_path(os.path.join(folder, strip_ext(os.path.basename(path)) or 'extracted'))
    return dest, dest


def test(job, path, kind=None):
    """Read every byte and check every checksum. Returns a list of problems."""
    kind = kind or detect(path)
    problems = []
    sink = io.BytesIO()

    class Null:
        def write(self, b):
            pass
    if kind == 'zip':
        with zipfile.ZipFile(path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            job.total = sum(i.file_size for i in infos) or 1
            for i in infos:
                job.current = i.filename
                try:
                    with zf.open(i) as src:
                        _copy(src, Null(), job)
                except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError) as e:
                    problems.append('%s: %s' % (i.filename, e))
    elif kind and kind.startswith('tar'):
        mode = 'r:' + (kind.split('.')[1] if '.' in kind else '')
        job.total = os.path.getsize(path) * 3 or 1
        with tarfile.open(path, mode) as tf:
            for m in tf:
                job.current = m.name
                if m.isreg():
                    f = tf.extractfile(m)
                    if f is not None:
                        with f:
                            _copy(f, Null(), job)
    elif kind in SINGLE:
        job.total = os.path.getsize(path) * 4 or 1
        with OPENERS[kind](path, 'rb') as src:
            _copy(src, Null(), job)
    else:
        raise ArchiveError('Not an archive.')
    del sink
    job.done = job.total
    return problems


# =============================================================================
# making archives
# =============================================================================
def _walk(sources, skip_real):
    """(full path, archive name) for every regular file and folder in sources.
    Symlinks are skipped: they would point at things outside the selection."""
    for src in sources:
        src = os.path.abspath(src)
        base = os.path.dirname(src)
        if os.path.islink(src):
            continue
        if os.path.isfile(src):
            yield src, os.path.basename(src), False
            continue
        for root, dirs, files in os.walk(src):
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(root, d)))
            rel_root = os.path.relpath(root, base).replace(os.sep, '/')
            yield root, rel_root + '/', True
            for n in sorted(files):
                full = os.path.join(root, n)
                if os.path.islink(full) or not os.path.isfile(full) or os.path.realpath(full) in skip_real:
                    continue
                yield full, rel_root + '/' + n, False


def _total(items):
    t = 0
    for full, _arc, is_dir in items:
        if not is_dir:
            try:
                t += os.path.getsize(full)
            except OSError:
                pass
    return t or 1


class _Counting(io.RawIOBase):
    def __init__(self, f, job):
        self.f, self.job = f, job

    def readable(self):
        return True

    def readinto(self, b):
        if self.job.cancel:
            raise Cancelled()
        data = self.f.read(len(b))
        n = len(data)
        b[:n] = data
        self.job.done += n
        return n


def _finish(tmp, out):
    os.replace(tmp, out)
    return out


def create(job, sources, out, fmt='zip', level=6):
    """Pack sources (files and folders) into out. fmt: zip, tar, tar.gz,
    tar.bz2, tar.xz, or gz/bz2/xz for a single file."""
    _need_home(os.path.dirname(os.path.abspath(out)) or '.')
    if not sources:
        raise ArchiveError('Nothing to compress.')
    out = os.path.abspath(out)
    tmp = out + '.nep-part'
    level = max(0, min(9, int(level)))
    try:
        if fmt in SINGLE:
            if len(sources) != 1 or not os.path.isfile(sources[0]):
                raise ArchiveError('%s compresses one file. Use ZIP or TAR.GZ for folders or several files.'
                                   % fmt.upper())
            src = sources[0]
            job.total = os.path.getsize(src) or 1
            job.current = os.path.basename(src)
            with open(src, 'rb') as f, open(tmp, 'wb') as raw:
                if fmt == 'gz':
                    z = gzip.GzipFile(filename=os.path.basename(src), mode='wb', fileobj=raw,
                                      compresslevel=max(1, level), mtime=int(os.path.getmtime(src)))
                elif fmt == 'bz2':
                    z = bz2.BZ2File(raw, 'wb', compresslevel=max(1, level))
                else:
                    z = lzma.LZMAFile(raw, 'wb', preset=level)
                with z:
                    _copy(f, z, job)
            return _finish(tmp, out)
        items = list(_walk(sources, {os.path.realpath(out), os.path.realpath(tmp)}))
        job.total = _total(items)
        if fmt == 'zip':
            method = zipfile.ZIP_STORED if level == 0 else zipfile.ZIP_DEFLATED
            with zipfile.ZipFile(tmp, 'w', method, compresslevel=None if level == 0 else level,
                                 allowZip64=True) as zf:
                for full, arc, is_dir in items:
                    job.tick()
                    job.current = arc
                    if is_dir:
                        zf.writestr(zipfile.ZipInfo.from_file(full, arc, strict_timestamps=False), b'')
                        continue
                    zi = zipfile.ZipInfo.from_file(full, arc, strict_timestamps=False)
                    zi.compress_type = method
                    with open(full, 'rb') as src, zf.open(zi, 'w', force_zip64=True) as dst:
                        _copy(src, dst, job)
            return _finish(tmp, out)
        if fmt in ('tar', 'tar.gz', 'tar.bz2', 'tar.xz'):
            comp = fmt.split('.')[1] if '.' in fmt else ''
            kw = {}
            if comp in ('gz', 'bz2'):
                kw['compresslevel'] = max(1, level)
            elif comp == 'xz':
                kw['preset'] = level
            with tarfile.open(tmp, 'w:' + comp, format=tarfile.PAX_FORMAT, **kw) as tf:
                for full, arc, is_dir in items:
                    job.tick()
                    job.current = arc
                    ti = tf.gettarinfo(full, arcname=arc.rstrip('/'))
                    ti.uid = ti.gid = 0
                    ti.uname = ti.gname = ''
                    if is_dir:
                        tf.addfile(ti)
                    else:
                        with open(full, 'rb') as f:
                            tf.addfile(ti, io.BufferedReader(_Counting(f, job), CHUNK))
            return _finish(tmp, out)
        raise ArchiveError('Unknown format %r.' % fmt)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def add_to_zip(job, archive, files):
    """Add files to an existing .zip (zip is the one format that can grow in place)."""
    _need_home(archive)
    items = list(_walk(files, {os.path.realpath(archive)}))
    job.total = _total(items)
    with zipfile.ZipFile(archive, 'a', zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        have = set(zf.namelist())
        for full, arc, is_dir in items:
            job.tick()
            if arc in have:
                job.skipped.append(arc + ' (already in the archive)')
                continue
            job.current = arc
            if is_dir:
                zf.writestr(zipfile.ZipInfo.from_file(full, arc, strict_timestamps=False), b'')
                continue
            zi = zipfile.ZipInfo.from_file(full, arc, strict_timestamps=False)
            zi.compress_type = zipfile.ZIP_DEFLATED
            with open(full, 'rb') as src, zf.open(zi, 'w', force_zip64=True) as dst:
                _copy(src, dst, job)
    return archive


def default_out(sources, fmt, folder=None):
    """A sensible output path for compressing sources."""
    first = os.path.abspath(sources[0])
    folder = folder or os.path.dirname(first)
    if not inside_home(folder):
        folder = os.path.join(theme.HOME, 'Documents')
    if len(sources) == 1:
        stem = os.path.basename(first.rstrip('/')) or 'archive'
        if fmt == 'zip' and os.path.isfile(first):
            stem = os.path.splitext(stem)[0] or stem
    else:
        stem = os.path.basename(os.path.dirname(first)) or 'archive'
    ext = {'zip': '.zip', 'tar': '.tar', 'tar.gz': '.tar.gz', 'tar.bz2': '.tar.bz2', 'tar.xz': '.tar.xz'}.get(fmt)
    if fmt in SINGLE:
        return unique_path(os.path.join(folder, os.path.basename(first) + '.' + fmt))
    return unique_path(os.path.join(folder, stem + ext))


def default_extract_dir(path):
    folder = os.path.dirname(os.path.abspath(path))
    if not inside_home(folder):
        folder = os.path.join(theme.HOME, 'Downloads')
    return folder
