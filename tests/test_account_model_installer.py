"""Private configuration preflight, preservation, idempotency and rollback."""
from contextlib import closing, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from test_account_video_models import fixture_models

spec = importlib.util.spec_from_file_location('account_model_installer',
    Path(__file__).resolve().parents[1] / 'deploy/provision_account_video_models.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class PrivateInstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='jht-private-install-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.data = self.root / 'data'; self.data.mkdir()
        self.db = self.data / 'fixture.db'
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('CREATE TABLE auth_users (id INTEGER PRIMARY KEY, username TEXT, active INTEGER)')
            db.execute("INSERT INTO auth_users VALUES (1, 'assigned-account', 1)")
            db.commit()
        self.document = {'version': 1, 'accounts': [{'username': 'assigned-account', 'models': fixture_models()}]}
        self.target = self.data / installer.FILE_NAME
        self.env = patch.dict(os.environ, {'JIANHUITONG_DATABASE_URL': 'sqlite:///' + self.db.as_posix()})
        self.env.start(); self.addCleanup(self.env.stop)

    def run_action(self, action):
        output = io.StringIO()
        with redirect_stdout(output): installer.execute(action, self.root, self.data, self.document)
        self.assertNotIn('fixture-private-', output.getvalue())

    def test_apply_verify_repeat_rollback_and_reinstall(self):
        self.run_action('preflight')
        self.assertFalse(self.target.exists())
        for action in ('apply', 'verify', 'apply', 'preflight-rollback', 'rollback', 'rollback'):
            self.run_action(action)
        self.assertFalse(self.target.exists())
        self.run_action('apply'); self.run_action('verify')
        self.assertEqual(json.loads(self.target.read_text('utf-8')), self.document)

    def test_missing_or_disabled_account_fails_before_changes(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute('DELETE FROM auth_users'); db.commit()
        for action in ('preflight', 'apply'):
            with self.assertRaises(SystemExit): self.run_action(action)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.data / '.deployment-patches').exists())

    def test_other_accounts_and_original_bytes_survive_round_trip(self):
        other = {'username': 'other', 'models': [{**fixture_models()[0], 'id': 'account-video:other'}]}
        original = json.dumps({'version': 1, 'accounts': [other]}, indent=4).encode()
        self.target.write_bytes(original)
        self.run_action('apply')
        self.assertEqual(json.loads(self.target.read_text('utf-8'))['accounts'][0], other)
        self.run_action('rollback')
        self.assertEqual(self.target.read_bytes(), original)

    def test_rollback_and_reapply_refuse_later_edits(self):
        self.run_action('apply')
        changed = json.loads(self.target.read_text('utf-8'))
        changed['accounts'][0]['models'][0]['api_key'] = 'changed-private-key'
        self.target.write_text(json.dumps(changed), encoding='utf-8')
        before = self.target.read_bytes()
        for action in ('preflight-rollback', 'rollback', 'preflight', 'apply'):
            with self.assertRaises(SystemExit): self.run_action(action)
            self.assertEqual(self.target.read_bytes(), before)


if __name__ == '__main__': unittest.main()
