// Real iframe/auth/download behavior, disposable storage and a fake video gateway.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {spawn}=require('node:child_process');
const {chromium}=require(process.env.JHT_PLAYWRIGHT||'playwright');
const {fixture,api}=require('./market_login.browser.cjs');

const setup=String.raw`
    import io, uuid
    from PIL import Image
    from urllib.error import URLError
    from agent_media import execute
    import video_service
    clip=app.DATA/'download-fixture.mp4'
    execute(['-f','lavfi','-i','color=c=0x377d89:s=160x90:r=24:d=1','-t','1','-c:v','libx264','-pix_fmt','yuv420p',str(clip)])
    video_bytes=clip.read_bytes()
    picture=io.BytesIO()
    Image.new('RGB',(32,32),'#377d89').save(picture,'PNG')
    (app.UPLOADS/'download-fixture.png').write_bytes(picture.getvalue())
    downloads={'fail':True,'calls':0,'internal':True,'signature':True}
    class VideoResponse(io.BytesIO):
        headers={'Content-Type':'video/mp4','Content-Length':str(len(video_bytes))}
        def __init__(self,url):
            super().__init__(video_bytes)
            self.url=url
        def geturl(self):return self.url
    class VideoOpener:
        def open(self,request,timeout):
            downloads['calls']+=1
            downloads['internal'] &= request.full_url.startswith('http://gateway.test:4091/v1/video-assets/')
            downloads['signature'] &= request.full_url.endswith('?token=a%2Fb%2Bc&expires=999')
            if downloads['fail']:raise URLError(ConnectionRefusedError('fixture offline'))
            return VideoResponse(request.full_url)
    video_service.build_opener=lambda *args:VideoOpener()
    @app.app.post('/api/test/downloads')
    def control(body:dict):
        if 'fail' in body:downloads['fail']=body['fail']
        if body.get('seed'):
            tid='download-'+uuid.uuid4().hex
            task=app.save('task',{'id':tid,'status':'done','project_id':'','created_at':1,
                'params':{'prompt':'下载测试视频','duration':1},
                'video_url':'https://aimarket.hncig.cn:4090/v1/video-assets/result.mp4?token=a%2Fb%2Bc&expires=999',
                'video_cache_status':'failed','video_cache_error':'服务器无法连接视频存储服务，请检查服务器出口网络和代理配置。'})
            asset=app.asset_public(app.ensure_video_asset(tid))
            image=app.save('asset',{'id':'download-image-'+uuid.uuid4().hex,'kind':'image','name':'下载测试图片',
                'url':'/uploads/download-fixture.png','source':'uploaded','in_library':False})
            return {'video':asset,'image':image,'video_size':len(video_bytes),'image_size':len(picture.getvalue())}
        return downloads
    app.app.router.routes.insert(0,app.app.router.routes.pop())
`;

(async()=>{
  const server=spawn(process.env.JHT_TEST_PYTHON||'.venv/Scripts/python.exe',['-u','-c',fixture.replace('    import app\n','    import app\n'+setup)],{stdio:['pipe','pipe','pipe']});
  let browser;
  try{
    const info=await new Promise((resolve,reject)=>{
      let output='';const timer=setTimeout(()=>reject(new Error('Fixture startup timeout')),30000);
      server.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/FIXTURE (.+)\r?\n/);if(match){clearTimeout(timer);resolve(JSON.parse(match[1]));}});
      server.stderr.on('data',chunk=>process.stderr.write(chunk));
      server.once('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exit '+code));});
    });
    browser=await chromium.launch({channel:'chrome',headless:true,args:['--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server']});
    const context=await browser.newContext({viewport:{width:1500,height:1050},acceptDownloads:true});
    // Browser previews of the fake external address must never hit a live gateway.
    await context.route('https://aimarket.hncig.cn:4090/**',route=>route.fulfill({status:503,body:'fixture offline'}));
    const page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
    await page.goto(info.parent+'/?user=downloads');
    const frame=await (await page.waitForSelector('#studio')).contentFrame();
    await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'));
    const seed=(await api(frame,'/api/test/downloads',{seed:true})).data;
    const originalUrl=frame.url();let navigations=0,downloads=0;
    page.on('framenavigated',changed=>{if(changed===frame)navigations++;});
    page.on('download',()=>downloads++);
    await frame.locator('#mainNav [data-view="assets"]').click();
    const card=frame.locator(`[data-asset-id="${seed.video.id}"]`);
    await card.hover();
    await card.locator('[data-al-download]').click();
    await frame.waitForFunction(()=>document.querySelector('#toast')?.textContent.includes('服务器出口网络'));
    assert.equal(frame.url(),originalUrl);assert.equal(navigations,0);assert.equal(downloads,0);
    assert.equal((await api(frame,'/api/test/downloads',{})).data.calls,1);
    await frame.locator(`[data-al-preview="${seed.video.id}"]`).click();
    await frame.waitForFunction(()=>document.querySelector('.al-preview-info .al-error')?.textContent.includes('服务器出口网络'));
    await frame.locator('#modalClose').click();
    console.log('PASS: failed video caching stays in the app and shows the server error without navigation or a false download');

    await api(frame,'/api/test/downloads',{fail:false});
    await card.hover();
    const videoDownload=page.waitForEvent('download');
    await card.locator('[data-al-download]').click();
    const video=await videoDownload;
    assert.equal(video.suggestedFilename(),'下载测试视频.mp4');
    assert.equal(fs.statSync(await video.path()).size,seed.video_size);
    const control=(await api(frame,'/api/test/downloads',{})).data;
    assert.equal(control.calls,2);assert.equal(control.internal,true);assert.equal(control.signature,true);
    assert.equal((await api(frame,'/api/assets/'+seed.video.id)).data.in_library,false);
    await card.hover();
    const cachedDownload=page.waitForEvent('download');
    await card.locator('[data-al-download]').click();await cachedDownload;
    assert.equal((await api(frame,'/api/test/downloads',{})).data.calls,2,'cached downloads never call the gateway again');
    console.log('PASS: gateway files download through the internal origin with signatures intact and remain in history without library membership');

    await frame.locator(`[data-al-preview="${seed.image.id}"]`).click();
    const imageDownload=page.waitForEvent('download');
    await frame.locator('#alPreviewDownload').click();
    const image=await imageDownload;
    assert.equal(image.suggestedFilename(),'下载测试图片.png');
    assert.equal(fs.statSync(await image.path()).size,seed.image_size);
    assert.equal(navigations,0);assert.equal(frame.url(),originalUrl);
    assert.equal((await api(frame,'/api/auth/me')).status,200);
    assert.deepEqual(errors,[]);
    console.log('PASS: image preview downloads are attachments and preserve the iframe URL and marketplace session');
  }finally{
    if(browser)await browser.close();server.stdin.end('\n');
    await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(()=>{server.kill();resolve();},15000);server.once('exit',()=>{clearTimeout(timer);resolve();});});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
