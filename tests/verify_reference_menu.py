"""Reference menu interactions against an isolated database; no model requests."""
from pathlib import Path
import socket
import sys
import threading
import time
import io
import wave
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from test_canvas_groups import CanvasGroupsTests
from verify_canvas_workspace import drawing
from auth_service import hash_password
import uvicorn
from playwright.sync_api import sync_playwright,expect


def main():
    CanvasGroupsTests.setUpClass();host=CanvasGroupsTests.host
    output=ROOT/'outputs/reference-menu-verification';output.mkdir(parents=True,exist_ok=True)
    with host.connect() as db:db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password('reference-test'),'admin'))
    images=[host.store_uploaded_asset(drawing(),name+'.png','') for name in ['平康坊外','平康坊内','伴舞团','逛窑子版','琵琶女','平康坊侍从','舞女月媚儿','富商甲']]
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as stream:
        stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(bytes(96000))
    audio=host.store_uploaded_asset(buffer.getvalue(),'刘三.wav','')
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
    origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(host.app,log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
            context=browser.new_context(viewport={'width':1440,'height':1000})
            assert context.request.post(origin+'/api/auth/login',headers={'Origin':origin},data={'username':'admin','password':'reference-test','remember':False}).ok
            nodes=[{'id':f'image{i}','type':'asset','title':name,'assetId':asset['id'],'x':120+i*30,'y':80+i*20} for i,(name,asset) in enumerate(zip(['平康坊外','平康坊内','伴舞团','逛窑子版','琵琶女','平康坊侍从','舞女月媚儿','富商甲'],images))]
            nodes.extend([{'id':'voice','type':'audio','title':'刘三','assetId':audio['id'],'x':100,'y':400},{'id':'target','type':'video','title':'平康坊 · 夜','x':740,'y':210,'settings':{'prompt':'盛唐，开元一天宝语境，长安平康坊，夜。'}},{'id':'text','type':'prompt','title':'场景设定','x':1100,'y':150,'settings':{'prompt':'长安，夜景。'}}])
            response=context.request.post(origin+'/api/projects',headers={'Origin':origin},data={'name':'节点详情 · 素材引用','nodes':nodes,'edges':[{'from':node['id'],'to':'target'} for node in nodes[:9]],'viewport':{'x':0,'y':0,'zoom':1}})
            assert response.ok,response.text()
            project_id=response.json()['id'];page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin+'/?project='+project_id)
            page.locator('[data-node="target"] .frame-name').click()
            editor=page.locator('#promptInput + .mention-editor');editor.fill('盛唐，长安平康坊，夜。 @')
            menu=page.locator('.asset-mention-menu:not([hidden])');expect(menu).to_be_visible()
            expect(menu.locator('.mention-linked button')).to_have_count(9)
            expect(menu.locator('[data-mention-kind]')).to_have_count(5)
            assert menu.bounding_box()['width']<260
            page.screenshot(path=str(output/'01-linked-references.png'))
            scroll=menu.locator('.mention-menu-scroll')
            scroll.evaluate('(el)=>el.scrollTop=el.scrollHeight')
            expect(menu.locator('[data-mention-kind="asset"]')).to_be_in_viewport()
            page.screenshot(path=str(output/'02-reference-categories.png'))
            menu.locator('[data-mention-kind="audio"]').click()
            expect(menu.locator('.mention-flyout')).to_be_visible()
            expect(menu.locator('[data-mention-index]')).to_have_count(1)
            menu.locator('[data-mention-index]').click()
            expect(editor.locator('.mention-token')).to_have_count(1)
            expect(page.locator('#selectedAssets .reference-asset')).to_have_count(9)
            expect(page.locator('#saveState')).to_have_text('已保存')
            graph=context.request.get(origin+'/api/projects/'+project_id).json()
            assert len(graph['edges'])==9
            target=next(n for n in graph['nodes'] if n['id']=='target')
            assert target['settings']['mentions'][0]['asset_id']==audio['id']
            # Searching preserves focus, supports Chinese names, and filters globally.
            editor.press('End');editor.press_sequentially(' @')
            expect(menu).to_be_visible();menu.locator('input').fill('平康坊外')
            expect(menu.locator('[data-mention-index]')).to_have_count(1)
            expect(menu.locator('input')).to_be_focused()
            menu.locator('input').press('Enter')
            expect(editor.locator('.mention-token')).to_have_count(2)
            expect(page.locator('#selectedAssets .reference-asset')).to_have_count(9)
            editor.press('End');editor.press_sequentially(' @');expect(menu).to_be_visible()
            menu.locator('input').fill('不存在的参考素材')
            expect(menu.locator('.mention-empty')).to_have_text('没有匹配素材')
            menu.locator('input').press('Escape');expect(menu).to_have_count(0)
            # A text reference gets a real token, and survives a reload with media numbering.
            editor.press('Backspace');editor.press_sequentially('@');expect(menu).to_be_visible()
            menu.locator('[data-mention-kind="text"]').click()
            expect(menu.locator('[data-mention-index]')).to_have_count(1)
            menu.locator('[data-mention-index]').click();expect(editor.locator('.mention-token')).to_have_count(3)
            expect(page.locator('#saveState')).to_have_text('已保存')
            page.reload();page.locator('[data-node="target"] .frame-name').click()
            expect(editor.locator('.mention-token')).to_have_count(3)
            expect(page.locator('#selectedAssets .reference-asset')).to_have_count(9)
            editor.press('End');editor.press_sequentially(' @');expect(menu).to_be_visible()
            editor.press('ArrowUp');editor.press('Enter')
            expect(menu.locator('[data-mention-kind="asset"]')).to_have_attribute('aria-expanded','true')
            expect(menu.locator('[data-mention-index]')).to_have_count(9)
            editor.press('ArrowLeft');expect(menu.locator('.mention-flyout')).to_have_count(0)
            editor.press('Escape');expect(menu).to_have_count(0)
            # Portrait images must fit the reference tile rather than growing a grid
            # track to their intrinsic height and painting over the prompt below.
            portrait_data=io.BytesIO();Image.new('RGB',(180,420),'#9eafb9').save(portrait_data,format='PNG')
            portrait=host.store_uploaded_asset(portrait_data.getvalue(),'竖版参考.png','')
            wide_data=io.BytesIO();Image.new('RGB',(1200,180),'#bcae9b').save(wide_data,format='PNG')
            wide=host.store_uploaded_asset(wide_data.getvalue(),'横版参考.png','')
            response=context.request.post(origin+'/api/projects',headers={'Origin':origin},data={'name':'图片引用尺寸验证','nodes':[{'id':'picture','type':'asset','title':'图片素材','x':500,'y':100,'image_settings':{'reference_asset_ids':[portrait['id'],wide['id']]+[a['id'] for a in images]}}],'edges':[],'viewport':{'x':0,'y':0,'zoom':1}})
            assert response.ok,response.text()
            page.goto(origin+'/?project='+response.json()['id'])
            page.locator('[data-node="picture"] .frame-name').click()
            composer=page.locator('[data-node-composer="picture"]');cards=composer.locator('.inline-reference-list > div')
            expect(cards).to_have_count(10)
            for width in [1440,1024,430]:
                page.set_viewport_size({'width':width,'height':1000})
                page.wait_for_timeout(150)
                metrics=composer.evaluate('''el=>{
                    const list=el.querySelector('.inline-reference-list'),note=el.querySelector('.mention-editor');
                    const box=element=>{const r=element.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right};};
                    return {list:box(list),note:box(note),cards:[...list.children].map(card=>({card:box(card),image:box(card.querySelector('img'))}))};
                }''')
                for item in metrics['cards']:
                    card,img=item['card'],item['image']
                    assert abs(card['width']-card['height'])<1,(width,item)
                    assert img['width']<=card['width']+1 and img['height']<=card['height']+1,(width,item)
                    assert img['bottom']<=card['bottom']+1,(width,item)
                assert metrics['note']['y']>=metrics['list']['bottom']-1,metrics
                if width==1440:page.screenshot(path=str(output/'03-image-reference-tiles.png'))
            assert not errors,errors
            print('PASS: linked references, scrolling, five categories, audio/text selection, search, empty state, no duplicate edges/cards, persistence; no browser errors')
            browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=10);listener.close();CanvasGroupsTests.tearDownClass()


if __name__=='__main__':main()
