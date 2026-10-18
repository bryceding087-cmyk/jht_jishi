import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Opening a saved canvas must restore completed previews as well as resume
// pending status reads, including completed videos still being saved as assets.
const source = readFileSync(new URL('../public/app.js', import.meta.url), 'utf8');
const tasks = [
  { id: 'complete', status: 'done', video_url: '/uploads/video.mp4' },
  { id: 'generating', status: 'running', provider_task_id: 'existing-provider-task' },
  { id: 'saving', status: 'done', video_url: '/uploads/saving.mp4', auto_save_video: true, asset_save_status: 'pending' },
].map((task, index) => ({ ...task, updated_at: index + 1, params: {
  node_id: 'node-' + task.id, prompt: 'fixture', model: 'fixture-model', resolution: '768p', duration: 4,
} }));
const updates = [], polls = [];
const history = { innerHTML: '', querySelectorAll: () => [] };
const state = { project: { id: 'saved-project' }, nodeTasks: {} };
const env = vm.createContext({ state,
  api: async url => { assert.equal(url, '/api/tasks?project_id=saved-project'); return tasks; },
  updateTaskNode: (task, nodeId) => updates.push([task.id, nodeId]),
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
console.log('PASS: saved canvas restores completed video previews and resumes generation/asset-saving polls');
