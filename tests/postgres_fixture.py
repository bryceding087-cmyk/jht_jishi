"""Private PostgreSQL schema per test class; never touch application tables."""
import os
import uuid

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def create_schema():
    raw = os.environ.get('JIANHUITONG_TEST_POSTGRES_URL')
    if not raw:
        raise RuntimeError('JIANHUITONG_TEST_POSTGRES_URL is required')
    url = make_url(raw)
    if url.drivername in ('postgres', 'postgresql'):
        url = url.set(drivername='postgresql+psycopg')
    schema = 'jht_test_' + uuid.uuid4().hex
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text('CREATE SCHEMA ' + schema))
    query = dict(url.query)
    query['options'] = '-csearch_path=' + schema
    return engine, schema, url.set(query=query).render_as_string(hide_password=False)


def drop_schema(engine, schema):
    if not schema.startswith('jht_test_') or not schema.removeprefix('jht_test_').isalnum():
        raise ValueError('Refusing to drop a non-test schema')
    with engine.begin() as db:
        db.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
    engine.dispose()
