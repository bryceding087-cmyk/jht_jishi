"""Four home creation modes with provider-free HTTP stubs and a disposable DB."""
from pathlib import Path
import json
import socket
import sys
import threading
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_agent_runtime import AgentRuntimeTests
from auth_service import hash_password
import uvicorn
from playwright.sync_api import sync_playwright,expect

def main():
    AgentRuntimeTests.setUpClass();fixture=AgentRuntimeTests();fixture.setUp();host=fixture.host
    password='home-composer-test'
    with host.connect() as db:db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password(password),'admin'))
    project=host.create_project(host.Project(name='春日影像',nodes=[]))
    assets={kind:fixture.add_media(kind,'首页生成结果',project['id']) for kind in ['image','audio','video']}
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128);origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(host.app,log_level='error'));thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    output=ROOT/'outputs/home-composer-verification';output.mkdir(parents=True,exist_ok=True)
    calls=[]
    prompts={'image':'清晨的城市，玻璃幕墙反射柔和阳光','video':'雨滴落在玻璃上，镜头缓缓推进','audio':'请朗读：让创意成为作品'}
    try:
        while not server.started:time.sleep(.05)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
            context=browser.new_context(viewport={'width':1512,'height':1000})
            assert context.request.post(origin+'/api/auth/login',headers={'Origin':origin},data={'username':'admin','password':password,'remember':False}).ok
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            def registry(route):
                data=route.fetch().json();data['models']=[{'id':'home-'+kind,'kind':kind,'name':{'image':'Seedream 图片模型','video':'Seedance 视频模型','audio':'Seed Audio 音频模型'}[kind],'model_id':'test-'+kind,'api_key_set':True,'is_default':True,'capabilities':{'aspect_ratios':['16:9','9:16','1:1','4:3','3:4','21:9'],'durations':list(range(4,16)),'resolutions':['1K','2K','4K'] if kind=='image' else ['720p','1080p']}} for kind in ['image','video','audio']];route.fulfill(json=data)
            page.route('**/api/model-registry',registry)
            polls={}
            def generation(route):
                request=route.request
                kind='image' if '/images/' in request.url else 'audio' if '/audio/' in request.url else 'video'
                if request.method=='POST':
                    body=request.post_data_json;calls.append((kind,body))
                    {'image':host.ImageGenerationRequest,'audio':host.AudioGenerationRequest,'video':host.Task}[kind](**body)
                    host.save({'image':'image_task','audio':'audio_task','video':'task'}[kind],{'id':'home-result-'+kind,'status':'queued','project_id':body['project_id'],'params':body,'created_at':time.time()})
                    route.fulfill(status=202,json={'id':'home-result-'+kind,'status':'queued'})
                else:
                    polls[kind]=polls.get(kind,0)+1
                    if polls[kind]==1:
                        route.fulfill(json={'id':'home-result-'+kind,'status':'running'});return
                    record_kind={'image':'image_task','audio':'audio_task','video':'task'}[kind]
                    task=host.save(record_kind,{**host.get(record_kind,'home-result-'+kind),'status':'succeeded' if kind=='image' else 'done',**({'video_url':assets[kind]['url']} if kind=='video' else {'asset':assets[kind]}),**({'assets':[dict(assets[kind],id=f'image-result-{i}') for i in range(3)]} if kind=='image' else {})})
                    route.fulfill(json=task)
            page.route('**/api/images/generations**',generation);page.route('**/api/audio/generations**',generation)
            page.route('**/api/tasks',lambda route:generation(route) if route.request.method=='POST' else route.continue_())
            page.route('**/api/tasks/home-result-video',generation)
            page.goto(origin+'/');expect(page.locator('#agentView')).to_be_visible()
            def switch_mode(kind):
                tab=page.locator('#homeTab-'+kind)
                if tab.is_visible():tab.click()
                else:
                    page.locator('.home-conversation-mode button').click()
                    page.get_by_role('dialog',name='生成模式',exact=True).get_by_role('button',name={'agent':'Agent','image':'图片生成','video':'视频生成','audio':'音频生成'}[kind],exact=True).click()
            expect(page.get_by_role('tablist',name='首页创作功能').get_by_role('tab')).to_have_count(4)
            gradient=page.locator('.agent-composer-wrap')
            assert gradient.evaluate('el=>getComputedStyle(el,"::before").animationName')=='home-gradient-flow'
            start=gradient.evaluate('el=>getComputedStyle(el,"::before").backgroundPosition')
            page.wait_for_function('start=>getComputedStyle(document.querySelector(".agent-composer-wrap"),"::before").backgroundPosition!==start',arg=start,timeout=3000)
            page.locator('.home-agent-parameters button').click()
            page.get_by_role('dialog',name='选择比例与目标时长').get_by_role('button',name='9:16',exact=True).click()
            page.locator('.home-agent-parameters button').click()
            page.get_by_role('dialog',name='选择比例与目标时长').get_by_role('button',name='30s',exact=True).click()
            expect(page.locator('.home-agent-parameters button')).to_contain_text('9:16 · 30s')
            page.screenshot(path=str(output/'01-agent-home.png'))
            agent=page.locator('#agentInput + .mention-editor');agent.fill('Agent 的创作草稿')
            for kind in ['image','video','audio']:
                switch_mode(kind);editor=page.locator('#homePrompt + .mention-editor');expect(editor).to_be_visible();editor.fill(prompts[kind])
                if kind=='image':
                    editor.fill('参考 @');mention=page.locator('.asset-mention-menu:not([hidden])');mention.locator('input').fill('首页生成结果');editor.press('Enter');expect(page.locator('.home-reference')).to_have_count(1)
                    editor.fill(prompts[kind])
                    page.locator('#homeParameters .home-preference-trigger').click()
                    panel=page.get_by_role('dialog',name='选择比例与选择分辨率与选择生成数量')
                    panel.get_by_role('button',name='3:4',exact=True).click();panel.get_by_role('button',name='4K',exact=True).click();panel.get_by_role('button',name='3 张',exact=True).click()
                    page.wait_for_timeout(200);page.screenshot(path=str(output/'08-image-options.png'));page.keyboard.press('Escape')
                if kind=='video':
                    page.locator('#homeParameters .home-preference-trigger').nth(0).click()
                    panel=page.get_by_role('dialog',name='选择比例与选择分辨率')
                    panel.get_by_role('button',name='4:3',exact=True).click()
                    panel.get_by_role('button',name='1080p',exact=True).click()
                    page.screenshot(path=str(output/'06-video-preferences.png'))
                    panel.press('Escape');expect(panel).not_to_be_visible()
                    page.locator('#homeParameters .home-preference-trigger').nth(1).click()
                    duration_panel=page.get_by_role('dialog',name='选择时长')
                    expect(duration_panel.get_by_role('button')).to_have_count(12)
                    duration_panel.get_by_role('button',name='13 秒',exact=True).click()
                    page.wait_for_timeout(200);page.screenshot(path=str(output/'09-video-durations.png'))
                    page.keyboard.press('Escape')
                if kind=='audio':
                    page.locator('#homeParameters .home-preference-trigger').nth(0).click()
                    page.get_by_role('dialog',name='音频类型',exact=True).get_by_role('button',name='配音',exact=True).click()
                    page.locator('#homeParameters .home-preference-trigger').nth(1).click()
                    panel=page.get_by_role('dialog',name='音频格式与配音语速')
                    panel.get_by_role('button',name='MP3',exact=True).click();panel.get_by_role('button',name='1.25×',exact=True).click()
                    page.screenshot(path=str(output/'07-audio-preferences.png'));page.keyboard.press('Escape')
                page.screenshot(path=str(output/f'02-{kind}-composer.png'))
                page.locator('.home-send').click()
                expect(page.locator('.home-generation-placeholder')).to_be_visible()
                expect(page.locator('.home-results article:not([hidden]) .home-result-summary')).to_contain_text(prompts[kind])
                expect(page.locator('.home-results article:not([hidden]) .home-result-date')).to_have_text('今天')
                expect(page.locator('.home-mode-tabs')).to_be_hidden()
                assert '普通队列' not in page.locator('#agentView').inner_text()
                composer=page.locator('.agent-composer-wrap').bounding_box();assert composer['y']+composer['height']>=970
                if kind=='image':
                    page.wait_for_timeout(300);page.screenshot(path=str(output/'10-generation-conversation-pending.png'))
                    page.locator('#mainNav [data-view=home]').click()
                    expect(page.locator('#homeTab-agent')).to_have_attribute('aria-selected','true')
                    expect(page.locator('.home-results')).to_be_hidden()
                    page.wait_for_timeout(2000)
                    expect(page.locator('#agentView')).not_to_have_class(__import__('re').compile('has-media-conversation'))
                    expect(page.locator('.home-mode-tabs')).to_be_visible()
                    expect(page.locator('#agentInput')).to_have_value('')
                    page.locator(f'#projectList [data-conversation="{calls[0][1]["project_id"]}"]').click()
                preview=page.locator('.home-results '+{'image':'.home-image-results img','video':'video','audio':'audio'}[kind]);expect(preview.first).to_be_visible()
                if kind=='image':expect(preview).to_have_count(3)
                page.locator('.home-results article:not([hidden]) [data-home-details]').click();expect(page.locator('.home-result-details')).to_be_visible()
                expect(page.locator('.home-result-details')).to_contain_text(prompts[kind]);page.get_by_role('button',name='关闭详情',exact=True).click()
                page.screenshot(path=str(output/f'03-{kind}-result.png'))
            assert [kind for kind,_ in calls]==['image','video','audio'],calls
            assert calls[0][1]['reference_asset_ids']==[assets['image']['id']]
            assert calls[0][1]['size']=='4K' and calls[0][1]['aspect_ratio']=='3:4' and calls[0][1]['count']==3
            assert calls[1][1]['aspect_ratio']=='4:3' and calls[1][1]['duration']==13
            assert calls[1][1]['resolution']=='1080p'
            assert calls[2][1]['mode']=='voice'
            assert calls[2][1]['audio_config']=={'format':'mp3','speech_rate':25}
            assert len(context.request.get(origin+'/api/projects').json())==1
            assert len(context.request.get(origin+'/api/conversations').json())==3
            generated=host.raw_record('conversation',calls[1][1]['project_id'])
            assert generated['nodes'][0]['settings']['prompt']==prompts['video']
            switch_mode('image');expect(page.locator('#homePrompt')).to_have_value(prompts['image']);expect(page.locator('.home-results .home-image-results img').first).to_be_visible();expect(page.locator('.home-results video')).to_be_hidden();expect(page.locator('.home-results audio')).to_be_hidden()
            expect(page.locator('#homeParameters .home-preference-trigger')).to_contain_text('3:4 · 4K · 3 张')
            switch_mode('agent');expect(page.locator('#agentInput')).to_have_value('');expect(page.locator('.home-results')).to_be_hidden()
            switch_mode('audio');page.set_viewport_size({'width':430,'height':900});page.wait_for_timeout(500);page.screenshot(path=str(output/'04-mobile.png'))
            expect(page.locator('.home-preference-panel:popover-open')).to_have_count(0)
            assert page.locator('#agentView').evaluate('el=>el.scrollWidth<=el.clientWidth+1')
            assert page.locator('.home-mode-tabs').evaluate('el=>el.scrollWidth<=el.clientWidth+1')
            assert page.locator('.home-send').bounding_box()['x']+34<=430
            assert page.locator('.home-send').bounding_box()['x']+36>=400
            page.emulate_media(reduced_motion='reduce');assert page.locator('#homeMediaComposer').evaluate('el=>getComputedStyle(el).animationName')=='none'
            assert gradient.evaluate('el=>getComputedStyle(el,"::before").animationName')=='none'
            expect(page.locator('#projectList [data-conversation]')).to_have_count(3)
            expect(page.locator('#projectList [data-project]')).to_have_count(1)
            conversations=context.request.get(origin+'/api/conversations').json()
            video_conversation=next(c for c in conversations if c['kind']=='video')
            page.set_viewport_size({'width':1512,'height':1000})
            page.locator('#projectList [data-project]').click();expect(page.locator('#canvasView')).to_be_visible()
            page.locator('#workspaceMenu').click()
            page.locator(f'#projectList [data-conversation="{video_conversation["id"]}"]').click()
            expect(page.locator('#homeTab-video')).to_have_attribute('aria-selected','true');expect(page.locator('.home-results video')).to_be_visible()
            page.reload();page.locator(f'#projectList [data-conversation="{video_conversation["id"]}"]').click();expect(page.locator('.home-results video')).to_be_visible()
            assert len(context.request.get(origin+'/api/projects').json())==1
            page.screenshot(path=str(output/'05-separate-conversation-history.png'))
            image_conversation=next(c for c in conversations if c['kind']=='image')
            page.locator(f'#projectList [data-conversation="{image_conversation["id"]}"]').click()
            active_result=page.locator('.home-results article:not([hidden])')
            expect(active_result.locator('.home-image-results img')).to_have_count(3)
            expect(active_result.locator('.home-result-references img')).to_have_count(1)
            expect(active_result.locator('.home-result-meta')).to_contain_text('3:4')
            expect(active_result.locator('.home-result-meta')).to_contain_text('4K')
            expect(active_result.locator('.home-result-meta')).to_contain_text('3 张')
            page.wait_for_timeout(350);page.screenshot(path=str(output/'11-restored-image-conversation.png'))
            # Home must leave the conversation layout and all media tabs must start clean.
            page.locator('#mainNav [data-view=home]').click()
            expect(page.locator('#agentView')).to_have_class(__import__('re').compile('home-landing'))
            expect(page.locator('#agentView')).not_to_have_class(__import__('re').compile('has-media-conversation'))
            expect(page.locator('#homeTab-agent')).to_have_attribute('aria-selected','true')
            expect(page.locator('#agentInput')).to_have_value('')
            expect(page.locator('.home-mode-tabs')).to_be_visible();expect(page.locator('.home-results')).to_be_hidden()
            expect(page.locator('.home-recent')).to_be_visible()
            expect(page.locator('#projectList [data-conversation].active')).to_have_count(0)
            page.wait_for_timeout(450);page.screenshot(path=str(output/'12-return-to-home.png'))
            for mode in ['image','video','audio']:
                page.locator('#homeTab-'+mode).click();expect(page.locator('.home-results')).to_be_hidden()
                expect(page.locator('#homePrompt')).to_have_value('');expect(page.locator('.home-reference')).to_have_count(0)
            page.locator(f'#projectList [data-conversation="{image_conversation["id"]}"]').click()
            expect(page.locator('.home-results article:not([hidden]) .home-image-results img')).to_have_count(3)
            expect(page.locator('.home-mode-tabs')).to_be_hidden()
            # The shared Agent must retain its floating canvas presentation after a media mode.
            page.set_viewport_size({'width':1512,'height':1000});page.goto(origin+'/?project='+project['id']);page.locator('#workspaceAgent').click()
            expect(page.locator('#canvasAgentPanel #agentComposer')).to_be_visible();expect(page.locator('#canvasAgentPanel .home-mode-tabs')).to_be_hidden()
            assert not errors,errors
            browser.close()
        print('PASS: four modes, drafts and references, validated image/video/audio requests, async results, independent conversation storage, mobile, reduced motion and canvas Agent')
    finally:
        server.should_exit=True;thread.join(5);fixture.doCleanups();AgentRuntimeTests.tearDownClass()

if __name__=='__main__':main()
