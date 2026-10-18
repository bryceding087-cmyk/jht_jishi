"""Account mapping for the deployment's explicitly trusted SDK context contract.

The platform user ID is the identity assertion in this integration. This is not
a signature verifier. The configured SDK-login endpoint is the only caller.
"""
import hashlib
import json
import secrets

from fastapi import HTTPException
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from storage.schema import auth_sessions, auth_users


def login_market_context_user(store, *, issuer, subject, display_name, ttl=3600):
    """Map a trusted platform issuer/subject to a password-disabled member."""
    if any(not isinstance(value, str) or not value.strip() or len(value) > 2048
           for value in (issuer, subject, display_name)):
        raise ValueError('Marketplace context identity is incomplete')
    if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= 3600:
        raise ValueError('Marketplace session TTL must be between 1 and 3600 seconds')
    identity = json.dumps([issuer, subject], ensure_ascii=False, separators=(',', ':'))
    username = 'market_' + hashlib.sha256(identity.encode('utf-8')).hexdigest()
    token = secrets.token_urlsafe(32)
    now = store.clock()
    with store.connect() as connection:
        db = connection.sa
        create = pg_insert if store.database.dialect == 'postgresql' else sqlite_insert
        db.execute(create(auth_users).values(
            username=username, display_name=display_name[:160], role='user',
            password_hash='!market-sso-only', created_at=now,
        ).on_conflict_do_nothing(index_elements=[auth_users.c.username]))
        statement = select(auth_users).where(auth_users.c.username == username)
        if store.database.dialect == 'postgresql':
            statement = statement.with_for_update()
        row = db.execute(statement).mappings().one()
        # A matching namespace must never take over a password/admin account.
        if row['password_hash'] != '!market-sso-only' or row['role'] != 'user':
            raise HTTPException(403, '集市账号映射异常，请联系管理员')
        if not row['active']:
            raise HTTPException(403, '该建绘通账号已被停用')
        db.execute(update(auth_users).where(auth_users.c.id == row['id']).values(display_name=display_name[:160]))
        db.execute(delete(auth_sessions).where(auth_sessions.c.expires_at <= now))
        db.execute(insert(auth_sessions).values(
            token_hash=hashlib.sha256(token.encode('utf-8')).hexdigest(), user_id=row['id'],
            created_at=now, expires_at=now + ttl,
        ))
        # Keep the newly issued session even when concurrent timestamps are equal.
        current_hash = hashlib.sha256(token.encode('utf-8')).hexdigest()
        keep = select(auth_sessions.c.token_hash).where(
            auth_sessions.c.user_id == row['id'], auth_sessions.c.token_hash != current_hash,
        ).order_by(auth_sessions.c.created_at.desc(), auth_sessions.c.token_hash.desc()).limit(19)
        db.execute(delete(auth_sessions).where(
            auth_sessions.c.user_id == row['id'], auth_sessions.c.token_hash != current_hash,
            auth_sessions.c.token_hash.not_in(keep),
        ))
        user = {'id': row['id'], 'username': username, 'display_name': display_name[:160], 'role': 'user'}
    return user, token, ttl
