"""Real-browser media controls regression, isolated database and local media only.

Run with .venv/Scripts/python.exe tests/verify_node_players.py.
An optional MP4 path may be supplied as the first argument.
"""
from pathlib import Path
from io import BytesIO
import importlib.util
import math
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import wave
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from auth_service import hash_password


def main():
    output = ROOT / 'outputs/node-player-verification'
    output.mkdir(parents=True, exist_ok=True)
    video_path = Path(sys.argv[1]) if len(sys.argv) > 1 else next((ROOT / 'data/uploads').glob('*.mp4'))
    with tempfile.TemporaryDirectory(prefix='jht-node-player-') as directory:
        previous = os.environ.get('JIANHUITONG_DATA_DIR')
        os.environ['JIANHUITONG_DATA_DIR'] = directory
        try:
            spec = importlib.util.spec_from_file_location('node_player_test_app', ROOT / 'app.py')
            host = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(host)
        finally:
            if previous is None:
                os.environ.pop('JIANHUITONG_DATA_DIR', None)
            else:
                os.environ['JIANHUITONG_DATA_DIR'] = previous
        assert host.DATA == Path(directory)
        with host.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password('player-test-password'), 'admin'))
        buffer = BytesIO()
        with wave.open(buffer, 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
            stream.writeframes(b''.join(struct.pack('<h', int(18000 * math.sin(i * .17) * (.5 + .5 * math.sin(i / 4000)))) for i in range(48000)))
        audio = host.store_uploaded_asset(buffer.getvalue(), '旁白试听.wav', '')
        video = host.store_uploaded_asset(video_path.read_bytes(), '视频预览.mp4', '')
        listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(128)
        port = listener.getsockname()[1]; origin = f'http://127.0.0.1:{port}'
        server = uvicorn.Server(uvicorn.Config(host.app, log_level='error'))
        thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True); thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started and time.monotonic() < deadline:
                time.sleep(.1)
            assert server.started
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe')
                context = browser.new_context(viewport={'width': 1440, 'height': 900})
                assert context.request.post(origin + '/api/auth/login', headers={'Origin': origin}, data={'username': 'admin', 'password': 'player-test-password', 'remember': False}).ok
                response = context.request.post(origin + '/api/projects', headers={'Origin': origin}, data={
                    'name': '音视频节点 · 播放器验证', 'nodes': [
                        {'id': 'video', 'type': 'video-asset', 'title': '视频素材', 'x': 200, 'y': 160, 'assetId': video['id']},
                        {'id': 'audio', 'type': 'audio', 'title': '旁白音频', 'x': 620, 'y': 160, 'assetId': audio['id']},
                        {'id': 'generated', 'type': 'video', 'title': '视频生成结果', 'x': 200, 'y': 530},
                        {'id': 'broken', 'type': 'audio', 'title': '失效素材', 'x': 620, 'y': 500, 'assetUrl': '/uploads/missing-player-test.wav'},
                    ], 'edges': [], 'viewport': {'x': 0, 'y': 0, 'zoom': 1}})
                assert response.ok, response.text()
                project_id = response.json()['id']
                host.save('task', {'id': 'generated-task', 'project_id': project_id, 'status': 'done',
                    'video_url': video['url'], 'video_cache_status': 'cached', 'created_at': time.time(),
                    'params': {'node_id': 'generated', 'prompt': 'Generated playback regression', 'duration': 5,
                               'model': 'fixture', 'resolution': '720p', 'aspect_ratio': '16:9'}})
                page = context.new_page(); errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.goto(origin + '/?project=' + response.json()['id'])
                expect(page.locator('.node .node-player')).to_have_count(4)
                page.keyboard.press('Escape')
                page.mouse.click(1200, 800)
                expect(page.locator('[data-node=generated] .node-player-video')).to_have_count(1)
                if os.environ.get('JHT_REFERENCE_ONLY'):
                    page.locator('[data-node="video"] .port.out').press('Enter')
                    page.locator('[data-node="generated"] .port.in').press('Enter')
                    expect(page.locator('#selectedAssets [data-reference-asset="'+video['id']+'"]')).to_have_count(1)
                    # Generated results can create a downstream video with a real saved reference.
                    page.locator('[data-node="generated"] .frame-name').click()
                    page.locator('#nodeContextToolbar [data-context="reference"]').click()
                    expect(page.locator('.node[data-node-type="video"]')).to_have_count(2)
                    expect(page.locator('#selectedAssets .reference-asset')).to_have_count(1)
                    page.wait_for_timeout(1000)
                    graph=context.request.get(origin+'/api/projects/'+project_id).json()
                    generated=next(node for node in graph['nodes'] if node['id']=='generated')
                    assert generated['referenceTaskId']=='generated-task'
                    assert any(edge['from']=='generated' and edge['to'] not in ('video','audio','broken') for edge in graph['edges'])
                    target=next(node for node in graph['nodes'] if node['type']=='video' and node['id']!='generated')
                    assert host.video_graph_asset_ids(graph,target)==[generated['referenceAssetId']]
                    # Removing the chip disconnects the video edge as well as its visual reference.
                    page.locator('#selectedAssets [data-remove-asset]').press('Enter')
                    expect(page.locator('#selectedAssets .reference-asset')).to_have_count(0)
                    page.wait_for_timeout(1000)
                    graph=context.request.get(origin+'/api/projects/'+project_id).json()
                    assert not any(edge['from']=='generated' and edge['to']==target['id'] for edge in graph['edges'])
                    # The shared picker includes video assets, and @ can bind a generated node directly.
                    page.locator('#chooseReferenceBtn').press('Enter')
                    expect(page.locator('.inline-asset-picker [data-inline-asset="'+video['id']+'"]')).to_have_count(1)
                    page.locator('.inline-picker-head button').press('Enter')
                    editor=page.locator('#promptInput + .mention-editor')
                    editor.fill('参考运动 @')
                    menu=page.locator('.asset-mention-menu:not([hidden])')
                    expect(menu).to_be_visible()
                    menu.locator('[data-mention-kind="video"]').click()
                    menu.locator('[data-mention-index]').filter(has_text='视频生成结果').click()
                    expect(page.locator('#selectedAssets .reference-asset')).to_have_count(1)
                    page.wait_for_timeout(1000)
                    graph=context.request.get(origin+'/api/projects/'+project_id).json()
                    target=next(node for node in graph['nodes'] if node['id']==target['id'])
                    assert host.video_graph_asset_ids(graph,target)==[generated['referenceAssetId']]
                    # Validate real local MP4 metadata and provider payloads without calling a model.
                    for provider,model in [('ark','doubao-seedance-2-0-mini-260615'),('aimarket','minimax-h3')]:
                        config={'id':'fixture-video','provider_id':provider,'provider':provider,'model_id':model,'name':'验证模型','api_key':'','capabilities':{}}
                        with patch.object(host,'resolve_model_config',return_value=config):
                            task=host._submit_video_task(host.Task(project_id=project_id,node_id=target['id'],prompt='参考视频的运动'),graph)
                        assert task['status']=='blocked'
                        reference=host.provider_payload(task['params'],config)['content'][1]
                        assert reference['type']=='video_url' and reference['role']=='reference_video'
                    assert not errors,errors
                    page.screenshot(path=str(output/'video-reference-nodes.png'))
                    print('PASS: video-node connection, generated result, @ video reference, reference picker, disconnect, saved graph, real MP4 validation, Seedance and H3 payloads')
                    browser.close()
                    return
                def verify_generated():
                    g = page.locator('[data-node="generated"] .node-player')
                    page.wait_for_function('document.querySelector("[data-node=generated] video").readyState>=2')
                    assert not g.locator('video').evaluate('v=>v.muted')
                    g.get_by_role('button', name='播放', exact=True).click()
                    page.wait_for_function('document.querySelector("[data-node=generated] video").currentTime>.5', timeout=6000)
                    assert float(g.locator('.np-seek').input_value()) > 0
                    if g.locator('video').evaluate('v=>v.muted'):
                        expect(g.locator('.np-status')).to_be_visible()
                        expect(g.locator('.np-status')).to_contain_text('已静音播放')
                    # Verify decoded frames actually advance, not only controls.
                    frames = g.locator('video').evaluate('v=>v.getVideoPlaybackQuality().totalVideoFrames')
                    page.wait_for_function('count=>document.querySelector("[data-node=generated] video").getVideoPlaybackQuality().totalVideoFrames>count', arg=frames)
                    g.get_by_role('button', name='暂停', exact=True).click()
                    g.locator('.np-seek').focus(); page.keyboard.press('End')
                    assert g.locator('video').evaluate('v=>v.currentTime/v.duration') > .95
                    page.wait_for_function('!document.querySelector("[data-node=generated] video").seeking')
                    g.get_by_role('button', name='播放', exact=True).click()
                    page.wait_for_function('document.querySelector("[data-node=generated] video").currentTime>.1 && document.querySelector("[data-node=generated] video").currentTime<2')
                    g.get_by_role('button', name='暂停', exact=True).click()
                if os.environ.get('JHT_GENERATED_ONLY'):
                    verify_generated()
                    video=page.locator('[data-node="generated"] video')
                    for view in ['projects','assets','models','home']:
                        video.evaluate('v=>{v.muted=true;v.currentTime=0;}')
                        page.locator('[data-node="generated"] .np-toggle').click()
                        page.wait_for_function('document.querySelector("[data-node=generated] video").currentTime>.1')
                        page.locator('#workspaceMenu').click()
                        page.locator(f'#mainNav [data-view="{view}"]').click()
                        assert video.evaluate('v=>v.paused'),view
                        paused=video.evaluate('v=>v.currentTime');page.wait_for_timeout(150)
                        assert abs(video.evaluate('v=>v.currentTime')-paused)<.02,view
                        page.locator(f'#projectList [data-project="{project_id}"]').click()
                        assert video.evaluate('v=>v.paused'),view
                    assert not errors, errors
                    print('PASS: generated video clock/decoded frames/seek/replay; projects/assets/models/Agent navigation pauses video and preserves its position')
                    browser.close()
                    return
                a = page.locator('[data-node="audio"] .node-player')
                v = page.locator('[data-node="video"] .node-player')
                expect(a.locator('.np-duration')).to_have_text('00:03')
                page.wait_for_function('document.querySelector("[data-node=audio] .np-wave svg").children.length===96')
                assert page.locator('.node video,.node audio').evaluate_all('els=>els.every(e=>!e.controls)')
                a.get_by_role('button', name='播放', exact=True).click()
                page.wait_for_function('document.querySelector("[data-node=audio] audio").currentTime>.1')
                samples = a.evaluate('''player=>new Promise(resolve=>{
                    const values=new Set();const start=performance.now();
                    function sample(now){
                        values.add(getComputedStyle(player.querySelector('.np-cursor')).transform);
                        if(now-start<400)requestAnimationFrame(sample);else resolve(values.size);
                    }
                    requestAnimationFrame(sample);
                })''')
                assert samples >= 8, f'Playhead did not advance at display cadence: {samples} positions in 400 ms'
                a.get_by_role('button', name='暂停', exact=True).click()
                assert a.locator('audio').evaluate('e=>e.paused')
                paused_transform = a.locator('.np-cursor').evaluate('e=>getComputedStyle(e).transform')
                page.wait_for_timeout(100)
                assert a.locator('.np-cursor').evaluate('e=>getComputedStyle(e).transform') == paused_transform
                # Waveform body moves the node without seeking; restore its position afterwards.
                position = a.locator('audio').evaluate('e=>e.currentTime')
                box = a.locator('.np-wave').bounding_box()
                x, y = box['x'] + box['width'] * .85, box['y'] + box['height'] / 2
                page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 36, y + 24, steps=6); page.mouse.up()
                assert page.locator('[data-node="audio"]').evaluate('e=>e.style.left') == '656px'
                assert abs(a.locator('audio').evaluate('e=>e.currentTime') - position) < .01
                page.mouse.move(x + 36, y + 24); page.mouse.down(); page.mouse.move(x, y, steps=6); page.mouse.up()
                assert page.locator('[data-node="audio"]').evaluate('e=>e.style.left') == '620px'
                # The time label / footer is also a node drag surface.
                footer = a.locator('.np-current').bounding_box()
                fx, fy = footer['x'] + 4, footer['y'] + footer['height'] / 2
                page.mouse.move(fx, fy); page.mouse.down(); page.mouse.move(fx + 20, fy, steps=5); page.mouse.up()
                assert page.locator('[data-node="audio"]').evaluate('e=>e.style.left') == '640px'
                page.mouse.move(fx + 20, fy); page.mouse.down(); page.mouse.move(fx, fy, steps=5); page.mouse.up()
                assert page.locator('[data-node="audio"]').evaluate('e=>e.style.left') == '620px'
                assert abs(a.locator('audio').evaluate('e=>e.currentTime') - position) < .01
                # Only the narrow hit area around the red line scrubs audio.
                box = a.locator('.np-wave').bounding_box()
                cursor_box = a.locator('.np-cursor').bounding_box()
                page.mouse.move(cursor_box['x'] + 1, cursor_box['y'] + cursor_box['height'] / 2)
                page.mouse.down()
                page.mouse.move(box['x'] + 8 + (box['width'] - 16) * .6 + 1, box['y'] + box['height'] / 2, steps=8)
                page.mouse.up()
                assert 1.6 < a.locator('audio').evaluate('e=>e.currentTime') < 2
                # Keyboard access to the visually hidden audio progress control.
                a.locator('.np-seek').focus(); page.keyboard.press('End')
                assert a.locator('audio').evaluate('e=>e.currentTime') > 2.9
                page.wait_for_function('document.querySelector("[data-node=video] video").readyState>=2')
                v.get_by_role('button', name='播放', exact=True).click()
                page.wait_for_function('document.querySelector("[data-node=video] video").currentTime>.1')
                v.get_by_role('button', name='暂停', exact=True).click()
                v.get_by_role('button', name='静音', exact=True).click()
                assert v.locator('video').evaluate('e=>e.muted')
                v.get_by_role('button', name='取消静音', exact=True).click()
                assert not v.locator('video').evaluate('e=>e.muted')
                v.locator('.np-seek').focus(); page.keyboard.press('End')
                assert v.locator('video').evaluate('e=>e.currentTime/e.duration') > .95
                v.get_by_role('button', name='全屏', exact=True).click()
                page.wait_for_function('!!document.fullscreenElement')
                page.evaluate('document.exitFullscreen()')
                expect(page.locator('[data-node="broken"] .np-status')).to_have_text('无法播放，请检查文件或更换素材')
                # Media controls must leave the owning nodes at their original coordinates.
                assert page.locator('[data-node="audio"]').evaluate('e=>e.style.left') == '620px'
                assert page.locator('[data-node="video"]').evaluate('e=>e.style.left') == '200px'
                a.locator('audio').evaluate('e=>e.currentTime=0')
                v.locator('video').evaluate('e=>e.currentTime=0')
                page.mouse.click(1100, 700)
                page.screenshot(path=str(output / 'canvas-players.png'))
                a.screenshot(path=str(output / 'audio-player.png'))
                v.screenshot(path=str(output / 'video-player.png'))
                verify_generated()
                # Replacing a rendered node must pause and release its detached media.
                a.get_by_role('button', name='播放', exact=True).click()
                page.evaluate('window.detachedAudio=document.querySelector("[data-node=audio] audio");document.querySelector("[data-node=audio]").remove()')
                page.wait_for_function('window.detachedAudio.paused')
                assert not errors, errors
                print('PASS: smooth playhead, red-line-only seek, waveform/footer node dragging, playback/pause, keyboard seek, mute, fullscreen, errors, detached media cleanup; no browser JS errors')
                browser.close()
        finally:
            server.should_exit = True; thread.join(timeout=10)


if __name__ == '__main__':
    main()

