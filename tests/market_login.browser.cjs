// Real Chrome, separate HTTP origins, real application routes; no live models.
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require(process.env.JHT_PLAYWRIGHT || 'playwright');
const fixture = String.raw`
import os, sys, tempfile, socket, threading, json, ssl, subprocess, http.client
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import uvicorn
ThreadingHTTPServer.request_queue_size = 128
with tempfile.TemporaryDirectory(prefix='jht-market-browser-') as directory:
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    use_proxy = os.getenv('JHT_TEST_PROXY') == '1'
    proxy = None
    tls = None
    app_origin = 'http://jht-market-test.test:' + str(listener.getsockname()[1])
    if use_proxy:
        cert, key = str(Path(directory) / 'fixture.crt'), str(Path(directory) / 'fixture.key')
        subprocess.run([os.getenv('JHT_TEST_OPENSSL', 'openssl'), 'req', '-x509', '-newkey', 'rsa:2048',
            '-nodes', '-keyout', key, '-out', cert, '-days', '1', '-subj', '/CN=jht-market-test.test'],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert, key)
        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def forward(self):
                upstream = http.client.HTTPConnection('127.0.0.1', listener.getsockname()[1], timeout=20)
                headers = {k:v for k,v in self.headers.items() if k.lower() not in ('host', 'connection', 'x-forwarded-proto', 'x-forwarded-host', 'forwarded')}
                # Deliberately rewrite Host, omit forwarded HTTPS information.
                headers['Host'] = '127.0.0.1:' + str(listener.getsockname()[1])
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                try:
                    upstream.request(self.command, self.path, body=body, headers=headers)
                    response = upstream.getresponse()
                    data = response.read()
                    self.send_response(response.status)
                    for k,v in response.getheaders():
                        if k.lower() not in ('connection', 'transfer-encoding', 'content-length'):
                            self.send_header(k,v)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    if self.command != 'HEAD': self.wfile.write(data)
                finally: upstream.close()
            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = forward
        proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
        proxy.socket = tls.wrap_socket(proxy.socket, server_side=True, do_handshake_on_connect=False)
        app_origin = 'https://jht-market-test.test:' + str(proxy.server_port)
    parent_html = '''<!doctype html><html><body><iframe id="studio" style="width:1200px;height:900px" src="APP_ORIGIN/market"></iframe>
    <script>
    const user = new URL(location.href).searchParams.get('user') || 'alpha';
    addEventListener('message', event => {
      if (event.origin !== 'APP_ORIGIN' || event.source !== document.getElementById('studio').contentWindow || event.data.type !== 'aimarket:connect') return;
      event.source.postMessage({type:'aimarket:context',version:1,requestId:event.data.requestId,
        user:{id:user,name:'Member '+user,accountType:'ordinary',workNumber:'WORK-'+user,organization:{id:'org',name:'Test Department',type:'department'}},
        agent:{id:'fixture-agent',name:'Studio'},apiKey:'sk-browser-fixture-'+user},event.origin);
    });
    </script></body></html>'''.replace('APP_ORIGIN', app_origin).encode()
    class Parent(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(parent_html)
    parent = ThreadingHTTPServer(('127.0.0.1', 0), Parent)
    if tls: parent.socket = tls.wrap_socket(parent.socket, server_side=True, do_handshake_on_connect=False)
    parent_origin = ('https' if use_proxy else 'http') + '://market-parent-test.test:' + str(parent.server_port)
    url = 'sqlite:///' + (Path(directory) / 'fixture.db').as_posix()
    os.environ.update(JIANHUITONG_DATA_DIR=directory, JIANHUITONG_DATABASE_URL=url, DATABASE_URL=url,
        JIANHUITONG_MARKET_ORIGIN=parent_origin, JIANHUITONG_MARKET_MODEL_ORIGIN='http://gateway.test:4091',
        JIANHUITONG_MARKET_AGENT_ID='fixture-agent', JIANHUITONG_MARKET_SDK_LOGIN='1')
    os.environ['JIANHUITONG_PUBLIC_ORIGIN'] = app_origin if use_proxy else ''
    import app
    server = uvicorn.Server(uvicorn.Config(app.app, log_level='error', proxy_headers=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets':[listener]}, daemon=True)
    thread.start()
    if proxy: threading.Thread(target=proxy.serve_forever, daemon=True).start()
    threading.Thread(target=parent.serve_forever, daemon=True).start()
    print('FIXTURE ' + json.dumps({'parent':parent_origin,'app':app_origin}), flush=True)
    try: sys.stdin.readline()
    finally:
        server.should_exit = True
        thread.join(10)
        parent.shutdown()
        if proxy: proxy.shutdown()
        app.database.engine.dispose()
`;
async function api(frame, url, body) {
  return frame.evaluate(async ({url, body}) => {
    const m = await import('/market-session.js?v=market-sdk-3');
    const response = await fetch(url, m.withMarketSession(body === undefined ? {} : {
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)
    }, url));
    return { status:response.status, data:m.marketResponseData(await response.json()) };
  }, {url, body});
}
(async () => {
  const server = spawn(process.env.JHT_TEST_PYTHON || '.venv/Scripts/python.exe', ['-u','-c',fixture], {stdio:['pipe','pipe','pipe']});
  let browser;
  try {
    const info = await new Promise((resolve,reject) => {
      let buffer='';
      const timer=setTimeout(()=>reject(new Error('Fixture startup timeout')),30000);
      server.stdout.on('data',bytes=>{buffer+=bytes;const m=buffer.match(/FIXTURE (.+)\r?\n/);if(m){clearTimeout(timer);resolve(JSON.parse(m[1]));}});
      server.stderr.on('data',bytes=>process.stderr.write(bytes));
      server.on('exit',code=>{clearTimeout(timer);reject(new Error('Fixture exited '+code));});
    });
    browser=await chromium.launch({channel:'chrome',headless:true,args:[
      '--host-resolver-rules=MAP jht-market-test.test 127.0.0.1, MAP market-parent-test.test 127.0.0.1','--no-proxy-server',
      ...(process.env.JHT_TEST_PROXY==='1'?['--ignore-certificate-errors']:[])]});
    const context=await browser.newContext({ignoreHTTPSErrors:true});
    const errors=[], loginRequests=[];
    context.on('page',page=>{
      page.on('pageerror',error=>errors.push(error.message));
      page.on('requestfailed',request=>console.log('REQUEST FAILED:', request.url(), request.failure()?.errorText));
    });
    context.on('request',r=>{if(new URL(r.url()).pathname==='/api/auth/login')loginRequests.push(r.url());});
    async function open(user) {
      const page=await context.newPage();
      await page.goto(info.parent+'/?user='+user);
      const frame=await (await page.waitForSelector('#studio')).contentFrame();
      try {
        await frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'),null,{timeout:20000});
      } catch(error) {
        console.log('BOOT DIAGNOSTIC:', await frame.locator('#appBootMessage').textContent(), errors);
        throw error;
      }
      assert.equal(await frame.locator('#sessionName').textContent(),'Member '+user);
      assert.equal(await frame.locator('#sessionRole').textContent(),'Test Department');
      assert.equal(await frame.locator('#loginForm').count(),0);
      assert.equal(await frame.evaluate(()=>isSecureContext),process.env.JHT_TEST_PROXY==='1');
      return {page,frame};
    }
    let alpha=await open('alpha');
    await alpha.frame.evaluate(()=>{
      document.querySelector('#sessionName').textContent='丁曼夫';
      document.querySelector('#sessionRole').textContent='湖南省第二工程有限公司智能建造中心';
    });
    const profile = alpha.frame.locator('#sessionUser');
    const bounds = await profile.evaluate(element=>{
      const button=element.querySelector('button'), role=element.querySelector('#sessionRole');
      const box=element.getBoundingClientRect(), b=button.getBoundingClientRect();
      return {icons:button.querySelectorAll('svg').length,label:button.getAttribute('aria-label'),
        fits:b.left>=box.left&&b.right<=box.right, roleHeight:role.getBoundingClientRect().height};
    });
    assert.equal(bounds.icons,1);
    assert.equal(bounds.label,'重新连接集市');
    assert.equal(bounds.fits,true);
    assert.ok(bounds.roleHeight<=36);
    await profile.screenshot({path:'work/market-profile-fixed.png'});
    await alpha.frame.waitForFunction(()=>document.querySelector('#marketConnection')?.hidden===true,null,{timeout:5000});
    console.log('PASS: profile reconnect icon fits with long department name; success banner disappears');
    assert.deepEqual(await context.cookies(info.app),[]);
    const project=await api(alpha.frame,'/api/projects',{name:'Alpha private project'});
    assert.equal(project.status,201);
    const beta=await open('beta');
    assert.equal((await api(beta.frame,'/api/projects/'+project.data.id)).status,404);
    assert.deepEqual((await api(beta.frame,'/api/projects')).data,[]);
    console.log('PASS: '+(process.env.JHT_TEST_PROXY==='1'?'HTTPS reverse proxy to HTTP backend':'cross-origin HTTP')+' iframe auto-login without cookies; names/departments and account isolation');
    await alpha.page.reload();
    alpha.frame=await (await alpha.page.waitForSelector('#studio')).contentFrame();
    await alpha.frame.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'),null,{timeout:20000});
    assert.equal((await api(alpha.frame,'/api/projects/'+project.data.id)).status,200);
    const asset=await alpha.frame.evaluate(async()=>{
      const m=await import('/market-session.js?v=market-sdk-3');
      const canvas=document.createElement('canvas');canvas.width=canvas.height=2;
      canvas.getContext('2d').fillRect(0,0,2,2);
      const data=new FormData();data.append('file',await new Promise(r=>canvas.toBlob(r)),'fixture.png');
      const response=await fetch('/api/assets',m.withMarketSession({method:'POST',body:data},'/api/assets'));
      if(!response.ok)throw new Error('Upload '+response.status+': '+await response.text());
      return m.marketResponseData(await response.json());
    });
    assert.equal(await alpha.frame.evaluate(url=>new Promise(resolve=>{const image=new Image();image.onload=()=>resolve(image.naturalWidth);image.onerror=()=>resolve(0);image.src=url;}),asset.url),2);
    await alpha.frame.evaluate(()=>{const f=document.createElement('iframe');f.id='testCanvas';f.src='/?embedded=1&view=canvas';document.body.append(f);});
    const canvas=await (await alpha.frame.waitForSelector('#testCanvas')).contentFrame();
    await canvas.waitForFunction(()=>!document.documentElement.hasAttribute('data-app-boot'),null,{timeout:20000});
    assert.equal(await canvas.locator('#sessionName').textContent(),'Member alpha');
    assert.equal((await api(canvas,'/api/projects/'+project.data.id)).status,200);
    assert.deepEqual(loginRequests,[]);
    assert.deepEqual(await context.cookies(info.app),[]);
    assert.deepEqual(errors,[]);
    console.log('PASS: refreshed identity, image upload/preview, nested canvas session, no password calls or JS errors');
  } finally {
    if(browser)await browser.close();
    server.stdin.end('\n');
    await new Promise(resolve=>{if(server.exitCode!==null)return resolve();const timer=setTimeout(()=>{server.kill();resolve();},15000);server.once('exit',()=>{clearTimeout(timer);resolve();});});
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
