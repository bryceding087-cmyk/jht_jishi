"""Browser regression for contextual canvas chrome, using a disposable database.

Run with the project's Python plus Playwright and an installed Edge or Chrome.
No external models are called and no workspace projects are changed.
"""
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, expect
from auth_service import hash_password


def drawing():
    picture = Image.new('RGB', (960, 540), '#b4c5c8')
    pen = ImageDraw.Draw(picture)
    for y in range(540):
        c = tuple(int(a+(b-a)*y/540) for a, b in zip((144, 174, 187), (232, 220, 194)))
        pen.line((0, y, 960, y), fill=c)
    pen.ellipse((680, 50, 780, 150), fill='#f3e7c6')
    pen.polygon([(0, 405), (960, 350), (960, 540), (0, 540)], fill='#64767a')
    for x, y, w, h in [(70, 210, 170, 225), (285, 130, 220, 290), (560, 230, 170, 170), (760, 185, 155, 205)]:
        pen.rectangle((x, y, x+w, y+h), fill='#526f7c')
        pen.polygon([(x+w, y), (x+w+35, y+24), (x+w+35, y+h), (x+w, y+h)], fill='#344e5d')
        for wx in range(x+12, x+w-10, 25):
            for wy in range(y+16, y+h-12, 22):
                pen.rectangle((wx, wy, wx+12, wy+10), fill='#a2c8d3')
    pen.line((170, 70, 170, 380), fill='#a7a18c', width=5)
    pen.line((105, 80, 430, 80), fill='#d2c4a4', width=5)
    pen.line((360, 80, 360, 180), fill='#a7a18c', width=2)
    buffer = BytesIO()
    picture.save(buffer, format='PNG')
    return buffer.getvalue()


def main():
    output = ROOT / 'outputs/canvas-workspace-verification'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='jht-canvas-ui-') as directory:
        previous = os.environ.get('JIANHUITONG_DATA_DIR')
        os.environ['JIANHUITONG_DATA_DIR'] = directory
        try:
            spec = importlib.util.spec_from_file_location('canvas_ui_test_app', ROOT / 'app.py')
            host = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(host)
        finally:
            if previous is None:
                os.environ.pop('JIANHUITONG_DATA_DIR', None)
            else:
                os.environ['JIANHUITONG_DATA_DIR'] = previous
        assert host.DATA == Path(directory), 'Refusing to use workspace data'
        password = 'isolated-canvas-ui-password'
        with host.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password(password), 'admin'))
        asset = host.store_uploaded_asset(drawing(), 'architecture-reference.png', '')
        settings = {'prompt': '晨光中的智慧工地，塔吊缓缓运转，镜头从建筑立面推向城市天际线。',
                    'negative_prompt': '模糊、水印', 'aspect_ratio': '16:9', 'duration': 5,
                    'resolution': '720p', 'model_config_id': 'default-seedance-2.0-mini', 'asset_ids': []}
        nodes = [
            {'id': 'idea', 'type': 'prompt', 'title': '创意与画面描述', 'x': 100, 'y': 120, 'settings': settings.copy()},
            {'id': 'reference', 'type': 'asset', 'title': '智慧工地 · 视觉参考', 'x': 100, 'y': 440, 'assetId': asset['id'], 'mediaWidth': 960, 'mediaHeight': 540},
            {'id': 'shot', 'type': 'video', 'title': '镜头 01 · 城市晨光', 'x': 620, 'y': 255, 'settings': settings.copy()},
            {'id': 'script', 'type': 'script', 'title': '宣传片 · 分镜脚本', 'x': 1110, 'y': 120,
             'script_data': {'title': '看见建造的力量', 'scenes': [], 'resources': []}},
            {'id': 'audio', 'type': 'audio', 'title': '旁白与配乐', 'x': 1110, 'y': 440},
        ]
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0)); listener.listen(128)
        port = listener.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(host.app, host='127.0.0.1', port=port, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        errors, report = [], []
        try:
            deadline = time.monotonic()+15
            while not server.started and time.monotonic() < deadline:
                time.sleep(.1)
            assert server.started
            with sync_playwright() as p:
                executable = next((path for path in [
                    Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'),
                    Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
                ] if path.exists()), None)
                browser = p.chromium.launch(headless=True, **({'executable_path': str(executable)} if executable else {}))
                context = browser.new_context(viewport={'width': 1440, 'height': 900}, device_scale_factor=1)
                response = context.request.post(origin+'/api/auth/login', data={'username':'admin','password':password,'remember':False}, headers={'Origin':origin})
                assert response.ok, response.text()
                response = context.request.post(origin+'/api/projects', headers={'Origin':origin}, data={
                    'name':'智慧建造 · 品牌宣传片', 'nodes':nodes,
                    'edges':[{'from':'idea','to':'shot'},{'from':'reference','to':'shot'}],
                    'viewport':{'x':10,'y':70,'zoom':.8}})
                assert response.ok, response.text()
                project_id = response.json()['id']
                page = context.new_page(); page.on('pageerror', lambda e: errors.append(str(e)))
                generated_image=host.store_uploaded_asset(drawing(),'inline-generated.png','')
                import wave
                audio_buffer=BytesIO()
                with wave.open(audio_buffer,'wb') as stream:
                    stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(b'\x00\x00'*16000)
                generated_audio=host.store_uploaded_asset(audio_buffer.getvalue(),'inline-generated.wav','')
                submitted=[]
                page.route('**/api/model-registry',lambda route:route.fulfill(json={'models':[
                    {'id':'inline-image','kind':'image','name':'测试图片模型','model_id':'fixture-image','api_key_set':True,'is_default':True},
                    {'id':'inline-audio','kind':'audio','name':'测试音频模型','model_id':'fixture-audio','api_key_set':True,'is_default':True}]}))
                def generation_route(route):
                    audio='/audio/' in route.request.url
                    submitted.append(route.request.post_data_json)
                    route.fulfill(json={'id':'inline-audio-task' if audio else 'inline-image-task','status':'queued'})
                page.route('**/api/images/generations',generation_route)
                page.route('**/api/audio/generations',generation_route)
                page.route('**/api/images/generations/inline-image-task',lambda route:route.fulfill(json={'id':'inline-image-task','status':'succeeded','asset':generated_image}))
                page.route('**/api/audio/generations/inline-audio-task',lambda route:route.fulfill(json={'id':'inline-audio-task','status':'succeeded','asset':generated_audio}))
                def shot(name):
                    page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    page.wait_for_function('''()=>document.getAnimations().filter(a=>a.effect?.getTiming().iterations!==Infinity).every(a=>a.playState!=='running')''')
                    page.screenshot(path=str(output/name), full_page=True)
                try:
                    page.goto(origin+'/?project='+project_id)
                    expect(page.locator('.node')).to_have_count(5)
                    expect(page.locator('#inspector')).to_be_visible()
                    shot('01-contextual-editor.png')
                    box = page.locator('#canvas').bounding_box()
                    assert box == {'x':0,'y':0,'width':1440,'height':900}, box
                    expect(page.locator('.sidebar')).to_be_hidden()
                    expect(page.locator('#promptInput')).to_have_attribute('readonly', '')
                    styles = page.locator('.composer-parameters select').evaluate_all('''elements=>elements.map(e=>({id:e.id,background:getComputedStyle(e).backgroundColor,height:getComputedStyle(e).height,appearance:getComputedStyle(e).appearance,optionBackground:getComputedStyle(e.options[e.selectedIndex]).backgroundColor}))''')
                    assert all(s['background']=='rgb(48, 48, 48)' for s in styles), styles
                    page.keyboard.press('Escape')
                    shot('02-workflow.png')
                    report.append('Full-screen workflow and contextual editor')

                    page.mouse.move(30,120)
                    port=page.locator('[data-node="reference"] .port.out')
                    expect(port).to_have_css('opacity','0')
                    page.locator('[data-node="reference"] .frame-head').hover()
                    expect(port).to_have_css('opacity','1')
                    expect(port).to_have_css('width','20px')
                    port.hover()
                    expect(port).to_have_css('transform', 'matrix(1, 0, 0, 1, 0, -10)')
                    assert port.evaluate('e=>getComputedStyle(e,"::after").display')=='none'
                    assert port.evaluate('e=>getComputedStyle(e,"::before").backgroundColor')=='rgba(0, 0, 0, 0)'
                    shot('18-hover-ports.png')
                    page.mouse.move(30,120)
                    expect(port).to_have_css('opacity','0')
                    expect(page.locator('.video-node .node-model-name')).to_have_count(0)
                    expect(page.locator('[data-save-video]')).to_have_count(0)
                    expect(page.locator('.agent-particles i')).to_have_count(10)
                    assert page.locator('.agent-particles i').first.evaluate('e=>getComputedStyle(e).animationName')=='canvas-particle-rise'
                    report.append('Hover-only circular ports, simplified video header and animated Agent particles')

                    expect(page.locator('.links marker,.edge-line[marker-end]')).to_have_count(0)
                    geometry=page.locator('.links .edge').evaluate_all('''edges=>edges.map(edge=>{
                      const line=edge.querySelector('.edge-line'),start=line.getPointAtLength(0),end=line.getPointAtLength(line.getTotalLength());
                      const svg=edge.closest('svg').getBoundingClientRect(),zoom=document.querySelector('#canvasStage').getBoundingClientRect().width/document.querySelector('#canvasStage').offsetWidth;
                      const from=document.querySelector('[data-node="'+edge.dataset.edgeFrom+'"] .frame-canvas').getBoundingClientRect();
                      const to=document.querySelector('[data-node="'+edge.dataset.edgeTo+'"] .frame-canvas').getBoundingClientRect();
                      return [start.x*zoom+svg.left-from.right,start.y*zoom+svg.top-from.top-from.height/2,end.x*zoom+svg.left-to.left,end.y*zoom+svg.top-to.top-to.height/2];
                    })''')
                    assert all(abs(delta)<1 for edge in geometry for delta in edge),geometry
                    expect(page.locator('.edge-related')).to_have_count(2)
                    page.locator('[data-node="idea"] .frame-head').click()
                    expect(page.locator('.edge-related')).to_have_count(1)
                    page.locator('#canvas').click(position={'x':1320,'y':780})
                    expect(page.locator('.edge-related')).to_have_count(0)
                    edge=page.locator('.edge').first
                    expect(edge.locator('.edge-flow')).to_have_css('display','none')
                    point=edge.locator('.edge-line').evaluate('e=>{const p=e.getPointAtLength(e.getTotalLength()/2),m=e.getScreenCTM();return {x:p.x*m.a+m.e,y:p.y*m.d+m.f};}')
                    page.mouse.move(point['x'],point['y'])
                    expect(edge.locator('.edge-flow')).to_have_css('display','inline')
                    expect(edge.locator('.edge-flow')).to_have_css('animation-duration','3s')
                    widths=edge.evaluate('e=>({line:getComputedStyle(e.querySelector(".edge-line")).strokeWidth,flow:[...e.querySelectorAll(".edge-flow path")].map(p=>({width:getComputedStyle(p).strokeWidth,filter:getComputedStyle(p).filter}))})')
                    assert all(p['width']==widths['line'] and p['filter']=='none' for p in widths['flow']),widths
                    shot('23-edge-hover-flow.png')
                    flow_frames=[]
                    for _ in range(30):
                        flow_frames.append(Image.open(BytesIO(page.screenshot(clip={'x':330,'y':180,'width':550,'height':420}))).convert('RGB'))
                        page.wait_for_timeout(65)
                    flow_frames[0].save(output/'24-edge-flow.gif',save_all=True,append_images=flow_frames[1:],duration=95,loop=0,disposal=2,optimize=True)
                    page.mouse.move(1320,780)
                    expect(edge.locator('.edge-flow')).to_have_css('display','none')
                    page.emulate_media(reduced_motion='reduce')
                    assert edge.locator('.edge-flow').evaluate('e=>getComputedStyle(e).animationName')=='none'
                    page.emulate_media(reduced_motion='no-preference')
                    report.append('Stable hovered ports, border-attached arrowless wires, node-related/hover flow and reduced motion')

                    page.locator('[data-node="idea"] .frame-head').click()
                    expect(page.locator('#detailPromptInput')).to_be_visible()
                    text = '更新后的创意：晨光照亮智慧工地，电影感写实镜头。'
                    page.locator('#detailPromptInput ~ .mention-editor').fill(text)
                    page.locator('[data-node="shot"] .frame-head').click()
                    expect(page.locator('#promptInput')).to_have_value(text)
                    page.locator('#editorAdvanced').click()
                    expect(page.locator('#negativeInput')).to_be_visible()
                    page.locator('#editorAdvanced').click()
                    expect(page.locator('#negativeInput')).to_be_hidden()
                    report.append('Upstream prompt edits and advanced options survive editor reuse')

                    page.locator('#ratioSelectTrigger').click()
                    expect(page.locator('#nodeOptionPopover')).to_be_visible()
                    option = page.locator('#nodeOptionPopover [role="option"]').last
                    choice = option.locator('span').inner_text()
                    option.click()
                    expect(page.locator('#ratioSelectTrigger')).to_contain_text(choice)
                    expect(page.locator('#nodeOptionPopover')).to_be_hidden()
                    page.locator('#resolutionSelectTrigger').click()
                    page.locator('#nodeOptionPopover').press('Escape')
                    expect(page.locator('#resolutionSelectTrigger')).to_have_attribute('aria-expanded','false')
                    report.append('Model-backed parameter popovers select values and close with Escape')

                    page.set_viewport_size({'width':1440,'height':1400})
                    page.locator('[data-node="reference"] .frame-head').click()
                    composer=page.locator('[data-node-composer="reference"]')
                    expect(composer.locator('.inline-generation-prompt')).to_be_visible()
                    expect(composer.locator('.inline-generate-button')).to_be_enabled()
                    composer.locator('.inline-generation-prompt + .mention-editor').fill('内嵌图片生成：晨光建筑立面')
                    composer.locator('[data-inline-action="reference"]').click()
                    expect(composer.locator('.inline-asset-picker')).to_be_visible()
                    composer.locator('[data-inline-asset]').first.click()
                    expect(composer.locator('.inline-reference-list > div')).to_have_count(1)
                    with page.expect_file_chooser() as chooser:
                        composer.locator('[data-inline-action="upload-reference"]').click()
                    chooser.value.set_files({'name':'inline-upload-reference.png','mimeType':'image/png','buffer':drawing()})
                    expect(composer.locator('.inline-reference-list > div')).to_have_count(2)
                    shot('20-inline-image-composer.png')
                    composer.locator('.inline-generate-button').click()
                    page.locator('[data-node="idea"] .frame-head').click()
                    page.locator('#detailPromptInput').focus()
                    expect(page.locator('[data-node="reference"] img.frame-media')).to_have_attribute('src',generated_image['url'],timeout=10000)
                    expect(page.locator('[data-node="idea"]')).to_have_class(__import__('re').compile(r'.*selected.*'))
                    assert submitted[-1]['prompt']=='内嵌图片生成：晨光建筑立面'
                    assert submitted[-1]['reference_asset_ids']
                    expect(page.locator('#detailPromptInput ~ .mention-editor')).to_be_focused()
                    expect(page.locator('#imageGenerationOverlay')).to_have_count(0)
                    expect(page.locator('#modalBackdrop')).to_be_hidden()
                    page.locator('[data-node="audio"] .frame-head').click()
                    composer=page.locator('[data-node-composer="audio"]')
                    composer.locator('[data-inline-mode="voice"]').click()
                    composer.locator('.inline-generation-prompt + .mention-editor').fill('欢迎来到智慧工地')
                    expect(composer.locator('.inline-generate-button')).to_be_enabled()
                    shot('21-inline-audio-composer.png')
                    composer.locator('.inline-generate-button').click()
                    expect(page.locator('[data-node="audio"] audio')).to_have_attribute('src',generated_audio['url'],timeout=10000)
                    assert submitted[-1]['mode']=='voice'
                    expect(page.locator('#audioGenerationOverlay')).to_have_count(0)
                    page.locator('[data-node="script"] .frame-head').click()
                    expect(page.locator('.inline-script-workflow #scriptGenerateScenes')).to_be_visible()
                    assert page.locator('.inline-script-workflow .script-workflow-main').evaluate('e=>e.clientHeight>200')
                    expect(page.locator('#modalBackdrop')).to_be_hidden()
                    shot('22-inline-script-workflow.png')
                    page.locator('[data-node="shot"] .frame-head').click()
                    page.locator('#chooseReferenceBtn').click()
                    expect(page.locator('#nodeEditorFields .inline-asset-picker')).to_be_visible()
                    expect(page.locator('#modalBackdrop')).to_be_hidden()
                    page.locator('.inline-picker-head button').click()
                    page.locator('[data-node="reference"] .frame-head').click()
                    composer=page.locator('[data-node-composer="reference"]')
                    with page.expect_file_chooser() as chooser:
                        composer.locator('[data-inline-action="upload"]').click()
                    chooser.value.set_files({'name':'inline-manual-replacement.png','mimeType':'image/png','buffer':drawing()})
                    expect(page.locator('[data-node="reference"] .frame-name')).to_contain_text('inline-manual-replacement')
                    manual_url=page.locator('[data-node="reference"] img.frame-media').get_attribute('src')
                    expect(page.locator('#saveState')).to_have_text('已保存',timeout=10000)
                    page.reload()
                    page.locator('[data-node="reference"] .frame-head').click()
                    expect(page.locator('[data-node-composer="reference"] .inline-generation-status')).to_contain_text('已生成',timeout=10000)
                    expect(page.locator('[data-node="reference"] img.frame-media')).to_have_attribute('src',manual_url)
                    page.locator('[data-node="audio"] .frame-head').click()
                    expect(page.locator('[data-node-composer="audio"] .inline-generation-prompt')).to_have_value('欢迎来到智慧工地')
                    expect(page.locator('[data-inline-mode="voice"]')).to_have_attribute('aria-pressed','true')
                    page.locator('[data-node="idea"] .frame-head').click()
                    prompt=page.locator('#detailPromptInput')
                    page.locator('#detailPromptInput ~ .mention-editor').fill(text+' @')
                    mention=page.locator('.asset-mention-menu:not([hidden])')
                    expect(mention).to_be_visible()
                    mention.locator('[data-mention-kind="audio"]').click()
                    expect(mention.locator('[data-mention-index]')).to_have_count(1)
                    shot('25-canvas-at-mention.png')
                    page.locator('#detailPromptInput ~ .mention-editor').press('Enter')
                    expect(prompt).to_have_value(text+' @inline-generated.wav ')
                    expect(mention).to_have_count(0)
                    chip=page.locator('#detailPromptInput ~ .mention-editor .mention-token')
                    expect(chip).to_have_text('♪音频 1')
                    shot('26-inline-mention-token.png')
                    chip.hover()
                    expect(page.locator('.asset-mention-menu:not([hidden]) .mention-linked')).to_be_visible()
                    shot('27-inline-mention-hover.png')
                    page.keyboard.press('Escape')
                    rich=page.locator('#detailPromptInput ~ .mention-editor')
                    rich.press('Control+End')
                    rich.press('Shift+Enter')
                    rich.press_sequentially('场景参考 @')
                    expect(mention).to_be_visible()
                    mention.locator('[data-mention-kind="image"]').click()
                    mention.locator('[data-mention-index]').first.click()
                    expect(rich.locator('.mention-token')).to_have_count(2)
                    expect(rich.locator('.mention-token img')).to_have_count(1)
                    expect(page.locator('.mention-reference-strip .mention-preview-card')).to_have_count(2)
                    page.locator('[data-node="shot"] .frame-head').click()
                    expect(page.locator('#selectedAssets .reference-audio-icon')).to_have_count(1)
                    expect(page.locator('#promptInput + .mention-editor .mention-token').filter(has_text='音频')).to_have_text('♪音频 2')
                    expect(page.locator('#promptInput + .mention-editor .mention-token').filter(has_text='图片')).to_have_text('图片 1')
                    expect(page.locator('#selectedAssets .reference-number')).to_have_text(['1','2'])
                    expect(page.locator('#selectedAssets .reference-duration')).to_have_text('1.0s')
                    shot('28-video-inline-references.png')
                    expect(page.locator('#saveState')).to_have_text('已保存',timeout=10000)
                    page.reload()
                    page.locator('[data-node="shot"] .frame-head').click()
                    expect(page.locator('#promptInput + .mention-editor .mention-token')).to_have_count(2)
                    page.locator('[data-node="idea"] .frame-head').click()
                    page.locator('#detailPromptInput ~ .mention-editor').fill(text)
                    page.locator('[data-node="audio"] .port.out').press('Enter')
                    page.locator('[data-node="shot"] .port.in').press('Enter')
                    expect(page.locator('.edge[data-edge-from="audio"][data-edge-to="shot"]')).to_have_count(1)
                    expect(page.locator('#selectedAssets .reference-audio-icon')).to_have_count(1)
                    page.locator('#selectedAssets .reference-asset:has(.reference-audio-icon) [data-remove-asset]').click()
                    expect(page.locator('.edge[data-edge-from="audio"][data-edge-to="shot"]')).to_have_count(0)
                    report.append('Canvas @ audio search, keyboard insertion, upstream reference propagation and audio port connection')
                    page.locator('[data-node="shot"] .frame-head').click()
                    page.set_viewport_size({'width':1440,'height':900})
                    report.append('Inline image/audio generation and uploads, automatic binding, selection/focus safety, reload persistence, script editor and video picker')

                    page.keyboard.press('Escape')
                    page.locator('#workspaceZoom').click()
                    page.locator('[data-zoom="50"]').click()
                    expect(page.locator('#workspaceZoomValue')).to_have_text('50%')
                    page.locator('#workspaceZoom').click()
                    page.locator('#workspaceZoomInput').fill('5')
                    page.locator('#workspaceZoomInput').press('Tab')
                    expect(page.locator('#workspaceZoomValue')).to_have_text('5%')
                    page.locator('[data-zoom="fit"]').click()
                    page.locator('#workspaceZoom').click()
                    page.locator('[data-zoom="800"]').click()
                    expect(page.locator('#workspaceZoomValue')).to_have_text('800%')
                    page.locator('#workspaceZoom').click()
                    page.locator('[data-zoom="fit"]').click()
                    page.locator('#toggleMinimap').click()
                    expect(page.locator('#workspaceMinimap')).to_be_visible()
                    expect(page.locator('.map-node')).to_have_count(5)
                    page.locator('#toggleEdges').click()
                    assert page.locator('#canvas').evaluate('e=>e.classList.contains("edges-hidden")')
                    page.locator('#toggleEdges').click()
                    page.locator('#toggleSnap').click()
                    expect(page.locator('#toggleSnap')).to_have_attribute('aria-pressed','true')
                    shot('03-minimap.png')
                    page.locator('#toggleMinimap').click()
                    report.append('Zoom 5–800%, fit, minimap, edge visibility and grid snap')

                    page.locator('#dockAddNode').click()
                    shot('04-add-node-menu.png')
                    page.locator('[data-palette-node="prompt"]').click()
                    expect(page.locator('.node')).to_have_count(6)
                    page.keyboard.press('Escape')
                    page.locator('#dockAddNode').click()
                    page.locator('[data-palette-node="video"]').click()
                    expect(page.locator('.node')).to_have_count(7)
                    page.locator('#promptInput + .mention-editor').fill('配合 @')
                    mention=page.locator('.asset-mention-menu:not([hidden])')
                    expect(mention).to_be_visible()
                    mention.locator('[data-mention-kind="audio"]').click()
                    page.locator('#promptInput + .mention-editor').press('Enter')
                    expect(page.locator('#promptInput')).to_have_value('配合 @inline-generated.wav ')
                    expect(page.locator('#selectedAssets .reference-audio-icon')).to_have_count(1)
                    page.locator('#selectedAssets .reference-asset:has(.reference-audio-icon) [data-remove-asset]').click()
                    expect(page.locator('#selectedAssets .reference-audio-icon')).to_have_count(0)
                    expect(page.locator('#promptInput')).to_have_value('配合  ')
                    page.keyboard.press('Escape')
                    page.locator('#arrangeCanvas').click()
                    page.locator('[data-node="idea"] .port.out').focus()
                    page.keyboard.press('Enter')
                    new_video = page.locator('.node[data-node-type="video"]').last
                    new_video.locator('.port.in').focus(); page.keyboard.press('Enter')
                    expect(page.locator('#canvasStage .edge')).to_have_count(3)
                    page.keyboard.press('Escape')
                    page.locator('#arrangeCanvas').click()
                    assert not page.locator('.node').evaluate_all('''nodes=>nodes.map(n=>n.getBoundingClientRect()).some((a,i,all)=>all.slice(i+1).some(b=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top))''')
                    report.append('Add text/video nodes, keyboard-accessible ports, link creation and graph layout')

                    page.keyboard.press('Control+k')
                    expect(page.locator('#workspaceSearch')).to_be_visible()
                    page.locator('#workspaceSearch input').fill('创意与画面')
                    expect(page.locator('[data-search-node]')).to_have_count(1)
                    page.locator('[data-search-node]').click()
                    expect(page.locator('#detailPromptInput')).to_have_value(text)
                    page.keyboard.press('Escape')
                    page.locator('#storyboardMode').click()
                    expect(page.locator('#workspaceBoard')).to_be_visible()
                    expect(page.locator('.board-card')).to_have_count(4)
                    shot('05-storyboard.png')
                    page.locator('[data-board-node="shot"]').click()
                    expect(page.locator('#workspaceBoard')).to_be_hidden()
                    expect(page.locator('#inspector')).to_be_visible()
                    report.append('Node search and storyboard-to-canvas navigation')

                    page.keyboard.press('Escape')
                    before = page.locator('.node[data-node="idea"]').get_attribute('style')
                    page.keyboard.press('Control+0')
                    page.locator('[data-node="idea"] .frame-head').hover()
                    box = page.locator('[data-node="idea"] .frame-head').bounding_box()
                    page.mouse.move(box['x']+35,box['y']+12); page.mouse.down()
                    page.mouse.move(box['x']+75,box['y']+42,steps=5); page.mouse.up()
                    expect(page.locator('[data-node="idea"]')).not_to_have_attribute('style',before)
                    x, y = page.locator('[data-node="idea"]').evaluate('e=>[parseFloat(e.style.left),parseFloat(e.style.top)]')
                    assert x % 24 == 0 and y % 24 == 0, (x,y)
                    page.keyboard.press('Escape')
                    expect(page.locator('#saveState')).to_have_text('已保存', timeout=10000)
                    page.reload()
                    expect(page.locator('.node')).to_have_count(7)
                    expect(page.locator('#canvasStage .edge')).to_have_count(3)
                    assert page.locator('[data-node="idea"]').evaluate('e=>[parseFloat(e.style.left),parseFloat(e.style.top)]') == [x,y]
                    report.append('Drag with grid snapping and persistence across reload')

                    # The upload path creates a correctly typed node, not a video reference.
                    page.keyboard.press('Escape')
                    page.locator('#dockAddNode').click()
                    page.locator('#workspaceUploadInput').set_input_files({'name':'upload.png','mimeType':'image/png','buffer':drawing()})
                    expect(page.locator('.node')).to_have_count(8)
                    report.append('Upload asset into a new canvas node')
                    expect(page.locator('#canvasPanel')).to_be_hidden()
                    page.keyboard.press('Escape')

                    for name, width, height in [('laptop',1024,768),('mobile',430,932)]:
                        page.set_viewport_size({'width':width,'height':height})
                        page.locator('#workspaceZoom').click(); page.locator('[data-zoom="fit"]').click()
                        page.locator('[data-node="shot"] .frame-head').click()
                        expect(page.locator('#inspector')).to_be_in_viewport()
                        shot('06-'+name+'.png')
                        rect = page.locator('#inspector').bounding_box()
                        card=page.locator('[data-node="shot"]').bounding_box()
                        zoom=float(page.locator('#workspaceZoomValue').inner_text().rstrip('%'))/100
                        assert abs(rect['x']+rect['width']/2-card['x']-card['width']/2)<2, (rect,card)
                        assert abs(rect['y']-card['y']-card['height']-12*zoom)<2,(rect,card)
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        shot('06-'+name+'.png')
                        page.keyboard.press('Escape')
                    report.append('1440, 1024 and 430px layouts without horizontal overflow')

                    page.set_viewport_size({'width':1440,'height':900})
                    import imageio_ffmpeg
                    clip = host.UPLOADS / 'history-fixture.mp4'
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i',
                                    'color=c=0x476878:s=320x180:r=12:d=1', '-c:v', 'libx264',
                                    '-pix_fmt', 'yuv420p', str(clip)], check=True, capture_output=True)
                    host.save('task', {'id':'history-fixture','project_id':project_id,'status':'done',
                                      'video_url':'/uploads/history-fixture.mp4','created_at':time.time(),
                                      'params':dict(settings, model='fixture', node_id='shot')})
                    page.locator('#dockHistory').click()
                    expect(page.locator('[data-history-add]')).to_be_visible()
                    shot('08-generation-history.png')
                    page.locator('[data-history-add]').click()
                    expect(page.locator('.node')).to_have_count(9)
                    page.keyboard.press('Escape')
                    report.append('Completed history video can be saved and added to the canvas')

                    expect(page.locator('#saveState')).to_have_text('已保存',timeout=10000)
                    page.reload()
                    expect(page.locator('.node')).to_have_count(9)
                    page.locator('#workspaceZoom').click(); page.locator('[data-zoom="fit"]').click()
                    for selector in ['[data-node="shot"]','.node[data-node-type="video-asset"]']:
                        card=page.locator(selector)
                        expect(card.locator('video')).to_be_visible()
                        card.locator('.frame-head').click()
                        expect(page.locator('#nodeContextToolbar [data-context="capture"]')).to_be_visible()
                        surface=card.locator('.video-drag-surface')
                        position=surface.bounding_box()
                        before=card.evaluate('e=>[parseFloat(e.style.left),parseFloat(e.style.top)]')
                        page.mouse.move(position['x']+position['width']/2,position['y']+10)
                        page.mouse.down()
                        page.mouse.move(position['x']+position['width']/2+65,position['y']+40,steps=8)
                        expect(page.locator('#inspector')).to_be_hidden()
                        expect(page.locator('#nodeContextToolbar')).to_be_hidden()
                        page.mouse.up()
                        expect(page.locator('#inspector')).to_be_visible()
                        expect(page.locator('#nodeContextToolbar')).to_be_visible()
                        after=card.evaluate('e=>[parseFloat(e.style.left),parseFloat(e.style.top)]')
                        assert before != after,(before,after)
                        if selector=='[data-node="shot"]':
                            shot('14-generated-video-context.png')
                        page.keyboard.press('Escape')
                    page.locator('[data-node="shot"] .frame-head').click()
                    page.locator('#nodeContextToolbar [data-context="play"]').click()
                    page.wait_for_function('document.querySelector(\'[data-node="shot"] video\').readyState>=2')
                    page.locator('#nodeContextToolbar [data-context="capture"]').click()
                    expect(page.locator('.node')).to_have_count(10)
                    expect(page.locator('#saveState')).to_have_text('已保存',timeout=10000)
                    page.reload()
                    expect(page.locator('.node')).to_have_count(10)
                    expect(page.locator('[data-node="shot"] .video-drag-surface')).to_be_attached()
                    report.append('Generated/uploaded video picture dragging, hidden details during gesture, playback, frame capture and reload')
                    page.keyboard.press('Escape')
                    page.locator('#workspaceSettings').click()
                    expect(page.locator('#projectPanel')).to_be_visible()
                    page.keyboard.press('Escape')
                    page.locator('#workspaceMenu').click()
                    expect(page.locator('.sidebar')).to_be_visible()
                    shot('07-project-navigation.png')
                    page.locator('[data-view="home"]').click()
                    expect(page.locator('#agentView')).to_be_visible()
                    page.locator(f'#projectList [data-project="{project_id}"]').click()
                    expect(page.locator('#canvasView')).to_be_visible()
                    report.append('Project information, navigation drawer and Agent return')

                    page.locator('#workspaceMenu').click()
                    page.locator('#mainNav [data-view="projects"]').click()
                    page.locator('.projects-create [data-project-action="create"]').click()
                    page.locator('#newProjectInput').fill('空白画布验证')
                    page.locator('#confirmNewProject').click()
                    expect(page.locator('#workspaceEmpty')).to_be_visible()
                    expect(page.locator('.node')).to_have_count(0)
                    expect(page.locator('#inspector')).to_be_hidden()
                    shot('09-empty-canvas.png')
                    page.locator('#canvas').dblclick(position={'x':420,'y':220})
                    expect(page.locator('#canvasPanel')).to_be_visible()
                    expect(page.locator('#canvasPanel')).to_have_attribute('data-anchor','pointer')
                    expect(page.locator('#dockAddNode')).to_have_attribute('aria-expanded','false')
                    shot('10-double-click-menu.png')
                    rect=page.locator('#canvasPanel').bounding_box()
                    assert abs(rect['x']-420)<1 and abs(rect['y']-220)<1,rect
                    assert page.locator('.palette-prompt b').evaluate('e=>getComputedStyle(e).fontSize')=='14px'
                    expect(page.locator('.palette-prompt > svg')).to_have_attribute('viewBox','0 0 16 16')
                    page.locator('[data-palette-node="prompt"]').click()
                    expect(page.locator('.node')).to_have_count(1)
                    node=page.locator('.node').first
                    assert node.evaluate('e=>[parseFloat(e.style.left),parseFloat(e.style.top)]')==[420,220]
                    expect(page.locator('#workspaceEmpty')).to_be_hidden()
                    page.locator('#dockAddNode').click()
                    expect(page.locator('#canvasPanel')).to_have_attribute('data-anchor','dock')
                    expect(page.locator('#dockAddNode')).to_have_attribute('aria-expanded','true')
                    shot('11-dock-menu.png')
                    panel=page.locator('#canvasPanel').bounding_box()
                    plus=page.locator('#dockAddNode').bounding_box()
                    dock=page.locator('.canvas-dock').bounding_box()
                    assert abs(panel['x']+panel['width']/2-plus['x']-plus['width']/2)<1,(panel,plus)
                    assert abs(panel['y']+panel['height']-dock['y']+8)<1,(panel,dock)
                    page.locator('#dockAddNode').click()
                    expect(page.locator('#canvasPanel')).to_have_attribute('data-open','false')
                    page.locator('#dockAddNode').click()
                    expect(page.locator('#canvasPanel')).to_be_visible()
                    expect(page.locator('[data-palette-node]')).to_have_count(6)
                    page.locator('#canvas').click(position={'x':50,'y':100})
                    expect(page.locator('#canvasPanel')).to_be_hidden()
                    page.locator('#canvas').dblclick(position={'x':1430,'y':890})
                    shot('12-edge-menu.png')
                    rect=page.locator('#canvasPanel').bounding_box()
                    assert rect['x']+rect['width']<=1432 and rect['y']+rect['height']<=892,rect
                    page.keyboard.press('Escape')
                    expect(page.locator('#canvasPanel')).to_be_hidden()
                    report.append('Blank canvas, double-click placement, dock anchoring, toggle, outside click and edge avoidance')
                    page.emulate_media(reduced_motion='reduce')
                    expect(page.locator('.agent-particles')).to_be_hidden()
                    page.locator('#dockAddNode').click()
                    assert not page.locator('#canvasPanel').evaluate('e=>e.getAnimations().some(a=>a.playState==="running")')
                    page.locator('#dockAddNode').click()
                    expect(page.locator('#canvasPanel')).to_be_hidden()
                    page.emulate_media(reduced_motion='no-preference')
                    report.append('Reference SVG/text metrics, menu motion and reduced-motion support')

                    # Node attachments belong to the infinite canvas, even when
                    # their rectangles extend beyond the visible viewport.
                    page.locator('#workspaceZoom').click()
                    page.locator('#workspaceZoomInput').fill('100')
                    page.locator('#workspaceZoomInput').press('Tab')
                    page.locator('#canvas').click(position={'x':60,'y':120})
                    node=page.locator('.node').first
                    node.locator('.frame-head').click()
                    card=node.bounding_box()
                    page.mouse.move(card['x']+80,card['y']+12)
                    page.mouse.down()
                    page.mouse.move(card['x']+80,712,steps=10)
                    page.mouse.up()
                    shot('15-world-details-clipped.png')
                    card=node.bounding_box(); detail=page.locator('#inspector').bounding_box()
                    assert abs(detail['y']-card['y']-card['height']-12)<2,(detail,card)
                    assert detail['y']+detail['height']>900,detail
                    assert page.locator('#inspector').evaluate('e=>getComputedStyle(e).maxHeight')=='none'
                    page.keyboard.down('Space')
                    page.mouse.move(80,450); page.mouse.down(); page.mouse.move(80,150,steps=10); page.mouse.up()
                    page.keyboard.up('Space')
                    shot('16-world-details-panned.png')
                    moved=page.locator('#inspector').bounding_box()
                    assert abs(moved['y']-detail['y']+300)<2,(moved,detail)
                    page.locator('#workspaceZoom').click()
                    page.locator('[data-zoom="50"]').click()
                    shot('17-world-details-zoomed.png')
                    scaled=page.locator('#inspector').bounding_box()
                    assert abs(scaled['width']-detail['width']/2)<2,(scaled,detail)
                    card=node.bounding_box(); tools=page.locator('#nodeContextToolbar').bounding_box()
                    assert abs(tools['y']+tools['height']+5-card['y'])<2,(tools,card)
                    report.append('World-anchored details and toolbar: viewport clipping, pan and proportional zoom')

                    # A small screen capture demonstrates the actual transitions;
                    # it uses only disposable fixture data and the running UI.
                    page.set_viewport_size({'width':960,'height':640})
                    frames, durations = [], []
                    def capture(duration=65):
                        frames.append(Image.open(BytesIO(page.screenshot())).convert('RGB'))
                        durations.append(duration)
                    page.mouse.move(30,150)
                    capture(400)
                    page.locator('#dockAddNode').click()
                    for _ in range(5): capture()
                    capture(500)
                    page.locator('#dockAddNode').click()
                    for _ in range(4): capture()
                    capture(300)
                    page.locator('#canvas').dblclick(position={'x':760,'y':110})
                    for _ in range(5): capture()
                    capture(650)
                    page.locator('#canvas').click(position={'x':50,'y':100})
                    for _ in range(4): capture()
                    capture(400)
                    frames[0].save(output/'13-menu-motion.gif',save_all=True,append_images=frames[1:],
                                   duration=durations,loop=0,disposal=2,optimize=True)

                    particle_frames=[]
                    for _ in range(36):
                        particle_frames.append(Image.open(BytesIO(page.screenshot(clip={'x':540,'y':490,'width':220,'height':150}))).convert('RGB'))
                        page.wait_for_timeout(60)
                    particle_frames[0].save(output/'19-agent-particles.gif',save_all=True,append_images=particle_frames[1:],duration=90,loop=0,disposal=2,optimize=True)

                    assert not errors, errors
                    (output/'report.json').write_text(json.dumps({'checks':report,'page_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
                    print(json.dumps({'passed':report,'screenshots':str(output)}, ensure_ascii=False))
                except Exception:
                    shot('failure.png')
                    print('Browser errors:', errors)
                    raise
                finally:
                    browser.close()
        finally:
            server.should_exit=True
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
