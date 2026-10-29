// Real application UI and auth; stopped requests never call a live provider.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture,api}=require('./market_login.browser.cjs');
const setup=String.raw`
    sys.path.insert(0, str(Path.cwd() / 'tests'))
    from studio_fixtures import reply
    agent_gate = threading.Event()
    def slow_reply(config, messages):
        context = json.loads(messages[-1]['content'])
        if context.get('latest_message', '').startswith('停止测试'):
            agent_gate.wait(20)
        return reply(config, messages)
    app.llm_json = slow_reply
    @app.app.post('/api/test-release-agent')
    def release_agent():
        agent_gate.set()
        return {'ok': True}
    app.app.router.routes.insert(0, app.app.router.routes.pop())
    app.start_generation_worker = lambda *args: None
    def no_network(*args, **kwargs):
        raise AssertionError('Agent stop test must not call real models')
    app.provider_json = no_network
`;
(async()=>{
  const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','inherit']});let browser;
  try{
    const info=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('fixture timeout')),30000);server.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/FIXTURE (.+)\r?\n/);if(match){clearTimeout(timer);resolve(JSON.parse(match[1]));}});server.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code));});});
    browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
    const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(info.parent+'/?user=agent-stopper');
    const frame=await(await page.waitForSelector('#studio')).contentFrame();
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    await frame.locator('#mainNav [data-view=home]').click();await frame.locator('#homeTab-agent').click();
    const draft=frame.locator('[data-mention-editor=agentInput]'),send=frame.locator('#agentComposer .agent-send');

    // Stop before the create request reaches the server: the request tombstone
    // rejects that later POST and leaves the user draft available for editing.
    let releaseCreate,createEntered;
    const createGate=new Promise(resolve=>releaseCreate=resolve),enteredCreate=new Promise(resolve=>createEntered=resolve);
    await page.route('**/api/agent/runs',async route=>{if(route.request().method()!=='POST')return route.continue();createEntered();await createGate;await route.continue();});
    await draft.fill('停止测试提交前');await send.click();await enteredCreate;
    assert.equal(await send.getAttribute('data-request-state'),'running');assert.equal(await send.isEnabled(),true);
    const earlyStop=page.waitForResponse(response=>response.url().endsWith('/api/generation/stop'));
    await send.click();await earlyStop;releaseCreate();
    await frame.waitForFunction(()=>document.querySelector('.agent-send').dataset.requestState==='idle');
    assert.equal(await draft.innerText(),'停止测试提交前');
    assert.equal((await api(frame,'/api/agent/runs')).data.length,0);
    await page.unroute('**/api/agent/runs');

    const completed=async id=>{for(let i=0;i<100;i++){const data=(await api(frame,'/api/agent/runs/'+id)).data;if(data.run.status==='completed')return data;await new Promise(resolve=>setTimeout(resolve,100));}throw Error('run did not complete');};
    const first=(await api(frame,'/api/agent/runs',{text:'第一段问候',workflow:'studio',request_id:crypto.randomUUID()})).data.run;await completed(first.id);
    const other=(await api(frame,'/api/agent/runs',{text:'另一段问候',workflow:'studio',request_id:crypto.randomUUID()})).data.run;await completed(other.id);
    await frame.goto(info.app+'/market?conversation='+first.project_id);await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    await draft.fill('停止测试运行中');await send.click();
    await frame.waitForFunction(()=>document.querySelector('#agentView').dataset.status==='running'&&!document.querySelector('#agentInput').value);
    await frame.waitForSelector('.agent-thinking');
    await draft.fill('这个草稿必须保留');await draft.press('Enter');
    assert.equal(await send.getAttribute('data-request-state'),'running');
    let snapshot=(await api(frame,'/api/agent/runs/'+first.id)).data;
    assert.equal(snapshot.events.some(event=>event.kind==='user'&&event.text==='这个草稿必须保留'),false,'Enter must not send or stop a running request');

    // Delay the stop response, then switch chats. It must only stop the old
    // run and must not replace the selected chat or clear its new draft.
    let releaseStop,stopEntered,stopPosts=0;
    const stopGate=new Promise(resolve=>releaseStop=resolve),enteredStop=new Promise(resolve=>stopEntered=resolve);
    await page.route('**/api/generation/stop',async route=>{
      stopPosts++;const target=new URL(route.request().url()),host=target.host;target.hostname='127.0.0.1';
      const response=await route.fetch({url:target.href,headers:{...route.request().headers(),host}});stopEntered();await stopGate;await route.fulfill({response});
    });
    await send.click();await enteredStop;await send.click({force:true});
    assert.equal(await frame.locator('.agent-thinking:visible').count(),0,'Stop must immediately hide the spinner and thinking text');
    assert.equal(stopPosts,1);assert.equal(await draft.innerText(),'这个草稿必须保留');
    assert.equal(await send.getAttribute('data-request-state'),'stopping');
    await frame.locator(`.recent-open[data-conversation="${other.project_id}"]`).click();
    await frame.waitForFunction(id=>document.querySelector('#agentView').dataset.conversationId===id,other.project_id);
    await draft.fill('新对话草稿');releaseStop();
    await frame.waitForFunction(()=>document.querySelector('.agent-send').dataset.requestState==='idle');
    assert.equal(await draft.innerText(),'新对话草稿');
    assert.equal(await frame.locator('#agentView').getAttribute('data-conversation-id'),other.project_id);
    const released=await api(frame,'/api/test-release-agent',{});
    assert.equal(released.status,200,JSON.stringify(released));
    for(let i=0;i<100;i++){snapshot=(await api(frame,'/api/agent/runs/'+first.id)).data;if(!snapshot.run.stopping)break;await new Promise(resolve=>setTimeout(resolve,100));}
    assert.equal(snapshot.run.status,'cancelled');assert.equal(snapshot.run.stopping,false);
    assert.equal(snapshot.run.studio_queue.length,0);
    assert.equal((await api(frame,'/api/agent/runs/'+other.id)).data.run.status,'completed');
    await frame.goto(info.app+'/market?conversation='+first.project_id);
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot')&&document.querySelector('#agentView').dataset.status==='cancelled');
    assert.equal(await frame.locator('.agent-thinking').count(),0,'Reloading a stopped run must discard its unclosed thinking step');
    assert.match(await frame.locator('#agentMessages').innerText(),/已停止/);
    await frame.locator('#agentView').screenshot({path:'work/agent-stopped-neutral.png',animations:'disabled'});
    assert.deepEqual(errors,[]);
    console.log('PASS Agent stop before submit, stop while running, Enter guard, preserved drafts, one stop, context race and worker drain');
  }finally{await browser?.close();server.stdin.end('\n');}
})().catch(error=>{console.error(error);process.exitCode=1;});
