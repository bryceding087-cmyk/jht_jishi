// Slow/failed media in a real browser. Only local static files and synthetic
// MP4s are used; no application server, user database or model API is needed.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { chromium } = require(process.env.JHT_PLAYWRIGHT || 'playwright');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'work/video-preview-verification');
fs.mkdirSync(output, { recursive: true });
const python = process.env.JHT_TEST_PYTHON || path.join(root, '.venv/Scripts/python.exe');
const generated = spawnSync(python, ['-c', `
import pathlib, subprocess, sys, imageio_ffmpeg
for name, size in [('portrait', '180x320'), ('landscape', '320x180'), ('square', '240x240')]:
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'testsrc2=s='+size+':r=24:d=2', '-an', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(pathlib.Path(sys.argv[1])/(name+'.mp4'))], check=True)
`, output], { encoding: 'utf8' });
assert.equal(generated.status, 0, generated.stderr);
const styles = fs.readFileSync(path.join(root, 'public/index.html'), 'utf8').match(/<link rel="stylesheet"[^>]+>/g).join('\n');
const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">${styles}
<style>body{display:block;overflow:auto}.creation-home{display:flex!important}.home-results{display:flex!important;flex-direction:column}</style></head>
<body><section class="creation-home has-media-conversation"><div class="home-results" id="results"></div></section>
<script type="module">
import { installNodePlayers } from '/node-player.js';
window.disposePlayers = installNodePlayers();
window.mount = (name, ratio, bind = true) => {
  const card = document.createElement('article'); card.dataset.homeJob = name;
  if(ratio)card.style.setProperty('--result-ratio', ratio);
  card.innerHTML = '<header class="home-result-summary"><p class="home-user-prompt">视频预览</p><div class="home-result-meta">'+(ratio || '默认比例')+'</div></header><video '+(bind?'data-conversation-video':'')+' controls playsinline preload="auto" src="/clips/'+name+'.mp4"></video><footer><button data-player-library data-library-saved="false">保存至资产库</button></footer>';
  card.querySelector('button').onclick = event => { event.target.dataset.librarySaved = 'true'; event.target.disabled = true; };
  document.getElementById('results').append(card);
};
</script></body></html>`;

(async () => {
  let browser;
  const gates = new Map(), failures = new Set(), releases = [];
  const hold = name => { let release; gates.set(name, new Promise(resolve => {release=resolve;})); releases.push(release); return release; };
  const errors = [];
  try {
    browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true });
    const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'preview.test') return route.abort();
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html });
      if (url.pathname.startsWith('/clips/')) {
        const name = path.basename(url.pathname, '.mp4');
        await gates.get(name);
        if (failures.has(name)) return route.fulfill({ status: 503, body: 'Temporarily unavailable' });
        const fixture = name.startsWith('portrait') ? 'portrait' : name.startsWith('square') ? 'square' : 'landscape';
        return route.fulfill({ contentType: 'video/mp4', body: fs.readFileSync(path.join(output, fixture+'.mp4')) });
      }
      const file = path.resolve(root, 'public', '.'+decodeURIComponent(url.pathname));
      if (!file.startsWith(path.join(root, 'public')+path.sep) || !fs.existsSync(file)) return route.fulfill({ status: 404, body: '' });
      const contentType = ({'.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)] || 'application/octet-stream';
      return route.fulfill({ contentType, body: fs.readFileSync(file) });
    });
    await page.goto('http://preview.test/');
    await page.waitForFunction(() => typeof mount === 'function');
    const player = name => page.locator(`[data-home-job="${name}"] .node-player`);
    const ready = name => page.waitForFunction(name => {
      const p = document.querySelector('[data-home-job="'+name+'"] .node-player');
      return p && p.querySelector('video').readyState >= 2 && p.getAttribute('aria-busy') === 'false' && p.querySelector('.np-preview').hidden;
    }, name);
    for (const [name, ratio, expected] of [['portrait','9 / 16',9/16],['landscape','16 / 9',16/9],['square','1',1],['unknown',null,16/9]]) {
      const release = hold(name);
      await page.evaluate(({name,ratio}) => mount(name,ratio), {name,ratio});
      const p = player(name);
      await p.locator('.np-preview-spinner').waitFor({ state: 'visible' });
      assert.equal(await p.getAttribute('aria-busy'), 'true');
      assert.equal(await p.locator('video').evaluate(v => v.readyState), 0, 'media response is still withheld');
      const before = await p.boundingBox();
      assert.ok(Math.abs(before.width/before.height-expected)<.005, name+' reserves its ratio before metadata');
      assert.ok(before.height<=420.5 && before.width<=640.5);
      const overlay = await p.locator('.np-preview').boundingBox();
      assert.ok(Math.abs(overlay.width-before.width)<1 && Math.abs(overlay.height-before.height)<1);
      const spinner = await p.locator('.np-preview-spinner').boundingBox();
      assert.ok(Math.abs(spinner.x+spinner.width/2-before.x-before.width/2)<1, 'spinner is horizontally centered');
      assert.equal(await p.locator('.np-controls').isVisible(), false);
      // A metadata event without a decoded frame must keep the placeholder.
      await p.locator('video').evaluate(v => v.dispatchEvent(new Event('loadedmetadata')));
      assert.equal(await p.locator('.np-preview').isVisible(), true);
      if(name==='portrait')await p.screenshot({path:path.join(output,'portrait-loading.png')});
      release();await ready(name);
      const after = await p.boundingBox();
      assert.ok(Math.abs(after.width-before.width)<1 && Math.abs(after.height-before.height)<1, 'loading should not change the reserved size');
      assert.equal(await p.locator('.np-controls').isVisible(), true);
    }
    console.log('PASS: delayed portrait/landscape/square/unknown previews reserve the correct size; first-frame readiness removes the overlay without a layout jump');

    const p = player('portrait');
    await p.locator('.np-toggle').click();
    await page.waitForFunction(() => document.querySelector('[data-home-job=portrait] video').currentTime>.1);
    await p.locator('.np-toggle').click();
    const previous = await p.locator('video').evaluate(v => {window.savedVideo=v;return v.currentTime;});
    await p.hover();await p.locator('.np-library').click();
    assert.equal(await p.locator('video').evaluate(v => v===window.savedVideo && v.currentTime===savedVideo.currentTime), true);
    assert.ok(await p.locator('video').evaluate((v,t) => Math.abs(v.currentTime-t)<.05, previous));
    assert.equal(await p.locator('.np-preview').isVisible(), false);
    await p.locator('.np-fullscreen').click();
    await page.waitForFunction(() => !!document.fullscreenElement);
    assert.equal(await p.locator('video').evaluate(v => getComputedStyle(v).objectFit), 'contain');
    await page.evaluate(() => document.exitFullscreen());
    console.log('PASS: playback, save state and fullscreen still work');

    failures.add('portrait-retry');await page.evaluate(() => mount('portrait-retry','9 / 16'));
    const retryPlayer = player('portrait-retry');
    await retryPlayer.locator('.np-preview-retry').waitFor({state:'visible'});
    assert.equal(await retryPlayer.getAttribute('aria-busy'), 'false');
    assert.equal(await retryPlayer.locator('.np-preview-spinner').isVisible(), false);
    await retryPlayer.screenshot({path:path.join(output,'portrait-error.png')});
    failures.delete('portrait-retry');const releaseRetry=hold('portrait-retry');
    await retryPlayer.locator('.np-preview-retry').click();
    await retryPlayer.locator('.np-preview-spinner').waitFor({state:'visible'});
    releaseRetry();await ready('portrait-retry');
    assert.equal(await retryPlayer.locator('.np-toggle').isEnabled(), true);
    console.log('PASS: failed requests stop the spinner and can reload successfully');

    await page.evaluate(() => mount('portrait-cached','9 / 16',false));
    await page.waitForFunction(() => document.querySelector('[data-home-job=portrait-cached] video').readyState>=2);
    await page.evaluate(() => {
      const card=document.querySelector('[data-home-job=portrait-cached]');
      card.querySelector('video').setAttribute('data-conversation-video','');
      card.append(document.createElement('span'));
    });
    await ready('portrait-cached');
    console.log('PASS: already decoded media binds without a stuck loading state');

    await page.setViewportSize({width:360,height:800});
    const releaseSlow=hold('portrait-slow');await page.evaluate(() => mount('portrait-slow','9 / 16'));
    const slow = player('portrait-slow');
    await slow.scrollIntoViewIfNeeded();
    const mobile=await slow.boundingBox();
    assert.ok(mobile.x>=0 && mobile.x+mobile.width<=360.5 && Math.abs(mobile.width/mobile.height-9/16)<.005);
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await slow.locator('.np-preview-spinner').evaluate(el => getComputedStyle(el).animationName), 'none');
    await slow.locator('.np-preview-retry').waitFor({state:'visible',timeout:18000});
    assert.match(await slow.locator('.np-preview-label').textContent(), /加载较慢/);
    await slow.screenshot({path:path.join(output,'portrait-slow-mobile.png')});
    releaseSlow();await ready('portrait-slow');
    await page.evaluate(() => {document.getElementById('results').replaceChildren();disposePlayers();});
    assert.deepEqual(errors,[]);
    console.log('PASS: narrow screens, reduced motion, slow-load retry and player disposal');
  } finally {
    releases.forEach(release => release());
    await browser?.close();
  }
})().catch(error => {console.error(error);process.exitCode=1;});
