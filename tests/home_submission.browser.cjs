// Real home/conversation layouts with delayed requests and disposable storage.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.JHT_PLAYWRIGHT || 'playwright');
const { fixture } = require('./market_login.browser.cjs');

const setup = String.raw`
    # Persist real queued tasks, but never launch a provider/model worker.
    app.start_generation_worker = lambda *args: None
    app.save('audio_model_config', {**app.DEFAULT_AUDIO_MODEL, 'api_key': 'fixture-audio-key'})
`;

(async () => {
  const server = spawn(process.env.JHT_TEST_PYTHON || '.venv/Scripts/python.exe',
    ['-u', '-c', fixture.replace('    import app\n', '    import app\n' + setup)], { stdio: ['pipe', 'pipe', 'pipe'] });
  let browser;
  const releases = [];
  try {
    const info = await new Promise((resolve, reject) => {
      let output = '';
      const timer = setTimeout(() => reject(new Error('Fixture startup timeout')), 30000);
      server.stdout.on('data', chunk => {
        output += chunk;
        const match = output.match(/FIXTURE (.+)\r?\n/);
        if (match) { clearTimeout(timer); resolve(JSON.parse(match[1])); }
      });
      server.stderr.on('data', chunk => process.stderr.write(chunk));
      server.once('exit', code => { clearTimeout(timer); reject(new Error('Fixture exit ' + code)); });
    });
    browser = await chromium.launch({ channel: 'chrome', headless: true, args: [
      '--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1', '--no-proxy-server',
    ] });
    const context = await browser.newContext({ viewport: { width: 1500, height: 1050 } });
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(info.parent + '/?user=home-submission');
    const frame = await (await page.waitForSelector('#studio')).contentFrame();
    await frame.waitForFunction(() => !document.documentElement.hasAttribute('data-app-boot'));
    const send = frame.locator('.home-send');
    const editor = frame.locator('[data-mention-editor="homePrompt"]');
    const pendingCard = '#agentView.has-media-conversation:not(.home-landing) [data-home-job]:not([hidden]) .home-generation-placeholder';
    const endpoints = { image: '/api/images/generations', audio: '/api/audio/generations', video: '/api/tasks' };
    async function home(kind, prompt) {
      await frame.locator('#mainNav [data-view="home"]').click();
      await frame.waitForSelector('#agentView.home-landing:not(.has-media-conversation)');
      await frame.locator('#homeTab-' + kind).click();
      await editor.fill(prompt);
    }
    async function holdPost(path, reply) {
      let release, enter;
      const gate = new Promise(resolve => { release = resolve; });
      const entered = new Promise(resolve => { enter = resolve; });
      const handler = async route => {
        if (route.request().method() === 'POST') {
          enter(route.request()); await gate;
          if (reply) return route.fulfill(reply);
        }
        await route.continue();
      };
      releases.push(release);
      await context.route('**' + path, handler);
      return { entered, release, clear: () => context.unroute('**' + path, handler) };
    }
    async function trackFrames() {
      await frame.evaluate(() => {
        const root = document.querySelector('#agentView');
        const trace = window.homeSubmissionTrace = [];
        function sample() {
          if (window.homeSubmissionTrace !== trace) return;
          if (root.classList.contains('active-view')) {
            const landing = root.classList.contains('home-landing');
            const hasContent = root.classList.contains('has-media-conversation') || root.classList.contains('has-agent-conversation');
            const state = landing ? 'home' : hasContent ? 'conversation' : 'intermediate';
            if (trace.at(-1) !== state) trace.push(state);
          }
          requestAnimationFrame(sample);
        }
        sample();
      });
    }
    const paint = () => frame.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    async function assertHome(prompt) {
      await paint();
      assert.ok(await frame.locator('#agentView').evaluate(el => el.classList.contains('home-landing')));
      assert.ok(await frame.locator('.agent-welcome').isVisible());
      assert.equal(await frame.locator('main > header').isVisible(), false);
      assert.equal(await frame.locator('#homePrompt').inputValue(), prompt);
      assert.equal(await frame.locator('.home-results').isVisible(), false);
    }

    for (const kind of ['image', 'audio', 'video']) {
      const prompt = kind + ' 提交过渡测试';
      await home(kind, prompt);
      const conversation = await holdPost('/api/conversations');
      const task = await holdPost(endpoints[kind]);
      await trackFrames();
      await send.click();
      await conversation.entered;
      await assertHome(prompt);
      assert.equal(await send.getAttribute('data-request-state'), 'running');
      assert.equal(await send.isDisabled(), false);
      conversation.release();
      await task.entered;
      await assertHome(prompt);
      assert.equal(await send.getAttribute('data-request-state'), 'running');
      assert.equal(await send.isDisabled(), false);
      if (kind === 'audio') await frame.locator('#agentView').screenshot({ path: 'work/home-submission-pending.png' });
      task.release();
      await frame.waitForSelector(pendingCard);
      await paint();
      assert.deepEqual(await frame.evaluate(() => window.homeSubmissionTrace), ['home', 'conversation']);
      assert.equal(await frame.locator('.agent-welcome').isVisible(), false);
      assert.equal(await frame.locator('main > header').isVisible(), false);
      assert.equal(await frame.locator('.home-user-prompt:visible').textContent(), prompt);
      if (kind === 'audio') await frame.locator('#agentView').screenshot({ path: 'work/home-submission-conversation.png' });
      await conversation.clear();
      await task.clear();
      console.log('PASS: ' + kind + ' stays on home during both requests and paints only home then the queued conversation');
    }

    // Stop before the original POST reaches the server: request tombstones
    // reject its delayed creation/submission while the composer keeps the draft.
    for(const kind of ['image','audio','video']) {
      for(const phase of ['conversation','task']) {
        const prompt=kind+' '+phase+' 停止时保留草稿';
        await home(kind,prompt);
        const path=phase==='conversation'?'/api/conversations':endpoints[kind];
        const held=await holdPost(path);
        await send.click();
        const original=await held.entered;
        const originalBody=original.postDataJSON();
        assert.equal(await send.getAttribute('data-request-state'),'running');
        // Enter in the editor must neither submit nor activate the Stop button.
        const stopRequests=[];
        const trackStop=request=>{if(new URL(request.url()).pathname==='/api/generation/stop')stopRequests.push(request);};
        page.on('request',trackStop);
        await editor.press('Enter');await paint();
        const retainedDraft=await frame.locator('#homePrompt').inputValue();
        assert.equal(stopRequests.length,0);
        const stopped=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/generation/stop'&&response.request().method()==='POST');
        await send.click();
        const stopResponse=await stopped;
        assert.equal(stopResponse.status(),200);
        assert.equal(stopResponse.request().postDataJSON().request_id,originalBody.request_id);
        assert.equal(stopResponse.request().postDataJSON().kind,kind);
        await frame.waitForFunction(()=>document.querySelector('.home-send').dataset.requestState==='idle');
        await assertHome(retainedDraft);
        assert.equal(await send.isDisabled(),false);
        const late=page.waitForResponse(response=>response.request()===original);
        held.release();
        assert.equal((await late).status(),409,'stopped request cannot create a task after the stop acknowledgment');
        await assertHome(retainedDraft);
        assert.equal(await frame.locator('.home-generation-error').isVisible(),false);
        await held.clear();page.off('request',trackStop);
      }
      console.log('PASS: '+kind+' stops during creation/submission, preserves the draft and rejects late task creation');
    }

    await home('audio', '失败后保留输入');
    let failedRequest;
    const failure = route => {
      if (route.request().method() !== 'POST') return route.continue();
      failedRequest = route.request().postDataJSON();
      return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '模拟任务提交失败' }) });
    };
    await context.route('**' + endpoints.audio, failure);
    await trackFrames();
    await send.click();
    await frame.waitForSelector('.home-generation-error:not([hidden])');
    await assertHome('失败后保留输入');
    assert.deepEqual(await frame.evaluate(() => window.homeSubmissionTrace), ['home']);
    await context.unroute('**' + endpoints.audio, failure);
    await frame.waitForFunction(()=>document.querySelector('.home-send').dataset.requestState==='idle');
    const retry = page.waitForRequest(request => new URL(request.url()).pathname === endpoints.audio && request.method() === 'POST');
    await send.click();
    const retried = (await retry).postDataJSON();
    assert.equal(retried.request_id, failedRequest.request_id);
    assert.equal(retried.project_id, failedRequest.project_id);
    await frame.waitForSelector(pendingCard);
    console.log('PASS: failed submission keeps the home layout and input, and retry reuses the original request');

    await home('image', '提交后返回首页');
    const late = await holdPost(endpoints.image);
    await send.click();
    const pendingRequest = await late.entered;
    await home('image', '新的首页草稿');
    await trackFrames();
    const response = page.waitForResponse(response => response.request() === pendingRequest);
    late.release();
    await (await response).finished();
    await assertHome('新的首页草稿');
    assert.deepEqual(await frame.evaluate(() => window.homeSubmissionTrace), ['home']);
    await late.clear();
    console.log('PASS: a late task response does not switch a fresh home draft into the old conversation');

    await frame.locator('#mainNav [data-view="home"]').click();
    await frame.locator('#homeTab-agent').click();
    await frame.locator('[data-mention-editor="agentInput"]').fill('Agent 提交过渡测试');
    const snapshot = {
      run: { id: 'home-transition-agent', project_id: 'home-transition-chat', creation_type: 'conversation',
        status: 'running', title: 'Agent 提交过渡测试', phase: 'analysis', preferences: {}, updated_at: 1 },
      events: [{ seq: 1, kind: 'user', text: 'Agent 提交过渡测试', time: 1 }],
    };
    const agentReply = { status: 200, contentType: 'application/json', body: JSON.stringify(snapshot) };
    await context.route('**/api/agent/runs/home-transition-agent*', route => route.fulfill(agentReply));
    const agent = await holdPost('/api/agent/runs', agentReply);
    await trackFrames();
    await frame.locator('#agentComposer [type="submit"]').click();
    await agent.entered;
    await paint();
    assert.ok(await frame.locator('#agentView.home-landing').isVisible());
    assert.ok(await frame.locator('.agent-welcome').isVisible());
    agent.release();
    await frame.waitForSelector('#agentView.has-agent-conversation:not(.home-landing) .agent-user-bubble');
    await paint();
    assert.deepEqual(await frame.evaluate(() => window.homeSubmissionTrace), ['home', 'conversation']);
    await agent.clear();
    console.log('PASS: Agent submission also switches directly from home to the prepared conversation');
    assert.deepEqual(errors, []);
  } finally {
    releases.forEach(release => release());
    if (browser) await browser.close();
    server.stdin.end('\n');
    await new Promise(resolve => {
      if (server.exitCode !== null) return resolve();
      const timer = setTimeout(() => { server.kill(); resolve(); }, 15000);
      server.once('exit', () => { clearTimeout(timer); resolve(); });
    });
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
