"""Serialize manual generation within each conversation across media types.

Conversation locks cover validation, the queued record and worker registration only.
Provider calls run after commit. The application deliberately runs one worker,
as required by its existing credential bindings and active-task registries.
"""
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
import hashlib
import threading
import uuid

from fastapi import HTTPException
from sqlalchemy import and_, select, text

from data_access import current_principal
from storage.schema import records

_submission = ContextVar('manual_conversation_submission', default=None)
TASK_KINDS = {'image_task': 'image', 'task': 'video', 'audio_task': 'audio'}
ACTIVE = {'image_task': 'ACTIVE_IMAGE_TASKS', 'task': 'ACTIVE_VIDEO_TASKS', 'audio_task': 'ACTIVE_AUDIO_TASKS'}


def lock_key(value):
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], 'big', signed=True)


def transaction_connection(database):
    state = _submission.get()
    return state['connection'] if state and state['service'].h.database is database else None


def after_commit(callback, on_rollback=None):
    state = _submission.get()
    if state:
        state['callbacks'].append(callback)
        if on_rollback is not None:
            state['rollbacks'].append(on_rollback)
    else:
        callback()


class ConversationGeneration:
    def __init__(self, host):
        self.h = host
        self._contexts_lock = threading.Lock()
        self._contexts = {}

    def conversation_lock(self, cid):
        with self._contexts_lock:
            return self._contexts.setdefault(cid, threading.RLock())

    def candidates(self, conversation_id=None, connection=None):
        task, context = records.alias('generation_task'), records.alias('generation_context')
        statement = select(task.c.kind, task.c.body).join(context, and_(context.c.id == task.c.context_id, context.c.kind == 'conversation'))
        statement = statement.where(task.c.kind.in_(TASK_KINDS), task.c.status.in_(('queued', 'running')),
            task.c.deleted_at.is_(None), context.c.body['kind'].as_string().in_(('image', 'video', 'audio')))
        if conversation_id is not None:
            statement = statement.where(task.c.context_id == conversation_id)
        statement = statement.order_by(task.c.created_at, task.c.id)
        if connection is not None:
            return list(connection.execute(statement))
        with self.h.database.engine.connect() as db:
            return list(db.execute(statement))

    def live(self, kind, task):
        return task['id'] in getattr(self.h, ACTIVE[kind])

    def effective_busy(self, kind, task):
        fallback = getattr(self.h, 'IMAGE_TASK_FALLBACKS' if kind == 'image_task' else 'AUDIO_TASK_FALLBACKS', {}).get(task['id']) if kind != 'task' else None
        if fallback and fallback.get('status') not in ('queued', 'running'):
            return False
        return self.live(kind, task) or kind == 'task' and bool(task.get('provider_task_id'))

    @staticmethod
    def public(kind=None, task=None):
        return {'is_busy': task is not None, 'conversation_id': task.get('project_id') if task else None,
                'task': {'id': task['id'], 'kind': TASK_KINDS[kind], 'status': task['status']} if task else None}

    def state(self, conversation_id=None):
        user = current_principal()
        if not user:
            raise HTTPException(401, '请先登录后再操作')
        if not conversation_id:
            return self.public()
        context = self.h.get('conversation', conversation_id)
        if context.get('deleted_at'):
            raise HTTPException(404, '对话不存在')
        with self.conversation_lock(conversation_id):
            for kind, task in self.candidates(conversation_id):
                if self.effective_busy(kind, task):
                    return self.public(kind, task)
        return self.public()

    def recover(self, conversation_id=None):
        """Only resume existing video GET polling; never resubmit paid work."""
        for kind, task in self.candidates(conversation_id):
            if self.live(kind, task):
                continue
            with self.h.worker_scope(task):
                if kind == 'image_task':
                    self.h.image_generation_status(task['id'])
                elif kind == 'audio_task':
                    self.h.audio_generation_status(task['id'])
                else:
                    try:
                        self.h.reconcile_video_task(task['id'])
                    except HTTPException as error:
                        self.h.update_task(task['id'], status='blocked', polling_interrupted=True,
                                           error=str(error.detail))

    def is_manual(self, request, kind):
        if not current_principal():
            return False
        if kind == 'new':
            return True
        cid = getattr(request, 'project_id', '')
        context = self.h.raw_record('conversation', cid) if cid else None
        return bool(context and context.get('kind') in ('image', 'video', 'audio'))

    def repeated(self, request, kind, uid):
        request_id = getattr(request, 'request_id', '').strip()
        if not request_id:
            return False
        if kind in ('image', 'audio'):
            tid = kind + '-' + str(uuid.uuid5(uuid.NAMESPACE_URL, uid + ':' + request_id))
            return self.h.raw_record(kind + '_task', tid) is not None
        return any(task.get('project_id') == getattr(request, 'project_id', '') and
                   (task.get('params') or {}).get('request_id') == request_id for task in self.h.listing('task'))

    @contextmanager
    def submission(self, request, kind):
        if not self.is_manual(request, kind):
            yield
            return
        uid = str(current_principal()['id'])
        cid = ('conversation-' + str(uuid.uuid5(uuid.NAMESPACE_URL, uid + ':' + request.request_id))
               if kind == 'new' else request.project_id)
        with self.conversation_lock(cid), ExitStack() as locks:
            if kind != 'new':
                self.recover(cid)
            # Match the existing canvas submission lock order before entering
            # SQL. This also preserves request-id idempotency when a canvas and
            # a manual conversation accidentally submit the same media ID.
            media_lock = {'image': 'IMAGE_TASK_LOCK', 'audio': 'AUDIO_TASK_LOCK', 'video': 'VIDEO_SUBMIT_LOCK'}.get(kind)
            if media_lock:
                locks.enter_context(getattr(self.h, media_lock))
            callbacks, rollbacks = [], []
            try:
                with self.h.database.transaction() as db:
                    if self.h.database.dialect == 'postgresql':
                        db.sa.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_key('jht-manual-conversation:' + cid)})
                    else:
                        db.execute('BEGIN IMMEDIATE')
                    token = _submission.set({'service': self, 'connection': db, 'user_id': uid,
                                             'callbacks': callbacks, 'rollbacks': rollbacks})
                    try:
                        if kind != 'new' and not self.repeated(request, kind, uid):
                            for task_kind, task in self.candidates(cid, db.sa):
                                if self.effective_busy(task_kind, task):
                                    raise HTTPException(409, {'code': 'conversation_generation_busy',
                                        'message': '请等待当前对话生成完成后再提交', **self.public(task_kind, task)})
                        yield
                    finally:
                        _submission.reset(token)
            except BaseException:
                for callback in reversed(rollbacks):
                    callback()
                raise
            for callback in callbacks:
                callback()
