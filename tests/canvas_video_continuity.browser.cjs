// Real canvas/player regression. Isolated database and synthetic local video;
// instrument the served module only to replay task polls and project snapshots.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {spawn,spawnSync}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture,api}=require('./market_login.browser.cjs');
const root=path.resolve(__dirname,'..'),clip=path.join(root,'work/canvas-continuity.mp4');
fs.mkdirSync(path.dirname(clip),{recursive:true});
const generated=spawnSync('.venv/Scripts/python.exe',['-c',`import subprocess,sys,imageio_ffmpeg
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=24:d=12','-an','-c:v','libx264','-pix_fmt','yuv420p','-movflags','+faststart',sys.argv[1]],check=True)`,clip],{encoding:'utf8'});
assert.equal(generated.status,0,generated.stderr);
(async()=>{
 const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture],{stdio:['pipe','pipe','pipe']});let browser;
 try{
  const info=await new Promise((resolve,reject)=>{let out='';const timer=setTimeout(()=>reject(Error('fixture startup timeout')),30000);server.stdout.on('data',chunk=>{out+=chunk;const m=out.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});server.stderr.on('data',chunk=>process.stderr.write(chunk));});
  browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
  const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/app.js?*',route=>route.fulfill({contentType:'application/javascript',body:fs.readFileSync(path.join(root,'public/app.js'),'utf8')+'\nwindow.canvasTest={state,renderNodes,updateTaskNode,updateSelection};'}));
  await page.route('**/fixture-video*.mp4',route=>route.fulfill({contentType:'video/mp4',body:fs.readFileSync(clip)}));
  await page.goto(info.parent+'/?user=canvas-continuity');
  const frame=await(await page.waitForSelector('#studio')).contentFrame();
  await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
  const nodes=[{id:'video',type:'video',title:'视频',x:40,y:50},{id:'text',type:'prompt',title:'文本',x:440,y:50,settings:{prompt:'selected'}}];
  const project=(await api(frame,'/api/projects',{name:'连续播放测试',nodes,edges:[]})).data;
  assert.ok(project.id,JSON.stringify(project));
  await frame.goto(info.app+'/market?project='+project.id);
  await frame.waitForSelector('.node[data-node=video]').catch(async error=>{console.error({project,errors,url:frame.url(),body:(await frame.locator('body').innerText()).slice(-3000)});throw error;});
  await frame.evaluate(()=>{const t=window.canvasTest;t.updateTaskNode({id:'fixture-task',node_id:'video',status:'done',video_url:'/fixture-video.mp4'},'video');});
  const video=frame.locator('.node[data-node=video] video');
  await frame.waitForFunction(()=>document.querySelector('.node video')?.readyState>=2);
  await frame.locator('.node[data-node=video] .np-toggle').click({force:true});
  await frame.waitForFunction(()=>document.querySelector('.node video')?.currentTime>.2);
  await frame.evaluate(()=>{
   window.originalVideo=document.querySelector('.node video');window.originalNode=originalVideo.closest('.node');window.originalText=document.querySelector('.node[data-node=text]');
   window.mediaResets=[];for(const event of ['emptied','loadstart','pause'])originalVideo.addEventListener(event,()=>mediaResets.push(event));
  });
  // Updates spaced across multiple animation frames catch asynchronous player disposal.
  for(let i=0;i<12;i++){
   const before=await video.evaluate(v=>v.currentTime);
   await frame.evaluate(i=>{
    const {state,updateTaskNode,renderNodes,updateSelection}=canvasTest;
    updateTaskNode({...state.nodeTasks.video,updated_at:Date.now(),...(i>=6?{video_url:'/fixture-video-cached.mp4',video_cache_status:'cached'}:{})},'video');
    state.nodes=structuredClone(state.nodes);state.nodes[0].x+=1;
    updateSelection(i%2?'text':'video');renderNodes();
   },i);
   await page.waitForTimeout(120);
   assert.equal(await video.evaluate(v=>v===originalVideo&&v.closest('.node')===originalNode&&!v.paused),true,'poll/snapshot preserves active video and card');
   assert.equal(await frame.locator('.node[data-node=text]').evaluate(n=>n===originalText),true,'unrelated card is not remounted');
   assert.ok(await video.evaluate((v,b)=>v.currentTime>=b,before),'playhead cannot reset');
  }
  assert.deepEqual(await frame.evaluate(()=>mediaResets),[]);
  console.log('PASS: polling, cache URL upgrade, cloned snapshots, selection and position updates preserve continuous playback and node identity');
  await frame.evaluate(()=>{const {state,renderNodes}=canvasTest;state.nodes[1].settings.prompt='内容已修改';state.nodes.push({id:'extra',type:'prompt',x:800,y:50,title:'新增',settings:{prompt:'new'}});renderNodes();});
  assert.match(await frame.locator('.node[data-node=text]').innerText(),/内容已修改/);
  await frame.evaluate(()=>{canvasTest.state.nodes=canvasTest.state.nodes.filter(n=>n.id!=='extra');canvasTest.renderNodes();});
  assert.equal(await frame.locator('.node[data-node=extra]').count(),0);
  assert.equal(await video.evaluate(v=>v===originalVideo&&!v.paused),true);
  await frame.evaluate(()=>canvasTest.updateTaskNode({id:'next-task',status:'running'},'video'));
  assert.equal(await video.count(),0,'new task replaces old preview');
  await frame.evaluate(()=>canvasTest.updateTaskNode({id:'next-task',status:'done',video_url:'/fixture-video-next.mp4'},'video'));
  await frame.waitForFunction(()=>document.querySelector('.node video')?.readyState>=2);
  assert.equal(await video.evaluate(v=>v!==originalVideo&&v.currentTime===0),true);
  assert.deepEqual(errors,[]);
  console.log('PASS: unrelated add/edit/delete keeps playback; new generation replaces the old media correctly; no browser errors');
 }finally{await browser?.close();server.stdin.end('\n');}
})().catch(error=>{console.error(error);process.exitCode=1;});
