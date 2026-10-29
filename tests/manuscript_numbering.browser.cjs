// Exercise the actual manuscript UI with an old zero-based draft; no models.
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture}=require('./browser_fixture.cjs');
(async()=>{
  const child=spawn('.venv/Scripts/python.exe',['-u','-c',fixture],{stdio:['pipe','pipe','inherit']});
  let browser;
  try{
    const {origin}=await new Promise((resolve,reject)=>{
      let output='';const timer=setTimeout(()=>reject(Error('Fixture timeout')),30000);
      child.stdout.on('data',b=>{output+=b;const m=output.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});
      child.once('exit',code=>{clearTimeout(timer);reject(Error('Fixture exit '+code));});
    });
    browser=await chromium.launch({channel:'chrome',headless:true});
    const page=await browser.newPage({viewport:{width:1560,height:960}}),errors=[];
    assert.ok((await page.context().request.post(origin+'/api/auth/login',{headers:{Origin:origin},data:{username:'admin',password:'viewport-test'}})).ok());
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/numbering-test',route=>route.fulfill({contentType:'text/html',body:'<link rel="stylesheet" href="/agent-studio.css"><main id="agentView" class="active-view" data-home-mode="agent"><div class="agent-scroll"></div></main>'}));
    await page.goto(origin+'/numbering-test');
    const expected='# 故事设定\n\n## 一、本稿采用\n10集，90—120秒，3号楼。\n\n## 二、一句话故事\n\n## 三、核心主题';
    await page.evaluate(async()=>{
      const {createAgentStudio}=await import('/agent-studio.js?v=manuscript-numbering-1');
      const content='# 故事设定\n\n## 0. 本稿采用\n10集，90—120秒，3号楼。\n\n## 1. 一句话故事\n\n## 2. 核心主题';
      const run={id:'test',revision:1,status:'completed',title:'后浇带',studio:{active:true,brief:{episode_count:1},documents:{bible:{title:'故事设定',content,version:1,status:'current'}}}};
      const esc=s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
      window.testStudio=createAgentStudio({root:document.querySelector('#agentView'),esc,icon:()=>'<span style="display:inline-block;width:16px;height:16px">◇</span>',json:x=>({body:JSON.stringify(x)}),toast:s=>{throw Error(s);},api:async(path,options)=>{
        if(!options)return [{revision:1,title:'旧稿',content}];
        window.savedDraft=JSON.parse(options.body).content;
        run.studio.documents.bible.content=window.savedDraft;run.revision++;return {run};
      },onSnapshot:s=>window.testStudio.update(s.run)});
      window.testStudio.update(run);
    });
    assert.deepEqual(await page.locator('.studio-document h2').allTextContents(),['一、本稿采用','二、一句话故事','三、核心主题']);
    assert.match(await page.locator('.studio-document').innerText(),/10集，90—120秒，3号楼/);
    await page.evaluate(()=>{
      const create=URL.createObjectURL.bind(URL);
      URL.createObjectURL=blob=>{window.downloadedDraft=blob.text();return create(blob);};
    });
    await page.locator('[data-studio-download]').evaluate(el=>el.click());
    assert.equal(await page.evaluate(()=>window.downloadedDraft),expected);
    await page.locator('[data-studio-versions]').click();
    assert.deepEqual(await page.locator('.studio-versions h2').allTextContents(),['一、本稿采用','二、一句话故事','三、核心主题']);
    await page.locator('.studio-versions [data-close]').click();
    await page.locator('[data-studio-edit]').click();
    assert.equal(await page.locator('textarea').inputValue(),expected);
    await page.locator('[data-studio-edit-save]').click();
    await page.waitForFunction(()=>!!window.savedDraft);
    assert.equal(await page.evaluate(()=>window.savedDraft),expected);
    assert.deepEqual(errors,[]);
    console.log('PASS old manuscript Chinese numbering in preview, download, version history, edit and saved content; factual numbers preserved');
  }finally{await browser?.close();child.stdin.end('\n');}
})().catch(error=>{console.error(error);process.exitCode=1;});
