import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createConversationResume } from '../public/conversation-resume.js';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const appSource = read('../public/app.js');
const sessionSource = read('../public/session.js');
const loginSource = read('../public/login.js');
const flush = async () => { for (let index = 0; index < 24; index++) await Promise.resolve(); };

function bootHarness({ market = true, required = market, connection = { state: 'error', connected: false, message: '连接超时' },
  conversations = [], search = '', openError } = {}) {
  const counters = { auth: 0, initialization: 0, data: 0, bind: 0, listeners: 0, opened: [], views: [] };
  const elements = new Map();
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, { hidden: true, textContent: '', remove() {}, classList: { add() {} } });
    return elements.get(selector);
  };
  const globals = {
    counters, $, URLSearchParams,
    state: {}, canvasWorkspace: null, canvasGroups: null, canvasHistory: {}, canvasAgent: {}, assetLibrary: {},
    location: { search, reload() { throw new Error('A banner retry should not reload the document'); } },
    conversationResume: createConversationResume({storage:()=>({getItem:()=>null,removeItem(){}})}),
    openConversation: async id => { counters.opened.push(id); if (openError) throw openError; },
    document: { documentElement: { dataset: {}, removeAttribute() {} }, body: { classList: { add() {} } } },
    window: { addEventListener() { counters.listeners++; } },
    session: { async load() { counters.auth++; return { id: 'local-member' }; } },
    isMarketEntry: () => market, isMarketSessionRequired: () => required,
    initializeMarket: async options => { counters.initialization++; globals.observer = options.onStatus; options.onStatus(connection); return connection; },
    api: async path => { counters.data++; return path === '/api/conversations' ? conversations : []; },
    createCanvasWorkspace: () => ({}), createCanvasGroups: () => ({}),
    bindCanvas: () => { counters.bind++; },
  };
  const noops = ['returnFromModelManagement', 'renderOptions', 'bindModelControls', 'bindNavigation', 'bindRailToggles',
    'bindCanvasDock', 'icon', 'escapeHtml', 'selectedNode', 'nodeFootprint', 'updateSelection', 'renderNodes', 'fitCanvas',
    'setZoom', 'scheduleSave', 'renderEdges', 'showView', 'showNodePalette', 'closeCanvasPanel', 'showCanvasHistoryPanel', 'newProjectModal',
    'openModelManagement', 'setRailState', 'toast', 'addLibraryAsset', 'nodeAsset', 'videoPreviewUrl', 'upsertAsset',
    'generateNodeImage', 'generateNodeAudio', 'openScriptWorkflow', 'expandScriptShots', 'chooseNodeMedia', 'uploadNodeMedia',
    'generateFromImage', 'prepareVideoReference', 'captureSettings', 'selectAsset', 'resolveVideoAssets', 'resolveVideoInput',
    'syncNodeSelectionVisuals', 'loadNodeSettings', 'installCanvasMentions', 'showAssetPanel'];
  for (const name of noops) globals[name] = () => {};
  globals.showView = view => counters.views.push(view);
  const sandbox = vm.createContext(globals);
  const start = appSource.indexOf('let bootInProgress = false;');
  const end = appSource.indexOf("\n$('#renameProject')", start);
  assert.ok(start > 0 && end > start);
  vm.runInContext(appSource.slice(start, end) + '\nglobalThis.runBoot = boot;', sandbox);
  return {
    globals, counters, elements,
    run: () => sandbox.runBoot(),
    connect() { connection = { state: 'connected', connected: true, message: '已连接' }; globals.observer(connection); },
  };
}

// A failed handshake performs no business reads or event binding. Banner retry
// resumes startup once, even if a parent repeats its successful response.
{
  const env = bootHarness();
  await env.run();
  assert.equal(env.counters.auth, 0);
  assert.equal(env.counters.data, 0);
  assert.equal(env.counters.bind, 0);
  assert.equal(env.elements.get('#appBootRetry').hidden, false);
  env.connect(); await flush();
  assert.equal(env.counters.data, 7);
  assert.equal(env.counters.bind, 1);
  assert.equal(env.counters.listeners, 2, env.elements.get('#appBootMessage').textContent);
  env.connect(); await env.run(); await flush();
  assert.equal(env.counters.data, 7);
  assert.equal(env.counters.bind, 1);
  assert.equal(env.counters.listeners, 2);
}
for (const state of ['standalone', 'inherited']) {
  const env = bootHarness({ market: false, connection: { state, connected: false, message: '' } });
  await env.run();
  assert.equal(env.counters.data, 7);
  assert.equal(env.counters.bind, 1);
}
// Real boot routes a fresh market entry to its persisted active conversation.
for (const search of ['', '?view=home']) {
  const env = bootHarness({connection:{connected:true}, search,
    conversations:[{id:'running-chat', owner_id:'local-member', status:'running', updated_at:1}]});
  await env.run();
  assert.deepEqual(env.counters.opened, search ? [] : ['running-chat']);
  await env.run();
  assert.equal(env.counters.data, 7, 'recovery must still finish startup exactly once');
}
// A conversation deleted between listing and opening cannot break startup.
{
  const env = bootHarness({connection:{connected:true}, openError:{status:404},
    conversations:[{id:'deleted-chat', owner_id:'local-member', status:'running'}]});
  await env.run();
  assert.deepEqual(env.counters.opened, ['deleted-chat']);
  assert.deepEqual(env.counters.views, ['home']);
  await env.run();
  assert.equal(env.counters.data, 7);
}
{
  const env = bootHarness({ market: false, required: true });
  await env.run();
  assert.equal(env.counters.data, 0, 'a market parent requires binding even if the canvas URL is not /market');
  assert.equal(env.counters.bind, 0);
}

function sessionHarness(market, failLogout = false) {
  const events = new Map(), elements = new Map(), navigations = [], actions = [];
  function element(id) {
    if (!elements.has(id)) elements.set(id, { textContent: '', disabled: true, hidden: true, handlers: {},
      addEventListener(name, callback) { this.handlers[name] = callback; }, focus() {} });
    return elements.get(id);
  }
  const globals = {
    URLSearchParams, document: { getElementById: element, querySelectorAll: () => [] },
    isMarketSessionRequired: () => market,
    navigateToMarketEntry: () => { if (market) navigations.push('/market'); return market; },
    disconnectMarket: async () => { actions.push('disconnect'); },
    localStorage: { setItem() {} }, crypto: { randomUUID: () => 'fixture-event' },
    window: { location: { pathname: market ? '/market' : '/', search: '', hash: '', replace: value => navigations.push(value) },
      addEventListener: (name, callback) => events.set(name, callback) },
  };
  const sandbox = vm.createContext(globals);
  vm.runInContext(sessionSource.replace(/^import .*;\r?\n/gm, '').replace(/^export /gm, '')
    + '\nglobalThis.exports = { checkSessionResponse, createSessionControls, redirectToLogin };', sandbox);
  const controls = sandbox.exports.createSessionControls({
    api: async (path) => {
      if (path.endsWith('/logout')) { actions.push('logout'); if (failLogout) throw new Error('网络暂时中断'); return {}; }
      return { user: { id: 'member-1', username: 'member', role: 'user' } };
    }, beforeLogout: async () => actions.push('save'), onError: value => { actions.push('error'); },
  });
  return { events, elements, navigations, actions, controls, exports: sandbox.exports };
}
for (const market of [false, true]) {
  const env = sessionHarness(market);
  await env.controls.load();
  await env.elements.get('logoutButton').handlers.click();
  assert.deepEqual(env.actions, ['save', 'logout', 'disconnect']);
  assert.equal(env.navigations.at(-1), market ? '/market' : '/login');
}
{
  const env = sessionHarness(true, true);
  await env.controls.load();
  await env.elements.get('logoutButton').handlers.click();
  assert.deepEqual(env.actions, ['save', 'logout', 'error']);
  assert.equal(env.elements.get('logoutButton').disabled, false);
  assert.equal(env.navigations.length, 0);
}
for (const scenario of ['expired', 'changed', 'storage', 'binding-expired']) {
  const env = sessionHarness(true);
  await env.controls.load();
  if (scenario === 'storage') env.events.get('storage')({ key: 'jianhuitong.session-change' });
  else assert.throws(() => env.exports.checkSessionResponse({ status: scenario === 'expired' ? 401 : scenario === 'binding-expired' ? 428 : 200,
    headers: { get: () => scenario === 'changed' ? 'other-local-user' : null } }));
  assert.equal(env.navigations.at(-1), '/market');
  assert.ok(env.actions.includes('disconnect'));
}

// Login is still the existing password POST; SDK setup cannot replace it.
assert.match(loginSource, /fetch\('\/api\/auth\/login'/);
assert.match(loginSource, /initializeMarket\(\{ authenticated: false \}\)/);
assert.match(loginSource, /window\.location\.replace\(isMarketEntry\(\) \? '\/market'/);
assert.match(appSource, /fetch\(url, withMarketSession\(requestOptions, url\)\)/);
for (const source of [appSource, sessionSource, loginSource, read('../public/agent-chat.js')]) {
  for (const match of source.matchAll(/(?:session|market-session)\.js\?v=([^']+)/g)) assert.equal(match[1], 'market-sdk-3');
}
console.log('PASS: market boot gating/retry, standalone canvas compatibility, local-login redirects, logout cleanup and module singleton versions');
