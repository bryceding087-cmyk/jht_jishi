"""Authentication integration tests; all application data lives in a temp dir.

Run with: python -m unittest discover -s tests -v
The small ASGI driver uses the standard library, so httpx is not required.
"""

import asyncio
from http.cookies import SimpleCookie
import importlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import urlsplit

from auth_service import (
    AuthStore, COOKIE_NAME, LOGIN_IP_LIMIT, LOGIN_PAIR_LIMIT, LOGIN_WINDOW,
    MAX_RATE_BUCKETS, REMEMBER_SECONDS, SESSION_SECONDS, hash_password,
    token_hash, verify_password,
)


class Browser:
    def __init__(self, app, scheme='http'):
        self.app = app
        self.scheme = scheme
        self.cookies = {}

    def request(self, method, path, payload=None, *, origin='default', headers=None):
        async def run():
            parsed = urlsplit(path)
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else b''
            request_headers = {'host': 'testserver'}
            if payload is not None:
                request_headers['content-type'] = 'application/octet-stream' if isinstance(payload, bytes) else 'application/json'
                request_headers['content-length'] = str(len(body))
            if self.cookies:
                request_headers['cookie'] = '; '.join(f'{key}={value}' for key, value in self.cookies.items())
            if origin == 'default' and method not in ('GET', 'HEAD', 'OPTIONS'):
                request_headers['origin'] = f'{self.scheme}://testserver'
            elif origin is not None and origin != 'default':
                request_headers['origin'] = origin
            request_headers.update(headers or {})
            scope = {
                'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
                'http_version': '1.1', 'method': method, 'scheme': self.scheme,
                'path': parsed.path, 'raw_path': parsed.path.encode(),
                'query_string': parsed.query.encode(), 'root_path': '',
                'headers': [(key.encode(), value.encode()) for key, value in request_headers.items()],
                'client': ('127.0.0.1', 12345), 'server': ('testserver', 443 if self.scheme == 'https' else 80),
            }
            sent = []
            first_receive = True
            completed = asyncio.Event()

            async def receive():
                nonlocal first_receive
                if first_receive:
                    first_receive = False
                    return {'type': 'http.request', 'body': body, 'more_body': False}
                await completed.wait()
                return {'type': 'http.disconnect'}

            async def send(message):
                sent.append(message)
                if message['type'] == 'http.response.body' and not message.get('more_body', False):
                    completed.set()

            await self.app(scope, receive, send)
            start = next(message for message in sent if message['type'] == 'http.response.start')
            response_headers = {key.decode(): value.decode() for key, value in start['headers']}
            for key, value in start['headers']:
                if key == b'set-cookie':
                    cookie = SimpleCookie()
                    cookie.load(value.decode())
                    for name, morsel in cookie.items():
                        if morsel['max-age'] == '0':
                            self.cookies.pop(name, None)
                        else:
                            self.cookies[name] = morsel.value
            content = b''.join(message.get('body', b'') for message in sent if message['type'] == 'http.response.body')
            return start['status'], response_headers, content

        return asyncio.run(run())


class AuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix='jianhuitong-auth-test-')
        cls.data = Path(cls.directory.name)
        previous = os.environ.get('JIANHUITONG_DATA_DIR')
        os.environ['JIANHUITONG_DATA_DIR'] = str(cls.data)
        try:
            cls.module = importlib.import_module('app')
        finally:
            if previous is None:
                os.environ.pop('JIANHUITONG_DATA_DIR', None)
            else:
                os.environ['JIANHUITONG_DATA_DIR'] = previous
        if cls.module.DATA != cls.data:
            raise RuntimeError('Tests require a fresh app import; refusing to use existing application data')
        cls.app = cls.module.app
        cls.store = cls.app.state.auth_store

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.now = 1_800_000_000.0
        self.store.clock = lambda: self.now
        with self.store.connect() as connection:
            connection.execute('DELETE FROM auth_sessions')
            connection.execute('DELETE FROM auth_users')
            connection.execute('DELETE FROM auth_login_limits')
        self.store.initialize()
        self.browser = Browser(self.app)

    def login(self, username='admin', password='zxyz@20261001', remember=False, browser=None):
        return (browser or self.browser).request('POST', '/api/auth/login', {
            'username': username, 'password': password, 'remember': remember,
        })

    def test_seeded_accounts_cookie_and_current_user(self):
        for username, role in (('admin', 'admin'), ('dingyufu', 'user')):
            with self.subTest(username=username):
                browser = Browser(self.app)
                status, headers, body = self.login(username=username, browser=browser)
                self.assertEqual(status, 200)
                user = json.loads(body)['user']
                self.assertEqual((user['username'], user['role']), (username, role))
                self.assertNotIn('password', body.decode())
                cookie = headers['set-cookie']
                self.assertIn('HttpOnly', cookie)
                self.assertIn('SameSite=lax', cookie)
                self.assertNotIn('Max-Age', cookie)
                status, headers, body = browser.request('GET', '/api/auth/me')
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body)['user'], user)
                self.assertEqual(headers['cache-control'], 'no-store')
        with self.store.connect() as connection:
            rows = connection.execute('SELECT password_hash FROM auth_users').fetchall()
        self.assertNotEqual(rows[0][0], rows[1][0])
        self.assertTrue(all(verify_password('zxyz@20261001', row[0]) for row in rows))

    def test_protected_routes_and_login_pages(self):
        for path in ('/', '/index.html', '/app.js', '/uploads/private.png', '/docs', '/openapi.json'):
            with self.subTest(path=path):
                status, headers, _ = self.browser.request('GET', path)
                self.assertEqual(status, 303)
                self.assertEqual(headers['location'], '/login')
        for path in ('/api/projects', '/api/assets', '/api/models', '/api/auth/me'):
            self.assertEqual(self.browser.request('GET', path)[0], 401)
        for path in ('/login', '/login.html', '/login.css', '/login.js', '/favicon.svg', '/iconography.js', '/api/health'):
            self.assertEqual(self.browser.request('GET', path)[0], 200, path)
        self.assertEqual(self.login()[0], 200)
        for path in ('/', '/index.html', '/app.js', '/api/projects', '/api/options'):
            self.assertEqual(self.browser.request('GET', path)[0], 200, path)
        for path in ('/login', '/login.html'):
            status, headers, _ = self.browser.request('GET', path)
            self.assertEqual(status, 303)
            self.assertEqual(headers['location'], '/')

    def test_uploaded_files_require_live_session(self):
        upload = self.data / 'uploads' / 'auth-protected.txt'
        upload.write_text('private test asset', encoding='utf-8')
        self.assertEqual(self.browser.request('GET', '/uploads/auth-protected.txt')[0], 303)
        self.login()
        status, headers, content = self.browser.request('GET', '/uploads/auth-protected.txt')
        self.assertEqual((status, content), (200, b'private test asset'))
        self.assertEqual(headers['cache-control'], 'no-store')
        self.browser.request('POST', '/api/auth/logout')
        self.assertEqual(self.browser.request('GET', '/uploads/auth-protected.txt')[0], 303)

    def test_bad_credentials_and_validation_do_not_echo_secrets(self):
        secret = 'private-invalid-password'
        for username in ('admin', 'unknown'):
            status, _, body = self.login(username=username, password=secret)
            self.assertEqual(status, 401)
            self.assertEqual(json.loads(body), {'detail': '用户名或密码不正确'})
        secret = 'x' * 257
        status, _, body = self.login(password=secret)
        self.assertEqual(status, 422)
        self.assertNotIn(secret.encode(), body)
        self.assertNotIn(b'input', body)

    def test_remember_cookie_and_https_secure_flag(self):
        browser = Browser(self.app, scheme='https')
        status, headers, _ = self.login(remember=True, browser=browser)
        self.assertEqual(status, 200)
        self.assertIn(f'Max-Age={REMEMBER_SECONDS}', headers['set-cookie'])
        self.assertIn('Secure', headers['set-cookie'])
        self.now += SESSION_SECONDS + 1
        self.assertEqual(browser.request('GET', '/api/auth/me')[0], 200)
        self.now += REMEMBER_SECONDS
        self.assertEqual(browser.request('GET', '/api/auth/me')[0], 401)

    def test_session_tampering_expiry_logout_and_rotation(self):
        self.login()
        initial = self.browser.cookies[COOKIE_NAME]
        self.browser.cookies[COOKIE_NAME] = 'A' * 43
        self.assertEqual(self.browser.request('GET', '/api/auth/me')[0], 401)
        self.browser.cookies[COOKIE_NAME] = initial
        self.assertEqual(self.login()[0], 200)
        current = self.browser.cookies[COOKIE_NAME]
        self.assertNotEqual(initial, current)
        self.assertIsNone(self.store.current_user(initial))
        with self.store.connect() as connection:
            row = connection.execute('SELECT token_hash FROM auth_sessions').fetchone()
        self.assertEqual(row['token_hash'], token_hash(current))
        self.assertNotEqual(row['token_hash'], current)
        status, headers, _ = self.browser.request('POST', '/api/auth/logout')
        self.assertEqual(status, 204)
        self.assertIn('Max-Age=0', headers['set-cookie'])
        self.assertNotIn(COOKIE_NAME, self.browser.cookies)
        self.browser.cookies[COOKIE_NAME] = current
        self.assertEqual(self.browser.request('GET', '/api/auth/me')[0], 401)
        self.login()
        self.now += SESSION_SECONDS + 1
        self.assertEqual(self.browser.request('GET', '/api/auth/me')[0], 401)

    def test_csrf_login_logout_and_workspace_writes(self):
        credentials = {'username': 'admin', 'password': 'zxyz@20261001'}
        for origin in ('https://evil.example', 'null', 'http://testserver.evil.example', 'http://testserver:9000', None):
            with self.subTest(origin=origin):
                self.assertEqual(self.browser.request('POST', '/api/auth/login', credentials, origin=origin)[0], 403)
        self.assertEqual(self.browser.request('POST', '/api/auth/login', credentials,
            headers={'sec-fetch-site': 'cross-site'})[0], 403)
        self.assertEqual(self.browser.request('POST', '/api/auth/login', credentials,
            origin=None, headers={'referer': 'http://testserver/login'})[0], 200)
        self.assertEqual(self.browser.request('POST', '/api/auth/logout', origin='https://evil.example')[0], 403)
        self.assertEqual(self.browser.request('POST', '/api/projects', {}, origin='https://evil.example')[0], 403)
        self.assertEqual(self.browser.request('GET', '/api/auth/me')[0], 200)
        self.assertEqual(self.browser.request('POST', '/api/auth/logout', origin=None,
            headers={'sec-fetch-site': 'same-origin'})[0], 204)

    def test_rate_limit_persists_and_recovers(self):
        for _ in range(LOGIN_PAIR_LIMIT):
            self.assertEqual(self.login(password='wrong')[0], 401)
        status, headers, _ = self.login()
        self.assertEqual(status, 429)
        self.assertTrue(int(headers['retry-after']) > 0)
        restarted = AuthStore(self.store.path, clock=lambda: self.now)
        with self.assertRaises(Exception) as error:
            restarted.login('admin', 'zxyz@20261001', '127.0.0.1', False)
        self.assertEqual(error.exception.status_code, 429)
        self.now += LOGIN_WINDOW + 1
        self.assertEqual(self.login()[0], 200)

    def test_ip_rate_limit_covers_rotating_usernames(self):
        for index in range(LOGIN_IP_LIMIT):
            self.store.reserve_login_attempt(f'unknown-{index}', '192.0.2.1')
        with self.assertRaises(Exception) as error:
            self.store.reserve_login_attempt('another-name', '192.0.2.1')
        self.assertEqual(error.exception.status_code, 429)
        with self.store.connect() as connection:
            count = connection.execute('SELECT COUNT(*) FROM auth_login_limits').fetchone()[0]
        self.assertLessEqual(count, MAX_RATE_BUCKETS)

    def test_restart_preserves_password_profile_and_sessions(self):
        self.login()
        token = self.browser.cookies[COOKIE_NAME]
        changed_hash = hash_password('new-example-password')
        with self.store.connect() as connection:
            connection.execute("UPDATE auth_users SET password_hash=?, display_name='更新后的名称' WHERE username='admin'", (changed_hash,))
        restarted = AuthStore(self.store.path, clock=lambda: self.now)
        with restarted.connect() as connection:
            row = connection.execute("SELECT * FROM auth_users WHERE username='admin'").fetchone()
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM auth_users').fetchone()[0], 2)
        self.assertEqual(row['password_hash'], changed_hash)
        self.assertEqual(restarted.current_user(token)['display_name'], '更新后的名称')
        self.assertEqual(self.login(password='new-example-password')[0], 200)

    def test_disabling_user_revokes_access_and_blocks_login(self):
        self.login()
        with self.store.connect() as connection:
            connection.execute("UPDATE auth_users SET active=0 WHERE username='admin'")
        self.assertEqual(self.browser.request('GET', '/api/auth/me')[0], 401)
        self.assertEqual(self.login()[0], 401)


if __name__ == '__main__':
    unittest.main()
