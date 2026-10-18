"""Browser verification with temporary data and simulated generation requests."""
from pathlib import Path
import io
import math
import struct
import sys
import threading
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_sidebar_metadata import SidebarMetadataTests


def main():
    SidebarMetadataTests.setUpClass()
    fixture = SidebarMetadataTests()
    fixture.setUp()
    host = fixture.host
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b''.join(struct.pack('<h', int(10000 * math.sin(i * .3))) for i in range(8000)))
    audio = host.store_uploaded_asset(buffer.getvalue(), 'test.wav', '')
    image = host.store_uploaded_asset((ROOT / 'public/icon-32.png').read_bytes(), 'test.png', '')
    video = host.store_uploaded_asset(next((ROOT / 'data/uploads').glob('*.mp4')).read_bytes(), 'test.mp4', '')
    references = [image['id']]
    server = uvicorn.Server(uvicorn.Config(host.app, host='127.0.0.1', port=8779, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        while not server.started:
            time.sleep(.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 960})
            origin = 'http://127.0.0.1:8779'
            assert context.request.post(origin + '/api/auth/login', data={'username': 'admin', 'password': 'sidebar-unit-test'}, headers={'Origin': origin}).ok
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            for kind, source in [('image', image), ('video', video), ('audio', audio)]:
                settings = {'model_config_id': 'original-' + kind, 'aspect_ratio': '16:9', 'size': '2K', 'count': 2,
                            'reference_asset_ids': references, 'asset_ids': references, 'resolution': '720p', 'duration': 5,
                            'mode': 'voice' if kind == 'audio' else 'image-to-video', 'audio_config': {'format': 'wav', 'speech_rate': .8}, 'watermark': False}
                conversation = context.request.post(origin + '/api/conversations', data={'title': '三按钮验证 ' + kind, 'kind': kind, 'prompt': '对话最初的描述', 'settings': settings, 'request_id': 'actions-' + kind}, headers={'Origin': origin}).json()
                cid = conversation['id']
                record_kind = {'image': 'image_task', 'audio': 'audio_task', 'video': 'task'}[kind]
                prompt = '本段完整提问：保持全部标点！\n第二行 ' + kind + ' @test.png'

                def save_task(tid, text):
                    asset = host.save('asset', {**source, 'id': 'out-' + tid, 'task_id': tid, 'project_id': cid, 'source': 'generated'})
                    task = {'id': tid, 'status': 'done', 'project_id': cid, 'prompt': text, 'name': text[:60], 'created_at': time.time(), 'asset': asset, 'assets': [asset], **settings}
                    if kind == 'video':
                        task.update(params={**settings, 'prompt': text}, video_url=asset['url'])
                    return host.save(record_kind, task)

                original = save_task(kind + '-source', prompt)
                other = save_task(kind + '-other', '另一段内容')
                requests = []
                endpoint = {'image': '/api/images/generations', 'audio': '/api/audio/generations', 'video': '/api/tasks'}[kind]

                def regenerate(route):
                    body = route.request.post_data_json
                    requests.append(body)
                    route.fulfill(json=save_task(kind + '-regenerated', body['prompt']), status=201)

                page.route(origin + endpoint, regenerate)
                page.goto(origin + '/?conversation=' + cid)
                page.wait_for_load_state('networkidle')
                card = page.locator('[data-home-job="' + original['id'] + '"]')
                expect(card.locator('.home-turn-actions button')).to_have_count(3)
                card.locator('[data-home-edit]').click()
                expect(page.locator('#homePrompt')).to_have_value(prompt)
                expect(page.locator('#homePrompt + .mention-editor')).to_be_focused()
                assert not requests
                card.locator('[data-home-regenerate]').click()
                expect(page.locator('[data-home-job="' + kind + '-regenerated"]')).to_be_visible()
                assert len(requests) == 1
                body = requests[0]
                assert body['prompt'] == prompt and body['project_id'] == cid and body['model_config_id'] == 'original-' + kind and body['request_id']
                assert body.get('reference_asset_ids', body.get('asset_ids')) == references
                if kind == 'image':
                    assert body['count'] == 2 and body['size'] == '2K' and body['watermark'] is False
                if kind == 'audio':
                    assert body['audio_config'] == settings['audio_config']
                if kind == 'video':
                    assert body['duration'] == 5 and body['resolution'] == '720p'
                card.screenshot(path=str(ROOT / 'outputs/brand/integration-preview' / ('turn-actions-' + kind + '.png')))
                page.locator('[data-home-job="' + kind + '-regenerated"] [data-home-delete]').click()
                expect(page.locator('[data-home-job="' + kind + '-regenerated"]')).to_have_count(0)
                card.locator('[data-home-delete]').click()
                expect(card).to_have_count(0)
                expect(page.locator('[data-home-job="' + other['id'] + '"]')).to_be_visible()
                page.reload()
                page.wait_for_load_state('networkidle')
                expect(page.locator('.home-turn-actions')).to_have_count(1)
                page.locator('[data-home-delete]').click()
                expect(page.locator('.home-turn-actions')).to_have_count(0)
                expect(page.locator('[data-conversation="' + cid + '"]')).to_have_count(0)
                assert host.get('asset', 'out-' + original['id']).get('deleted_at')
                assert not host.get('asset', image['id']).get('deleted_at')
                page.unroute(origin + endpoint, regenerate)
                print('PASS', kind, ': exact re-edit; original generation settings; scoped deletion; persistence and empty conversation removal')
            assert not errors, errors
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        SidebarMetadataTests.tearDownClass()


if __name__ == '__main__':
    main()
