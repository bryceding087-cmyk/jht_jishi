// Real canvas and image persistence; generated PNGs replace all provider traffic.
const assert=require('node:assert/strict'),{spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright'),{fixture,api}=require('./market_login.browser.cjs');
const setup=String.raw`
    import io
    from PIL import Image
    picture = io.BytesIO()
    Image.new('RGB', (256, 256), '#bf733c').save(picture, 'PNG')
    app.provider_json = lambda *args, **kwargs: {'data': [{'url': 'https://fixture.invalid/image.png'}]}
    app.download_generated_image = lambda url: (picture.getvalue(), '.png')
    app.save('image_model_config', {**app.DEFAULT_IMAGE_MODEL, 'api_key': 'fixture-only'})
`;
(async()=>{const server=spawn('.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','pipe']});let browser;try{
 const info=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(Error('startup timeout')),30000);server.stdout.on('data',data=>{text+=data;const m=text.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});server.stderr.on('data',data=>process.stderr.write(data));});
 browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
 const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(info.parent+'/?user=prop-preview');const frame=await (await page.waitForSelector('#studio')).contentFrame();
 await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
 const nodes=[['studio-prop','prop',true],['plain-prop','prop',false],['image','asset',false]].map(([id,type,studio],i)=>({id,type,title:id,x:i*400,y:50,image_settings:{prompt:'一把三角抹子',count:1,size:'1K',aspect_ratio:'1:1'},...(studio?{mujian:{role:'asset',assetType:'prop',inputs:[],runId:'fixture-run'}}:{})}));
 const project=(await api(frame,'/api/projects',{name:'生成图片即时预览',nodes,edges:[]})).data;
 await frame.goto(info.app+'/market?project='+project.id);await frame.waitForSelector('.node[data-node=studio-prop]');
 await frame.locator('#canvas').focus();await page.keyboard.press('Control+0');
 for(const node of nodes){
  await frame.locator(`.node[data-node="${node.id}"] .frame-stage`).click({force:true});
  await frame.waitForFunction(()=>{const b=document.querySelector('.inline-generate-button');return b&&!b.disabled;});
  await frame.locator('.inline-generate-button').click();
  await frame.waitForFunction(id=>{const img=document.querySelector(`.node[data-node="${id}"] img.frame-media`);return img?.complete&&img.naturalWidth>0;},node.id,{timeout:20000}).catch(async error=>{console.error(await frame.locator('.inline-generation-status').innerText());console.error(await frame.locator(`.node[data-node="${node.id}"] .frame-stage`).innerHTML());throw error;});
  await frame.waitForFunction(()=>document.querySelector('.inline-generation-status')?.textContent.includes('已生成并更新当前节点'));
  const asset=(await api(frame,'/api/assets')).data.find(a=>a.name===node.id||a.name.startsWith(node.id+'.'));
  assert.equal(asset.kind,'image','Props intentionally use normal image media');
  await frame.waitForFunction(async({pid,nid,aid})=>{const m=await import('/market-session.js?v=market-sdk-3'),url='/api/projects/'+pid;const p=await(await fetch(url,m.withMarketSession({},url))).json();return p.nodes.find(n=>n.id===nid)?.assetId===aid;},{pid:project.id,nid:node.id,aid:asset.id});
 }
 await page.screenshot({path:'work/generated-prop-preview.png'});
 await frame.goto(info.app+'/market?project='+project.id);await frame.waitForFunction(()=>[...document.querySelectorAll('.node img.frame-media')].filter(img=>img.complete&&img.naturalWidth>0).length===3);
 assert.deepEqual(errors,[]);console.log('PASS: Agent prop, ordinary prop and image nodes show decoded generated pictures immediately and after reload');
}finally{await browser?.close();server.stdin.end('\n');}})().catch(e=>{console.error(e);process.exitCode=1;});
