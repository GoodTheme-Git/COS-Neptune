# Handoff: add JavaScript to Webinator

Status: **built and tested headless (2026-10-31).** See "What was built" at the end.
Main goal: make JavaScript single-page apps work, starting with
**TCFNet** (https://main.taila6def0.ts.net/, a Vite/React app; the page is just
an empty `<div id="root">` plus `<script type="module" src="/assets/index-*.js">`).

## Ground rules (from the user)
- Only write inside `~/cos/neptune`. Don't change anything else in `~/cos`.
- Neptune is pure Python + PIL/numpy. Keep it that way.
- Test headless (see "Testing" below). Never take over the real display.

## Plan (decided, not built)
Run page JavaScript in **Deno**, one subprocess per page, sandboxed:
`deno run --quiet --no-prompt --no-config --allow-net --allow-import --location <page origin> <runtime.js>`
(network only: no files, no running programs, no env access). Deno 2.9.7 is
at `~/.deno/bin/deno` (no Node on this machine). Deno already provides modern
JS, `fetch`, `WebSocket`, timers, `localStorage` (per `--location`),
`crypto` and `MessageChannel`.

What we have to write ourselves:
1. `nep/web/jsrt/runtime.js`, which runs inside Deno:
   - A DOM: Node/Element/Text/Document/DocumentFragment, attributes,
     `style` (camelCase and `setProperty`, plus `cssText`), `classList`,
     `dataset`, `innerHTML` (needs a small HTML parser), `querySelector(All)`,
     and events with capture/bubble up to `window`.
   - The globals React needs: `window`, `document`, `location`, `history`
     (pushState/replaceState/popstate), `getComputedStyle`, `matchMedia`,
     `requestAnimationFrame`, and stubs for ResizeObserver,
     IntersectionObserver, MutationObserver and Audio.
   - **React controlled inputs:** `value` must be an accessor on
     `HTMLInputElement.prototype`. When the user types, store the new value
     internally *without* going through the instance setter, then dispatch an
     `input` event. Otherwise React's value tracker swallows `onChange`.
   - Script execution: classic scripts via indirect eval. Module scripts via
     `await import(absoluteUrl)`, with inline modules imported from a
     `data:text/javascript,` URL. After that, fire `DOMContentLoaded` and
     then `load`.
   - The bridge is JSON lines over stdin/stdout. Webinator → JS: `load {url,
     html, width, height}`, `click {id}`, `input {id, value}`,
     `change {id, value}`, `key`, `submit`, `resize`, `history`.
     JS → Webinator: `dom {html, title}` (throttled about 40 ms after
     mutations), `url` (pushState), `navigate`, `submit`, `focus`,
     `alert`, `console`, `error`.
   - Serialize the DOM to HTML with `data-nep="<id>"` on every element, and
     input/select/textarea values written out from their live values.
2. `nep/web/jsengine.py`, which starts and stops Deno, runs reader threads,
   and has `send()`, `poll()` and `close()`. If Deno is missing, Webinator
   stays static.

## Where to hook in (current code)
- `nep/apps/webinator.py`
  - `_load()` (~line 240): the raw HTML is in `res['html']`. Start the engine
    here for http(s) pages that contain `<script>`.
  - `finish_load()` (~385) and `on_tick()` (~536): drain engine messages here.
    On `dom`, run `dom.parse(html)` (the parser already drops `<script>`),
    swap it into `self.doc` and `relayout()`, keeping the scroll position and
    the focused control by its `data-nep` id.
  - `on_down()` (~811), `click_ctl()` (~842), `ctl_key()` (~1178),
    `set_select()` (~864) and `submit()` (~868): when JS is active, send
    click/input/change/submit to the engine instead of (or before) the
    built-in action. JS answers `navigate`/`submit` when nothing called
    `preventDefault()`.
  - `back()`/`forward()`: send `history` to JS if it has pushState entries.
  - `on_close()` and `navigate()`: close the engine.
  - Add an "Enable JavaScript" toggle to `tools_menu()`.
- `nep/web/layout.py`
  - Hit testing only covers links (`self.regions`) and controls
    (`self.controls`). JS needs "which element is at this point". Add a
    `Style` slot (e.g. `el`), set it in `style_for()`, and record
    `(x0, y0, x1, y1, node)` boxes in `_emit_run()` and `block()`. Then pick
    the smallest box containing the point and climb to the nearest node with
    `data-nep`.
  - `control()` (~453) keys controls by `node.nid`, which changes on every
    re-parse. Key them by `data-nep` when present.
- `nep/web/dom.py`: CSS support is minimal (no flexbox), so JS apps will
  render roughly but should be usable.

## TCFNet facts (from its bundle)
- `window.TCFNET_API` is an empty string (same origin), and realtime is
  `wss://<host>/rt`.
- The auth token is in `localStorage['tcfnet.token']`, sent as
  `Authorization: Bearer`.
- Data goes through `POST /api/query` (Supabase-style: table, action,
  filters). There's also `/api/auth/*`, `/api/friends*`, `/api/pages*`,
  `/api/streams*`, `/api/upload` and `/api/rpc`. Calls and QuickShare use
  PeerJS/WebRTC, which won't work here and is fine.
- The TCFNet server source is at `~/TCFNet` (a separate project). Its access
  rules were just tightened (`server/access.js`), so test with a real
  account, not by poking at other users' data.

## Testing
- Headless harness pattern: build a `nep.shell.Shell(w, h, Settings(),
  backend='headless', registry=REGISTRY, boot=False)`, feed it events with
  `shell.handle({...})`, tick it with `shell.tick(time.time())`, and save
  `shell.frame(now)[0]` as a PNG to look at. (The previous session's harness
  lived in its scratchpad and is gone; it's about 60 lines to recreate.)
- Start with a local test page: inline script, a button with a click handler
  that changes text, a text input bound with `oninput`, a `fetch`, and
  `pushState`. Then a tiny React app. Then TCFNet.
- Clean up any Deno processes you start, using their PIDs (not a broad
  `pkill`).


## What was built (2026-10-31)
- `nep/web/jsrt/runtime.js` (DOM, selectors, HTML parser/serializer, events,
  location/history, storage, fetch/XHR/WebSocket wrappers, script loading) and
  `nep/web/jsengine.py` (process, reader threads, per-site storage, console log).
- **Change from the plan:** no `--location`. With it, Deno's `location` global
  is read-only, so pushState couldn't update it. The runtime keeps its own
  `location`. `localStorage` and cookies are kept by Python per origin in
  `home/.neptune/web/storage/<origin>.json` (mode 0600, it holds login tokens)
  and handed over in `load`. The sandbox is still network-only.
  `DENO_DIR=neptune/cache/deno` so nothing is written outside `~/cos/neptune`.
  `--reload` is used so remote modules are never served stale.
- Deno, process, Buffer and global are hidden from page code.
- Messages added beyond the plan: `ready`, `storage`, `open`, and `ack` on `dom`
  (the last input seq JS handled, so Webinator keeps the user's typing until JS
  has caught up). The input message also carries `key`/`char`, so the page sees
  keydown → beforeinput → input → keyup and can `preventDefault()` keys.
- Layout: `Style.el`, `Layout.boxes`, `Layout.el_at()`; controls keyed by
  `data-nep`; icon-only buttons are labelled with aria-label/title.
- Shell: `_remove()` calls `app.on_destroy()` so a force-closed window still
  stops its Deno. jsengine also kills its engines at exit.
- Setting `webinator_js` (default on), toggled in Tools > Enable JavaScript.

Tested headless: local page (inline onclick, listeners, typing, fetch, submit
intercept, pushState + Back), React 18 UMD app (controlled input, form Enter,
checkbox, label click), TCFNet (renders login/register, typing, tab switch,
theme select persisted), Hacker News (links go through the JS click path).
Not tested: anything behind TCFNet's login (no test account was used).

Known gaps: CSS `var()`/flexbox aren't laid out, so TCFNet looks plain (no
dark theme); no canvas/media/WebRTC; geometry APIs return zeros;
MutationObserver/ResizeObserver are stubs; top-level `let`/`const` in classic
scripts aren't shared between scripts (indirect eval).

## CSS engine upgrade (2026-10-31, later)
The "looks plain" gap is closed for app-style pages:
- `nep/web/dom.py`: new cascade. Selectors with attributes, `:root`,
  `:first-child`/`:last-child`/`:only-child`, simple `:not()`; specificity
  ordering; custom properties with `var()` and inheritance (`node.vars`);
  desktop `@media` handling. `<html>` attributes now land in
  `root.html_attrs`, so `:root[data-theme=...]` themes switch.
- `nep/web/cssbox.py` (mixed into Layout): box model (margin/padding/border/
  radius/background/width/max-width/min-height), flexbox (row/column, wrap,
  gap, flex grow/shrink/basis, justify-content, align-items/self, stretch),
  grid (`grid-template-columns` px/fr/repeat/auto-fill minmax), inline-blocks
  (styled buttons when JS runs), calc()/min()/max()/clamp(), colour emoji
  (Noto Color Emoji), round images (border-radius), text-transform,
  text-overflow: ellipsis, CSS-styled text fields/selects.
- New display item `('b', x0, y0, x1, y1, fill, border, (t,r,b,l), radius, node)`,
  painted by `Webinator.draw_box`. Image items may carry a 9th field: radius.
Still missing: floats, position absolute/fixed (laid out in flow), transforms,
animations, `:hover` styles, CJK/Arabic fonts.

## Cookies (reworked 2026-11-05)

The per-site name=value cookie store is gone. Cookies now live in one jar,
`nep/web/cookies.py`, saved in `home/.neptune/web/cookies.json` (0600,
persistent cookies only; session cookies end when Neptune exits). It keeps
each cookie's rules: Domain/host-only (checked against the system Public
Suffix List, so `.google.com` set by accounts.google.com reaches
www.google.com, and nobody can set `.com` or `.co.uk`), Path, Secure,
HttpOnly, SameSite (Lax by default; Strict only same-site; None needs
Secure), Expires/Max-Age (400-day cap), `__Secure-`/`__Host-` prefixes,
4 KB / 180-per-domain / 3000-total limits.

HttpOnly is enforced by never giving those cookies to the Deno process:
- the runtime gets only the document.cookie view (`script_view`) plus the
  *names* of HttpOnly cookies, so it can refuse to shadow them locally;
- document.cookie writes go to Python as `setcookie` and are checked with
  `from_script=True` (no HttpOnly, can't overwrite or shadow one);
- script fetch/XHR that should carry cookies (same-origin, or
  `credentials: 'include'`) is sent to Python as a `fetch` message and made
  there with the jar (SameSite judged as a subresource of the page); the
  reply has no Set-Cookie headers. Other requests go straight from Deno with
  no cookies. Scripts can't set a Cookie header.
- Webinator's own loads pass `initiator` (the page that caused it, None for
  typed/favourite/Back/Reload) and `kind` ('navigate' / 'subresource') to
  `open_url`, which the jar uses for SameSite.

Old cookies were migrated as host-only, HttpOnly, Secure-on-https, 7-day.
Not done: cookies on WebSocket/EventSource handshakes (Deno's constructors
can't take headers), streaming response bodies for proxied fetches (they are
buffered, 32 MB max), and the "Lax+POST" two-minute grace some browsers have.
