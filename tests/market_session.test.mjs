import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const sdk = readFileSync(new URL('../public/vendor/aimarket.js', import.meta.url), 'utf8');
const adapter = readFileSync(new URL('../public/market-session.js', import.meta.url), 'utf8');
const ORIGIN = 'https://market.example.test';
const APP_ORIGIN = 'https://studio.example.test';
const SECRET = 'sk-fixture-never-render-this';
const BINDING = 'binding_fixture_0123456789abcdef';
const MEDIA = 'media_fixture_0123456789abcdef';
const contextMessage = () => ({
  type: 'aimarket:context', version: 1,
  user: { id: 'external-1', name: '集市成员', accountType: 'ordinary', workNumber: null, organization: null },
  agent: { id: 'jht', name: '建绘通' }, apiKey: SECRET,
});
const flush = async () => { for (let index = 0; index < 12; index++) await Promise.resolve(); };

function harness({ path = '/market', query = '', embedded = true, sameOriginParent = false, parentMarket = false, config = {}, bindStatus = 200 } = {}) {
  const listeners = new Map(), timeouts = new Map(), intervals = new Map();
  const requests = [], sent = [], logs = [], elements = new Map(), frames = [], parentNavigations = [];
  let counter = 0, storageAccess = 0, reloaded = 0;
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.dataset = {}; this.handlers = {}; this.hidden = false; this._text = ''; }
    set id(value) { this._id = value; elements.set(value, this); }
    get id() { return this._id; }
    set textContent(value) { this._text = String(value); }
    get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
    setAttribute(name, value) { this[name] = value; }
    addEventListener(name, callback) { this.handlers[name] = callback; }
    append(...children) { this.children.push(...children); }
  }
  class Headers {
    constructor(initial = {}) { this.values = new Map(); for (const [name, value] of initial instanceof Headers ? initial.values : Object.entries(initial)) this.set(name, value); }
    set(name, value) { this.values.set(name.toLowerCase(), String(value)); }
    get(name) { return this.values.get(name.toLowerCase()) || null; }
    delete(name) { this.values.delete(name.toLowerCase()); }
  }
  const document = { head: new Element('head'), body: new Element('body'), createElement: tag => new Element(tag), getElementById: id => elements.get(id) || null, querySelectorAll: selector => selector === 'iframe' ? frames : [] };
  const parent = {
    location: { origin: sameOriginParent ? APP_ORIGIN : ORIGIN, pathname: parentMarket ? '/market' : '/', search: '', replace: path => parentNavigations.push(path) },
    postMessage: (message, targetOrigin) => sent.push({ message, targetOrigin }),
  };
  const globals = {
    URL, URLSearchParams, AbortController, Uint8Array, Headers, document,
    location: { pathname: path, search: query, origin: APP_ORIGIN, reload() { reloaded++; } },
    crypto: { randomUUID: () => `request-${++counter}` },
    console: { log: (...args) => logs.push(args), error: (...args) => logs.push(args), warn: (...args) => logs.push(args) },
    setTimeout: (fn, ms) => { const id = ++counter; timeouts.set(id, { fn, ms }); return id; },
    clearTimeout: id => timeouts.delete(id),
    setInterval: (fn, ms) => { const id = ++counter; intervals.set(id, { fn, ms }); return id; },
    clearInterval: id => intervals.delete(id),
    addEventListener: (name, callback) => { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
    removeEventListener: (name, callback) => listeners.get(name)?.delete(callback),
    fetch: async (url, options = {}) => {
      requests.push({ url, ...options });
      if (url.endsWith('/config')) return { ok: true, status: 200, json: async () => ({ enabled: true, market_origin: ORIGIN, agent_id: 'jht', uses_session_key: true, ...config }) };
      return { ok: bindStatus >= 200 && bindStatus < 300, status: bindStatus, json: async () => ({ connected: true, binding_id: BINDING, media_token: MEDIA, expires_at: 1893456000, secret: SECRET }) };
    },
  };
  Object.defineProperties(globals, {
    localStorage: { get() { storageAccess++; throw new Error('Persistent storage is forbidden'); } },
    sessionStorage: { get() { storageAccess++; throw new Error('Persistent storage is forbidden'); } },
  });
  globals.window = globals;
  globals.parent = embedded ? parent : globals;
  const sandbox = vm.createContext(globals);
  vm.runInContext(sdk, sandbox, { filename: 'aimarket.js' });
  function loadAdapter() {
    const source = adapter.replace(/^import .*;\r?\n/gm, '').replace(/^export (?=(?:async )?function)/gm, '');
    vm.runInContext(`${source}\nglobalThis.moduleExports = { isMarketEntry, isMarketSessionRequired, navigateToMarketEntry, initializeMarket, disconnectMarket, withMarketSession, marketMediaUrl, marketResponseData };`, sandbox, { filename: 'market-session.js' });
    return sandbox.moduleExports;
  }
  function dispatch(name, value = {}) { for (const callback of [...(listeners.get(name) || [])]) callback(value); }
  function receive(data = contextMessage(), overrides = {}) {
    const requestId = sent.at(-1)?.message.requestId;
    dispatch('message', { origin: ORIGIN, source: parent, data: { ...data, requestId }, ...overrides });
  }
  function expire(ms) {
    for (const [id, timer] of [...timeouts]) if (timer.ms === ms) { timeouts.delete(id); timer.fn(); }
  }
  function assertPrivate() {
    assert.equal(storageAccess, 0, 'adapter must never access persistent browser storage');
    assert.ok(!document.body.textContent.includes(SECRET), 'credential must never reach DOM');
    assert.ok(!JSON.stringify(logs).includes(SECRET), 'credential must never reach logs');
    assert.equal(logs.length, 0);
    assert.ok(!JSON.stringify(sent).includes(SECRET), 'credential must not be sent to any parent message');
  }
  return { sandbox, parent, parentNavigations, listeners, timeouts, intervals, requests, sent, elements, frames, document, loadAdapter, dispatch, receive, expire, assertPrivate, reloads: () => reloaded };
}

// The unmodified SDK must reject spoofed source/origin/correlation/version.
{
  const env = harness();
  let completed = false;
  const result = env.sandbox.aimarket.connect({ marketOrigin: ORIGIN }).then(value => { completed = true; return value; });
  const valid = { ...contextMessage(), requestId: env.sent[0].message.requestId };
  env.receive(valid, { origin: 'https://evil.example.test' });
  env.receive(valid, { source: {} });
  env.receive(valid, { data: { ...valid, requestId: 'wrong-id' } });
  env.receive(valid, { data: { ...valid, version: 2 } });
  await flush();
  assert.equal(completed, false);
  env.receive(valid);
  assert.equal((await result).apiKey, SECRET);
  assert.equal(env.sent[0].targetOrigin, ORIGIN);
  assert.equal(env.listeners.get('message').size, 0);
  assert.equal(env.timeouts.size, 0);
  assert.equal(env.intervals.size, 0);
}
{
  const env = harness();
  const abort = new AbortController();
  const result = env.sandbox.aimarket.connect({ marketOrigin: ORIGIN, signal: abort.signal });
  abort.abort();
  await assert.rejects(result, error => error.code === 'ABORTED');
  assert.equal(env.listeners.get('message').size, 0);
  assert.equal(env.intervals.size, 0);
  const timeout = env.sandbox.aimarket.connect({ marketOrigin: ORIGIN, timeoutMs: 15000 });
  env.expire(15000);
  await assert.rejects(timeout, error => error.code === 'TIMEOUT');
  assert.equal(env.timeouts.size, 0);
  assert.equal(env.intervals.size, 0);
}

// Unauthenticated handshake only displays the local login instruction.
{
  const env = harness(), module = env.loadAdapter();
  const statuses = [];
  const result = module.initializeMarket({ authenticated: false, onStatus: state => statuses.push(state) });
  await flush(); env.receive();
  const state = await result;
  assert.equal(state.state, 'login-required');
  assert.equal(env.requests.length, 1);
  assert.ok(!JSON.stringify({ state, statuses }).includes(SECRET));
  assert.equal(env.timeouts.size, 0);
  env.assertPrivate();
}

// Authenticated binding sends the contract only to the application's fixed endpoint.
{
  const env = harness(), module = env.loadAdapter();
  const result = module.initializeMarket({ authenticated: true });
  await flush();
  const message = contextMessage();
  message.user.role = 'admin'; message.user.api_url = 'https://evil.example.test';
  message.modelApiUrl = 'https://evil.example.test';
  env.receive(message);
  const state = await result;
  assert.equal(state.state, 'connected');
  const binding = env.requests.find(request => request.method === 'POST');
  assert.equal(binding.url, '/api/integrations/aimarket/session');
  assert.equal(binding.credentials, 'same-origin');
  const body = JSON.parse(binding.body);
  assert.equal(body.apiKey, SECRET);
  assert.equal(body.user.role, undefined);
  assert.equal(body.user.api_url, undefined);
  assert.equal(body.modelApiUrl, undefined);
  assert.ok(!JSON.stringify(state).includes(SECRET));
  assert.equal(module.withMarketSession({}, '/api/projects').headers.get('X-JHT-Market-Session'), BINDING);
  for (const target of ['', 'https://evil.example.test/api/projects', '/api/auth/login', '/api/integrations/aimarket/config', '/uploads/file.png']) {
    assert.equal(module.withMarketSession({ headers: { 'X-JHT-Market-Session': BINDING } }, target).headers.get('X-JHT-Market-Session'), null);
  }
  const childMessages = [];
  const child = { postMessage: (...args) => childMessages.push(args) };
  const inheritance = { type: 'jht-market-session:request', requestId: 'child-request' };
  env.dispatch('message', { origin: APP_ORIGIN, source: child, data: inheritance });
  assert.equal(childMessages.length, 0, 'unregistered child window cannot receive the binding');
  env.frames.push({ contentWindow: child });
  env.dispatch('message', { origin: 'https://evil.example.test', source: child, data: inheritance });
  assert.equal(childMessages.length, 0, 'foreign-origin child cannot receive the binding');
  env.dispatch('message', { origin: APP_ORIGIN, source: child, data: inheritance });
  assert.equal(childMessages[0][0].binding_id, BINDING);
  assert.equal(childMessages[0][1], APP_ORIGIN);
  env.dispatch('pagehide'); await flush();
  const deletion = env.requests.find(request => request.method === 'DELETE');
  assert.equal(deletion.url, binding.url);
  assert.equal(deletion.keepalive, true);
  assert.equal(deletion.body, undefined);
  assert.equal(deletion.headers['X-JHT-Market-Session'], BINDING);
  assert.throws(() => module.withMarketSession({}, '/api/projects'), error => error.code === 'LOGIN_REQUIRED');
  assert.equal(module.withMarketSession({}, '/api/auth/logout').headers.get('X-JHT-Market-Session'), null);
  assert.equal(env.elements.get('marketConnection').hidden, true);
  env.assertPrivate();
  env.dispatch('pageshow', { persisted: true });
  assert.equal(env.reloads(), 1);
}

// Parent error text is never displayed, even if it includes a secret.
{
  const env = harness(), module = env.loadAdapter();
  const result = module.initializeMarket({ authenticated: true });
  await flush(); env.receive({ type: 'aimarket:error', version: 1, message: `upstream says ${SECRET}` });
  const state = await result;
  assert.equal(state.state, 'error');
  assert.match(state.message, /暂未授权/);
  assert.equal(env.elements.get('marketConnectionRetry').hidden, false);
  env.assertPrivate();
}
{
  const env = harness(), module = env.loadAdapter();
  const result = module.initializeMarket({ authenticated: true });
  await flush(); env.expire(15000);
  assert.match((await result).message, /超时/);
  const retry = module.initializeMarket({ authenticated: true });
  await flush(); env.receive();
  assert.equal((await retry).state, 'connected');
  env.assertPrivate();
}

// Standalone entry and top-level market URL never send a handshake.
for (const scenario of [
  { path: '/', expected: 'standalone' },
  { path: '/market', embedded: false, expected: 'error' },
]) {
  const env = harness(scenario), module = env.loadAdapter();
  assert.equal((await module.initializeMarket({ authenticated: true })).state, scenario.expected);
  await module.disconnectMarket();
  assert.equal(env.requests.length, 0);
  assert.equal(env.sent.length, 0);
  env.assertPrivate();
}

// A nested canvas gets only an opaque reference from its direct same-origin parent.
for (const inherited of [BINDING, '']) {
  const env = harness({ path: '/', query: '?embedded=1&view=canvas', sameOriginParent: true }), module = env.loadAdapter();
  let completed = false;
  const result = module.initializeMarket({ authenticated: true }).then(state => { completed = true; return state; });
  await flush();
  assert.equal(env.sent[0].message.type, 'jht-market-session:request');
  const message = { type: 'jht-market-session:response', requestId: env.sent[0].message.requestId, binding_id: inherited };
  env.dispatch('message', { origin: 'https://evil.example.test', source: env.parent, data: message });
  env.dispatch('message', { origin: APP_ORIGIN, source: {}, data: message });
  env.dispatch('message', { origin: APP_ORIGIN, source: env.parent, data: { ...message, requestId: 'wrong-id' } });
  await flush(); assert.equal(completed, false);
  env.dispatch('message', { origin: APP_ORIGIN, source: env.parent, data: message });
  assert.equal((await result).state, 'inherited');
  assert.equal(module.withMarketSession({}, '/api/projects').headers.get('X-JHT-Market-Session'), inherited || null);
  await module.disconnectMarket();
  assert.equal(env.requests.length, 0, 'nested canvas cannot revoke its parent binding');
  assert.equal(env.sent.some(item => item.message.type === 'aimarket:connect'), false);
  env.assertPrivate();
}
{
  const env = harness({ query: '?embedded=1&view=canvas', sameOriginParent: true }), module = env.loadAdapter();
  const result = module.initializeMarket({ authenticated: true });
  env.expire(1500);
  assert.equal((await result).state, 'inherited');
  assert.equal(module.withMarketSession({}, '/api/projects').headers.get('X-JHT-Market-Session'), null);
}

// A market parent is never treated as standalone when inheritance fails.
for (const timedOut of [false, true]) {
  const env = harness({ path: '/', query: '?embedded=1&view=canvas', sameOriginParent: true, parentMarket: true }), module = env.loadAdapter();
  assert.equal(module.isMarketSessionRequired(), true);
  const result = module.initializeMarket({ authenticated: true });
  if (timedOut) env.expire(1500);
  else env.dispatch('message', { origin: APP_ORIGIN, source: env.parent,
    data: { type: 'jht-market-session:response', requestId: env.sent[0].message.requestId, binding_id: '' } });
  assert.equal((await result).state, 'error');
  assert.throws(() => module.withMarketSession({}, '/api/projects'), error => error.code === 'PARENT_NOT_READY');
  assert.equal(module.navigateToMarketEntry(), true);
  assert.deepEqual(env.parentNavigations, ['/market']);
  assert.equal(env.requests.length, 0);
  env.assertPrivate();
}
{
  const env = harness({ path: '/', query: '?embedded=1&view=canvas', sameOriginParent: true }), module = env.loadAdapter();
  assert.equal(module.navigateToMarketEntry(), false);
  assert.equal(env.parentNavigations.length, 0);
}

for (const scenario of [
  { config: { market_origin: ORIGIN + '/unsafe' }, expected: /配置无效/ },
  { config: { enabled: false }, expected: /尚未启用/ },
  { config: { agent_id: 'another-agent' }, expected: /不一致/, respond: true },
  { bindStatus: 401, expected: /先登录/, respond: true },
  { bindStatus: 403, expected: /无法绑定/, respond: true },
]) {
  const env = harness(scenario), module = env.loadAdapter();
  const result = module.initializeMarket({ authenticated: true });
  await flush(); if (scenario.respond) env.receive();
  assert.match((await result).message, scenario.expected);
  env.assertPrivate();
}
{
  const env = harness(), module = env.loadAdapter();
  const result = module.initializeMarket({ authenticated: true });
  await flush(); await module.disconnectMarket();
  assert.equal((await result).state, 'disconnected');
  env.receive(); await flush();
  assert.equal(env.requests.filter(request => request.method === 'POST').length, 0);
  env.assertPrivate();
}
// The trusted SDK contract creates its own user session without local password login.
{
  const env = harness({ config: { sdk_login: true } }), module = env.loadAdapter();
  const result = module.initializeMarket({ authenticated: false });
  await flush(); env.receive();
  assert.equal((await result).state, 'connected');
  assert.equal(env.requests.find(item => item.method === 'POST').url, '/api/integrations/aimarket/login');
  const notice = env.elements.get('marketConnection');
  assert.equal(notice.hidden, false);
  env.expire(3000);
  assert.equal(notice.dataset.dismissing, 'true');
  assert.equal(notice.hidden, false, 'the 300 ms fade must finish before hiding');
  env.expire(300);
  assert.equal(notice.hidden, true);
  for (const url of ['/api/auth/me', '/api/auth/logout', '/api/projects']) {
    assert.equal(module.withMarketSession({}, url).headers.get('X-JHT-Market-Session'), BINDING);
  }
  assert.equal(module.withMarketSession({}, '/api/auth/login').headers.get('X-JHT-Market-Session'), null);
  const url = module.marketMediaUrl('/uploads/private.png?size=2#preview');
  assert.equal(url, `/uploads/private.png?size=2&jht_media=${MEDIA}#preview`);
  for (const target of ['https://foreign.test/uploads/a.png', '//foreign.test/uploads/a.png', '/api/projects', 'data:image/png;base64,test']) {
    assert.equal(module.marketMediaUrl(target), target);
  }
  const data = module.marketResponseData({ assets: [{ url: '/uploads/private.png?jht_media=stale' }] });
  assert.equal(data.assets[0].url, `/uploads/private.png?jht_media=${MEDIA}`);
  const saved = module.withMarketSession({ headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }, '/api/projects');
  assert.equal(JSON.parse(saved.body).assets[0].url, '/uploads/private.png');
  const messages = [], child = { postMessage: value => messages.push(value) };
  env.frames.push({ contentWindow: child });
  env.dispatch('message', { origin: APP_ORIGIN, source: child, data: { type: 'jht-market-session:request', requestId: 'child' } });
  assert.equal(messages[0].media_token, MEDIA);
  assert.equal(messages[0].sdk_login, true);
  await module.disconnectMarket();
  assert.equal(module.marketMediaUrl('/uploads/private.png?jht_media=stale'), '/uploads/private.png');
  env.assertPrivate();
}
{
  const env = harness({ path: '/', query: '?embedded=1&view=canvas', sameOriginParent: true, parentMarket: true }), module = env.loadAdapter();
  const result = module.initializeMarket({ authenticated: false });
  await flush();
  env.dispatch('message', { origin: APP_ORIGIN, source: env.parent, data: {
    type: 'jht-market-session:response', requestId: env.sent[0].message.requestId,
    binding_id: BINDING, media_token: MEDIA, sdk_login: true,
  } });
  assert.equal((await result).state, 'inherited');
  assert.equal(module.withMarketSession({}, '/api/auth/me').headers.get('X-JHT-Market-Session'), BINDING);
  assert.equal(module.marketMediaUrl('/uploads/private.png'), `/uploads/private.png?jht_media=${MEDIA}`);
  await module.disconnectMarket();
  assert.equal(env.requests.length, 0);
}
console.log('PASS: SDK validation, trusted-context login, identity headers, media capabilities, memory-only credentials and iframe lifecycle');
