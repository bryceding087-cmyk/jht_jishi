"""Project gallery workflow in Chrome, with an isolated database and local media."""
from pathlib import Path
import socket
import sys
import threading
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_canvas_groups import CanvasGroupsTests
from verify_canvas_workspace import drawing
from auth_service import hash_password
import uvicorn
from playwright.sync_api import sync_playwright,expect


def main():
    CanvasGroupsTests.setUpClass();host=CanvasGroupsTests.host
    with host.connect() as db:db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password('project-gallery-test'),'admin'))
    image=host.store_uploaded_asset(drawing(),'城市参考.png')
    project_ids=[]
    for i,name in enumerate(['长安异闻录 · 第一卷','未命名项目','品牌宣传 · 城市晨光','安全培训','春日影像','人物设定','项目进展','周末创作','山间旅程','智慧工地']):
        nodes=[{'id':'picture','type':'asset','title':'参考图片','assetId':image['id'],'x':200,'y':150}] if i in (0,2,4,5,8) else []
        project=host.create_project(host.Project(name=name,nodes=nodes));host.save('project',{**project,'updated_at':1792100000-i*86400});project_ids.append(project['id'])
    host.save('task',{'id':'old-work','project_id':project_ids[0],'status':'done','params':{'prompt':'旧作品视频，不应出现在项目卡片中'},'video_url':'/uploads/not-a-project.mp4','created_at':time.time()})
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128);origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(host.app,log_level='error'));thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    output=ROOT/'outputs/project-library-verification';output.mkdir(parents=True,exist_ok=True)
    try:
        while not server.started:time.sleep(.05)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
            context=browser.new_context(viewport={'width':1944,'height':1110},timezone_id='Asia/Shanghai')
            assert context.request.post(origin+'/api/auth/login',headers={'Origin':origin},data={'username':'admin','password':'project-gallery-test','remember':False}).ok
            page=context.new_page();page.set_default_timeout(10000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin+'/?view=projects')
            expect(page.locator('#mainNav [data-view="projects"]')).to_have_count(1)
            expect(page.locator('#mainNav [data-view="projects"]')).to_have_text('项目')
            expect(page.locator('#mainNav [data-view="works"]')).to_have_count(0)
            expect(page.locator('[data-project-card]')).to_have_count(10)
            expect(page.locator('.projects-create')).to_be_visible()
            expect(page.locator('.projects-cover img')).to_have_count(5)
            expect(page.locator('#newProject')).to_have_count(0)
            sidebar_fonts=page.locator('.sidebar').evaluate('el=>({nav:getComputedStyle(el.querySelector("#mainNav button")).fontSize,recent:getComputedStyle(el.querySelector("#projectList button")).fontSize})')
            centered=page.locator('.projects-create .projects-cover').evaluate('el=>{const b=el.getBoundingClientRect(),a=el.querySelector("svg").getBoundingClientRect(),t=el.querySelector("strong").getBoundingClientRect();return Math.abs((a.top+t.bottom)/2-(b.top+b.bottom)/2)<1;}')
            assert centered
            page.wait_for_function('[...document.querySelectorAll(".projects-cover img")].every(img=>img.complete&&img.naturalWidth>0)')
            assert page.locator('.projects-cover img').first.evaluate('img=>{const b=img.parentElement.getBoundingClientRect(),r=img.getBoundingClientRect();return r.width<=b.width+1&&r.height<=b.height+1;}')
            expect(page.get_by_text('旧作品视频，不应出现在项目卡片中')).to_have_count(0)
            page.screenshot(path=str(output/'01-all-projects.png'))
            page.locator('#projectSearch').fill('城市晨光');expect(page.locator('[data-project-card]')).to_have_count(1)
            page.locator('#projectSearch').fill('');expect(page.locator('[data-project-card]')).to_have_count(10)
            page.locator('[data-project-action="new-folder"]').click();page.locator('#projectLibraryName').fill('宣传片合集');page.locator('#projectNameForm [type="submit"]').click()
            expect(page.locator('.projects-folder')).to_have_count(1)
            folder=context.request.get(origin+'/api/project-folders').json()[0]
            card=page.locator(f'[data-project-card="{project_ids[0]}"]')
            card.locator('summary').click();card.locator('[data-project-action="move"]').click()
            page.locator('#projectMoveFolder').select_option(folder['id']);page.locator('#projectMoveConfirm').click()
            expect(page.locator('#modalBackdrop')).to_be_hidden()
            page.locator('[data-project-action="folder"]').click();expect(page.locator('[data-project-card]')).to_have_count(1)
            expect(page.locator('.projects-location h1')).to_have_text('宣传片合集')
            card.locator('summary').click();card.locator('[data-project-action="rename"]').click()
            page.locator('#projectLibraryName').fill('长安异闻录 · 修订版');page.locator('#projectNameForm [type="submit"]').click()
            expect(card.locator('h2')).to_have_text('长安异闻录 · 修订版')
            card.locator('summary').click();card.locator('[data-project-action="duplicate"]').click();expect(page.locator('[data-project-card]')).to_have_count(2)
            page.locator('[data-project-action="back"]').click();expect(page.locator('[data-project-card]')).to_have_count(11)
            page.reload();expect(page.locator('.projects-folder')).to_have_count(1);expect(page.locator('[data-project-card]')).to_have_count(11)
            # Open a real canvas, then trash that same selected project from the gallery.
            page.locator(f'[data-project-card="{project_ids[0]}"] .projects-cover[data-project-action="open"]').click()
            expect(page.locator('#canvasView')).to_have_class('workspace view active-view')
            expect(page.locator('.node')).to_have_count(1)
            page.locator('#workspaceMenu').click()
            assert page.locator('.sidebar').evaluate('el=>({nav:getComputedStyle(el.querySelector("#mainNav button")).fontSize,recent:getComputedStyle(el.querySelector("#projectList button")).fontSize})')==sidebar_fonts
            page.locator('#mainNav [data-view="projects"]').click()
            card=page.locator(f'[data-project-card="{project_ids[0]}"]');card.locator('summary').click();card.locator('[data-project-action="delete"]').click();page.locator('#confirmProjectTrash').click()
            expect(page.locator('.projects-library')).to_be_visible();expect(card).to_have_count(0)
            page.locator('#projectTrashBtn').click();expect(page.locator('.projects-location h1')).to_have_text('回收站');expect(card).to_have_count(1)
            card.locator('.projects-restore').click();expect(card).to_have_count(0)
            page.locator('#projectTrashBtn').click();expect(page.locator('[data-project-card]')).to_have_count(11)
            folder_card=page.locator('.projects-folder');folder_card.locator('summary').click();folder_card.locator('[data-project-action="delete-folder"]').click();page.locator('#projectFolderConfirm').click()
            expect(folder_card).to_have_count(0);expect(page.locator('[data-project-card]')).to_have_count(11)
            page.locator('.projects-create [data-project-action="create"]').click();page.locator('#newProjectInput').fill('新建空白画布');page.locator('#confirmNewProject').click()
            expect(page.locator('#canvasView')).to_have_class('workspace view active-view');expect(page.locator('.node')).to_have_count(0)
            page.locator('#workspaceMenu').click();page.locator('#mainNav [data-view="projects"]').click();expect(page.locator('[data-project-card]')).to_have_count(12)
            for width in (1024,430):
                page.set_viewport_size({'width':width,'height':1000});page.wait_for_timeout(150)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                page.screenshot(path=str(output/f'02-projects-{width}.png'))
            assert not errors,errors
            print('PASS: single Projects navigation, real-project covers, search, persistent folders/move/rename/duplicate, canvas opening, trash/restore, safe folder deletion, blank creation, responsive layouts; no browser errors')
            browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=10);listener.close();CanvasGroupsTests.tearDownClass()


if __name__=='__main__':main()
