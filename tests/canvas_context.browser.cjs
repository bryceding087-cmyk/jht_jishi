// Real browser and server, deterministic model; verifies what the model actually receives.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture,api}=require('./market_login.browser.cjs');
const setup=String.raw`
    sys.path.insert(0, str(Path.cwd() / 'tests'))
    from studio_fixtures import reply
    def aware_reply(config, messages):
        context=json.loads(messages[-1]['content'])
        if 'latest_message' not in context: return reply(config,messages)
        canvas=context['canvas']
        assert any(s['id']=='canvas-context' for s in context['skills'])
        if canvas['available']:
            selected=[n for n in canvas['nodes'] if n['id'] in canvas['selection']['selected_node_ids']]
            text='当前画布：'+canvas['project']['name']+'；节点数：'+str(canvas['summary']['node_count'])+'；选中：'+','.join(n.get('title','') for n in selected)+'；节点文本：'+','.join(n.get('text','') for n in canvas['nodes'])
        else: text='当前对话未关联画布'
        return {'choices':[{'message':{'content':json.dumps({'intent':'chat','reply':text},ensure_ascii=False)}}]}
    app.llm_json=aware_reply
    def no_network(*args,**kwargs): raise AssertionError('No live model requests allowed')
    app.provider_json=no_network
`;
(async()=>{const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','inherit']});let browser;
try{
 const info=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('fixture timeout')),30000);server.stdout.on('data',b=>{output+=b;const m=output.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});server.once('exit',c=>{clearTimeout(timer);reject(Error('fixture exit '+c));});});
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:1600,height:1050}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(info.parent+'/?user=canvas-aware');await page.locator('#studio').evaluate(el=>{el.style.width='1560px';el.style.height='1000px';});const frame=await(await page.waitForSelector('#studio')).contentFrame();await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
 await page.route(/\/app\.js\?/,route=>route.fulfill({contentType:'application/javascript',body:fs.readFileSync('public/app.js','utf8')+'\nwindow.canvasAwareTest={state,updateSelection,scheduleSave};'}));
 const project=(await api(frame,'/api/projects',{name:'上下文画布',nodes:[{id:'a',type:'prompt',title:'雨后街道',text:'清晨的小镇',x:160,y:200},{id:'b',type:'prompt',title:'人物',text:'背着书包的学生',x:600,y:200}],edges:[{from:'a',to:'b'}]})).data;
 const open=async pid=>{await frame.goto(info.app+'/market?project='+pid);await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));await frame.locator('#workspaceAgent').click();await frame.locator('#canvasAgentPanel').waitFor({state:'visible'});};
 const ask=async(text,expected)=>{await frame.locator('#canvasAgentPanel [data-mention-editor=agentInput]').fill(text);await frame.locator('#canvasAgentPanel .agent-send').click();await frame.waitForFunction(expected=>document.querySelector('#agentView')?.dataset.status==='completed'&&[...document.querySelectorAll('#agentMessages .agent-prose')].some(el=>el.textContent.includes(expected)),expected);};
 await open(project.id);await frame.evaluate(()=>canvasAwareTest.updateSelection('a'));await ask('我在哪个项目','当前画布：上下文画布；节点数：2；选中：雨后街道');
 await frame.evaluate(()=>{canvasAwareTest.state.nodes[0].text='未自动保存的最新文本';canvasAwareTest.scheduleSave();clearTimeout(canvasAwareTest.state.saveTimer);});
 await ask('看一下最新内容','节点文本：未自动保存的最新文本');
 assert.equal((await api(frame,'/api/projects/'+project.id)).data.nodes[0].text,'未自动保存的最新文本');
 await frame.locator('#canvasAgentPanel [data-command=skills]').click();assert.match(await frame.locator('#agentDialog').innerText(),/画布理解/);assert.match(await frame.locator('#agentDialog').innerText(),/剧本创作/);assert.match(await frame.locator('#agentDialog').innerText(),/图片与视频生成/);await frame.locator('#agentDialog .agent-dialog-head button').click();
 // Reopening restores the bound run and reads the latest server graph, not the conversation title.
 await open(project.id);await frame.evaluate(()=>canvasAwareTest.updateSelection('b'));await ask('我选中了什么','选中：人物');
 const empty=(await api(frame,'/api/projects',{name:'未命名画布',nodes:[],edges:[]})).data;await open(empty.id);await ask('我在哪个项目','当前画布：未命名画布；节点数：0');assert.doesNotMatch(await frame.locator('#agentMessages').innerText(),/清晨的小镇|背着书包的学生|未自动保存的最新文本/);
 await frame.locator('#canvasAgentPanel').screenshot({path:'work/canvas-aware-agent.png'});
 const runs=(await api(frame,'/api/agent/runs')).data;assert.equal(runs.filter(r=>r.creation_type==='canvas').length,2);assert.equal((await api(frame,'/api/generation-queue')).data.tasks.length,0);assert.deepEqual(errors,[]);
 console.log('PASS first-message canvas metadata/selection, latest unsaved edits flushed before follow-up, restored run selection, real skill catalog, empty canvas, project isolation, no media tasks');
}finally{await browser?.close();server.stdin.end('\n');}})().catch(e=>{console.error(e);process.exitCode=1;});
