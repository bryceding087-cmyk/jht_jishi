// Destroy/recreate the marketplace iframe against a real isolated app and fake provider.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.JHT_PLAYWRIGHT || 'playwright');
const { fixture, api } = require('./market_login.browser.cjs');

const setup = String.raw`
    submitted, queried, finished = [], [], set()
    def provider(method, url, key, *args):
        from market_service import model_api_key
        model_api_key(url, key)
        if method == 'POST':
            tid = 'provider-' + str(len(submitted) + 1)
            submitted.append(tid)
            return {'task_id': tid}
        tid = url.rsplit('/', 1)[-1]
        queried.append(tid)
        return {'status': 'Success', 'video_url': 'https://cdn.test/video.mp4'} if tid in finished else {'status': 'Processing'}
    def cache_video(url):
        (app.UPLOADS / 'fixture.mp4').write_bytes(b'fixture video')
        return '/uploads/fixture.mp4'
    app.provider_json = provider
    app.cache_generated_video = cache_video
    app.video_poll_delay = lambda *args: .05
    @app.app.post('/api/test/generation')
    def control(body: dict):
        if body.get('finish'): finished.update(submitted)
        return {'submitted': submitted, 'queried': queried, 'tasks': app.listing('task')}
    app.app.router.routes.insert(0, app.app.router.routes.pop())
`;

(async () => {
  const server = spawn(process.env.JHT_TEST_PYTHON || '.venv/Scripts/python.exe',
    ['-u', '-c', fixture.replace('    import app\n', '    import app\n' + setup)], {stdio:['pipe','pipe','pipe']});
  let browser;
  try {
    const info = await new Promise((resolve, reject) => {
      let buffer = '';
      const timer = setTimeout(() => reject(new Error('Fixture startup timeout')), 30000);
      server.stdout.on('data', bytes => { buffer += bytes; const m = buffer.match(/FIXTURE (.+)\r?\n/); if (m) { clearTimeout(timer); resolve(JSON.parse(m[1])); } });
      server.stderr.on('data', bytes => process.stderr.write(bytes));
      server.on('exit', code => { clearTimeout(timer); reject(new Error('Fixture exited ' + code)); });
    });
    browser = await chromium.launch({channel:'chrome', headless:true, args:[
      '--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1', '--no-proxy-server',
      ...(process.env.JHT_TEST_PROXY==='1'?['--ignore-certificate-errors']:[])]});
    const context = await browser.newContext({ignoreHTTPSErrors:true});
    const errors = [];
    context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
    const page = await context.newPage();
    await page.goto(info.parent + '/?user=alpha');
    async function ready() {
      const frame = await (await page.waitForSelector('#studio')).contentFrame();
      await frame.waitForFunction(() => !document.documentElement.hasAttribute('data-app-boot'), null, {timeout:20000});
      return frame;
    }
    async function returnToMarket(search = '') {
      await page.evaluate(url => {
        const frame = document.createElement('iframe'); frame.id = 'studio'; frame.src = url;
        frame.style = 'width:1200px;height:900px'; document.body.append(frame);
      }, info.app + '/market' + search);
      return ready();
    }
    async function leave() { await page.locator('#studio').evaluate(element => element.remove()); }
    let frame = await ready();
    await frame.locator('#homeTab-video').click();
    await frame.locator('#homePrompt').fill('生成小熊猫跳舞');
    await frame.locator('.home-send').click();
    await frame.waitForSelector('[data-home-job]');
    const cid = new URL(frame.url()).searchParams.get('conversation');
    assert.ok(cid);
    const before = await api(frame, '/api/test/generation', {});
    assert.equal(before.status, 200, JSON.stringify(before.data));
    assert.equal(before.data.submitted.length, 1);
    const tid = before.data.tasks[0].id;
    await leave();
    frame = await returnToMarket();
    assert.equal(new URL(frame.url()).searchParams.get('conversation'), cid, 'fresh market entry must restore the running conversation');
    await frame.waitForSelector(`[data-home-job="${tid}"]`);
    assert.equal((await api(frame, '/api/test/generation', {})).data.submitted.length, 1, 'returning must never submit another paid task');
    assert.equal(await frame.locator('[data-home-job]').count(), 1);
    await frame.locator('#homePrompt').fill('等待原任务完成后再生成');
    assert.ok(await frame.locator('.home-send').isDisabled());
    assert.ok(await frame.locator('.home-generation-status').isVisible());
    await frame.locator('#agentView').screenshot({path:'work/market-task-return-running.png'});
    console.log('PASS: iframe recreation restores the running task without a second provider POST');

    // No browser storage: fall back to the authenticated server task list.
    await frame.evaluate(() => sessionStorage.clear());
    await leave();
    frame = await returnToMarket();
    assert.equal(new URL(frame.url()).searchParams.get('conversation'), cid);
    console.log('PASS: active task recovery works without browser navigation storage');

    // Complete while the iframe is absent, then show the original result on return.
    const observer = await context.newPage();
    await observer.goto(info.parent + '/?user=alpha');
    const other = await (await observer.waitForSelector('#studio')).contentFrame();
    await other.waitForFunction(() => !document.documentElement.hasAttribute('data-app-boot'));
    await leave();
    await api(other, '/api/test/generation', {finish:true});
    await other.waitForFunction(async () => {
      const m = await import('/market-session.js?v=market-sdk-3');
      const r = await fetch('/api/test/generation', m.withMarketSession({method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}, '/api/test/generation'));
      const data = await r.json(); return data.tasks[0]?.video_cache_status === 'cached';
    });
    frame = await returnToMarket();
    assert.equal(new URL(frame.url()).searchParams.get('conversation'), cid);
    await frame.waitForSelector('.home-results video');
    assert.equal((await api(frame, '/api/test/generation', {})).data.submitted.length, 1);
    console.log('PASS: completion while away returns to the same conversation and saved result');

    await leave();
    frame = await returnToMarket('?view=home');
    assert.equal(new URL(frame.url()).searchParams.get('conversation'), null, 'explicit home navigation wins');
    await page.goto(info.parent + '/?user=beta');
    frame = await ready();
    assert.deepEqual((await api(frame, '/api/conversations')).data, []);
    assert.equal(await frame.locator('[data-home-job]').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS: explicit navigation and account isolation are preserved');
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
