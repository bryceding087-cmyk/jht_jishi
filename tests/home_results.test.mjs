import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createGenerationGuard } from '../public/generation-state.js';
import { buildRegenerationRequest } from '../public/generation-request.js';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const homeSource = read('../public/home-composer.js');
const agentSource = read('../public/agent-chat.js');
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
import { Element } from './dom-fixture.mjs';

function sourceBetween(source, startText, endText) {
  const start = source.indexOf(startText);
  const end = source.indexOf(endText, start);
  assert.ok(start >= 0 && end > start, `Cannot locate production boundary ${startText}`);
  return source.slice(start, end);
}

const imageAsset = (id, name = id) => ({ id, name, kind: 'image', url: `/uploads/${id}.png`, creator: { id: 'creator-1' } });
const resultJob = (id, assets, status = 'done') => ({
  kind: 'image', conversationId: 'conversation-1', prompt: '山水 <原图>',
  settings: { aspect_ratio: 'adaptive', size: '2K', count: 2, model_config_id: 'image-model' },
  references: [], task: { id, status, assets, count: 2, created_at: 1 },
});

// A newly completed video must replace the stale placeholder when returning home.
{
  const recent = new Element('section');
  const state = { conversations: [{ id: 'video-chat', kind: 'video', title: '视频', updated_at: 1 }] };
  let bound = [];
  const context = vm.createContext({
    recent, state, esc: escapeHtml, icon: () => '<i></i>',
    recentVideoPreview: { stop() {}, bind: cards => { bound = cards; } },
    generation: { requireRefresh() {} }, generationScope: () => undefined, visible: false, loading: null,
    api: async () => ({ models: [] }), models: [], drawModel() {}, poll: async () => {}, toast() {},
    onConversations: async () => { state.conversations[0].cover = { kind: 'video', url: '/uploads/generated.mp4' }; },
  });
  vm.runInContext(sourceBetween(homeSource, '  function renderRecent()', '  recent.onclick='), context);
  context.renderRecent();
  assert.equal(recent.querySelector('video'), null);
  vm.runInContext(`globalThis.controller={${sourceBetween(homeSource, '    async show()', '    hide()')}}`, context);
  await context.controller.show();
  const video = recent.querySelector('video');
  assert.equal(video.getAttribute('data-media-src'), '/uploads/generated.mp4#t=0.05');
  assert.equal(video.hasAttribute('src'), false, 'video is deferred until visible');
  assert.equal(video.hasAttribute('muted'), true);
  assert.equal(video.hasAttribute('loop'), true);
  assert.equal(video.hasAttribute('controls'), false);
  assert.equal(bound[0].dataset.homeConversation, 'video-chat');
}

function homeHarness(jobs = [], initialKind = 'image') {
  const root = new Element('section'), results = new Element('section'), form = new Element('form');
  const prompt = new Element('textarea'), send = new Element('button'), error = new Element('div');
  send.className = 'home-send';
  prompt.value = '下一张图片';
  form.append(prompt, send);
  root.append(form, results);
  const heading=new Element('span'),query=root.querySelector.bind(root);
  root.querySelector=selector=>selector==='.agent-location span'?heading:query(selector);
  const d = { viewConversationId: 'conversation-1', prompt: prompt.value, model: initialKind+'-model', ratio:'16:9',resolution:'720p',duration:5,videoMode:'all', references: [], frames: [], count: 1, imageRatio: 'adaptive', imageSize: '2K' };
  const previews = [], referencePreviews = [], requests = [], notifications = [], stateReads = [];
  const windowEvents = new Map(), documentEvents = new Map(), timers = new Map();
  let serverBusy = false, serverResponse, mediaResponse, taskResponse = { id: 'submitted-task', status: 'queued' }, context, timerId = 0, requestId = 0, conversationResponse = { id: 'created-conversation', title: 'New conversation' };
  const api = async (url, body) => {
    if (url.startsWith('/api/conversations/generation-state?conversation_id=')) { stateReads.push(url); return serverResponse ?? { is_busy: serverBusy }; }
    if (body?.method==='DELETE') return {conversation_deleted:true,deleted_asset_ids:[]};
    if (url === '/api/assets') return [];
    if (url.startsWith('/api/conversations/') && !body) return { id: url.split('/').at(-1), kind: 'image', settings: { model_config_id: 'image-model' }, tasks: [] };
    requests.push({ url, body });
    if(body?.background && mediaResponse)return mediaResponse;
    if (url === '/api/conversations') { if (conversationResponse instanceof Error) throw conversationResponse; return conversationResponse; }
    if (taskResponse instanceof Error) throw taskResponse;
    return taskResponse;
  };
  const generation = createGenerationGuard({ api, onChange: () => context?.update?.() });
  context = vm.createContext({
    root, results, form, prompt, error, jobs, generation, kind: initialKind,
    drafts: { image:initialKind==='image'?d:{...d,model:'image-model',viewConversationId:null,generationScope:'draft:image'}, video:initialKind==='video'?d:{...d,model:'video-model',viewConversationId:null,generationScope:'draft:video'} }, draft: () => context.drafts[context.kind], uploading: false, navigationRevision: 0,
    visible: false, polling: null, pollTimer: null, statePollError: false,
    models: ['image','video'].map(kind=>({id:kind+'-model',kind,name:kind,api_key_set:true})),
    TYPES: [['image', '', '图片生成'], ['video', '', '视频生成'], ['audio', '', '音频生成']],
    document: { createElement: tag => new Element(tag), hidden: false, addEventListener: (name, callback) => documentEvents.set(name, callback) },
    window: { addEventListener: (name, callback) => windowEvents.set(name, callback) },
    clearTimeout: id => timers.delete(id), setTimeout: callback => { const id = ++timerId; timers.set(id, callback); return id; },
    CSS: { escape: value => value },
    $: selector => { assert.equal(selector, '.home-send'); return send; },
    pending: task => ['queued', 'running', 'waiting_tool'].includes(task.status),
    complete: task => ['done', 'succeeded'].includes(task.status),
    esc: escapeHtml, icon: () => '', normalizeContentError: value => value,
    onPreviewAsset: asset => previews.push(asset),
    referencePreview: { open: asset => referencePreviews.push(asset) },
    frameMode: () => false, referencesFor: draft => draft.references,
    api, json: body => body, buildRegenerationRequest,
    crypto: { randomUUID: () => 'fixture-request-'+(++requestId) }, structuredClone,
    toast: message => notifications.push(message), onConversations: async () => {},
    editTurn: () => {}, renderRecent: () => {}, drawReferences: () => {},
    state: {}, preferences: { close() {} }, details: { open: false }, onModeChange() {},
    select: next => { context.kind=next; prompt.value=context.drafts[next]?.prompt||''; context.update(); },
    mentions: { refresh() {}, close() {} }, matchMedia: () => ({ matches: true }),
  });
  vm.runInContext([
    sourceBetween(homeSource, 'const needsPoll=', 'const complete='),
    sourceBetween(homeSource, '  const generationScope=', '  const modeControl='),
    sourceBetween(homeSource, '  function updateSend()', '  function select('),
    sourceBetween(homeSource, '  const endpoint=job=>', '  function metadata(job)'),
    sourceBetween(homeSource, '  function metadata(job)', '  function editTurn(job)'),
    sourceBetween(homeSource, '  async function regenerateTurn(job,button)', '  async function deleteTurn('),
    sourceBetween(homeSource, '  async function deleteTurn(job,button)', '  const details='),
    sourceBetween(homeSource, '  results.onclick=e=>', '  async function poll()'),
    sourceBetween(homeSource, '  async function refreshGenerationState(', '  form.onsubmit=async e=>'),
    sourceBetween(homeSource, '  form.onsubmit=async e=>', '  function renderRecent()'),
    sourceBetween(homeSource, "  root.addEventListener('click',e=>{", '  return {'),
    `globalThis.controller={${sourceBetween(homeSource, '    resetHome()', '    async show()')}${sourceBetween(homeSource, '    async openConversation(', '    select\n  };')}};`,
    'globalThis.update=updateSend; globalThis.render=renderResults; globalThis.regenerate=regenerateTurn;',
  ].join('\n'), context);
  return {
    root, results, form, prompt, send, get d() { return context.drafts[context.kind]; }, previews, referencePreviews, requests, notifications, generation, stateReads, timers,
    render: () => context.render(), update: () => context.update(),
    click: target => results.onclick({ target }),
    submit: () => form.onsubmit({ preventDefault() {} }),
    regenerate: job => context.regenerate(job, results.querySelector(`[data-home-regenerate="${job.task.id}"]`)),
    async setServerBusy(value) { serverBusy = value; await generation.refresh('conversation-1'); },
    setServerResponse(value) { serverResponse = value; },
    setMediaResponse(value) { mediaResponse=value; },
    selectMode: kind => context.select(kind),
    setTaskResponse(value) { taskResponse = value; },
    setConversationResponse(value) { conversationResponse = value; },
    resetHome() { context.controller.resetHome(); context.select('image'); },
    newConversation() { const button=new Element('button'); button.setAttribute('data-command','new'); root.events.get('click')({target:button,preventDefault(){},stopImmediatePropagation(){}}); },
    openConversation: id => context.controller.openConversation(id),
    deleteTurn: job => context.deleteTurn(job,new Element('button')),
    returnEvent(name, { visible = true, hidden = false } = {}) {
      context.visible = visible; context.document.hidden = hidden;
      const callback = (name === 'visibilitychange' ? documentEvents : windowEvents).get(name);
      assert.equal(typeof callback, 'function', `${name} must refresh the task state`);
      callback();
    },
    poll: () => context.poll(),
    settlePoll: async () => { for(let i=0;i<10;i++)await Promise.resolve(); },
    async runScheduledPoll() {
      const callback = timers.values().next().value;
      assert.equal(typeof callback, 'function', 'blocked state must schedule another task-state check');
      timers.clear();
      await callback();
    },
  };
}

{
  const job = resultJob('reference-preview', [], 'running');
  job.references = [imageAsset('ref-one', '图片 <一>'), { id: 'audio-ref', kind: 'audio' },
    { ...imageAsset('ref-two'), kind: 'prop' }];
  const env = homeHarness([job]);
  env.render();
  const buttons = env.results.querySelectorAll('[data-home-reference-index]');
  assert.equal(buttons.length, 2, 'only image references open the image viewer');
  for (const button of buttons) {
    assert.equal(button.tagName, 'button');
    assert.equal(button.getAttribute('type'), 'button');
    assert.ok(button.getAttribute('aria-label'));
    env.click(button.querySelector('img'));
  }
  assert.deepEqual(env.referencePreviews, [job.references[0], job.references[2]], 'reference indices must survive mixed media');
  assert.equal(env.previews.length, 0, 'reference preview must not open asset details');
  assert.equal(env.requests.length, 0, 'preview while generating must not submit another task');
}

{
  const first = resultJob('first', [imageAsset('wide'), imageAsset('portrait')]);
  const second = resultJob('second', []);
  second.task.asset = imageAsset('legacy-single');
  const env = homeHarness([first, second]);
  env.render();
  const buttons = env.results.querySelectorAll('.home-image-preview');
  assert.equal(buttons.length, 3);
  for (const [index, button] of buttons.entries()) {
    assert.equal(button.tagName, 'button');
    assert.equal(button.getAttribute('type'), 'button');
    assert.ok(button.getAttribute('aria-label'), 'preview must have an accessible name');
    const image = button.querySelector('img');
    assert.equal(image.getAttribute('draggable'), 'false');
    // Event delegation must also work when the click lands on the child image.
    env.click(index % 2 ? button : image);
  }
  assert.deepEqual(env.previews, [...first.task.assets, second.task.asset]);
  const originalButton = buttons[0];
  env.render();
  assert.equal(env.results.querySelector('.home-image-preview'), originalButton, 'polling unchanged results must preserve the existing media elements');
}

{
  const partial = resultJob('partial', [imageAsset('partial-one')], 'running');
  const env = homeHarness([partial]);
  await env.setServerBusy(true);
  env.render();
  assert.ok(env.results.textContent.includes('正在生成 1/2 张'));
  env.click(env.results.querySelector('.home-image-preview'));
  assert.equal(env.previews[0], partial.task.assets[0], 'partial results remain inspectable while generation is active');
  partial.task = { ...partial.task, status: 'done', assets: [...partial.task.assets, imageAsset('partial-two')] };
  env.render();
  const buttons = env.results.querySelectorAll('.home-image-preview');
  assert.equal(buttons.length, 2);
  env.click(buttons[1].querySelector('img'));
  assert.equal(env.previews[1], partial.task.assets[1]);
  assert.ok(!env.results.textContent.includes('正在生成 1/2 张'));
}

{
  const job = resultJob('complete', [imageAsset('complete-image')]);
  const env = homeHarness([job]);
  env.render();
  assert.equal(env.send.disabled, true, 'unknown conversation task state must block a fresh submission');
  await env.setServerBusy(false);
  const regenerate = env.results.querySelector('[data-home-regenerate]');
  assert.equal(env.send.disabled, false);
  assert.equal(regenerate.disabled, false);
  await env.setServerBusy(true);
  assert.equal(env.send.disabled, true);
  assert.equal(regenerate.disabled, true, 'an unchanged completed card must reflect a task in the same conversation started in another tab');
  assert.equal(env.form.getAttribute('aria-busy'), 'true');
  await env.submit();
  await env.regenerate(job);
  assert.equal(env.requests.length, 0, 'programmatic or stale clicks must not bypass disabled controls');
  env.click(env.results.querySelector('.home-image-preview'));
  assert.equal(env.previews[0], job.task.assets[0]);
  await env.setServerBusy(false);
  assert.equal(env.send.disabled, false);
  assert.equal(regenerate.disabled, false);
  assert.equal(env.form.getAttribute('aria-busy'), 'false');
  env.prompt.value = '   ';
  env.update();
  assert.equal(env.send.disabled, true, 'unlocking a conversation does not enable an empty prompt');
}

for (const source of ['submit', 'regenerate']) {
  const job = resultJob('complete', [imageAsset('complete-image')]);
  const env = homeHarness([job]);
  env.render();
  await env.setServerBusy(false);
  let resolveTask;
  env.setTaskResponse(new Promise(resolve => { resolveTask = resolve; }));
  const first = source === 'submit' ? env.submit() : env.regenerate(job);
  assert.equal(env.send.disabled, true);
  assert.equal(env.generation.isSubmitting('conversation-1'), true);
  await env.submit();
  await env.regenerate(job);
  assert.equal(env.requests.length, 1, 'double submission and cross-entry clicks share one guard');
  resolveTask({ id: 'new-task', status: 'queued' });
  await first;
  assert.equal(env.notifications.length, 0);
  assert.equal(env.generation.isSubmitting('conversation-1'), false);
  assert.equal(env.send.disabled, true, 'a queued task continues locking generation after the POST completes');
  env.prompt.value = '后续草稿';
  env.d.prompt = env.prompt.value;
  await env.setServerBusy(false);
  assert.equal(env.send.disabled, false, 'server completion releases the existing composer');
}

console.log('PASS: conversation image preview identity, partial-result updates, accessible buttons, conversation generation lock and duplicate submission prevention');

for (const event of ['focus', 'online', 'visibilitychange']) {
  const env = homeHarness();
  await env.setServerBusy(false);
  assert.equal(env.send.disabled, false);
  const before = env.stateReads.length;
  env.returnEvent(event, { visible: false });
  env.returnEvent(event, { hidden: true });
  assert.equal(env.stateReads.length, before, 'hidden views and background documents do not restart polling');
  assert.equal(env.send.disabled, false);
  let resolveState;
  env.setServerResponse(new Promise(resolve => { resolveState = resolve; }));
  env.returnEvent(event);
  assert.equal(env.send.disabled, true, 'returning to the page synchronously disables stale submission controls');
  assert.equal(env.generation.snapshot('conversation-1').known, false);
  await Promise.resolve();
  assert.equal(env.stateReads.length, before + 1);
  resolveState({ is_busy: true, task: { id: 'other-tab-task', status: 'running' } });
  await env.settlePoll();
  assert.equal(env.send.disabled, true);
  env.setServerResponse({ is_busy: false });
  await env.runScheduledPoll();
  assert.equal(env.send.disabled, false, 'a fresh server completion response unlocks the original composer');
  assert.equal(env.timers.size, 0);
}

{
  const env = homeHarness();
  await env.setServerBusy(false);
  let resolveOld;
  env.setServerResponse(new Promise(resolve => { resolveOld = resolve; }));
  env.returnEvent('focus');
  await Promise.resolve();
  let resolveNew;
  env.setServerResponse(new Promise(resolve=>{resolveNew=resolve;}));
  env.returnEvent('visibilitychange');
  const before = env.stateReads.length;
  resolveOld({ is_busy: false });
  await env.settlePoll();
  assert.equal(env.send.disabled, true, 'a response started before the latest foreground event must not unlock the page');
  resolveNew({ is_busy: true, task: { id: 'newer-task', status: 'running' } });
  await env.settlePoll();
  assert.equal(env.stateReads.length, before);
  assert.equal(env.send.disabled, true);
  assert.equal(env.generation.snapshot('conversation-1').task.id, 'newer-task');
}

{
  const env = homeHarness();
  await env.setServerBusy(false);
  env.setTaskResponse(new Error('响应丢失'));
  await env.submit();
  assert.equal(env.requests.length, 1);
  const requestId = env.requests[0].body.request_id;
  assert.equal(env.d.requestId, requestId, 'an unknown submission outcome preserves its idempotency key');
  assert.equal(env.send.disabled, true);
  await env.submit();
  assert.equal(env.requests.length, 1, 'the unknown outcome cannot be submitted again before a server check');
  await env.setServerBusy(false);
  env.setTaskResponse({ id: 'recovered-task', status: 'queued' });
  await env.submit();
  assert.equal(env.requests.length, 2);
  assert.equal(env.requests[1].body.request_id, requestId, 'an explicit retry uses the same idempotency key');
  assert.equal(env.send.disabled, true);
}
console.log('PASS: focus/online/visibility task-state refresh, stale-response protection, hidden-view behavior and explicit retry idempotency');

// A video status GET that never returns must not delay another conversation's
// state check or block submission from home, including a second video draft.
for(const destination of ['existing-image','home-image','home-video']) {
  const job={...resultJob('slow-video',[],'running'),kind:'video'};
  const env=homeHarness([job],'video');
  let resolveMedia;
  env.setMediaResponse(new Promise(resolve=>{resolveMedia=resolve;}));
  await env.setServerBusy(true);
  env.returnEvent('focus');await env.settlePoll();
  assert.equal(env.send.disabled,true,'the original video conversation stays locked');
  assert.equal(env.requests[0].url,'/api/tasks/slow-video');
  env.setServerResponse({is_busy:false});
  if(destination==='existing-image')await env.openConversation('conversation-2');
  else {env.resetHome();if(destination==='home-video')env.selectMode('video');}
  await env.settlePoll();
  env.prompt.value='Independent generation';env.d.prompt=env.prompt.value;env.update();
  assert.equal(env.send.disabled,false,'a slow video poll cannot gray out another composer');
  await env.submit();
  const post=env.requests.find(r=>r.url===(destination==='home-video'?'/api/tasks':'/api/images/generations'));
  assert.ok(post,'another generation is submitted before the first video GET resolves');
  assert.notEqual(post.body.project_id,'conversation-1');
  resolveMedia({...job.task,status:'running'});await env.settlePoll();
}
// State polling must also keep ticking when the current media GET stalls.
{
  const job={...resultJob('slow-current',[],'running'),kind:'video'};
  const env=homeHarness([job],'video');let resolveMedia;
  env.setMediaResponse(new Promise(resolve=>{resolveMedia=resolve;}));
  await env.setServerBusy(true);env.returnEvent('focus');await env.settlePoll();
  env.setServerResponse({is_busy:false});await env.runScheduledPoll();
  assert.equal(env.send.disabled,false,'completion state unlocks the current composer without waiting on the stalled media response');
  resolveMedia({...job.task,status:'done'});await env.settlePoll();
}
console.log('PASS: stalled video polling cannot block other image/video conversations or state refresh timers.');

// Navigation during a slow POST must not carry busy flags into another draft
// or let the old finally handler unlock the newer in-flight submission.
for (const navigate of ['resetHome', 'newConversation', 'openConversation']) {
  const env=homeHarness();
  await env.setServerBusy(false);
  let resolveOld, resolveNew;
  env.setTaskResponse(new Promise(resolve=>{resolveOld=resolve;}));
  const oldDraft=env.d, first=env.submit();
  assert.equal(oldDraft.busy,true);
  if(navigate==='openConversation') {
    await env.openConversation('conversation-2');
    await env.generation.refresh('conversation-2');
  } else env[navigate]();
  assert.notEqual(env.d,oldDraft,'navigation replaces the in-flight draft object');
  env.prompt.value='Independent request';env.d.prompt=env.prompt.value;env.update();
  assert.equal(env.send.disabled,false,'another conversation or home can submit during the old POST');
  env.setTaskResponse(new Promise(resolve=>{resolveNew=resolve;}));
  const second=env.submit();
  await Promise.resolve();await Promise.resolve();await Promise.resolve();
  assert.equal(env.d.busy,true);
  resolveOld({id:'old-task',status:'queued'});await first;
  assert.equal(env.d.busy,true,'the old completion cannot clear the new draft busy flag');
  assert.equal(env.send.disabled,true);
  assert.equal(env.generation.isBlocked('conversation-1'),true);
  resolveNew({id:'new-task',status:'queued'});await second;
  const posts=env.requests.filter(request=>request.url==='/api/images/generations');
  assert.equal(posts.length,2);
  assert.equal(posts[0].body.project_id,'conversation-1');
  assert.equal(posts[1].body.project_id,navigate==='openConversation'?'conversation-2':'created-conversation');
  assert.equal(env.d.viewConversationId,posts[1].body.project_id);
  assert.equal(env.notifications.length,0);
}

// Deleting the last turn releases the old draft alias as well as the visible
// conversation ID, so a deleted context cannot keep a new composer blocked.
{
  const job=resultJob('last-turn',[imageAsset('last-image')]);
  const env=homeHarness([job]);
  env.d.generationScope='draft:original';
  env.generation.bindConversation('draft:original','conversation-1');
  env.generation.requireRefresh('conversation-1');
  await env.deleteTurn(job);
  env.update();
  assert.equal(env.d.viewConversationId,null);
  assert.notEqual(env.d.generationScope,'draft:original');
  assert.equal(env.send.disabled,false);
  assert.deepEqual(env.notifications,['已删除本段对话及生成内容']);
}

// Leaving before conversation creation returns must bind only the old draft;
// the home composer stays available and the response cannot steal its context.
{
  const env=homeHarness();env.resetHome();
  env.prompt.value='First draft';env.d.prompt=env.prompt.value;
  let resolveConversation;
  env.setConversationResponse(new Promise(resolve=>{resolveConversation=resolve;}));
  const oldDraft=env.d,first=env.submit();
  env.resetHome();
  env.prompt.value='Second draft';env.d.prompt=env.prompt.value;env.update();
  assert.equal(env.send.disabled,false);
  resolveConversation({id:'late-conversation',title:'Late'});
  await first;
  assert.notEqual(env.d,oldDraft);
  assert.equal(env.d.viewConversationId,null);
  assert.equal(env.d.prompt,'Second draft');
  assert.equal(env.send.disabled,false);
  assert.equal(env.generation.isBlocked('late-conversation'),true);
  assert.equal(env.requests.filter(r=>r.url==='/api/images/generations').length,1);
  await env.openConversation('late-conversation');
  assert.equal(env.send.disabled,true,'reopening the in-flight conversation uses its existing gate');
}

// New conversation creation failure allows an idempotent retry, and a task
// response lost after creation is reconciled against the created ID only.
{
  const env=homeHarness();env.resetHome();
  env.prompt.value='New image';env.d.prompt=env.prompt.value;
  env.setConversationResponse(new Error('creation response lost'));
  await env.submit();
  assert.equal(env.send.disabled,false);
  const requestId=env.requests[0].body.request_id;
  env.setConversationResponse({id:'retry-conversation',title:'Retry'});
  env.setTaskResponse(new Error('task response lost'));
  await env.submit();
  assert.equal(env.requests[1].body.request_id,requestId);
  assert.equal(env.send.disabled,true);
  assert.equal(env.generation.isBlocked('retry-conversation'),true);
  assert.equal(env.generation.isBlocked('draft:independent'),false);
  await env.generation.refresh('retry-conversation');
  assert.equal(env.stateReads.at(-1),'/api/conversations/generation-state?conversation_id=retry-conversation');
  assert.equal(env.send.disabled,false);
}
console.log('PASS: real composer submit/navigation handlers isolate home, new conversations and existing conversations during pending requests.');

function agentHarness() {
  const messages = new Element('section');
  const previews = [];
  const context = vm.createContext({
    document: { createElement: tag => new Element(tag) },
    $: selector => { assert.equal(selector, '#agentMessages'); return messages; },
    esc: escapeHtml, icon: () => '', onPreviewAsset: asset => previews.push(asset),
    normalizeContentError: value => value,
  });
  vm.runInContext(sourceBetween(agentSource, '  function renderEvent(e)', '  function renderReview(el,review)')
    + '\nglobalThis.render = renderEvent;', context);
  return { messages, previews, render: event => context.render(event) };
}

// Both the initial image and later delivery event retain the exact asset object,
// so preview metadata (creator, generation settings, id) is not lost at the UI.
for (const eventKind of ['media', 'delivery']) {
  for (const assetKind of ['image', 'prop']) {
    const env = agentHarness();
    const asset = { id: 'asset-1', kind: assetKind, name: '山水 "原图"', url: '/uploads/result.png', creator: { id: 'creator-1' } };
    env.render({ kind: eventKind, asset, text: '生成完成', seq: 1, time: 1 });
    const button = env.messages.querySelector('.agent-image-preview');
    assert.ok(button, 'generated images expose a keyboard-operable preview button');
    assert.equal(button.tagName, 'button');
    assert.equal(button.getAttribute('type'), 'button');
    assert.ok(button.getAttribute('aria-label').includes(asset.name));
    assert.equal(button.querySelector('img').getAttribute('alt'), asset.name);
    button.onclick();
    assert.equal(env.previews[0], asset);
    assert.equal(env.previews.length, 1);
  }
}

// Natural dimensions arrive asynchronously. Tall images narrow their cards to
// stay within the 440px height; wide images stay within the 660px width.
for (const [width, height, expectedWidth] of [[900, 1600, 247.5], [1600, 900, 660], [1000, 1000, 440]]) {
  const env = agentHarness();
  env.render({ kind: 'media', asset: { id: 'ratio', kind: 'image', name: '原图', url: '/uploads/ratio.png' }, seq: 1, time: 1 });
  const card = env.messages.querySelector('.agent-media-card');
  const image = card.querySelector('img');
  assert.equal(card.style.values.has('--agent-media-width'), false, 'unknown dimensions must not impose a square ratio');
  image.naturalWidth = width;
  image.naturalHeight = height;
  image.events.get('load')();
  assert.equal(card.style.values.get('--agent-media-width'), `${expectedWidth}px`);
  assert.ok(expectedWidth * height / width <= 440 + 1e-9);
}

// Video and audio controls keep their native playback path.
for (const kind of ['video', 'audio']) {
  const env = agentHarness();
  env.render({ kind: 'media', asset: { id: kind, kind, name: kind, url: `/uploads/result.${kind === 'video' ? 'mp4' : 'wav'}` }, seq: 1, time: 1 });
  assert.equal(env.messages.querySelector('.agent-image-preview'), null);
  assert.ok(env.messages.querySelector(kind).hasAttribute('controls'));
  assert.equal(env.previews.length, 0);
}

console.log('PASS: Agent generated-image preview identity, accessible buttons, natural portrait/landscape sizing and native audio/video controls');

// These are source-level style contracts, not a replacement for browser layout
// verification. Intrinsic image dimensions must determine the displayed ratio;
// a requested generation ratio may still size the pending placeholder.
function declarations(css, selector) {
  for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (match[1].split(',').some(item => item.trim() === selector)) {
      return Object.fromEntries(match[2].split(';').filter(value => value.includes(':')).map(value => {
        const colon = value.indexOf(':');
        return [value.slice(0, colon).trim(), value.slice(colon + 1).trim()];
      }));
    }
  }
  assert.fail(`Missing image style boundary ${selector}`);
}

const conversationCss = read('../public/generation-conversation.css');
const imageStyle = declarations(conversationCss, '.creation-home.has-media-conversation:not(.agent-docked)>.home-results .home-image-results img');
assert.equal(imageStyle.height, 'auto', 'generated images must not inherit the fixed media-player height');
assert.equal(imageStyle.width, 'auto', 'a tall image must be allowed to narrow with its height limit');
assert.equal(imageStyle['aspect-ratio'], 'auto', 'the actual image ratio must replace the requested/fallback ratio after generation');
assert.equal(imageStyle.background, 'transparent', 'the source image must not acquire a letterbox background');
const homeButtonStyle = declarations(conversationCss, '.creation-home .home-results .home-image-preview');
assert.equal(homeButtonStyle.background, 'transparent');
assert.equal(homeButtonStyle.padding, '0');
const agentStyle = declarations(read('../public/agent-chat.css'), '.agent-media-card img');
assert.equal(agentStyle.height, 'auto');
assert.equal(agentStyle['aspect-ratio'], 'auto');
assert.equal(agentStyle.background, 'transparent');
const html = read('../public/index.html');
assert.ok(html.indexOf('generation-conversation.css') > html.indexOf('home-composer.css'), 'natural image sizing must load after the base media styles');
console.log('PASS: generated-image styles preserve intrinsic aspect ratio and transparent previews (source contract; no browser layout claim)');

// A just-generated image may not be in the asset-list cache yet. Exercise the
// real app callback, asset preview, and delete confirmation together so the
// preview's delete button cannot silently do nothing because byId() misses it.
{
  const appSource = read('../public/app.js'), librarySource = read('../public/asset-library.js');
  const modal = new Element('div'), state = { assets: [] }, requests = [], notifications = [];
  const fresh = imageAsset('freshly-generated');
  const original = { ...imageAsset('existing'), usage_count: 3, tags: ['已保存'] };
  state.assets.push(original);
  let context, closeCount = 0;
  const $ = (selector, root = modal) => root.querySelector(selector);
  context = vm.createContext({
    state, ui: { selected: new Set() }, $, $$: (selector, root = modal) => root.querySelectorAll(selector),
    byId: id => state.assets.find(asset => asset.id === id),
    esc: escapeHtml, safeUrl: escapeHtml, icon: () => '', loadingMarkup: label => `<span>${label}</span>`,
    kindOf: asset => asset.kind === 'prop' ? 'image' : asset.kind,
    TYPES: { image: ['图片', 'image'] }, bytes: () => '', sourceOf: () => 'generated',
    stopPreviews() {}, downloadAsset() {}, closeModal: () => { closeCount++; },
    showModal: content => { modal.innerHTML = content; },
    toast: message => notifications.push(message), formError: message => { $('#alFormError').textContent = message; },
    api: async (url, options) => {
      requests.push({ url, options });
      if (options?.method === 'DELETE') return {};
      return { ...fresh, creator_name: '测试创作者' };
    },
    refresh: async () => { state.assets = state.assets.filter(asset => asset.id !== fresh.id); },
    assetLibrary: { preview: asset => context.preview(asset) },
  });
  vm.runInContext([
    sourceBetween(appSource, 'function previewGeneratedAsset(asset)', 'function addLibraryAsset('),
    sourceBetween(librarySource, '  function preview(asset, onPick)', '  function editFolder('),
    sourceBetween(librarySource, '  function deleteAssets(ids)', '  function sendUpload('),
    'globalThis.preview=preview; globalThis.openGenerated=previewGeneratedAsset;',
  ].join('\n'), context);
  context.openGenerated({ ...original, usage_count: 0 });
  assert.equal(state.assets.length, 1);
  assert.equal(state.assets[0], original, 'opening an existing result must preserve richer asset cache metadata');
  context.openGenerated(fresh);
  assert.equal(state.assets[0], fresh);
  assert.equal(state.assets.length, 2);
  assert.ok($('#alPreviewDelete'));
  $('#alPreviewDelete').onclick();
  assert.ok($('#deleteConfirmTitle').textContent.includes('1'), 'fresh assets must reach the normal deletion confirmation');
  assert.equal(requests.filter(request => request.options?.method === 'DELETE').length, 0, 'opening preview or confirmation never deletes immediately');
  const confirm = $('#alDeleteConfirm');
  await confirm.onclick({ currentTarget: confirm });
  const deleted = requests.filter(request => request.options?.method === 'DELETE');
  assert.equal(deleted.length, 1);
  assert.equal(deleted[0].url, '/api/assets/freshly-generated');
  assert.equal(state.assets.some(asset => asset.id === fresh.id), false);
  assert.equal(state.assets[0], original);
  assert.equal(closeCount, 1);
  assert.deepEqual(notifications, ['已删除 1 项素材']);
}
console.log('PASS: newly generated preview assets populate the cache and reach the existing confirm-before-delete flow');
