const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
(async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
    const page=await browser.newPage({viewport:{width:800,height:400}});
    await page.setContent(`<style>${fs.readFileSync('public/request-button.css','utf8')}body{background:#18191b;color:#ddd;font:14px sans-serif;padding:40px;display:flex;gap:24px}button{display:inline-flex;align-items:center;justify-content:center;background:#b6b4c9;color:#171819;border:0;border-radius:24px;padding:12px;min-width:44px;height:44px}</style><form><button id="send" type="submit" aria-label="发送消息" disabled>↑</button></form><button id="labeled" type="button">生成图片</button>`);
    const source=fs.readFileSync('public/request-button.js','utf8').replace('export function createRequestButton','function createRequestButton');
    await page.addScriptTag({content:source+'\nwindow.createRequestButton=createRequestButton;'});
    await page.evaluate(()=>{
      window.submits=0;window.stops=0;
      document.querySelector('form').onsubmit=event=>{event.preventDefault();window.submits++;};
      window.control=createRequestButton(document.querySelector('#send'),{onStop(){window.stops++;return new Promise(resolve=>window.releaseStop=resolve);}});
      window.labeled=createRequestButton(document.querySelector('#labeled'),{showLabel:true,sendLabel:'生成图片',stopLabel:'停止生成',initialState:'running',onStop(){}});
      control.setState('idle',{disabled:false});
    });
    await page.locator('#send').click();assert.equal(await page.evaluate(()=>submits),1);
    await page.evaluate(()=>control.setState('running'));
    assert.equal(await page.locator('#send').isEnabled(),true);
    assert.equal(await page.locator('#send').getAttribute('type'),'button');
    assert.equal(await page.locator('#send').getAttribute('aria-label'),'停止生成');
    assert.equal(await page.locator('#labeled').innerText(),'停止生成');
    const duration=await page.locator('#send .request-button-stop').evaluate(element=>getComputedStyle(element).transitionDuration);
    assert.equal(duration,'0.2s, 0.2s');
    await page.locator('#send').click();await page.locator('#send').click({force:true});
    assert.equal(await page.evaluate(()=>stops),1);assert.equal(await page.evaluate(()=>submits),1);
    assert.equal(await page.locator('#send').getAttribute('aria-disabled'),'true');
    assert.equal(await page.locator('#send').evaluate(button=>button===document.activeElement),true);
    await page.evaluate(()=>{control.setState('idle',{disabled:false});releaseStop();});
    assert.equal(await page.locator('#send').getAttribute('data-request-state'),'idle','Late stop completion must not overwrite a new scope');
    await page.emulateMedia({reducedMotion:'reduce'});
    await page.evaluate(()=>control.setState('running'));
    assert.equal(await page.locator('#send .request-button-stop').evaluate(element=>getComputedStyle(element).transitionDuration),'0s');
    await page.screenshot({path:'work/request-button-states.png'});
    await page.evaluate(()=>control.destroy());
    assert.equal(await page.locator('#send').innerText(),'↑');
    assert.equal(await page.locator('#send').getAttribute('data-request-state'),null);
    console.log('PASS shared send/stop icon, labeled button, single stop, focus, stale completion, reduced motion and cleanup');
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
