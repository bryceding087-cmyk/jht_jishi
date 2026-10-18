"""Optional browser verification; uses a disposable DB and simulated providers.

Run: .venv/Scripts/python.exe tests/verify_agent_ui.py
Requires Playwright and Chrome. Does not change the development server or data.
"""
from pathlib import Path
from copy import deepcopy
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

import uvicorn
from playwright.sync_api import sync_playwright, expect
from auth_service import hash_password
from agent_runtime import AgentService
from test_agent_runtime import AgentRuntimeTests


def main():
    AgentRuntimeTests.setUpClass()
    fixture=AgentRuntimeTests()
    fixture.setUp()
    host=fixture.host
    host.app.state.agent_service.kick=AgentService.kick.__get__(host.app.state.agent_service)
    with host.connect() as db:
        db.execute('UPDATE auth_users SET password_hash=? WHERE username=?',(hash_password('agent-ui-test-password'),'admin'))
    config=uvicorn.Config(host.app,host='127.0.0.1',port=8768,log_level='error')
    server=uvicorn.Server(config)
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    output=ROOT/'outputs/agent-ui-verification';output.mkdir(parents=True,exist_ok=True)
    errors=[]
    project_errors=[]
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='msedge',headless=True)
            context=browser.new_context(viewport={'width':1512,'height':1050},device_scale_factor=1)
            response=context.request.post('http://127.0.0.1:8768/api/auth/login',data={'username':'admin','password':'agent-ui-test-password','remember':False},headers={'Origin':'http://127.0.0.1:8768'})
            assert response.ok,response.status
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
            phase=['workflow']
            page.on('response',lambda r:project_errors.append((phase[0],r.status,r.text()[:500])) if '/api/projects/' in r.url and r.status>=400 else None)
            page.goto('http://127.0.0.1:8768/');page.locator('#agentView').wait_for(state='visible')
            page.screenshot(path=str(output/'01-home.png'),full_page=True)
            mention_asset=fixture.add_media('image','Agent 引用测试','')
            page.locator('#agentInput + .mention-editor').fill('请使用 @')
            mention=page.locator('.asset-mention-menu:not([hidden])')
            expect(mention).to_be_visible()
            mention.locator('input').fill('Agent 引用测试')
            expect(mention.locator('[data-mention-index]')).to_have_count(1)
            page.screenshot(path=str(output/'12-agent-at-mention.png'),full_page=True)
            page.locator('#agentInput + .mention-editor').press('Enter')
            expect(page.locator('#agentInput')).to_have_value('请使用 @'+mention_asset['name']+' ')
            expect(page.locator('#agentAttached .agent-reference')).to_have_count(1)
            chip=page.locator('#agentInput + .mention-editor .mention-token')
            expect(chip).to_have_text('图片 1')
            expect(page.locator('#agentAttached .reference-number')).to_have_text('1')
            page.screenshot(path=str(output/'13-agent-inline-reference.png'),full_page=True)
            chip.hover()
            expect(page.locator('.asset-mention-menu:not([hidden]) .mention-linked')).to_be_visible()
            page.screenshot(path=str(output/'14-agent-reference-hover.png'),full_page=True)
            page.keyboard.press('Escape')
            editor=page.locator('#agentInput + .mention-editor')
            editor.press('Control+End')
            editor.press('Shift+Enter')
            editor.press_sequentially('再次引用 @')
            expect(mention).to_be_visible()
            mention.locator('input').fill('Agent 引用测试')
            editor.press('Enter')
            expect(editor.locator('.mention-token')).to_have_count(2)
            expect(editor.locator('.mention-token').nth(1)).to_have_text('图片 1')
            expect(page.locator('#agentInput')).to_have_value('请使用 @'+mention_asset['name']+' \n再次引用 @'+mention_asset['name']+' ')
            expect(page.locator('#agentAttached .agent-reference')).to_have_count(1)
            replacement=fixture.add_media('image','Agent 替换测试','')
            editor.locator('.mention-token').nth(1).hover()
            expect(mention).to_be_visible()
            mention.locator('[data-mention-kind="image"]').click()
            mention.locator('[data-mention-index]').filter(has_text='Agent 替换测试').click()
            expect(page.locator('#agentInput')).to_have_value('请使用 @'+mention_asset['name']+' \n再次引用 @'+replacement['name']+' ')
            expect(editor.locator('.mention-token').nth(0)).to_have_text('图片 1')
            expect(editor.locator('.mention-token').nth(1)).to_have_text('图片 2')
            editor.press('Control+a')
            copied=editor.evaluate('''el=>{const clipboardData=new DataTransfer();el.dispatchEvent(new ClipboardEvent('copy',{clipboardData,bubbles:true,cancelable:true}));return clipboardData.getData('text/plain');}''')
            assert copied==page.locator('#agentInput').input_value(),copied
            editor.press('Backspace')
            expect(page.locator('#agentInput')).to_have_value('')
            expect(editor.locator('.mention-token')).to_have_count(0)
            expect(page.locator('#agentDialog')).to_be_hidden()
            while page.locator('#agentAttached [data-remove]').count():page.locator('#agentAttached [data-remove]').first.click()
            page.locator('#agentInput + .mention-editor').fill('')
            page.locator('[data-command="skills"]').click();page.locator('#agentDialog').wait_for(state='visible')
            assert page.locator('.agent-skills>div').count()==16
            page.keyboard.press('Escape')
            page.locator('#agentInput + .mention-editor').fill('小林回到家，母亲说：“欢迎回家。”两人相拥。片尾：一起回家。')
            page.locator('.agent-send').click()
            page.get_by_text('视频画面比例选哪种？',exact=True).wait_for(timeout=15000)
            expect(page.locator('#agentInput')).to_have_value('')
            expect(page.locator('#agentInput + .mention-editor')).to_be_empty()
            page.screenshot(path=str(output/'02-parameters.png'),full_page=True)
            for viewport in ({'width':2048,'height':983},{'width':1280,'height':720},{'width':430,'height':932}):
                page.set_viewport_size(viewport)
                for ratio in ('9:16','1:1','16:9'):
                    page.locator(f'.agent-choice:has(input[value="{ratio}"]) b').click()
                    try:
                        expect(page.locator('#agentInput + .mention-editor')).to_be_in_viewport(timeout=2000)
                        expect(page.locator('.agent-topbar')).to_be_in_viewport(timeout=2000)
                        expect(page.locator(f'.agent-choice.selected input[value="{ratio}"]')).to_be_checked()
                        assert page.locator('#agentView').evaluate('(el) => el.scrollTop')==0
                    except AssertionError:
                        page.screenshot(path=str(output/'ratio-selection-failure.png'),full_page=True)
                        print('Selection layout:',page.evaluate('''() => ['body','main','#agentView','#agentScroll','#agentInput'].map(selector=>{const e=document.querySelector(selector),r=e.getBoundingClientRect();return {selector,scrollTop:e.scrollTop,scrollLeft:e.scrollLeft,className:e.className,height:r.height,y:r.y,x:r.x}})'''))
                        raise
                page.locator('.agent-choice input[value="16:9"]').focus()
                page.keyboard.press('ArrowDown')
                expect(page.locator('.agent-choice.selected input[value="9:16"]')).to_be_checked()
                expect(page.locator('.agent-choice input[value="9:16"]')).to_be_focused()
                expect(page.locator('#agentInput + .mention-editor')).to_be_in_viewport()
                page.locator('.agent-choice:has(input[value="16:9"])').click()
            page.set_viewport_size({'width':1512,'height':1050})
            page.locator('.agent-review-actions [data-page="next"]').click()
            page.locator('.agent-review-actions [data-page="next"]').click()
            page.get_by_role('button',name='确认参数，继续',exact=True).click()
            page.get_by_role('button',name='按此继续，规划分镜',exact=True).wait_for(timeout=15000)
            page.screenshot(path=str(output/'03-artifacts.png'),full_page=True)
            page.locator('button.agent-artifact',has_text='创意设计 · 素材已回填').click()
            document=page.locator('.agent-document')
            expect(document.locator('h1')).to_have_text('创意设计')
            expect(document.locator('h2').first).to_have_text('视觉方向')
            expect(document.locator('strong')).to_have_text('暖光')
            assert document.locator('li').count()>=4
            assert document.locator('table tbody tr').count()==2
            page.screenshot(path=str(output/'07-markdown-document.png'),full_page=True)
            with page.expect_download() as downloaded:
                page.locator('[data-download-doc]').click()
            downloaded.value.save_as(str(output/'creative.md'))
            assert '# 创意设计' in (output/'creative.md').read_text(encoding='utf-8')
            page.keyboard.press('Escape')
            safe=page.evaluate('''async () => {
              const {renderMarkdown}=await import('/markdown.js');
              const host=document.createElement('div');
              host.append(renderMarkdown('# Safe\\n\\n<script>window.markdownAttack=1</script>\\n<img src=x onerror="window.markdownAttack=1">\\n\\n[bad](javascript:alert(1)) [safe](https://example.com)\\n\\n> Quote\\n\\n1. First\\n2. Second\\n\\n```js\\n<a onclick="bad()">\\n```'));
              document.body.append(host);
              const result={script:host.querySelectorAll('script,img,iframe,[onerror],[onclick]').length,
                badLinks:[...host.querySelectorAll('a')].filter(a=>(a.getAttribute('href')||'').startsWith('javascript:')).length,
                headings:host.querySelectorAll('h1').length,code:host.querySelectorAll('pre code').length,
                quote:host.querySelectorAll('blockquote').length,ordered:host.querySelectorAll('ol li').length,
                external:host.querySelector('a[href="https://example.com"]').rel};
              host.replaceChildren(renderMarkdown('{"unsafe":"<img src=x onerror=alert(1)>"}'));
              result.jsonCode=host.querySelector('pre code')?.textContent;
              result.injected=!!window.markdownAttack;host.remove();return result;
            }''')
            assert safe['script']==safe['badLinks']==0 and not safe['injected'],safe
            assert safe['headings']==safe['code']==safe['quote']==1 and safe['ordered']==2,safe
            assert 'noopener' in safe['external'] and '<img' in safe['jsonCode'],safe
            assert context.request.get('http://127.0.0.1:8768/api/projects').json()==[]
            expect(page.locator('.agent-canvas-switch')).to_be_hidden()
            phase[0]='production'
            page.get_by_role('button',name='按此继续，规划分镜',exact=True).click()
            page.get_by_role('button',name='生成全部镜头并合成',exact=True).wait_for(timeout=15000)
            page.get_by_role('button',name='生成全部镜头并合成',exact=True).click()
            page.get_by_role('button',name='已检查，完成交付',exact=True).wait_for(timeout=120000)
            page.get_by_role('button',name='已检查，完成交付',exact=True).scroll_into_view_if_needed()
            final_video=page.locator('.agent-media video').last
            final_video.evaluate('(v) => v.play()')
            page.wait_for_function('Array.from(document.querySelectorAll(".agent-media video")).at(-1)?.currentTime > 0.1')
            final_video.evaluate('(v) => v.pause()')
            page.screenshot(path=str(output/'05-delivery.png'),full_page=True)
            page.get_by_role('button',name='已检查，完成交付',exact=True).click()
            page.get_by_text('视频已完成并保存到素材库。',exact=True).wait_for()
            page.reload();page.get_by_text('视频已完成并保存到素材库。',exact=True).wait_for(timeout=15000)
            page.locator('#agentInput + .mention-editor').fill('继续参考 @')
            mention=page.locator('.asset-mention-menu:not([hidden])')
            expect(mention).to_be_visible()
            mention.locator('input').fill('Agent 引用测试')
            expect(mention.locator('[data-mention-index]')).to_have_count(1)
            page.locator('#agentInput + .mention-editor').press('Enter')
            expect(page.locator('#agentAttached .agent-reference')).to_have_count(1)
            page.locator('#agentAttached [data-remove]').click()
            page.locator('#agentInput + .mention-editor').fill('')
            assert context.request.get('http://127.0.0.1:8768/api/projects').json()==[]
            expect(page.locator('#projectList [data-conversation]')).to_have_count(1)
            page.goto('http://127.0.0.1:8768/')
            page.locator('#projectList [data-conversation]').first.click()
            page.get_by_text('视频已完成并保存到素材库。',exact=True).wait_for(timeout=15000)
            page.set_viewport_size({'width':430,'height':932})
            page.locator('#agentScroll').evaluate('(el) => el.scrollTop=el.scrollHeight')
            page.wait_for_timeout(350)
            box=page.locator('.agent-send').bounding_box()
            assert box and box['x']>=0 and box['x']+box['width']<=430,box
            page.screenshot(path=str(output/'06-mobile.png'),full_page=True)
            assert not errors,errors
            assert not project_errors,project_errors
            print('Browser workflow passed: home, paged approvals, assets, independent conversation history, real render, delivery, reload. Screenshots:',output)
            browser.close()
    finally:
        server.should_exit=True;thread.join(timeout=5)
        fixture.doCleanups()
        AgentRuntimeTests.tearDownClass()


if __name__=='__main__':
    main()
