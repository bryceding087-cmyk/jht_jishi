"""Provider contract tests. No paid APIs or workspace data are used."""

import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from PIL import Image

from provider_adapters import (
    provider_catalog, provider_id, video_payload, video_query_url,
    video_result, video_result_url, video_task_id,
)


def aimarket_config(**overrides):
    return {'provider_id': 'aimarket', 'provider': 'AI Market', 'name': 'MiniMax 测试',
            **provider_catalog()['video'][1]['defaults'], 'api_key': 'test-api-key', **overrides}


def params(**overrides):
    return {'prompt': '镜头缓慢向前移动', 'negative_prompt': '', 'resolution': '768p',
            'duration': 4, 'aspect_ratio': '16:9', 'asset_ids': [], 'mode': 'text-to-video', **overrides}


class ProviderContractTests(unittest.TestCase):
    def test_wan_dashscope_payload_and_query(self):
        from market_video_models import WAN_MODEL
        wire = video_payload(params(resolution='480p', duration=2), WAN_MODEL, lambda _: None)
        self.assertEqual(wire, {'model': 'wan3.0-video', 'input': {'prompt': '镜头缓慢向前移动'},
                               'parameters': {'resolution': '480P', 'duration': 2, 'ratio': '16:9'}})
        self.assertEqual(video_query_url(WAN_MODEL, 'task/42'),
                         'http://192.168.241.202:4091/v1/tasks/task%2F42')
        for mode in ('image-to-video', 'first-last', 'multi-frame'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, '文生视频'):
                video_payload(params(mode=mode, asset_ids=['image']), WAN_MODEL, lambda _: None)

    def test_wan_response_states_and_string_error_codes(self):
        for status, expected in [('PENDING', 'queued'), ('RUNNING', 'running'), ('SUCCEEDED', 'done'), ('FAILED', 'failed')]:
            self.assertEqual(video_result({'output': {'task_id': 'wan42', 'task_status': status,
                              'video_url': 'https://cdn.example/wan.mp4'}})['status'], expected)
        result = video_result({'code': 'InvalidParameter', 'message': '参数无效'})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], '参数无效')

    def test_aimarket_submission_matches_documented_request(self):
        self.assertEqual(video_payload(params(prompt='蓝天白云，镜头缓慢移动'), aimarket_config(), lambda _: None), {
            'model': 'minimax-h3', 'content': [{'type': 'text', 'text': '蓝天白云，镜头缓慢移动'}],
            'resolution': '768P', 'duration': 4, 'ratio': '16:9',
        })

    def test_aimarket_never_forwards_legacy_watermark_or_ark_audio_flag(self):
        for watermark in (True, False, 'false', 'true', None):
            with self.subTest(watermark=watermark):
                wire = video_payload(params(watermark=watermark, generate_audio=True), aimarket_config(), lambda _: None)
                self.assertNotIn('watermark', wire)
                self.assertNotIn('generate_audio', wire)

    def test_legacy_aimarket_configs_omit_watermark_on_the_wire(self):
        for provider, base_url in [('AI Market', 'https://gateway.example'),
                                   ('旧网关', 'http://192.168.241.202:4091')]:
            with self.subTest(provider=provider):
                config = {'provider': provider, 'base_url': base_url, 'model_id': 'minimax-h3'}
                payload = video_payload(params(), config, lambda _: None)
                wire = json.loads(json.dumps(payload))
                self.assertNotIn('watermark', wire)
                self.assertNotIn('generate_audio', wire)

    def test_ark_payload_retains_flags_references_and_negative_prompt(self):
        config = {'provider_id': 'ark', 'model_id': 'seedance-test'}
        payload = video_payload(params(resolution='720p', negative_prompt='模糊',
                                       asset_ids=['first', 'last'], mode='first-last'),
                                config, lambda aid: 'data:image/png;base64,' + aid)
        self.assertEqual(payload['resolution'], '720p')
        self.assertIs(payload['generate_audio'], True)
        self.assertIs(payload['watermark'], False)
        self.assertIn('Negative prompt: 模糊', payload['content'][0]['text'])
        self.assertEqual([item['role'] for item in payload['content'][1:]], ['first_frame', 'last_frame'])

    def test_aimarket_query_uses_separate_endpoint_and_encoded_task_id(self):
        self.assertEqual(video_query_url(aimarket_config(), 'task /?#中'),
                         'http://192.168.241.202:4091/v1/query/video_generation/task%20%2F%3F%23%E4%B8%AD')
        self.assertEqual(video_query_url(aimarket_config(query_path='/custom/jobs/{task_id}'), '123'),
                         'http://192.168.241.202:4091/custom/jobs/123')

    def test_legacy_ark_config_preserves_custom_query_endpoint(self):
        config = {'provider': '旧网关', 'base_url': 'https://gateway.example/v3/', 'api_path': '/custom/tasks/'}
        self.assertEqual(provider_id('video', config), 'ark')
        self.assertEqual(video_query_url(config, 'job123'), 'https://gateway.example/v3/custom/tasks/job123')

    def test_task_id_ignores_echoed_model_and_reference_ids(self):
        self.assertEqual(video_task_id({'request_id': 'request', 'id': 'gateway-id',
                                       'input': {'id': 'image'}, 'data': {'task_id': 'real-task'}}), 'real-task')
        self.assertEqual(video_task_id({'input': {'id': 'image'}}), '')

    def test_native_states_inside_gateway_response(self):
        for state, expected in [('QueueD', 'queued'), ('Preparing', 'running'),
                                ('Processing', 'running'), ('IN_PROGRESS', 'running'), ('Success', 'done')]:
            with self.subTest(state=state):
                result = video_result({'status': 'success', 'data': {'task_status': state,
                                      'output': {'video_url': 'https://cdn.example/video.mp4'}}})
                self.assertEqual(result['status'], expected)
                self.assertEqual(bool(result['video_url']), expected == 'done')

    def test_native_error_is_preserved(self):
        result = video_result({'data': {'status': 'Fail', 'base_resp': {'status_code': 1002, 'status_msg': '余额不足'}}})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error'], '余额不足')
        self.assertEqual(video_result({'code': 403, 'message': '模型无权限'})['error'], '模型无权限')

    def test_video_urls_from_supported_output_envelopes(self):
        url = 'https://cdn.example/video.mp4'
        for output in [{'content': {'video_url': url}}, {'data': {'output': {'url': url}}},
                       {'output': [url]}, {'url': 'https://api.example/task', 'output': {'video_url': url}},
                       {'raw_response': {'result': {'videos': [{'url': url}]}}}]:
            with self.subTest(output=output):
                self.assertEqual(video_result_url(output), url)
        self.assertEqual(video_result_url({'input': {'url': 'https://cdn.example/reference.png'}}), '')
        self.assertEqual(video_result_url({'content': [{'type': 'image_url', 'image_url': {'url': url}}]}), '')

    def test_unknown_state_is_an_explicit_error(self):
        with self.assertRaisesRegex(ValueError, '无法识别'):
            video_result({'data': {'status': 'mystery'}})


class ModelRegistryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jianhuitong-provider-test-')
        spec = importlib.util.spec_from_file_location('provider_test_app', Path(__file__).resolve().parents[1] / 'app.py')
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'JIANHUITONG_DATA_DIR': cls.directory.name}):
            spec.loader.exec_module(cls.module)
        if cls.module.DATA != Path(cls.directory.name):
            raise RuntimeError('Refusing to run tests against workspace data')

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        with self.module.connect() as db:
            db.execute('DELETE FROM records')

    def create_model(self, **overrides):
        config = {'kind': 'video', **aimarket_config(), **overrides}
        return self.module.create_registry_model(self.module.RegistryModelCreate(**config))

    def test_shared_wan_seed_is_visible_to_members_and_preserves_admin_edits(self):
        from market_video_models import install_shared_market_video_models, WAN_MODEL_ID
        from data_access import access_scope
        install_shared_market_video_models(self.module.connect)
        for uid in ('member-a', 'member-b'):
            with access_scope({'id': uid, 'role': 'member', 'active': True}):
                models = self.module.model_registry()['models']
                wan = next(m for m in models if m['id'] == WAN_MODEL_ID)
                self.assertFalse(wan['is_default'])
                self.assertEqual(wan['model_id'], 'wan3.0-video')
                self.assertNotIn('api_key', wan)
        model = self.module.internal_registry_model('video', WAN_MODEL_ID)
        self.module.save('model_config', {**model, 'name': '管理员改名'})
        install_shared_market_video_models(self.module.connect)
        self.assertEqual(self.module.internal_registry_model('video', WAN_MODEL_ID)['name'], '管理员改名')
        with self.module.connect() as db:
            db.execute('DELETE FROM records WHERE id=?', (WAN_MODEL_ID,))
        install_shared_market_video_models(self.module.connect)
        self.assertIsNone(self.module.raw_record('model_config', WAN_MODEL_ID))

    def test_wan_actual_request_has_async_header_only_on_submission(self):
        from market_video_models import WAN_MODEL
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{"output":{"task_id":"wan42","task_status":"PENDING"}}'
        with patch.object(self.module, 'open_model_request', return_value=response) as send:
            self.module.provider_json('POST', WAN_MODEL['base_url'] + WAN_MODEL['api_path'], 'fixture-key',
                                      video_payload(params(resolution='480p', duration=2), WAN_MODEL, lambda _: None))
            request = send.call_args.args[0]
            self.assertEqual(request.get_header('X-dashscope-async'), 'enable')
            self.assertEqual(request.get_header('Authorization'), 'Bearer fixture-key')
            self.module.provider_json('GET', video_query_url(WAN_MODEL, 'wan42'), 'fixture-key')
            self.assertIsNone(send.call_args.args[0].get_header('X-dashscope-async'))

    def test_wan_worker_submits_polls_and_saves_local_video(self):
        from market_video_models import WAN_MODEL
        self.module.save('task', {'id': 'wan-task', 'status': 'queued'})
        responses = [{'output': {'task_id': 'wan42', 'task_status': 'PENDING'}},
                     {'output': {'task_id': 'wan42', 'task_status': 'RUNNING'}},
                     {'output': {'task_id': 'wan42', 'task_status': 'SUCCEEDED',
                                 'video_url': 'https://cdn.example/wan.mp4'}}]
        with patch.object(self.module, 'provider_json', side_effect=responses) as send, \
                patch.object(self.module.time, 'sleep'), \
                patch.object(self.module, 'cache_generated_video', return_value='/uploads/wan.mp4'):
            self.module.run_provider_task('wan-task', params(resolution='480p', duration=2), WAN_MODEL)
        self.assertEqual(send.call_args_list[0].args[1], WAN_MODEL['base_url'] + WAN_MODEL['api_path'])
        self.assertEqual(send.call_args_list[1].args[1], WAN_MODEL['base_url'] + '/v1/tasks/wan42')
        self.assertEqual(self.module.get('task', 'wan-task')['video_url'], '/uploads/wan.mp4')

    def create_video_project(self):
        return self.module.create_project(self.module.Project(
            name='服务商请求测试', nodes=[{'id': 'video-node', 'type': 'video', 'settings': {}}]))

    def test_registry_round_trip_preserves_query_and_hides_key(self):
        model = self.create_model(query_path='/custom/{task_id}')
        self.assertEqual(model['provider_id'], 'aimarket')
        self.assertEqual(model['query_path'], '/custom/{task_id}')
        self.assertNotIn('api_key', model)
        self.module.update_registry_model('video', model['id'], self.module.RegistryModelUpdate(name='新名称', api_key=''))
        saved = self.module.internal_registry_model('video', model['id'])
        self.assertEqual(saved['query_path'], '/custom/{task_id}')
        self.assertEqual(saved['api_key'], 'test-api-key')
        registry = self.module.model_registry()
        self.assertEqual({p['id'] for p in registry['providers']['video']}, {'ark', 'aimarket', 'minimax'})
        self.assertNotIn('test-api-key', json.dumps(registry))

    def test_query_default_and_768p_are_available(self):
        model = self.create_model(query_path='')
        self.assertEqual(model['query_path'], '/v1/query/video_generation/{task_id}')
        self.assertIn('768p', [item['value'] for item in self.module.options()['resolutions']])

    def test_saved_aimarket_h3_configuration_exposes_its_parameter_limits(self):
        model = self.create_model()
        saved = self.module.raw_record('model_config', model['id'])
        self.assertNotIn('capabilities', saved)
        public = next(item for item in self.module.public_models() if item['id'] == model['id'])
        self.assertEqual(public['capabilities']['resolutions'], ['768p', '2k'])
        self.assertEqual(public['capabilities']['durations'], list(range(4, 16)))
        self.assertEqual(self.module.internal_registry_model('video', model['id'])['api_key'], 'test-api-key')

    def test_h3_limits_do_not_apply_to_other_models_or_providers(self):
        for overrides in ({'model_id': 'another-video-model'}, {'provider_id': 'ark'}):
            with self.subTest(overrides=overrides):
                model = self.create_model(**overrides)
                config = self.module.internal_registry_model('video', model['id'])
                self.assertEqual(self.module.model_public(config)['capabilities'], {})

    def test_changing_the_saved_model_drops_previous_parameter_limits(self):
        model = self.create_model()
        self.module.update_registry_model('video', model['id'], self.module.RegistryModelUpdate(name='已编辑'))
        self.module.update_registry_model('video', model['id'], self.module.RegistryModelUpdate(model_id='another-video-model'))
        config = self.module.internal_registry_model('video', model['id'])
        self.assertEqual(config['capabilities'], {})

    def test_unsupported_h3_parameters_are_rejected_before_starting_a_task(self):
        model = self.create_model()
        project = self.create_video_project()
        for overrides in ({'resolution': '720p'}, {'duration': 3}, {'duration': 16}):
            with self.subTest(overrides=overrides), \
                    patch.object(self.module.threading, 'Thread') as worker, \
                    patch.object(self.module, 'urlopen') as request:
                with self.assertRaises(HTTPException) as caught:
                    self.module.create_task(self.module.Task(
                        project_id=project['id'], node_id='video-node', model_config_id=model['id'],
                        **params(**overrides)))
                self.assertEqual(caught.exception.status_code, 422)
                worker.assert_not_called()
                request.assert_not_called()
        self.assertEqual(self.module.listing('task'), [])

    def test_task_submission_uses_saved_provider_for_the_actual_http_request(self):
        model = self.create_model(provider_id='ark', model_id='previous-ark-model',
                                  base_url='https://ark.example', api_path='/ark/tasks',
                                  query_path='/ark/tasks/{task_id}', api_key='old-ark-key')
        self.module.update_registry_model('video', model['id'], self.module.RegistryModelUpdate(
            provider_id='aimarket', model_id='minimax-h3', api_key='new-aimarket-key'))
        project = self.create_video_project()
        for resolution, duration in [('768p', 15), ('2k', 4), ('2k', 10)]:
            with self.subTest(resolution=resolution, duration=duration):
                responses = [
                    io.BytesIO(json.dumps({'data': {'task_id': 'upstream/42'}}).encode()),
                    io.BytesIO(json.dumps({'data': {'status': 'Success', 'video_url': 'https://cdn.example/final.mp4'}}).encode()),
                ]
                with patch.object(self.module.threading, 'Thread') as worker, \
                        patch.object(self.module, 'urlopen', side_effect=responses) as request, \
                        patch.object(self.module.time, 'sleep'), \
                        patch.object(self.module, 'cache_generated_video', return_value='/uploads/final.mp4'):
                    task = self.module.create_task(self.module.Task(
                        project_id=project['id'], node_id='video-node', model_config_id=model['id'],
                        model='previous-ark-model',
                        **params(resolution=resolution, duration=duration, negative_prompt='模糊')))
                    worker.assert_not_called()
                    self.module.generation_queue.tick()
                    worker.return_value.start.assert_called_once_with()
                    job = worker.call_args.kwargs
                    job['target'](*job['args'])
                self.assertEqual(request.call_count, 2)
                submitted = request.call_args_list[0].args[0]
                self.assertEqual(submitted.full_url, 'http://192.168.241.202:4091/v1/video_generation')
                self.assertEqual(submitted.get_method(), 'POST')
                self.assertEqual(submitted.get_header('Authorization'), 'Bearer new-aimarket-key')
                self.assertEqual(submitted.get_header('Content-type'), 'application/json')
                self.assertEqual(json.loads(submitted.data), {
                    'model': 'minimax-h3',
                    'content': [{'type': 'text', 'text': '镜头缓慢向前移动\n\nNegative prompt: 模糊'}],
                    'resolution': resolution.upper(), 'duration': duration, 'ratio': '16:9',
                })
                queried = request.call_args_list[1].args[0]
                self.assertEqual(queried.full_url, 'http://192.168.241.202:4091/v1/query/video_generation/upstream%2F42')
                self.assertEqual(queried.get_method(), 'GET')
                self.assertEqual(queried.get_header('Authorization'), 'Bearer new-aimarket-key')
                self.assertEqual(self.module.get('task', task['id'])['status'], 'done')

    def test_legacy_record_uses_existing_submit_and_query_addresses(self):
        self.module.save('model_config', {'id': 'old', 'name': '已有模型', 'provider': '自定义网关',
                                         'model_id': 'old-model', 'base_url': 'https://gateway.example',
                                         'api_path': '/custom/tasks', 'api_key': 'old-key'})
        old = self.module.internal_registry_model('video', 'old')
        self.assertEqual(old['provider_id'], 'ark')
        self.assertEqual(old['query_path'], '/custom/tasks/{task_id}')
        self.assertEqual(old['api_key'], 'old-key')

    def test_switch_provider_drops_old_credentials_and_capabilities(self):
        mid = self.module.DEFAULT_MODEL_ID
        self.module.save('model_config', {
            'id': mid, 'provider_id': 'ark', 'name': 'Legacy Ark',
            **provider_catalog()['video'][0]['defaults'], 'api_key': 'old-ark-key'})
        self.module.update_registry_model('video', mid, self.module.RegistryModelUpdate(api_key='old-ark-key'))
        with patch.dict(os.environ, {'SEEDANCE_API_KEY': 'old-env-key'}):
            result = self.module.update_registry_model('video', mid,
                      self.module.RegistryModelUpdate(provider_id='aimarket', model_id='minimax-h3'))
            config = self.module.internal_registry_model('video', mid)
        self.assertEqual(result['base_url'], 'http://192.168.241.202:4091')
        self.assertEqual(result['query_path'], '/v1/query/video_generation/{task_id}')
        self.assertFalse(config['api_key'])
        self.assertEqual(config['capabilities']['resolutions'], ['768p', '2k'])
        self.assertEqual(config['capabilities']['durations'], list(range(4, 16)))

    def test_invalid_provider_kind_and_query_templates_rejected(self):
        for query in ['/tasks', '//evil.example/{task_id}', '/tasks/{id}', '/tasks/{task_id}/{task_id}',
                      '/tasks/../{task_id}', '/tasks/{task_id}?key=secret']:
            with self.subTest(query=query), self.assertRaises(HTTPException) as caught:
                self.create_model(query_path=query)
            self.assertEqual(caught.exception.status_code, 422)
        with self.assertRaises(HTTPException):
            self.module.validate_model_updates('text', {'provider_id': 'volcengine-speech'})

    def test_worker_submits_polls_and_caches_aimarket_result(self):
        self.module.save('task', {'id': 'local-task', 'status': 'queued'})
        responses = [
            {'code': 0, 'data': {'task_id': 'upstream/42'}},
            {'code': 0, 'data': {'status': 'Preparing'}},
            {'code': 0, 'data': {'status': 'Processing'}},
            {'code': 0, 'data': {'status': 'Success', 'video_url': 'https://cdn.example/final.mp4'}},
        ]
        with patch.object(self.module, 'provider_json', side_effect=responses) as request, \
                patch.object(self.module.time, 'sleep'), \
                patch.object(self.module, 'cache_generated_video', return_value='/uploads/final.mp4') as cache:
            self.module.run_provider_task('local-task', params(), aimarket_config())
        first = request.call_args_list[0].args
        self.assertEqual(first[:3], ('POST', 'http://192.168.241.202:4091/v1/video_generation', 'test-api-key'))
        self.assertEqual(first[3]['resolution'], '768P')
        self.assertNotIn('generate_audio', first[3])
        for query in request.call_args_list[1:]:
            self.assertEqual(query.args, ('GET', 'http://192.168.241.202:4091/v1/query/video_generation/upstream%2F42', 'test-api-key'))
        self.assertEqual(self.module.get('task', 'local-task')['video_url'], '/uploads/final.mp4')
        self.assertEqual(self.module.get('task', 'local-task')['status'], 'done')
        cache.assert_called_once_with('https://cdn.example/final.mp4')

    def test_worker_preserves_ark_success_flow(self):
        self.module.save('task', {'id': 'ark-task', 'status': 'queued'})
        config = {'provider_id': 'ark', **provider_catalog()['video'][0]['defaults'], 'api_key': 'ark-test-key'}
        with patch.object(self.module, 'provider_json', side_effect=[{'id': 'ark123'},
                          {'status': 'succeeded', 'content': {'video_url': 'https://cdn.example/ark.mp4'}}]) as request, \
                patch.object(self.module.time, 'sleep'), \
                patch.object(self.module, 'cache_generated_video', return_value='/uploads/ark.mp4'):
            self.module.run_provider_task('ark-task', params(resolution='720p'), config)
        self.assertIs(request.call_args_list[0].args[3]['generate_audio'], True)
        self.assertEqual(request.call_args_list[1].args[1],
                         'https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/ark123')
        self.assertEqual(self.module.get('task', 'ark-task')['status'], 'done')

    def test_failed_submission_is_not_polled(self):
        self.module.save('task', {'id': 'failed-task', 'status': 'queued'})
        with patch.object(self.module, 'provider_json', return_value={'code': 403, 'message': '没有权限'}) as request:
            self.module.run_provider_task('failed-task', params(), aimarket_config())
        request.assert_called_once()
        self.assertEqual(self.module.get('task', 'failed-task')['status'], 'failed')
        self.assertIn('没有权限', self.module.get('task', 'failed-task')['error'])

    def local_image(self):
        buffer = io.BytesIO()
        Image.new('RGB', (256, 256), 'blue').save(buffer, format='PNG')
        return self.module.store_uploaded_asset(buffer.getvalue(), 'reference.png', '')

    def test_local_h3_reference_is_encoded_and_submitted(self):
        image = self.local_image()
        self.module.save('task', {'id': 'image-task', 'status': 'queued'})
        with patch.object(self.module, 'provider_json', side_effect=[
            {'task_id': 'upstream-task'}, {'status': 'failed', 'message': 'fixture stop'}
        ]) as request, patch.object(self.module.time, 'sleep'):
            self.module.run_provider_task('image-task', params(asset_ids=[image['id']], mode='image-to-video',
                                                             resolution='2k', duration=10), aimarket_config())
        payload = request.call_args_list[0].args[3]
        reference = payload['content'][1]
        self.assertEqual(reference['role'], 'reference_image')
        uri = reference['image_url']['url']
        mime = self.module.MIMES[Path(image['url']).suffix.lower()]
        self.assertTrue(uri.startswith(f'data:{mime};base64,'))
        original = (self.module.UPLOADS / Path(image['url']).name).read_bytes()
        self.assertEqual(base64.b64decode(uri.split(',', 1)[1]), original)
        self.assertEqual(payload['resolution'], '2K')
        self.assertEqual(payload['duration'], 10)
        self.assertNotIn('watermark', payload)
        self.assertNotIn('generate_audio', payload)
        self.assertEqual(request.call_count, 2)

    def test_h3_public_reference_url_is_preserved(self):
        self.module.save('asset', {'id': 'remote', 'kind': 'image', 'url': 'https://cdn.example/image.png'})
        payload = self.module.provider_payload(params(asset_ids=['remote']), aimarket_config())
        self.assertEqual(payload['content'][1]['image_url']['url'], 'https://cdn.example/image.png')

    def test_video_images_use_derivatives_but_image_generation_keeps_original(self):
        buffer=io.BytesIO();Image.new('RGB',(3000,2000),'blue').save(buffer,'PNG')
        # Generated sources preserve the original pixels in the asset library.
        asset=self.module.store_uploaded_asset(buffer.getvalue(),'large.png','',source='generated')
        path=self.module.UPLOADS/Path(asset['url']).name
        before=path.read_bytes()
        for config in (aimarket_config(),{'provider_id':'ark','model_id':'fixture-video'}):
            payload=self.module.provider_payload(params(asset_ids=[asset['id']],mode='image-to-video'),config)
            uri=payload['content'][1]['image_url']['url']
            with Image.open(io.BytesIO(base64.b64decode(uri.split(',',1)[1]))) as image:
                self.assertLessEqual(max(image.size),1920)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(base64.b64decode(self.module.reference_image_data_uri(asset['id']).split(',',1)[1]),before)
        # Frame order and roles must survive compression, including same-image loops.
        wire=self.module.provider_payload(params(asset_ids=[asset['id'],asset['id']],mode='first-last'),aimarket_config())
        self.assertEqual([item['role'] for item in wire['content'][1:]],['first_frame','last_frame'])

    def test_missing_h3_reference_is_rejected_before_submission(self):
        self.module.save('task', {'id': 'image-task', 'status': 'queued'})
        self.module.save('asset', {'id': 'image', 'kind': 'image', 'url': '/uploads/missing.png'})
        with patch.object(self.module, 'provider_json') as request:
            self.module.run_provider_task('image-task', params(asset_ids=['image']), aimarket_config())
        request.assert_not_called()
        self.assertEqual(self.module.get('task', 'image-task')['status'], 'failed')

    def test_other_aimarket_models_keep_url_reference_contract(self):
        image = self.local_image()
        with self.assertRaisesRegex(ValueError, 'HTTP'):
            self.module.provider_payload(params(asset_ids=[image['id']]), aimarket_config(model_id='other-model'))


if __name__ == '__main__':
    unittest.main()
