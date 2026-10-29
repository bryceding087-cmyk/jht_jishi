// Real text endpoints, isolated auth/storage, and gated fake model responses.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture,api}=require('./market_login.browser.cjs');
const setup=String.raw`
    text_gates = []
    def gated_text(config, messages):
        gate = threading.Event()
        text_gates.append(gate)
        if not gate.wait(30):
            raise AssertionError('Text test did not release provider response')
        if '提示词编辑' in messages[0]['content']:
            content = '模型润色后的新文本'
        else:
            content = json.dumps({'title': '模型生成的脚本', 'scenes': [{'index': 1, 'duration': '5s', 'description': '人物走过街道'}],
                'resources': [], 'prompts': [{'scene_index': 1, 'storyboard_prompt': '人物站在暖色街道中', 'motion_prompt': '镜头缓慢向前移动'}]}, ensure_ascii=False)
        return {'choices': [{'message': {'content': content}}]}
    app.llm_json = gated_text
    @app.app.get('/api/test-text-gates')
    def text_gate_state():
        return {'count': len(text_gates)}
    app.app.router.routes.insert(0, app.app.router.routes.pop())
    @app.app.post('/api/test-release-text/{index}')
    def release_text(index: int):
        text_gates[index - 1].set()
        return {'ok': True}
    app.app.router.routes.insert(0, app.app.router.routes.pop())
    def no_network(*args, **kwargs):
        raise AssertionError('Text stop test must not call real providers')
    app.provider_json = no_network
`;
(async()=>{
  const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','inherit']});let browser;
  try{
    const info=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('fixture timeout')),30000);server.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/FIXTURE (.+)\r?\n/);if(match){clearTimeout(timer);resolve(JSON.parse(match[1]));}});server.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code));});});
    browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
    const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.route(/\/app\.js\?/,route=>route.fulfill({contentType:'application/javascript',body:fs.readFileSync('public/app.js','utf8')+'\nwindow.textStopTest={state,updateSelection,renderNodeDetail,renderScriptWorkflow,openScriptWorkflow,selectProject,showView,flushSave};'}));
    await page.goto(info.parent+'/?user=text-stopper');
    await page.locator('#studio').evaluate(element=>{element.style.width='1560px';element.style.height='960px';});
    const frame=await(await page.waitForSelector('#studio')).contentFrame();
    await frame.waitForFunction(()=>window.textStopTest&&!document.documentElement.hasAttribute('data-app-boot'));
    const graph=(await api(frame,'/api/projects',{name:'文本停止测试',nodes:[
      {id:'idea',type:'prompt',title:'创意',x:100,y:100,settings:{prompt:'原始创意'}},
      {id:'other',type:'prompt',title:'其他文本',x:100,y:400,settings:{prompt:'另一节点的内容'}},
      {id:'script',type:'script',title:'脚本',x:500,y:100,script_data:{title:'原剧本',source_node_id:'idea',scenes:[{index:1,duration:'5s',description:'原镜头',storyboard_prompt:'原画面',motion_prompt:'原运动'}],resources:[]}},
    ],edges:[{from:'idea',to:'script'}]})).data;
    await frame.evaluate(id=>textStopTest.selectProject(id),graph.id);
    await frame.evaluate(()=>textStopTest.updateSelection('idea'));
    const polish=frame.locator('#polishPromptBtn'),input=frame.locator('#detailPromptInput'),editor=frame.locator('[data-mention-editor=detailPromptInput]');
    const gate=async count=>{for(let n=0;n<100;n++){if((await api(frame,'/api/test-text-gates')).data.count>=count)return;await new Promise(resolve=>setTimeout(resolve,50));}throw Error('provider gate '+count+' did not start');};
    const release=async index=>assert.equal((await api(frame,'/api/test-release-text/'+index,{})).status,200);
    const state=async(selector,value)=>frame.waitForFunction(({selector,value})=>document.querySelector(selector)?.dataset.requestState===value,{selector,value});
    const responseFor=(path,status)=>{const response=page.waitForResponse(response=>response.url().endsWith(path)&&response.request().method()==='POST'&&(!status||response.status()===status));response.catch(()=>{});return response;};

    // A stop before submission creates a tombstone; the original draft stays.
    let enterCreate,releaseCreate;
    const createEntered=new Promise(resolve=>enterCreate=resolve),createGate=new Promise(resolve=>releaseCreate=resolve);
    await page.route('**/api/prompts/polish',async route=>{enterCreate();await createGate;await route.continue();});
    const earlyResponse=responseFor('/api/prompts/polish');
    await polish.click();await createEntered;const earlyStop=responseFor('/api/generation/stop');await polish.click();await earlyStop;
    await state('#polishPromptBtn','idle');releaseCreate();assert.equal((await earlyResponse).status(),409);
    assert.equal(await input.inputValue(),'原始创意');assert.equal((await api(frame,'/api/test-text-gates')).data.count,0);
    await page.unroute('**/api/prompts/polish');

    // Preserve the same button and focus across an editor redraw. Stop keeps
    // edits, and an old response cannot reset a newer request's running state.
    const oldPolish=responseFor('/api/prompts/polish');await polish.click();await gate(1);
    await frame.evaluate(()=>{window.savedTextButton=document.querySelector('#polishPromptBtn');savedTextButton.focus();textStopTest.renderNodeDetail(textStopTest.state.nodes.find(n=>n.id==='idea'));});
    assert.equal(await frame.evaluate(()=>savedTextButton===document.querySelector('#polishPromptBtn')&&document.activeElement===savedTextButton),true);
    await editor.fill('运行期间的新草稿');const stop1=responseFor('/api/generation/stop');await polish.click();await stop1;await state('#polishPromptBtn','idle');
    assert.equal(await input.inputValue(),'运行期间的新草稿');
    const newPolish=responseFor('/api/prompts/polish',200);await polish.click();await gate(2);
    await release(1);assert.equal((await oldPolish).status(),409);await state('#polishPromptBtn','running');
    await frame.evaluate(()=>textStopTest.renderNodeDetail(textStopTest.state.nodes.find(n=>n.id==='idea')));
    await release(2);assert.equal((await newPolish).status(),200);await state('#polishPromptBtn','idle');
    assert.equal(await input.inputValue(),'模型润色后的新文本');
    await frame.evaluate(()=>textStopTest.flushSave());

    // Each script action uses its own retained button; changing steps moves
    // the active Stop into the header and does not make it unreachable.
    await frame.evaluate(()=>textStopTest.openScriptWorkflow('script',1));
    const originalScript=await frame.evaluate(()=>JSON.stringify(textStopTest.state.nodes.find(n=>n.id==='script').script_data));
    const cases=[['#scriptGenerateScenes',1,2],['#scriptGenerateResources',2,3],['#scriptGeneratePrompts',3,1],['[data-script-generate-prompt="0"]',3,2]];
    for(let index=0;index<cases.length;index++){
      const [selector,step,nextStep]=cases[index],gateIndex=index+3;
      await frame.locator('[data-script-step="'+step+'"]').click();
      const generated=responseFor('/api/scripts/generate');await frame.locator(selector).click();await gate(gateIndex);
      await frame.locator(selector).evaluate(button=>{window.savedScriptButton=button;button.focus();});
      await frame.evaluate(()=>textStopTest.renderScriptWorkflow());
      assert.equal(await frame.evaluate(()=>savedScriptButton.isConnected&&document.activeElement===savedScriptButton),true);
      await frame.locator('[data-script-step="'+nextStep+'"]').click();
      assert.equal(await frame.evaluate(()=>savedScriptButton.isConnected&&savedScriptButton.dataset.requestState==='running'),true);
      let enterStop,releaseStop,stopPosts=0;
      const enteredStop=new Promise(resolve=>enterStop=resolve),stopGate=new Promise(resolve=>releaseStop=resolve);
      await page.route('**/api/generation/stop',async route=>{stopPosts++;const target=new URL(route.request().url()),host=target.host;target.hostname='127.0.0.1';const response=await route.fetch({url:target.href,headers:{...route.request().headers(),host}});enterStop();await stopGate;await route.fulfill({response});});
      await frame.evaluate(()=>savedScriptButton.click());await enteredStop;await frame.evaluate(()=>savedScriptButton.click());
      assert.equal(stopPosts,1);assert.equal(await frame.evaluate(()=>savedScriptButton.dataset.requestState),'stopping');
      releaseStop();await frame.waitForFunction(()=>savedScriptButton.dataset.requestState==='idle');
      await release(gateIndex);assert.equal((await generated).status(),409);
      assert.equal(await frame.evaluate(()=>JSON.stringify(textStopTest.state.nodes.find(n=>n.id==='script').script_data)),originalScript);
      await page.unroute('**/api/generation/stop');
    }
    assert.equal((await api(frame,'/api/projects/'+graph.id)).data.nodes.find(n=>n.id==='script').script_data.title,'原剧本');

    // A failed stop returns to running; retrying Stop succeeds, and navigation
    // cannot receive the old model text or request state.
    await frame.evaluate(()=>textStopTest.updateSelection('idea'));
    const lastPolish=responseFor('/api/prompts/polish');await polish.click();await gate(7);
    await page.route('**/api/generation/stop',route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"测试停止失败"}'}));
    await polish.click();await state('#polishPromptBtn','running');await page.unroute('**/api/generation/stop');
    const finalStop=responseFor('/api/generation/stop');await polish.click();await finalStop;await state('#polishPromptBtn','idle');
    await frame.evaluate(()=>textStopTest.updateSelection('other'));await editor.fill('新节点的新草稿');
    await release(7);assert.equal((await lastPolish).status(),409);await state('#polishPromptBtn','idle');assert.equal(await input.inputValue(),'新节点的新草稿');

    // Ordinary script generation remains usable after stops; a failure's
    // retry button becomes the same stoppable control as the primary action.
    await frame.evaluate(()=>textStopTest.openScriptWorkflow('script',3));
    const successfulScript=responseFor('/api/scripts/generate',200);await frame.locator('#scriptGeneratePrompts').click();await gate(8);await release(8);await successfulScript;
    await state('#scriptGeneratePrompts','idle');
    assert.equal(await frame.evaluate(()=>textStopTest.state.nodes.find(n=>n.id==='script').script_data.scenes[0].storyboard_prompt),'人物站在暖色街道中');
    await page.route('**/api/scripts/generate',route=>route.fulfill({status:503,contentType:'application/json',body:'{"detail":"测试生成失败"}'}));
    await frame.locator('#scriptGeneratePrompts').click();await frame.locator('#scriptRetryAction').waitFor();await page.unroute('**/api/scripts/generate');
    const retried=responseFor('/api/scripts/generate');await frame.locator('#scriptRetryAction').click();await gate(9);
    const retryStop=responseFor('/api/generation/stop');await frame.locator('#scriptRetryAction').click();await retryStop;await state('#scriptRetryAction','idle');
    await release(9);assert.equal((await retried).status(),409);
    assert.equal(await frame.evaluate(()=>textStopTest.state.nodes.find(n=>n.id==='script').script_data.scenes[0].storyboard_prompt),'人物站在暖色街道中');
    const backgroundScript=responseFor('/api/scripts/generate',200);await frame.locator('#scriptGeneratePrompts').click();await gate(10);
    await frame.evaluate(()=>textStopTest.showView('agent'));await release(10);await backgroundScript;
    assert.equal(await frame.evaluate(()=>textStopTest.state.view),'agent');
    assert.equal(await frame.locator('#modalBackdrop').isVisible(),false,'A late script response must not reopen its editor over another view');
    assert.deepEqual(errors,[]);
    console.log('PASS text stop before submit, running polish, preserved edits, stable buttons/focus, newer request race, all script actions across steps, one stop, server save guard, stop failure, navigation isolation, script success and stoppable retry');
  }finally{await browser?.close();server.stdin.end('\n');}
})().catch(error=>{console.error(error);process.exitCode=1;});
