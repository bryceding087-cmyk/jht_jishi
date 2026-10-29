"""Server-side account scope for records and background work.

An absent scope is reserved for trusted startup/maintenance code. Every
authenticated HTTP request receives a scope from the verified session.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import time

from fastapi import HTTPException

SHARED_KINDS = frozenset({'model_config', 'llm_config', 'image_model_config', 'audio_model_config', 'model_default'})
_principal = ContextVar('jianhuitong_principal', default=None)


def current_principal():
    return _principal.get()


@contextmanager
def access_scope(user):
    token = _principal.set(dict(user) if user else None)
    try:
        yield
    finally:
        _principal.reset(token)


def is_admin():
    user = current_principal()
    return user is None or user.get('role') == 'admin'


def require_admin():
    if not is_admin():
        raise HTTPException(403, '仅管理员可以管理模型配置')


def record_owner(record):
    return str(record.get('owner_id') or record.get('creator_id') or '')


def can_access(kind, record):
    user = current_principal()
    return (user is None or user.get('role') == 'admin' or kind in SHARED_KINDS
            or bool(record_owner(record)) and record_owner(record) == str(user['id']))


def require_access(kind, record):
    if not can_access(kind, record):
        # Do not reveal whether another account's ID exists.
        raise HTTPException(404, '记录不存在')
    return record


def request_key(value):
    user = current_principal()
    return f"{user['id']}:{value}" if user else value


def parent_records(kind, record):
    if kind in ('agent_event', 'agent_artifact', 'agent_operation'):
        yield 'agent_run', record.get('run_id') or record['id'].split(':', 1)[0]
    if kind == 'asset' and record.get('task_id'):
        yield {'video': 'task', 'audio': 'audio_task'}.get(record.get('kind'), 'image_task'), record['task_id']
    if kind in ('asset', 'task', 'image_task', 'audio_task', 'agent_run', 'studio_render') and record.get('project_id'):
        yield 'conversation', record['project_id']
        yield 'project', record['project_id']


def parent_owner(kind, record, lookup):
    for parent_kind, parent_id in parent_records(kind, record):
        parent = lookup(parent_kind, parent_id)
        if parent is not None:
            return record_owner(parent)
    return None


def migrate_ownership(connect, data):
    """Infer ownership only from creation metadata or a recorded parent.

    Unknown historical records stay unassigned and are visible to admins only.
    This does not invent creator metadata. Back up before altering old records.
    """
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY, applied_at REAL NOT NULL)')
        rows = {(kind, rid): json.loads(body) for kind, rid, body in db.execute('SELECT kind,id,body FROM records')}
        first = not db.execute("SELECT 1 FROM app_migrations WHERE name='user-isolation-v1'").fetchone()
    original = {key: dict(value) for key, value in rows.items()}

    def resolve(kind, record, visited):
        key = (kind, record['id'])
        if record_owner(record):
            return record_owner(record)
        if key in visited:
            return ''
        visited = visited | {key}
        for parent_kind, parent_id in parent_records(kind, record):
            parent = rows.get((parent_kind, parent_id))
            if parent:
                owner = resolve(parent_kind, parent, visited)
                if owner:
                    return owner
        if kind == 'project':
            owners = {record_owner(run) for (rk, _), run in rows.items()
                      if rk == 'agent_run' and run.get('created_project') and run.get('project_id') == record['id'] and record_owner(run)}
            if len(owners) == 1:
                return owners.pop()
        return ''

    for (kind, _), record in rows.items():
        if kind not in SHARED_KINDS:
            record['owner_id'] = resolve(kind, record, set())
    changes = [(kind, rid, record) for (kind, rid), record in rows.items() if record != original[(kind, rid)]]
    if rows and (changes or first):
        from storage.backup import backup_database
        backup_database(connect, data, 'user-isolation')
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for kind, rid, record in changes:
            db.execute('UPDATE records SET body=? WHERE kind=? AND id=?', (json.dumps(record, ensure_ascii=False), kind, rid))
        if first:
            # Rollout requires a fresh password login, including administrators.
            db.execute('DELETE FROM auth_sessions')
            db.execute("INSERT INTO app_migrations VALUES ('user-isolation-v1', ?)", (time.time(),))
    return len(changes)
