"""The HTTP isolation suite against a real, disposable PostgreSQL schema."""
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from sqlalchemy import select
from fastapi import HTTPException
from auth_service import LOGIN_PAIR_LIMIT
from storage.schema import records
from postgres_fixture import create_schema, drop_schema
from test_user_isolation import UserIsolationTests as _UserIsolationTests


@unittest.skipUnless(os.environ.get('JIANHUITONG_TEST_POSTGRES_URL'), 'PostgreSQL test URL is not configured')
class PostgreSQLIsolationTests(_UserIsolationTests):
    @classmethod
    def setUpClass(cls):
        cls.pg_engine, cls.pg_schema, cls.pg_url = create_schema()
        try:
            with patch.dict(os.environ, {'JIANHUITONG_DATABASE_URL': cls.pg_url}):
                super().setUpClass()
        except BaseException:
            drop_schema(cls.pg_engine, cls.pg_schema)
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.h.database.engine.dispose()
            super().tearDownClass()
        finally:
            drop_schema(cls.pg_engine, cls.pg_schema)

    def test_concurrent_revision_save_accepts_exactly_one_writer(self):
        project = self.request(self.a, 'GET', '/api/projects/' + self.pa['id'])
        barrier = threading.Barrier(2)
        def write(name):
            barrier.wait()
            return self.a.request('PUT', '/api/projects/' + project['id'], {
                'name': name, 'nodes': [], 'edges': [], 'revision': project['revision']})[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = sorted(pool.map(write, ('first', 'second')))
        self.assertEqual(outcomes, [200, 409])
        saved = self.request(self.a, 'GET', '/api/projects/' + project['id'])
        self.assertEqual(saved['revision'], project['revision'] + 1)

    def test_concurrent_initial_login_bucket_enforces_limit(self):
        store = self.h.app.state.auth_store
        barrier = threading.Barrier(10)
        def attempt(_):
            barrier.wait()
            try:
                store.reserve_login_attempt('concurrency-test', '127.0.0.77')
                return 200
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=10) as pool:
            outcomes = list(pool.map(attempt, range(10)))
        self.assertEqual(outcomes.count(200), LOGIN_PAIR_LIMIT)
        self.assertEqual(outcomes.count(429), 10 - LOGIN_PAIR_LIMIT)

    def test_postgres_uses_jsonb_and_indexed_ownership(self):
        with self.h.database.engine.connect() as db:
            row = db.execute(select(records.c.owner_id, records.c.body).where(records.c.id == self.pa['id'])).one()
            self.assertEqual(row.owner_id, str(self.users['dingyufu']['id']))
            self.assertIsInstance(row.body, dict)
            from sqlalchemy import text
            kind = db.execute(text("SELECT data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='records' AND column_name='body'" )).scalar_one()
            self.assertEqual(kind, 'jsonb')

# unittest discovery should not run the imported base class a second time.
del _UserIsolationTests
