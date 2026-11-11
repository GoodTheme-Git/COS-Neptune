"""Neptune Task Manager: COS v2.4's system monitor and process manager,
rebuilt in the Windows 7 style. Applications, Processes and Performance tabs,
with CPU and memory history graphs and End Process.

Everything is read straight from /proc, so no extra packages are needed."""
import os
import pwd
import signal
import time

from PIL import ImageDraw

from .. import gfx, theme
from ..app import App
from .dialogs import message

HISTORY = 90          # samples kept for the graphs (one per second)
ROW_H = 19


def _human_kb(kb):
    if kb >= 1024 * 1024:
        return '%.1f GB' % (kb / 1048576.0)
    if kb >= 1024:
        return '{:,} K'.format(int(kb))
    return '%d K' % kb


class ProcStat:
    """Samples /proc once a second and keeps the deltas needed for CPU %."""

    def __init__(self):
        self.ncpu = os.cpu_count() or 1
        self.page_kb = os.sysconf('SC_PAGE_SIZE') // 1024
        self.clk = os.sysconf('SC_CLK_TCK')
        self.prev_total = None
        self.prev_cores = None
        self.prev_proc = {}
        self.cpu = 0.0
        self.cores = [0.0] * self.ncpu
        self.cpu_hist = [None] * HISTORY
        self.core_hist = [[None] * HISTORY for _ in range(self.ncpu)]
        self.mem_hist = [None] * HISTORY
        self.mem = {}
        self.procs = []
        self.threads = 0
        self.users = {}
        self.sample()

    def _user(self, uid):
        if uid not in self.users:
            try:
                self.users[uid] = pwd.getpwuid(uid).pw_name
            except KeyError:
                self.users[uid] = str(uid)
        return self.users[uid]

    def sample(self):
        # -- whole-machine CPU ------------------------------------------------------
        cores = []
        total = None
        with open('/proc/stat') as f:
            for line in f:
                if not line.startswith('cpu'):
                    break
                parts = line.split()
                vals = [int(v) for v in parts[1:9]]
                idle = vals[3] + vals[4]
                rec = (sum(vals), idle)
                if parts[0] == 'cpu':
                    total = rec
                else:
                    cores.append(rec)
        dt_total = 0
        if self.prev_total is not None:
            dt_total = total[0] - self.prev_total[0]
            didle = total[1] - self.prev_total[1]
            self.cpu = 100.0 * (1 - didle / dt_total) if dt_total > 0 else 0.0
            for i, (c, p) in enumerate(zip(cores, self.prev_cores)):
                dt = c[0] - p[0]
                if i < len(self.cores):
                    self.cores[i] = 100.0 * (1 - (c[1] - p[1]) / dt) if dt > 0 else 0.0
        first = self.prev_total is None
        self.prev_total, self.prev_cores = total, cores
        if not first:
            self.cpu_hist = self.cpu_hist[1:] + [self.cpu]
            for i in range(min(self.ncpu, len(self.cores))):
                self.core_hist[i] = self.core_hist[i][1:] + [self.cores[i]]

        # -- memory -----------------------------------------------------------------
        mem = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':', 1)
                mem[k] = int(v.split()[0])
        self.mem = mem
        used = mem.get('MemTotal', 1) - mem.get('MemAvailable', 0)
        self.mem_used = used
        self.mem_pct = 100.0 * used / max(1, mem.get('MemTotal', 1))
        self.mem_hist = self.mem_hist[1:] + [self.mem_pct]

        # -- processes ----------------------------------------------------------------
        procs = []
        seen = {}
        threads = 0
        for name in os.listdir('/proc'):
            if not name.isdigit():
                continue
            pid = int(name)
            try:
                with open('/proc/%d/stat' % pid) as f:
                    st = f.read()
                uid = os.stat('/proc/%d' % pid).st_uid
            except OSError:
                continue
            l, r = st.find('('), st.rfind(')')
            comm = st[l + 1:r]
            rest = st[r + 2:].split()
            ticks = int(rest[11]) + int(rest[12])
            nthreads = int(rest[17])
            rss_kb = int(rest[21]) * self.page_kb
            threads += nthreads
            seen[pid] = ticks
            prev = self.prev_proc.get(pid)
            cpu = 0.0
            if prev is not None and dt_total > 0:
                cpu = 100.0 * (ticks - prev) / dt_total
            procs.append({'pid': pid, 'name': comm, 'user': self._user(uid), 'uid': uid,
                          'cpu': cpu, 'mem': rss_kb, 'threads': nthreads})
        self.prev_proc = seen
        self.procs = procs
        self.threads = threads
        try:
            with open('/proc/uptime') as f:
                self.uptime = float(f.read().split()[0])
        except OSError:
            self.uptime = 0


class TaskManager(App):
    title = 'Neptune Task Manager'
    icon = 'taskmgr'
    size = (600, 560)
    min_size = (460, 420)
    single_instance = True
    bg = theme.PANEL_BG
    TABS = ['Applications', 'Processes', 'Performance']
    COLS = [('name', 'Image Name', 0.34), ('user', 'User Name', 0.18), ('cpu', 'CPU', 0.10),
            ('mem', 'Memory (Private Working Set)', 0.26), ('pid', 'PID', 0.12)]

    def __init__(self, shell, tab=None, **kw):
        super().__init__(shell, **kw)
        self.tab = tab if tab in self.TABS else 'Processes'
        self.stat = ProcStat()
        self.last = time.time()
        self.sort = ('cpu', True)
        self.sel_pid = None
        self.sel_win = None
        self.scroll = 0
        self.only_mine = False
        self.me = os.getuid()

    # -- data -----------------------------------------------------------------------
    def on_tick(self, now):
        if now - self.last >= 1.0:
            self.last = now
            self.stat.sample()
            return True
        return False

    def rows(self):
        procs = [p for p in self.stat.procs if not self.only_mine or p['uid'] == self.me]
        key, rev = self.sort
        if key in ('name', 'user'):
            procs.sort(key=lambda p: (p[key].lower(), p['pid']), reverse=rev)
        else:
            procs.sort(key=lambda p: (p[key], -p['pid']), reverse=rev)
        return procs

    # -- drawing ----------------------------------------------------------------------
    def draw(self, img, ui):
        w, h = img.size
        ui.rect((0, 0, w, h), fill=theme.PANEL_BG)
        # tab strip
        x = 8
        tab_y0, tab_y1 = 8, 31
        for t in self.TABS:
            tw = gfx.text_width(gfx.font(12), t) + 22
            box = (x, tab_y0 if t == self.tab else tab_y0 + 2, x + tw, tab_y1 + (1 if t == self.tab else 0))
            hot = ui.hot(box)
            if t == self.tab:
                top, bot = (255, 255, 255), (255, 255, 255)
            elif hot:
                top, bot = (234, 246, 253), (190, 230, 253)
            else:
                top, bot = (243, 243, 243), (222, 222, 222)
            gfx.round_rect(img, box, 3, fill=gfx.vgradient(box[2] - box[0], box[3] - box[1], ((0, top), (1, bot))),
                           corners=(True, True, False, False))
            gfx.round_rect(img, box, 3, outline=(137, 140, 149, 255), corners=(True, True, False, False))
            ui.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), t, 12, anchor='mm')
            ui.region(box, ('tab', t))
            x += tw - 1
        page = (8, tab_y1, w - 8, h - 30)
        ui.rect(page, fill=(255, 255, 255), outline=(137, 140, 149))
        # erase the line under the active tab so it joins the page
        x = 8
        for t in self.TABS:
            tw = gfx.text_width(gfx.font(12), t) + 22
            if t == self.tab:
                ui.hline(x + 1, x + tw - 2, tab_y1, (255, 255, 255))
            x += tw - 1
        inner = (page[0] + 8, page[1] + 8, page[2] - 8, page[3] - 8)
        getattr(self, 'draw_' + self.tab.lower())(img, ui, inner)
        self.draw_status(img, ui, (0, h - 26, w, h))

    def draw_status(self, img, ui, box):
        x0, y0, x1, y1 = box
        ui.hline(x0, x1, y0, (200, 205, 212))
        s = self.stat
        parts = ['Processes: %d' % len(s.procs), 'CPU Usage: %d%%' % round(s.cpu),
                 'Physical Memory: %d%%' % round(s.mem_pct)]
        x = x0 + 10
        for p in parts:
            ui.text((x, (y0 + y1) // 2), p, 12, anchor='lm')
            x += max(150, gfx.text_width(gfx.font(12), p) + 30)

    # Applications ---------------------------------------------------------------
    def app_windows(self):
        return [w for w in self.shell.windows if not w.closing and w.app.show_in_taskbar and w is not self.win]

    def draw_applications(self, img, ui, box):
        x0, y0, x1, y1 = box
        lb = (x0, y0, x1, y1 - 40)
        ui.rect(lb, outline=(171, 173, 179))
        ui.text((x0 + 26, y0 + 10), 'Task', 12, anchor='lm')
        ui.text((x1 - 100, y0 + 10), 'Status', 12, anchor='lm')
        ui.hline(x0 + 1, x1 - 2, y0 + 20, (220, 225, 230))
        y = y0 + 23
        wins = self.app_windows()
        if self.sel_win not in wins:
            self.sel_win = None
        for wnd in wins:
            r = (x0 + 2, y, x1 - 2, y + ROW_H + 2)
            ui.listrow(r, ('win', id(wnd)), selected=wnd is self.sel_win, data=wnd)
            ui.icon(wnd.app.icon, (x0 + 6, y + 2), 16)
            ui.text((x0 + 26, y + 11), gfx.ellipsize(gfx.font(12), wnd.app.title, x1 - x0 - 140), 12, anchor='lm')
            ui.text((x1 - 100, y + 11), 'Not Responding' if getattr(wnd.app, 'hung', False) else 'Running', 12,
                    anchor='lm')
            y += ROW_H + 3
            if y > lb[3] - ROW_H:
                break
        if not wins:
            ui.text(((x0 + x1) // 2, y0 + 50), 'No other programs are running.', 12, color=theme.MUTED, anchor='mm')
        by = y1 - 30
        ui.button((x1 - 290, by, x1 - 196, by + 26), 'End Task', 'endtask', enabled=self.sel_win is not None)
        ui.button((x1 - 190, by, x1 - 96, by + 26), 'Switch To', 'switch', enabled=self.sel_win is not None)
        ui.button((x1 - 90, by, x1, by + 26), 'New Task...', 'newtask')

    # Processes ------------------------------------------------------------------
    def draw_processes(self, img, ui, box):
        x0, y0, x1, y1 = box
        lb = (x0, y0, x1, y1 - 40)
        ui.rect(lb, outline=(171, 173, 179))
        sbw = 17
        width = lb[2] - lb[0] - sbw - 2
        # header
        hx = lb[0] + 1
        hdr = (lb[0] + 1, lb[1] + 1, lb[2] - 1, lb[1] + 24)
        img.alpha_composite(gfx.vgradient(hdr[2] - hdr[0], hdr[3] - hdr[1],
                                          ((0, (255, 255, 255)), (1, (242, 244, 247)))), (hdr[0], hdr[1]))
        cols = []
        for key, label, frac in self.COLS:
            cw = int(width * frac)
            cbox = (hx, hdr[1], hx + cw, hdr[3])
            if ui.hot(cbox):
                ui.rect(cbox, fill=(227, 247, 255))
            ui.d.line([(cbox[2] - 1, cbox[1] + 3), (cbox[2] - 1, cbox[3] - 3)], fill=(222, 225, 230, 255))
            right = key in ('cpu', 'mem', 'pid')
            lab = gfx.ellipsize(gfx.font(12), label, cw - 16)
            ui.text((cbox[2] - 8 if right else cbox[0] + 6, (cbox[1] + cbox[3]) // 2), lab, 12,
                    color=(76, 96, 122), anchor='rm' if right else 'lm')
            if self.sort[0] == key:
                cx = (cbox[0] + cbox[2]) // 2
                pts = [(cx - 4, cbox[1] + 4), (cx + 4, cbox[1] + 4), (cx, cbox[1] + 1)] if not self.sort[1] else \
                    [(cx - 4, cbox[1] + 1), (cx + 4, cbox[1] + 1), (cx, cbox[1] + 4)]
                ui.d.polygon(pts, fill=(150, 160, 175, 255))
            ui.region(cbox, ('sort', key))
            cols.append((key, cbox[0], cbox[2], right))
            hx += cw
        ui.hline(hdr[0], hdr[2], hdr[3], (213, 213, 213))
        rows = self.rows()
        area_y0 = hdr[3] + 2
        page = max(1, (lb[3] - area_y0 - 2) // ROW_H)
        self.page = page
        self.scroll = gfx.clamp(self.scroll, 0, max(0, len(rows) - page))
        y = area_y0
        for p in rows[self.scroll:self.scroll + page]:
            r = (lb[0] + 2, y, lb[2] - sbw - 2, y + ROW_H)
            ui.listrow(r, ('proc', p['pid']), selected=p['pid'] == self.sel_pid, data=p)
            for key, cx0, cx1, right in cols:
                v = p[key]
                if key == 'cpu':
                    v = '%02d' % min(99, round(v))
                elif key == 'mem':
                    v = _human_kb(v)
                else:
                    v = str(v)
                v = gfx.ellipsize(gfx.font(12), v, cx1 - cx0 - 12)
                ui.text((cx1 - 8 if right else cx0 + 6, y + ROW_H // 2), v, 12, anchor='rm' if right else 'lm')
            y += ROW_H
        ui.scrollbar((lb[2] - sbw - 1, area_y0, lb[2] - 1, lb[3] - 1), 'pscroll', self.scroll, page, len(rows))
        by = y1 - 30
        ui.checkbox((x0, by + 5), 'Show processes from all users', 'allusers', not self.only_mine)
        ui.button((x1 - 120, by, x1, by + 26), 'End Process', 'endproc', enabled=self.sel_pid is not None)

    # Performance ------------------------------------------------------------------
    def meter(self, img, ui, box, pct, label):
        """Win7 'CPU Usage' LED column: black box, green segments."""
        x0, y0, x1, y1 = box
        ui.rect(box, fill=(0, 0, 0))
        bars = (y1 - y0 - 26) // 3
        lit = int(round(bars * pct / 100.0))
        bw = (x1 - x0 - 16) // 2
        for i in range(bars):
            y = y1 - 22 - i * 3
            on = i < lit
            col = (0, 255, 0) if on else (0, 70, 0)
            for bx in (x0 + 7, x0 + 9 + bw):
                ui.d.line([(bx, y), (bx + bw - 1, y)], fill=col + (255,))
        ui.text(((x0 + x1) // 2, y1 - 9), label, 11, color=(0, 255, 0), anchor='mm')

    def graph(self, img, ui, box, series, color=(0, 255, 0)):
        x0, y0, x1, y1 = box
        ui.rect(box, fill=(0, 0, 0))
        d = ui.d
        grid = (0, 128, 64, 255)
        step = 12
        off = int(time.time()) % 2 * (step // 2)    # the grid scrolls, like Windows
        for gx in range(x1 - 1 - off, x0, -step):
            d.line([(gx, y0 + 1), (gx, y1 - 2)], fill=grid)
        for gy in range(y1 - 1, y0, -step):
            d.line([(x0 + 1, gy), (x1 - 2, gy)], fill=grid)
        n = len(series)
        pts = []
        for i, v in enumerate(series):
            if v is None:
                continue
            px = x0 + 1 + (x1 - x0 - 3) * i / max(1, n - 1)
            py = y1 - 2 - (y1 - y0 - 4) * gfx.clamp(v, 0, 100) / 100.0
            pts.append((px, py))
        if len(pts) > 1:
            d.line(pts, fill=color + (255,), width=1)
        ui.rect(box, outline=(130, 135, 145))

    def draw_performance(self, img, ui, box):
        x0, y0, x1, y1 = box
        s = self.stat
        mw = 84
        gh = (y1 - y0 - 170) // 2
        # CPU
        ui.groupbox((x0, y0, x0 + mw + 12, y0 + gh + 22), 'CPU Usage')
        self.meter(img, ui, (x0 + 6, y0 + 18, x0 + mw + 6, y0 + gh + 16), s.cpu, '%d %%' % round(s.cpu))
        ui.groupbox((x0 + mw + 20, y0, x1, y0 + gh + 22), 'CPU Usage History')
        gx0, gx1 = x0 + mw + 26, x1 - 6
        n = s.ncpu
        per_row = max(1, (gx1 - gx0 + 4) // 32)
        nrows = -(-n // per_row)
        if n > 1 and nrows <= 3 and (gh - 4) // nrows >= 24:
            per_row = -(-n // nrows)
            cw = (gx1 - gx0 - (per_row - 1) * 4) // per_row
            rh = (gh - 2 - (nrows - 1) * 4) // nrows
            for i in range(n):
                cx = gx0 + (i % per_row) * (cw + 4)
                cy0 = y0 + 18 + (i // per_row) * (rh + 4)
                self.graph(img, ui, (cx, cy0, cx + cw, cy0 + rh), s.core_hist[i])
        else:
            self.graph(img, ui, (gx0, y0 + 18, gx1, y0 + gh + 16), s.cpu_hist)
        # Memory
        my = y0 + gh + 30
        tot = s.mem.get('MemTotal', 1)
        ui.groupbox((x0, my, x0 + mw + 12, my + gh + 22), 'Memory')
        lab = ('%.2f GB' % (s.mem_used / 1048576.0)) if s.mem_used > 1048576 else '%d MB' % (s.mem_used // 1024)
        self.meter(img, ui, (x0 + 6, my + 18, x0 + mw + 6, my + gh + 16), s.mem_pct, lab)
        ui.groupbox((x0 + mw + 20, my, x1, my + gh + 22), 'Physical Memory Usage History')
        self.graph(img, ui, (gx0, my + 18, gx1, my + gh + 16), s.mem_hist, (255, 255, 0))
        # numbers
        ty = my + gh + 32
        colw = (x1 - x0) // 3

        def table(cx, title, rows):
            ui.groupbox((cx, ty, cx + colw - 8, y1), title)
            yy = ty + 20
            for k, v in rows:
                ui.text((cx + 8, yy), k, 12)
                ui.text((cx + colw - 18, yy), v, 12, anchor='ra')
                yy += 17

        mb = lambda k: '{:,}'.format(s.mem.get(k, 0) // 1024)
        table(x0, 'Physical Memory (MB)', [('Total', mb('MemTotal')), ('Cached', mb('Cached')),
                                           ('Available', mb('MemAvailable')), ('Free', mb('MemFree'))])
        up = int(s.uptime)
        table(x0 + colw, 'System', [('Users', str(len({p['uid'] for p in s.procs}))),
                                    ('Threads', '{:,}'.format(s.threads)), ('Processes', str(len(s.procs))),
                                    ('Up Time', '%d:%02d:%02d:%02d' % (up // 86400, up // 3600 % 24, up // 60 % 60,
                                                                       up % 60))])
        table(x0 + 2 * colw, 'Swap (MB)', [('Total', mb('SwapTotal')), ('Free', mb('SwapFree')),
                                           ('Cores', str(s.ncpu)), ('Load', '%.2f' % os.getloadavg()[0])])

    # -- input ------------------------------------------------------------------------
    def on_command(self, cid, data):
        if isinstance(cid, tuple):
            kind = cid[0]
            if kind == 'tab':
                self.tab = cid[1]
                self.scroll = 0
            elif kind == 'sort':
                key = cid[1]
                if self.sort[0] == key:
                    self.sort = (key, not self.sort[1])
                else:
                    self.sort = (key, key in ('cpu', 'mem', 'pid'))
            elif kind == 'proc':
                self.sel_pid = cid[1]
            elif kind == 'win':
                self.sel_win = data
            self.invalidate()
            return
        if cid == 'pscroll':
            self.scroll = data
        elif cid == 'allusers':
            self.only_mine = not data
        elif cid == 'endproc':
            self.end_process()
        elif cid == 'endtask' and self.sel_win is not None:
            self.shell.close_window(self.sel_win)
            self.sel_win = None
        elif cid == 'switch' and self.sel_win is not None:
            w = self.sel_win
            if w.minimized:
                self.shell.restore_window(w)
            self.shell.activate(w)
        elif cid == 'newtask':
            self.shell.launch('run')
        self.invalidate()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'win':
            self.sel_win = data
            self.on_command('switch', None)
        else:
            self.on_command(cid, data)

    def end_process(self):
        p = next((p for p in self.stat.procs if p['pid'] == self.sel_pid), None)
        if p is None:
            self.sel_pid = None
            return
        if p['pid'] == os.getpid():
            message(self.shell, 'Neptune Task Manager',
                    'That is Neptune itself. To leave, use POWER > Exit to Console.', 'warning', parent=self.win)
            return

        def go(ans):
            if ans != 'End process':
                return
            try:
                os.kill(p['pid'], signal.SIGTERM)
                self.shell.notify('Task Manager', 'Asked %s (PID %d) to end.' % (p['name'], p['pid']), 'info', 4)
            except PermissionError:
                message(self.shell, 'Unable to Terminate Process',
                        'The operation could not be completed.\n\nAccess is denied. %s belongs to %s.' %
                        (p['name'], p['user']), 'error', parent=self.win)
            except ProcessLookupError:
                pass
            self.sel_pid = None
            self.invalidate()

        message(self.shell, 'Neptune Task Manager',
                'Do you want to end "%s" (PID %d)?\n\nIf an open program is associated with this process, it will '
                'close and you will lose any unsaved data.' % (p['name'], p['pid']),
                'warning', buttons=('End process', 'Cancel'), callback=go, parent=self.win)

    def on_wheel(self, dy, x, y):
        if self.tab == 'Processes':
            self.scroll = max(0, self.scroll - dy * 3)
            self.invalidate()

    def on_key(self, ev):
        k = ev['key']
        if k == 'delete' and self.tab == 'Processes' and self.sel_pid is not None:
            self.end_process()
            return True
        if self.tab == 'Processes' and k in ('up', 'down', 'pageup', 'pagedown'):
            rows = self.rows()
            pids = [p['pid'] for p in rows]
            i = pids.index(self.sel_pid) if self.sel_pid in pids else -1
            step = {'up': -1, 'down': 1, 'pageup': -getattr(self, 'page', 10), 'pagedown': getattr(self, 'page', 10)}[k]
            i = gfx.clamp(i + step, 0, len(pids) - 1)
            if pids:
                self.sel_pid = pids[i]
                page = getattr(self, 'page', 10)
                if i < self.scroll:
                    self.scroll = i
                elif i >= self.scroll + page:
                    self.scroll = i - page + 1
            return True
        if ev.get('ctrl') and k == 'tab':
            i = self.TABS.index(self.tab)
            self.tab = self.TABS[(i + (-1 if ev.get('shift') else 1)) % len(self.TABS)]
            return True
        return False
