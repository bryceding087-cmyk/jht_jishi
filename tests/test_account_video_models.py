"""Account-specific official video access; isolated storage and fake model IO."""
import base64
from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from fastapi import HTTPException
from PIL import Image
from account_video_models import FILE_NAME, validate_document
from data_access import access_scope
from market_service import account_video_credentials, model_api_key, open_model_request, video_query_credentials
from provider_adapters import video_payload, video_query_url
from test_auth import Browser


def fixture_models():
    return [
        {'id': 'account-video:fixture-minimax', 'name': 'MiniMax H3（官网·个人）', 'provider_id': 'minimax',
         'model_id': 'MiniMax-H3', 'base_url': 'https://api.minimax.cn', 'api_path': '/v2/video_generation',
         'query_path': '/v2/query/video_generation/{task_id}', 'api_key': 'fixture-private-minimax-key',
         'capabilities': {'resolutions': ['768p', '2k'], 'durations': list(range(4, 16)), 'aspect_ratios': ['16:9', '9:16']}},
        {'id': 'account-video:fixture-ark', 'name': 'Seedance 2.5（火山引擎·个人）', 'provider_id': 'ark',
         'model_id': 'doubao-seedance-2-5-260628', 'base_url': 'https://ark.cn-beijing.volces.com',
         'api_path': '/api/v3/contents/generations/tasks', 'query_path': '/api/v3/contents/generations/tasks/{task_id}',
         'api_key': 'fixture-private-ark-key', 'capabilities': {}}
    ]


class AccountVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jht-account-video-')
        url = 'sqlite:///' + (Path(cls.directory.name) / 'fixture.db').as_posix()
        spec = importlib.util.spec_from_file_location('account_video_app', Path(__file__).resolve().parents[1] / 'app.py')
        cls.h = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'JIANHUITONG_DATA_DIR': cls.directory.name, 'JIANHUITONG_DATABASE_URL': url,
                'DATABASE_URL': url, 'JIANHUITONG_MARKET_ORIGIN': 'https://market.example.test',
                'JIANHUITONG_MARKET_MODEL_ORIGIN': 'https://gateway.example.test',
                'JIANHUITONG_MARKET_AGENT_ID': 'fixture-agent', 'JIANHUITONG_MARKET_SDK_LOGIN': '1'}):
            spec.loader.exec_module(cls.h)
        cls.market = cls.h.app.state.market_integration

    @classmethod
    def tearDownClass(cls):
        cls.h.database.engine.dispose()
        cls.directory.cleanup()

    def setUp(self):
        self.market._bindings.clear()
        self.h.ACTIVE_VIDEO_TASKS.clear()
        with self.h.connect() as db:
            for table in ('records', 'auth_sessions', 'auth_login_limits'):
                db.execute('DELETE FROM ' + table)
            db.execute('UPDATE auth_users SET active=1')
        self.browser = Browser(self.h.app)
        self.owner = self.login('owner')
        self.other = self.login('other')
        self.user = self.call('GET', '/api/auth/me', binding=self.owner)['user']
        self.document = {'version': 1, 'accounts': [{'username': self.user['username'], 'models': fixture_models()}]}
        self.h.account_video_models.path.write_text(json.dumps(self.document, ensure_ascii=False), encoding='utf-8')

    def call(self, method, path, body=None, binding=None, expected=200):
        status, _, raw = self.browser.request(method, path, body,
            headers={'x-jht-market-session': binding} if binding else {})
        self.assertEqual(status, expected, raw.decode(errors='replace'))
        self.assertNotIn(b'fixture-private-', raw)
        self.assertNotIn(b'sk-fixture-session-', raw)
        return json.loads(raw) if raw else None

    def login(self, subject):
        data = self.call('POST', '/api/integrations/aimarket/login',
            {'user': {'id': subject, 'name': '刘方雅', 'accountType': 'ordinary'},
             'agent': {'id': 'fixture-agent', 'name': '建绘通'}, 'apiKey': 'sk-fixture-session-' + subject}, expected=201)
        return data['binding_id']

    def create_task(self, index=0, mode='text-to-video', asset_ids=None, binding=None):
        binding = binding or self.owner
        conversation = self.call('POST', '/api/conversations', {'title': '官网视频', 'kind': 'video',
            'prompt': '小猫散步', 'settings': {}, 'request_id': 'conversation-' + str(index)}, binding, 201)
        body = {'project_id': conversation['id'], 'node_id': 'conversation-video', 'prompt': '小猫散步',
            'model_config_id': fixture_models()[index]['id'], 'duration': 8,
            'resolution': '2k' if index == 0 else '720p', 'aspect_ratio': '16:9',
            'mode': mode, 'asset_ids': asset_ids or [], 'request_id': 'generation-' + str(index)}
        return self.call('POST', '/api/tasks', body, binding, 202 if binding == self.owner else 422)

    def test_models_visible_only_to_exact_account_not_same_display_name(self):
        own = self.call('GET', '/api/model-registry', binding=self.owner)
        private = [m for m in own['models'] if m.get('is_account_model')]
        self.assertEqual(len(private), 2)
        self.assertFalse(own['can_manage'])
        self.assertTrue(all(m['credential_source'] == 'account' and m['api_key_set'] and m['can_activate'] for m in private))
        self.assertTrue(all(not m['api_key_hint'] for m in private))
        for path in ('/api/models', '/api/model-registry'):
            other = self.call('GET', path, binding=self.other)
            models = other.get('models', []) if isinstance(other, dict) else other
            self.assertFalse(any(m.get('is_account_model') or m.get('can_activate') for m in models))
        self.call('GET', '/api/model-registry', expected=401)

    def test_personal_switch_is_persisted_and_global_default_stays_unchanged(self):
        for mid in [m['id'] for m in fixture_models()] + [self.h.DEFAULT_MODEL_ID]:
            result = self.call('POST', '/api/model-registry/video/' + mid + '/activate', {}, self.owner)
            self.assertTrue(result['is_default'])
            own = self.call('GET', '/api/models', binding=self.owner)
            self.assertEqual([m['id'] for m in own if m['is_default']], [mid])
            other = self.call('GET', '/api/models', binding=self.other)
            self.assertEqual([m['id'] for m in other if m['is_default']], [self.h.DEFAULT_MODEL_ID])
        self.assertIsNone(self.h.raw_record('model_default', 'model-default:video'))
        with access_scope(self.user):
            self.assertEqual(self.h.raw_record('account_model_default', self.h.account_video_models.preference_id('video'))['owner_id'], str(self.user['id']))

    def test_other_user_cannot_submit_activate_or_edit_private_models(self):
        mid = fixture_models()[0]['id']
        self.create_task(binding=self.other)
        self.call('POST', '/api/model-registry/video/' + mid + '/activate', {}, self.other, 403)
        self.call('PUT', '/api/model-registry/video/' + mid, {'api_key': 'replacement'}, self.owner, 403)
        self.call('POST', '/api/model-registry/text/' + self.h.DEFAULT_LLM_ID + '/activate', {}, self.owner, 403)

    def test_private_key_stays_scoped_to_its_owner_and_exact_endpoint(self):
        task = self.create_task()
        with self.h.worker_scope(task):
            config = self.h.resolve_model_config(task['params']['model_config_id'])
            endpoint = config['base_url'] + config['api_path']
            with self.assertRaises(HTTPException): model_api_key(endpoint, config['api_key'])
            with account_video_credentials(config):
                self.assertEqual(model_api_key(endpoint, 'sk-fixture-session-owner'), config['api_key'])
                for url in ('https://attacker.invalid/video', config['base_url'] + '/v2/other'):
                    with self.assertRaises(HTTPException): model_api_key(url, config['api_key'])
                with self.assertRaises(HTTPException):
                    open_model_request(Request(endpoint), timeout=1, fallback=lambda *a, **k: None)
                with patch('market_service.build_opener') as build:
                    open_model_request(Request(endpoint, data=b'{}'), timeout=1, fallback=lambda *a, **k: None)
                    redirect = build.call_args.args[0]
                    with self.assertRaises(HTTPException): redirect.redirect_request(None, None, 302, '', {}, 'https://attacker.invalid')
            with self.assertRaises(HTTPException): model_api_key(endpoint, config['api_key'])
        with access_scope({'id': 999, 'username': 'other', 'role': 'user'}):
            with self.assertRaises(HTTPException):
                with account_video_credentials(config): pass
        # Password sessions must retain the same redirect protection on polls.
        query_url = video_query_url(config, 'existing-task')
        with access_scope(self.user), account_video_credentials(config), video_query_credentials(query_url, config['api_key']):
            with patch('market_service.build_opener') as build:
                open_model_request(Request(query_url), timeout=1, fallback=lambda *a, **k: self.fail('unrestricted fallback'))
                with self.assertRaises(HTTPException):
                    build.call_args.args[0].redirect_request(None, None, 302, '', {}, 'https://attacker.invalid')

    def test_official_requests_use_private_keys_and_native_results_complete(self):
        for index in (0, 1):
            with self.subTest(provider=index):
                task = self.create_task(index)
                with self.h.worker_scope(task): config = self.h.resolve_model_config(task['params']['model_config_id'])
                created = {'task_id': 'provider-123'} if index == 0 else {'id': 'provider-123'}
                completed = ({'task': {'id': 'provider-123', 'status': 'succeeded', 'content': {'url': 'https://cdn.example/video.mp4'}}}
                             if index == 0 else {'status': 'succeeded', 'content': {'video_url': 'https://cdn.example/video.mp4'}})
                responses, requests = iter((created, completed)), []
                def send(request, timeout):
                    requests.append(request)
                    return io.BytesIO(json.dumps(next(responses)).encode())
                with patch('market_service.build_opener') as build, patch.object(self.h.time, 'sleep'), \
                        patch.object(self.h, 'cache_generated_video', return_value='/uploads/native-video.mp4'):
                    build.return_value.open.side_effect = send
                    self.h.run_provider_task(task['id'], task['params'], config)
                self.assertEqual([r.get_method() for r in requests], ['POST', 'GET'])
                self.assertEqual(requests[0].full_url, config['base_url'] + config['api_path'])
                self.assertEqual(requests[1].full_url, video_query_url(config, 'provider-123'))
                self.assertTrue(all(r.get_header('Authorization') == 'Bearer ' + config['api_key'] for r in requests))
                submitted = json.loads(requests[0].data)
                self.assertEqual(submitted['model'], config['model_id'])
                self.assertEqual(submitted['resolution'], '2K' if index == 0 else '720p')
                if index == 0: self.assertNotIn('watermark', submitted)
                saved = self.h.get('task', task['id'])
                self.assertEqual(saved['status'], 'done')
                self.assertNotIn(config['api_key'], json.dumps(saved))
                self.assertNotIn(config['api_key'], json.dumps(self.h.generation_queue.job('task', task['id'])))

    def test_resume_only_queries_the_existing_official_task(self):
        task = self.create_task()
        self.h.update_task(task['id'], provider_task_id='old-id', status='running')
        with self.h.worker_scope(task): config = self.h.resolve_model_config(task['params']['model_config_id'])
        with patch('market_service.build_opener') as build, patch.object(self.h.time, 'sleep'):
            build.return_value.open.return_value = io.BytesIO(b'{"task":{"status":"failed","error":"fixture stopped"}}')
            self.h.run_provider_task(task['id'], task['params'], config, True)
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertTrue(request.full_url.endswith('/old-id'))
        self.assertEqual(build.return_value.open.call_count, 1)

    def test_two_original_images_are_sent_once_with_correct_frame_roles(self):
        ids, originals = [], []
        with access_scope(self.user):
            for color in ('red', 'blue'):
                stream = io.BytesIO(); Image.new('RGB', (256, 256), color).save(stream, 'PNG')
                asset = self.h.store_uploaded_asset(stream.getvalue(), color + '.png', '')
                ids.append(asset['id'])
                originals.append((self.h.UPLOADS / Path(asset['url']).name).read_bytes())
        for index in (0, 1):
            task = self.create_task(index, 'first-last', ids)
            with self.h.worker_scope(task):
                config = self.h.resolve_model_config(task['params']['model_config_id'])
                payload = self.h.provider_payload(task['params'], config)
            self.assertEqual(payload['ratio'], 'adaptive')
            self.assertEqual([v['role'] for v in payload['content'][1:]], ['first_frame', 'last_frame'])
            self.assertEqual([base64.b64decode(v['image_url']['url'].split(',', 1)[1]) for v in payload['content'][1:]], originals)
            refs = video_payload({**task['params'], 'mode': 'image-to-video'}, config, lambda aid: 'data:image/png;base64,fixture')
            self.assertEqual(refs['ratio'], '16:9')
            self.assertEqual([v['role'] for v in refs['content'][1:]], ['reference_image', 'reference_image'])

    def test_disabled_owner_cannot_use_provisioned_credentials(self):
        task = self.create_task()
        with self.h.worker_scope(task): config = self.h.resolve_model_config(task['params']['model_config_id'])
        with self.h.connect() as db: db.execute('UPDATE auth_users SET active=0 WHERE id=?', (self.user['id'],))
        with patch('market_service.build_opener') as build, self.assertRaises(HTTPException):
            self.h.run_provider_task(task['id'], task['params'], config)
        build.assert_not_called()

    def test_installer_configuration_rejects_redirect_targets_and_duplicate_ids(self):
        for field, value in (('base_url', 'https://attacker.invalid'), ('api_path', '/unrelated'), ('query_path', '/other/{task_id}')):
            data = deepcopy(self.document); data['accounts'][0]['models'][0][field] = value
            with self.assertRaises(ValueError): validate_document(data)
        data = deepcopy(self.document); data['accounts'][0]['models'].append(data['accounts'][0]['models'][0])
        with self.assertRaises(ValueError): validate_document(data)


if __name__ == '__main__': unittest.main()
