// Saved-library filtering and image previews in real conversation composers.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.JHT_PLAYWRIGHT || 'playwright');
const { fixture, api } = require('./market_login.browser.cjs');

const setup = String.raw`
    import io, wave, time
    from PIL import Image
    @app.app.post('/api/test/reference-library')
    def reference_fixture(body:dict):
        if body.get('remove'):
            return app.remove_asset_from_library(body['remove'])
        images=[]
        for name,size in [('已收藏的竖图.png',(180,320)),('仅生成历史的横图.png',(400,100))]:
            data=io.BytesIO()
            Image.new('RGB',size,'#377d89').save(data,'PNG')
            images.append(app.store_uploaded_asset(data.getvalue(),name))
        data=io.BytesIO()
        with wave.open(data,'wb') as stream:
            stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000)
            stream.writeframes(b'\x00\x00'*8000)
        audio=app.store_uploaded_asset(data.getvalue(),'已收藏的音频.wav')
        for asset in [images[0],audio]:app.save_asset_to_library(asset['id'])
        app.save('audio_task',{'id':'reference-audio-task','project_id':body['conversation'],
            'status':'done','prompt':'引用素材预览测试','mode':'sfx','audio_config':{'format':'wav'},
            'asset':audio,'created_at':time.time(),'updated_at':time.time()})
        return {'saved':images[0],'history':images[1],'audio':audio}
    app.app.router.routes.insert(0,app.app.router.routes.pop())
`;

(async()=>{
  const server=spawn(process.env.JHT_TEST_PYTHON||'.venv/Scripts/python.exe',
    ['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','pipe']});
  let browser;
  try{
    const info=await new Promise((resolve,reject)=>{
      let output='';const timer=setTimeout(()=>reject(new Error('Fixture startup timeout')),30000);
      server.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/FIXTURE (.+)\r?\n/);if(match){clearTimeout(timer);resolve(JSON.parse(match[1]));}});
      server.stderr.on('data',chunk=>process.stderr.write(chunk));
      server.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exit '+code));});
    });
    browser=await chromium.launch({channel:'chrome',headless:true,args:[
      '--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
    const context=await browser.newContext({viewport:{width:1500,height:1050}});
    const page=await context.newPage(),errors=[],generationPosts=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(request.method()==='POST'&&/^\/api\/(tasks|images\/generations|audio\/generations|agent\/runs)$/.test(new URL(request.url()).pathname))generationPosts.push(request.url());});
    await page.goto(info.parent+'/?user=reference-preview');
    const frame=await(await page.waitForSelector('#studio')).contentFrame();
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    const conversation=(await api(frame,'/api/conversations',{title:'引用素材预览测试',kind:'audio',prompt:'预览测试',settings:{},request_id:'reference-fixture'})).data;
    const seed=(await api(frame,'/api/test/reference-library',{conversation:conversation.id})).data;
    assert.ok(seed.saved?.id,JSON.stringify(seed));
    await frame.locator('#mainNav [data-view="home"]').click();
    await frame.locator(`[data-conversation="${conversation.id}"]`).click();
    await frame.waitForSelector('#agentView.has-media-conversation:not(.home-landing)');
    const menu=frame.locator('.asset-mention-menu:not([hidden])');
    const viewer=frame.locator('.image-lightbox[open]');
    async function openMenu(agent=false){
      if(agent)await frame.locator('[data-mention-editor="agentInput"]').fill('@');
      else await frame.locator('.home-mention').click();
      await menu.waitFor();
      await menu.locator('[data-mention-kind="asset"]').click();
    }
    async function expectImage(asset){
      await viewer.waitFor();
      await frame.waitForFunction(()=>document.querySelector('.image-lightbox[open] img')?.naturalWidth>0);
      assert.equal(await viewer.locator('img').getAttribute('alt'),asset.name);
      const fits=await viewer.locator('img').evaluate(el=>{
        const r=el.getBoundingClientRect();
        return r.width<=innerWidth&&r.height<=innerHeight&&Math.abs(r.width/r.height-el.naturalWidth/el.naturalHeight)<.02;
      });
      assert.ok(fits,'full reference image fits the viewport without cropping');
    }
    async function closePreview(){await page.keyboard.press('Escape');await viewer.waitFor({state:'hidden'});}
    async function verifyComposer(agent=false){
      await openMenu(agent);
      assert.deepEqual((await menu.locator('[data-mention-index]').allTextContents()).sort(),[seed.saved.name,seed.audio.name].sort());
      if(!agent)await menu.screenshot({path:'work/reference-library-menu.png'});
      await menu.locator('[data-mention-index]').filter({hasText:seed.saved.name}).click();
      const input=frame.locator(agent?'#agentInput':'#homePrompt');
      const value=await input.inputValue();
      const preview=frame.locator(agent?'[data-agent-reference-preview]':'[data-home-reference-preview]');
      await preview.click();await expectImage(seed.saved);
      if(!agent)await viewer.screenshot({path:'work/reference-image-preview.png'});
      await closePreview();
      assert.equal(await input.inputValue(),value);
      const token=frame.locator(`[data-mention-editor="${agent?'agentInput':'homePrompt'}"] .mention-token`);
      await token.click();await expectImage(seed.saved);await closePreview();
      await token.focus();await token.press('Enter');await expectImage(seed.saved);await closePreview();
      await preview.focus();await preview.press('Space');await expectImage(seed.saved);await closePreview();
      assert.equal(await input.inputValue(),value,'preview must not alter the prompt or reference token');
      await frame.locator(agent?'#agentAttached [data-remove]':'#homeReferences [data-home-remove]').click();
      assert.equal(await token.count(),0,'removal still removes the serialized reference');
      assert.equal(await viewer.count(),0,'remove is separate from preview');
    }
    await verifyComposer();
    console.log('PASS: audio conversation assets show only library members; thumbnail and token click/keyboard preview preserve references and removal');

    await api(frame,'/api/test/reference-library',{remove:seed.saved.id});
    await openMenu();
    assert.deepEqual(await menu.locator('[data-mention-index]').allTextContents(),[seed.audio.name]);
    await menu.locator('[data-mention-kind="image"]').click();
    assert.deepEqual((await menu.locator('[data-mention-index]').allTextContents()).sort(),[seed.saved.name,seed.history.name].sort());
    await menu.locator('[data-mention-index]').filter({hasText:seed.history.name}).click();
    await frame.locator('[data-home-reference-preview]').click();await expectImage(seed.history);await closePreview();
    assert.equal((await api(frame,'/api/assets/'+seed.history.id)).data.in_library,false);
    await api(frame,'/api/assets/'+seed.saved.id+'/library',{});
    console.log('PASS: removing from the library updates the next picker; image category still offers history and preview never adds it to the library');

    await frame.locator('#mainNav [data-view="home"]').click();
    await frame.locator('#homeTab-agent').click();
    await verifyComposer(true);
    console.log('PASS: Agent shares library filtering and reference image preview');

    const project=(await api(frame,'/api/projects',{name:'画布引用资产筛选',nodes:[
      {id:'saved-image',type:'asset',title:seed.saved.name,assetId:seed.saved.id,x:0,y:0},
      {id:'history-image',type:'asset',title:seed.history.name,assetId:seed.history.id,x:0,y:400},
      {id:'reference-target',type:'video',title:'引用目标',x:600,y:100,settings:{prompt:''}},
    ],edges:[]})).data;
    await frame.goto(info.app+'/market?project='+project.id);
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    await frame.locator('[data-node="reference-target"] .frame-name').click();
    await frame.locator('#promptInput + .mention-editor').fill('@');
    await menu.waitFor();
    await menu.locator('[data-mention-kind="asset"]').click();
    assert.deepEqual((await menu.locator('[data-mention-index]').allTextContents()).sort(),[seed.saved.name,seed.audio.name].sort());
    await menu.locator('[data-mention-kind="image"]').click();
    assert.deepEqual((await menu.locator('[data-mention-index]').allTextContents()).sort(),[seed.saved.name,seed.history.name].sort());
    assert.deepEqual(generationPosts,[]);
    assert.deepEqual(errors,[]);
    console.log('PASS: canvas node references also filter asset-library membership; no accidental generation or browser errors');
  }finally{
    if(browser)await browser.close();server.stdin.end('\n');
    await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(()=>{server.kill();resolve();},15000);server.once('exit',()=>{clearTimeout(timer);resolve();});});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
