const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture,api}=require('./market_login.browser.cjs');
const setup=String.raw`
    app.generation_queue.start = lambda: None
    def no_paid_calls(*args, **kwargs):
        raise AssertionError('Queue UI test must never call a model')
    app.provider_json = no_paid_calls
`;
(async()=>{
  const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','pipe']});let browser;
  try{
    const info=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('fixture timeout')),30000);server.stdout.on('data',b=>{output+=b;const m=output.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});server.stderr.on('data',b=>process.stderr.write(b));server.once('exit',code=>{clearTimeout(timer);reject(Error('fixture exit '+code));});});
    browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
    const page=await browser.newPage({viewport:{width:1600,height:1050}}),errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(info.parent+'/?user=queue-a');await page.locator('#studio').evaluate(el=>{el.style.width='1560px';el.style.height='1000px';});
    const frame=await(await page.waitForSelector('#studio')).contentFrame();await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    const editor=frame.locator('[data-mention-editor=homePrompt]'),send=frame.locator('.home-send');
    let cid;
    for(let i=0;i<5;i++){
      await editor.fill('连续入队测试 '+i);await send.waitFor({state:'visible'});
      await frame.waitForFunction(()=>!document.querySelector('.home-send').disabled);
      await send.click();
      await frame.waitForFunction(n=>document.querySelectorAll('.home-results [data-home-job]').length===n,i+1);
      const active=new URL(frame.url()).searchParams.get('conversation');cid ||= active;assert.equal(active,cid);
    }
    const queue=(await api(frame,'/api/generation-queue')).data;assert.equal(queue.tasks.length,5);assert.ok(queue.tasks.every(task=>task.state==='waiting'));
    assert.match(await frame.locator('.home-results').innerText(),/排队中/);
    await frame.locator('.generation-queue-trigger').click();await frame.waitForFunction(()=>document.querySelectorAll('.generation-queue-content article').length===5);
    assert.match(await frame.locator('.generation-queue-dialog').innerText(),/图片最多同时生成 3 个任务，视频最多 2 个/);
    await frame.locator('.generation-queue-dialog [data-cancel]').first().click();await frame.waitForFunction(()=>document.querySelectorAll('.generation-queue-content article').length===4);
    await frame.locator('.generation-queue-dialog').screenshot({path:'work/generation-queue-panel.png',animations:'disabled'});
    const other=await browser.newPage();await other.goto(info.parent+'/?user=queue-b');const otherFrame=await(await other.waitForSelector('#studio')).contentFrame();await otherFrame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    assert.deepEqual((await api(otherFrame,'/api/generation-queue')).data.tasks,[]);
    assert.equal((await api(otherFrame,'/api/generation-queue/image_task/'+queue.tasks[1].id+'/cancel',{})).status,404);
    assert.deepEqual(errors,[]);
    console.log('PASS: five sequential submissions in one conversation, queue labels, scoped queue dialog, cancellation, second-account isolation, no model requests');
  }finally{await browser?.close();server.stdin.end('\n');}
})().catch(error=>{console.error(error);process.exitCode=1;});
