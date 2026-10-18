"""Creator attribution across authenticated requests, workers and later edits."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import wave
from unittest.mock import patch

from PIL import Image
from auth_service import hash_password
from creator_metadata import creator_scope, current_creator, user_creator
from test_auth import Browser


class CreatorMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jht-creators-')
        spec = importlib.util.spec_from_file_location('creator_test_app', Path(__file__).resolve().parents[1] / 'app.py')
        cls.h = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'JIANHUITONG_DATA_DIR': cls.directory.name}):
            spec.loader.exec_module(cls.h)
        assert cls.h.DATA == Path(cls.directory.name)
        with cls.h.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=?', (hash_password('creator-fixture'),))
            cls.users = {row[1]: dict(zip(('id', 'username', 'display_name'), row)) for row in
                         db.execute('SELECT id, username, display_name FROM auth_users')}
        image = io.BytesIO()
        Image.new('RGB', (64, 64), '#23868a').save(image, 'PNG')
        cls.image = image.getvalue()
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
            stream.writeframes(bytes(32000))
        cls.audio = audio.getvalue()

        @cls.h.app.post('/creator-fixture-upload')
        async def fixture_upload():
            return cls.h.store_uploaded_asset(cls.image, 'reference.png')
        # Register the test endpoint before the catch-all static frontend.
        cls.h.app.router.routes.insert(0, cls.h.app.router.routes.pop())

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        with self.h.connect() as db:
            db.execute('DELETE FROM records')
            db.execute('DELETE FROM auth_login_limits')
        self.h.ACTIVE_IMAGE_TASKS.clear(); self.h.ACTIVE_AUDIO_TASKS.clear()
        self.a = self.login('admin'); self.b = self.login('dingyufu')

    def login(self, username):
        browser = Browser(self.h.app)
        status, _, _ = browser.request('POST', '/api/auth/login', {'username': username, 'password': 'creator-fixture'})
        self.assertEqual(status, 200)
        return browser

    def request(self, browser, method, path, payload=None, expected=200):
        status, _, body = browser.request(method, path, payload)
        self.assertEqual(status, expected, body.decode())
        return json.loads(body) if body else None

    def assert_creator(self, record, username):
        for key, value in user_creator(self.users[username]).items():
            self.assertEqual(record.get(key), value)

    def test_canvas_creation_edit_copy_and_persisted_information(self):
        project = self.request(self.b, 'POST', '/api/projects',
                               {'name': '原始画布', 'creator_id': 'forged', 'creator_name': 'forged'}, expected=201)
        self.assert_creator(project, 'dingyufu')
        self.request(self.a, 'PUT', '/api/projects/' + project['id'],
                     {'name': '协作修改', 'nodes': [{'id': 'text', 'type': 'prompt'}], 'revision': 1,
                      'creator_name': 'forged'})
        info = self.request(self.b, 'GET', '/api/projects/' + project['id'] + '/info')
        self.assert_creator(info, 'dingyufu')
        self.assertEqual(info['created_at'], project['created_at'])
        self.assertGreaterEqual(info['updated_at'], project['created_at'])
        self.assertEqual(info['node_counts']['total'], 1)
        self.assertEqual(info['node_counts']['text'], 1)
        copy = self.request(self.a, 'POST', '/api/projects/' + project['id'] + '/duplicate', expected=201)
        self.assert_creator(copy, 'admin')
        self.assert_creator(self.h.get('project', copy['id']), 'admin')
        self.assertEqual(current_creator(), {})

    def test_node_distribution_uses_current_saved_graph_and_asset_kind(self):
        self.h.save('asset', {'id': 'video-ref', 'kind': 'video'})
        nodes = [{'id': str(i), 'type': kind} for i, kind in enumerate(
            ['prompt', 'script', 'asset', 'asset', 'audio', 'timeline', 'character', 'unknown'])]
        nodes[3]['assetId'] = 'video-ref'
        project = self.request(self.a, 'POST', '/api/projects', {'name': '分布', 'nodes': nodes}, expected=201)
        counts = self.h.project_info(project['id'])['node_counts']
        self.assertEqual(counts, {'total': 8, 'image': 1, 'video': 1, 'audio': 1, 'text': 2,
                                  'timeline': 1, 'subject': 1, 'other': 1})

    def test_upload_and_later_metadata_edit_keep_actual_uploader(self):
        asset = self.request(self.b, 'POST', '/creator-fixture-upload')
        self.assert_creator(asset, 'dingyufu')
        self.request(self.a, 'PATCH', '/api/assets/' + asset['id'], {'name': '管理员重命名'})
        self.assert_creator(self.h.asset_details(asset['id']), 'dingyufu')
        self.assert_creator(self.h.get('asset', asset['id']), 'dingyufu')

    def test_image_and_audio_workers_retain_the_submitter_without_request_context(self):
        image_config = {**self.h.DEFAULT_IMAGE_MODEL, 'api_key': 'fixture'}
        audio_config = {**self.h.DEFAULT_AUDIO_MODEL, 'api_key': 'fixture'}
        with patch.object(self.h.threading, 'Thread'), \
                patch.object(self.h, 'internal_image_config', return_value=image_config), \
                patch.object(self.h, 'internal_registry_model', return_value=audio_config):
            image = self.request(self.a, 'POST', '/api/images/generations', {'prompt': '图片'}, expected=202)
            audio = self.request(self.b, 'POST', '/api/audio/generations', {'prompt': '雨声'}, expected=202)
        self.assert_creator(self.h.get('image_task', image['id']), 'admin')
        self.assert_creator(self.h.get('audio_task', audio['id']), 'dingyufu')
        with patch.object(self.h, 'provider_json', return_value={'data': [{'url': 'https://fixture.invalid/p.png'}]}), \
                patch.object(self.h, 'download_generated_image', return_value=(self.image, '.png')), \
                patch.object(self.h, 'request_audio', return_value=({'code': 0, 'audio': base64.b64encode(self.audio).decode()}, '')):
            workers = [threading.Thread(target=self.h.run_image_generation, args=(image['id'], image_config)),
                       threading.Thread(target=self.h.run_audio_generation, args=(audio['id'], audio_config))]
            for worker in workers: worker.start()
            for worker in workers: worker.join(15); self.assertFalse(worker.is_alive())
        for kind, task, username in [('image_task', image, 'admin'), ('audio_task', audio, 'dingyufu')]:
            result = self.h.get(kind, task['id'])
            self.assertEqual(result['status'], 'succeeded', result.get('error'))
            self.assert_creator(result['asset'], username)

    def test_later_video_save_uses_original_task_author(self):
        with creator_scope(user_creator(self.users['admin'])):
            self.h.save('task', {'id': 'video-task', 'status': 'done'})
        with creator_scope(user_creator(self.users['dingyufu'])), \
                patch.object(self.h, 'validate_bytes', return_value=(b'video', '.mp4', {'duration': 1})):
            asset = self.h.store_uploaded_asset(b'video', 'clip.mp4', source='generated', task_id='video-task')
        self.assert_creator(asset, 'admin')

    def test_agent_worker_restores_author_for_new_tasks_and_final_render(self):
        run = self.h.save('agent_run', {'id': 'agent-fixture', 'owner_id': str(self.users['admin']['id'])})
        service = self.h.app.state.agent_service
        def work(_):
            self.h.save('image_task', {'id': 'agent-image'})
            self.h.save('asset', {'id': 'agent-final', 'kind': 'video', 'source': 'generated', 'agent_render': True})
        with creator_scope(user_creator(self.users['dingyufu'])), patch.object(service, '_work', side_effect=work):
            service.work(run['id'])
            self.assertEqual(current_creator(), user_creator(self.users['dingyufu']))
        self.assert_creator(self.h.get('image_task', 'agent-image'), 'admin')
        self.assert_creator(self.h.get('asset', 'agent-final'), 'admin')

    def test_legacy_records_are_not_attributed_to_the_viewer_or_canvas_owner(self):
        self.h.save('project', {'id': 'old', 'name': '旧画布', 'nodes': []})
        self.h.save('conversation', {'id': 'shared', 'owner_id': str(self.users['admin']['id'])})
        self.h.save('asset', {'id': 'legacy', 'kind': 'image', 'name': '旧图片', 'project_id': 'shared'})
        self.assertEqual(self.h.asset_details('legacy')['creator_name'], '')
        with creator_scope(user_creator(self.users['dingyufu'])):
            self.h.save('project', {**self.h.get('project', 'old'), 'name': '改名'})
        self.assertNotIn('creator_id', self.h.project_info('old'))


if __name__ == '__main__':
    unittest.main()
