// Real composer clicks with simulated generation, followed by backend validation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const root = path.resolve(__dirname, '..');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || path.join(root, 'outputs/canvas-test-runtime/playwright/driver/package'));

(async () => {
  const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true });
  const bodies = [];
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('http://localhost:18777/**', async route => {
      const file = new URL(route.request().url()).pathname;
      if (file === '/') return route.fulfill({ contentType: 'text/html', body: '<section id="agentView"><div class="agent-location"><span></span></div><div class="agent-welcome"></div><div class="agent-composer-wrap"><form id="agentComposer"><textarea id="agentInput"></textarea></form></div></section>' });
      const local = path.resolve(root, 'public', '.' + file);
      if (!local.startsWith(path.join(root, 'public') + path.sep)) return route.abort();
      if (!fs.existsSync(local)) return route.fulfill({ status: 404, body: '' });
      return route.fulfill({ path: local, contentType: file.endsWith('.js') ? 'application/javascript' : undefined });
    });
    for (const kind of ['image', 'audio', 'video']) {
      await page.goto('http://localhost:18777/');
      await page.evaluate(async kind => {
        const { createHomeComposer } = await import('/home-composer.js');
        const settings = { model: 'saved-provider-model', model_config_id: 'original-' + kind, reference_asset_ids: ['reference-a'], asset_ids: ['reference-a'],
          size: '2K', aspect_ratio: '16:9', count: 2, mode: kind === 'video' ? 'image-to-video' : 'voice', duration: 5, resolution: '720p',
          audio_config: { format: 'wav', speech_rate: 20 }, speaker: 'speaker-a', watermark: true, status: 'done', id: 'old-id', created_at: 1, request_id: 'old-request' };
        const reference = { id: 'reference-a', kind: 'image', name: 'reference.png', url: '/icon-32.png' };
        const result = { id: 'result-a', kind, name: '生成结果', url: '/icon-32.png' };
        const original = { id: kind + '-source', status: 'done', prompt: '原始完整描述\n第二行内容', created_at: 1, generation_settings: settings, model: settings.model, assets: [result], asset: result, video_url: result.url };
        window.submissions = []; window.toasts = [];
        const api = async (url, options) => {
          if (options?.method === 'POST') {
            const body = JSON.parse(options.body);
            submissions.push({ kind, body });
            return { ...original, id: kind + '-generated-' + submissions.length, generation_settings: undefined };
          }
          if (url === '/api/model-registry') return { models: [{ id: 'original-' + kind, kind, model_id: settings.model, name: '测试模型', api_key_set: true }] };
          if (url === '/api/assets') return [reference];
          if (url === '/api/conversations/history-' + kind) return { id: 'history-' + kind, title: '已有生成对话', kind, prompt: original.prompt, settings, tasks: [original] };
          throw new Error('Unexpected request: ' + url);
        };
        window.composer = createHomeComposer({ api, json: body => ({ method: 'POST', body: JSON.stringify(body) }), escapeHtml: s => String(s).replaceAll('<', '&lt;'),
          icon: () => '<svg></svg>', toast: message => toasts.push(message), state: { conversations: [] }, onConversations: async () => {}, onAssets: () => {}, onModels: () => {} });
        await composer.show(); await composer.openConversation('history-' + kind);
      }, kind);
      const button = page.locator(`[data-home-regenerate="${kind}-source"]`);
      await button.click();
      assert.deepEqual(await page.evaluate(() => toasts), [], 'first regeneration errors');
      await page.waitForFunction(() => submissions.length === 1);
      await page.locator(`[data-home-regenerate="${kind}-generated-1"]`).click();
      await page.waitForFunction(() => submissions.length === 2);
      const submitted = await page.evaluate(() => submissions);
      for (const item of submitted) {
        assert.equal(item.body.model_config_id, 'original-' + kind);
        assert.equal(item.body.prompt, '原始完整描述\n第二行内容');
        assert.equal(item.body.project_id, 'history-' + kind);
        assert.equal('status' in item.body, false);
        assert.equal('created_at' in item.body, false);
        assert.deepEqual(item.body.asset_ids || item.body.reference_asset_ids, ['reference-a']);
        if (kind !== 'video') assert.equal('model' in item.body, false);
      }
      assert.notEqual(submitted[0].body.request_id, submitted[1].body.request_id);
      assert.deepEqual(await page.evaluate(() => toasts), []);
      bodies.push(...submitted);
      await page.evaluate(() => composer.hide());
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
  const python = process.env.PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const validation = spawnSync(python, ['-X', 'utf8', '-c', String.raw`
import ast, json, re, sys
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from audio_service import AudioGenerationRequest
tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name in ('ImageGenerationRequest', 'Task')]
exec(compile(ast.Module(body=classes, type_ignores=[]), 'app.py', 'exec'))
validators = {'image': ImageGenerationRequest, 'audio': AudioGenerationRequest, 'video': Task}
for item in json.load(sys.stdin):
    validator = validators[item['kind']]
    assert set(item['body']) <= set(validator.model_fields), 'Extra request fields'
    validator.model_validate(item['body'])
print('PASS: all six captured requests satisfy the actual image/audio/video backend schemas.')
`], { cwd: root, input: JSON.stringify(bodies), encoding: 'utf8' });
  assert.equal(validation.status, 0, validation.stderr);
  console.log('PASS: reopened image/audio/video history -> regenerate -> regenerate result again; original model/prompt/references preserved, fresh IDs, no browser errors.');
  process.stdout.write(validation.stdout);
})().catch(error => { console.error(error); process.exitCode = 1; });
