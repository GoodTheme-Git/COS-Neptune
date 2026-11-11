/* Neptune Mobile service worker: works offline once loaded. Network first for the
   shell files (so updates show up at once), cache as the fallback; IBBS is never cached. */
const CACHE = 'neptune-mobile-v1';
const SHELL = ['./', 'index.html', 'neptune.css', 'neptune.js', 'apps.js', 'manifest.webmanifest',
  'icons/icon-192.png', 'icons/icon-512.png', 'sounds/boot.mp3', 'sounds/notify.mp3', 'sounds/shutdown.mp3', 'sounds/warn.mp3'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())); });
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== location.origin || u.pathname.includes('/api/')) return;
  e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r; })
    .catch(() => caches.match(e.request).then(r => r || caches.match('index.html'))));
});
