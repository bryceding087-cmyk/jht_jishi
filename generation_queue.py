"""Persistent, account-scoped image/video scheduling for the single-worker server.

Queue records contain credential fingerprints, never API keys. A started request
without a known result holds its slot until reconciled or stalled for 30 minutes,
never automatically POSTs again. Workers are created only for admitted jobs.
"""
from collections import Counter
import hashlib
import logging
import socket
import ssl
import threading
import time
from http.client import IncompleteRead, RemoteDisconnected
from urllib.error import URLError

from fastapi import HTTPException
from data_access import access_scope, current_principal
from generation_stop import task_request_id
from video_callbacks import WAITING_MESSAGE

KINDS = {'image_task': 'image', 'task': 'video'}
LIMITS = {'image_task': None, 'task': 10}
GLOBAL_LIMITS = {'image_task': None, 'task': 100}
STALL_TIMEOUT = 30 * 60
WAITING = {'waiting', 'credentials'}
OCCUPIED = {'executing', 'provider', 'unknown'}
TERMINAL = {'done', 'failed', 'cancelled'}
log = logging.getLogger(__name__)


def request_result_unknown(error):
    """Distinguish a lost response from a definite pre-connect/rejected request."""
    if getattr(error, 'status_code', 0) >= 500: return True
    message = str(error).lower()
    if '超时' in message or 'timed out' in message: return True
    seen = set()
    current = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        reason = current.reason if isinstance(current, URLError) else current
        if isinstance(reason, (socket.gaierror, ConnectionRefusedError, ssl.SSLCertVerificationError)): return False
        if isinstance(reason, (TimeoutError, ConnectionResetError, BrokenPipeError, IncompleteRead, RemoteDisconnected)): return True
        if isinstance(current, URLError): return True
        if current.__class__.__name__ == 'JSONDecodeError': return True
        current = current.__cause__ or current.__context__
    return False


class GenerationQueue:
    def __init__(self, host):
        self.h = host
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.workers = set()
        self.last_user = {}

    @staticmethod
    def identifier(kind, tid):
        return 'generation-queue:' + kind + ':' + tid

    def job(self, kind, tid):
        return self.h.raw_record('generation_queue', self.identifier(kind, tid))

    def jobs(self):
        return sorted(self.h.listing('generation_queue'), key=lambda j: (j.get('sequence', 0), j['created_at'], j['id']))

    def patch(self, job, **fields):
        return self.h.save('generation_queue', {**job, **fields, 'updated_at': time.time()})

    def pool(self, kind, config):
        # Retain credential fingerprints for existing queue records. Admission
        # is limited by account and system totals, not by a shared API key.
        origin = config['base_url'].rstrip('/')
        key = hashlib.sha256((origin + '\0' + config.get('api_key', '')).encode()).hexdigest()
        return key, None

    def config(self, kind, task):
        if kind == 'image_task':
            config = self.h.internal_registry_model('image', task.get('model_config_id') or None)
        else:
            config = self.h.resolve_model_config(task['params'].get('model_config_id'), task['params'].get('model'))
        config = self.h.model_for_generation(config)
        if not config.get('api_key'):
            raise HTTPException(428, '等待原账号重新连接集市或配置模型凭据')
        return config

    def enqueue(self, kind, task, config):
        with self.lock:
            previous = self.job(kind, task['id'])
            if previous:
                return previous
            task = self.h.raw_record(kind, task['id']) or task
            stopped = bool(task.get('local_stop_requested') or self.h.generation_stop.is_stopped(
                KINDS[kind], task_request_id(task), task.get('creator_id') or task.get('owner_id')))
            pool, pool_limit = self.pool(kind, config)
            sequence = max((j.get('sequence', 0) for j in self.jobs()), default=0) + 1
            with access_scope(None):
                job = self.h.save('generation_queue', {
                    'id': self.identifier(kind, task['id']), 'kind': kind, 'task_id': task['id'],
                    'owner_id': task.get('owner_id'), 'creator_id': task.get('creator_id'),
                    'user_id': str(task.get('creator_id') or task.get('owner_id') or 'system'),
                    'pool': pool, 'pool_limit': pool_limit, 'state': 'cancelled' if stopped else 'waiting',
                    'sequence': sequence,
                    'created_at': task.get('created_at', time.time()), 'updated_at': time.time()})
            if stopped:
                self.h.save(kind, {**task, 'status': 'cancelled', 'queue_state': 'cancelled', 'stop_requested': True,
                    'local_stop_requested': True, 'provider_may_continue': False, 'stopped_at': task.get('stopped_at') or time.time(),
                    'status_message': '已停止，排队任务未提交给模型'})
                self.active_set(kind).discard(task['id'])
            else:
                self.h.save(kind, {**task, 'queue_state': 'waiting', 'status_message': '已加入个人队列，等待生成名额'})
        self.wake.set()
        return job

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stopped.clear()
            self.thread = threading.Thread(target=self.loop, name='generation-dispatcher', daemon=True)
            self.thread.start()

    def stop(self):
        self.stopped.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=3)

    def loop(self):
        with access_scope(None):
            while not self.stopped.is_set():
                self.wake.clear()
                try:
                    self.tick()
                except Exception:
                    log.exception('Generation queue dispatch failed')
                self.wake.wait(1)

    def active_set(self, kind):
        return self.h.ACTIVE_IMAGE_TASKS if kind == 'image_task' else self.h.ACTIVE_VIDEO_TASKS

    def recover(self):
        """Never confuse a durable waiting job with an interrupted provider call."""
        with self.lock:
            # Adopt pre-queue unfinished tasks conservatively on an upgrade.
            for kind in KINDS:
                for task in self.h.listing(kind):
                    if task.get('deleted_at') or task.get('status') not in ('queued', 'running') or self.job(kind, task['id']): continue
                    try:
                        with self.h.worker_scope(task): pool, limit = self.pool(kind, self.config(kind, task))
                    except Exception:
                        pool, limit = 'legacy:' + str(task.get('model_config_id') or (task.get('params') or {}).get('model_config_id')), None
                    self.h.save('generation_queue', {'id': self.identifier(kind, task['id']), 'kind': kind,
                        'task_id': task['id'], 'owner_id': task.get('owner_id'), 'creator_id': task.get('creator_id'),
                        'user_id': str(task.get('creator_id') or task.get('owner_id') or 'system'),
                        'pool': pool, 'pool_limit': limit, 'state': 'provider' if task.get('provider_task_id') else 'unknown',
                        'created_at': task.get('created_at', time.time()), 'updated_at': time.time()})
            for job in self.jobs():
                if job['state'] in TERMINAL:
                    continue
                task = self.h.raw_record(job['kind'], job['task_id'])
                if not task:
                    self.patch(job, state='failed'); continue
                if task.get('status') in ('done', 'succeeded', 'cancelled'):
                    self.patch(job, state='done' if task['status'] != 'cancelled' else 'cancelled'); continue
                if job['kind'] == 'task' and task.get('provider_task_id'):
                    self.patch(job, state='provider')
                elif job['state'] in WAITING:
                    self.active_set(job['kind']).add(task['id'])
                else:
                    self.patch(job, state='unknown')
                    video = job['kind'] == 'task'
                    self.h.save(job['kind'], {**task, 'status': 'running' if video else 'blocked', 'submit_unknown': True,
                        'queue_state': 'unknown', 'status_message': WAITING_MESSAGE if video else '生成结果待核对',
                        'error': None if video else '服务中断时生成结果未知，请核对供应商记录；未自动重新提交。'})
            self.sweep(self.jobs())

    def refresh_credentials(self, kind, task):
        job = self.job(kind, task['id'])
        if not job or job['state'] not in WAITING:
            return task
        refreshed = self.h.refresh_market_credentials(task)
        if refreshed != task:
            task = self.h.save(kind, {**refreshed, **({'query_retry_at': 0} if task.get('provider_task_id') else {})})
        # A browser reconnection only makes never-submitted jobs eligible.
        if job['state'] == 'credentials':
            try:
                with self.h.worker_scope(task): self.config(kind, task)
            except Exception:
                return task
            with self.lock:
                current = self.job(kind, task['id'])
                if current['state'] == 'credentials': self.patch(current, state='waiting')
        self.wake.set()
        return task

    def sweep(self, jobs):
        for job in jobs:
            if job['state'] in TERMINAL:
                continue
            task = self.h.raw_record(job['kind'], job['task_id'])
            if not task:
                self.patch(job, state='failed'); continue
            if task.get('queue_timed_out_at'):
                self.patch(job, state='failed'); continue
            if task.get('deleted_at'):
                self.patch(job, state='cancelled'); continue
            if task.get('status') not in ('done', 'succeeded', 'cancelled'):
                # Repeated polling and bookkeeping writes are not progress.
                # A new stage, provider receipt, or completed image is progress.
                signature = [task.get('status'), task.get('provider_task_id'),
                             len(task.get('assets') or []), (task.get('asset') or {}).get('id')]
                now = time.time()
                since = job.get('progress_at') or job.get('started_at') or task.get('submitted_at') or job['created_at']
                if job.get('progress_signature') is not None and job['progress_signature'] != signature:
                    since = now
                if now - since >= STALL_TIMEOUT:
                    self.expire(job, task, now)
                    continue
                if job.get('progress_signature') != signature:
                    job = self.patch(job, progress_signature=signature, progress_at=since)
            if job['state'] in WAITING and task.get('provider_task_id'):
                self.patch(job, state='provider')
                self.active_set(job['kind']).discard(task['id'])
                continue
            if task.get('status') in ('done', 'succeeded', 'cancelled'):
                self.patch(job, state='cancelled' if task['status'] == 'cancelled' else 'done')
            elif task.get('submit_unknown'):
                self.patch(job, state='unknown')
            elif task.get('status') in ('failed', 'blocked') and not task.get('polling_interrupted'):
                self.patch(job, state='failed')

    def expire(self, job, task, now):
        message = '任务超过 30 分钟没有有效进展，已判定失败并释放生成名额'
        saved = self.h.save(job['kind'], {**task, 'status': 'failed', 'queue_state': 'failed',
            'queue_timed_out_at': now, 'submit_unknown': False, 'polling_interrupted': False,
            'query_paused': False, 'local_stop_requested': True, 'stop_requested': True,
            'stopped_at': now, 'provider_may_continue': job['state'] in OCCUPIED,
            'error': message, 'status_message': message, 'updated_at': now})
        if saved.get('queue_timed_out_at'):
            self.patch(job, state='failed', timed_out_at=now)
        else:
            self.patch(job, state='cancelled' if saved.get('status') == 'cancelled' else 'done')
        self.active_set(job['kind']).discard(task['id'])
        self.workers.discard(job['id'])

    def tick(self):
        with self.lock:
            jobs = self.jobs()
            self.sweep(jobs)
            jobs = self.jobs()
            users = Counter((j['kind'], j['user_id']) for j in jobs if j['state'] in OCCUPIED)
            # Include provider and uncertain submissions, including recovered
            # jobs whose original credentials are no longer available.
            totals = Counter(j['kind'] for j in jobs if j['state'] in OCCUPIED)
            pending = [j for j in jobs if j['state'] == 'waiting']
            for kind in KINDS:
                while True:
                    if GLOBAL_LIMITS[kind] is not None and totals[kind] >= GLOBAL_LIMITS[kind]:
                        break
                    eligible = [j for j in pending if j['kind'] == kind
                        and (LIMITS[kind] is None or users[kind, j['user_id']] < LIMITS[kind])]
                    if not eligible:
                        break
                    accounts = list(dict.fromkeys(j['user_id'] for j in eligible))
                    last = self.last_user.get(kind)
                    user = accounts[(accounts.index(last) + 1) % len(accounts)] if last in accounts else accounts[0]
                    job = next(j for j in eligible if j['user_id'] == user)
                    pending.remove(job)
                    task = self.h.raw_record(kind, job['task_id'])
                    if not task or task.get('deleted_at') or task.get('local_stop_requested'):
                        self.patch(job, state='cancelled'); self.active_set(kind).discard(job['task_id']); continue
                    try:
                        with self.h.worker_scope(task):
                            config = self.config(kind, task)
                        pool, limit = self.pool(kind, config)
                    except Exception as error:
                        self.patch(job, state='credentials')
                        self.h.save(kind, {**task, 'queue_state': 'credentials',
                            'status_message': '等待原账号重新连接集市或检查模型配置'})
                        continue
                    if (pool, limit) != (job['pool'], job['pool_limit']):
                        job = self.patch(job, pool=pool, pool_limit=limit)
                    job = self.patch(job, state='executing', started_at=time.time(), progress_at=time.time())
                    self.h.save(kind, {**task, 'queue_state': 'executing', 'status_message': '正在提交生成请求'})
                    users[kind, user] += 1; totals[kind] += 1
                    self.last_user[kind] = user
                    self.workers.add(job['id'])
                    try:
                        threading.Thread(target=self.execute, args=(job, config), name='generation-' + KINDS[kind], daemon=True).start()
                    except Exception:
                        self.workers.discard(job['id'])
                        self.patch(job, state='waiting')
                        self.h.save(kind, {**task, 'queue_state': 'waiting'})
                        break

    def execute(self, job, config=None):
        credentials_lost = False
        try:
            with access_scope(None):
                task = self.h.raw_record(job['kind'], job['task_id'])
                if not task or task.get('queue_timed_out_at') or task.get('deleted_at'):
                    return
                if task.get('local_stop_requested'):
                    self.h.save(job['kind'], {**task, 'status': 'cancelled', 'queue_state': 'cancelled',
                        'provider_may_continue': False, 'status_message': '已停止，任务未提交给模型'})
                    return
                with self.h.worker_scope(task):
                    try:
                        config = self.h.model_for_generation(config) if config is not None else self.config(job['kind'], task)
                    except Exception:
                        credentials_lost = True
                        self.h.save(job['kind'], {**task, 'status': 'queued', 'queue_state': 'credentials',
                            'status_message': '等待原账号重新连接集市或检查模型配置'})
                        return
                    if job['kind'] == 'image_task':
                        self.h.run_image_generation(task['id'], config)
                    else:
                        self.h.run_provider_task(task['id'], task['params'], config)
        except Exception as error:
            with access_scope(None):
                task = self.h.raw_record(job['kind'], job['task_id'])
                # Before a request is issued, credentials may have expired. Do
                # not convert arbitrary execution exceptions into a safe retry.
                if task and not task.get('queue_timed_out_at') and not task.get('deleted_at'):
                    video = job['kind'] == 'task'
                    self.h.save(job['kind'], {**task, 'status': 'running' if video else 'blocked', 'submit_unknown': True,
                        'status_message': WAITING_MESSAGE if video else '生成结果待核对',
                        'error': None if video else '生成执行中断，请核对原任务；未自动重新提交。'})
            log.exception('Generation queue worker interrupted')
        finally:
            with access_scope(None), self.lock:
                self.workers.discard(job['id'])
                if not credentials_lost: self.active_set(job['kind']).discard(job['task_id'])
                task = self.h.raw_record(job['kind'], job['task_id']) or {}
                state = ('failed' if not task or task.get('queue_timed_out_at') else
                    'cancelled' if task.get('status') == 'cancelled' or task.get('deleted_at') else
                    'credentials' if credentials_lost else 'unknown' if task.get('submit_unknown') else
                    'done' if task.get('status') in ('done', 'succeeded') else
                    'provider' if task.get('provider_task_id') and task.get('status') in ('queued', 'running') else 'failed')
                current_job = self.job(job['kind'], job['task_id'])
                if current_job:
                    self.patch(current_job, state=state)
                self.wake.set()

    def public(self, kind, task):
        job = self.job(kind, task['id'])
        if not job:
            return {}
        jobs = self.jobs()
        waiting = [j for j in jobs if j['kind'] == kind and j['user_id'] == job['user_id'] and j['state'] in WAITING]
        ahead = next((i for i,j in enumerate(waiting) if j['id'] == job['id']), 0)
        labels = {'waiting': f'个人队列排队中，前面还有 {ahead} 个任务' if ahead else '个人队列排队中，等待生成名额', 'credentials': '等待重新连接集市或配置模型',
            'executing': '生成中', 'provider': '供应商任务仍在处理中',
            'unknown': WAITING_MESSAGE if kind == 'task' else '生成结果待核对，未自动重发'}
        result = {'queue_state': job['state'], 'queue_ahead': ahead, 'queue_limit': LIMITS[kind],
            'queue_global_limit': GLOBAL_LIMITS[kind],
            'can_cancel_queue': job['state'] in WAITING}
        if job['state'] in WAITING or job['state'] == 'unknown': result['status_message'] = labels[job['state']]
        elif task.get('provider_task_id') and task.get('status') == 'queued': result['status_message'] = '供应商排队中'
        user = current_principal()
        if user and user.get('role') != 'admin' and str(user['id']) != job['user_id']:
            result['queue_ahead'] = None
            if job['state'] == 'waiting': result['status_message'] = '任务排队中'
        return result

    def cancel(self, kind, tid):
        if kind not in KINDS: raise HTTPException(404, '任务不存在')
        task = self.h.get(kind, tid)
        with self.lock:
            job = self.job(kind, tid)
            if not job: raise HTTPException(404, '排队任务不存在')
            if job['state'] == 'cancelled': return task
            if job['state'] not in WAITING:
                raise HTTPException(409, '该任务已提交给模型，不能作为排队任务取消')
            self.patch(job, state='cancelled')
            task = self.h.save(kind, {**task, 'status': 'cancelled', 'queue_state': 'cancelled',
                'status_message': '已取消排队', 'updated_at': time.time()})
            self.active_set(kind).discard(tid)
            self.wake.set()
            return task

    def resolve(self, kind, tid):
        if kind not in KINDS: raise HTTPException(404, '任务不存在')
        task = self.h.get(kind, tid)
        with self.lock:
            job = self.job(kind, tid)
            if not job or job['state'] != 'unknown': raise HTTPException(409, '此任务不处于结果待核对状态')
            self.patch(job, state='failed')
            task = self.h.save(kind, {**task, 'submit_unknown': False, 'status': 'failed',
                'queue_state': 'failed', 'error': '用户已确认供应商任务结束；已释放名额，未重新提交。'})
            self.wake.set()
            return task


def install_generation_queue(app, host):
    queue = GenerationQueue(host)
    app.state.generation_queue = queue

    @app.get('/api/generation-queue')
    def listing():
        user = current_principal()
        if not user: raise HTTPException(401, '请先登录')
        result = []
        for j in queue.jobs():
            if j['user_id'] != str(user['id']) or j['state'] in TERMINAL: continue
            task = queue.refresh_credentials(j['kind'], host.get(j['kind'], j['task_id']))
            info = queue.public(j['kind'], task)
            name = task.get('name', '')
            title = name if name and name != 'AI 生成图片' else (task.get('prompt') or (task.get('params') or {}).get('prompt') or '视频生成')[:60]
            result.append({'id': task['id'], 'kind': KINDS[j['kind']], 'state': info['queue_state'],
                'title': title,
                'status_message': info.get('status_message'), 'created_at': j['created_at']})
        return {'limits': {'image': LIMITS['image_task'], 'video': LIMITS['task']},
                'global_limits': {'image': GLOBAL_LIMITS['image_task'], 'video': GLOBAL_LIMITS['task']},
                'tasks': result}

    @app.post('/api/generation-queue/{kind}/{tid}/cancel')
    def cancel(kind: str, tid: str):
        task = queue.cancel(kind, tid)
        return {'id': task['id'], 'status': task['status'], **queue.public(kind, task)}

    @app.post('/api/generation-queue/{kind}/{tid}/confirm-stopped')
    def resolve(kind: str, tid: str):
        task = queue.resolve(kind, tid)
        return {'id': task['id'], 'status': task['status'], **queue.public(kind, task)}
    return queue
