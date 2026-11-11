"""Webinator's cookie jar, with the rules browsers use (RFC 6265bis).

One jar for all of Webinator: page loads, images, stylesheets and the
requests page scripts make all go through it. Every cookie keeps the rules
it was set with:

* Domain / host-only: a cookie for ".google.com" (set by accounts.google.com)
  is sent to www.google.com too; a host-only one is sent only to its host.
  A site can't set a cookie for a public suffix like ".co.uk" or ".com"
  (checked against the system's Public Suffix List).
* Path: sent only for URLs under its path.
* Secure: sent only over https; an http page can't set or overwrite one.
* HttpOnly: never visible to page scripts, and scripts can't create or
  overwrite one. HttpOnly values never leave Python: script requests that
  carry cookies are made by Python on the script's behalf.
* SameSite: Strict cookies go only to same-site requests; Lax (the default
  when a site doesn't say) also to top-level GET navigations from other
  sites; None (which must be Secure) everywhere.
* Expires / Max-Age (Max-Age wins). Session cookies (neither) live only
  until Neptune exits; persistent ones are saved in
  home/.neptune/web/cookies.json, readable only by you (0600).
* The __Secure- and __Host- name prefixes.
"""
import email.utils
import ipaddress
import json
import os
import re
import threading
import time
import urllib.parse

from .. import theme

JAR_FILE = os.path.join(theme.STATE, 'web', 'cookies.json')
OLD_STORE_DIR = os.path.join(theme.STATE, 'web', 'storage')
PSL_FILES = ('/usr/share/publicsuffix/public_suffix_list.dat', '/usr/share/publicsuffix/effective_tld_names.dat')
MAX_COOKIE_BYTES = 4096
MAX_PER_DOMAIN = 180
MAX_TOTAL = 3000
MAX_AGE_LIMIT = 400 * 86400          # browsers cap expiry at 400 days


# =============================================================================
# public suffixes
# =============================================================================
class _PSL:
    def __init__(self):
        self.rules, self.wild, self.excl = set(), set(), set()
        self.loaded = False
        for p in PSL_FILES:
            try:
                with open(p, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('//'):
                            continue
                        rule = line.split()[0].lower()
                        try:
                            rule = rule.encode('idna').decode('ascii') if not rule.isascii() else rule
                        except UnicodeError:
                            pass
                        if rule.startswith('!'):
                            self.excl.add(rule[1:])
                        elif rule.startswith('*.'):
                            self.wild.add(rule[2:])
                        else:
                            self.rules.add(rule)
                self.loaded = True
                break
            except OSError:
                continue

    def suffix(self, host):
        """The public suffix of host (e.g. 'co.uk' for 'bbc.co.uk')."""
        labels = host.split('.')
        best = labels[-1]                       # implicit '*' rule
        for i in range(len(labels)):
            cand = '.'.join(labels[i:])
            if cand in self.excl:
                return '.'.join(labels[i + 1:])
            if cand in self.rules:
                return cand
            parent = '.'.join(labels[i + 1:])
            if i + 1 < len(labels) and parent in self.wild:
                return cand
        return best


_psl = None


def psl():
    global _psl
    if _psl is None:
        _psl = _PSL()
    return _psl


def is_ip(host):
    try:
        ipaddress.ip_address(host.strip('[]'))
        return True
    except ValueError:
        return False


def is_public_suffix(domain):
    return not is_ip(domain) and psl().suffix(domain) == domain


def site_of(host):
    """Registrable domain ('eTLD+1'): accounts.google.com -> google.com."""
    host = (host or '').lower().rstrip('.')
    if not host or is_ip(host) or '.' not in host:
        return host
    suf = psl().suffix(host)
    if host == suf:
        return host
    rest = host[:-(len(suf) + 1)]
    return rest.rsplit('.', 1)[-1] + '.' + suf


def same_site(url_a, url_b):
    a, b = urllib.parse.urlsplit(url_a), urllib.parse.urlsplit(url_b)
    return site_of(a.hostname or '') == site_of(b.hostname or '')


def domain_match(host, domain):
    return host == domain or (not is_ip(host) and host.endswith('.' + domain))


def default_path(path):
    if not path or not path.startswith('/'):
        return '/'
    if path.count('/') == 1:
        return '/'
    return path[:path.rfind('/')]


def path_match(req_path, cookie_path):
    if req_path == cookie_path:
        return True
    if req_path.startswith(cookie_path):
        return cookie_path.endswith('/') or req_path[len(cookie_path):len(cookie_path) + 1] == '/'
    return False


def _parse_expires(v):
    v = v.strip()
    try:
        return email.utils.parsedate_to_datetime(v).timestamp()
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ('%a, %d-%b-%Y %H:%M:%S GMT', '%a, %d-%b-%y %H:%M:%S GMT', '%A, %d-%b-%y %H:%M:%S GMT'):
        try:
            return time.mktime(time.strptime(v, fmt)) - time.timezone
        except ValueError:
            continue
    return None


# =============================================================================
# the jar
# =============================================================================
class Cookie:
    __slots__ = ('name', 'value', 'domain', 'host_only', 'path', 'expires', 'secure', 'httponly', 'samesite',
                 'created', 'accessed')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def persistent(self):
        return self.expires is not None

    def key(self):
        return (self.name, self.domain, self.path)

    def to_json(self):
        return {k: getattr(self, k) for k in self.__slots__}


class CookieJar:
    def __init__(self, path=JAR_FILE):
        self.path = path
        self.lock = threading.RLock()
        self.cookies = {}                 # key -> Cookie
        self._load()
        self._migrate_old_store()

    # -- persistence -----------------------------------------------------------------
    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        now = time.time()
        for d in data.get('cookies', []):
            try:
                c = Cookie(**d)
                if c.expires is not None and c.expires > now:
                    self.cookies[c.key()] = c
            except TypeError:
                continue

    def save(self):
        with self.lock:
            data = {'version': 1, 'cookies': [c.to_json() for c in self.cookies.values() if c.persistent]}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + '.tmp'
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def _migrate_old_store(self):
        """Older Webinator kept bare name=value cookies per site. Their rules are
        unknown, so they come across in the safest form: host-only, HttpOnly
        (scripts can't read them), Secure on https sites, expiring in 7 days."""
        try:
            names = os.listdir(OLD_STORE_DIR)
        except OSError:
            return
        moved = False
        for n in names:
            p = os.path.join(OLD_STORE_DIR, n)
            if not n.endswith('.json'):
                continue
            try:
                with open(p) as f:
                    d = json.load(f)
            except (OSError, ValueError):
                continue
            old = d.get('cookies')
            if not old:
                continue
            m = re.match(r'^(https?)_(.+)\.json$', n)
            if m:
                scheme, host = m.group(1), m.group(2).split('_')[0].lower()
                now = time.time()
                for name, value in old.items():
                    c = Cookie(name=str(name), value=str(value), domain=host, host_only=True, path='/',
                               expires=now + 7 * 86400, secure=scheme == 'https', httponly=True, samesite='lax',
                               created=now, accessed=now)
                    self.cookies.setdefault(c.key(), c)
            d.pop('cookies', None)
            try:
                tmp = p + '.tmp'
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w') as f:
                    json.dump(d, f)
                os.replace(tmp, p)
                moved = True
            except OSError:
                pass
        if moved:
            self.save()

    # -- storing ------------------------------------------------------------------------
    def set_cookie(self, header, url, from_script=False):
        """Store one Set-Cookie value (or a document.cookie assignment) set for url.
        Returns True if the jar changed."""
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or '').lower().rstrip('.')
        if not host or u.scheme not in ('http', 'https'):
            return False
        secure_origin = u.scheme == 'https' or host in ('localhost', '127.0.0.1', '::1')
        parts = header.split(';')
        nv = parts[0]
        if '=' in nv:
            name, value = nv.split('=', 1)
        else:
            name, value = '', nv
        name, value = name.strip(), value.strip()
        if not name and not value:
            return False
        if len(name) + len(value) > MAX_COOKIE_BYTES or re.search(r'[\x00-\x08\x0a-\x1f\x7f]', name + value):
            return False
        attrs = {}
        for p in parts[1:]:
            k, _, v = p.partition('=')
            attrs[k.strip().lower()] = v.strip()
        now = time.time()
        expires = None
        if 'max-age' in attrs and re.fullmatch(r'-?\d+', attrs['max-age'] or 'x'):
            age = int(attrs['max-age'])
            expires = now + min(age, MAX_AGE_LIMIT) if age > 0 else 0
        elif attrs.get('expires'):
            t = _parse_expires(attrs['expires'])
            if t is not None:
                expires = min(t, now + MAX_AGE_LIMIT) if t > now else 0
        domain = attrs.get('domain', '').lower().lstrip('.').rstrip('.')
        if domain:
            if is_public_suffix(domain):
                if domain != host:
                    return False              # nobody may set a cookie for all of ".com"
                domain = ''
            elif not domain_match(host, domain):
                return False                  # a site can only set cookies for itself and its parents
        host_only = not domain
        domain = domain or host
        path = attrs.get('path', '')
        if not path.startswith('/'):
            path = default_path(u.path)
        secure = 'secure' in attrs
        httponly = 'httponly' in attrs
        samesite = attrs.get('samesite', '').lower()
        samesite = samesite if samesite in ('strict', 'lax', 'none') else 'lax'
        if secure and not secure_origin:
            return False
        if samesite == 'none' and not secure:
            return False                      # SameSite=None is only allowed with Secure
        if name.startswith('__Secure-') and not (secure and secure_origin):
            return False
        if name.startswith('__Host-') and not (secure and secure_origin and host_only and path == '/'):
            return False
        if from_script and httponly:
            return False                      # scripts can't create HttpOnly cookies
        with self.lock:
            # an insecure origin may not overwrite or shadow a Secure cookie
            if not secure_origin:
                for c in self.cookies.values():
                    if c.secure and c.name == name and (domain_match(c.domain, domain) or domain_match(domain, c.domain)) \
                            and path_match(path, c.path):
                        return False
            # scripts may neither overwrite nor shadow an HttpOnly cookie
            if from_script:
                for c in self.cookies.values():
                    if c.httponly and c.name == name and \
                            (domain_match(c.domain, domain) or domain_match(domain, c.domain)) and \
                            (path_match(path, c.path) or path_match(c.path, path)):
                        return False
            key = (name, domain, path)
            old = self.cookies.get(key)
            if expires == 0:
                if old is not None:
                    del self.cookies[key]
                    self._changed(old)
                    return True
                return False
            c = Cookie(name=name, value=value, domain=domain, host_only=host_only, path=path, expires=expires,
                       secure=secure, httponly=httponly, samesite=samesite,
                       created=old.created if old else now, accessed=now)
            self.cookies[key] = c
            self._evict(domain)
            self._changed(c if c.persistent else old)
            return True

    def set_from_headers(self, headers, url):
        if headers is None:
            return False
        changed = False
        for h in headers.get_all('Set-Cookie') or []:
            changed = self.set_cookie(h, url) or changed
        return changed

    def _evict(self, domain):
        now = time.time()
        for k in [k for k, c in self.cookies.items() if c.expires is not None and c.expires <= now]:
            del self.cookies[k]
        same = sorted((c for c in self.cookies.values() if c.domain == domain), key=lambda c: c.accessed or 0)
        for c in same[:max(0, len(same) - MAX_PER_DOMAIN)]:
            self.cookies.pop(c.key(), None)
        if len(self.cookies) > MAX_TOTAL:
            for c in sorted(self.cookies.values(), key=lambda c: c.accessed or 0)[:len(self.cookies) - MAX_TOTAL]:
                self.cookies.pop(c.key(), None)

    def _changed(self, c):
        if c is not None and c.persistent:
            try:
                self.save()
            except OSError:
                pass

    # -- reading -----------------------------------------------------------------------------
    def matching(self, url):
        u = urllib.parse.urlsplit(url)
        host = (u.hostname or '').lower().rstrip('.')
        secure_ch = u.scheme in ('https', 'wss') or host in ('localhost', '127.0.0.1', '::1')
        path = u.path or '/'
        now = time.time()
        out = []
        with self.lock:
            for c in list(self.cookies.values()):
                if c.expires is not None and c.expires <= now:
                    del self.cookies[c.key()]
                    continue
                if c.host_only:
                    if host != c.domain:
                        continue
                elif not domain_match(host, c.domain):
                    continue
                if not path_match(path, c.path):
                    continue
                if c.secure and not secure_ch:
                    continue
                out.append(c)
        out.sort(key=lambda c: (-len(c.path), c.created or 0))
        return out

    def header_for(self, url, initiator=None, kind='navigate', method='GET'):
        """The Cookie header for a request.

        initiator: the URL of the page that caused the request, or None when you
        asked for it yourself (typed it, a favourite, Back). kind: 'navigate'
        for a top-level page load, 'subresource' for images, styles and script
        requests."""
        cross = initiator is not None and not same_site(url, initiator)
        out = []
        now = time.time()
        for c in self.matching(url):
            if cross:
                if c.samesite == 'strict':
                    continue
                if c.samesite == 'lax' and not (kind == 'navigate' and method.upper() in ('GET', 'HEAD')):
                    continue
            c.accessed = now
            out.append('%s=%s' % (c.name, c.value) if c.name else c.value)
        return '; '.join(out)

    def script_view(self, url):
        """What document.cookie shows on a page: no HttpOnly cookies, ever."""
        vis = [(c.name, c.value) for c in self.matching(url) if not c.httponly]
        hidden = sorted({c.name for c in self.matching(url) if c.httponly})
        return vis, hidden

    def clear(self, site=None):
        with self.lock:
            if site is None:
                self.cookies.clear()
            else:
                for k in [k for k, c in self.cookies.items() if site_of(c.domain) == site]:
                    del self.cookies[k]
        try:
            self.save()
        except OSError:
            pass

    def sites(self):
        with self.lock:
            out = {}
            for c in self.cookies.values():
                out[site_of(c.domain)] = out.get(site_of(c.domain), 0) + 1
        return out


_jar = None
_jar_lock = threading.Lock()


def jar():
    global _jar
    with _jar_lock:
        if _jar is None:
            _jar = CookieJar()
        return _jar
