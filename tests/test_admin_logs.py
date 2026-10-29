"""Admin-only audit and queue diagnostics through real ASGI requests."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from admin_logs import clean
from auth_service import hash_password
from creator_metadata import creator_scope, user_creator
from data_access import access_scope
from test_auth import Browser


class AdminLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jht-admin-logs-')
        url = 'sqlite:///' + (Path(cls.directory.name) / 'fixture.db').as_posix()
        spec = importlib.util.spec_from_file_location('admin_logs_app', Path(__file__).resolve().parents[1] / 'app.py')
        cls.h = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'JIANHUITONG_DATA_DIR': cls.directory.name, 'DATABASE_URL': url,
                'JIANHUITONG_DATABASE_URL': url, 'JIANHUITONG_MARKET_ORIGIN': 'https://market.test',
                'JIANHUITONG_MARKET_AGENT_ID': 'fixture', 'JIANHUITONG_MARKET_SDK_LOGIN': '1'}):
            spec.loader.exec_module(cls.h)
        with cls.h.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=?', (hash_password('audit-fixture-password'),))
            cls.users = {row[1]: dict(zip(('id', 'username', 'display_name', 'role'), row))
                         for row in db.execute('SELECT id,username,display_name,role FROM auth_users')}

    @classmethod
    def tearDownClass(cls):
        cls.h.database.engine.dispose()
        cls.directory.cleanup()

    def request(self, browser, method, route, body=None, expected=200, **kwargs):
        status, _, raw = browser.request(method, route, body, **kwargs)
        self.assertEqual(status, expected, raw.decode(errors='replace'))
        return json.loads(raw) if raw else None

    def login(self, username):
        browser = Browser(self.h.app)
        self.request(browser, 'POST', '/api/auth/login', {'username': username, 'password': 'audit-fixture-password'})
        return browser

    def setUp(self):
        with self.h.connect() as db:
            db.execute('DELETE FROM records')
            db.execute('DELETE FROM auth_sessions')
            db.execute('DELETE FROM auth_login_limits')
        self.h.ACTIVE_VIDEO_TASKS.clear()
        self.admin, self.member = self.login('admin'), self.login('dingyufu')

    def seed_task(self, tid='video-fixture', **changes):
        user = self.users['dingyufu']
        with access_scope(user), creator_scope(user_creator(user)):
            return self.h.save('task', {'id': tid, 'status': 'queued', 'created_at': time.time(),
                'updated_at': time.time(), 'params': {'prompt': '城市建筑', 'model': 'minimax-h3',
                'request_id': 'request-' + tid, 'resolution': '768p', 'duration': 4}, **changes})

    def test_all_admin_routes_reject_members_and_anonymous_clients(self):
        tid = self.seed_task()['id']
        for route in ('users', 'summary', 'operations', 'tasks', 'tasks/task/' + tid):
            path = '/api/admin/logs/' + route
            self.request(self.member, 'GET', path, expected=403)
            self.request(Browser(self.h.app), 'GET', path, expected=401)
        user_list = self.request(self.admin, 'GET', '/api/admin/logs/users')['users']
        self.assertNotIn('password_hash', str(user_list))

    def test_login_operations_have_verified_identity_and_no_credentials(self):
        events = self.request(self.admin, 'GET', '/api/admin/logs/operations')['items']
        self.assertEqual(len(events), 2)
        member = next(item for item in events if item['actor']['username'] == 'dingyufu')
        self.assertEqual(member['actor']['id'], str(self.users['dingyufu']['id']))
        self.assertEqual(member['action'], '账号登录')
        self.assertNotIn('audit-fixture-password', str(events))
        self.assertNotIn(next(iter(self.admin.cookies.values())), str(events))
        self.request(Browser(self.h.app), 'POST', '/api/auth/login', {'username': 'missing', 'password': 'private-password'}, expected=401)
        failed = self.request(self.admin, 'GET', '/api/admin/logs/operations?outcome=failed')['items']
        self.assertEqual(failed[0]['attempted_username'], 'missing')
        self.assertEqual(failed[0]['actor']['id'], '')
        self.assertNotIn('private-password', str(failed))

    def test_mutations_record_actor_outcome_trace_and_only_safe_metadata(self):
        status, headers, raw = self.member.request('POST', '/api/projects?token=private-query', {'name': '测试项目'})
        self.assertEqual(status, 201)
        trace = headers['x-jht-request-id']
        events = self.request(self.admin, 'GET', '/api/admin/logs/operations?q=' + trace)['items']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['actor']['username'], 'dingyufu')
        self.assertEqual(events[0]['resource_id'], json.loads(raw)['id'])
        self.assertEqual(events[0]['route'], '/api/projects')
        self.assertNotIn('private-query', str(events))
        self.request(self.member, 'PUT', '/api/llm', {'api_key': 'secret-admin-key', 'model': 'fixture'}, expected=403)
        failed = self.request(self.admin, 'GET', '/api/admin/logs/operations?outcome=failed')['items']
        self.assertEqual(failed[0]['http_status'], 403)
        self.assertNotIn('secret-admin-key', str(failed))

    def test_task_filters_details_queue_summary_and_state_timeline(self):
        task = self.seed_task()
        self.h.generation_queue.enqueue('task', task, {'base_url': 'https://fixture.invalid', 'api_key': 'private-model-key'})
        job = self.h.generation_queue.job('task', task['id'])
        self.h.generation_queue.patch(job, state='provider')
        self.h.update_task(task['id'], status='running', provider_task_id='provider-original',
            provider_response={'api_key': 'private-provider-secret'}, error=None)
        # Poll bookkeeping must not grow the transition timeline.
        before = len(self.h.listing('generation_event'))
        self.h.update_task(task['id'], last_query_at=time.time())
        self.assertEqual(len(self.h.listing('generation_event')), before)
        self.seed_task('another-video', status='done')
        filters = f"task_type=video&queue_state=provider&status=running&user_id={self.users['dingyufu']['id']}&q=provider-original"
        result = self.request(self.admin, 'GET', '/api/admin/logs/tasks?' + filters)
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['items'][0]['request_id'], 'request-video-fixture')
        detail = self.request(self.admin, 'GET', '/api/admin/logs/tasks/task/' + task['id'])
        self.assertEqual(detail['task']['queue']['state'], 'provider')
        self.assertEqual(detail['task']['parameters']['resolution'], '768p')
        self.assertTrue(any('provider_task_id' in event['changes'] for event in detail['events']))
        self.assertTrue(any(event['changes'].get('state', {}).get('to') == 'provider' for event in detail['events']))
        self.assertNotIn('private-model-key', str(detail))
        self.assertNotIn('private-provider-secret', str(detail))
        summary = self.request(self.admin, 'GET', '/api/admin/logs/summary')
        self.assertEqual(summary['video_active'], 1)
        self.assertEqual((summary['video_limit'], summary['video_account_limit']), (100, 10))

    def test_unknown_queue_is_counted_and_legacy_tasks_have_no_invented_events(self):
        task = self.seed_task(submit_unknown=True, status='failed', error={'message': 'HTTP 502 请求待确认', 'api_key': 'secret'})
        self.h.generation_queue.enqueue('task', task, {'base_url': 'https://fixture.invalid'})
        self.h.generation_queue.patch(self.h.generation_queue.job('task', task['id']), state='unknown')
        summary = self.request(self.admin, 'GET', '/api/admin/logs/summary')
        self.assertEqual(summary['unknown'], 1)
        self.assertEqual(summary['video_active'], 1)
        with self.h.connect() as db: db.execute("DELETE FROM records WHERE kind='generation_event'")
        detail = self.request(self.admin, 'GET', '/api/admin/logs/tasks/task/' + task['id'])
        self.assertEqual(detail['events'], [])
        self.assertIn('HTTP 502', detail['task']['error'])
        self.assertNotIn('secret', str(detail))

    def test_real_submission_and_queue_cancel_are_linked_to_task_details(self):
        self.h.update_registry_model('video', self.h.DEFAULT_MODEL_ID, self.h.RegistryModelUpdate(api_key='fixture-video-key'))
        conversation = self.request(self.member, 'POST', '/api/conversations', {
            'title': '日志关联', 'kind': 'video', 'prompt': '建筑', 'request_id': 'log-conversation'}, expected=201)
        with patch.object(self.h, 'provider_json') as provider:
            task = self.request(self.member, 'POST', '/api/tasks', {'project_id': conversation['id'],
                'node_id': 'conversation-video', 'prompt': '建筑', 'request_id': 'correlated-request'}, expected=202)
            self.request(self.member, 'POST', '/api/generation-queue/task/' + task['id'] + '/cancel', {})
            detail = self.request(self.admin, 'GET', '/api/admin/logs/tasks/task/' + task['id'])
            provider.assert_not_called()
        self.assertEqual(detail['task']['status'], 'cancelled')
        self.assertEqual({event['action'] for event in detail['operations']}, {'提交视频生成', '取消排队'})
        submission = next(event for event in detail['operations'] if event['action'] == '提交视频生成')
        self.assertEqual(submission['request_id'], 'correlated-request')
        self.assertEqual(submission['project_id'], conversation['id'])
        self.assertNotIn('fixture-video-key', str(detail))

    def test_pagination_and_time_filters_are_bounded_and_gets_do_not_log_polling(self):
        before = len(self.h.listing('audit_event'))
        for _ in range(3): self.request(self.admin, 'GET', '/api/admin/logs/summary')
        self.assertEqual(len(self.h.listing('audit_event')), before)
        first = self.request(self.admin, 'GET', '/api/admin/logs/operations?page_size=1&page=1')
        second = self.request(self.admin, 'GET', '/api/admin/logs/operations?page_size=1&page=2')
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
        self.assertEqual(first['total'], 2)
        empty = self.request(self.admin, 'GET', '/api/admin/logs/operations?since=' + str(time.time()+5))
        self.assertEqual(empty['items'], [])
        for query in ('page_size=101', 'page=0', 'since=200&until=100'):
            self.request(self.admin, 'GET', '/api/admin/logs/operations?' + query, expected=422)

    def test_market_login_records_organization_without_session_secrets(self):
        result = self.request(Browser(self.h.app), 'POST', '/api/integrations/aimarket/login', {
            'user': {'id': 'market-member', 'name': '集市成员', 'accountType': 'ordinary', 'workNumber': 'A123',
                     'organization': {'id': 'department', 'name': '设计部', 'type': 'department'}},
            'agent': {'id': 'fixture', 'name': '建绘通'}, 'apiKey': 'sk-private-market-fixture'}, expected=201)
        event = self.request(self.admin, 'GET', '/api/admin/logs/operations?' + urlencode({'q': '集市登录'}))['items'][0]
        self.assertEqual(event['actor']['organization'], '设计部')
        self.assertEqual(event['actor']['work_number'], 'A123')
        self.assertNotIn('sk-private-market-fixture', str(event))
        self.assertNotIn(result['binding_id'], str(event))
        self.assertNotIn(result['media_token'], str(event))

    def test_diagnostic_redaction_and_missing_tasks(self):
        text = clean('HTTP 502 Bearer private-auth sk-secret-key https://u:password@cdn.test/file?token=private-link api_key=plain-secret')
        for secret in ('private-auth', 'sk-secret-key', 'password', 'private-link', 'plain-secret'):
            self.assertNotIn(secret, text)
        self.request(self.admin, 'GET', '/api/admin/logs/tasks/model_config/hidden', expected=404)
        self.request(self.admin, 'GET', '/api/admin/logs/tasks/task/missing', expected=404)


if __name__ == '__main__': unittest.main()
