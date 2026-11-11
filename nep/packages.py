"""COS packages in Neptune: .cip (COS Installer Package) and .cdi (COS
Driver Install). Same file format and the same rules as COS v2.4 (see
~/cos/PACKAGES.md), so every existing package installs unchanged.

A package is a zip with package.json at the top. Installing writes files
and a record of every file (with its SHA-256), so uninstalling removes
exactly what was installed and keeps anything the user has changed since.
Installing runs nothing.

Where things go in Neptune (all inside ~/cos/neptune/home/.neptune):
  .cosapp named in "apps"   compat/apps/      (run in COS compatibility mode)
  .nepapp named in "apps"   apps/             (native Neptune apps)
  other files of a .cip     packages/<name>/files/
  a whole .cdi              drivers/<name>/
  install record            packages/<name>/installed.json
"""
import hashlib
import json
import os
import re
import time
import zipfile

from . import theme

STATE = theme.STATE
PKG_DIR = os.path.join(STATE, 'packages')
COMPAT_APP_DIR = os.path.join(STATE, 'compat', 'apps')
NEP_APP_DIR = os.path.join(STATE, 'apps')
DRIVER_DIR = os.path.join(STATE, 'drivers')
PKG_TYPES = {'.cip': 'cip', '.cdi': 'cdi'}
PKG_FORMATS = {'cip': 'cip/1', 'cdi': 'cdi/1'}
PKG_MANIFEST = 'package.json'
PKG_MAX_FILES = 2000
PKG_MAX_BYTES = 512 * 1024 * 1024
PKG_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')
DRIVER_KINDS = ('input', 'sound', 'display', 'storage', 'other')
APP_EXTS = ('.cosapp', '.nepapp')
COS_VERSION = theme.VERSION  # Neptune satisfies "requires_cos" up to its own version


class PackageError(Exception):
    pass


def human_size(n):
    for unit in ('bytes', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return ('%d %s' % (n, unit)) if unit == 'bytes' else ('%.1f %s' % (n, unit))
        n /= 1024.0


def version_tuple(v):
    out = []
    for part in str(v).split('.'):
        m = re.match(r'\d+', part)
        out.append(int(m.group()) if m else 0)
    return tuple(out)


def safe_member(name):
    if not name or '\\' in name or name.startswith('/') or '\x00' in name:
        return False
    if re.match(r'^[A-Za-z]:', name):
        return False
    return all(part not in ('', '.', '..') for part in name.split('/'))


def sha256_of_file(path):
    h = hashlib.sha256()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
    except OSError:
        return ''
    return h.hexdigest()


class Package:
    """One .cip or .cdi, opened and checked. Installs nothing by itself."""

    def __init__(self, path):
        self.path = os.path.abspath(os.path.expanduser(path))
        ext = os.path.splitext(self.path)[1].lower()
        if ext not in PKG_TYPES:
            raise PackageError('not a COS package (.cip or .cdi): %s' % os.path.basename(self.path))
        self.type = PKG_TYPES[ext]
        try:
            self.zip = zipfile.ZipFile(self.path)
        except (OSError, zipfile.BadZipFile) as e:
            raise PackageError('cannot open %s: %s' % (os.path.basename(self.path), e))
        self.members = {}
        total = 0
        for info in self.zip.infolist():
            if info.is_dir():
                continue
            if not safe_member(info.filename):
                raise PackageError('unsafe file name in package: %r' % info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise PackageError('package contains a symlink: %s' % info.filename)
            total += info.file_size
            self.members[info.filename] = info
        if len(self.members) > PKG_MAX_FILES:
            raise PackageError('package has too many files (%d)' % len(self.members))
        if total > PKG_MAX_BYTES:
            raise PackageError('package unpacks to %s, over the %s limit' % (human_size(total), human_size(PKG_MAX_BYTES)))
        self.size = total
        if PKG_MANIFEST not in self.members:
            raise PackageError('no %s in the package' % PKG_MANIFEST)
        try:
            self.manifest = json.loads(self.zip.read(PKG_MANIFEST).decode('utf-8'))
        except Exception as e:
            raise PackageError('%s is not valid JSON: %s' % (PKG_MANIFEST, e))
        self._check()

    def _check(self):
        m = self.manifest
        if not isinstance(m, dict):
            raise PackageError('%s must be an object' % PKG_MANIFEST)
        want = PKG_FORMATS[self.type]
        if m.get('format') != want:
            raise PackageError('format is %r; a .%s needs "format": "%s"' % (m.get('format'), self.type, want))
        self.name = str(m.get('name', ''))
        if not PKG_NAME_RE.match(self.name):
            raise PackageError('bad package name %r (letters, digits, . _ -)' % self.name)
        self.version = str(m.get('version', '0'))
        self.title = str(m.get('title') or self.name)
        self.author = str(m.get('author', ''))
        self.description = str(m.get('description', ''))
        self.requires = str(m.get('requires_cos', '') or '')
        if self.requires and version_tuple(COS_VERSION) < version_tuple(self.requires):
            raise PackageError('needs COS %s or newer (this is COS %s Neptune)' % (self.requires, COS_VERSION))
        # COS v2.4 only knew "license"; Neptune also accepts the British spelling
        self.license_key = 'license' if m.get('license') else ('licence' if m.get('licence') else None)
        for key in ('readme', self.license_key):
            if key and m.get(key) and m[key] not in self.members:
                raise PackageError('%s names %s, which is not in the package' % (key, m[key]))
        self.driver = None
        if self.type == 'cip':
            apps = m.get('apps', [])
            if not isinstance(apps, list) or not apps:
                raise PackageError('a .cip lists its programs in "apps"')
            for a in apps:
                if a not in self.members:
                    raise PackageError('app %s is not in the package' % a)
                if not a.endswith(APP_EXTS) or '/' in a:
                    raise PackageError('apps must be top-level .cosapp or .nepapp files: %s' % a)
            self.apps = list(apps)
        else:
            d = m.get('driver')
            if not isinstance(d, dict):
                raise PackageError('a .cdi describes itself in "driver"')
            mod = d.get('module')
            if not mod or mod not in self.members or not mod.endswith('.py'):
                raise PackageError('driver.module must name a .py file in the package')
            if d.get('kind', 'other') not in DRIVER_KINDS:
                raise PackageError('driver.kind must be one of %s' % ', '.join(DRIVER_KINDS))
            match = d.get('match')
            if not isinstance(match, list) or not match:
                raise PackageError('driver.match lists the devices it is for')
            for rule in match:
                if not isinstance(rule, dict) or rule.get('bus') not in ('usb', 'pci', 'input'):
                    raise PackageError('each match rule needs "bus": usb, pci or input')
            self.driver = d
            self.apps = []

    def text(self, member):
        try:
            return self.zip.read(member).decode('utf-8', 'replace')
        except Exception:
            return ''

    def readme(self):
        return self.text(self.manifest['readme']) if self.manifest.get('readme') else ''

    def license(self):
        return self.text(self.manifest[self.license_key]) if self.license_key else ''

    def plan(self):
        """[(member, destination)] for every file; nothing is written."""
        out = []
        base = os.path.join(PKG_DIR, self.name)
        for name in sorted(self.members):
            if name == PKG_MANIFEST:
                dest = os.path.join(base, PKG_MANIFEST)
            elif self.type == 'cip' and name in self.apps:
                dest = os.path.join(COMPAT_APP_DIR if name.endswith('.cosapp') else NEP_APP_DIR, name)
            elif self.type == 'cdi':
                dest = os.path.join(DRIVER_DIR, self.name, name)
            else:
                dest = os.path.join(base, 'files', name)
            out.append((name, dest))
        return out

    def installed_record(self):
        return read_record(self.name)

    def conflicts(self):
        mine = set()
        old = read_record(self.name)
        if old:
            mine = {f['path'] for f in old.get('files', [])}
        owned = {}
        for rec in installed_packages():
            if rec.get('name') != self.name:
                for f in rec.get('files', []):
                    owned[f['path']] = rec.get('name')
        out = []
        for _m, dest in self.plan():
            if dest in owned:
                out.append('%s (belongs to %s)' % (dest, owned[dest]))
            elif os.path.exists(dest) and dest not in mine and \
                    not dest.startswith(os.path.join(PKG_DIR, self.name) + os.sep):
                out.append('%s (already exists)' % dest)
        return out

    def install(self, progress=None, force=False):
        clash = self.conflicts()
        if clash and not force:
            raise PackageError('would overwrite: ' + '; '.join(clash[:4]) + (' ...' if len(clash) > 4 else ''))
        old = read_record(self.name)
        plan = self.plan()
        files = []
        for i, (member, dest) in enumerate(plan):
            if progress:
                progress(i, len(plan), member)
            data = self.zip.read(member)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp = dest + '.nep-install'
            with open(tmp, 'wb') as f:
                f.write(data)
            os.replace(tmp, dest)
            files.append({'path': dest, 'sha256': hashlib.sha256(data).hexdigest()})
        if old:
            keep = {f['path'] for f in files}
            for f in old.get('files', []):
                if f['path'] not in keep:
                    remove_if_unchanged(f)
        rec = {'name': self.name, 'title': self.title, 'type': self.type, 'version': self.version,
               'author': self.author, 'description': self.description,
               'installed': time.strftime('%Y-%m-%d %H:%M:%S'), 'source': self.path,
               'source_sha256': sha256_of_file(self.path), 'apps': self.apps, 'files': files,
               'size': self.size}
        if self.driver:
            rec['driver'] = self.driver
        write_record(rec)
        if progress:
            progress(len(plan), len(plan), 'done')
        return rec


def _record_path(name):
    return os.path.join(PKG_DIR, name, 'installed.json')


def read_record(name):
    try:
        with open(_record_path(name)) as f:
            return json.load(f)
    except Exception:
        return None


def write_record(rec):
    p = _record_path(rec['name'])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + '.tmp', 'w') as f:
        json.dump(rec, f, indent=2)
    os.replace(p + '.tmp', p)


def installed_packages(kind=None):
    out = []
    try:
        names = sorted(os.listdir(PKG_DIR), key=str.lower)
    except OSError:
        return out
    for n in names:
        rec = read_record(n)
        if rec and (kind is None or rec.get('type') == kind):
            out.append(rec)
    return out


def remove_if_unchanged(f):
    p = f['path']
    if not os.path.exists(p):
        return 'gone'
    if sha256_of_file(p) != f.get('sha256'):
        return 'kept'
    try:
        os.remove(p)
    except OSError:
        return 'kept'
    d = os.path.dirname(p)
    roots = (PKG_DIR, DRIVER_DIR, COMPAT_APP_DIR, NEP_APP_DIR, STATE)
    while d not in roots and d.startswith(STATE + os.sep):
        try:
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)
    return 'removed'


def uninstall(name):
    """Remove a package. Returns (removed, kept) lists of paths."""
    rec = read_record(name)
    if not rec:
        raise PackageError('%s is not installed' % name)
    removed, kept = [], []
    for f in rec.get('files', []):
        (kept if remove_if_unchanged(f) == 'kept' else removed).append(f['path'])
    try:
        os.remove(_record_path(name))
    except OSError:
        pass
    for d in (os.path.join(PKG_DIR, name), os.path.join(DRIVER_DIR, name)):
        try:
            os.rmdir(d)
        except OSError:
            pass
    return removed, kept


def build(src_dir, out_path=None):
    """Zip a folder holding package.json into a .cip/.cdi, then check it."""
    src_dir = os.path.abspath(os.path.expanduser(src_dir))
    try:
        with open(os.path.join(src_dir, PKG_MANIFEST)) as f:
            m = json.load(f)
    except Exception as e:
        raise PackageError('%s: %s' % (PKG_MANIFEST, e))
    kind = str(m.get('format', '')).split('/')[0]
    if kind not in PKG_FORMATS:
        raise PackageError('"format" must be "cip/1" or "cdi/1"')
    if not out_path:
        out_path = os.path.join(os.path.dirname(src_dir), '%s.%s' % (m.get('name', 'package'), kind))
    tmp = out_path + '.tmp'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, names in os.walk(src_dir):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d != '__pycache__')
            for n in sorted(names):
                if n.startswith('.') or n.endswith('.pyc'):
                    continue
                full = os.path.join(root, n)
                if os.path.islink(full):
                    raise PackageError('symlinks cannot go in a package: %s' % full)
                z.write(full, os.path.relpath(full, src_dir).replace(os.sep, '/'))
    os.replace(tmp, out_path)
    try:
        Package(out_path)
    except PackageError:
        os.remove(out_path)
        raise
    return out_path
