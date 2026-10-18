"""Double-click/blur renaming across canvas node types, using disposable data."""
from pathlib import Path
import socket
import sys
import threading
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_canvas_groups import CanvasGroupsTests
from auth_service import hash_password
import uvicorn
from playwright.sync_api import sync_playwright,expect

def main():
    CanvasGroupsTests.setUpClass();host=CanvasGroupsTests.host
    password='node-rename-test'
    with host.connect() as db:db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password(password),'admin'))
    projects={}
    for kind in ['prompt','script','asset','video-asset','audio','video','prop','character']:
        projects[kind]=host.create_project(host.Project(name=kind,nodes=[{'id':'target','type':kind,'title':'原始名称','x':180,'y':120}]))
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128);origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(host.app,log_level='error'));thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    output=ROOT/'outputs/node-rename-verification';output.mkdir(parents=True,exist_ok=True)
    try:
        while not server.started:time.sleep(.05)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
            context=browser.new_context(viewport={'width':1440,'height':1000})
            assert context.request.post(origin+'/api/auth/login',headers={'Origin':origin},data={'username':'admin','password':password,'remember':False}).ok
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            for kind,project in projects.items():
                page.goto(origin+'/?project='+project['id']);card=page.locator('[data-node="target"]');label=card.locator('.node-title b')
                expect(label).to_have_text('原始名称');page.wait_for_timeout(250)
                position=card.bounding_box();label.dblclick();editor=card.locator('.node-title-editor');expect(editor).to_be_focused()
                editor.fill(f'{kind} · 新名称');page.locator('#canvas').click(position={'x':1200,'y':500})
                expect(label).to_have_text(f'{kind} · 新名称');expect(editor).to_have_count(0)
                expect(page.locator('#saveState')).to_have_text('已保存')
                saved=context.request.get(origin+'/api/projects/'+project['id']).json()['nodes'][0]
                assert saved['title']==f'{kind} · 新名称',saved
                assert saved['x']==180 and saved['y']==120
                assert card.bounding_box()['x']==position['x'] and card.bounding_box()['y']==position['y']
                # Replacement labels must still support another double click.
                label.dblclick();expect(editor).to_be_focused();editor.fill('取消修改');editor.press('Escape');expect(label).to_have_text(f'{kind} · 新名称')
                label.dblclick();editor.fill(f'{kind} · 最终名称');editor.press('Enter');expect(label).to_have_text(f'{kind} · 最终名称')
                expect(page.locator('#saveState')).to_have_text('已保存');page.reload();expect(label).to_have_text(f'{kind} · 最终名称')
                if kind=='audio':
                    label.dblclick();editor.fill('音频素材 · 环境声');page.screenshot(path=str(output/'01-inline-edit.png'));editor.blur();expect(page.locator('#saveState')).to_have_text('已保存')
                    page.screenshot(path=str(output/'02-saved.png'))
            assert not errors,errors
            browser.close()
        print('PASS: all 8 node types support double-click rename, blur saves, repeat editing, Enter confirms, Escape cancels, reload persistence and no node movement')
    finally:
        server.should_exit=True;thread.join(5);CanvasGroupsTests.tearDownClass()

if __name__=='__main__':main()
