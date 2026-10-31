"""Render the uncertain-submission state in Chrome using isolated fixtures."""
from pathlib import Path
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from auth_service import hash_password
from test_account_video_models import AccountVideoTests
from playwright.sync_api import sync_playwright, expect
import uvicorn


def main():
    AccountVideoTests.setUpClass()
    fixture = AccountVideoTests(); fixture.setUp(); h = fixture.h
    task = fixture.create_task(1)
    h.update_task(task['id'], status='running', submit_unknown=True, error=None,
                  status_message='提交结果待确认')
    h.generation_queue.patch(h.generation_queue.job('task', task['id']), state='unknown')
    with h.connect() as db:
        db.execute('UPDATE auth_users SET password_hash=?', (hash_password('callback-fixture'),))
    h.generation_queue.start = h.generation_queue.recover = lambda: None
    h.recover_video_tasks = h.manual_generation.recover = h.video_callbacks.start = lambda: None
    def no_models(*a, **k):
        raise AssertionError('Provider requests are forbidden in browser verification')
    h.provider_json = no_models
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(128)
    origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(h.app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    out = ROOT / 'outputs/video-callback-verification'; out.mkdir(exist_ok=True)
    try:
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            assert context.request.post(origin + '/api/auth/login', headers={'Origin': origin},
                data={'username': fixture.user['username'], 'password': 'callback-fixture'}).ok
            page = context.new_page(); errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            for index in range(2):
                page.goto(origin + '/')
                page.locator('#projectList [data-conversation="' + task['project_id'] + '"]').click()
                expect(page.locator('.home-generation-placeholder')).to_contain_text('提交结果待确认')
                expect(page.locator('.home-generation-status')).to_contain_text('提交结果待确认')
                expect(page.locator('.home-task-state')).to_have_count(0)
                page.wait_for_timeout(700)
                page.screenshot(path=str(out / ('pending-' + str(index) + '.png')))
            assert not errors, errors
            browser.close()
        print('PASS: pending state and continued polling survive reopening; no false failure, browser errors or paid calls')
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        AccountVideoTests.tearDownClass()


if __name__ == '__main__':
    main()
