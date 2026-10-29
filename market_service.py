"""AI Market integration with ephemeral credentials and optional trusted-SDK login.

SDK login follows the deployment's platform-context trust contract; it does not
claim to verify a signed identity. Local password login remains separate.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from market_identity import login_market_context_user
from model_gateway import with_model_gateway
from account_video_models import is_account_video, require_account_video_access


_credential_scope = ContextVar('jianhuitong_market_credentials', default=None)
_video_query_scope = ContextVar('jianhuitong_video_query', default=None)
_account_video_scope = ContextVar('jianhuitong_account_video', default=None)


@contextmanager
def account_video_credentials(config):
    """Use a provisioned key only for its owner's exact video submission URL."""
    direct = require_account_video_access(config) if is_account_video(config) else None
    token = _account_video_scope.set(direct)
    try:
        yield
    finally:
        _account_video_scope.reset(token)


@contextmanager
def video_query_credentials(url, key, actor=''):
    """In-memory capability for one already-submitted task's GET endpoint only.

    The worker owns this capability until completion or its bounded query window;
    leaving a page revokes browser access, not the submitted provider job.
    """
    token = _video_query_scope.set((url, key, actor))
    try:
        yield
    finally:
        _video_query_scope.reset(token)
_TASK_KINDS = frozenset({'task', 'image_task', 'audio_task', 'agent_run'})
SEED_AUDIO_ORIGIN = 'https://openspeech.bytedance.com'
SEED_AUDIO_PATH = '/api/v3/tts/create'


def is_direct_seed_audio(config):
    return (config.get('model_id') == 'seed-audio-1.0'
            and config.get('provider_id') == 'volcengine-speech'
            and config.get('base_url', '').rstrip('/') == SEED_AUDIO_ORIGIN
            and config.get('api_path') == SEED_AUDIO_PATH)


def exact_origin(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ('https', 'http') or not parts.hostname or
                parts.username or parts.password or parts.path or parts.query or parts.fragment):
            raise ValueError()
        # Match URL.origin in the SDK, including case and default ports.
        port = parts.port
        if any(char.isspace() for char in value) or any(char in value for char in ('\\', ';', '*')):
            raise ValueError()
        host = parts.hostname.encode('idna').decode('ascii').lower()
        if ':' in host:
            host = f'[{host}]'
        suffix = f':{port}' if port and port != (443 if parts.scheme == 'https' else 80) else ''
        return f'{parts.scheme}://{host}{suffix}'
    except (TypeError, ValueError):
        raise ValueError('集市来源必须是准确的 http(s) origin，不含路径、查询参数或末尾斜杠') from None


@dataclass(frozen=True)
class MarketSettings:
    origin: str = ''
    model_origin: str = ''
    agent_id: str = ''
    credential_ttl: int = 3600
    sdk_login: bool = False

    @classmethod
    def from_environment(cls):
        origin = os.getenv('JIANHUITONG_MARKET_ORIGIN', '').strip()
        model_origin = os.getenv('JIANHUITONG_MARKET_MODEL_ORIGIN', '').strip()
        return cls(exact_origin(origin) if origin else '',
                   exact_origin(model_origin or origin) if origin else '',
                   os.getenv('JIANHUITONG_MARKET_AGENT_ID', '').strip() or '020884f4-99f0-4d6f-bbc4-74426e6fed1c',
                   max(60, min(3600, int(os.getenv('JIANHUITONG_MARKET_KEY_TTL', '3600')))),
                   os.getenv('JIANHUITONG_MARKET_SDK_LOGIN', '1').strip() == '1')


class MarketContext(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user: dict
    agent: dict
    apiKey: SecretStr = Field(min_length=4, max_length=4096)


def token_fingerprint(value):
    return hashlib.sha256(value.encode()).hexdigest()


class MarketIntegration:
    def __init__(self, auth_store, settings=None, clock=time.time):
        self.auth = auth_store
        self.settings = settings or MarketSettings.from_environment()
        self.clock = clock
        self._lock = threading.RLock()
        self._bindings = {}

    def config(self):
        result = {'enabled': bool(self.settings.origin), 'market_origin': self.settings.origin,
                'agent_id': self.settings.agent_id, 'uses_session_key': True}
        if self.settings.sdk_login:
            result['sdk_login'] = True
        return result

    def _session_active(self, fingerprint, user_id):
        with self.auth.connect() as db:
            row = db.execute('''SELECT u.id FROM auth_sessions s JOIN auth_users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.active=1 AND u.id=?''',
                (fingerprint, self.clock(), int(user_id))).fetchone()
        return bool(row)

    def validate_context(self, context):
        if not self.settings.origin:
            raise HTTPException(404, '集市接入未启用')
        external = context.user
        agent = context.agent
        key = context.apiKey.get_secret_value()
        organization = external.get('organization')
        if (not isinstance(external.get('id'), str) or not external['id'] or len(external['id']) > 256 or
                not isinstance(external.get('name'), str) or len(external['name']) > 256 or
                external.get('accountType') not in ('group', 'ordinary') or
                external.get('workNumber') is not None and (not isinstance(external['workNumber'], str) or len(external['workNumber']) > 256) or
                organization is not None and (not isinstance(organization, dict) or
                    not isinstance(organization.get('id'), str) or not isinstance(organization.get('name'), str) or
                    len(organization['id']) > 256 or len(organization['name']) > 256 or
                    organization.get('type') not in ('organization', 'department', 'virtual')) or
                not isinstance(agent.get('id'), str) or not agent['id'] or len(agent['id']) > 256 or
                not isinstance(agent.get('name'), str) or len(agent['name']) > 256 or
                not key.startswith('sk-') or any(c.isspace() for c in key)):
            raise HTTPException(422, '集市接入信息格式无效')
        if self.settings.agent_id and agent['id'] != self.settings.agent_id:
            raise HTTPException(403, '当前集市应用与建绘通接入配置不匹配')
        return external, agent, key

    def create_sdk_session(self, request, context):
        if not self.settings.sdk_login:
            raise HTTPException(404, '集市免登录未启用')
        external, agent, key = self.validate_context(context)
        address = request.client.host if request.client else 'unknown'
        bucket = self.auth.reserve_login_attempt('market:' + external['id'], address)
        with self._lock:
            self._expire()
            if len(self._bindings) >= 512:
                raise HTTPException(503, '当前集市连接较多，请稍后重试')
            user, token, ttl = login_market_context_user(self.auth, issuer=self.settings.origin,
                subject=external['id'], display_name=external['name'] or external['id'], ttl=self.settings.credential_ttl)
            profile = {name: external.get(name) for name in ('id', 'name', 'accountType', 'workNumber', 'organization')}
            if profile['organization'] is not None:
                profile['organization'] = {name: profile['organization'][name] for name in ('id', 'name', 'type')}
            bid, media = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            expires = self.clock() + ttl
            self._bindings[bid] = {'user_id': str(user['id']), 'session': token_fingerprint(token),
                'key': key, 'expires_at': expires, 'sdk_login': True, 'profile': profile, 'media_token': media}
        with self.auth.connect() as db:
            db.execute('DELETE FROM auth_login_limits WHERE bucket=?', (bucket,))
        request.state.audit_user = {**user, 'market_user': profile}
        return {'connected': True, 'binding_id': bid, 'media_token': media, 'expires_at': expires}

    def user_for_request(self, request):
        """Resolve this page's opaque session, independent of third-party cookies."""
        bid = request.headers.get('x-jht-market-session', '')
        if not bid and request.method in ('GET', 'HEAD') and (
                request.url.path.startswith('/uploads/') or request.url.path == '/api/scripts/template'):
            media = request.query_params.get('jht_media', '')
            if media:
                with self._lock:
                    self._expire()
                    bid = next((key for key, value in self._bindings.items()
                                if value.get('media_token') == media), '')
                if not bid:
                    raise HTTPException(401, '素材访问会话已失效，请重新打开集市应用')
        if not bid:
            return None
        with self._lock:
            self._expire()
            value = self._bindings.get(bid)
            if not value:
                raise HTTPException(428, '集市连接已失效，请重新连接')
            value = dict(value)
        if not value.get('sdk_login'):
            return None
        self.binding(bid, value['user_id'])
        with self.auth.connect() as db:
            row = db.execute('SELECT id,username,display_name,role FROM auth_users WHERE id=? AND active=1',
                             (int(value['user_id']),)).fetchone()
        if not row:
            raise HTTPException(401, '集市账号不可用')
        request.state.market_binding_id = bid
        return dict(zip(('id', 'username', 'display_name', 'role'), row)) | {'market_user': value['profile']}

    def create_binding(self, request, context):
        user = request.state.user
        if not user:
            raise HTTPException(401, '请先登录建绘通')
        _, _, key = self.validate_context(context)
        fingerprint = token_fingerprint(request.cookies.get('jianhuitong_session', ''))
        if not self._session_active(fingerprint, user['id']):
            raise HTTPException(401, '请重新登录建绘通')
        with self._lock:
            self._expire()
            if len(self._bindings) >= 512:
                raise HTTPException(503, '当前集市连接较多，请稍后重试')
            binding_id = secrets.token_urlsafe(32)
            expires = self.clock() + self.settings.credential_ttl
            self._bindings[binding_id] = {'user_id': str(user['id']), 'session': fingerprint,
                'key': key, 'expires_at': expires}
        return {'connected': True, 'binding_id': binding_id, 'expires_at': expires}

    def _expire(self):
        now = self.clock()
        for bid in [bid for bid, binding in self._bindings.items() if binding['expires_at'] <= now]:
            self._bindings.pop(bid, None)

    def binding(self, binding_id, actor, session=None):
        with self._lock:
            self._expire()
            value = self._bindings.get(binding_id)
            if not value or value['user_id'] != str(actor) or session is not None and value['session'] != session:
                raise HTTPException(428, '集市连接已失效，请从集市重新打开建绘通后重试')
            value = dict(value)
        if not self._session_active(value['session'], value['user_id']):
            self.revoke(binding_id)
            raise HTTPException(428, '集市登录已失效，请重新连接')
        return value

    def revoke(self, binding_id):
        with self._lock:
            value = self._bindings.pop(binding_id, None)
        if value and value.get('sdk_login'):
            with self.auth.connect() as db:
                db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (value['session'],))

    def revoke_session(self, fingerprint):
        with self._lock:
            for bid in [bid for bid, value in self._bindings.items() if value['session'] == fingerprint]:
                self._bindings.pop(bid, None)

    @contextmanager
    def request_scope(self, request, user):
        sdk_bid = getattr(request.state, 'market_binding_id', '')
        bid = sdk_bid or request.headers.get('x-jht-market-session', '')
        scope = None
        if bid:
            if not user:
                raise HTTPException(401, '请先登录建绘通')
            self.binding(bid, user['id'], None if sdk_bid else token_fingerprint(request.cookies.get('jianhuitong_session', '')))
            scope = (self, bid, str(user['id']))
        token = _credential_scope.set(scope)
        try:
            yield
        finally:
            _credential_scope.reset(token)

    @contextmanager
    def worker_scope(self, record):
        bid = record.get('_market_binding_id')
        scope = (self, bid, str(record.get('_market_actor_id', ''))) if bid else None
        token = _credential_scope.set(scope)
        try:
            yield
        finally:
            _credential_scope.reset(token)

    def allows_url(self, value):
        try:
            parsed = urlsplit(value)
            return bool(self.settings.model_origin and not parsed.username and not parsed.password and
                        exact_origin(f'{parsed.scheme}://{parsed.netloc}') == self.settings.model_origin)
        except (TypeError, ValueError):
            return False

    def headers(self, path, query, authenticated):
        # Only the explicit market document and the existing internal canvas may
        # have an external ancestor. Ordinary app/login pages retain DENY.
        entry = path == '/market' or path in ('/', '/login') and query.get('market') == '1'
        canvas = (authenticated or self.settings.sdk_login) and path == '/' and query.get('embedded') == '1' and query.get('view') == 'canvas'
        if self.settings.origin and (entry or canvas):
            return {'Content-Security-Policy': f"frame-ancestors 'self' {self.settings.origin}",
                    'X-Frame-Options': None}
        return {}


def stamp_market_credentials(kind, record):
    scope = _credential_scope.get()
    if scope and kind in _TASK_KINDS:
        _, bid, actor = scope
        return {**record, '_market_binding_id': bid, '_market_actor_id': actor}
    return record


def refresh_market_credentials(record):
    scope = _credential_scope.get()
    if not record.get('_market_binding_id') or not scope:
        return record
    integration, bid, actor = scope
    if str(record.get('_market_actor_id', '')) != actor:
        raise HTTPException(403, '此集市任务需要由原提交账号重新连接后继续')
    integration.binding(bid, actor)
    return {**record, '_market_binding_id': bid}


def model_api_key(url, original):
    query = _video_query_scope.get()
    if query:
        if url != query[0]:
            raise HTTPException(403, '后台查询凭据仅用于已提交的视频任务')
        scope = _credential_scope.get()
        if scope and query[2]:
            with scope[0].auth.connect() as db:
                active = db.execute('SELECT active FROM auth_users WHERE id=?', (int(query[2]),)).fetchone()
            if not active or not active[0]:
                raise HTTPException(403, '任务所属账号不可用')
        return query[1]
    direct = _account_video_scope.get()
    if direct:
        require_account_video_access(direct)
        if url != direct['base_url'] + direct['api_path']:
            raise HTTPException(403, '账号专属凭据只能用于指定的视频接口')
        scope = _credential_scope.get()
        if scope:
            scope[0].binding(scope[1], scope[2])
        return direct['api_key']
    scope = _credential_scope.get()
    if not scope:
        return original
    integration, bid, actor = scope
    if not integration.allows_url(url):
        raise HTTPException(422, '集市模式只能使用管理员配置的集市模型，请选择匹配的模型')
    return integration.binding(bid, actor)['key']


def model_for_generation(config):
    """Return a transient copy; callers must never persist this result."""
    config = with_model_gateway(config)
    if is_account_video(config):
        require_account_video_access(config)
        scope = _credential_scope.get()
        if scope:
            scope[0].binding(scope[1], scope[2])
        return dict(config)
    if is_direct_seed_audio(config):
        return {**config, 'api_key': audio_api_key(SEED_AUDIO_ORIGIN + SEED_AUDIO_PATH, config.get('api_key', ''))}
    return {**config, 'api_key': model_api_key(config.get('base_url', ''), config.get('api_key', ''))}


def audio_api_key(url, original):
    """Only the exact Seed Audio endpoint may use its own server credential."""
    scope = _credential_scope.get()
    if scope and url == SEED_AUDIO_ORIGIN + SEED_AUDIO_PATH:
        integration, bid, actor = scope
        integration.binding(bid, actor)
        return original
    return model_api_key(url, original)


def model_session_state(config):
    if is_account_video(config):
        require_account_video_access(config)
        return None
    scope = _credential_scope.get()
    if not scope:
        return None
    integration, bid, actor = scope
    integration.binding(bid, actor)
    if is_direct_seed_audio(config):
        # Its availability and key hint come from the server's audio config.
        return None
    return integration.allows_url(config.get('base_url', ''))


def open_model_request(request, *, timeout, fallback):
    query = _video_query_scope.get()
    if query:
        if request.get_method() != 'GET' or request.full_url != query[0]:
            raise HTTPException(403, '后台查询凭据不能提交新任务')
        if not _credential_scope.get() and not _account_video_scope.get():
            return fallback(request, timeout=timeout)
        class QueryRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise HTTPException(502, '视频查询接口跳转已停止，请核对接口配置')
        return build_opener(QueryRedirect()).open(request, timeout=timeout)
    direct = _account_video_scope.get()
    if direct:
        require_account_video_access(direct)
        if request.get_method() != 'POST' or request.full_url != direct['base_url'] + direct['api_path']:
            raise HTTPException(403, '账号专属凭据只能用于指定的视频提交接口')
        class AccountVideoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise HTTPException(502, '官网视频接口跳转已停止，请核对接口配置')
        return build_opener(AccountVideoRedirect()).open(request, timeout=timeout)
    scope = _credential_scope.get()
    if not scope:
        return fallback(request, timeout=timeout)
    integration = scope[0]

    class MarketRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            if not integration.allows_url(newurl):
                raise HTTPException(502, '集市接口返回了不同来源的跳转，已停止请求')
            return super().redirect_request(request, fp, code, msg, headers, newurl)

    return build_opener(MarketRedirect()).open(request, timeout=timeout)


def redact_market_result(value, request_key=''):
    # A provider may finish after logout/expiry has already removed the binding.
    # Retain the request's key only on the call stack until its result is scrubbed.
    if isinstance(value, str):
        return safe_model_error(value, request_key)
    if isinstance(value, list):
        return [redact_market_result(item, request_key) for item in value]
    if isinstance(value, dict):
        return {safe_model_error(key, request_key): redact_market_result(item, request_key) for key, item in value.items()}
    return value


def safe_model_error(error, original_key=''):
    message = str(error)
    secrets_to_hide = [original_key] if original_key else []
    scope = _credential_scope.get()
    if scope:
        integration, bid, _ = scope
        with integration._lock:
            value = integration._bindings.get(bid)
            if value:
                secrets_to_hide.append(value['key'])
    for key in secrets_to_hide:
        message = message.replace(key, '[已隐藏密钥]')
    return message


def install_market(app, auth_store, public_directory):
    integration = MarketIntegration(auth_store)
    app.state.market_integration = integration
    public_directory = Path(public_directory)

    @app.get('/api/integrations/aimarket/config')
    def config():
        return integration.config()

    @app.get('/market', include_in_schema=False)
    def market_page(request: Request):
        if not integration.settings.origin:
            raise HTTPException(404, '尚未配置集市接入来源，请使用普通登录入口')
        filename = 'index.html' if integration.settings.sdk_login or request.state.user else 'login.html'
        html = (public_directory / filename).read_text(encoding='utf-8')
        html = html.replace('<head>', '<head>\n  <base href="/">', 1)
        return HTMLResponse(html)

    @app.post('/api/integrations/aimarket/session', status_code=201)
    def connect_session(body: MarketContext, request: Request):
        return integration.create_binding(request, body)

    @app.post('/api/integrations/aimarket/login', status_code=201)
    def sdk_login(body: MarketContext, request: Request):
        return integration.create_sdk_session(request, body)

    @app.delete('/api/integrations/aimarket/session', status_code=204)
    def disconnect_session(request: Request):
        # Middleware has verified that the reference belongs to this session.
        integration.revoke(request.headers.get('x-jht-market-session', ''))

    return integration
