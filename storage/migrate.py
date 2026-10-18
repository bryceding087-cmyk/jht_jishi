"""Read-only legacy SQLite import with atomic writes and content verification."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from sqlalchemy import func, insert, select, text

from .database import Database
from .schema import metadata, records, auth_users, auth_sessions, auth_login_limits, app_migrations
from .records import values_for

TABLES = (auth_users, auth_sessions, auth_login_limits, records, app_migrations)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def snapshot_sqlite(path):
    """One SQLite read transaction; never creates or updates the source file."""
    path = Path(path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        available = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        unknown = available - {table.name for table in TABLES}
        if unknown:
            raise ValueError('Source has unsupported tables; add an explicit migration before importing: ' + ', '.join(sorted(unknown)))
        if 'records' not in available or 'auth_users' not in available:
            raise ValueError('Source is not a Jianhuitong database')
        snapshot = {}
        for table in TABLES:
            if table.name not in available:
                snapshot[table.name] = []
                continue
            if table is records:
                source_columns = {row[1] for row in connection.execute('PRAGMA table_info(records)')}
                order_column = 'saved_at' if 'saved_at' in source_columns else 'rowid'
                rows = list(connection.execute(f'SELECT kind,id,body,{order_column} AS saved_order FROM records ORDER BY {order_column},id'))
                values = []
                for row in rows:
                    body = json.loads(row['body'])
                    if not isinstance(body, dict) or str(body.get('id')) != str(row['id']):
                        raise ValueError('Source contains an invalid record document or mismatched id')
                    canonical(body)
                    values.append(values_for(row['kind'], body, saved_at=float(row['saved_order'])))
            else:
                names = [column.name for column in table.columns]
                values = [dict(row) for row in connection.execute('SELECT ' + ','.join(names) + ' FROM ' + table.name)]
            snapshot[table.name] = sorted(values, key=lambda value: canonical([value[column.name] for column in table.primary_key]))
        connection.rollback()
        return snapshot
    finally:
        connection.close()


def fingerprint(snapshot):
    return hashlib.sha256(canonical(snapshot).encode('utf-8')).hexdigest()


def verify(connection, snapshot, marker):
    counts = {}
    for table in TABLES:
        expected = snapshot[table.name]
        values = list(connection.execute(select(table)).mappings())
        rows = [dict(row) for row in values]
        if table is app_migrations:
            expected_names = {row['name'] for row in expected}
            rows = [row for row in rows if row['name'] in expected_names or row['name'] not in ('sql-schema-v2', marker)]
        rows = sorted(rows, key=lambda value: canonical([value[column.name] for column in table.primary_key]))
        if canonical(rows) != canonical(expected):
            raise ValueError(f'Migration verification failed for {table.name}; target transaction rolled back')
        counts[table.name] = len(expected)
    return counts


def migrate(source, target_url, *, verify_only=False, allow_test_sqlite=False):
    if not target_url:
        raise ValueError('JIANHUITONG_DATABASE_URL (or DATABASE_URL) is required')
    if not target_url.startswith(('postgresql:', 'postgresql+psycopg:', 'postgres:')) and not allow_test_sqlite:
        raise ValueError('Migration target must be PostgreSQL')
    snapshot = snapshot_sqlite(source)
    digest = fingerprint(snapshot)
    marker = 'sqlite-import:' + digest[:40]
    database = Database(target_url)
    try:
        with database.engine.begin() as connection:
            if database.dialect == 'postgresql':
                connection.execute(text('SELECT pg_advisory_xact_lock(1785993478)'))
                connection.execute(text('LOCK TABLE auth_users,auth_sessions,auth_login_limits,records,app_migrations IN EXCLUSIVE MODE'))
            existing = connection.execute(select(app_migrations.c.name).where(app_migrations.c.name == marker)).first()
            if verify_only and not existing:
                raise ValueError('This source snapshot has not been migrated into the target')
            if not existing:
                for table in (auth_users, auth_sessions, auth_login_limits, records):
                    if connection.execute(select(func.count()).select_from(table)).scalar_one():
                        raise ValueError('Target contains application data; import requires a new empty database')
                # Source migration records are authoritative; preserve their
                # original timestamps as part of the verified snapshot.
                for table in TABLES:
                    rows = snapshot[table.name]
                    if table is app_migrations:
                        for row in rows:
                            connection.execute(table.delete().where(table.c.name == row['name']))
                    if rows:
                        connection.execute(insert(table), rows)
                if database.dialect == 'postgresql':
                    connection.execute(text("SELECT setval(pg_get_serial_sequence('auth_users','id'), COALESCE((SELECT MAX(id) FROM auth_users),1), EXISTS(SELECT 1 FROM auth_users))"))
                connection.execute(insert(app_migrations).values(name=marker, applied_at=time.time()))
            counts = verify(connection, snapshot, marker)
        return {'status': 'verified' if existing else 'migrated', 'sha256': digest, 'tables': counts,
                'records_by_kind': dict(sorted(Counter(row['kind'] for row in snapshot['records']).items()))}
    finally:
        database.engine.dispose()
