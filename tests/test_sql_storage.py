"""Storage contracts use disposable databases and never import production app."""
from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import event, func, select
from data_access import access_scope
from storage.database import Database
from storage.records import fetch, listing, upsert
from storage.schema import records, auth_users, app_migrations
from storage.migrate import migrate, snapshot_sqlite


class SQLStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='jht-sql-contract-')
        self.root = Path(self.directory.name)
        self.database = Database('sqlite:///' + (self.root / 'new.sqlite3').as_posix())

    def tearDown(self):
        self.database.engine.dispose()
        self.directory.cleanup()

    def source(self):
        source = self.root / 'legacy.sqlite3'
        with closing(sqlite3.connect(source)) as db:
            db.executescript('''
                CREATE TABLE records(kind TEXT,id TEXT PRIMARY KEY,body TEXT);
                CREATE TABLE auth_users(id INTEGER PRIMARY KEY,username TEXT,display_name TEXT,role TEXT,password_hash TEXT,active INTEGER,created_at REAL);
                CREATE TABLE auth_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER,created_at REAL,expires_at REAL);
                CREATE TABLE auth_login_limits(bucket TEXT PRIMARY KEY,attempts INTEGER,expires_at REAL);
                CREATE TABLE app_migrations(name TEXT PRIMARY KEY,applied_at REAL);
            ''')
            db.execute('INSERT INTO auth_users VALUES(7,?,?,?,?,1,?)', ('member', '成员', 'user', 'preserved-hash', 12.5))
            db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)', ('session-hash', 7, 10.0, 90.0))
            db.execute('INSERT INTO auth_login_limits VALUES(?,?,?)', ('bucket', 2, 99.0))
            db.execute('INSERT INTO app_migrations VALUES(?,?)', ('user-isolation-v1', 20.5))
            for kind, body in [('project', {'id': 'canvas', 'owner_id': '7', 'creator_id': '7', 'nodes': [{'text': '原文'}], 'revision': 4}),
                               ('asset', {'id': 'asset', 'owner_id': '7', 'url': '/uploads/original.png', 'project_id': 'canvas'}),
                               ('project', {'id': 'unknown', 'owner_id': ''})]:
                db.execute('INSERT INTO records VALUES(?,?,?)', (kind, body['id'], json.dumps(body, ensure_ascii=False)))
            db.commit()
        return source

    def test_structured_columns_and_account_filter_are_in_sql(self):
        with self.database.transaction() as db:
            for owner in ('7', '8'):
                db.execute('INSERT INTO records VALUES (?,?,?)', ('project', 'p' + owner, json.dumps({'id': 'p' + owner, 'owner_id': owner})))
            db.execute("UPDATE records SET body=? WHERE kind='project' AND id=?", (json.dumps({'id': 'p7', 'owner_id': '7', 'status': 'done', 'revision': 3}), 'p7'))
        statements = []
        def capture(_conn, _cursor, statement, parameters, _context, _many):
            statements.append((statement, parameters))
        event.listen(self.database.engine, 'before_cursor_execute', capture)
        try:
            with access_scope({'id': 7, 'role': 'user'}), self.database.transaction() as db:
                self.assertEqual([row['id'] for row in listing(db.sa, 'project')], ['p7'])
                self.assertIsNone(fetch(db.sa, 'project', 'p8'))
                self.assertEqual([row[0] for row in db.execute('SELECT id FROM records WHERE kind=?', ('project',))], ['p7'])
            self.assertTrue(all('owner_id' in statement and 'WHERE' in statement for statement, _ in statements))
        finally:
            event.remove(self.database.engine, 'before_cursor_execute', capture)
        with self.database.engine.connect() as db:
            value = db.execute(select(records).where(records.c.id == 'p7')).mappings().one()
            self.assertEqual((value['owner_id'], value['status'], value['revision']), ('7', 'done', 3))

    def test_transaction_rolls_back_documents_and_index_columns(self):
        with self.assertRaisesRegex(RuntimeError, 'rollback'):
            with self.database.transaction() as db:
                upsert(db.sa, 'project', {'id': 'rolled-back', 'owner_id': '7'})
                raise RuntimeError('rollback')
        with self.database.engine.connect() as db:
            self.assertEqual(db.execute(select(func.count()).select_from(records)).scalar_one(), 0)

    def test_legacy_select_or_and_delete_keep_account_boundary(self):
        with self.database.transaction() as db:
            for kind, document in [('project', {'id': 'mine', 'owner_id': '7'}),
                                   ('project', {'id': 'other', 'owner_id': '8'}),
                                   ('model_config', {'id': 'shared-model'})]:
                upsert(db.sa, kind, document)
        with access_scope({'id': 7, 'role': 'user'}), self.database.transaction() as db:
            selected = db.execute('SELECT id FROM records WHERE kind=? OR id=? ORDER BY id', ('project', 'other')).fetchall()
            self.assertEqual([row[0] for row in selected], ['mine'])
            self.assertEqual(db.execute('DELETE FROM records WHERE kind=? OR id=?', ('project', 'other')).rowcount, 1)
            self.assertEqual(db.execute('DELETE FROM records WHERE kind=?', ('model_config',)).rowcount, 0)
        with self.database.engine.connect() as db:
            self.assertEqual(set(db.execute(select(records.c.id)).scalars()), {'other', 'shared-model'})

    def test_old_sqlite_upgrade_preserves_documents_and_has_backup(self):
        source = self.source()
        before = snapshot_sqlite(source)
        upgraded = Database('sqlite:///' + source.as_posix(), legacy_path=source)
        upgraded.engine.dispose()
        after = snapshot_sqlite(source)
        self.assertEqual([r['body'] for r in before['records']], [r['body'] for r in after['records']])
        self.assertEqual(before['auth_users'], after['auth_users'])
        self.assertEqual(len(list((self.root / 'backups').glob('sql-schema-v2-*.sqlite3'))), 1)

    def test_import_preserves_source_ids_secrets_paths_and_is_idempotent(self):
        source = self.source()
        original = hashlib.sha256(source.read_bytes()).hexdigest()
        url = self.database.engine.url.render_as_string(hide_password=False)
        result = migrate(source, url, allow_test_sqlite=True)
        again = migrate(source, url, allow_test_sqlite=True, verify_only=True)
        self.assertEqual(result['status'], 'migrated')
        self.assertEqual(again['status'], 'verified')
        self.assertEqual(result['sha256'], again['sha256'])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), original)
        with self.database.engine.connect() as db:
            user = db.execute(select(auth_users)).mappings().one()
            self.assertEqual((user['id'], user['password_hash']), (7, 'preserved-hash'))
            self.assertEqual(fetch(db, 'asset', 'asset')['url'], '/uploads/original.png')
        with access_scope({'id': 7, 'role': 'user'}), self.database.engine.connect() as db:
            self.assertEqual([r['id'] for r in listing(db, 'project')], ['canvas'])

    def test_failed_verification_rolls_back_all_imported_rows(self):
        source = self.source()
        with patch('storage.migrate.verify', side_effect=ValueError('forced verification failure')):
            with self.assertRaisesRegex(ValueError, 'forced'):
                migrate(source, self.database.engine.url.render_as_string(hide_password=False), allow_test_sqlite=True)
        with self.database.engine.connect() as db:
            for table in (records, auth_users):
                self.assertEqual(db.execute(select(func.count()).select_from(table)).scalar_one(), 0)
            self.assertFalse(db.execute(select(app_migrations.c.name).where(app_migrations.c.name.like('sqlite-import:%'))).first())

    def test_import_rejects_existing_target_data_and_detects_drift(self):
        source = self.source()
        url = self.database.engine.url.render_as_string(hide_password=False)
        with self.database.engine.begin() as db:
            upsert(db, 'project', {'id': 'existing'})
        with self.assertRaisesRegex(ValueError, 'empty database'):
            migrate(source, url, allow_test_sqlite=True)
        with self.database.engine.begin() as db:
            db.execute(records.delete())
        migrate(source, url, allow_test_sqlite=True)
        with self.database.engine.begin() as db:
            upsert(db, 'project', {'id': 'canvas', 'owner_id': '7', 'name': 'unexpected change'})
        with self.assertRaisesRegex(ValueError, 'verification failed'):
            migrate(source, url, allow_test_sqlite=True)


if __name__ == '__main__':
    unittest.main()
