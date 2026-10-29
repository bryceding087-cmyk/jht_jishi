"""Owner-scoped stop receipts, including requests whose task ID has not arrived."""
from contextlib import contextmanager
import threading
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from data_access import current_principal


MEDIA_KINDS = {'image': 'image_task', 'video': 'task', 'audio': 'audio_task'}
FINISHED = {'done', 'succeeded', 'failed', 'cancelled'}


def task_request_id(task):
    return str(task.get('request_id') or (task.get('params') or {}).get('request_id') or '')


def task_finished(task):
    return task.get('status') in FINISHED and not (task.get('provider_task_id')
        and task.get('polling_interrupted') and task.get('status') not in ('done', 'succeeded', 'cancelled'))


class StopGeneration(BaseModel):
    kind: Literal['image', 'video', 'audio', 'agent', 'text']
    request_id: str = Field(default='', max_length=240)
    task_id: str = Field(default='', max_length=240)
    run_id: str = Field(default='', max_length=240)
    conversation_id: str = Field(default='', max_length=240)
    project_id: str = Field(default='', max_length=240)
    node_id: str = Field(default='', max_length=240)

    @model_validator(mode='after')
    def target_required(self):
        if not (self.request_id.strip() or self.task_id or self.run_id):
            raise ValueError('需要请求编号或任务编号')
        if self.kind == 'text' and not self.request_id.strip():
            raise ValueError('停止文本生成需要请求编号')
        if self.conversation_id and self.project_id and self.conversation_id != self.project_id:
            raise ValueError('对话或画布范围不一致')
        return self


class GenerationStop:
    def __init__(self, host):
        self.h = host
        self._guard = threading.Lock()
        self._locks = {}

    def owner(self, owner_id=None):
        principal = current_principal()
        return str(owner_id if owner_id is not None else principal['id'] if principal else '')

    def identifier(self, kind, request_id, owner_id=None):
        return 'generation-stop:' + str(uuid.uuid5(uuid.NAMESPACE_URL,
            self.owner(owner_id) + ':' + kind + ':' + request_id.strip()))

    def lock(self, kind, request_id, owner_id=None):
        key = self.identifier(kind, request_id, owner_id)
        with self._guard:
            return self._locks.setdefault(key, threading.RLock())

    def check(self, kind, request_id, owner_id=None):
        if self.is_stopped(kind, request_id, owner_id):
            raise HTTPException(409, {'code': 'generation_request_stopped',
                'message': '本次请求已停止，请使用新请求编号重新发送'})

    def is_stopped(self, kind, request_id, owner_id=None):
        return bool(request_id and self.h.raw_record('generation_stop', self.identifier(kind, request_id, owner_id)))

    @contextmanager
    def submission(self, kind, request_id, owner_id=None):
        if not request_id:
            yield
            return
        with self.lock(kind, request_id, owner_id):
            self.check(kind, request_id, owner_id)
            yield

    def remember(self, kind, request_id, owner_id=None):
        if not request_id:
            return
        key = self.identifier(kind, request_id, owner_id)
        previous = self.h.raw_record('generation_stop', key)
        if not previous:
            self.h.save('generation_stop', {'id': key, 'kind': kind,
                'owner_id': self.owner(owner_id), 'request_id': request_id.strip(), 'stopped_at': time.time()})

    def stop_task(self, kind, tid):
        """Only a never-submitted queue job can truthfully be cancelled."""
        task = self.h.get(kind, tid)
        queue = self.h.generation_queue
        with queue.lock:
            task = self.h.get(kind, tid)
            if task.get('local_stop_requested'):
                return task
            if task_finished(task):
                return task
            job = queue.job(kind, tid) if kind in ('image_task', 'task') else None
            waiting = bool(job and job['state'] in ('waiting', 'credentials'))
            terminal = task_finished(task)
            may_continue = not terminal and not waiting
            fields = {'stop_requested': True, 'local_stop_requested': True,
                'stopped_at': time.time(), 'provider_may_continue': may_continue}
            if waiting:
                queue.patch(job, state='cancelled')
                queue.active_set(kind).discard(tid)
                fields.update(status='cancelled', queue_state='cancelled', status_message='已停止，排队任务未提交给模型')
            elif not terminal:
                fields['status_message'] = '已停止本地等待及后续操作，供应商可能继续处理本次请求'
            task = self.h.save(kind, {**task, **fields, 'updated_at': time.time()})
            queue.wake.set()
            return task

    def authorize_scope(self, body):
        scope = body.conversation_id or body.project_id
        if scope:
            context = self.h.creation_context(scope)
            if context.get('deleted_at'):
                raise HTTPException(404, '对话或画布不存在')
        return scope

    def stop(self, body):
        principal = current_principal()
        if not principal:
            raise HTTPException(401, '请先登录')
        owner = str(principal['id'])
        scope = self.authorize_scope(body)
        request_id = body.request_id.strip()
        with self.lock(body.kind, request_id or body.task_id or body.run_id, owner):
            result = {'kind': body.kind, 'request_id': request_id or None, 'task_id': body.task_id or None,
                'run_id': body.run_id or None, 'tasks': [], 'provider_may_continue': False}
            if body.kind == 'agent':
                return self.stop_agent(body, owner, scope, result)
            if body.kind == 'text':
                self.remember('text', request_id, owner)
                result.update(status='stop_requested', provider_may_continue=True,
                    message='已停止本次文本请求及后续回写，已完成的内容会保留；已提交的模型调用可能继续。')
                return result
            kind = MEDIA_KINDS[body.kind]
            tasks = [self.h.get(kind, body.task_id)] if body.task_id else [task for task in self.h.listing(kind)
                if task_request_id(task) == request_id and str(task.get('creator_id') or task.get('owner_id') or '') == owner]
            # Historical image/audio tasks did not store request_id; their IDs
            # were already deterministic and remain a safe exact fallback.
            if not tasks and request_id and body.kind in ('image', 'audio'):
                tid = body.kind + '-' + str(uuid.uuid5(uuid.NAMESPACE_URL, owner + ':' + request_id))
                old = self.h.raw_record(kind, tid)
                if old:
                    tasks = [self.h.get(kind, tid)]
            for task in tasks:
                if scope and task.get('project_id') != scope or body.node_id and (task.get('node_id') or (task.get('params') or {}).get('node_id')) != body.node_id:
                    raise HTTPException(404, '生成任务不存在')
                if request_id and task_request_id(task) and task_request_id(task) != request_id:
                    raise HTTPException(409, '请求编号与任务不一致')
            self.remember(body.kind, request_id, owner)
            for task in tasks:
                existing_id = task_request_id(task)
                self.remember(body.kind, existing_id, task.get('creator_id') or task.get('owner_id') or owner)
                stopped = self.stop_task(kind, task['id'])
                result['tasks'].append({'id': stopped['id'], 'kind': body.kind, 'status': stopped['status'],
                    'request_id': existing_id or request_id or None,
                    'local_stop_requested': bool(stopped.get('local_stop_requested')), 'provider_may_continue': stopped.get('provider_may_continue', False)})
            result['provider_may_continue'] = any(task['provider_may_continue'] for task in result['tasks'])
            result['task_id'] = result['task_id'] or (tasks[0]['id'] if len(tasks) == 1 else None)
            result['status'] = ('pending_submission' if not tasks else 'already_finished'
                if all(task_finished(task) for task in tasks) else 'stop_requested'
                if result['provider_may_continue'] else 'stopped')
            result['message'] = ('已停止本地等待及后续操作，供应商可能继续处理本次请求。'
                if result['provider_may_continue'] else '本次请求已停止，已完成的内容会保留。')
            return result

    def stop_agent(self, body, owner, scope, result):
        from agent_runtime import Message
        service = self.h.app.state.agent_service
        request_id = body.request_id.strip()
        with service.lock:
            run = service.get(body.run_id, owner) if body.run_id else None
            if not run and request_id:
                for rid in (str(uuid.uuid5(uuid.NAMESPACE_URL, f'agent:{owner}:{request_id}')),
                            'agent-' + str(uuid.uuid5(uuid.NAMESPACE_URL, f'{owner}:{request_id}'))):
                    if self.h.raw_record('agent_run', rid):
                        run = service.get(rid, owner)
                        break
            if run and scope and scope not in (run.get('project_id'), (run.get('studio') or {}).get('canvas_project_id')):
                raise HTTPException(404, '对话不存在')
            self.remember('agent', request_id, owner)
            if run:
                current = {(run.get('studio_job') or {}).get('id'), run.get('revision_request'), run.get('active_request_id')}
                current.update(job.get('id') for job in run.get('studio_queue', []))
                if request_id and request_id not in current:
                    result.update(status='pending_submission', message='本次消息已停止。')
                    return result
                if run.get('status') == 'completed' and run['id'] not in service.active:
                    result.update(service.snapshot(run['id'], owner), run_id=run['id'], status='already_finished',
                        message='本次请求已完成，已完成的内容会保留。')
                    return result
                active = run['id'] in service.active or run.get('status') in ('running', 'waiting_tool', 'waiting_user')
                snapshot = service.message(run['id'], Message(request_id='stop:' + str(uuid.uuid4()), action='cancel'), run['owner_id'])
                result.update(snapshot, run_id=run['id'], status='stop_requested' if snapshot['run'].get('stopping') else 'stopped',
                    provider_may_continue=active, message='已停止后续操作并清空待处理消息，已完成的内容会保留；已提交的模型请求可能继续。')
            else:
                result.update(status='pending_submission', message='本次请求已停止，后续到达的提交不会执行。')
            return result


def install_generation_stop(app, host):
    service = GenerationStop(host)
    app.state.generation_stop = service

    @app.post('/api/generation/stop')
    def stop(body: StopGeneration):
        return service.stop(body)

    return service
