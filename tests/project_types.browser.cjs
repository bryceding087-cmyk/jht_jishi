// Real routes in an isolated database; all Agent responses are local fixtures.
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
        raise AssertionError('Project tests must never call live models')
    app.provider_json = no_network
    @app.app.post('/api/test-conversation-cover')
    def seed_cover(body: dict):
        return app.save('asset', {'id': 'fixture-cover', 'name': '生成图片', 'kind': 'image', 'url': '/icon-32.png', 'source': 'generated', 'project_id': body['id'], 'created_at': 1})
    app.app.router.routes.insert(0, app.app.router.routes.pop())
`;
(async()=>{
  const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','inherit']});let browser;
  try{
    const info=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('fixture timeout')),30000);server.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/FIXTURE (.+)\r?\n/);if(match){clearTimeout(timer);resolve(JSON.parse(match[1]));}});server.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code));});});
    browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
    const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(info.parent+'/?user=project-types');
    const frame=await(await page.waitForSelector('#studio')).contentFrame();
    await page.locator('#studio').evaluate(el=>{el.style.width='1560px';el.style.height='960px';});
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    const chats=[];
    for(const kind of ['image','video','audio']){
      const result=await api(frame,'/api/conversations',{kind,title:'对话分类 '+kind,prompt:'分类验证',request_id:crypto.randomUUID()});
      assert.equal(result.status,201);chats.push(result.data);
    }
    assert.equal((await api(frame,'/api/test-conversation-cover',{id:chats[0].id})).status,200);
    const standalone=(await api(frame,'/api/agent/runs',{text:'你好',workflow:'studio',request_id:crypto.randomUUID()})).data.run;
    const linked=(await api(frame,'/api/agent/runs',{text:'一句话创建 2 集短剧：小林回家送信',workflow:'studio',request_id:crypto.randomUUID()})).data.run;
    async function completed(id){for(let i=0;i<150;i++){const result=(await api(frame,'/api/agent/runs/'+id)).data;if(result.run.status==='completed')return result.run;if(result.run.status==='blocked')throw Error(result.run.error);await new Promise(resolve=>setTimeout(resolve,100));}throw Error('Agent timeout');}
    await completed(standalone.id);const generated=await completed(linked.id);assert.ok(generated.studio.canvas_project_id);
    const canvas=(await api(frame,'/api/projects',{name:'手动画布',nodes:[],edges:[]})).data;
    const openProjects=async()=>{await frame.locator('#mainNav [data-view=projects]').click();await frame.waitForSelector('.projects-library[aria-busy=false]');};
    const tab=kind=>frame.locator(`[data-project-category-tab="${kind}"]`);
    const card=id=>frame.locator(`[data-project-card="${id}"]`);
    const menu=async(id,action)=>{await card(id).locator('summary').click();await card(id).locator(`[role=menuitem][data-project-action="${action}"]`).click();};
    await openProjects();
    assert.deepEqual(await frame.locator('.recent-section-label').allTextContents(),['对话','画布']);
    assert.deepEqual(await frame.locator('.projects-type-tabs button').allTextContents(),['对话项目','画布项目']);
    assert.equal(await card(standalone.project_id).count(),1,'Agent before canvas creation belongs to canvas projects');
    assert.equal(await card(generated.studio.canvas_project_id).count(),1);
    assert.equal(await card(linked.project_id).count(),0,'Agent and generated canvas must not duplicate');
    assert.equal(await card(canvas.id).count(),1);
    for(const chat of chats)assert.equal(await card(chat.id).count(),0);
    await frame.locator('.projects-library').screenshot({path:'work/project-types-canvas.png',animations:'disabled'});
    await tab('conversation').click();
    assert.equal(await frame.locator('[data-project-card]').count(),3);
    assert.equal(await card(chats[0].id).locator('img').count(),1);
    assert.equal(await frame.locator('[data-project-action=new-folder]').count(),0);
    assert.equal(await card(chats[0].id).locator('.projects-cover').evaluate(el=>{const box=el.getBoundingClientRect();return Math.abs(box.width-box.height)<1;}),true);
    await card(chats[0].id).locator('summary').click();
    assert.deepEqual(await card(chats[0].id).locator('[role=menuitem]').allTextContents(),['打开','信息','重命名','移入回收站']);
    await frame.locator('.projects-library').screenshot({path:'work/project-types-conversations.png',animations:'disabled'});
    await card(chats[0].id).locator('[data-project-action=info]').click();
    await frame.waitForSelector('.canvas-info');assert.match(await frame.locator('.canvas-info').innerText(),/对话信息.*图片对话/s);await frame.locator('#modalClose').click();
    await menu(chats[0].id,'rename');await frame.locator('#projectLibraryName').fill('改名后的图片对话');await frame.locator('#projectNameForm [type=submit]').click();
    await frame.waitForFunction(id=>document.querySelector(`[data-project-card="${id}"] h2`)?.textContent==='改名后的图片对话',chats[0].id);
    await frame.locator('#projectSearch').fill('改名后的');assert.equal(await frame.locator('[data-project-card]').count(),1);await frame.locator('#projectSearch').fill('');
    await menu(chats[0].id,'open');await frame.waitForFunction(id=>new URL(location.href).searchParams.get('conversation')===id&&document.querySelector('#agentView').dataset.homeMode==='image',chats[0].id);
    await openProjects();assert.equal(await tab('conversation').getAttribute('aria-selected'),'true');
    await menu(chats[0].id,'delete');await frame.locator('#cancelConversationTrash').click();assert.equal(await card(chats[0].id).count(),1);
    await menu(chats[0].id,'delete');await frame.locator('#confirmConversationTrash').click();await frame.waitForSelector(`[data-project-card="${chats[0].id}"]`,{state:'detached'});
    assert.equal((await api(frame,'/api/conversations?trashed=true')).data.length,1);
    await frame.locator('#projectTrashBtn').click();await frame.waitForSelector('.projects-library[aria-busy=false]');assert.equal(await card(chats[0].id).count(),1);
    await tab('canvas').click();assert.equal(await card(chats[0].id).count(),0);await tab('conversation').click();
    await card(chats[0].id).locator('.projects-restore').click();await frame.waitForSelector(`[data-project-card="${chats[0].id}"]`,{state:'detached'});
    await frame.locator('#projectTrashBtn').click();await frame.waitForSelector('.projects-library[aria-busy=false]');assert.equal(await card(chats[0].id).count(),1);
    assert.equal(await card(chats[0].id).locator('h2').innerText(),'改名后的图片对话');
    await tab('conversation').press('ArrowRight');assert.equal(await tab('canvas').getAttribute('aria-selected'),'true');
    await menu(standalone.project_id,'open');await frame.waitForFunction(id=>document.querySelector('#agentView').dataset.conversationId===id,standalone.project_id);
    await openProjects();await tab('conversation').click();
    for(const width of [900,430]){await page.locator('#studio').evaluate((el,width)=>{el.style.width=width+'px';},width);assert.equal(await frame.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);await frame.locator('#libraryView').screenshot({path:`work/project-types-${width}.png`,animations:'disabled'});}
    assert.deepEqual(errors,[]);
    console.log('PASS project tabs, exclusive Agent grouping, square covers, four conversation actions, search, rename, open, cancel trash, recycle, restore, keyboard and responsive layouts');
  }finally{await browser?.close();server.stdin.end('\n');}
})().catch(error=>{console.error(error);process.exitCode=1;});
