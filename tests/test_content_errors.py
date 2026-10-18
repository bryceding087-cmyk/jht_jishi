"""Moderation failures get one message; unrelated failures retain their meaning."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from content_errors import CONTENT_REJECTION_MESSAGE, normalize_content_error
from image_service import image_error_message
from audio_service import audio_error_message
from provider_adapters import video_result, video_response_error


class ContentErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jht-content-errors-')
        spec = importlib.util.spec_from_file_location('content_error_test_app', ROOT / 'app.py')
        cls.host = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'JIANHUITONG_DATA_DIR': cls.directory.name}):
            spec.loader.exec_module(cls.host)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def http_error(self, body):
        return HTTPError('https://provider.example/generate', 400, 'Bad Request', {},
                         io.BytesIO(json.dumps(body).encode()))

    def test_shared_cases(self):
        cases = json.loads((ROOT / 'tests/content_rejection_cases.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(error=case['error']):
                expected = CONTENT_REJECTION_MESSAGE if case['rejected'] else case['error']
                self.assertEqual(normalize_content_error(case['error']), expected)

    def test_image_and_audio_http_failures_preserve_rejection_code(self):
        # The message alone is generic; the error code identifies the rejection.
        body = {'error': {'code': 'InputImageSensitiveContentDetected', 'message': 'Request rejected'}}
        for formatter in (image_error_message, audio_error_message):
            with self.subTest(formatter=formatter.__name__):
                self.assertEqual(formatter(self.http_error(body)), CONTENT_REJECTION_MESSAGE)
                self.assertEqual(formatter(RuntimeError('OutputImageSensitiveContentDetected')), CONTENT_REJECTION_MESSAGE)
                plain = HTTPError('https://provider.example/generate', 400, 'Bad Request', {}, io.BytesIO(b'content_policy_violation'))
                self.assertEqual(formatter(plain), CONTENT_REJECTION_MESSAGE)
                self.assertNotEqual(formatter(self.http_error({'error': {'message': 'invalid image size'}})), CONTENT_REJECTION_MESSAGE)

    def test_video_submission_and_poll_failures(self):
        self.assertEqual(video_response_error({'code': 400, 'message': 'content_policy_violation'}), CONTENT_REJECTION_MESSAGE)
        result = video_result({'status': 'failed', 'error': {'code': 'OutputVideoSensitiveContentDetected', 'message': 'Request rejected'}})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], CONTENT_REJECTION_MESSAGE)
        self.assertEqual(video_result({'status': 'failed', 'error': {'code': 'SetLimitExceeded'}})['error'], {'code': 'SetLimitExceeded'})

    def test_provider_http_response_normalized_before_truncation(self):
        body = {'padding': 'x' * 600, 'error': {'code': 'OutputImageSensitiveContentDetected'}}
        with patch.object(self.host, 'urlopen', side_effect=self.http_error(body)):
            with self.assertRaisesRegex(RuntimeError, '^' + CONTENT_REJECTION_MESSAGE + '$'):
                self.host.provider_json('POST', 'https://provider.example/generate', 'test-key', {})

    def test_llm_filter_is_reported_as_content_rejection(self):
        with patch.object(self.host, 'provider_json', return_value={'choices': [{'finish_reason': 'content_filter', 'message': {'content': ''}}]}):
            with self.assertRaisesRegex(RuntimeError, '^' + CONTENT_REJECTION_MESSAGE + '$'):
                self.host.llm_json({'api_key': 'test-key', 'base_url': 'https://provider.example', 'model_id': 'test'}, [])
        self.assertEqual(self.host.provider_error_message(RuntimeError('DataInspectionFailed')), CONTENT_REJECTION_MESSAGE)
        self.assertIn('超时', self.host.provider_error_message(TimeoutError('timed out')))


if __name__ == '__main__':
    unittest.main()
