"""Exercise pending canvas references in Chrome with local data and fake jobs."""
from pathlib import Path
import socket
import threading
import time
from test_canvas_groups import CanvasGroupsTests
from verify_canvas_workspace import drawing
from auth_service import hash_password
from playwright.sync_api import sync_playwright, expect
import uvicorn


def main():
    CanvasGroupsTests.setUpClass();host=CanvasGroupsTests.host
    output=Path(__file__).resolve().parents[1]/'outputs/studio-canvas-verification';output.mkdir(parents=True,exist_ok=True)
    with host.connect() as db:db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password('reference-test'),'admin'))
    actor=host.store_uploaded_asset(drawing(),'小林.png','')
    scene=host.store_uploaded_asset(drawing(),'车站.png','')
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
    origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(host.app,log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
            context=browser.new_context(viewport={'width':1600,'height':1000})
            headers={'Origin':origin}
            assert context.request.post(origin+'/api/auth/login',headers=headers,data={'username':'admin','password':'reference-test'}).ok
            def mention(nid,name):return {'token':'@'+name,'node_id':nid,'asset_id':'','kind':'image','name':name}
            def meta(role,inputs=[]):return {'runId':'reference-demo','role':role,'inputs':[{'nodeId':nid,'kind':'image','role':'参考'} for nid in inputs],'mode':'reference'}
            nodes=[{'id':'hero','type':'asset','title':'小林','x':100,'y':100,'image_settings':{'prompt':'灰色外套的年轻人'},'mujian':meta('reference')},
                   {'id':'scene','type':'asset','title':'车站场景','x':540,'y':100,'image_settings':{'prompt':'@小林 站在车站前','mentions':[mention('hero','小林')]},'mujian':meta('reference',['hero'])},
                   {'id':'variant','type':'asset','title':'雨衣造型','x':100,'y':500,'image_settings':{'prompt':'雨衣造型'},'mujian':meta('reference')},
                   {'id':'clip','type':'video','title':'第 1 集 · 片段 01','x':1000,'y':100,'settings':{'prompt':'@小林 在 @车站场景 推门，随后切换到人物近景。','duration':10,'mentions':[mention('hero','小林'),mention('scene','车站场景')]},'mujian':meta('shot',['hero','scene'])}]
            response=context.request.post(origin+'/api/projects',headers=headers,data={'name':'多分镜与待生成引用','nodes':nodes,'edges':[{'from':'hero','to':'scene'},{'from':'hero','to':'clip','hidden':True},{'from':'scene','to':'clip','hidden':True}],'viewport':{'x':0,'y':0,'zoom':.85}})
            assert response.ok,response.text()
            pid=response.json()['id'];page=context.new_page();errors=[];submissions=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/api/model-registry',lambda route:route.fulfill(json={'models':[{'id':'fixture-image','kind':'image','name':'测试图片模型','model_id':'fixture-image','api_key_set':True,'is_default':True}]}))
            def generate(route):
                submissions.append(route.request.post_data_json)
                route.fulfill(json={'id':'fixture-scene-job','status':'succeeded','asset':scene,'assets':[scene]})
            page.route('**/api/images/generations',generate)
            page.route('**/api/images/generations/fixture-scene-job',lambda route:route.fulfill(json={'id':'fixture-scene-job','status':'succeeded','asset':scene,'assets':[scene]}))
            page.goto(origin+'/?project='+pid)
            page.locator('[data-node="clip"] .frame-name').click()
            editor=page.locator('#promptInput + .mention-editor')
            expect(editor.locator('.mention-token[data-pending="true"]')).to_have_count(2)
            expect(editor).to_contain_text('车站场景')
            expect(page.locator('.links [data-edge-to="clip"]')).to_have_count(0)
            page.screenshot(path=str(output/'01-pending-video-references.png'),animations='disabled')
            page.locator('#chooseReferenceBtn').click()
            menu=page.locator('.asset-mention-menu:not([hidden])');menu.locator('input').fill('雨衣造型')
            expect(menu.locator('[data-mention-index]')).to_contain_text('待生成')
            menu.locator('[data-mention-index]').click()
            expect(editor.locator('.mention-token[data-pending="true"]')).to_have_count(3)
            expect(page.locator('#saveState')).to_have_text('已保存')
            page.reload();page.locator('[data-node="clip"] .frame-name').click()
            expect(editor.locator('.mention-token[data-pending="true"]')).to_have_count(3)
            page.locator('[data-remove-reference-node="variant"]').click(force=True)
            expect(editor.locator('.mention-token')).to_have_count(2)
            expect(page.locator('#saveState')).to_have_text('已保存')
            graph=context.request.get(origin+'/api/projects/'+pid).json()
            assert not any(e['from']=='variant' for e in graph['edges'])
            page.locator('#editorClose').click();page.locator('[data-node="scene"] .frame-name').click()
            composer=page.locator('[data-node-composer="scene"]')
            expect(composer.locator('.mention-token[data-pending="true"]')).to_have_count(1)
            composer.locator('.inline-generate-button').click()
            expect(composer.locator('.inline-generation-status')).to_contain_text('请先生成前置素材')
            assert not submissions
            # Complete the character in persisted state, then return to the scene.
            graph=context.request.get(origin+'/api/projects/'+pid).json()
            parent=next(n for n in graph['nodes'] if n['id']=='hero');parent.update(assetId=actor['id'],assetUrl=actor['url'])
            response=context.request.put(origin+'/api/projects/'+pid,headers=headers,data=graph);assert response.ok,response.text()
            page.reload();page.locator('#editorClose').click();page.locator('[data-node="scene"] .frame-name').click()
            expect(composer.locator('.mention-token[data-pending="false"] img')).to_have_count(1)
            composer.locator('.inline-generate-button').click()
            expect(composer.locator('.inline-generation-status')).to_contain_text('已生成')
            assert len(submissions)==1 and submissions[0]['reference_asset_ids']==[actor['id']]
            page.locator('#editorClose').click();page.locator('[data-node="clip"] .frame-name').click()
            expect(editor.locator('.mention-token[data-pending="false"] img')).to_have_count(2)
            expect(page.locator('.links [data-edge-to="clip"]')).to_have_count(0)
            page.screenshot(path=str(output/'02-resolved-video-references.png'),animations='disabled')
            # Removal updates all three: token, visible reference and planned input.
            page.locator('[data-remove-asset="'+scene['id']+'"]').click(force=True)
            expect(editor.locator('.mention-token')).to_have_count(1)
            expect(page.locator('#saveState')).to_have_text('已保存')
            graph=context.request.get(origin+'/api/projects/'+pid).json();video=next(n for n in graph['nodes'] if n['id']=='clip')
            assert video['mujian']['inputs']==[{'nodeId':'hero','kind':'image','role':'参考'}]
            assert not any(e['from']=='scene' and e['to']=='clip' for e in graph['edges'])
            # Render the actual generated layout, including asset variants and
            # multiple shots packed into a single duration-compatible clip.
            from studio_graph import build_graph, digest
            from test_studio_clip_planning import shot
            catalog=[dict(key=k,type=t,name=name,variant=v,facts='原文',design='暖光',anchors=[],sources=[]) for k,t,name,v in
                     [('hero','character','小林','默认'),('coat','character','小林','雨衣'),('scene','scene','车站','默认'),('night','scene','车站','夜晚')]]
            run={'id':'layout-demo','title':'Agent 分组与片段编排','preferences':{'aspect_ratio':'16:9','models':{},'studio_video_mode':'reference'},
                 'studio':{'brief':{'episode_count':2},'catalog':catalog,
                           'documents':{f'episode-{ep}':{'status':'current','content':f'第 {ep} 集剧本','version':1} for ep in [1,2]},
                           'extractions':{str(ep):{'source_digest':digest(f'第 {ep} 集剧本'),'shots':[shot(f'ep{ep}-a',7.5),shot(f'ep{ep}-b',6)]} for ep in [1,2]}}}
            layout=build_graph(run,capabilities={'durations':[5,10,15],'max_images':9})
            response=context.request.post(origin+'/api/projects',headers=headers,data=layout);assert response.ok,response.text()
            page.goto(origin+'/?project='+response.json()['id'])
            page.locator('#editorClose').click()
            expect(page.locator('.node')).to_have_count(len(layout['nodes']))
            page.screenshot(path=str(output/'03-generated-canvas-overview.png'),animations='disabled')
            assert not errors,errors
            print('PASS: pending named chips, @ selection, persistence, hidden video wires, blocked premature submission, completed media resolution and removal; no paid requests')
            browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=10);listener.close();CanvasGroupsTests.tearDownClass()


if __name__=='__main__':main()
