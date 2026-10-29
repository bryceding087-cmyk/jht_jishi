// Real Chromium decoding, synthetic local media, no provider or user database.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const root=path.resolve(__dirname,'..'),output=path.join(root,'work/storyboard-covers');
fs.mkdirSync(output,{recursive:true});
const generated=spawnSync(path.join(root,'.venv/Scripts/python.exe'),['-c',`
import imageio_ffmpeg, subprocess, sys
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=320x180:r=24:d=2', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', sys.argv[1]],check=True)
`,path.join(output,'clip.mp4')],{encoding:'utf8'});
assert.equal(generated.status,0,generated.stderr);
const styles=fs.readFileSync(path.join(root,'public/index.html'),'utf8').match(/<link rel="stylesheet"[^>]+>/g).join('\n');
const html=`<!doctype html><html><head><meta charset="utf-8">${styles}<style>body{display:block;overflow:auto;height:auto}#board{width:800px;margin:20px}.board-grid{display:grid}</style></head><body><div id="board"></div><script type="module">
import {createStoryboard} from '/storyboard.js';
import {icon} from '/iconography.js';
const esc=text=>String(text).replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll('<','&lt;');
window.items=Array.from({length:24},(_,i)=>({id:'clip-'+i,nodeId:'clip-'+i,category:'video',kind:'video',url:'/clips/'+i+'.mp4',title:'视频 '+i,description:'生成的视频',status:'已生成',duration:2,ratio:'16:9'}));
window.board=createStoryboard(document.querySelector('#board'),{esc,icon,onSelect:id=>window.selected=id});
window.update=()=>board.update({title:'视频封面验证',items});update();
</script></body></html>`;
(async()=>{let browser;const requested=new Set(),errors=[];try{
 browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:900,height:650}});
 page.on('pageerror',error=>errors.push(error.message));
 await page.route('**/*',route=>{
  const url=new URL(route.request().url());
  if(url.hostname!=='storyboard.test')return route.abort();
  if(url.pathname==='/')return route.fulfill({contentType:'text/html; charset=utf-8',body:html});
  if(url.pathname.startsWith('/clips/')){requested.add(url.pathname);return route.fulfill({contentType:'video/mp4',body:fs.readFileSync(path.join(output,'clip.mp4'))});}
  const file=path.resolve(root,'public','.'+url.pathname);
  if(!file.startsWith(path.join(root,'public')+path.sep)||!fs.existsSync(file))return route.fulfill({status:404,body:''});
  return route.fulfill({contentType:({'.js':'application/javascript','.css':'text/css','.svg':'image/svg+xml'})[path.extname(file)]||'application/octet-stream',body:fs.readFileSync(file)});
 });
 await page.goto('http://storyboard.test/');
 const first=page.locator('[data-storyboard-id=clip-0] video');
 const ready=selector=>page.waitForFunction(selector=>{const v=document.querySelector(selector);return v&&v.readyState>=2&&!v.seeking;},selector);
 await ready('[data-storyboard-id=clip-0] video');
 assert.equal(await first.evaluate(v=>v.paused&&!v.autoplay&&v.controls),true,'Cover loading must not play video');
 const pixel=await first.evaluate(v=>{const c=document.createElement('canvas');c.width=c.height=1;const ctx=c.getContext('2d');ctx.drawImage(v,0,0,1,1);return [...ctx.getImageData(0,0,1,1).data];});
 assert.ok(pixel[0]>200&&pixel[1]<30&&pixel[2]<30,'The cover must contain a decoded red frame, not a blank player: '+pixel);
 assert.equal(requested.has('/clips/23.mp4'),false,'Offscreen videos must not fetch');
 assert.ok(requested.size<24);
 await page.screenshot({path:path.join(output,'covers-visible.png')});
 await first.evaluate(async v=>{window.original=v;await v.play();});
 await page.waitForFunction(()=>original.currentTime>.2&&!original.paused);
 await page.evaluate(()=>update());
 assert.equal(await first.evaluate(v=>v===original&&!v.paused),true,'Polling must preserve the existing video and playback');
 await page.evaluate(()=>board.pause());assert.equal(await first.evaluate(v=>v.paused),true);
 await page.locator('[data-board-category=image]').click();
 assert.equal(await page.evaluate(()=>original.paused&&!original.hasAttribute('src')),true,'Removed cards must release playback sources');
 await page.locator('[data-board-category=video]').click();await ready('[data-storyboard-id=clip-0] video');
 await page.locator('[data-storyboard-id=clip-23]').scrollIntoViewIfNeeded();await ready('[data-storyboard-id=clip-23] video');
 assert.equal(requested.has('/clips/23.mp4'),true);
 await page.locator('[data-storyboard-id=clip-23] [data-board-node]').click();assert.equal(await page.evaluate(()=>selected),'clip-23');
 await page.reload();await page.evaluate(()=>scrollTo(0,0));await ready('[data-storyboard-id=clip-0] video');
 assert.deepEqual(errors,[]);
 console.log('PASS storyboard decoded covers before play, viewport-only loading, playback, stable updates, category switching, media cleanup, node navigation and reload');
}finally{await browser?.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
