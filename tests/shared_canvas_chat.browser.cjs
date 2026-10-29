// Linked canvas and writing chat use one persisted Studio run; providers are simulated.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture,api}=require('./market_login.browser.cjs');
const setup=String.raw`
    sys.path.insert(0, str(Path.cwd() / 'tests'))
    from studio_fixtures import reply
    app.llm_json = reply
    app.start_generation_worker = lambda *args: None
    def no_network(*args, **kwargs):
        raise AssertionError('Shared chat test must not call models')
    app.provider_json = no_network
`;
(async()=>{
 const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','inherit']});let browser;
 try{
 const info=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('fixture timeout')),30000);server.stdout.on('data',b=>{output+=b;const m=output.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});server.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code));});});
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:1600,height:1050}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(info.parent+'/?user=shared-writer');await page.locator('#studio').evaluate(el=>{el.style.width='1560px';el.style.height='1000px';});const frame=await(await page.waitForSelector('#studio')).contentFrame();await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
 const complete=async id=>{for(let n=0;n<100;n++){const data=(await api(frame,'/api/agent/runs/'+id)).data;if(data.run.status==='completed')return data;if(data.run.status==='blocked')throw Error(data.run.error);await new Promise(r=>setTimeout(r,100));}throw Error('run timeout');};
 const created=(await api(frame,'/api/agent/runs',{text:'创建 2 集短剧并准备制作画布',request_id:crypto.randomUUID(),workflow:'studio'})).data.run;const source=await complete(created.id);const pid=source.run.studio.canvas_project_id;assert.ok(pid);
 const graph=(await api(frame,'/api/projects/'+pid)).data;assert.equal(graph.studio_run_id,created.id);assert.equal(source.canvas_project_revision,graph.revision);
 const openCanvas=async id=>{await frame.goto(info.app+'/market?project='+id);await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));await frame.locator('#workspaceAgent').click();await frame.locator('#canvasAgentPanel #agentView.has-conversation').waitFor();};
 await openCanvas(pid);const messages=frame.locator('#canvasAgentPanel #agentMessages');assert.match(await messages.innerText(),/创建 2 集短剧并准备制作画布/);assert.equal(await frame.locator('[data-dock-starter],.agent-dock-welcome').count(),0);assert.equal(await frame.locator('#agentView').getAttribute('data-conversation-id'),created.project_id);assert.equal((await api(frame,'/api/agent/runs')).data.length,1);
 await frame.locator('#canvasAgentPanel [data-mention-editor=agentInput]').fill('画布里继续问候');await frame.locator('#canvasAgentPanel .agent-send').click();await frame.waitForFunction(()=>document.querySelector('#agentView')?.dataset.status==='completed'&&document.querySelector('#agentMessages').textContent.includes('画布里继续问候'));
 let shared=await complete(created.id);assert.equal(shared.events.filter(e=>e.kind==='user'&&e.text==='画布里继续问候').length,1);assert.equal((await api(frame,'/api/agent/runs')).data.length,1);
 await frame.goto(info.app+'/market?conversation='+created.project_id);await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));assert.match(await frame.locator('#agentMessages').innerText(),/画布里继续问候/);
 await frame.locator('[data-mention-editor=agentInput]').fill('对话页面继续问候');await frame.locator('#agentComposer .agent-send').click();await frame.waitForFunction(()=>document.querySelector('#agentView')?.dataset.status==='completed'&&document.querySelector('#agentMessages').textContent.includes('对话页面继续问候'));await complete(created.id);
 await openCanvas(pid);assert.match(await messages.innerText(),/对话页面继续问候/);assert.equal((await api(frame,'/api/agent/runs')).data.length,1);

 // Polling the shared conversation also refreshes its separate canvas graph.
 const liveGraph=(await api(frame,'/api/projects/'+pid)).data;
 const update=await frame.evaluate(async({pid,graph})=>{
   const m=await import('/market-session.js?v=market-sdk-3');
   const response=await fetch('/api/projects/'+pid,m.withMarketSession({method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:graph.name,revision:graph.revision,groups:graph.groups,edges:graph.edges,viewport:graph.viewport,nodes:[...graph.nodes,{id:'shared-live-update',type:'prompt',title:'同步更新',x:50,y:50}]})},'/api/projects/'+pid));
   return {status:response.status,data:m.marketResponseData(await response.json())};
 },{pid,graph:liveGraph});assert.equal(update.status,200);
 await frame.locator('[data-node="shared-live-update"]').waitFor({timeout:15000});
 assert.equal((await api(frame,'/api/agent/runs/'+created.id)).data.canvas_project_revision,update.data.revision);
 await frame.locator('.canvas-agent-close').click();await frame.locator('#canvasAgentPanel').waitFor({state:'hidden'});await frame.locator('#workspaceAgent').click();await frame.locator('#canvasAgentPanel').waitFor({state:'visible'});assert.match(await messages.innerText(),/画布里继续问候/);
 // An unrelated canvas must not inherit the previous conversation.
 const other=(await api(frame,'/api/projects',{name:'独立空画布',nodes:[],edges:[]})).data;await frame.goto(info.app+'/market?project='+other.id);await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));await frame.locator('#workspaceAgent').click();await frame.locator('#canvasAgentPanel').waitFor({state:'visible'});assert.doesNotMatch(await frame.locator('#agentMessages').innerText(),/画布里继续问候|对话页面继续问候/);assert.equal(await frame.locator('[data-dock-starter]').count(),0);
 // Source history still loads from the linked canvas on a fresh page.
 await openCanvas(pid);assert.match(await messages.innerText(),/画布里继续问候/);await frame.locator('#canvasAgentPanel').screenshot({path:'work/shared-studio-canvas-chat.png'});
 const otherPage=await browser.newPage();await otherPage.goto(info.parent+'/?user=unrelated-writer');const otherFrame=await(await otherPage.waitForSelector('#studio')).contentFrame();await otherFrame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));assert.equal((await api(otherFrame,'/api/agent/runs/'+created.id)).status,404);assert.equal((await api(otherFrame,'/api/projects/'+pid)).status,404);
 assert.deepEqual(errors,[]);console.log('PASS linked source history, canvas-to-writing and writing-to-canvas messages, single run, fresh reload, close/reopen, project/account isolation, removed legacy welcome');
 }finally{await browser?.close();server.stdin.end('\n');}
})().catch(e=>{console.error(e);process.exitCode=1;});
