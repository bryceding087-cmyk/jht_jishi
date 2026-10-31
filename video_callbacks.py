"""Durable, task-scoped official video callbacks; results are verified by GET.

The callback capability is independent of browser/market sessions. No API keys
or raw callback tokens are persisted. Notifications only nominate a task ID;
the worker queries the configured provider before accepting any result.
"""
import hashlib
import json
import os
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from account_video_models import PROVIDERS
from data_access import access_scope, current_principal
from market_service import account_video_credentials, video_query_credentials
from provider_adapters import video_query_url, video_task_id


CALLBACK_PATH = re.compile(r'/api/video-callbacks/(ark|minimax)/[a-zA-Z0-9_-]{1,160}')
# Confirmed public entry for this deployment; /market is a page, not an API prefix.
DEFAULT_CALLBACK_BASE_URL = 'https://aimarket.hncig.cn:4193'
WAITING_MESSAGE = '提交结果待确认'
SUBMIT_TIMEOUT = 120
MAX_BODY = 64 * 1024
RETENTION = 7 * 24 * 60 * 60


def is_callback_request(method, path):
    return method == 'POST' and CALLBACK_PATH.fullmatch(path) is not None


def official_provider(config):
    provider = config.get('provider_id')
    preset = PROVIDERS.get(provider)
    return provider if preset and tuple(config.get(k) for k in ('base_url', 'api_path', 'query_path')) == preset[1:] else None


def callback_base():
    # Use the confirmed deployment address, never the request Host or internal IP.
    # An explicit empty value disables callbacks; other deployments can override it.
    value = os.getenv('JIANHUITONG_VIDEO_CALLBACK_BASE_URL', DEFAULT_CALLBACK_BASE_URL).strip().rstrip('/')
    if not value:
        return ''
    url = urlsplit(value)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password
            or url.query or url.fragment or any(c.isspace() for c in value)):
        raise ValueError('视频回调地址必须是无凭据、查询参数和片段的 HTTPS 应用地址')
    return value


def credential_fingerprint(config):
    return hashlib.sha256(config.get('api_key', '').encode()).hexdigest()


class VideoCallbacks:
    def __init__(self, host):
        self.h = host
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopped = threading.Event()
        self.thread = None
        self.active = set()

    @staticmethod
    def identifier(tid):
        return 'video-callback:' + tid

    def prepare(self, task, config):
        provider = official_provider(config)
        base = callback_base() if provider else ''
        if not base:
            return None
        tid = task['id']
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}', tid):
            raise ValueError('视频任务编号不能用于回调')
        with self.lock:
            if self.h.raw_record('video_callback', self.identifier(tid)):
                raise RuntimeError('此视频请求已准备提交，请查询原任务')
            token = secrets.token_hex(32)
            self.h.save('video_callback', {
                'id': self.identifier(tid), 'task_id': tid, 'provider': provider,
                'owner_id': task.get('owner_id'), 'creator_id': task.get('creator_id'),
                'token_hash': hashlib.sha256(token.encode()).hexdigest(),
                'config': {k: config.get(k) for k in ('id', 'provider_id', 'base_url', 'api_path', 'query_path', 'model_id')},
                'credential_fingerprint': credential_fingerprint(config),
                'created_at': time.time(), 'expires_at': time.time() + RETENTION,
                'state': 'waiting', 'candidate_id': '', 'next_check_at': 0,
            })
            self.h.update_task(tid, callback_enabled=True)
        return f'{base}/api/video-callbacks/{provider}/{tid}?token={token}'

    def receive(self, provider, tid, token, body):
        with access_scope(None), self.lock:
            row = self.h.raw_record('video_callback', self.identifier(tid))
            if (not row or row['provider'] != provider or row['expires_at'] < time.time()
                    or not re.fullmatch(r'[0-9a-f]{64}', token)
                    or not secrets.compare_digest(row['token_hash'], hashlib.sha256(token.encode()).hexdigest())):
                raise HTTPException(404, '回调凭据无效或已过期')
            if not isinstance(body, dict):
                raise HTTPException(422, '回调格式无效')
            if provider == 'minimax' and 'challenge' in body:
                challenge = body['challenge']
                if not isinstance(challenge, str) or not 1 <= len(challenge) <= 1024:
                    raise HTTPException(422, '回调验证格式无效')
                return {'challenge': challenge}
            native = body.get('task', {}) if provider == 'minimax' else body
            candidate = native.get('id') if isinstance(native, dict) else None
            if not isinstance(candidate, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,200}', candidate):
                raise HTTPException(422, '回调缺少有效任务编号')
            task = self.h.raw_record('task', tid)
            if not task or task.get('deleted_at') or task.get('queue_timed_out_at') or task.get('status') == 'cancelled':
                return {'received': True}
            known = task.get('provider_task_id') or row.get('candidate_id')
            if known and known != candidate:
                raise HTTPException(409, '回调任务编号不一致')
            if task.get('status') == 'done' or row['state'] == 'done':
                return {'received': True}
            # Store only a candidate identifier, never trust callback URLs,
            # statuses or media. A duplicate must not reset a retry backoff.
            if not row.get('candidate_id'):
                self.h.save('video_callback', {**row, 'candidate_id': candidate,
                    'state': 'pending', 'next_check_at': 0, 'received_at': time.time()})
                self.wake.set()
            return {'received': True}

    def process(self, tid):
        with access_scope(None):
            row = self.h.raw_record('video_callback', self.identifier(tid))
            if not row or not row.get('candidate_id') or row['state'] == 'done':
                return
            task = self.h.raw_record('task', tid)
            if (not task or task.get('deleted_at') or task.get('queue_timed_out_at')
                    or task.get('status') in ('done', 'cancelled') or row['expires_at'] < time.time()):
                self.h.save('video_callback', {**row, 'state': 'done'})
                return
            try:
                with self.h.worker_scope(task):
                    user = current_principal()
                    if user and not user.get('active'):
                        raise ValueError('任务所属账号不可用')
                    config = self.h.internal_registry_model('video', row['config']['id'])
                    if (not official_provider(config)
                            or any(config.get(k) != v for k, v in row['config'].items())
                            or not secrets.compare_digest(credential_fingerprint(config), row['credential_fingerprint'])):
                        raise ValueError('原视频模型配置已变更，等待核对')
                    url = video_query_url(config, row['candidate_id'])
                    with account_video_credentials(config), video_query_credentials(url, config['api_key'], str((user or {}).get('id', ''))):
                        result = self.h.provider_json('GET', url, config['api_key'])
                    if video_task_id(result) != row['candidate_id']:
                        raise ValueError('查询返回的任务编号不一致')
                    with self.h.VIDEO_WORKER_LOCK:
                        current = self.h.get('task', tid)
                        if current.get('provider_task_id') and current['provider_task_id'] != row['candidate_id']:
                            raise ValueError('原任务编号不一致')
                        self.h.update_task(tid, status='running', provider_task_id=row['candidate_id'], submit_unknown=False,
                            submitted_at=current.get('submitted_at') or row['created_at'],
                            callback_received_at=time.time(), error=None)
                        status = self.h.apply_video_result(tid, result)
                    if status == 'done':
                        self.h.finalize_video_task(tid)
                self.h.save('video_callback', {**row, 'state': 'done' if status in ('done', 'failed', 'blocked') else 'pending',
                    'next_check_at': time.time() + 20, 'last_error': '', 'checked_at': time.time()})
            except Exception as error:
                # A durable notification survives process restarts or temporary
                # provider outages. Never turn this into another paid POST.
                self.h.save('video_callback', {**row, 'state': 'pending', 'next_check_at': time.time() + 60,
                    'last_error': self.h.provider_error_message(error), 'checked_at': time.time()})
            self.h.generation_queue.wake.set()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        callback_base()  # Fail clearly on an invalid operator configuration.
        self.stopped.clear()
        self.thread = threading.Thread(target=self.loop, name='video-callbacks', daemon=True)
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
                    self.dispatch()
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Video callback dispatch failed')
                self.wake.wait(2)

    def dispatch(self):
        for row in self.h.listing('video_callback'):
            if row['state'] != 'pending' or row.get('next_check_at', 0) > time.time():
                continue
            tid = row['task_id']
            with self.lock:
                if len(self.active) >= 4:
                    break
                if tid in self.active:
                    continue
                self.active.add(tid)
            def run(task_id=tid):
                try:
                    self.process(task_id)
                finally:
                    with self.lock:
                        self.active.discard(task_id)
            threading.Thread(target=run, name='video-callback-check', daemon=True).start()


def install_video_callbacks(app, host):
    service = VideoCallbacks(host)
    app.state.video_callbacks = service

    @app.post('/api/video-callbacks/{provider}/{tid}', include_in_schema=False)
    async def receive(provider: str, tid: str, request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BODY:
                raise HTTPException(413, '回调内容过大')
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(422, '回调 JSON 格式无效') from None
        return await run_in_threadpool(service.receive, provider, tid, request.query_params.get('token', ''), body)

    return service
