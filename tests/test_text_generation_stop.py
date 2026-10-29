"""Text stop receipts interrupt local publication, never the provider thread."""
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_user_isolation as fixtures


class TextGenerationStopTests(unittest.TestCase):
    setUpClass = fixtures.UserIsolationTests.__dict__['setUpClass']
    tearDownClass = fixtures.UserIsolationTests.__dict__['tearDownClass']
    login = fixtures.UserIsolationTests.login
    request = fixtures.UserIsolationTests.request
    upload = fixtures.UserIsolationTests.upload

    def setUp(self):
        fixtures.UserIsolationTests.setUp(self)
        self.config = patch.object(self.h, 'model_for_generation', side_effect=lambda value: {
            **value, 'api_key': 'fixture-only', 'model_id': 'fixture-model'})
        self.config.start()
        self.addCleanup(self.config.stop)
        self.project = self.request(self.a, 'POST', '/api/projects', {'name': '脚本停止测试',
            'nodes': [{'id': 'script', 'type': 'script', 'title': '原稿',
                'script_data': {'title': '需要保留的原稿', 'scenes': [{'index': 1, 'description': '原画面'}]}}]}, 201)

    def stop(self, request_id, browser=None, **fields):
        return self.request(browser or self.a, 'POST', '/api/generation/stop',
            {'kind': 'text', 'request_id': request_id, **fields})

    def payload(self, request_id):
        return {'project_id': self.project['id'], 'node_id': 'script', 'request_id': request_id,
            'source_text': '小林回家', 'revision': self.project['revision']}

    def reply(self, content=None):
        return {'choices': [{'message': {'content': content if content is not None else json.dumps({
            'title': '新稿', 'scenes': [{'description': '小林推门回家', 'duration': '6s'}], 'resources': []}, ensure_ascii=False)}}]}

    def test_stop_before_submission_does_not_call_model_and_is_owner_scoped(self):
        self.stop('before')
        with patch.object(self.h, 'llm_json', return_value=self.reply('镜头缓缓向前推进')) as model:
            for path, body in (('/api/prompts/polish', {'text': '镜头前移', 'request_id': 'before'}),
                               ('/api/scripts/generate', self.payload('before'))):
                rejected = self.request(self.a, 'POST', path, body, 409)
                self.assertEqual(rejected['detail']['code'], 'generation_request_stopped')
            model.assert_not_called()
            other = self.request(self.b, 'POST', '/api/prompts/polish', {'text': '镜头前移', 'request_id': 'before'})
            self.assertEqual(other['text'], '镜头缓缓向前推进')
            self.assertEqual(model.call_count, 1)
            self.request(self.a, 'POST', '/api/prompts/polish', {'text': '重新发送', 'request_id': 'new-request'})
            self.assertEqual(model.call_count, 2)

    def test_stop_during_polish_discards_late_response_and_allows_new_id(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(*_):
            entered.set()
            self.assertTrue(release.wait(10))
            return self.reply('迟到的润色文字')
        with patch.object(self.h, 'llm_json', side_effect=delayed), ThreadPoolExecutor(max_workers=2) as pool:
            response = pool.submit(self.request, self.a, 'POST', '/api/prompts/polish',
                {'text': '镜头前移', 'request_id': 'polish-flight'}, 409)
            self.assertTrue(entered.wait(10))
            stopped = pool.submit(self.stop, 'polish-flight').result(3)
            self.assertTrue(stopped['provider_may_continue'])
            release.set()
            self.assertEqual(response.result(10)['detail']['code'], 'generation_request_stopped')
        with patch.object(self.h, 'llm_json', return_value=self.reply('新的润色文字')):
            self.assertEqual(self.request(self.a, 'POST', '/api/prompts/polish',
                {'text': '镜头前移', 'request_id': 'polish-new'})['text'], '新的润色文字')

    def test_stop_during_script_model_call_preserves_old_script_and_retry_saves(self):
        entered, release = threading.Event(), threading.Event()
        def delayed(*_):
            entered.set()
            self.assertTrue(release.wait(10))
            return self.reply()
        before = self.request(self.a, 'GET', '/api/projects/' + self.project['id'])
        with patch.object(self.h, 'llm_json', side_effect=delayed), ThreadPoolExecutor(max_workers=2) as pool:
            response = pool.submit(self.request, self.a, 'POST', '/api/scripts/generate', self.payload('script-flight'), 409)
            self.assertTrue(entered.wait(10))
            stopped = pool.submit(self.stop, 'script-flight', project_id=self.project['id'], node_id='script').result(3)
            self.assertEqual(stopped['status'], 'stop_requested')
            release.set()
            self.assertEqual(response.result(10)['detail']['code'], 'generation_request_stopped')
        self.assertEqual(self.request(self.a, 'GET', '/api/projects/' + self.project['id']), before)
        with patch.object(self.h, 'llm_json', return_value=self.reply()):
            retried = self.request(self.a, 'POST', '/api/scripts/generate', self.payload('script-new'))
        self.assertEqual(retried['script_data']['title'], '新稿')
        self.assertEqual(retried['project_revision'], before['revision'] + 1)

    def test_stop_after_response_before_final_save_is_checked_under_save_lock(self):
        entered, release = threading.Event(), threading.Event()
        original = self.h.merge_preparation_resources
        def delayed(*args):
            entered.set()
            self.assertTrue(release.wait(10))
            return original(*args)
        before = self.request(self.a, 'GET', '/api/projects/' + self.project['id'])
        with patch.object(self.h, 'llm_json', return_value=self.reply()), \
                patch.object(self.h, 'merge_preparation_resources', side_effect=delayed), ThreadPoolExecutor(max_workers=2) as pool:
            response = pool.submit(self.request, self.a, 'POST', '/api/scripts/generate', self.payload('save-race'), 409)
            self.assertTrue(entered.wait(10))
            pool.submit(self.stop, 'save-race').result(3)
            release.set()
            self.assertEqual(response.result(10)['detail']['code'], 'generation_request_stopped')
        self.assertEqual(self.request(self.a, 'GET', '/api/projects/' + self.project['id']), before)

    def test_foreign_scope_cannot_stop_text_request(self):
        rejected = self.request(self.b, 'POST', '/api/generation/stop',
            {'kind': 'text', 'request_id': 'foreign', 'project_id': self.project['id']}, 404)
        self.assertTrue(rejected['detail'])
        self.assertFalse(self.h.listing('generation_stop'))
        self.request(self.a, 'POST', '/api/generation/stop', {'kind': 'text', 'task_id': 'not-enough'}, 422)


if __name__ == '__main__':
    unittest.main()
