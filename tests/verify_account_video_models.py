"""Exercise personal model switching in Chrome with fake credentials only."""
from pathlib import Path
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from auth_service import hash_password
from test_account_video_models import AccountVideoTests, fixture_models
from playwright.sync_api import sync_playwright, expect
import uvicorn


def main():
    AccountVideoTests.setUpClass()
    fixture = AccountVideoTests(); fixture.setUp(); h = fixture.h
    other = fixture.call('GET', '/api/auth/me', binding=fixture.other)['user']
    with h.connect() as db:
        db.execute('UPDATE auth_users SET password_hash=?', (hash_password('personal-model-fixture'),))
    h.generation_queue.start = h.generation_queue.recover = lambda: None
    h.recover_video_tasks = h.manual_generation.recover = lambda: None
    def no_models(*a, **k): raise AssertionError('Paid API requests are forbidden in this browser check')
    h.provider_json = no_models
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(128)
    origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(h.app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True); thread.start()
    out = ROOT / 'outputs/account-video-verification'; out.mkdir(parents=True, exist_ok=True)
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline: time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            assert context.request.post(origin + '/api/auth/login', headers={'Origin': origin},
                data={'username': fixture.user['username'], 'password': 'personal-model-fixture'}).ok
            page = context.new_page(); errors=[]; page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(origin + '/')
            expect(page.locator('html')).not_to_have_attribute('data-app-boot', 'loading')
            page.locator('#mainNav [data-view="models"]').click()
            page.locator('[data-mm-kind="video"]').click()
            expect(page.locator('.mm-card')).to_have_count(3)
            expect(page.locator('[data-mm-action="add"]')).to_have_count(0)
            for model in fixture_models():
                card = page.locator('[data-mm-model="' + model['id'] + '"]')
                expect(card).to_contain_text('已配置个人专属凭据')
                card.locator('[data-mm-action="activate"]').click()
                expect(card).to_have_class('mm-card is-default')
            page.reload()
            expect(page.locator('html')).not_to_have_attribute('data-app-boot', 'loading')
            page.locator('#mainNav [data-view="models"]').click()
            page.locator('[data-mm-kind="video"]').click()
            expect(page.locator('.mm-card.is-default')).to_contain_text('Seedance 2.5')
            page.screenshot(path=str(out / 'personal-models-desktop.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(out / 'personal-models-mobile.png'), full_page=True)
            assert page.locator('#modelsView').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
            second = browser.new_context(viewport={'width': 1440, 'height': 1000})
            assert second.request.post(origin + '/api/auth/login', headers={'Origin': origin},
                data={'username': other['username'], 'password': 'personal-model-fixture'}).ok
            second_page = second.new_page(); second_page.goto(origin + '/')
            expect(second_page.locator('html')).not_to_have_attribute('data-app-boot', 'loading')
            second_page.locator('#mainNav [data-view="models"]').click()
            second_page.locator('[data-mm-kind="video"]').click()
            expect(second_page.locator('.mm-card')).to_have_count(1)
            expect(second_page.locator('[data-mm-action="activate"]')).to_have_count(0)
            assert not errors, errors
            browser.close()
        print('PASS: personal models visible, switch persists, other account isolated, desktop/mobile layout, no paid calls')
    finally:
        server.should_exit = True; thread.join(timeout=10)
        AccountVideoTests.tearDownClass()


if __name__ == '__main__': main()
