"""No paid requests: verify timeout ambiguity, callback ownership and recovery."""
import json
import os
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from test_auth import Browser
import test_account_video_models as accounts

fixture_models = accounts.fixture_models
from data_access import access_scope
from market_service import model_api_key
from video_callbacks import VideoCallbacks, WAITING_MESSAGE


class VideoCallbackTests(unittest.TestCase):
    setUpClass = classmethod(accounts.AccountVideoTests.setUpClass.__func__)
    tearDownClass = classmethod(accounts.AccountVideoTests.tearDownClass.__func__)
    call = accounts.AccountVideoTests.call
    login = accounts.AccountVideoTests.login
    create_task = accounts.AccountVideoTests.create_task

    def setUp(self):
        accounts.AccountVideoTests.setUp(self)
        self.env = patch.dict(os.environ, {'JIANHUITONG_VIDEO_CALLBACK_BASE_URL': 'https://app.example.test/jht'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.service = self.h.video_callbacks

    def prepared(self, index=1, timeout=False):
        task = self.create_task(index)
        with self.h.worker_scope(task):
            config = self.h.resolve_model_config(task['params']['model_config_id'])
            if timeout:
                with patch.object(self.h, 'provider_json', side_effect=TimeoutError('lost submit response')) as provider:
                    self.h.run_provider_task(task['id'], task['params'], config)
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(provider.call_args.kwargs['timeout'], 120)
                url = provider.call_args.args[3]['callback_url']
            else:
                url = self.service.prepare(task, config)
        path = urlsplit(url).path.removeprefix('/jht') + '?' + urlsplit(url).query
        token = parse_qs(urlsplit(url).query)['token'][0]
        return task, config, path, token

    def post(self, path, body):
        status, _, raw = Browser(self.h.app).request('POST', path, body, origin=None)
        return status, json.loads(raw)

    def result(self, index=1, status='succeeded', task_id='upstream-fixture'):
        body = {'id': task_id, 'status': status, 'model': fixture_models()[index]['model_id']}
        if status == 'succeeded':
            body['content'] = {'video_url' if index else 'url': 'https://cdn.example/video.mp4'}
        return body if index else {'task': body}

    def cache(self, url):
        (self.h.UPLOADS / 'callback.mp4').write_bytes(b'cached fixture')
        return '/uploads/callback.mp4'

    def test_timeout_is_pending_and_cannot_repeat_post(self):
        task, config, path, token = self.prepared(timeout=True)
        current = self.h.get('task', task['id'])
        self.assertEqual(current['status'], 'running')
        self.assertEqual(current['status_message'], WAITING_MESSAGE)
        self.assertTrue(current['submit_unknown'])
        self.assertIsNone(current['error'])
        with patch.object(self.h, 'provider_json') as provider:
            self.h.run_provider_task(task['id'], task['params'], config)
            self.h.refresh_video_task(task['id'])
        provider.assert_not_called()
        public = self.call('GET', '/api/tasks/' + task['id'], binding=self.owner)
        self.assertNotIn(token, json.dumps(public))
        self.assertNotIn(token, json.dumps(self.h.listing('video_callback')))

    def test_official_callback_restores_timeout_after_restart_and_market_logout(self):
        for index in (0, 1):
            with self.subTest(provider=index):
                task, config, path, token = self.prepared(index, timeout=True)
                self.assertEqual(self.post(path, self.result(index))[0], 200)
                # A new service sees the durable receipt; its API key remains
                # scoped to the original account despite a revoked page login.
                self.h.app.state.market_integration.revoke(self.owner)
                restored = VideoCallbacks(self.h.AgentHost())
                def query(method, url, key):
                    self.assertEqual(method, 'GET')
                    self.assertTrue(url.endswith('/upstream-fixture'))
                    self.assertEqual(model_api_key(url, key), config['api_key'])
                    return self.result(index)
                with patch.object(self.h, 'provider_json', side_effect=query) as provider, \
                        patch.object(self.h, 'cache_generated_video', side_effect=self.cache):
                    restored.process(task['id'])
                    restored.process(task['id'])
                self.assertEqual(provider.call_count, 1)
                current = self.h.get('task', task['id'])
                self.assertEqual(current['status'], 'done')
                self.assertEqual(current['provider_task_id'], 'upstream-fixture')
                self.assertFalse(current['submit_unknown'])
                self.assertEqual(current['owner_id'], str(self.user['id']))
                self.assertEqual(current['video_url'], '/uploads/callback.mp4')
                self.owner = self.login('owner')

    def test_minimax_challenge_is_fast_without_provider_io(self):
        task, _, path, _ = self.prepared(0)
        with patch.object(self.h, 'provider_json') as provider:
            before = time.monotonic()
            self.assertEqual(self.post(path, {'challenge': 'original-challenge'}),
                             (200, {'challenge': 'original-challenge'}))
            self.assertLess(time.monotonic() - before, 3)
        provider.assert_not_called()
        self.assertFalse(self.h.get('task', task['id']).get('provider_task_id'))

    def test_confirmed_public_default_submits_and_receives_without_browser_login(self):
        for index, provider in ((0, 'minimax'), (1, 'ark')):
            with self.subTest(provider=provider), patch.dict(os.environ):
                os.environ.pop('JIANHUITONG_VIDEO_CALLBACK_BASE_URL', None)
                task = self.create_task(index)
                with self.h.worker_scope(task):
                    config = self.h.resolve_model_config(task['params']['model_config_id'])
                with patch.object(self.h, 'provider_json', side_effect=TimeoutError('lost response')) as submit:
                    self.h.run_provider_task(task['id'], task['params'], config)
                submit.assert_called_once()
                url = urlsplit(submit.call_args.args[3]['callback_url'])
                self.assertEqual((url.scheme, url.netloc), ('https', 'aimarket.hncig.cn:4193'))
                self.assertEqual(url.path, f'/api/video-callbacks/{provider}/{task["id"]}')
                path = url.path + '?' + url.query
                if provider == 'minimax':
                    self.assertEqual(self.post(path, {'challenge': 'verify-public-entry'}),
                                     (200, {'challenge': 'verify-public-entry'}))
                self.assertEqual(self.post(path, self.result(index, status='running'))[0], 200)
                receipt = self.h.raw_record('video_callback', self.service.identifier(task['id']))
                self.assertEqual(receipt['candidate_id'], 'upstream-fixture')

    def test_callback_auth_body_limit_and_browser_routes_remain_protected(self):
        task, _, path, token = self.prepared()
        self.assertEqual(self.post(path.replace(token, '0' * 64), self.result())[0], 404)
        self.assertEqual(self.post(path, {'id': '../wrong'})[0], 422)
        self.assertEqual(self.post(path, {'id': 'upstream', 'padding': 'x' * 65536})[0], 413)
        self.assertEqual(Browser(self.h.app).request('GET', path, origin=None)[0], 401)
        self.assertEqual(Browser(self.h.app).request('POST', '/api/tasks', {}, origin=None)[0], 401)
        self.assertEqual(self.call('GET', '/api/tasks/' + task['id'], binding=self.other, expected=404)['detail'], '记录不存在')

    def test_callback_cannot_replace_known_task_or_trust_claimed_video(self):
        task, _, path, _ = self.prepared()
        self.h.update_task(task['id'], provider_task_id='known-task')
        self.assertEqual(self.post(path, self.result(task_id='other-task'))[0], 409)
        self.assertEqual(self.post(path, self.result(task_id='known-task'))[0], 200)
        with patch.object(self.h, 'provider_json', return_value=self.result(status='running', task_id='known-task')), \
                patch.object(self.h, 'cache_generated_video') as download:
            self.service.process(task['id'])
        self.assertEqual(self.h.get('task', task['id'])['status'], 'running')
        self.assertFalse(self.h.get('task', task['id']).get('video_url'))
        download.assert_not_called()

    def test_temporary_query_failure_is_durable_and_duplicate_preserves_backoff(self):
        task, _, path, _ = self.prepared(timeout=True)
        self.post(path, self.result())
        with patch.object(self.h, 'provider_json', side_effect=TimeoutError('query timeout')):
            self.service.process(task['id'])
        row = self.h.raw_record('video_callback', self.service.identifier(task['id']))
        self.post(path, self.result())
        self.assertEqual(self.h.raw_record('video_callback', row['id'])['next_check_at'], row['next_check_at'])
        self.assertTrue(self.h.get('task', task['id'])['submit_unknown'])
        with patch.object(self.h, 'provider_json', return_value=self.result()), \
                patch.object(self.h, 'cache_generated_video', side_effect=self.cache):
            self.service.process(task['id'])
        self.assertEqual(self.h.get('task', task['id'])['status'], 'done')

    def test_deleted_and_expired_tasks_do_not_resurrect(self):
        for index, change in enumerate(({'deleted_at': time.time()}, {'queue_timed_out_at': time.time(), 'status': 'failed'})):
            task, _, path, _ = self.prepared(index)
            self.post(path, self.result())
            self.h.update_task(task['id'], **change)
            with patch.object(self.h, 'provider_json') as provider:
                self.service.process(task['id'])
            provider.assert_not_called()
            self.assertFalse(self.h.get('task', task['id']).get('video_url'))

    def test_changed_key_and_disabled_owner_are_not_used(self):
        task, _, path, _ = self.prepared()
        self.post(path, self.result())
        with self.h.connect() as db:
            db.execute('UPDATE auth_users SET active=0 WHERE id=?', (self.user['id'],))
        with patch.object(self.h, 'provider_json') as provider:
            self.service.process(task['id'])
        provider.assert_not_called()
        with self.h.connect() as db:
            db.execute('UPDATE auth_users SET active=1 WHERE id=?', (self.user['id'],))
        document = self.document
        document['accounts'][0]['models'][1]['api_key'] = 'changed-fixture-key'
        self.h.account_video_models.path.write_text(json.dumps(document), encoding='utf-8')
        with patch.object(self.h, 'provider_json') as provider:
            self.service.process(task['id'])
        provider.assert_not_called()

    def test_callback_can_arrive_before_post_response_without_status_regression(self):
        task = self.create_task(1)
        with self.h.worker_scope(task): config = self.h.resolve_model_config(task['params']['model_config_id'])
        requests = []
        def query(method, url, key, body=None, **kwargs):
            requests.append(method)
            if method == 'POST':
                parsed = urlsplit(body['callback_url'])
                path = parsed.path.removeprefix('/jht') + '?' + parsed.query
                self.assertEqual(self.post(path, self.result())[0], 200)
                self.service.process(task['id'])
                raise TimeoutError('late submission response lost')
            return self.result()
        with patch.object(self.h, 'provider_json', side_effect=query), \
                patch.object(self.h, 'cache_generated_video', side_effect=self.cache):
            self.h.run_provider_task(task['id'], task['params'], config)
        self.assertEqual(requests, ['POST', 'GET'])
        self.assertEqual(self.h.get('task', task['id'])['status'], 'done')

    def test_explicitly_disabled_or_gateway_models_use_polling_without_callback(self):
        task = self.create_task(1)
        with self.h.worker_scope(task):
            config = self.h.resolve_model_config(task['params']['model_config_id'])
            with patch.dict(os.environ, {'JIANHUITONG_VIDEO_CALLBACK_BASE_URL': ''}):
                self.assertIsNone(self.service.prepare(task, config))
            self.assertIsNone(self.service.prepare(task, {**config, 'provider_id': 'aimarket'}))
        self.assertEqual(self.h.listing('video_callback'), [])


if __name__ == '__main__':
    unittest.main()
