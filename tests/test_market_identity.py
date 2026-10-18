"""Trusted SDK account/session mapping tests."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from auth_service import AuthStore, verify_password
from market_identity import login_market_context_user


class MarketIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='jht-verified-identity-')
        path = Path(self.directory.name) / 'auth.db'
        url = 'sqlite:///' + path.as_posix()
        with patch.dict(os.environ, {'DATABASE_URL': url, 'JIANHUITONG_DATABASE_URL': url}):
            self.store = AuthStore(path, clock=lambda: 1000)

    def tearDown(self):
        self.store.database.engine.dispose()
        self.directory.cleanup()

    def login(self, subject='member-a', name='集市成员', issuer='https://market.example.test', **kwargs):
        return login_market_context_user(self.store, issuer=issuer, subject=subject, display_name=name, **kwargs)

    def test_stable_identity_updates_name_without_changing_owner(self):
        first, token, ttl = self.login()
        second, _, _ = self.login(name='新名称')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(second['role'], 'user')
        self.assertEqual(self.store.current_user(token)['display_name'], '新名称')
        self.store.clock = lambda: 1000 + ttl
        self.assertIsNone(self.store.current_user(token))

    def test_same_name_does_not_merge_users_or_platforms(self):
        ids = {self.login()[0]['id'], self.login(subject='member-b')[0]['id'],
               self.login(issuer='https://other.example.test')[0]['id']}
        self.assertEqual(len(ids), 3)

    def test_admin_name_does_not_grant_admin_or_enable_password(self):
        user, _, _ = self.login(subject='admin', name='管理员')
        self.assertEqual(user['role'], 'user')
        with self.store.connect() as db:
            row = db.execute('SELECT password_hash FROM auth_users WHERE id=?', (user['id'],)).fetchone()
            admin = db.execute('SELECT id,role FROM auth_users WHERE username=?', ('admin',)).fetchone()
        self.assertNotEqual(user['id'], admin[0])
        self.assertEqual(admin[1], 'admin')
        self.assertFalse(verify_password('!market-sso-only', row[0]))

    def test_disabled_account_remains_disabled(self):
        user, token, _ = self.login()
        with self.store.connect() as db:
            db.execute('UPDATE auth_users SET active=0 WHERE id=?', (user['id'],))
        with self.assertRaises(HTTPException):
            self.login()
        self.assertIsNone(self.store.current_user(token))

    def test_password_account_cannot_be_taken_over(self):
        user, _, _ = self.login()
        with self.store.connect() as db:
            db.execute('UPDATE auth_users SET password_hash=? WHERE id=?', ('existing-password-hash', user['id']))
        with self.assertRaises(HTTPException):
            self.login()

    def test_session_limit_keeps_latest_even_with_equal_timestamps(self):
        for _ in range(25):
            user, token, _ = self.login()
            self.assertIsNotNone(self.store.current_user(token))
        with self.store.connect() as db:
            count = db.execute('SELECT COUNT(*) FROM auth_sessions WHERE user_id=?', (user['id'],)).fetchone()[0]
        self.assertEqual(count, 20)

    def test_invalid_verified_claims_and_ttl_are_rejected(self):
        for ttl in (0, -1, 3601, True, 1.5):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                self.login(ttl=ttl)
        with self.assertRaises(ValueError):
            self.login(subject='')


if __name__ == '__main__':
    unittest.main()
