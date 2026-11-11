#!/usr/bin/env python3
"""Build ~/cos/pkgs/COS_Neptune_Upgrade.cip: the COS v2.4 -> Neptune upgrade.

Takes the wizard from ~/cos/pkgsrc/neptune_upgrade and the current Neptune
program files (no home/, no cache/, no backups) and zips them into a package
COS v2.4's own installer accepts. Run it again whenever Neptune changes.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from nep import packages  # noqa: E402

SRC = os.path.expanduser('~/cos/pkgsrc/neptune_upgrade')
OUT = os.path.expanduser('~/cos/pkgs/COS_Neptune_Upgrade.cip')
PROGRAM = ('nep', 'neptune.py', 'assets', 'README.md', 'docs')
SKIP = shutil.ignore_patterns('__pycache__', '*.pyc', '*.bak', '*.orig', '*~')


def main():
    with tempfile.TemporaryDirectory() as tmp:
        work = os.path.join(tmp, 'neptune_upgrade')
        shutil.copytree(SRC, work, ignore=SKIP)
        dest = os.path.join(work, 'neptune')            # installs to ~/.cos/packages/NeptuneUpgrade/files/neptune
        os.makedirs(dest)
        for item in PROGRAM:
            s = os.path.join(ROOT, item)
            if os.path.isdir(s):
                shutil.copytree(s, os.path.join(dest, item), ignore=SKIP)
            elif os.path.isfile(s):
                shutil.copy2(s, os.path.join(dest, item))
        out = packages.build(work, OUT)
        p = packages.Package(out)
        print('built %s  (%d files, %.1f MB unpacked)' % (out, len(p.members),
                                                           sum(i.file_size for i in p.zip.infolist()) / 1e6
                                                           if hasattr(p, 'zip') else 0))


if __name__ == '__main__':
    main()
