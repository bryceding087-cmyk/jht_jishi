"""SQLAlchemy connections and a bounded bridge for legacy SQL call sites.

Production uses PostgreSQL. SQLite is retained for isolated tests and reading /
upgrading the previous local database before an explicit PostgreSQL migration.
"""
from contextlib import closing, contextmanager
from pathlib import Path
import json
import os
import re
import sqlite3
import threading
import time

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.pool import NullPool

from data_access import SHARED_KINDS, current_principal
from .schema import metadata, records
from . import records as record_store

_instances = {}
_instance_lock = threading.RLock()


class LegacyRow:
    """sqlite.Row-compatible keyed and positional access for old services/tests."""
    def __init__(self, row, names):
        self._names = tuple(names)
        self._values = tuple(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in row)

    def keys(self):
        return self._names

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __getitem__(self, key):
        return self._values[self._names.index(key)] if isinstance(key, str) else self._values[key]


class LegacyResult:
    def __init__(self, result):
        self.result = result
        self.names = tuple(result.keys()) if result.returns_rows else ()
        self.rowcount = result.rowcount
        # PostgreSQL has no SQLite lastrowid. SQLAlchemy's compatibility
        # property can close a psycopg SELECT cursor while probing it.
        self.lastrowid = None

    def fetchone(self):
        row = self.result.fetchone()
        return LegacyRow(row, self.names) if row is not None else None

    def fetchall(self):
        return [LegacyRow(row, self.names) for row in self.result.fetchall()]

    def __iter__(self):
        return (LegacyRow(row, self.names) for row in self.result)


def bind_sql(sql, parameters):
    """Translate DB-API qmarks to SQLAlchemy binds, never interpolate values."""
    if isinstance(parameters, dict):
        return sql, dict(parameters)
    values = tuple(parameters or ())
    pieces = sql.split('?')
    if len(pieces) - 1 != len(values):
        raise ValueError('SQL placeholder count does not match parameters')
    rewritten = pieces[0] + ''.join(f':p{i}{part}' for i, part in enumerate(pieces[1:]))
    return rewritten, {f'p{i}': value for i, value in enumerate(values)}


class Connection:
    def __init__(self, database, connection):
        self.database = database
        self.sa = connection
        self.lock_reads = False

    def _scoped_select(self, sql, parameters, *, mutation=False):
        user = current_principal()
        if user and user.get('role') != 'admin' and re.search(r'\bFROM\s+records\b', sql, re.I):
            shared = ','.join(f':scope_kind_{i}' for i, _ in enumerate(SHARED_KINDS))
            clause = (f'(owner_id=:scope_owner AND kind NOT IN ({shared}))' if mutation
                      else f'(owner_id=:scope_owner OR kind IN ({shared}))')
            parameters['scope_owner'] = str(user['id'])
            parameters.update({f'scope_kind_{i}': kind for i, kind in enumerate(SHARED_KINDS)})
            # Parenthesize the caller's entire condition. Simply prepending
            # "owner AND ..." would leak rows through an original OR branch.
            source = re.search(r'\bFROM\s+records\b', sql, re.I)
            head, tail = sql[:source.end()], sql[source.end():]
            where = re.match(r'\s+WHERE\s+', tail, re.I)
            if where:
                condition = tail[where.end():]
                ending = re.search(r'\s+(?:ORDER\s+BY|GROUP\s+BY|LIMIT|OFFSET|FOR\s+UPDATE)\b', condition, re.I)
                predicate, suffix = (condition[:ending.start()], condition[ending.start():]) if ending else (condition, '')
                sql = f'{head} WHERE {clause} AND ({predicate}){suffix}'
            else:
                sql = f'{head} WHERE {clause}{tail}'
        if self.lock_reads and self.database.dialect == 'postgresql' and re.match(r'SELECT\b', sql, re.I) and re.search(r'\bFROM\s+records\b', sql, re.I):
            sql += ' FOR UPDATE'
        return sql, parameters

    def execute(self, statement, parameters=()):
        if not isinstance(statement, str):
            return self.sa.execute(statement, parameters or {})
        sql = statement.strip().rstrip(';')
        if sql.upper() == 'BEGIN IMMEDIATE':
            self.lock_reads = True
            if self.database.dialect == 'sqlite':
                return LegacyResult(self.sa.exec_driver_sql('BEGIN IMMEDIATE'))
            return LegacyResult(self.sa.execute(text('SELECT 1')))
        # Existing record writers use full JSON documents. Synchronize indexed
        # columns here so compatibility writes cannot leave stale ACL metadata.
        if re.match(r'INSERT(?: OR REPLACE)? INTO records\b', sql, re.I):
            if len(parameters) != 3:
                raise ValueError('Record inserts must specify kind, id and body')
            kind, rid, body = parameters
            document = json.loads(body) if isinstance(body, str) else body
            if str(document.get('id')) != str(rid):
                raise ValueError('Record id does not match document id')
            return LegacyResult(record_store.upsert(self.sa, kind, document, enforce_owner=False))
        if re.match(r'UPDATE records\s+SET\s+', sql, re.I) and re.search(r'\bbody\s*=\s*\?', sql, re.I):
            assignments, where = re.split(r'\s+WHERE\s+', re.split(r'\s+SET\s+', sql, maxsplit=1, flags=re.I)[1], maxsplit=1, flags=re.I)
            used = assignments.count('?')
            body_index = assignments[:re.search(r'\bbody\s*=', assignments, re.I).start()].count('?')
            document = parameters[body_index]
            document = json.loads(document) if isinstance(document, str) else document
            select_sql, bindings = bind_sql('SELECT kind,id FROM records WHERE ' + where, parameters[used:])
            select_sql, bindings = self._scoped_select(select_sql, bindings, mutation=True)
            rows = self.sa.execute(text(select_sql), bindings).all()
            changed_kind = re.search(r"\bkind\s*=\s*'([^']+)'", assignments, re.I)
            result = None
            for old_kind, rid in rows:
                if str(document.get('id')) != str(rid):
                    raise ValueError('Record id does not match document id')
                values = record_store.values_for(changed_kind[1] if changed_kind else old_kind, document)
                result = self.sa.execute(records.update().where(records.c.id == rid, records.c.kind == old_kind).values(**values))
            return LegacyResult(result if result is not None else self.sa.execute(records.update().where(records.c.id == None).values(revision=0)))
        sql = re.sub(r'\browid\b', 'saved_at', sql, flags=re.I) if re.search(r'\bFROM\s+records\b', sql, re.I) else sql
        if re.match(r'UPDATE\s+records\b', sql, re.I):
            raise ValueError('Record updates must write the complete document to keep query columns consistent')
        sql, bindings = bind_sql(sql, parameters)
        if re.match(r'(?:SELECT|DELETE)\b', sql, re.I):
            sql, bindings = self._scoped_select(sql, bindings, mutation=bool(re.match(r'DELETE\b', sql, re.I)))
        return LegacyResult(self.sa.execute(text(sql), bindings))

    def backup(self, target):
        if self.database.dialect != 'sqlite':
            raise RuntimeError('Use database.backup() for PostgreSQL backups')
        self.sa.connection.driver_connection.backup(target)


class Database:
    def __init__(self, url, *, legacy_path=None, initialize=True):
        if url.startswith('postgres://'):
            url = 'postgresql+psycopg://' + url[len('postgres://'):]
        elif url.startswith('postgresql://'):
            url = 'postgresql+psycopg://' + url[len('postgresql://'):]
        self.legacy_path = Path(legacy_path) if legacy_path else None
        # SQLAlchemy exceptions include bound parameters by default. Documents
        # may contain provider credentials, so never include them in logs.
        kwargs = {'pool_pre_ping': True, 'hide_parameters': True}
        if url.startswith('sqlite:'):
            kwargs.update(poolclass=NullPool, connect_args={'timeout': 15, 'check_same_thread': False})
        self.engine = create_engine(url, **kwargs)
        self.dialect = self.engine.dialect.name
        if self.dialect not in ('sqlite', 'postgresql'):
            raise ValueError('Supported databases: PostgreSQL; SQLite for tests and legacy migration')
        if self.dialect == 'sqlite':
            @event.listens_for(self.engine, 'connect')
            def sqlite_connection(connection, _):
                connection.execute('PRAGMA foreign_keys = ON')
        if initialize:
            self.initialize()

    def initialize(self):
        inspector = inspect(self.engine)
        old_columns = {column['name'] for column in inspector.get_columns('records')} if inspector.has_table('records') else set()
        upgrade = bool(old_columns and set(records.c.keys()) - old_columns)
        if upgrade and self.dialect != 'sqlite':
            raise RuntimeError('Unversioned PostgreSQL records schema; run an explicit migration')
        if upgrade:
            if self.legacy_path:
                self.backup(self.legacy_path.parent, 'sql-schema-v2')
            declarations = {'owner_id': 'VARCHAR(80)', 'creator_id': 'VARCHAR(80)', 'context_id': 'VARCHAR(200)',
                'task_id': 'VARCHAR(200)', 'local_media_url': 'VARCHAR(512)', 'status': 'VARCHAR(80)', 'created_at': 'FLOAT', 'updated_at': 'FLOAT',
                'deleted_at': 'FLOAT', 'revision': 'BIGINT NOT NULL DEFAULT 0', 'saved_at': 'FLOAT NOT NULL DEFAULT 0'}
            with self.engine.begin() as connection:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
                for name, declaration in declarations.items():
                    if name not in old_columns:
                        connection.exec_driver_sql(f'ALTER TABLE records ADD COLUMN {name} {declaration}')
                rows = connection.execute(select(records.c.kind, records.c.id, records.c.body)).all()
                for order, (kind, rid, body) in enumerate(rows):
                    values = record_store.values_for(kind, body, saved_at=float(order))
                    connection.execute(records.update().where(records.c.id == rid).values(**values))
        metadata.create_all(self.engine)
        for index in records.indexes:
            index.create(self.engine, checkfirst=True)
        with self.engine.begin() as connection:
            from .schema import app_migrations
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            insert = pg_insert if self.dialect == 'postgresql' else sqlite_insert
            connection.execute(insert(app_migrations).values(name='sql-schema-v2', applied_at=time.time()).on_conflict_do_nothing())

    @contextmanager
    def transaction(self):
        with self.engine.begin() as connection:
            yield Connection(self, connection)

    def backup(self, data, prefix):
        """Preserve a transaction-consistent snapshot before legacy transforms.

        PostgreSQL snapshots are JSON exports of all application SQL tables and
        contain secrets; they remain in the local backup directory, never logs.
        Media files are not removed by SQL migrations.
        """
        directory = Path(data) / 'backups'
        directory.mkdir(parents=True, exist_ok=True)
        if self.dialect == 'sqlite':
            path = directory / f'{prefix}-{time.time_ns()}.sqlite3'
            with self.engine.connect() as source, closing(sqlite3.connect(path)) as target:
                source.connection.driver_connection.backup(target)
        else:
            path = directory / f'{prefix}-{time.time_ns()}.json'
            with self.engine.connect().execution_options(isolation_level='REPEATABLE READ') as source:
                with source.begin():
                    payload = {table.name: [dict(row) for row in source.execute(select(table)).mappings()] for table in metadata.sorted_tables}
            path.write_text(json.dumps({'format': 'jianhuitong-sql-backup-v1', 'tables': payload}, ensure_ascii=False), encoding='utf-8')
        return path


def database_for(path, url=None):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    selected = url or os.environ.get('JIANHUITONG_DATABASE_URL') or os.environ.get('DATABASE_URL') or ('sqlite:///' + path.as_posix())
    key = (selected, str(path))
    with _instance_lock:
        if key not in _instances:
            _instances[key] = Database(selected, legacy_path=path)
        return _instances[key]
