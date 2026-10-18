const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const { createInterface } = require('node:readline');
const { fixture } = require('./browser_fixture.cjs');
const root = path.resolve(__dirname, '..');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || path.join(root, 'outputs/canvas-test-runtime/playwright/driver/package'));
const python = process.env.PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

(async () => {
  const child = spawn(python, ['-u', '-c', fixture], { cwd: root, stdio: ['pipe', 'pipe', 'inherit'] });
  const exited = new Promise(resolve => child.once('exit', resolve));
  let browser;
  try {
    const { origin } = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Fixture startup timed out')), 20000);
      createInterface({ input: child.stdout }).on('line', line => {
        if (line.startsWith('FIXTURE ')) { clearTimeout(timer); resolve(JSON.parse(line.slice(8))); }
      });
      child.once('error', error => { clearTimeout(timer); reject(error); });
      child.once('exit', code => { clearTimeout(timer); reject(new Error('Fixture exited: ' + code)); });
    });
    browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.request.post(origin + '/api/auth/login', { headers: { Origin: origin }, data: { username: 'admin', password: 'viewport-test' } });
    const response = await context.request.post(origin + '/api/projects', { headers: { Origin: origin }, data: { name: '慢网验证项目', nodes: [{ id: 'n', type: 'prompt', x: 100, y: 100 }], edges: [], viewport: { x: 10, y: 20, zoom: .8 } } });
    const project = await response.json();
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route(/\/app\.js\?/, route => route.fulfill({ contentType: 'application/javascript', body: fs.readFileSync(path.join(root, 'public/app.js'), 'utf8') + '\nwindow.loadingTest = {state, api, selectProject, openConversation, imageGenerator, audioGenerator};' }));
    let slowProjects = true, failFolders = false, slowAssets = false, failAssets = false, slowModel = false;
    await page.route(origin + '/api/projects', async route => { const response = await route.fetch(); if (slowProjects) await delay(700); await route.fulfill({ response }); });
    await page.route(origin + '/api/project-folders', async route => {
      if (failFolders) return route.fulfill({ status: 503, json: { detail: '暂时无法读取项目文件夹' } });
      await route.continue();
    });
    await page.route(origin + '/api/assets', async route => {
      if (slowAssets) await delay(700);
      if (failAssets) return route.fulfill({ status: 503, json: { detail: '暂时无法读取资产' } });
      await route.continue();
    });
    await page.route(origin + '/api/model-registry', async route => { if (slowModel) await delay(700); await route.continue(); });
    await page.goto(origin + '/?view=projects', { waitUntil: 'domcontentloaded' });
    await page.locator('#appBootScreen .app-boot-loader').waitFor({ state: 'visible' });
    await page.locator('#appBootScreen').waitFor({ state: 'detached' });
    await page.locator('.projects-library[aria-busy=true]').waitFor({ state: 'visible' });
    assert.equal(await page.locator('.projects-loading, .app-network-loading').count(), 0);
    await page.locator('.projects-library[aria-busy=false]').waitFor({ state: 'visible' });
    assert.equal(await page.locator('[data-project-card]').count(), 1);
    failFolders = true;
    await page.locator('#mainNav [data-view=projects]').click();
    await page.locator('.projects-load-error').waitFor({ state: 'visible' });
    assert.match(await page.locator('.projects-load-error').textContent(), /暂时无法读取/);
    assert.equal(await page.locator('.projects-loading').count(), 0);
    failFolders = false;
    await page.locator('[data-project-action=retry]').click();
    await page.locator('.projects-load-error').waitFor({ state: 'detached' });
    await page.locator('.projects-library[aria-busy=false]').waitFor({ state: 'visible' });
    slowProjects = false;

    slowAssets = true; failAssets = true;
    await page.locator('#mainNav [data-view=assets]').click();
    await page.locator('#alLibrary[aria-busy=true]').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#alLibrary .app-loading, .app-network-loading').count(), 0);
    await page.locator('#alLibrary .al-error').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#alLibrary .app-loading').count(), 0, 'failed assets stop loading');
    failAssets = false;
    await page.locator('[data-al-action=retry]').click();
    await page.locator('#alLibrary[aria-busy=true]').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#alLibrary .app-loading, .app-network-loading').count(), 0);
    await page.locator('#alLibrary[aria-busy=false]').waitFor({ state: 'visible' });
    assert.equal(await page.locator('.al-history-card').count(), 2);
    slowAssets = false;
    slowModel = true;
    await page.locator('#mainNav [data-view=models]').click();
    await page.locator('#modelManagerContent .app-loading img').waitFor({ state: 'visible' });
    await page.locator('#modelManagerContent .app-loading').waitFor({ state: 'detached' });
    slowModel = false;
    await page.locator('.app-network-loading').waitFor({ state: 'detached' });

    await page.route(origin + '/api/projects/' + project.id, async route => { const response = await route.fetch(); await delay(700); await route.fulfill({ response }); });
    await page.evaluate(id => { window.navigation = loadingTest.selectProject(id); }, project.id);
    await page.locator('.app-loading-overlay').waitFor({ state: 'visible' });
    assert.equal(await page.locator('main').getAttribute('aria-busy'), 'true');
    await page.evaluate(() => navigation);
    await page.locator('.app-loading-overlay').waitFor({ state: 'detached' });
    assert.equal(await page.locator('main').getAttribute('aria-busy'), null);
    assert.deepEqual(await page.evaluate(() => ({ x: loadingTest.state.panX, y: loadingTest.state.panY, zoom: loadingTest.state.zoom })), { x: 10, y: 20, zoom: .8 });

    await page.evaluate(async () => {
      const { beginRequest } = await import('/loading-state.js?v=1');
      const finish = beginRequest('/api/projects'); finish();
    });
    await page.waitForTimeout(280);
    assert.equal(await page.locator('.app-network-loading').count(), 0, 'fast requests do not flash');
    await page.evaluate(async () => {
      const { beginRequest } = await import('/loading-state.js?v=1');
      window.finishPolling = beginRequest('/api/images/generations/poll-id');
    });
    await page.waitForTimeout(280);
    assert.equal(await page.locator('.app-network-loading').count(), 0, 'polls do not show page loaders');
    await page.evaluate(() => finishPolling());
    await page.evaluate(async () => {
      const { beginRequest } = await import('/loading-state.js?v=1');
      window.finishA = beginRequest('/api/assets'); window.finishB = beginRequest('/api/projects');
    });
    await page.locator('.app-network-loading').waitFor({ state: 'visible' });
    await page.evaluate(() => finishA());
    assert.equal(await page.locator('.app-network-loading').count(), 1, 'one completed request does not hide another');
    await page.evaluate(() => finishB());
    await page.locator('.app-network-loading').waitFor({ state: 'detached' });
    await page.evaluate(async () => {
      const { withLoading } = await import('/loading-state.js?v=1');
      window.failedNavigation = withLoading(() => new Promise((_, reject) => setTimeout(() => reject(new Error('模拟断网')), 450))).catch(() => {});
    });
    await page.locator('.app-loading-overlay').waitFor({ state: 'visible' });
    await page.screenshot({ path: path.join(root, 'outputs/loading-states-preview.png') });
    await page.evaluate(() => failedNavigation);
    await page.locator('.app-loading-overlay').waitFor({ state: 'detached' });
    slowModel = true;
    await page.evaluate(() => { window.openImage = loadingTest.imageGenerator.open(); });
    await page.locator('.ig-preview .app-loading img').waitFor({ state: 'visible' });
    await page.evaluate(() => openImage);
    await page.locator('.ig-preview .app-loading').waitFor({ state: 'detached' });
    await page.evaluate(() => loadingTest.imageGenerator.close());
    await page.evaluate(() => { window.openAudio = loadingTest.audioGenerator.open(); });
    await page.locator('#agForm .app-loading img').waitFor({ state: 'visible' });
    await page.evaluate(() => openAudio);
    await page.locator('#agForm .app-loading').waitFor({ state: 'detached' });
    await page.evaluate(() => loadingTest.audioGenerator.close());
    const login = await browser.newPage();
    await login.route(origin + '/api/auth/login', async route => { await delay(600); await route.fulfill({ status: 401, json: { detail: '账号或密码不正确' } }); });
    await login.goto(origin + '/login');
    await login.locator('#username').fill('test-user'); await login.locator('#password').fill('test-password');
    await login.locator('#loginButton').click();
    await login.locator('#loginButton .spinner svg').waitFor({ state: 'visible' });
    assert.equal(await login.locator('#loginButton .spinner .triangle-flow-line').count(), 3);
    await login.locator('#loginError').waitFor({ state: 'visible' });
    await login.locator('#loginButton .spinner').waitFor({ state: 'hidden' });
    assert.equal(await login.locator('#loginButton').isEnabled(), true);
    await login.close();
    assert.deepEqual(errors, []);
    console.log('PASS: slow login/bootstrap/projects/assets/models/canvas/image/audio, failure and retry, no false empty state or endless retry, fast-request suppression, polling exclusion, concurrent requests, failed navigation cleanup, viewport unchanged; no browser errors.');
  } finally { await browser?.close(); child.stdin.end('stop\n'); await exited; }
})().catch(error => { console.error(error); process.exitCode = 1; });
