"""Install an account-specific credential file; release builds supply the payload.

Run as the application's normal container user, not root. No business tasks or
account records are created. Existing accounts are checked with a read-only SQL
query before any release files change.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

DOCUMENT_B64 = ''
DOCUMENT_SHA256 = ''
VERSION = 'account-video-update-20261029-1'
FILE_NAME = 'account-video-models.json'


def sha(value):
    return hashlib.sha256(value).hexdigest() if value is not None else None


def encoded(document):
    return (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def payload():
    try:
        raw = base64.b64decode(DOCUMENT_B64, validate=True)
        if sha(raw) != DOCUMENT_SHA256: raise ValueError
        document = json.loads(raw)
        if document.get('version') != 1 or not document.get('accounts'): raise ValueError
        return document
    except (ValueError, TypeError, KeyError):
        raise SystemExit('Private configuration payload checksum/format mismatch.') from None


def read(path):
    return path.read_bytes() if path.exists() else None


def atomic_write(path, content):
    fd, name = tempfile.mkstemp(prefix='.account-models-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def merge(original, incoming):
    try:
        previous = json.loads(original) if original is not None else {'version': 1, 'accounts': []}
        if previous.get('version') != 1 or not isinstance(previous.get('accounts'), list): raise ValueError
        # Preserve other provisioned accounts and any additional models.
        accounts = {account['username']: account for account in previous['accounts']}
        if len(accounts) != len(previous['accounts']): raise ValueError
        for account in incoming['accounts']:
            username = account['username']
            models = {model['id']: model for model in accounts.get(username, {}).get('models', [])}
            models.update({model['id']: model for model in account['models']})
            accounts[username] = {**accounts.get(username, {}), **account, 'models': list(models.values())}
        result = {**previous, 'accounts': list(accounts.values())}
        ids = [model['id'] for account in result['accounts'] for model in account['models']]
        if len(ids) != len(set(ids)): raise ValueError
        return encoded(result)
    except (ValueError, TypeError, KeyError):
        raise SystemExit('Existing private configuration conflicts with this update; no credentials changed.') from None


def check_accounts(root, incoming):
    from dotenv import load_dotenv
    from sqlalchemy import create_engine, text
    load_dotenv(root / '.env', override=False)
    url = os.getenv('JIANHUITONG_DATABASE_URL') or os.getenv('DATABASE_URL')
    if not url: raise SystemExit('Application database configuration is missing.')
    if url.startswith('postgresql://'): url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    engine = create_engine(url, hide_parameters=True, pool_pre_ping=True)
    try:
        with engine.connect() as db:
            for account in incoming['accounts']:
                user = db.execute(text('SELECT id, active FROM auth_users WHERE username=:username'),
                                  {'username': account['username']}).first()
                if user is None or not user[1]:
                    raise SystemExit('The exact assigned account does not exist or is disabled; no files changed.')
    except SystemExit:
        raise
    except Exception:
        raise SystemExit('Could not verify the assigned account in the application database.') from None
    finally:
        engine.dispose()


def execute(action, root, data, incoming):
    root, data = Path(root).resolve(), Path(data).resolve()
    target = data / FILE_NAME
    backup = data / '.deployment-patches' / VERSION
    saved_path = backup / 'private-models-backup.json'
    for path in (target, backup, saved_path):
        if not path.resolve().is_relative_to(data) or any(p.is_symlink() for p in (path, *path.parents) if p != data):
            raise SystemExit('Refusing an unexpected private configuration path.')
    original = read(target)
    saved = None
    if saved_path.exists():
        try:
            saved = json.loads(saved_path.read_text('utf-8'))
            restored = base64.b64decode(saved['original'], validate=True) if saved['original'] is not None else None
            if sha(restored) != saved['original_sha256']: raise ValueError
        except (ValueError, TypeError, KeyError):
            raise SystemExit('Private configuration backup is invalid.') from None
    if action in ('rollback', 'preflight-rollback'):
        if saved is None: raise SystemExit('Private configuration backup is missing; rollback stopped.')
        if sha(original) not in (saved['original_sha256'], saved['installed_sha256']):
            raise SystemExit('Private configuration changed after installation; rollback stopped.')
        if action == 'rollback':
            if restored is None: target.unlink(missing_ok=True)
            else: atomic_write(target, restored)
        print('PASS: private configuration ' + action)
        return

    check_accounts(root, incoming)
    expected = merge(original, incoming)
    if action == 'verify':
        if original != expected: raise SystemExit('Installed private models do not match this release.')
        print('PASS: assigned account exists and both private models are installed; credentials omitted')
        return
    if saved and (sha(expected) != saved['installed_sha256'] or sha(original) not in (saved['installed_sha256'], saved['original_sha256'])):
        raise SystemExit('Private configuration differs from this release backup; no credentials changed.')
    if action == 'preflight':
        if not data.is_dir() or not os.access(data, os.W_OK):
            raise SystemExit('Application data directory is not writable by the application user.')
        print('PASS: assigned account and private configuration preflight')
        return
    if not saved:
        backup.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(backup, 0o700)
        atomic_write(saved_path, encoded({'original': base64.b64encode(original).decode() if original is not None else None,
            'original_sha256': sha(original), 'installed_sha256': sha(expected)}))
    atomic_write(target, expected)
    if read(target) != expected: raise SystemExit('Private configuration post-write verification failed.')
    print('PASS: installed two account-specific video models; credentials omitted')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('preflight', 'apply', 'verify', 'preflight-rollback', 'rollback'))
    parser.add_argument('--root', default='/app')
    args = parser.parse_args()
    root = Path(args.root)
    data = Path(os.getenv('JIANHUITONG_DATA_DIR') or root / 'data')
    execute(args.action, root, data, payload())


if __name__ == '__main__': main()
