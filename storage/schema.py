"""Relational query columns plus JSON documents for flexible canvas content."""
from sqlalchemy import (BigInteger, CheckConstraint, Column, Float, Index, Integer,
                        JSON, MetaData, String, Table, Text, ForeignKey)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
document_type = JSON().with_variant(JSONB(), 'postgresql')

records = Table('records', metadata,
    Column('kind', String(80), nullable=False),
    Column('id', String(200), primary_key=True),
    Column('body', document_type, nullable=False),
    Column('owner_id', String(80)),
    Column('creator_id', String(80)),
    Column('context_id', String(200)),
    Column('task_id', String(200)),
    Column('local_media_url', String(512)),
    Column('status', String(80)),
    Column('created_at', Float),
    Column('updated_at', Float),
    Column('deleted_at', Float),
    Column('revision', BigInteger, nullable=False, default=0),
    Column('saved_at', Float, nullable=False),
)
Index('records_scope_order', records.c.kind, records.c.owner_id, records.c.deleted_at, records.c.saved_at)
Index('records_context_kind', records.c.context_id, records.c.kind)
Index('records_task_kind', records.c.task_id, records.c.kind)
Index('records_status', records.c.kind, records.c.status)
Index('records_creator', records.c.creator_id)
Index('records_local_media', records.c.local_media_url, records.c.owner_id, records.c.deleted_at)

auth_users = Table('auth_users', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('username', String(80), nullable=False, unique=True),
    Column('display_name', String(160), nullable=False),
    Column('role', String(20), nullable=False),
    Column('password_hash', Text, nullable=False),
    Column('active', Integer, nullable=False, server_default='1'),
    Column('created_at', Float, nullable=False),
    CheckConstraint("role IN ('admin', 'user')", name='auth_users_role'),
)
auth_sessions = Table('auth_sessions', metadata,
    Column('token_hash', String(64), primary_key=True),
    Column('user_id', Integer, ForeignKey('auth_users.id', ondelete='CASCADE'), nullable=False),
    Column('created_at', Float, nullable=False),
    Column('expires_at', Float, nullable=False),
)
Index('auth_sessions_user', auth_sessions.c.user_id)
Index('auth_sessions_expiry', auth_sessions.c.expires_at)
auth_login_limits = Table('auth_login_limits', metadata,
    Column('bucket', String(64), primary_key=True),
    Column('attempts', Integer, nullable=False),
    Column('expires_at', Float, nullable=False),
)
Index('auth_login_limits_expiry', auth_login_limits.c.expires_at)
app_migrations = Table('app_migrations', metadata,
    Column('name', String(160), primary_key=True),
    Column('applied_at', Float, nullable=False),
)
