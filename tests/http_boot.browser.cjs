// Run with Node >=20 and JHT_PLAYWRIGHT pointing to an installed Playwright package.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.JHT_PLAYWRIGHT || 'playwright');

const fixture = String.raw`
import os, sys, tempfile, socket, threading, json
from pathlib import Path
import uvicorn
with tempfile.TemporaryDirectory(prefix='jht-http-boot-') as directory:
    url = 'sqlite:///' + (Path(directory) / 'fixture.db').as_posix()
    os.environ.update(JIANHUITONG_DATA_DIR=directory, JIANHUITONG_DATABASE_URL=url,
                      DATABASE_URL=url, JIANHUITONG_MARKET_ORIGIN='', JIANHUITONG_COOKIE_SAMESITE='lax')
    import app
    from auth_service import hash_password
    from fastapi.responses import HTMLResponse
    @app.app.middleware('http')
    async def original_page(request, call_next):
        if request.url.path == '/' and request.query_params.get('fixture_original') == '1':
            html = (Path.cwd() / 'public/index.html').read_text(encoding='utf-8')
            html = html.replace('  <script src="browser-runtime.js?v=http-uuid-1"></script>\n', '')
            assert 'browser-runtime.js' not in html
            return HTMLResponse(html)
        return await call_next(request)
    with app.connect() as db:
        db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password('http-fixture-only'), 'admin'))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    server = uvicorn.Server(uvicorn.Config(app.app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    print('FIXTURE ' + json.dumps({'port': listener.getsockname()[1]}), flush=True)
    try:
        sys.stdin.readline()
    finally:
        server.should_exit = True
        thread.join(10)
        app.database.engine.dispose()
`;

(async () => {
  const server = spawn(process.env.JHT_TEST_PYTHON || '.venv/Scripts/python.exe', ['-u', '-c', fixture],
    { cwd: process.cwd(), stdio: ['pipe', 'pipe', 'pipe'] });
  let browser;
  try {
    const info = await new Promise((resolve, reject) => {
      let buffer = '';
      const timer = setTimeout(() => reject(new Error('Fixture startup timeout')), 30000);
      server.stdout.on('data', bytes => {
        buffer += bytes;
        const match = buffer.match(/FIXTURE (.+)\r?\n/);
        if (match) { clearTimeout(timer); resolve(JSON.parse(match[1])); }
      });
      server.stderr.on('data', bytes => process.stderr.write(bytes));
      server.on('exit', code => { clearTimeout(timer); reject(new Error(`Fixture exited: ${code}`)); });
    });
    // A non-localhost HTTP origin exercises the real secure-context restriction.
    const origin = `http://jht-http-test.test:${info.port}`;
    browser = await chromium.launch({ channel: 'chrome', headless: true,
      args: ['--host-resolver-rules=MAP jht-http-test.test 127.0.0.1', '--no-proxy-server'] });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') console.log('BROWSER:', message.text()); });
    page.on('requestfailed', request => console.log('REQUEST FAILED:', request.url(), request.failure()?.errorText));
    await page.goto(origin + '/login');
    assert.deepEqual(await page.evaluate(() => [isSecureContext, typeof crypto.randomUUID]), [false, 'undefined']);
    const login = await page.evaluate(async () => (await fetch('/api/auth/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'admin', password: 'http-fixture-only' }),
    })).status);
    assert.equal(login, 200);

    // Reproduce the deployed page before the compatibility script was included.
    const crash = page.waitForEvent('pageerror', { predicate: error => error.message.includes('crypto.randomUUID'), timeout: 15000 });
    await page.goto(origin + '/?fixture_original=1');
    await page.waitForFunction(() => document.querySelector('#agentView'));
    await crash;
    assert.ok(errors.some(error => error.includes('crypto.randomUUID is not a function')), errors.join('\n'));
    assert.equal(await page.locator('html').getAttribute('data-app-boot'), 'loading');
    console.log('PASS: original page reproduces randomUUID crash on ordinary HTTP');

    errors.length = 0;
    await page.goto(origin + '/');
    await page.waitForFunction(() => !document.documentElement.hasAttribute('data-app-boot'), null, { timeout: 15000 });
    assert.equal(await page.locator('#appBootScreen').count(), 0);
    assert.equal(await page.locator('#sessionName').textContent(), '管理员');
    assert.match(await page.evaluate(() => crypto.randomUUID()), /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    assert.deepEqual(errors, []);
    console.log('PASS: patched HTTP page boots, displays admin identity, generates UUIDs, no JS errors');
    await page.reload();
    await page.waitForFunction(() => !document.documentElement.hasAttribute('data-app-boot'));
    assert.deepEqual(errors, []);
    console.log('PASS: authenticated HTTP refresh also boots');
  } finally {
    if (browser) await browser.close();
    server.stdin.end('\n');
    await new Promise(resolve => {
      if (server.exitCode !== null) return resolve();
      const timer = setTimeout(() => { server.kill(); resolve(); }, 15000);
      server.once('exit', () => { clearTimeout(timer); resolve(); });
    });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
