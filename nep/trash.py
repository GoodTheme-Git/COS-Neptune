"""The Recycle Bin: deleted files go here first, so a delete can be undone.

Each deleted item is moved (not copied) into home/.neptune/trash/files/<id>
with a small record next to it in home/.neptune/trash/info/<id>.json saying
where it came from and when. Restore moves it back (to a new name if
something now sits in its old place). Empty and Delete permanently remove for
good. Only things inside Neptune's home folder can be recycled, like every
other change Neptune makes.
"""
import json
import os
import shutil
import time
import uuid

from . import theme

TRASH = os.path.join(theme.STATE, 'trash')
FILES = os.path.join(TRASH, 'files')
INFO = os.path.join(TRASH, 'info')


class TrashError(Exception):
    pass


def _inside_home(p):
    home = os.path.realpath(theme.HOME)
    r = os.path.realpath(p)
    return r.startswith(home + os.sep)


def _size(p):
    if os.path.islink(p) or not os.path.isdir(p):
        try:
            return os.lstat(p).st_size
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def recycle(path):
    """Move path into the Recycle Bin. Returns the item's id."""
    path = os.path.abspath(path)
    if not os.path.lexists(path):
        raise TrashError('"%s" is not there any more.' % os.path.basename(path))
    if not _inside_home(path) or os.path.realpath(path).startswith(os.path.realpath(TRASH)):
        raise TrashError('Only things inside Neptune\'s home folder can go in the Recycle Bin.')
    os.makedirs(FILES, exist_ok=True)
    os.makedirs(INFO, exist_ok=True)
    tid = time.strftime('%Y%m%d%H%M%S-') + uuid.uuid4().hex[:6]
    rec = {'id': tid, 'name': os.path.basename(path), 'from': os.path.dirname(path), 'deleted': time.time(),
           'is_dir': os.path.isdir(path) and not os.path.islink(path), 'size': _size(path)}
    os.rename(path, os.path.join(FILES, tid))          # same filesystem (both in home), so this is instant
    with open(os.path.join(INFO, tid + '.json'), 'w') as f:
        json.dump(rec, f)
    return tid


def items():
    """Everything in the bin, newest first."""
    out = []
    try:
        names = os.listdir(INFO)
    except OSError:
        return out
    for n in names:
        if not n.endswith('.json'):
            continue
        try:
            with open(os.path.join(INFO, n)) as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if os.path.lexists(os.path.join(FILES, rec.get('id', ''))):
            out.append(rec)
    out.sort(key=lambda r: -r.get('deleted', 0))
    return out


def total_size():
    return sum(r.get('size', 0) for r in items())


def restore(tid):
    """Put an item back where it came from. Returns the path it was restored to."""
    src = os.path.join(FILES, tid)
    info = os.path.join(INFO, tid + '.json')
    with open(info) as f:
        rec = json.load(f)
    folder = rec['from']
    if not _inside_home(os.path.join(folder, 'x')):
        raise TrashError('That item came from outside Neptune\'s home folder.')
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, rec['name'])
    if os.path.lexists(dest):
        stem, ext = os.path.splitext(rec['name'])
        i = 2
        while os.path.lexists(dest):
            dest = os.path.join(folder, '%s (restored %d)%s' % (stem, i, ext) if i > 2 else '%s (restored)%s' % (stem, ext))
            i += 1
    os.rename(src, dest)
    os.remove(info)
    return dest


def delete_forever(tid):
    p = os.path.join(FILES, tid)
    if os.path.isdir(p) and not os.path.islink(p):
        shutil.rmtree(p)
    elif os.path.lexists(p):
        os.remove(p)
    try:
        os.remove(os.path.join(INFO, tid + '.json'))
    except OSError:
        pass


def empty():
    n = 0
    for rec in items():
        delete_forever(rec['id'])
        n += 1
    return n
