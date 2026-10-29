// Real authenticated routes and native generation panels, with no provider calls.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture,api}=require('./market_login.browser.cjs');
const setup=String.raw`
    app.generation_queue.start = lambda: None
    original_start = app.start_generation_worker
    app.start_generation_worker = lambda kind, *args: None if kind == 'audio_task' else original_start(kind, *args)
    app.save('audio_model_config', {**app.DEFAULT_AUDIO_MODEL, 'api_key': 'fixture-audio-key'})
    def no_provider(*args, **kwargs):
        raise AssertionError('Stop UI test must never call real providers')
    app.provider_json = no_provider
    app.request_audio = no_provider
`;
(async()=>{
 const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','pipe']});
 let browser;const releases=[];
 try{
  const info=await new Promise((resolve,reject)=>{let out='';const timeout=setTimeout(()=>reject(Error('Fixture timeout')),30000);server.stdout.on('data',b=>{out+=b;const match=out.match(/FIXTURE (.+)\r?\n/);if(match){clearTimeout(timeout);resolve(JSON.parse(match[1]));}});server.stderr.on('data',b=>process.stderr.write(b));server.on('exit',code=>{clearTimeout(timeout);reject(Error('Fixture exit '+code));});});
  browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
  const context=await browser.newContext({viewport:{width:1600,height:1100}}),page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await context.route(/\/app\.js\?/,route=>route.fulfill({contentType:'application/javascript',body:fs.readFileSync('public/app.js','utf8')+'\nwindow.stopTest={state,selectProject,updateSelection,setZoom,inlineNodeMedia,imageGenerator,audioGenerator};'}));
  await page.goto(info.parent+'/?user=media-stop');
  const frame=await(await page.waitForSelector('#studio')).contentFrame();
  await frame.waitForFunction(()=>window.stopTest&&!document.documentElement.hasAttribute('data-app-boot'));
  const project=(await api(frame,'/api/projects',{name:'停止生成验证',nodes:[
   {id:'picture',type:'asset',title:'图片素材',x:100,y:100,image_settings:{prompt:'测试图片，保留草稿',size:'2K',count:1}},
   {id:'sound',type:'audio',title:'声音素材',x:650,y:100,audio_settings:{prompt:'测试音频，保留草稿',name:'测试声音',mode:'sfx'}},
   {id:'clip',type:'video',title:'视频片段',x:1200,y:100,settings:{prompt:'测试镜头，保留草稿',duration:4,resolution:'768p',aspect_ratio:'16:9'}}
  ],edges:[]})).data;
  await frame.evaluate(id=>stopTest.selectProject(id),project.id);
  async function heldPost(path){
   let enter,release;const entered=new Promise(resolve=>enter=resolve),gate=new Promise(resolve=>release=resolve);releases.push(release);
   const handler=async route=>{if(route.request().method()==='POST'){enter(route.request().postDataJSON());await gate;}await route.continue();};
   await context.route('**'+path,handler);return {entered,release,clear:()=>context.unroute('**'+path,handler)};
  }
  async function stopPending(button,path,promptSelector){
   const held=await heldPost(path);const before=await frame.locator(promptSelector).inputValue();
   await button.evaluate(el=>{window.stoppingButtonElement=el;});
   await button.click();const body=await held.entered;assert.ok(body.request_id);
   await button.waitFor({state:'visible'});assert.equal(await button.getAttribute('data-request-state'),'running');assert.equal(await button.isEnabled(),true);
   const response=page.waitForResponse(r=>r.url().endsWith('/api/generation/stop')&&r.request().method()==='POST');
   await button.click();assert.equal((await response).status(),200);
   await frame.waitForFunction(selector=>document.querySelector(selector)?.dataset.requestState==='idle',await button.evaluate(el=>el.id?'#'+el.id:'.'+[...el.classList].find(c=>c!=='request-button'&&!c.startsWith('is-'))));
   const late=page.waitForResponse(r=>r.url().endsWith(path)&&r.request().method()==='POST');held.release();assert.equal((await late).status(),409);await held.clear();
   assert.equal(await frame.locator(promptSelector).inputValue(),before);
   assert.equal(await button.getAttribute('data-request-state'),'idle');
   assert.equal(await button.evaluate(el=>el===window.stoppingButtonElement&&document.activeElement===el),true,'Stop preserves the focused button across panel updates');
  }
  // Canvas image and audio controls preserve their DOM node and draft.
  for(const [id,path] of [['picture','/api/images/generations'],['sound','/api/audio/generations']]){
   await frame.evaluate(id=>stopTest.inlineNodeMedia.focus(stopTest.state.nodes.find(n=>n.id===id)),id);
   const button=frame.locator('.inline-generate-button');await frame.waitForFunction(()=>!document.querySelector('.inline-generate-button')?.disabled);
   await stopPending(button,path,'.inline-generation-prompt');
   assert.equal(await frame.evaluate(id=>!!stopTest.state.nodes.find(n=>n.id===id).assetId,id),false);
  }
  // The native video send control also stops a POST whose task ID is unknown.
  await frame.evaluate(()=>{stopTest.updateSelection('clip');stopTest.state.panX=-800;stopTest.setZoom(1);});
  await frame.waitForFunction(()=>!document.querySelector('#generateBtn')?.disabled);
  const held=await heldPost('/api/tasks'),video=frame.locator('#generateBtn');await video.click();const payload=await held.entered;
  assert.ok(payload.request_id);assert.equal(await video.getAttribute('data-request-state'),'running');
  const stop=page.waitForResponse(r=>r.url().endsWith('/api/generation/stop'));await video.click();assert.equal((await stop).status(),200);
  const late=page.waitForResponse(r=>r.url().endsWith('/api/tasks')&&r.request().method()==='POST');held.release();assert.equal((await late).status(),409);await held.clear();
  await frame.waitForFunction(()=>document.querySelector('#generateBtn').dataset.requestState==='idle');
  // Standalone image and audio dialogs use the same stop control.
  await frame.evaluate(id=>stopTest.imageGenerator.open({project_id:id,prompt:'弹窗图片描述保留'}),project.id);
  await frame.waitForFunction(()=>!document.querySelector('#igGenerate')?.disabled);
  await stopPending(frame.locator('#igGenerate'),'/api/images/generations','#igPrompt');
  await frame.evaluate(()=>stopTest.imageGenerator.close());
  await frame.evaluate(id=>stopTest.audioGenerator.open({project_id:id,prompt:'弹窗音频描述保留',name:'测试音频'}),project.id);
  await frame.waitForFunction(()=>!document.querySelector('.ag-form-actions > button')?.disabled);
  await stopPending(frame.locator('.ag-form-actions > button'),'/api/audio/generations','#agPrompt');
  await frame.locator('#audioGenerationOverlay').screenshot({path:'work/request-stop-audio.png'});
  assert.deepEqual(errors,[]);
  console.log('PASS: canvas image/audio/video and image/audio dialogs stop in-flight POSTs; late submissions are blocked, drafts remain, no result binds, no browser errors');
 }finally{releases.forEach(release=>release());await browser?.close();server.stdin.end('\n');}
})().catch(error=>{console.error(error);process.exitCode=1;});
