"""Trusted creation identity, scoped to one request or Agent worker."""
from contextlib import contextmanager
from contextvars import ContextVar

CREATOR_FIELDS = ('creator_id', 'creator_name', 'creator_username')
_creator = ContextVar('jianhuitong_creator', default=None)


def user_creator(user):
    if not user:
        return {}
    return {'creator_id': str(user['id']),
            'creator_name': user.get('display_name') or user.get('username', ''),
            'creator_username': user.get('username', '')}


def current_creator():
    return dict(_creator.get() or {})


@contextmanager
def creator_scope(identity):
    token = _creator.set({key: identity[key] for key in CREATOR_FIELDS if key in identity})
    try:
        yield
    finally:
        _creator.reset(token)
