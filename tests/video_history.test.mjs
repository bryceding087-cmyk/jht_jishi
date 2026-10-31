import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Opening a saved canvas must restore completed previews as well as resume
// pending status reads, including completed videos still downloading to history.
const source = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const tasks = [
  { id: 'complete', status: 'done', video_url: '/uploads/video.mp4' },
  { id: 'generating', status: 'running', provider_task_id: 'existing-provider-task' },
  { id: 'saving', status: 'done', video_url: '/uploads/saving.mp4', video_cache_status: 'pending', asset_save_status: 'history' },
].map((task, index) => ({ ...task, created_at: index + 1, updated_at: index + 1, params: {
  node_id: 'node-' + task.id, prompt: 'fixture', model: 'fixture-model', resolution: '768p', duration: 4,
} }));
const updates = [], polls = [];
const history = { innerHTML: '', querySelectorAll: () => [] };
const state = { project: { id: 'saved-project' }, nodeTasks: {} };
const env = vm.createContext({ state, videoRequests: new Map(),
  videoSubmissionKey: (nodeId, projectId) => `${projectId}:${nodeId}`,
  stoppedTask: task => !!task?.local_stop_requested,
  api: async url => { assert.equal(url, '/api/tasks?project_id=saved-project'); return tasks; },
  updateTaskNode: (task, nodeId) => updates.push([task.id, nodeId]),
  syncVideoGenerateButton: () => {},
  pollTask: (taskId, nodeId) => polls.push([taskId, nodeId]),
  $: () => history, escapeHtml: value => value, statusText: value => value,
  taskErrorHtml: () => '', taskRetryHtml: () => '',
});
vm.runInContext(source.slice(source.indexOf('async function loadHistory()'), source.indexOf('async function retryTask(')), env);
await env.loadHistory();
assert.equal(updates.length, 3);
assert.ok(updates.some(([id, node]) => id === 'complete' && node === 'node-complete'));
assert.deepEqual(polls.map(([id]) => id).sort(), ['generating', 'saving']);
assert.equal(state.nodeTasks['node-complete'].video_url, '/uploads/video.mp4');
console.log('PASS: saved canvas restores completed video previews and resumes generation/file-download polls');

// Saving an old result to the library updates its timestamp, but must not
// replace the newer generation or enable the button while any task is active.
tasks.push({ ...tasks[1], id: 'older-saved', status: 'done', created_at: 1, updated_at: 99 });
await env.loadHistory();
assert.equal(state.nodeTasks['node-generating'].id, 'generating');
tasks.push({ ...tasks[1], id: 'newer-complete', status: 'done', created_at: 100, updated_at: 100 });
await env.loadHistory();
assert.equal(state.nodeTasks['node-generating'].id, 'generating');
tasks[1].status = 'done';
tasks[1].updated_at = 200;
await env.loadHistory();
assert.equal(state.nodeTasks['node-generating'].id, 'newer-complete');
console.log('PASS: active generations take priority; a late library save cannot replace the newest completed result');

let resolveHistory;
env.api = () => new Promise(resolve => { resolveHistory = resolve; });
const loading = env.loadHistory();
state.project = { id: 'another-project' };
state.nodeTasks = {};
const previousHtml = history.innerHTML;
resolveHistory(tasks);
await loading;
assert.deepEqual(state.nodeTasks, {});
assert.equal(history.innerHTML, previousHtml);
console.log('PASS: history arriving after a project switch cannot overwrite the new canvas');

// Both the compact canvas badge and full task details expose uncertainty.
vm.runInContext(source.slice(source.indexOf('function statusText('), source.indexOf('\n', source.indexOf('function statusText('))), env);
assert.equal(env.statusText('running', {submit_unknown:true}, true), '提交结果待确认');
assert.equal(env.statusText('running', {submit_unknown:true}, false), '提交结果待确认');
assert.equal(env.statusText('cancelled', {submit_unknown:true}, true), '已停止');
console.log('PASS: uncertain video submissions have consistent labels and retain stop status');
assert.equal(env.statusText('failed', {queue_timed_out_at:2000,local_stop_requested:true}, true), '任务超时');
assert.equal(env.statusText('failed', {}, true), '生成失败');
console.log('PASS: queue expiry is labeled as a timeout; explicit cancellation remains stopped');
