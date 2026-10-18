"""Long video jobs, recoverable queries and idempotent automatic asset registration."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from data_access import access_scope
from market_service import model_api_key, video_query_credentials, open_model_request
from urllib.request import Request
from test_auth import Browser
from test_provider_adapters import params, aimarket_config


class VideoReliabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jht-video-reliability-')
        url = 'sqlite:///' + (Path(cls.directory.name) / 'fixture.db').as_posix()
        spec = importlib.util.spec_from_file_location('video_reliability_app', Path(__file__).resolve().parents[1] / 'app.py')
        cls.h = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'JIANHUITONG_DATA_DIR': cls.directory.name, 'DATABASE_URL': url,
                'JIANHUITONG_DATABASE_URL': url, 'JIANHUITONG_MARKET_ORIGIN': 'https://market.test',
                'JIANHUITONG_MARKET_MODEL_ORIGIN': 'http://192.168.241.202:4091',
                'JIANHUITONG_MARKET_AGENT_ID': 'fixture', 'JIANHUITONG_MARKET_SDK_LOGIN': '1'}):
            spec.loader.exec_module(cls.h)

    @classmethod
    def tearDownClass(cls):
        cls.h.database.engine.dispose()
        cls.directory.cleanup()

    def setUp(self):
        self.now = 1000
        self.h.ACTIVE_VIDEO_TASKS.clear()
        self.h.app.state.market_integration._bindings.clear()
        with self.h.connect() as db:
            db.execute('DELETE FROM records')
            db.execute('DELETE FROM auth_login_limits')
        self.parameters = params(model_config_id=self.h.DEFAULT_MODEL_ID, model='minimax-h3')

    def seed(self, **changes):
        return self.h.save('task', {'id': 'video-fixture', 'status': 'queued', 'created_at': self.now,
            'params': self.parameters, 'project_id': '', **changes})

    def cache(self, url):
        (self.h.UPLOADS / 'fixture.mp4').write_bytes(b'fixture local video')
        return '/uploads/fixture.mp4'

    def sleep(self, seconds):
        self.now += seconds

    def run_job(self, responses, resume=False, config=None):
        with patch.object(self.h, 'provider_json', side_effect=responses) as request, \
                patch.object(self.h, 'cache_generated_video', side_effect=self.cache), \
                patch.object(self.h.time, 'sleep', side_effect=self.sleep), \
                patch.object(self.h.time, 'time', side_effect=lambda: self.now), \
                patch.object(self.h.time, 'monotonic', side_effect=lambda: self.now):
            self.h.run_provider_task('video-fixture', self.parameters, config or aimarket_config(), resume)
        return request

    def done(self):
        return {'status': 'Success', 'video_url': 'https://cdn.test/video.mp4'}

    def test_more_than_120_queries_finish_and_automatically_save_once(self):
        self.seed()
        messages = []
        def response(method, *args):
            if method == 'POST': return {'task_id': 'original-id'}
            messages.append(self.h.get('task', 'video-fixture').get('status_message'))
            return {'status': 'Processing'} if len(messages) <= 130 else self.done()
        request = self.run_job(response)
        task = self.h.get('task', 'video-fixture')
        self.assertEqual(task['status'], 'done')
        self.assertEqual(task['asset_save_status'], 'saved')
        self.assertIn('本次生成较慢，仍在处理中', messages)
        self.assertEqual(sum(c.args[0] == 'POST' for c in request.call_args_list), 1)
        self.h.ensure_video_asset(task['id'])
        self.h.save_task_asset(self.h.AssetFromTask(task_id=task['id']))
        assets = self.h.listing('asset')
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['url'], task['video_url'])
        self.assertEqual(assets[0]['task_id'], task['id'])

    def test_transient_errors_retry_same_task_without_another_submission(self):
        self.seed()
        request = self.run_job([{'task_id': 'original-id'}, TimeoutError('timeout'),
            RuntimeError('HTTP 502 temporary'), {'code': 503, 'message': 'busy'}, {'status': 'Processing'}, self.done()])
        self.assertEqual(self.h.get('task', 'video-fixture')['status'], 'done')
        self.assertEqual([c.args[0] for c in request.call_args_list], ['POST'] + ['GET'] * 5)
        self.assertEqual(len({c.args[1] for c in request.call_args_list[1:]}), 1)

    def test_only_explicit_provider_failure_is_terminal(self):
        self.seed()
        self.run_job([{'task_id': 'original-id'}, {'status': 'Failed', 'message': 'provider failure'}])
        self.assertEqual(self.h.get('task', 'video-fixture')['status'], 'failed')
        self.assertEqual(self.h.listing('asset'), [])

    def test_query_auth_failure_pauses_without_losing_provider_task(self):
        self.seed()
        self.run_job([{'task_id': 'original-id'}, HTTPException(401, 'expired')])
        task = self.h.get('task', 'video-fixture')
        self.assertEqual(task['status'], 'running')
        self.assertTrue(task['query_paused'])
        self.assertIsNone(task['error'])
        request = self.run_job([self.done()], resume=True)
        self.assertEqual([c.args[0] for c in request.call_args_list], ['GET'])

    def test_saving_failure_keeps_success_and_later_retries_without_generation(self):
        self.seed(status='done', video_url='https://cdn.test/video.mp4', auto_save_video=True)
        with patch.object(self.h, 'cache_generated_video', side_effect=OSError('disk error')), patch.object(self.h.time, 'sleep'):
            self.h.finalize_video_task('video-fixture')
        task = self.h.get('task', 'video-fixture')
        self.assertEqual(task['status'], 'done')
        self.assertEqual(task['asset_save_status'], 'pending')
        with patch.object(self.h, 'cache_generated_video', side_effect=self.cache):
            self.h.finalize_video_task('video-fixture')
        self.assertEqual(len(self.h.listing('asset')), 1)

    def test_reopening_interrupted_task_starts_one_get_worker(self):
        self.h.save('model_config', {'id': self.h.DEFAULT_MODEL_ID, 'kind': 'video', **aimarket_config(model_id='minimax-h3')})
        self.seed(status='failed', polling_interrupted=True, provider_task_id='original-id')
        with patch.object(self.h.threading, 'Thread') as thread:
            self.h.task('video-fixture')
            self.h.task('video-fixture')
        self.assertEqual(thread.call_count, 1)
        self.assertIs(thread.call_args.kwargs['args'][-1], True)
        request = self.run_job([self.done()], resume=True)
        self.assertEqual([c.args[0] for c in request.call_args_list], ['GET'])

    def test_page_session_revocation_does_not_stop_submitted_job_or_share_identity(self):
        browser = Browser(self.h.app)
        status, _, raw = browser.request('POST', '/api/integrations/aimarket/login', {
            'user': {'id': 'fixture-user', 'name': '成员', 'accountType': 'ordinary'},
            'agent': {'id': 'fixture', 'name': 'app'}, 'apiKey': 'sk-job-fixture'})
        self.assertEqual(status, 201)
        import json
        bid = json.loads(raw)['binding_id']
        market = self.h.app.state.market_integration
        record = market._bindings[bid]
        user = {'id': record['user_id'], 'role': 'user'}
        with access_scope(user):
            self.seed(_market_binding_id=bid, _market_actor_id=user['id'])
            def response(method, url, key, *args):
                used = model_api_key(url, key)
                if method == 'POST':
                    market.revoke(bid)
                    return {'task_id': 'original-id'}
                self.assertEqual(used, 'sk-job-fixture')
                return self.done()
            self.run_job(response, config=aimarket_config(api_key='sk-job-fixture'))
            self.assertEqual(self.h.get('task', 'video-fixture')['asset_save_status'], 'saved')
            asset = self.h.listing('asset')[0]
            self.assertEqual(asset['owner_id'], user['id'])
        self.assertEqual(browser.request('GET', '/api/auth/me', headers={'x-jht-market-session': bid})[0], 428)
        self.assertNotIn('sk-job-fixture', str(asset))

    def test_query_capability_cannot_call_another_url_or_post(self):
        url = 'https://gateway.test/query/original-id'
        with video_query_credentials(url, 'fixture-key'):
            self.assertEqual(model_api_key(url, ''), 'fixture-key')
            with self.assertRaises(HTTPException): model_api_key('https://gateway.test/submit', '')
            with self.assertRaises(HTTPException):
                open_model_request(Request(url, data=b'{}'), timeout=1, fallback=lambda *a, **k: None)

if __name__ == '__main__':
    unittest.main()
