"""Completed-video recovery uses only the original GET and persists local bytes."""
import json
import os
import socket
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import test_account_video_models as accounts
from market_service import model_api_key
from video_file_recovery import VideoFileRecovery


class VideoFileRecoveryTests(unittest.TestCase):
    setUpClass = classmethod(accounts.AccountVideoTests.setUpClass.__func__)
    tearDownClass = classmethod(accounts.AccountVideoTests.tearDownClass.__func__)
    setUp = accounts.AccountVideoTests.setUp
    call = accounts.AccountVideoTests.call
    login = accounts.AccountVideoTests.login
    create_task = accounts.AccountVideoTests.create_task

    def ready(self, index=1, **changes):
        task = self.create_task(index)
        with self.h.worker_scope(task):
            config = self.h.internal_registry_model('video', task['params']['model_config_id'])
        values = dict(status='done', provider_task_id='original-job', video_url='https://cdn.test/expired.mp4',
                      video_source=self.h.video_source_snapshot(config), video_cache_status='failed',
                      video_cache_error='服务器下载视频超时')
        values.update(changes)
        task = self.h.update_task(task['id'], **values)
        return task, self.h.ensure_video_asset(task['id']), config

    def result(self, index=1, task_id='original-job'):
        value = {'id': task_id, 'status': 'succeeded',
                 'content': {'video_url' if index else 'url': 'https://cdn.test/fresh.mp4'}}
        return value if index else {'task': value}

    def cache(self, url):
        self.assertEqual(url, 'https://cdn.test/fresh.mp4')
        (self.h.UPLOADS / 'recovered.mp4').write_bytes(b'recovered video bytes')
        return '/uploads/recovered.mp4'

    def test_async_retry_requeries_each_official_provider_and_keeps_local_result(self):
        for index in (0, 1):
            with self.subTest(provider=index):
                task, asset, config = self.ready(index)
                before = time.monotonic()
                with patch.object(self.h, 'provider_json') as query:
                    queued = self.call('POST', f'/api/assets/{asset["id"]}/retry-file', {}, self.owner, 202)
                    self.call('POST', f'/api/assets/{asset["id"]}/retry-file', {}, self.owner, 202)
                query.assert_not_called()
                self.assertLess(time.monotonic() - before, 3)
                self.assertEqual(queued['file_status'], 'pending')
                self.h.app.state.market_integration.revoke(self.owner)
                def get(method, url, key):
                    self.assertEqual(method, 'GET')
                    self.assertTrue(url.endswith('/original-job'))
                    self.assertEqual(model_api_key(url, key), config['api_key'])
                    return self.result(index)
                with patch.object(self.h, 'provider_json', side_effect=get) as query, \
                        patch.object(self.h, 'cache_generated_video', side_effect=self.cache) as download:
                    # A new service sees the pending request after a process restart.
                    service = VideoFileRecovery(self.h.AgentHost())
                    service.process(task['id']); service.process(task['id'])
                query.assert_called_once(); download.assert_called_once()
                final = self.h.get('task', task['id'])
                saved = self.h.get('asset', asset['id'])
                self.assertEqual(final['status'], 'done')
                self.assertEqual(final['video_url'], '/uploads/recovered.mp4')
                self.assertEqual(saved['file_status'], 'cached')
                self.assertEqual(saved['file_error'], '')
                self.assertFalse(saved['in_library'])
                self.assertEqual(saved['id'], asset['id'])
                self.assertEqual(final['provider_video_url'], 'https://cdn.test/fresh.mp4')
                self.owner = self.login('owner')

    def test_timeout_retries_are_persistent_bounded_and_do_not_regenerate(self):
        task, _, _ = self.ready(video_cache_status='pending')
        with patch.object(self.h, 'cache_generated_video', side_effect=URLError(socket.timeout('timed out'))), \
                patch.object(self.h, 'provider_json', return_value=self.result()) as query:
            self.h.finalize_video_task(task['id'])
            first = self.h.get('task', task['id'])
            self.assertEqual(first['video_cache_status'], 'pending')
            self.assertGreater(first['video_cache_retry_at'], time.time())
            service = VideoFileRecovery(self.h.AgentHost())
            service.process(task['id'])
            query.assert_not_called()  # Reading history cannot bypass the backoff.
            for _ in range(2):
                self.h.update_task(task['id'], video_cache_retry_at=1)
                service.process(task['id'])
        final = self.h.get('task', task['id'])
        self.assertEqual(final['status'], 'done')
        self.assertEqual(final['video_cache_status'], 'failed')
        self.assertEqual(final['video_cache_attempts'], 3)
        self.assertEqual(final['video_cache_retry_at'], 0)
        self.assertEqual([c.args[0] for c in query.call_args_list], ['GET', 'GET'])
        self.assertIn('超时', self.h.get('asset', final['asset_id'])['file_error'])

    def test_refresh_during_file_transfer_does_not_launch_another_worker(self):
        task, _, _ = self.ready(video_cache_status='pending')
        def download(url):
            with patch.object(self.h.threading, 'Thread') as worker:
                self.h.refresh_video_task(task['id'])
            worker.assert_not_called()
            raise TimeoutError('slow transfer')
        with patch.object(self.h, 'cache_generated_video', side_effect=download):
            self.h.finalize_video_task(task['id'])
        with patch.object(self.h, 'cache_generated_video') as download:
            self.h.finalize_video_task(task['id'])
        download.assert_not_called()

    def test_expired_url_on_first_download_is_refreshed_once(self):
        task, _, _ = self.ready(video_cache_status='pending')
        old = task['video_url']
        def download(url):
            if url == old:
                raise HTTPError(old, 403, 'expired', {}, None)
            return self.cache(url)
        with patch.object(self.h, 'cache_generated_video', side_effect=download) as cache, \
                patch.object(self.h, 'provider_json', return_value=self.result()) as query:
            self.h.finalize_video_task(task['id'])
        self.assertEqual(cache.call_count, 2)
        query.assert_called_once()
        self.assertEqual(self.h.get('task', task['id'])['video_cache_status'], 'cached')

    def test_missing_local_file_and_empty_legacy_url_can_be_recovered(self):
        for index, url in enumerate(('/uploads/lost.mp4', '')):
            task, asset, _ = self.ready(index, video_url=url, video_cache_status='cached' if url else 'expired')
            self.h.save('asset', {**asset, 'in_library': True})
            self.h.request_video_file_recovery(task['id'])
            with patch.object(self.h, 'provider_json', return_value=self.result(index)), \
                    patch.object(self.h, 'cache_generated_video', side_effect=self.cache):
                self.h.video_files.process(task['id'])
            self.assertTrue(self.h.get('asset', asset['id'])['in_library'])
            self.assertEqual(self.h.get('asset', asset['id'])['file_status'], 'cached')

    def test_wrong_task_and_changed_credentials_do_not_download_other_results(self):
        task, asset, _ = self.ready()
        with patch.object(self.h, 'provider_json', return_value=self.result(task_id='someone-elses-job')), \
                patch.object(self.h, 'cache_generated_video') as cache:
            self.h.finalize_video_task(task['id'])
        cache.assert_not_called()
        self.assertIn('编号不一致', self.h.get('asset', asset['id'])['file_error'])
        self.document['accounts'][0]['models'][1]['api_key'] = 'changed-fixture'
        self.h.account_video_models.path.write_text(json.dumps(self.document), encoding='utf-8')
        with patch.object(self.h, 'provider_json') as query:
            self.h.finalize_video_task(task['id'])
        query.assert_not_called()
        self.assertIn('配置已变更', self.h.get('asset', asset['id'])['file_error'])

    def test_deleted_asset_and_disabled_account_do_not_recover(self):
        task, asset, _ = self.ready()
        self.h.save('asset', {**asset, 'deleted_at': time.time()})
        with patch.object(self.h, 'provider_json') as query:
            self.h.video_files.process(task['id'])
        query.assert_not_called()
        task, asset, _ = self.ready(0)
        self.h.request_video_file_recovery(task['id'])
        with self.h.connect() as db:
            db.execute('UPDATE auth_users SET active=0 WHERE id=?', (self.user['id'],))
        with patch.object(self.h, 'provider_json') as query:
            self.h.video_files.process(task['id'])
        query.assert_not_called()
        self.assertIn('停用', self.h.get('asset', asset['id'])['file_error'])

    def test_recovery_endpoint_keeps_owner_isolation(self):
        _, asset, _ = self.ready()
        self.call('POST', f'/api/assets/{asset["id"]}/retry-file', {}, self.other, 404)

    def test_market_reconnection_refreshes_only_original_actors_query_key(self):
        task, asset, _ = self.ready()
        self.h.update_task(task['id'], video_source=None,
            params={**task['params'], 'model_config_id': self.h.DEFAULT_MODEL_ID, 'model': 'minimax-h3'})
        self.h.app.state.market_integration.revoke(self.owner)
        self.owner = self.login('owner')
        self.call('POST', f'/api/assets/{asset["id"]}/retry-file', {}, self.owner, 202)
        def query(method, url, key):
            self.assertEqual(method, 'GET')
            self.assertTrue(url.startswith('https://gateway.example.test/'))
            self.assertEqual(model_api_key(url, key), 'sk-fixture-session-owner')
            return {'status': 'Success', 'video_url': 'https://cdn.test/fresh.mp4'}
        with patch.dict(os.environ, {'JIANHUITONG_MARKET_MODEL_ORIGIN': 'https://gateway.example.test'}), \
                patch.object(self.h, 'provider_json', side_effect=query) as get, \
                patch.object(self.h, 'cache_generated_video', side_effect=self.cache):
            self.h.video_files.process(task['id'])
        get.assert_called_once()
        self.assertEqual(self.h.get('asset', asset['id'])['file_status'], 'cached')


if __name__ == '__main__':
    unittest.main()
