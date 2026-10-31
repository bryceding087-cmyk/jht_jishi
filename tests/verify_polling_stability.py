"""Real Chrome: polling preserves library/canvas DOM and active media. No models."""
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from playwright.sync_api import sync_playwright, expect
import uvicorn
from auth_service import hash_password
from agent_media import execute


def main():
    with tempfile.TemporaryDirectory(prefix='jht-polling-') as directory:
        database_url = 'sqlite:///' + (Path(directory) / 'fixture.db').as_posix()
        spec = importlib.util.spec_from_file_location('polling_test_app', ROOT / 'app.py')
        app = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'JIANHUITONG_DATA_DIR': directory,
                        'JIANHUITONG_DATABASE_URL': database_url, 'DATABASE_URL': database_url}):
            spec.loader.exec_module(app)
        with app.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password('poll-fixture'), 'admin'))
        clip = Path(directory) / 'clip.mp4'
        execute(['-f', 'lavfi', '-i', 'color=c=0x374c60:s=320x180:r=24:d=12', '-t', '12',
                 '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)])
        video = app.store_uploaded_asset(clip.read_bytes(), '轮询视频.mp4')
        image_data = io.BytesIO()
        Image.new('RGB', (80, 60), '#475c70').save(image_data, 'PNG')
        image = app.store_uploaded_asset(image_data.getvalue(), '轮询图片.png')
        assets = [{**video, 'created_at': 100, 'duration': 12, 'file_status': 'pending', 'task_id': 'fixture'},
                  {**image, 'created_at': 99}]
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(128)
        origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
        server = uvicorn.Server(uvicorn.Config(app.app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
        thread.start()
        try:
            while not server.started: time.sleep(.05)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
                context = browser.new_context(viewport={'width': 1500, 'height': 1000})
                assert context.request.post(origin + '/api/auth/login', headers={'Origin': origin},
                       data={'username': 'admin', 'password': 'poll-fixture'}).ok
                project = context.request.post(origin + '/api/projects', headers={'Origin': origin}, data={
                    'name': '轮询稳定性', 'nodes': [{'id': 'video', 'type': 'video', 'title': '生成中', 'x': 40, 'y': 50}], 'edges': []}).json()
                page = context.new_page(); errors = []; polls = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                source = (ROOT / 'public/app.js').read_text('utf-8')
                page.route('**/app.js?*', lambda route: route.fulfill(content_type='application/javascript', body=source +
                    '\nwindow.pollingTest={state,assetLibrary,updateTaskNode,renderNodes,showView};'))
                def asset_response(route):
                    polls.append(time.monotonic())
                    route.fulfill(json=assets)
                page.route('**/api/assets', asset_response)
                page.goto(origin + '/?project=' + project['id'])
                page.wait_for_selector('.node[data-node="video"]')
                page.locator('#workspaceAssets').click()
                expect(page.locator('.al-history-card')).to_have_count(2)
                video_card = page.locator(f'.al-history-card[data-asset-id="{video["id"]}"]')
                video_card.hover()
                page.wait_for_function('document.querySelector(".al-history-card video")?.currentTime>.2')
                page.evaluate('''() => {
                    window.savedCards=[...document.querySelectorAll('.al-history-card')];
                    window.savedMedia=document.querySelector('.al-history-card video');window.resets=[];
                    for(const event of ['emptied','loadstart','pause'])savedMedia.addEventListener(event,()=>resets.push(event));
                }''')
                count = len(polls)
                page.wait_for_timeout(6800)
                assert len(polls) >= count + 2, 'must exercise real automatic refresh timers'
                assert page.evaluate('savedCards.every(card=>card.isConnected)&&document.querySelector(".al-history-card video")===savedMedia&&!savedMedia.paused')
                assert page.evaluate('resets') == [], 'unchanged polling must not reload or pause media'
                assets[0]['file_status'] = 'cached'
                page.evaluate('pollingTest.assetLibrary.refresh()')
                expect(video_card.locator('.al-history-file-error')).to_have_text('文件已保存')
                assert page.evaluate('document.querySelector(".al-history-card video")===savedMedia&&!savedMedia.paused')
                page.mouse.move(1400, 150)
                video_card.locator('input').check(force=True)
                expect(video_card).to_have_class('al-history-card is-selected')
                assert page.evaluate('savedCards.every(card=>card.isConnected)')
                page.locator('[data-al-tab="image"]').click()
                expect(page.locator('.al-history-card')).to_have_count(1)
                page.locator('[data-al-tab="all"]').click()
                expect(page.locator('.al-history-card')).to_have_count(2)
                page.locator('#alHistorySort').click()
                assert page.locator('.al-history-card').first.get_attribute('data-asset-id') == image['id']
                print('PASS: automatic asset polling, status changes and selection preserve cards/decoders; filters/sorting update')

                page.evaluate('''() => {
                    pollingTest.showView('canvas');
                    pollingTest.updateTaskNode({id:'fixture-task',status:'running'},'video');
                    window.canvasNode=document.querySelector('.node[data-node=video]');
                    window.canvasMeta=canvasNode.querySelector('.node-meta');
                    window.canvasPreview=canvasNode.querySelector('.video-preview').firstElementChild;
                }''')
                for i in range(4):
                    page.evaluate('(i)=>pollingTest.updateTaskNode({id:"fixture-task",status:"running",updated_at:i},"video")', i)
                    page.wait_for_timeout(80)
                assert page.evaluate('canvasNode===document.querySelector(".node[data-node=video]")&&canvasMeta===canvasNode.querySelector(".node-meta")&&canvasPreview===canvasNode.querySelector(".video-preview").firstElementChild')
                page.evaluate('(url)=>pollingTest.updateTaskNode({id:"fixture-task",status:"done",video_url:url},"video")', video['url'])
                page.wait_for_function('document.querySelector(".node video")?.readyState>=2')
                page.evaluate('''async () => {
                    window.canvasVideo=document.querySelector('.node video');canvasVideo.muted=true;await canvasVideo.play();
                    window.canvasResets=[];for(const event of ['emptied','loadstart','pause'])canvasVideo.addEventListener(event,()=>canvasResets.push(event));
                }''')
                for i in range(4):
                    page.evaluate('(i)=>pollingTest.updateTaskNode({...pollingTest.state.nodeTasks.video,updated_at:i},"video")', i)
                    page.wait_for_timeout(100)
                assert page.evaluate('canvasVideo===document.querySelector(".node video")&&!canvasVideo.paused&&canvasMeta===canvasNode.querySelector(".node-meta")')
                assert page.evaluate('canvasResets') == []
                assert not errors, errors
                print('PASS: running/completed canvas updates preserve status elements and continuous playback; no browser errors')
                browser.close()
        finally:
            server.should_exit = True; thread.join(timeout=10); listener.close(); app.database.engine.dispose()


if __name__ == '__main__':
    main()
