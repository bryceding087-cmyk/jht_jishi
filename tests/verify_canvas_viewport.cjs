// Browser regression using disposable storage; no real model requests.
// Run with Node 20+. PLAYWRIGHT_MODULE may point to a local Playwright package.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const root = path.resolve(__dirname, '..');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || path.join(root, 'outputs/canvas-test-runtime/playwright/driver/package'));
const python = process.env.PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const { fixture } = require('./browser_fixture.cjs');

(async () => {
  const child = spawn(python, ['-u', '-c', fixture], { cwd: root, stdio: ['pipe', 'pipe', 'inherit'] });
  let browser;
  const exited = new Promise(resolve => child.once('exit', resolve));
  try {
    const setup = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture startup timed out')), 20000);
      const lines = createInterface({ input: child.stdout });
      lines.on('line', line => {
        if (line.startsWith('FIXTURE ')) { clearTimeout(timer); resolve(JSON.parse(line.slice(8))); }
      });
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); reject(new Error('Fixture exited: ' + code)); });
    });
    browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const { origin, assets } = setup;
    const login = await context.request.post(origin + '/api/auth/login', { headers: { Origin: origin }, data: { username: 'admin', password: 'viewport-test', remember: false } });
    assert.ok(login.ok(), await login.text());
    const viewport = { x: -180, y: 80, zoom: 1.35 };
    const nodes = [{ id: 'picture', type: 'asset', title: '参考图', assetId: assets[0].id, x: 300, y: 230 }];
    for (let i = 0; i < 20; i++) nodes.push({ id: 'far-' + i, type: 'prompt', title: '远处节点 ' + i, x: 3000 + i * 400, y: i * 450 });
    const created = await context.request.post(origin + '/api/projects', { headers: { Origin: origin }, data: { name: '视角保持验证', nodes, edges: [], viewport } });
    assert.ok(created.ok(), await created.text());
    const project = await created.json();
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    // Expose production operations only in this isolated browser response.
    await page.route(/\/app\.js\?/, route => route.fulfill({ contentType: 'application/javascript', body: fs.readFileSync(path.join(root, 'public/app.js'), 'utf8') + '\nwindow.viewportTest = {state, appendNode, selectAsset, expandScriptShots, openScriptWorkflow, setZoom, updateSelection, renderNodes, flushSave, deleteNodes, scheduleSave, canvasHistory, canvasGroups, selectProject};' }));
    await page.goto(origin + '/?project=' + project.id);
    await page.waitForFunction(() => window.viewportTest?.state.project && !viewportTest.state.switchingProject && document.querySelector('[data-node="picture"]'));
    const snapshot = () => page.evaluate(() => ({ zoom: viewportTest.state.zoom, x: viewportTest.state.panX, y: viewportTest.state.panY, transform: document.querySelector('#canvasStage').style.transform }));
    const initial = await snapshot();
    assert.deepEqual({ x: initial.x, y: initial.y, zoom: initial.zoom }, viewport);

    if (process.argv.includes('--undo')) {
      const settle = () => page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      const graph = () => page.evaluate(() => ({
        nodes: viewportTest.state.nodes.map(n => ({ id: n.id, x: n.x, y: n.y, title: n.title })),
        edges: viewportTest.state.edges,
        groups: viewportTest.state.groups.map(g => ({ id: g.id, title: g.title, color: g.color, node_ids: g.node_ids }))
      }));
      await settle();
      const states = [await graph()];
      await page.evaluate(() => { const t = viewportTest; t.state.selectedNodeIds = new Set(['picture', 'far-0']); t.state.selectedGroupId = null; });
      await page.keyboard.press('Control+g'); await settle();
      states.push(await graph());
      assert.equal(states[1].groups.length, 1);
      const label = page.locator('.canvas-group-name');
      await label.dblclick();
      await label.locator('input').fill('测试分组');
      await label.locator('input').press('Enter'); await settle(); states.push(await graph());
      await page.locator('.group-color-trigger').click();
      await page.locator('[data-group-color="#4285f4"]').click(); await settle(); states.push(await graph());
      const groupName = await label.boundingBox();
      await page.mouse.move(groupName.x + groupName.width / 2, groupName.y + groupName.height / 2); await page.mouse.down();
      await page.mouse.move(groupName.x + groupName.width / 2 + 60, groupName.y + groupName.height / 2 + 30, { steps: 5 }); await page.mouse.up(); await settle(); states.push(await graph());
      assert.notEqual(states.at(-1).nodes[0].x, states.at(-2).nodes[0].x, 'group drag moves its children');
      await page.evaluate(() => viewportTest.updateSelection('picture'));
      const picture = await page.locator('[data-node="picture"] .frame-stage').boundingBox();
      await page.mouse.move(picture.x + 40, picture.y + 40); await page.mouse.down();
      await page.mouse.move(picture.x + 120, picture.y + 80, { steps: 5 }); await page.mouse.up(); await settle();
      states.push(await graph());
      assert.notEqual(states.at(-1).nodes[0].x, states.at(-2).nodes[0].x);
      await page.keyboard.press('Delete'); await settle(); states.push(await graph());
      assert.equal(states.at(-1).nodes.some(n => n.id === 'picture'), false);
      await page.evaluate(() => viewportTest.updateSelection('far-0'));
      await page.keyboard.press('Delete'); await settle(); states.push(await graph());
      assert.equal(states.at(-1).groups.length, 0);
      for (let i = states.length - 2; i >= 0; i--) {
        await page.keyboard.press('Control+z'); await settle();
        assert.deepEqual(await graph(), states[i], 'undo mixed operation index ' + i);
        assert.deepEqual(await snapshot(), initial, 'undo keeps viewport');
      }
      for (let i = 1; i < states.length; i++) {
        await page.keyboard.press(i % 2 ? 'Control+Shift+z' : 'Control+y'); await settle();
        assert.deepEqual(await graph(), states[i], 'redo mixed operation index ' + i);
      }
      await page.keyboard.press('Control+z'); await settle();
      await page.evaluate(() => viewportTest.appendNode('prompt', { title: '新分支', x: 600, y: 600 })); await settle();
      const branch = await graph();
      await page.keyboard.press('Control+Shift+z'); await settle();
      assert.deepEqual(await graph(), branch, 'new edit invalidates redo');
      await page.keyboard.press('Control+z'); await settle();
      assert.deepEqual(await graph(), states.at(-2), 'new node undo');
      await page.keyboard.press('Control+Shift+z'); await settle();
      assert.deepEqual(await graph(), branch, 'new node redo');
      await page.evaluate(() => { const editor = document.createElement('div'); editor.id = 'nativeEditor'; editor.contentEditable = 'plaintext-only'; editor.textContent = '测试编辑'; document.body.append(editor); editor.focus(); });
      await page.keyboard.type('text'); await page.keyboard.press('Control+z'); await settle();
      assert.deepEqual(await graph(), branch, 'text editor undo does not change canvas');
      await page.evaluate(() => document.querySelector('#nativeEditor').remove());
      await page.evaluate(() => viewportTest.flushSave());
      const persisted = await (await context.request.get(origin + '/api/projects/' + project.id)).json();
      assert.deepEqual(persisted.viewport, viewport);
      const second = await context.request.post(origin + '/api/projects', { headers: { Origin: origin }, data: { name: '另一个项目', nodes: [], edges: [] } });
      const secondProject = await second.json();
      await page.evaluate(id => viewportTest.selectProject(id), secondProject.id);
      await page.keyboard.press('Control+z'); await settle();
      assert.equal((await graph()).nodes.length, 0, 'undo cannot cross projects');
      assert.deepEqual(errors, []);
      console.log('PASS: actual shortcuts for group -> node drag -> two deletes, undo/redo in exact order, branch invalidation, node add undo/redo, native text undo, viewport and persistence, project isolation; no browser errors.');
      return;
    }

    await page.locator('[data-node="picture"] .frame-name').click();
    await page.locator('#nodeContextToolbar [data-context="reference"]').click();
    await page.waitForFunction(() => viewportTest.state.nodes.some(n => n.type === 'video'));
    assert.deepEqual(await snapshot(), initial, 'image reference preserves viewport');
    const graph = await page.evaluate(() => ({ nodes: viewportTest.state.nodes, edges: viewportTest.state.edges }));
    const video = graph.nodes.find(n => n.type === 'video');
    assert.ok(graph.edges.some(e => e.from === 'picture' && e.to === video.id));
    assert.ok(video.x > 600, 'reference node is placed to the right of its source');

    // Repeated clicks must allocate empty space without moving existing nodes.
    const originalPositions = graph.nodes.map(n => ({ id: n.id, x: n.x, y: n.y }));
    for (let i = 0; i < 3; i++) {
      await page.locator('[data-node="picture"] .frame-name').click();
      await page.locator('#nodeContextToolbar [data-context="reference"]').click();
      await page.waitForFunction(expected => viewportTest.state.nodes.filter(n => n.type === 'video').length === expected, i + 2);
    }
    const placement = await page.evaluate(() => ({ nodes: viewportTest.state.nodes, edges: viewportTest.state.edges }));
    const referenceVideos = placement.nodes.filter(n => placement.edges.some(e => e.from === 'picture' && e.to === n.id));
    assert.equal(referenceVideos.length, 4);
    for (let i = 0; i < referenceVideos.length; i++) {
      for (let j = i + 1; j < referenceVideos.length; j++) {
        assert.ok(Math.abs(referenceVideos[i].y - referenceVideos[j].y) >= 398, 'space reserved for portrait output and toolbar');
      }
    }
    for (const original of originalPositions) {
      const current = placement.nodes.find(n => n.id === original.id);
      assert.deepEqual({ id: current.id, x: current.x, y: current.y }, original, 'existing nodes stay in place');
    }
    assert.deepEqual(await snapshot(), initial, 'repeated references preserve viewport');

    // A different node can occupy the next slot, and palette coordinates can be
    // stale. Neither may force the newly generated video onto an existing node.
    const blockedPosition = await page.evaluate(last => {
      const t = viewportTest;
      const blocker = t.appendNode('audio', { x: last.x - 100, y: last.y + 398 });
      t.state.pendingNodePosition = { x: last.x, y: last.y };
      return { x: blocker.x, y: blocker.y };
    }, referenceVideos.at(-1));
    await page.locator('[data-node="picture"] .frame-name').click();
    await page.locator('#nodeContextToolbar [data-context="reference"]').click();
    await page.waitForFunction(() => viewportTest.state.nodes.filter(n => n.type === 'video').length === 5);
    const lastVideo = await page.evaluate(() => viewportTest.state.nodes.filter(n => n.type === 'video').at(-1));
    assert.ok(lastVideo.y >= blockedPosition.y + 284 + 64, 'skips differently sized nearby node');
    assert.deepEqual(await snapshot(), initial, 'collision avoidance preserves viewport');

    // Completed-video path awaits /from-task before creating its reference node.
    const reference = { id: 'test-video-reference', kind: 'video', name: '已生成视频', url: '/uploads/test-reference.mp4' };
    await page.route('**/api/assets/from-task', route => route.fulfill({ json: reference }));
    await page.route('**/uploads/test-reference.mp4', route => route.fulfill({ status: 404, body: '' }));
    await page.evaluate(id => {
      const t = viewportTest;
      t.state.nodeTasks[id] = { id: 'completed-test-task', status: 'done', video_url: '/uploads/test-reference.mp4' };
      t.updateSelection(id); t.renderNodes();
    }, video.id);
    await page.locator('#nodeContextToolbar [data-context="reference"]').click();
    await page.waitForFunction(() => viewportTest.state.nodes.filter(n => n.type === 'video').length === 6);
    assert.deepEqual(await snapshot(), initial, 'completed video reference preserves viewport');

    const offscreen = await page.evaluate(id => {
      const t = viewportTest, video = t.appendNode('video', { x: -5000, y: 4000 });
      t.selectAsset(id, video);
      return t.state.nodes.find(n => n.assetId === id).x;
    }, assets[1].id);
    assert.ok(offscreen < -1000, 'reference is outside current viewport');
    assert.deepEqual(await snapshot(), initial, 'offscreen asset reference preserves viewport');

    await page.evaluate(() => {
      const t = viewportTest;
      const node = t.appendNode('script', { x: 500, y: 300, script_data: { title: '测试分镜', scenes: [{ index: 1, duration: 5, description: '镜头一', final_prompt: '测试镜头一' }, { index: 2, duration: 5, description: '镜头二', final_prompt: '测试镜头二' }], resources: [] } });
      t.expandScriptShots(node);
      if (t.state.nodes.filter(n => n.scriptSourceId === node.id).length !== 2) throw new Error('Shots not expanded');
      t.openScriptWorkflow(node.id);
      t.state.scriptWorkflow.revealResources = true;
      document.querySelector('#scriptWorkflowClose').click();
    });
    assert.deepEqual(await snapshot(), initial, 'expand shots and close editor preserve viewport');
    await page.evaluate(() => viewportTest.flushSave());
    const saved = await (await context.request.get(origin + '/api/projects/' + project.id)).json();
    assert.deepEqual(saved.viewport, viewport, 'saved viewport unchanged');
    for (const placed of [...referenceVideos, lastVideo]) {
      const stored = saved.nodes.find(n => n.id === placed.id);
      assert.deepEqual({ x: stored.x, y: stored.y }, { x: placed.x, y: placed.y }, 'separate reference positions persist');
    }
    await page.reload();
    await page.waitForFunction(() => viewportTest?.state.project && document.querySelector('[data-node="picture"]'));
    assert.deepEqual(await snapshot(), initial, 'reload restores same viewport');
    await page.keyboard.press('Control+0');
    assert.notDeepEqual(await snapshot(), initial, 'explicit fit still changes viewport');
    assert.deepEqual(errors, []);
    console.log('PASS: repeated references avoid overlap, existing nodes/viewport unchanged, nearby obstacles and stale palette position handled, image/video reference, offscreen asset reference, shot expansion, editor close, persistence/reload, explicit fit; no browser errors.');
  } finally {
    await browser?.close();
    child.stdin.end('stop\n');
    await exited;
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
