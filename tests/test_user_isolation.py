"""Account isolation through real ASGI requests; isolated DB, no provider calls."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
from auth_service import hash_password
from creator_metadata import creator_scope, user_creator
from data_access import access_scope, migrate_ownership
from test_auth import Browser


class UserIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jht-isolation-')
        spec = importlib.util.spec_from_file_location('isolation_app', Path(__file__).resolve().parents[1] / 'app.py')
        cls.h = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'JIANHUITONG_DATA_DIR': cls.directory.name}):
            spec.loader.exec_module(cls.h)
        password = hash_password('isolation-fixture')
        with cls.h.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=?', (password,))
            db.execute('INSERT INTO auth_users (username,display_name,role,password_hash,created_at) VALUES (?,?,?,?,?)',
                       ('member-b', '成员乙', 'user', password, time.time()))
            cls.users = {r[1]: dict(zip(('id', 'username', 'display_name', 'role'), r)) for r in
                         db.execute('SELECT id,username,display_name,role FROM auth_users')}
        image = io.BytesIO()
        Image.new('RGB', (64, 64), '#368a9b').save(image, 'PNG')
        cls.image = image.getvalue()

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        with self.h.connect() as db:
            db.execute('DELETE FROM records')
            db.execute('DELETE FROM auth_sessions')
            db.execute('DELETE FROM auth_login_limits')
        self.h.IMAGE_TASK_FALLBACKS.clear()
        self.h.AUDIO_TASK_FALLBACKS.clear()
        self.h.ACTIVE_IMAGE_TASKS.clear()
        self.h.ACTIVE_AUDIO_TASKS.clear()
        self.admin = self.login('admin')
        self.a = self.login('dingyufu')
        self.b = self.login('member-b')
        self.pa = self.request(self.a, 'POST', '/api/projects', {'name': '甲的画布'}, 201)
        self.pb = self.request(self.b, 'POST', '/api/projects', {'name': '乙的画布'}, 201)
        self.aa = self.upload(self.a, 'a.png')
        self.ab = self.upload(self.b, 'b.png')

    def login(self, username):
        browser = Browser(self.h.app)
        self.request(browser, 'POST', '/api/auth/login', {'username': username, 'password': 'isolation-fixture'})
        return browser

    def request(self, browser, method, path, payload=None, expected=200, headers=None):
        status, _, body = browser.request(method, path, payload, headers=headers)
        self.assertEqual(status, expected, (method, path, body.decode('utf-8', errors='replace')))
        return json.loads(body) if body else None

    def upload(self, browser, name, fields=None, expected=201):
        boundary = 'jht-isolation-boundary'
        body = b''
        for key, value in (fields or {}).items():
            body += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode()
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n').encode()
        body += self.image + f'\r\n--{boundary}--\r\n'.encode()
        return self.request(browser, 'POST', '/api/assets', body, expected,
                            {'content-type': f'multipart/form-data; boundary={boundary}'})

    def owned_save(self, username, kind, obj):
        user = self.users[username]
        with access_scope(user), creator_scope(user_creator(user)):
            return self.h.save(kind, obj)

    def conversation(self, browser, suffix):
        return self.request(browser, 'POST', '/api/conversations',
            {'title': suffix, 'kind': 'image', 'prompt': '测试', 'request_id': suffix, 'settings': {}}, 201)

    def test_lists_owner_fields_admin_overview_and_no_client_role_override(self):
        for browser, project, asset in [(self.a, self.pa, self.aa), (self.b, self.pb, self.ab)]:
            self.assertEqual([p['id'] for p in self.request(browser, 'GET', '/api/projects')], [project['id']])
            self.assertEqual([a['id'] for a in self.request(browser, 'GET', '/api/assets')], [asset['id']])
        self.assertEqual(len(self.request(self.admin, 'GET', '/api/projects')), 2)
        self.assertEqual(len(self.request(self.admin, 'GET', '/api/assets')), 2)
        forged = self.request(self.a, 'POST', '/api/projects',
            {'name': '伪造归属', 'owner_id': str(self.users['member-b']['id']), 'creator_id': '1', 'role': 'admin'}, 201)
        self.assertEqual(forged['owner_id'], str(self.users['dingyufu']['id']))
        self.assertEqual(forged['creator_id'], str(self.users['dingyufu']['id']))
        self.request(self.b, 'GET', '/api/projects/' + self.pa['id'], expected=404, headers={'x-role': 'admin', 'x-user-id': '1'})

    def test_uploads_are_history_until_manually_saved_and_library_is_private(self):
        self.assertFalse(self.aa['in_library'])
        self.assertEqual(self.request(self.a, 'GET', '/api/assets?collection=library'), [])
        route = '/api/assets/' + self.aa['id'] + '/library'
        self.request(self.b, 'POST', route, {}, expected=404)
        self.request(self.b, 'POST', '/api/assets/' + self.aa['id'] + '/retry-file', {}, expected=404)
        first = self.request(self.a, 'POST', route, {})
        second = self.request(self.a, 'POST', route, {})
        self.assertTrue(first['in_library'])
        self.assertEqual(first['library_saved_at'], second['library_saved_at'])
        self.assertEqual(first['url'], self.aa['url'])
        self.assertEqual(len(self.request(self.a, 'GET', '/api/assets')), 1)
        self.assertEqual([a['id'] for a in self.request(self.a, 'GET', '/api/assets?collection=library')], [self.aa['id']])
        self.assertEqual(self.request(self.b, 'GET', '/api/assets?collection=library'), [])

    def test_remove_from_library_preserves_history_files_and_references(self):
        route = '/api/assets/' + self.aa['id'] + '/library'
        original_file = self.a.request('GET', self.aa['url'])[2]
        self.request(self.a, 'POST', route, {})
        self.request(self.b, 'DELETE', route, expected=404)
        self.assertTrue(self.request(self.a, 'GET', '/api/assets/' + self.aa['id'])['in_library'])
        node = {'id': 'reference', 'type': 'asset', 'assetId': self.aa['id'], 'assetUrl': self.aa['url']}
        self.request(self.a, 'PUT', '/api/projects/' + self.pa['id'], {'name': '甲的画布', 'nodes': [node]})
        conversation = self.request(self.a, 'POST', '/api/conversations', {
            'title': '保留参考图', 'kind': 'image', 'prompt': '参考图', 'request_id': 'remove-library-reference',
            'settings': {'reference_asset_ids': [self.aa['id']]},
        }, 201)
        removed = self.request(self.a, 'DELETE', route)
        repeated = self.request(self.a, 'DELETE', route)
        self.assertFalse(removed['in_library'])
        self.assertIsNone(removed['library_saved_at'])
        self.assertEqual(repeated['updated_at'], removed['updated_at'])
        self.assertEqual(self.request(self.a, 'GET', '/api/assets?collection=library'), [])
        self.assertEqual([a['id'] for a in self.request(self.a, 'GET', '/api/assets')], [self.aa['id']])
        self.assertEqual(self.a.request('GET', self.aa['url'])[2], original_file)
        project = self.request(self.a, 'GET', '/api/projects/' + self.pa['id'])
        self.assertEqual(project['nodes'][0]['assetId'], self.aa['id'])
        details = self.request(self.a, 'GET', '/api/conversations/' + conversation['id'])
        self.assertEqual(details['settings']['reference_asset_ids'], [self.aa['id']])
        restored = self.request(self.a, 'POST', route, {})
        self.assertTrue(restored['in_library'])
        self.assertEqual(restored['id'], self.aa['id'])
        self.assertEqual(restored['url'], self.aa['url'])
        self.request(self.a, 'DELETE', '/api/assets/missing/library', expected=404)

    def test_generation_snapshots_reflect_manual_library_membership(self):
        for kind, field, getter in [('image', 'assets', self.h.image_task_public), ('audio', 'asset', self.h.audio_task_public)]:
            with self.subTest(kind=kind), access_scope(self.users['dingyufu']):
                snapshot = {'id': 'task-' + kind, 'status': 'succeeded', field: [self.aa] if field == 'assets' else self.aa}
                self.h.save_asset_to_library(self.aa['id'])
                result = getter(snapshot)
                asset = result[field][0] if field == 'assets' else result[field]
                self.assertTrue(asset['in_library'])
                self.h.remove_asset_from_library(self.aa['id'])
                result = getter(snapshot)
                asset = result[field][0] if field == 'assets' else result[field]
                self.assertFalse(asset['in_library'])

    def test_foreign_project_reads_writes_copy_scripts_recent_and_trash_are_denied(self):
        path = '/api/projects/' + self.pa['id']
        for method, route, body in [('GET', path, None), ('GET', path + '/info', None),
            ('PUT', path, {'name': '偷改', 'nodes': []}), ('PATCH', path + '/metadata', {'name': '偷改'}),
            ('POST', path + '/duplicate', None), ('POST', path + '/restore', None), ('DELETE', path, None),
            ('DELETE', '/api/recent-items/project/' + self.pa['id'], None),
            ('PUT', '/api/scripts/' + self.pa['id'] + '/script', {'script_data': {}})]:
            with self.subTest(route=route, method=method):
                self.request(self.b, method, route, body, 404)
        self.request(self.admin, 'PATCH', path + '/metadata', {'name': '管理员编辑'})
        self.assertEqual(self.request(self.a, 'GET', path)['owner_id'], self.pa['owner_id'])
        self.request(self.a, 'DELETE', path, expected=204)
        self.assertEqual(len(self.request(self.a, 'GET', '/api/projects?trashed=true')), 1)
        self.assertEqual(self.request(self.b, 'GET', '/api/projects?trashed=true'), [])
        self.request(self.b, 'POST', path + '/restore', expected=404)
        self.request(self.a, 'POST', path + '/restore')

    def test_foreign_assets_upload_destinations_and_graph_references_are_denied(self):
        path = '/api/assets/' + self.aa['id']
        for method, body in [('GET', None), ('PATCH', {'name': '偷改'}), ('DELETE', None)]:
            self.request(self.b, method, path, body, 404)
        self.upload(self.b, 'foreign.png', {'project_id': self.pa['id']}, expected=404)
        for nodes in [[{'id': 'x', 'type': 'asset', 'assetId': self.aa['id']}],
                      [{'id': 'x', 'type': 'audio', 'audio_settings': {'reference_asset_ids': [self.aa['id']]}}]]:
            self.request(self.b, 'PUT', '/api/projects/' + self.pb['id'], {'name': '乙', 'nodes': nodes}, 404)
        self.request(self.b, 'POST', '/api/conversations',
            {'title': '乙', 'kind': 'image', 'prompt': '测试', 'request_id': 'foreign-ref',
             'settings': {'reference_asset_ids': [self.aa['id']]}}, 404)
        self.request(self.a, 'PATCH', path, {'name': '甲重命名'})

    def test_media_get_head_range_guesses_and_anonymous_access(self):
        url = self.aa['url']
        for browser, expected in [(self.a, 200), (self.admin, 200), (self.b, 404)]:
            for method in ('GET', 'HEAD'):
                self.assertEqual(browser.request(method, url)[0], expected)
        status, headers, body = self.a.request('GET', url, headers={'range': 'bytes=0-15'})
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 16)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(self.b.request('GET', url, headers={'range': 'bytes=0-15'})[0], 404)
        self.assertEqual(Browser(self.h.app).request('GET', url)[0], 303)
        self.assertEqual(self.b.request('GET', '/uploads/unrecorded.png')[0], 404)

    def test_folders_are_private_and_names_are_scoped(self):
        for route in ('/api/project-folders', '/api/asset-folders'):
            fa = self.request(self.a, 'POST', route, {'name': '同名文件夹'}, 201)
            fb = self.request(self.b, 'POST', route, {'name': '同名文件夹'}, 201)
            self.assertEqual([f['id'] for f in self.request(self.b, 'GET', route)], [fb['id']])
            self.assertEqual(len(self.request(self.admin, 'GET', route)), 2)
            self.request(self.b, 'PATCH', route + '/' + fa['id'], {'name': '改名'}, 404)
            self.request(self.b, 'DELETE', route + '/' + fa['id'], expected=404)
            if route.endswith('project-folders'):
                self.request(self.b, 'PATCH', '/api/projects/' + self.pb['id'] + '/metadata', {'folder_id': fa['id']}, 404)
            else:
                self.upload(self.b, 'bad-folder.png', {'folder_id': fa['id']}, expected=404)

    def test_conversations_admin_visibility_and_transactional_deletion(self):
        ca = self.conversation(self.a, '甲对话')
        cb = self.conversation(self.b, '乙对话')
        self.assertEqual([c['id'] for c in self.request(self.a, 'GET', '/api/conversations')], [ca['id']])
        self.assertEqual(len(self.request(self.admin, 'GET', '/api/conversations')), 2)
        self.owned_save('dingyufu', 'image_task', {'id': 'turn-a', 'project_id': ca['id'], 'status': 'succeeded'})
        for method, route, body in [('GET', '/api/conversations/' + ca['id'], None),
            ('PATCH', '/api/conversations/' + ca['id'] + '/metadata', {'title': '偷改'}),
            ('DELETE', '/api/conversations/' + ca['id'] + '/tasks/turn-a', None),
            ('DELETE', '/api/recent-items/conversation/' + ca['id'], None)]:
            self.request(self.b, method, route, body, 404)
        self.request(self.admin, 'GET', '/api/conversations/' + ca['id'])
        self.request(self.admin, 'PATCH', '/api/conversations/' + ca['id'] + '/metadata', {'title': '管理员修改'})
        self.request(self.a, 'DELETE', '/api/conversations/' + ca['id'] + '/tasks/turn-a')
        self.request(self.b, 'GET', '/api/conversations/' + cb['id'])

    def test_task_queries_fallbacks_and_saving_another_users_video(self):
        for kind, route in [('task', '/api/tasks/'), ('image_task', '/api/images/generations/'), ('audio_task', '/api/audio/generations/')]:
            task = self.owned_save('dingyufu', kind, {'id': kind + '-a', 'project_id': self.pa['id'], 'status': 'succeeded', 'created_at': 1})
            self.request(self.a, 'GET', route + task['id'])
            self.request(self.admin, 'GET', route + task['id'])
            self.request(self.b, 'GET', route + task['id'], expected=404)
            if kind != 'task':
                cache = self.h.IMAGE_TASK_FALLBACKS if kind == 'image_task' else self.h.AUDIO_TASK_FALLBACKS
                cache[task['id']] = task
                self.request(self.b, 'GET', route + task['id'], expected=404)
        self.assertEqual(self.request(self.b, 'GET', '/api/tasks'), [])
        self.request(self.b, 'POST', '/api/assets/from-task', {'task_id': 'task-a'}, 404)
        self.request(self.b, 'POST', '/api/tasks/task-a/cache-video', expected=404)
        local_video = self.h.UPLOADS / 'task-a.mp4'
        local_video.write_bytes(b'fixture')
        self.h.save('task', {**self.h.get('task', 'task-a'), 'video_url': '/uploads/task-a.mp4'})
        self.assertEqual(self.b.request('GET', '/uploads/task-a.mp4')[0], 404)
        self.assertEqual(self.a.request('GET', '/uploads/task-a.mp4')[0], 200)

    def test_deleting_a_context_does_not_cancel_foreign_legacy_runs(self):
        # Before isolation, different authors could use the same canvas. Deleting
        # a member's canvas must not mutate another member's surviving run.
        with self.h.connect() as db:
            for username in ('dingyufu', 'member-b'):
                run = {'id': 'legacy-' + username, 'project_id': self.pa['id'],
                       'owner_id': str(self.users[username]['id']), 'status': 'running'}
                db.execute('INSERT INTO records VALUES (?,?,?)', ('agent_run', run['id'], json.dumps(run)))
        self.request(self.a, 'DELETE', '/api/recent-items/project/' + self.pa['id'], expected=204)
        self.assertEqual(self.h.get('agent_run', 'legacy-dingyufu')['status'], 'cancelled')
        self.assertEqual(self.h.get('agent_run', 'legacy-member-b')['status'], 'running')

    def test_generation_request_ids_are_per_user_and_workers_keep_ownership(self):
        self.h.save('image_model_config', {**self.h.DEFAULT_IMAGE_MODEL, 'api_key': 'fixture-key'})
        with patch.object(self.h.threading, 'Thread'):
            ta = self.request(self.a, 'POST', '/api/images/generations', {'prompt': '同样内容', 'request_id': 'same-request'}, 202)
            tb = self.request(self.b, 'POST', '/api/images/generations', {'prompt': '同样内容', 'request_id': 'same-request'}, 202)
            repeat = self.request(self.a, 'POST', '/api/images/generations', {'prompt': '同样内容', 'request_id': 'same-request'}, 202)
        self.assertNotEqual(ta['id'], tb['id'])
        self.assertEqual(ta['id'], repeat['id'])
        with patch.object(self.h, 'provider_json', return_value={'data': [{'url': 'https://fixture.invalid/image.png'}]}), \
                patch.object(self.h, 'download_generated_image', return_value=(self.image, '.png')):
            worker = threading.Thread(target=self.h.run_image_generation, args=(ta['id'], self.h.internal_image_config()))
            worker.start(); worker.join(15)
            self.assertFalse(worker.is_alive())
        result = self.request(self.a, 'GET', '/api/images/generations/' + ta['id'])
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        asset = result['asset']
        self.assertEqual(self.h.get('asset', asset['id'])['owner_id'], self.pa['owner_id'])
        self.assertEqual(self.b.request('GET', asset['url'])[0], 404)

    def test_admin_image_worker_reads_mixed_owner_references_in_member_conversation(self):
        context = self.conversation(self.a, 'admin-reference-edit')
        admin_asset = self.upload(self.admin, 'admin-reference.png')
        self.h.save('image_model_config', {**self.h.DEFAULT_IMAGE_MODEL, 'api_key': 'fixture-key'})
        payload = {'prompt': '参考两张图片生成', 'project_id': context['id'],
                   'reference_asset_ids': [admin_asset['id'], self.aa['id']], 'request_id': 'admin-reference-edit'}
        with patch.object(self.h.threading, 'Thread'):
            task = self.request(self.admin, 'POST', '/api/images/generations', payload, 202)
        saved = self.h.get('image_task', task['id'])
        self.assertEqual(saved['owner_id'], str(self.users['dingyufu']['id']))
        self.assertEqual(saved['creator_id'], str(self.users['admin']['id']))
        with patch.object(self.h, 'provider_json', return_value={'data': [{'url': 'https://fixture.invalid/result.png'}]}) as provider, \
                patch.object(self.h, 'download_generated_image', return_value=(self.image, '.png')):
            worker = threading.Thread(target=self.h.run_image_generation, args=(task['id'], self.h.internal_image_config()))
            worker.start(); worker.join(15)
            self.assertFalse(worker.is_alive())
        result = self.request(self.admin, 'GET', '/api/images/generations/' + task['id'])
        self.assertEqual(result['status'], 'succeeded', result.get('error'))
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(len(provider.call_args.args[3]['image']), 2)
        output = self.h.get('asset', result['asset']['id'])
        self.assertEqual(output['owner_id'], saved['owner_id'])
        self.assertEqual(output['creator_id'], saved['creator_id'])
        self.assertEqual(self.a.request('GET', output['url'])[0], 200)
        self.assertEqual(self.b.request('GET', output['url'])[0], 404)
        self.assertEqual(self.a.request('GET', admin_asset['url'])[0], 404)
        member_details = self.request(self.a, 'GET', '/api/assets/' + output['id'])
        self.assertEqual([ref['id'] for ref in member_details['generation']['references']], [self.aa['id']])
        admin_details = self.request(self.admin, 'GET', '/api/assets/' + output['id'])
        self.assertEqual(len(admin_details['generation']['references']), 2)
        self.request(self.a, 'POST', '/api/images/generations', {**payload, 'request_id': 'member-forbidden-reference'}, 422)

    def test_worker_uses_creator_current_role_and_legacy_owner_fallback(self):
        from fastapi import HTTPException
        from data_access import current_principal
        admin_asset = self.upload(self.admin, 'private-admin.png')
        admin_id = str(self.users['admin']['id'])
        member_id = str(self.users['dingyufu']['id'])
        # Owning data in an admin context cannot grant a member admin rights.
        with self.h.worker_scope({'owner_id': admin_id, 'creator_id': member_id}):
            self.assertEqual(current_principal()['id'], member_id)
            with self.assertRaises(HTTPException):
                self.h.get('asset', admin_asset['id'])
        with self.h.worker_scope({'owner_id': member_id}):
            self.assertEqual(current_principal()['id'], member_id)
            with self.assertRaises(HTTPException):
                self.h.get('asset', self.ab['id'])
        with self.h.connect() as db:
            db.execute("UPDATE auth_users SET role='user' WHERE id=?", (admin_id,))
        try:
            with self.h.worker_scope({'owner_id': member_id, 'creator_id': admin_id, 'role': 'admin'}):
                self.assertEqual(current_principal()['role'], 'user')
                with self.assertRaises(HTTPException):
                    self.h.get('asset', self.ab['id'])
        finally:
            with self.h.connect() as db:
                db.execute("UPDATE auth_users SET role='admin' WHERE id=?", (admin_id,))
        self.assertIsNone(current_principal())

    def test_agent_runs_documents_events_and_admin_access(self):
        service = self.h.app.state.agent_service
        with patch.object(service, 'kick'):
            ra = self.request(self.a, 'POST', '/api/agent/runs', {'text': '甲的故事', 'request_id': 'same'}, 202)['run']
            rb = self.request(self.b, 'POST', '/api/agent/runs', {'text': '乙的故事', 'request_id': 'same'}, 202)['run']
        self.assertNotEqual(ra['id'], rb['id'])
        self.assertEqual(len(self.request(self.admin, 'GET', '/api/agent/runs')), 2)
        self.assertEqual(len(self.request(self.a, 'GET', '/api/agent/runs')), 1)
        doc = self.owned_save('dingyufu', 'agent_artifact', {'id': ra['id'] + ':artifact:test:1', 'content': '甲的私密文档'})
        root = '/api/agent/runs/' + ra['id']
        self.request(self.b, 'GET', root, expected=404)
        self.request(self.b, 'GET', root + '/artifacts/' + doc['id'], expected=404)
        self.request(self.b, 'POST', root + '/messages', {'request_id': 'steal', 'action': 'pause'}, 404)
        self.request(self.b, 'POST', root + '/canvas/layout', expected=404)
        self.request(self.admin, 'GET', root)
        self.request(self.admin, 'GET', root + '/artifacts/' + doc['id'])
        self.request(self.admin, 'POST', root + '/messages', {'request_id': 'admin-pause', 'action': 'pause'}, 202)
        self.assertEqual(self.h.get('agent_run', ra['id'])['owner_id'], self.pa['owner_id'])

    def test_video_request_retries_are_scoped_to_the_context_owner(self):
        payloads = []
        for browser, project in [(self.a, self.pa), (self.b, self.pb)]:
            self.request(browser, 'PUT', '/api/projects/' + project['id'],
                {'name': project['name'], 'nodes': [{'id': 'video', 'type': 'video'}]})
            payload = {'project_id': project['id'], 'node_id': 'video', 'prompt': '天空', 'request_id': 'same-video'}
            payloads.append(payload)
        # No API key: real validation/persistence runs without a provider request.
        first = self.request(self.a, 'POST', '/api/tasks', payloads[0], 202)
        second = self.request(self.b, 'POST', '/api/tasks', payloads[1], 202)
        self.assertNotEqual(first['id'], second['id'])
        repeat = self.request(self.admin, 'POST', '/api/tasks', payloads[0], 202)
        self.assertEqual(repeat['id'], first['id'])
        self.assertEqual(self.h.get('task', repeat['id'])['owner_id'], self.pa['owner_id'])

    def test_model_configuration_is_admin_only_but_members_can_select_models(self):
        self.h.save('image_model_config', {**self.h.DEFAULT_IMAGE_MODEL, 'api_key': 'fixture-secret-9876'})
        registry = self.request(self.a, 'GET', '/api/model-registry')
        self.assertFalse(registry['can_manage'])
        self.assertNotIn('9876', str(registry))
        self.assertTrue(self.request(self.admin, 'GET', '/api/model-registry')['can_manage'])
        body = {'kind': 'text', 'name': 'test', 'model_id': 'model', 'base_url': 'https://example.invalid', 'api_path': '/chat/completions'}
        for method, route, payload in [('POST', '/api/model-registry', body),
            ('PUT', '/api/model-registry/image/' + self.h.DEFAULT_IMAGE_MODEL_ID, {'api_key': 'stolen'}),
            ('POST', '/api/model-registry/image/' + self.h.DEFAULT_IMAGE_MODEL_ID + '/activate', None),
            ('DELETE', '/api/model-registry/image/' + self.h.DEFAULT_IMAGE_MODEL_ID, None),
            ('PUT', '/api/image-model', {'api_key': 'stolen'}), ('PUT', '/api/llm', {'api_key': 'stolen'}),
            ('PUT', '/api/models/' + self.h.DEFAULT_MODEL_ID, {'api_key': 'stolen'}),
            ('DELETE', '/api/models/' + self.h.DEFAULT_MODEL_ID, None)]:
            self.request(self.a, method, route, payload, 403)
        self.request(self.admin, 'PUT', '/api/image-model', {'name': '管理员配置'})
        self.assertEqual(self.h.internal_image_config()['api_key'], 'fixture-secret-9876')

    def test_login_is_required_for_admin_and_unknown_history_is_not_shared(self):
        anonymous = Browser(self.h.app)
        self.assertEqual(anonymous.request('GET', '/')[0], 303)
        self.request(anonymous, 'GET', '/api/projects', expected=401)
        self.request(anonymous, 'POST', '/api/auth/login', {'username': 'admin', 'password': 'wrong'}, 401)
        self.request(anonymous, 'GET', '/api/projects', expected=401)
        unknown = self.h.save('project', {'id': 'unassigned', 'name': '未知历史', 'nodes': []})
        self.request(self.a, 'GET', '/api/projects/' + unknown['id'], expected=404)
        self.request(self.admin, 'GET', '/api/projects/' + unknown['id'])

    def test_migration_infers_only_supported_ownership_and_keeps_authors_unknown(self):
        uid = self.pa['owner_id']
        records = [('conversation', {'id': 'legacy-c', 'owner_id': uid, 'name': '旧对话'}),
                   ('image_task', {'id': 'legacy-t', 'project_id': 'legacy-c'}),
                   ('asset', {'id': 'legacy-a', 'task_id': 'legacy-t', 'kind': 'image'}),
                   ('asset', {'id': 'orphan-a', 'kind': 'image'}),
                   ('agent_run', {'id': 'legacy-run', 'owner_id': uid}),
                   ('agent_artifact', {'id': 'legacy-run:artifact:test:1'})]
        with self.h.connect() as db:
            for kind, value in records:
                db.execute('INSERT INTO records VALUES (?,?,?)', (kind, value['id'], json.dumps(value)))
            db.execute("DELETE FROM app_migrations WHERE name='user-isolation-v1'")
        self.assertGreater(migrate_ownership(self.h.connect, self.h.DATA), 0)
        self.assertEqual(self.h.get('asset', 'legacy-a')['owner_id'], uid)
        self.assertNotIn('creator_id', self.h.get('asset', 'legacy-a'))
        self.assertEqual(self.h.get('agent_artifact', 'legacy-run:artifact:test:1')['owner_id'], uid)
        self.assertEqual(self.h.get('asset', 'orphan-a')['owner_id'], '')
        suffix = 'json' if self.h.database.dialect == 'postgresql' else 'sqlite3'
        self.assertTrue(list((self.h.DATA / 'backups').glob('user-isolation-*.' + suffix)))
        self.request(self.admin, 'GET', '/api/auth/me', expected=401)
        fresh = self.login('dingyufu')
        self.request(fresh, 'GET', '/api/assets/legacy-a')
        self.request(fresh, 'GET', '/api/assets/orphan-a', expected=404)
        self.assertEqual(migrate_ownership(self.h.connect, self.h.DATA), 0)
        self.request(fresh, 'GET', '/api/auth/me')


if __name__ == '__main__':
    unittest.main()
