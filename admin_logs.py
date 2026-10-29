"""Administrator audit trail and read-only generation diagnostics."""
import json
import logging
import re
import time
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException, Query
from sqlalchemy import and_, func, or_, select
from starlette.concurrency import run_in_threadpool

from data_access import current_principal
from generation_queue import GLOBAL_LIMITS, LIMITS, OCCUPIED, WAITING
from storage import records as record_store
from storage.schema import auth_users, records

TASK_KINDS = {'task': 'video', 'image_task': 'image', 'audio_task': 'audio',
              'agent_run': 'agent', 'studio_render': 'render'}
EVENT_FIELDS = ('status', 'provider_task_id', 'submit_unknown', 'polling_interrupted',
                'query_paused', 'queue_timed_out_at', 'local_stop_requested', 'error',
                'video_cache_status', 'asset_save_status')
log = logging.getLogger(__name__)


def clean(value, limit=2000):
    if value is None:
        return ''
    if isinstance(value, dict):
        value = value.get('message') or value.get('detail') or value.get('code') or ''
    text = str(value)
    text = re.sub(r'(?i)\bBearer\s+[^\s,"\'}]+|\bsk-[\w-]+', '[已隐藏凭据]', text)
    text = re.sub(r'''(?ix)(["']?(?:api[_-]?key|token|password|secret|authorization|jht_media|binding_id|media_token)["']?\s*[:=]\s*)["']?[^\s,"'&}]+''', r'\1[已隐藏]', text)
    def safe_url(match):
        try:
            url = urlsplit(match.group())
            return url.scheme + '://' + (url.hostname or '') + url.path
        except ValueError:
            return '[链接已隐藏]'
    text = re.sub(r'https?://[^\s<>"\']+', safe_url, text)
    return text[:limit]


def actor_info(user):
    user = user or {}
    profile = user.get('market_user') or {}
    organization = profile.get('organization') or {}
    return {'id': str(user.get('id') or ''), 'username': clean(user.get('username'), 160),
            'name': clean(user.get('display_name') or user.get('username'), 160),
            'role': user.get('role', ''), 'organization': clean(organization.get('name'), 256),
            'work_number': clean(profile.get('workNumber'), 256),
            'source': 'market' if profile else 'local'}


def require_log_admin():
    user = current_principal()
    if not user:
        raise HTTPException(401, '请先登录')
    if user.get('role') != 'admin':
        raise HTTPException(403, '仅管理员可以查看系统日志')


def object_json(raw):
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, UnicodeError):
        return {}


def operation_label(method, path):
    if path == '/api/auth/login': return '账号登录'
    if path == '/api/auth/logout': return '退出登录'
    if path == '/api/integrations/aimarket/login': return '集市登录'
    if path == '/api/generation/stop': return '停止生成'
    if path.endswith('/cancel'): return '取消排队'
    if path.endswith('/confirm-stopped'): return '核对并释放名额'
    if path.endswith('/download'): return '下载素材'
    if path in ('/api/tasks', '/api/images/generations', '/api/audio/generations') and method == 'POST':
        return '提交' + {'/api/tasks': '视频', '/api/images/generations': '图片', '/api/audio/generations': '音频'}[path] + '生成'
    categories = {'projects': '项目', 'conversations': '对话', 'assets': '素材', 'agent': 'Agent',
                  'model-registry': '模型配置', 'models': '视频模型', 'llm': '文本模型',
                  'image-model': '图片模型', 'characters': '角色', 'scripts': '剧本',
                  'studio': '制作任务', 'integrations': '集市连接', 'prompts': '提示词'}
    category = categories.get(path.split('/')[2] if len(path.split('/')) > 2 else '', '资源')
    return {'POST': '提交', 'PUT': '更新', 'PATCH': '修改', 'DELETE': '删除', 'GET': '查看'}.get(method, method) + category


class AuditMiddleware:
    def __init__(self, app, service):
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send):
        path, method = scope.get('path', ''), scope.get('method', '')
        read_action = method == 'GET' and (path.endswith('/download') or bool(re.fullmatch(r'/api/(projects|conversations)/[^/]+', path)))
        if scope['type'] != 'http' or not path.startswith('/api/') or path.startswith('/api/admin/') or not (method in ('POST', 'PUT', 'PATCH', 'DELETE') or read_action):
            return await self.app(scope, receive, send)
        started, trace = time.monotonic(), uuid.uuid4().hex
        request_body, response_body = bytearray(), bytearray()
        content_type = dict(scope.get('headers', [])).get(b'content-type', b'')
        status = 500
        async def wrapped_receive():
            message = await receive()
            if message['type'] == 'http.request' and b'application/json' in content_type and len(request_body) <= 65536:
                request_body.extend(message.get('body', b'')[:65537-len(request_body)])
            return message
        async def wrapped_send(message):
            nonlocal status
            if message['type'] == 'http.response.start':
                status = message['status']
                message = {**message, 'headers': [*message.get('headers', []), (b'x-jht-request-id', trace.encode())]}
            elif message['type'] == 'http.response.body' and len(response_body) <= 65536:
                response_body.extend(message.get('body', b'')[:65537-len(response_body)])
            await send(message)
        try:
            await self.app(scope, wrapped_receive, wrapped_send)
        finally:
            try:
                await run_in_threadpool(self.service.record_operation, scope, status, trace,
                    object_json(request_body), object_json(response_body), round((time.monotonic()-started)*1000))
            except Exception:
                # Logging failures must not replay or invalidate accepted work.
                log.error('Could not persist administrator operation log')


class AdminLogs:
    def __init__(self, host):
        self.h = host

    def record_operation(self, scope, status, trace, body, response, elapsed):
        user = (scope.get('state') or {}).get('audit_user') or (scope.get('state') or {}).get('user')
        actor = actor_info(user)
        path = scope['path']
        route = getattr(scope.get('route'), 'path', '')
        path_params = scope.get('path_params') or {}
        task_id = body.get('task_id') or path_params.get('tid')
        if not task_id and path in ('/api/tasks', '/api/images/generations', '/api/audio/generations'):
            task_id = response.get('id')
        # Store route templates and selected identifiers, never query strings,
        # credentials, uploaded content, or complete request/response bodies.
        event = {'id': 'audit-' + trace, 'created_at': time.time(), 'owner_id': actor['id'],
            'actor': actor, 'action': operation_label(scope['method'], path), 'method': scope['method'],
            'route': route or '/api/unknown', 'http_status': status,
            'status': 'success' if status < 400 else 'failed', 'duration_ms': elapsed,
            'trace_id': trace, 'address': clean((scope.get('client') or ('',))[0], 100),
            'resource_id': clean(response.get('id') or task_id or next(iter(path_params.values()), ''), 200),
            'request_id': clean(body.get('request_id') or response.get('request_id') or (response.get('params') or {}).get('request_id'), 200),
            'project_id': clean(body.get('project_id') or body.get('conversation_id') or response.get('project_id') or path_params.get('pid'), 200),
            'task_id': clean(task_id, 200),
            'error': clean(response.get('detail')) if status >= 400 else ''}
        if path == '/api/auth/login' and not actor['id']:
            event['attempted_username'] = clean(body.get('username'), 80)
        with self.h.database.transaction() as db:
            record_store.upsert(db.sa, 'audit_event', event, enforce_owner=False)

    def record_transition(self, connection, kind, previous, task):
        if kind not in TASK_KINDS and kind != 'generation_queue': return
        fields = ('state',) if kind == 'generation_queue' else EVENT_FIELDS
        changes = {key: {'from': (previous or {}).get(key), 'to': task.get(key)}
                   for key in fields if (previous or {}).get(key) != task.get(key)}
        if previous and not changes: return
        tid = task['task_id'] if kind == 'generation_queue' else task['id']
        event = {'id': 'generation-event-' + uuid.uuid4().hex, 'task_id': tid,
            'task_kind': task.get('kind') if kind == 'generation_queue' else kind,
            'owner_id': task.get('owner_id'), 'creator_id': task.get('creator_id'),
            'created_at': time.time(), 'action': '队列状态变化' if kind == 'generation_queue' else '创建任务' if not previous else '任务状态变化',
            'changes': {key: {'from': clean(value['from'], 1000), 'to': clean(value['to'], 1000)} for key, value in changes.items()}}
        record_store.upsert(connection, 'generation_event', event, enforce_owner=False)

    def users(self):
        with self.h.database.transaction() as db:
            return [dict(row) for row in db.sa.execute(select(auth_users.c.id, auth_users.c.username,
                auth_users.c.display_name, auth_users.c.role, auth_users.c.active).order_by(auth_users.c.display_name)).mappings()]

    def operations(self, user_id, q, outcome, since, until, page, page_size):
        filters = [records.c.kind == 'audit_event']
        if user_id: filters.append(records.c.owner_id == user_id)
        if outcome: filters.append(records.c.status == outcome)
        if since is not None: filters.append(records.c.created_at >= since)
        if until is not None: filters.append(records.c.created_at <= until)
        if q:
            filters.append(or_(*(records.c.body[key].as_string().icontains(q, autoescape=True)
                                for key in ('action', 'request_id', 'trace_id', 'resource_id', 'task_id', 'attempted_username'))))
        with self.h.database.transaction() as db:
            total = db.sa.execute(select(func.count()).select_from(records).where(*filters)).scalar_one()
            items = list(db.sa.execute(select(records.c.body).where(*filters)
                .order_by(records.c.created_at.desc(), records.c.id.desc()).offset((page-1)*page_size).limit(page_size)).scalars())
        return {'items': items, 'total': total, 'page': page, 'page_size': page_size}

    def task_item(self, kind, task, queue, users, detail=False):
        params = task.get('params') or task
        actor_id = str(task.get('creator_id') or task.get('owner_id') or '')
        actor = users.get(actor_id) or {'id': actor_id, 'name': clean(task.get('creator_name')), 'username': clean(task.get('creator_username'))}
        result = {'id': task['id'], 'kind': kind, 'type': TASK_KINDS[kind], 'actor': actor,
            'owner_id': str(task.get('owner_id') or ''), 'project_id': task.get('project_id', ''),
            'request_id': clean(task.get('request_id') or params.get('request_id'), 200),
            'provider_task_id': clean(task.get('provider_task_id'), 200),
            'model': clean(params.get('model_name') or task.get('model') or params.get('model'), 160),
            'prompt': clean(params.get('prompt') or task.get('title'), 16000 if detail else 180),
            'status': task.get('status', ''), 'queue_state': (queue or {}).get('state') or task.get('queue_state', ''),
            'status_message': clean(task.get('status_message')), 'error': clean(task.get('error')),
            'created_at': task.get('created_at'), 'updated_at': task.get('updated_at'),
            'submitted_at': task.get('submitted_at'), 'completed_at': task.get('completed_at'),
            'submit_unknown': bool(task.get('submit_unknown')), 'deleted': bool(task.get('deleted_at'))}
        if detail:
            result['parameters'] = {key: clean(params[key], 500) for key in ('mode', 'resolution', 'duration', 'aspect_ratio', 'size', 'count', 'model_config_id', 'node_id') if key in params}
            result['queue'] = {key: (queue or {}).get(key) for key in ('state', 'sequence', 'user_id', 'created_at', 'started_at', 'progress_at', 'timed_out_at')}
        return result

    def user_map(self):
        return {str(row['id']): actor_info(row) for row in self.users()}

    def tasks(self, user_id, q, task_type, status, queue_state, since, until, page, page_size):
        queue = records.alias('queue')
        joined = records.outerjoin(queue, and_(queue.c.kind == 'generation_queue', queue.c.task_id == records.c.id))
        filters = [records.c.kind.in_(TASK_KINDS)]
        if user_id: filters.append(func.coalesce(records.c.creator_id, records.c.owner_id) == user_id)
        if task_type: filters.append(records.c.kind == next(key for key, value in TASK_KINDS.items() if value == task_type))
        if status: filters.append(records.c.status == status)
        if queue_state == 'active': filters.append(queue.c.body['state'].as_string().in_([*OCCUPIED, *WAITING]))
        elif queue_state: filters.append(queue.c.body['state'].as_string() == queue_state)
        if since is not None: filters.append(records.c.created_at >= since)
        if until is not None: filters.append(records.c.created_at <= until)
        if q:
            filters.append(or_(records.c.id.icontains(q, autoescape=True),
                records.c.body['provider_task_id'].as_string().icontains(q, autoescape=True),
                records.c.body['request_id'].as_string().icontains(q, autoescape=True),
                records.c.body['params']['request_id'].as_string().icontains(q, autoescape=True)))
        with self.h.database.transaction() as db:
            total = db.sa.execute(select(func.count()).select_from(joined).where(*filters)).scalar_one()
            rows = db.sa.execute(select(records.c.kind, records.c.body, queue.c.body).select_from(joined).where(*filters)
                .order_by(records.c.created_at.desc(), records.c.id.desc()).offset((page-1)*page_size).limit(page_size)).all()
        users = self.user_map()
        return {'items': [self.task_item(kind, task, job, users) for kind, task, job in rows],
                'total': total, 'page': page, 'page_size': page_size}

    def detail(self, kind, tid):
        if kind not in TASK_KINDS: raise HTTPException(404, '任务不存在')
        with self.h.database.transaction() as db:
            task = db.sa.execute(select(records.c.body).where(records.c.kind == kind, records.c.id == tid)).scalar_one_or_none()
            if not task: raise HTTPException(404, '任务不存在')
            job = db.sa.execute(select(records.c.body).where(records.c.kind == 'generation_queue', records.c.task_id == tid)).scalar_one_or_none()
            events = list(db.sa.execute(select(records.c.body).where(records.c.kind == 'generation_event', records.c.task_id == tid)
                .order_by(records.c.created_at.desc(), records.c.id.desc()).limit(200)).scalars())
            operations = list(db.sa.execute(select(records.c.body).where(records.c.kind == 'audit_event', records.c.task_id == tid)
                .order_by(records.c.created_at.desc()).limit(20)).scalars())
        return {'task': self.task_item(kind, task, job, self.user_map(), True), 'events': events, 'operations': operations}

    def summary(self):
        with self.h.database.transaction() as db:
            queue = records.c.body['state'].as_string()
            kind = records.c.body['kind'].as_string()
            counts = db.sa.execute(select(kind, queue, func.count()).where(records.c.kind == 'generation_queue')
                .group_by(kind, queue)).all()
            failed = db.sa.execute(select(func.count()).where(records.c.kind.in_(TASK_KINDS),
                records.c.status.in_(('failed', 'blocked')),
                func.coalesce(records.c.updated_at, records.c.created_at) >= time.time()-86400)).scalar_one()
        video = {state: count for task_kind, state, count in counts if task_kind == 'task'}
        return {'video_active': sum(video.get(state, 0) for state in OCCUPIED),
            'video_waiting': sum(video.get(state, 0) for state in WAITING),
            'unknown': sum(count for _, state, count in counts if state == 'unknown'), 'failed_24h': failed,
            'video_limit': GLOBAL_LIMITS['task'], 'video_account_limit': LIMITS['task'], 'updated_at': time.time()}


def install_admin_logs(app, host):
    service = AdminLogs(host)
    app.state.admin_logs = service
    app.add_middleware(AuditMiddleware, service=service)

    @app.get('/api/admin/logs/users')
    def users():
        require_log_admin()
        return {'users': service.users()}

    @app.get('/api/admin/logs/summary')
    def summary():
        require_log_admin()
        return service.summary()

    @app.get('/api/admin/logs/operations')
    def operations(user_id: str = Query('', max_length=80), q: str = Query('', max_length=200),
                   outcome: str = Query('', pattern='^(|success|failed)$'), since: float | None = Query(None, ge=0),
                   until: float | None = Query(None, ge=0), page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100)):
        require_log_admin()
        if since is not None and until is not None and since > until: raise HTTPException(422, '开始时间不能晚于结束时间')
        return service.operations(user_id, q, outcome, since, until, page, page_size)

    @app.get('/api/admin/logs/tasks')
    def tasks(user_id: str = Query('', max_length=80), q: str = Query('', max_length=200),
              task_type: str = Query('', pattern='^(|image|video|audio|agent|render)$'), status: str = Query('', max_length=80),
              queue_state: str = Query('', pattern='^(|active|waiting|credentials|executing|provider|unknown|done|failed|cancelled)$'),
              since: float | None = Query(None, ge=0), until: float | None = Query(None, ge=0),
              page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100)):
        require_log_admin()
        if since is not None and until is not None and since > until: raise HTTPException(422, '开始时间不能晚于结束时间')
        return service.tasks(user_id, q, task_type, status, queue_state, since, until, page, page_size)

    @app.get('/api/admin/logs/tasks/{kind}/{tid}')
    def detail(kind: str, tid: str):
        require_log_admin()
        return service.detail(kind, tid)

    return service
