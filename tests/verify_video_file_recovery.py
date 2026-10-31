"""Real Chrome: expired preview -> async recovery -> persistent playback/download."""
from pathlib import Path
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from auth_service import hash_password
from test_account_video_models import AccountVideoTests
from agent_media import execute
from playwright.sync_api import sync_playwright, expect
import uvicorn


def main():
    AccountVideoTests.setUpClass()
    fixture = AccountVideoTests(); fixture.setUp(); h = fixture.h
    task = fixture.create_task(1)
    h.update_task(task['id'], status='done', provider_task_id='original-video',
                  video_url='https://cdn.test/expired.mp4', video_cache_status='failed',
                  video_cache_error='服务器下载视频超时，请检查服务器到视频存储服务的网络后重试。')
    asset = h.ensure_video_asset(task['id'])
    clip = h.DATA / 'browser-video.mp4'
    execute(['-f', 'lavfi', '-i', 'color=c=0x367a69:s=320x180:r=24:d=1',
             '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(clip)])
    content = clip.read_bytes()
    allow_download = threading.Event()
    calls = []
    def query(method, url, key, *args, **kwargs):
        assert method == 'GET' and url.endswith('/original-video'), (method, url)
        calls.append(method)
        return {'id': 'original-video', 'status': 'succeeded', 'content': {'video_url': 'https://cdn.test/fresh.mp4'}}
    def cache(url):
        assert url == 'https://cdn.test/fresh.mp4'
        assert allow_download.wait(20)
        (h.UPLOADS / 'recovered-browser.mp4').write_bytes(content)
        return '/uploads/recovered-browser.mp4'
    h.provider_json = query; h.cache_generated_video = cache
    with h.connect() as db:
        db.execute('UPDATE auth_users SET password_hash=?', (hash_password('recovery-fixture'),))
    h.generation_queue.start = h.generation_queue.recover = lambda: None
    h.recover_video_tasks = h.manual_generation.recover = h.video_callbacks.start = lambda: None
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(128)
    origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(h.app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True); thread.start()
    out = ROOT / 'outputs/video-file-recovery-verification'; out.mkdir(exist_ok=True)
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline: time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
            context.route('https://cdn.test/**', lambda route: route.fulfill(status=403, body='Expired fixture'))
            assert context.request.post(origin + '/api/auth/login', headers={'Origin': origin},
                data={'username': fixture.user['username'], 'password': 'recovery-fixture'}).ok
            page = context.new_page(); errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(origin + '/')
            page.locator('#mainNav [data-view="assets"]').click()
            card = page.locator(f'[data-asset-id="{asset["id"]}"]')
            expect(card).to_contain_text('文件未保存')
            card.locator('[data-al-preview]').click()
            expect(page.locator('.al-preview-info .al-error')).to_contain_text('服务器下载视频超时')
            with page.expect_response(lambda r: r.url.endswith('/retry-file')) as reply:
                page.locator('#alRetryFile').click()
            assert reply.value.status == 202
            expect(page.locator('.al-preview-file-state')).to_contain_text('正在保存')
            page.wait_for_timeout(500); page.screenshot(path=str(out / 'saving.png'))
            allow_download.set()
            expect(page.locator('.al-preview-file-state')).to_contain_text('文件已保存', timeout=15000)
            video = page.locator('.al-preview-original')
            expect(video).to_have_attribute('src', '/uploads/recovered-browser.mp4')
            page.wait_for_function('document.querySelector(".al-preview-original")?.readyState >= 2')
            video.evaluate('(video) => video.play()')
            page.wait_for_function('document.querySelector(".al-preview-original")?.currentTime > 0.1')
            page.wait_for_timeout(700)
            page.screenshot(path=str(out / 'saved.png'))
            with page.expect_download() as download:
                page.locator('#alPreviewDownload').click()
            assert Path(download.value.path()).read_bytes() == content
            page.reload()
            page.locator('#mainNav [data-view="assets"]').click()
            card = page.locator(f'[data-asset-id="{asset["id"]}"]')
            expect(card).to_contain_text('文件已保存')
            card.locator('[data-al-preview]').click()
            expect(page.locator('.al-preview-original')).to_have_attribute('src', '/uploads/recovered-browser.mp4')
            page.wait_for_function('document.querySelector(".al-preview-original")?.readyState >= 2')
            assert calls == ['GET'], calls
            assert not errors, errors
            browser.close()
        print('PASS: async 202, saving/saved labels, refreshed original GET, local playback/download and reload; no generation POST')
    finally:
        allow_download.set(); server.should_exit = True; thread.join(timeout=10)
        AccountVideoTests.tearDownClass()


if __name__ == '__main__': main()
