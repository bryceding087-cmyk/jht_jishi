// Disposable storage and real Chrome; no model generation requests.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.JHT_PLAYWRIGHT || 'playwright');
const { fixture } = require('./browser_fixture.cjs');

(async () => {
  const child = spawn('.venv/Scripts/python.exe', ['-u', '-c', fixture], { stdio: ['pipe', 'pipe', 'inherit'] });
  let browser;
  try {
    const { origin } = await new Promise((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(Error('Fixture timeout')), 30000);
      child.stdout.on('data', chunk => {
        output += chunk;
        const match = output.match(/FIXTURE (.+)\r?\n/);
        if (match) { clearTimeout(timer); resolve(JSON.parse(match[1])); }
      });
      child.once('exit', code => { clearTimeout(timer); reject(Error('Fixture exit ' + code)); });
    });
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const headers = { Origin: origin };
    assert.ok((await context.request.post(origin + '/api/auth/login', {
      headers, data: { username: 'admin', password: 'viewport-test' }
    })).ok());
    const response = await context.request.post(origin + '/api/projects', {
      headers, data: {
        name: '概览回归验证',
        nodes: [
          { id: 'left', type: 'prompt', title: '左侧节点', x: -400, y: -200 },
          { id: 'right', type: 'prompt', title: '右侧节点', x: 1200, y: 500 }
        ], edges: [], viewport: { x: 50, y: 60, zoom: .5 }
      }
    });
    assert.ok(response.ok());
    const project = await response.json();
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin + '/?project=' + project.id);
    await page.locator('#canvas [data-node="right"]').waitFor();
    await page.locator('#toggleMinimap').click();
    const host = page.locator('#workspaceMinimap'), map = host.locator(':scope > svg');
    await host.waitFor({ state: 'visible' });
    assert.equal(await map.locator('.map-node').count(), 2);
    assert.equal(await map.locator('.map-viewport').count(), 1);
    assert.equal(await page.locator('#closeMinimap .map-node').count(), 0);
    assert.ok(await page.locator('#closeMinimap svg path').count());
    for (const rect of await map.locator('.map-node').evaluateAll(els => els.map(el => ({
      x: +el.getAttribute('x'), y: +el.getAttribute('y'),
      width: +el.getAttribute('width'), height: +el.getAttribute('height')
    })))) {
      assert.ok(rect.x >= 0 && rect.y >= 0 && rect.width > 0 && rect.height > 0);
      assert.ok(rect.x + rect.width <= 200 && rect.y + rect.height <= 115);
    }
    const viewport = map.locator('.map-viewport');
    const before = await viewport.getAttribute('x');
    await map.click({ position: { x: 155, y: 80 } });
    await page.waitForFunction(old => document.querySelector('#workspaceMinimap > svg .map-viewport').getAttribute('x') !== old, before);
    await page.locator('#closeMinimap').click();
    await host.waitFor({ state: 'hidden' });
    await page.locator('#toggleMinimap').click();
    await host.waitFor({ state: 'visible' });
    assert.equal(await map.locator('.map-node').count(), 2);
    await host.screenshot({ path: 'work/minimap-fixed.png' });
    assert.deepEqual(errors, []);
    console.log('PASS minimap nodes, negative coordinates, viewport navigation, close icon, reopen, no JavaScript errors');
  } finally {
    await browser?.close();
    child.stdin.end('\n');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
