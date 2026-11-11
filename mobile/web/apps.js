/* COS Neptune Mobile -- the apps. Each one: NM.register({id, name, icon, mount(win, args)}).
   mount() fills win.client and may return {back(), close(), show(), hide(), args(a)}. */
'use strict';
(function () {
const { $, $$, el, esc } = NM;
const tb = (label, fn, cls) => el('button', { class: 'tbtn' + (cls ? ' ' + cls : ''), onclick: fn }, label);
const toolbar = (...kids) => el('div', { class: 'toolbar' }, ...kids);
const fill = (win, ...kids) => { win.client.style.display = 'flex'; win.client.style.flexDirection = 'column'; win.client.append(...kids); };

/* ================================================================ Files */
NM.register({ id: 'files', name: 'Files', icon: 'files', c1: '#d9a634', c2: '#8a5a0c', mount(win, args) {
  let cwd = (args && args.path) || '/', picking = null;
  const list = el('div', { class: 'list', style: { flex: 1, overflow: 'auto' } });
  const path = el('div', { style: { flex: 1, fontSize: '14px', color: '#456', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } });
  const up = el('input', { type: 'file', multiple: true, class: 'hidden', onchange: async e => {
    for (const f of e.target.files) {
      const data = await new Promise(r => { const fr = new FileReader(); fr.onload = () => r(fr.result);
        if (f.type.startsWith('text') || /\.(txt|md|json|js|py|ceap|html|css|csv)$/i.test(f.name)) fr.readAsText(f); else fr.readAsDataURL(f); });
      if (NM.fs.write(NM.fs.join(cwd, f.name), data)) NM.toast('Added ' + f.name);
    }
    up.value = ''; draw();
  } });
  const draw = () => {
    path.textContent = cwd === '/' ? 'Phone' : 'Phone' + cwd.replace(/\//g, ' › ');
    win.setTitle(cwd === '/' ? 'Files' : cwd.split('/').pop());
    list.innerHTML = '';
    const items = NM.fs.list(cwd);
    if (!items.length) list.append(el('div', { class: 'empty' }, 'This folder is empty.'));
    for (const it of items) {
      const icon = it.dir ? '📁' : /\.(png|jpe?g|gif|webp)$/i.test(it.name) ? '🖼' : '📄';
      const row = el('div', { class: 'row' }, el('div', { class: 'ri' }, icon),
        el('div', { class: 'rt' }, it.name, el('small', {}, (it.dir ? 'Folder' : NM.human(NM.fs.size(it))) + ' · ' + new Date(it.t).toLocaleString())),
        el('button', { class: 'chev', style: { fontSize: '22px', padding: '0 6px' }, onclick: e => { e.stopPropagation(); menu(it); } }, '⋯'));
      row.addEventListener('click', () => { if (it.dir) { cwd = it.path; draw(); } else NM.openFile(it.path); });
      list.append(row);
    }
  };
  const menu = async it => {
    const v = await NM.dialog({ title: it.name, body: it.dir ? 'Folder' : NM.human(NM.fs.size(it)), buttons: [['Open', 'open'], ['Rename', 'ren'], ['Download to device', 'dl'], ['Delete', 'del'], ['Cancel', null]] });
    if (v === 'open') it.dir ? (cwd = it.path, draw()) : NM.openFile(it.path);
    if (v === 'ren') { const n = await NM.prompt('Rename', 'New name', it.name); if (n && n !== it.name) { NM.fs.rename(it.path, NM.fs.join(cwd, n.replace(/\//g, '-'))); draw(); } }
    if (v === 'del' && await NM.confirm('Delete', 'Delete ' + it.name + '?', 'Delete')) { NM.fs.remove(it.path); draw(); }
    if (v === 'dl' && !it.dir) { const d = it.data.startsWith('data:') ? it.data : 'data:text/plain;charset=utf-8,' + encodeURIComponent(it.data);
      if (window.NeptuneNative && window.NeptuneNative.save) NM.toast(window.NeptuneNative.save(it.name, d));   // Android app: into Downloads
      else el('a', { href: d, download: it.name }).click(); }
  };
  fill(win, toolbar(tb('↑', () => { if (cwd !== '/') { cwd = cwd.slice(0, cwd.lastIndexOf('/')) || '/'; draw(); } }), path,
    tb('+ Folder', async () => { const n = await NM.prompt('New Folder', 'Name', 'New Folder'); if (n) { NM.fs.mkdir(NM.fs.join(cwd, n)); draw(); } }),
    tb('+ Note', async () => { const n = await NM.prompt('New Note', 'Name', 'Note.txt'); if (n) { NM.fs.write(NM.fs.join(cwd, n), ''); NM.open('notepad', { path: NM.fs.join(cwd, n) }); } }),
    tb('Import', () => up.click())), list, up);
  draw();
  const onfs = () => NM.running.has('files') && draw(); NM.on('fs', onfs);
  return { back() { if (cwd === '/') return false; cwd = cwd.slice(0, cwd.lastIndexOf('/')) || '/'; draw(); return true; }, show: draw };
} });

/* ================================================================ Notepad */
NM.register({ id: 'notepad', name: 'Notepad', icon: 'note', c1: '#4b8fd6', c2: '#123f8a', mount(win, args) {
  let path = null, dirty = false;
  const ta = el('textarea', { class: 'note-ed', spellcheck: 'true', placeholder: 'Start typing...' });
  const name = () => path ? path.split('/').pop() : 'Untitled';
  const title = () => win.setTitle((dirty ? '• ' : '') + name());
  ta.addEventListener('input', () => { if (!dirty) { dirty = true; title(); } });
  const save = async () => {
    if (!path) { const n = await NM.prompt('Save As', 'Save in Documents as', 'Note.txt'); if (!n) return false; path = NM.fs.join('/Documents', n); }
    if (NM.fs.write(path, ta.value)) { dirty = false; title(); NM.toast('Saved ' + name()); return true; }
    return false;
  };
  const load = p => { path = p || null; ta.value = p ? (NM.fs.read(p) || '') : ''; dirty = false; title(); };
  const words = el('span', { style: { fontSize: '12px', color: '#678', marginLeft: 'auto', fontFamily: 'var(--sans)' } });
  ta.addEventListener('input', () => words.textContent = (ta.value.trim().match(/\S+/g) || []).length + ' words');
  fill(win, toolbar(tb('New', async () => { if (!dirty || await NM.confirm('Notepad', 'Discard changes?', 'Discard')) load(null); }),
    tb('Open', () => NM.open('files', { path: '/Documents' })), tb('Save', save, 'primary'),
    tb('A−', () => ta.style.fontSize = Math.max(11, parseFloat(getComputedStyle(ta).fontSize) - 2) + 'px'),
    tb('A+', () => ta.style.fontSize = Math.min(32, parseFloat(getComputedStyle(ta).fontSize) + 2) + 'px'), words), el('div', { style: { flex: 1 } }, ta));
  load(args.path);
  return {
    args(a) { if (a.path && a.path !== path) { if (dirty) save(); load(a.path); } },
    back() { if (dirty) { save(); } return false; },
    close() { if (dirty && path) NM.fs.write(path, ta.value); },
  };
} });

/* ================================================================ Calculator */
NM.register({ id: 'calc', name: 'Calculator', icon: 'calc', c1: '#5a6878', c2: '#1d2630', mount(win) {
  let cur = '0', acc = null, op = null, fresh = true, exprTxt = '';
  const ex = el('div', { class: 'expr' }), val = el('div', { class: 'val' });
  const show = () => { val.textContent = cur; ex.textContent = exprTxt; val.style.fontSize = cur.length > 9 ? Math.max(24, 52 - (cur.length - 9) * 3) + 'px' : ''; };
  const num = s => parseFloat(s);
  const fmt = n => { if (!isFinite(n)) return 'Error'; const s = String(+n.toPrecision(12)); return s.length > 16 ? n.toExponential(8) : s; };
  const apply = () => { if (op == null || acc == null) return num(cur);
    const a = acc, b = num(cur); return op === '+' ? a + b : op === '−' ? a - b : op === '×' ? a * b : b === 0 ? NaN : a / b; };
  const press = k => {
    NM.buzz(6);
    if (/[0-9]/.test(k)) { cur = fresh || cur === '0' ? k : cur.length < 16 ? cur + k : cur; fresh = false; }
    else if (k === '.') { if (fresh) { cur = '0.'; fresh = false; } else if (!cur.includes('.')) cur += '.'; }
    else if (k === 'C') { cur = '0'; acc = null; op = null; fresh = true; exprTxt = ''; }
    else if (k === '⌫') { cur = cur.length > 1 && !fresh ? cur.slice(0, -1) : '0'; }
    else if (k === '±') cur = fmt(-num(cur));
    else if (k === '%') cur = fmt(acc != null ? acc * num(cur) / 100 : num(cur) / 100);
    else if ('+−×÷'.includes(k)) { acc = fresh && op ? acc : apply(); op = k; fresh = true; exprTxt = fmt(acc) + ' ' + k; cur = fmt(acc); }
    else if (k === '=') { if (op) { exprTxt = fmt(acc) + ' ' + op + ' ' + cur + ' ='; cur = fmt(apply()); acc = null; op = null; fresh = true; } }
    show();
  };
  const keys = el('div', { class: 'keys' });
  [['C', 'fn'], ['±', 'fn'], ['%', 'fn'], ['÷', 'op'], ['7'], ['8'], ['9'], ['×', 'op'], ['4'], ['5'], ['6'], ['−', 'op'], ['1'], ['2'], ['3'], ['+', 'op'], ['⌫', 'fn'], ['0'], ['.'], ['=', 'op']]
    .forEach(([k, c]) => keys.append(el('button', { class: c || '', onclick: () => press(k) }, k)));
  win.client.append(el('div', { class: 'calc' }, el('div', { class: 'disp' }, ex, val), keys));
  const kd = e => { if (NM.current() !== 'calc') return; const m = { '*': '×', '/': '÷', '-': '−', Enter: '=', Backspace: '⌫', Escape: null, c: 'C' }; const k = e.key in m ? m[e.key] : e.key;
    if (k && /^[0-9.+×÷−=⌫C%]$/.test(k)) { e.preventDefault(); press(k); } };
  window.addEventListener('keydown', kd);
  show();
  return { close() { window.removeEventListener('keydown', kd); } };
} });

/* ================================================================ Clock */
NM.register({ id: 'clock', name: 'Clock', icon: 'clock', c1: '#3a4f6a', c2: '#0b1830', dark: true, mount(win) {
  let mode = 'clock', swStart = 0, swAcc = 0, swRun = false, laps = [], tEnd = 0, tLeft = 300000, tRun = false, raf;
  const big = el('div', { class: 'big' }), sub = el('div', { style: { opacity: .8 } }), laps$ = el('div', { class: 'laps' });
  const btns = el('div', { style: { display: 'flex', gap: '14px' } });
  const seg = el('div', { class: 'seg' });
  const pad = n => String(n).padStart(2, '0');
  const fmtMs = ms => { ms = Math.max(0, ms); const h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, s = Math.floor(ms / 1000) % 60, cs = Math.floor(ms / 10) % 100;
    return (h ? h + ':' : '') + pad(m) + ':' + pad(s) + (mode === 'stopwatch' ? '.' + pad(cs) : ''); };
  const B = (label, fn) => el('button', { class: 'glass pill', style: { minWidth: '96px', color: '#fff' }, onclick: fn }, label);
  const render = () => {
    seg.innerHTML = '';
    [['clock', 'Clock'], ['stopwatch', 'Stopwatch'], ['timer', 'Timer']].forEach(([k, l]) => seg.append(el('button', { class: k === mode ? 'on' : '', onclick: () => { mode = k; render(); } }, l)));
    btns.innerHTML = ''; laps$.innerHTML = '';
    if (mode === 'stopwatch') {
      btns.append(B(swRun ? 'Lap' : 'Reset', () => { if (swRun) { laps.unshift(swAcc + Date.now() - swStart); } else { swAcc = 0; laps = []; } render(); }),
        B(swRun ? 'Stop' : 'Start', () => { if (swRun) swAcc += Date.now() - swStart; else swStart = Date.now(); swRun = !swRun; render(); }));
      laps.forEach((l, i) => laps$.append(el('div', {}, el('span', {}, 'Lap ' + (laps.length - i)), el('span', {}, fmtMs(l)))));
    } else if (mode === 'timer') {
      if (!tRun) [['+1 min', 60000], ['+5 min', 300000], ['Clear', 0]].forEach(([l, v]) => btns.append(B(l, () => { tLeft = v ? tLeft + v : 0; render(); })));
      btns.append(B(tRun ? 'Pause' : 'Start', () => { if (tRun) { tLeft = tEnd - Date.now(); tRun = false; } else if (tLeft > 0) { tEnd = Date.now() + tLeft; tRun = true; } render(); }));
    }
  };
  const frame = () => {
    const now = new Date();
    if (mode === 'clock') { big.textContent = NM.fmtTime(now, true); sub.textContent = NM.fmtDate(now); }
    else if (mode === 'stopwatch') { big.textContent = fmtMs(swAcc + (swRun ? Date.now() - swStart : 0)); sub.textContent = ''; }
    else { const left = tRun ? tEnd - Date.now() : tLeft; big.textContent = fmtMs(left); sub.textContent = tRun ? 'Running' : 'Set a time';
      if (tRun && left <= 0) { tRun = false; tLeft = 0; render(); NM.notify('Timer', "Time's up!", { app: 'clock', system: true }); NM.buzz([300, 100, 300]); } }
    raf = requestAnimationFrame(frame);
  };
  win.client.append(el('div', { class: 'clockapp' }, seg, el('div', { class: 'face' }, big, sub, btns, laps$)));
  render(); frame();
  return { close() { cancelAnimationFrame(raf); } };
} });

/* ================================================================ Webinator */
NM.register({ id: 'webinator', name: 'Webinator', icon: 'web', c1: '#3aa0e0', c2: '#0a3a8a', mount(win, args) {
  const favs = NM.store.get('favs', [['Wikipedia', 'https://en.m.wikipedia.org/'], ['DuckDuckGo Lite', 'https://lite.duckduckgo.com/lite/'], ['Neptune Help', 'about:help']]);
  const url = el('input', { class: 'field', style: { padding: '7px 10px', fontSize: '15px' }, enterkeyhint: 'go', inputmode: 'url', placeholder: 'Search or type a web address' });
  const frame = el('iframe', { sandbox: 'allow-scripts allow-forms allow-same-origin allow-popups', referrerpolicy: 'no-referrer' });
  const start = el('div', { class: 'start client' });
  const body = el('div', { style: { flex: 1, display: 'flex', flexDirection: 'column', position: 'relative' } }, start, frame);
  const hist = [];
  const home = () => {
    frame.classList.add('hidden'); start.classList.remove('hidden'); url.value = ''; win.setTitle('Webinator');
    start.innerHTML = `<h2 style="font-family:var(--sc);font-weight:normal;letter-spacing:2px;margin:0 0 4px;color:rgb(var(--g))">WEBINATOR</h2>
      <p style="color:#678;margin:0 0 14px">The COS browser, now in your pocket.</p><div class="section" style="padding-left:0">FAVOURITES</div>`;
    const l = el('div', { class: 'list' });
    favs.forEach(([n, u], i) => { const r = el('div', { class: 'row' }, el('div', { class: 'ri' }, '★'), el('div', { class: 'rt' }, n, el('small', {}, u)),
      el('button', { class: 'chev', onclick: e => { e.stopPropagation(); favs.splice(i, 1); NM.store.set('favs', favs); home(); } }, '✕'));
      r.onclick = () => go(u); l.append(r); });
    start.append(l, el('p', { style: { color: '#678', fontSize: '13px', marginTop: '18px' } },
      'Many big sites refuse to be shown inside another page. If a page stays blank, use ', el('b', {}, 'Open outside'), '.'));
  };
  const go = (q, noHist) => {
    q = (q || '').trim(); if (!q) return home();
    if (q === 'about:help') { NM.open('help'); return; }
    let u = q;
    if (!/^[a-z]+:/i.test(q)) u = /^[^\s]+\.[a-z]{2,}(\/|$)/i.test(q) ? 'https://' + q : 'https://lite.duckduckgo.com/lite/?q=' + encodeURIComponent(q);
    if (!noHist) hist.push(u);
    url.value = u; start.classList.add('hidden'); frame.classList.remove('hidden'); frame.src = u;
    win.setTitle(u.replace(/^https?:\/\//, '').split('/')[0]);
  };
  url.addEventListener('keydown', e => { if (e.key === 'Enter') { url.blur(); go(url.value); } });
  fill(win, toolbar(tb('⌂', home), url, tb('★', () => { if (url.value) { favs.push([win.titleEl.textContent, url.value]); NM.store.set('favs', favs); NM.toast('Added to favourites'); } }),
    tb('Open outside', () => url.value && window.open(url.value, '_blank', 'noopener'))), body);
  home();
  if (args.search) go(args.search); else if (args.url) go(args.url);
  return {
    args(a) { if (a.search) go(a.search); if (a.url) go(a.url); },
    back() { if (hist.length > 1) { hist.pop(); go(hist[hist.length - 1], true); return true; } if (hist.length === 1) { hist.pop(); home(); return true; } return false; },
  };
} });

/* ================================================================ IBBS */
// Talks to a TCF_IBBS board (ibbs.py --serve). The board has no CORS, so when the page is
// served by server.py we go through its /api/ibbs relay; the relay forwards to the board
// named in X-IBBS-Server with the X-IBBS-Token header, using the board's own endpoints.
const IBBS = NM.ibbs = {
  client: NM.store.get('ibbsClient', null) || (() => { const c = Math.random().toString(16).slice(2, 14); NM.store.set('ibbsClient', c); return c; })(),
  name: () => NM.settings.ibbsName || ((NM.settings.user || 'mobile').toLowerCase().replace(/\s+/g, '') + '@neptune-mobile'),
  base: null,
  async detect() {
    if (this.base !== null) return this.base;
    try { const r = await fetch('api/ping', { cache: 'no-store' }); if (r.ok && (await r.json()).neptune) return (this.base = 'api/ibbs'); } catch (e) {}
    if (window.NeptuneNative && window.NeptuneNative.ibbs) return (this.base = 'native');
    return (this.base = '');
  },
  async call(method, path, body) {
    const base = await this.detect();
    if (base === 'native') {   // the Android app's bridge (see android/)
      const res = JSON.parse(window.NeptuneNative.ibbs(method, NM.settings.ibbsServer, NM.settings.ibbsToken || '', path, body ? JSON.stringify(body) : ''));
      if (res.error) throw new Error(res.error); return res.data;
    }
    const url = base ? base + path : 'http://' + NM.settings.ibbsServer.replace(/^https?:\/\//, '') + path;
    const h = { 'Content-Type': 'application/json', 'X-IBBS-Server': NM.settings.ibbsServer };
    if (NM.settings.ibbsToken) h['X-IBBS-Token'] = NM.settings.ibbsToken;
    const r = await fetch(url, { method, headers: base ? h : undefined, body: body ? JSON.stringify(body) : undefined, cache: 'no-store' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('board said ' + r.status));
    return j;
  },
  rooms() { return this.call('GET', '/rooms').then(j => j.rooms || []); },
  read(room, since) { return this.call('GET', '/room/' + encodeURIComponent(room) + '?since=' + since); },
  post(room, text) { return this.call('POST', '/room/' + encodeURIComponent(room), { from: this.name(), text, host: 'neptune-mobile', client: this.client }); },
  mine(m) { return m.client === this.client || (m.from || '').toLowerCase() === this.name().toLowerCase(); },
  // background watcher: notifications + badge while the app is closed
  offsets: {}, unread: 0, timer: null,
  watch() {
    clearInterval(this.timer);
    if (!NM.settings.ibbsNotify) return;
    const room = NM.settings.ibbsRoom || 'general';
    let fails = 0, retryAt = 0;
    const poll = async () => {
      if (NM.current() === 'ibbs' || Date.now() < retryAt) return;
      try {
        if (this.offsets[room] == null) { const j = await this.call('GET', '/room/' + room + '?since=end'); this.offsets[room] = j.offset; return; }
        const j = await this.read(room, this.offsets[room]); this.offsets[room] = j.offset;
        const fresh = (j.messages || []).filter(m => !this.mine(m));
        if (fresh.length) { this.unread += fresh.length; NM.badge('ibbs', this.unread); $('#sb-ibbs').classList.remove('hidden');
          const m = fresh[fresh.length - 1]; NM.notify('#' + room + ' · ' + m.from, m.text.slice(0, 120), { app: 'ibbs', system: true }); }
        fails = 0;
      } catch (e) { fails++; retryAt = Date.now() + Math.min(120000, 6000 * 2 ** fails); }   // board away: back off
    };
    poll(); this.timer = setInterval(poll, 6000);
  },
  seen() { this.unread = 0; NM.badge('ibbs', 0); $('#sb-ibbs').classList.add('hidden'); },
};
NM.on('started', () => IBBS.watch());
NM.on('settings', k => { if (k.startsWith('ibbs')) { IBBS.offsets = {}; IBBS.watch(); } });

NM.register({ id: 'ibbs', name: 'IBBS', icon: 'chat', c1: '#e0a030', c2: '#8a3a0a', mount(win) {
  let room = NM.settings.ibbsRoom || 'general', offset = 0, timer = null, view = 'chat', busy = false, fails = 0;
  const msgs = el('div', { class: 'msgs' });
  const status = el('div', { style: { fontSize: '12px', color: '#678', padding: '4px 10px', fontFamily: 'var(--sans)' } });
  const ta = el('textarea', { class: 'field', placeholder: 'Message #' + room, enterkeyhint: 'send', rows: 1 });
  const roomsBox = el('div', { class: 'list client hidden', style: { flex: 1 } });
  const chat = el('div', { class: 'chat', style: { flex: 1, height: 'auto', minHeight: 0 } }, status, msgs,
    el('div', { class: 'send' }, ta, tb('Send', () => send(), 'primary')));
  const roomBtn = tb('#' + room, () => showRooms());
  const fmtWhen = t => { const d = new Date(t * 1000); return (Date.now() - d < 86400000 ? '' : d.toLocaleDateString() + ' ') + NM.fmtTime(d); };
  const linkify = s => esc(s).replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  const add = m => {
    const me = IBBS.mine(m), bot = /^claude/i.test(m.from || '');
    const isFile = /^\[file\] /.test(m.text);
    const d = el('div', { class: 'msg' + (me ? ' me' : '') + (isFile ? ' file' : '') });
    d.innerHTML = (me ? '' : `<div class="who${bot ? ' bot' : ''}">${esc(m.from || '?')}${m.host && m.host !== '?' ? ' <span style="font-weight:normal;color:#8a96a3">· ' + esc(m.host) + '</span>' : ''}</div>`)
      + linkify(m.text) + `<span class="when">${fmtWhen(m.time)}</span>`;
    if (isFile && IBBS.base === 'api/ibbs') {
      const name = (m.text.match(/^\[file\] (.+?)  /) || [])[1];
      if (name) d.append(el('div', {}, el('a', { href: 'api/ibbs/file/' + encodeURIComponent(name) + '?server=' + encodeURIComponent(NM.settings.ibbsServer), download: name }, '⬇ Download ' + name)));
    }
    msgs.append(d);
  };
  const atBottom = () => msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 60;
  let retryAt = 0;
  const poll = async (force) => {
    if (busy || (force !== true && Date.now() < retryAt)) return; busy = true;
    try {
      const j = await IBBS.read(room, offset);
      const stick = atBottom() || offset === 0;
      let list = j.messages || [];
      if (offset === 0) { msgs.innerHTML = ''; if (list.length > 150) { msgs.append(el('div', { class: 'empty', style: { padding: '8px' } }, (list.length - 150) + ' older messages not shown')); list = list.slice(-150); } }
      list.forEach(add);
      if (offset === 0 && !list.length) msgs.append(el('div', { class: 'empty' }, 'No messages in #' + room + ' yet. Say hi!'));
      offset = j.offset; IBBS.offsets[room] = j.offset;
      if (stick) msgs.scrollTop = msgs.scrollHeight;
      status.textContent = 'Connected to ' + NM.settings.ibbsServer + ' as ' + IBBS.name(); fails = 0;
    } catch (e) {
      fails++; retryAt = Date.now() + Math.min(30000, 2000 * 2 ** fails);   // don't hammer a board that's down
      status.innerHTML = ''; status.append('⚠ ' + e.message + ' ', el('a', { href: '#', onclick: ev => { ev.preventDefault(); NM.open('settings', { page: 'ibbs' }); } }, 'IBBS settings'));
      if (IBBS.base === '' && offset === 0) { msgs.innerHTML = ''; msgs.append(el('div', { class: 'empty' }, 'Browsers can\'t talk to an IBBS board directly. Open Neptune Mobile through its server (python3 mobile/server.py) and IBBS will work.')); }
    }
    busy = false;
  };
  const send = async () => {
    const t = ta.value.trim(); if (!t) return;
    ta.value = ''; ta.style.height = '';
    try { await IBBS.post(room, t); NM.buzz(10); await poll(true); msgs.scrollTop = msgs.scrollHeight; }
    catch (e) { ta.value = t; NM.toast('Not sent: ' + e.message); }
  };
  ta.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  ta.addEventListener('input', () => { ta.style.height = 'auto'; ta.style.height = Math.min(120, ta.scrollHeight) + 'px'; });
  const switchRoom = r => { room = r; offset = 0; roomBtn.textContent = '#' + r; ta.placeholder = 'Message #' + r; NM.set('ibbsRoom', r); showChat(); poll(true); };
  const showChat = () => { view = 'chat'; roomsBox.classList.add('hidden'); chat.classList.remove('hidden'); win.setTitle('IBBS · #' + room); };
  const showRooms = async () => {
    view = 'rooms'; chat.classList.add('hidden'); roomsBox.classList.remove('hidden'); roomsBox.innerHTML = ''; win.setTitle('IBBS Rooms');
    roomsBox.append(el('div', { class: 'section' }, 'ROOMS ON ' + NM.settings.ibbsServer.toUpperCase()));
    try {
      const rs = await IBBS.rooms();
      rs.forEach(r => { const row = el('div', { class: 'row' }, el('div', { class: 'ri' }, '#'), el('div', { class: 'rt' }, r), el('span', { class: 'chev' }, '›')); row.onclick = () => switchRoom(r); roomsBox.append(row); });
    } catch (e) { roomsBox.append(el('div', { class: 'empty' }, '⚠ ' + e.message)); }
    const nr = el('div', { class: 'row' }, el('div', { class: 'ri' }, '+'), el('div', { class: 'rt' }, 'New room...'));
    nr.onclick = async () => { const n = await NM.prompt('New Room', 'Room name (letters, numbers, - and _)'); if (n) switchRoom(n.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '') || 'general'); };
    roomsBox.append(nr);
  };
  fill(win, toolbar(roomBtn, tb('Name: ' + IBBS.name(), async function () { const n = await NM.prompt('IBBS Name', 'Your name on the board', IBBS.name()); if (n) { NM.set('ibbsName', n.trim()); this.textContent = 'Name: ' + IBBS.name(); } }),
    tb('⚙', () => NM.open('settings', { page: 'ibbs' }))), chat, roomsBox);
  showChat();
  IBBS.detect().then(() => { poll(); timer = setInterval(poll, 2000); });
  return {
    show() { IBBS.seen(); }, close() { clearInterval(timer); },
    back() { if (view === 'rooms') { showChat(); return true; } return false; },
  };
} });

/* ================================================================ Paint */
NM.register({ id: 'paint', name: 'Paint', icon: 'paint', c1: '#e05a8a', c2: '#7a1a5a', mount(win, args) {
  const cv = el('canvas'); let ctx, color = '#1a1a1a', size = 6, erase = false, undo = [];
  const COLORS = ['#1a1a1a', '#ffffff', '#e53935', '#fb8c00', '#fdd835', '#43a047', '#1e88e5', '#8e24aa', '#26969f', '#6d4c41'];
  const cols = el('div', { class: 'colors' });
  const drawCols = () => { cols.innerHTML = ''; COLORS.forEach(c => cols.append(el('button', { class: c === color && !erase ? 'on' : '', style: { background: c }, onclick: () => { color = c; erase = false; drawCols(); } }))); };
  const sz = el('input', { type: 'range', min: 1, max: 40, value: size, style: { width: '80px' }, oninput: e => size = +e.target.value });
  const wrap = el('div', { class: 'paint' }, toolbar(cols), toolbar(sz,
    tb('Eraser', function () { erase = !erase; this.classList.toggle('on', erase); drawCols(); }),
    tb('Undo', () => { const u = undo.pop(); if (u) ctx.putImageData(u, 0, 0); }),
    tb('Clear', () => { snap(); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height); }),
    tb('Save', async () => { const n = await NM.prompt('Save Picture', 'Save in Pictures as', 'Drawing ' + new Date().toISOString().slice(0, 10) + '.png');
      if (n) { if (NM.fs.write(NM.fs.join('/Pictures', /\.png$/i.test(n) ? n : n + '.png'), cv.toDataURL('image/png'))) NM.toast('Saved to Pictures'); } }, 'primary')), cv);
  win.client.append(wrap);
  const snap = () => { undo.push(ctx.getImageData(0, 0, cv.width, cv.height)); if (undo.length > 20) undo.shift(); };
  requestAnimationFrame(() => {
    const dpr = Math.min(2, devicePixelRatio || 1);
    cv.width = cv.clientWidth * dpr; cv.height = cv.clientHeight * dpr; ctx = cv.getContext('2d');
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height); ctx.lineCap = ctx.lineJoin = 'round';
    if (args.path) { const img = new Image(); img.onload = () => ctx.drawImage(img, 0, 0, Math.min(cv.width, img.width), Math.min(cv.height, img.height)); img.src = NM.fs.read(args.path); }
    let last = null;
    const pos = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * dpr, (e.clientY - r.top) * dpr, e.pressure || 0.5]; };
    cv.addEventListener('pointerdown', e => { cv.setPointerCapture(e.pointerId); snap(); last = pos(e); stroke(last, last); });
    cv.addEventListener('pointermove', e => { if (!last) return; const p = pos(e); stroke(last, p); last = p; });
    const up = () => last = null; cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    const stroke = (a, b) => { ctx.strokeStyle = erase ? '#fff' : color; ctx.lineWidth = size * dpr * (0.6 + b[2] * 0.8); ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); };
  });
  drawCols();
} });

/* ================================================================ Photos */
NM.register({ id: 'photos', name: 'Photos', icon: 'photo', c1: '#40b080', c2: '#0a5a3a', mount(win, args) {
  const grid = el('div', { class: 'photos' });
  const pick = el('input', { type: 'file', accept: 'image/*', multiple: true, class: 'hidden', onchange: async e => {
    for (const f of e.target.files) { const d = await shrink(f); NM.fs.write('/Pictures/' + f.name.replace(/\.[^.]+$/, '') + '.jpg', d); }
    pick.value = ''; draw();
  } });
  let viewer = null;
  const shrink = f => new Promise(r => { const img = new Image(); img.onload = () => { const s = Math.min(1, 1600 / Math.max(img.width, img.height)); const c = el('canvas');
    c.width = img.width * s; c.height = img.height * s; c.getContext('2d').drawImage(img, 0, 0, c.width, c.height); URL.revokeObjectURL(img.src); r(c.toDataURL('image/jpeg', 0.85)); }; img.src = URL.createObjectURL(f); });
  const pics = () => { const out = []; const walk = p => NM.fs.list(p).forEach(it => it.dir ? walk(it.path) : /\.(png|jpe?g|gif|webp)$/i.test(it.name) && out.push(it)); walk('/'); return out.sort((a, b) => b.t - a.t); };
  const view = path => {
    const ps = pics(); let i = Math.max(0, ps.findIndex(p => p.path === path));
    viewer && viewer.remove();
    const img = el('img');
    viewer = el('div', { class: 'viewer' }, img);
    const show = () => { img.src = ps[i].data; win.setTitle(ps[i].name); };
    let x0 = null;
    viewer.addEventListener('pointerdown', e => x0 = e.clientX);
    viewer.addEventListener('pointerup', e => { if (x0 == null) return; const dx = e.clientX - x0; x0 = null;
      if (dx < -50 && i < ps.length - 1) { i++; show(); } else if (dx > 50 && i > 0) { i--; show(); } });
    viewer.append(el('div', { style: { position: 'absolute', bottom: '12px', display: 'flex', gap: '8px' } },
      tb('Edit in Paint', () => NM.open('paint', { path: ps[i].path })), tb('Set as background', () => { NM.store.set('wallCustom', ps[i].data) && NM.set('wallpaper', 'Custom'); NM.toast('Background set'); }),
      tb('Delete', async () => { if (await NM.confirm('Photos', 'Delete ' + ps[i].name + '?', 'Delete')) { NM.fs.remove(ps[i].path); viewer.remove(); viewer = null; draw(); } })));
    win.client.append(viewer); show();
  };
  const draw = () => {
    grid.innerHTML = ''; win.setTitle('Photos');
    const ps = pics();
    if (!ps.length) grid.append(el('div', { class: 'empty', style: { gridColumn: '1/-1' } }, 'No pictures yet. Import some, or draw one in Paint.'));
    ps.forEach(p => grid.append(el('img', { src: p.data, loading: 'lazy', onclick: () => view(p.path) })));
  };
  fill(win, toolbar(tb('Import from device', () => pick.click(), 'primary'), tb('Paint', () => NM.open('paint'))), el('div', { style: { flex: 1, overflow: 'auto' } }, grid), pick);
  draw();
  if (args.path) view(args.path);
  return { back() { if (viewer) { viewer.remove(); viewer = null; draw(); return true; } return false; }, args(a) { if (a.path) view(a.path); }, show() { if (!viewer) draw(); } };
} });

/* ================================================================ Druman */
NM.register({ id: 'druman', name: 'Druman', icon: 'music', c1: '#7a4ad0', c2: '#2a0a6a', dark: true, mount(win) {
  let tracks = [], idx = -1, actx, an, src, raf;
  const media = el('video', { playsinline: true, class: 'hidden' });
  const cv = el('canvas', { width: 600, height: 240 });
  const np = el('div', { class: 'np' }, 'Pick some music or video');
  const seek = el('input', { type: 'range', min: 0, max: 1000, value: 0, style: { width: '90%', margin: '0 5%', accentColor: 'rgb(var(--g))' } });
  const time = el('div', { style: { textAlign: 'center', fontFamily: 'var(--sans)', fontSize: '12px', opacity: .7 } });
  const list = el('div', { class: 'list tracks', style: { flex: 1, overflow: 'auto' } });
  const pick = el('input', { type: 'file', accept: 'audio/*,video/*', multiple: true, class: 'hidden', onchange: e => {
    for (const f of e.target.files) tracks.push({ name: f.name.replace(/\.[^.]+$/, ''), url: URL.createObjectURL(f), video: f.type.startsWith('video') });
    pick.value = ''; drawList(); if (idx < 0) play(0);
  } });
  const playBtn = el('button', { class: 'glass', onclick: () => { if (idx < 0) return pick.click(); media.paused ? media.play() : media.pause(); } }, '▶');
  const fmt = s => isFinite(s) ? Math.floor(s / 60) + ':' + String(Math.floor(s % 60)).padStart(2, '0') : '0:00';
  const drawList = () => { list.innerHTML = ''; tracks.forEach((t, i) => { const r = el('div', { class: 'row' + (i === idx ? ' on' : '') }, el('div', { class: 'ri', style: { background: 'transparent' } }, t.video ? '🎬' : '♪'), el('div', { class: 'rt' }, t.name)); r.onclick = () => play(i); list.append(r); }); };
  const play = i => {
    if (i < 0 || i >= tracks.length) return; idx = i; const t = tracks[i];
    media.src = t.url; media.classList.toggle('hidden', !t.video); cv.classList.toggle('hidden', t.video);
    np.textContent = t.name; win.setTitle('Druman · ' + t.name);
    if (!actx) { try { actx = new (window.AudioContext || window.webkitAudioContext)(); src = actx.createMediaElementSource(media); an = actx.createAnalyser(); an.fftSize = 256; src.connect(an); an.connect(actx.destination); } catch (e) {} }
    actx && actx.resume();
    media.play().catch(() => {}); drawList();
  };
  media.addEventListener('play', () => playBtn.textContent = '❚❚');
  media.addEventListener('pause', () => playBtn.textContent = '▶');
  media.addEventListener('ended', () => play(idx + 1 < tracks.length ? idx + 1 : 0));
  media.addEventListener('timeupdate', () => { seek.value = media.duration ? media.currentTime / media.duration * 1000 : 0; time.textContent = fmt(media.currentTime) + ' / ' + fmt(media.duration); });
  seek.addEventListener('input', () => { if (media.duration) media.currentTime = seek.value / 1000 * media.duration; });
  const viz = () => {   // the numpy spectrum visualiser, redone with WebAudio
    const c = cv.getContext('2d'), W = cv.width, H = cv.height;
    c.fillStyle = 'rgba(0,8,16,.35)'; c.fillRect(0, 0, W, H);
    if (an) { const d = new Uint8Array(an.frequencyBinCount); an.getByteFrequencyData(d); const n = 48, bw = W / n;
      for (let i = 0; i < n; i++) { const v = d[Math.floor(i * d.length / n * 0.8)] / 255, h = v * H * 0.9;
        const g = c.createLinearGradient(0, H, 0, H - h); g.addColorStop(0, 'rgb(' + getComputedStyle(document.documentElement).getPropertyValue('--g') + ')'); g.addColorStop(1, '#bff');
        c.fillStyle = g; c.fillRect(i * bw + 2, H - h, bw - 4, h); } }
    raf = requestAnimationFrame(viz);
  };
  fill(win, cv, media, np, seek, time, el('div', { class: 'ctl' }, el('button', { class: 'glass', onclick: () => play(idx - 1) }, '⏮'), playBtn, el('button', { class: 'glass', onclick: () => play(idx + 1) }, '⏭')),
    toolbar(tb('+ Add music or video', () => pick.click(), 'primary')), list, pick);
  viz();
  return { close() { cancelAnimationFrame(raf); media.pause(); tracks.forEach(t => URL.revokeObjectURL(t.url)); actx && actx.close(); } };
} });

/* ================================================================ Terminal */
NM.register({ id: 'terminal', name: 'Terminal', icon: 'term', c1: '#3a3a3a', c2: '#000000', dark: true, mount(win) {
  let cwd = '/Documents';
  const hist = NM.store.get('termHist', []); let hi = hist.length;
  const out = el('div');
  const inp = el('input', { autocapitalize: 'off', autocomplete: 'off', spellcheck: 'false', enterkeyhint: 'send' });
  const prompt = el('span', { class: 'p' });
  const term = el('div', { class: 'term' }, out, el('div', { class: 'in' }, prompt, inp));
  const print = (t, cls) => { out.append(el('div', { class: cls || '' }, t)); term.scrollTop = term.scrollHeight; };
  const ps = () => prompt.textContent = (NM.settings.user || 'user').toLowerCase().replace(/\s+/g, '') + '@neptune:' + (cwd === '/' ? '/' : cwd) + '$ ';
  const CMDS = {
    help: () => print('COS Neptune Mobile shell. Commands:\n  ls [dir]   cd <dir>   pwd   cat <file>   echo <text> [> file]\n  mkdir <d>  rm <path>  mv <a> <b>  touch <f>  edit <f>\n  open <app> apps   date   ver   whoami   calc <expr>\n  ibbs [room]      read the last messages   ibbs say <text>\n  neofetch   clear   history   exit', 'h'),
    ls: a => { const p = NM.fs.join(cwd, a[0] || '.'); const s = NM.fs.stat(p); if (!s) return print('ls: no such file or directory: ' + (a[0] || ''), 'e');
      if (!s.dir) return print(a[0]); const l = NM.fs.list(p); print(l.map(x => x.dir ? x.name + '/' : x.name).join('  ') || '(empty)'); },
    cd: a => { const p = NM.fs.join(cwd, a[0] || '/'); const s = NM.fs.stat(p); if (!s || !s.dir) return print('cd: not a directory: ' + (a[0] || ''), 'e'); cwd = p; ps(); },
    pwd: () => print(cwd),
    cat: a => { const t = NM.fs.read(NM.fs.join(cwd, a[0] || '')); t == null ? print('cat: ' + (a[0] || '') + ': no such file', 'e') : print(t.startsWith('data:') ? '(binary data, ' + NM.human(t.length) + ')' : t); },
    echo: (a, raw) => { const m = raw.match(/^echo\s+(.*?)\s*(>>?)\s*(\S+)$/);
      if (m) { const p = NM.fs.join(cwd, m[3]); NM.fs.write(p, (m[2] === '>>' ? (NM.fs.read(p) || '') : '') + m[1] + '\n'); } else print(a.join(' ')); },
    mkdir: a => a[0] ? NM.fs.mkdir(NM.fs.join(cwd, a[0])) : print('mkdir: missing name', 'e'),
    rm: a => { const p = NM.fs.join(cwd, a[0] || ''); NM.fs.stat(p) && a[0] ? NM.fs.remove(p) : print('rm: cannot remove ' + (a[0] || ''), 'e'); },
    mv: a => { const p = NM.fs.join(cwd, a[0] || ''); NM.fs.stat(p) && a[1] ? NM.fs.rename(p, NM.fs.join(cwd, a[1])) : print('mv: usage: mv <from> <to>', 'e'); },
    touch: a => { const p = NM.fs.join(cwd, a[0] || ''); if (!NM.fs.stat(p)) NM.fs.write(p, ''); },
    edit: a => NM.open('notepad', { path: NM.fs.join(cwd, a[0] || 'untitled.txt') }),
    open: a => { const id = (a[0] || '').toLowerCase(); const hit = NM.apps[id] ? id : NM.order.find(k => NM.apps[k].name.toLowerCase() === id); hit ? NM.open(hit) : print('open: no app called ' + (a[0] || ''), 'e'); },
    apps: () => print(NM.order.map(k => k.padEnd(11) + NM.apps[k].name).join('\n')),
    date: () => print(new Date().toString()),
    ver: () => print(NM.FULLNAME + '  Build ' + NM.BUILD),
    whoami: () => print((NM.settings.user || 'user') + ' (IBBS: ' + NM.ibbs.name() + ')'),
    calc: a => { const e = a.join(''); if (!/^[0-9+\-*/().%\s^]+$/.test(e)) return print('calc: numbers and + - * / ^ ( ) only', 'e');
      try { print(String(Function('"use strict";return (' + e.replace(/\^/g, '**') + ')')())); } catch (x) { print('calc: ' + x.message, 'e'); } },
    history: () => print(hist.map((h, i) => String(i + 1).padStart(4) + '  ' + h).join('\n')),
    clear: () => out.innerHTML = '',
    exit: () => NM.close('terminal'),
    neofetch: () => print(`   .----.      ${NM.settings.user || 'user'}@neptune\n  / COS  \\     -----------\n |  ()    |    OS: ${NM.FULLNAME}\n  \\      /     Build: ${NM.BUILD}\n   '----'      Screen: ${screen.width}x${screen.height} @${devicePixelRatio}x\n               Glass: ${NM.settings.glass}  Wallpaper: ${NM.settings.wallpaper}\n               Apps: ${NM.order.length}  Running: ${NM.running.size}`, 'h'),
    ibbs: async a => {
      if (a[0] === 'say') { const t = a.slice(1).join(' '); if (!t) return print('ibbs say <text>', 'e'); try { await NM.ibbs.post(NM.settings.ibbsRoom, t); print('sent to #' + NM.settings.ibbsRoom); } catch (e) { print('ibbs: ' + e.message, 'e'); } return; }
      const room = a[0] || NM.settings.ibbsRoom;
      try { const j = await NM.ibbs.read(room, 0); const ms = (j.messages || []).slice(-15);
        print(ms.map(m => '[' + new Date(m.time * 1000).toLocaleTimeString() + '] <' + m.from + '> ' + m.text).join('\n') || '(no messages in #' + room + ')'); }
      catch (e) { print('ibbs: ' + e.message, 'e'); }
    },
  };
  const run = async raw => {
    print(prompt.textContent + raw);
    if (!raw.trim()) return;
    hist.push(raw); NM.store.set('termHist', hist.slice(-100)); hi = hist.length;
    const [cmd, ...args] = raw.trim().split(/\s+/);
    const f = CMDS[cmd];
    if (!f) return print(cmd + ': command not found (try help)', 'e');
    await f(args, raw.trim());
  };
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') { const v = inp.value; inp.value = ''; run(v); }
    else if (e.key === 'ArrowUp') { if (hi > 0) inp.value = hist[--hi]; e.preventDefault(); }
    else if (e.key === 'ArrowDown') { inp.value = hi < hist.length - 1 ? hist[++hi] : (hi = hist.length, ''); e.preventDefault(); }
    else if (e.key === 'Tab') { e.preventDefault(); const w = inp.value.split(' '); const last = w.pop();
      const pool = w.length ? NM.fs.list(cwd).map(x => x.name) : Object.keys(CMDS); const m = pool.filter(x => x.startsWith(last)); if (m.length === 1) inp.value = [...w, m[0]].join(' '); }
  });
  term.addEventListener('click', () => inp.focus());
  win.client.append(term);
  ps(); print(NM.FULLNAME + '  Build ' + NM.BUILD + '\nType help for commands.', 'h');
  return { show() { setTimeout(() => inp.focus(), 300); } };
} });

/* ================================================================ Bubbles (game) */
NM.register({ id: 'bubbles', name: 'Bubbles', icon: 'bubbles', c1: '#30c0f0', c2: '#0a4aa0', mount(win) {
  const cv = el('canvas'), hud = el('div', { class: 'hud' }), over = el('div', { class: 'over' });
  const box = el('div', { class: 'game' }, cv, hud, over);
  win.client.append(box);
  let W, H, dpr, bubs = [], pops = [], score = 0, lives = 5, level = 1, running = false, raf, last = 0, spawn = 0;
  const best = () => NM.store.get('bubblesBest', 0);
  const size = () => { dpr = Math.min(2, devicePixelRatio || 1); W = cv.width = box.clientWidth * dpr; H = cv.height = box.clientHeight * dpr; };
  const menu = (title, sub) => { over.innerHTML = ''; over.classList.remove('hidden');
    over.append(el('h2', {}, title), el('div', {}, sub), el('div', {}, 'Best: ' + best()), el('button', { class: 'glass pill', style: { fontSize: '18px' }, onclick: startGame }, 'Play')); };
  const startGame = () => { bubs = []; pops = []; score = 0; lives = 5; level = 1; running = true; over.classList.add('hidden'); last = performance.now(); loop(last); };
  const loop = t => {
    const dt = Math.min(50, t - last) / 1000; last = t;
    spawn -= dt;
    if (spawn <= 0) { spawn = Math.max(0.28, 1.1 - level * 0.08);
      const r = (16 + Math.random() * 26) * dpr, gold = Math.random() < 0.06, bomb = !gold && level > 2 && Math.random() < 0.08;
      bubs.push({ x: r + Math.random() * (W - 2 * r), y: H + r, r, v: (60 + level * 14 + Math.random() * 40) * dpr, w: Math.random() * 6, gold, bomb }); }
    const ctx = cv.getContext('2d'); ctx.clearRect(0, 0, W, H);
    for (const b of bubs) { b.y -= b.v * dt; b.x += Math.sin(t / 500 + b.w) * 0.4 * dpr;
      if (b.bomb) { ctx.fillStyle = '#300'; ctx.beginPath(); ctx.arc(b.x, b.y, b.r, 0, 7); ctx.fill(); ctx.strokeStyle = '#f44'; ctx.lineWidth = 2 * dpr; ctx.stroke(); ctx.fillStyle = '#f66'; ctx.font = `bold ${b.r}px sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText('✕', b.x, b.y); }
      else NM.drawBubbleLogo(ctx, b.x, b.y, b.r, b.gold ? [[230, 180, 40], [150, 80, 10]] : [[44, 100, 128], [12, 32, 132]], b.gold ? [255, 240, 180] : [205, 205, 190], b.r > 26 * dpr ? 'COS' : ''); }
    const escaped = bubs.filter(b => b.y < -b.r && !b.bomb).length;
    if (escaped) { lives -= escaped; NM.buzz(40); }
    bubs = bubs.filter(b => b.y >= -b.r);
    pops = pops.filter(p => (p.a -= dt * 2.5) > 0);
    for (const p of pops) { ctx.strokeStyle = `rgba(200,240,255,${p.a})`; ctx.lineWidth = 2 * dpr; for (let i = 0; i < 8; i++) { const an = i * Math.PI / 4, d = p.r * (1.6 - p.a * 0.6);
      ctx.beginPath(); ctx.moveTo(p.x + Math.cos(an) * d * 0.6, p.y + Math.sin(an) * d * 0.6); ctx.lineTo(p.x + Math.cos(an) * d, p.y + Math.sin(an) * d); ctx.stroke(); }
      if (p.txt) { ctx.fillStyle = `rgba(255,255,255,${p.a})`; ctx.font = `${16 * dpr}px Marcellus, serif`; ctx.textAlign = 'center'; ctx.fillText(p.txt, p.x, p.y - p.r - (1 - p.a) * 30 * dpr); } }
    level = 1 + Math.floor(score / 25);
    hud.innerHTML = `<span>Score ${score}</span><span>Level ${level}</span><span>${'●'.repeat(Math.max(0, lives))}</span>`;
    if (lives <= 0) { running = false; if (score > best()) NM.store.set('bubblesBest', score); menu('GAME OVER', 'You popped ' + score + ' bubbles.'); return; }
    if (running) raf = requestAnimationFrame(loop);
  };
  cv.addEventListener('pointerdown', e => {
    if (!running) return; const r = cv.getBoundingClientRect(), x = (e.clientX - r.left) * dpr, y = (e.clientY - r.top) * dpr;
    for (let i = bubs.length - 1; i >= 0; i--) { const b = bubs[i]; if (Math.hypot(b.x - x, b.y - y) < b.r + 8 * dpr) {
      bubs.splice(i, 1); NM.buzz(10);
      if (b.bomb) { lives -= 2; pops.push({ x: b.x, y: b.y, r: b.r, a: 1, txt: '−2 ●' }); }
      else { const pts = b.gold ? 10 : 1; score += pts; pops.push({ x: b.x, y: b.y, r: b.r, a: 1, txt: b.gold ? '+10' : '' }); }
      break; } }
  });
  requestAnimationFrame(() => { size(); menu('BUBBLES', 'Pop the COS bubbles before they float away. Gold ones are worth 10. Don\'t touch the red ones.'); });
  return { close() { running = false; cancelAnimationFrame(raf); }, hide() { if (running) { running = false; cancelAnimationFrame(raf); menu('PAUSED', 'Score ' + score); } } };
} });

/* ================================================================ Task Manager */
NM.register({ id: 'taskmgr', name: 'Task Manager', icon: 'task', c1: '#30a050', c2: '#0a4a1a', mount(win) {
  let tab = 'apps', raf, frames = [], lastT = performance.now(), fpsHist = [];
  const body = el('div', { style: { flex: 1, overflow: 'auto' } });
  const seg = el('div', { class: 'toolbar' });
  const draw = () => {
    seg.innerHTML = ''; [['apps', 'Applications'], ['perf', 'Performance']].forEach(([k, l]) => seg.append(tb(l, () => { tab = k; draw(); }, k === tab ? 'on' : '')));
    body.innerHTML = '';
    if (tab === 'apps') {
      const l = el('div', { class: 'list' });
      for (const [id, r] of NM.running) l.append(el('div', { class: 'row' }, NM.bubble(r.app, 34), el('div', { class: 'rt' }, r.win.titleEl.textContent, el('small', {}, 'Running for ' + Math.round((Date.now() - r.opened) / 1000) + ' s')),
        id !== 'taskmgr' ? tb('Switch to', () => NM.open(id)) : null, tb('End Task', () => { NM.close(id); setTimeout(draw, 350); })));
      body.append(l);
    } else {
      const cv = el('canvas', { width: 600, height: 240 }), info = el('div', { style: { padding: '10px 14px', fontFamily: 'var(--sans)', fontSize: '14px', lineHeight: 1.7 } });
      body.append(el('div', { class: 'section' }, 'FRAME RATE'), el('div', { class: 'perf' }, cv), info);
      const upd = () => { if (tab !== 'perf' || !NM.running.has('taskmgr')) return;
        const c = cv.getContext('2d'); c.fillStyle = '#001a10'; c.fillRect(0, 0, 600, 240); c.strokeStyle = '#0a4a2a';
        for (let x = 0; x < 600; x += 30) { c.beginPath(); c.moveTo(x, 0); c.lineTo(x, 240); c.stroke(); } for (let y = 0; y < 240; y += 30) { c.beginPath(); c.moveTo(0, y); c.lineTo(600, y); c.stroke(); }
        c.strokeStyle = '#3f3'; c.lineWidth = 2; c.beginPath(); fpsHist.forEach((v, i) => { const x = 600 - (fpsHist.length - i) * 6, y = 240 - Math.min(1, v / 120) * 230; i ? c.lineTo(x, y) : c.moveTo(x, y); }); c.stroke();
        const m = performance.memory;
        let used = 0; try { for (const k in localStorage) if (k.startsWith('nm.')) used += (localStorage.getItem(k) || '').length; } catch (e) {}
        info.innerHTML = `Frame rate: <b>${fpsHist.length ? fpsHist[fpsHist.length - 1] : 0} fps</b><br>Apps running: <b>${NM.running.size}</b><br>
          CPU threads: <b>${navigator.hardwareConcurrency || '?'}</b>${navigator.deviceMemory ? '<br>Device memory: <b>' + navigator.deviceMemory + ' GB</b>' : ''}
          ${m ? '<br>Script memory: <b>' + NM.human(m.usedJSHeapSize) + '</b> of ' + NM.human(m.jsHeapSizeLimit) : ''}<br>Neptune storage: <b>${NM.human(used * 2)}</b>`;
        setTimeout(upd, 500); };
      upd();
    }
  };
  const count = t => { frames.push(t); while (frames[0] < t - 1000) frames.shift(); if (t - lastT > 500) { lastT = t; fpsHist.push(frames.length); if (fpsHist.length > 100) fpsHist.shift(); } raf = requestAnimationFrame(count); };
  fill(win, seg, body); draw(); raf = requestAnimationFrame(count);
  const re = () => tab === 'apps' && NM.running.has('taskmgr') && draw(); NM.on('apps', re);
  return { close() { cancelAnimationFrame(raf); } };
} });

/* ================================================================ Settings (the Control Panel) */
NM.register({ id: 'settings', name: 'Settings', icon: 'gear', c1: '#7a8a9a', c2: '#2a3a4a', mount(win, args) {
  const S = NM.settings;
  let page = null;
  const body = el('div', { class: 'client', style: { background: '#f4f6f9' } });
  win.client.append(body); win.client.style.display = 'flex';
  const row = (label, sub, right, onclick) => { const r = el('div', { class: 'row setrow', style: { background: '#fff' } }, el('div', { class: 'rt' }, label, sub ? el('small', {}, sub) : null), right || null); if (onclick) r.onclick = onclick; return r; };
  const toggle = (k, label, sub) => { const sw = el('span', { class: 'sw' + (S[k] ? ' on' : '') });
    return row(label, sub, sw, () => { NM.set(k, !S[k]); sw.classList.toggle('on', S[k]); if (k === 'bubbo' || k === 'tip') NM.renderHome(); }); };
  const PAGES = {
    personalize: ['Personalize', 'gear', 'Glass colour, intensity, Aero', () => {
      const sw = el('div', { class: 'big-swatches' });
      const paint = () => { sw.innerHTML = ''; NM.GLASS.forEach(([n, c]) => sw.append(el('button', { title: n, class: n === S.glass ? 'on' : '', style: { background: `rgb(${c})` }, onclick: () => { NM.set('glass', n); paint(); } }))); };
      paint();
      const inten = el('input', { type: 'range', min: 0, max: 100, value: Math.round(S.intensity * 100), oninput: e => NM.set('intensity', e.target.value / 100) });
      return [el('div', { class: 'section' }, 'GLASS COLOUR'), sw, row('Colour intensity', null, inten),
        toggle('transparency', 'Enable transparency'), toggle('aero', 'Aero Glass', 'Off = Neptune Basic: no blur, for slow phones'), toggle('animations', 'Animations')];
    }],
    background: ['Background', 'photo', 'Neptune and bubble wallpapers', () => {
      const g = el('div', { class: 'wall-grid' });
      const paint = () => { g.innerHTML = ''; Object.keys(NM.WALLPAPERS).forEach(n => g.append(el('button', { class: n === S.wallpaper ? 'on' : '', onclick: () => { NM.set('wallpaper', n); paint(); } }, el('img', { src: NM.wallThumb(n) }), el('span', {}, n)))); };
      setTimeout(paint, 30);
      const pick = el('input', { type: 'file', accept: 'image/*', class: 'hidden', onchange: e => { const f = e.target.files[0]; if (!f) return; const img = new Image();
        img.onload = () => { const s = Math.min(1, 1400 / Math.max(img.width, img.height)), c = el('canvas'); c.width = img.width * s; c.height = img.height * s; c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
          if (NM.store.set('wallCustom', c.toDataURL('image/jpeg', 0.85))) { NM.set('wallpaper', 'Custom'); NM.toast('Background set'); paint(); } }; img.src = URL.createObjectURL(f); } });
      return [g, pick, row('Use my own picture...', S.wallpaper === 'Custom' ? 'In use' : null, el('span', { class: 'chev' }, '›'), () => pick.click())];
    }],
    lock: ['Lock Screen', 'lock', 'PIN, lock at start', () => [
      row(S.pin ? 'Change PIN' : 'Set a PIN', S.pin ? 'A PIN is set' : 'No PIN: swipe up unlocks', el('span', { class: 'chev' }, '›'), async () => {
        const p = await NM.dialog({ title: 'Lock Screen PIN', body: '4 to 8 digits. It locks the look, not real security.', input: 'tel', buttons: [['Save', true], ['Cancel', null]] });
        if (p == null) return; if (!/^\d{4,8}$/.test(p)) return NM.toast('A PIN is 4 to 8 digits'); NM.set('pin', p); NM.toast('PIN set'); open('lock'); }),
      S.pin ? row('Remove PIN', null, null, () => { NM.set('pin', ''); NM.toast('PIN removed'); open('lock'); }) : null,
      toggle('lockOnStart', 'Lock screen at start'), toggle('bootScreen', 'Boot screen'),
      row('Lock now', null, el('span', { class: 'chev' }, '›'), () => NM.lock())]],
    sound: ['Sounds', 'music', 'Sounds and volume', () => {
      const v = el('input', { type: 'range', min: 0, max: 100, value: S.volume, oninput: e => NM.set('volume', +e.target.value), onchange: () => NM.sound('notify') });
      return [toggle('sounds', 'Play sounds'), row('Volume', null, v), row('Test sound', null, el('span', { class: 'chev' }, '▶'), () => NM.sound('notify'))];
    }],
    time: ['Date and Time', 'clock', '12/24-hour clock', () => [toggle('clock24', '24-hour clock'), row('Time zone', Intl.DateTimeFormat().resolvedOptions().timeZone)]],
    home: ['Home Screen', 'files', 'Bubbo, tip of the day', () => [toggle('bubbo', 'Bubbo the pet', 'Lives on page 2'), toggle('tip', 'Tip of the day')]],
    ibbs: ['IBBS', 'chat', 'Board server, name, token', () => {
      const f = (k, label, ph, type) => { const i = el('input', { class: 'field', value: S[k] || '', placeholder: ph, type: type || 'text', autocapitalize: 'off', onchange: e => NM.set(k, e.target.value.trim()) });
        return el('div', { style: { padding: '8px 14px', background: '#fff' } }, el('div', { style: { fontSize: '13px', color: '#678', marginBottom: '4px' } }, label), i); };
      const test = el('small');
      return [el('div', { class: 'section' }, 'TCF INTERNAL BULLETIN BOARD SYSTEM'),
        f('ibbsServer', 'Board server (host:port)', '192.168.1.115:8619'), f('ibbsName', 'Your name on the board', NM.ibbs.name()),
        f('ibbsToken', 'Token (IBBS_TOKEN, if the board has one)', 'none', 'password'), f('ibbsRoom', 'Room to watch', 'general'),
        toggle('ibbsNotify', 'Notify me about new messages', 'Checks every few seconds while Neptune is open'),
        row('Test connection', test, el('span', { class: 'chev' }, '›'), async () => { test.textContent = 'Connecting...';
          try { const r = await NM.ibbs.rooms(); test.textContent = 'OK: ' + r.length + ' rooms (' + r.slice(0, 5).join(', ') + ')'; } catch (e) { test.textContent = '⚠ ' + e.message; } }),
        'Notification' in window ? row('Allow system notifications', Notification.permission, null, () => Notification.requestPermission().then(() => open('ibbs'))) : null];
    }],
    about: ['About', 'info', NM.FULLNAME, () => [el('div', { class: 'about' }, el('div', { class: 'boot-orb' }), el('h2', {}, 'COS NEPTUNE MOBILE'),
      el('p', {}, NM.FULLNAME), el('p', {}, 'Build ' + NM.BUILD), el('p', {}, 'Screen ' + screen.width + '×' + screen.height + ' at ' + devicePixelRatio + 'x'),
      el('p', { style: { marginTop: '14px' } }, 'The Aero release of COS, for phones. Glass, bubbles and IBBS in your pocket.')),
      row('Run Setup again', null, el('span', { class: 'chev' }, '›'), () => { NM.set('oobe', false); location.reload(); }),
      row('Reset COS Neptune Mobile', 'Deletes settings, files and notes on this phone', null, async () => {
        if (await NM.confirm('Reset', 'Erase everything Neptune Mobile stored on this phone?', 'Erase')) { try { Object.keys(localStorage).filter(k => k.startsWith('nm.')).forEach(k => localStorage.removeItem(k)); } catch (e) {} location.reload(); } })]],
  };
  const open = p => {
    page = p; body.innerHTML = ''; body.scrollTop = 0;
    if (!p) {
      win.setTitle('Settings');
      body.append(el('div', { class: 'section' }, 'CONTROL PANEL'));
      for (const k in PAGES) { const [t, ic, sub] = PAGES[k]; body.append(row(t, sub, el('span', { class: 'chev' }, '›'), () => open(k))); body.lastChild.prepend(NM.bubble({ icon: ic }, 34)); }
      return;
    }
    win.setTitle(PAGES[p][0]);
    PAGES[p][3]().forEach(x => x && body.append(x));
  };
  open(args.page || null);
  return { back() { if (page) { open(null); return true; } return false; }, args(a) { open(a.page || null); } };
} });

/* ================================================================ Help */
NM.register({ id: 'help', name: 'Help', icon: 'help', c1: '#4ab0d0', c2: '#0a4a7a', mount(win) {
  win.client.innerHTML = `<div style="padding:16px 18px;font-family:var(--sans);line-height:1.55;color:#223">
  <h2 style="font-family:var(--sc);font-weight:normal;letter-spacing:2px;color:rgb(var(--g));margin-top:0">NEPTUNE MOBILE HELP</h2>
  <h3>Getting around</h3><ul>
  <li><b>Orb</b> (bottom middle): home. <b>Hold it</b> for Lock, Restart and Shut Down.</li>
  <li><b>‹</b> (bottom left) or your phone's back gesture: back.</li>
  <li><b>▢</b> (bottom right): recent apps. Tap a card to switch, swipe it up to close.</li>
  <li><b>Swipe down</b> from the top: quick settings, brightness, glass colour, notifications.</li>
  <li>Swipe the home screen sideways for more apps (and Bubbo).</li></ul>
  <h3>Make it an app</h3><p>In Chrome on Android: ⋮ › <i>Add to Home screen</i> (or <i>Install app</i>). On iPhone: Share › <i>Add to Home Screen</i>. Neptune then opens full screen with its own icon.</p>
  <h3>IBBS</h3><p>IBBS talks to a TCF_IBBS board, the same one <code>ibbs.py</code> uses. Run Neptune Mobile from its server (<code>python3 ~/cos/neptune/mobile/server.py</code>) and open the address it prints on your phone. Set the board in Settings › IBBS. Messages from Claude agents are shown in orange.</p>
  <h3>Your files</h3><p>Files, Notepad, Paint, Photos and the Terminal share a little file system that's kept on this phone, in the browser. Use Files › ⋯ › <i>Download to device</i> to take a copy out.</p>
  <h3>Keyboard</h3><p>Esc = back. Ctrl+Shift+Esc = Task Manager. And there's a secret.</p>
  <p style="color:#789;font-size:13px">${NM.esc(NM.FULLNAME)} · Build ${NM.BUILD}</p></div>`;
} });

/* ================================================================ About (shortcut) */
NM.register({ id: 'about', name: 'About', icon: 'info', c1: '#2c6480', c2: '#0c2084', mount(win) {
  setTimeout(() => { NM.close('about', true); NM.open('settings', { page: 'about' }); }, 0);
} });

NM.start();
})();
