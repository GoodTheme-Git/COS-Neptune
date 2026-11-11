// Webinator's JavaScript runtime.
//
// Runs inside Deno, one process per page, started by nep/web/jsengine.py with
// network access only (no files, no programs, no environment). It gives page
// scripts a small browser: a DOM, events, location/history, storage, timers,
// fetch/XHR/WebSocket. Webinator talks to it with JSON lines on stdin/stdout:
//
//   in : load {url, html, width, height, storage, cookies, httponly}
//        cookies {cookies, httponly}   fetchres {id, status, statusText, url, headers, body | error}
//        click {id, button}   input {id, value, key, char, seq}   key {id, key}
//        change {id, value}   focus {id}   resize {width, height}   history {delta}
//   out: dom {html, title, ack}  url {url, index, length}  navigate {url, method, body, target}
//        open {url}  focus {id}  alert {text}  console {level, text}  error {text}
//        storage {area, key, value}  setcookie {text}  fetch {id, url, method, headers, body}  ready {}
//
// The DOM is serialized back to HTML with data-nep="<id>" on every element;
// Webinator lays that out with its own engine and sends events by those ids.
'use strict';

const G = globalThis;
const D = G.Deno;
const NEvent = G.Event;
const NFormData = G.FormData;
const NRequest = G.Request;
const NWebSocket = G.WebSocket;
const NEventSource = G.EventSource;
const nfetch = G.fetch.bind(G);
const inspect = D.inspect;
const enc = new TextEncoder();
const XHTML = 'http://www.w3.org/1999/xhtml';
const SVGNS = 'http://www.w3.org/2000/svg';

// ---------------------------------------------------------------------------
// bridge
// ---------------------------------------------------------------------------
function send(m) {
  const b = enc.encode(JSON.stringify(m) + '\n');
  let off = 0;
  try {
    while (off < b.length) off += D.stdout.writeSync(b.subarray(off));
  } catch (_) { /* Webinator went away */ }
}
function fmt(a) {
  if (typeof a === 'string') return a;
  if (a instanceof Error) return a.stack || String(a);
  try { return inspect(a, { depth: 3, colors: false }); } catch (_) { return String(a); }
}
function report(e) {
  send({ t: 'error', text: (e && e.stack) || String(e) });
}

// Uncaught errors and rejections must never end the page.
G.addEventListener('error', (e) => { e.preventDefault(); report(e.error || e.message); });
G.addEventListener('unhandledrejection', (e) => { e.preventDefault(); report(e.reason); });

const con = {};
for (const lv of ['log', 'info', 'warn', 'error', 'debug', 'trace']) {
  con[lv] = (...a) => send({ t: 'console', level: lv, text: a.map(fmt).join(' ') });
}
const counts = {};
Object.assign(G.console, con, {
  dir: con.log, dirxml: con.log, table: con.log, group: con.log, groupCollapsed: con.log,
  groupEnd() {}, time() {}, timeEnd() {}, timeLog() {},
  assert(c, ...a) { if (!c) con.error('Assertion failed:', ...a); },
  count(l = 'default') { counts[l] = (counts[l] || 0) + 1; con.log(l + ': ' + counts[l]); },
});

// Hide that this is Deno, or libraries take their Node/Deno code paths.
for (const k of ['Deno', 'process', 'Buffer', 'global', 'Worker', 'caches']) {
  try { delete G[k]; } catch (_) { /* ignore */ }
}

let PAGE = new URL('about:blank');
function abs(u) {
  try { return new URL(String(u), PAGE.href).href; } catch (_) { return String(u); }
}

// ---------------------------------------------------------------------------
// events
// ---------------------------------------------------------------------------
const listeners = new WeakMap();      // target -> Map(type -> [entry])
const def = (o, k, v) => Object.defineProperty(o, k, { value: v, writable: true, configurable: true, enumerable: true });

class Event extends NEvent {
  constructor(type, init = {}) {
    super(type, init);
    def(this, 'target', null);
    def(this, 'currentTarget', null);
    def(this, 'srcElement', null);
    def(this, 'eventPhase', 0);
    this._stopImm = false;
    this._path = [];
  }
  stopImmediatePropagation() { this._stopImm = true; super.stopImmediatePropagation(); }
  composedPath() { return this._path.slice(); }
  initEvent(type, bubbles = false, cancelable = false) {
    def(this, 'type', type); def(this, 'bubbles', !!bubbles); def(this, 'cancelable', !!cancelable);
    if (cancelable) {
      let p = false;
      def(this, 'preventDefault', () => { p = true; });
      Object.defineProperty(this, 'defaultPrevented', { get: () => p, configurable: true });
    }
  }
}
class CustomEvent extends Event {
  constructor(type, init = {}) { super(type, init); this.detail = init.detail ?? null; }
  initCustomEvent(type, b, c, detail) { this.initEvent(type, b, c); this.detail = detail; }
}
class UIEvent extends Event {
  constructor(type, init = {}) { super(type, init); this.view = init.view ?? G; this.detail = init.detail ?? 0; this.which = init.which ?? 0; }
}
class FocusEvent extends UIEvent {
  constructor(type, init = {}) { super(type, init); this.relatedTarget = init.relatedTarget ?? null; }
}
class MouseEvent extends UIEvent {
  constructor(type, init = {}) {
    super(type, init);
    for (const k of ['screenX', 'screenY', 'clientX', 'clientY', 'pageX', 'pageY', 'offsetX', 'offsetY',
      'movementX', 'movementY', 'button', 'buttons']) this[k] = init[k] ?? 0;
    this.x = this.clientX; this.y = this.clientY;
    for (const k of ['ctrlKey', 'shiftKey', 'altKey', 'metaKey']) this[k] = !!init[k];
    this.relatedTarget = init.relatedTarget ?? null;
    this.which = this.button + 1;
  }
  getModifierState(k) { return !!{ Control: this.ctrlKey, Shift: this.shiftKey, Alt: this.altKey, Meta: this.metaKey }[k]; }
}
class PointerEvent extends MouseEvent {
  constructor(type, init = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1; this.pointerType = init.pointerType ?? 'mouse';
    this.isPrimary = init.isPrimary ?? true; this.width = 1; this.height = 1; this.pressure = init.pressure ?? 0;
    this.tiltX = 0; this.tiltY = 0; this.twist = 0; this.tangentialPressure = 0;
  }
  getCoalescedEvents() { return [this]; }
  getPredictedEvents() { return []; }
}
class WheelEvent extends MouseEvent {
  constructor(type, init = {}) { super(type, init); this.deltaX = init.deltaX ?? 0; this.deltaY = init.deltaY ?? 0; this.deltaZ = 0; this.deltaMode = 0; }
}
const KEYCODES = { Enter: 13, Escape: 27, Backspace: 8, Tab: 9, ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39,
  ArrowDown: 40, Delete: 46, ' ': 32, Home: 36, End: 35, PageUp: 33, PageDown: 34, Shift: 16, Control: 17, Alt: 18 };
class KeyboardEvent extends UIEvent {
  constructor(type, init = {}) {
    super(type, init);
    this.key = init.key ?? ''; this.code = init.code ?? '';
    this.location = 0; this.repeat = !!init.repeat; this.isComposing = false;
    for (const k of ['ctrlKey', 'shiftKey', 'altKey', 'metaKey']) this[k] = !!init[k];
    const kc = KEYCODES[this.key] ?? (this.key.length === 1 ? this.key.toUpperCase().charCodeAt(0) : 0);
    this.keyCode = init.keyCode ?? kc;
    this.charCode = type === 'keypress' && this.key.length === 1 ? this.key.charCodeAt(0) : 0;
    this.which = this.keyCode;
  }
  getModifierState(k) { return !!{ Control: this.ctrlKey, Shift: this.shiftKey, Alt: this.altKey, Meta: this.metaKey }[k]; }
}
class InputEvent extends UIEvent {
  constructor(type, init = {}) {
    super(type, init);
    this.data = init.data ?? null; this.inputType = init.inputType ?? ''; this.isComposing = false;
    this.dataTransfer = null;
  }
  getTargetRanges() { return []; }
}
class CompositionEvent extends UIEvent { constructor(t, i = {}) { super(t, i); this.data = i.data ?? ''; } }
class SubmitEvent extends Event { constructor(t, i = {}) { super(t, i); this.submitter = i.submitter ?? null; } }
class PopStateEvent extends Event { constructor(t, i = {}) { super(t, i); this.state = i.state ?? null; } }
class HashChangeEvent extends Event { constructor(t, i = {}) { super(t, i); this.oldURL = i.oldURL ?? ''; this.newURL = i.newURL ?? ''; } }
class StorageEvent extends Event {
  constructor(t, i = {}) { super(t, i); Object.assign(this, { key: null, oldValue: null, newValue: null, url: '', storageArea: null }, i); }
}
class ProgressEvent extends Event {
  constructor(t, i = {}) { super(t, i); this.lengthComputable = !!i.lengthComputable; this.loaded = i.loaded ?? 0; this.total = i.total ?? 0; }
}
class AnimationEvent extends Event { constructor(t, i = {}) { super(t, i); this.animationName = i.animationName ?? ''; this.elapsedTime = 0; } }
class TransitionEvent extends Event { constructor(t, i = {}) { super(t, i); this.propertyName = i.propertyName ?? ''; this.elapsedTime = 0; } }
class ClipboardEvent extends Event { constructor(t, i = {}) { super(t, i); this.clipboardData = i.clipboardData ?? null; } }
class DragEvent extends MouseEvent { constructor(t, i = {}) { super(t, i); this.dataTransfer = i.dataTransfer ?? null; } }

function lmap(t, create) {
  let m = listeners.get(t);
  if (!m && create) listeners.set(t, m = new Map());
  return m;
}
const ETP = {
  addEventListener(type, fn, opts) {
    if (this == null || (typeof this !== 'object' && typeof this !== 'function')) return ETP.addEventListener.call(G, type, fn, opts);
    if (!fn) return;
    const capture = typeof opts === 'boolean' ? opts : !!(opts && opts.capture);
    const once = !!(opts && typeof opts === 'object' && opts.once);
    const m = lmap(this, true);
    let arr = m.get(type);
    if (!arr) m.set(type, arr = []);
    if (arr.some((e) => e.fn === fn && e.capture === capture)) return;
    const entry = { fn, capture, once, removed: false };
    arr.push(entry);
    const sig = opts && typeof opts === 'object' && opts.signal;
    if (sig) {
      if (sig.aborted) { this.removeEventListener(type, fn, capture); return; }
      sig.addEventListener('abort', () => this.removeEventListener(type, fn, capture));
    }
  },
  removeEventListener(type, fn, opts) {
    if (this == null || (typeof this !== 'object' && typeof this !== 'function')) return ETP.removeEventListener.call(G, type, fn, opts);
    const capture = typeof opts === 'boolean' ? opts : !!(opts && opts.capture);
    const m = lmap(this, false);
    const arr = m && m.get(type);
    if (!arr) return;
    const i = arr.findIndex((e) => e.fn === fn && e.capture === capture);
    if (i >= 0) { arr[i].removed = true; arr.splice(i, 1); }
  },
  dispatchEvent(ev) { return dispatch(this == null ? G : this, ev); },
};

function parentOf(n) {
  if (n === G) return null;
  if (n instanceof Document) return n === document ? G : null;
  if (n instanceof ShadowRoot) return n.host;
  return n.parentNode || null;
}
function invoke(t, ev, phase) {
  const m = lmap(t, false);
  const arr = m && m.get(ev.type);
  if (!arr || !arr.length) return;
  ev.currentTarget = t;
  for (const e of arr.slice()) {
    if (e.removed) continue;
    if (phase === 1 && !e.capture) continue;
    if (phase === 3 && e.capture) continue;
    if (e.once) t.removeEventListener(ev.type, e.fn, e.capture);
    try {
      if (typeof e.fn === 'function') e.fn.call(t, ev);
      else if (e.fn && typeof e.fn.handleEvent === 'function') e.fn.handleEvent(ev);
    } catch (err) { report(err); }
    if (ev._stopImm) break;
  }
}
function dispatch(target, ev) {
  if (!(ev instanceof Event)) {
    // a native event (or plain object) from page code: wrap it
    const w = new Event(ev.type, { bubbles: ev.bubbles, cancelable: ev.cancelable });
    for (const k of Object.keys(ev)) { try { w[k] = ev[k]; } catch (_) { /* ignore */ } }
    ev = w;
  }
  const path = [];
  for (let p = parentOf(target); p; p = parentOf(p)) path.push(p);
  ev.target = target; ev.srcElement = target;
  ev._path = [target, ...path];
  const prevEvent = G.event;
  G.event = ev;
  for (let i = path.length - 1; i >= 0 && !ev.cancelBubble; i--) { ev.eventPhase = 1; invoke(path[i], ev, 1); }
  if (!ev.cancelBubble) { ev.eventPhase = 2; invoke(target, ev, 2); }
  if (ev.bubbles) {
    for (let i = 0; i < path.length && !ev.cancelBubble; i++) { ev.eventPhase = 3; invoke(path[i], ev, 3); }
  }
  ev.eventPhase = 0; ev.currentTarget = null;
  G.event = prevEvent;
  return !ev.defaultPrevented;
}

// on<event> handler properties
const handlers = new WeakMap();
const EVENTS = ('abort animationend animationiteration animationstart auxclick beforeinput blur cancel canplay ' +
  'canplaythrough change click close contextmenu copy cut dblclick drag dragend dragenter dragleave dragover ' +
  'dragstart drop durationchange emptied ended error focus focusin focusout gotpointercapture input invalid ' +
  'keydown keypress keyup load loadeddata loadedmetadata loadstart lostpointercapture mousedown mouseenter ' +
  'mouseleave mousemove mouseout mouseover mouseup paste pause play playing pointercancel pointerdown ' +
  'pointerenter pointerleave pointermove pointerout pointerover pointerup progress ratechange reset resize ' +
  'scroll scrollend seeked seeking select selectionchange selectstart stalled submit suspend timeupdate toggle ' +
  'transitionend transitioncancel transitionrun transitionstart volumechange waiting wheel').split(' ');
const WIN_EVENTS = 'hashchange popstate storage beforeunload unload message messageerror online offline pageshow pagehide orientationchange languagechange'.split(' ');
function setHandler(t, name, fn) {
  let h = handlers.get(t);
  if (!h) handlers.set(t, h = {});
  h[name] = typeof fn === 'function' ? fn : null;
  if (!h['@' + name]) {
    h['@' + name] = true;
    ETP.addEventListener.call(t, name, function (e) {
      const f = handlers.get(t)[name];
      if (!f) return;
      const r = f.call(t, e);
      if (r === false) e.preventDefault();
    });
  }
}
function defHandlers(obj, names) {
  for (const name of names) {
    Object.defineProperty(obj, 'on' + name, {
      configurable: true, enumerable: true,
      get() { const h = handlers.get(this); return (h && h[name]) || null; },
      set(fn) { setHandler(this, name, fn); },
    });
  }
}
function compileHandler(el, code) {
  try {
    // eslint-disable-next-line no-new-func
    return new Function('event', code);
  } catch (e) { report(e); return null; }
}

class EventTarget {}
Object.assign(EventTarget.prototype, ETP);

// ---------------------------------------------------------------------------
// DOM
// ---------------------------------------------------------------------------
let changed = false;
let mainDoc = null;
function mutated(node) {
  if (changed || !node || !mainDoc) return;
  for (let p = node; p; p = p.parentNode || (p instanceof ShadowRoot ? p.host : null)) {
    if (p === mainDoc) { changed = true; scheduleFlush(); return; }
  }
}

class NodeList extends Array {
  item(i) { return this[i] ?? null; }
  namedItem(n) { return this.find((e) => e.id === n || e.getAttribute?.('name') === n) ?? null; }
}

class Node extends EventTarget {
  constructor() {
    super();
    this.parentNode = null;
    this.childNodes = new NodeList();
    this.ownerDocument = curDoc;
  }
  get parentElement() { const p = this.parentNode; return p && p.nodeType === 1 ? p : null; }
  get firstChild() { return this.childNodes[0] ?? null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] ?? null; }
  get nextSibling() {
    const p = this.parentNode; if (!p) return null;
    return p.childNodes[p.childNodes.indexOf(this) + 1] ?? null;
  }
  get previousSibling() {
    const p = this.parentNode; if (!p) return null;
    const i = p.childNodes.indexOf(this);
    return i > 0 ? p.childNodes[i - 1] : null;
  }
  get isConnected() {
    for (let p = this; p; p = p.parentNode || (p instanceof ShadowRoot ? p.host : null)) if (p instanceof Document) return true;
    return false;
  }
  get nodeValue() { return null; }
  set nodeValue(_) { /* ignore */ }
  get baseURI() { return PAGE.href; }
  hasChildNodes() { return this.childNodes.length > 0; }
  getRootNode() { let p = this; while (p.parentNode) p = p.parentNode; return p; }
  contains(n) { for (; n; n = n.parentNode) if (n === this) return true; return false; }
  _adopt(nodes) {
    const out = [];
    for (const n of nodes) {
      if (n instanceof DocumentFragment) { out.push(...n.childNodes); for (const c of n.childNodes) c.parentNode = null; n.childNodes.length = 0; mutated(n); } else out.push(n);
    }
    for (const n of out) {
      if (n.parentNode) n.parentNode._detach(n);
      if (n === this || n.contains(this)) throw new DOMException('HierarchyRequestError', 'HierarchyRequestError');
      n.parentNode = this;
    }
    return out;
  }
  _detach(n) {
    const i = this.childNodes.indexOf(n);
    if (i >= 0) this.childNodes.splice(i, 1);
    n.parentNode = null;
    mutated(this);
  }
  insertBefore(node, ref) {
    if (ref && ref.parentNode !== this) throw new DOMException('NotFoundError', 'NotFoundError');
    const list = this._adopt([node]);
    const i = ref ? this.childNodes.indexOf(ref) : this.childNodes.length;
    this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, ...list);
    mutated(this);
    for (const n of list) connectedHooks(n);
    return node;
  }
  appendChild(node) { return this.insertBefore(node, null); }
  removeChild(node) {
    if (node.parentNode !== this) throw new DOMException('NotFoundError', 'NotFoundError');
    this._detach(node);
    if (document.activeElement && node.contains(document.activeElement)) document._active = null;
    return node;
  }
  replaceChild(node, old) { this.insertBefore(node, old); this.removeChild(old); return old; }
  _nodes(args) { return args.map((a) => (a instanceof Node ? a : (this.ownerDocument || document).createTextNode(String(a)))); }
  append(...a) { for (const n of this._nodes(a)) this.appendChild(n); }
  prepend(...a) { const f = this.firstChild; for (const n of this._nodes(a)) this.insertBefore(n, f); }
  replaceChildren(...a) { for (const c of [...this.childNodes]) this.removeChild(c); this.append(...a); }
  before(...a) { const p = this.parentNode; if (p) for (const n of this._nodes(a)) p.insertBefore(n, this); }
  after(...a) { const p = this.parentNode; if (!p) return; let r = this.nextSibling; for (const n of this._nodes(a)) p.insertBefore(n, r); }
  replaceWith(...a) { const p = this.parentNode; if (!p) return; this.before(...a); p.removeChild(this); }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get textContent() {
    let s = '';
    for (const c of this.childNodes) if (c.nodeType === 1 || c.nodeType === 3 || c.nodeType === 11) s += c.textContent;
    return s;
  }
  set textContent(v) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes.length = 0;
    v = v == null ? '' : String(v);
    if (v) this.appendChild((this.ownerDocument || document).createTextNode(v));
    mutated(this);
  }
  cloneNode(deep = false) {
    const c = this._clone();
    if (deep) for (const ch of this.childNodes) c.appendChild(ch.cloneNode(true));
    return c;
  }
  normalize() {}
  isSameNode(o) { return o === this; }
  isEqualNode(o) { return !!o && o.nodeType === this.nodeType && o.outerHTML === this.outerHTML; }
  compareDocumentPosition(o) {
    if (o === this) return 0;
    if (this.contains(o)) return 20;
    if (o.contains(this)) return 10;
    const all = [...allNodes(this.getRootNode())];
    const a = all.indexOf(this), b = all.indexOf(o);
    if (a < 0 || b < 0) return 1;
    return b > a ? 4 : 2;
  }
  lookupNamespaceURI() { return null; }
}
const NODE_TYPES = { ELEMENT_NODE: 1, ATTRIBUTE_NODE: 2, TEXT_NODE: 3, CDATA_SECTION_NODE: 4, PROCESSING_INSTRUCTION_NODE: 7,
  COMMENT_NODE: 8, DOCUMENT_NODE: 9, DOCUMENT_TYPE_NODE: 10, DOCUMENT_FRAGMENT_NODE: 11,
  DOCUMENT_POSITION_DISCONNECTED: 1, DOCUMENT_POSITION_PRECEDING: 2, DOCUMENT_POSITION_FOLLOWING: 4,
  DOCUMENT_POSITION_CONTAINS: 8, DOCUMENT_POSITION_CONTAINED_BY: 16 };
Object.assign(Node, NODE_TYPES);
Object.assign(Node.prototype, NODE_TYPES);
defHandlers(Node.prototype, EVENTS);

function* allNodes(n) {
  yield n;
  for (const c of n.childNodes) yield* allNodes(c);
}

class CharacterData extends Node {
  constructor(data) { super(); this._d = String(data); }
  get data() { return this._d; }
  set data(v) { this._d = String(v); mutated(this); }
  get nodeValue() { return this._d; }
  set nodeValue(v) { this.data = v; }
  get textContent() { return this._d; }
  set textContent(v) { this.data = v == null ? '' : v; }
  get length() { return this._d.length; }
  appendData(s) { this.data = this._d + s; }
  deleteData(o, c) { this.data = this._d.slice(0, o) + this._d.slice(o + c); }
  insertData(o, s) { this.data = this._d.slice(0, o) + s + this._d.slice(o); }
  replaceData(o, c, s) { this.data = this._d.slice(0, o) + s + this._d.slice(o + c); }
  substringData(o, c) { return this._d.substr(o, c); }
  get nextElementSibling() { return Element.prototype._nextEl.call(this); }
  get previousElementSibling() { return Element.prototype._prevEl.call(this); }
}
class Text extends CharacterData {
  get nodeType() { return 3; }
  get nodeName() { return '#text'; }
  get wholeText() { return this._d; }
  splitText(o) {
    const t = new Text(this._d.slice(o));
    this.data = this._d.slice(0, o);
    if (this.parentNode) this.parentNode.insertBefore(t, this.nextSibling);
    return t;
  }
  _clone() { return (this.ownerDocument || document).createTextNode(this._d); }
}
class Comment extends CharacterData {
  get nodeType() { return 8; }
  get nodeName() { return '#comment'; }
  _clone() { return (this.ownerDocument || document).createComment(this._d); }
}
class DocumentType extends Node {
  constructor(name) { super(); this.name = name; this.publicId = ''; this.systemId = ''; }
  get nodeType() { return 10; }
  get nodeName() { return this.name; }
  _clone() { return new DocumentType(this.name); }
}

// -- parent-node mixin (Element, Document, DocumentFragment) ------------------------------
const ParentMixin = {
  get children() { return NodeList.from(this.childNodes.filter((c) => c.nodeType === 1)); },
  get childElementCount() { return this.childNodes.filter((c) => c.nodeType === 1).length; },
  get firstElementChild() { return this.childNodes.find((c) => c.nodeType === 1) ?? null; },
  get lastElementChild() { return this.childNodes.findLast((c) => c.nodeType === 1) ?? null; },
  querySelector(s) { return qsa(this, s, true)[0] ?? null; },
  querySelectorAll(s) { return qsa(this, s, false); },
  getElementsByTagName(t) {
    t = String(t).toLowerCase();
    return NodeList.from(descendants(this).filter((e) => t === '*' || e.localName.toLowerCase() === t));
  },
  getElementsByClassName(c) {
    const want = String(c).split(/\s+/).filter(Boolean);
    return NodeList.from(descendants(this).filter((e) => { const cl = e.classList; return want.every((w) => cl.contains(w)); }));
  },
  getElementById(id) { return descendants(this).find((e) => e.getAttribute('id') === id) ?? null; },
};
function mixin(cls, obj) {
  for (const k of Object.keys(obj)) {
    Object.defineProperty(cls.prototype, k, Object.getOwnPropertyDescriptor(obj, k));
  }
}
function descendants(n, out = []) {
  for (const c of n.childNodes) if (c.nodeType === 1) { out.push(c); descendants(c, out); }
  return out;
}

// -- style ------------------------------------------------------------------------------
function kebab(p) {
  if (p.startsWith('--')) return p;
  if (p === 'cssFloat') return 'float';
  return p.replace(/^(webkit|moz|ms|o)(?=[A-Z])/, '-$1').replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
}
function camel(p) { return p.replace(/^-(webkit|moz|o)-/, '$1-').replace(/^-ms-/, 'ms-').replace(/-([a-z])/g, (_, c) => c.toUpperCase()); }
function parseStyle(text) {
  const m = new Map();
  for (const part of String(text || '').split(';')) {
    const i = part.indexOf(':');
    if (i > 0) {
      const k = part.slice(0, i).trim().toLowerCase();
      const v = part.slice(i + 1).trim();
      if (k && v) m.set(k.startsWith('--') ? part.slice(0, i).trim() : k, v);
    }
  }
  return m;
}
function makeStyle(el) {
  const map = () => {
    if (el._styleStale) { el._sm = parseStyle(el._a.get('style')); el._styleStale = false; }
    return el._sm;
  };
  const write = () => {
    const t = [...map()].map(([k, v]) => `${k}: ${v};`).join(' ');
    if (t) el._a.set('style', t); else el._a.delete('style');
    mutated(el);
  };
  const api = {
    getPropertyValue(p) { return map().get(String(p).startsWith('--') ? p : String(p).toLowerCase())?.replace(/\s*!important$/, '') ?? ''; },
    getPropertyPriority(p) { return /!important$/.test(map().get(p) || '') ? 'important' : ''; },
    setProperty(p, v, prio) {
      p = String(p).startsWith('--') ? String(p) : String(p).toLowerCase();
      if (v == null || v === '') { map().delete(p); } else map().set(p, String(v) + (prio === 'important' ? ' !important' : ''));
      write();
    },
    removeProperty(p) { const old = api.getPropertyValue(p); map().delete(p); write(); return old; },
    item(i) { return [...map().keys()][i] ?? ''; },
    get length() { return map().size; },
    get cssText() { return el._a.get('style') || ''; },
    set cssText(v) { el._sm = parseStyle(v); el._styleStale = false; write(); },
    get parentRule() { return null; },
    [Symbol.iterator]() { return map().keys(); },
  };
  return new Proxy(api, {
    get(t, k) {
      if (typeof k !== 'string' || k in t) return Reflect.get(t, k);
      if (/^\d+$/.test(k)) return t.item(+k);
      return t.getPropertyValue(kebab(k));
    },
    set(t, k, v) {
      if (k === 'cssText') { t.cssText = v; return true; }
      if (typeof k === 'string') t.setProperty(kebab(k), v);
      return true;
    },
    has(t, k) { return typeof k === 'string'; },
  });
}

class DOMTokenList {
  constructor(el, attr) { this._el = el; this._attr = attr; }
  _get() { return (this._el.getAttribute(this._attr) || '').split(/\s+/).filter(Boolean); }
  _set(a) { this._el.setAttribute(this._attr, [...new Set(a)].join(' ')); }
  get length() { return this._get().length; }
  get value() { return this._el.getAttribute(this._attr) || ''; }
  set value(v) { this._el.setAttribute(this._attr, v); }
  item(i) { return this._get()[i] ?? null; }
  contains(t) { return this._get().includes(t); }
  add(...t) { const a = this._get(); const b = a.concat(t.filter((x) => !a.includes(x))); if (b.length !== a.length) this._set(b); }
  remove(...t) { const a = this._get(); const b = a.filter((x) => !t.includes(x)); if (b.length !== a.length) this._set(b); }
  toggle(t, force) {
    const has = this.contains(t);
    const on = force === undefined ? !has : !!force;
    if (on && !has) this.add(t); else if (!on && has) this.remove(t);
    return on;
  }
  replace(a, b) { const l = this._get(); const i = l.indexOf(a); if (i < 0) return false; l[i] = b; this._set(l); return true; }
  supports(t) { return ['stylesheet', 'modulepreload', 'preload', 'icon', 'prefetch', 'noopener', 'noreferrer'].includes(t); }
  forEach(f, th) { this._get().forEach(f, th); }
  entries() { return this._get().entries(); }
  keys() { return this._get().keys(); }
  values() { return this._get().values(); }
  [Symbol.iterator]() { return this._get()[Symbol.iterator](); }
  toString() { return this.value; }
}

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr', 'keygen']);
const BOOL_ATTRS = ['disabled', 'hidden', 'readOnly', 'required', 'multiple', 'autofocus', 'noValidate', 'open',
  'async', 'defer', 'noModule', 'controls', 'autoplay', 'loop', 'muted', 'playsInline', 'inert', 'reversed', 'allowFullscreen'];
const STR_ATTRS = ['id', 'title', 'lang', 'dir', 'name', 'placeholder', 'alt', 'rel', 'target', 'download', 'role',
  'autocomplete', 'min', 'max', 'step', 'pattern', 'accept', 'enctype', 'crossOrigin', 'integrity', 'as', 'media',
  'sizes', 'srcset', 'loading', 'decoding', 'referrerPolicy', 'inputMode', 'enterKeyHint', 'slot', 'accessKey',
  'httpEquiv', 'content', 'charset', 'wrap', 'scope', 'headers', 'abbr', 'label', 'nonce', 'popover', 'translate',
  'autocapitalize', 'formAction', 'formMethod', 'dateTime', 'cite', 'coords', 'shape', 'hreflang', 'ping', 'kind', 'srclang', 'poster', 'preload'];
const attrName = (p) => ({ htmlFor: 'for', className: 'class', httpEquiv: 'http-equiv', acceptCharset: 'accept-charset' }[p] || p.toLowerCase());
const URL_ATTRS = ['href', 'src', 'action', 'formAction', 'poster', 'cite'];

class Attr {
  constructor(el, name) { this._el = el; this.name = name; this.localName = name; this.namespaceURI = null; this.prefix = null; this.specified = true; }
  get value() { return this._el.getAttribute(this.name) ?? ''; }
  set value(v) { this._el.setAttribute(this.name, v); }
  get nodeValue() { return this.value; }
  get ownerElement() { return this._el; }
  get nodeType() { return 2; }
  get nodeName() { return this.name; }
}

class Element extends Node {
  constructor(localName, ns) {
    super();
    this.localName = localName;
    this.namespaceURI = ns || XHTML;
    this._a = new Map();
    this._sm = new Map();
    this._styleStale = false;
  }
  get nodeType() { return 1; }
  get tagName() { return this.namespaceURI === XHTML ? this.localName.toUpperCase() : this.localName; }
  get nodeName() { return this.tagName; }
  get prefix() { return null; }
  // attributes
  _norm(n) { n = String(n); return this.namespaceURI === XHTML ? n.toLowerCase() : n; }
  getAttribute(n) { const v = this._a.get(this._norm(n)); return v === undefined ? null : v; }
  setAttribute(n, v) {
    n = this._norm(n);
    if (!/^[^\s"'>\/=\x00-\x1f]+$/.test(n)) throw new DOMException(`'${n}' is not a valid attribute name.`, 'InvalidCharacterError');
    v = String(v);
    if (this._a.get(n) === v) return;
    this._a.set(n, v);
    if (n === 'style') this._styleStale = true;
    else if (n.startsWith('on') && (EVENTS.includes(n.slice(2)) || WIN_EVENTS.includes(n.slice(2)))) {
      const fn = compileHandler(this, v);
      if (fn) setHandler(this, n.slice(2), fn);
    }
    mutated(this);
    this._attrChanged?.(n);
  }
  removeAttribute(n) {
    n = this._norm(n);
    if (!this._a.has(n)) return;
    this._a.delete(n);
    if (n === 'style') { this._sm = new Map(); this._styleStale = false; }
    mutated(this);
    this._attrChanged?.(n);
  }
  hasAttribute(n) { return this._a.has(this._norm(n)); }
  hasAttributes() { return this._a.size > 0; }
  toggleAttribute(n, force) {
    const has = this.hasAttribute(n);
    const on = force === undefined ? !has : !!force;
    if (on && !has) this.setAttribute(n, ''); else if (!on && has) this.removeAttribute(n);
    return on;
  }
  getAttributeNames() { return [...this._a.keys()]; }
  getAttributeNS(_ns, n) { return this.getAttribute(n.includes(':') ? n : n); }
  setAttributeNS(_ns, n, v) { this.setAttribute(n, v); }
  removeAttributeNS(_ns, n) { this.removeAttribute(n); }
  hasAttributeNS(_ns, n) { return this.hasAttribute(n); }
  getAttributeNode(n) { return this.hasAttribute(n) ? new Attr(this, this._norm(n)) : null; }
  setAttributeNode(a) { this.setAttribute(a.name, a.value); }
  get attributes() {
    const list = NodeList.from([...this._a.keys()].map((k) => new Attr(this, k)));
    list.getNamedItem = (n) => this.getAttributeNode(n);
    list.setNamedItem = (a) => this.setAttribute(a.name, a.value);
    list.removeNamedItem = (n) => this.removeAttribute(n);
    return list;
  }
  get id() { return this.getAttribute('id') || ''; }
  set id(v) { this.setAttribute('id', v); }
  get className() { return this.getAttribute('class') || ''; }
  set className(v) { this.setAttribute('class', v); }
  get classList() { return this._cl || (this._cl = new DOMTokenList(this, 'class')); }
  set classList(v) { this.setAttribute('class', v); }
  get style() { return this._st || (this._st = makeStyle(this)); }
  set style(v) { this.style.cssText = v; }
  get dataset() {
    if (this._ds) return this._ds;
    const el = this;
    const an = (k) => 'data-' + String(k).replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
    return this._ds = new Proxy({}, {
      get(_, k) { return typeof k === 'string' ? (el.getAttribute(an(k)) ?? undefined) : undefined; },
      set(_, k, v) { el.setAttribute(an(k), v); return true; },
      deleteProperty(_, k) { el.removeAttribute(an(k)); return true; },
      has(_, k) { return el.hasAttribute(an(k)); },
      ownKeys() { return el.getAttributeNames().filter((n) => n.startsWith('data-')).map((n) => camel(n.slice(5))); },
      getOwnPropertyDescriptor(_, k) { const v = el.getAttribute(an(k)); return v === null ? undefined : { value: v, enumerable: true, configurable: true, writable: true }; },
    });
  }
  // tree
  _nextEl() { for (let s = this.nextSibling; s; s = s.nextSibling) if (s.nodeType === 1) return s; return null; }
  _prevEl() { for (let s = this.previousSibling; s; s = s.previousSibling) if (s.nodeType === 1) return s; return null; }
  get nextElementSibling() { return this._nextEl(); }
  get previousElementSibling() { return this._prevEl(); }
  matches(s) { return parseSel(s).some((ch) => matchChain(this, ch, ch.length - 1, this)); }
  webkitMatchesSelector(s) { return this.matches(s); }
  msMatchesSelector(s) { return this.matches(s); }
  closest(s) { const l = parseSel(s); for (let e = this; e && e.nodeType === 1; e = e.parentNode) if (l.some((ch) => matchChain(e, ch, ch.length - 1, e))) return e; return null; }
  get innerHTML() { return serializeChildren(this, false); }
  set innerHTML(html) {
    for (const c of this.childNodes) c.parentNode = null;
    this.childNodes.length = 0;
    const target = this.localName === 'template' ? this.content : this;
    parseFragment(String(html ?? ''), target);
    mutated(this);
  }
  get outerHTML() { return serializeNode(this, false); }
  set outerHTML(html) {
    const p = this.parentNode; if (!p) return;
    const f = (this.ownerDocument || document).createDocumentFragment();
    parseFragment(String(html), f);
    p.replaceChild(f, this);
  }
  insertAdjacentHTML(pos, html) {
    const f = (this.ownerDocument || document).createDocumentFragment();
    parseFragment(String(html), f);
    this.insertAdjacentElement(pos, f);
  }
  insertAdjacentElement(pos, el) {
    switch (String(pos).toLowerCase()) {
      case 'beforebegin': this.parentNode?.insertBefore(el, this); break;
      case 'afterbegin': this.insertBefore(el, this.firstChild); break;
      case 'beforeend': this.appendChild(el); break;
      case 'afterend': this.parentNode?.insertBefore(el, this.nextSibling); break;
    }
    return el;
  }
  insertAdjacentText(pos, t) { this.insertAdjacentElement(pos, (this.ownerDocument || document).createTextNode(t)); }
  get innerText() { return textOf(this); }
  set innerText(v) { this.textContent = v; }
  get outerText() { return textOf(this); }
  _clone() {
    const c = (this.ownerDocument || document).createElementNS(this.namespaceURI, this.localName);
    for (const [k, v] of this._a) c._a.set(k, v);
    c._styleStale = this._a.has('style');
    return c;
  }
  // geometry: Webinator lays the page out itself, so these are rough
  getBoundingClientRect() {
    const w = this === document.documentElement || this === document.body ? G.innerWidth : 0;
    return new DOMRect(0, 0, w, 0);
  }
  getClientRects() { return []; }
  get offsetWidth() { return this.getBoundingClientRect().width; }
  get offsetHeight() { return 0; }
  get offsetTop() { return 0; }
  get offsetLeft() { return 0; }
  get offsetParent() { return this.parentElement; }
  get clientWidth() { return this.getBoundingClientRect().width; }
  get clientHeight() { return this === document.documentElement ? G.innerHeight : 0; }
  get clientTop() { return 0; }
  get clientLeft() { return 0; }
  get scrollWidth() { return this.clientWidth; }
  get scrollHeight() { return this.clientHeight; }
  get scrollTop() { return this._sy || 0; }
  set scrollTop(v) { this._sy = +v || 0; }
  get scrollLeft() { return this._sx || 0; }
  set scrollLeft(v) { this._sx = +v || 0; }
  scrollIntoView() {}
  scrollTo() {}
  scroll() {}
  scrollBy() {}
  focus(_opts) { if (this.matches(FOCUSABLE)) focusEl(this, true); }
  blur() { if (document._active === this) focusEl(null, true); }
  click() {
    if (this.disabled) return;
    const ev = new MouseEvent('click', { bubbles: true, cancelable: true, view: G, detail: 1 });
    activate(this, ev);
  }
  attachShadow(_o) { this.shadowRoot = new ShadowRoot(this); return this.shadowRoot; }
  animate() {
    const a = { finished: Promise.resolve(), ready: Promise.resolve(), cancel() {}, finish() {}, play() {}, pause() {},
      reverse() {}, onfinish: null, oncancel: null, playState: 'finished', currentTime: 0, addEventListener() {}, removeEventListener() {} };
    setTimeout(() => { a.onfinish?.({}); }, 0);
    return a;
  }
  getAnimations() { return []; }
  setPointerCapture() {}
  releasePointerCapture() {}
  hasPointerCapture() { return false; }
  requestFullscreen() { return Promise.reject(new Error('fullscreen is not available')); }
  checkVisibility() { return true; }
  computedStyleMap() { return new Map(); }
}
mixin(Element, ParentMixin);
// :focus targets
const FOCUSABLE = 'a[href],button,input,select,textarea,iframe,summary,[tabindex],[contenteditable]';

class HTMLElement extends Element {
  get hidden() { return this.hasAttribute('hidden'); }
  set hidden(v) { this.toggleAttribute('hidden', !!v); }
  get tabIndex() { const v = parseInt(this.getAttribute('tabindex'), 10); return Number.isNaN(v) ? (this.matches(FOCUSABLE) ? 0 : -1) : v; }
  set tabIndex(v) { this.setAttribute('tabindex', v); }
  get draggable() { return this.getAttribute('draggable') === 'true'; }
  set draggable(v) { this.setAttribute('draggable', v ? 'true' : 'false'); }
  get contentEditable() { return this.getAttribute('contenteditable') ?? 'inherit'; }
  set contentEditable(v) { this.setAttribute('contenteditable', v); }
  get isContentEditable() { return false; }
  get spellcheck() { return this.getAttribute('spellcheck') !== 'false'; }
  set spellcheck(v) { this.setAttribute('spellcheck', v ? 'true' : 'false'); }
  get accessKeyLabel() { return ''; }
  get form() { return this.closest('form'); }
  get labels() { return NodeList.from(this.id ? document.querySelectorAll(`label[for="${this.id}"]`) : []); }
  get dir() { return this.getAttribute('dir') || ''; }
  set dir(v) { this.setAttribute('dir', v); }
  attachInternals() { return { setFormValue() {}, setValidity() {}, states: new Set(), form: null }; }
  showPopover() {}
  hidePopover() {}
  togglePopover() {}
}
for (const p of STR_ATTRS) {
  if (Object.getOwnPropertyDescriptor(Element.prototype, p) || Object.getOwnPropertyDescriptor(HTMLElement.prototype, p)) continue;
  Object.defineProperty(HTMLElement.prototype, p, {
    configurable: true,
    get() { return this.getAttribute(attrName(p)) ?? ''; },
    set(v) { this.setAttribute(attrName(p), v); },
  });
}
for (const p of BOOL_ATTRS) {
  if (Object.getOwnPropertyDescriptor(HTMLElement.prototype, p)) continue;
  Object.defineProperty(HTMLElement.prototype, p, {
    configurable: true,
    get() { return this.hasAttribute(attrName(p)); },
    set(v) { this.toggleAttribute(attrName(p), !!v); },
  });
}
Object.defineProperty(HTMLElement.prototype, 'htmlFor', { configurable: true, get() { return this.getAttribute('for') ?? ''; }, set(v) { this.setAttribute('for', v); } });
for (const p of URL_ATTRS) {
  Object.defineProperty(HTMLElement.prototype, p, {
    configurable: true,
    get() { const v = this.getAttribute(attrName(p)); return v === null ? (p === 'action' ? PAGE.href : '') : abs(v); },
    set(v) { this.setAttribute(attrName(p), v); },
  });
}
for (const p of ['width', 'height', 'cols', 'rows', 'size', 'maxLength', 'minLength', 'colSpan', 'rowSpan', 'span', 'start', 'border', 'cellPadding', 'cellSpacing']) {
  Object.defineProperty(HTMLElement.prototype, p, {
    configurable: true,
    get() { const v = parseInt(this.getAttribute(attrName(p)), 10); return Number.isNaN(v) ? 0 : v; },
    set(v) { this.setAttribute(attrName(p), v); },
  });
}
// URL parts on <a> and <area>
const URL_PARTS = ['protocol', 'host', 'hostname', 'port', 'pathname', 'search', 'hash', 'origin', 'username', 'password'];

class HTMLAnchorElement extends HTMLElement {
  _url() { try { return new URL(this.getAttribute('href') ?? '', PAGE.href); } catch (_) { return null; } }
  get text() { return this.textContent; }
  set text(v) { this.textContent = v; }
  get relList() { return this._rl || (this._rl = new DOMTokenList(this, 'rel')); }
  toString() { return this.href; }
}
for (const p of URL_PARTS) {
  Object.defineProperty(HTMLAnchorElement.prototype, p, {
    configurable: true,
    get() { const u = this._url(); return u ? u[p] : ''; },
    set(v) { const u = this._url(); if (u && p !== 'origin') { u[p] = v; this.setAttribute('href', u.href); } },
  });
}
class HTMLAreaElement extends HTMLAnchorElement {}

class HTMLInputElement extends HTMLElement {
  get type() {
    const t = (this.getAttribute('type') || 'text').toLowerCase();
    return ['hidden', 'text', 'search', 'tel', 'url', 'email', 'password', 'date', 'month', 'week', 'time',
      'datetime-local', 'number', 'range', 'color', 'checkbox', 'radio', 'file', 'submit', 'image', 'reset', 'button'].includes(t) ? t : 'text';
  }
  set type(v) { this.setAttribute('type', v); }
  get value() {
    if (this._dv) return this._v;
    const a = this.getAttribute('value');
    if (a === null) return this.type === 'checkbox' || this.type === 'radio' ? 'on' : '';
    return a;
  }
  set value(v) {
    v = v == null ? '' : String(v);
    if (this.type === 'file') return;
    if (this.type === 'checkbox' || this.type === 'radio') { this.setAttribute('value', v); return; }
    if (this._dv && this._v === v) return;
    this._v = v; this._dv = true;
    this._ss = this._se = v.length;
    mutated(this);
  }
  get defaultValue() { return this.getAttribute('value') ?? ''; }
  set defaultValue(v) { this.setAttribute('value', v); }
  get valueAsNumber() { const n = parseFloat(this.value); return Number.isNaN(n) ? NaN : n; }
  set valueAsNumber(n) { this.value = String(n); }
  get valueAsDate() { const d = new Date(this.value); return Number.isNaN(+d) ? null : d; }
  set valueAsDate(d) { this.value = d ? d.toISOString().slice(0, 10) : ''; }
  get checked() { return this._dc ? this._c : this.hasAttribute('checked'); }
  set checked(v) {
    v = !!v;
    if (this._dc && this._c === v) return;
    this._c = v; this._dc = true;
    if (v && this.type === 'radio' && this.name) {
      const root = this.form || this.getRootNode();
      for (const o of descendants(root)) {
        if (o !== this && o instanceof HTMLInputElement && o.type === 'radio' && o.name === this.name) { o._c = false; o._dc = true; }
      }
    }
    mutated(this);
  }
  get defaultChecked() { return this.hasAttribute('checked'); }
  set defaultChecked(v) { this.toggleAttribute('checked', !!v); }
  get indeterminate() { return !!this._ind; }
  set indeterminate(v) { this._ind = !!v; }
  get selectionStart() { return this._ss ?? this.value.length; }
  set selectionStart(v) { this._ss = v; }
  get selectionEnd() { return this._se ?? this.value.length; }
  set selectionEnd(v) { this._se = v; }
  get selectionDirection() { return 'none'; }
  setSelectionRange(a, b) { this._ss = a; this._se = b; }
  setRangeText(t) { this.value = t; }
  select() { this._ss = 0; this._se = this.value.length; }
  get files() { return this._files || (this._files = Object.assign([], { item(i) { return this[i] ?? null; } })); }
  set files(_) {}
  get list() { const id = this.getAttribute('list'); return id ? document.getElementById(id) : null; }
  showPicker() {}
  stepUp() {}
  stepDown() {}
}
const Validity = { validity: { valid: true, valueMissing: false, typeMismatch: false, patternMismatch: false, tooLong: false,
  tooShort: false, rangeUnderflow: false, rangeOverflow: false, stepMismatch: false, badInput: false, customError: false } };
const ValidityMixin = {
  get validity() { return Validity.validity; },
  get validationMessage() { return ''; },
  get willValidate() { return true; },
  checkValidity() { return true; },
  reportValidity() { return true; },
  setCustomValidity() {},
};

class HTMLTextAreaElement extends HTMLElement {
  get type() { return 'textarea'; }
  get value() { return this._dv ? this._v : this.textContent; }
  set value(v) {
    v = v == null ? '' : String(v);
    if (this._dv && this._v === v) return;
    this._v = v; this._dv = true; this._ss = this._se = v.length;
    mutated(this);
  }
  get defaultValue() { return this.textContent; }
  set defaultValue(v) { this.textContent = v; }
  get textLength() { return this.value.length; }
  get selectionStart() { return this._ss ?? this.value.length; }
  set selectionStart(v) { this._ss = v; }
  get selectionEnd() { return this._se ?? this.value.length; }
  set selectionEnd(v) { this._se = v; }
  setSelectionRange(a, b) { this._ss = a; this._se = b; }
  setRangeText(t) { this.value = t; }
  select() {}
}

class HTMLOptionElement extends HTMLElement {
  get value() { return this.getAttribute('value') ?? this.text; }
  set value(v) { this.setAttribute('value', v); }
  get text() { return this.textContent.replace(/\s+/g, ' ').trim(); }
  set text(v) { this.textContent = v; }
  get label() { return this.getAttribute('label') ?? this.text; }
  set label(v) { this.setAttribute('label', v); }
  get _select() { for (let p = this.parentNode; p; p = p.parentNode) if (p instanceof HTMLSelectElement) return p; return null; }
  get selected() {
    if (this._ds2) return this._sel;
    if (this.hasAttribute('selected')) return true;
    const s = this._select;
    if (s && !s.multiple) {
      // nothing explicitly selected: the first option is
      const opts = s._opts();
      if (!opts.some((o) => o._ds2 ? o._sel : o.hasAttribute('selected'))) return opts[0] === this;
    }
    return false;
  }
  set selected(v) {
    v = !!v;
    const s = this._select;
    if (v && s && !s.multiple) for (const o of s._opts()) { o._sel = false; o._ds2 = true; }
    this._sel = v; this._ds2 = true;
    mutated(this);
  }
  get defaultSelected() { return this.hasAttribute('selected'); }
  set defaultSelected(v) { this.toggleAttribute('selected', !!v); }
  get index() { const s = this._select; return s ? s._opts().indexOf(this) : 0; }
  get form() { return this._select?.form ?? null; }
}
class HTMLSelectElement extends HTMLElement {
  get type() { return this.multiple ? 'select-multiple' : 'select-one'; }
  _opts() { return NodeList.from(descendants(this).filter((e) => e instanceof HTMLOptionElement)); }
  get options() {
    const o = this._opts();
    o.selectedIndex = o.findIndex((x) => x.selected);
    o.add = (opt, before) => this.add(opt, before);
    o.remove = (i) => this.remove(i);
    return o;
  }
  get length() { return this.options.length; }
  set length(n) { const o = this.options; for (let i = o.length - 1; i >= n; i--) o[i].remove(); }
  get selectedOptions() { return NodeList.from(this.options.filter((o) => o.selected)); }
  get selectedIndex() { return this._opts().findIndex((o) => o.selected); }
  set selectedIndex(i) { const o = this.options; o.forEach((x, j) => { x._sel = j === +i; x._ds2 = true; }); mutated(this); }
  get value() { const o = this.options.find((x) => x.selected); return o ? o.value : ''; }
  set value(v) { v = String(v); const o = this.options; let hit = false; o.forEach((x) => { x._sel = !hit && x.value === v; if (x._sel) hit = true; x._ds2 = true; }); mutated(this); }
  item(i) { return this.options[i] ?? null; }
  namedItem(n) { return this.options.find((o) => o.id === n || o.getAttribute('name') === n) ?? null; }
  add(opt, before) { this.insertBefore(opt, typeof before === 'number' ? this.options[before] ?? null : before ?? null); }
  remove(i) { if (i === undefined) { super.remove(); return; } this.options[i]?.remove(); }
}
class HTMLButtonElement extends HTMLElement {
  get type() { const t = (this.getAttribute('type') || 'submit').toLowerCase(); return ['submit', 'reset', 'button'].includes(t) ? t : 'submit'; }
  set type(v) { this.setAttribute('type', v); }
  get value() { return this.getAttribute('value') ?? ''; }
  set value(v) { this.setAttribute('value', v); }
}
for (const C of [HTMLInputElement, HTMLTextAreaElement, HTMLSelectElement, HTMLButtonElement]) mixin(C, ValidityMixin);

class HTMLFormElement extends HTMLElement {
  get elements() {
    const l = NodeList.from(descendants(this).filter((e) => ['input', 'select', 'textarea', 'button', 'fieldset', 'output', 'object'].includes(e.localName)));
    return new Proxy(l, { get(t, k) { if (typeof k === 'string' && !(k in t) && !/^\d+$/.test(k)) return t.namedItem(k) ?? undefined; return Reflect.get(t, k); } });
  }
  get length() { return this.elements.length; }
  get method() { const m = (this.getAttribute('method') || 'get').toLowerCase(); return m === 'post' || m === 'dialog' ? m : 'get'; }
  set method(v) { this.setAttribute('method', v); }
  get encoding() { return this.enctype; }
  submit() { submitForm(this, null); }
  requestSubmit(submitter) {
    const ev = new SubmitEvent('submit', { bubbles: true, cancelable: true, submitter: submitter ?? null });
    if (this.dispatchEvent(ev)) submitForm(this, submitter ?? null);
  }
  reset() {
    if (!this.dispatchEvent(new Event('reset', { bubbles: true, cancelable: true }))) return;
    for (const e of descendants(this)) {
      if (e instanceof HTMLInputElement) { e._dv = false; e._dc = false; mutated(e); } else if (e instanceof HTMLTextAreaElement) { e._dv = false; mutated(e); } else if (e instanceof HTMLOptionElement) { e._ds2 = false; mutated(e); }
    }
  }
}
// form.<name> access
const formProxyHandler = {
  get(t, k, r) {
    if (typeof k === 'string' && !(k in t)) {
      const hit = t.elements.namedItem?.(k);
      if (hit) return hit;
    }
    return Reflect.get(t, k, r);
  },
};
class HTMLImageElement extends HTMLElement {
  get complete() { return true; }
  get naturalWidth() { return 0; }
  get naturalHeight() { return 0; }
  get currentSrc() { return this.src; }
  decode() { return Promise.resolve(); }
}
class HTMLScriptElement extends HTMLElement {
  get type() { return this.getAttribute('type') ?? ''; }
  set type(v) { this.setAttribute('type', v); }
  get text() { return this.textContent; }
  set text(v) { this.textContent = v; }
  static supports(t) { return t === 'classic' || t === 'module'; }
}
class HTMLLinkElement extends HTMLElement {
  get relList() { return this._rl || (this._rl = new DOMTokenList(this, 'rel')); }
  get sheet() { return null; }
}
class CSSStyleSheet {
  constructor(owner) { this.ownerNode = owner || null; this.cssRules = []; this.disabled = false; this.type = 'text/css'; this.media = []; }
  get rules() { return this.cssRules; }
  insertRule(text, i = 0) { this.cssRules.splice(i, 0, { cssText: String(text), type: 1, selectorText: String(text).split('{')[0].trim() }); mutated(this.ownerNode); return i; }
  deleteRule(i) { this.cssRules.splice(i, 1); mutated(this.ownerNode); }
  addRule(s, b, i) { return this.insertRule(`${s} { ${b} }`, i ?? this.cssRules.length); }
  removeRule(i) { this.deleteRule(i); }
  replace(t) { this.replaceSync(t); return Promise.resolve(this); }
  replaceSync(t) { this.cssRules = [{ cssText: String(t), type: 1 }]; mutated(this.ownerNode); }
}
class HTMLStyleElement extends HTMLElement {
  get sheet() { return this._sheet || (this._sheet = new CSSStyleSheet(this)); }
}
class HTMLTemplateElement extends HTMLElement {
  get content() { return this._content || (this._content = (this.ownerDocument || document).createDocumentFragment()); }
}
class HTMLMediaElement extends HTMLElement {
  play() { return Promise.reject(new DOMException('Webinator cannot play media from scripts', 'NotAllowedError')); }
  pause() {}
  load() {}
  canPlayType() { return ''; }
  get paused() { return true; }
  get currentTime() { return 0; }
  set currentTime(_) {}
  get duration() { return NaN; }
  get volume() { return 1; }
  set volume(_) {}
  get readyState() { return 0; }
}
class HTMLAudioElement extends HTMLMediaElement {}
class HTMLVideoElement extends HTMLMediaElement {}
class HTMLCanvasElement extends HTMLElement {
  getContext() { return null; }
  toDataURL() { return 'data:,'; }
  toBlob(cb) { cb(null); }
}
class HTMLIFrameElement extends HTMLElement {
  get contentWindow() { return null; }
  get contentDocument() { return null; }
}
class HTMLLabelElement extends HTMLElement {
  get control() {
    const f = this.getAttribute('for');
    if (f) return document.getElementById(f);
    return this.querySelector('input,select,textarea,button');
  }
}
class HTMLDetailsElement extends HTMLElement {}
class HTMLDialogElement extends HTMLElement {
  show() { this.open = true; }
  showModal() { this.open = true; }
  close(rv) { this.open = false; this.returnValue = rv ?? ''; this.dispatchEvent(new Event('close')); }
}
class HTMLUnknownElement extends HTMLElement {}
class SVGElement extends Element {
  get className() { return { baseVal: this.getAttribute('class') || '', animVal: this.getAttribute('class') || '' }; }
  set className(v) { this.setAttribute('class', v); }
  get ownerSVGElement() { return this.closest('svg'); }
  get viewportElement() { return this.ownerSVGElement; }
  getBBox() { return { x: 0, y: 0, width: 0, height: 0 }; }
  getTotalLength() { return 0; }
  get tabIndex() { return -1; }
  focus() {}
  blur() {}
}
class SVGSVGElement extends SVGElement {
  createSVGPoint() { return { x: 0, y: 0, matrixTransform() { return this; } }; }
  getScreenCTM() { return null; }
}
const simple = (n) => ({ [n]: class extends HTMLElement {} })[n];
const HTMLDivElement = simple('HTMLDivElement');
const HTMLSpanElement = simple('HTMLSpanElement');
const HTMLParagraphElement = simple('HTMLParagraphElement');
const HTMLHeadingElement = simple('HTMLHeadingElement');
const HTMLBodyElement = simple('HTMLBodyElement');
const HTMLHeadElement = simple('HTMLHeadElement');
const HTMLHtmlElement = simple('HTMLHtmlElement');
const HTMLMetaElement = simple('HTMLMetaElement');
const HTMLTitleElement = simple('HTMLTitleElement');
const HTMLUListElement = simple('HTMLUListElement');
const HTMLOListElement = simple('HTMLOListElement');
const HTMLLIElement = simple('HTMLLIElement');
const HTMLTableElement = simple('HTMLTableElement');
const HTMLTableRowElement = simple('HTMLTableRowElement');
const HTMLTableCellElement = simple('HTMLTableCellElement');
const HTMLTableSectionElement = simple('HTMLTableSectionElement');
const HTMLPreElement = simple('HTMLPreElement');
const HTMLBRElement = simple('HTMLBRElement');
const HTMLHRElement = simple('HTMLHRElement');
const HTMLFieldSetElement = simple('HTMLFieldSetElement');
const HTMLLegendElement = simple('HTMLLegendElement');
const HTMLBaseElement = simple('HTMLBaseElement');
const HTMLPictureElement = simple('HTMLPictureElement');
const HTMLSourceElement = simple('HTMLSourceElement');
const HTMLSlotElement = simple('HTMLSlotElement');
const HTMLOutputElement = simple('HTMLOutputElement');
const HTMLProgressElement = simple('HTMLProgressElement');
const HTMLMeterElement = simple('HTMLMeterElement');
const HTMLQuoteElement = simple('HTMLQuoteElement');
const HTMLTimeElement = simple('HTMLTimeElement');
const HTMLDataListElement = simple('HTMLDataListElement');
const HTMLOptGroupElement = simple('HTMLOptGroupElement');
const HTMLEmbedElement = simple('HTMLEmbedElement');
const HTMLObjectElement = simple('HTMLObjectElement');

const TAGS = {
  a: HTMLAnchorElement, area: HTMLAreaElement, input: HTMLInputElement, textarea: HTMLTextAreaElement,
  select: HTMLSelectElement, option: HTMLOptionElement, button: HTMLButtonElement, form: HTMLFormElement,
  img: HTMLImageElement, script: HTMLScriptElement, link: HTMLLinkElement, style: HTMLStyleElement,
  template: HTMLTemplateElement, audio: HTMLAudioElement, video: HTMLVideoElement, canvas: HTMLCanvasElement,
  iframe: HTMLIFrameElement, label: HTMLLabelElement, details: HTMLDetailsElement, dialog: HTMLDialogElement,
  div: HTMLDivElement, span: HTMLSpanElement, p: HTMLParagraphElement, body: HTMLBodyElement, head: HTMLHeadElement,
  html: HTMLHtmlElement, meta: HTMLMetaElement, title: HTMLTitleElement, ul: HTMLUListElement, ol: HTMLOListElement,
  li: HTMLLIElement, table: HTMLTableElement, tr: HTMLTableRowElement, td: HTMLTableCellElement, th: HTMLTableCellElement,
  thead: HTMLTableSectionElement, tbody: HTMLTableSectionElement, tfoot: HTMLTableSectionElement, pre: HTMLPreElement,
  br: HTMLBRElement, hr: HTMLHRElement, fieldset: HTMLFieldSetElement, legend: HTMLLegendElement, base: HTMLBaseElement,
  picture: HTMLPictureElement, source: HTMLSourceElement, slot: HTMLSlotElement, output: HTMLOutputElement,
  progress: HTMLProgressElement, meter: HTMLMeterElement, blockquote: HTMLQuoteElement, q: HTMLQuoteElement,
  time: HTMLTimeElement, datalist: HTMLDataListElement, optgroup: HTMLOptGroupElement, embed: HTMLEmbedElement,
  object: HTMLObjectElement, h1: HTMLHeadingElement, h2: HTMLHeadingElement, h3: HTMLHeadingElement,
  h4: HTMLHeadingElement, h5: HTMLHeadingElement, h6: HTMLHeadingElement,
};
const KNOWN = new Set(('abbr address article aside b bdi bdo big center cite code col colgroup dd del dfn dl dt em ' +
  'figcaption figure font footer header hgroup i ins kbd main map mark menu nav noscript rp rt ruby s samp search ' +
  'section small strike strong sub summary sup tt u var wbr caption').split(' '));

const customRegistry = new Map();
const customElements = {
  define(name, cls) { customRegistry.set(name, cls); },
  get(name) { return customRegistry.get(name); },
  whenDefined(name) { return Promise.resolve(customRegistry.get(name)); },
  upgrade() {},
  getName(cls) { for (const [k, v] of customRegistry) if (v === cls) return k; return null; },
};

class DocumentFragment extends Node {
  get nodeType() { return 11; }
  get nodeName() { return '#document-fragment'; }
  _clone() { return (this.ownerDocument || document).createDocumentFragment(); }
  get innerHTML() { return serializeChildren(this, false); }
}
mixin(DocumentFragment, ParentMixin);
class ShadowRoot extends DocumentFragment {
  constructor(host) { super(); this.host = host; this.mode = 'open'; }
  get activeElement() { return null; }
  set innerHTML(h) { for (const c of this.childNodes) c.parentNode = null; this.childNodes.length = 0; parseFragment(String(h), this); mutated(this); }
  get innerHTML() { return serializeChildren(this, false); }
}

class DOMRect {
  constructor(x = 0, y = 0, w = 0, h = 0) { this.x = x; this.y = y; this.width = w; this.height = h; }
  get left() { return this.x; }
  get top() { return this.y; }
  get right() { return this.x + this.width; }
  get bottom() { return this.y + this.height; }
  toJSON() { return { x: this.x, y: this.y, width: this.width, height: this.height, left: this.left, top: this.top, right: this.right, bottom: this.bottom }; }
  static fromRect(r = {}) { return new DOMRect(r.x, r.y, r.width, r.height); }
}

class Range {
  constructor() { this.startContainer = this.endContainer = document; this.startOffset = this.endOffset = 0; this.collapsed = true; this.commonAncestorContainer = document; }
  setStart(n, o) { this.startContainer = n; this.startOffset = o; }
  setEnd(n, o) { this.endContainer = n; this.endOffset = o; }
  setStartBefore(n) { this.startContainer = n; }
  setStartAfter(n) { this.startContainer = n; }
  setEndBefore(n) { this.endContainer = n; }
  setEndAfter(n) { this.endContainer = n; }
  selectNode(n) { this.startContainer = this.endContainer = n; }
  selectNodeContents(n) { this.startContainer = this.endContainer = n; }
  collapse() { this.collapsed = true; }
  cloneRange() { return Object.assign(new Range(), this); }
  cloneContents() { return document.createDocumentFragment(); }
  deleteContents() {}
  extractContents() { return document.createDocumentFragment(); }
  insertNode(n) { this.startContainer?.appendChild?.(n); }
  surroundContents() {}
  detach() {}
  getBoundingClientRect() { return new DOMRect(); }
  getClientRects() { return []; }
  toString() { return ''; }
  createContextualFragment(html) { const f = document.createDocumentFragment(); parseFragment(String(html), f); return f; }
  compareBoundaryPoints() { return 0; }
  isPointInRange() { return false; }
  intersectsNode() { return false; }
}
const selection = {
  rangeCount: 0, isCollapsed: true, type: 'None', anchorNode: null, focusNode: null, anchorOffset: 0, focusOffset: 0,
  getRangeAt() { return new Range(); }, addRange() {}, removeAllRanges() {}, removeRange() {}, empty() {},
  collapse() {}, collapseToStart() {}, collapseToEnd() {}, extend() {}, selectAllChildren() {}, setBaseAndExtent() {},
  containsNode() { return false; }, deleteFromDocument() {}, modify() {}, toString() { return ''; },
};

class TreeWalker {
  constructor(root, what = 0xFFFFFFFF, filter = null) { this.root = root; this.whatToShow = what; this.filter = filter; this.currentNode = root; this._all = null; }
  _list() {
    const out = [];
    const ok = (n) => {
      const bit = 1 << (n.nodeType - 1);
      if (!(this.whatToShow & bit)) return false;
      const f = this.filter;
      const r = f ? (typeof f === 'function' ? f(n) : f.acceptNode(n)) : 1;
      return r === 1;
    };
    for (const n of allNodes(this.root)) if (n !== this.root && ok(n)) out.push(n);
    return out;
  }
  nextNode() { const l = this._list(); const i = l.indexOf(this.currentNode); const n = l[i + 1] ?? null; if (n) this.currentNode = n; return n; }
  previousNode() { const l = this._list(); const i = l.indexOf(this.currentNode); const n = i > 0 ? l[i - 1] : null; if (n) this.currentNode = n; return n; }
  firstChild() { return this.nextNode(); }
  parentNode() { const p = this.currentNode.parentNode; if (p && p !== this.root) { this.currentNode = p; return p; } return null; }
  nextSibling() { const s = this.currentNode.nextSibling; if (s) this.currentNode = s; return s; }
}
const NodeFilter = { FILTER_ACCEPT: 1, FILTER_REJECT: 2, FILTER_SKIP: 3, SHOW_ALL: 0xFFFFFFFF, SHOW_ELEMENT: 1, SHOW_TEXT: 4, SHOW_COMMENT: 128 };

let curDoc = null;
class Document extends Node {
  constructor() {
    super();
    this.ownerDocument = null;
    this._active = null;
    this.readyState = 'loading';
    const save = curDoc;
    curDoc = this;
    this.doctype = null;
    const html = this.createElement('html');
    html.appendChild(this.createElement('head'));
    html.appendChild(this.createElement('body'));
    this.childNodes.push(html);
    html.parentNode = this;
    curDoc = save || this;
    this.implementation = {
      createHTMLDocument: (t) => { const d = new Document(); if (t !== undefined) d.title = t; return d; },
      createDocument: () => new Document(),
      createDocumentType: (n) => new DocumentType(n),
      hasFeature: () => true,
    };
  }
  get nodeType() { return 9; }
  get nodeName() { return '#document'; }
  get documentElement() { return this.childNodes.find((c) => c.nodeType === 1) ?? null; }
  get head() { return this.documentElement?.childNodes.find((c) => c.localName === 'head') ?? null; }
  get body() { return this.documentElement?.childNodes.find((c) => c.localName === 'body' || c.localName === 'frameset') ?? null; }
  set body(b) { const old = this.body; if (old) this.documentElement.replaceChild(b, old); else this.documentElement.appendChild(b); }
  get title() { const t = this.querySelector('title'); return t ? t.textContent.replace(/\s+/g, ' ').trim() : ''; }
  set title(v) {
    let t = this.querySelector('title');
    if (!t) { t = this.createElement('title'); this.head?.appendChild(t); }
    t.textContent = v;
  }
  get URL() { return PAGE.href; }
  get documentURI() { return PAGE.href; }
  get location() { return this === document ? G.location : null; }
  set location(v) { G.location.href = v; }
  get defaultView() { return this === document ? G : null; }
  get activeElement() {
    const a = this._active;
    return a && a.isConnected ? a : this.body;
  }
  get domain() { return PAGE.hostname; }
  get referrer() { return ''; }
  get cookie() { return cookieString(); }
  set cookie(v) { setCookie(String(v)); }
  get characterSet() { return 'UTF-8'; }
  get charset() { return 'UTF-8'; }
  get inputEncoding() { return 'UTF-8'; }
  get contentType() { return 'text/html'; }
  get compatMode() { return 'CSS1Compat'; }
  get visibilityState() { return 'visible'; }
  get hidden() { return false; }
  get scrollingElement() { return this.documentElement; }
  get styleSheets() { return NodeList.from(this.querySelectorAll('style').map((s) => s.sheet)); }
  get forms() { return this.getElementsByTagName('form'); }
  get images() { return this.getElementsByTagName('img'); }
  get links() { return NodeList.from(this.querySelectorAll('a[href],area[href]')); }
  get scripts() { return this.getElementsByTagName('script'); }
  get fonts() { return fontSet; }
  get dir() { return this.documentElement?.getAttribute('dir') || ''; }
  get lastModified() { return new Date().toUTCString(); }
  get fullscreenElement() { return null; }
  get fullscreenEnabled() { return false; }
  get pointerLockElement() { return null; }
  get currentScript() { return this._cs || null; }
  get adoptedStyleSheets() { return this._ass || (this._ass = []); }
  set adoptedStyleSheets(v) { this._ass = v; }
  getElementsByName(n) { return NodeList.from(descendants(this).filter((e) => e.getAttribute('name') === n)); }
  createElement(tag, opts) {
    tag = String(tag);
    const t = tag.toLowerCase();
    const C = TAGS[t] || (KNOWN.has(t) || t.includes('-') ? HTMLElement : HTMLUnknownElement);
    const save = curDoc; curDoc = this;
    let el;
    const Custom = customRegistry.get(t);
    if (Custom) {
      try { el = new Custom(); el.localName = t; el.namespaceURI = XHTML; el._a = el._a || new Map(); } catch (_) { el = new HTMLElement(t); }
    } else el = new C(t);
    curDoc = save;
    el.ownerDocument = this;
    if (t === 'form') return new Proxy(el, formProxyHandler);
    return el;
  }
  createElementNS(ns, qname) {
    if (!ns || ns === XHTML) return this.createElement(qname);
    const save = curDoc; curDoc = this;
    const local = String(qname).split(':').pop();
    const el = ns === SVGNS ? (local === 'svg' ? new SVGSVGElement(local, ns) : new SVGElement(local, ns)) : new Element(local, ns);
    curDoc = save;
    el.ownerDocument = this;
    return el;
  }
  createTextNode(t) { const save = curDoc; curDoc = this; const n = new Text(t); curDoc = save; return n; }
  createComment(t) { const save = curDoc; curDoc = this; const n = new Comment(t); curDoc = save; return n; }
  createDocumentFragment() { const save = curDoc; curDoc = this; const n = new DocumentFragment(); curDoc = save; return n; }
  createAttribute(n) { const holder = this.createElement('span'); holder.setAttribute(n, ''); return new Attr(holder, n); }
  createEvent(kind) {
    const C = { mouseevents: MouseEvent, mouseevent: MouseEvent, keyboardevent: KeyboardEvent, uievent: UIEvent,
      uievents: UIEvent, customevent: CustomEvent, focusevent: FocusEvent }[String(kind).toLowerCase()] || Event;
    return new C('');
  }
  createRange() { return new Range(); }
  createTreeWalker(root, what, filter) { return new TreeWalker(root, what, filter); }
  createNodeIterator(root, what, filter) { return new TreeWalker(root, what, filter); }
  importNode(n, deep) { return n.cloneNode(deep); }
  adoptNode(n) { n.remove(); n.ownerDocument = this; return n; }
  getSelection() { return selection; }
  hasFocus() { return true; }
  elementFromPoint() { return null; }
  elementsFromPoint() { return []; }
  caretRangeFromPoint() { return null; }
  execCommand() { return false; }
  queryCommandSupported() { return false; }
  queryCommandState() { return false; }
  exitFullscreen() { return Promise.resolve(); }
  exitPointerLock() {}
  open() { return this; }
  close() {}
  write(...a) { docWrite(this, a.join('')); }
  writeln(...a) { docWrite(this, a.join('') + '\n'); }
  startViewTransition(cb) {
    const p = Promise.resolve().then(() => cb?.());
    return { finished: p, ready: p, updateCallbackDone: p, skipTransition() {} };
  }
  _clone() { return new Document(); }
}
mixin(Document, ParentMixin);
const fontSet = Object.assign(new Set(), {
  ready: Promise.resolve(), status: 'loaded', check: () => true, load: () => Promise.resolve([]),
  addEventListener() {}, removeEventListener() {}, onloadingdone: null,
});
fontSet.ready = Promise.resolve(fontSet);

function textOf(n) {
  if (n.nodeType === 3) return n.data;
  if (n.nodeType !== 1 && n.nodeType !== 11 && n.nodeType !== 9) return '';
  if (n.localName === 'script' || n.localName === 'style' || n.localName === 'template') return '';
  if (n.localName === 'br') return '\n';
  let s = '';
  for (const c of n.childNodes) s += textOf(c);
  return s;
}

// ---------------------------------------------------------------------------
// selectors
// ---------------------------------------------------------------------------
const selCache = new Map();
function parseSel(src) {
  src = String(src);
  let hit = selCache.get(src);
  if (hit) return hit;
  let i = 0;
  const n = src.length;
  const ws = () => { while (i < n && /\s/.test(src[i])) i++; };
  const ident = () => {
    let s = '';
    while (i < n) {
      const c = src[i];
      if (/[\w\- -￿]/.test(c)) { s += c; i++; } else if (c === '\\') { s += src[i + 1] ?? ''; i += 2; } else break;
    }
    return s;
  };
  const str = () => {
    const q = src[i];
    if (q === '"' || q === "'") {
      i++;
      let s = '';
      while (i < n && src[i] !== q) { if (src[i] === '\\') i++; s += src[i++]; }
      i++;
      return s;
    }
    let s = '';
    while (i < n && !/[\s\]]/.test(src[i])) s += src[i++];
    return s;
  };
  const compound = () => {
    const c = { tag: null, id: null, cls: [], attrs: [], ps: [] };
    let any = false;
    if (src[i] === '*') { i++; any = true; } else if (/[\w-]/.test(src[i] || '')) { c.tag = ident().toLowerCase(); any = true; }
    for (;;) {
      const ch = src[i];
      if (ch === '#') { i++; c.id = ident(); any = true; } else if (ch === '.') { i++; c.cls.push(ident()); any = true; } else if (ch === '[') {
        i++; ws();
        const name = ident().toLowerCase();
        ws();
        let op = null, val = null, ci = false;
        if (src[i] !== ']') {
          op = '';
          while ('~|^$*='.includes(src[i])) op += src[i++];
          ws(); val = str(); ws();
          if (/[iIsS]/.test(src[i] || '') && src[i + 1] !== undefined) { ci = src[i].toLowerCase() === 'i'; i++; ws(); }
        }
        if (src[i] === ']') i++;
        c.attrs.push({ name, op, val, ci });
        any = true;
      } else if (ch === ':') {
        i++;
        let el = false;
        if (src[i] === ':') { i++; el = true; }
        const name = ident().toLowerCase();
        let arg = null;
        if (src[i] === '(') {
          i++;
          let depth = 1;
          const st = i;
          while (i < n && depth) { if (src[i] === '(') depth++; else if (src[i] === ')') depth--; i++; }
          arg = src.slice(st, i - 1).trim();
        }
        const sub = ['not', 'is', 'where', 'matches', '-webkit-any', 'any'].includes(name) && arg ? parseSel(arg) : null;
        c.ps.push({ name: el ? '::' + name : name, arg, sub });
        any = true;
      } else break;
    }
    return any ? c : null;
  };
  const list = [];
  let chain = [];
  let comb = null;
  ws();
  while (i < n) {
    const c = compound();
    if (!c) throw new DOMException(`'${src}' is not a valid selector`, 'SyntaxError');
    chain.push({ comb, c });
    const before = i;
    ws();
    const ch = src[i];
    if (ch === ',') { i++; ws(); list.push(chain); chain = []; comb = null; continue; }
    if (ch === '>' || ch === '+' || ch === '~') { comb = ch; i++; ws(); continue; }
    if (i >= n) break;
    if (i === before) throw new DOMException(`'${src}' is not a valid selector`, 'SyntaxError');
    comb = ' ';
  }
  if (chain.length) list.push(chain);
  if (selCache.size > 2000) selCache.clear();
  selCache.set(src, list);
  return list;
}
function nth(arg, idx) {
  arg = (arg || '').split(/\s+of\s+/)[0].replace(/\s+/g, '').toLowerCase();
  let a = 0, b = 0;
  if (arg === 'odd') { a = 2; b = 1; } else if (arg === 'even') { a = 2; b = 0; } else if (arg.includes('n')) {
    const [x, y] = arg.split('n');
    a = x === '' || x === '+' ? 1 : x === '-' ? -1 : parseInt(x, 10);
    b = y ? parseInt(y, 10) : 0;
  } else b = parseInt(arg, 10);
  if (a === 0) return idx === b;
  return (idx - b) / a >= 0 && (idx - b) % a === 0;
}
function siblingsOf(el) { return el.parentNode ? el.parentNode.childNodes.filter((c) => c.nodeType === 1) : [el]; }
function matchComp(el, c, scope) {
  if (c.tag && el.localName.toLowerCase() !== c.tag) return false;
  if (c.id !== null && el.getAttribute('id') !== c.id) return false;
  if (c.cls.length) {
    const cl = (el.getAttribute('class') || '').split(/\s+/);
    for (const x of c.cls) if (!cl.includes(x)) return false;
  }
  for (const a of c.attrs) {
    let v = el.getAttribute(a.name);
    if (v === null) return false;
    if (a.op === null) continue;
    let w = a.val;
    if (a.ci) { v = v.toLowerCase(); w = w.toLowerCase(); }
    switch (a.op) {
      case '=': if (v !== w) return false; break;
      case '~=': if (!v.split(/\s+/).includes(w)) return false; break;
      case '|=': if (v !== w && !v.startsWith(w + '-')) return false; break;
      case '^=': if (!w || !v.startsWith(w)) return false; break;
      case '$=': if (!w || !v.endsWith(w)) return false; break;
      case '*=': if (!w || !v.includes(w)) return false; break;
      default: return false;
    }
  }
  for (const p of c.ps) {
    switch (p.name) {
      case 'not': if (p.sub.some((ch) => matchChain(el, ch, ch.length - 1, scope))) return false; break;
      case 'is': case 'where': case 'matches': case 'any': case '-webkit-any':
        if (!p.sub.some((ch) => matchChain(el, ch, ch.length - 1, scope))) return false; break;
      case 'has': try { if (!el.querySelector(p.arg.replace(/^\s*>/, ':scope >'))) return false; } catch (_) { return false; } break;
      case 'first-child': if (siblingsOf(el)[0] !== el) return false; break;
      case 'last-child': { const s = siblingsOf(el); if (s[s.length - 1] !== el) return false; break; }
      case 'only-child': if (siblingsOf(el).length !== 1) return false; break;
      case 'nth-child': if (!nth(p.arg, siblingsOf(el).indexOf(el) + 1)) return false; break;
      case 'nth-last-child': { const s = siblingsOf(el); if (!nth(p.arg, s.length - s.indexOf(el))) return false; break; }
      case 'first-of-type': if (siblingsOf(el).find((s) => s.localName === el.localName) !== el) return false; break;
      case 'last-of-type': if (siblingsOf(el).findLast((s) => s.localName === el.localName) !== el) return false; break;
      case 'only-of-type': if (siblingsOf(el).filter((s) => s.localName === el.localName).length !== 1) return false; break;
      case 'nth-of-type': if (!nth(p.arg, siblingsOf(el).filter((s) => s.localName === el.localName).indexOf(el) + 1)) return false; break;
      case 'checked': if (!(el.checked === true || (el instanceof HTMLOptionElement && el.selected))) return false; break;
      case 'selected': if (!(el instanceof HTMLOptionElement && el.selected)) return false; break;
      case 'disabled': if (!el.hasAttribute('disabled')) return false; break;
      case 'enabled': if (el.hasAttribute('disabled')) return false; break;
      case 'required': if (!el.hasAttribute('required')) return false; break;
      case 'optional': if (el.hasAttribute('required')) return false; break;
      case 'read-only': if (!el.hasAttribute('readonly')) return false; break;
      case 'read-write': if (el.hasAttribute('readonly')) return false; break;
      case 'placeholder-shown': if (!el.hasAttribute('placeholder') || el.value) return false; break;
      case 'focus': if (document._active !== el) return false; break;
      case 'focus-within': if (!document._active || !el.contains(document._active)) return false; break;
      case 'root': if (el !== el.ownerDocument?.documentElement) return false; break;
      case 'scope': if (el !== scope) return false; break;
      case 'empty': if (el.childNodes.some((x) => x.nodeType === 1 || (x.nodeType === 3 && x.data))) return false; break;
      case 'link': case 'any-link': if (!(el.localName === 'a' || el.localName === 'area') || !el.hasAttribute('href')) return false; break;
      case 'defined': break;
      case 'lang': if (!(el.closest('[lang]')?.getAttribute('lang') || '').startsWith(p.arg)) return false; break;
      default: return false;      // :hover, :active, :visited, ::before ...
    }
  }
  return true;
}
function matchChain(el, chain, k, scope) {
  if (!el || el.nodeType !== 1 || !matchComp(el, chain[k].c, scope)) return false;
  if (k === 0) return true;
  const comb = chain[k].comb;
  if (comb === ' ') {
    for (let p = el.parentNode; p && p.nodeType === 1; p = p.parentNode) if (matchChain(p, chain, k - 1, scope)) return true;
    return false;
  }
  if (comb === '>') { const p = el.parentNode; return !!p && p.nodeType === 1 && matchChain(p, chain, k - 1, scope); }
  if (comb === '+') return matchChain(el.previousElementSibling, chain, k - 1, scope);
  if (comb === '~') {
    for (let p = el.previousElementSibling; p; p = p.previousElementSibling) if (matchChain(p, chain, k - 1, scope)) return true;
    return false;
  }
  return false;
}
function qsa(root, sel, first) {
  const list = parseSel(sel);
  const out = new NodeList();
  const scope = root.nodeType === 1 ? root : null;
  const walk = (n) => {
    for (const c of n.childNodes) {
      if (c.nodeType !== 1) continue;
      if (list.some((ch) => matchChain(c, ch, ch.length - 1, scope))) {
        out.push(c);
        if (first) return true;
      }
      if (walk(c)) return true;
      if (c.localName === 'template' && c._content && walk(c._content)) return true;
    }
    return false;
  };
  walk(root);
  return out;
}

// ---------------------------------------------------------------------------
// HTML parsing
// ---------------------------------------------------------------------------
const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', copy: '©', reg: '®', trade: '™',
  hellip: '…', mdash: '—', ndash: '–', lsquo: '‘', rsquo: '’', ldquo: '“', rdquo: '”', sbquo: '‚', bdquo: '„',
  bull: '•', middot: '·', laquo: '«', raquo: '»', times: '×', divide: '÷', deg: '°', euro: '€', pound: '£',
  yen: '¥', cent: '¢', sect: '§', para: '¶', shy: '­', plusmn: '±', frac12: '½', frac14: '¼', frac34: '¾',
  larr: '←', rarr: '→', uarr: '↑', darr: '↓', harr: '↔', hearts: '♥', check: '✓', ensp: ' ', emsp: ' ',
  thinsp: ' ', zwj: '‍', zwnj: '‌', iexcl: '¡', iquest: '¿', acute: '´', uml: '¨', ordf: 'ª', ordm: 'º',
  sup1: '¹', sup2: '²', sup3: '³', micro: 'µ', not: '¬', macr: '¯', cedil: '¸', dagger: '†', Dagger: '‡', permil: '‰',
  prime: '′', Prime: '″', minus: '−', le: '≤', ge: '≥', ne: '≠', infin: '∞', star: '☆', starf: '★', lrm: '‎', rlm: '‏' };
for (const [k, v] of Object.entries({ a: 'àáâãäå', e: 'èéêë', i: 'ìíîï', o: 'òóôõö', u: 'ùúûü' })) {
  ['grave', 'acute', 'circ', 'tilde', 'uml', 'ring'].forEach((s, j) => { if (v[j]) { ENT[k + s] = v[j]; ENT[k.toUpperCase() + s] = v[j].toUpperCase(); } });
}
Object.assign(ENT, { ntilde: 'ñ', Ntilde: 'Ñ', ccedil: 'ç', Ccedil: 'Ç', szlig: 'ß', oslash: 'ø', Oslash: 'Ø', aelig: 'æ', AElig: 'Æ', yacute: 'ý', yuml: 'ÿ', eth: 'ð', thorn: 'þ' });
function decodeEnt(s) {
  if (!s.includes('&')) return s;
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z][a-z0-9]*);?/gi, (m, e) => {
    if (e[0] === '#') {
      const cp = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10);
      try { return String.fromCodePoint(cp || 0xfffd); } catch (_) { return '�'; }
    }
    return ENT[e] ?? m;
  });
}
const RAW = new Set(['script', 'style', 'textarea', 'title', 'xmp', 'noscript', 'iframe', 'noembed', 'noframes', 'plaintext']);
const CLOSES_P = new Set(['address', 'article', 'aside', 'blockquote', 'details', 'div', 'dl', 'fieldset', 'figcaption',
  'figure', 'footer', 'form', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header', 'hgroup', 'hr', 'main', 'menu', 'nav', 'ol',
  'p', 'pre', 'section', 'table', 'ul']);
const HEAD_TAGS = new Set(['meta', 'link', 'title', 'style', 'script', 'base', 'noscript', 'template']);

// Parse html into `parent`. With doc mode, fills a Document's html/head/body.
function parseInto(html, parent, docMode) {
  const doc = parent.ownerDocument || parent;
  const stack = [parent];
  let mode = docMode ? 'head' : 'body';
  let head = null, body = null;
  if (docMode) {
    head = doc.head; body = doc.body;
    stack.length = 0; stack.push(head);
  }
  const cur = () => { const c = stack[stack.length - 1]; return c.localName === 'template' ? c.content : c; };
  const inSvg = () => stack.some((e) => e.namespaceURI === SVGNS);
  const toBody = () => { if (docMode && mode === 'head') { mode = 'body'; stack.length = 0; stack.push(body); } };
  const closeUntil = (names, stop) => {
    for (let k = stack.length - 1; k > 0; k--) {
      const t = stack[k].localName;
      if (stop.has(t)) return;
      if (names.has(t)) { stack.length = k; return; }
    }
  };
  const pushText = (t) => {
    if (!t) return;
    if (docMode && mode === 'head') {
      if (!t.trim()) return;
      toBody();
    }
    const c = cur();
    const last = c.childNodes[c.childNodes.length - 1];
    if (last && last.nodeType === 3) last._d += t;
    else { const n = doc.createTextNode(t); n.parentNode = c; c.childNodes.push(n); }
  };
  const P_STOP = new Set(['button', 'td', 'th', 'table', 'html', 'template']);
  const n = html.length;
  let i = 0;
  while (i < n) {
    const lt = html.indexOf('<', i);
    if (lt < 0) { pushText(decodeEnt(html.slice(i))); break; }
    if (lt > i) pushText(decodeEnt(html.slice(i, lt)));
    i = lt;
    if (html.startsWith('<!--', i)) {
      const e = html.indexOf('-->', i + 4);
      i = e < 0 ? n : e + 3;
      continue;
    }
    if (html[i + 1] === '!' || html[i + 1] === '?') {
      if (/^<!\[CDATA\[/.test(html.slice(i, i + 9))) {
        const e = html.indexOf(']]>', i);
        pushText(html.slice(i + 9, e < 0 ? n : e));
        i = e < 0 ? n : e + 3;
        continue;
      }
      const e = html.indexOf('>', i);
      i = e < 0 ? n : e + 1;
      continue;
    }
    if (html[i + 1] === '/') {
      const m = /^<\/([a-zA-Z][^\s\/>]*)[^>]*>/.exec(html.slice(i, i + 200));
      if (!m) { pushText('<'); i++; continue; }
      i += m[0].length;
      const tag = inSvg() ? m[1] : m[1].toLowerCase();
      if (docMode && tag === 'head') { toBody(); continue; }
      if (tag === 'body' || tag === 'html') continue;
      if (tag === 'p' && !stack.some((e) => e.localName === 'p')) {
        const p = doc.createElement('p'); p.parentNode = cur(); cur().childNodes.push(p); continue;
      }
      for (let k = stack.length - 1; k > 0; k--) {
        if (stack[k].localName === tag || stack[k].localName.toLowerCase() === tag.toLowerCase()) { stack.length = k; break; }
      }
      continue;
    }
    if (!/[a-zA-Z]/.test(html[i + 1] || '')) { pushText('<'); i++; continue; }
    // start tag
    let j = i + 1;
    while (j < n && !/[\s\/>]/.test(html[j])) j++;
    const rawName = html.slice(i + 1, j);
    const attrs = [];
    let selfClose = false;
    for (;;) {
      while (j < n && /[\s\/]/.test(html[j])) { if (html[j] === '/' && html[j + 1] === '>') selfClose = true; j++; }
      if (j >= n) break;
      if (html[j] === '>') { j++; break; }
      const s = j;
      while (j < n && !/[\s\/>=]/.test(html[j])) j++;
      const name = html.slice(s, j);
      while (j < n && /\s/.test(html[j])) j++;
      let val = '';
      if (html[j] === '=') {
        j++;
        while (j < n && /\s/.test(html[j])) j++;
        const q = html[j];
        if (q === '"' || q === "'") {
          const e = html.indexOf(q, j + 1);
          val = html.slice(j + 1, e < 0 ? n : e);
          j = e < 0 ? n : e + 1;
        } else {
          const s2 = j;
          while (j < n && !/[\s>]/.test(html[j])) j++;
          val = html.slice(s2, j);
        }
      }
      if (name) attrs.push([name, decodeEnt(val)]);
    }
    i = j;
    const svg = inSvg() || rawName.toLowerCase() === 'svg';
    const tag = svg ? rawName : rawName.toLowerCase();
    const lt2 = tag.toLowerCase();
    if (docMode) {
      if (lt2 === 'html') { for (const [k, v] of attrs) if (!doc.documentElement.hasAttribute(k)) doc.documentElement._a.set(k.toLowerCase(), v); continue; }
      if (lt2 === 'head') continue;
      if (lt2 === 'body') {
        toBody();
        for (const [k, v] of attrs) if (!body.hasAttribute(k)) body.setAttribute(k, v);
        continue;
      }
      if (mode === 'head' && !HEAD_TAGS.has(lt2)) toBody();
    }
    if (!svg) {
      if (CLOSES_P.has(lt2)) closeUntil(new Set(['p']), P_STOP);
      if (lt2 === 'li') closeUntil(new Set(['li']), new Set(['ul', 'ol', 'menu']));
      else if (lt2 === 'dt' || lt2 === 'dd') closeUntil(new Set(['dt', 'dd']), new Set(['dl']));
      else if (lt2 === 'tr') closeUntil(new Set(['tr', 'td', 'th']), new Set(['table', 'tbody', 'thead', 'tfoot']));
      else if (lt2 === 'td' || lt2 === 'th') closeUntil(new Set(['td', 'th']), new Set(['tr', 'table']));
      else if (lt2 === 'tbody' || lt2 === 'thead' || lt2 === 'tfoot') closeUntil(new Set(['tbody', 'thead', 'tfoot', 'tr', 'td', 'th']), new Set(['table']));
      else if (lt2 === 'option') closeUntil(new Set(['option']), new Set(['select', 'datalist', 'optgroup']));
      else if (lt2 === 'optgroup') closeUntil(new Set(['option', 'optgroup']), new Set(['select']));
      else if (lt2 === 'a') closeUntil(new Set(['a']), new Set(['table', 'td', 'th']));
    }
    const el = svg ? doc.createElementNS(SVGNS, tag) : doc.createElement(tag);
    const real = el._target || el;
    for (const [k, v] of attrs) {
      const kk = svg ? k : k.toLowerCase();
      if (!real._a.has(kk)) real._a.set(kk, v);
      if (kk === 'style') real._styleStale = true;
      else if (kk.startsWith('on') && EVENTS.includes(kk.slice(2))) { const fn = compileHandler(real, v); if (fn) setHandler(real, kk.slice(2), fn); }
    }
    if (lt2 === 'script') real._parser = true;
    const c = cur();
    el.parentNode = c;
    c.childNodes.push(el);
    if (!svg && RAW.has(lt2)) {
      const close = new RegExp('</' + lt2 + '\\s*>', 'i');
      const rest = html.slice(i);
      const m = close.exec(rest);
      const text = m ? rest.slice(0, m.index) : rest;
      i += m ? m.index + m[0].length : rest.length;
      if (text) {
        const t = doc.createTextNode(lt2 === 'textarea' || lt2 === 'title' ? decodeEnt(text) : text);
        t.parentNode = el;
        el.childNodes.push(t);
      }
      continue;
    }
    if ((svg && selfClose) || (!svg && VOID.has(lt2))) continue;
    stack.push(el);
  }
}
function parseFragment(html, parent) { parseInto(html, parent, false); }
function docWrite(doc, html) {
  const s = doc._cs;
  const f = doc.createDocumentFragment();
  parseFragment(html, f);
  if (s && s.parentNode) s.parentNode.insertBefore(f, s.nextSibling);
  else (doc.body || doc.documentElement).appendChild(f);
}

// ---------------------------------------------------------------------------
// serializing (for innerHTML, and for Webinator with data-nep ids)
// ---------------------------------------------------------------------------
const escText = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/ /g, '&nbsp;');
const escAttr = (s) => s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/ /g, '&nbsp;');
const RAW_OUT = new Set(['script', 'style', 'xmp', 'noscript', 'iframe', 'noembed', 'noframes', 'plaintext']);
function serializeNode(n, nep) {
  const out = [];
  ser(n, out, nep, false);
  return out.join('');
}
function serializeChildren(n, nep) {
  const out = [];
  const raw = n.nodeType === 1 && RAW_OUT.has(n.localName);
  for (const c of n.childNodes) ser(c, out, nep, raw);
  return out.join('');
}
let nidCounter = 0;
let idMap = new Map();
function ser(n, out, nep, raw) {
  if (n.nodeType === 3) { out.push(raw ? n._d : escText(n._d)); return; }
  if (n.nodeType === 8) { if (!nep) out.push('<!--', n._d, '-->'); return; }
  if (n.nodeType === 11 || n.nodeType === 9) { for (const c of n.childNodes) ser(c, out, nep, false); return; }
  if (n.nodeType !== 1) return;
  const tag = n.localName;
  if (nep && (tag === 'script' || tag === 'noscript' || tag === 'template')) return;
  out.push('<', tag);
  if (nep) {
    if (!n._nid) n._nid = ++nidCounter;
    idMap.set(n._nid, n);
    out.push(' data-nep="', n._nid, '"');
  }
  const isIn = n instanceof HTMLInputElement;
  for (const [k, v] of n._a) {
    if (nep) {
      if (k === 'data-nep' || (k.startsWith('on') && k.length > 2)) continue;
      if (isIn && (k === 'value' || k === 'checked')) continue;
      if (n instanceof HTMLOptionElement && k === 'selected') continue;
    }
    out.push(' ', k, '="', escAttr(v), '"');
  }
  if (nep) {
    if (isIn) {
      const t = n.type;
      if (t === 'checkbox' || t === 'radio') { if (n.checked) out.push(' checked'); if (n.hasAttribute('value')) out.push(' value="', escAttr(n.getAttribute('value')), '"'); } else if (t !== 'file') out.push(' value="', escAttr(n.value), '"');
    } else if (n instanceof HTMLOptionElement && n.selected) out.push(' selected');
  }
  out.push('>');
  if (VOID.has(tag) && n.namespaceURI === XHTML) return;
  if (nep && tag === 'svg') { out.push('</svg>'); return; }
  if (tag === 'textarea' && nep) out.push(escText(n.value));
  else if (tag === 'style') {
    for (const c of n.childNodes) if (c.nodeType === 3) out.push(c._d);
    if (n._sheet) for (const r of n._sheet.cssRules) out.push('\n', r.cssText);
  } else {
    if (nep && n.shadowRoot) for (const c of n.shadowRoot.childNodes) ser(c, out, nep, false);
    const r = RAW_OUT.has(tag);
    const kids = tag === 'template' && n._content ? n._content.childNodes : n.childNodes;
    for (const c of kids) ser(c, out, nep, r);
  }
  out.push('</', tag, '>');
}

// ---------------------------------------------------------------------------
// the window
// ---------------------------------------------------------------------------
curDoc = null;
const document = new Document();
curDoc = document;
mainDoc = document;

let VW = 1000, VH = 700;
class Location {
  get href() { return PAGE.href; }
  set href(v) { goTo(v, false); }
  assign(v) { goTo(v, false); }
  replace(v) { goTo(v, true); }
  reload() { send({ t: 'navigate', url: PAGE.href, reload: true }); }
  toString() { return PAGE.href; }
  valueOf() { return this; }
  get ancestorOrigins() { return []; }
}
for (const p of URL_PARTS) {
  if (p === 'username' || p === 'password') continue;
  Object.defineProperty(Location.prototype, p, {
    configurable: true, enumerable: true,
    get() { return PAGE[p]; },
    set(v) {
      if (p === 'origin') return;
      const u = new URL(PAGE.href);
      u[p] = v;
      goTo(u.href, false);
    },
  });
}
const location = new Location();

const hist = { entries: [{ url: '', state: null }], index: 0 };
function sendUrl() { send({ t: 'url', url: PAGE.href, index: hist.index, length: hist.entries.length }); }
function goTo(v, replace) {
  const u = new URL(String(v), PAGE.href);
  if (u.protocol === 'javascript:') { try { (0, eval)(decodeURIComponent(u.href.slice(11))); } catch (e) { report(e); } return; }
  const a = new URL(PAGE.href); a.hash = '';
  const b = new URL(u.href); b.hash = '';
  if (a.href === b.href && u.hash && loaded) {
    const old = PAGE.href;
    if (u.href === old) return;
    if (replace) hist.entries[hist.index] = { url: u.href, state: null };
    else { hist.entries.splice(hist.index + 1); hist.entries.push({ url: u.href, state: null }); hist.index++; }
    PAGE = u;
    sendUrl();
    dispatch(G, new HashChangeEvent('hashchange', { oldURL: old, newURL: u.href }));
    return;
  }
  send({ t: 'navigate', url: u.href, replace: !!replace });
}
const history = {
  get length() { return hist.entries.length; },
  get state() { return hist.entries[hist.index].state; },
  scrollRestoration: 'auto',
  pushState(state, _t, url) { this._set(state, url, true); },
  replaceState(state, _t, url) { this._set(state, url, false); },
  _set(state, url, push) {
    const u = url == null ? new URL(PAGE.href) : new URL(String(url), PAGE.href);
    if (u.origin !== PAGE.origin) throw new DOMException('pushState to another origin', 'SecurityError');
    let st = null;
    try { st = state === undefined ? null : structuredClone(state); } catch (_) { st = state; }
    if (push) { hist.entries.splice(hist.index + 1); hist.entries.push({ url: u.href, state: st }); hist.index++; } else hist.entries[hist.index] = { url: u.href, state: st };
    PAGE = u;
    sendUrl();
  },
  back() { this.go(-1); },
  forward() { this.go(1); },
  go(d = 0) {
    d = +d || 0;
    if (d === 0) { location.reload(); return; }
    const i = hist.index + d;
    if (i < 0 || i >= hist.entries.length) return;
    setTimeout(() => {
      const oldHash = PAGE.hash;
      hist.index = i;
      PAGE = new URL(hist.entries[i].url);
      sendUrl();
      dispatch(G, new PopStateEvent('popstate', { state: hist.entries[i].state }));
      if (PAGE.hash !== oldHash) dispatch(G, new HashChangeEvent('hashchange', { newURL: PAGE.href }));
    }, 0);
  },
};

// storage: kept by Webinator per site (this process can't touch files)
class Storage {
  constructor(area) { this._area = area; this._m = new Map(); }
  get length() { return this._m.size; }
  key(i) { return [...this._m.keys()][i] ?? null; }
  getItem(k) { k = String(k); return this._m.has(k) ? this._m.get(k) : null; }
  setItem(k, v) {
    k = String(k); v = String(v);
    if (this._m.get(k) === v) return;
    this._m.set(k, v);
    if (this._area === 'local') send({ t: 'storage', area: 'local', key: k, value: v });
  }
  removeItem(k) {
    k = String(k);
    if (!this._m.has(k)) return;
    this._m.delete(k);
    if (this._area === 'local') send({ t: 'storage', area: 'local', key: k, value: null });
  }
  clear() { this._m.clear(); if (this._area === 'local') send({ t: 'storage', area: 'local', clear: true }); }
}
const storageProxy = (s) => new Proxy(s, {
  get(t, k) { if (typeof k === 'string' && !(k in t)) return t.getItem(k) ?? undefined; const v = Reflect.get(t, k); return typeof v === 'function' ? v.bind(t) : v; },
  set(t, k, v) { if (typeof k === 'string' && !(k in t)) t.setItem(k, v); else Reflect.set(t, k, v); return true; },
  deleteProperty(t, k) { t.removeItem(k); return true; },
  has(t, k) { return k in t || t._m.has(String(k)); },
  ownKeys(t) { return [...t._m.keys()]; },
  getOwnPropertyDescriptor(t, k) { return t._m.has(String(k)) ? { value: t._m.get(String(k)), enumerable: true, configurable: true, writable: true } : undefined; },
});
const localStore = new Storage('local');
const sessionStore = new Storage('session');

// cookies: Webinator's jar (nep/web/cookies.py) owns them. This side only ever
// holds what document.cookie may show: never HttpOnly cookies, not even their
// values. Requests that carry cookies are made by Webinator (see fetch below).
const jar = new Map();            // name -> value, as document.cookie shows them
let httpOnlyNames = new Set();    // names a script may not set (no values)
function cookieString() { return [...jar].map(([k, v]) => (k ? `${k}=${v}` : v)).join('; '); }
function takeCookieView(list, hidden) {
  jar.clear();
  for (const [k, v] of list || []) jar.set(String(k), String(v));
  httpOnlyNames = new Set(hidden || []);
}
function pathMatch(p, cp) { return p === cp || (p.startsWith(cp) && (cp.endsWith('/') || p[cp.length] === '/')); }
function setCookie(text) {
  // Webinator applies the real rules and sends back the view; this local update
  // only keeps document.cookie right for the script's very next read.
  send({ t: 'setcookie', text });
  const parts = text.split(';');
  const i = parts[0].indexOf('=');
  const k = (i < 0 ? '' : parts[0].slice(0, i)).trim(), v = (i < 0 ? parts[0] : parts[0].slice(i + 1)).trim();
  if (!k && !v) return;
  let gone = false, visible = true;
  for (const p of parts.slice(1)) {
    const j = p.indexOf('=');
    const an = (j < 0 ? p : p.slice(0, j)).trim().toLowerCase(), av = j < 0 ? '' : p.slice(j + 1).trim();
    if (an === 'httponly') visible = false;
    if (an === 'secure' && PAGE.protocol !== 'https:') visible = false;
    if (an === 'path' && av.startsWith('/') && !pathMatch(PAGE.pathname, av)) visible = false;
    if (an === 'domain') { const d = av.replace(/^\./, '').toLowerCase(); const h = PAGE.hostname; if (h !== d && !h.endsWith('.' + d)) visible = false; }
    if (an === 'max-age' && parseInt(av, 10) <= 0) gone = true;
    if (an === 'expires' && Date.parse(av) < Date.now()) gone = true;
  }
  if (!visible || httpOnlyNames.has(k)) return;
  if (gone) jar.delete(k); else jar.set(k, v);
}

// fetch, XHR, WebSocket: relative URLs, and cookies
function sameOrigin(u) { try { return new URL(u).origin === PAGE.origin; } catch (_) { return false; } }
const pendingFetch = new Map();
let fetchSeq = 0;
function b64encode(bytes) {
  let s = '';
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(s);
}
function b64decode(t) {
  const s = atob(t || '');
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}
function fetchResult(m) {
  const p = pendingFetch.get(m.id);
  if (!p) return;
  pendingFetch.delete(m.id);
  if (m.error) { p.reject(new TypeError('Failed to fetch: ' + m.error)); return; }
  const status = m.status | 0;
  const nobody = status === 204 || status === 205 || status === 304 || p.method === 'HEAD';
  let res;
  try {
    res = new Response(nobody ? null : b64decode(m.body), { status: status >= 200 && status <= 599 ? status : 200, statusText: m.statusText || '', headers: m.headers || [] });
  } catch (e) { p.reject(e); return; }
  Object.defineProperty(res, 'url', { value: m.url || p.url });
  Object.defineProperty(res, 'redirected', { value: !!m.url && m.url !== p.url });
  p.resolve(res);
}
class Request extends NRequest {
  constructor(input, init) { super(typeof input === 'string' || input instanceof URL ? abs(input) : input, init); }
}
async function fetch(input, init = {}) {
  let req = input instanceof NRequest ? input : new NRequest(abs(input), init);
  if (input instanceof NRequest && init && Object.keys(init).length) req = new NRequest(input, init);
  const mode = (init && init.credentials) || req.credentials || 'same-origin';
  const withCookies = mode === 'include' || (mode === 'same-origin' && sameOrigin(req.url));
  const headers = new Headers(req.headers);
  headers.delete('cookie');                       // scripts can't send their own Cookie header
  if (!withCookies || !/^https?:/.test(req.url)) {
    return nfetch(new NRequest(req, { headers }));  // no cookies at all, straight from here
  }
  // Cookies go with this request, so Webinator makes it: the HttpOnly ones
  // must never come into this process.
  const body = req.body ? new Uint8Array(await req.arrayBuffer()) : null;
  const id = ++fetchSeq;
  const signal = (init && init.signal) || req.signal;
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException('The operation was aborted.', 'AbortError')); return; }
    pendingFetch.set(id, { resolve, reject, url: req.url, method: req.method });
    signal?.addEventListener('abort', () => {
      if (pendingFetch.delete(id)) reject(new DOMException('The operation was aborted.', 'AbortError'));
    });
    send({ t: 'fetch', id, url: req.url, method: req.method, headers: [...headers], body: body && body.length ? b64encode(body) : null });
  });
}
class WebSocket extends NWebSocket {
  constructor(url, protocols) {
    const u = new URL(String(url), PAGE.href);
    if (u.protocol === 'http:') u.protocol = 'ws:';
    if (u.protocol === 'https:') u.protocol = 'wss:';
    super(u.href, protocols);
  }
}
class EventSource extends (NEventSource || Object) {
  constructor(url, opts) { super(abs(url), opts); }
}
class XMLHttpRequest extends EventTarget {
  constructor() {
    super();
    this.readyState = 0; this.status = 0; this.statusText = ''; this.responseType = ''; this.response = null;
    this.responseText = ''; this.responseURL = ''; this.timeout = 0; this.withCredentials = false;
    this._h = {}; this._rh = null; this._ac = null;
    this.upload = new EventTarget();
  }
  open(method, url, _async = true, user, pw) {
    this._m = String(method).toUpperCase(); this._u = abs(url); this._h = {};
    this._setState(1);
  }
  setRequestHeader(k, v) { this._h[k] = this._h[k] ? this._h[k] + ', ' + v : String(v); }
  overrideMimeType() {}
  getResponseHeader(k) { return this._rh ? this._rh.get(k) : null; }
  getAllResponseHeaders() { if (!this._rh) return ''; let s = ''; for (const [k, v] of this._rh) s += `${k}: ${v}\r\n`; return s; }
  abort() { this._ac?.abort(); this._fire('abort'); this._fire('loadend'); }
  _setState(s) { this.readyState = s; this._fire('readystatechange'); }
  _fire(type, init) {
    const ev = type === 'readystatechange' ? new Event(type) : new ProgressEvent(type, init);
    dispatch(this, ev);
    const f = this['on' + type];
    if (typeof f === 'function') { try { f.call(this, ev); } catch (e) { report(e); } }
  }
  send(body = null) {
    this._ac = new AbortController();
    let timer = null;
    if (this.timeout) timer = setTimeout(() => { this._ac.abort(); this._timedOut = true; }, this.timeout);
    const init = { method: this._m, headers: this._h, signal: this._ac.signal, credentials: this.withCredentials ? 'include' : 'same-origin' };
    if (body !== null && this._m !== 'GET' && this._m !== 'HEAD') init.body = body instanceof Document ? body.documentElement.outerHTML : body;
    this._fire('loadstart');
    fetch(this._u, init).then(async (res) => {
      this.status = res.status; this.statusText = res.statusText; this._rh = res.headers; this.responseURL = res.url;
      this._setState(2);
      this._setState(3);
      const rt = this.responseType;
      if (rt === 'arraybuffer') this.response = await res.arrayBuffer();
      else if (rt === 'blob') this.response = await res.blob();
      else {
        const text = await res.text();
        this.responseText = text;
        if (rt === 'json') { try { this.response = JSON.parse(text); } catch (_) { this.response = null; } } else if (rt === 'document') {
          const d = new Document(); parseInto(text, d, true); this.response = d;
        } else this.response = text;
      }
      clearTimeout(timer);
      this._setState(4);
      this._fire('load', { loaded: 1, total: 1 });
      this._fire('loadend');
    }).catch((e) => {
      clearTimeout(timer);
      this.status = 0;
      this._setState(4);
      this._fire(this._timedOut ? 'timeout' : 'error');
      this._fire('loadend');
    });
  }
}
Object.assign(XMLHttpRequest, { UNSENT: 0, OPENED: 1, HEADERS_RECEIVED: 2, LOADING: 3, DONE: 4 });

class FormData extends NFormData {
  constructor(form, submitter) {
    super();
    if (form) for (const [k, v] of formEntries(form, submitter)) this.append(k, v);
  }
}
function formEntries(form, submitter) {
  const out = [];
  for (const e of descendants(form._target || form)) {
    const name = e.getAttribute('name');
    if (!name || e.hasAttribute('disabled')) continue;
    if (e.closest('fieldset[disabled]')) continue;
    if (e instanceof HTMLInputElement) {
      const t = e.type;
      if (t === 'submit' || t === 'image' || t === 'button' || t === 'reset') { if (e === submitter) out.push([name, e.value]); continue; }
      if ((t === 'checkbox' || t === 'radio') && !e.checked) continue;
      if (t === 'file') continue;
      out.push([name, e.value]);
    } else if (e instanceof HTMLButtonElement) { if (e === submitter) out.push([name, e.value]); } else if (e instanceof HTMLSelectElement) { for (const o of e.selectedOptions) out.push([name, o.value]); } else if (e instanceof HTMLTextAreaElement) out.push([name, e.value]);
  }
  return out;
}
function submitForm(form, submitter) {
  const f = form._target || form;
  const action = submitter?.getAttribute?.('formaction') ? abs(submitter.getAttribute('formaction')) : (f.getAttribute('action') ? abs(f.getAttribute('action')) : PAGE.href);
  const method = (submitter?.getAttribute?.('formmethod') || f.getAttribute('method') || 'get').toLowerCase();
  if (method === 'dialog') { f.closest('dialog')?.close(); return; }
  const body = new URLSearchParams(formEntries(f, submitter)).toString();
  const target = f.getAttribute('target') || '';
  if (method === 'post') send({ t: 'navigate', url: action, method: 'post', body, target });
  else { const u = new URL(action); u.search = body; send({ t: 'navigate', url: u.href, target }); }
}

// observers
class MutationObserver {
  constructor(cb) { this._cb = cb; }
  observe() {}
  disconnect() {}
  takeRecords() { return []; }
}
class ResizeObserver {
  constructor(cb) { this._cb = cb; }
  observe() {}
  unobserve() {}
  disconnect() {}
}
class IntersectionObserver {
  constructor(cb, opts = {}) { this._cb = cb; this.root = opts.root ?? null; this.rootMargin = opts.rootMargin ?? '0px'; this.thresholds = [].concat(opts.threshold ?? 0); this._els = new Set(); }
  observe(el) {
    // everything counts as on screen, so lazy loading and infinite lists load
    if (this._els.has(el)) return;
    this._els.add(el);
    setTimeout(() => {
      if (!this._els.has(el)) return;
      const r = new DOMRect(0, 0, 1, 1);
      try {
        this._cb([{ target: el, isIntersecting: true, intersectionRatio: 1, boundingClientRect: r, intersectionRect: r, rootBounds: r, time: performance.now() }], this);
      } catch (e) { report(e); }
    }, 0);
  }
  unobserve(el) { this._els.delete(el); }
  disconnect() { this._els.clear(); }
  takeRecords() { return []; }
}
class PerformanceObserver {
  constructor() {}
  observe() {}
  disconnect() {}
  takeRecords() { return []; }
  static get supportedEntryTypes() { return []; }
}

// media queries
const mqls = new Set();
function evalMedia(q) {
  q = String(q).toLowerCase().trim();
  if (!q) return true;
  return q.split(',').some((one) => {
    one = one.trim();
    let neg = false;
    if (one.startsWith('not ')) { neg = true; one = one.slice(4); }
    one = one.replace(/^only\s+/, '');
    const ok = one.split(/\s+and\s+/).every((term) => {
      term = term.trim();
      if (term === 'all' || term === 'screen') return true;
      if (term === 'print' || term === 'speech') return false;
      const m = /^\(\s*([a-z-]+)\s*(?::\s*([^)]+))?\)$/.exec(term);
      if (!m) return false;
      const [, f, v = ''] = m;
      const px = (s) => { const x = parseFloat(s); return /em$/.test(s.trim()) ? x * 16 : x; };
      switch (f) {
        case 'min-width': return VW >= px(v);
        case 'max-width': return VW <= px(v);
        case 'min-height': return VH >= px(v);
        case 'max-height': return VH <= px(v);
        case 'orientation': return v.trim() === (VW >= VH ? 'landscape' : 'portrait');
        case 'prefers-color-scheme': return v.trim() === 'light';
        case 'prefers-reduced-motion': return v.trim() === 'reduce';
        case 'prefers-contrast': return v.trim() === 'no-preference';
        case 'hover': case 'any-hover': return v.trim() === 'hover' || !v;
        case 'pointer': case 'any-pointer': return v.trim() === 'fine' || !v;
        case 'display-mode': return v.trim() === 'browser';
        case 'min-resolution': case 'min-device-pixel-ratio': case '-webkit-min-device-pixel-ratio': return parseFloat(v) <= 1;
        case 'color': return true;
        default: return false;
      }
    });
    return neg ? !ok : ok;
  });
}
class MediaQueryList extends EventTarget {
  constructor(q) { super(); this.media = String(q); this.matches = evalMedia(q); this.onchange = null; mqls.add(new WeakRef(this)); }
  addListener(f) { this.addEventListener('change', f); }
  removeListener(f) { this.removeEventListener('change', f); }
}
function refreshMedia() {
  for (const r of mqls) {
    const m = r.deref();
    if (!m) { mqls.delete(r); continue; }
    const now = evalMedia(m.media);
    if (now !== m.matches) {
      m.matches = now;
      const ev = new Event('change');
      ev.matches = now; ev.media = m.media;
      dispatch(m, ev);
      try { m.onchange?.(ev); } catch (e) { report(e); }
    }
  }
}

const BLOCKS = new Set(['html', 'body', 'div', 'p', 'ul', 'ol', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'form', 'table',
  'header', 'footer', 'section', 'article', 'nav', 'aside', 'main', 'pre', 'blockquote', 'figure', 'fieldset', 'dl', 'dd', 'dt', 'hr', 'address', 'details', 'summary', 'dialog']);
function getComputedStyle(el) {
  const st = el && el.style;
  const dflt = (p) => {
    if (p === 'display') {
      if (!el || el.nodeType !== 1) return 'inline';
      if (el.hidden || ['head', 'script', 'style', 'template', 'meta', 'link', 'title'].includes(el.localName)) return 'none';
      return BLOCKS.has(el.localName) ? 'block' : (el.localName === 'tr' ? 'table-row' : el.localName === 'td' || el.localName === 'th' ? 'table-cell' : 'inline');
    }
    if (p === 'visibility') return 'visible';
    if (p === 'position') return 'static';
    if (p === 'opacity') return '1';
    if (p === 'font-size') return '16px';
    if (p === 'line-height') return 'normal';
    if (p === 'overflow' || p === 'overflow-x' || p === 'overflow-y') return 'visible';
    if (p === 'box-sizing') return 'content-box';
    if (p === 'direction') return 'ltr';
    if (p === 'color') return 'rgb(0, 0, 0)';
    if (p === 'background-color') return 'rgba(0, 0, 0, 0)';
    if (p === 'transform' || p === 'animation-name' || p === 'max-width' || p === 'max-height') return 'none';
    if (/duration|delay/.test(p)) return '0s';
    if (/^(width|height|min-width|min-height|top|left|right|bottom|margin.*|padding.*|border.*width|gap|row-gap|column-gap|letter-spacing|word-spacing|text-indent)$/.test(p)) return '0px';
    return '';
  };
  const get = (p) => (st && st.getPropertyValue(p)) || dflt(p);
  return new Proxy({ getPropertyValue: get, getPropertyPriority: () => '', item: () => '', length: 0, cssText: st ? st.cssText : '' }, {
    get(t, k) { if (typeof k !== 'string' || k in t) return t[k]; return get(kebab(k)); },
  });
}

let rafId = 0;
const rafs = new Map();
function requestAnimationFrame(cb) {
  const id = ++rafId;
  rafs.set(id, setTimeout(() => { rafs.delete(id); try { cb(performance.now()); } catch (e) { report(e); } }, 32));
  return id;
}
function cancelAnimationFrame(id) { clearTimeout(rafs.get(id)); rafs.delete(id); }
function requestIdleCallback(cb) {
  return setTimeout(() => { try { cb({ didTimeout: false, timeRemaining: () => 20 }); } catch (e) { report(e); } }, 50);
}
const nSetTimeout = G.setTimeout, nSetInterval = G.setInterval;
function wrapCb(cb, args) {
  if (typeof cb === 'string') { const code = cb; return () => { try { (0, eval)(code); } catch (e) { report(e); } }; }
  return () => { try { cb(...args); } catch (e) { report(e); } };
}
const setTimeout2 = (cb, ms, ...a) => nSetTimeout(wrapCb(cb, a), ms);
const setInterval2 = (cb, ms, ...a) => nSetInterval(wrapCb(cb, a), Math.max(10, +ms || 0));

class Image_ { constructor(w, h) { const i = document.createElement('img'); if (w) i.width = w; if (h) i.height = h; return i; } }
class Option_ { constructor(text = '', value, ds, sel) { const o = document.createElement('option'); o.text = text; if (value !== undefined) o.value = value; if (ds) o.defaultSelected = true; if (sel) o.selected = true; return o; } }
class Audio_ { constructor(src) { const a = document.createElement('audio'); if (src) a.src = src; return a; } }
class DOMParser {
  parseFromString(s, type) { const d = new Document(); parseInto(String(s), d, true); d.readyState = 'complete'; return d; }
}
class XMLSerializer { serializeToString(n) { return serializeNode(n, false); } }

const nav = {
  userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) COS-Webinator/Neptune Safari/537.36',
  appName: 'Netscape', appVersion: '5.0 (X11)', appCodeName: 'Mozilla', product: 'Gecko', productSub: '20030107',
  vendor: 'COS', vendorSub: '', platform: 'Linux x86_64', language: 'en-US', languages: ['en-US', 'en'],
  onLine: true, cookieEnabled: true, doNotTrack: '1', hardwareConcurrency: 2, deviceMemory: 4, maxTouchPoints: 0,
  webdriver: false, pdfViewerEnabled: false,
  clipboard: { writeText: () => Promise.resolve(), readText: () => Promise.resolve(''), write: () => Promise.resolve(), read: () => Promise.resolve([]) },
  permissions: { query: () => Promise.resolve({ state: 'denied', onchange: null, addEventListener() {} }) },
  sendBeacon: (u, d) => { fetch(u, { method: 'POST', body: d }).catch(() => {}); return true; },
  vibrate: () => false,
  share: () => Promise.reject(new DOMException('not supported', 'NotAllowedError')),
  canShare: () => false,
  getGamepads: () => [],
  javaEnabled: () => false,
  storage: { estimate: () => Promise.resolve({ quota: 0, usage: 0 }), persist: () => Promise.resolve(false), persisted: () => Promise.resolve(false) },
  locks: { request: async (_n, a, b) => (typeof a === 'function' ? a({}) : b({})), query: async () => ({ held: [], pending: [] }) },
  userActivation: { isActive: true, hasBeenActive: true },
};
const screen_ = { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24, pixelDepth: 24,
  orientation: { type: 'landscape-primary', angle: 0, addEventListener() {}, removeEventListener() {}, onchange: null } };

const DOM_GLOBALS = {
  window: G, top: G, parent: G, frames: G, opener: null, frameElement: null, closed: false, name: '', length: 0,
  document, location, history, navigator: nav, screen: screen_, localStorage: storageProxy(localStore),
  sessionStorage: storageProxy(sessionStore), customElements, visualViewport: null, devicePixelRatio: 1,
  scrollX: 0, scrollY: 0, pageXOffset: 0, pageYOffset: 0, screenX: 0, screenY: 0, screenLeft: 0, screenTop: 0,
  isSecureContext: true, origin: '', crossOriginIsolated: false, event: undefined, external: {},
  Node, Element, HTMLElement, Text, Comment, CharacterData, Document, HTMLDocument: Document, DocumentFragment,
  DocumentType, ShadowRoot, Attr, NodeList, HTMLCollection: NodeList, NamedNodeMap: NodeList, DOMTokenList, DOMRect,
  DOMRectReadOnly: DOMRect, Range, Selection: Object, TreeWalker, NodeFilter, CSSStyleSheet, StyleSheet: CSSStyleSheet,
  CSSStyleDeclaration: Object, EventTarget, Event, CustomEvent, UIEvent, MouseEvent, PointerEvent, WheelEvent,
  KeyboardEvent, InputEvent, FocusEvent, CompositionEvent, SubmitEvent, PopStateEvent, HashChangeEvent, StorageEvent,
  ProgressEvent, AnimationEvent, TransitionEvent, ClipboardEvent, DragEvent,
  MutationObserver, WebKitMutationObserver: MutationObserver, ResizeObserver, IntersectionObserver, PerformanceObserver,
  MediaQueryList, XMLHttpRequest, FormData, Request, fetch, WebSocket, EventSource, DOMParser, XMLSerializer,
  Image: Image_, Option: Option_, Audio: Audio_, Storage, Location,
  HTMLAnchorElement, HTMLAreaElement, HTMLInputElement, HTMLTextAreaElement, HTMLSelectElement, HTMLOptionElement,
  HTMLButtonElement, HTMLFormElement, HTMLImageElement, HTMLScriptElement, HTMLLinkElement, HTMLStyleElement,
  HTMLTemplateElement, HTMLMediaElement, HTMLAudioElement, HTMLVideoElement, HTMLCanvasElement, HTMLIFrameElement,
  HTMLLabelElement, HTMLDetailsElement, HTMLDialogElement, HTMLUnknownElement, HTMLDivElement, HTMLSpanElement,
  HTMLParagraphElement, HTMLHeadingElement, HTMLBodyElement, HTMLHeadElement, HTMLHtmlElement, HTMLMetaElement,
  HTMLTitleElement, HTMLUListElement, HTMLOListElement, HTMLLIElement, HTMLTableElement, HTMLTableRowElement,
  HTMLTableCellElement, HTMLTableSectionElement, HTMLPreElement, HTMLBRElement, HTMLHRElement, HTMLFieldSetElement,
  HTMLLegendElement, HTMLBaseElement, HTMLPictureElement, HTMLSourceElement, HTMLSlotElement, HTMLOutputElement,
  HTMLProgressElement, HTMLMeterElement, HTMLQuoteElement, HTMLTimeElement, HTMLDataListElement,
  HTMLOptGroupElement, HTMLEmbedElement, HTMLObjectElement, SVGElement, SVGSVGElement,
  SVGGraphicsElement: SVGElement, SVGPathElement: SVGElement,
  getComputedStyle, matchMedia: (q) => new MediaQueryList(q), requestAnimationFrame, cancelAnimationFrame,
  webkitRequestAnimationFrame: requestAnimationFrame, requestIdleCallback, cancelIdleCallback: (id) => clearTimeout(id),
  setTimeout: setTimeout2, setInterval: setInterval2,
  getSelection: () => selection,
  alert: (m) => { send({ t: 'alert', text: m === undefined ? '' : String(m) }); },
  confirm: (m) => { send({ t: 'alert', text: String(m ?? ''), confirm: true }); return true; },
  prompt: () => null,
  print() {}, focus() {}, blur() {}, stop() {}, close() {}, moveTo() {}, resizeTo() {}, scrollTo() {}, scroll() {}, scrollBy() {},
  open: (u, target) => { if (u) send({ t: 'open', url: abs(u) }); return null; },
  postMessage: (data) => { setTimeout(() => { const e = new Event('message'); e.data = data; e.origin = PAGE.origin; e.source = G; dispatch(G, e); }, 0); },
  CSS: { supports: () => false, escape: (s) => String(s).replace(/([^\w-])/g, '\\$1'), px: (n) => n + 'px' },
  Notification: Object.assign(function Notification() {}, { permission: 'denied', requestPermission: () => Promise.resolve('denied') }),
  speechSynthesis: undefined,
  indexedDB: undefined,
  trustedTypes: undefined,
};
for (const [k, v] of Object.entries(DOM_GLOBALS)) {
  try { Object.defineProperty(G, k, { value: v, writable: true, configurable: true, enumerable: true }); } catch (_) { /* ignore */ }
}
for (const k of ['innerWidth', 'outerWidth']) Object.defineProperty(G, k, { get: () => VW, set() {}, configurable: true });
for (const k of ['innerHeight', 'outerHeight']) Object.defineProperty(G, k, { get: () => VH, set() {}, configurable: true });
Object.defineProperty(G, 'origin', { get: () => PAGE.origin, configurable: true });
G.addEventListener = ETP.addEventListener;
G.removeEventListener = ETP.removeEventListener;
G.dispatchEvent = ETP.dispatchEvent;
for (const k of EVENTS.concat(WIN_EVENTS)) { try { delete G['on' + k]; } catch (_) { /* ignore */ } }
defHandlers(G, EVENTS.concat(WIN_EVENTS));
defHandlers(Document.prototype, ['readystatechange', 'visibilitychange', 'selectionchange', 'pointerlockchange', 'fullscreenchange']);

// ---------------------------------------------------------------------------
// focus, clicks, keys
// ---------------------------------------------------------------------------
let lastFocusSent;
function reportFocus() {
  setTimeout2(() => {
    const a = document._active && document._active.isConnected ? document._active : null;
    const id = a ? (a._nid || (a._nid = ++nidCounter)) : null;
    if (id !== lastFocusSent) { lastFocusSent = id; send({ t: 'focus', id }); }
  }, 0);
}
function focusEl(el, fromScript) {
  const old = document._active && document._active.isConnected ? document._active : null;
  if (old === el) return;
  if (el && (el.disabled || !el.isConnected)) return;
  document._active = el;
  if (old) {
    dispatch(old, new FocusEvent('blur', { relatedTarget: el }));
    dispatch(old, new FocusEvent('focusout', { bubbles: true, relatedTarget: el }));
  }
  if (el) {
    dispatch(el, new FocusEvent('focus', { relatedTarget: old }));
    dispatch(el, new FocusEvent('focusin', { bubbles: true, relatedTarget: old }));
  }
  if (fromScript) reportFocus();
}
// a click's default action
const rawChecked = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set;
function activate(el, ev) {
  // checkboxes and radios change before the click is dispatched, and change back if it is cancelled
  const box = el instanceof HTMLInputElement && (el.type === 'checkbox' || el.type === 'radio') ? el : null;
  let was = null, radioWas = null;
  if (box) {
    was = box.checked;
    if (box.type === 'radio' && box.name) {
      radioWas = descendants(box.form || box.getRootNode()).filter((o) => o instanceof HTMLInputElement && o.type === 'radio' && o.name === box.name && o.checked);
    }
    rawChecked.call(box, box.type === 'checkbox' ? !was : true);
  }
  const ok = dispatch(el, ev);
  if (box) {
    if (!ok) { rawChecked.call(box, was); if (radioWas) for (const o of radioWas) rawChecked.call(o, true); return; }
    if (box.checked !== was) {
      dispatch(box, new InputEvent('input', { bubbles: true }));
      dispatch(box, new Event('change', { bubbles: true }));
    }
    return;
  }
  if (!ok) return;
  for (let n = el; n && n.nodeType === 1; n = n.parentNode) {
    const t = n.localName;
    if ((t === 'a' || t === 'area') && n.hasAttribute('href')) {
      const href = n.getAttribute('href');
      if (href.startsWith('javascript:')) { try { (0, eval)(decodeURIComponent(href.slice(11))); } catch (e) { report(e); } return; }
      const target = n.getAttribute('target') || '';
      if (target === '_blank') send({ t: 'open', url: n.href });
      else goTo(n.href, false);
      return;
    }
    if ((t === 'button' || (t === 'input' && ['submit', 'image', 'reset'].includes(n.type))) && !n.disabled) {
      const form = n.form;
      if (!form) return;
      if (n.type === 'reset') form.reset();
      else if (n.type === 'submit' || n.type === 'image') form.requestSubmit(n);
      return;
    }
    if (t === 'label') {
      const c = n.control;
      if (c && c !== el && !c.contains(el)) c.click();
      return;
    }
    if (t === 'summary' && n.parentNode?.localName === 'details') {
      n.parentNode.toggleAttribute('open');
      dispatch(n.parentNode, new Event('toggle'));
      return;
    }
    if (t === 'select' || t === 'input' || t === 'textarea') return;
  }
}
function focusTarget(el) {
  for (let n = el; n && n.nodeType === 1; n = n.parentNode) {
    if (n instanceof HTMLElement && n.matches(FOCUSABLE)) return n.disabled ? null : n;
  }
  return null;
}
function mouseInit(m) {
  return { bubbles: true, cancelable: true, view: G, button: m.button === 3 ? 2 : (m.button === 2 ? 1 : 0), buttons: 1,
    clientX: m.x || 0, clientY: m.y || 0, pageX: m.x || 0, pageY: m.y || 0, ctrlKey: !!m.ctrl, shiftKey: !!m.shift, altKey: !!m.alt, detail: 1 };
}
function doClick(el, m) {
  const init = mouseInit(m);
  dispatch(el, new PointerEvent('pointerover', init));
  dispatch(el, new MouseEvent('mouseover', init));
  const pd = dispatch(el, new PointerEvent('pointerdown', { ...init, pressure: 0.5 }));
  const md = pd && dispatch(el, new MouseEvent('mousedown', init));
  if (md) {
    const f = focusTarget(el);
    focusEl(f, false);
  }
  reportFocus();
  dispatch(el, new PointerEvent('pointerup', init));
  dispatch(el, new MouseEvent('mouseup', { ...init, buttons: 0 }));
  if (init.button === 0) activate(el, new MouseEvent('click', { ...init, buttons: 0 }));
  else if (init.button === 2) dispatch(el, new MouseEvent('contextmenu', init));
}
function keyInit(m, key) {
  return { bubbles: true, cancelable: true, view: G, key, code: codeFor(key), ctrlKey: !!m.ctrl, shiftKey: !!m.shift, altKey: !!m.alt };
}
function codeFor(k) {
  if (k.length === 1) return /[a-z]/i.test(k) ? 'Key' + k.toUpperCase() : /\d/.test(k) ? 'Digit' + k : k === ' ' ? 'Space' : '';
  return k;
}
function doKey(el, m) {
  const key = m.key;
  const init = keyInit(m, key);
  const ok = dispatch(el, new KeyboardEvent('keydown', init));
  if (ok && key.length === 1) dispatch(el, new KeyboardEvent('keypress', init));
  if (ok && key === 'Enter') {
    if (el instanceof HTMLInputElement && el.form) {
      const form = el.form;
      const btn = form.querySelector('button:not([type]),button[type=submit],input[type=submit],input[type=image]');
      if (btn) btn.click(); else form.requestSubmit();
    } else if (el instanceof HTMLButtonElement || (el.localName === 'a' && el.hasAttribute('href'))) el.click();
  }
  if (ok && key === ' ' && el instanceof HTMLButtonElement) el.click();
  dispatch(el, new KeyboardEvent('keyup', init));
}
function doInput(el, m) {
  if (document._active !== el) focusEl(el, false);
  let ok = true;
  const key = m.key || null;
  if (key) {
    const init = keyInit(m, key);
    ok = dispatch(el, new KeyboardEvent('keydown', init));
    if (ok && key.length === 1) ok = dispatch(el, new KeyboardEvent('keypress', init));
  }
  if (ok) {
    const inputType = key === 'Backspace' ? 'deleteContentBackward' : key === 'Delete' ? 'deleteContentForward'
      : key === 'Enter' ? 'insertLineBreak' : m.paste ? 'insertFromPaste' : 'insertText';
    const bi = new InputEvent('beforeinput', { bubbles: true, cancelable: true, data: m.char || null, inputType });
    if (dispatch(el, bi)) {
      el._v = String(m.value); el._dv = true;
      if (m.cur != null) el._ss = el._se = m.cur;
      mutated(el);
      dispatch(el, new InputEvent('input', { bubbles: true, data: m.char || null, inputType }));
    }
  }
  if (key) dispatch(el, new KeyboardEvent('keyup', keyInit(m, key)));
}
function doChange(el, m) {
  if (el instanceof HTMLSelectElement) el.value = m.value;
  else { el._v = String(m.value); el._dv = true; mutated(el); }
  dispatch(el, new InputEvent('input', { bubbles: true }));
  dispatch(el, new Event('change', { bubbles: true }));
}

// ---------------------------------------------------------------------------
// sending the page back
// ---------------------------------------------------------------------------
let flushTimer = null;
let lastHtml = '';
let ack = 0;
function scheduleFlush() {
  if (flushTimer === null && loaded0) flushTimer = nSetTimeout(() => flush(false), 40);
}
function flush(force) {
  if (flushTimer !== null) { clearTimeout(flushTimer); flushTimer = null; }
  changed = false;
  idMap = new Map();
  let html;
  try { html = '<!DOCTYPE html>' + serializeNode(document.documentElement, true); } catch (e) { report(e); return; }
  if (html === lastHtml && !force) return;
  lastHtml = html;
  send({ t: 'dom', html, title: document.title, ack });
}

// ---------------------------------------------------------------------------
// scripts
// ---------------------------------------------------------------------------
let loaded = false;     // load finished
let loaded0 = false;    // first flush allowed
const ran = new WeakSet();
function scriptKind(s) {
  const t = (s.getAttribute('type') || '').trim().toLowerCase();
  if (t === 'module') return 'module';
  if (!t || /^(text|application)\/(x-)?(java|ecma)script$|^text\/(jscript|livescript)$/.test(t)) return s.hasAttribute('nomodule') ? null : 'classic';
  return null;
}
async function fetchText(u) {
  const r = await fetch(u);
  if (!r.ok) throw new Error(`${u}: HTTP ${r.status}`);
  return r.text();
}
function evalClassic(code, url) {
  try { (0, eval)(code + '\n//# sourceURL=' + url); } catch (e) { report(e); }
}
async function runScript(s) {
  if (ran.has(s)) return;
  ran.add(s);
  const kind = scriptKind(s);
  if (!kind) return;
  const src = s.getAttribute('src');
  document._cs = kind === 'classic' ? s : null;
  try {
    if (kind === 'module') {
      const u = src ? abs(src) : 'data:text/javascript;charset=utf-8,' + encodeURIComponent(s.textContent);
      await import(u);
    } else if (src) {
      evalClassic(await fetchText(abs(src)), abs(src));
    } else {
      evalClassic(s.textContent, PAGE.href + '#inline');
    }
    document._cs = null;
    if (src) dispatch(s, new Event('load'));
  } catch (e) {
    document._cs = null;
    report(e);
    if (src) dispatch(s, new Event('error'));
  }
}
// elements that do something when they are put into the page
function connectedHooks(n) {
  if (n.nodeType !== 1 || !n.isConnected) return;
  const each = (el) => {
    const t = el.localName;
    if (t === 'script' && !el._parser && !ran.has(el)) {
      if (el.hasAttribute('src') || el.getAttribute('type') === 'module') runScript(el);
      else if (el.textContent.trim()) runScript(el);
    } else if (t === 'link' && /stylesheet|preload|modulepreload/i.test(el.getAttribute('rel') || '') && el.getAttribute('href')) {
      setTimeout2(() => dispatch(el, new Event('load')), 0);
    } else if (t === 'img' && el.getAttribute('src') && loaded) {
      setTimeout2(() => dispatch(el, new Event('load')), 0);
    }
  };
  each(n);
  for (const d of descendants(n)) each(d);
}
async function boot(m) {
  PAGE = new URL(m.url);
  VW = m.width || VW; VH = m.height || VH;
  hist.entries = [{ url: PAGE.href, state: null }];
  for (const [k, v] of Object.entries(m.storage || {})) localStore._m.set(k, String(v));
  takeCookieView(m.cookies, m.httponly);
  parseInto(m.html || '', document, true);
  loaded0 = true;
  changed = false;
  const scripts = qsa(document, 'script', false);
  const classic = [], later = [];
  for (const s of scripts) {
    const k = scriptKind(s);
    if (!k) continue;
    if (k === 'module' || (s.hasAttribute('src') && (s.hasAttribute('defer') || s.hasAttribute('async')))) later.push(s);
    else classic.push(s);
  }
  for (const s of classic) await runScript(s);
  document.readyState = 'interactive';
  dispatch(document, new Event('readystatechange'));
  for (const s of later) await runScript(s);
  dispatch(document, new Event('DOMContentLoaded', { bubbles: true }));
  document.readyState = 'complete';
  dispatch(document, new Event('readystatechange'));
  loaded = true;
  dispatch(G, new Event('load'));
  dispatch(G, new Event('pageshow'));
  for (const el of qsa(document, '[autofocus]', true)) focusEl(el, true);
  flush(true);
  send({ t: 'ready' });
}

// ---------------------------------------------------------------------------
// the message loop
// ---------------------------------------------------------------------------
function handle(m) {
  const el = m.id != null ? idMap.get(m.id) || null : null;
  switch (m.t) {
    case 'load': boot(m).catch(report); return;
    case 'click': if (el) doClick(el, m); break;
    case 'input': if (el) doInput(el, m); if (m.seq) ack = m.seq; flush(true); return;
    case 'key': doKey(el || document._active || document.body, m); if (m.seq) ack = m.seq; flush(true); return;
    case 'change': if (el) doChange(el, m); break;
    case 'focus': focusEl(el, false); break;
    case 'resize':
      VW = m.width || VW; VH = m.height || VH;
      refreshMedia();
      dispatch(G, new UIEvent('resize'));
      break;
    case 'history': history.go(m.delta); break;
    case 'cookies': takeCookieView(m.cookies, m.httponly); return;
    case 'fetchres': fetchResult(m); return;
    case 'eval': try { send({ t: 'console', level: 'log', text: fmt((0, eval)(m.code)) }); } catch (e) { report(e); } break;
  }
  if (changed) scheduleFlush();
}

(async () => {
  const dec = new TextDecoder();
  let buf = '';
  const reader = D.stdin.readable.getReader();
  for (;;) {
    let r;
    try { r = await reader.read(); } catch (_) { break; }
    if (r.done) break;
    buf += dec.decode(r.value, { stream: true });
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl);
      buf = buf.slice(nl + 1);
      if (!line.trim()) continue;
      let m;
      try { m = JSON.parse(line); } catch (e) { report(e); continue; }
      try { handle(m); } catch (e) { report(e); }
    }
  }
  D.exit(0);
})();
