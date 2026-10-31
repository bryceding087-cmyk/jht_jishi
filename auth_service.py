"""Cookie-based authentication for the shared local workspace."""

from contextlib import contextmanager
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool
from creator_metadata import creator_scope, user_creator
from data_access import access_scope
from market_service import exact_origin, install_market
from video_callbacks import is_callback_request
from storage.database import database_for
from storage.schema import auth_users, auth_sessions, auth_login_limits
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


COOKIE_NAME = 'jianhuitong_session'
SESSION_SECONDS = 12 * 60 * 60
REMEMBER_SECONDS = 7 * 24 * 60 * 60
PASSWORD_ITERATIONS = 600_000
LOGIN_WINDOW = 15 * 60
LOGIN_PAIR_LIMIT = 5
LOGIN_IP_LIMIT = 30
MAX_RATE_BUCKETS = 10_000

# Individually salted PBKDF2 hashes. Existing accounts are never overwritten.
SEED_USERS = (
    ('admin', '管理员', 'admin',
     'pbkdf2_sha256$600000$1abe605aaf9c9fff48647e4e25a7b93a$dd301874ab21b1020fd87e225f58e8865e80b4865a2284e0a8d07a9b585a499b'),
    ('dingyufu', 'dingyufu', 'user',
     'pbkdf2_sha256$600000$6093d319a894dbb0fda68c4c4dac4e25$900c596abf74368319fa7a7f8b248a88d1438e1b621c2735f3ae89ae29417b11'),
)
PUBLIC_PATHS = frozenset({
    '/api/health', '/api/auth/login', '/login', '/login.html',
    '/login.css', '/login.js', '/login-art.svg', '/favicon.svg', '/iconography.js',
    '/brand.css', '/favicon.ico', '/apple-touch-icon.png',
    '/icon-16.png', '/icon-32.png', '/icon-128.png', '/icon-192.png', '/icon-512.png',
    '/market', '/market-session.js', '/market.css', '/vendor/aimarket.js',
    '/api/integrations/aimarket/config',
    '/api/integrations/aimarket/login',
})
SAFE_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PASSWORD_ITERATIONS)
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 100_000 <= int(iterations) <= 2_000_000:
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(digest, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def public_user(row) -> dict:
    return {key: row[key] for key in ('id', 'username', 'display_name', 'role')}


class AuthStore:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.database = database_for(self.path)
        self.initialize()

    @contextmanager
    def connect(self):
        with self.database.transaction() as connection:
            yield connection

    def initialize(self):
        with self.connect() as connection:
            create = pg_insert if self.database.dialect == 'postgresql' else sqlite_insert
            for username, display_name, role, encoded in SEED_USERS:
                connection.sa.execute(create(auth_users).values(username=username, display_name=display_name,
                    role=role, password_hash=encoded, created_at=self.clock()).on_conflict_do_nothing(index_elements=[auth_users.c.username]))
            connection.sa.execute(delete(auth_sessions).where(auth_sessions.c.expires_at <= self.clock()))
            connection.sa.execute(delete(auth_login_limits).where(auth_login_limits.c.expires_at <= self.clock()))

    def reserve_login_attempt(self, username: str, address: str) -> str:
        """Persist bounded attempt counters before doing expensive password work."""
        now = self.clock()
        pair_bucket = token_hash(f'pair\0{address}\0{username}')
        buckets = ((token_hash(f'ip\0{address}'), LOGIN_IP_LIMIT), (pair_bucket, LOGIN_PAIR_LIMIT))
        with self.connect() as connection:
            if self.database.dialect == 'sqlite':
                connection.execute('BEGIN IMMEDIATE')
            db = connection.sa
            create = pg_insert if self.database.dialect == 'postgresql' else sqlite_insert
            db.execute(delete(auth_login_limits).where(auth_login_limits.c.expires_at <= now))
            # Create missing buckets before locking. ON CONFLICT serializes
            # concurrent first attempts as well as attempts on existing rows.
            for bucket, limit in buckets:
                db.execute(create(auth_login_limits).values(bucket=bucket, attempts=0, expires_at=now + LOGIN_WINDOW).on_conflict_do_nothing())
                statement = select(auth_login_limits).where(auth_login_limits.c.bucket == bucket)
                if self.database.dialect == 'postgresql':
                    statement = statement.with_for_update()
                row = db.execute(statement).mappings().one()
                if row and row['attempts'] >= limit:
                    retry_after = max(1, int(row['expires_at'] - now) + 1)
                    raise HTTPException(429, '登录尝试过于频繁，请稍后再试。', headers={'Retry-After': str(retry_after)})
            # Expired entries are collected above; this also caps storage under
            # a large number of distinct clients without storing raw addresses.
            count = db.execute(select(func.count()).select_from(auth_login_limits)).scalar_one()
            if count > MAX_RATE_BUCKETS:
                oldest = select(auth_login_limits.c.bucket).where(auth_login_limits.c.bucket.not_in([key for key, _ in buckets])).order_by(auth_login_limits.c.expires_at).limit(count - MAX_RATE_BUCKETS)
                db.execute(delete(auth_login_limits).where(auth_login_limits.c.bucket.in_(oldest)))
            for bucket, _ in buckets:
                db.execute(update(auth_login_limits).where(auth_login_limits.c.bucket == bucket).values(attempts=auth_login_limits.c.attempts + 1))
        return pair_bucket

    def login(self, username: str, password: str, address: str, remember: bool, old_token: str | None = None):
        pair_bucket = self.reserve_login_attempt(username, address)
        with self.connect() as connection:
            row = connection.sa.execute(select(auth_users).where(auth_users.c.username == username)).mappings().first()
        # Unknown accounts still do the same password derivation work.
        encoded = row['password_hash'] if row else SEED_USERS[0][3]
        valid = verify_password(password, encoded)
        if not valid or not row or not row['active']:
            raise HTTPException(401, '用户名或密码不正确')
        token = secrets.token_urlsafe(32)
        now = self.clock()
        ttl = REMEMBER_SECONDS if remember else SESSION_SECONDS
        with self.connect() as connection:
            db = connection.sa
            db.execute(delete(auth_sessions).where(auth_sessions.c.expires_at <= now))
            if old_token:
                db.execute(delete(auth_sessions).where(auth_sessions.c.token_hash == token_hash(old_token)))
            db.execute(insert(auth_sessions).values(token_hash=token_hash(token), user_id=row['id'], created_at=now, expires_at=now + ttl))
            # Bound forgotten sessions while allowing multiple devices.
            keep = select(auth_sessions.c.token_hash).where(auth_sessions.c.user_id == row['id']).order_by(auth_sessions.c.created_at.desc(), auth_sessions.c.token_hash.desc()).limit(20)
            db.execute(delete(auth_sessions).where(auth_sessions.c.user_id == row['id'], auth_sessions.c.token_hash.not_in(keep)))
            db.execute(delete(auth_login_limits).where(auth_login_limits.c.bucket == pair_bucket))
        return public_user(row), token, ttl

    def current_user(self, token: str | None):
        if not token or len(token) != 43:
            return None
        with self.connect() as connection:
            row = connection.sa.execute(select(auth_users.c.id, auth_users.c.username, auth_users.c.display_name, auth_users.c.role)
                .join(auth_sessions, auth_users.c.id == auth_sessions.c.user_id)
                .where(auth_sessions.c.token_hash == token_hash(token), auth_sessions.c.expires_at > self.clock(), auth_users.c.active == 1)).mappings().first()
        return public_user(row) if row else None

    def logout(self, token: str | None):
        if token:
            with self.connect() as connection:
                connection.sa.execute(delete(auth_sessions).where(auth_sessions.c.token_hash == token_hash(token)))


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)
    remember: bool = False

    @field_validator('username')
    @classmethod
    def normalize_username(cls, username):
        username = username.strip()
        if not username:
            raise ValueError('请输入账号')
        return username


def same_origin_request(request: Request, public_origin: str = '') -> bool:
    if request.headers.get('sec-fetch-site', '').lower() == 'cross-site':
        return False
    origin = request.headers.get('origin')
    referer = request.headers.get('referer')
    source = origin if origin is not None else referer
    if source:
        try:
            parsed = urlsplit(source)
            target = urlsplit(str(request.base_url))
            if parsed.username or parsed.password or parsed.scheme not in ('http', 'https'):
                return False
            if origin is not None and (parsed.path not in ('', '/') or parsed.query or parsed.fragment):
                return False
            source_port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            target_port = target.port or (443 if target.scheme == 'https' else 80)
            source_origin = (parsed.scheme, parsed.hostname, source_port)
            if source_origin == (target.scheme, target.hostname, target_port):
                return True
            # TLS termination or Host rewriting can hide the browser-facing
            # origin. Accept only the explicitly configured application origin,
            # with browser-confirmed same-origin fetch metadata. Never infer a
            # trusted origin from arbitrary Forwarded/X-Forwarded-* headers.
            if public_origin and request.headers.get('sec-fetch-site', '').lower() == 'same-origin':
                public = urlsplit(public_origin)
                return source_origin == (public.scheme, public.hostname,
                    public.port or (443 if public.scheme == 'https' else 80))
            return False
        except ValueError:
            return False
    # This browser-controlled header cannot be supplied by cross-origin JS.
    return request.headers.get('sec-fetch-site', '').lower() == 'same-origin'


def install_auth(app: FastAPI, database: Path, public_directory: Path) -> AuthStore:
    store = AuthStore(database)
    app.state.auth_store = store
    market = install_market(app, store, public_directory)
    # This deployment's published app URL, separate from the marketplace parent.
    configured_origin = os.getenv('JIANHUITONG_PUBLIC_ORIGIN', 'https://aimarket.hncig.cn:4193').strip()
    public_origin = exact_origin(configured_origin) if configured_origin else ''
    market_assets = {'/' + path.relative_to(public_directory).as_posix()
                     for path in public_directory.rglob('*') if path.is_file() and path.suffix != '.html'}
    cookie_samesite = os.getenv('JIANHUITONG_COOKIE_SAMESITE', 'lax').lower()
    if cookie_samesite not in ('lax', 'strict', 'none'):
        raise ValueError('JIANHUITONG_COOKIE_SAMESITE 必须为 lax、strict 或 none')

    @app.middleware('http')
    async def authenticate_request(request: Request, call_next):
        if is_callback_request(request.method, request.url.path):
            # Only this POST route uses a per-task callback capability instead
            # of a browser session/CSRF. The handler validates the capability.
            request.state.user = None
            with access_scope(None):
                response = await call_next(request)
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Content-Type-Options'] = 'nosniff'
            return response
        user = await run_in_threadpool(store.current_user, request.cookies.get(COOKIE_NAME))
        try:
            market_user = await run_in_threadpool(market.user_for_request, request)
            if market_user:
                user = market_user
        except HTTPException as error:
            return JSONResponse({'detail': error.detail}, status_code=error.status_code,
                                headers={'Cache-Control': 'no-store'})
        request.state.user = user
        path = request.url.path
        sdk_shell = bool(market.settings.origin and market.settings.sdk_login and (
            path in market_assets or path == '/' and request.query_params.get('embedded') == '1'
            and request.query_params.get('view') == 'canvas'))
        if market.settings.origin and market.settings.sdk_login and path in ('/', '/login', '/login.html') and request.query_params.get('market') == '1':
            response = RedirectResponse('/market', status_code=303)
        elif path not in PUBLIC_PATHS and not sdk_shell and not user:
            if path.startswith('/api/') or path == '/api':
                response = JSONResponse({'detail': '请先登录后再操作'}, status_code=401)
            else:
                response = RedirectResponse('/login', status_code=303)
        elif request.method not in SAFE_METHODS and not same_origin_request(request, public_origin):
            response = JSONResponse({'detail': '请求来源无效，请刷新页面后重试。'}, status_code=403)
        elif path in ('/login', '/login.html') and user:
            response = RedirectResponse('/market' if request.query_params.get('market') == '1' else '/', status_code=303)
        else:
            try:
                with access_scope(user), creator_scope(user_creator(user)), market.request_scope(request, user):
                    response = await call_next(request)
            except HTTPException as error:
                response = JSONResponse({'detail': error.detail}, status_code=error.status_code)
        # Protected pages and responses must not reappear from a shared cache
        # after logout. Public login assets can still use normal static caching.
        if user or path not in PUBLIC_PATHS or path in ('/login', '/login.html', '/api/auth/login', '/market', '/api/integrations/aimarket/config'):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        if user:
            response.headers['X-Authenticated-User'] = str(user['id'])
        # The conversation embeds the authenticated canvas from this same app.
        embedded_canvas = user and path == '/' and request.query_params.get('embedded') == '1' and request.query_params.get('view') == 'canvas'
        response.headers['X-Frame-Options'] = 'SAMEORIGIN' if embedded_canvas else 'DENY'
        for name, value in market.headers(path, request.query_params, bool(user)).items():
            if value is None:
                if name in response.headers:
                    del response.headers[name]
            else:
                response.headers[name] = value
        response.headers['Referrer-Policy'] = 'same-origin'
        return response

    @app.get('/login', include_in_schema=False)
    def login_page():
        return FileResponse(public_directory / 'login.html')

    @app.post('/api/auth/login')
    def login(payload: LoginRequest, request: Request, response: Response):
        if cookie_samesite == 'none' and request.url.scheme != 'https':
            raise HTTPException(503, '跨站嵌入登录需要 HTTPS，请使用 HTTPS 地址或同站部署')
        address = request.client.host if request.client else 'unknown'
        user, token, ttl = store.login(payload.username, payload.password, address, payload.remember, request.cookies.get(COOKIE_NAME))
        request.state.audit_user = user
        if request.cookies.get(COOKIE_NAME):
            market.revoke_session(token_hash(request.cookies[COOKIE_NAME]))
        response.set_cookie(
            COOKIE_NAME, token, max_age=ttl if payload.remember else None,
            httponly=True, secure=request.url.scheme == 'https', samesite=cookie_samesite, path='/',
        )
        return {'user': user}

    @app.get('/api/auth/me')
    def me(request: Request):
        return {'user': request.state.user}

    @app.post('/api/auth/logout', status_code=204)
    def logout(request: Request):
        sdk_bid = getattr(request.state, 'market_binding_id', '')
        if sdk_bid:
            market.revoke(sdk_bid)
            return Response(status_code=204)
        store.logout(request.cookies.get(COOKIE_NAME))
        if request.cookies.get(COOKIE_NAME):
            market.revoke_session(token_hash(request.cookies[COOKIE_NAME]))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE_NAME, path='/', secure=request.url.scheme == 'https', httponly=True, samesite=cookie_samesite)
        return response

    return store
