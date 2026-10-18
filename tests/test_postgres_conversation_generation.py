"""Manual generation concurrency on a real disposable PostgreSQL schema."""
import os
import unittest
from unittest.mock import patch

from postgres_fixture import create_schema, drop_schema
from test_conversation_generation import ConversationGenerationTests as _Base


@unittest.skipUnless(os.environ.get('JIANHUITONG_TEST_POSTGRES_URL'), 'PostgreSQL test URL is not configured')
class PostgreSQLConversationGenerationTests(_Base):
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


del _Base
