"""Deployment profile and mixed-credential transport, with no live model calls."""
import base64
from dataclasses import replace
import io
import json
import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

import test_market_integration as fixtures
from test_audio_generation import wav_bytes
from market_service import audio_api_key, model_api_key, model_for_generation
from model_gateway import LOCAL_MODEL_ORIGIN, DOCKER_MODEL_ORIGIN, MODEL_ORIGIN_ENV
from test_provider_adapters import params


AUDIO_URL = 'https://openspeech.bytedance.com/api/v3/tts/create'
AUDIO_KEY = 'fixture-audio-server-only'


class ProfileTests(unittest.TestCase):
    gateway = DOCKER_MODEL_ORIGIN
    setUpClass = fixtures.MarketIntegrationTests.__dict__['setUpClass']
    tearDownClass = fixtures.MarketIntegrationTests.__dict__['tearDownClass']
    login = fixtures.MarketIntegrationTests.login
    request = fixtures.MarketIntegrationTests.request
    context = fixtures.MarketIntegrationTests.context
    bind = fixtures.MarketIntegrationTests.bind
    task = fixtures.MarketIntegrationTests.task
    assert_not_persisted = fixtures.MarketIntegrationTests.assert_not_persisted

    def setUp(self):
        environment = patch.dict(os.environ, {MODEL_ORIGIN_ENV: self.gateway})
        environment.start()
        self.addCleanup(environment.stop)
        fixtures.MarketIntegrationTests.setUp(self)
        with self.h.connect() as db:
            db.execute('DELETE FROM records')
        self.market.settings = replace(self.market.settings, model_origin=self.gateway)
        self.h.AUDIO_TASK_FALLBACKS.clear()
        self.h.ACTIVE_AUDIO_TASKS.clear()

    def test_defaults_and_environment_keys_match_deployment(self):
        with patch.dict(os.environ, {'JIANHUITONG_MARKET_API_KEY': '', 'OPENAI_API_KEY': 'old-openai',
                                     'LLM_API_KEY': 'old-text', 'SEEDREAM_API_KEY': 'old-image',
                                     'SEEDANCE_API_KEY': 'old-video', 'SEEDAUDIO_API_KEY': AUDIO_KEY}):
            models = {kind: self.h.internal_registry_model(kind) for kind in ('text', 'image', 'video', 'audio')}
        for kind, model_id, path in (
            ('text', 'deepseek-v4-flash-vision-exp-0817', '/v1/chat/completions'),
            ('image', 'doubao-seedream-5.0-pro', '/v1/images/generations'),
            ('video', 'minimax-h3', '/v1/video_generation'),
        ):
            model = models[kind]
            self.assertEqual(model.get('model') or model['model_id'], model_id)
            self.assertEqual(model['base_url'] + model['api_path'], self.gateway + path)
            self.assertEqual(model['api_key'], '')
        self.assertEqual(models['audio']['api_key'], AUDIO_KEY)
        self.assertEqual(models['audio']['base_url'] + models['audio']['api_path'], AUDIO_URL)
        self.assertEqual(models['audio']['timeout_seconds'], 300)
        self.assertEqual(models['video']['query_path'], '/v1/query/video_generation/{task_id}')
        request = self.h.Task(project_id='test', node_id='video', prompt='test')
        self.assertEqual((request.resolution, request.duration, request.aspect_ratio), ('768p', 4, '16:9'))

    def test_text_request_uses_sdk_key_and_exact_model(self):
        binding = self.bind()
        record = self.h.get('image_task', self.task(binding)['id'])
        outbound = []

        class Opener:
            def open(self, request, timeout):
                outbound.append(request)
                return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')

        messages = [{'role': 'user', 'content': 'normal'}]
        with self.h.worker_scope(record), patch('market_service.build_opener', return_value=Opener()):
            self.h.llm_json(self.h.internal_llm_config(), messages)
        request = outbound[0]
        self.assertEqual(request.full_url, self.gateway + '/v1/chat/completions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + fixtures.KEY)
        body = json.loads(request.data)
        self.assertEqual(body['model'], 'deepseek-v4-flash-vision-exp-0817')
        self.assertEqual(body['messages'], messages)
        self.assert_not_persisted()

    def test_image_worker_sends_n_without_ark_switches_and_saves_result(self):
        binding = self.bind()
        task = self.task(binding)
        outbound = []
        png = io.BytesIO()
        Image.new('RGB', (16, 16), 'blue').save(png, format='PNG')

        class Opener:
            def open(self, request, timeout):
                outbound.append(request)
                return io.BytesIO(b'{"data":[{"url":"https://cdn.example/image.png"}]}')

        with patch('market_service.build_opener', return_value=Opener()), \
                patch.object(self.h, 'download_generated_image', return_value=(png.getvalue(), '.png')):
            self.h.run_image_generation(task['id'], self.h.internal_image_config())
        self.assertEqual(self.h.get('image_task', task['id'])['status'], 'succeeded')
        request = outbound[0]
        self.assertEqual(request.full_url, self.gateway + '/v1/images/generations')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + fixtures.KEY)
        body = json.loads(request.data)
        self.assertEqual(body['model'], 'doubao-seedream-5.0-pro')
        self.assertEqual(body['n'], 1)
        for field in ('stream', 'watermark', 'response_format'):
            self.assertNotIn(field, body)
        self.assert_not_persisted()

    def audio_task(self, binding):
        with patch.object(self.h.threading, 'Thread'):
            return self.request(self.member, 'POST', '/api/audio/generations',
                                {'prompt': 'gentle music', 'mode': 'music', 'request_id': 'direct-audio'},
                                expected=202, binding=binding)

    def test_market_audio_worker_uses_server_key_and_saves_result(self):
        binding = self.bind()
        outbound = []
        audio = base64.b64encode(wav_bytes()).decode()

        class Response(io.BytesIO):
            headers = {'X-Tt-Logid': 'fixture-trace'}

        class Opener:
            def open(self, request, timeout):
                outbound.append((request, timeout))
                return Response(json.dumps({'code': 0, 'audio': audio}).encode())

        with patch.dict(os.environ, {'SEEDAUDIO_API_KEY': AUDIO_KEY}):
            task = self.audio_task(binding)
            with patch('audio_service.build_opener', return_value=Opener()):
                self.h.run_audio_generation(task['id'], self.h.internal_registry_model('audio'))
        self.assertEqual(self.h.get('audio_task', task['id'])['status'], 'succeeded')
        request, timeout = outbound[0]
        self.assertEqual(request.full_url, AUDIO_URL)
        self.assertEqual(timeout, 300)
        self.assertEqual(request.get_header('X-api-key'), AUDIO_KEY)
        self.assertIsNone(request.get_header('Authorization'))
        self.assertEqual(json.loads(request.data)['model'], 'seed-audio-1.0')
        self.assertNotIn(fixtures.KEY, str(request.header_items()))
        with self.h.connect() as db:
            self.assertNotIn(AUDIO_KEY, json.dumps([row[0] for row in db.execute('SELECT body FROM records')]))
        self.assert_not_persisted()

    def test_registry_reports_audio_server_key_and_gateway_session_key(self):
        binding = self.bind()
        for key in ('', AUDIO_KEY):
            with patch.dict(os.environ, {'SEEDAUDIO_API_KEY': key}):
                registry = self.request(self.member, 'GET', '/api/model-registry', binding=binding)
            audio = next(model for model in registry['models'] if model['kind'] == 'audio')
            self.assertEqual(audio['credential_source'], 'administrator')
            self.assertEqual(audio['api_key_set'], bool(key))
            self.assertNotIn(AUDIO_KEY, json.dumps(registry))
            for model in registry['models']:
                if model['kind'] != 'audio':
                    self.assertEqual(model['credential_source'], 'market-session')
                    self.assertTrue(model['api_key_set'])

    def test_audio_exception_does_not_allow_other_hosts_or_paths(self):
        binding = self.bind()
        record = self.h.get('image_task', self.task(binding)['id'])
        with self.h.worker_scope(record):
            for url in (AUDIO_URL + '?key=x', AUDIO_URL + '/other',
                        AUDIO_URL.replace('https:', 'http:'),
                        AUDIO_URL.replace('openspeech.bytedance.com', 'evil.invalid')):
                with self.subTest(url=url), self.assertRaises(HTTPException):
                    audio_api_key(url, AUDIO_KEY)
            with self.assertRaises(HTTPException):
                model_api_key(AUDIO_URL, AUDIO_KEY)
            with self.assertRaises(HTTPException):
                model_for_generation({**self.h.DEFAULT_AUDIO_MODEL, 'api_path': '/other', 'api_key': AUDIO_KEY})

    def test_revoked_market_audio_does_not_fall_back_to_server_key(self):
        binding = self.bind()
        with patch.dict(os.environ, {'SEEDAUDIO_API_KEY': AUDIO_KEY}):
            task = self.audio_task(binding)
            self.market.revoke(binding)
            with patch('audio_service.build_opener') as upstream:
                self.h.run_audio_generation(task['id'], self.h.internal_registry_model('audio'))
        upstream.assert_not_called()
        self.assertEqual(self.h.get('audio_task', task['id'])['status'], 'failed')


    def test_saved_models_and_provider_presets_follow_deployment_without_rewriting_data(self):
        for original in (LOCAL_MODEL_ORIGIN, DOCKER_MODEL_ORIGIN):
            for kind in ('text', 'image', 'video'):
                with self.subTest(original=original, kind=kind):
                    builtin = self.h.MODEL_BUILTINS[kind]
                    self.h.save(self.h.MODEL_STORES[kind], {
                        **builtin, 'base_url': original, 'api_key': 'saved-fixture-key'})
                    model = self.h.internal_registry_model(kind)
                    self.assertEqual(model['base_url'], self.gateway)
                    self.assertEqual(model['api_key'], 'saved-fixture-key')
                    self.assertEqual(self.h.raw_record(self.h.MODEL_STORES[kind], builtin['id'])['base_url'], original)
                    self.assertEqual(self.h.registry_public(kind, model)['base_url'], self.gateway)
                    preset = self.h.provider_preset(kind, {'provider_id': 'aimarket'})
                    self.assertEqual(preset['defaults']['base_url'], self.gateway)

    def test_standalone_requests_use_selected_gateway_for_all_three_model_kinds(self):
        responses = [
            io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}'),
            io.BytesIO(b'{"data":[{"url":"https://cdn.example/image.png"}]}'),
            io.BytesIO(b'{"task_id":"video-42"}'),
            io.BytesIO(b'{"status":"Success","video_url":"https://cdn.example/video.mp4"}'),
        ]
        png = io.BytesIO()
        Image.new('RGB', (16, 16), 'blue').save(png, format='PNG')
        with patch.dict(os.environ, {'JIANHUITONG_MARKET_API_KEY': 'standalone-fixture-key'}), \
                patch.object(self.h, 'urlopen', side_effect=responses) as upstream, \
                patch.object(self.h, 'download_generated_image', return_value=(png.getvalue(), '.png')), \
                patch.object(self.h.time, 'sleep'), patch.object(self.h, 'finalize_video_task'):
            self.h.llm_json(self.h.internal_llm_config(), [{'role': 'user', 'content': 'test'}])
            task = self.task(None, request_id='standalone-gateway')
            self.h.run_image_generation(task['id'], self.h.internal_image_config())
            self.assertEqual(self.h.get('image_task', task['id'])['status'], 'succeeded')
            self.h.save('task', {'id': 'standalone-video', 'status': 'queued'})
            self.h.run_provider_task('standalone-video', params(), self.h.internal_registry_model('video'))
            self.assertEqual(self.h.get('task', 'standalone-video')['status'], 'done')
        requests = [call.args[0] for call in upstream.call_args_list]
        self.assertEqual([request.full_url for request in requests], [self.gateway + path for path in (
            '/v1/chat/completions', '/v1/images/generations', '/v1/video_generation',
            '/v1/query/video_generation/video-42')])
        self.assertTrue(all(request.get_header('Authorization') == 'Bearer standalone-fixture-key'
                            for request in requests))

    def test_sdk_video_submission_and_query_use_selected_gateway(self):
        binding = self.bind()
        stamped = self.h.get('image_task', self.task(binding)['id'])
        record = self.h.save('task', {**stamped, 'id': 'sdk-video', 'status': 'queued'})
        requests = []
        responses = iter((b'{"task_id":"sdk-42"}',
                          b'{"status":"Success","video_url":"https://cdn.example/video.mp4"}'))

        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                return io.BytesIO(next(responses))

        with self.h.worker_scope(record), patch('market_service.build_opener', return_value=Opener()), \
                patch.object(self.h.time, 'sleep'), patch.object(self.h, 'finalize_video_task'):
            config = model_for_generation(self.h.internal_registry_model('video'))
            self.h.run_provider_task(record['id'], params(), config)
        self.assertEqual(self.h.get('task', record['id'])['status'], 'done')
        self.assertEqual([request.full_url for request in requests], [
            self.gateway + '/v1/video_generation', self.gateway + '/v1/query/video_generation/sdk-42'])
        self.assertTrue(all(request.get_header('Authorization') == 'Bearer ' + fixtures.KEY for request in requests))
        self.assert_not_persisted()


class LocalProfileTests(ProfileTests):
    gateway = LOCAL_MODEL_ORIGIN


if __name__ == '__main__':
    unittest.main()
