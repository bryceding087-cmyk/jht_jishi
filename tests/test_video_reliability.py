"""Long video jobs, recoverable downloads, history and manual library saves."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

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

    def test_long_job_enters_history_and_manual_save_reuses_the_file(self):
        self.seed()
        messages = []
        def response(method, *args, **kwargs):
            if method == 'POST': return {'task_id': 'original-id'}
            messages.append(self.h.get('task', 'video-fixture').get('status_message'))
            return {'status': 'Processing'} if len(messages) <= 130 else self.done()
        request = self.run_job(response)
        task = self.h.get('task', 'video-fixture')
        self.assertEqual(task['status'], 'done')
        self.assertEqual(task['asset_save_status'], 'history')
        self.assertEqual(self.h.assets(collection='library'), [])
        self.assertIn('本次生成较慢，仍在处理中', messages)
        self.assertEqual(sum(c.args[0] == 'POST' for c in request.call_args_list), 1)
        self.h.ensure_video_asset(task['id'])
        self.h.save_task_asset(self.h.AssetFromTask(task_id=task['id']))
        self.h.save_task_asset(self.h.AssetFromTask(task_id=task['id']))
        assets = self.h.listing('asset')
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]['url'], task['video_url'])
        self.assertEqual(assets[0]['task_id'], task['id'])
        self.assertTrue(assets[0]['in_library'])
        self.assertEqual(len(self.h.assets(collection='library')), 1)

    def test_transient_errors_retry_same_task_without_another_submission(self):
        self.seed()
        request = self.run_job([{'task_id': 'original-id'}, TimeoutError('timeout'),
            RuntimeError('HTTP 502 temporary'), {'code': 503, 'message': 'busy'}, {'status': 'Processing'}, self.done()])
        self.assertEqual(self.h.get('task', 'video-fixture')['status'], 'done')
        self.assertEqual([c.args[0] for c in request.call_args_list], ['POST'] + ['GET'] * 5)
        self.assertEqual(len({c.args[1] for c in request.call_args_list[1:]}), 1)

    def test_library_removal_keeps_completed_video_and_does_not_download_or_generate(self):
        self.seed(status='done', video_url=self.cache('unused'))
        asset = self.h.ensure_video_asset('video-fixture')
        self.h.save_asset_to_library(asset['id'])
        with patch.object(self.h, 'provider_json') as provider, patch.object(self.h, 'cache_generated_video') as download:
            removed = self.h.remove_asset_from_library(asset['id'])
            task = self.h.refresh_video_task('video-fixture')
            self.assertFalse(removed['in_library'])
            self.assertEqual(task['status'], 'done')
            self.assertEqual(task['asset_save_status'], 'history')
            self.assertEqual(task['video_cache_status'], 'cached')
            self.assertFalse(self.h.ensure_video_asset(task['id'])['in_library'])
            self.assertEqual(len(self.h.assets()), 1)
            self.assertEqual(self.h.assets(collection='library'), [])
            self.assertEqual((self.h.UPLOADS / 'fixture.mp4').read_bytes(), b'fixture local video')
            self.h.save_asset_to_library(asset['id'])
            self.assertEqual(self.h.get('task', task['id'])['asset_save_status'], 'saved')
            provider.assert_not_called()
            download.assert_not_called()

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
        self.assertEqual(task['asset_save_status'], 'history')
        self.assertEqual(task['video_cache_status'], 'failed')
        asset = self.h.assets()[0]
        self.assertEqual(asset['file_status'], 'failed')
        self.assertFalse(asset['in_library'])
        with patch.object(self.h, 'cache_generated_video') as download, patch.object(self.h.threading, 'Thread') as thread:
            for _ in range(4):
                self.h.refresh_video_task(task['id'])
                self.h.assets()
            download.assert_not_called()
            thread.assert_not_called()
        with patch.object(self.h, 'cache_generated_video', side_effect=self.cache):
            result = self.h.retry_asset_file(asset['id'])
            self.assertEqual(result['file_status'], 'pending')
            self.h.video_files.process(task['id'])
            result = self.h.get('asset', asset['id'])
        self.assertEqual(result['file_status'], 'cached')
        self.assertFalse(result['in_library'])
        self.h.save_asset_to_library(asset['id'])
        self.assertEqual(len(self.h.listing('asset')), 1)

    def test_file_errors_describe_download_stage_without_exposing_signed_urls(self):
        import socket
        import ssl
        from urllib.error import HTTPError, URLError
        cases = [
            (URLError(socket.timeout('timed out')), '下载视频超时'),
            (URLError(socket.gaierror('dns failure')), 'DNS'),
            (URLError(ssl.SSLError('certificate failure')), '证书'),
            (URLError(ConnectionRefusedError('refused')), '出口网络'),
            (PermissionError('denied'), '写入权限'),
            (HTTPError('https://cdn.test/video?secret=private', 403, 'forbidden', {}, None), 'HTTP 403'),
        ]
        for error, expected in cases:
            message = self.h.video_file_error_message(error)
            self.assertIn(expected, message)
            self.assertNotIn('secret=private', message)
            self.assertNotIn('模型可能仍在处理', message)

    def test_legacy_pending_failure_stops_retrying_even_if_history_is_opened_first(self):
        self.seed(status='done', video_url='https://cdn.test/video.mp4', auto_save_video=True,
            asset_save_status='pending', asset_save_error='old failed download', video_cache_status='pending')
        with patch.object(self.h.threading, 'Thread') as thread:
            self.assertEqual(self.h.assets()[0]['file_status'], 'failed')
            current = self.h.refresh_video_task('video-fixture')
            self.assertEqual(current['video_cache_status'], 'failed')
            self.assertEqual(current['asset_save_status'], 'history')
            self.assertIn('恢复视频', current['status_message'])
            thread.assert_not_called()

    def test_failed_manual_save_does_not_claim_library_membership_or_resubmit(self):
        self.seed(status='done', video_url='https://cdn.test/video.mp4')
        asset = self.h.ensure_video_asset('video-fixture')
        with patch.object(self.h, 'cache_generated_video', side_effect=OSError('disk error')), patch.object(self.h, 'provider_json') as provider:
            with self.assertRaises(HTTPException) as raised:
                self.h.save_asset_to_library(asset['id'])
            self.assertEqual(raised.exception.status_code, 502)
            provider.assert_not_called()
        self.assertEqual(self.h.assets(collection='library'), [])
        self.assertEqual(self.h.get('task', 'video-fixture')['status'], 'done')

    def test_deleted_history_is_not_recreated(self):
        self.seed(status='done', video_url=self.cache('unused'))
        asset = self.h.ensure_video_asset('video-fixture')
        self.h.save('asset', {**asset, 'deleted_at': 1001})
        self.assertEqual(self.h.assets(), [])
        with self.assertRaises(HTTPException):
            self.h.save_asset_to_library(asset['id'])

    def test_concurrent_saves_share_one_download_and_one_record(self):
        self.seed(status='done', video_url='https://cdn.test/video.mp4')
        with patch.object(self.h, 'cache_generated_video', side_effect=self.cache) as download, ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.h.save_task_asset(self.h.AssetFromTask(task_id='video-fixture')), range(4)))
        download.assert_called_once()
        self.assertEqual(len({asset['id'] for asset in results}), 1)
        self.assertTrue(all(asset['in_library'] for asset in results))
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
            def response(method, url, key, *args, **kwargs):
                used = model_api_key(url, key)
                if method == 'POST':
                    market.revoke(bid)
                    return {'task_id': 'original-id'}
                self.assertEqual(used, 'sk-job-fixture')
                return self.done()
            self.run_job(response, config=aimarket_config(api_key='sk-job-fixture'))
            self.assertEqual(self.h.get('task', 'video-fixture')['asset_save_status'], 'history')
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
