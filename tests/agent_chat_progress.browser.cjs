// Real chat renderer with deterministic snapshots; no model or media requests.
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:900,height:1000}}),errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('http://localhost/**',async route=>{
      const pathname=new URL(route.request().url()).pathname;
      if(pathname==='/')return route.fulfill({contentType:'text/html',body:'<link rel="stylesheet" href="/agent-chat.css"><link rel="stylesheet" href="/request-button.css"><style>:root{--bg:#181818;--panel:#222;--panel2:#292929;--line:#333;--text:#eee;--muted:#999}body{margin:0;background:#181818}.agent-view{max-width:700px;margin:auto}.agent-scroll{overflow:auto}</style><main id="agentView" class="agent-view active-view"></main>'});
      const file=path.resolve('public','.'+pathname);
      if(!file.startsWith(path.resolve('public')+path.sep))return route.abort();
      try{await route.fulfill({contentType:pathname.endsWith('.js')?'text/javascript':pathname.endsWith('.css')?'text/css':pathname.endsWith('.svg')?'image/svg+xml':'application/octet-stream',body:await fs.readFile(file)});}catch{await route.fulfill({status:404,body:''});}
    });
    await page.goto('http://localhost/');
    await page.evaluate(async()=>{
      const {createAgentChat}=await import('/agent-chat.js');
      window.toasts=[];window.stopCalls=0;
      window.snapshot={run:{id:'run',project_id:'project',title:'制作成片',creation_type:'conversation',status:'waiting_tool',phase:'studio_media',revision:1,preferences:{}},events:[
        {seq:1,kind:'user',text:'按画布中选择的模型制作成片'},
        {seq:2,kind:'step',step_id:'run:studio:1:route:1',status:'running',text:'理解你的创作需求'},
        {seq:3,kind:'step',step_id:'render',status:'running',text:'准备素材'},
        ...Array.from({length:5},(_,i)=>({seq:i+4,kind:'task',task_id:'task-'+i,status:'running',text:'生成任务已受理'}))]};
      window.chat=createAgentChat({api:async(url,options)=>{
        if(url==='/api/agent/capabilities')return {models:{models:[]},skills:[]};
        if(url==='/api/generation/stop'){
          window.stopCalls++;
          await new Promise(resolve=>window.releaseStop=resolve);
          window.snapshot={...snapshot,run:{...snapshot.run,status:'cancelled',revision:snapshot.run.revision+1}};
          return structuredClone(snapshot);
        }
        if(url.endsWith('/messages')){
          window.actionBody=JSON.parse(options.body);
          await new Promise((resolve,reject)=>{window.releaseAction=resolve;window.rejectAction=reject;});
          return structuredClone(snapshot);
        }
        if(url.startsWith('/api/agent/runs/'))return structuredClone(snapshot);
        throw Error('Unexpected API '+url);
      },json:body=>({method:'POST',body:JSON.stringify(body)}),escapeHtml:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),toast:message=>toasts.push(message)});
      await chat.openRun('run',structuredClone(snapshot));
    });
    const count=()=>page.locator('#agentMessages .agent-loading').count();
    assert.equal(await count(),1);
    assert.equal(await page.locator('#agentMessages > .agent-task').count(),0);
    assert.equal(await page.locator('.agent-process-details .agent-task').count(),5);
    assert.equal(await page.locator('.agent-process-details').getAttribute('open'),null);
    assert.equal(await page.locator('.agent-task:visible').count(),0);
    await page.locator('.agent-process-details summary').click();
    assert.equal(await page.locator('.agent-task:visible').count(),5);
    assert.equal(await count(),1,'Expanded details must use static task marks');
    // Incremental updates keep one indicator and preserve the expanded group.
    await page.evaluate(async()=>{snapshot.run.revision++;snapshot.events.push({seq:9,kind:'task',task_id:'task-0',status:'done'},{seq:10,kind:'step',step_id:'render',status:'done',text:'素材已准备'});await chat.show();});
    await page.waitForFunction(()=>document.querySelector('[data-task-id="task-0"]').dataset.stepStatus==='done');
    assert.equal(await count(),1);assert.equal(await page.locator('.agent-task').count(),5);
    assert.equal(await page.locator('.agent-process-details').getAttribute('open'),'');
    assert.equal(await page.locator('.agent-process-count').innerText(),'2/7');
    await page.locator('.agent-process-details summary').click();
    await page.screenshot({path:'work/agent-single-thinking.png'});
    await page.locator('.agent-send').click();
    await page.waitForFunction(()=>!!window.releaseStop);
    assert.equal(await count(),0,'Stop removes the indicator before its API response');
    await page.evaluate(()=>releaseStop());
    await page.waitForFunction(()=>document.querySelector('#agentView').dataset.status==='cancelled');
    assert.equal(await count(),0);
    // Restored terminal snapshots must not animate stale pending task/step events.
    for(const status of ['cancelled','paused','blocked','waiting_user','completed']){
      await page.evaluate(async status=>{chat.hide();snapshot.run.status=status;snapshot.run.revision++;await chat.openRun('run',structuredClone(snapshot));},status);
      assert.equal(await count(),0,status);
    }
    // A production action shows feedback while its HTTP request is still pending.
    await page.evaluate(()=>{const b=document.createElement('button');b.dataset.action='studio_generate';b.textContent='制作成片';document.querySelector('#agentView').append(b);});
    await page.locator('[data-action=studio_generate]').click();
    await page.waitForFunction(()=>!!window.releaseAction||toasts.length);
    assert.deepEqual(await page.evaluate(()=>toasts),[]);
    assert.equal(await count(),1);assert.equal(await page.locator('.agent-send').getAttribute('data-request-state'),'running');
    await page.locator('.agent-send').click();await page.waitForFunction(()=>stopCalls===2);
    assert.equal(await count(),0);
    await page.evaluate(()=>releaseStop());
    await page.evaluate(()=>releaseAction());
    await page.waitForFunction(()=>document.querySelector('.agent-send').dataset.requestState==='idle');
    assert.equal(await count(),0);
    // A rejected action also clears the local pending indicator.
    await page.evaluate(()=>{releaseAction=null;document.querySelector('[data-action=studio_generate]').disabled=false;});
    await page.locator('[data-action=studio_generate]').click();await page.waitForFunction(()=>!!releaseAction);
    assert.equal(await count(),1);
    await page.evaluate(()=>rejectAction(Error('测试请求失败')));
    await page.waitForFunction(()=>document.querySelector('.agent-send').dataset.requestState==='idle');
    assert.equal(await count(),0);assert.deepEqual(await page.evaluate(()=>toasts),['测试请求失败']);
    // Changing conversations during submission cannot leak a pending indicator.
    await page.evaluate(()=>{releaseAction=null;});await page.locator('[data-action=studio_generate]').click();await page.waitForFunction(()=>!!releaseAction);
    await page.evaluate(()=>chat.newConversation());assert.equal(await count(),0);
    await page.evaluate(()=>releaseAction());assert.equal(await count(),0);
    assert.deepEqual(errors,[]);
    console.log('PASS: one thinking indicator, collapsed task/step details, incremental updates, restored terminal runs, immediate stop, production action submission/error and conversation switching.');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
