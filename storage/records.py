"""Account-scoped record queries. Ownership is indexed and filtered by SQL."""
import time

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data_access import SHARED_KINDS, current_principal
from .schema import records


def scope_predicate():
    user = current_principal()
    if user is None or user.get('role') == 'admin':
        return None
    return or_(records.c.kind.in_(SHARED_KINDS), records.c.owner_id == str(user['id']))


def values_for(kind, body, saved_at=None):
    """Preserve the exact document while deriving authoritative query columns."""
    def timestamp(key):
        value = body.get(key)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    media_url = body.get('url') if kind == 'asset' else body.get('video_url') if kind == 'task' else None
    media_url = media_url if isinstance(media_url, str) and media_url.startswith('/uploads/') and len(media_url) <= 512 else None
    return dict(kind=kind, id=str(body['id']), body=body,
        owner_id=str(body.get('owner_id') or body.get('creator_id') or '') or None,
        creator_id=str(body.get('creator_id') or '') or None,
        context_id=str(body.get('project_id') or body.get('context_id') or '') or None,
        task_id=str(body.get('task_id') or '') or None,
        local_media_url=media_url,
        status=str(body.get('status') or '') or None,
        created_at=timestamp('created_at'), updated_at=timestamp('updated_at'), deleted_at=timestamp('deleted_at') or None,
        revision=int(body.get('revision') or 0), saved_at=time.time() if saved_at is None else saved_at)


def query(kind=None, rid=None):
    statement = select(records.c.body)
    if kind is not None:
        statement = statement.where(records.c.kind == kind)
    if rid is not None:
        statement = statement.where(records.c.id == rid)
    scope = scope_predicate()
    return statement.where(scope) if scope is not None else statement


def fetch(connection, kind, rid, lock=False):
    statement = query(kind, rid)
    if lock and connection.dialect.name == 'postgresql':
        statement = statement.with_for_update()
    return connection.execute(statement).scalar_one_or_none()


def listing(connection, kind):
    return list(connection.execute(query(kind).order_by(records.c.saved_at.desc(), records.c.id.desc())).scalars())


def has_upload(connection, url):
    statement = select(records.c.id).where(records.c.kind.in_(('asset', 'task')),
        records.c.local_media_url == url, records.c.deleted_at.is_(None)).limit(1)
    scope = scope_predicate()
    if scope is not None:
        statement = statement.where(scope)
    return connection.execute(statement).first() is not None


def upsert(connection, kind, body, *, saved_at=None, enforce_owner=True):
    values = values_for(kind, body, saved_at)
    insert = pg_insert if connection.dialect.name == 'postgresql' else sqlite_insert
    statement = insert(records).values(**values)
    updates = {column: getattr(statement.excluded, column) for column in values if column != 'id'}
    condition = and_(records.c.kind == kind,
        func.coalesce(records.c.owner_id, '') == (values['owner_id'] or '')) if enforce_owner else None
    statement = statement.on_conflict_do_update(index_elements=[records.c.id], set_=updates, where=condition)
    result = connection.execute(statement.execution_options(preserve_rowcount=True))
    if result.rowcount != 1:
        from fastapi import HTTPException
        raise HTTPException(409, '记录归属冲突，请刷新后重试')
    return result
