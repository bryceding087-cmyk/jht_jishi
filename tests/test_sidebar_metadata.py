"""Persistence and isolation for recent-list metadata actions."""
import unittest
import json
import test_auth


class SidebarMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import importlib.util
        import os
        import sys
        import tempfile
        from pathlib import Path
        cls.directory = tempfile.TemporaryDirectory(prefix='sidebar-metadata-test-')
        previous = os.environ.get('JIANHUITONG_DATA_DIR')
        os.environ['JIANHUITONG_DATA_DIR'] = cls.directory.name
        try:
            spec = importlib.util.spec_from_file_location('sidebar_metadata_test_app', Path(__file__).resolve().parents[1] / 'app.py')
            cls.host = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = cls.host
            spec.loader.exec_module(cls.host)
        finally:
            if previous is None:
                os.environ.pop('JIANHUITONG_DATA_DIR', None)
            else:
                os.environ['JIANHUITONG_DATA_DIR'] = previous

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        from auth_service import hash_password
        store = self.host.app.state.auth_store
        with store.connect() as db:
            db.execute('DELETE FROM records')
            db.execute('DELETE FROM auth_sessions')
            db.execute('DELETE FROM auth_login_limits')
            db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password('sidebar-unit-test'), 'admin'))
        self.browser = test_auth.Browser(self.host.app)
        self.assertEqual(self.browser.request('POST', '/api/auth/login', {'username': 'admin', 'password': 'sidebar-unit-test'})[0], 200)

    def login_as_member(self):
        from auth_service import hash_password
        with self.host.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password('member-unit-test'), 'dingyufu'))
        self.browser = test_auth.Browser(self.host.app)
        self.assertEqual(self.browser.request('POST', '/api/auth/login',
            {'username': 'dingyufu', 'password': 'member-unit-test'})[0], 200)

    def request(self, *args, **kwargs):
        status, headers, body = self.browser.request(*args, **kwargs)
        return status, headers, json.loads(body) if body else None

    def test_project_pin_rename_preserves_graph_and_revision(self):
        _, _, project = self.request('POST', '/api/projects', {
            'name': '原画布', 'nodes': [{'id': 'text', 'type': 'prompt'}], 'edges': [],
        })
        path = '/api/projects/' + project['id']
        status, _, updated = self.request('PATCH', path + '/metadata', {
            'name': '新画布', 'pinned': True, 'revision': project['revision'],
        })
        self.assertEqual(status, 200)
        self.assertTrue(updated['pinned'])
        self.assertEqual(updated['nodes'], project['nodes'])
        self.assertEqual(updated['name'], '新画布')
        self.assertEqual(self.request('PATCH', path + '/metadata', {
            'pinned': False, 'revision': project['revision'],
        })[0], 409)
        status, _, saved = self.request('PUT', path, {
            'name': updated['name'], 'nodes': updated['nodes'], 'revision': updated['revision'],
        })
        self.assertEqual(status, 200)
        self.assertTrue(saved['pinned'])
        self.assertTrue(self.request('GET', path)[2]['pinned'])
        self.assertFalse(self.request('PATCH', path + '/metadata', {'pinned': False})[2]['pinned'])

    def test_conversation_metadata_persists_and_checks_ownership(self):
        _, _, item = self.request('POST', '/api/conversations', {
            'title': '原对话', 'kind': 'image', 'prompt': '测试', 'settings': {'size': '1024'}, 'request_id': 'sidebar-test',
        })
        path = '/api/conversations/' + item['id']
        status, _, updated = self.request('PATCH', path + '/metadata', {'title': ' 新对话 ', 'pinned': True})
        self.assertEqual(status, 200)
        self.assertEqual(updated['title'], '新对话')
        self.assertEqual(updated['name'], '新对话')
        self.assertEqual(updated['settings'], item['settings'])
        self.assertTrue(updated['pinned'])
        self.assertTrue(next(c for c in self.request('GET', '/api/conversations')[2] if c['id'] == item['id'])['pinned'])
        for invalid in [' ', 'a' * 81]:
            self.assertEqual(self.request('PATCH', path + '/metadata', {'title': invalid})[0], 422)
        self.assertFalse(self.request('PATCH', path + '/metadata', {'pinned': False})[2]['pinned'])
        host = self.host
        self.login_as_member()
        self.assertEqual(self.request('PATCH', path + '/metadata', {'title': '禁止修改'})[0], 404)
        self.assertEqual(host.get('conversation', item['id'])['title'], '新对话')


if __name__ == '__main__':
    unittest.main()
