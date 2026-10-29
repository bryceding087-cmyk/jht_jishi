import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../public/generation-state.js', import.meta.url), 'utf8');
const pending = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
function harness() {
  const requests = [], stops = [], changes = [];
  const context = vm.createContext({
    api: (url, options) => {
      if (url === '/api/generation/stop') {
        assert.equal(options.method, 'POST');
        const request = pending(); request.body = JSON.parse(options.body); stops.push(request); return request.promise;
      }
      assert.ok(url.startsWith('/api/conversations/generation-state?conversation_id='));
      assert.equal(options.background, true);
      const request = pending(); request.scope=decodeURIComponent(url.split('conversation_id=')[1]); requests.push(request); return request.promise;
    },
    onChange: state => changes.push(state),
  });
  vm.runInContext(source.replace(/^export /gm, '') + '\nglobalThis.guard=createGenerationGuard({api,onChange});', context);
  return { guard: context.guard, requests, stops, changes };
}

// A confirmed queue receipt frees the composer, while each POST stays exclusive.
for (const kind of ['image', 'video']) {
  const env=harness(),{guard}=env;
  assert.equal(guard.beginSubmission('draft:queue'),true);
  guard.finishSubmission('draft:queue',{id:'queued',status:'queued',queue_state:'waiting'},kind,'queue-conversation');
  assert.equal(guard.isBlocked('queue-conversation'),false);
  assert.equal(guard.beginSubmission('queue-conversation'),true);
  assert.equal(guard.beginSubmission('queue-conversation'),false);
  guard.finishSubmission('queue-conversation',{id:'queued-2',status:'queued',queue_state:'waiting'},kind,'queue-conversation');
  await answer(env,{is_busy:true,allow_queue:true,task:{id:'queued',status:'running'}},'queue-conversation');
  assert.equal(guard.isBlocked('queue-conversation'),false);
}
async function answer(env, state, scope='conversation-1') {
  const result = env.guard.refresh(scope);
  env.requests.at(-1).resolve(state);
  assert.equal(await result, true);
}

// An existing conversation restores only its own pending task state.
{
  const env = harness(), { guard } = env;
  assert.equal(guard.isBlocked('conversation-1'), true);
  assert.equal(guard.beginSubmission('conversation-1'), false);
  await answer(env, { is_busy: true, conversation_id: 'conversation-1', task: { id: 'restored', kind: 'video', status: 'running' } });
  assert.equal(guard.isBlocked('conversation-1'), true);
  assert.equal(guard.snapshot('conversation-1').task.id, 'restored');
  assert.equal(guard.beginSubmission('conversation-1'), false);
  await answer(env, { is_busy: false, task: null });
  assert.equal(guard.isBlocked('conversation-1'), false);
}

// Each media kind prevents double submission within its own conversation.
for (const kind of ['image', 'video', 'audio']) {
  const env = harness(), { guard } = env;
  await answer(env, { is_busy: false });
  assert.equal(guard.beginSubmission('conversation-1'), true);
  assert.equal(guard.isSubmitting('conversation-1'), true);
  assert.equal(guard.beginSubmission('conversation-1'), false, 'a second click cannot enter before the first request awaits');
  assert.equal(await guard.refresh('conversation-1'), false, 'an in-flight submission cannot be cleared by a state read');
  assert.equal(env.requests.length, 1);
  guard.finishSubmission('conversation-1', { id: `${kind}-task`, status: 'queued' }, kind, 'conversation-1');
  assert.equal(guard.isSubmitting('conversation-1'), false);
  assert.equal(guard.isBlocked('conversation-1'), true);
  assert.equal(guard.snapshot('conversation-1').task.kind, kind);
  assert.equal(guard.beginSubmission('conversation-1'), false, 'the same conversation remains blocked while generating');
  const refresh = guard.refresh('conversation-1'); env.requests.at(-1).reject(new Error('offline'));
  await assert.rejects(refresh, /offline/);
  assert.equal(guard.isBlocked('conversation-1'), true, 'a polling network error is not a terminal task status');
  await answer(env, { is_busy: false });
  assert.equal(guard.isBlocked('conversation-1'), false);
  assert.equal(guard.beginSubmission('conversation-1'), true);
  guard.finishSubmission('conversation-1', { id: 'failed-task', status: 'failed' }, kind, 'conversation-1');
  assert.equal(guard.isBlocked('conversation-1'), false, 'a confirmed failed response is terminal');
}

// Lost POST responses remain locked until the server reconciles their outcome.
{
  const env = harness(), { guard } = env;
  await answer(env, { is_busy: false });
  guard.beginSubmission('conversation-1'); guard.finishSubmission('conversation-1');
  assert.equal(guard.isBlocked('conversation-1'), true);
  assert.equal(guard.snapshot('conversation-1').known, false);
  const refresh = guard.refresh('conversation-1'); env.requests.at(-1).reject(new Error('still offline'));
  await assert.rejects(refresh, /still offline/);
  assert.equal(guard.isBlocked('conversation-1'), true);
  await answer(env, { is_busy: true, task: { id: 'accepted-despite-disconnect', kind: 'audio', status: 'running' } });
  assert.equal(guard.isBlocked('conversation-1'), true);
  await answer(env, { is_busy: false });
  assert.equal(guard.isBlocked('conversation-1'), false);
}

// A read started before POST cannot erase a newer queued task or submission.
{
  const env = harness(), { guard } = env;
  await answer(env, { is_busy: false });
  const stale = guard.refresh('conversation-1'), staleRequest = env.requests.at(-1);
  guard.beginSubmission('conversation-1'); guard.finishSubmission('conversation-1', { id: 'new-task', status: 'running' }, 'image', 'conversation-1');
  staleRequest.resolve({ is_busy: false });
  assert.equal(await stale, false);
  assert.equal(guard.isBlocked('conversation-1'), true);
  assert.equal(guard.snapshot('conversation-1').task.id, 'new-task');
  const older = guard.refresh('conversation-1'), olderRequest = env.requests.at(-1);
  const newer = guard.refresh('conversation-1'), newerRequest = env.requests.at(-1);
  newerRequest.resolve({ is_busy: false }); assert.equal(await newer, true);
  olderRequest.resolve({ is_busy: true, task: { id: 'new-task' } }); assert.equal(await older, false);
  assert.equal(guard.isBlocked('conversation-1'), false, 'out-of-order responses cannot restore a completed task lock');
}

// Returning from a hidden view checks the server again; invalid/unknown data fails closed.
{
  const env = harness(), { guard } = env;
  await answer(env, { is_busy: false });
  const oldRead = guard.refresh('conversation-1'), oldRequest = env.requests.at(-1);
  guard.requireRefresh('conversation-1');
  oldRequest.resolve({ is_busy: false }); assert.equal(await oldRead, false);
  assert.equal(guard.isBlocked('conversation-1'), true);
  const invalid = guard.refresh('conversation-1'); env.requests.at(-1).resolve({});
  await assert.rejects(invalid, /无法确认/);
  assert.equal(guard.isBlocked('conversation-1'), true);
  await answer(env, { is_busy: false });
  guard.beginSubmission('conversation-1'); guard.finishSubmission('conversation-1', { id: 'future-status', status: 'processing' }, 'image', 'conversation-1');
  assert.equal(guard.isBlocked('conversation-1'), true, 'an unrecognized status is not assumed to be terminal');
  await answer(env, { is_busy: false });
  for (const status of ['done', 'succeeded', 'failed', 'blocked', 'cancelled', 'canceled', 'interrupted']) {
    assert.equal(guard.beginSubmission('conversation-1'), true);
    guard.finishSubmission('conversation-1', { id: `task-${status}`, status }, 'image', 'conversation-1');
    assert.equal(guard.isBlocked('conversation-1'), false);
  }
}

console.log('PASS: generation restore, synchronous same-conversation exclusion, failed/complete unlock, unknown outcomes, polling failures, stale response protection and hidden-view refresh.');

// A running conversation never blocks another conversation or a fresh draft.
{
  const env = harness(), { guard } = env;
  await answer(env, { is_busy: true, task: { id: 'task-a', status: 'running' } }, 'conversation-a');
  await answer(env, { is_busy: false }, 'conversation-b');
  assert.equal(guard.isBlocked('conversation-a'), true);
  assert.equal(guard.beginSubmission('conversation-b'), true);
  assert.equal(guard.beginSubmission('draft:new'), true);
  assert.equal(guard.beginSubmission('draft:new'), false);
  guard.bindConversation('draft:new', 'conversation-new');
  assert.equal(guard.isBlocked('conversation-new'), true, 'opening the newly created conversation during POST retains the draft gate');
  guard.finishSubmission('conversation-b', { id: 'task-b', status: 'queued' }, 'audio', 'conversation-b');
  guard.finishSubmission('draft:new', { id: 'task-new', status: 'queued' }, 'image', 'conversation-new');
  assert.equal(guard.isBlocked('conversation-new'), true);
  assert.equal(guard.isBlocked('draft:another'), false);
  await answer(env, { is_busy: false }, 'conversation-a');
  assert.equal(guard.isBlocked('conversation-a'), false);
  assert.equal(guard.isBlocked('conversation-b'), true);
  assert.equal(guard.isBlocked('conversation-new'), true);
  assert.deepEqual(env.requests.map(r => r.scope), ['conversation-a', 'conversation-b', 'conversation-a']);
}

// A stale read of the new ID must not erase a POST bound from a draft.
{
  const env = harness(), { guard } = env;
  const stale = guard.refresh('new-id'), request = env.requests.at(-1);
  guard.beginSubmission('draft:one');
  guard.bindConversation('draft:one', 'new-id');
  request.resolve({ is_busy: false });
  assert.equal(await stale, false);
  assert.equal(guard.isSubmitting('new-id'), true);
  guard.finishSubmission('draft:one', undefined, 'image', 'new-id');
  assert.equal(guard.isBlocked('new-id'), true, 'lost task response reconciles only the known conversation');
  assert.equal(guard.isBlocked('draft:two'), false);
  await answer(env, { is_busy: true, task: { id: 'accepted', status: 'running' } }, 'new-id');
  assert.equal(guard.snapshot('draft:one').task.id, 'accepted');
}

// Failed conversation creation has no submitted generation yet; allow an
// idempotent retry on the draft instead of requiring a nonexistent state URL.
{
  const env = harness(), { guard } = env;
  guard.requireRefresh('draft:one');
  assert.equal(guard.beginSubmission('draft:one'), true);
  guard.finishSubmission('draft:one');
  assert.equal(guard.beginSubmission('draft:one'), true);
  assert.equal(await guard.refresh('draft:two'), false);
  assert.equal(env.requests.length, 0);
}
console.log('PASS: independent conversation/draft submissions, draft binding, navigation races and creation retry.');

// Stop is available before either conversation creation or generation POST
// returns. Double clicks share one request, and late results cannot own a new send.
for (const kind of ['image', 'video', 'audio']) {
  const env = harness(), {guard} = env, scope = 'draft:stop-' + kind;
  guard.beginSubmission(scope, {requestId: 'old-request', kind});
  assert.equal(guard.isActive(scope), true);
  const first = guard.stop(scope), second = guard.stop(scope);
  assert.equal(first, second);
  assert.equal(guard.snapshot(scope).stopping, true);
  assert.equal(guard.wasStopped(scope, 'old-request'), true, 'pending stop must already protect the draft');
  await Promise.resolve();
  assert.equal(env.stops.length, 1);
  assert.deepEqual(env.stops[0].body, {request_id: 'old-request', kind});
  env.stops[0].resolve({status: 'stopped', tasks: []});
  await first;
  assert.equal(guard.isActive(scope), false);
  assert.equal(guard.beginSubmission(scope, {requestId: 'new-request', kind}), true);
  assert.equal(guard.bindConversation(scope, 'late-old-conversation', 'old-request'), false);
  assert.equal(guard.finishSubmission(scope, {id: 'late-old-task', status: 'running'}, kind, 'late-old-conversation', 'old-request'), false);
  assert.equal(guard.snapshot(scope).request.request_id, 'new-request');
  assert.equal(guard.isSubmitting(scope), true);
  guard.finishSubmission(scope, {id: 'new-task', status: 'running'}, kind, 'new-conversation', 'new-request');
  assert.equal(guard.snapshot(scope).task.id, 'new-task');
}

// Restored tasks supply the exact original request, never the current draft's
// new ID. Stopping one conversation has no effect on another conversation.
{
  const env = harness(), {guard} = env;
  await answer(env, {is_busy: true, allow_queue: true, task: {id: 'restored-task', kind: 'video', status: 'running', request_id: 'restored-request'}}, 'old-conversation');
  guard.beginSubmission('draft:other', {requestId: 'other-request', kind: 'image'});
  const stale = guard.refresh('old-conversation'), staleRequest = env.requests.at(-1);
  const stopping = guard.stop('old-conversation');
  await Promise.resolve();
  assert.deepEqual(env.stops[0].body, {request_id: 'restored-request', task_id: 'restored-task', kind: 'video', conversation_id: 'old-conversation'});
  env.stops[0].resolve({status: 'stopped'}); await stopping;
  staleRequest.resolve({is_busy: true, task: {id: 'restored-task', kind: 'video'}});
  assert.equal(await stale, false);
  assert.equal(guard.snapshot('draft:other').request.request_id, 'other-request');
  assert.equal(guard.isSubmitting('draft:other'), true);
  assert.equal(guard.isActive('old-conversation'), false);
}

// A failed stop remains active and retryable. Old restored records without a
// request ID can use the task ID without inventing a new request identity.
{
  const env = harness(), {guard} = env;
  await answer(env, {is_busy: true, task: {id: 'legacy-audio', kind: 'audio', status: 'running'}});
  const stopping = guard.stop('conversation-1'); await Promise.resolve();
  assert.deepEqual(env.stops[0].body, {task_id: 'legacy-audio', kind: 'audio', conversation_id: 'conversation-1'});
  env.stops[0].reject(new Error('stop offline')); await assert.rejects(stopping, /stop offline/);
  assert.equal(guard.isActive('conversation-1'), true);
  assert.equal(guard.snapshot('conversation-1').stopping, false);
  const retry = guard.stop('conversation-1'); await Promise.resolve();
  assert.equal(env.stops.length, 2);
  env.stops[1].resolve({status: 'stopped'}); await retry;
  assert.equal(guard.isBlocked('conversation-1'), false);
}
console.log('PASS: request-scoped stop before task IDs, double-click deduplication, retained drafts, exact restored IDs, failure retry and late-result isolation.');
