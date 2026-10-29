// Full writing workspace in Chromium with real auth/routes and a fake text provider.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture,api}=require('./market_login.browser.cjs');
async function slideTo(frame,groupSelector,buttonSelector,motion=true){
  const result=await frame.evaluate(async({groupSelector,buttonSelector})=>{
    await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
    const group=document.querySelector(groupSelector),marker=group.querySelector('.selection-indicator');
    const before=marker.style.transform;
    document.querySelector(buttonSelector).click();
    const same=marker===group.querySelector('.selection-indicator');
    void getComputedStyle(marker).transform;
    const animations=marker.getAnimations(),animated=animations.some(animation=>animation.playState==='running');
    await Promise.all(animations.map(animation=>animation.finished.catch(()=>{})));
    const selected=group.querySelector('[aria-selected=true],[aria-pressed=true],:scope > [aria-current=true]');
    const target=selected.getBoundingClientRect(),rect=marker.getBoundingClientRect();
    return {same,animated,moved:before!==marker.style.transform,aligned:Math.abs(rect.x-target.x)<1&&Math.abs(rect.y-target.y)<1&&Math.abs(rect.width-target.width)<1};
  },{groupSelector,buttonSelector});
  assert.equal(result.same,true,'Selection marker must survive navigation');
  assert.equal(result.moved,true,'Selection marker must move to the chosen option');
  assert.equal(result.animated,motion,'Motion must respect the reduced-motion preference');
  assert.equal(result.aligned,true,'Selection marker must finish directly behind the active button');
}
const setup=String.raw`
    sys.path.insert(0, str(Path.cwd() / 'tests'))
    from studio_fixtures import reply
    def delayed_reply(config, messages):
        context = json.loads(messages[-1]['content'])
        if '最新消息' in messages[0]['content'] and context.get('latest_message') == '下午好':
            value = json.dumps({'intent': 'chat', 'reply': '下午好！'}, ensure_ascii=False) if 'repair' in context else '下午好！'
            return {'choices': [{'message': {'content': value}}]}
        if '只输出故事设定' in messages[0]['content']:
            import time
            time.sleep(6)
        if '统一美术方向' in messages[0]['content'] and 'repair' not in context:
            return {'choices': [{'message': {'content': json.dumps({'style_bible': '写实电影、自然光、稳定的角色身份。' * 100})}}]}
        if '最新消息' in messages[0]['content'] and context.get('latest_message') == '李白是谁？':
            import time
            time.sleep(6)
            value = {'intent': 'chat', 'reply': '李白是唐代诗人，字太白，号青莲居士，被后人称为“诗仙”。\n\n他的诗想象丰富、气势奔放，《静夜思》《将进酒》都是广为流传的作品。', 'action': '', 'episode': None}
            return {'choices': [{'message': {'content': json.dumps(value, ensure_ascii=False)}}]}
        if '离开页面恢复测试' in messages[-1]['content'] and '最新消息' in messages[0]['content']:
            import time
            time.sleep(3)
        return reply(config, messages)
    app.llm_json = delayed_reply
    app.start_generation_worker = lambda *args: None
    def no_network(*args, **kwargs):
        raise AssertionError('Browser fixture must not call real models')
    app.provider_json = no_network
`;
(async()=>{
  const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','pipe']});
  let browser;
  try{
    const info=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(new Error('Fixture timeout')),30000);server.stdout.on('data',b=>{output+=b;const m=output.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});server.stderr.on('data',b=>process.stderr.write(b));server.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exit '+code));});});
    browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
    const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(info.parent+'/?user=studio-writer');
    await page.locator('#studio').evaluate(el=>{el.style.width='1560px';el.style.height='960px';});
    const frame=await(await page.waitForSelector('#studio')).contentFrame();
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    await frame.locator('#mainNav [data-view=home]').click();
    await frame.locator('#homeTab-agent').click();
    const intent=frame.locator('.agent-intent-control .home-preference-trigger');
    await intent.click();
    await slideTo(frame,'.home-preference-panel:popover-open .home-preference-options','.home-preference-panel:popover-open [data-value=script]');
    assert.equal(await frame.locator('.agent-intent-select').inputValue(),'script');
    await slideTo(frame,'.home-preference-panel:popover-open .home-preference-options','.home-preference-panel:popover-open [data-value=auto]');
    await frame.locator('.home-preference-panel:popover-open').screenshot({path:'work/agent-studio-intent-menu.png',animations:'disabled'});
    await page.keyboard.press('Escape');
    await frame.locator('.home-agent-parameters:not(.agent-intent-control) .home-preference-trigger').click();
    await slideTo(frame,'.home-preference-panel:popover-open .ratios','.home-preference-panel:popover-open [data-value="9:16"]');
    await slideTo(frame,'.home-preference-panel:popover-open .ratios','.home-preference-panel:popover-open [data-value="16:9"]');
    await page.keyboard.press('Escape');
    await frame.locator('[data-mention-editor=agentInput]').fill('一句话创建 2 集短剧：小林回家送信');
    await frame.locator('#agentComposer .agent-send').click();
    await frame.waitForSelector('.agent-studio-workspace:not([hidden])');
    await frame.waitForSelector('.studio-empty');
    assert.equal(await frame.locator('.studio-empty').innerText(),'正在把灵感写成故事');
    assert.equal(await frame.locator('.studio-empty > .ui-icon').evaluate(el=>getComputedStyle(el).width),'56px');
    assert.equal(await frame.locator('#agentView > .agent-topbar').isVisible(),false);
    assert.equal(await frame.locator('.studio-document').evaluate(el=>getComputedStyle(el).fontSize),'16px');
    assert.equal(await frame.locator('.studio-section-label').first().evaluate(el=>getComputedStyle(el).fontSize),'13px');
    await frame.locator('.agent-studio-workspace').screenshot({path:'work/agent-studio-writing-empty.png',animations:'disabled'});
    await frame.waitForFunction(()=>document.querySelector('#agentView')?.dataset.status==='completed'&&document.querySelectorAll('[data-studio-doc]').length===4,{},{timeout:30000});
    const runs=(await api(frame,'/api/agent/runs')).data,run=(await api(frame,'/api/agent/runs/'+runs[0].id)).data.run;
    assert.equal(run.status,'completed',run.error);
    assert.ok(run.studio.canvas_project_id);
    assert.equal(await frame.locator('.agent-assistant-orb').isVisible(),false);
    const details=frame.locator('.agent-process-details').first();
    assert.ok(await details.count());assert.equal(await details.getAttribute('open'),null);
    assert.equal(await frame.locator('#agentMessages > .agent-step:not(.agent-thinking)').count(),0);
    assert.equal(await frame.locator('.agent-process-details .agent-step:visible').count(),0);
    assert.match(await details.locator('summary').innerText(),/处理详情.*已完成/s);
    assert.ok(await frame.locator('.agent-process-details .agent-process-note').count());
    await details.locator('summary').click();assert.equal(await details.locator('.agent-step').first().isVisible(),true);
    assert.ok(await details.locator('.agent-process-events').evaluate(el=>el.clientHeight<=280));
    await details.locator('summary').press('Enter');assert.equal(await details.getAttribute('open'),null);
    assert.equal(await frame.evaluate(()=>{const root=document.querySelector('#agentView').getBoundingClientRect(),feed=document.querySelector('#agentScroll').getBoundingClientRect();return Math.abs(root.top-feed.top)<1;}),true,'Chat must use the removed heading space');
    // A mounted writing workspace must yield to every sidebar destination.
    for(const destination of ['assets','models','projects','assets','models']){
      await frame.locator(`#mainNav [data-view=${destination}]`).click();
      assert.equal(await frame.locator('#agentView').isVisible(),false,`Writing workspace leaked into ${destination}`);
      assert.equal(await frame.locator('.studio-chat-resizer').isVisible(),false,`Writing divider leaked into ${destination}`);
      assert.equal(await frame.locator(destination==='models'?'#modelsView':'#libraryView').isVisible(),true);
      assert.equal(await frame.locator('.view:visible').count(),1);
      await frame.locator('.active-view').screenshot({path:`work/studio-navigation-${destination}.png`,animations:'disabled'});
      await frame.locator(`.recent-open[data-conversation="${run.project_id}"]`).click();
      await frame.waitForSelector('#agentView.active-view.studio-active');
      assert.equal(await frame.locator('.agent-studio-workspace').isVisible(),true);
    }
    // Media, plain chat and writing share a root, but must show only their own content.
    const mediaConversations=[];
    for(const kind of ['image','video','audio']){
      const response=await api(frame,'/api/conversations',{kind,title:'切换验证 '+kind,prompt:'独立的 '+kind+' 对话',request_id:crypto.randomUUID()});
      assert.equal(response.status,201);mediaConversations.push(response.data);
      if(kind==='image'){
        const generated=await api(frame,'/api/images/generations',{project_id:response.data.id,prompt:'只属于图片对话的内容',request_id:crypto.randomUUID()});
        assert.equal(generated.status,202);
      }
    }
    const question='普通问答切换验证';
    const chatRun=(await api(frame,'/api/agent/runs',{text:question,request_id:crypto.randomUUID(),workflow:'studio'})).data.run;
    for(let attempt=0;attempt<40;attempt++){
      if((await api(frame,'/api/agent/runs/'+chatRun.id)).data.run.status==='completed')break;
      await new Promise(resolve=>setTimeout(resolve,250));
    }
    await frame.goto(info.app+'/market?conversation='+run.project_id);
    await frame.waitForSelector('#agentView.active-view.studio-active');
    for(const conversation of [...mediaConversations,{id:chatRun.project_id,kind:'agent'},...mediaConversations].reverse()){
      await frame.locator(`.recent-open[data-conversation="${conversation.id}"]`).click();
      await frame.waitForFunction(({id,kind})=>new URL(location.href).searchParams.get('conversation')===id&&document.querySelector('#agentView').dataset.homeMode===kind,conversation);
      assert.equal(await frame.locator('.agent-studio-workspace').isVisible(),false,`Previous script leaked into ${conversation.kind}`);
      assert.equal(await frame.locator('.studio-chat-resizer').isVisible(),false);
      assert.equal(await frame.locator('#agentView > .agent-topbar').isVisible(),false,`No assistant heading in ${conversation.kind} conversations`);
      if(conversation.kind==='agent'){
        assert.match(await frame.locator('#agentMessages').innerText(),new RegExp(question));
        assert.equal(await frame.locator('#agentView').getAttribute('data-conversation-id'),chatRun.project_id);
      }else{
        assert.equal(await frame.locator('#homeMediaComposer').isVisible(),true);
        assert.equal(await frame.locator('#agentMessages').isVisible(),false);
        if(conversation.kind==='image')assert.match(await frame.locator('.home-results').innerText(),/只属于图片对话的内容/);
        assert.equal(await frame.locator('#agentView').evaluate(el=>el.clientHeight<=innerHeight+1),true);
      }
      await frame.locator('#agentView').screenshot({path:`work/studio-conversation-${conversation.kind}.png`,animations:'disabled'});
      await frame.locator(`.recent-open[data-conversation="${run.project_id}"]`).click();
      await frame.waitForFunction(id=>new URL(location.href).searchParams.get('conversation')===id&&document.querySelector('#agentView').dataset.homeMode==='agent'&&document.querySelector('#agentView').dataset.conversationId===id,run.project_id);
      assert.equal(await frame.locator('.agent-studio-workspace').isVisible(),true);
    }
    // A reply sent before navigation cannot replace the newly selected chat.
    let releaseReply,replyEntered;
    const replyGate=new Promise(resolve=>{releaseReply=resolve;});
    const enteredReply=new Promise(resolve=>{replyEntered=resolve;});
    const replyPath='**/api/agent/runs/'+run.id+'/messages';
    await page.route(replyPath,async route=>{
      const target=new URL(route.request().url());const host=target.host;target.hostname='127.0.0.1';
      const response=await route.fetch({url:target.href,headers:{...route.request().headers(),host}});replyEntered();await replyGate;await route.fulfill({response});
    });
    await frame.locator('[data-mention-editor=agentInput]').fill('晚返回切换验证');
    await frame.locator('#agentComposer .agent-send').click();await enteredReply;
    await frame.locator(`.recent-open[data-conversation="${chatRun.project_id}"]`).click();
    await frame.waitForFunction(id=>document.querySelector('#agentView').dataset.conversationId===id,chatRun.project_id);
    const lateResponse=page.waitForResponse(response=>response.url().endsWith('/api/agent/runs/'+run.id+'/messages')&&response.request().method()==='POST');
    releaseReply();await lateResponse;
    await frame.waitForFunction(()=>!document.querySelector('#agentInput').disabled);
    await frame.locator('[data-mention-editor=agentInput]').fill('新对话草稿');
    assert.equal(await frame.locator('#agentView').getAttribute('data-conversation-id'),chatRun.project_id);
    assert.equal(await frame.locator('.agent-studio-workspace').isVisible(),false);
    assert.match(await frame.locator('#agentMessages').innerText(),new RegExp(question));
    await page.unroute(replyPath);
    await frame.locator(`.recent-open[data-conversation="${run.project_id}"]`).click();
    await frame.waitForFunction(id=>document.querySelector('#agentView').dataset.conversationId===id,run.project_id);
    const styleSteps=frame.locator('.agent-step').filter({hasText:'统一角色与场景的美术方向'});
    assert.equal(await styleSteps.count(),1,'A repaired response must update the existing stage');
    assert.match(await styleSteps.textContent(),/已完成/);
    assert.equal(await styleSteps.isVisible(),false);
    assert.equal(await frame.locator('.agent-issue').count(),0);
    assert.equal(await frame.locator('[data-studio-preferences],.studio-directory-foot').count(),0);
    assert.equal(await frame.locator('#agentComposer [data-command=skills]').isVisible(),false);
    // Hover, selection and keyboard focus never paint a tab background.
    for(const state of ['hover','focus']){
      const tab=frame.locator('[data-studio-tab=bible]');
      await tab[state]();
      const style=await tab.evaluate(el=>{
        const css=getComputedStyle(el),box=el.getBoundingClientRect(),range=document.createRange();
        range.selectNodeContents(el);const text=range.getBoundingClientRect();
        return {background:css.backgroundColor,centered:Math.abs((box.left+box.right-text.left-text.right)/2)<1};
      });
      assert.equal(style.background,'rgba(0, 0, 0, 0)');
      assert.equal(style.centered,true,'Script tab labels must be centered inside their boxes');
    }
    await slideTo(frame,'.studio-document-tabs','[data-studio-tab=outline]');
    await slideTo(frame,'.studio-document-list','[data-studio-doc=episode-2]');
    await slideTo(frame,'.studio-directory','.studio-directory [data-studio-page=production]');
    await slideTo(frame,'.studio-directory','.studio-directory [data-studio-page=script]');
    await page.emulateMedia({reducedMotion:'reduce'});
    await slideTo(frame,'.studio-document-tabs','[data-studio-tab=bible]',false);
    await page.emulateMedia({reducedMotion:'no-preference'});
    const draft=frame.locator('[data-mention-editor=agentInput]');
    await draft.fill('拖动后保留的创作要求');
    const handle=frame.locator('.studio-chat-resizer'),bounds=await handle.boundingBox();
    const initialWidth=Number(await handle.getAttribute('aria-valuenow'));
    await page.mouse.move(bounds.x+bounds.width/2,bounds.y+100);await page.mouse.down();
    await page.mouse.move(bounds.x+bounds.width/2-120,bounds.y+100,{steps:8});await page.mouse.up();
    const resizedWidth=Number(await handle.getAttribute('aria-valuenow'));
    assert.equal(resizedWidth,initialWidth+120);
    assert.match(await draft.innerText(),/拖动后保留的创作要求/);
    assert.equal(await frame.locator('body').evaluate(el=>el.classList.contains('studio-chat-resizing')),false);
    await handle.press('ArrowRight');assert.equal(Number(await handle.getAttribute('aria-valuenow')),resizedWidth-24);
    await handle.press('ArrowLeft');assert.equal(Number(await handle.getAttribute('aria-valuenow')),resizedWidth);
    await draft.fill('');
    await frame.locator('[data-studio-doc=episode-1]').click();
    assert.match(await frame.locator('.studio-document').innerText(),/场1-1/);
    await frame.locator('#agentView').screenshot({path:'work/agent-studio-script.png',animations:'disabled'});
    const geometry=await frame.evaluate(()=>{const r=s=>{const b=document.querySelector(s).getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom};};return {work:r('.agent-studio-workspace'),chat:r('#agentScroll'),compose:r('.agent-composer-wrap'),root:r('#agentView'),bodyWidth:document.body.scrollWidth,width:innerWidth};});
    assert.ok(geometry.work.right<=geometry.chat.left+1,JSON.stringify(geometry));
    assert.ok(Math.abs(geometry.work.top-geometry.root.top)<1,'The removed top bar must not leave an empty row');
    assert.ok(geometry.compose.bottom<=geometry.root.bottom+1,JSON.stringify(geometry));
    assert.ok(geometry.bodyWidth<=geometry.width+1,JSON.stringify(geometry));
    assert.equal(await frame.locator('.studio-episode-select').count(),0);
    await frame.locator('[data-studio-doc=episode-2]').click();
    assert.match(await frame.locator('.studio-document').innerText(),/场2-1/);
    // Narrow layouts keep the selected draft and production reachable.
    await page.locator('#studio').evaluate(el=>{el.style.width='900px';});
    await frame.locator('.studio-mobile-stage').click();
    assert.equal(await frame.locator('.studio-production-pane [data-board-category]').count(),9);
    assert.equal(await frame.evaluate(()=>{const a=document.querySelector('.studio-view-switch').getBoundingClientRect(),b=document.querySelector('.studio-mobile-stage').getBoundingClientRect();return a.bottom<=b.top||a.right<=b.left||b.right<=a.left;}),true,'Narrow view switch must not overlap the script navigation');
    await frame.locator('#agentView').screenshot({path:'work/agent-studio-production-narrow.png',animations:'disabled'});
    await frame.locator('.studio-mobile-stage').click();
    await page.locator('#studio').evaluate(el=>{el.style.width='600px';});
    assert.equal(await frame.locator('.studio-chat-resizer').isVisible(),false);
    assert.equal(await frame.evaluate(()=>document.body.scrollWidth<=innerWidth+1),true);
    await frame.locator('#agentView').screenshot({path:'work/agent-studio-narrow.png',animations:'disabled'});
    await page.locator('#studio').evaluate(el=>{el.style.width='1560px';});
    await frame.locator('.studio-directory [data-studio-page=production]').click();
    assert.equal(await frame.locator('.studio-production-pane [data-board-category]').count(),9);
    await frame.waitForSelector('.studio-production-pane .board-card');
    assert.equal(await frame.locator('.studio-production-intro').count(),0);
    const board=frame.locator('.studio-production-pane');
    const switchPosition=await frame.evaluate(()=>{
      const head=document.querySelector('.studio-editor-head').getBoundingClientRect(),toggle=document.querySelector('.studio-view-switch').getBoundingClientRect();
      return {centered:Math.abs(toggle.x+toggle.width/2-head.x-head.width/2)<1,top:toggle.top};
    });
    assert.equal(switchPosition.centered,true,'Storyboard switch must be centered in the top chrome');
    assert.ok(switchPosition.top<=12,'Storyboard switch must sit at the page top');
    await board.evaluate(el=>{el.scrollTop=200;});
    assert.equal(await frame.locator('.studio-view-switch').evaluate(el=>el.getBoundingClientRect().top),switchPosition.top,'Scrolling cards must not move the switch');
    await board.evaluate(el=>{el.scrollTop=0;});
    await frame.locator('.storyboard-tabs [data-board-category=all]').hover();
    const tinted=await frame.evaluate(()=>{
      const selectors=['.studio-view-switch','.studio-view-switch button','.studio-view-switch .selection-indicator','.studio-directory button','.studio-project-title svg','.studio-canvas-link','.studio-canvas-link svg','.agent-user-bubble','.storyboard-tabs button','.storyboard-actions button','.storyboard-status','.agent-artifact svg','.agent-composer-wrap','.agent-send'];
      const failures=[];
      for(const selector of selectors)for(const el of document.querySelectorAll(selector)){
        if(!el.getClientRects().length)continue;
        const style=getComputedStyle(el);
        for(const key of ['color','backgroundColor','borderTopColor']){
          const rgb=style[key].match(/[\d.]+/g)?.map(Number)||[];
          if(rgb.length>=3&&(rgb[0]!==rgb[1]||rgb[1]!==rgb[2]))failures.push(selector+': '+key+'='+style[key]);
        }
      }
      return failures;
    });
    assert.deepEqual(tinted,[],'Studio controls must use neutral greys');

    const initialCards=await board.locator('.board-card').count();assert.ok(initialCards>=10);
    await slideTo(frame,'.studio-production-pane .storyboard-tabs','.studio-production-pane [data-board-category=character]');
    assert.equal(await board.locator('.board-card').count(),1);
    await board.locator('[data-board-category=scene]').click();assert.equal(await board.locator('.board-card').count(),1);
    await board.locator('[data-board-category=prop]').click();assert.equal(await board.locator('.board-card').count(),1);
    await board.locator('[data-board-category=audio]').click();assert.equal(await board.locator('.board-card').count(),0);assert.equal(await board.locator('.board-empty').isVisible(),true);
    await board.locator('[data-board-category=video]').click();assert.equal(await board.locator('.board-card').count(),4);
    await board.locator('[data-board-category=video]').press('ArrowLeft');assert.equal(await board.locator('[data-board-category=prop]').getAttribute('aria-selected'),'true');
    await board.locator('[data-board-category=all]').click();assert.equal(await board.locator('.board-card').count(),initialCards);
    await frame.locator('#agentView').screenshot({path:'work/agent-studio-production.png',animations:'disabled'});
    await draft.fill('切换故事板和画布时保留这条草稿');
    await frame.locator('.studio-editor-head [data-view-mode=workflow]').click();
    await frame.waitForSelector('#canvasView.active-view');
    assert.equal(await frame.locator('#agentView').isVisible(),false,'Writing workspace must not overlay the canvas');
    assert.equal(await frame.locator('.studio-chat-resizer').isVisible(),false,'No writing divider on the canvas');
    assert.equal(await frame.locator('.node[data-node-type=timeline]').count(),3);
    assert.equal(await frame.locator('.workspace-modes .selection-indicator').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(57, 57, 57)');
    await frame.locator('#storyboardMode').click();
    await frame.waitForSelector('#agentView.active-view .studio-production-pane:not([hidden])');
    assert.equal(await draft.innerText(),'切换故事板和画布时保留这条草稿');
    await frame.waitForSelector('.studio-production-pane .board-card');
    const cardNode=await board.locator('[data-board-node]').first().getAttribute('data-board-node');
    await board.locator('[data-board-node]').first().click();
    await frame.waitForSelector('#canvasView.active-view');
    assert.equal(await frame.locator(`.node[data-node="${cardNode}"]`).evaluate(el=>el.classList.contains('selected')),true);
    const graph=(await api(frame,'/api/projects/'+run.studio.canvas_project_id)).data;
    assert.equal(graph.nodes.some(node=>['firstFrame','lastFrame'].includes(node.mujian?.role)),false,'Agent canvas must use direct asset references without frame intermediates');
    const nodeIndex=new Map(graph.nodes.map(node=>[node.id,node]));
    for(const video of graph.nodes.filter(node=>node.type==='video')){
      assert.equal(video.mujian.mode,'reference');
      assert.equal(video.mujian.inputs.length,3);
      assert.ok(video.mujian.inputs.every(ref=>nodeIndex.get(ref.nodeId)?.mujian?.role==='reference'));
      assert.equal(video.settings.mentions.length,3);
    }
    const optimized=(await api(frame,'/api/agent/runs/'+run.id)).data.run.studio.prompt_optimization_progress;
    assert.equal(optimized.current,7);
    assert.equal(optimized.total,7);
    assert.equal(optimized.batch_size,6);
    assert.ok(optimized.batch_total<optimized.total,'Several node prompts must share each provider request');
    assert.deepEqual(graph.groups.map(group=>group.title).sort(),['人物 · 小林','道具参考','场景 · 小镇车站','第 1 集 · 视频片段','第 2 集 · 视频片段'].sort());
    for(const node of graph.nodes.filter(node=>node.type==='video'))assert.equal(await frame.locator(`.links [data-edge-to="${node.id}"]`).count(),0);
    const timeline=graph.nodes.find(n=>n.type==='timeline');
    await frame.locator('#canvas').focus();await page.keyboard.press('Control+0');
    await frame.locator(`.node[data-node="${timeline.id}"]`).click({force:true});
    await frame.waitForSelector('.studio-timeline-detail');
    await frame.locator('.studio-timeline-detail [data-edit-timeline]').click();
    assert.equal(await frame.locator('.studio-timeline-detail [data-render]').isDisabled(),true);
    // Returning through the persisted conversation restores the writing workspace.
    await frame.goto(info.app+'/market?conversation='+run.project_id);
    await frame.waitForSelector('.agent-studio-workspace:not([hidden])');
    assert.equal(Number(await frame.locator('.studio-chat-resizer').getAttribute('aria-valuenow')),resizedWidth);
    await frame.locator('[data-studio-doc=episode-1]').click();
    const originalScript=await frame.locator('.studio-document').innerText();
    await frame.locator('.studio-document-scroll').evaluate(el=>{el.scrollTop=el.scrollHeight;});
    await frame.locator('[data-studio-edit]').click();
    const editor=frame.locator('.studio-manual-editor');
    const originalDraft=await editor.inputValue();
    const start=await editor.evaluate(el=>({cursor:el.selectionStart,scroll:el.closest('.studio-document-scroll').scrollTop,height:el.clientHeight,content:el.scrollHeight}));
    assert.equal(start.cursor,0);assert.equal(start.scroll,0);
    assert.ok(start.height>=start.content-1&&start.height>400,'The editor must expand to the entire draft');
    assert.equal(await frame.locator('[data-studio-edit]').isDisabled(),true);
    assert.equal(await frame.locator('.studio-episode-select').count(),0);
    const longDraft=originalDraft+'\n'+('△小林沿着街道走向远处，回头望了一眼熟悉的车站。\n'.repeat(70));
    await editor.fill(longDraft);
    assert.equal(await editor.evaluate(el=>el.clientHeight>=el.scrollHeight-1),true,'Pasting a long draft must expand the editor');
    await frame.locator('.studio-chat-resizer').press('ArrowLeft');
    await frame.waitForFunction(()=>{const el=document.querySelector('.studio-manual-editor');return el.clientHeight>=el.scrollHeight-1;});
    assert.equal(await editor.inputValue(),longDraft);
    await frame.locator('.studio-chat-resizer').press('ArrowRight');
    await frame.locator('.studio-document-scroll').evaluate(el=>{el.scrollTop=el.scrollHeight;});
    const saveVisible=await frame.locator('[data-studio-edit-save]').evaluate(el=>{const box=el.getBoundingClientRect(),pane=el.closest('.studio-document-scroll').getBoundingClientRect();return box.top>=pane.top&&box.bottom<=pane.bottom;});
    assert.equal(saveVisible,true,'Save must remain visible while scrolling the full draft');
    await frame.locator('[data-studio-edit-cancel]').click();
    assert.equal(await frame.locator('.studio-document').innerText(),originalScript);
    await frame.locator('[data-studio-edit]').click();
    assert.equal(await editor.inputValue(),originalDraft);
    await frame.locator('#agentView').screenshot({path:'work/agent-studio-full-editor.png',animations:'disabled'});
    await editor.fill((await editor.inputValue())+'\n△小林回头望向车站。');
    await frame.locator('[data-studio-tab=outline]').press('ArrowRight');
    assert.match(await editor.inputValue(),/回头望向车站/);
    assert.equal(await frame.locator('[data-studio-tab=episodes]').getAttribute('aria-selected'),'true');
    await frame.locator('[data-studio-edit-save]').click();
    await frame.waitForSelector('.studio-manual-editor',{state:'detached'});
    assert.match(await frame.locator('.studio-document').innerText(),/回头望向车站/);
    await frame.locator('[data-studio-versions]').click();
    await frame.waitForSelector('.studio-versions[open]');
    assert.equal(await frame.locator('.studio-versions select option').count(),2);
    await frame.locator('.studio-versions [data-close]').click();
    // An active background run survives navigating away and a fresh page load.
    const pending=(await api(frame,'/api/agent/runs',{text:'离开页面恢复测试：创建 2 集短剧',request_id:crypto.randomUUID(),workflow:'studio'})).data.run;
    assert.equal(pending.status,'running');
    await frame.goto(info.app+'/market?view=home');
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    await frame.goto(info.app+'/market?conversation='+pending.project_id);
    try{await frame.waitForFunction(()=>document.querySelector('#agentView')?.dataset.status==='completed'&&!!document.querySelector('.agent-studio-workspace:not([hidden])'),{},{timeout:30000});}
    catch(error){const current=(await api(frame,'/api/agent/runs/'+pending.id)).data.run;throw new Error('Recovery failed: '+JSON.stringify({status:current.status,error:current.error,stage:current.studio.stage,browserErrors:errors}));}
    const recovered=(await api(frame,'/api/agent/runs/'+pending.id)).data.run;
    assert.equal(recovered.studio.documents['episode-2'].version,1);
    // Q&A in the writing workspace uses the supplied ring and leaves the script intact.
    await frame.locator('[data-mention-editor=agentInput]').fill('李白是谁？');
    await frame.locator('#agentComposer .agent-send').click();
    await frame.waitForSelector('.agent-thinking');
    assert.equal(await frame.locator('.agent-thinking').innerText(),'思考中...');
    await frame.waitForFunction(()=>{const img=document.querySelector('.agent-thinking img');return img?.complete&&img.naturalWidth===128;});
    assert.match(await frame.locator('.agent-thinking img').getAttribute('src'),/agent-breathing-ring\.svg$/);
    assert.equal(await frame.locator('.agent-thinking summary,.agent-thinking .agent-step-state').count(),0);
    await frame.locator('.agent-thinking').screenshot({path:'work/agent-thinking-ring.png'});
    await frame.locator('#agentView').screenshot({path:'work/agent-studio-thinking.png',animations:'disabled'});
    await frame.waitForFunction(()=>document.querySelector('#agentView')?.dataset.status==='completed'&&document.querySelector('#agentMessages')?.textContent.includes('李白是唐代诗人'),{},{timeout:15000});
    assert.equal(await frame.locator('.agent-thinking').count(),0);
    assert.doesNotMatch(await frame.locator('#agentMessages').innerText(),/validation error|errors\.pydantic\.dev|理解你的创作需求|记录时间/);
    assert.deepEqual((await api(frame,'/api/agent/runs/'+pending.id)).data.run.studio.documents,recovered.studio.documents);
    // A received plain-text greeting is repaired without exposing a JSON error.
    await frame.locator('[data-mention-editor=agentInput]').fill('下午好');
    await frame.locator('#agentComposer .agent-send').click();
    await frame.waitForFunction(()=>document.querySelector('#agentView')?.dataset.status==='completed'&&document.querySelector('#agentMessages')?.textContent.includes('下午好！'),{},{timeout:15000});
    assert.equal(await frame.locator('.agent-issue').count(),0);
    assert.deepEqual((await api(frame,'/api/agent/runs/'+pending.id)).data.run.studio.documents,recovered.studio.documents);
    await frame.locator('#agentView').screenshot({path:'work/agent-studio-conversation.png',animations:'disabled'});
    // A second market account cannot inspect this run or edit its drafts.
    const other=await browser.newPage();await other.goto(info.parent+'/?user=studio-other');
    const otherFrame=await(await other.waitForSelector('#studio')).contentFrame();
    await otherFrame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    assert.equal((await api(otherFrame,'/api/agent/runs/'+run.id)).status,404);
    // Switching from a loaded script to an empty canvas must leave no divider.
    const empty=(await api(frame,'/api/projects',{name:'空画布导航验证',nodes:[],edges:[],viewport:{x:0,y:0,zoom:.06}})).data;
    await frame.goto(info.app+'/market?conversation='+run.project_id);
    await frame.waitForSelector('#agentView.active-view.studio-active');
    await frame.locator(`.recent-open[data-project="${empty.id}"]`).click();
    await frame.waitForSelector('#canvasView.active-view');
    assert.equal(await frame.locator('#agentView').isVisible(),false);
    assert.equal(await frame.locator('.studio-chat-resizer').isVisible(),false);
    assert.equal(await frame.locator('#canvasStage .node').count(),0);
    assert.equal(await frame.locator('.view:visible').count(),1);
    await frame.locator('#canvasView').screenshot({path:'work/studio-navigation-empty-canvas.png',animations:'disabled'});
    // Existing untouched Agent groups compact on open and use translucent rounded frames.
    const groupedNodes=Array.from({length:9},(_,i)=>({id:'compact-'+i,type:'video',title:'片段 '+(i+1),x:104+i%4*460,y:134+Math.floor(i/4)*360,
      settings:{prompt:'雨后街道',aspect_ratio:'16:9'},mujian:{runId:run.id,role:'video',inputs:[]}}));
    const compact=(await api(frame,'/api/projects',{name:'柔和分组与紧凑布局',nodes:groupedNodes,edges:[],groups:[{id:'compact-group',title:'第 1 集 · 视频片段',color:'#9061f9',x:80,y:80,width:1864,height:1134,node_ids:groupedNodes.map(n=>n.id)}],viewport:{x:0,y:0,zoom:.65}})).data;
    await frame.goto(info.app+'/market?project='+compact.id);
    await frame.waitForSelector('.canvas-group');
    const groupStyle=await frame.locator('.canvas-group').evaluate(el=>{const s=getComputedStyle(el);return {radius:s.borderRadius,background:s.backgroundColor};});
    assert.equal(groupStyle.radius,'24px');
    assert.match(groupStyle.background,/0\.08\)/);
    await frame.waitForFunction(()=>document.querySelector('#saveState')?.textContent==='已保存');
    const compactSaved=(await api(frame,'/api/projects/'+compact.id)).data;
    assert.equal(compactSaved.nodes[1].x-compactSaved.nodes[0].x,368);
    assert.equal(compactSaved.nodes[4].y-compactSaved.nodes[0].y,262);
    await frame.locator('#canvasView').screenshot({path:'work/studio-soft-compact-groups.png',animations:'disabled'});
    // Large canvases retain all records while mounting only nearby nodes.
    const large=(await api(frame,'/api/projects',{name:'大画布验证',nodes:Array.from({length:500},(_,i)=>({id:'large-'+i,type:'prompt',title:'文本 '+i,x:80+i%4*440,y:100+Math.floor(i/4)*360,settings:{prompt:'分镜参考'}})),edges:[],viewport:{x:0,y:0,zoom:1}})).data;
    await frame.goto(info.app+'/market?project='+large.id);
    await frame.waitForSelector('#canvasView.active-view .node');
    assert.ok(await frame.locator('#canvasStage .node').count()<100);
    assert.equal((await api(frame,'/api/projects/'+large.id)).data.nodes.length,500);
    await frame.locator('#canvasView').screenshot({path:'work/agent-studio-large-canvas.png'});
    assert.deepEqual(errors,[]);
    console.log('PASS: collapsed processing details, header-free conversations, readable writing workspace, full-draft editing/resizing/cancel/save, transparent centered tabs, breathing-ring thinking status, Q&A preserves scripts, sliding navigation, draggable chat, writing/canvas/revisions/recovery, account scope, 500-node viewport rendering');
  }finally{await browser?.close();server.stdin.end('\n');}
})().catch(e=>{console.error(e);process.exitCode=1;});
