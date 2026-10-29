const assert=require('node:assert/strict');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture}=require('./browser_fixture.cjs');
(async()=>{
 const child=spawn('.venv/Scripts/python.exe',['-u','-c',fixture],{stdio:['pipe','pipe','inherit']});let browser;
 try{
 const {origin}=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('fixture timeout')),30000);child.stdout.on('data',b=>{text+=b;const m=text.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});child.on('exit',c=>{clearTimeout(timer);reject(Error('fixture exit '+c));});});
 browser=await chromium.launch({channel:'chrome',headless:true});const context=await browser.newContext({viewport:{width:1600,height:1000}});const headers={Origin:origin};
 assert.ok((await context.request.post(origin+'/api/auth/login',{headers,data:{username:'admin',password:'viewport-test'}})).ok());
 const response=await context.request.post(origin+'/api/projects',{headers,data:{name:'网格吸附验证',nodes:[{id:'a',type:'prompt',title:'A',x:151,y:149},{id:'b',type:'prompt',title:'B',x:561,y:181}],groups:[{id:'group',title:'Group',node_ids:['a','b'],color:'#4285f4'}],edges:[],viewport:{x:80,y:80,zoom:.43}}});assert.ok(response.ok());const project=await response.json();
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route(/\/app\.js\?/,route=>route.fulfill({contentType:'application/javascript',body:fs.readFileSync('public/app.js','utf8')+'\nwindow.snapTest={state,setZoom,renderNodes,flushSave,canvasGroups};'}));
 await page.goto(origin+'/?project='+project.id);await page.waitForFunction(()=>window.snapTest?.state.project&&!snapTest.state.switchingProject&&document.querySelector('[data-node="a"]'));
 const positions=()=>page.evaluate(()=>snapTest.state.nodes.map(n=>({x:n.x,y:n.y})));
 const drag=async(dx,dy,group=false)=>{const target=group?page.locator('[data-canvas-group="group"] .canvas-group-name'):page.locator('[data-node="a"] .node-title b');const box=await target.boundingBox();const x=box.x+box.width/2,y=box.y+box.height/2;await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+dx,y+dy,{steps:4});};
 await page.locator('#toggleSnap').click();assert.equal(await page.locator('#toggleSnap').getAttribute('aria-pressed'),'true');
 for(const zoom of [.43,1,1.7]){
 await page.evaluate(z=>{snapTest.state.panX=80;snapTest.state.panY=80;snapTest.setZoom(z);snapTest.state.selectedNodeIds=new Set(['a']);snapTest.state.selectedNodeId='a';snapTest.state.selectedGroupId=null;},zoom);
 const before=await positions();await drag(37,29);const live=await positions();assert.equal(live[0].x%24,0);assert.equal(live[0].y%24,0);assert.notDeepEqual(live[0],before[0]);await page.mouse.up();assert.deepEqual((await positions())[0],live[0]);
 const grid=await page.locator('#canvas .grid').evaluate(el=>({size:el.style.backgroundSize,position:el.style.backgroundPosition}));assert.equal(grid.size,`${24*zoom}px ${24*zoom}px`);assert.equal(grid.position,`${80-12*zoom}px ${80-12*zoom}px`);
 }
 // Multiple selection follows the dragged anchor without changing spacing.
 await page.evaluate(()=>{snapTest.setZoom(1);snapTest.state.selectedNodeIds=new Set(['a','b']);snapTest.state.selectedNodeId='a';snapTest.state.selectedGroupId=null;});
 const multiBefore=await positions();await drag(41,34);const multiAfter=await positions();await page.mouse.up();assert.equal(multiAfter[1].x-multiAfter[0].x,multiBefore[1].x-multiBefore[0].x);assert.equal(multiAfter[1].y-multiAfter[0].y,multiBefore[1].y-multiBefore[0].y);
 // Group movement snaps its frame while preserving all member offsets.
 await page.waitForTimeout(150);const groupBefore=await positions();await drag(35,43,true);const groupAfter=await positions();assert.equal(groupAfter[1].x-groupAfter[0].x,groupBefore[1].x-groupBefore[0].x);assert.equal(groupAfter[1].y-groupAfter[0].y,groupBefore[1].y-groupBefore[0].y);assert.ok(await page.evaluate(()=>{const g=snapTest.state.groups[0];return g.x%24===0&&g.y%24===0;}));await page.mouse.up();
 // Disabling snapping restores free movement.
 await page.locator('#toggleSnap').click();await page.evaluate(()=>{snapTest.state.selectedGroupId=null;snapTest.state.selectedNodeIds=new Set(['a']);});const freeBefore=await positions();await drag(13,17);await page.mouse.up();const freeAfter=await positions();assert.equal(freeAfter[0].x-freeBefore[0].x,13);assert.equal(freeAfter[0].y-freeBefore[0].y,17);
 await page.evaluate(()=>snapTest.flushSave());const saved=await(await context.request.get(origin+'/api/projects/'+project.id)).json();assert.equal(saved.nodes.find(n=>n.id==='a').x,freeAfter[0].x);assert.equal(saved.nodes.find(n=>n.id==='b').y,freeAfter[1].y);assert.deepEqual(errors,[]);
 console.log('PASS live snap at 43%/100%/170%, world grid transform, multiselect spacing, group frame snap, free drag, persisted coordinates');
 }finally{await browser?.close();child.stdin.end('\n');}
})().catch(e=>{console.error(e);process.exitCode=1;});
