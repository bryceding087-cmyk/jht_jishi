"""Real Chrome/admin/member checks on isolated storage, without model calls."""
from pathlib import Path
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from test_admin_logs import AdminLogTests
from data_access import access_scope
from playwright.sync_api import sync_playwright, expect
import uvicorn


def main():
    AdminLogTests.setUpClass()
    fixture = AdminLogTests()
    fixture.setUp()
    host = fixture.h
    host.generation_queue.start = lambda: None
    host.generation_queue.recover = lambda: None
    host.recover_video_tasks = lambda: None
    host.manual_generation.recover = lambda: None
    def no_models(*args, **kwargs): raise AssertionError('No paid requests in admin log checks')
    host.provider_json = no_models
    for index in range(34):
        task = fixture.seed_task('video-' + str(index), params={'prompt': '城市建筑宣传片，展现园区环境与施工过程',
            'request_id': 'request-' + str(index), 'model': 'MiniMax H3', 'duration': 6, 'resolution': '768p'})
        host.generation_queue.enqueue('task', task, {'base_url': 'https://fixture.invalid'})
        job = host.generation_queue.job('task', task['id'])
        if index < 8:
            host.generation_queue.patch(job, state='provider')
            host.update_task(task['id'], status='running', provider_task_id='provider-' + str(index))
        if index == 8:
            host.generation_queue.patch(job, state='unknown')
            host.update_task(task['id'], status='failed', submit_unknown=True,
                error='HTTP 502 视频任务提交状态待确认，请勿重复提交 <script>window.auditXss=true</script>')
    listener = socket.socket(); listener.bind(('127.0.0.1', 0)); listener.listen(128)
    origin = f'http://127.0.0.1:{listener.getsockname()[1]}'
    server = uvicorn.Server(uvicorn.Config(host.app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True); thread.start()
    output = ROOT / 'outputs/admin-logs-verification'; output.mkdir(parents=True, exist_ok=True)
    try:
        deadline = time.monotonic()+20
        while not server.started and time.monotonic()<deadline: time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1600, 'height': 1050})
            assert context.request.post(origin+'/api/auth/login', headers={'Origin': origin},
                data={'username': 'admin', 'password': 'audit-fixture-password'}).ok
            page = context.new_page(); errors=[]; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(origin+'/?view=logs')
            expect(page.locator('#adminLogsView')).to_be_visible()
            expect(page.locator('.admin-log-results tbody tr')).to_have_count(3)
            expect(page.locator('.admin-log-summary')).to_contain_text('9 / 100')
            page.locator('[data-log-auto]').uncheck()
            page.screenshot(path=str(output/'operations-desktop.png'))
            page.locator('[data-log-tab="tasks"]').click()
            expect(page.locator('.admin-log-results tbody tr')).to_have_count(30)
            page.locator('[data-log-next]').click()
            expect(page.locator('.admin-log-results tbody tr')).to_have_count(4)
            expect(page.locator('[data-log-page]')).to_have_text('第 2 / 2 页')
            page.locator('[data-log-prev]').click()
            expect(page.locator('.admin-log-results tbody tr')).to_have_count(30)
            page.screenshot(path=str(output/'tasks-desktop.png'))
            page.locator('[name="queue_state"]').select_option('unknown')
            page.locator('.admin-log-filters button[type="submit"]').click()
            expect(page.locator('.admin-log-results tbody tr')).to_have_count(1)
            page.locator('[data-log-detail]').click()
            expect(page.locator('.admin-log-detail')).to_be_visible()
            expect(page.locator('[data-log-detail-body]')).to_contain_text('HTTP 502')
            expect(page.locator('.admin-log-timeline')).to_contain_text('队列状态变化')
            assert not page.evaluate('Boolean(window.auditXss)')
            page.screenshot(path=str(output/'task-detail.png'), animations='disabled')
            page.locator('[data-log-close]').click()
            page.locator('[data-log-reset]').click()
            page.locator('[name="q"]').fill('provider-3')
            page.locator('.admin-log-filters button[type="submit"]').click()
            expect(page.locator('.admin-log-results tbody tr')).to_have_count(1)
            expect(page.locator('.admin-log-results')).to_contain_text('provider-3')
            page.locator('[name="q"]').fill('missing-task')
            page.locator('.admin-log-filters button[type="submit"]').click()
            expect(page.locator('.admin-log-empty')).to_be_visible()
            page.locator('[data-log-reset]').click()
            expect(page.locator('.admin-log-results tbody tr')).to_have_count(30)
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(output/'tasks-mobile.png'))
            assert page.locator('#adminLogsView').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
            page.locator('[data-log-back]').click()
            expect(page.locator('#adminLogsView')).not_to_be_visible()
            member = browser.new_context(viewport={'width': 1400, 'height': 1000})
            assert member.request.post(origin+'/api/auth/login', headers={'Origin': origin},
                data={'username': 'dingyufu', 'password': 'audit-fixture-password'}).ok
            member_page = member.new_page(); member_page.goto(origin+'/?view=logs')
            expect(member_page.locator('html')).not_to_have_attribute('data-app-boot', 'loading')
            expect(member_page.locator('#adminLogsNav')).not_to_be_visible()
            assert member.request.get(origin+'/api/admin/logs/tasks').status == 403
            assert not errors, errors
            browser.close()
        print('PASS: admin navigation, real log API, queue counts, pagination, filters, escaped details, mobile layout and member isolation')
        print('Screenshots:', output)
    finally:
        server.should_exit = True; thread.join(timeout=10)
        AdminLogTests.tearDownClass()


if __name__ == '__main__': main()
