const decode = value => value.replace(/&(amp|lt|gt|quot|#39);/g, (_, key) => ({ amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" })[key]);
const dataKey = name => name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase());
const VOID_ELEMENTS = new Set(['img', 'input', 'br', 'hr', 'source']);

// A small in-memory DOM boundary for generated result cards. It executes the
// production renderers and event handlers without opening a browser or server.
export class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName.toLowerCase();
    this.children = [];
    this.attributes = new Map();
    this.dataset = {};
    this.events = new Map();
    this.style = { values: new Map(), setProperty(name, value) { this.values.set(name, value); } };
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.classList = {
      contains: name => this.className.split(/\s+/).includes(name),
      add: name => this.classList.toggle(name, true),
      toggle: (name, enabled) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        const next = enabled ?? !classes.has(name);
        if (next) classes.add(name); else classes.delete(name);
        this.className = [...classes].join(' ');
        return next;
      },
    };
  }
  get className() { return this.attributes.get('class') || ''; }
  set className(value) { this.attributes.set('class', value); }
  get textContent() { return (this.text || '') + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this.text = String(value); this.children = []; }
  get innerHTML() { return this.html || ''; }
  set innerHTML(html) {
    this.html = html;
    this.children = [];
    this.text = '';
    const stack = [this];
    for (const token of html.match(/<[^>]+>|[^<]+/g) || []) {
      if (token.startsWith('</')) { stack.pop(); continue; }
      if (token.startsWith('<')) {
        const tag = token.match(/^<([\w-]+)/)?.[1];
        if (!tag) continue;
        const element = new Element(tag);
        const attrs = token.slice(tag.length + 1, -1);
        for (const match of attrs.matchAll(/([^\s=/]+)(?:="([^"]*)"|='([^']*)'|=([^\s>]+))?/g)) {
          element.setAttribute(match[1], decode(match[2] ?? match[3] ?? match[4] ?? ''));
        }
        stack.at(-1).append(element);
        if (!VOID_ELEMENTS.has(tag) && !token.endsWith('/>')) stack.push(element);
      } else stack.at(-1).text = (stack.at(-1).text || '') + decode(token);
    }
  }
  setAttribute(name, value) {
    this.attributes.set(name, String(value));
    if (name.startsWith('data-')) this.dataset[dataKey(name)] = String(value);
    if (name === 'hidden' || name === 'disabled') this[name] = true;
  }
  getAttribute(name) {
    if (name.startsWith('data-')) return this.dataset[dataKey(name)] ?? null;
    if (name === 'hidden') return this.hidden ? '' : null;
    if (name === 'disabled') return this.disabled ? '' : null;
    return this.attributes.get(name) ?? null;
  }
  hasAttribute(name) { return this.getAttribute(name) !== null; }
  removeAttribute(name) { this.attributes.delete(name); if (name === 'disabled' || name === 'hidden') this[name] = false; }
  append(...elements) { for (const element of elements) { element.parentElement = this; this.children.push(element); } }
  addEventListener(name, callback) { this.events.set(name, callback); }
  matches(selector) {
    if (selector.includes(',')) return selector.split(',').some(part => this.matches(part.trim()));
    const excluded = [...selector.matchAll(/:not\(([^)]+)\)/g)].map(match => match[1]);
    if (excluded.some(part => this.matches(part))) return false;
    selector = selector.replace(/:not\([^)]+\)/g, '');
    const tag = selector.match(/^[\w-]+/)?.[0];
    if (tag && this.tagName !== tag) return false;
    const id = selector.match(/#([\w-]+)/)?.[1];
    if (id && this.getAttribute('id') !== id) return false;
    for (const match of selector.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(match[1])) return false;
    for (const match of selector.matchAll(/\[([^\]=]+)(?:="?([^\]"]*)"?)?\]/g)) {
      if (!this.hasAttribute(match[1]) || match[2] !== undefined && this.getAttribute(match[1]) !== match[2]) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  scrollTo() {}
  focus() {}
}

