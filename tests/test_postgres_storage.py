"""Run migration, rollback and SQL filtering contracts on PostgreSQL itself."""
import os
from pathlib import Path
import tempfile
import unittest

from postgres_fixture import create_schema, drop_schema
from storage.database import Database
from test_sql_storage import SQLStorageTests as _SQLStorageTests


@unittest.skipUnless(os.environ.get('JIANHUITONG_TEST_POSTGRES_URL'), 'PostgreSQL test URL is not configured')
class PostgreSQLStorageTests(_SQLStorageTests):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='jht-pg-contract-')
        self.root = Path(self.directory.name)
        self.pg_engine, self.pg_schema, url = create_schema()
        self.database = Database(url)

    def tearDown(self):
        try:
            self.database.engine.dispose()
            self.directory.cleanup()
        finally:
            drop_schema(self.pg_engine, self.pg_schema)


del _SQLStorageTests
