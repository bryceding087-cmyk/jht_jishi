// Real scoped graph mutations/UI actions and queue receipts, with simulated LLM.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture,api}=require('./market_login.browser.cjs');
const setup=String.raw`
    app.generation_queue.start=lambda:None
    def tools_reply(config,messages):
        context=json.loads(messages[-1]['content']);text=context['latest_message']
        if text=='编辑当前画布':
            ops=[{'name':'rename','args':{'title':'Agent编辑后的画布'}},
                 {'name':'update_node','args':{'node_id':'a','changes':{'text':'Agent更新的正文','x':240,'y':220}}},
                 {'name':'add_node','args':{'node':{'id':'c','type':'prompt','title':'新增文本','text':'新增内容','x':700,'y':300}}},
                 {'name':'connect','args':{'from':'a','to':'c'}},
                 {'name':'create_group','args':{'group_id':'g','node_ids':['a','c'],'title':'创意组','color':'#4285f4'}},
                 {'name':'select','args':{'node_ids':['c']}}]
        elif text in ('undo','redo'):ops=[{'name':text,'args':{}}]
        elif text=='调整视图':ops=[{'name':'zoom','args':{'value':.7}},{'name':'pan','args':{'x':80,'y':90}},{'name':'toggle','args':{'value':'snap','enabled':True}},{'name':'toggle','args':{'value':'minimap','enabled':True}}]
        elif text=='打开当前画布素材':ops=[{'name':'panel','args':{'value':'assets'}}]
        elif text=='生成B':ops=[{'name':'generate','args':{'node_ids':['b']}}]
        elif text=='取消B':ops=[{'name':'cancel_generation','args':{'node_ids':['b']}}]
        elif text=='越权修改':ops=[{'name':'update_node','args':{'node_id':'foreign','changes':{'text':'越权内容'}}}]
        elif text=='冲突测试':
            import time
            time.sleep(2)
            ops=[{'name':'rename','args':{'title':'不应覆盖手动编辑'}}]
        else:ops=[{'name':'fit','args':{'node_ids':'all'}}]
        assert context['canvas']['project']['id']
        return {'choices':[{'message':{'content':json.dumps({'intent':'canvas','reply':'开始操作','operations':ops},ensure_ascii=False)}}]}
    app.llm_json=tools_reply
    def no_paid_calls(*args,**kwargs):raise AssertionError('No real provider calls')
    app.provider_json=no_paid_calls
`;
(async()=>{const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','inherit']});let browser;
try{
 const info=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('fixture timeout')),30000);server.stdout.on('data',b=>{output+=b;const m=output.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});server.once('exit',code=>{clearTimeout(timer);reject(Error('fixture '+code));});});
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:1700,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(info.parent+'/?user=canvas-operator');await page.locator('#studio').evaluate(el=>{el.style.width='1660px';el.style.height='1050px';});const frame=await(await page.waitForSelector('#studio')).contentFrame();await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
 const project=(await api(frame,'/api/projects',{name:'原画布',nodes:[{id:'a',type:'prompt',title:'A',text:'原文',x:180,y:200},{id:'b',type:'asset',title:'B',x:550,y:200,image_settings:{prompt:'一只橘猫',size:'2K',aspect_ratio:'1:1'}}],edges:[],viewport:{x:30,y:30,zoom:1}})).data;
 const other=(await api(frame,'/api/projects',{name:'另一张画布',nodes:[{id:'foreign',type:'prompt',title:'禁止修改',text:'原样保留',x:100,y:100}],edges:[]})).data;
 await frame.goto(info.app+'/market?project='+project.id);await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));await frame.locator('#workspaceAgent').click();await frame.locator('#canvasAgentPanel').waitFor({state:'visible'});
 const ask=async(text,status='completed')=>{const before=await frame.locator('#agentMessages .agent-user').count();await frame.locator('#canvasAgentPanel [data-mention-editor=agentInput]').fill(text);await frame.locator('#canvasAgentPanel .agent-send').click();await frame.waitForFunction(({before,status})=>document.querySelectorAll('#agentMessages .agent-user').length>before&&document.querySelector('#agentView').dataset.status===status,{before,status});};
 await ask('编辑当前画布');await frame.locator('[data-node="c"]').waitFor();assert.match(await frame.locator('[data-node="a"]').innerText(),/Agent更新的正文/);let saved=(await api(frame,'/api/projects/'+project.id)).data;assert.equal(saved.name,'Agent编辑后的画布');assert.equal(saved.edges.length,1);assert.equal(saved.groups[0].title,'创意组');await frame.waitForFunction(()=>document.querySelector('[data-node="c"]')?.classList.contains('selected'));
 await ask('undo');await frame.locator('[data-node="c"]').waitFor({state:'detached'});assert.equal((await api(frame,'/api/projects/'+project.id)).data.name,'原画布');
 await ask('redo');await frame.locator('[data-node="c"]').waitFor();assert.equal((await api(frame,'/api/projects/'+project.id)).data.name,'Agent编辑后的画布');
 await ask('调整视图');await frame.waitForFunction(()=>document.querySelector('#workspaceZoomValue')?.textContent==='70%'&&document.querySelector('#toggleSnap')?.getAttribute('aria-pressed')==='true'&&document.querySelector('#toggleMinimap')?.getAttribute('aria-pressed')==='true');assert.equal(await frame.locator('#workspaceMinimap').isVisible(),true);
 await ask('打开当前画布素材');await frame.locator('.canvas-tools-panel[open]').waitFor();assert.doesNotMatch(await frame.locator('.canvas-tools-panel').innerText(),/另一张画布|禁止修改/);await frame.locator('.canvas-tools-panel button').click();
 await ask('生成B');let queue=(await api(frame,'/api/generation-queue')).data;assert.equal(queue.tasks.length,1);assert.equal(queue.tasks[0].state,'waiting');const imageId=queue.tasks[0].id;
 const runs=(await api(frame,'/api/agent/runs')).data;assert.equal(runs.length,1);const rid=runs[0].id;
 assert.equal((await api(frame,'/api/images/generations/'+imageId)).data.project_id,project.id);
 await ask('取消B');assert.equal((await api(frame,'/api/images/generations/'+imageId)).data.status,'cancelled');
 await ask('越权修改','blocked');assert.equal((await api(frame,'/api/projects/'+other.id)).data.nodes[0].text,'原样保留');assert.match(await frame.locator('#agentMessages').innerText(),/只能操作当前画布/);

 // A concurrent manual edit wins over an older model plan.
 await frame.locator('#canvasAgentPanel [data-mention-editor=agentInput]').fill('冲突测试');await frame.locator('#canvasAgentPanel .agent-send').click();await frame.locator('.agent-thinking').waitFor();
 const latest=(await api(frame,'/api/projects/'+project.id)).data;
 const conflict=await frame.evaluate(async({pid,graph})=>{const m=await import('/market-session.js?v=market-sdk-3');const url='/api/projects/'+pid;const r=await fetch(url,m.withMarketSession({method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'用户手动修改',revision:graph.revision,nodes:graph.nodes,edges:graph.edges,groups:graph.groups,viewport:graph.viewport})},url));return r.status;},{pid:project.id,graph:latest});assert.equal(conflict,200);
 await frame.waitForFunction(()=>document.querySelector('#agentView').dataset.status==='blocked'&&document.querySelector('#agentMessages').textContent.includes('画布已更新'));
 assert.equal((await api(frame,'/api/projects/'+project.id)).data.name,'用户手动修改');
 // The same account's other graph is also outside this run's authority.
 assert.equal((await api(frame,'/api/agent/runs/'+rid+'/canvas-context')).data.project.id,project.id);
 await frame.locator('#canvasAgentPanel').screenshot({path:'work/canvas-tools-scoped.png'});
 const outsider=await browser.newPage();await outsider.goto(info.parent+'/?user=other-account');const outsiderFrame=await(await outsider.waitForSelector('#studio')).contentFrame();await outsiderFrame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));assert.equal((await api(outsiderFrame,'/api/agent/runs/'+rid+'/canvas-context')).status,404);
 // History restoration does not replay old camera commands.
 await frame.goto(info.app+'/market?project='+project.id);await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));await frame.locator('#workspaceAgent').click();await frame.locator('#canvasAgentPanel').waitFor({state:'visible'});assert.equal((await api(frame,'/api/generation-queue')).data.tasks.filter(t=>t.state==='waiting').length,0);
 assert.deepEqual(errors,[]);console.log('PASS real Agent graph mutations, grouping/edges, undo/redo, selected node, zoom/pan/toggles, scoped panel, durable queue submission/cancellation, same-account cross-canvas and cross-account denial, no history replay, no paid calls');
}finally{await browser?.close();server.stdin.end('\n');}})().catch(error=>{console.error(error);process.exitCode=1;});
