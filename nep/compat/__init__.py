"""COS compatibility mode: run COS 2.x programs (.cosapp) inside Neptune.

cursesshim.py  a stand-in `curses` module (keys, attributes, line drawing)
cos24.py       the `cos` API a v2.4 app expects, drawing into a cell grid
host.py        the Neptune window that shows one COS window, plus settings
"""
