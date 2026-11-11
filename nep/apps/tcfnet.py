"""TCFNet: the TCF Communication Network, opened in Webinator.

It's an ordinary Webinator window that starts at the TCFNet address and wears
the TCFNet name and icon. Links, forms, favourites and downloads all work as
they do in Webinator. Set TCFNET_URL to point it somewhere else.
"""
import os

from .webinator import Webinator

TCFNET_URL = os.environ.get('TCFNET_URL', 'https://main.taila6def0.ts.net/')
TCFNET_NAME = 'TCFNet - TCF Communication Network'


class TCFNet(Webinator):
    title = TCFNET_NAME
    icon = 'tcfnet'

    def __init__(self, shell, url=None, **kw):
        super().__init__(shell, url=url or TCFNET_URL, **kw)

    def set_title(self, t):
        if t == 'Webinator':
            t = TCFNET_NAME
        elif t.endswith(' - Webinator'):
            t = t[:-len(' - Webinator')] + ' - TCFNet'
        super().set_title(t)
