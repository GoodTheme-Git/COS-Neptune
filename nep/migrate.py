"""Bring COS v2.4 settings into Neptune (used by Setup / the OOBE).

Reads ~/.cos_config and ~/.cos/bookmarks.txt; never writes outside Neptune's
own home folder, and never overwrites something Neptune already has unless asked.
"""
import json
import os
import shutil

from . import theme

COS_CONFIG = os.path.expanduser('~/.cos_config')
COS_BOOKMARKS = os.path.expanduser('~/.cos/bookmarks.txt')


def load_cos_config():
    try:
        with open(COS_CONFIG) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def available():
    """{item: description} of what can be imported from this machine's COS v2.4."""
    cfg = load_cos_config()
    out = {}
    if not cfg and not os.path.isfile(COS_BOOKMARKS):
        return out
    if 'sound_on' in cfg:
        out['sound'] = 'Sounds %s' % ('on' if cfg['sound_on'] else 'off')
    pic = os.path.expanduser(cfg.get('wallpaper_image') or '')
    if pic and os.path.isfile(pic):
        out['wallpaper'] = 'Wallpaper picture (%s)' % os.path.basename(pic)
    if any(cfg.get(k) for k in ('ibbs_server', 'ibbs_name', 'ibbs_dir')):
        out['ibbs'] = 'IBBS board settings%s' % (' (%s)' % cfg['ibbs_server'] if cfg.get('ibbs_server') else '')
    try:
        n = sum(1 for ln in open(COS_BOOKMARKS) if ln.strip())
    except OSError:
        n = 0
    if n:
        out['bookmarks'] = '%d web bookmark(s)' % n
    if cfg.get('username') or cfg.get('login_user'):
        out['name'] = 'Your name (%s)' % (cfg.get('username') or cfg.get('login_user'))
    return out


def import_cos24(settings, items):
    """Apply the chosen items to a live Settings object (and Neptune's state files). Returns notes."""
    cfg = load_cos_config()
    notes = []
    state = theme.STATE
    os.makedirs(state, exist_ok=True)
    if 'sound' in items and 'sound_on' in cfg:
        settings['sounds'] = bool(cfg['sound_on'])
        notes.append('sounds')
    pic = os.path.expanduser(cfg.get('wallpaper_image') or '')
    if 'wallpaper' in items and pic and os.path.isfile(pic):
        walls = os.path.join(state, 'wallpapers')
        os.makedirs(walls, exist_ok=True)
        dest = os.path.join(walls, os.path.basename(pic))
        if os.path.realpath(dest) != os.path.realpath(pic):
            shutil.copy2(pic, dest)
        fit = cfg.get('wallpaper_fit', 'fill')
        settings['wall'] = {'kind': 'image', 'path': dest, 'fit': fit if fit in ('fill', 'fit', 'stretch') else 'fill',
                            'bg': '#000000'}
        notes.append('wallpaper')
    if 'ibbs' in items:
        path = os.path.join(state, 'ibbs.json')
        if not os.path.exists(path):
            with open(path, 'w') as f:
                json.dump({'name': cfg.get('ibbs_name', ''), 'server': cfg.get('ibbs_server', ''),
                           'token': cfg.get('ibbs_token', ''), 'board_dir': cfg.get('ibbs_dir', ''),
                           'room': cfg.get('ibbs_room') or 'general', 'notify': True}, f, indent=2)
            os.chmod(path, 0o600)                       # it can hold the board token
            notes.append('IBBS settings')
    if 'bookmarks' in items and os.path.isfile(COS_BOOKMARKS):
        favs = os.path.join(state, 'favorites.txt')
        have = set()
        try:
            with open(favs) as f:
                have = {ln.rstrip('\n').split('\t', 1)[-1] for ln in f if '\t' in ln}
        except OSError:
            pass
        added = 0
        with open(COS_BOOKMARKS) as f, open(favs, 'a') as out:
            for ln in f:
                url, _, title = ln.rstrip('\n').partition('\t')        # v2.4: url<TAB>title
                url = url.strip()
                if url and url not in have:
                    out.write('%s\t%s\n' % (title.strip() or url, url))  # Neptune: title<TAB>url
                    have.add(url)
                    added += 1
        if added:
            notes.append('%d bookmark(s)' % added)
    if 'name' in items and (cfg.get('username') or cfg.get('login_user')):
        settings['user_name'] = cfg.get('username') or cfg.get('login_user')
        notes.append('your name')
    return notes
