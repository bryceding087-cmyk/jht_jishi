"""Unified home, generation/canvas switching and canvas creation without paid calls."""
from pathlib import Path
import socket
import sys
import threading
import time
import io
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_canvas_groups import CanvasGroupsTests
from verify_canvas_workspace import drawing
from auth_service import hash_password
import uvicorn
from playwright.sync_api import sync_playwright,expect

def main():
    CanvasGroupsTests.setUpClass();host=CanvasGroupsTests.host
    password='home-hub-test'
    with host.connect() as db:db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password(password),'admin'))
    asset=host.store_uploaded_asset(drawing(),'城市光影.png')
    project=host.create_project(host.Project(name='城市影像',nodes=[{'id':'ref','type':'asset','title':'城市参考','assetId':asset['id'],'x':160,'y':170}]))
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128);origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(host.app,log_level='error'));thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    output=ROOT/'outputs/home-hub-verification';output.mkdir(parents=True,exist_ok=True)
    try:
        while not server.started:time.sleep(.05)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
            context=browser.new_context(viewport={'width':1512,'height':1000})
            assert context.request.post(origin+'/api/auth/login',headers={'Origin':origin},data={'username':'admin','password':password,'remember':False}).ok
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            paid=[];page.on('request',lambda r:paid.append(r.url) if r.method=='POST' and any(path in r.url for path in ['/api/tasks','/api/images/generations','/api/audio/generations','/api/agent/runs']) else None)
            page.goto(origin+'/');expect(page.locator('#homeGenerateMethod')).to_have_attribute('aria-selected','true')
            expect(page.locator('#mainNav [data-view=home]')).to_have_count(1)
            expect(page.locator('#mainNav [data-view=agent],#mainNav [data-view=canvas]')).to_have_count(0)
            expect(page.locator('#mainNav [data-view=home]')).to_have_text('首页')
            expect(page.get_by_role('tablist',name='首页创作功能').get_by_role('tab')).to_have_count(4)
            expect(page.locator('#homeCanvasArea')).to_be_hidden()
            def reference_uploads(mode):
                page.locator('#homeTab-'+mode).click()
                files=[]
                for label,size,color in [('竖图',(100,260),'#498d9a'),('横图',(260,100),'#bf9c66'),('方图',(160,160),'#7d669e')]:
                    encoded=io.BytesIO();Image.new('RGB',size,color).save(encoded,'PNG')
                    files.append({'name':label+'.png','mimeType':'image/png','buffer':encoded.getvalue()})
                page.locator('#agentUpload' if mode=='agent' else '#homeMediaUpload').set_input_files(files)
                items=page.locator('#agentAttached .agent-reference' if mode=='agent' else '#homeReferences .home-reference')
                expect(items).to_have_count(3)
                for item in items.all():
                    expect(item.locator('img')).to_be_visible()
                    page.wait_for_function('el=>el.complete&&el.naturalWidth>0',arg=item.locator('img').element_handle())
                    tile=item.bounding_box();preview=item.locator('img').bounding_box()
                    assert abs(preview['width']-tile['width'])<=2 and abs(preview['height']-tile['height'])<=2,(mode,tile,preview)
                editor=page.locator('#agentInput + .mention-editor' if mode=='agent' else '#homePrompt + .mention-editor')
                assert editor.bounding_box()['y']>=max(item.bounding_box()['y']+item.bounding_box()['height'] for item in items.all())+8
                return items
            for mode in ['agent','image','video','audio']:
                items=reference_uploads(mode)
                if mode=='audio':
                    page.wait_for_timeout(400);page.screenshot(path=str(output/'07-uploaded-references.png'))
                    page.set_viewport_size({'width':430,'height':900})
                    page.screenshot(path=str(output/'08-mobile-references.png'))
                    assert page.locator('#agentView').evaluate('el=>el.scrollWidth<=el.clientWidth+1')
                    page.set_viewport_size({'width':1512,'height':1000})
                for _ in range(3):items.first.locator('[data-home-remove],[data-remove]').click()
                expect(items).to_have_count(0)
            page.locator('#homeTab-agent').click()
            expect(page.locator('#agentComposer [data-command=preferences],#agentComposer [data-command=scope],#agentModelLabel')).to_have_count(0)
            upload=page.locator('#agentComposer .agent-compose-tools [data-command=upload]').bounding_box()
            parameters=page.locator('.home-agent-parameters').bounding_box()
            skills=page.locator('#agentComposer [data-command=skills]').bounding_box()
            assert 0<parameters['x']-upload['x']<120
            assert skills['x']>=parameters['x']+parameters['width']
            page.wait_for_timeout(250);page.screenshot(path=str(output/'01-generation-home.png'))
            page.locator('#homeTab-image').click();page.wait_for_timeout(400)
            page.screenshot(path=str(output/'09-image-connected-tabs.png'))
            # Real custom controls on the landing page, including viewport placement and dismissal.
            page.locator('#homeTab-video').click();page.wait_for_timeout(400)
            thumb=page.locator('.home-mode-thumb').bounding_box();tab=page.locator('#homeTab-video').bounding_box()
            assert abs(thumb['x']-tab['x'])<2 and abs(thumb['width']-tab['width'])<2
            page.locator('#homeParameters .home-preference-trigger').first.click()
            panel=page.locator('.home-preference-panel:popover-open');expect(panel).to_be_visible()
            box=panel.bounding_box();assert box['y']>=0 and box['x']+box['width']<=1512
            page.wait_for_timeout(220);page.screenshot(path=str(output/'05-video-parameters.png'))
            page.keyboard.press('Escape');expect(panel).not_to_be_visible()
            page.locator('#homeTab-audio').click();page.locator('#homeParameters .home-preference-trigger').first.click()
            page.get_by_role('dialog',name='音频类型',exact=True).get_by_role('button',name='配音',exact=True).click()
            page.locator('#homeParameters .home-preference-trigger').nth(1).click();page.wait_for_timeout(220)
            page.screenshot(path=str(output/'06-audio-parameters.png'))
            page.locator('#homeTab-image').click();expect(panel).not_to_be_visible()
            page.locator('#homeTab-agent').click()
            page.locator('#agentInput + .mention-editor').fill('独立保留的 Agent 草稿')
            page.locator('#homeTab-image').click();page.locator('#homePrompt + .mention-editor').fill('图片模式的草稿')
            page.locator('#homeCanvasMethod').click();expect(page.locator('#homeCanvasArea')).to_be_visible();expect(page.locator('#homeGenerationArea')).to_be_hidden()
            expect(page.locator('.home-canvas-grid [data-open-canvas]')).to_have_count(1)
            assert len(context.request.get(origin+'/api/projects').json())==1
            page.wait_for_timeout(280);page.screenshot(path=str(output/'02-canvas-home.png'))
            page.locator('#homeGenerateMethod').click();expect(page.locator('#homePrompt')).to_have_value('图片模式的草稿')
            page.locator('#homeTab-agent').click();expect(page.locator('#agentInput')).to_have_value('独立保留的 Agent 草稿')
            def back_home():
                page.locator('#workspaceMenu').click();page.locator('#mainNav [data-view=home]').click();expect(page.locator('#homeCanvasArea')).to_be_visible()
            page.locator('#homeCanvasMethod').click();page.locator('[data-canvas-start=blank]').click();expect(page.locator('#canvasView')).to_be_visible()
            blank=context.request.get(origin+'/api/projects').json()[0];assert blank['nodes']==[]
            page.reload();expect(page.locator('#canvasView')).to_be_visible();back_home()
            page.locator('[data-canvas-start=video]').click();expect(page.locator('#canvasView')).to_be_visible()
            video=context.request.get(origin+'/api/projects').json()[0];assert [n['type'] for n in video['nodes']]==['prompt','video'] and len(video['edges'])==1
            back_home();page.locator('[data-canvas-start=image]').click();expect(page.locator('#canvasView')).to_be_visible()
            image=context.request.get(origin+'/api/projects').json()[0];assert [n['type'] for n in image['nodes']]==['prompt','asset']
            back_home();page.locator('#homeCanvasIdea').fill('春日城市里的一段散步');page.get_by_role('button',name='根据想法新建画布').click();expect(page.locator('#canvasView')).to_be_visible()
            idea=context.request.get(origin+'/api/projects').json()[0];assert len(idea['nodes'])==1 and idea['nodes'][0]['settings']['prompt']=='春日城市里的一段散步'
            back_home();page.locator(f'.home-canvas-grid [data-open-canvas="{project["id"]}"]').click();expect(page.locator('[data-node=ref]')).to_be_visible()
            back_home();assert len(context.request.get(origin+'/api/projects').json())==5
            assert context.request.get(origin+'/api/conversations').json()==[]
            page.set_viewport_size({'width':430,'height':900});page.wait_for_timeout(250);page.screenshot(path=str(output/'03-mobile-canvas.png'))
            assert page.locator('#agentView').evaluate('el=>el.scrollWidth<=el.clientWidth+1')
            page.locator('#homeGenerateMethod').click();page.wait_for_timeout(280);page.screenshot(path=str(output/'04-mobile-generation.png'))
            assert page.locator('.home-mode-tabs').evaluate('el=>el.scrollWidth<=el.clientWidth+1')
            page.locator('#homeTab-video').click();page.locator('#homeParameters .home-preference-trigger').first.click()
            panel=page.locator('.home-preference-panel:popover-open');box=panel.bounding_box()
            assert box['x']>=0 and box['x']+box['width']<=430 and box['y']>=0 and box['y']+box['height']<=900
            panel.get_by_role('button',name='9:16',exact=True).focus();page.keyboard.press('Enter')
            expect(panel.get_by_role('button',name='9:16',exact=True)).to_be_focused()
            page.keyboard.press('ArrowRight');expect(panel.get_by_role('button',name='1:1',exact=True)).to_be_focused()
            page.keyboard.press('Escape');expect(panel).not_to_be_visible()
            page.locator('.home-model-control .home-preference-trigger').click()
            panel=page.locator('.home-preference-panel:popover-open');expect(panel).to_be_visible()
            panel.get_by_role('button').first.click();expect(panel).not_to_be_visible()
            page.locator('#homeCanvasMethod').focus();page.keyboard.press('ArrowRight');expect(page.locator('#homeCanvasMethod')).to_have_attribute('aria-selected','true')
            page.emulate_media(reduced_motion='reduce');assert page.locator('.home-method-thumb').evaluate('el=>getComputedStyle(el).transitionDuration')=='0s'
            assert not paid,paid
            assert not errors,errors
            browser.close()
        print('PASS: single Home entry, two creation methods, draft retention, blank/video/image/idea canvases, existing canvas opening, independent histories, URL reload, mobile, keyboard and reduced motion; zero generation requests')
    finally:
        server.should_exit=True;thread.join(5);CanvasGroupsTests.tearDownClass()

if __name__=='__main__':main()
