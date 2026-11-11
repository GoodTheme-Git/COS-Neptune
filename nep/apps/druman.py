"""Druman: the COS music AND video player (drum + man = druman), back from
COS v2.4. In Neptune it renders real pixels instead of character cells.

* Audio plays through ffplay (no window). Video is decoded by ffmpeg into raw
  RGB frames on a worker thread, then paced against the audio clock.
* Audio-only files get a live spectrum visualiser, computed with numpy from
  the decoded track, so the bars match what you hear.
* Pause stops both processes with SIGSTOP/SIGCONT. Seeking restarts them at
  the new position.
"""
import json
import os
import signal
import subprocess
import threading
import time

import numpy as np
from PIL import Image, ImageDraw

from .. import gfx, theme
from ..app import App
from .dialogs import FileDialog, message

AUDIO_EXTS = ('.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.opus')
VIDEO_EXTS = ('.mp4', '.mkv', '.webm', '.avi', '.mov', '.m4v', '.mpg', '.mpeg', '.wmv', '.flv', '.gif')
MEDIA_EXTS = AUDIO_EXTS + VIDEO_EXTS
VIS_RATE = 22050
BARS = 40
CTRL_H = 70
LIST_W = 230


def is_media(name):
    return name.lower().endswith(MEDIA_EXTS)


def fmt_time(t):
    t = max(0, int(t))
    return '%d:%02d:%02d' % (t // 3600, t // 60 % 60, t % 60) if t >= 3600 else '%02d:%02d' % (t // 60, t % 60)


def probe(path):
    info = {'duration': 0.0, 'video': False, 'audio': False, 'w': 0, 'h': 0, 'fps': 25.0, 'title': '', 'artist': ''}
    try:
        out = subprocess.run(['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', path],
                             capture_output=True, timeout=8).stdout
        data = json.loads(out or b'{}')
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return info
    fmt = data.get('format', {})
    try:
        info['duration'] = float(fmt.get('duration', 0) or 0)
    except ValueError:
        pass
    tags = {k.lower(): v for k, v in (fmt.get('tags') or {}).items()}
    info['title'] = tags.get('title', '')
    info['artist'] = tags.get('artist', '') or tags.get('album_artist', '')
    for s in data.get('streams', []):
        if s.get('codec_type') == 'audio':
            info['audio'] = True
        elif s.get('codec_type') == 'video' and not (s.get('disposition') or {}).get('attached_pic'):
            info['video'] = True
            info['w'], info['h'] = int(s.get('width') or 0), int(s.get('height') or 0)
            try:
                a, b = (s.get('avg_frame_rate') or s.get('r_frame_rate') or '25/1').split('/')
                fps = float(a) / float(b or 1)
                if 1 <= fps <= 120:
                    info['fps'] = fps
            except (ValueError, ZeroDivisionError):
                pass
    return info


class Druman(App):
    title = 'Druman'
    icon = 'druman'
    size = (780, 520)
    min_size = (420, 300)
    bg = (12, 16, 22, 255)
    dark_client = True

    def __init__(self, shell, path=None, **kw):
        super().__init__(shell, **kw)
        self.files = []
        self.idx = -1
        self.info = None
        self.procs = []
        self.state = 'stopped'          # stopped | playing | paused
        self.t0 = 0.0
        self.pos = 0.0
        self.volume = int(shell.settings.get('druman_volume', 80) or 80)
        self.show_list = True
        self.repeat = True
        self.list_scroll = 0
        self.sel = -1
        self.frame = None
        self.frame_new = False
        self.vthread = None
        self.vstop = None
        self.vsize = None
        self.pcm = None
        self.pcm_path = None
        self.levels = np.zeros(BARS)
        self.peaks = np.zeros(BARS)
        self.seek_preview = None
        self.vol_preview = None
        self.last_draw = 0
        self.last_resize = None
        if path:
            folder = os.path.dirname(path)
            self.playlist_name = os.path.basename(folder) or 'Playlist'
            self.load_folder(folder)
            if path in self.files:
                self.play(self.files.index(path))
            else:
                self.files.insert(0, path)
                self.play(0)
        else:
            self.load_default()

    # -- playlist --------------------------------------------------------------------
    def load_folder(self, folder):
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            names = []
        self.files = [os.path.join(folder, n) for n in names if is_media(n) and not n.startswith('.')]

    def load_default(self):
        for folder in (os.path.join(theme.HOME, 'Music'), os.path.join(theme.HOME, 'Videos'),
                       os.path.expanduser('~/Music'), os.path.expanduser('~/Videos'),
                       os.path.join(theme.ASSETS, 'sounds')):
            self.load_folder(folder)
            if self.files:
                self.playlist_name = 'COS Sounds' if folder.startswith(theme.ASSETS) else os.path.basename(folder)
                return
        self.playlist_name = 'Playlist'

    def add_files(self, paths):
        for p in paths:
            if p not in self.files:
                self.files.append(p)

    # -- engine ------------------------------------------------------------------------
    @property
    def position(self):
        if self.state == 'playing':
            return time.time() - self.t0
        return self.pos

    def _kill(self):
        if self.vstop is not None:
            self.vstop.set()
        for p in self.procs:
            try:
                if self.state == 'paused':
                    p.send_signal(signal.SIGCONT)
                p.kill()
            except OSError:
                pass
        for p in self.procs:
            try:
                p.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.procs = []
        self.vthread = None

    def video_box(self):
        w, h = self.client_size
        x0 = LIST_W if self.show_list else 0
        return (x0, 0, w, h - CTRL_H)

    def fit_size(self):
        bx = self.video_box()
        aw, ah = max(16, bx[2] - bx[0]), max(16, bx[3] - bx[1])
        vw, vh = self.info['w'] or 16, self.info['h'] or 9
        s = min(aw / vw, ah / vh)
        return max(2, int(vw * s) // 2 * 2), max(2, int(vh * s) // 2 * 2)

    def play(self, idx, pos=0.0):
        if not self.files:
            return
        idx %= len(self.files)
        changed = idx != self.idx or self.info is None
        self._kill()
        self.idx = idx
        self.sel = idx
        path = self.files[idx]
        if changed:
            self.info = probe(path)
            self.frame = None
            name = os.path.splitext(os.path.basename(path))[0]
            self.set_title('%s - Druman' % (self.info['title'] or name))
        info = self.info
        if not info['audio'] and not info['video']:
            self.state = 'stopped'
            message(self.shell, 'Druman', 'Druman cannot play this file:\n%s' % os.path.basename(path), 'error',
                    parent=self.win)
            return
        pos = gfx.clamp(pos, 0, max(0, info['duration'] - 0.2)) if info['duration'] else max(0, pos)
        if info['audio']:
            try:
                self.procs.append(subprocess.Popen(
                    ['ffplay', '-nodisp', '-autoexit', '-vn', '-loglevel', 'quiet', '-ss', '%.2f' % pos,
                     '-volume', str(int(self.volume)), path],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True))
            except OSError:
                pass
        if info['video']:
            self.start_video(path, pos)
        elif self.pcm_path != path:
            self.pcm, self.pcm_path = None, path
            threading.Thread(target=self._decode_pcm, args=(path,), daemon=True).start()
        self.t0 = time.time() - pos
        self.pos = pos
        self.state = 'playing'
        self.invalidate()

    def start_video(self, path, pos):
        w, h = self.fit_size()
        self.vsize = (w, h)
        fps = min(30.0, self.info['fps'])
        self.vstop = threading.Event()
        try:
            proc = subprocess.Popen(
                ['ffmpeg', '-nostdin', '-loglevel', 'quiet', '-ss', '%.2f' % pos, '-i', path, '-an',
                 '-vf', 'scale=%d:%d:flags=bilinear' % (w, h), '-r', '%.3f' % fps,
                 '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                start_new_session=True, bufsize=w * h * 3 * 2)
        except OSError:
            return
        self.procs.append(proc)
        self.vthread = threading.Thread(target=self._video_loop, args=(proc, w, h, fps, pos, self.vstop), daemon=True)
        self.vthread.start()

    def _video_loop(self, proc, w, h, fps, pos0, stop):
        n = w * h * 3
        k = 0
        while not stop.is_set():
            buf = proc.stdout.read(n)
            if not buf or len(buf) < n:
                break
            ft = pos0 + k / fps
            k += 1
            while not stop.is_set():
                if self.state == 'paused':
                    time.sleep(0.03)
                    continue
                lead = ft - self.position
                if lead <= 0.004:
                    break
                time.sleep(min(lead, 0.03))
            if stop.is_set():
                break
            if self.position - ft > 2.5 / fps:
                continue        # running late: drop this frame
            self.frame = Image.frombytes('RGB', (w, h), buf)
            self.frame_new = True

    def _decode_pcm(self, path):
        try:
            out = subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'quiet', '-i', path, '-vn', '-ac', '1',
                                  '-ar', str(VIS_RATE), '-f', 's16le', '-'],
                                 capture_output=True, timeout=120).stdout
        except (OSError, subprocess.TimeoutExpired):
            return
        if self.pcm_path == path:
            self.pcm = np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0

    def toggle(self):
        if self.state == 'playing':
            self.pos = self.position
            for p in self.procs:
                try:
                    p.send_signal(signal.SIGSTOP)
                except OSError:
                    pass
            self.state = 'paused'
        elif self.state == 'paused':
            for p in self.procs:
                try:
                    p.send_signal(signal.SIGCONT)
                except OSError:
                    pass
            self.t0 = time.time() - self.pos
            self.state = 'playing'
        else:
            self.play(self.sel if self.sel >= 0 else 0)
        self.invalidate()

    def stop(self):
        self._kill()
        self.state = 'stopped'
        self.pos = 0.0
        self.levels[:] = 0
        self.peaks[:] = 0
        self.invalidate()

    def seek(self, t):
        if self.idx < 0 or self.state == 'stopped' and not self.info:
            return
        was_paused = self.state == 'paused'
        self.play(self.idx, t)
        if was_paused:
            self.toggle()

    def step(self, d):
        if self.files:
            if d < 0 and self.position > 3:
                self.seek(0)
            else:
                self.play((self.idx if self.idx >= 0 else 0) + d)

    # -- shell hooks ----------------------------------------------------------------------
    @property
    def live(self):
        return self.state == 'playing'

    @property
    def inhibit_saver(self):
        """No screensaver while a video plays (music is fine)."""
        return self.state == 'playing' and bool(self.info and self.info.get('video'))

    def on_tick(self, now):
        if self.state == 'playing':
            dur = self.info['duration'] if self.info else 0
            if dur and self.position >= dur + 0.15:
                if self.idx < len(self.files) - 1 or self.repeat:
                    self.play(self.idx + 1)
                else:
                    self.stop()
                return True
            if self.info and self.info['video']:
                if self.frame_new:
                    self.frame_new = False
                    return True
                return int(now) != int(self.last_draw)
            return now - self.last_draw > 1 / 30.0
        if self.last_resize and now - self.last_resize > 0.35:
            self.last_resize = None
        return False

    def on_resize(self):
        if self.info and self.info['video'] and self.state != 'stopped' and self.fit_size() != self.vsize:
            self.seek(self.position)

    def on_close(self):
        self._kill()
        self.state = 'stopped'
        return True

    # -- drawing ----------------------------------------------------------------------------
    def draw(self, img, ui):
        self.last_draw = time.time()
        w, h = img.size
        img.alpha_composite(gfx.vgradient(w, h, ((0, (18, 26, 36)), (1, (4, 6, 10)))), (0, 0))
        if self.show_list:
            self.draw_list(img, ui, (0, 0, LIST_W, h - CTRL_H))
        vb = self.video_box()
        if self.info and self.info['video'] and self.frame is not None:
            fw, fh = self.frame.size
            ui.rect(vb, fill=(0, 0, 0))
            img.paste(self.frame, ((vb[0] + vb[2] - fw) // 2, (vb[1] + vb[3] - fh) // 2))
        else:
            self.draw_vis(img, ui, vb)
        self.draw_controls(img, ui, (0, h - CTRL_H, w, h))

    def draw_list(self, img, ui, box):
        x0, y0, x1, y1 = box
        img.alpha_composite(gfx.vgradient(x1 - x0, y1 - y0, ((0, (34, 46, 60)), (1, (16, 22, 30)))), (x0, y0))
        ui.d.line([(x1 - 1, y0), (x1 - 1, y1)], fill=(60, 80, 100, 255))
        ui.text((x0 + 12, y0 + 16), getattr(self, 'playlist_name', 'Playlist'), 13, bold=True,
                color=(220, 235, 245), anchor='lm')
        ui.text((x1 - 10, y0 + 16), '%d item%s' % (len(self.files), '' if len(self.files) == 1 else 's'), 11, color=(130, 150, 170), anchor='rm')
        ui.d.line([(x0 + 10, y0 + 30), (x1 - 10, y0 + 30)], fill=(60, 80, 100, 255))
        rh = 24
        top = y0 + 36
        page = max(1, (y1 - top - 34) // rh)
        self.list_page = page
        self.list_scroll = gfx.clamp(self.list_scroll, 0, max(0, len(self.files) - page))
        fnt = gfx.font(12)
        for i in range(self.list_scroll, min(len(self.files), self.list_scroll + page)):
            y = top + (i - self.list_scroll) * rh
            r = (x0 + 4, y, x1 - 6, y + rh - 2)
            hot = ui.hot(r)
            if i == self.idx or i == self.sel or hot:
                a = 120 if i == self.sel else 60
                gfx.round_rect(img, r, 3, fill=gfx.vgradient(r[2] - r[0], r[3] - r[1],
                                                            ((0, (90, 170, 230, a)), (1, (40, 100, 170, a)))))
            name = os.path.splitext(os.path.basename(self.files[i]))[0]
            col = (120, 220, 255) if i == self.idx else (220, 228, 236)
            if i == self.idx and self.state != 'stopped':
                ui.text((r[0] + 8, (r[1] + r[3]) // 2), '▶' if self.state == 'playing' else '❚❚', 9,
                        color=col, anchor='lm')
            ui.text((r[0] + 24, (r[1] + r[3]) // 2), gfx.ellipsize(fnt, name, r[2] - r[0] - 30), 12, color=col,
                    anchor='lm')
            ui.region(r, ('track', i), 'row', i)
        if not self.files:
            ui.wrap((x0 + 14, top + 10), 'No media yet. Click Open, or put music in\n%s' %
                    os.path.join(theme.HOME, 'Music'), x1 - x0 - 28, 11, color=(140, 160, 180))
        by = y1 - 30
        for i, (lab, cid) in enumerate((('Open...', 'open'), ('Clear', 'clear'))):
            bx = x0 + 8 + i * 74
            self.dark_button(img, ui, (bx, by, bx + 68, by + 22), lab, cid)

    def dark_button(self, img, ui, box, label, cid):
        hot = ui.hot(box)
        top, bot = ((80, 140, 190), (30, 70, 120)) if hot else ((70, 80, 92), (36, 42, 50))
        gfx.round_rect(img, box, 3, fill=gfx.vgradient(box[2] - box[0], box[3] - box[1], ((0, top), (1, bot))))
        gfx.round_rect(img, box, 3, outline=(110, 130, 150, 255))
        ui.text(((box[0] + box[2]) // 2, (box[1] + box[3]) // 2), label, 11, color=(235, 240, 245), anchor='mm')
        ui.region(box, cid)

    def compute_levels(self):
        if self.pcm is None or self.state != 'playing':
            self.levels *= 0.85
            self.peaks = np.maximum(self.peaks - 0.015, 0)
            return
        n = 2048
        i = int(self.position * VIS_RATE)
        seg = self.pcm[max(0, i - n // 2): i + n // 2]
        if len(seg) < n:
            seg = np.pad(seg, (0, n - len(seg)))
        spec = np.abs(np.fft.rfft(seg * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, 1.0 / VIS_RATE)
        edges = np.geomspace(40, VIS_RATE / 2, BARS + 1)
        lv = np.zeros(BARS)
        for b in range(BARS):
            m = (freqs >= edges[b]) & (freqs < edges[b + 1])
            if m.any():
                lv[b] = spec[m].mean()
        lv = np.clip((20 * np.log10(lv + 1e-6) + 20) / 50.0, 0, 1)
        self.levels = np.maximum(lv, self.levels * 0.78)
        self.peaks = np.maximum(self.levels, self.peaks - 0.012)

    def draw_vis(self, img, ui, box):
        x0, y0, x1, y1 = box
        bw, bh = x1 - x0, y1 - y0
        if bw < 40 or bh < 60:
            return
        glow = gfx.radial(bw, bh, ((0, (30, 120, 150, 90)), (0.7, (10, 40, 70, 30)), (1, (0, 0, 0, 0))), cy=0.75)
        img.alpha_composite(glow, (x0, y0))
        self.compute_levels()
        tint = tuple(self.shell.settings.get('glass_rgb', [38, 150, 160]))
        top_col = gfx.mix(tint, (255, 255, 255), 0.55)[:3]
        base = y0 + int(bh * 0.72)
        maxh = int(bh * 0.52)
        gap = 3
        slot = (bw - 40) / BARS
        cw = max(2, int(slot - gap))
        for b in range(BARS):
            bx = int(x0 + 20 + b * slot)
            hgt = int(maxh * self.levels[b])
            if hgt > 0:
                bar = gfx.vgradient(cw, hgt, ((0, top_col + (235,)), (1, tint + (200,))))
                img.alpha_composite(bar, (bx, base - hgt))
                # glassy shine on the left half
                img.alpha_composite(Image.new('RGBA', (max(1, cw // 3), hgt), (255, 255, 255, 50)), (bx, base - hgt))
                # reflection
                rh = min(hgt // 3, y1 - base - 2)
                if rh > 1:
                    ref = gfx.vgradient(cw, rh, ((0, tint + (70,)), (1, tint + (0,))))
                    img.alpha_composite(ref, (bx, base + 2))
            py = base - int(maxh * self.peaks[b]) - 4
            ui.d.rectangle((bx, py, bx + cw - 1, py + 1), fill=(220, 245, 255, 220))
        # track caption
        if self.info and self.idx >= 0:
            name = self.info['title'] or os.path.splitext(os.path.basename(self.files[self.idx]))[0]
            fnt = gfx.font(22, False, 'light')
            gfx.shadow_text(img, ((x0 + x1) // 2, y0 + 34), gfx.ellipsize(fnt, name, bw - 40), fnt,
                            (240, 250, 255, 255), (0, 0, 0, 200), radius=3, anchor='mm')
            sub = self.info['artist'] or ('Audio' if not self.info['video'] else 'Loading video...')
            ui.text(((x0 + x1) // 2, y0 + 62), sub, 12, color=(150, 190, 210), anchor='mm')
        else:
            fnt = gfx.font(26, False, 'light')
            gfx.shadow_text(img, ((x0 + x1) // 2, y0 + bh // 3), 'Druman', fnt, (240, 250, 255, 255),
                            (0, 0, 0, 200), radius=3, anchor='mm')
            ui.text(((x0 + x1) // 2, y0 + bh // 3 + 30), 'the COS music and video player', 12,
                    color=(150, 190, 210), anchor='mm')

    def draw_controls(self, img, ui, box):
        x0, y0, x1, y1 = box
        w = x1 - x0
        img.alpha_composite(gfx.vgradient(w, y1 - y0, ((0, (70, 80, 92)), (0.45, (38, 44, 52)),
                                                       (0.46, (20, 24, 30)), (1, (8, 10, 14)))), (x0, y0))
        ui.d.line([(x0, y0), (x1, y0)], fill=(120, 135, 150, 255))
        # seek bar
        dur = self.info['duration'] if self.info else 0
        pos = self.position
        frac = (self.seek_preview if self.seek_preview is not None else (pos / dur if dur else 0))
        sb = (x0 + 70, y0 + 8, x1 - 70, y0 + 16)
        gfx.round_rect(img, sb, 3, fill=(0, 0, 0, 200), outline=(90, 100, 115, 255))
        fx = int(sb[0] + (sb[2] - sb[0]) * gfx.clamp(frac, 0, 1))
        if fx > sb[0] + 2:
            gfx.round_rect(img, (sb[0] + 1, sb[1] + 1, fx, sb[3] - 1), 2,
                           fill=gfx.vgradient(fx - sb[0] - 1, sb[3] - sb[1] - 2,
                                              ((0, (140, 220, 255)), (1, (30, 110, 200)))))
        if ui.hot((sb[0], sb[1] - 4, sb[2], sb[3] + 4)) or self.seek_preview is not None:
            ui.d.ellipse((fx - 6, sb[1] - 3, fx + 6, sb[3] + 3), fill=(230, 245, 255, 255), outline=(60, 120, 180, 255))
        ui.region((sb[0], sb[1] - 4, sb[2], sb[3] + 4), 'seek', 'slider')
        ui.text((x0 + 62, y0 + 12), fmt_time(frac * dur if self.seek_preview is not None else pos), 11,
                color=(200, 215, 225), anchor='rm')
        ui.text((x1 - 62, y0 + 12), fmt_time(dur), 11, color=(200, 215, 225), anchor='lm')
        # transport
        cy = y0 + 44
        cx = (x0 + x1) // 2
        self.round_btn(img, ui, (cx, cy), 21, 'play', 'pause' if self.state == 'playing' else 'play', big=True)
        self.round_btn(img, ui, (cx - 48, cy), 14, 'prev', 'prev')
        self.round_btn(img, ui, (cx + 48, cy), 14, 'next', 'next')
        self.round_btn(img, ui, (cx - 86, cy), 12, 'stop', 'stop')
        # left: playlist toggle + repeat
        self.flat_btn(img, ui, (x0 + 12, cy - 11, x0 + 40, cy + 11), 'list', 'list', self.show_list)
        self.flat_btn(img, ui, (x0 + 46, cy - 11, x0 + 74, cy + 11), 'repeat', 'repeat', self.repeat)
        # right: volume
        vol = self.vol_preview if self.vol_preview is not None else self.volume
        vx1 = x1 - 16
        vx0 = vx1 - 90
        self.flat_btn(img, ui, (vx0 - 32, cy - 11, vx0 - 6, cy + 11), 'mute', 'speaker', vol == 0)
        ui.d.rectangle((vx0, cy - 2, vx1, cy + 2), fill=(0, 0, 0, 220), outline=(90, 100, 115, 255))
        kx = int(vx0 + (vx1 - vx0) * vol / 100.0)
        if kx > vx0 + 1:
            ui.d.rectangle((vx0 + 1, cy - 1, kx, cy + 1), fill=(120, 200, 255, 255))
        ui.d.ellipse((kx - 6, cy - 6, kx + 6, cy + 6), fill=(220, 230, 240, 255), outline=(40, 50, 60, 255))
        ui.region((vx0 - 4, cy - 10, vx1 + 4, cy + 10), 'vol', 'slider')

    def _glyph(self, d, kind, cx, cy, s, col):
        if kind == 'play':
            d.polygon([(cx - s * 0.35, cy - s * 0.55), (cx - s * 0.35, cy + s * 0.55), (cx + s * 0.6, cy)], fill=col)
        elif kind == 'pause':
            for dx in (-s * 0.42, s * 0.12):
                d.rectangle((cx + dx, cy - s * 0.5, cx + dx + s * 0.3, cy + s * 0.5), fill=col)
        elif kind == 'stop':
            d.rectangle((cx - s * 0.4, cy - s * 0.4, cx + s * 0.4, cy + s * 0.4), fill=col)
        elif kind in ('prev', 'next'):
            sg = -1 if kind == 'prev' else 1
            for off in (-0.45, 0.1):
                o = off * s * sg
                d.polygon([(cx + o, cy - s * 0.45), (cx + o, cy + s * 0.45), (cx + o + sg * s * 0.5, cy)], fill=col)
            bx = cx + sg * s * 0.62
            d.rectangle((min(bx, bx + sg * 2), cy - s * 0.45, max(bx, bx + sg * 2), cy + s * 0.45), fill=col)
        elif kind == 'list':
            for i in range(3):
                y = cy - s * 0.4 + i * s * 0.4
                d.rectangle((cx - s * 0.5, y - 1, cx + s * 0.5, y + 1), fill=col)
        elif kind == 'repeat':
            d.arc((cx - s * 0.55, cy - s * 0.45, cx + s * 0.55, cy + s * 0.45), 200, 520, fill=col, width=2)
            d.polygon([(cx + s * 0.35, cy - s * 0.65), (cx + s * 0.7, cy - s * 0.35), (cx + s * 0.25, cy - s * 0.2)],
                      fill=col)
        elif kind == 'speaker':
            d.polygon([(cx - s * 0.6, cy - s * 0.2), (cx - s * 0.3, cy - s * 0.2), (cx + s * 0.1, cy - s * 0.55),
                       (cx + s * 0.1, cy + s * 0.55), (cx - s * 0.3, cy + s * 0.2), (cx - s * 0.6, cy + s * 0.2)],
                      fill=col)
            d.arc((cx - s * 0.2, cy - s * 0.45, cx + s * 0.55, cy + s * 0.45), 300, 60, fill=col, width=2)

    def round_btn(self, img, ui, c, r, cid, glyph, big=False):
        cx, cy = c
        box = (cx - r, cy - r, cx + r, cy + r)
        hot = ui.hot(box)
        pressed = ui.pressed(cid) and hot
        if big:
            if hot:
                g = gfx.radial(r * 4, r * 4, ((0, (90, 200, 255, 150)), (1, (0, 0, 0, 0))))
                img.alpha_composite(g, (cx - r * 2, cy - r * 2))
            top, bot = ((90, 170, 230), (10, 60, 140)) if not pressed else ((40, 110, 180), (10, 40, 100))
        else:
            top, bot = ((110, 120, 135), (30, 34, 42)) if hot else ((80, 88, 100), (22, 26, 32))
            if pressed:
                top, bot = bot, top
        mask = Image.new('L', (r * 2, r * 2), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, r * 2 - 1, r * 2 - 1), fill=255)
        face = gfx.vgradient(r * 2, r * 2, ((0, top), (1, bot)))
        gfx.paste_masked(img, face, mask, (cx - r, cy - r))
        d = ui.d
        d.ellipse((cx - r, cy - r, cx + r - 1, cy + r - 1), outline=(10, 14, 20, 255))
        d.ellipse((cx - r + 1, cy - r + 1, cx + r - 2, cy + r - 2), outline=(255, 255, 255, 60))
        # gloss
        gl = Image.new('L', (r * 2, r), 0)
        ImageDraw.Draw(gl).ellipse((r * 0.25, 2, r * 1.75, r * 0.95), fill=70)
        lay = Image.new('RGBA', (r * 2, r), (255, 255, 255, 0))
        lay.putalpha(gl)
        img.alpha_composite(lay, (cx - r, cy - r))
        self._glyph(d, glyph, cx, cy, r * 0.75, (255, 255, 255, 255))
        ui.region(box, cid)

    def flat_btn(self, img, ui, box, cid, glyph, on):
        hot = ui.hot(box)
        if on or hot:
            a = 110 if on else 60
            gfx.round_rect(img, box, 3, fill=(90, 170, 230, a), outline=(120, 180, 230, 140))
        col = (140, 215, 255, 255) if on else (215, 225, 235, 255)
        self._glyph(ui.d, glyph, (box[0] + box[2]) // 2, (box[1] + box[3]) // 2, (box[3] - box[1]) * 0.55, col)
        ui.region(box, cid)

    # -- input -------------------------------------------------------------------------------
    def on_slider(self, cid, value):
        if cid == 'seek':
            self.seek_preview = value
        elif cid == 'vol':
            self.vol_preview = int(round(value * 100))

    def mouse(self, kind, x, y, button=1, clicks=1):
        super().mouse(kind, x, y, button, clicks)
        if kind == 'up':
            if self.seek_preview is not None:
                v, self.seek_preview = self.seek_preview, None
                if self.info and self.info['duration']:
                    self.seek(v * self.info['duration'])
            if self.vol_preview is not None:
                self.set_volume(self.vol_preview)
                self.vol_preview = None
            self.invalidate()

    def set_volume(self, v):
        self.volume = gfx.clamp(int(v), 0, 100)
        if self.state != 'stopped' and self.info and self.info['audio']:
            self.seek(self.position)

    def on_command(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'track':
            self.sel = cid[1]
        elif cid == 'play':
            self.toggle()
        elif cid == 'stop':
            self.stop()
        elif cid == 'prev':
            self.step(-1)
        elif cid == 'next':
            self.step(1)
        elif cid == 'list':
            self.show_list = not self.show_list
            self.on_resize()
        elif cid == 'repeat':
            self.repeat = not self.repeat
        elif cid == 'mute':
            self._unmute = getattr(self, '_unmute', 80)
            if self.volume:
                self._unmute = self.volume
                self.set_volume(0)
            else:
                self.set_volume(self._unmute)
        elif cid == 'open':
            self.open_dialog()
        elif cid == 'clear':
            self.stop()
            self.files, self.idx, self.sel, self.info = [], -1, -1, None
            self.set_title('Druman')
        self.invalidate()

    def on_activate(self, cid, data):
        if isinstance(cid, tuple) and cid[0] == 'track':
            self.play(cid[1])
        else:
            self.on_command(cid, data)

    def open_dialog(self):
        def done(p):
            self.add_files([p])
            folder = os.path.dirname(p)
            for f in sorted(os.listdir(folder), key=str.lower):
                fp = os.path.join(folder, f)
                if is_media(f) and fp != p and not f.startswith('.'):
                    self.add_files([fp])
            self.play(self.files.index(p))
        start = self.files[self.idx if self.idx >= 0 else 0] if self.files else os.path.expanduser('~')
        start = os.path.dirname(start) if os.path.isfile(start) else start
        self.shell.open_window(FileDialog(self.shell, 'open', start=start, exts=MEDIA_EXTS, callback=done),
                               parent=self.win)

    def on_wheel(self, dy, x, y):
        if self.show_list and x < LIST_W:
            self.list_scroll = max(0, self.list_scroll - dy * 3)
        else:
            self.set_volume(self.volume + dy * 5)
        self.invalidate()

    def on_key(self, ev):
        k, ch = ev['key'], ev.get('char', '')
        if ch == ' ' or k == 'space':
            self.toggle()
        elif k == 'left':
            self.seek(self.position - (30 if ev.get('ctrl') else 5))
        elif k == 'right':
            self.seek(self.position + (30 if ev.get('ctrl') else 5))
        elif k == 'up':
            self.set_volume(self.volume + 5)
        elif k == 'down':
            self.set_volume(self.volume - 5)
        elif k == 'enter' and self.sel >= 0:
            self.play(self.sel)
        elif ch in ('n', 'N'):
            self.step(1)
        elif ch in ('p', 'P'):
            self.step(-1)
        elif ch in ('s', 'S'):
            self.stop()
        elif ch in ('l', 'L'):
            self.on_command('list', None)
        elif ev.get('ctrl') and k == 'o':
            self.open_dialog()
        else:
            return False
        return True

    def context_menu(self, x, y):
        return [
            {'label': 'Pause' if self.state == 'playing' else 'Play', 'action': self.toggle},
            {'label': 'Stop', 'action': self.stop},
            {'sep': True},
            {'label': 'Previous', 'action': lambda: self.step(-1)},
            {'label': 'Next', 'action': lambda: self.step(1)},
            {'sep': True},
            {'label': 'Open File...', 'action': self.open_dialog},
            {'label': 'Show Playlist', 'checked': self.show_list, 'action': lambda: self.on_command('list', None)},
            {'label': 'Repeat', 'checked': self.repeat, 'action': lambda: self.on_command('repeat', None)},
        ]
