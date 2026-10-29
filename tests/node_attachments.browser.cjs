const assert=require('node:assert/strict');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture}=require('./browser_fixture.cjs');
(async()=>{
 const child=spawn('.venv/Scripts/python.exe',['-u','-c',fixture],{stdio:['pipe','pipe','inherit']});let browser;
 try{
 const {origin}=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('fixture timeout')),30000);child.stdout.on('data',b=>{text+=b;const m=text.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});child.on('exit',c=>{clearTimeout(timer);reject(Error('fixture exit '+c));});});
 browser=await chromium.launch({channel:'chrome',headless:true});const context=await browser.newContext({viewport:{width:1440,height:1000}});const headers={Origin:origin};
 assert.ok((await context.request.post(origin+'/api/auth/login',{headers,data:{username:'admin',password:'viewport-test'}})).ok());
 const response=await context.request.post(origin+'/api/projects',{headers,data:{name:'节点附属面板定位验证',nodes:[{id:'text',type:'prompt',title:'文本节点',text:'测试文本',x:350,y:220}],edges:[],viewport:{x:60,y:40,zoom:1}}});assert.ok(response.ok());const project=await response.json();
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route(/\/app\.js\?/,route=>route.fulfill({contentType:'application/javascript',body:fs.readFileSync('public/app.js','utf8')+'\nwindow.anchorTest={state,setZoom,updateSelection};'}));
 await page.goto(origin+'/?project='+project.id);await page.waitForFunction(()=>window.anchorTest?.state.project&&!anchorTest.state.switchingProject&&document.querySelector('[data-node="text"]'));
 await page.evaluate(()=>anchorTest.updateSelection('text'));await page.waitForTimeout(250);
 const snapshot=()=>page.evaluate(()=>{const r=s=>{const el=document.querySelector(s),b=el.getBoundingClientRect();return {x:b.x,y:b.y,width:b.width,height:b.height,visibility:getComputedStyle(el).visibility,hidden:el.hidden};};return {node:r('[data-node="text"]'),toolbar:r('#nodeContextToolbar'),editor:r('#inspector'),zoom:anchorTest.state.zoom};});
 const close=(a,b)=>assert.ok(Math.abs(a-b)<1,`${a} != ${b}`);
 const check=s=>{for(const key of ['toolbar','editor']){const el=s[key];assert.equal(el.hidden,false);assert.equal(el.visibility,'visible');close(el.x+el.width/2,s.node.x+s.node.width/2);}close((s.node.y-s.toolbar.y-s.toolbar.height)/s.zoom,10);close((s.editor.y-s.node.y-s.node.height)/s.zoom,12);close(s.editor.width/s.zoom,520);};
 const initial=await snapshot();check(initial);
 // Pan the actual canvas while the pointer is still held: attachments stay present.
 await page.mouse.move(1100,180);await page.mouse.down({button:'middle'});await page.mouse.move(400,80,{steps:6});await page.waitForTimeout(100);const panned=await snapshot();check(panned);for(const key of ['node','toolbar','editor']){close(panned[key].x-initial[key].x,-700);close(panned[key].y-initial[key].y,-100);}assert.ok(panned.editor.x<0);await page.mouse.up({button:'middle'});
 // Moving to every side of the viewport never clamps an attachment to an edge.
 for(const [x,y,z] of [[-1500,-1100,.74],[1700,1400,.74],[100,80,.43],[60,40,1.7]]){await page.evaluate(([x,y,z])=>{anchorTest.state.panX=x;anchorTest.state.panY=y;anchorTest.setZoom(z);},[x,y,z]);await page.waitForTimeout(80);check(await snapshot());}
 // Resizing the viewport must not resize the world-space editor.
 await page.setViewportSize({width:900,height:650});await page.waitForTimeout(150);check(await snapshot());
 await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>{anchorTest.state.panX=60;anchorTest.state.panY=40;anchorTest.setZoom(1);});await page.waitForTimeout(150);
 const beforeDrag=await snapshot(),title=await page.locator('[data-node="text"] .node-title b').boundingBox();await page.mouse.move(title.x+20,title.y+5);await page.mouse.down();await page.mouse.move(title.x+100,title.y+65,{steps:5});await page.waitForTimeout(80);const duringDrag=await snapshot();for(const key of ['toolbar','editor'])assert.equal(duringDrag[key].visibility,'hidden');await page.mouse.up();await page.waitForTimeout(220);const afterDrag=await snapshot();check(afterDrag);close(afterDrag.editor.x-beforeDrag.editor.x,80);close(afterDrag.toolbar.y-beforeDrag.toolbar.y,60);
 await page.locator('#canvas').screenshot({path:'work/node-anchored-panels.png'});assert.deepEqual(errors,[]);
 console.log('PASS node-relative toolbar/editor during canvas pan, hidden during node drag and restored on release, offscreen positions on all sides, zoom, resize, no JavaScript errors');
 }finally{await browser?.close();child.stdin.end('\n');}
})().catch(e=>{console.error(e);process.exitCode=1;});
