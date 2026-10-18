"""Seed Audio contract and task lifecycle tests, isolated from workspace data."""

import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from fastapi import HTTPException
from PIL import Image

from audio_service import (AUDIO_MODE_INSTRUCTIONS, IMAGE_SOUND_INSTRUCTION, AudioGenerationRequest,
                           audio_payload, audio_reference, decode_audio_result, image_reference, request_audio)


def wav_bytes(seconds=1, rate=8000):
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b'\x00\x00' * int(seconds * rate))
    return output.getvalue()


def png_bytes():
    output = io.BytesIO()
    Image.new('RGB', (16, 16), 'red').save(output, 'PNG')
    return output.getvalue()


class AudioContractTests(unittest.TestCase):
    def test_modes_use_documented_fields_and_distinct_prompts(self):
        config = {'model_id': 'seed-audio-1.0'}
        for mode, prefix in AUDIO_MODE_INSTRUCTIONS.items():
            with self.subTest(mode=mode):
                request = AudioGenerationRequest(mode=mode, prompt='雨夜工地', speaker='test-speaker')
                payload = audio_payload(request.model_dump(), config, [])
                self.assertEqual(payload, {
                    'model': 'seed-audio-1.0', 'text_prompt': prefix + '雨夜工地',
                    'audio_config': request.audio_config.model_dump(),
                    'watermark': {'aigc_watermark': False},
                    'references': [{'speaker': 'test-speaker'}],
                })
                self.assertLessEqual(len(payload['text_prompt']), 3000)
        for mode, instruction in AUDIO_MODE_INSTRUCTIONS.items():
            for refs in ([], [image_reference(png_bytes())]):
                limit = 3000 - len(instruction) - (len(IMAGE_SOUND_INSTRUCTION) if refs and mode != 'voice' else 0)
                request = AudioGenerationRequest(mode=mode, prompt='x' * limit)
                self.assertEqual(len(audio_payload(request.model_dump(), config, refs)['text_prompt']), 3000)
                with self.assertRaisesRegex(ValueError, '请缩短'):
                    audio_payload(request.model_copy(update={'prompt': 'x' * (limit+1)}).model_dump(), config, refs)

    def test_http_request_uses_seed_audio_headers_and_configured_endpoint(self):
        class FakeResponse(io.BytesIO):
            headers = {'X-Tt-Logid': 'trace-from-provider'}

        class FakeOpener:
            def open(self, request, timeout):
                self.request, self.timeout = request, timeout
                return FakeResponse(b'{"code":0,"audio":"QQ=="}')

        opener = FakeOpener()
        config = {'base_url': 'https://openspeech.bytedance.com', 'api_path': '/api/v3/tts/create',
                  'api_key': 'test-key', 'timeout_seconds': 345}
        with patch('audio_service.build_opener', return_value=opener):
            body, log_id = request_audio(config, {'model': 'seed-audio-1.0', 'text_prompt': '音效：雨声'}, 'local-trace')
        self.assertEqual(opener.request.full_url, 'https://openspeech.bytedance.com/api/v3/tts/create')
        self.assertEqual(opener.request.get_method(), 'POST')
        self.assertEqual(opener.request.get_header('X-api-key'), 'test-key')
        self.assertEqual(opener.request.get_header('X-api-request-id'), 'local-trace')
        self.assertEqual(json.loads(opener.request.data)['text_prompt'], '音效：雨声')
        self.assertEqual(opener.timeout, 345)
        self.assertEqual((body['code'], log_id), (0, 'trace-from-provider'))

    def test_reference_limits_and_result_decoding(self):
        audio = wav_bytes()
        self.assertEqual(base64.b64decode(audio_reference(audio, '.wav')['audio_data']), audio)
        self.assertEqual(base64.b64decode(image_reference(png_bytes())['image_data']), png_bytes())
        with self.assertRaisesRegex(ValueError, '30 秒'):
            audio_reference(wav_bytes(31), '.wav')
        with self.assertRaisesRegex(ValueError, 'M4A'):
            audio_reference(audio, '.m4a')
        with self.assertRaisesRegex(ValueError, '不能与参考音频'):
            audio_payload({'mode': 'sfx', 'prompt': 'test', 'speaker': '', 'audio_config': {}},
                          {'model_id': 'seed-audio-1.0'}, [audio_reference(audio, '.wav'), image_reference(png_bytes())])
        self.assertEqual(decode_audio_result({'audio': base64.b64encode(audio).decode()}, 'wav')[:2], (audio, '.wav'))
        with self.assertRaisesRegex(ValueError, 'Base64'):
            decode_audio_result({'audio': 'not-base64!'}, 'wav')


class AudioTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jianhuitong-audio-test-')
        spec = importlib.util.spec_from_file_location('audio_test_app', Path(__file__).resolve().parents[1] / 'app.py')
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'JIANHUITONG_DATA_DIR': cls.directory.name}):
            spec.loader.exec_module(cls.module)
        if cls.module.DATA != Path(cls.directory.name):
            raise RuntimeError('Refusing to run audio tests against workspace data')

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        with self.module.connect() as db:
            db.execute('DELETE FROM records')
        self.module.ACTIVE_AUDIO_TASKS.clear()
        self.module.AUDIO_TASK_FALLBACKS.clear()

    def configure_model(self):
        model = self.module.update_registry_model('audio', self.module.DEFAULT_AUDIO_MODEL['id'],
                                                  self.module.RegistryModelUpdate(api_key='test-key'))
        self.assertTrue(model['api_key_set'])
        self.assertNotIn('api_key', model)
        return model

    def test_task_calls_saved_model_once_and_stores_playable_asset(self):
        model = self.configure_model()
        request = AudioGenerationRequest(prompt='机械运转声', name='机械音效', mode='sfx',
                                         model_config_id=model['id'], request_id='same-request')
        with patch.object(self.module.threading, 'Thread') as worker:
            queued = self.module.create_audio_generation(request)
            repeated = self.module.create_audio_generation(request)
            self.assertEqual(repeated['id'], queued['id'])
            worker.assert_called_once()
            with patch.object(self.module, 'request_audio', return_value=(
                    {'code': 0, 'audio': base64.b64encode(wav_bytes()).decode(), 'original_duration': 1}, 'upstream-log')) as upstream:
                job = worker.call_args.kwargs
                job['target'](*job['args'])
        payload = upstream.call_args.args[1]
        self.assertEqual(payload['model'], 'seed-audio-1.0')
        self.assertEqual(payload['text_prompt'], AUDIO_MODE_INSTRUCTIONS['sfx'] + '机械运转声')
        self.assertEqual(upstream.call_args.args[0]['api_key'], 'test-key')
        result = self.module.audio_generation_status(queued['id'])
        self.assertEqual(result['status'], 'succeeded')
        self.assertEqual(result['log_id'], 'upstream-log')
        self.assertEqual(result['asset']['kind'], 'audio')
        self.assertEqual(result['asset']['source'], 'generated')
        self.assertEqual(result['asset']['audio_mode'], 'sfx')
        self.assertAlmostEqual(result['asset']['duration'], 1)
        self.assertTrue((self.module.DATA / result['asset']['url'].lstrip('/')).is_file())
        self.assertNotIn('api_key', json.dumps(result))
        with self.assertRaises(HTTPException) as conflict:
            self.module.create_audio_generation(request.model_copy(update={'prompt': 'different'}))
        self.assertEqual(conflict.exception.status_code, 409)

    def test_added_audio_model_is_used_for_generation(self):
        model = self.module.create_registry_model(self.module.RegistryModelCreate(
            kind='audio', name='定制音频模型', provider='火山引擎语音', provider_id='volcengine-speech',
            model_id='seed-audio-custom', base_url='https://speech.example',
            api_path='/custom/create', api_key='custom-test-key', timeout_seconds=420))
        self.assertEqual(model['kind'], 'audio')
        self.assertNotIn('api_key', model)
        with patch.object(self.module.threading, 'Thread') as worker:
            queued = self.module.create_audio_generation(AudioGenerationRequest(
                prompt='轻柔钢琴', mode='music', model_config_id=model['id']))
            with patch.object(self.module, 'request_audio', return_value=(
                    {'code': 0, 'audio': base64.b64encode(wav_bytes()).decode()}, '')) as upstream:
                job = worker.call_args.kwargs
                job['target'](*job['args'])
        config, payload = upstream.call_args.args
        self.assertEqual((config['base_url'], config['api_path'], config['api_key'], config['timeout_seconds']),
                         ('https://speech.example', '/custom/create', 'custom-test-key', 420))
        self.assertEqual((payload['model'], payload['text_prompt']), ('seed-audio-custom', AUDIO_MODE_INSTRUCTIONS['music'] + '轻柔钢琴'))
        self.assertEqual(self.module.audio_generation_status(queued['id'])['status'], 'succeeded')

    def test_image_music_intent_and_reference_reach_actual_http_body(self):
        model = self.configure_model()
        image = self.module.store_uploaded_asset(png_bytes(), 'reference.png')
        prompt = '为这个皮影戏配一段背景音'

        class Response(io.BytesIO):
            headers = {}

        class Opener:
            calls = []
            def open(self, request, timeout):
                self.calls.append(request)
                return Response(json.dumps({'code': 0, 'audio': base64.b64encode(wav_bytes()).decode()}).encode())

        opener = Opener()
        with patch.object(self.module.threading, 'Thread') as worker:
            task = self.module.create_audio_generation(AudioGenerationRequest(
                prompt=prompt, mode='music', model_config_id=model['id'], reference_asset_ids=[image['id']]))
            with patch('audio_service.build_opener', return_value=opener):
                job = worker.call_args.kwargs
                job['target'](*job['args'])
        self.assertEqual(len(opener.calls), 1)
        payload = json.loads(opener.calls[0].data)
        self.assertEqual(set(payload), {'model', 'text_prompt', 'audio_config', 'references', 'watermark'})
        self.assertIn('纯器乐背景音乐', payload['text_prompt'])
        self.assertIn('不要任何人声', payload['text_prompt'])
        self.assertIn('不要口头描述图片', payload['text_prompt'])
        self.assertTrue(payload['text_prompt'].endswith(prompt))
        original = (self.module.UPLOADS / Path(image['url']).name).read_bytes()
        self.assertEqual(base64.b64decode(payload['references'][0]['image_data']), original)
        saved = self.module.get('audio_task', task['id'])
        self.assertEqual((saved['mode'], saved['prompt'], saved['status']), ('music', prompt, 'succeeded'))

    def test_overlong_mode_prompt_is_rejected_before_worker(self):
        self.configure_model()
        with patch.object(self.module.threading, 'Thread') as worker, patch.object(self.module, 'request_audio') as upstream:
            with self.assertRaises(HTTPException) as caught:
                self.module.create_audio_generation(AudioGenerationRequest(mode='music', prompt='x' * 3000))
            self.assertEqual(caught.exception.status_code, 422)
            worker.assert_not_called()
            upstream.assert_not_called()
        self.assertEqual(self.module.listing('audio_task'), [])

    def test_invalid_reference_combinations_rejected_before_worker(self):
        self.configure_model()
        audio = self.module.store_uploaded_asset(wav_bytes(), 'reference.wav')
        image = self.module.store_uploaded_asset(png_bytes(), 'reference.png')
        with patch.object(self.module.threading, 'Thread') as worker:
            for references, speaker in [([audio['id'], image['id']], ''), ([image['id']], 'speaker-id')]:
                with self.subTest(references=references, speaker=speaker), self.assertRaises(HTTPException) as error:
                    self.module.create_audio_generation(AudioGenerationRequest(
                        prompt='测试', reference_asset_ids=references, speaker=speaker))
                self.assertEqual(error.exception.status_code, 422)
            worker.assert_not_called()
        self.assertEqual(self.module.listing('audio_task'), [])

    def test_interrupted_job_fails_without_recharging_provider(self):
        self.module.save('audio_task', {'id': 'interrupted', 'status': 'running'})
        with patch.object(self.module, 'request_audio') as upstream:
            result = self.module.audio_generation_status('interrupted')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('服务已重启', result['error'])
        upstream.assert_not_called()


if __name__ == '__main__':
    unittest.main()
