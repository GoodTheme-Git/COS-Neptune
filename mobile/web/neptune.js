/* COS Neptune Mobile -- the shell: settings, wallpapers, lock, home, windows,
   recents, notification shade, dialogs and the little on-device file system.
   Apps live in apps.js and register themselves with NM.register(). */
'use strict';
const NM = window.NM = {};
NM.VERSION = '3.1';
NM.CODENAME = 'Neptune Mobile';
NM.$ = (s, r) => (r || document).querySelector(s);
NM.$$ = (s, r) => Array.from((r || document).querySelectorAll(s));
NM.el = (tag, attrs, ...kids) => {
  const e = document.createElement(tag);
  for (const k in (attrs || {})) {
    const v = attrs[k];
    if (k === 'class') e.className = v;
    else if (k === 'style' && typeof v === 'object') Object.assign(e.style, v);
    else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
    else if (k === 'html') e.innerHTML = v;
    else if (v !== false && v != null) e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
};
NM.esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const { $, $$, el } = NM;

/* ---------------------------------------------------------------- storage */
NM.store = {
  get(k, d) { try { const v = localStorage.getItem('nm.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('nm.' + k, JSON.stringify(v)); return true; } catch (e) { NM.toast('Storage is full: ' + (e.name || e)); return false; } },
};

NM.GLASS = [
  ['Neptune', [38, 150, 160]], ['Sky', [66, 140, 210]], ['Twilight', [40, 70, 140]], ['Sea', [30, 130, 120]],
  ['Leaf', [80, 160, 60]], ['Lime', [150, 190, 40]], ['Sun', [230, 180, 40]], ['Pumpkin', [230, 120, 30]],
  ['Ruby', [190, 30, 50]], ['Fuchsia', [200, 50, 160]], ['Violet', [120, 70, 190]], ['Lavender', [160, 140, 210]],
  ['Taupe', [150, 130, 110]], ['Chocolate', [100, 60, 40]], ['Slate', [90, 100, 110]], ['Frost', [220, 230, 240]],
];
const DEFAULTS = {
  glass: 'Neptune', intensity: 0.42, transparency: true, aero: true, wallpaper: 'Neptune Aurora',
  clock24: false, sounds: true, volume: 80, pin: '', lockOnStart: true, bootScreen: true, animations: true,
  user: '', device: '', oobe: false, bubbo: true, tip: true,
  ibbsServer: '192.168.1.115:8619', ibbsName: '', ibbsToken: '', ibbsRoom: 'general', ibbsNotify: true,
};
NM.settings = Object.assign({}, DEFAULTS, NM.store.get('settings', {}));
NM.set = (k, v) => { NM.settings[k] = v; NM.store.set('settings', NM.settings); NM.applyTheme(); NM.emit('settings', k); };

/* tiny event bus */
const handlers = {};
NM.on = (ev, fn) => (handlers[ev] = handlers[ev] || []).push(fn);
NM.emit = (ev, ...a) => (handlers[ev] || []).forEach(fn => { try { fn(...a); } catch (e) { console.error(e); } });

/* ---------------------------------------------------------------- build number */
// <general version><revision><YYYYMMDDHHMMSS>, exactly like Neptune: the time is when
// these files were last changed (the server's Last-Modified), so it moves with the code.
(function () {
  const d = new Date(document.lastModified);
  const p = n => String(n).padStart(2, '0');
  const stamp = isNaN(d) ? '00000000000000' : d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
  NM.BUILD = NM.VERSION.replace('.', '') + stamp;
  NM.FULLNAME = 'COS ' + NM.VERSION + ' "' + NM.CODENAME + '"';
})();

/* ---------------------------------------------------------------- theme */
NM.glassRGB = () => (NM.GLASS.find(g => g[0] === NM.settings.glass) || NM.GLASS[0])[1];
NM.applyTheme = () => {
  const s = NM.settings, r = document.documentElement.style;
  r.setProperty('--g', NM.glassRGB().join(','));
  r.setProperty('--gi', s.intensity);
  r.setProperty('--ga', s.transparency ? 0.25 + s.intensity * 0.6 : 0.95);
  document.body.classList.toggle('basic', !s.aero);
  document.body.classList.toggle('noanim', !s.animations);
  const meta = $('meta[name=theme-color]'); if (meta) meta.content = 'rgb(' + NM.glassRGB().join(',') + ')';
};

/* ---------------------------------------------------------------- sounds */
const sounds = {};
NM.sound = name => {
  if (!NM.settings.sounds) return;
  try {
    const a = sounds[name] || (sounds[name] = new Audio('sounds/' + name + '.mp3'));
    a.volume = NM.settings.volume / 100; a.currentTime = 0; a.play().catch(() => {});
  } catch (e) { /* no audio here */ }
};
NM.buzz = ms => { try { navigator.vibrate && navigator.vibrate(ms || 12); } catch (e) {} };

/* ---------------------------------------------------------------- wallpapers */
// Ported from nep/wallpaper.py: the procedural aurora ribbons and the COS 3.1 bubbles.
NM.WALLPAPERS = {
  'Neptune Aurora': { kind: 'aurora', top: [12, 120, 125], bot: [8, 30, 90], rib: [150, 255, 220], k: 1.0, seed: 3 },
  'Deep Trench': { kind: 'aurora', top: [10, 60, 100], bot: [2, 8, 30], rib: [110, 190, 255], k: 0.8, seed: 11 },
  'Emerald Drift': { kind: 'aurora', top: [20, 110, 70], bot: [5, 35, 40], rib: [190, 255, 170], k: 0.9, seed: 5 },
  'Nebula': { kind: 'aurora', top: [70, 30, 110], bot: [10, 10, 45], rib: [255, 160, 240], k: 0.9, seed: 21 },
  'COS Bubble': { kind: 'bubble', bg: [[0, 174, 255], [0, 64, 255], [0, 50, 255]], ball: [[44, 100, 128], [12, 32, 132]], rim: [205, 205, 190], layout: 'single' },
  'COS Multi-Bubble': { kind: 'bubble', bg: [[255, 255, 255], [255, 255, 255], [255, 255, 255]], ball: [[44, 100, 128], [12, 32, 132]], rim: [225, 205, 150], layout: 'multi', swoosh: [[[0, 156, 255], [0, 112, 244]], [[0, 122, 255], [0, 86, 250]]] },
  'Bubble Field': { kind: 'bubble', bg: [[4, 60, 140], [2, 20, 80], [1, 8, 40]], ball: [[44, 100, 128], [12, 32, 132]], rim: [160, 220, 255], layout: 'field' },
  'Bubble Sunset': { kind: 'bubble', bg: [[255, 170, 60], [240, 70, 110], [90, 20, 110]], ball: [[150, 60, 140], [60, 20, 110]], rim: [255, 225, 170], layout: 'single' },
  'Bubble Night': { kind: 'bubble', bg: [[10, 20, 50], [4, 8, 26], [0, 2, 10]], ball: [[20, 60, 90], [6, 14, 50]], rim: [90, 230, 255], layout: 'night' },
  'Bubble Mint': { kind: 'bubble', bg: [[240, 255, 250], [240, 255, 250], [240, 255, 250]], ball: [[30, 110, 100], [8, 60, 70]], rim: [200, 240, 220], layout: 'multi', swoosh: [[[40, 200, 170], [20, 160, 140]], [[30, 170, 150], [10, 120, 110]]] },
  'Solid Midnight': { kind: 'solid', c: [18, 42, 78] },
};
function rng(seed) { let s = seed >>> 0 || 1; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }
const rgb = (c, a) => a == null ? `rgb(${c.join(',')})` : `rgba(${c.join(',')},${a})`;

function drawAurora(ctx, w, h, P) {
  // per-pixel like the Python version, at a reduced size, then scaled up smoothly
  const sc = Math.min(1, 360 / w), W = Math.max(60, Math.round(w * sc)), H = Math.max(100, Math.round(h * sc));
  const img = ctx.createImageData(W, H), d = img.data, R = rng(P.seed);
  const ribs = [];
  for (let i = 0; i < 7; i++) ribs.push({ g: 0.22 + 0.5 * R(), ph: R() * 6.28, amp: 0.08 + R() * 0.14, fr: 0.4 + R() * 0.5, th: 0.012 + R() * 0.048, tilt: 0.2 + R() * 0.55 });
  // portrait: ribbons run diagonally across a tall screen, so lay them out on a rotated frame
  for (let y = 0; y < H; y++) {
    const t = y / H;
    for (let x = 0; x < W; x++) {
      const u = x / W, v = y / H;
      const dist = Math.min(1.2, Math.hypot(u - 0.62, (v - 0.18) * 0.6));
      const vig = 1.15 - 0.55 * dist;
      let L = 0;
      if (P.k > 0) {
        const xx = (u * 0.45 + v * 0.55), yy = (v * 0.8 - u * 0.25 + 0.2);   // diagonal frame
        for (const r of ribs) {
          const path = 0.55 - r.tilt * xx + r.amp * Math.sin(r.fr * xx * 6.283 + r.ph) + 0.03 * Math.sin(3.3 * xx * 6.283 + r.ph * 1.7);
          const dd = (yy - path) / r.th;
          if (dd > -4 && dd < 4) {
            const fib = 0.5 + 0.5 * Math.sin(dd * 9 + xx * 4 + r.ph);
            L += r.g * Math.exp(-dd * dd) * (0.55 + 0.45 * Math.pow(fib, 6));
          }
        }
        const arc = (yy - (0.95 - Math.pow(xx, 0.8) * 0.85)) / 0.09;
        L += 0.55 * Math.exp(-arc * arc);
        L *= P.k;
      }
      const i = (y * W + x) * 4;
      for (let c = 0; c < 3; c++) d[i + c] = Math.min(255, (P.top[c] * (1 - t) + P.bot[c] * t) * vig + L * P.rib[c] * 0.55);
      d[i + 3] = 255;
    }
  }
  const off = document.createElement('canvas'); off.width = W; off.height = H;
  off.getContext('2d').putImageData(img, 0, 0);
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(off, 0, 0, w, h);
  if (P.k > 0) {   // bokeh along the ribbons
    const n = Math.round(w * h / 9000);
    for (let i = 0; i < n; i++) {
      const px = R() * w, py = h * (0.55 - 0.4 * (px / w)) + (R() + R() + R() - 1.5) * h * 0.18, r = 0.6 + R() * 2.2;
      const g = ctx.createRadialGradient(px, py, 0, px, py, r * 5);
      g.addColorStop(0, rgb([255, 255, 255], 0.55 + R() * 0.4)); g.addColorStop(0.25, rgb(P.rib, 0.35)); g.addColorStop(1, rgb(P.rib, 0));
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(px, py, r * 5, 0, 7); ctx.fill();
    }
  }
}

NM.drawBubbleLogo = function (ctx, cx, cy, r, ball, rim, text) {
  ctx.save();
  ctx.shadowColor = 'rgba(0,0,20,.6)'; ctx.shadowBlur = r * 0.18; ctx.shadowOffsetX = -r * 0.05; ctx.shadowOffsetY = r * 0.07;
  const g = ctx.createLinearGradient(cx + r * 0.6, cy - r, cx - r * 0.5, cy + r);
  g.addColorStop(0, rgb(ball[0])); g.addColorStop(1, rgb(ball[1]));
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7); ctx.fill();
  ctx.restore();
  const hl = ctx.createRadialGradient(cx + r * 0.45, cy - r * 0.5, 0, cx + r * 0.45, cy - r * 0.5, r * 0.9);
  hl.addColorStop(0, 'rgba(255,255,255,.12)'); hl.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = hl; ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7); ctx.fill();
  const w1 = Math.max(1, r * 0.018);
  ctx.lineWidth = w1; ctx.strokeStyle = rgb(rim, 0.9); ctx.beginPath(); ctx.arc(cx, cy, r - w1 / 2, 0, 7); ctx.stroke();
  ctx.strokeStyle = 'rgba(70,150,255,.6)'; ctx.beginPath(); ctx.arc(cx, cy, r - w1 * 1.5, 0, 7); ctx.stroke();
  if (text !== '') {
    ctx.save(); ctx.translate(cx, cy - r * 0.02); ctx.scale(0.84, 1.3);
    ctx.fillStyle = '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.font = `bold ${Math.round(r * 0.64)}px "DejaVu Sans Condensed", "Arial Narrow", system-ui, sans-serif`;
    ctx.fillText(text || 'COS', 0, 0); ctx.restore();
  }
};

function circle3(p1, p2, p3) {
  const [x1, y1] = p1, [x2, y2] = p2, [x3, y3] = p3;
  const d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2));
  const ux = ((x1 * x1 + y1 * y1) * (y2 - y3) + (x2 * x2 + y2 * y2) * (y3 - y1) + (x3 * x3 + y3 * y3) * (y1 - y2)) / d;
  const uy = ((x1 * x1 + y1 * y1) * (x3 - x2) + (x2 * x2 + y2 * y2) * (x1 - x3) + (x3 * x3 + y3 * y3) * (x2 - x1)) / d;
  return [ux, uy, Math.hypot(x1 - ux, y1 - uy)];
}

function drawBubble(ctx, w, h, P) {
  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, rgb(P.bg[0])); g.addColorStop(0.55, rgb(P.bg[1])); g.addColorStop(1, rgb(P.bg[2]));
  ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);
  const cx = w / 2, cy = h * 0.64, r = Math.min(w * 0.3, h * 0.2);   // below the first rows of icons
  if (P.layout === 'single') {
    const v = ctx.createRadialGradient(cx, h / 2, Math.min(w, h) * 0.3, cx, h / 2, Math.max(w, h) * 0.75);
    v.addColorStop(0, 'rgba(0,0,30,0)'); v.addColorStop(1, 'rgba(0,0,30,.4)');
    ctx.fillStyle = v; ctx.fillRect(0, 0, w, h);
  } else if (P.layout === 'multi') {
    // the two swooshes, laid out on a tall design canvas (400x720) for a phone
    const sx = w / 400, sy = h / 720;
    const defs = [[[0, 470], [220, 360], [400, 120]], [[0, 720], [230, 520], [400, 440]]];
    defs.forEach((pts, i) => {
      const [ux, uy, rr] = circle3(...pts);
      ctx.save(); ctx.scale(sx, sy);
      ctx.shadowColor = 'rgba(0,10,40,.45)'; ctx.shadowBlur = 14 * sx; ctx.shadowOffsetX = 4 * sx; ctx.shadowOffsetY = 7 * sy;
      const fg = ctx.createLinearGradient(0, 0, 0, 720);
      fg.addColorStop(0, rgb(P.swoosh[i][0])); fg.addColorStop(1, rgb(P.swoosh[i][1]));
      ctx.fillStyle = fg;
      ctx.beginPath(); ctx.arc(ux, uy, rr, 0, 7); ctx.fill();
      ctx.shadowColor = 'transparent'; ctx.lineWidth = 6; ctx.strokeStyle = 'rgba(160,220,255,.35)';
      ctx.beginPath(); ctx.arc(ux, uy, rr - 3, 0, 7); ctx.stroke();
      ctx.restore();
    });
  } else if (P.layout === 'field') {
    const R = rng(31), n = Math.round(30 + w * h / 9000);
    for (let i = 0; i < n; i++) {
      const br = (8 + R() * 50) * (0.4 + 0.6 * R()) * (h / 720), bx = R() * w, by = R() * h, a = 0.12 + R() * 0.3;
      ctx.fillStyle = `rgba(120,200,255,${a / 3})`; ctx.strokeStyle = `rgba(190,235,255,${a + 0.2})`; ctx.lineWidth = Math.max(1, br / 18);
      ctx.beginPath(); ctx.arc(bx, by, br, 0, 7); ctx.fill(); ctx.stroke();
      ctx.fillStyle = `rgba(255,255,255,${a})`; ctx.beginPath(); ctx.ellipse(bx + br * 0.25, by - br * 0.35, br * 0.25, br * 0.17, -0.5, 0, 7); ctx.fill();
    }
  } else if (P.layout === 'night') {
    const R = rng(7), n = Math.round(w * h / 1400);
    for (let i = 0; i < n; i++) { ctx.fillStyle = `rgba(200,230,255,${0.3 + R() * 0.7})`; ctx.fillRect(R() * w, R() * h, R() < 0.1 ? 2 : 1, R() < 0.1 ? 2 : 1); }
    const gl = ctx.createRadialGradient(cx, cy, r * 0.5, cx, cy, r * 2.2);
    gl.addColorStop(0, rgb(P.rim, 0.45)); gl.addColorStop(1, rgb(P.rim, 0));
    ctx.fillStyle = gl; ctx.fillRect(0, 0, w, h);
  }
  NM.drawBubbleLogo(ctx, cx, cy, r, P.ball, P.rim);
}

NM.paintWallpaper = function (canvas, name, w, h) {
  const P = NM.WALLPAPERS[name] || NM.WALLPAPERS['Neptune Aurora'];
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext('2d');
  if (P.kind === 'aurora') drawAurora(ctx, w, h, P);
  else if (P.kind === 'bubble') drawBubble(ctx, w, h, P);
  else { ctx.fillStyle = rgb(P.c); ctx.fillRect(0, 0, w, h); }
  // a custom picture overrides it
  return canvas;
};
NM.wallThumb = name => { const c = document.createElement('canvas'); NM.paintWallpaper(c, name, 90, 160); return c.toDataURL(); };

function renderWall() {
  const c = $('#wall'), ph = $('#phone'), dpr = Math.min(2, window.devicePixelRatio || 1);
  const w = Math.round(ph.clientWidth * dpr), h = Math.round(ph.clientHeight * dpr);
  const custom = NM.settings.wallpaper === 'Custom' && NM.store.get('wallCustom', null);
  if (custom) {
    const img = new Image();
    img.onload = () => { c.width = w; c.height = h; const ctx = c.getContext('2d'); const s = Math.max(w / img.width, h / img.height);
      ctx.drawImage(img, (w - img.width * s) / 2, (h - img.height * s) / 2, img.width * s, img.height * s); };
    img.src = custom; return;
  }
  NM.paintWallpaper(c, NM.settings.wallpaper, w, h);
}
NM.on('settings', k => { if (k === 'wallpaper') renderWall(); });
let wallTimer; window.addEventListener('resize', () => { clearTimeout(wallTimer); wallTimer = setTimeout(renderWall, 250); });

/* ---------------------------------------------------------------- icons (SVG paths, 24x24) */
NM.ICONS = {
  files: '<path d="M3 7h6l2 2h10v10H3z"/><path d="M3 7V5h6l2 2"/>',
  note: '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4M9 12h7M9 16h7"/>',
  calc: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 12h2M12 12h2M16 12v5M8 16h2M12 16h2"/>',
  clock: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/>',
  web: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c3 3 3 14 0 17M12 3.5c-3 3-3 14 0 17"/>',
  chat: '<path d="M4 5h16v11H9l-5 4z"/><path d="M8 9h8M8 12h5"/>',
  paint: '<path d="M14 4l6 6-9 9H5v-6z"/><path d="M12 6l6 6"/>',
  photo: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M4 18l5-5 4 4 3-3 4 4"/>',
  music: '<path d="M9 18V6l10-2v12"/><circle cx="7" cy="18" r="2.5"/><circle cx="17" cy="16" r="2.5"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>',
  term: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M12 15h5"/>',
  bubbles: '<circle cx="9" cy="14" r="5"/><circle cx="16.5" cy="7.5" r="3"/><circle cx="18" cy="16" r="2"/><path d="M7 12a2 2 0 0 1 2-2"/>',
  task: '<path d="M3 20h18M6 16v-4M10 16V8M14 16v-6M18 16V5"/>',
  help: '<circle cx="12" cy="12" r="8.5"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 1-1 1.7M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="8.5"/><path d="M12 11v6M12 7.5h.01"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  search: '<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',
  folder: '<path d="M3 6h6l2 2h10v11H3z"/>',
  file: '<path d="M6 3h9l4 4v14H6z"/><path d="M15 3v4h4"/>',
  power: '<path d="M12 3v9M6.3 7a8 8 0 1 0 11.4 0"/>',
  pigeon: '<path d="M4 15c3 0 5-2 6-5 1-3 4-4 6-3l3 1-3 1c0 6-4 10-10 10l-2-4z"/><circle cx="15.5" cy="8.5" r=".5"/>',
};
NM.svg = name => `<svg viewBox="0 0 24 24">${NM.ICONS[name] || NM.ICONS.file}</svg>`;
NM.bubble = (app, size) => {
  const b = el('div', { class: 'bub', html: NM.svg(app.icon) });
  if (app.c1) b.style.setProperty('--c1', app.c1);
  if (app.c2) b.style.setProperty('--c2', app.c2);
  if (size) { b.style.width = b.style.height = size + 'px'; }
  return b;
};

/* ---------------------------------------------------------------- toast, notifications */
let toastT;
NM.toast = (msg, ms) => {
  const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden', 'out');
  clearTimeout(toastT); toastT = setTimeout(() => { t.classList.add('out'); setTimeout(() => t.classList.add('hidden'), 300); }, ms || 2600);
};
NM.notes = [];
NM.notify = (title, body, opts) => {
  opts = opts || {};
  const n = { title, body, app: opts.app, time: Date.now(), args: opts.args };
  NM.notes.unshift(n); NM.notes = NM.notes.slice(0, 30);
  renderNotes();
  if (!opts.quiet) { NM.sound('notify'); NM.toast(title + (body ? ': ' + body : '')); NM.buzz(30); }
  if (document.hidden && opts.system && window.NeptuneNative && window.NeptuneNative.notify) {   // the Android app
    try { window.NeptuneNative.notify(String(title), String(body || '')); } catch (e) {}
  } else if (document.hidden && opts.system && 'Notification' in window && Notification.permission === 'granted') {
    try { new Notification(title, { body, icon: 'icons/icon-192.png' }); } catch (e) {}
  }
};
function noteEl(n) {
  const app = NM.apps[n.app];
  const t = new Date(n.time);
  const e = el('div', { class: 'note', onclick: () => { if (app) { closeShade(); NM.open(n.app, n.args); } } },
    app ? NM.bubble(app, 30) : null,
    el('div', { style: { flex: 1, minWidth: 0 } }, el('b', {}, n.title), n.body || ''),
    el('small', {}, NM.fmtTime(t)));
  return e;
}
function renderNotes() {
  const box = $('#notes'); box.innerHTML = '';
  if (!NM.notes.length) box.append(el('div', { style: { opacity: .7, fontSize: '14px', padding: '6px 4px' } }, 'No notifications'));
  NM.notes.forEach(n => box.append(noteEl(n)));
  const lk = $('#lock-notes'); lk.innerHTML = ''; NM.notes.slice(0, 3).forEach(n => lk.append(noteEl(n)));
}

/* ---------------------------------------------------------------- time */
NM.fmtTime = (d, secs) => {
  d = d || new Date();
  let h = d.getHours(); const m = String(d.getMinutes()).padStart(2, '0'), s = String(d.getSeconds()).padStart(2, '0');
  if (NM.settings.clock24) return String(h).padStart(2, '0') + ':' + m + (secs ? ':' + s : '');
  const ap = h >= 12 ? 'PM' : 'AM'; h = h % 12 || 12;
  return h + ':' + m + (secs ? ':' + s : '') + ' ' + ap;
};
NM.fmtDate = d => (d || new Date()).toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
function tick() {
  const now = new Date();
  $('#sb-time').textContent = NM.fmtTime(now);
  $('#lock-time').textContent = NM.fmtTime(now).replace(/ [AP]M/, '');
  $('#lock-date').textContent = NM.fmtDate(now);
  $('#shade-clock').textContent = NM.fmtTime(now);
  $('#shade-date').textContent = NM.fmtDate(now);
  $('#sb-net').textContent = navigator.onLine ? '▲' : '✕';
  NM.emit('tick', now);
}

/* battery (where the browser tells us) */
async function battery() {
  const box = $('#sb-batt');
  if (!navigator.getBattery) { box.textContent = ''; return; }
  try {
    const b = await navigator.getBattery();
    const draw = () => { const p = Math.round(b.level * 100);
      box.innerHTML = `<span class="batt">${b.charging ? '⚡' : ''}${p}%<i><b style="width:${p}%;background:${p <= 15 && !b.charging ? '#ff5a4a' : '#fff'}"></b></i></span>`; };
    draw(); b.addEventListener('levelchange', draw); b.addEventListener('chargingchange', draw);
  } catch (e) {}
}

/* ---------------------------------------------------------------- dialogs */
NM.dialog = function ({ title, html, body, buttons, input, value }) {
  return new Promise(resolve => {
    const m = el('div', { class: 'modal' });
    const inp = input ? el('input', { class: 'field', value: value || '', type: input === true ? 'text' : input }) : null;
    const done = v => { m.remove(); resolve(v); };
    const b = el('div', { class: 'dlg-b' });
    if (html) b.innerHTML = html; else if (body) b.append(el('div', {}, body));
    if (inp) { b.append(inp); inp.addEventListener('keydown', e => { if (e.key === 'Enter') done(inp.value); }); }
    const acts = el('div', { class: 'acts' });
    (buttons || [['OK', true]]).forEach(([label, v]) => acts.append(el('button', { onclick: () => done(v === true && inp ? inp.value : v) }, '[' + label + ']')));
    b.append(acts);
    m.append(el('div', { class: 'dlg' }, el('div', { class: 'dlg-t' }, (title || 'COS').toUpperCase(), el('button', { class: 'tb-x', onclick: () => done(null) }, 'X')), b));
    $('#phone').append(m);
    if (inp) setTimeout(() => inp.focus(), 50);
  });
};
NM.alert = (title, body) => NM.dialog({ title, body, buttons: [['Close Dialog', true]] });
NM.confirm = (title, body, yes) => NM.dialog({ title, body, buttons: [[yes || 'OK', true], ['Cancel', false]] });
NM.prompt = (title, body, value) => NM.dialog({ title, body, input: true, value, buttons: [['OK', true], ['Cancel', null]] });

/* the COS runtime error dialog from the Neptune concept art */
NM.crash = function (appId, err) {
  const app = NM.apps[appId] || { name: 'COS' };
  const msg = String((err && err.message) || err || 'Stopped By External Program');
  let code = 0; for (const ch of msg) code = (code * 31 + ch.charCodeAt(0)) & 0xfff;
  console.error(err);
  NM.sound('warn');
  const html = `<div>${NM.esc(app.name.toUpperCase())} RUNTIME ERROR</div><div class="rule">${'-'.repeat(60)}</div>
    <div>EXCEPTION 0x${code.toString(16).padStart(3, '0')}</div><div>${NM.esc(msg.length > 80 ? msg.slice(0, 77) + '...' : msg)}</div>`;
  NM.dialog({ title: app.name, html, buttons: [['Start ' + app.name + ' Again', 'again'], ['Close Dialog', 'close']] }).then(v => {
    if (v === 'again') { NM.close(appId, true); setTimeout(() => NM.open(appId), 350); }
  });
};

/* ---------------------------------------------------------------- the on-device file system */
// A flat map of paths in localStorage, so Notepad, Paint, Files and the Terminal share files.
NM.fs = {
  _d: null,
  _load() { if (!this._d) { this._d = NM.store.get('fs', null); if (!this._d) { this._d = {}; this._seed(); } } return this._d; },
  _save() { return NM.store.set('fs', this._d); },
  _seed() {
    ['/Documents', '/Pictures', '/Downloads', '/Music'].forEach(p => this._d[p] = { dir: true, t: Date.now() });
    this._d['/Documents/Welcome.txt'] = { data: 'Welcome to COS Neptune Mobile!\n\nThis is the Neptune you can take with you: Aero glass, bubbles and all.\n\n- Swipe down from the top for the notification shade.\n- The orb at the bottom goes home; hold it for power options.\n- The square shows your recent apps; swipe a card up to close it.\n- IBBS talks to the TCF board. Set the server in Settings > IBBS.\n\nHave fun!', t: Date.now() };
  },
  norm(p) { const out = []; for (const s of String(p).split('/')) { if (!s || s === '.') continue; if (s === '..') out.pop(); else out.push(s); } return '/' + out.join('/'); },
  join(a, b) { return b.startsWith('/') ? this.norm(b) : this.norm(a + '/' + b); },
  stat(p) { p = this.norm(p); return p === '/' ? { dir: true } : this._load()[p] || null; },
  list(p) {
    p = this.norm(p); const pre = p === '/' ? '/' : p + '/', d = this._load(), out = [];
    for (const k in d) if (k.startsWith(pre) && k.length > pre.length && !k.slice(pre.length).includes('/')) out.push(Object.assign({ name: k.slice(pre.length), path: k }, d[k]));
    return out.sort((a, b) => (b.dir ? 1 : 0) - (a.dir ? 1 : 0) || a.name.localeCompare(b.name));
  },
  read(p) { const s = this.stat(p); return s && !s.dir ? s.data : null; },
  write(p, data) { p = this.norm(p); this.mkdir(p.slice(0, p.lastIndexOf('/')) || '/'); this._load()[p] = { data, t: Date.now() }; NM.emit('fs', p); return this._save(); },
  mkdir(p) { p = this.norm(p); if (p === '/') return; const d = this._load(); if (!d[p]) { this.mkdir(p.slice(0, p.lastIndexOf('/')) || '/'); d[p] = { dir: true, t: Date.now() }; this._save(); } },
  remove(p) { p = this.norm(p); const d = this._load(); for (const k of Object.keys(d)) if (k === p || k.startsWith(p + '/')) delete d[k]; this._save(); NM.emit('fs', p); },
  rename(a, b) { a = this.norm(a); b = this.norm(b); const d = this._load(); for (const k of Object.keys(d)) if (k === a || k.startsWith(a + '/')) { d[b + k.slice(a.length)] = d[k]; delete d[k]; } this._save(); NM.emit('fs', b); },
  size(s) { return s && s.data ? s.data.length : 0; },
};
NM.human = n => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
NM.openFile = path => {
  const ext = path.split('.').pop().toLowerCase();
  if (['png', 'jpg', 'jpeg', 'gif', 'webp'].includes(ext)) NM.open('photos', { path });
  else NM.open('notepad', { path });
};

/* ---------------------------------------------------------------- app registry + windows */
NM.apps = {};
NM.order = [];
NM.register = app => { NM.apps[app.id] = app; if (!app.hidden) NM.order.push(app.id); };
NM.running = new Map();   // id -> {app, win, inst, opened}
NM.stack = [];            // most recent last
let current = null;

function makeWin(app, origin) {
  const w = el('div', { class: 'win opening' + (app.dark ? ' dark' : '') });
  if (origin) { w.style.setProperty('--ox', origin[0] + 'px'); w.style.setProperty('--oy', origin[1] + 'px'); }
  const back = el('button', { class: 'tb-l', 'aria-label': 'Back', onclick: () => NM.back() }, '‹');
  const title = el('div', { class: 't' }, app.name);
  const x = el('button', { class: 'tb-x', 'aria-label': 'Close', onclick: () => NM.close(app.id) }, 'X');
  const bar = el('div', { class: 'titlebar' }, back, title, x);
  const client = el('div', { class: 'client' });
  w.append(bar, client);
  return { el: w, client, bar, titleEl: title, setTitle: t => { title.textContent = t; if (current === app.id) $('#sb-title').textContent = t; } };
}

NM.open = function (id, args, origin) {
  const app = NM.apps[id];
  if (!app) return NM.toast('No such app: ' + id);
  closeShade(); hideRecents();
  let r = NM.running.get(id);
  if (r) {
    r.win.el.classList.remove('min');
    if (args && r.inst && r.inst.args) { try { r.inst.args(args); } catch (e) { NM.crash(id, e); } }
  } else {
    const win = makeWin(app, origin);
    $('#windows').append(win.el);
    r = { app, win, inst: null, opened: Date.now() };
    NM.running.set(id, r);
    try { r.inst = app.mount(win, args || {}) || {}; } catch (e) { setTimeout(() => NM.crash(id, e), 50); r.inst = {}; }
    requestAnimationFrame(() => requestAnimationFrame(() => win.el.classList.remove('opening')));
  }
  NM.stack = NM.stack.filter(x => x !== id); NM.stack.push(id);
  for (const [k, o] of NM.running) if (k !== id) o.win.el.classList.add('min');
  r.win.el.style.zIndex = 10;
  current = id;
  $('#home').classList.add('away');
  $('#sb-title').textContent = r.win.titleEl.textContent;
  try { r.inst.show && r.inst.show(); } catch (e) { NM.crash(id, e); }
  NM.emit('apps');
};
NM.close = function (id, quiet) {
  const r = NM.running.get(id); if (!r) return;
  try { r.inst.close && r.inst.close(); } catch (e) { console.error(e); }
  r.win.el.classList.add('closing');
  setTimeout(() => r.win.el.remove(), 320);
  NM.running.delete(id);
  NM.stack = NM.stack.filter(x => x !== id);
  if (current === id) goHome();
  NM.emit('apps');
};
function goHome() {
  for (const [, o] of NM.running) { o.win.el.classList.add('min'); try { o.inst.hide && o.inst.hide(); } catch (e) {} }
  current = null; $('#home').classList.remove('away'); $('#sb-title').textContent = 'COS';
}
NM.home = () => { closeShade(); hideRecents(); goHome(); };
NM.back = function () {
  if ($('.modal')) { $('.modal').remove(); return; }
  if (!$('#shade').classList.contains('hidden')) return closeShade();
  if (!$('#recents').classList.contains('hidden')) return hideRecents();
  if (current) {
    const r = NM.running.get(current);
    let handled = false;
    try { handled = r.inst.back && r.inst.back(); } catch (e) { NM.crash(current, e); }
    if (!handled) NM.close(current);
    return;
  }
  const pages = $('#pages'); if (pages.scrollLeft > 10) pages.scrollTo({ left: 0, behavior: 'smooth' });
};
NM.current = () => current;

/* ---------------------------------------------------------------- recents */
function showRecents() {
  closeShade();
  const strip = $('#recents-strip'); strip.innerHTML = '';
  const ids = NM.stack.slice().reverse();
  $('#recents-empty').classList.toggle('hidden', ids.length > 0);
  $('#recents-clear').classList.toggle('hidden', ids.length === 0);
  const ph = $('#windows');
  for (const id of ids) {
    const r = NM.running.get(id);
    const cv = el('div', { class: 'cv' });
    // a live, scaled clone of the window as its thumbnail
    const snap = r.win.el.cloneNode(true);
    snap.classList.remove('min', 'opening', 'closing'); snap.classList.add('snap');
    snap.style.cssText = `width:${ph.clientWidth}px;height:${ph.clientHeight}px;position:absolute;inset:auto;visibility:visible;opacity:1;transition:none;`;
    NM.$$('canvas', r.win.el).forEach((c, i) => { const d = NM.$$('canvas', snap)[i]; try { d.getContext('2d').drawImage(c, 0, 0); } catch (e) {} });
    cv.append(snap);
    const card = el('div', { class: 'card' }, el('div', { class: 'ct' }, NM.bubble(r.app), r.win.titleEl.textContent), cv);
    strip.append(card);
    requestAnimationFrame(() => { const s = cv.clientWidth / ph.clientWidth; snap.style.transform = `scale(${s})`; });
    let y0 = null, dy = 0;
    card.addEventListener('pointerdown', e => { y0 = e.clientY; dy = 0; });
    card.addEventListener('pointermove', e => { if (y0 == null) return; dy = Math.min(0, e.clientY - y0); if (dy < -8) { card.style.transform = `translateY(${dy}px)`; card.style.opacity = 1 + dy / 300; } });
    const end = () => { if (y0 == null) return; y0 = null;
      if (dy < -110) { card.style.transform = 'translateY(-120%)'; card.style.opacity = 0; setTimeout(() => { NM.close(id); card.remove(); if (!NM.running.size) hideRecents(); }, 220); }
      else if (Math.abs(dy) < 8) NM.open(id);
      else { card.style.transform = ''; card.style.opacity = ''; } };
    card.addEventListener('pointerup', end); card.addEventListener('pointercancel', () => { y0 = null; card.style.transform = ''; card.style.opacity = ''; });
  }
  $('#recents').classList.remove('hidden');
  $('#home').classList.add('away');
  for (const [, o] of NM.running) o.win.el.classList.add('min');
}
function hideRecents() {
  if ($('#recents').classList.contains('hidden')) return;
  $('#recents').classList.add('hidden');
  if (current && NM.running.has(current)) NM.running.get(current).win.el.classList.remove('min'); else goHome();
}
NM.recents = () => $('#recents').classList.contains('hidden') ? showRecents() : hideRecents();

/* ---------------------------------------------------------------- notification shade */
const TOGGLES = [
  ['aero', '◈', 'Aero Glass'], ['sounds', '♪', 'Sounds'], ['clock24', '24', '24-hour'], ['transparency', '◐', 'Transparent'],
  ['animations', '✧', 'Motion'], ['bubbo', '●', 'Bubbo'], ['ibbsNotify', '✉', 'IBBS alerts'], ['__lock', '🔒', 'Lock now'],
];
function renderShade() {
  const t = $('#toggles'); t.innerHTML = '';
  for (const [k, ic, label] of TOGGLES) {
    const on = k !== '__lock' && NM.settings[k];
    t.append(el('button', { class: 'tog' + (on ? ' on' : ''), onclick: () => {
      if (k === '__lock') { closeShade(); NM.lock(); return; }
      NM.set(k, !NM.settings[k]); renderShade();
    } }, el('i', {}, ic), label));
  }
  const s = $('#swatches'); s.innerHTML = '';
  for (const [name, c] of NM.GLASS) s.append(el('button', { class: 'swatch' + (name === NM.settings.glass ? ' on' : ''), title: name, style: { background: rgb(c) },
    onclick: () => { NM.set('glass', name); renderShade(); } }));
}
function openShade() {
  renderShade(); renderNotes();
  const sh = $('#shade'); sh.classList.remove('hidden'); sh.classList.add('closed');
  requestAnimationFrame(() => requestAnimationFrame(() => sh.classList.remove('closed')));
}
function closeShade() {
  const sh = $('#shade'); if (sh.classList.contains('hidden')) return;
  sh.classList.add('closed'); setTimeout(() => sh.classList.add('hidden'), 280);
}
NM.shade = openShade;

/* ---------------------------------------------------------------- home screen */
const DOCK = ['files', 'ibbs', 'webinator', 'settings'];
let bubbo;
function renderHome() {
  const pages = $('#pages'); pages.innerHTML = '';
  const apps = NM.order.filter(id => !DOCK.includes(id));
  // page 1: widgets + first apps
  const p1 = el('div', { class: 'page' });
  const search = el('input', { placeholder: 'Search Neptune', enterkeyhint: 'search', type: 'search' });
  search.addEventListener('keydown', e => { if (e.key === 'Enter' && search.value.trim()) { const q = search.value.trim(); search.value = '';
    const hit = NM.order.find(id => NM.apps[id].name.toLowerCase().startsWith(q.toLowerCase()));
    if (hit) NM.open(hit); else NM.open('webinator', { search: q }); } });
  p1.append(el('label', { class: 'search glass' }, el('span', { html: NM.svg('search'), style: { width: '20px', display: 'flex' } }), search));
  const cc = el('canvas', { width: 220, height: 220 });
  const wc = el('div', { class: 'widget glass w-clock' }, cc, el('div', {}, el('div', { class: 'big wc-t' }), el('div', { class: 'sm wc-d' }), el('div', { class: 'sm wc-u' })));
  p1.append(wc);
  NM.on('tick', now => { drawClock(cc, now); NM.$('.wc-t', wc).textContent = NM.fmtTime(now).replace(/ [AP]M/, ''); NM.$('.wc-d', wc).textContent = NM.fmtDate(now);
    NM.$('.wc-u', wc).textContent = NM.settings.user ? 'Hi, ' + NM.settings.user : NM.FULLNAME; });
  const g1 = el('div', { class: 'grid' }); apps.slice(0, 8).forEach(id => g1.append(iconEl(id))); p1.append(g1);
  if (NM.settings.tip) p1.append(el('div', { class: 'widget glass w-tip' }, el('b', {}, 'TIP OF THE DAY'), NM.tip()));
  pages.append(p1);
  // page 2+: the rest, plus Bubbo
  const rest = apps.slice(8);
  for (let i = 0; i < Math.max(1, Math.ceil(rest.length / 20)); i++) {
    const p = el('div', { class: 'page' }), g = el('div', { class: 'grid' });
    rest.slice(i * 20, i * 20 + 20).forEach(id => g.append(iconEl(id)));
    p.append(g);
    if (i === 0 && NM.settings.bubbo) p.append(bubboWidget());
    pages.append(p);
  }
  const dock = $('#dock'); dock.innerHTML = ''; DOCK.forEach(id => NM.apps[id] && dock.append(iconEl(id)));
  const dots = $('#dots'); dots.innerHTML = '';
  for (let i = 0; i < pages.children.length; i++) dots.append(el('i', { class: i ? '' : 'on' }));
  pages.onscroll = () => { const i = Math.round(pages.scrollLeft / pages.clientWidth); $$('#dots i').forEach((d, j) => d.classList.toggle('on', i === j)); };
}
NM.renderHome = renderHome;
function iconEl(id) {
  const app = NM.apps[id];
  const b = NM.bubble(app);
  const e = el('button', { class: 'icon', 'data-app': id }, b, el('span', {}, app.name));
  e.addEventListener('click', () => { const r = b.getBoundingClientRect(), w = $('#windows').getBoundingClientRect();
    NM.buzz(8); NM.open(id, null, [r.left + r.width / 2 - w.left, r.top + r.height / 2 - w.top]); });
  return e;
}
NM.badge = (id, n) => $$(`.icon[data-app="${id}"] .bub`).forEach(b => { let x = NM.$('.badge', b);
  if (!n) { x && x.remove(); return; } if (!x) { x = el('span', { class: 'badge' }); b.append(x); } x.textContent = n > 99 ? '99+' : n; });

function drawClock(c, now) {
  const ctx = c.getContext('2d'), W = c.width, r = W / 2 - 8;
  ctx.clearRect(0, 0, W, W); ctx.save(); ctx.translate(W / 2, W / 2);
  const g = ctx.createRadialGradient(-r * 0.3, -r * 0.4, r * 0.1, 0, 0, r);
  g.addColorStop(0, 'rgba(255,255,255,.35)'); g.addColorStop(1, 'rgba(255,255,255,.05)');
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, r, 0, 7); ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,.8)'; ctx.lineWidth = 3; ctx.stroke();
  for (let i = 0; i < 12; i++) { ctx.save(); ctx.rotate(i * Math.PI / 6); ctx.fillStyle = '#fff'; ctx.fillRect(-2, -r + 8, 4, i % 3 ? 10 : 18); ctx.restore(); }
  const hand = (a, len, w, col) => { ctx.save(); ctx.rotate(a); ctx.strokeStyle = col; ctx.lineWidth = w; ctx.lineCap = 'round';
    ctx.shadowColor = 'rgba(0,0,0,.5)'; ctx.shadowBlur = 4; ctx.beginPath(); ctx.moveTo(0, len * 0.15); ctx.lineTo(0, -len); ctx.stroke(); ctx.restore(); };
  const s = now.getSeconds() + now.getMilliseconds() / 1000, m = now.getMinutes() + s / 60, h = now.getHours() % 12 + m / 60;
  hand(h * Math.PI / 6, r * 0.5, 7, '#fff'); hand(m * Math.PI / 30, r * 0.75, 5, '#fff'); hand(s * Math.PI / 30, r * 0.82, 2, '#ff5040');
  ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(0, 0, 6, 0, 7); ctx.fill(); ctx.restore();
}

/* Bubbo, Neptune's desktop pet, now in a widget */
const BUBBO_LINES = ["Hi! I'm Bubbo.", 'Have you tried turning the glass up?', 'I live in your pocket now.', "I'm 100% bubble.",
  'Bloop.', 'Swipe me? No. Tap me.', 'Pigeon delivers. Mostly.', 'Wheee!', 'Skill issue? Never heard of it.', 'Druman: drum + man.',
  'Every build has a number. This one is special.', 'Is it warm in here or is it the battery?'];
function bubboWidget() {
  const w = el('div', { class: 'widget glass w-bubbo' });
  const b = el('div', { class: 'bubbo', style: { left: '40%' } });
  const say = el('div', { class: 'bubbo-say', style: { opacity: 0 } });
  w.append(say, b);
  let last = Date.now(), sayT;
  const speak = t => { say.textContent = t; say.style.opacity = 1; clearTimeout(sayT); sayT = setTimeout(() => say.style.opacity = 0, 3500); };
  b.addEventListener('click', () => { last = Date.now(); b.classList.remove('sleep', 'hop'); void b.offsetWidth; b.classList.add('hop');
    speak(BUBBO_LINES[Math.floor(Math.random() * BUBBO_LINES.length)]); NM.buzz(15); });
  clearInterval(bubbo);
  bubbo = setInterval(() => {
    if (Date.now() - last > 45000) { b.classList.add('sleep'); return; }
    if (Math.random() < 0.5) b.style.left = (8 + Math.random() * 74) + '%';
  }, 3200);
  return w;
}

const TIPS = ['Swipe down from the top of the screen for quick settings and your glass colour.',
  'Hold the orb for Lock, Restart and Shut Down.', 'In Recents, swipe a card up to close that app.',
  'IBBS talks to the same board as ibbs.py, so you can chat with people and Claude agents from here.',
  'Settings > Background has every Neptune and bubble wallpaper, or use your own photo.',
  'The Terminal has a little COS shell: try help, ls, cat, ibbs and open.',
  'Add Neptune to your home screen and it runs full screen, like a real app.',
  'Why did the window go to therapy? It had too many panes.', 'Bubbo is not a screensaver. Bubbo is a lifestyle.',
  'Up, up, down, down... you know the rest.'];
NM.tip = () => TIPS[Math.floor((Date.now() - new Date(new Date().getFullYear(), 0, 0)) / 86400000) % TIPS.length];

/* ---------------------------------------------------------------- lock screen */
let locked = false, pin = '';
NM.lock = function () {
  locked = true; closeShade(); hideRecents();
  const L = $('#lock'); L.classList.remove('hidden', 'up');
  $('#lock-pin').classList.add('hidden'); $('#lock-hint').classList.remove('hidden');
  renderNotes();
};
function unlocked() { locked = false; const L = $('#lock'); L.classList.add('up'); setTimeout(() => L.classList.add('hidden'), 350); NM.emit('unlock'); }
function tryUnlock() {
  if (!NM.settings.pin) return unlocked();
  pin = ''; drawPin(); $('#lock-hint').classList.add('hidden'); $('#lock-pin').classList.remove('hidden');
}
function drawPin() { const d = $('#pin-dots'); d.innerHTML = ''; for (let i = 0; i < Math.max(4, NM.settings.pin.length); i++) d.append(el('i', { class: i < pin.length ? 'f' : '' })); }
function setupLock() {
  const pad = $('#pin-pad');
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', '⌫'].forEach(k => pad.append(k ? el('button', { onclick: () => {
    NM.buzz(8);
    if (k === '⌫') pin = pin.slice(0, -1); else pin += k;
    drawPin();
    if (pin.length >= NM.settings.pin.length) {
      if (pin === NM.settings.pin) unlocked();
      else { const d = $('#pin-dots'); d.classList.remove('bad'); void d.offsetWidth; d.classList.add('bad'); NM.buzz([40, 40, 40]); pin = ''; setTimeout(drawPin, 400); }
    }
  } }, k) : el('span')));
  $('#pin-cancel').onclick = () => { $('#lock-pin').classList.add('hidden'); $('#lock-hint').classList.remove('hidden'); };
  const L = $('#lock'); let y0 = null;
  L.addEventListener('pointerdown', e => { if (!$('#lock-pin').classList.contains('hidden')) return; y0 = e.clientY; L.style.transition = 'none'; });
  L.addEventListener('pointermove', e => { if (y0 == null) return; const dy = Math.min(0, e.clientY - y0); L.style.transform = `translateY(${dy}px)`; L.style.opacity = 1 + dy / 500; });
  const end = e => { if (y0 == null) return; const dy = e.clientY - y0; y0 = null; L.style.transition = ''; L.style.transform = ''; L.style.opacity = '';
    if (dy < -90 || (Math.abs(dy) < 6 && e.type === 'pointerup' && e.target.closest('#lock-hint'))) tryUnlock(); };
  L.addEventListener('pointerup', end); L.addEventListener('pointercancel', end);
  window.addEventListener('keydown', e => { if (locked && $('#lock-pin').classList.contains('hidden') && (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowUp')) tryUnlock(); });
}

/* ---------------------------------------------------------------- power */
function powerMenu() {
  NM.buzz(30);
  const P = $('#power'); P.innerHTML = ''; P.style.background = 'rgba(0,0,0,.7)'; P.classList.remove('hidden');
  const btn = (ic, label, fn) => el('button', { class: 'glass', onclick: fn }, el('span', { html: NM.svg(ic), style: { width: '26px', display: 'flex' } }), label);
  P.append(el('div', { class: 'pw' },
    btn('lock', 'Lock', () => { P.classList.add('hidden'); NM.lock(); }),
    btn('clock', 'Restart', () => { P.classList.add('hidden'); location.reload(); }),
    btn('power', 'Shut Down', () => shutdown()),
    el('button', { class: 'link', style: { alignSelf: 'center', marginTop: '10px' }, onclick: () => P.classList.add('hidden') }, 'Cancel')));
}
function shutdown() {
  NM.sound('shutdown');
  for (const id of [...NM.running.keys()]) NM.close(id);
  const P = $('#power'); P.innerHTML = ''; P.style.background = '#000';
  P.append(el('div', { class: 'boot-orb', style: { animation: 'none', opacity: .8 } }), el('div', { class: 'boot-name' }, 'SHUTTING DOWN...'));
  setTimeout(() => { P.innerHTML = ''; P.append(el('div', { style: { color: '#e8a040', fontSize: '18px', textAlign: 'center', padding: '20px' } }, 'It is now safe to turn off your phone.'),
    el('button', { class: 'link', onclick: () => location.reload() }, 'Start COS Neptune Mobile')); }, 2200);
}

/* ---------------------------------------------------------------- first-run setup */
function oobe() {
  return new Promise(resolve => {
    const box = el('div', { class: 'oobe' });
    const name = el('input', { class: 'field', placeholder: 'Your name', value: NM.settings.user });
    const sw = el('div', { class: 'big-swatches', style: { padding: 0 } });
    const paint = () => { sw.innerHTML = ''; NM.GLASS.forEach(([n, c]) => sw.append(el('button', { class: n === NM.settings.glass ? 'on' : '', style: { background: rgb(c) }, title: n, onclick: () => { NM.set('glass', n); paint(); } }))); };
    paint();
    const walls = el('div', { class: 'wall-grid', style: { padding: 0, gridTemplateColumns: 'repeat(4,1fr)' } });
    const pw = () => { walls.innerHTML = ''; ['Neptune Aurora', 'COS Bubble', 'COS Multi-Bubble', 'Bubble Night'].forEach(n => walls.append(el('button', { class: n === NM.settings.wallpaper ? 'on' : '', onclick: () => { NM.set('wallpaper', n); pw(); } }, el('img', { src: NM.wallThumb(n) })))); };
    pw();
    box.append(el('h1', {}, 'Set Up COS Neptune Mobile'), el('p', {}, 'A few choices and you\'re in. You can change all of this later in Settings.'),
      el('div', { class: 'card2 glass' }, el('div', {}, 'What should Neptune call you?'), name, el('div', {}, 'Glass colour'), sw, el('div', {}, 'Background'), walls),
      el('button', { class: 'glass pill', style: { alignSelf: 'flex-end', fontSize: '18px' }, onclick: () => {
        NM.settings.user = name.value.trim(); NM.set('oobe', true); box.remove(); resolve();
      } }, 'Next ›'));
    $('#phone').append(box);
  });
}

/* ---------------------------------------------------------------- gestures */
function setupGestures() {
  // pull the shade down from the status bar (or the top 30px of the home screen)
  let y0 = null;
  const start = e => { if (locked) return; if (e.clientY - $('#phone').getBoundingClientRect().top < 44) y0 = e.clientY; };
  document.addEventListener('pointerdown', start, true);
  document.addEventListener('pointermove', e => { if (y0 != null && e.clientY - y0 > 40) { y0 = null; openShade(); } }, true);
  document.addEventListener('pointerup', () => { y0 = null; }, true);
  $('#statusbar').addEventListener('click', () => openShade());
  // close the shade by tapping outside or swiping it up
  const sh = $('#shade'); let sy = null;
  sh.addEventListener('pointerdown', e => { sy = e.clientY; });
  sh.addEventListener('pointerup', e => { if (sy != null && (sy - e.clientY > 50 || e.target === sh)) closeShade(); sy = null; });
  // nav bar
  $('#nav-back').onclick = () => { NM.buzz(8); NM.back(); };
  $('#nav-recents').onclick = () => { NM.buzz(8); NM.recents(); };
  const home = $('#nav-home'); let ht;
  home.addEventListener('pointerdown', () => { ht = setTimeout(() => { ht = 'long'; powerMenu(); }, 650); });
  home.addEventListener('pointerup', () => { if (ht !== 'long') { clearTimeout(ht); NM.buzz(8); NM.home(); } ht = null; });
  home.addEventListener('pointerleave', () => { if (ht !== 'long') clearTimeout(ht); });
  home.addEventListener('contextmenu', e => e.preventDefault());
  $('#recents-clear').onclick = () => { for (const id of [...NM.running.keys()]) NM.close(id); hideRecents(); };
  $('#recents').addEventListener('click', e => { if (e.target.id === 'recents' || e.target.id === 'recents-strip') hideRecents(); });
  $('#notes-clear').onclick = () => { NM.notes = []; renderNotes(); };
  $('#bright').oninput = e => { $('#dim').style.opacity = (100 - e.target.value) / 100; };
  // hardware/keyboard
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') NM.back();
    if (e.key === 'Home' && e.altKey) NM.home();
    if (e.ctrlKey && e.shiftKey && e.key === 'Escape') NM.open('taskmgr');
  });
  // Android back button / browser back: keep one history entry and treat pops as Back
  history.pushState({ nm: 1 }, '');
  window.addEventListener('popstate', () => { history.pushState({ nm: 1 }, ''); NM.back(); });
  // Konami code
  const K = ['ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight', 'b', 'a']; let ki = 0;
  window.addEventListener('keydown', e => { ki = e.key === K[ki] ? ki + 1 : (e.key === K[0] ? 1 : 0); if (ki === K.length) { ki = 0; NM.party(); } });
}
NM.party = () => {
  NM.toast('🎉 You found the secret! Bubbo says hi.');
  const c = el('canvas', { style: { position: 'absolute', inset: 0, zIndex: 99, pointerEvents: 'none', width: '100%', height: '100%' } });
  $('#phone').append(c); const W = c.width = c.clientWidth, H = c.height = c.clientHeight, ctx = c.getContext('2d');
  const ps = Array.from({ length: 140 }, () => ({ x: Math.random() * W, y: -Math.random() * H, v: 2 + Math.random() * 4, r: 3 + Math.random() * 5, c: `hsl(${Math.random() * 360},90%,65%)`, s: Math.random() * 6 }));
  const t0 = performance.now();
  (function f(t) { ctx.clearRect(0, 0, W, H); ps.forEach(p => { p.y += p.v; p.x += Math.sin(t / 300 + p.s); ctx.fillStyle = p.c; ctx.fillRect(p.x, p.y, p.r, p.r * 1.6); });
    if (t - t0 < 4500) requestAnimationFrame(f); else c.remove(); })(t0);
};

/* ---------------------------------------------------------------- start */
NM.start = async function () {
  NM.applyTheme();
  $('#boot-build').textContent = NM.FULLNAME + '  Build ' + NM.BUILD;
  renderWall(); renderHome(); setupLock(); setupGestures(); battery(); renderNotes();
  tick(); setInterval(tick, 1000);
  window.addEventListener('error', e => { const cur = NM.current(); if (cur && e.error) NM.crash(cur, e.error); });
  window.addEventListener('unhandledrejection', e => { const cur = NM.current(); if (cur) NM.crash(cur, e.reason); });
  if (location.protocol.startsWith('http') && 'serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1'))
    navigator.serviceWorker.register('sw.js').catch(() => {});
  const boot = $('#boot');
  await new Promise(r => setTimeout(r, NM.settings.bootScreen ? 1900 : 50));
  boot.classList.add('out'); setTimeout(() => boot.classList.add('hidden'), 600);
  if (!NM.settings.oobe) await oobe();
  if (NM.settings.lockOnStart) NM.lock();
  // the first touch is the earliest a browser lets us play the boot sound
  const once = () => { NM.sound('boot'); document.removeEventListener('pointerdown', once, true); };
  document.addEventListener('pointerdown', once, true);
  NM.emit('started');
};
