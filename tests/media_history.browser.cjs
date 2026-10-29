// Real marketplace iframe, real persistence, fake providers and disposable data.
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const { chromium } = require(process.env.JHT_PLAYWRIGHT || 'playwright');
const { fixture, api } = require('./market_login.browser.cjs');

const setup = String.raw`
    import base64, io, wave, uuid
    from PIL import Image
    from agent_media import execute
    picture = io.BytesIO()
    Image.new('RGB', (320, 180), '#377d89').save(picture, 'PNG')
    sound = io.BytesIO()
    with wave.open(sound, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b'\x00\x00' * 8000)
    clip = app.DATA / 'fixture.mp4'
    edge_markers = ',drawbox=x=0:y=0:w=iw:h=ih/6:color=red:t=fill,drawbox=x=0:y=ih*5/6:w=iw:h=ih/6:color=yellow:t=fill'
    execute(['-f','lavfi','-i','color=c=0x377d89:s=320x180:r=24:d=1'+edge_markers,'-t','1','-c:v','libx264','-pix_fmt','yuv420p',str(clip)])
    portrait = app.DATA / 'portrait.mp4'
    execute(['-f','lavfi','-i','color=c=0x377d89:s=180x320:r=24:d=1'+edge_markers,'-t','1','-c:v','libx264','-pix_fmt','yuv420p',str(portrait)])
    calls = {'video': 0, 'image': 0, 'audio': 0, 'download': 0, 'fail_download': False, 'video_status': 'Success', 'portrait': False}
    def provider(method, url, key, *args, **kwargs):
        if '/images/' in url:
            calls['image'] += 1
            return {'data': [{'url': 'https://cdn.test/image.png'}]}
        if method == 'POST':
            calls['video'] += 1
            return {'task_id': 'fake-video-' + str(calls['video'])}
        return {'status':calls['video_status'], 'video_url':'https://cdn.test/video.mp4'}
    def cache_video(url):
        calls['download'] += 1
        if calls['fail_download']: raise OSError('fixture download failure')
        name = 'generated-' + uuid.uuid4().hex + '.mp4'
        (app.UPLOADS / name).write_bytes((portrait if calls['portrait'] else clip).read_bytes())
        return '/uploads/' + name
    def request_audio(*args):
        calls['audio'] += 1
        return {'code':0,'audio':base64.b64encode(sound.getvalue()).decode(),'duration':1}, 'fixture'
    app.provider_json = provider
    app.download_generated_image = lambda url: (picture.getvalue(), '.png')
    app.cache_generated_video = cache_video
    app.request_audio = request_audio
    app.save('audio_model_config', {**app.DEFAULT_AUDIO_MODEL, 'api_key': 'fixture-audio-key'})
    app.video_poll_delay = lambda *args: .05
    @app.app.post('/api/test/media-history')
    def control(body: dict):
        if 'fail_download' in body: calls['fail_download'] = body['fail_download']
        for key in ('video_status', 'portrait'):
            if key in body: calls[key] = body[key]
        return {'calls':calls, 'png':base64.b64encode(picture.getvalue()).decode()}
    app.app.router.routes.insert(0, app.app.router.routes.pop())
`;

(async()=>{
  const server=spawn(process.env.JHT_TEST_PYTHON||'.venv/Scripts/python.exe',['-u','-c',fixture.replace('<iframe id="studio"','<iframe id="studio" allowfullscreen').replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','pipe']});
  let browser, releaseSubmission;
  try{
    const info=await new Promise((resolve,reject)=>{
      let buffer='';const timer=setTimeout(()=>reject(new Error('Fixture startup timeout')),30000);
      server.stdout.on('data',bytes=>{buffer+=bytes;const match=buffer.match(/FIXTURE (.+)\r?\n/);if(match){clearTimeout(timer);resolve(JSON.parse(match[1]));}});
      server.stderr.on('data',bytes=>process.stderr.write(bytes));
      server.on('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exit '+code));});
    });
    browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
    const context=await browser.newContext({viewport:{width:1500,height:1050}}),errors=[];
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    await page.goto(info.parent+'/?user=alpha');
    const frame=await (await page.waitForSelector('#studio')).contentFrame();
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    const png=(await api(frame,'/api/test/media-history',{})).data.png;
    const history=async()=>(await api(frame,'/api/assets')).data;
    const library=async()=>(await api(frame,'/api/assets?collection=library')).data;
    const nav=async view=>frame.locator(`#mainNav [data-view="${view}"]`).click();
    const waitSaved=async selector=>frame.waitForFunction(selector=>document.querySelector(selector)?.textContent==='移出资产库',selector);

    async function verifyFullscreen(node,prefix='video'){
      const controls=node.locator('.node-player-video');
      const fits=await controls.evaluate(player=>{
        const bounds=player.getBoundingClientRect();
        return [...player.querySelectorAll('.np-controls button,.np-seek')].every(control=>{
          const box=control.getBoundingClientRect();
          return box.width>0&&box.left>=bounds.left&&box.right<=bounds.right&&box.bottom<=bounds.bottom;
        });
      });
      assert.ok(fits,'all playback controls must fit in landscape and portrait nodes');
      await controls.locator('.np-fullscreen').click();
      await frame.waitForFunction(()=>!!document.fullscreenElement);
      assert.ok(await controls.evaluate(player=>document.fullscreenElement===player));
      await frame.waitForFunction(()=>document.fullscreenElement.querySelector('video').readyState>=2);
      const full=await controls.evaluate(player=>{
        const video=player.querySelector('video'),box=player.getBoundingClientRect(),style=getComputedStyle(video);
        return {width:video.videoWidth,height:video.videoHeight,fit:style.objectFit,
          fillsViewport:Math.abs(box.width-innerWidth)<2&&Math.abs(box.height-innerHeight)<2};
      });
      assert.equal(full.fit,'contain','fullscreen must display the whole frame without cropping');
      assert.ok(full.fillsViewport,'fullscreen player must fill the available viewport');
      const orientation=full.height>full.width?'portrait':'landscape';
      const capture=await controls.screenshot({path:`work/${prefix}-fullscreen-${orientation}.png`});
      const pixels=spawnSync(process.env.JHT_TEST_PYTHON||'.venv/Scripts/python.exe',['-c',String.raw`
import io, sys
from PIL import Image
image = Image.open(io.BytesIO(sys.stdin.buffer.read())).convert('RGB')
width, height = image.size
native_w, native_h = map(int, sys.argv[1:])
scale = min(width / native_w, height / native_h)
content_w, content_h = native_w * scale, native_h * scale
left, top = (width - content_w) / 2, (height - content_h) / 2
red = image.getpixel((width // 2, round(top + content_h * .08)))
yellow = image.getpixel((width // 2, round(top + content_h * .92)))
assert red[0] > 180 and red[1] < 70 and red[2] < 70, ('top edge cropped', red)
assert yellow[0] > 160 and yellow[1] > 160 and yellow[2] < 70, ('bottom edge cropped', yellow)
if left > 4:
    assert max(image.getpixel((round(left / 2), height // 2))) < 10, 'side bars must be black'
if top > 4:
    assert max(image.getpixel((width // 2, round(top / 2)))) < 10, 'top/bottom bars must be black'
`,String(full.width),String(full.height)],{input:capture});
      assert.equal(pixels.status,0,pixels.stderr.toString());
      const media=controls.locator('video');
      const playback=await media.evaluate(video=>{
        const saved={time:video.currentTime,muted:video.muted,loop:video.loop};
        video.pause();video.currentTime=0;video.muted=true;video.loop=true;return saved;
      });
      await media.click();
      assert.equal(await media.evaluate(video=>video.paused),false,'fullscreen picture click starts playback');
      await frame.waitForFunction(()=>document.fullscreenElement.querySelector('video').currentTime>.05);
      await media.click();
      assert.equal(await media.evaluate(video=>video.paused),true,'fullscreen picture click pauses playback');
      assert.equal(await controls.locator('.np-toggle').getAttribute('aria-label'),'播放');
      const bounds=await controls.boundingBox();
      await media.click({position:orientation==='portrait'?{x:8,y:bounds.height/2}:{x:bounds.width/2,y:8}});
      assert.equal(await media.evaluate(video=>video.paused),false,'fullscreen black bars also toggle playback');
      await controls.locator('.np-toggle').click();
      assert.equal(await media.evaluate(video=>video.paused),true,'play button must toggle only once');
      await controls.locator('.np-volume').click();
      assert.equal(await media.evaluate(video=>video.muted),false);
      assert.equal(await media.evaluate(video=>video.paused),true,'volume control does not toggle playback');
      await controls.locator('.np-seek').click();
      assert.equal(await media.evaluate(video=>video.paused),true,'seeking does not toggle playback');
      await controls.locator('.np-volume').dblclick();
      assert.ok(await controls.evaluate(player=>document.fullscreenElement===player),'double-clicking controls must not exit fullscreen');
      await media.dblclick();
      await frame.waitForFunction(()=>!document.fullscreenElement);
      assert.equal(await media.evaluate(video=>video.paused),true,'double-click exits fullscreen while leaving a paused video paused');
      assert.notEqual(await controls.locator('.np-status').textContent(),'无法播放，请检查文件或更换素材');
      await controls.locator('.np-fullscreen').click();
      await frame.waitForFunction(()=>!!document.fullscreenElement);
      await media.evaluate(video=>{video.muted=true;video.currentTime=0;});
      await controls.locator('.np-toggle').click();
      assert.equal(await media.evaluate(video=>video.paused),false);
      await media.dblclick();
      await frame.waitForFunction(()=>!document.fullscreenElement);
      assert.equal(await media.evaluate(video=>video.paused),false,'double-click exits fullscreen while keeping a playing video playing');
      await media.evaluate(video=>video.pause());
      await media.evaluate((video,saved)=>{video.currentTime=saved.time;video.muted=saved.muted;video.loop=saved.loop;},playback);
    }

    await nav('assets');
    await frame.waitForSelector('[data-al-action="upload"]');
    const chooserPromise=page.waitForEvent('filechooser');
    await frame.locator('[data-al-action="upload"]').click();
    await (await chooserPromise).setFiles({name:'微信图片_2026-10-18_114611_958.png',mimeType:'image/png',buffer:Buffer.from(png,'base64')});
    await frame.waitForSelector('.al-history-card');
    const imported=(await history())[0];assert.equal(imported.in_library,false);assert.equal((await library()).length,0);
    await frame.locator('[data-al-collection="library"]').click();
    assert.equal(await frame.locator('.al-history-card').count(),0);
    await frame.locator('[data-al-collection="history"]').click();
    await frame.locator(`[data-al-preview="${imported.id}"]`).click();
    await frame.locator('#alPreviewSave').click();await waitSaved('#alPreviewSave');
    assert.equal((await library()).length,1);
    await frame.locator('#modalClose').click();
    await frame.locator('[data-al-collection="library"]').click();
    const importedCard=frame.locator(`[data-asset-id="${imported.id}"]`);
    const libraryAction=importedCard.locator('[data-al-save]');
    await importedCard.hover();
    assert.equal(await libraryAction.getAttribute('title'),'移出资产库');
    assert.equal(await libraryAction.locator('.ui-icon-libraryRemove').count(),1);
    assert.ok(await libraryAction.isEnabled());
    await frame.locator('#libraryView').screenshot({path:'work/library-remove-action.png'});
    await context.route('**/api/assets/*/library',route=>route.request().method()==='DELETE'
      ?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'测试移出失败'})})
      :route.continue());
    await libraryAction.click();
    await frame.waitForFunction(()=>document.querySelector('#toast')?.textContent==='测试移出失败');
    assert.equal((await library()).length,1);
    assert.ok(await libraryAction.isEnabled(),'failed removal remains retryable');
    await context.unroute('**/api/assets/*/library');
    await libraryAction.click();
    await importedCard.waitFor({state:'detached'});
    assert.equal((await library()).length,0);
    assert.equal((await history()).length,1);
    assert.equal((await history())[0].url,imported.url);
    await frame.goto(frame.url());
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    await nav('assets');
    await frame.locator('[data-al-collection="library"]').click();
    assert.equal(await frame.locator('.al-history-card').count(),0,'removal persists after page reload');
    await frame.locator('[data-al-collection="history"]').click();
    await importedCard.hover();
    assert.equal(await libraryAction.getAttribute('title'),'保存至资产库');
    await libraryAction.click();
    await frame.waitForFunction(id=>document.querySelector(`[data-al-save="${id}"]`)?.title==='移出资产库',imported.id);
    await frame.locator(`[data-al-preview="${imported.id}"]`).click();
    await frame.locator('#alPreviewSave').click();
    await frame.waitForFunction(()=>document.querySelector('#alPreviewSave')?.textContent==='保存至资产库');
    assert.equal((await library()).length,0);
    await frame.locator('#alPreviewSave').click();await waitSaved('#alPreviewSave');
    await frame.locator('#modalClose').click();
    console.log('PASS: library cards and previews toggle membership; failed removal is retryable; history and files survive removal and reload');

    async function generate(kind,prompt,reference){
      await nav('home');await frame.locator('#homeTab-'+kind).click();
      const promptEditor=frame.locator('[data-mention-editor="homePrompt"]');
      if(reference){
        await promptEditor.fill('将@');
        const menu=frame.locator('.asset-mention-menu:not([hidden])');
        await menu.locator('[data-mention-kind="image"]').click();
        await menu.locator('[data-mention-index]').filter({hasText:reference.name}).click();
        await frame.waitForSelector('#homeReferences .home-reference');
      }
      await promptEditor.fill(prompt);await frame.locator('.home-send').click();
      try { await frame.waitForSelector('[data-home-job]:not([hidden]) [data-home-save]',{timeout:20000}); }
      catch(error){
        console.error('Generation diagnostics',kind,await frame.locator('.home-results').innerText(),await frame.locator('#toast').innerText(),await frame.locator('.home-compose-error').allTextContents(),(await api(frame,'/api/test/media-history',{})).data.calls);
        await frame.locator('#agentView').screenshot({path:'work/media-history-failure.png'});throw error;
      }
      const card=frame.locator('[data-home-job]:not([hidden])').last();
      return {card,tid:await card.getAttribute('data-home-job'),cid:new URL(frame.url()).searchParams.get('conversation')};
    }
    for(const kind of ['image','audio','video']){
      const before=(await library()).length;
      const prompt=kind==='image'?`将@${imported.name}的背景换成雪景，保持主体不变`:'测试生成'+kind;
      const result=await generate(kind,prompt,kind==='image'?imported:null);
      const asset=(await history()).find(item=>item.task_id===result.tid);
      assert.ok(asset,kind+' must be registered in history');assert.equal(asset.in_library,false);assert.equal((await library()).length,before);
      if(kind==='image'){
        assert.equal(asset.name,'图1的背景换成雪景.png');
        const details=(await api(frame,'/api/assets/'+asset.id)).data;
        assert.equal(details.generation.prompt,prompt);
        assert.equal(details.generation.references[0].id,imported.id);
        await result.card.locator('.home-image-preview').click();
        await frame.locator('#alPreviewLayout').waitFor();
        await frame.waitForFunction(name=>document.querySelector('#alPreviewLayout .al-preview-properties dd')?.textContent===name,asset.name);
        assert.equal(await frame.locator('#alPreviewLayout .al-preview-prompt').textContent(),prompt);
        await frame.locator('#alPreviewLayout').screenshot({path:'work/image-name-reference-preview.png'});
        await frame.locator('#modalClose').click();
        console.log('PASS: real @ selection produces a concise image filename while preview details retain the full prompt and reference');
      }
      const saveButton=result.card.locator('[data-home-save]');
      if(kind==='video'){
        const player=result.card.locator('.node-player-conversation');
        await player.waitFor();
        await player.locator('video').evaluate(video=>{window.testConversationVideo=video;});
        assert.equal(await player.locator('video').evaluate(video=>video.controls),false);
        await result.card.locator('.home-result-summary').hover();
        await frame.waitForFunction(()=>getComputedStyle(window.testConversationVideo.closest('.node-player').querySelector('.np-library')).opacity==='0');
        await player.hover();
        const overlay=player.locator('.np-library');
        await frame.waitForFunction(()=>getComputedStyle(window.testConversationVideo.closest('.node-player').querySelector('.np-library')).opacity==='1');
        assert.equal(await overlay.getAttribute('title'),'保存至资产库');
        const position=await overlay.evaluate(button=>{const box=button.getBoundingClientRect(),player=button.closest('.node-player').getBoundingClientRect();return {top:box.top-player.top,right:player.right-box.right};});
        assert.ok(position.top>=8&&position.top<=16&&position.right>=8&&position.right<=16);
        await player.locator('.np-toggle').click();
        await frame.waitForFunction(()=>window.testConversationVideo.currentTime>.1);
        await player.locator('.np-toggle').click();
        assert.ok(await player.locator('video').evaluate(video=>video.paused));
        await player.locator('.np-seek').fill('500');
        await frame.waitForFunction(()=>Math.abs(window.testConversationVideo.currentTime-.5)<.1);
        await player.locator('.np-volume').click();
        assert.ok(await player.locator('video').evaluate(video=>video.muted));
        await overlay.click();
        await frame.waitForFunction(tid=>document.querySelector(`[data-home-save="${tid}"]`)?.textContent==='已保存至资产库',result.tid);
        assert.equal(await player.locator('video').evaluate(video=>video===window.testConversationVideo),true);
        assert.ok(await player.locator('video').evaluate(video=>Math.abs(video.currentTime-.5)<.1),'saving preserves playback position');
        assert.ok(await overlay.isDisabled());
        assert.equal(await overlay.getAttribute('title'),'已保存至资产库');
        assert.equal(await overlay.evaluate(button=>getComputedStyle(button).cursor),'default');
        await result.card.screenshot({path:'work/conversation-video-player.png'});
        await verifyFullscreen(result.card,'conversation-video');
        await context.route('**/api/assets/*/library',route=>route.request().method()==='POST'
          ?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'测试保存失败'})})
          :route.continue());
      }else await saveButton.click();
      await frame.waitForFunction(tid=>document.querySelector(`[data-home-save="${tid}"]`)?.textContent==='已保存至资产库',result.tid);
      assert.ok(await saveButton.isDisabled());
      assert.equal(await saveButton.evaluate(button=>getComputedStyle(button).cursor),'default');
      assert.equal((await library()).length,before+1);
      const reopened=(await api(frame,'/api/conversations/'+result.cid)).data.tasks[0];
      assert.equal(kind==='video'?reopened.asset_save_status==='saved':reopened.asset.in_library,true);
      await api(frame,'/api/assets/'+asset.id);
      if(kind==='image')await result.card.locator('.home-image-preview').click();
      else {
        await nav('assets');
        await frame.locator(`[data-al-preview="${asset.id}"]`).click();
      }
      await frame.locator('#alPreviewSave').click();
      await frame.waitForFunction(tid=>document.querySelector(`[data-home-save="${tid}"]`)?.textContent==='保存至资产库',result.tid);
      assert.equal((await library()).length,before);
      assert.equal((await history()).find(item=>item.id===asset.id).url,asset.url);
      await frame.locator('#modalClose').click();
      if(kind!=='image'){
        await frame.locator(`#projectList [data-conversation="${result.cid}"]`).click();
        await result.card.waitFor();
      }
      if(kind==='video'){
        const overlay=result.card.locator('.np-library');
        await result.card.locator('.node-player-conversation').hover();
        await overlay.click();
        await frame.waitForFunction(()=>document.querySelector('#toast')?.textContent==='测试保存失败');
        assert.ok(await overlay.isEnabled(),'failed save remains retryable');
        assert.ok(await saveButton.isEnabled());
        await context.unroute('**/api/assets/*/library');
        await overlay.click();
      }else await saveButton.click();
      await frame.waitForFunction(tid=>document.querySelector(`[data-home-save="${tid}"]`)?.textContent==='已保存至资产库',result.tid);
    }
    console.log('PASS: image/audio/video conversation generation enters history and requires explicit library save');

    await api(frame,'/api/test/media-history',{portrait:true});
    const portraitConversation=await generate('video','竖屏对话播放器测试');
    await frame.waitForFunction(tid=>document.querySelector(`[data-home-job="${tid}"] video`)?.videoHeight===320,portraitConversation.tid);
    const portraitPlayer=portraitConversation.card.locator('.node-player-conversation');
    const portraitBox=await portraitPlayer.boundingBox();
    assert.ok(Math.abs(portraitBox.width/portraitBox.height-180/320)<.02);
    await portraitPlayer.hover();
    await portraitConversation.card.screenshot({path:'work/conversation-video-portrait.png'});
    await verifyFullscreen(portraitConversation.card,'conversation-video');
    await api(frame,'/api/test/media-history',{portrait:false});
    console.log('PASS: conversation videos use canvas controls, preserve playback while saving, show hover save state and fit portrait fullscreen');

    const deletedVideo=(await history()).find(item=>item.task_id===portraitConversation.tid);
    const deletedCard=frame.locator(`[data-home-job="${portraitConversation.tid}"]`);
    await nav('assets');
    await frame.locator('[data-al-collection="history"]').click();
    await context.route('**/api/assets/'+deletedVideo.id,route=>route.request().method()==='DELETE'
      ?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'测试删除失败'})})
      :route.continue());
    await frame.locator(`[data-al-preview="${deletedVideo.id}"]`).click();
    await frame.locator('#alPreviewDelete').click();
    await frame.locator('#alDeleteConfirm').click();
    await frame.waitForFunction(()=>document.querySelector('#alFormError')?.textContent.includes('测试删除失败'));
    assert.equal(await portraitConversation.card.locator('video').count(),1,'failed deletion preserves the cached player');
    await frame.locator('#alDeleteCancel').click();
    await context.unroute('**/api/assets/'+deletedVideo.id);
    await frame.locator(`[data-al-preview="${deletedVideo.id}"]`).click();
    await frame.locator('#alPreviewDelete').click();
    await frame.locator('#alDeleteConfirm').click();
    await frame.locator('#alDeleteConfirm').waitFor({state:'detached'});
    assert.equal(await portraitConversation.card.locator('video').count(),0,'successful deletion immediately removes the cached player');
    assert.equal((await history()).some(item=>item.id===deletedVideo.id),false);
    await frame.locator(`#projectList [data-conversation="${portraitConversation.cid}"]`).click();
    await portraitConversation.card.locator('.home-deleted-media').waitFor();
    assert.equal(await portraitConversation.card.locator('[data-home-save], [data-home-retry-file], .home-turn-note, .node-player').count(),0);
    assert.equal(await portraitConversation.card.locator('.home-user-prompt').textContent(),'竖屏对话播放器测试');
    assert.ok(await portraitConversation.card.locator('[data-home-edit]').isEnabled());
    assert.ok(await portraitConversation.card.locator('[data-home-regenerate]').isEnabled());
    await portraitConversation.card.locator('[data-home-details]').click();
    assert.ok((await frame.locator('.home-result-details[open]').innerText()).includes('视频已删除'));
    await frame.locator('.home-result-details[open] header button').click();
    await frame.goto(info.app+'/market?conversation='+portraitConversation.cid);
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    await portraitConversation.card.locator('.home-deleted-media').waitFor();
    assert.equal(await portraitConversation.card.locator('video').count(),0,'page reload must not resurrect the old video URL');
    await portraitConversation.card.screenshot({path:'work/deleted-video-card.png'});
    await frame.locator('#agentView').screenshot({path:'work/deleted-video-desktop.png'});
    await page.locator('#studio').evaluate(el=>el.style.width='390px');
    await frame.waitForFunction(()=>innerWidth===390);
    assert.ok(await portraitConversation.card.locator('.home-deleted-media').evaluate(card=>{
      const box=card.getBoundingClientRect();return box.left>=0&&box.right<=innerWidth;
    }),'the deleted placeholder must fit the mobile viewport');
    await frame.locator('#agentView').screenshot({path:'work/deleted-video-mobile.png'});
    await page.locator('#studio').evaluate(el=>el.style.width='1200px');
    await frame.waitForFunction(()=>innerWidth===1200);
    const deletedRegenerationCalls=(await api(frame,'/api/test/media-history',{})).data.calls.video;
    await portraitConversation.card.locator('[data-home-regenerate]').click();
    await frame.waitForFunction(()=>document.querySelectorAll('[data-home-job]:not([hidden]) video').length===1);
    assert.equal((await api(frame,'/api/test/media-history',{})).data.calls.video,deletedRegenerationCalls+1);
    assert.equal(await deletedCard.locator('video').count(),0);
    assert.equal(await deletedCard.locator('.home-deleted-media').count(),1);
    console.log('PASS: deleted videos show a compact placeholder immediately and after reload, fit mobile, preserve prompt/details and allow regeneration; failed deletion preserves video');

    for(const kind of ['image','audio']){
      const prompt=kind==='image'?'阳光穿过树叶，人物转身微笑':'雨后森林中的鸟鸣与风声';
      const result=await generate(kind,prompt);
      const card=frame.locator(`[data-home-job="${result.tid}"]`);
      const asset=(await history()).find(item=>item.task_id===result.tid);
      if(kind==='image')await card.locator('.home-image-preview').click();
      else {await nav('assets');await frame.locator(`[data-al-preview="${asset.id}"]`).click();}
      await frame.locator('#alPreviewDelete').click();
      await frame.locator('#alDeleteConfirm').click();
      await frame.locator('#alDeleteConfirm').waitFor({state:'detached'});
      assert.equal(await card.locator('img, audio').count(),0);
      if(kind==='audio')await frame.locator(`#projectList [data-conversation="${result.cid}"]`).click();
      await card.locator('.home-deleted-media').waitFor();
      assert.equal(await card.locator('.home-deleted-media strong').textContent(),kind==='image'?'图片已删除':'音频已删除');
      assert.equal(await card.locator('.home-deleted-media p').textContent(),'原素材已删除，提示词与生成参数仍保留');
      assert.equal(await card.locator('[data-home-save], .home-task-state, .home-turn-note').count(),0);
      await frame.goto(info.app+'/market?conversation='+result.cid);
      await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
      await card.locator('.home-deleted-media').waitFor();
      assert.equal(await card.locator('.home-user-prompt').textContent(),prompt);
      assert.ok(await card.locator('[data-home-edit]').isEnabled());
      await card.screenshot({path:`work/deleted-${kind}-card.png`});
      await card.locator('[data-home-regenerate]').click();
      await frame.waitForFunction(kind=>document.querySelectorAll(`[data-home-job]:not([hidden]) ${kind==='image'?'.home-image-preview':'audio'}`).length===1,kind);
      assert.equal(await card.locator('.home-deleted-media').count(),1);
    }
    console.log('PASS: deleted image/audio results use the shared copy, survive page reload and retain working regeneration without old previews or save actions');

    await api(frame,'/api/test/media-history',{fail_download:true});
    const failure=await generate('video','测试下载失败恢复');
    await failure.card.locator('[data-home-retry-file]').waitFor();
    const failed=(await history()).find(item=>item.task_id===failure.tid);
    assert.equal(failed.file_status,'failed');assert.equal(failed.in_library,false);
    const before=(await api(frame,'/api/test/media-history',{})).data.calls;
    for(let i=0;i<3;i++)await api(frame,'/api/tasks/'+failure.tid);
    assert.equal((await api(frame,'/api/test/media-history',{})).data.calls.download,before.download,'GET must not repeat failed downloads');
    await api(frame,'/api/test/media-history',{fail_download:false});
    await failure.card.locator('[data-home-retry-file]').click();
    await frame.waitForFunction(tid=>!document.querySelector(`[data-home-retry-file="${tid}"]`),failure.tid);
    assert.equal((await history()).find(item=>item.id===failed.id).file_status,'cached');
    assert.equal((await history()).find(item=>item.id===failed.id).in_library,false);
    assert.equal((await api(frame,'/api/test/media-history',{})).data.calls.video,before.video);
    console.log('PASS: failed video download terminates waiting; explicit retry preserves task and history without library membership');

    const imageAsset=(await library()).find(item=>item.task_id&&item.kind==='image');
    const audioAsset=(await library()).find(item=>item.kind==='audio');
    const project=(await api(frame,'/api/projects',{name:'历史与资产库测试',nodes:[
      {id:'video-node',type:'video-asset',title:'视频',assetId:failed.id,assetUrl:(await history()).find(item=>item.id===failed.id).url,x:100,y:120},
      {id:'image-node',type:'asset',title:'图片',assetId:imported.id,assetUrl:imported.url,x:500,y:120},
      {id:'audio-node',type:'audio',title:'音频',assetId:audioAsset.id,assetUrl:audioAsset.url,x:900,y:120}
    ],edges:[]})).data;
    await frame.goto(info.app+'/market?project='+project.id);
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    await frame.locator('[data-node="video-node"]').click({position:{x:80,y:20}});
    await frame.locator('[data-context="save"]').click();
    await frame.waitForFunction(()=>document.querySelector('[data-context="save"]')?.textContent.includes('移出资产库'));
    assert.equal((await history()).find(item=>item.id===failed.id).in_library,true);
    await frame.locator('[data-context="save"]').click();
    await frame.waitForFunction(()=>document.querySelector('[data-context="save"]')?.textContent.includes('保存至资产库'));
    assert.equal((await history()).find(item=>item.id===failed.id).in_library,false);
    assert.equal((await api(frame,'/api/projects/'+project.id)).data.nodes.find(node=>node.id==='video-node').assetId,failed.id);
    assert.equal(await frame.locator('[data-node="video-node"] video').count(),1);
    await frame.locator('[data-context="save"]').click();
    await frame.waitForFunction(()=>document.querySelector('[data-context="save"]')?.textContent.includes('移出资产库'));
    await frame.locator('#dockAddNode').click();
    await frame.locator('[data-palette-node="library"]').click();
    await frame.waitForSelector('[data-picker-collection="history"]');
    assert.equal(await frame.locator('[data-picker-collection="library"]').count(),1);
    await frame.locator('[data-picker-collection="library"]').click();
    console.log('PASS: canvas media toolbar saves to library; picker has separate history/library collections');

    await frame.locator('#alPickerGenerate').click();
    await frame.locator('#igPrompt').fill('独立图片面板测试');
    await frame.locator('#igGenerate').click();
    await frame.waitForSelector('[data-ig-action="save-library"]');
    let dialogAsset=(await history()).find(item=>item.name==='独立图片面板测试'||item.name==='独立图片面板测试.png');
    if(!dialogAsset)dialogAsset=(await history()).filter(item=>item.kind==='image'&&item.source==='generated').find(item=>!item.in_library);
    assert.ok(dialogAsset);assert.equal(dialogAsset.in_library,false);
    await frame.locator('#imageGenerationOverlay').screenshot({path:'work/media-history-image-dialog.png'});
    await frame.locator('[data-ig-action="save-library"]').click();await waitSaved('[data-ig-action="save-library"]');
    assert.equal((await history()).find(item=>item.id===dialogAsset.id).in_library,true);
    await frame.locator('[data-ig-action="save-library"]').click();
    await frame.waitForFunction(()=>document.querySelector('[data-ig-action="save-library"]')?.textContent==='保存至资产库');
    assert.equal((await history()).find(item=>item.id===dialogAsset.id).in_library,false);
    await frame.locator('[data-ig-action="save-library"]').click();await waitSaved('[data-ig-action="save-library"]');
    await frame.locator('[data-ig-action="close"]').first().click();
    await frame.locator('#canvasPanelClose').click();
    if(await frame.locator('#editorClose').isVisible())await frame.locator('#editorClose').click();
    await frame.locator('[data-node="audio-node"]').click({position:{x:80,y:20}});
    await frame.locator('[data-context="pick"]').click();
    await frame.locator('[data-inline-collection="library"]').click();
    await frame.locator('#canvasView').screenshot({path:'work/media-history-canvas.png'});
    await frame.locator('.inline-picker-head button').click({timeout:5000});
    await frame.getByRole('textbox',{name:'音频生成描述',exact:true}).fill('画布音频生成测试');
    const previousAudioIds=(await history()).filter(item=>item.kind==='audio').map(item=>item.id);
    await frame.locator('.inline-generate-button').click();
    await frame.waitForFunction(async previous=>{
      const m=await import('/market-session.js?v=market-sdk-3');const data=await (await fetch('/api/assets',m.withMarketSession({},'/api/assets'))).json();
      return data.some(item=>item.kind==='audio'&&item.source==='generated'&&!previous.includes(item.id));
    },previousAudioIds);
    await frame.waitForFunction(()=>document.querySelector('[data-context="save"]')?.textContent.includes('保存至资产库')&&!document.querySelector('[data-context="save"]')?.textContent.includes('已保存'));
    const canvasAudio=(await history()).find(item=>item.kind==='audio'&&!previousAudioIds.includes(item.id));
    assert.ok(canvasAudio);
    await frame.waitForFunction(async({pid,aid})=>{
      const m=await import('/market-session.js?v=market-sdk-3'),url='/api/projects/'+pid;
      const data=await (await fetch(url,m.withMarketSession({},url))).json();
      return data.nodes.find(node=>node.id==='audio-node')?.assetId===aid;
    },{pid:project.id,aid:canvasAudio.id});
    await frame.locator('[data-context="save"]').click();
    await frame.waitForFunction(()=>document.querySelector('[data-context="save"]')?.textContent.includes('移出资产库'));
    assert.equal((await history()).find(item=>item.id===canvasAudio.id).in_library,true);
    console.log('PASS: image generation dialog and canvas audio generation support manual library saves');

    if(await frame.locator('#editorClose').isVisible())await frame.locator('#editorClose').click();
    await frame.locator('#dockAddNode').click();await frame.locator('[data-palette-node="video"]').click();
    await frame.locator('#inspector [data-mention-editor="promptInput"]').fill('画布视频生成测试');
    const videoNodeId=await frame.locator('.node.selected[data-node-type="video"]').getAttribute('data-node');
    const videoNode=frame.locator(`[data-node="${videoNodeId}"]`);
    const generatedBefore=(await api(frame,'/api/test/media-history',{video_status:'Queued',portrait:true})).data.calls.video;
    const submissionGate=new Promise(resolve=>{releaseSubmission=resolve;});
    let submissionRequests=0;
    const holdSubmission=async route=>{
      if(route.request().method()==='POST'){submissionRequests++;await submissionGate;}
      await route.continue();
    };
    await context.route('**/api/tasks',holdSubmission);
    const submittingRequest=page.waitForRequest(request=>new URL(request.url()).pathname==='/api/tasks'&&request.method()==='POST');
    await frame.locator('#generateBtn').click();
    await submittingRequest;
    assert.ok(await frame.locator('#generateBtn').isDisabled());
    assert.equal(await frame.locator('#generateBtn .generate-label').textContent(),'提交中…');
    await frame.evaluate(async()=>{await document.querySelector('#generateBtn').onclick();await document.querySelector('#generateBtn').onclick();});
    assert.equal(submissionRequests,1,'duplicate handler calls must not resubmit');
    releaseSubmission();
    await frame.waitForFunction(()=>document.querySelector('#generateBtn .generate-label')?.textContent==='排队中');
    await context.unroute('**/api/tasks',holdSubmission);
    assert.equal(await videoNode.locator('.node-meta').textContent(),'排队中');
    assert.ok(await frame.locator('#generateBtn').isDisabled());
    if(await frame.locator('#editorClose').isVisible())await frame.locator('#editorClose').click();
    await frame.locator('#workspaceZoom').click();await frame.locator('[data-zoom="fit"]').click();
    await frame.locator('[data-node="image-node"] .frame-name').click();
    await frame.locator('#inspector').waitFor({state:'visible'});
    await frame.waitForFunction(()=>document.querySelector('#inspector .inline-model-select')?.disabled===false);
    await frame.locator('#editorClose').click();
    await frame.locator('#inspector').waitFor({state:'hidden'});
    await videoNode.locator('.frame-name').click();
    assert.ok(await frame.locator('#generateBtn').isDisabled(),'reselecting a queued node must keep generation locked');
    await frame.evaluate(()=>document.querySelector('#generateBtn').onclick());
    await api(frame,'/api/test/media-history',{video_status:'Processing'});
    await frame.waitForFunction(()=>document.querySelector('#generateBtn .generate-label')?.textContent==='生成中');
    assert.equal(await videoNode.locator('.node-meta').textContent(),'生成中');
    assert.ok(await frame.locator('#generateBtn').isDisabled());
    await frame.goto(info.app+'/market?project='+project.id);
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    await videoNode.locator('.frame-name').click();
    assert.ok(await frame.locator('#generateBtn').isDisabled(),'reopened canvas must restore the running task lock');
    assert.equal(await videoNode.locator('.node-meta').textContent(),'生成中');
    assert.equal((await api(frame,'/api/test/media-history',{})).data.calls.video,generatedBefore+1);
    await frame.locator('#canvasView').screenshot({path:'work/video-node-running.png'});
    console.log('PASS: submission, queue and generation disable the button; reselection/reopening preserve the lock with one provider POST');

    await api(frame,'/api/test/media-history',{video_status:'Success'});
    await frame.waitForFunction(id=>document.querySelector(`[data-node="${id}"] .node-meta`)?.textContent==='生成完成',videoNodeId);
    assert.ok(await frame.locator('#generateBtn').isEnabled());
    assert.equal(await frame.locator('[data-save-video]').count(),0);
    const canvasTask=(await api(frame,'/api/tasks?project_id='+project.id)).data[0];
    assert.ok(canvasTask);assert.equal(canvasTask.status,'done');
    assert.equal((await history()).find(item=>item.task_id===canvasTask.id).in_library,false);
    await frame.locator('[data-context="save"]').click();
    await frame.waitForFunction(()=>document.querySelector('[data-context="save"]')?.textContent.includes('移出资产库'));
    assert.equal((await history()).find(item=>item.task_id===canvasTask.id).in_library,true);
    assert.equal(await videoNode.locator('.node-meta').textContent(),'生成完成','library membership must not expand the canvas status');
    console.log('PASS: canvas video generation completes into history and the video task save action adds it to library');

    if(await frame.locator('#editorClose').isVisible())await frame.locator('#editorClose').click();
    await frame.locator('#inspector').waitFor({state:'hidden'});
    await frame.waitForFunction(id=>document.querySelector(`[data-node="${id}"] video`)?.videoHeight===320,videoNodeId);
    await videoNode.screenshot({path:'work/video-node-portrait.png'});
    await frame.locator('[data-node="video-node"]').screenshot({path:'work/video-node-landscape.png'});
    for(const node of [frame.locator('[data-node="video-node"]'),videoNode])await verifyFullscreen(node);
    console.log('PASS: landscape and 9:16 portrait fullscreen preserve both frame edges and center the video with black bars');

    await videoNode.locator('.frame-name').click();
    await context.route('**/api/tasks',route=>route.request().method()==='POST'
      ? route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'测试提交失败'})})
      : route.continue());
    await frame.locator('#generateBtn').click();
    await frame.waitForFunction(()=>document.querySelector('#toast')?.textContent==='测试提交失败');
    assert.ok(await frame.locator('#generateBtn').isEnabled(),'a rejected submission must release the submission lock');
    assert.equal(await videoNode.locator('.node-meta').textContent(),'生成完成');
    assert.equal((await api(frame,'/api/test/media-history',{})).data.calls.video,generatedBefore+1);
    await context.unroute('**/api/tasks');
    console.log('PASS: rejected submissions release the button and preserve the previous completed preview');

    await frame.locator('#dockMaterial').click();await frame.locator('[data-al-collection="history"]').click();
    await frame.locator('#libraryView').screenshot({path:'work/media-history-library.png'});
    await frame.locator(`[data-al-preview="${imageAsset.id}"]`).click();
    await frame.locator('#alPreviewCopy').waitFor();
    await frame.locator('#alPreviewLayout').screenshot({path:'work/media-history-preview.png'});
    assert.deepEqual(errors,[]);
    console.log('PASS: no browser JavaScript errors; captured history and preview for visual inspection');
  }finally{
    releaseSubmission?.();
    if(browser)await browser.close();server.stdin.end('\n');
    await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(()=>{server.kill();resolve();},15000);server.once('exit',()=>{clearTimeout(timer);resolve();});});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
