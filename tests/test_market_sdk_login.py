"""Trusted SDK login, cookie-free API/media ownership and key lifecycle."""
from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PIL import Image

from auth_service import hash_password
from data_access import access_scope
from creator_metadata import creator_scope, user_creator
from market_service import model_api_key
from test_auth import Browser


class MarketSDKLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jht-sdk-login-')
        url = 'sqlite:///' + (Path(cls.directory.name) / 'fixture.db').as_posix()
        spec = importlib.util.spec_from_file_location('sdk_login_app', Path(__file__).resolve().parents[1] / 'app.py')
        cls.h = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, {'JIANHUITONG_DATA_DIR': cls.directory.name, 'DATABASE_URL': url,
                'JIANHUITONG_DATABASE_URL': url, 'JIANHUITONG_MARKET_ORIGIN': 'https://market.example.test',
                'JIANHUITONG_MARKET_MODEL_ORIGIN': 'http://gateway.test:4091',
                'JIANHUITONG_PUBLIC_ORIGIN': 'https://aimarket.hncig.cn:4193',
                'JIANHUITONG_MARKET_AGENT_ID': 'fixture-agent', 'JIANHUITONG_MARKET_SDK_LOGIN': '1'}):
            spec.loader.exec_module(cls.h)
        cls.market = cls.h.app.state.market_integration
        cls.store = cls.h.app.state.auth_store
        with cls.h.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password('fixture-password'), 'admin'))

    @classmethod
    def tearDownClass(cls):
        cls.h.database.engine.dispose()
        cls.directory.cleanup()

    def setUp(self):
        self.now = time.time()
        self.market.clock = self.store.clock = lambda: self.now
        self.market._bindings.clear()
        with self.h.connect() as db:
            for table in ('records', 'auth_sessions', 'auth_login_limits'):
                db.execute('DELETE FROM ' + table)
        self.browser = Browser(self.h.app)

    def context(self, subject='user-a'):
        return {'user': {'id': subject, 'name': '同名成员', 'accountType': 'group', 'workNumber': 'TEST-001',
            'organization': {'id': 'org', 'name': '测试部门', 'type': 'department'}, 'role': 'admin'},
            'agent': {'id': 'fixture-agent', 'name': '建绘通'}, 'apiKey': 'sk-fixture-' + subject}

    def call(self, method, path, body=None, binding=None, expected=200, browser=None, **kwargs):
        headers = kwargs.pop('headers', {})
        if binding:
            headers['x-jht-market-session'] = binding
        status, response_headers, raw = (browser or self.browser).request(method, path, body, headers=headers, **kwargs)
        self.assertEqual(status, expected, raw.decode(errors='replace'))
        self.assertNotIn(b'sk-fixture', raw)
        return json.loads(raw) if raw else None

    def login(self, subject='user-a'):
        return self.call('POST', '/api/integrations/aimarket/login', self.context(subject), expected=201)

    def test_anonymous_market_shell_and_assets_without_business_access(self):
        status, headers, body = self.browser.request('GET', '/market')
        self.assertEqual(status, 200)
        self.assertIn(b'id="sessionUser"', body)
        self.assertNotIn(b'id="loginForm"', body)
        self.assertIn('https://market.example.test', headers['content-security-policy'])
        self.assertEqual(self.browser.request('GET', '/app.js')[0], 200)
        self.assertEqual(self.browser.request('GET', '/?embedded=1&view=canvas')[0], 200)
        self.call('GET', '/api/projects', expected=401)
        self.assertEqual(self.browser.request('GET', '/')[0], 303)

    def test_login_without_cookie_and_own_profile(self):
        login = self.login()
        self.assertEqual(self.browser.cookies, {})
        user = self.call('GET', '/api/auth/me', binding=login['binding_id'])['user']
        self.assertEqual(user['display_name'], '同名成员')
        self.assertEqual(user['role'], 'user')
        self.assertEqual(user['market_user']['organization']['name'], '测试部门')
        self.assertEqual(user['market_user']['workNumber'], 'TEST-001')
        self.call('PUT', '/api/llm', {'api_key': 'cannot-promote'}, binding=login['binding_id'], expected=403)

    def test_two_users_isolated_and_refresh_preserves_identity(self):
        first, second = self.login(), self.login('user-b')
        project = self.call('POST', '/api/projects', {'name': '用户甲的项目'}, binding=first['binding_id'], expected=201)
        self.call('GET', '/api/projects/' + project['id'], binding=second['binding_id'], expected=404)
        self.assertEqual(self.call('GET', '/api/projects', binding=second['binding_id']), [])
        refreshed = self.login()
        self.assertEqual(self.call('GET', '/api/projects/' + project['id'], binding=refreshed['binding_id'])['id'], project['id'])
        self.assertNotEqual(first['binding_id'], refreshed['binding_id'])
        with self.h.connect() as db:
            self.assertNotIn('sk-fixture', str([tuple(row) for row in db.execute('SELECT body FROM records')]))

    def test_media_capability_is_read_only_and_checks_owner(self):
        first, second = self.login(), self.login('user-b')
        user = self.call('GET', '/api/auth/me', binding=first['binding_id'])['user']
        image = io.BytesIO()
        Image.new('RGB', (2, 2), 'blue').save(image, format='PNG')
        payload = image.getvalue()
        with access_scope(user), creator_scope(user_creator(user)):
            asset = self.h.store_uploaded_asset(payload, 'fixture.png', '')
        status, _, body = self.browser.request('GET', asset['url'] + '?jht_media=' + first['media_token'])
        self.assertEqual(status, 200)
        self.assertEqual(Image.open(io.BytesIO(body)).size, (2, 2))
        ranged_status, _, ranged = self.browser.request('GET', asset['url'] + '?jht_media=' + first['media_token'], headers={'range': 'bytes=0-7'})
        self.assertEqual((ranged_status, ranged), (206, body[:8]))
        self.assertEqual(self.browser.request('GET', asset['url'] + '?jht_media=' + second['media_token'])[0], 404)
        self.call('GET', '/api/projects?jht_media=' + first['media_token'], expected=401)
        self.call('GET', '/api/projects', binding=first['media_token'], expected=428)
        self.assertEqual(self.browser.request('GET', '/api/scripts/template?jht_media=' + first['media_token'])[0], 200)

    def test_logout_and_expiry_revoke_key_and_media(self):
        first = self.login()
        record = {'_market_binding_id': first['binding_id'], '_market_actor_id': self.call('GET', '/api/auth/me', binding=first['binding_id'])['user']['id']}
        with self.market.worker_scope(record):
            self.assertEqual(model_api_key('http://gateway.test:4091/v1/chat/completions', 'global'), 'sk-fixture-user-a')
        self.call('POST', '/api/auth/logout', binding=first['binding_id'], expected=204)
        self.call('GET', '/api/auth/me', binding=first['binding_id'], expected=428)
        self.assertEqual(self.browser.request('GET', '/uploads/example.txt?jht_media=' + first['media_token'])[0], 401)
        fresh = self.login()
        self.now += 3601
        self.call('GET', '/api/auth/me', binding=fresh['binding_id'], expected=428)

    def test_wrong_agent_and_cross_origin_cannot_create_session(self):
        context = self.context()
        context['agent']['id'] = 'wrong'
        self.call('POST', '/api/integrations/aimarket/login', context, expected=403)
        self.call('POST', '/api/integrations/aimarket/login', self.context(), origin='https://market.example.test', expected=403)
        self.assertEqual(self.market._bindings, {})

    def test_local_admin_cookie_is_not_used_as_market_identity(self):
        self.call('POST', '/api/auth/login', {'username': 'admin', 'password': 'fixture-password'})
        first = self.login()
        self.assertEqual(self.call('GET', '/api/auth/me', binding=first['binding_id'])['user']['role'], 'user')
        self.assertEqual(self.call('GET', '/api/auth/me')['user']['role'], 'admin')
        self.call('POST', '/api/auth/logout', binding=first['binding_id'], expected=204)
        self.assertEqual(self.call('GET', '/api/auth/me')['user']['role'], 'admin')

    def test_https_proxy_can_login_create_project_and_logout(self):
        # Actual production browser Origin, but HTTP backend with rewritten Host.
        headers = {'sec-fetch-site': 'same-origin', 'host': '192.168.168.117:8000'}
        origin = 'https://aimarket.hncig.cn:4193'
        login = self.call('POST', '/api/integrations/aimarket/login', self.context(),
                         expected=201, origin=origin, headers=dict(headers))
        project = self.call('POST', '/api/projects', {'name': '代理访问的个人项目'},
                           expected=201, binding=login['binding_id'], origin=origin, headers=dict(headers))
        self.assertEqual(self.call('GET', '/api/projects/' + project['id'], binding=login['binding_id'])['id'], project['id'])
        self.call('POST', '/api/auth/logout', binding=login['binding_id'], expected=204,
                  origin=origin, headers=dict(headers))
        self.call('GET', '/api/auth/me', binding=login['binding_id'], expected=428)

    def test_proxy_origin_does_not_allow_foreign_origin_or_forged_forwarding(self):
        for origin in ('https://evil.example', 'https://aimarket.hncig.cn:4090',
                       'https://aimarket.hncig.cn:4194', 'http://aimarket.hncig.cn:4193', 'null'):
            with self.subTest(origin=origin):
                self.call('POST', '/api/integrations/aimarket/login', self.context(), expected=403,
                    origin=origin, headers={'sec-fetch-site': 'same-origin',
                        'x-forwarded-proto': 'https', 'x-forwarded-host': 'aimarket.hncig.cn:4193'})
        for metadata in ('cross-site', 'same-site', ''):
            with self.subTest(metadata=metadata):
                self.call('POST', '/api/integrations/aimarket/login', self.context(), expected=403,
                    origin='https://aimarket.hncig.cn:4193', headers={'sec-fetch-site': metadata})
if __name__ == '__main__':
    unittest.main()
