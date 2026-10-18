"""Exercise canvas groups in Chrome against a disposable project database."""
from pathlib import Path
import sys
import socket
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from test_canvas_groups import CanvasGroupsTests
from verify_canvas_workspace import drawing
from auth_service import hash_password
import uvicorn
from playwright.sync_api import sync_playwright,expect


def main():
    CanvasGroupsTests.setUpClass();host=CanvasGroupsTests.host
    output=ROOT/'outputs/canvas-group-verification';output.mkdir(parents=True,exist_ok=True)
    with host.connect() as db:db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password('canvas-group-test'),'admin'))
    image=host.store_uploaded_asset(drawing(),'城市视觉参考.png','')
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
            context=browser.new_context(viewport={'width':1440,'height':960})
            assert context.request.post(origin+'/api/auth/login',headers={'Origin':origin},data={'username':'admin','password':'canvas-group-test','remember':False}).ok
            response=context.request.post(origin+'/api/projects',headers={'Origin':origin},data={
                'name':'分组工作流 · 品牌宣传片','nodes':[
                    {'id':'visual','type':'asset','title':'城市视觉参考','assetId':image['id'],'x':150,'y':240,'mediaWidth':960,'mediaHeight':540},
                    {'id':'prompt','type':'prompt','title':'镜头与运动设计','x':540,'y':240,'settings':{'prompt':'晨光穿过城市建筑，镜头缓缓向前推进。'}},
                    {'id':'outside','type':'prompt','title':'独立创意','x':1030,'y':240,'settings':{'prompt':'另一组创意。'}},
                ],'edges':[{'from':'visual','to':'prompt'}],'viewport':{'x':0,'y':0,'zoom':1}})
            assert response.ok,response.text()
            project_id=response.json()['id'];page=context.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.set_default_timeout(10000)
            def graph():return context.request.get(origin+'/api/projects/'+project_id).json()
            def saved():expect(page.locator('#saveState')).to_have_text('已保存')
            def shot(name):page.screenshot(path=str(output/name))
            try:
                page.goto(origin+'/?project='+project_id);expect(page.locator('.node')).to_have_count(3)
                page.mouse.click(1400,800)
                page.mouse.move(105,190);page.mouse.down();page.mouse.move(925,535,steps=12);page.mouse.up()
                expect(page.locator('.node.selected')).to_have_count(2)
                expect(page.get_by_role('toolbar',name='多选与分组操作')).to_be_visible()
                shot('01-multi-selection.png')
                page.get_by_role('button',name='打组',exact=True).click()
                group=page.locator('.canvas-group');expect(group).to_have_count(1);expect(group).to_have_class('canvas-group is-selected')
                saved();original=graph();assert original['groups'][0]['node_ids']==['visual','prompt']
                assert original['edges']==[{'from':'visual','to':'prompt'}]
                # Drag padding, not child content; both members translate by the same amount.
                box=group.bounding_box();page.mouse.move(box['x']+10,box['y']+10);page.mouse.down();page.mouse.move(box['x']+70,box['y']+50,steps=8);page.mouse.up()
                saved();moved=graph();positions={node['id']:(node['x'],node['y']) for node in moved['nodes']}
                assert positions=={'visual':(210,280),'prompt':(600,280),'outside':(1030,240)},positions
                # Group naming is inline, with no creation modal.
                group.locator('.canvas-group-name').dblclick()
                page.get_by_role('textbox',name='组名称',exact=True).fill('第一幕 · 城市晨光')
                colors=page.get_by_role('textbox',name='组名称',exact=True).evaluate('el=>({background:getComputedStyle(el).backgroundColor,color:getComputedStyle(el).color,border:getComputedStyle(el).borderTopColor})')
                assert colors=={'background':'rgb(44, 44, 44)','color':'rgb(242, 242, 242)','border':'rgb(146, 146, 146)'},colors
                page.mouse.click(1400,800)
                expect(group.locator('.canvas-group-name')).to_have_text('第一幕 · 城市晨光')
                group.locator('.canvas-group-name').click()
                page.get_by_role('button',name='组颜色',exact=True).click()
                page.get_by_role('radio',name='蓝色',exact=True).click();saved()
                assert graph()['groups'][0]['color']=='#4285f4'
                page.keyboard.press('Control+z');saved();assert graph()['groups'][0]['color']=='#999999'
                page.keyboard.press('Control+Shift+z');saved();assert graph()['groups'][0]['color']=='#4285f4'
                # Resizing only changes the frame, retaining member locations and containment.
                before=group.bounding_box();handle=group.locator('.canvas-group-handle.se').bounding_box()
                page.mouse.move(handle['x']+3,handle['y']+3);page.mouse.down();page.mouse.move(handle['x']+73,handle['y']+53,steps=8);page.mouse.up()
                saved();after=group.bounding_box();assert after['width']>before['width']+60,(before,after,graph()['groups'])
                assert {node['id']:(node['x'],node['y']) for node in graph()['nodes']}==positions
                page.get_by_role('button',name='组内排列',exact=True).click()
                page.get_by_role('button',name='垂直排列',exact=True).click();saved()
                arranged=graph();a,b=[next(n for n in arranged['nodes'] if n['id']==key) for key in ['visual','prompt']]
                assert a['x']==b['x'] and b['y']>a['y']+170
                page.keyboard.press('Control+z');saved()
                assert {node['id']:(node['x'],node['y']) for node in graph()['nodes']}==positions
                shot('02-group-selected.png')
                # A child remains individually editable and draggable.
                page.locator('[data-node="prompt"] .frame-name').click()
                expect(page.get_by_role('toolbar',name='多选与分组操作')).to_be_hidden()
                title=page.locator('[data-node="prompt"] .frame-name').bounding_box()
                page.mouse.move(title['x']+50,title['y']+8);page.mouse.down();page.mouse.move(title['x']+70,title['y']+18,steps=6);page.mouse.up();saved()
                child=next(n for n in graph()['nodes'] if n['id']=='prompt')
                assert (child['x'],child['y'])==(620,290)
                assert next(n for n in graph()['nodes'] if n['id']=='visual')['x']==210
                # Keyboard ungroup preserves all content and is itself undoable.
                group.locator('.canvas-group-name').click();page.keyboard.press('Control+Shift+g')
                expect(group).to_have_count(0);saved();assert len(graph()['nodes'])==3 and not graph()['groups']
                page.keyboard.press('Control+g');expect(group).to_have_count(1);saved()
                page.keyboard.press('Control+z');expect(group).to_have_count(0);saved()
                page.keyboard.press('Control+z');expect(group).to_have_count(1);saved()
                # Whole-group deletion restores its nodes, edges, membership and metadata on undo.
                group.locator('.canvas-group-name').click();page.keyboard.press('Delete');expect(page.locator('.node')).to_have_count(1);saved()
                page.keyboard.press('Control+z');expect(page.locator('.node')).to_have_count(3);expect(group).to_have_count(1);saved()
                final=graph();assert final['groups'][0]['title']=='第一幕 · 城市晨光' and len(final['edges'])==1
                page.reload();expect(group).to_have_count(1);expect(group.locator('.canvas-group-name')).to_have_text('第一幕 · 城市晨光')
                page.mouse.click(1400,800);group.locator('.canvas-group-name').click()
                # Zoomed gestures use world coordinates, and whole-canvas arrangement
                # treats the group as one unit rather than scattering its members.
                page.keyboard.press('Control+-');saved();zoom=graph()['viewport']['zoom'];assert zoom<1
                before_move=graph();box=group.bounding_box()
                page.mouse.move(box['x']+8,box['y']+8);page.mouse.down();page.mouse.move(box['x']+53,box['y']+35,steps=8);page.mouse.up();saved()
                after_move=graph()
                for node in after_move['nodes']:
                    original=next(item for item in before_move['nodes'] if item['id']==node['id'])
                    if node['id'] in ('visual','prompt'):
                        assert abs(node['x']-original['x']-45/zoom)<.1
                        assert abs(node['y']-original['y']-27/zoom)<.1
                page.keyboard.press('Control+z');saved()
                before_arrange=graph();by_id={node['id']:node for node in before_arrange['nodes']}
                offset=(by_id['prompt']['x']-by_id['visual']['x'],by_id['prompt']['y']-by_id['visual']['y'])
                page.locator('#arrangeCanvas').click();saved();by_id={node['id']:node for node in graph()['nodes']}
                assert (by_id['prompt']['x']-by_id['visual']['x'],by_id['prompt']['y']-by_id['visual']['y'])==offset
                page.keyboard.press('Control+z');saved()
                shot('03-group-reloaded.png')
                assert not errors,errors
                print('PASS: marquee grouping, group drag, inline rename, colors, undo/redo, resize, layout, individual child editing, ungroup, delete/undo, reload persistence; no JS errors')
            except Exception:
                shot('failure.png');print('JS errors:',errors);raise
            finally:browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=10);CanvasGroupsTests.tearDownClass()


if __name__=='__main__':main()
