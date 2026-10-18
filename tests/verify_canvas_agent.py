"""Canvas Agent floating window, project binding, motion and responsive UI in an isolated DB."""
from pathlib import Path
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
    with host.connect() as db:db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password('canvas-agent-test'),'admin'))
    project=host.create_project(host.Project(name='春日影像',nodes=[{'id':'idea','type':'prompt','title':'故事创意','settings':{'prompt':'春日城市的晨光'},'x':160,'y':200}]))
    other=host.create_project(host.Project(name='另一个项目',nodes=[]))
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128);origin=f'http://127.0.0.1:{listener.getsockname()[1]}'
    server=uvicorn.Server(uvicorn.Config(host.app,log_level='error'));thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    output=ROOT/'outputs/canvas-agent-verification';output.mkdir(parents=True,exist_ok=True)
    try:
        while not server.started:time.sleep(.05)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True,executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
            context=browser.new_context(viewport={'width':1512,'height':960})
            assert context.request.post(origin+'/api/auth/login',headers={'Origin':origin},data={'username':'admin','password':'canvas-agent-test','remember':False}).ok
            page=context.new_page();page.set_default_timeout(12000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(origin+'/');page.locator('#agentInput + .mention-editor').fill('独立对话的首页草稿')
            page.locator(f'#projectList [data-project="{project["id"]}"]').click();expect(page.locator('#canvasView')).to_be_visible()
            canvas_before=page.locator('#canvasView').bounding_box();node_before=page.locator('[data-node="idea"]').bounding_box()
            page.locator('#workspaceAgent').click();panel=page.locator('#canvasAgentPanel');expect(panel).to_be_visible()
            expect(page.locator('#canvasView')).to_be_visible();expect(panel.locator('.agent-dock-starters button')).to_have_count(4)
            expect(panel.locator('#agentView')).to_be_visible()
            page.wait_for_timeout(350)
            assert abs(page.locator('main').bounding_box()['width']-1512)<2
            assert abs(panel.bounding_box()['width']-400)<2
            assert panel.bounding_box()['y']==64
            assert abs(panel.bounding_box()['x']+400-(1512-16))<2
            assert abs(panel.bounding_box()['height']-(960-64-16))<2
            assert panel.evaluate('el=>getComputedStyle(el).borderRadius')=='24px'
            assert page.locator('#canvasView').bounding_box()==canvas_before
            assert page.locator('[data-node="idea"]').bounding_box()==node_before
            assert page.locator('iframe:visible').count()==0
            page.screenshot(path=str(output/'01-right-panel.png'))
            panel.locator('[data-dock-starter="sense"]').click()
            expect(page.locator('#agentInput')).to_have_value('请基于当前画布继续创作：\n项目：春日影像\n故事创意：春日城市的晨光')
            editor=panel.locator('#agentInput + .mention-editor');editor.fill('保留我的画布草稿')
            panel.locator('.canvas-agent-close').click();expect(panel).to_be_hidden()
            page.locator('#workspaceAgent').click();expect(panel).to_be_visible();expect(page.locator('#agentInput')).to_have_value('保留我的画布草稿')
            handle=panel.locator('[role=separator]');handle.focus();handle.press('ArrowLeft');page.wait_for_timeout(300)
            assert abs(panel.bounding_box()['width']-424)<2
            assert page.locator('#canvasView').bounding_box()==canvas_before
            page.keyboard.press('Escape');expect(panel).to_be_hidden()
            page.locator('#workspaceAgent').click();expect(panel).to_be_visible()
            editor.fill('小林回到家，母亲说：“欢迎回家。”两人相拥。片尾：一起回家。')
            panel.locator('.agent-send').click()
            page.wait_for_function('document.querySelector("#agentView").classList.contains("has-conversation")')
            runs=context.request.get(origin+'/api/agent/runs').json();assert len(runs)==1 and runs[0]['project_id']==project['id'],runs
            assert len(context.request.get(origin+'/api/projects').json())==2
            expect(page.locator('#canvasView')).to_be_visible();assert page.locator('iframe:visible').count()==0
            page.locator('#workspaceMenu').click();page.locator('#workspaceMenu').click()
            expect(panel).to_be_visible()
            # Simulate a completed Agent graph update; polling must update this canvas without navigation.
            graph=host.raw_record('project',project['id']);graph['nodes'].append({'id':'agent-added','type':'prompt','title':'Agent 新增内容','settings':{'prompt':'新镜头'},'x':600,'y':200});graph['revision']+=1;host.save('project',graph)
            page.wait_for_function('!!document.querySelector("[data-node=agent-added]")',timeout=15000)
            page.screenshot(path=str(output/'02-conversation.png'))
            page.locator('#workspaceMenu').click() if page.locator('#workspaceMenu').count() else None
            # Navigation cleans up immediately and returns the shared chat to its full-page host.
            page.evaluate('document.querySelector("#mainNav [data-view=assets]").click()')
            expect(panel).to_be_hidden();expect(page.locator('body')).not_to_have_class(__import__('re').compile('canvas-agent-open'))
            page.evaluate('document.querySelector("#mainNav [data-view=home]").click()');expect(page.locator('main #agentView')).to_be_visible()
            expect(page.locator('#agentInput')).to_have_value('独立对话的首页草稿')
            assert context.request.get(origin+'/api/conversations').json()==[]
            page.goto(origin+'/?project='+other['id']);page.locator('#workspaceAgent').click();expect(panel).to_be_visible()
            expect(panel.locator('.agent-dock-welcome')).to_be_visible();expect(page.locator('#agentMessages')).to_be_empty()
            page.set_viewport_size({'width':430,'height':860});page.wait_for_timeout(300)
            assert panel.bounding_box()['width']<=414
            assert abs(page.locator('main').bounding_box()['width']-430)<2
            assert panel.locator('.agent-send').bounding_box()['x']+32<=430
            page.screenshot(path=str(output/'03-mobile.png'))
            page.emulate_media(reduced_motion='reduce');panel.locator('.canvas-agent-close').click();expect(panel).to_be_hidden()
            page.locator('#workspaceAgent').click();expect(panel).to_be_visible()
            assert panel.locator('.agent-dock-starters button').first.evaluate('el=>getComputedStyle(el).animationName')=='none'
            assert not errors,errors
            browser.close()
        print('PASS: floating window bounds and corners, unchanged canvas and node position, draft, resize, keyboard, project-bound run, graph refresh, navigation cleanup, mobile and reduced motion')
    finally:
        server.should_exit=True;thread.join(5);fixture.doCleanups();AgentRuntimeTests.tearDownClass()

if __name__=='__main__':main()
