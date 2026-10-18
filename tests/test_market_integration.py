"""AI Market/local-login isolation with disposable SQL data and no model calls."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request

from fastapi import HTTPException

from auth_service import hash_password
from market_service import model_api_key, model_for_generation, safe_model_error
from test_auth import Browser


ORIGIN = 'https://market.example.test'
KEY = 'sk-test-no-real-secret'
SECOND_KEY = 'sk-test-second-page-secret'
GLOBAL_KEY = 'configured-server-fixture-key'
SESSION_PATH = '/api/integrations/aimarket/session'


class MarketIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jht-market-integration-')
        spec = importlib.util.spec_from_file_location('market_test_app', Path(__file__).resolve().parents[1] / 'app.py')
        cls.h = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {
            'JIANHUITONG_DATA_DIR': cls.directory.name,
            'DATABASE_URL': 'sqlite:///' + (Path(cls.directory.name) / 'market-test.db').as_posix(),
            'JIANHUITONG_MARKET_ORIGIN': ORIGIN,
            'JIANHUITONG_MARKET_MODEL_ORIGIN': ORIGIN,
            'JIANHUITONG_MARKET_AGENT_ID': 'fixture-agent',
            'JIANHUITONG_COOKIE_SAMESITE': 'lax',
            'JIANHUITONG_MARKET_SDK_LOGIN': '0',
        }):
            spec.loader.exec_module(cls.h)
        cls.store = cls.h.app.state.auth_store
        cls.market = cls.h.app.state.market_integration
        password = hash_password('market-fixture-password')
        with cls.h.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=?', (password,))
            db.execute('INSERT INTO auth_users (username,display_name,role,password_hash,created_at) VALUES (?,?,?,?,?)',
                       ('market-member-b', '成员乙', 'user', password, time.time()))
            cls.users = {row[1]: dict(zip(('id', 'username', 'display_name', 'role'), row)) for row in
                         db.execute('SELECT id,username,display_name,role FROM auth_users')}

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'h'):
            cls.h.database.engine.dispose()
        cls.directory.cleanup()

    def setUp(self):
        self.now = time.time()
        self.store.clock = lambda: self.now
        self.market.clock = lambda: self.now
        self.market._bindings.clear()
        with self.h.connect() as db:
            db.execute('DELETE FROM records')
            db.execute('DELETE FROM auth_sessions')
            db.execute('DELETE FROM auth_login_limits')
        self.h.IMAGE_TASK_FALLBACKS.clear()
        self.h.ACTIVE_IMAGE_TASKS.clear()
        self.admin = self.login('admin')
        self.member = self.login('dingyufu')
        self.other = self.login('market-member-b')
        self.h.save('llm_config', {**self.h.DEFAULT_LLM, 'base_url': ORIGIN + '/v1', 'api_key': GLOBAL_KEY})
        self.h.save('image_model_config', {**self.h.DEFAULT_IMAGE_MODEL, 'base_url': ORIGIN + '/v1', 'api_key': GLOBAL_KEY})

    def request(self, browser, method, path, body=None, expected=200, binding=None, **kwargs):
        headers = kwargs.pop('headers', {})
        if binding:
            headers['x-jht-market-session'] = binding
        status, response_headers, raw = browser.request(method, path, body, headers=headers, **kwargs)
        self.assertNotIn(KEY, raw.decode('utf-8', errors='replace'))
        self.assertNotIn(SECOND_KEY, raw.decode('utf-8', errors='replace'))
        self.assertEqual(status, expected, (method, path, raw.decode('utf-8', errors='replace')))
        return json.loads(raw) if raw else None

    def login(self, username):
        browser = Browser(self.h.app)
        self.request(browser, 'POST', '/api/auth/login', {'username': username, 'password': 'market-fixture-password'})
        return browser

    def context(self, key=KEY):
        return {
            'user': {'id': 'external-user', 'name': '集市来访者', 'accountType': 'ordinary', 'workNumber': None, 'organization': None},
            'agent': {'id': 'fixture-agent', 'name': '建绘通'},
            'apiKey': key,
        }

    def bind(self, browser=None, key=KEY):
        response = self.request(browser or self.member, 'POST', SESSION_PATH, self.context(key), expected=201)
        self.assertTrue(response['connected'])
        self.assertGreater(response['expires_at'], self.now)
        return response['binding_id']

    def task(self, binding, request_id='market-image'):
        with patch.object(self.h.threading, 'Thread'):
            return self.request(self.member, 'POST', '/api/images/generations',
                                {'prompt': '一张无敏感信息的测试图片', 'request_id': request_id}, expected=202, binding=binding)

    def assert_not_persisted(self):
        with self.h.connect() as db:
            records = [row[0] for row in db.execute('SELECT body FROM records')]
        encoded = json.dumps(records)
        self.assertNotIn(KEY, encoded)
        self.assertNotIn(SECOND_KEY, encoded)
        for filename in Path(self.directory.name).rglob('*.db'):
            self.assertNotIn(KEY.encode(), filename.read_bytes())
            self.assertNotIn(SECOND_KEY.encode(), filename.read_bytes())

    def test_public_config_and_frame_policy_keep_password_login(self):
        anonymous = Browser(self.h.app)
        config = self.request(anonymous, 'GET', '/api/integrations/aimarket/config')
        self.assertEqual(config, {'enabled': True, 'market_origin': ORIGIN, 'agent_id': 'fixture-agent', 'uses_session_key': True})
        status, headers, page = anonymous.request('GET', '/market')
        self.assertEqual(status, 200)
        self.assertIn(b'id="loginForm"', page)
        self.assertIn(b'<base href="/">', page)
        self.assertEqual(headers['content-security-policy'], "frame-ancestors 'self' " + ORIGIN)
        self.assertNotIn('x-frame-options', headers)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(anonymous.request('GET', '/login')[1]['x-frame-options'], 'DENY')
        status, headers, page = self.admin.request('GET', '/market')
        self.assertEqual(status, 200)
        self.assertIn(b'id="sessionUser"', page)
        self.assertNotIn(b'id="loginForm"', page)
        self.assertEqual(self.admin.request('GET', '/')[1]['x-frame-options'], 'DENY')
        embedded = self.admin.request('GET', '/?embedded=1&view=canvas')[1]
        self.assertEqual(embedded['content-security-policy'], "frame-ancestors 'self' " + ORIGIN)

    def test_anonymous_binding_and_cross_origin_posts_are_denied(self):
        self.request(Browser(self.h.app), 'POST', SESSION_PATH, self.context(), expected=401)
        self.request(self.member, 'POST', SESSION_PATH, self.context(), expected=403, origin=ORIGIN)
        self.assertEqual(self.market._bindings, {})

    def test_sdk_identity_cannot_promote_member_or_demote_admin(self):
        context = self.context()
        context['user'].update({'id': str(self.users['admin']['id']), 'name': 'admin', 'role': 'admin', 'accountType': 'group'})
        member_binding = self.request(self.member, 'POST', SESSION_PATH, context, expected=201)['binding_id']
        user = self.request(self.member, 'GET', '/api/auth/me', binding=member_binding)['user']
        self.assertEqual((user['username'], user['role']), ('dingyufu', 'user'))
        project = self.request(self.member, 'POST', '/api/projects', {'name': '私有画布'}, expected=201, binding=member_binding)
        self.assertEqual(project['owner_id'], str(self.users['dingyufu']['id']))
        self.request(self.other, 'GET', '/api/projects/' + project['id'], expected=404)
        self.request(self.member, 'PUT', '/api/llm', {'api_key': 'attempted-global-change'}, expected=403, binding=member_binding)
        admin_binding = self.bind(self.admin)
        admin = self.request(self.admin, 'GET', '/api/auth/me', binding=admin_binding)['user']
        self.assertEqual(admin['role'], 'admin')
        self.assertEqual(self.request(self.admin, 'GET', '/api/projects', binding=admin_binding)[0]['id'], project['id'])

    def test_binding_is_bound_to_exact_local_user_and_login_session(self):
        binding = self.bind()
        second_login = self.login('dingyufu')
        for browser in (self.other, self.admin, second_login):
            self.request(browser, 'GET', '/api/projects', expected=428, binding=binding)
            self.request(browser, 'DELETE', SESSION_PATH, expected=428, binding=binding)
        self.request(self.member, 'GET', '/api/projects', expected=428, binding='guessed-reference')
        self.request(self.member, 'GET', '/api/projects', binding=binding)

    def test_validation_errors_never_echo_sdk_key_or_overwrite_models(self):
        bad_bodies = [self.context() | {'api_url': 'https://untrusted.invalid'},
                      {'agent': self.context()['agent'], 'apiKey': KEY},
                      {'user': self.context()['user'], 'apiKey': KEY},
                      self.context() | {'apiKey': KEY + ' has-whitespace'}]
        for body in bad_bodies:
            with self.subTest(fields=list(body)):
                self.request(self.member, 'POST', SESSION_PATH, body, expected=422)
        mismatch = self.context()
        mismatch['agent']['id'] = 'another-agent'
        self.request(self.member, 'POST', SESSION_PATH, mismatch, expected=403)
        self.bind()
        self.assertEqual(self.h.internal_llm_config()['api_key'], GLOBAL_KEY)
        self.assertEqual(self.h.internal_image_config()['api_key'], GLOBAL_KEY)
        self.assert_not_persisted()

    def test_task_json_contains_only_reference_and_keeps_creator(self):
        binding = self.bind()
        response = self.task(binding)
        task = self.h.get('image_task', response['id'])
        self.assertEqual(task['_market_binding_id'], binding)
        self.assertEqual(task['_market_actor_id'], str(self.users['dingyufu']['id']))
        self.assertEqual(task['creator_id'], str(self.users['dingyufu']['id']))
        self.assertEqual(task['owner_id'], str(self.users['dingyufu']['id']))
        self.assertNotIn('_market_binding_id', response)
        self.assertNotIn('_market_actor_id', response)
        self.assert_not_persisted()

    def test_worker_thread_recovers_credential_and_request_scope_does_not_leak(self):
        binding = self.bind()
        task = self.h.get('image_task', self.task(binding)['id'])
        selected, errors = [], []

        def work():
            try:
                with self.h.worker_scope(task):
                    selected.append(model_api_key(ORIGIN + '/v1/chat/completions', GLOBAL_KEY))
                    selected.append(model_for_generation(self.h.internal_image_config())['api_key'])
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=work)
        thread.start()
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(selected, [KEY, KEY])
        self.assertEqual(model_api_key(ORIGIN, GLOBAL_KEY), GLOBAL_KEY)
        self.assertEqual(self.h.internal_image_config()['api_key'], GLOBAL_KEY)
        self.assert_not_persisted()

    def test_foreign_model_origins_are_rejected_before_transport(self):
        binding = self.bind()
        task = self.h.get('image_task', self.task(binding)['id'])
        for target in ('https://untrusted.invalid/v1', ORIGIN + '.evil.invalid/v1',
                       'http://market.example.test/v1', 'https://market.example.test:444/v1',
                       'https://user:password@market.example.test/v1'):
            with self.subTest(target=target), self.h.worker_scope(task):
                with self.assertRaises(HTTPException) as denied:
                    model_api_key(target, GLOBAL_KEY)
                self.assertEqual(denied.exception.status_code, 422)
        self.h.save('llm_config', {**self.h.internal_llm_config(), 'base_url': 'https://untrusted.invalid/v1'})
        with patch.object(self.h, 'llm_json') as provider:
            self.request(self.member, 'POST', '/api/prompts/polish', {'text': '镜头向前移动'}, expected=422, binding=binding)
            provider.assert_not_called()

    def test_revoke_is_per_page_and_expired_task_cannot_fallback_to_global_key(self):
        first = self.bind()
        second = self.bind(key=SECOND_KEY)
        self.assertNotEqual(first, second)
        first_task = self.h.get('image_task', self.task(first, 'first-page')['id'])
        second_task = self.h.get('image_task', self.task(second, 'second-page')['id'])
        self.request(self.member, 'DELETE', SESSION_PATH, expected=204, binding=first)
        self.request(self.member, 'GET', '/api/projects', expected=428, binding=first)
        self.request(self.member, 'GET', '/api/projects', binding=second)
        with self.h.worker_scope(first_task), self.assertRaises(HTTPException) as error:
            model_api_key(ORIGIN, GLOBAL_KEY)
        self.assertEqual(error.exception.status_code, 428)
        with self.h.worker_scope(second_task):
            self.assertEqual(model_api_key(ORIGIN, GLOBAL_KEY), SECOND_KEY)
        self.now += self.market.settings.credential_ttl + 1
        with self.h.worker_scope(second_task), self.assertRaises(HTTPException) as error:
            model_api_key(ORIGIN, GLOBAL_KEY)
        self.assertEqual(error.exception.status_code, 428)
        self.assertEqual(self.market._bindings, {})

    def test_logout_revokes_all_same_login_bindings_and_tasks(self):
        first = self.bind()
        second = self.bind(key=SECOND_KEY)
        task = self.h.get('image_task', self.task(first)['id'])
        self.request(self.member, 'POST', '/api/auth/logout', expected=204)
        self.assertNotIn(first, self.market._bindings)
        self.assertNotIn(second, self.market._bindings)
        with self.h.worker_scope(task), self.assertRaises(HTTPException) as error:
            model_for_generation(self.h.internal_image_config())
        self.assertEqual(error.exception.status_code, 428)
        self.assert_not_persisted()

    def test_provider_errors_redact_session_key_and_global_key(self):
        binding = self.bind()
        task = self.h.get('image_task', self.task(binding)['id'])
        with self.h.worker_scope(task):
            error = safe_model_error(RuntimeError('supplier error ' + KEY + ' ' + GLOBAL_KEY), GLOBAL_KEY)
        self.assertNotIn(KEY, error)
        self.assertNotIn(GLOBAL_KEY, error)
        with patch.object(self.h, 'llm_json', side_effect=RuntimeError('provider included ' + KEY)):
            error = self.request(self.member, 'POST', '/api/prompts/polish', {'text': '测试镜头'}, expected=502, binding=binding)
        self.assertNotIn(KEY, json.dumps(error))
        self.assert_not_persisted()

    def test_provider_transport_uses_bound_key_and_redacts_echoed_results(self):
        binding = self.bind()
        task = self.h.get('image_task', self.task(binding)['id'])
        outbound = []

        class FakeOpener:
            def open(self, request, timeout):
                outbound.append(request)
                return io.BytesIO(json.dumps({'choices': [{'message': {'content': 'result ' + KEY}}]}).encode())

        with self.h.worker_scope(task), patch('market_service.build_opener', return_value=FakeOpener()), \
                patch.object(self.h, 'urlopen', side_effect=AssertionError('Market requests must use guarded transport')):
            result = self.h.provider_json('POST', ORIGIN + '/v1/chat/completions', GLOBAL_KEY, {'messages': []})
        self.assertEqual(outbound[0].get_header('Authorization'), 'Bearer ' + KEY)
        self.assertNotIn(KEY, json.dumps(result))
        self.assertNotIn(GLOBAL_KEY, outbound[0].get_header('Authorization'))

    def test_provider_redirect_never_forwards_credentials_outside_fixed_origin(self):
        binding = self.bind()
        task = self.h.get('image_task', self.task(binding)['id'])
        handlers = []

        class FakeOpener:
            def open(self, request, timeout):
                return io.BytesIO(b'{}')

        def factory(handler):
            handlers.append(handler)
            return FakeOpener()

        with self.h.worker_scope(task), patch('market_service.build_opener', side_effect=factory):
            self.h.provider_json('GET', ORIGIN + '/v1/jobs/1', GLOBAL_KEY)
            request = Request(ORIGIN + '/v1/jobs/1', headers={'Authorization': 'Bearer ' + KEY})
            for destination in ('https://untrusted.invalid/v1/jobs/1', 'http://market.example.test/v1/jobs/1'):
                with self.subTest(destination=destination), self.assertRaises(HTTPException) as error:
                    handlers[0].redirect_request(request, None, 302, 'Found', {}, destination)
                self.assertEqual(error.exception.status_code, 502)
            allowed = handlers[0].redirect_request(request, None, 302, 'Found', {}, ORIGIN + '/v1/jobs/2')
            self.assertEqual(allowed.full_url, ORIGIN + '/v1/jobs/2')

    def test_revoked_background_image_task_fails_without_calling_shared_provider(self):
        binding = self.bind()
        task_id = self.task(binding)['id']
        before = self.h.get('image_task', task_id)
        self.request(self.member, 'DELETE', SESSION_PATH, expected=204, binding=binding)
        with patch.object(self.h, 'provider_json') as transport:
            self.h.run_image_generation(task_id, self.h.internal_image_config())
            transport.assert_not_called()
        task = self.h.get('image_task', task_id)
        self.assertEqual(task['status'], 'failed')
        for key in ('prompt', 'project_id', 'model_config_id', '_market_binding_id', '_market_actor_id', 'owner_id', 'creator_id'):
            self.assertEqual(task[key], before[key])
        self.assertNotIn(KEY, task.get('error', ''))
        self.assert_not_persisted()

    def test_provider_result_is_redacted_when_binding_revoked_during_request(self):
        binding = self.bind()
        task = self.h.get('image_task', self.task(binding)['id'])
        market = self.market

        class FakeOpener:
            def open(self, request, timeout):
                market.revoke(binding)
                return io.BytesIO(json.dumps({'content': 'late result ' + KEY, KEY: 'echoed key'}).encode())

        with self.h.worker_scope(task), patch('market_service.build_opener', return_value=FakeOpener()):
            result = self.h.provider_json('POST', ORIGIN + '/v1/chat/completions', GLOBAL_KEY, {})
        self.assertNotIn(KEY, json.dumps(result))

    def test_audio_transport_rechecks_binding_and_redacts_echoed_metadata(self):
        from audio_service import request_audio
        binding = self.bind()
        task = self.h.get('image_task', self.task(binding)['id'])
        config = {'base_url': ORIGIN, 'api_path': '/v1/audio', 'api_key': GLOBAL_KEY}
        outbound = []

        class FakeOpener:
            def open(self, request, timeout):
                outbound.append(request)
                response = io.BytesIO(json.dumps({'code': 0, 'subtitle': {'text': KEY}}).encode())
                response.headers = {'X-Tt-Logid': KEY}
                return response

        with self.h.worker_scope(task), patch('audio_service.build_opener', return_value=FakeOpener()):
            body, log_id = request_audio(config, {})
            self.assertEqual(outbound[0].get_header('X-api-key'), KEY)
            self.assertNotIn(KEY, json.dumps(body) + log_id)
            self.market.revoke(binding)
            with self.assertRaises(HTTPException) as denied:
                request_audio(config, {})
            self.assertEqual(denied.exception.status_code, 428)
        self.assertEqual(len(outbound), 1)

    def test_resuming_expired_market_video_from_standalone_keeps_credential_source(self):
        binding = self.bind()
        stamped = self.h.get('image_task', self.task(binding)['id'])
        self.h.save('model_config', {**self.h.DEFAULT_MODEL, 'base_url': ORIGIN, 'api_key': GLOBAL_KEY})
        task = self.h.save('task', {**stamped, 'id': 'expired-market-video', 'status': 'running',
            'provider_task_id': 'provider-existing-id',
            'params': {'model_config_id': self.h.DEFAULT_MODEL_ID, 'model': self.h.DEFAULT_MODEL['model_id']}})
        self.market.revoke(binding)
        with patch.object(self.h.threading, 'Thread') as worker, self.assertRaises(HTTPException) as denied:
            self.h.reconcile_video_task(task['id'])
        self.assertEqual(denied.exception.status_code, 428)
        worker.assert_not_called()


if __name__ == '__main__':
    unittest.main()
