from pathlib import Path
from contextlib import asynccontextmanager, contextmanager, nullcontext
import base64, errno, hashlib, io, json, logging, os, re, shutil, sqlite3, threading, time, uuid
from typing import Literal
from urllib.error import HTTPError, URLError
import socket
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from PIL import Image
from script_excel import MAX_FILE_BYTES, parse_storyboard_excel
from asset_service import FILE_TYPES, MIMES, file_type, validate_bytes, project_asset_references, is_image_asset_kind, validate_asset_kind
from image_service import download_generated_image, image_error_message, image_capabilities, image_output_size, generated_image_name
from audio_service import (AudioGenerationRequest, audio_reference, image_reference, audio_payload,
                           request_audio, decode_audio_result, audio_error_message)
from video_service import download_generated_video
from provider_adapters import (provider_catalog, provider_id, provider_preset, video_capabilities, video_query_path,
                               video_query_url, video_payload, video_task_id, video_response_error, video_result,
                               video_reference_transport, response_objects, STATES)
from auth_service import install_auth
from market_service import (stamp_market_credentials, refresh_market_credentials, model_for_generation,
                            model_api_key, model_session_state, open_model_request,
                            safe_model_error, redact_market_result, video_query_credentials)
from group_service import normalize_groups
from content_errors import normalize_content_error
from creator_metadata import CREATOR_FIELDS, current_creator, user_creator
from data_access import (SHARED_KINDS, access_scope, current_principal, is_admin, require_admin,
                         can_access, require_access, record_owner, parent_owner, request_key, migrate_ownership)
from starlette.concurrency import run_in_threadpool
from storage import records as record_store
from storage.schema import records as record_table
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from conversation_generation import ConversationGeneration, transaction_connection, after_commit
from visual_styles import VisualStyleSelection, public_styles, style_task_fields
from model_gateway import DOCKER_MODEL_ORIGIN, with_model_gateway, model_gateway_origin

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('JIANHUITONG_DATA_DIR') or ROOT / 'data')
DATA.mkdir(parents=True, exist_ok=True)
UPLOADS = DATA / 'uploads'
UPLOADS.mkdir(exist_ok=True)
@asynccontextmanager
async def application_lifespan(application):
    await run_in_threadpool(manual_generation.recover)
    await run_in_threadpool(recover_video_tasks)
    yield


app = FastAPI(title='建绘通', version='0.4.0', lifespan=application_lifespan)
install_auth(app, DATA / 'jianhuitong.db', ROOT / 'public')
database = app.state.auth_store.database

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, error):
    # Validation failures must not echo request inputs that may contain API keys.
    return JSONResponse(status_code=422, content={'detail': [
        {'loc': item['loc'], 'msg': item['msg'], 'type': item['type']} for item in error.errors()]})

def is_storage_full(error):
    return (getattr(error, 'sqlite_errorcode', None) == sqlite3.SQLITE_FULL
            or getattr(error, 'errno', None) == errno.ENOSPC
            or getattr(error, 'winerror', None) == 112
            or 'database or disk is full' in str(error).lower())

@app.exception_handler(sqlite3.OperationalError)
@app.exception_handler(SQLAlchemyError)
@app.exception_handler(OSError)
async def storage_error_handler(request, error):
    if is_storage_full(error):
        return JSONResponse(status_code=507, content={'detail': '服务端磁盘空间不足，暂时无法保存或上传。请保留当前页面，释放空间后点击重试。'})
    logging.getLogger('uvicorn.error').error('Storage operation failed', exc_info=error)
    return JSONResponse(status_code=500, content={'detail': '服务端存储异常，操作未完成，请稍后重试。'})

DEFAULT_MODEL_ID = 'default-seedance-2.0-mini'
# Keep the opaque configuration ID so existing canvas references remain valid.
DEFAULT_MODEL = {
    'id': DEFAULT_MODEL_ID,
    'name': 'MiniMax H3（默认）',
    'provider': 'AI Market', 'provider_id': 'aimarket',
    'model_id': 'minimax-h3',
    'base_url': DOCKER_MODEL_ORIGIN,
    'api_path': '/v1/video_generation',
    'query_path': '/v1/query/video_generation/{task_id}',
    'is_default': True,
    'capabilities': {'resolutions': ['768p', '2k'], 'durations': list(range(4, 16)), 'aspect_ratios': ['16:9', '9:16', '1:1']},
}

DEFAULT_LLM_ID = 'default-llm'
DEFAULT_LLM = {
    'id': DEFAULT_LLM_ID,
    'name': 'DeepSeek V4 Flash Vision（默认）',
    'provider': 'AI Market', 'provider_id': 'aimarket',
    'model_id': 'deepseek-v4-flash-vision-exp-0817',
    'base_url': DOCKER_MODEL_ORIGIN,
    'api_path': '/v1/chat/completions',
    # Text generation is a long-running operation for script synthesis.  The
    # value is persisted with custom model configurations and can be adjusted
    # from Model Management rather than relying on urllib's short default.
    'timeout_seconds': 300,
    'is_default': True,
}

DEFAULT_IMAGE_MODEL_ID = 'default-seedream'
DEFAULT_IMAGE_MODEL = {
    'id': DEFAULT_IMAGE_MODEL_ID,
    'name': 'Seedream 5.0 Pro',
    'provider': 'AI Market', 'provider_id': 'aimarket',
    'model': 'doubao-seedream-5.0-pro',
    'base_url': DOCKER_MODEL_ORIGIN,
    'api_path': '/v1/images/generations',
    'size': '2K',
    'watermark': False,
}
IMAGE_TASK_LOCK = threading.RLock()
IMAGE_WORKER_SLOTS = threading.Semaphore(2)
ACTIVE_IMAGE_TASKS = set()
IMAGE_TASK_FALLBACKS = {}

DEFAULT_AUDIO_MODEL = {
    'id': 'default-seed-audio', 'name': 'Seed Audio 1.0（默认）',
    'provider': '火山引擎语音', 'provider_id': 'volcengine-speech', 'model_id': 'seed-audio-1.0',
    'base_url': 'https://openspeech.bytedance.com', 'api_path': '/api/v3/tts/create',
    'timeout_seconds': 300,
}
AUDIO_TASK_LOCK = threading.RLock()
AUDIO_WORKER_SLOTS = threading.Semaphore(2)
ACTIVE_AUDIO_TASKS = set()
AUDIO_TASK_FALLBACKS = {}


class ImageModelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=160)
    base_url: str | None = Field(default=None, min_length=1, max_length=300)
    api_key: str = Field(default='', max_length=500)
    size: Literal['2K'] | None = None
    watermark: bool | None = None
    api_path: str | None = Field(default=None, max_length=300)


class ImageGenerationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(min_length=1, max_length=4000)
    name: str = Field(default='AI 生成图片', min_length=1, max_length=160)
    project_id: str = Field(default='', max_length=160)
    folder_id: str = Field(default='', max_length=160)
    size: Literal['1K', '1.5K', '2K', '3K', '4K'] = '2K'
    aspect_ratio: Literal['adaptive', '1:1', '3:4', '16:9', '4:3', '9:16', '2:3', '3:2', '21:9'] = 'adaptive'
    count: int = Field(default=1, ge=1, le=4)
    watermark: bool = False
    request_id: str = Field(default='', max_length=160)
    model_config_id: str = Field(default='', max_length=160)
    # Keep generated props separate in the library while using the same image
    # generation provider and image file validation path.
    asset_kind: Literal['image', 'prop'] = 'image'
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=10)
    visual_style: VisualStyleSelection | None = None

    @field_validator('watermark', mode='before')
    @classmethod
    def disable_watermark(cls, value):
        # Enforce the application policy even for cached clients and old drafts.
        return False

    @field_validator('prompt', 'name')
    @classmethod
    def non_blank_text(cls, value):
        if not value.strip(): raise ValueError('提示词和素材名称不能为空')
        return value.strip()

    @field_validator('reference_asset_ids')
    @classmethod
    def valid_reference_ids(cls, value):
        cleaned = []
        for item in value:
            item = str(item).strip()
            if not item or len(item) > 80 or not re.fullmatch(r'[A-Za-z0-9_-]+', item):
                raise ValueError('参考图编号无效')
            if item not in cleaned:
                cleaned.append(item)
        return cleaned

# 可见配置与后端校验共用同一份定义；接入具体供应商时可在这里映射 provider_model。
VIDEO_OPTIONS = {
    'modes': [
        {'value': 'text-to-video', 'label': '文生视频', 'min_assets': 0},
        {'value': 'image-to-video', 'label': '图生视频', 'min_assets': 1},
        {'value': 'first-last', 'label': '首尾帧视频', 'min_assets': 2},
        {'value': 'multi-frame', 'label': '智能多帧', 'min_assets': 2},
    ],
    'models': [
        {'value': 'minimax-h3', 'label': 'MiniMax H3', 'provider_model': 'minimax-h3'},
    ],
    'aspect_ratios': [
        {'value': '16:9', 'label': '16:9 横屏'},
        {'value': '9:16', 'label': '9:16 竖屏'},
        {'value': '1:1', 'label': '1:1 方形'},
    ],
    'resolutions': [
        {'value': '480p', 'label': '480P'},
        {'value': '720p', 'label': '720P'},
        {'value': '768p', 'label': '768P'},
        {'value': '1080p', 'label': '1080P'},
        {'value': '2k', 'label': '2K'},
        {'value': '4k', 'label': '4K'},
    ],
    # MiniMax H3 accepts integer durations from 4 through 15 seconds.
    'durations': list(range(4, 16)),
}

DEFAULT_CHARACTERS = []

@contextmanager
def connect():
    active = transaction_connection(database)
    if active is not None:
        yield active
        return
    with database.transaction() as db:
        yield db

def save(kind, obj):
    with connect() as db:
        if kind in SHARED_KINDS:
            require_admin()
        else:
            statement = select(record_table.c.body).where(record_table.c.kind == kind, record_table.c.id == obj['id'])
            if database.dialect == 'postgresql':
                statement = statement.with_for_update()
            previous_body = db.sa.execute(statement).scalar_one_or_none()
            obj = dict(obj)
            if previous_body is not None:
                # Editing, retrying or saving someone else's work never changes its author.
                previous = require_access(kind, previous_body)
                obj['owner_id'] = record_owner(previous)
                for key in CREATOR_FIELDS:
                    obj.pop(key, None)
                    if key in previous:
                        obj[key] = previous[key]
            else:
                if kind == 'asset':
                    obj.setdefault('in_library', False)
                identity = current_creator()
                if not identity and kind in ('agent_run', 'conversation'):
                    identity = record_creator(obj, legacy_owner=True)
                obj = {**identity, **obj}
                owner = parent_owner(kind, obj, raw_record)
                user = current_principal()
                obj['owner_id'] = owner if owner is not None else str(user['id']) if user else record_owner(obj)
                obj = stamp_market_credentials(kind, obj)
        record_store.upsert(db.sa, kind, obj, enforce_owner=current_principal() is not None)
    return obj


def record_creator(record, legacy_owner=False):
    identity = {key: record[key] for key in CREATOR_FIELDS if record.get(key)}
    user_id = identity.get('creator_id') or (record.get('owner_id') if legacy_owner else None)
    if user_id and not identity.get('creator_name'):
        with connect() as db:
            row = db.execute('SELECT id, username, display_name FROM auth_users WHERE id=?', (user_id,)).fetchone()
        if row:
            identity = {**user_creator(dict(zip(('id', 'username', 'display_name'), row))), **identity}
    return identity

def listing(kind):
    with connect() as db:
        return record_store.listing(db.sa, kind)

def get(kind, rid):
    with connect() as db:
        value = record_store.fetch(db.sa, kind, rid)
    if value is None: raise HTTPException(404, '记录不存在')
    return value

class Project(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    nodes: list[dict] = Field(default_factory=list, max_length=200)
    edges: list[dict] = Field(default_factory=list, max_length=400)
    groups: list[dict] = Field(default_factory=list, max_length=100)
    selected_asset_ids: list[str] = Field(default_factory=list, max_length=100)
    viewport: dict = Field(default_factory=dict)
    revision: int | None = None
    folder_id: str = Field(default='', max_length=80)

    @field_validator('name')
    @classmethod
    def valid_name(cls, value):
        if not value.strip(): raise ValueError('项目名称不能为空')
        return value.strip()

class Task(BaseModel):
    project_id: str
    node_id: str
    prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str = Field(default='', max_length=1000)
    mode: Literal['text-to-video', 'image-to-video', 'first-last', 'multi-frame'] = 'text-to-video'
    model: str = 'minimax-h3'
    model_config_id: str = ''
    aspect_ratio: Literal['16:9', '9:16', '1:1', '4:3', '3:4', '21:9'] = '16:9'
    duration: int = Field(default=4, ge=1, le=60)
    resolution: str = '768p'
    asset_ids: list[str] = Field(default_factory=list)
    request_id: str = Field(default='', max_length=160)
    visual_style: VisualStyleSelection | None = None

class ModelConfig(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(default='兼容 OpenAI 风格接口', max_length=80)
    provider_id: str = Field(default='', max_length=80)
    model_id: str = Field(min_length=1, max_length=160)
    base_url: str = Field(default=DEFAULT_MODEL['base_url'], max_length=300)
    api_path: str = Field(default=DEFAULT_MODEL['api_path'], max_length=300)
    query_path: str = Field(default='', max_length=300)
    api_key: str = Field(default='', max_length=500)
    capabilities: dict = Field(default_factory=dict)

class ModelConfigUpdate(BaseModel):
    api_key: str = Field(default='', max_length=500)
    name: str | None = Field(default=None, max_length=80)
    provider: str | None = Field(default=None, max_length=80)
    provider_id: str | None = Field(default=None, max_length=80)
    model_id: str | None = Field(default=None, max_length=160)
    base_url: str | None = Field(default=None, max_length=300)
    api_path: str | None = Field(default=None, max_length=300)
    query_path: str | None = Field(default=None, max_length=300)
    capabilities: dict | None = None

class LLMConfig(BaseModel):
    name: str = Field(default='脚本大模型（默认）', max_length=80)
    provider: str = Field(default='OpenAI 兼容接口', max_length=80)
    model_id: str = Field(default=DEFAULT_LLM['model_id'], min_length=1, max_length=160)
    base_url: str = Field(default=DEFAULT_LLM['base_url'], max_length=300)
    api_path: str = Field(default=DEFAULT_LLM['api_path'], max_length=300)
    api_key: str = Field(default='', max_length=500)
    timeout_seconds: int = Field(default=300, ge=5, le=900)

class LLMConfigUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    provider: str | None = Field(default=None, max_length=80)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)
    base_url: str | None = Field(default=None, max_length=300)
    api_path: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    timeout_seconds: int | None = Field(default=None, ge=5, le=900)

class RegistryModelCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['text', 'image', 'video', 'audio']
    name: str = Field(min_length=1, max_length=80)
    provider: str = Field(default='兼容接口', min_length=1, max_length=80)
    provider_id: str = Field(default='', max_length=80)
    model_id: str = Field(min_length=1, max_length=160)
    base_url: str = Field(min_length=1, max_length=300)
    api_path: str = Field(min_length=1, max_length=300)
    query_path: str = Field(default='', max_length=300)
    api_key: str = Field(default='', max_length=500)
    timeout_seconds: int = Field(default=300, ge=5, le=900)

class RegistryModelUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str | None = Field(default=None, min_length=1, max_length=80)
    provider: str | None = Field(default=None, min_length=1, max_length=80)
    provider_id: str | None = Field(default=None, max_length=80)
    model_id: str | None = Field(default=None, min_length=1, max_length=160)
    base_url: str | None = Field(default=None, min_length=1, max_length=300)
    api_path: str | None = Field(default=None, min_length=1, max_length=300)
    query_path: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    timeout_seconds: int | None = Field(default=None, ge=5, le=900)

class PromptPolishRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)

class ScriptGenerateRequest(BaseModel):
    project_id: str
    node_id: str
    action: Literal['script', 'resources', 'prompts'] = 'script'
    source_text: str = Field(default='', max_length=12000)
    script_data: dict = Field(default_factory=dict)
    revision: int | None = None
    scene_index: int | None = Field(default=None, ge=1, le=80)

class ScriptUpdateRequest(BaseModel):
    script_data: dict = Field(default_factory=dict)
    revision: int | None = None

class Character(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(default='角色设定', max_length=80)
    tone: Literal['blue', 'green', 'orange', 'purple', 'pink'] = 'blue'
    description: str = Field(default='', max_length=2000)
    asset_id: str = ''

class CharacterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    role: str | None = Field(default=None, max_length=80)
    tone: Literal['blue', 'green', 'orange', 'purple', 'pink'] | None = None
    description: str | None = Field(default=None, max_length=2000)
    asset_id: str | None = None

class AssetContent(BaseModel):
    kind: Literal['text', 'script']
    name: str = Field(min_length=1, max_length=160)
    content: str = Field(default='', max_length=1024 * 1024)
    script_data: dict = Field(default_factory=dict)
    project_id: str = ''
    folder_id: str = ''
    tags: list[str] = Field(default_factory=list, max_length=20)
    description: str = Field(default='', max_length=2000)

class AssetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=20)
    folder_id: str | None = None
    favorite: bool | None = None

class AssetFolder(BaseModel):
    name: str = Field(min_length=1, max_length=80)

class ProjectMetadata(BaseModel):
    pinned: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)
    folder_id: str | None = Field(default=None, max_length=80)
    revision: int | None = None

class AssetFromTask(BaseModel):
    task_id: str
    name: str | None = Field(default=None, min_length=1, max_length=160)
    folder_id: str = ''

def raw_record(kind, rid):
    with connect() as db:
        value = record_store.fetch(db.sa, kind, rid)
        # Preserve callers' distinction between a missing record and a forbidden
        # parent; otherwise a foreign parent could be treated as a new context.
        if value is None:
            exists = db.sa.execute(select(record_table.c.id).where(record_table.c.kind == kind, record_table.c.id == rid)).scalar_one_or_none()
            if exists is not None:
                raise HTTPException(404, '记录不存在')
        return value


@contextmanager
def worker_scope(record):
    # Data stays with its context owner, but execution uses the trusted task
    # creator's current permissions (an admin may work in a member's context).
    # Legacy tasks without a recorded creator retain their owner scope.
    actor = str(record.get('creator_id') or record_owner(record))
    user = None
    if actor:
        with connect() as db:
            row = db.execute('SELECT id,role FROM auth_users WHERE id=?', (actor,)).fetchone()
        user = {'id': actor, 'role': row[1] if row else 'user'}
    with access_scope(user), app.state.market_integration.worker_scope(record):
        yield


def validate_private_references(value, owner=None):
    if current_principal() is None:
        return
    singles = {'assetId': 'asset', 'asset_id': 'asset', 'video_asset_id': 'asset',
               'audio_asset_id': 'asset', 'image_asset_id': 'asset', 'last_result_asset_id': 'asset',
               'characterId': 'character', 'agent_run_id': 'agent_run'}
    arrays = {'asset_ids', 'reference_asset_ids', 'selected_asset_ids'}
    if isinstance(value, dict):
        for key, item in value.items():
            ids = item if key in arrays and isinstance(item, list) else [item] if key in singles else []
            for rid in ids:
                if isinstance(rid, str) and rid:
                    record = get(singles.get(key, 'asset'), rid)
                    if owner and not owner_is_admin(owner) and record_owner(record) != owner:
                        raise HTTPException(422, '只能关联同一用户的数据')
            validate_private_references(item, owner)
    elif isinstance(value, list):
        for item in value:
            validate_private_references(item, owner)


def owner_is_admin(owner):
    with connect() as db:
        row = db.execute('SELECT role FROM auth_users WHERE id=?', (owner,)).fetchone()
    return bool(row and row[0] == 'admin')


def authorize_upload(path):
    if not path or Path(path).name != path or '\\' in path or path in ('.', '..'):
        raise HTTPException(404, '文件不存在')
    if is_admin():
        return
    url = '/uploads/' + path
    with connect() as db:
        if record_store.has_upload(db.sa, url):
            return
    raise HTTPException(404, '文件不存在')


class PrivateUploads(StaticFiles):
    async def get_response(self, path, scope):
        await run_in_threadpool(authorize_upload, path)
        return await super().get_response(path, scope)

MODEL_STORES = {'text': 'llm_config', 'image': 'image_model_config', 'video': 'model_config', 'audio': 'audio_model_config'}
MODEL_BUILTINS = {'text': DEFAULT_LLM, 'image': DEFAULT_IMAGE_MODEL, 'video': DEFAULT_MODEL, 'audio': DEFAULT_AUDIO_MODEL}
MODEL_PATHS = {'text': '/chat/completions', 'image': '/images/generations', 'video': '/api/v3/contents/generations/tasks', 'audio': '/api/v3/tts/create'}

def require_model_kind(kind):
    if kind not in MODEL_STORES: raise HTTPException(422, '模型类型必须是 text、image、video 或 audio')
    return kind

def active_model_id(kind):
    require_model_kind(kind)
    builtin_id = MODEL_BUILTINS[kind]['id']
    pointer = raw_record('model_default', 'model-default:' + kind) or {}
    mid = pointer.get('model_id', builtin_id)
    return mid if mid == builtin_id or raw_record(MODEL_STORES[kind], mid) else builtin_id

def internal_registry_model(kind, mid=None):
    require_model_kind(kind)
    builtin = MODEL_BUILTINS[kind]
    mid = mid or active_model_id(kind)
    override = raw_record(MODEL_STORES[kind], mid)
    if mid == builtin['id']:
        config = {**builtin, **(override or {}), 'id': mid}
        # Never send a legacy provider's environment key to the new gateway.
        fallback = (os.getenv('JIANHUITONG_MARKET_API_KEY', '')
                    if kind != 'audio' and provider_id(kind, config) == 'aimarket' else
                    {'text': os.getenv('LLM_API_KEY') or os.getenv('OPENAI_API_KEY', ''),
                     'image': os.getenv('SEEDREAM_API_KEY', ''), 'video': os.getenv('SEEDANCE_API_KEY', ''),
                     'audio': os.getenv('SEEDAUDIO_API_KEY', '')}[kind])
        config['api_key'] = config.get('api_key') or (fallback if 'api_key' not in (override or {}) else '')
    elif override:
        config = dict(override)
    else:
        raise HTTPException(404, '模型配置不存在')
    config.setdefault('api_path', MODEL_PATHS[kind])
    config.setdefault('provider', '火山方舟 Ark' if kind == 'image' else '兼容接口')
    config['provider_id'] = provider_id(kind, config)
    config = with_model_gateway(config)
    if kind == 'video':
        config['query_path'] = video_query_path(config)
        config['capabilities'] = video_capabilities(config)
    # Existing model records predate the timeout setting.  Keep them
    # compatible while giving script synthesis enough time to complete.
    config.setdefault('timeout_seconds', 300)
    if kind == 'image':
        config.setdefault('model', config.get('model_id', ''))
        config.setdefault('size', '2K')
        config['watermark'] = False
    config['is_default'] = mid == active_model_id(kind)
    return config

def safe_public_model_url(value):
    """Never echo credentials from old configurations created before URL validation."""
    try:
        parsed = urlparse(str(value or ''))
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            hostname = parsed.hostname or ''
            if ':' in hostname: hostname = '[' + hostname + ']'
            netloc = hostname + (':' + str(parsed.port) if parsed.port else '')
            return parsed._replace(netloc=netloc, query='', fragment='').geturl()
        return str(value or '')
    except ValueError:
        return ''

def registry_public(kind, config):
    config = with_model_gateway({**config, 'provider_id': provider_id(kind, config)})
    key = config.get('api_key', '')
    market_available = model_session_state(config)
    return {'id': config['id'], 'kind': kind, 'name': config.get('name', ''),
            'provider': config.get('provider', '兼容接口'),
            'provider_id': provider_id(kind, config),
            'model_id': config.get('model', '') if kind == 'image' else config.get('model_id', ''),
            'base_url': safe_public_model_url(config.get('base_url')),
            'api_path': safe_public_model_url(config.get('api_path', MODEL_PATHS[kind])),
            **({'query_path': safe_public_model_url(video_query_path(config))} if kind == 'video' else {}),
            'api_key_set': bool(key) if market_available is None else market_available,
            'api_key_hint': ('••••' + key[-4:] if len(key) > 8 else '••••') if key and is_admin() and market_available is None else '',
            'credential_source': 'market-session' if market_available is not None else 'administrator',
            'available_for_session': market_available is not False,
            'timeout_seconds': int(provider_timeout(config)),
            **({'capabilities': image_capabilities(config)} if kind == 'image' else {'capabilities': config.get('capabilities', {})} if kind == 'video' else {}),
            'is_default': config['id'] == active_model_id(kind),
            'is_builtin': config['id'] == MODEL_BUILTINS[kind]['id']}

def validate_model_updates(kind, updates, current=None):
    updates = dict(updates)
    for field in ('name', 'provider', 'provider_id', 'model_id', 'model', 'base_url', 'api_path', 'query_path', 'api_key'):
        if field in updates and isinstance(updates[field], str): updates[field] = updates[field].strip()
    if not updates.get('api_key'): updates.pop('api_key', None)
    try:
        preset = provider_preset(kind, {**(current or {}), **updates})
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    changed_provider = current is not None and preset['id'] != provider_id(kind, current)
    changed_model = current is not None and 'model_id' in updates and updates['model_id'] != current.get('model_id')
    if changed_provider:
        for field in ('base_url', 'api_path', 'query_path'):
            if field in preset['defaults']: updates.setdefault(field, preset['defaults'][field])
        # A new service must not inherit another service's stored credentials.
        updates.setdefault('api_key', '')
    if kind == 'video' and (changed_provider or changed_model):
        updates.setdefault('capabilities', {})
    updates['provider_id'] = preset['id']
    if changed_provider or (current is None and updates.get('provider') == '兼容接口'):
        updates['provider'] = preset['name']
    if kind == 'video' and 'query_path' in updates and not updates['query_path']:
        updates['query_path'] = video_query_path({**(current or {}), **updates})
    elif kind != 'video' and updates.get('query_path'):
        raise HTTPException(422, '只有视频模型需要配置任务查询路径')
    for field in ('name', 'provider', 'model_id', 'model'):
        if field in updates and not updates[field]: raise HTTPException(422, '模型名称、服务商和模型 ID 不能为空')
    if 'base_url' in updates:
        url = updates['base_url']
        try:
            parsed = urlparse(url)
            invalid = parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            parsed.port
        except ValueError:
            invalid = True
        if invalid or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url:
            raise HTTPException(422, 'Base URL 必须是有效的 http:// 或 https:// 服务地址，且不能包含凭证或查询参数')
        updates['base_url'] = url.rstrip('/')
    if 'api_path' in updates:
        path = updates['api_path']
        if not path.startswith('/') or path.startswith('//') or any(c in path for c in ('?', '#', '\\')) or any(c.isspace() or ord(c) < 32 for c in path) or '..' in path.split('/'):
            raise HTTPException(422, '接口路径必须以单个 / 开头，不能包含域名、凭证、查询参数或相对路径')
    if kind == 'video' and 'query_path' in updates:
        path = updates['query_path']
        literal = path.replace('{task_id}', 'task-id')
        if (path.count('{task_id}') != 1 or not path.startswith('/') or path.startswith('//')
                or any(c in literal for c in ('?', '#', '\\', '{', '}'))
                or any(c.isspace() or ord(c) < 32 for c in path) or '..' in path.split('/')):
            raise HTTPException(422, '查询路径必须以单个 / 开头，包含一个 {task_id}，且不能包含域名、查询参数或相对路径')
    if kind == 'image' and 'model_id' in updates: updates['model'] = updates.pop('model_id')
    return updates

def internal_default_model():
    # Explicit legacy ids continue to refer to the built-in model after switching defaults.
    return internal_registry_model('video', DEFAULT_MODEL_ID)

def model_public(config):
    public = registry_public('video', config)
    return public | {'capabilities': config.get('capabilities', {})}

def public_models():
    return [model_public(internal_default_model())] + [model_public(internal_registry_model('video', item['id'])) for item in listing('model_config') if item.get('id') != DEFAULT_MODEL_ID]

def internal_llm_config():
    return internal_registry_model('text')

def internal_image_config():
    return internal_registry_model('image')

def image_model_public(config):
    public = registry_public('image', config)
    return public | {'model': public['model_id'], 'configured': public['api_key_set'],
                     'size': config.get('size', '2K'), 'watermark': config.get('watermark', False)}

def llm_public(config):
    return registry_public('text', config)

def provider_timeout(config=None, fallback=300):
    """Return a bounded provider read timeout in seconds.

    Script and prompt requests are synchronous because their result is written
    into the project in the same transaction.  The previous fixed 120 second
    timeout was too short for long storyboard prompts and urllib exposed its
    low-level ``The read operation timed out`` text to users.  A model can set
    a timeout in Model Management; values are clamped to a safe range.
    """
    value = (config or {}).get('timeout_seconds', fallback)
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = float(fallback)
    return max(5.0, min(900.0, value))

def llm_json(config, messages):
    config = model_for_generation(config)
    if not config.get('api_key'):
        raise RuntimeError('尚未配置脚本大模型 API Key')
    base_url = config['base_url'].rstrip('/')
    api_path = '/' + config.get('api_path', '').lstrip('/')
    payload = {'model': config['model_id'], 'messages': messages, 'temperature': 0.7}
    response = provider_json('POST', base_url + api_path, config['api_key'], payload,
                             timeout=provider_timeout(config))
    if isinstance(response, dict):
        failure = response.get('error')
        for choice in response.get('choices') or []:
            if isinstance(choice, dict) and choice.get('finish_reason') == 'content_filter':
                failure = 'content_filter'
        if failure and normalize_content_error(failure) != failure:
            raise RuntimeError(normalize_content_error(failure))
    return response

def llm_text(response):
    choices = response.get('choices') if isinstance(response, dict) else None
    if isinstance(choices, list) and choices:
        message = choices[0].get('message', {}) if isinstance(choices[0], dict) else {}
        content = message.get('content', '') if isinstance(message, dict) else ''
        if isinstance(content, list):
            return ''.join(str(item.get('text', '')) if isinstance(item, dict) else str(item) for item in content)
        return str(content or '')
    return str(response.get('output_text') or response.get('text') or '') if isinstance(response, dict) else ''

def clean_llm_text(text):
    """Remove the light Markdown wrappers models sometimes add around plain text."""
    value = str(text or '').strip()
    if value.startswith('```'):
        value = value.split('\n', 1)[1] if '\n' in value else value
        value = value.rsplit('```', 1)[0].strip()
    return value

def parse_llm_json(response):
    text = llm_text(response).strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text
        text = text.rsplit('```', 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min([index for index in (text.find('{'), text.find('[')) if index >= 0], default=-1)
        end = max(text.rfind('}'), text.rfind(']'))
        if start >= 0 and end > start:
            try: return json.loads(text[start:end + 1])
            except json.JSONDecodeError: pass
    raise RuntimeError('大模型返回的内容不是有效 JSON，请重试或更换模型')

def script_defaults(source_node_id=''):
    return {'title': '未命名脚本', 'genre': '企业宣传片', 'duration': '60-90秒', 'style': '写实电影感', 'aspect_ratio': '16:9', 'resolution': '768p', 'video_duration': 4, 'negative_prompt': '', 'source_node_id': source_node_id, 'scenes': [], 'resources': [], 'updated_at': time.time()}

def script_resource_kind(resource):
    for field in ('kind', 'type'):
        explicit = str(resource.get(field) or '').strip().lower()
        if explicit in ('image', 'video', 'audio', 'text', 'script'): return explicit
    category = str(resource.get('category') or '').lower()
    if any(word in category for word in ('声音', '音频', '旁白', '配音', 'bgm', '音乐', '音效', 'audio', 'voice')): return 'audio'
    if any(word in category for word in ('视频', '片段', 'video')): return 'video'
    if any(word in category for word in ('脚本', '剧本', 'script')): return 'script'
    if any(word in category for word in ('文本', '台词', '文案', 'text')): return 'text'
    return 'image'

def script_resource_optional(resource):
    """Whether a preparation entry is a model recommendation.

    New entries are persisted with ``required: false``.  The generated
    ``resource-*`` id check keeps older AI-produced scripts compatible after
    this policy change without changing hand-authored required resources.
    """
    return resource.get('required', False) is False or str(resource.get('id') or '').startswith('resource-')


def validate_script_resource_assets(script_data, previous_data=None):
    labels = {'image': '图片', 'video': '视频', 'audio': '音频', 'text': '文本', 'script': '脚本'}
    previous = {r.get('id'): r for r in (previous_data or {}).get('resources', []) if isinstance(r, dict)}
    for resource in script_data.get('resources', []):
        resource.pop('binding_issue', None)
        aid = resource.get('asset_id')
        if not aid: continue
        asset = raw_record('asset', aid)
        label = resource.get('name', '脚本资源')
        if not asset or asset.get('deleted_at'):
            raise HTTPException(422, f'「{label}」绑定的素材不存在或已删除，请重新选择')
        expected = script_resource_kind(resource)
        actual = asset.get('kind', 'image')
        # Props are a dedicated library category, while remaining valid image
        # files for a storyboard's image reference slot.
        matches = is_image_asset_kind(actual) if expected == 'image' else actual == expected
        if not matches:
            old = previous.get(resource.get('id')) or {}
            if (old.get('asset_id') or old.get('assetId')) == aid and script_resource_kind(old) == expected:
                resource['binding_issue'] = f'需要{labels[expected]}资源，当前关联{labels.get(actual, "其他类型")}素材，请更换为匹配类型'
                continue
            raise HTTPException(422, f'「{label}」需要{labels[expected]}资源，不能绑定{labels.get(actual, "其他类型")}素材，请选择匹配的文件')


def script_scene_prompt(scene):
    """Use the same authored prompt parts shown in the script editor everywhere."""
    parts = [str(scene.get(key) or '').strip() for key in ('storyboard_prompt', 'motion_prompt')]
    if any(parts):
        return '；'.join(part for part in parts if part)
    # Excel imports and older hand-authored scripts may only have a final prompt.
    return str(scene.get('final_prompt') or '').strip() or str(scene.get('prompt') or '').strip()


def normalise_script(data, source_node_id=''):
    value = data if isinstance(data, dict) else {}
    result = {**script_defaults(source_node_id), **{key: value.get(key) for key in ('title', 'genre', 'duration', 'style', 'aspect_ratio', 'resolution', 'video_duration', 'negative_prompt', 'source_node_id') if value.get(key) is not None}}
    scenes = value.get('scenes') if isinstance(value.get('scenes'), list) else []
    result['scenes'] = []
    for index, scene in enumerate(scenes[:80], 1):
        item = scene if isinstance(scene, dict) else {}
        result['scenes'].append({
            'index': index,
            'duration': str(item.get('duration') or '6s'),
            'description': str(item.get('description') or item.get('frame_description') or ''),
            'shot_size': str(item.get('shot_size') or item.get('景别') or '中景'),
            'lighting': str(item.get('lighting') or item.get('atmosphere') or ''),
            'dialogue': str(item.get('dialogue') or ''),
            'sound': str(item.get('sound') or ''),
            'camera': str(item.get('camera') or ''),
            'aspect_ratio': str(item.get('aspect_ratio') or item.get('aspectRatio') or ''),
            'resolution': str(item.get('resolution') or ''),
            'storyboard_prompt': str(item.get('storyboard_prompt') or '').strip(),
            'motion_prompt': str(item.get('motion_prompt') or '').strip(),
            'final_prompt': str(item.get('final_prompt') or '').strip() or str(item.get('prompt') or '').strip(),
            'resource_ids': [str(rid) for rid in item.get('resource_ids', []) if isinstance(rid, str)] if isinstance(item.get('resource_ids'), list) else [],
        })
        result['scenes'][-1]['final_prompt'] = script_scene_prompt(result['scenes'][-1])
    resources = value.get('resources') if isinstance(value.get('resources'), list) else []
    result['resources'] = []
    for index, resource in enumerate(resources[:120], 1):
        item = resource if isinstance(resource, dict) else {}
        result['resources'].append({
            'id': str(item.get('id') or f'resource-{index}'),
            'name': str(item.get('name') or item.get('title') or f'参考资源 {index}'),
            'category': str(item.get('category') or '画面素材'),
            'kind': script_resource_kind(item),
            'reason': str(item.get('reason') or item.get('description') or '用于保持分镜中的主体和场景一致'),
            # A missing flag must not turn a model suggestion into a gate.
            # Explicit values are retained for backwards compatibility with
            # hand-authored scripts.
            'required': bool(item.get('required', False)),
            'asset_id': str(item.get('asset_id') or item.get('assetId') or ''),
            'status': 'uploaded' if item.get('asset_id') or item.get('assetId') else 'pending',
            'file_name': str(item.get('file_name') or ''),
            'scene_indices': [int(n) for n in item.get('scene_indices', []) if str(n).isdigit() and 0 < int(n) <= len(scenes)] if isinstance(item.get('scene_indices'), list) else [],
        })
    result['updated_at'] = time.time()
    return result


def script_completion_status(script_data):
    """Return the derived completion state for the three storyboard steps.

    A script can only be expanded into per-shot video nodes once it has at
    least one shot, every explicitly-required media resource is bound to an
    existing asset of the expected kind, and every shot has a final video
    prompt. Model-generated recommendations are normalized as optional before
    reaching this gate.
    """
    data = normalise_script(script_data or {})
    scenes = data.get('scenes') or []
    resources = [item for item in (data.get('resources') or [])
                 if script_resource_kind(item) in ('image', 'video', 'audio')]
    missing_resources = []
    labels = {'image': '图片', 'video': '视频', 'audio': '音频'}
    for resource in resources:
        if script_resource_optional(resource):
            continue
        asset_id = str(resource.get('asset_id') or '').strip()
        asset = raw_record('asset', asset_id) if asset_id else None
        expected = script_resource_kind(resource)
        if not asset or asset.get('deleted_at'):
            missing_resources.append(resource.get('name') or '未命名资源')
            continue
        actual = asset.get('kind', 'image')
        matches = is_image_asset_kind(actual) if expected == 'image' else actual == expected
        if not matches:
            missing_resources.append(f"{resource.get('name') or '未命名资源'}（需要{labels.get(expected, expected)}）")
    missing_prompts = [scene.get('index') for scene in scenes
                       if not str(scene.get('final_prompt') or '').strip()]
    return {
        'script': bool(scenes),
        'resources': not missing_resources,
        'prompts': bool(scenes) and not missing_prompts,
        'complete': bool(scenes) and not missing_resources and not missing_prompts,
        'scene_count': len(scenes),
        'missing_resources': missing_resources,
        'missing_prompts': missing_prompts,
    }


def script_video_settings(script_data, scene_index=None):
    """Resolve the storyboard prompt and duration for one shot.

    Expanded video nodes keep a snapshot for the canvas UI, but the saved
    script remains the source of truth for the shot prompt and duration.
    Aspect ratio and resolution are chosen on the video node.
    """
    data = normalise_script(script_data or {})
    scenes = data.get('scenes') or []
    scene = None
    if scene_index is not None:
        try:
            scene = next((item for item in scenes if int(item.get('index', 0)) == int(scene_index)), None)
        except (TypeError, ValueError):
            scene = None
    scene = scene or (scenes[0] if scenes else {})
    prompt = script_scene_prompt(scene)
    aspect_ratio = str(scene.get('aspect_ratio') or data.get('aspect_ratio') or '16:9')
    resolution = str(scene.get('resolution') or data.get('resolution') or '768p').lower()
    raw_duration = scene.get('duration') or data.get('video_duration') or 4
    try:
        parsed_duration = float(re.sub(r'[^0-9.]', '', str(raw_duration)))
    except (TypeError, ValueError):
        parsed_duration = 5
    duration = min(60, max(1, round(parsed_duration))) if parsed_duration > 0 else 5
    return {
        'prompt': prompt,
        'aspect_ratio': aspect_ratio,
        'duration': int(duration),
        'resolution': resolution,
    }

def save_script_to_project(project_id, node_id, script_data, expected_revision=None):
    normalised = normalise_script(script_data)
    original = raw_record('project', project_id) or {}
    previous = next((node.get('script_data') or {} for node in original.get('nodes', []) if node.get('id') == node_id), {})
    validate_script_resource_assets(normalised, previous)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT body FROM records WHERE kind=? AND id=?', ('project', project_id)).fetchone()
        if not row: raise HTTPException(404, '项目不存在')
        project = require_access('project', json.loads(row[0]))
        if project.get('deleted_at'): raise HTTPException(409, '项目已移入回收站')
        if expected_revision is not None and expected_revision != project.get('revision', 0):
            raise HTTPException(409, '生成期间项目已更新，未覆盖新内容。请重新打开项目后重试')
        target = next((node for node in project.get('nodes', []) if node.get('id') == node_id and node.get('type') == 'script'), None)
        if not target: raise HTTPException(422, '脚本节点不存在，请先保存画布')
        if not normalised.get('source_node_id'):
            normalised['source_node_id'] = target.get('script_data', {}).get('source_node_id', '')
        target['script_data'] = normalised
        body = {**project, 'updated_at': time.time(), 'revision': project.get('revision', 0) + 1}
        db.execute('UPDATE records SET body=? WHERE kind=? AND id=?', (json.dumps(body, ensure_ascii=False), 'project', project_id))
    return body

def merge_script_resources(previous, incoming):
    """Keep explicit uploaded references when a later analysis revises the resource list."""
    merged, used = [], set()
    for index, item in enumerate(incoming):
        if not isinstance(item, dict): raise ValueError('资源清单格式不完整，请重试')
        match = next((old for old in previous if old.get('id') not in used and
                      ((item.get('id') and old.get('id') == item.get('id')) or
                       (old.get('name') == item.get('name') and old.get('category') == item.get('category')))), None)
        rid = match['id'] if match else 'resource-' + uuid.uuid4().hex[:12]
        used.add(rid)
        asset_id = (match.get('asset_id') or match.get('assetId') or '') if match else ''
        merged.append({**{key: value for key, value in item.items() if key != 'assetId'}, 'id': rid,
                       'kind': script_resource_kind(match) if asset_id else script_resource_kind(item),
                       'asset_id': asset_id, 'file_name': match.get('file_name', '') if match else ''})
    merged.extend(old for old in previous if (old.get('asset_id') or old.get('assetId')) and old.get('id') not in used)
    return merged


def merge_preparation_resources(previous, incoming):
    supported = [item for item in incoming if isinstance(item, dict) and script_resource_kind(item) in ('image', 'video', 'audio')]
    merged = merge_script_resources(previous, supported)
    # Every item returned by the resource-analysis step is a recommendation.
    # Normalise the flag even if a provider ignores the JSON instruction and
    # returns ``required: true``.
    incoming_ids = {str(item.get('id')) for item in supported if item.get('id')}
    for item in merged:
        item_id = str(item.get('id') or '')
        if item_id in incoming_ids or item_id.startswith('resource-'):
            item['required'] = False
    present = {item.get('id') for item in merged}
    # Existing text/script references remain available in project history and snapshots.
    merged.extend(item for item in previous if script_resource_kind(item) in ('text', 'script') and item.get('id') not in present)
    return merged

def resolve_model_config(config_id, model_id=''):
    try:
        config = internal_registry_model('video', config_id)
    except HTTPException:
        raise HTTPException(422, '模型配置不存在，请重新选择模型')
    return model_for_generation(config)

def provider_error_message(error):
    text = normalize_content_error(safe_model_error(error)).replace('\n', ' ').strip()
    # urllib/Python versions use several different exception strings for a
    # socket read timeout.  Normalize all of them so API consumers receive a
    # useful, retryable Chinese message instead of implementation details.
    lowered = text.lower()
    if isinstance(error, (TimeoutError, socket.timeout)) or any(marker in lowered for marker in (
            'read operation timed out', 'timed out', 'timeout', 'timedout')):
        return '模型接口读取超时，模型可能仍在处理，请稍后重试。'
    return text[:500] or '模型服务请求失败'

MAX_REFERENCE_IMAGE_BYTES = 10 * 1024 * 1024

def require_reference_image(asset_id):
    try:
        asset = get('asset', asset_id)
    except HTTPException:
        raise HTTPException(422, '有参考图不存在或已删除，请重新连接图片素材') from None
    name = asset.get('name') or '参考图'
    if asset.get('deleted_at') or not is_image_asset_kind(asset.get('kind', 'image')):
        raise HTTPException(422, f'「{name}」不是图片素材，不能作为生图参考')
    file_name = Path(str(asset.get('url') or '')).name
    file_path = UPLOADS / file_name
    if not file_name or file_name != Path(file_name).name or not file_path.is_file():
        raise HTTPException(422, f'参考图「{name}」文件已丢失，请重新上传')
    if file_path.stat().st_size > MAX_REFERENCE_IMAGE_BYTES:
        raise HTTPException(422, f'参考图「{name}」超过 10 MB，请压缩后再生成')
    return asset, file_path

def reference_image_data_uri(asset_id):
    _asset, file_path = require_reference_image(asset_id)
    encoded = base64.b64encode(file_path.read_bytes()).decode('ascii')
    mime = MIMES.get(file_path.suffix.lower(), 'image/jpeg')
    return f'data:{mime};base64,{encoded}'

def asset_data_uri(asset_id):
    asset = get('asset', asset_id)
    if asset.get('deleted_at') or (not is_image_asset_kind(asset.get('kind', 'image')) and asset.get('kind') not in ('audio', 'video')):
        raise HTTPException(422, '当前视频模型仅支持图片参考，不能将视频或音频素材作为图片提交')
    file_name = Path(asset.get('url', '')).name
    file_path = UPLOADS / file_name
    if not file_path.exists():
        raise RuntimeError(f'素材文件不存在：{asset.get("name", asset_id)}')
    encoded = base64.b64encode(file_path.read_bytes()).decode('ascii')
    if asset.get('kind') == 'video':
        if file_path.suffix.lower() != '.mp4':
            raise HTTPException(422, '视频参考请选择 MP4 文件')
        mime = 'video/mp4'
    elif asset.get('kind') == 'audio':
        if file_path.suffix.lower() not in ('.wav', '.mp3'):
            raise HTTPException(422, '视频音频参考请选择 WAV 或 MP3 文件')
        mime = 'audio/wav' if file_path.suffix.lower() == '.wav' else 'audio/mp3'
    else:
        mime = MIMES.get(file_path.suffix.lower(), 'image/jpeg')
    return f'data:{mime};base64,{encoded}'

def provider_payload(params, config):
    def reference_url(asset_id):
        if provider_id('video', config) != 'aimarket':
            return asset_data_uri(asset_id)
        asset = get('asset', asset_id)
        if asset.get('deleted_at') or (not is_image_asset_kind(asset.get('kind')) and asset.get('kind') != 'video'):
            raise ValueError('当前服务商仅接入图片或视频参考素材')
        url = str(asset.get('url') or '')
        if video_reference_transport(config) == 'data_uri' and url.startswith('/uploads/'):
            return asset_data_uri(asset_id) if asset.get('kind') == 'video' else reference_image_data_uri(asset_id)
        if not url.startswith(('https://', 'http://')):
            raise ValueError('AI Market 参考图需要上游可访问的 HTTP(S) URL，当前本地素材无法直接使用；可改用文生视频或火山方舟模型')
        return url
    payload = video_payload(params, config, reference_url, lambda aid: get('asset', aid).get('kind', 'image'))
    if provider_id('video', config) == 'aimarket' and video_reference_transport(config) == 'data_uri':
        if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > 64 * 1024 * 1024:
            raise ValueError('MiniMax H3 请求超过 64 MB，请减少参考图数量或压缩图片后重试')
    return payload

def provider_json(method, url, api_key, body=None, timeout=45):
    api_key = model_api_key(url, api_key)
    payload = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
    request = Request(url, data=payload, method=method, headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with open_model_request(request, timeout=timeout, fallback=urlopen) as response:
            raw = response.read().decode('utf-8')
            return redact_market_result(json.loads(raw), api_key) if raw else {}
    except HTTPError as error:
        detail = safe_model_error(error.read().decode('utf-8', errors='replace'), api_key)
        message = normalize_content_error(detail)
        if message != detail:
            raise RuntimeError(message) from None
        failure = RuntimeError(f'模型接口返回 HTTP {error.code}: {detail[:400]}')
        failure.status_code = error.code
        raise failure from None
    except (TimeoutError, socket.timeout) as error:
        # ``urlopen`` may raise this from either connect() or response.read().
        # In particular, the latter used to escape as the raw English error
        # ``The read operation timed out``.
        raise RuntimeError(f'模型接口读取超时（{int(timeout)}秒），模型可能仍在处理，请稍后重试。') from error
    except URLError as error:
        reason = error.reason
        if isinstance(reason, (TimeoutError, socket.timeout)) or 'timed out' in str(reason).lower():
            raise RuntimeError(f'模型接口读取超时（{int(timeout)}秒），模型可能仍在处理，请稍后重试。') from error
        raise RuntimeError(f'无法连接模型接口：{reason}') from error

def is_quota_limit_error(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return 'SetLimitExceeded' in text or 'has reached the set usage limit' in text or 'has reached the set inference limit' in text

def update_task(tid, **updates):
    current = get('task', tid)
    return save('task', {**current, **updates, 'updated_at': time.time()})


def cache_generated_video(url):
    """Persist a provider result independently of manual library membership.

    The provider URL is a short-lived signed URL.  Write the downloaded bytes
    through a temporary file and atomically rename it so a browser can never
    observe a partially written MP4/WebM file.
    """
    data, suffix = download_generated_video(url, trusted_origin=model_gateway_origin())
    UPLOADS.mkdir(parents=True, exist_ok=True)
    target = UPLOADS / ('generated-video-' + uuid.uuid4().hex + suffix)
    temporary = target.with_name(target.name + '.part')
    try:
        temporary.write_bytes(data)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return '/uploads/' + target.name


def persisted_task_video_url(task_id):
    """Return an existing local copy created by the explicit asset action."""
    for asset in listing('asset'):
        if asset.get('task_id') != task_id or asset.get('kind') != 'video' or asset.get('deleted_at'):
            continue
        url = str(asset.get('url') or '')
        name = Path(url).name
        if url.startswith('/uploads/') and name and name == url[len('/uploads/'): ] and (UPLOADS / name).is_file():
            return url
    return ''


def normalize_task_video(task):
    """Use a saved local asset when an older task still points at a remote URL."""
    if task.get('video_cache_status') == 'expired' and task.get('video_url'):
        return save('task', {**task, 'video_url': '', 'updated_at': time.time()})
    if task.get('status') != 'done' or not task.get('video_url') or str(task['video_url']).startswith('/uploads/'):
        return task
    local_url = persisted_task_video_url(task.get('id', ''))
    if not local_url:
        return task
    changed = {**task, 'video_url': local_url, 'video_cache_status': 'cached', 'updated_at': time.time()}
    return save('task', changed)

ACTIVE_VIDEO_TASKS = set()
VIDEO_WORKER_LOCK = threading.RLock()


def run_provider_task(tid, params, config, resume=False):
    with worker_scope(get('task', tid)):
        return _run_provider_task(tid, params, config, resume)


VIDEO_QUERY_WINDOW = 24 * 60 * 60
VIDEO_SLOW_AFTER = 120


def video_poll_delay(elapsed, errors=0):
    return min(60, 5 * 2 ** min(errors, 4)) if errors else (5 if elapsed < 60 else 10 if elapsed < 300 else 20)


def video_failure_is_terminal(response):
    # A gateway/query error is not proof that the submitted generation failed.
    return any(STATES.get(str(item.get(key, '')).strip().lower().replace('-', '_').replace(' ', '_')) == 'failed'
               for item in response_objects(response) for key in ('task_status', 'status', 'state'))


def _run_provider_task(tid, params, config, resume=False):
    config = with_model_gateway(config)
    provider_task_id = get('task', tid).get('provider_task_id') if resume else None
    try:
        if not resume:
            base_url = config['base_url'].rstrip('/')
            api_path = '/' + config.get('api_path', '').lstrip('/')
            created = provider_json('POST', base_url + api_path, config['api_key'], provider_payload(params, config))
            creation_error = video_response_error(created)
            if creation_error:
                raise RuntimeError(creation_error)
            provider_task_id = video_task_id(created)
            update_task(tid, status='queued', provider_task_id=provider_task_id, provider_response=created,
                        submitted_at=time.time(), status_message='已提交，正在排队或生成', auto_save_video=False)
        if not provider_task_id:
            raise RuntimeError('模型接口未返回任务 ID')
        current = update_task(tid, polling_interrupted=False, query_paused=False, error=None, auto_save_video=False)
        query_url = video_query_url(config, provider_task_id)
        submitted = current.get('submitted_at') or current.get('created_at') or time.time()
        deadline = time.monotonic() + VIDEO_QUERY_WINDOW
        errors = 0
        while time.monotonic() < deadline:
            elapsed = max(0, time.time() - submitted)
            time.sleep(video_poll_delay(elapsed, errors))
            current = get('task', tid)
            if current.get('deleted_at'):
                return
            message = '本次生成较慢，仍在处理中' if elapsed >= VIDEO_SLOW_AFTER else '已提交，正在排队或生成'
            try:
                # Key remains only in this worker. The capability can query this
                # exact submitted job, never create a new one or call another URL.
                with video_query_credentials(query_url, config['api_key'], current.get('_market_actor_id', '')):
                    result = provider_json('GET', query_url, config['api_key'])
                parsed = video_result(result)
                if parsed['status'] == 'failed' and not video_failure_is_terminal(result):
                    raise RuntimeError('查询接口暂未返回有效任务状态')
                if parsed['status'] == 'done' and not parsed['video_url']:
                    raise RuntimeError('模型已完成，正在等待视频地址')
            except Exception as error:
                errors += 1
                if getattr(error, 'status_code', None) in (401, 403, 428):
                    update_task(tid, status='running', error=None, polling_interrupted=True, query_paused=True,
                        query_retry_at=time.time()+60, status_message='查询授权已失效，请从集市重新打开；原任务已保留')
                    return
                update_task(tid, status='running', error=None, query_errors=errors,
                    status_message=message if elapsed >= VIDEO_SLOW_AFTER else '查询暂时中断，正在自动重试',
                    last_query_at=time.time())
                continue
            errors = 0
            status = parsed['status']
            if status in ('queued', 'running'):
                update_task(tid, status=status, error=None, query_errors=0, status_message=message,
                            last_query_at=time.time(), provider_response=result)
            elif status == 'done':
                update_task(tid, status='done', error=None, video_url=parsed['video_url'],
                    video_cache_status='pending', asset_save_status='history', provider_response=result,
                    status_message='生成完成，已加入生成历史', completed_at=time.time())
                finalize_video_task(tid)
                return
            elif status == 'failed':
                provider_error = parsed['error'] or '模型任务失败'
                update_task(tid, status='blocked' if is_quota_limit_error(provider_error) else 'failed',
                    error=normalize_content_error(provider_error), provider_response=result,
                    status_message='生成失败', polling_interrupted=False)
                return
        update_task(tid, status='running', error=None, polling_interrupted=True, query_paused=True,
                    query_retry_at=time.time()+60, status_message='任务仍未结束，重新打开项目后继续查询原任务')
    except Exception as error:
        message = provider_error_message(error)
        update_task(tid, status='running' if provider_task_id else 'blocked' if is_quota_limit_error(message) else 'failed',
                    error=None if provider_task_id else message, polling_interrupted=bool(provider_task_id),
                    query_paused=bool(provider_task_id), query_retry_at=time.time()+60,
                    status_message='查询暂时中断，稍后自动继续' if provider_task_id else '',
                    submit_unknown=not provider_task_id and ('超时' in message or '未返回任务 ID' in message))
    finally:
        with VIDEO_WORKER_LOCK:
            ACTIVE_VIDEO_TASKS.discard(tid)


def ensure_video_asset(tid):
    """Record completed video in history independently of library membership."""
    with _task_asset_lock:
        task = normalize_task_video(get('task', tid))
        if task.get('deleted_at') or task.get('status') != 'done' or not task.get('video_url'):
            return None
        if task.get('asset_save_status') == 'pending' and task.get('asset_save_error'):
            task = update_task(tid, video_cache_status='failed', video_cache_error=task['asset_save_error'])
        previous = next((a for a in listing('asset') if a.get('task_id') == tid and a.get('kind') == 'video'), None)
        if previous and previous.get('deleted_at'):
            return previous
        url = task['video_url']
        name = url[len('/uploads/'):] if url.startswith('/uploads/') else ''
        path = UPLOADS / name if name and Path(name).name == name and '\\' not in name else None
        cached = bool(path and path.is_file())
        if not cached and (not task.get('video_cache_status') or path):
            task = update_task(tid, video_cache_status='failed' if path else 'pending',
                video_cache_error='视频文件不存在，请重新生成或导入视频' if path else '')
        asset = {**(previous or {}), 'id': previous['id'] if previous else str(uuid.uuid5(uuid.NAMESPACE_URL, 'jht-video-task:' + tid)),
            'kind': 'video', 'name': (previous or {}).get('name') or clean_asset_name((task.get('params') or {}).get('prompt', '')[:50] or '生成视频'),
            'url': url, 'source': 'generated', 'project_id': task.get('project_id', ''),
            'folder_id': (previous or {}).get('folder_id', ''), 'task_id': tid,
            'in_library': bool((previous or {}).get('in_library')),
            'size_bytes': path.stat().st_size if cached else 0, 'mime_type': MIMES.get(path.suffix, 'video/mp4') if cached else 'video/mp4',
            'created_at': (previous or {}).get('created_at') or task.get('completed_at') or task.get('created_at') or time.time(),
            'duration': (task.get('params') or {}).get('duration', 0),
            'file_status': 'cached' if cached else task.get('video_cache_status', 'pending'),
            'file_error': task.get('video_cache_error', ''), **record_creator(task)}
        if asset != previous:
            asset = save('asset', asset)
        updates = {'asset_id': asset['id'], 'asset_save_status': 'saved' if asset['in_library'] else 'history',
            'auto_save_video': False, 'status_message': ('生成完成，文件下载失败，可在生成历史中重试下载'
                if asset['file_status'] == 'failed' else '生成完成，已保存至资产库' if asset['in_library'] else '生成完成，已加入生成历史')}
        if cached:
            updates.update(video_cache_status='cached', video_cache_error='')
        if any(task.get(k) != v for k, v in updates.items()):
            update_task(tid, **updates)
        return asset


_video_file_locks = {}
_video_file_lock_guard = threading.Lock()


def finalize_video_task(tid):
    # Download failures are separate from generation and never loop forever.
    with _video_file_lock_guard:
        lock = _video_file_locks.setdefault(tid, threading.Lock())
    with lock:
        current = get('task', tid)
        if current.get('deleted_at') or current.get('status') != 'done' or not current.get('video_url'):
            return
        asset = ensure_video_asset(tid)
        if not asset or asset.get('deleted_at'):
            return
        current = get('task', tid)
        try:
            if not current.get('video_url', '').startswith('/uploads/'):
                update_task(tid, video_cache_status='pending', video_cache_error='')
                url = cache_generated_video(current['video_url'])
                update_task(tid, video_url=url, video_cache_status='cached', video_cache_error='')
            else:
                if not asset.get('size_bytes'):
                    raise ValueError('视频文件不存在，请重新生成或导入视频')
                update_task(tid, video_cache_status='cached', video_cache_error='')
        except Exception as error:
            update_task(tid, video_cache_status='failed', video_cache_error=provider_error_message(error),
                asset_save_error='', auto_save_video=False, asset_save_status='history',
                status_message='生成已完成，文件下载失败；可在生成历史中重试保存')
        return ensure_video_asset(tid)


def refresh_video_task(tid):
    current = normalize_task_video(get('task', tid))
    if current.get('deleted_at'):
        return current
    if current.get('provider_task_id') and (current['status'] in ('queued', 'running') or current.get('polling_interrupted')):
        try:
            return reconcile_video_task(tid)
        except HTTPException as error:
            if error.status_code not in (401, 403, 422, 428):
                raise
            return update_task(tid, status='running', error=None, polling_interrupted=True, query_paused=True,
                query_retry_at=time.time()+30, status_message='等待重新连接集市以继续查询，原任务已保留')
    if current['status'] == 'done' and current.get('video_url'):
        legacy_pending = current.get('auto_save_video') and current.get('asset_save_status') == 'pending'
        asset = ensure_video_asset(tid)
        if asset and asset.get('deleted_at'):
            return update_task(tid, asset_save_status='deleted', auto_save_video=False,
                video_cache_status='deleted', status_message='生成记录已删除')
        current = get('task', tid)
        if current.get('video_cache_status') == 'pending' or (legacy_pending and current.get('video_cache_status') != 'failed'):
            with VIDEO_WORKER_LOCK:
                if tid not in ACTIVE_VIDEO_TASKS:
                    ACTIVE_VIDEO_TASKS.add(tid)
                    def finish():
                        try:
                            with worker_scope(get('task', tid)):
                                finalize_video_task(tid)
                        finally:
                            with VIDEO_WORKER_LOCK:
                                ACTIVE_VIDEO_TASKS.discard(tid)
                    start_generation_worker('task', current, finish, ())
        current = get('task', tid)
    return current


def recover_video_tasks():
    for item in listing('task'):
        if item.get('deleted_at') or not item.get('provider_task_id'):
            continue
        with worker_scope(item):
            refresh_video_task(item['id'])


def reconcile_video_task(tid):
    """Resume only the GET loop of an existing task; never repeat its POST."""
    with VIDEO_WORKER_LOCK:
        current = get('task', tid)
        if tid in ACTIVE_VIDEO_TASKS or current['status']=='done':
            return current
        if current.get('provider_task_id') and (current['status'] in ('queued','running') or current.get('polling_interrupted')):
            refreshed = refresh_market_credentials(current)
            if refreshed != current:
                current = save('task', {**refreshed, 'query_retry_at': 0})
            if current['status'] not in ('queued', 'running'):
                current = update_task(tid, status='running', error=None)
            if current.get('query_retry_at', 0) > time.time():
                return current
            # A resumed job keeps its credential source, including when opened
            # from an ordinary page after its marketplace connection expired.
            with worker_scope(current):
                config=resolve_model_config(current['params'].get('model_config_id'),current['params'].get('model'))
            if not config.get('api_key'):
                raise HTTPException(422,'恢复查询需要原视频模型的 API Key，请在模型管理中配置')
            ACTIVE_VIDEO_TASKS.add(tid)
            current=update_task(tid,status='running',polling_interrupted=False,query_paused=False,error=None,
                status_message='本次生成较慢，仍在处理中' if time.time()-current.get('created_at',time.time()) >= VIDEO_SLOW_AFTER else '已提交，正在排队或生成')
            start_generation_worker('task', current, run_provider_task, (tid,current['params'],config,True))
        elif current['status'] in ('queued','running'):
            current=update_task(tid,status='blocked',submit_unknown=True,
                                error='服务中断时未收到供应商任务编号。请先核对供应商记录，重试可能再次计费。')
        return current

@app.get('/api/health')
def health(): return {'status': 'ok', 'backend': 'Python / FastAPI', 'storage': 'PostgreSQL' if database.dialect == 'postgresql' else 'SQLite (legacy/test)', 'storage_free_bytes': shutil.disk_usage(DATA).free, 'provider_configured': any(item.get('api_key_set') for item in public_models())}

@app.get('/api/options')
def options(): return VIDEO_OPTIONS

@app.get('/api/visual-styles')
def visual_styles(): return public_styles()

@app.get('/api/characters')
def characters():
    return DEFAULT_CHARACTERS + listing('character')

@app.post('/api/characters', status_code=201)
def create_character(character: Character):
    raise HTTPException(410, '角色资产已移除，请使用图片素材')
@app.put('/api/characters/{cid}')
def update_character(cid: str, patch: CharacterUpdate):
    current = raw_record('character', cid)
    if not current: raise HTTPException(404, '角色不存在')
    if patch.name is not None and not patch.name.strip(): raise HTTPException(422, '角色名称不能为空')
    if patch.asset_id: require_image_asset(patch.asset_id)
    return save('character', {**current, **patch.model_dump(exclude_none=True), 'updated_at': time.time()})

@app.delete('/api/characters/{cid}', status_code=204)
def delete_character(cid: str):
    if cid in {item['id'] for item in DEFAULT_CHARACTERS} and not raw_record('character', cid):
        raise HTTPException(400, '内置角色不能删除')
    if not raw_record('character', cid): raise HTTPException(404, '角色不存在')
    for project in listing('project'):
        if any(node.get('characterId') == cid for node in project.get('nodes', [])):
            raise HTTPException(409, f'角色被项目「{project.get("name", "未命名项目")}」引用，请先移除画布中的角色节点（含回收站项目）')
    with connect() as db: db.execute('DELETE FROM records WHERE kind=? AND id=?', ('character', cid))

@app.get('/api/models')
def models(): return public_models()

@app.get('/api/model-registry')
def model_registry():
    models = []
    for kind, store_kind in MODEL_STORES.items():
        builtin_id = MODEL_BUILTINS[kind]['id']
        ids = [builtin_id] + [item['id'] for item in listing(store_kind) if item.get('id') != builtin_id]
        models.extend(registry_public(kind, internal_registry_model(kind, mid)) for mid in ids)
    return {'models': models, 'providers': provider_catalog(), 'can_manage': is_admin()}

@app.post('/api/model-registry', status_code=201)
def create_registry_model(config: RegistryModelCreate):
    require_admin()
    kind = config.kind
    updates = validate_model_updates(kind, config.model_dump(exclude={'kind'}))
    body = {'id': str(uuid.uuid4()), **updates, 'created_at': time.time(), 'updated_at': time.time()}
    if kind == 'image': body.update({'size': '2K', 'watermark': False})
    save(MODEL_STORES[kind], body)
    return registry_public(kind, internal_registry_model(kind, body['id']))

@app.put('/api/model-registry/{kind}/{mid}')
def update_registry_model(kind: str, mid: str, patch: RegistryModelUpdate):
    require_admin()
    current = internal_registry_model(kind, mid)
    updates = validate_model_updates(kind, patch.model_dump(exclude_none=True), current)
    body = {**current, **updates, 'id': mid, 'updated_at': time.time()}
    save(MODEL_STORES[kind], body)
    return registry_public(kind, body)

@app.post('/api/model-registry/{kind}/{mid}/activate')
def activate_registry_model(kind: str, mid: str):
    require_admin()
    current = internal_registry_model(kind, mid)
    save('model_default', {'id': 'model-default:' + kind, 'model_id': mid, 'updated_at': time.time()})
    return registry_public(kind, current)

def project_uses_model(value, mid):
    if isinstance(value, dict):
        return value.get('model_config_id') == mid or any(project_uses_model(item, mid) for item in value.values())
    return isinstance(value, list) and any(project_uses_model(item, mid) for item in value)

@app.delete('/api/model-registry/{kind}/{mid}', status_code=204)
def delete_registry_model(kind: str, mid: str):
    require_admin()
    internal_registry_model(kind, mid)
    if mid == MODEL_BUILTINS[kind]['id']: raise HTTPException(409, '内置模型不能删除，可以编辑配置或切换默认模型')
    if mid == active_model_id(kind): raise HTTPException(409, '请先将其他模型设为默认，再删除此模型')
    if kind in ('video', 'audio') and any(project_uses_model(project, mid) or (project.get('settings') or {}).get('model_config_id')==mid for project in listing('project')+listing('conversation')):
        raise HTTPException(409, '仍有画布或对话记录使用此模型，请先切换生成模型')
    with connect() as db: db.execute('DELETE FROM records WHERE kind=? AND id=?', (MODEL_STORES[kind], mid))

@app.post('/api/models', status_code=201)
def create_model(config: ModelConfig):
    require_admin()
    if not config.api_key.strip(): raise HTTPException(422, '请填写 API Key，Key 只会保存在服务端')
    updates = validate_model_updates('video', config.model_dump())
    body = {'id': str(uuid.uuid4()), **updates, 'is_default': False, 'created_at': time.time()}
    return model_public(save('model_config', body))

@app.put('/api/models/{mid}')
def update_model(mid: str, patch: ModelConfigUpdate):
    require_admin()
    current = internal_registry_model('video', mid)
    updates = validate_model_updates('video', patch.model_dump(exclude_none=True), current)
    body = {**current, **updates, 'id': mid, 'updated_at': time.time()}
    return model_public(save('model_config', body))

@app.delete('/api/models/{mid}', status_code=204)
def delete_model(mid: str):
    return delete_registry_model('video', mid)

@app.get('/api/llm')
def llm_config(): return llm_public(internal_llm_config())


@app.get('/api/image-model')
def image_model_config():
    return image_model_public(internal_image_config())


@app.put('/api/image-model')
def update_image_model_config(patch: ImageModelUpdate):
    require_admin()
    updates = validate_model_updates('image', patch.model_dump(exclude_none=True))
    if updates.get('base_url', '').endswith('/images/generations'):
        updates['base_url'] = updates['base_url'][:-len('/images/generations')]
    current = internal_image_config()
    config = {**current, **updates, 'id': current['id'], 'watermark': False, 'updated_at': time.time()}
    return image_model_public(save('image_model_config', config))


def image_task_public(task):
    public = {key: task[key] for key in ('id', 'status', 'name', 'prompt', 'model', 'size', 'watermark',
                                      'aspect_ratio', 'count', 'assets', 'model_config_id', 'project_id', 'folder_id', 'created_at', 'updated_at', 'error', 'asset', 'visual_style', 'style_snapshot', 'effective_prompt') if key in task}
    return hydrate_task_assets(public)


def hydrate_task_assets(public):
    def fresh(snapshot):
        if not snapshot or not snapshot.get('id'):
            return None
        try:
            return asset_public(require_live_asset(snapshot['id']))
        except HTTPException:
            return None
    if 'asset' in public:
        public['asset'] = fresh(public['asset'])
    if 'assets' in public:
        public['assets'] = [asset for snapshot in public['assets'] if (asset := fresh(snapshot))]
    return public


def run_image_generation(tid, config):
    with worker_scope(get('image_task', tid)):
        return _run_image_generation(tid, config)


def _run_image_generation(tid, config):
    task, asset, assets = None, None, []
    try:
        with IMAGE_WORKER_SLOTS:
            task = get('image_task', tid)
            config = model_for_generation(config)
            task = save('image_task', {**task, 'status': 'running', 'updated_at': time.time()})
            payload = {key: task[key] for key in ('model', 'prompt', 'size')}
            payload['prompt'] = task.get('effective_prompt') or task['prompt']
            payload['watermark'] = False
            payload['size'] = image_output_size(task['size'], task.get('aspect_ratio', 'adaptive'))
            reference_ids = task.get('reference_asset_ids') or []
            if reference_ids:
                images = [reference_image_data_uri(asset_id) for asset_id in reference_ids]
                payload['image'] = images[0] if len(images) == 1 else images
            payload.update({'response_format': 'url', 'stream': False})
            if provider_id('image', config) == 'aimarket':
                # The gateway uses OpenAI's n field; Ark-only switches are omitted.
                payload['n'] = 1
                for field in ('watermark', 'stream', 'response_format'):
                    payload.pop(field, None)
            name = task['name']
            if name == 'AI 生成图片':
                name = generated_image_name(task['prompt'], [get('asset', aid).get('name', '') for aid in reference_ids])
            if Path(name).suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp'): name = Path(name).stem
            folder_id = task['folder_id'] if not task['folder_id'] or raw_record('asset_folder', task['folder_id']) else ''
            # Independent single-image calls give an exact count on models without group generation.
            for index in range(task.get('count', 1)):
                response = provider_json('POST', config['base_url'].rstrip('/') + '/' + config.get('api_path', '/images/generations').lstrip('/'),
                                         config['api_key'], payload, timeout=180)
                items = response.get('data') if isinstance(response, dict) else None
                if not isinstance(items, list) or not items or not isinstance(items[0], dict) or not items[0].get('url'):
                    detail = response.get('error') if isinstance(response, dict) else None
                    if not detail and isinstance(items, list) and items and isinstance(items[0], dict): detail = items[0].get('error')
                    if detail:
                        normalized = normalize_content_error(detail)
                        raise RuntimeError(str(normalized.get('message', normalized) if isinstance(normalized, dict) else normalized))
                    raise RuntimeError('图片模型未返回可下载图片，请检查模型配置或稍后重试')
                data, suffix = download_generated_image(items[0]['url'])
                asset = store_uploaded_asset(data, name + (f'-{index + 1}' if task.get('count', 1) > 1 else '') + suffix,
                                            task['project_id'], folder_id, source='generated', task_id=tid, asset_kind=task.get('asset_kind', 'image'))
                assets.append(asset)
                task = save('image_task', {**task, 'asset': assets[0], 'assets': assets, 'updated_at': time.time()})
            save('image_task', {**task, 'status': 'succeeded', 'updated_at': time.time()})
    except Exception as error:
        message = '服务端磁盘空间不足，图片暂时无法保存。请释放空间后重试。' if is_storage_full(error) else image_error_message(error, config.get('api_key', ''))
        failed = {**(task or {'id': tid}), 'status': 'failed', 'error': message, 'updated_at': time.time()}
        if assets: failed.update({'asset': assets[0], 'assets': assets})
        try:
            save('image_task', failed)
        except Exception:
            # Keep the failure pollable even when the database has run out of space.
            with IMAGE_TASK_LOCK:
                if len(IMAGE_TASK_FALLBACKS) >= 256: IMAGE_TASK_FALLBACKS.pop(next(iter(IMAGE_TASK_FALLBACKS)))
                IMAGE_TASK_FALLBACKS[tid] = failed
    finally:
        with IMAGE_TASK_LOCK:
            ACTIVE_IMAGE_TASKS.discard(tid)


@app.post('/api/images/generations', status_code=202)
def create_image_generation(request: ImageGenerationRequest):
    with manual_generation.submission(request, 'image'):
        return _create_image_generation(request)


def _create_image_generation(request: ImageGenerationRequest):
    params = request.model_dump(exclude={'request_id', 'model_config_id'})
    if params.get('visual_style') is None:
        params.pop('visual_style', None)
    params['name'] = clean_asset_name(params['name'])
    request_id = request.request_id.strip() or str(uuid.uuid4())
    tid = 'image-' + str(uuid.uuid5(uuid.NAMESPACE_URL, request_key(request_id)))
    with submission_task_lock(IMAGE_TASK_LOCK):
        existing = IMAGE_TASK_FALLBACKS.get(tid) or raw_record('image_task', tid)
        if existing:
            require_access('image_task', existing)
            comparable = {'aspect_ratio': 'adaptive', 'count': 1, **existing, 'reference_asset_ids': existing.get('reference_asset_ids') or []}
            if existing.get('visual_style') != params.get('visual_style') or any(comparable.get(key) != value for key, value in params.items()) or (request.model_config_id and request.model_config_id != existing.get('model_config_id')):
                raise HTTPException(409, '该生成请求已提交，请为修改后的内容重新生成请求编号')
            return image_task_public(existing)
        validate_asset_location(request.project_id, request.folder_id)
        for asset_id in request.reference_asset_ids:
            require_reference_image(asset_id)
        config = model_for_generation(internal_registry_model('image', request.model_config_id) if request.model_config_id else internal_image_config())
        if request.size not in image_capabilities(config)['resolutions']:
            raise HTTPException(422, '当前图片模型支持的分辨率为：' + '、'.join(image_capabilities(config)['resolutions']))
        if not config.get('api_key'): raise HTTPException(422, '请先绑定图片生成模型 API Key')
        try:
            style_fields = style_task_fields(params.get('visual_style'), params['prompt'], 'image')
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        task = save('image_task', {**params, 'visual_style': params.get('visual_style'), **style_fields, 'id': tid, 'model': config['model'], 'model_config_id': config['id'], 'status': 'queued',
                                   'created_at': time.time(), 'updated_at': time.time()})
        ACTIVE_IMAGE_TASKS.add(tid)
    start_generation_worker('image_task', task, run_image_generation, (tid, config))
    return image_task_public(task)


@app.get('/api/images/generations/{tid}')
def image_generation_status(tid: str):
    with IMAGE_TASK_LOCK:
        task = IMAGE_TASK_FALLBACKS.get(tid) or get('image_task', tid)
        require_access('image_task', task)
        if task.get('deleted_at'): raise HTTPException(404, '生成记录已删除')
        if task.get('status') in ('queued', 'running') and not manual_generation.live('image_task', task):
            task = save('image_task', {**task, 'status': 'failed', 'updated_at': time.time(),
                                      'error': '服务已重启，生成任务中断。请先检查生成历史是否已有结果，再决定是否重新生成。'})
        return image_task_public(task)

def audio_task_public(task):
    fields = ('id', 'status', 'name', 'prompt', 'mode', 'model', 'model_config_id', 'project_id', 'folder_id',
              'reference_asset_ids', 'speaker', 'audio_config', 'watermark', 'created_at', 'updated_at',
              'error', 'asset', 'duration', 'original_duration', 'subtitle', 'log_id')
    return hydrate_task_assets({key: task[key] for key in fields if key in task})


def audio_task_references(params):
    references = []
    for aid in params.get('reference_asset_ids') or []:
        asset = require_live_asset(aid)
        url = str(asset.get('url') or '')
        filename = url[len('/uploads/'):]
        if not url.startswith('/uploads/') or not filename or Path(filename).name != filename or '\\' in filename:
            raise ValueError('请先将参考素材上传到本地历史文件')
        target = UPLOADS / filename
        if not target.is_file():
            raise ValueError('参考素材文件不存在，请重新上传')
        with target.open('rb') as stream:
            data = stream.read(10 * 1024 * 1024 + 1)
        if asset.get('kind') == 'audio':
            references.append(audio_reference(data, target.suffix))
        elif is_image_asset_kind(asset.get('kind')):
            references.append(image_reference(data))
        else:
            raise ValueError('音频生成只支持图片或音频作为参考素材')
    return references


def run_audio_generation(tid, config):
    with worker_scope(get('audio_task', tid)):
        return _run_audio_generation(tid, config)


def _run_audio_generation(tid, config):
    task, asset = None, None
    try:
        with AUDIO_WORKER_SLOTS:
            task = get('audio_task', tid)
            config = model_for_generation(config)
            task = save('audio_task', {**task, 'status': 'running', 'updated_at': time.time()})
            payload = audio_payload(task, config, audio_task_references(task))
            response, log_id = request_audio(config, payload)
            data, suffix, duration = decode_audio_result(response, task['audio_config']['format'])
            name = Path(task['name']).stem if Path(task['name']).suffix.lower() in ('.wav', '.mp3', '.ogg', '.pcm') else task['name']
            folder_id = task['folder_id'] if not task['folder_id'] or raw_record('asset_folder', task['folder_id']) else ''
            asset = store_uploaded_asset(data, name + suffix, task['project_id'], folder_id, source='generated', task_id=tid)
            metadata = {'duration': duration, 'audio_format': task['audio_config']['format'],
                        'description': task['prompt'], 'audio_mode': task['mode']}
            asset = asset_public(save('asset', {**get('asset', asset['id']), **metadata}))
            result = {**task, 'status': 'succeeded', 'asset': asset, 'duration': duration,
                      'log_id': log_id, 'updated_at': time.time()}
            original_duration = response.get('original_duration')
            if isinstance(original_duration, (int, float)) and 0 < original_duration <= 120:
                result['original_duration'] = original_duration
            if task['audio_config']['enable_subtitle'] and isinstance(response.get('subtitle'), dict):
                result['subtitle'] = response['subtitle']
            save('audio_task', result)
    except Exception as error:
        message = '服务端磁盘空间不足，音频暂时无法保存。请释放空间后重试。' if is_storage_full(error) else audio_error_message(error, config.get('api_key', ''))
        failed = {**(task or {'id': tid}), 'status': 'failed', 'error': message, 'updated_at': time.time()}
        if asset:
            failed['asset'] = asset
        try:
            save('audio_task', failed)
        except Exception:
            with AUDIO_TASK_LOCK:
                if len(AUDIO_TASK_FALLBACKS) >= 256:
                    AUDIO_TASK_FALLBACKS.pop(next(iter(AUDIO_TASK_FALLBACKS)))
                AUDIO_TASK_FALLBACKS[tid] = failed
    finally:
        with AUDIO_TASK_LOCK:
            ACTIVE_AUDIO_TASKS.discard(tid)


@app.post('/api/audio/generations', status_code=202)
def create_audio_generation(request: AudioGenerationRequest):
    with manual_generation.submission(request, 'audio'):
        return _create_audio_generation(request)


def _create_audio_generation(request: AudioGenerationRequest):
    params = request.model_dump(exclude={'request_id', 'model_config_id'})
    params['name'] = clean_asset_name(params['name'])
    request_id = request.request_id.strip() or str(uuid.uuid4())
    tid = 'audio-' + str(uuid.uuid5(uuid.NAMESPACE_URL, request_key(request_id)))
    with submission_task_lock(AUDIO_TASK_LOCK):
        existing = AUDIO_TASK_FALLBACKS.get(tid) or raw_record('audio_task', tid)
        if existing:
            require_access('audio_task', existing)
            if any(existing.get(key) != value for key, value in params.items()) or (request.model_config_id and request.model_config_id != existing.get('model_config_id')):
                raise HTTPException(409, '该生成请求已提交，请为修改后的内容重新生成请求编号')
            return audio_task_public(existing)
        validate_asset_location(request.project_id, request.folder_id)
        config = model_for_generation(internal_registry_model('audio', request.model_config_id or None))
        if not config.get('api_key'):
            raise HTTPException(422, '请先在模型管理中配置音频模型 API Key')
        try:
            audio_payload(params, config, audio_task_references(params))
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        task = save('audio_task', {**params, 'id': tid, 'model': config['model_id'], 'model_config_id': config['id'],
                                   'status': 'queued', 'created_at': time.time(), 'updated_at': time.time()})
        ACTIVE_AUDIO_TASKS.add(tid)
    start_generation_worker('audio_task', task, run_audio_generation, (tid, config))
    return audio_task_public(task)


@app.get('/api/audio/generations/{tid}')
def audio_generation_status(tid: str):
    with AUDIO_TASK_LOCK:
        task = AUDIO_TASK_FALLBACKS.get(tid) or get('audio_task', tid)
        require_access('audio_task', task)
        if task.get('deleted_at'): raise HTTPException(404, '生成记录已删除')
        if task.get('status') in ('queued', 'running') and not manual_generation.live('audio_task', task):
            task = save('audio_task', {**task, 'status': 'failed', 'updated_at': time.time(),
                                     'error': '服务已重启，音频生成任务中断。请先检查生成历史和服务商记录，再决定是否重新生成。'})
        return audio_task_public(task)


@app.put('/api/llm')
def update_llm_config(patch: LLMConfigUpdate):
    require_admin()
    current = internal_llm_config()
    updates = validate_model_updates('text', patch.model_dump(exclude_none=True))
    body = {**current, **updates, 'id': current['id'], 'is_default': True, 'updated_at': time.time()}
    return llm_public(save('llm_config', body))

@app.post('/api/prompts/polish')
def polish_prompt(request: PromptPolishRequest):
    """Polish a text node's prompt through the configured text model.

    The prompt is intentionally returned without writing to the project. The
    canvas applies the result to the currently edited node and saves it through
    the normal project update flow, so a failed request cannot overwrite text.
    """
    config = model_for_generation(internal_llm_config())
    if not config.get('api_key'):
        raise HTTPException(422, '请先配置文本大模型 API Key')
    source = request.text.strip()
    if not source:
        raise HTTPException(422, '请先填写文本内容')
    system = '你是视频创作平台的提示词编辑。将用户的中文创意润色成具体、清晰、可直接用于视频生成的提示词。保留原意和关键主体，补充合理的场景、镜头、光线、动作与风格细节；不要擅自加入品牌、人物身份或敏感内容。只返回润色后的提示词，不要解释、标题、引号或 Markdown。'
    user = f'请润色下面这段文本节点内容，控制在 4000 字以内：\n{source}'
    try:
        polished = clean_llm_text(llm_text(llm_json(config, [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': user},
        ])))
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(502, provider_error_message(error))
    if not polished:
        raise HTTPException(502, '大模型未返回润色结果，请重试')
    return {'text': polished[:4000], 'model': config['model_id']}

@app.get('/api/scripts/template')
def script_excel_template():
    path = ROOT / 'public' / 'templates' / 'storyboard-template.xlsx'
    if not path.is_file():
        raise HTTPException(503, '分镜模板暂不可用，请稍后重试')
    return FileResponse(path, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename='分镜脚本模板.xlsx')


@app.post('/api/scripts/import-excel')
def import_script_excel(file: UploadFile = File(...)):
    # Only return a validated preview; applying it uses the normal versioned project save.
    try:
        data = file.file.read(MAX_FILE_BYTES + 1)
        return parse_storyboard_excel(data, file.filename)
    finally:
        file.file.close()


@app.put('/api/scripts/{pid}/{nid}')
def update_script(pid: str, nid: str, request: ScriptUpdateRequest):
    body = save_script_to_project(pid, nid, request.script_data, request.revision)
    return {'script_data': next(node['script_data'] for node in body['nodes'] if node.get('id') == nid), 'project_revision': body['revision'], 'updated_at': body['updated_at']}

@app.post('/api/scripts/generate')
def generate_script(request: ScriptGenerateRequest):
    project = active_project(request.project_id)
    node = next((item for item in project.get('nodes', []) if item.get('id') == request.node_id and item.get('type') == 'script'), None)
    if not node: raise HTTPException(422, '脚本节点不存在，请先保存画布')
    config = model_for_generation(internal_llm_config())
    if not config.get('api_key'): raise HTTPException(422, '请先在脚本节点中配置大模型 API Key')
    existing = normalise_script(request.script_data or node.get('script_data') or {}, node.get('script_data', {}).get('source_node_id', ''))
    validate_script_resource_assets(existing, node.get('script_data') or {})
    source_text = request.source_text.strip()
    if not source_text:
        source_id = existing.get('source_node_id')
        source = next((item for item in project.get('nodes', []) if item.get('id') == source_id), None)
        source_text = str(source.get('settings', {}).get('prompt', '') if source else '').strip()
    if request.action == 'script' and not source_text: raise HTTPException(422, '请先连接一个文本节点并填写内容')
    system = '你是专业影视分镜编剧。只输出符合要求的 JSON，不要 Markdown、解释或额外文字。中文内容要具体、可拍摄、避免空泛形容词。每个 resources 项必须给出文件形态 kind，只能是 image、video、audio。人物、场景、道具参考图或 Logo 用 image；视频片段用 video；配音、旁白、BGM、音乐、音效用 audio。不要要求用户准备文本或脚本文件，台词和剧本保留在分镜表中。category 保留资源的具体用途分类，不能用用途取代 kind。scene_indices 用整数数组标出该资源适用的镜号，不确定时为空数组。'
    try:
        if request.action == 'script':
            user = f'''根据下面的文本创意生成一份可拍摄的分镜脚本。输出 JSON：{{"title": "", "genre": "", "duration": "", "style": "", "scenes": [{{"duration":"6s","description":"画面描述","shot_size":"景别","lighting":"光影氛围","dialogue":"对白/旁白，没有则写+","sound":"音效","camera":"运镜"}}], "resources": [{{"name":"建议上传的资源名称","kind":"image|video|audio","category":"人物/场景/道具/Logo/旁白/BGM/音效/片段等用途","reason":"为什么建议准备","required":false,"scene_indices":[1]}}]}}。kind 必须从三种值选择一个，不可输出竖线组合。生成 4-12 个镜头，镜头时长合计与内容相符。文本创意：\n{source_text}'''
            parsed = parse_llm_json(llm_json(config, [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]))
            result = normalise_script(parsed, existing.get('source_node_id', ''))
            result['resources'] = merge_preparation_resources(existing.get('resources', []), result.get('resources', []))
        elif request.action == 'resources':
            user = f'''分析这份分镜脚本需要哪些可上传资源。只输出 JSON：{{"resources":[{{"name":"资源名称","kind":"image|video|audio","category":"人物/场景/道具/Logo/旁白/BGM/音效/片段等用途","reason":"服务哪些镜头以及用途","required":false,"scene_indices":[1]}}]}}。所有资源都只是可选建议，有更好，没有也不应阻塞脚本完成或展开分镜。kind 必须从三种值选择一个，不可输出竖线组合。按文件形态分类，旁白和 BGM 应请求音频文件；已写出的台词文案和脚本不作为待上传资源。不要重复同类资源，最多 20 项。脚本：\n{json.dumps(existing, ensure_ascii=False)}'''
            parsed = parse_llm_json(llm_json(config, [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]))
            incoming_resources = parsed.get('resources', []) if isinstance(parsed, dict) else []
            result = normalise_script({**existing, 'resources': merge_preparation_resources(existing.get('resources', []), incoming_resources)}, existing.get('source_node_id', ''))
        else:
            scene_scope = f'只处理第 {request.scene_index} 个镜头，并返回该镜头。' if request.scene_index else '处理全部镜头。'
            user = f'''为下面分镜生成视频模型可直接使用的提示词。{scene_scope}只输出 JSON：{{"prompts":[{{"scene_index":1,"storyboard_prompt":"主体、环境、构图、景别、光影和风格","motion_prompt":"人物或物体动作、镜头运动、速度和转场"}}]}}。storyboard_prompt 和 motion_prompt 必须使用简体中文，禁止将整段提示词翻译成英文，API、BIM 等专业术语和专有名称可以保留。每个镜头的两段中文提示词合计不超过 4000 字（含合并时的分隔符）。不要输出 final_prompt，应用会按用户可见的这两项原文合并最终提示词。提示词要具体、可拍摄，保留人物和场景连续性。脚本：\n{json.dumps(existing, ensure_ascii=False)}'''
            parsed = parse_llm_json(llm_json(config, [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]))
            prompts = parsed.get('prompts', []) if isinstance(parsed, dict) else []
            prompt_map = {int(item.get('scene_index', 0)): item for item in prompts if isinstance(item, dict) and str(item.get('scene_index', '')).isdigit()}
            result = normalise_script(existing, existing.get('source_node_id', ''))
            for scene in result['scenes']:
                if request.scene_index and scene['index'] != request.scene_index:
                    continue
                item = prompt_map.get(scene['index'])
                if not item:
                    raise RuntimeError(f'大模型未返回第 {scene["index"]} 镜的提示词，原有内容已保留，请重新合成')
                storyboard = str(item.get('storyboard_prompt') or '').strip()
                motion = str(item.get('motion_prompt') or '').strip()
                final = script_scene_prompt({'storyboard_prompt': storyboard, 'motion_prompt': motion})
                if not final:
                    raise RuntimeError(f'大模型返回的第 {scene["index"]} 镜提示词为空，原有内容已保留，请重新合成')
                if not re.search(r'[\u3400-\u9fff]', final):
                    raise RuntimeError(f'大模型返回的第 {scene["index"]} 镜提示词不是中文，原有内容已保留，请重新合成')
                if len(final) > 4000:
                    raise RuntimeError(f'大模型返回的第 {scene["index"]} 镜提示词超过 4000 字，原有内容已保留，请重新合成')
                scene['storyboard_prompt'], scene['motion_prompt'], scene['final_prompt'] = storyboard, motion, final
    except HTTPException: raise
    except Exception as error:
        raise HTTPException(502, provider_error_message(error))
    body = save_script_to_project(request.project_id, request.node_id, result, request.revision)
    saved = next(node['script_data'] for node in body['nodes'] if node.get('id') == request.node_id)
    return {'script_data': saved, 'project_revision': body['revision'], 'updated_at': body['updated_at'], 'model': config['model_id']}

@app.get('/api/projects')
def projects(trashed: bool = False):
    return sorted([p for p in listing('project') if not p.get('permanently_deleted') and bool(p.get('deleted_at')) == trashed], key=lambda p: p.get('updated_at', p.get('created_at', 0)), reverse=True)


@app.get('/api/project-folders')
def project_folders():
    return sorted(listing('project_folder'), key=lambda folder: folder.get('created_at', 0), reverse=True)


def project_folder_name(name, excluded=''):
    name = name.strip()
    if not name: raise HTTPException(422, '文件夹名称不能为空')
    if any(folder['id'] != excluded and folder['name'].casefold() == name.casefold() for folder in listing('project_folder')):
        raise HTTPException(409, '已存在同名项目文件夹')
    return name


@app.post('/api/project-folders', status_code=201)
def create_project_folder(body: AssetFolder):
    return save('project_folder', {'id': str(uuid.uuid4()), 'name': project_folder_name(body.name), 'created_at': time.time()})


@app.patch('/api/project-folders/{fid}')
def rename_project_folder(fid: str, body: AssetFolder):
    return save('project_folder', {**get('project_folder', fid), 'name': project_folder_name(body.name, fid)})


@app.delete('/api/project-folders/{fid}', status_code=204)
def delete_project_folder(fid: str):
    get('project_folder', fid)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for rid, raw in db.execute("SELECT id,body FROM records WHERE kind='project'").fetchall():
            project = json.loads(raw)
            if project.get('folder_id') == fid and can_access('project', project):
                project.update(folder_id='', updated_at=time.time(), revision=project.get('revision', 0) + 1)
                db.execute("UPDATE records SET body=? WHERE kind='project' AND id=?", (json.dumps(project, ensure_ascii=False), rid))
        db.execute("DELETE FROM records WHERE kind='project_folder' AND id=?", (fid,))


@app.patch('/api/projects/{pid}/metadata')
def update_project_metadata(pid: str, body: ProjectMetadata):
    if body.folder_id:
        get('project_folder', body.folder_id)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT body FROM records WHERE kind='project' AND id=?", (pid,)).fetchone()
        if not row: raise HTTPException(404, '项目不存在')
        project = require_access('project', json.loads(row[0]))
        if project.get('deleted_at'): raise HTTPException(409, '请先从回收站恢复项目')
        if body.revision is not None and body.revision != project.get('revision', 0):
            raise HTTPException(409, '项目已在其他页面更新，请刷新后重试')
        if body.name is not None:
            if not body.name.strip(): raise HTTPException(422, '项目名称不能为空')
            project['name'] = body.name.strip()
        if body.folder_id is not None:
            if body.folder_id and not db.execute("SELECT 1 FROM records WHERE kind='project_folder' AND id=?", (body.folder_id,)).fetchone():
                raise HTTPException(422, '项目文件夹不存在')
            project['folder_id'] = body.folder_id
        if body.pinned is not None:
            project['pinned'] = body.pinned
        project.update(updated_at=time.time(), revision=project.get('revision', 0) + 1)
        db.execute("UPDATE records SET body=? WHERE kind='project' AND id=?", (json.dumps(project, ensure_ascii=False), pid))
    return project

def active_project(pid):
    record = get('project', pid)
    if record.get('deleted_at'): raise HTTPException(409, '项目在回收站中，请先恢复')
    return record


def creation_context(cid):
    """Internal production data may belong to a conversation or a canvas."""
    conversation=raw_record('conversation',cid)
    if conversation and conversation.get('deleted_at'):raise HTTPException(404,'对话已删除')
    return conversation or active_project(cid)


def update_creation_context(cid, p: Project):
    if not raw_record('conversation',cid):return update_project(cid,p)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute("SELECT body FROM records WHERE kind='conversation' AND id=?",(cid,)).fetchone()
        if not row:raise HTTPException(404,'对话不存在')
        old=require_access('conversation', json.loads(row[0]))
        if old.get('deleted_at'):raise HTTPException(404,'对话已删除')
        if p.revision is not None and p.revision!=old.get('revision',0):raise HTTPException(409,'对话已更新，请重试')
        value={**old,**p.model_dump(exclude={'revision'}),'title':p.name,'updated_at':time.time(),'revision':old.get('revision',0)+1}
        db.execute("UPDATE records SET body=? WHERE kind='conversation' AND id=?",(json.dumps(value,ensure_ascii=False),cid))
    return value

def validate_graph(p, previous_project=None):
    validate_private_references(p.model_dump(), record_owner(previous_project or {}) or None)
    if 'folder_id' not in p.model_fields_set:
        p.folder_id = (previous_project or {}).get('folder_id', '')
    if p.folder_id and not raw_record('project_folder', p.folder_id):
        raise HTTPException(422, '项目文件夹不存在')
    ids = [n.get('id') for n in p.nodes]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        raise HTTPException(422, '节点 ID 不能为空或重复')
    # Older clients and Agent graph edits must not silently erase user groups.
    groups = p.groups if 'groups' in p.model_fields_set else (previous_project or {}).get('groups', [])
    p.groups = normalize_groups(groups, set(ids))
    if any(e.get('from') not in ids or e.get('to') not in ids or e.get('from') == e.get('to') for e in p.edges):
        raise HTTPException(422, '连线必须连接两个有效节点')
    script_nodes = {node.get('id'): node for node in p.nodes if node.get('type') == 'script'}
    for node in p.nodes:
        if node.get('type') == 'script' and isinstance(node.get('script_data'), dict):
            normalised = normalise_script(node['script_data'])
            previous = next((old.get('script_data') or {} for old in (previous_project or {}).get('nodes', []) if old.get('id') == node.get('id')), {})
            validate_script_resource_assets(normalised, previous)
            node['script_data'] = normalised
    for node in p.nodes:
        if node.get('type') != 'video':
            continue
        settings = node.get('settings') if isinstance(node.get('settings'), dict) else {}
        source_id = node.get('scriptSourceId') or node.get('script_source_id') or settings.get('script_source_id')
        if not source_id:
            continue
        previous_node = next((old for old in (previous_project or {}).get('nodes', [])
                              if old.get('id') == node.get('id') and old.get('type') == 'video'), None)
        previous_settings = (previous_node or {}).get('settings') or {}
        previous_source_id = ((previous_node or {}).get('scriptSourceId') or (previous_node or {}).get('script_source_id')
                              or previous_settings.get('script_source_id'))
        # Existing expanded videos remain saveable while the user revises their
        # script.  Only a newly created/reassigned expansion must be complete.
        if previous_source_id == source_id:
            continue
        source = script_nodes.get(source_id)
        if not source:
            raise HTTPException(422, '视频节点引用的脚本节点不存在，请重新连接')
        status = script_completion_status(source.get('script_data') or {})
        if not status['complete']:
            missing = []
            if not status['script']:
                missing.append('确认镜头')
            if not status['resources']:
                missing.append('准备必需资源')
            if not status['prompts']:
                missing.append('合成提示词')
            raise HTTPException(422, f"脚本节点尚未完成三个步骤（{ '、'.join(missing) }），暂不能展开分镜视频节点")
        scene_index = settings.get('script_scene_index')
        if scene_index is not None:
            try:
                numeric_index = int(scene_index)
            except (ValueError, TypeError):
                raise HTTPException(422, '视频节点引用的脚本镜头无效，请重新展开分镜')
            if not any(int(scene.get('index', 0)) == numeric_index for scene in (source.get('script_data') or {}).get('scenes', [])):
                raise HTTPException(422, '视频节点引用的脚本镜头不存在，请重新展开分镜')

@app.post('/api/projects', status_code=201)
def create_project(p: Project):
    validate_graph(p)
    return save('project', {**p.model_dump(exclude={'revision'}), 'id': str(uuid.uuid4()), 'created_at': time.time(), 'updated_at': time.time(), 'revision': 1})

@app.get('/api/projects/{pid}')
def project(pid: str):
    graph = active_project(pid)
    service = getattr(app.state, 'agent_service', None)
    if service:
        for rid in {n.get('agent_run_id') for n in graph['nodes']} - {None}:
            run = raw_record('agent_run', rid)
            if run and run.get('project_id') == pid:
                graph = service.repair_canvas(rid) or graph
    return graph


@app.get('/api/projects/{pid}/info')
def project_info(pid: str):
    record = active_project(pid)
    counts = dict.fromkeys(('total', 'image', 'video', 'audio', 'text', 'timeline', 'subject', 'other'), 0)
    assets = {asset['id']: asset for asset in listing('asset')}
    categories = {'prompt': 'text', 'text': 'text', 'script': 'text',
                  'image': 'image', 'prop': 'image', 'video': 'video', 'video-asset': 'video',
                  'audio': 'audio', 'timeline': 'timeline', 'character': 'subject', 'subject': 'subject'}
    for node in record.get('nodes', []):
        kind = node.get('type', '')
        if kind == 'asset':
            kind = assets.get(node.get('assetId'), {}).get('kind', 'image')
        counts[categories.get(kind, 'other')] += 1
        counts['total'] += 1
    return {key: record.get(key) for key in ('id', 'name', 'created_at', 'updated_at')} | {
        **record_creator(record, legacy_owner=True), 'node_counts': counts}

@app.put('/api/projects/{pid}')
def update_project(pid: str, p: Project):
    validate_graph(p, raw_record('project', pid))
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT body FROM records WHERE kind=? AND id=?', ('project', pid)).fetchone()
        if not row: raise HTTPException(404, '项目不存在')
        old = require_access('project', json.loads(row[0]))
        if old.get('deleted_at'): raise HTTPException(409, '项目已移入回收站')
        if p.revision is not None and p.revision != old.get('revision', 0):
            raise HTTPException(409, '项目已在其他页面更新，请先保存本地草稿副本，再重新打开项目')
        body = {**old, **p.model_dump(exclude={'revision'}), 'updated_at': time.time(), 'revision': old.get('revision', 0) + 1}
        db.execute('UPDATE records SET body=? WHERE kind=? AND id=?', (json.dumps(body, ensure_ascii=False), 'project', pid))
    return body

@app.post('/api/projects/{pid}/duplicate', status_code=201)
def duplicate_project(pid: str):
    original = active_project(pid)
    payload = {k: original[k] for k in ('name', 'nodes', 'edges', 'groups', 'selected_asset_ids', 'viewport', 'folder_id') if k in original}
    payload['name'] = original['name'][:75] + ' 副本'
    return create_project(Project(**payload))

@app.post('/api/projects/{pid}/restore')
def restore_project(pid: str):
    record = get('project', pid)
    if record.get('permanently_deleted'):raise HTTPException(404,'项目已永久删除')
    record.pop('deleted_at', None)
    return save('project', {**record, 'updated_at': time.time()})

def purge_project(pid):
    get('project', pid)
    with connect() as db:
        db.execute('DELETE FROM records WHERE kind=? AND id=?', ('project', pid))
        for kind in ('task', 'image_task', 'audio_task'):
            rows = db.execute('SELECT id, body FROM records WHERE kind=?', (kind,)).fetchall()
            for rid, body in rows:
                try:
                    item = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if item.get('project_id') == pid and can_access(kind, item):
                    db.execute('DELETE FROM records WHERE kind=? AND id=?', (kind, rid))

@app.delete('/api/projects/{pid}', status_code=204)
def delete_project(pid: str):
    record = get('project', pid)
    if record.get('deleted_at'):
        purge_project(pid)
        return
    save('project', {**record, 'deleted_at': time.time()})

def require_live_asset(aid):
    asset = get('asset', aid)
    if asset.get('deleted_at'): raise HTTPException(404, '素材已删除')
    return asset


def require_image_asset(aid):
    asset = require_live_asset(aid)
    if asset.get('kind', 'image') != 'image': raise HTTPException(422, '角色参考素材必须是图片')
    return asset


def validate_asset_location(project_id='', folder_id=''):
    if project_id: creation_context(project_id)
    if folder_id: get('asset_folder', folder_id)


def clean_asset_name(value):
    name = str(value or '').replace('\\', '/').rsplit('/', 1)[-1].strip()
    if not name or name in ('.', '..'): raise HTTPException(422, '素材名称不能为空')
    return name[:160]


def clean_asset_tags(values):
    tags = []
    for value in values or []:
        value = str(value).strip()
        if len(value) > 40: raise HTTPException(422, '每个标签最多 40 个字符')
        if value and value not in tags: tags.append(value)
    return tags


def asset_public(asset, projects=None, characters=None, all_assets=None, conversations=None):
    projects = listing('project') if projects is None else projects
    characters = listing('character') if characters is None else characters
    references = [{'id': p['id'], 'name': p.get('name', '未命名项目'), 'deleted': bool(p.get('deleted_at'))}
                  for p in projects if project_asset_references(p, asset['id'], characters)]
    conversations=listing('conversation') if conversations is None else conversations
    conversations=[c for c in conversations if not c.get('deleted_at')]
    conversation_refs=[{'id':c['id'],'name':c.get('title') or c.get('name','未命名对话')} for c in conversations
                       if project_asset_references(c,asset['id'],characters) or asset['id'] in (c.get('settings') or {}).get('reference_asset_ids',[])]
    roles = [{'id': c['id'], 'name': c.get('name', '未命名角色')} for c in characters if c.get('asset_id') == asset['id']]
    all_assets = listing('asset') if all_assets is None else all_assets
    snapshots = [{'id': a['id'], 'name': a.get('name', '未命名脚本')} for a in all_assets
                 if a['id'] != asset['id'] and not a.get('deleted_at') and any(
                     r.get('asset_id') == asset['id'] for r in (a.get('script_data') or {}).get('resources', []) if isinstance(r, dict))]
    return {'id': asset['id'], 'kind': 'image' if asset.get('kind') == 'prop' else asset.get('kind', 'image'), 'name': asset.get('name', '未命名素材'),
            'url': asset.get('url', ''), 'content': asset.get('content', ''), 'script_data': asset.get('script_data', {}),
            'tags': asset.get('tags', []), 'folder_id': asset.get('folder_id', ''), 'description': asset.get('description', ''),
            'in_library': bool(asset.get('in_library')), 'library_saved_at': asset.get('library_saved_at'),
            'file_status': asset.get('file_status', 'cached'), 'file_error': asset.get('file_error', ''),
            'favorite': bool(asset.get('favorite')), 'source': asset.get('source', 'uploaded'),
            'project_id': asset.get('project_id', ''), 'size_bytes': asset.get('size_bytes', 0),
            'mime_type': asset.get('mime_type') or MIMES.get(Path(asset.get('url', '')).suffix.lower(), ''),
            'created_at': asset.get('created_at', 0), 'updated_at': asset.get('updated_at', asset.get('created_at', 0)),
            'task_id': asset.get('task_id', ''), 'usage_count': len(references) + len(conversation_refs) + len(roles) + len(snapshots),
            'usage_projects': references, 'usage_conversations': conversation_refs, 'usage_characters': roles, 'usage_assets': snapshots,
            **{key: asset[key] for key in CREATOR_FIELDS if key in asset},
            **({key: asset[key] for key in ('duration', 'audio_format', 'audio_mode') if key in asset} if asset.get('kind') in ('audio','video') else {})}


@app.get('/api/assets')
def assets(collection: Literal['history', 'library'] = 'history'):
    for task in listing('task'):
        if task.get('status') == 'done' and task.get('video_url') and not task.get('deleted_at'):
            refresh_video_task(task['id'])
    projects, characters = listing('project'), listing('character')
    all_assets = listing('asset')
    conversations=listing('conversation')
    return [asset_public(a, projects, characters, all_assets, conversations) for a in all_assets if not a.get('deleted_at') and (collection == 'history' or a.get('in_library')) and a.get('kind', 'image') in ('image','prop','video','audio')]


@app.get('/api/assets/{aid}')
def asset_details(aid: str):
    asset = require_live_asset(aid)
    result = asset_public(asset)
    kind = asset.get('kind', 'image')
    task = raw_record({'video': 'task', 'audio': 'audio_task'}.get(kind, 'image_task'), asset.get('task_id', '')) or {}
    params = task.get('params') if kind == 'video' else task
    params = params or {}
    config = raw_record({'video': 'model_config', 'audio': 'audio_model_config'}.get(kind, 'image_model_config'), params.get('model_config_id', '')) or {}
    # A shared canvas owner is not necessarily the person who made this asset.
    result.update(record_creator(asset) or record_creator(task))
    result.setdefault('creator_name', '')
    result['generation'] = None
    if task:
        references = []
        for ref_id in params.get('asset_ids' if kind == 'video' else 'reference_asset_ids', []):
            # Admin-created output may cite private inputs from another owner.
            # Show only references readable by the viewer, without failing the
            # output's entire details page or exposing those private inputs.
            with connect() as db:
                ref = record_store.fetch(db.sa, 'asset', ref_id)
            if ref and not ref.get('deleted_at'):
                references.append({key: ref.get(key, '') for key in ('id', 'kind', 'name', 'url')})
        result['generation'] = {
            'prompt': params.get('prompt', ''), 'model_name': params.get('model_name') or config.get('name') or '',
            'model_id': params.get('model', ''), 'references': references,
            **{key: params[key] for key in ('size', 'aspect_ratio', 'resolution', 'duration', 'count') if key in params},
        }
    return result


def store_uploaded_asset(data, filename, project_id='', folder_id='', source='uploaded', task_id='', asset_kind=''):
    suffix, kind, _ = file_type(filename)
    kind = validate_asset_kind(asset_kind, kind)
    original_data = data
    data, suffix, extras = validate_bytes(data, suffix)
    if source == 'generated' and is_image_asset_kind(kind):
        # Keep the provider's resolution after validation; uploaded references may be resized.
        data = original_data
        with Image.open(io.BytesIO(data)) as image:
            suffix = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}[image.format]
    aid = str(uuid.uuid4())
    identity = current_creator()
    if task_id:
        task = raw_record({'video': 'task', 'audio': 'audio_task'}.get(kind, 'image_task'), task_id) or {}
        # Background workers and later saves use the original task's author.
        # Unknown legacy authors must not become the user who clicked "save".
        task_creator = record_creator(task)
        identity = {key: task_creator.get(key, '') for key in CREATOR_FIELDS}
    target = UPLOADS / (aid + suffix)
    try:
        target.write_bytes(data)
        asset = save('asset', {'id': aid, 'kind': kind, 'name': clean_asset_name(filename),
                              'url': '/uploads/' + target.name, 'source': source, 'project_id': project_id,
                              'folder_id': folder_id, 'size_bytes': len(data), 'mime_type': MIMES[suffix],
                              'created_at': time.time(), 'task_id': task_id, **extras, **identity})
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return asset_public(asset)


@app.post('/api/assets', status_code=201)
async def upload(file: UploadFile = File(...), project_id: str = Form(''), folder_id: str = Form(''), kind: str = Form('')):
    validate_asset_location(project_id, folder_id)
    _, detected_kind, limit = file_type(file.filename)
    requested_kind = validate_asset_kind(kind, detected_kind)
    data = await file.read(limit + 1)
    return store_uploaded_asset(data, file.filename, project_id, folder_id, asset_kind=requested_kind)


@app.post('/api/assets/content', status_code=201)
def create_content_asset(body: AssetContent):
    raise HTTPException(410, '资产库仅支持图片、视频和音频，文本与脚本请保存在画布或 Agent 创作记录中')


@app.patch('/api/assets/{aid}')
def update_asset(aid: str, body: AssetPatch):
    asset = require_live_asset(aid)
    patch = body.model_dump(exclude_none=True)
    if 'name' in patch: patch['name'] = clean_asset_name(patch['name'])
    if 'tags' in patch: patch['tags'] = clean_asset_tags(patch['tags'])
    if 'folder_id' in patch: validate_asset_location(folder_id=patch['folder_id'])
    return asset_public(save('asset', {**asset, **patch, 'updated_at': time.time()}))


@app.delete('/api/assets/{aid}', status_code=204)
def delete_asset(aid: str):
    asset = require_live_asset(aid)
    usage = asset_public(asset)
    if usage['usage_projects']:
        raise HTTPException(409, f'素材被项目「{usage["usage_projects"][0]["name"]}」引用，请先移除引用（含回收站项目）')
    if usage['usage_conversations']:
        raise HTTPException(409, f'素材被对话「{usage["usage_conversations"][0]["name"]}」引用，请先移除引用')
    if usage['usage_characters']:
        raise HTTPException(409, '素材被角色引用，请先修改角色参考图')
    for item in listing('asset'):
        if item.get('deleted_at') or item.get('id') == aid: continue
        if any(r.get('asset_id') == aid for r in (item.get('script_data') or {}).get('resources', [])):
            raise HTTPException(409, f'素材被脚本资产「{item.get("name", "未命名脚本") }」引用，请先移除脚本资产')
    save('asset', {**asset, 'deleted_at': time.time()})


@app.get('/api/asset-folders')
def asset_folders():
    all_assets = listing('asset')
    return [{**folder, 'count': sum(1 for a in all_assets if not a.get('deleted_at') and a.get('folder_id') == folder['id'])}
            for folder in listing('asset_folder')]


def validated_folder_name(name, excluded_id=''):
    name = name.strip()
    if not name: raise HTTPException(422, '分组名称不能为空')
    if any(f['name'].casefold() == name.casefold() and f['id'] != excluded_id for f in listing('asset_folder')):
        raise HTTPException(409, '已存在同名分组')
    return name


@app.post('/api/asset-folders', status_code=201)
def create_asset_folder(body: AssetFolder):
    return save('asset_folder', {'id': str(uuid.uuid4()), 'name': validated_folder_name(body.name), 'created_at': time.time(), 'count': 0})


@app.patch('/api/asset-folders/{fid}')
def update_asset_folder(fid: str, body: AssetFolder):
    return save('asset_folder', {**get('asset_folder', fid), 'name': validated_folder_name(body.name, fid)})


@app.delete('/api/asset-folders/{fid}', status_code=204)
def delete_asset_folder(fid: str):
    get('asset_folder', fid)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for (raw,) in db.execute('SELECT body FROM records WHERE kind=?', ('asset',)).fetchall():
            asset = json.loads(raw)
            if asset.get('folder_id') == fid and can_access('asset', asset):
                asset.update(folder_id='', updated_at=time.time())
                db.execute('UPDATE records SET body=? WHERE kind=? AND id=?', (json.dumps(asset, ensure_ascii=False), 'asset', asset['id']))
        db.execute('DELETE FROM records WHERE kind=? AND id=?', ('asset_folder', fid))


_task_asset_lock = threading.Lock()


@app.post('/api/tasks/{tid}/history-asset')
def video_history_asset(tid: str):
    asset = ensure_video_asset(tid)
    if not asset or asset.get('deleted_at'):
        raise HTTPException(422, '视频尚未生成完成，或生成记录已删除')
    return asset_public(asset)


@app.post('/api/assets/{aid}/library')
def save_asset_to_library(aid: str):
    asset = require_live_asset(aid)
    if asset.get('kind') == 'video' and asset.get('task_id') and (asset.get('file_status') != 'cached' or not asset.get('url', '').startswith('/uploads/')):
        finalize_video_task(asset['task_id'])
        asset = require_live_asset(aid)
        if asset.get('file_status') != 'cached' or not asset.get('url', '').startswith('/uploads/'):
            raise HTTPException(502, '视频已生成，但文件下载失败，尚未加入资产库。请稍后重试。' + asset.get('file_error', ''))
    with _task_asset_lock:
        asset = require_live_asset(aid)
        if not asset.get('in_library'):
            asset = save('asset', {**asset, 'in_library': True, 'library_saved_at': time.time(), 'updated_at': time.time()})
        if asset.get('kind') == 'video' and asset.get('task_id'):
            update_task(asset['task_id'], asset_save_status='saved', auto_save_video=False,
                status_message='生成完成，已保存至资产库')
    return asset_public(asset)


@app.delete('/api/assets/{aid}/library')
def remove_asset_from_library(aid: str):
    """Remove library membership while keeping the history record and references."""
    with _task_asset_lock:
        asset = require_live_asset(aid)
        if asset.get('in_library'):
            asset = save('asset', {**asset, 'in_library': False, 'library_saved_at': None, 'updated_at': time.time()})
        if asset.get('kind') == 'video' and asset.get('task_id'):
            update_task(asset['task_id'], asset_save_status='history', auto_save_video=False,
                status_message='生成完成，已加入生成历史')
    return asset_public(asset)


@app.post('/api/assets/{aid}/retry-file')
def retry_asset_file(aid: str):
    asset = require_live_asset(aid)
    if asset.get('kind') != 'video' or not asset.get('task_id'):
        raise HTTPException(422, '此记录没有可重试的视频下载')
    finalize_video_task(asset['task_id'])
    return asset_public(require_live_asset(aid))


@app.post('/api/assets/from-task', status_code=201)
def save_task_asset(body: AssetFromTask):
    validate_asset_location(folder_id=body.folder_id)
    asset = video_history_asset(body.task_id)
    if body.name or body.folder_id:
        with _task_asset_lock:
            current = require_live_asset(asset['id'])
            save('asset', {**current, 'name': clean_asset_name(body.name) if body.name else current['name'],
                'folder_id': body.folder_id or current.get('folder_id', '')})
    return save_asset_to_library(asset['id'])

@app.get('/api/tasks')
def tasks(project_id: str | None = None):
    return [refresh_video_task(t['id']) for t in listing('task') if not t.get('deleted_at') and (not project_id or t['project_id'] == project_id)]


@app.post('/api/tasks/{tid}/cache-video')
def cache_task_video(tid: str):
    """Repair an older task whose provider URL has not expired yet."""
    task = normalize_task_video(get('task', tid))
    if task.get('status') != 'done' or not task.get('video_url'):
        raise HTTPException(422, '只有生成成功的视频才能缓存')
    finalize_video_task(tid)
    current = get('task', tid)
    if current.get('video_cache_status') != 'cached':
        raise HTTPException(502, '视频已生成，文件下载失败，可稍后重试下载')
    return current

def script_scene_image_resources(script, scene_index=None):
    data = normalise_script(script.get('script_data') or {})
    scene = next((scene for scene in data['scenes'] if str(scene['index']) == str(scene_index)), None)
    scene = scene or next(iter(data['scenes']), None)
    if not scene: return []
    selected_ids = scene.get('resource_ids') or []
    return [resource for resource in data['resources']
            if resource['kind'] in ('image', 'audio', 'video') and resource['asset_id'] and
            (resource['id'] in selected_ids or scene['index'] in resource['scene_indices'] or
             (not selected_ids and not resource['scene_indices']))]


def video_graph_asset_ids(project, video):
    """Resolve references from the saved graph, including old inspector-only data."""
    node_by_id = {node.get('id'): node for node in project.get('nodes', [])}
    settings = video.get('settings') or {}
    asset_ids = []

    def add_asset(asset_id, label):
        if not isinstance(asset_id, str) or not asset_id:
            raise HTTPException(422, f'「{label}」尚未绑定参考图片，请先添加素材')
        if asset_id in asset_ids:
            return
        asset = raw_record('asset', asset_id)
        if not asset or asset.get('deleted_at'):
            raise HTTPException(422, f'「{label}」引用的素材不存在，请重新添加')
        if not is_image_asset_kind(asset.get('kind', 'image')) and asset.get('kind') not in ('audio', 'video'):
            raise HTTPException(422, f'「{label}」是{ {"prop": "道具", "video": "视频", "audio": "音频", "text": "文本", "script": "脚本"}.get(asset.get("kind"), "非图片")}素材；当前视频模型仅支持图片参考，请断开此素材连接后生成')
        file_name = Path(str(asset.get('url') or '')).name
        if not file_name or not (UPLOADS / file_name).is_file():
            raise HTTPException(422, f'「{label}」的素材文件已丢失，请重新上传')
        asset_ids.append(asset_id)

    for edge in project.get('edges', []):
        if edge.get('to') != video.get('id') or edge.get('relation') == 'agent-flow':
            continue
        source = node_by_id.get(edge.get('from'))
        if not source:
            raise HTTPException(422, '视频节点的上游节点不存在，请移除失效连线')
        if source.get('type') == 'video':
            task_id = source.get('referenceTaskId')
            latest = sorted([task for task in listing('task') if task.get('project_id') == project.get('id')
                             and task.get('params', {}).get('node_id') == source.get('id')],
                            key=lambda task: task.get('created_at', 0), reverse=True)
            if not task_id or not latest or latest[0]['id'] != task_id or latest[0].get('status') != 'done':
                raise HTTPException(422, '上游视频尚未生成完成或结果已更新，请重新连接该视频节点')
            reference = raw_record('asset', source.get('referenceAssetId', '')) or {}
            if reference.get('task_id') != task_id or reference.get('kind') != 'video':
                raise HTTPException(422, '上游视频参考已失效，请重新连接')
            add_asset(reference['id'], source.get('title') or '上游视频')
            continue
        if source.get('type') == 'script':
            text_sources = [node_by_id.get(e.get('from')) for e in project.get('edges', []) if e.get('to') == video.get('id') and e.get('relation') != 'agent-flow']
            text_sources = [node for node in text_sources if node and node.get('type') in ('prompt', 'script')]
            selected_source = next((node for node in text_sources if node.get('id') == settings.get('prompt_source_id')), None) or next(iter(text_sources), None)
            if selected_source and selected_source.get('id') == source.get('id'):
                for resource in script_scene_image_resources(source, settings.get('script_scene_index')):
                    add_asset(resource['asset_id'], resource['name'])
            continue
        if source.get('type') not in ('asset', 'prop', 'image', 'character', 'audio', 'video-asset'):
            continue
        asset_id = source.get('assetId')
        if not asset_id and source.get('type') == 'character':
            character_id = source.get('characterId')
            character = raw_record('character', character_id) if character_id else None
            asset_id = (character or {}).get('asset_id')
        add_asset(asset_id, source.get('title') or source.get('characterName') or '上游素材节点')

    for asset_id in settings.get('asset_ids') or []:
        add_asset(asset_id, '参考素材')
    owners = [video] + [node_by_id.get(edge.get('from')) for edge in project.get('edges', []) if edge.get('to') == video.get('id') and edge.get('relation') != 'agent-flow']
    for owner in owners:
        if not owner or owner.get('type') not in ('video', 'prompt'):
            continue
        own_settings = owner.get('settings') or {}
        for mention in own_settings.get('mentions') or []:
            if mention.get('asset_id') and mention.get('kind') not in ('text', 'script') and mention.get('token') and mention['token'] in str(own_settings.get('prompt') or ''):
                add_asset(mention['asset_id'], owner.get('title') or '提示词引用')
    order = settings.get('reference_order') or []
    rank = {asset_id: order.index(asset_id) for asset_id in asset_ids if asset_id in order}
    asset_ids.sort(key=lambda asset_id: rank.get(asset_id, len(order)))
    return asset_ids

@app.post('/api/tasks', status_code=202)
def create_task(p: Task):
    with manual_generation.submission(p, 'video'):
        return submit_video_task(p)


VIDEO_SUBMIT_LOCK = threading.RLock()


def video_request_digest(request):
    # Preserve old idempotency keys when no style was selected.
    values = request.model_dump()
    if values.get('visual_style') is None:
        values.pop('visual_style', None)
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def submit_video_task(p: Task, project_snapshot=None):
    # A saved request ID reconciles an interrupted Agent submission without
    # creating a second paid task. Freeze graph inputs for this one submission.
    with submission_task_lock(VIDEO_SUBMIT_LOCK):
        submission_owner = record_owner(project_snapshot or creation_context(p.project_id)) if current_principal() else None
        if p.request_id:
            previous = next((t for t in listing('task') if t.get('params', {}).get('request_id') == p.request_id
                             and (submission_owner is None or record_owner(t) == submission_owner)), None)
            if previous:
                if previous.get('project_id') != p.project_id or previous['params'].get('node_id') != p.node_id:
                    raise HTTPException(409, '该视频请求编号已用于其他节点')
                digest = video_request_digest(p)
                if previous['params'].get('client_input_digest') != digest:
                    raise HTTPException(409, '同一视频请求编号不能使用不同参数')
                return normalize_task_video(previous)
        return _submit_video_task(p, project_snapshot)


def _submit_video_task(p: Task, project_snapshot=None):
    request_digest = video_request_digest(p)
    project = project_snapshot or creation_context(p.project_id)
    video = next((n for n in project['nodes'] if n.get('id') == p.node_id and n.get('type', 'video' if 'video' in n.get('id', '') else '') == 'video'), None)
    if not video: raise HTTPException(422, '请先保存视频生成节点')
    # A video expanded from a storyboard must use the current shot prompt and
    # duration.  Aspect ratio and resolution stay on the video node.
    video_settings = video.get('settings') if isinstance(video.get('settings'), dict) else {}
    script_source_id = (video.get('scriptSourceId') or video.get('script_source_id')
                        or video_settings.get('script_source_id'))
    script_source = next((node for node in project.get('nodes', [])
                          if node.get('id') == script_source_id and node.get('type') == 'script'), None)
    if script_source:
        inherited = script_video_settings(script_source.get('script_data') or {}, video_settings.get('script_scene_index'))
        p = p.model_copy(update={'prompt': inherited['prompt'], 'duration': inherited['duration']})
    # model_copy does not rerun Task's validators. Validate the canonical
    # prompt after inheritance, including a script whose prompt was cleared.
    if not p.prompt.strip():
        raise HTTPException(422, '脚本分镜提示词为空，请先在脚本节点合成提示词' if script_source else '请输入提示词')
    if len(p.prompt) > 4000:
        raise HTTPException(422, '提示词超过 4000 字，请先在脚本节点中精简后再生成视频' if script_source else '提示词不能超过 4000 字')
    if project.get('creation_type') == 'conversation':
        # Each conversational turn owns its current inputs, including frame order.
        input_video = {**video, 'settings': {'asset_ids': p.asset_ids}}
        checked_ids = video_graph_asset_ids({**project, 'nodes': [input_video], 'edges': []}, input_video)
        asset_ids = list(p.asset_ids) if p.mode == 'first-last' else checked_ids
    else:
        asset_ids = video_graph_asset_ids(project, video)
        if p.mode == 'first-last':
            if len(p.asset_ids) != 2 or any(aid not in asset_ids for aid in p.asset_ids):
                raise HTTPException(422, '首尾帧需要两张已连接到当前视频节点的图片')
            asset_ids = list(p.asset_ids)
    mode = p.mode if p.mode in ('first-last', 'multi-frame') else 'image-to-video' if asset_ids else 'text-to-video'
    if mode == 'first-last':
        if len(asset_ids) != 2:
            raise HTTPException(422, '首尾帧生成需要两张图片')
        for aid in asset_ids:
            asset = require_live_asset(aid)
            if not is_image_asset_kind(asset.get('kind', 'image')):
                raise HTTPException(422, '首尾帧仅支持图片')
    p = p.model_copy(update={'asset_ids': asset_ids, 'mode': mode})
    blocked_terms = ('违法', '暴恐', '恐怖主义', '色情', '未成年人裸露')
    if any(term in f'{p.prompt} {p.negative_prompt}'.lower() for term in blocked_terms):
        tid = str(uuid.uuid4())
        params = p.model_dump()
        params['model_name'] = '内容安全审核'
        params['client_input_digest'] = request_digest
        return save('task', {'id': tid, 'task_id': tid, 'project_id': p.project_id, 'params': params, 'status': 'blocked', 'error': '提示词未通过内容安全审核，请修改后重试。当前未调用模型、未扣费。', 'created_at': time.time()})
    config = resolve_model_config(p.model_config_id, p.model)
    video_refs = [get('asset', aid) for aid in asset_ids if get('asset', aid).get('kind') == 'video']
    if video_refs:
        model_id = str(config.get('model_id', '')).lower()
        provider = provider_id('video', config)
        seed25 = provider == 'ark' and ('seedance-2-5' in model_id or 'seedance-2.5' in model_id)
        seed20 = provider == 'ark' and ('seedance-2-0' in model_id or 'seedance-2.0' in model_id)
        h3 = provider == 'aimarket' and model_id == 'minimax-h3'
        declared = provider == 'ark' and (config.get('capabilities') or {}).get('video_reference') is True
        if not (seed25 or seed20 or h3 or declared):
            raise HTTPException(422, f'{config["name"]}不支持视频参考，请选择 Seedance 2.0 / 2.5 或 MiniMax H3')
        max_count, max_seconds = (10, 30) if seed25 else (3, 15)
        if len(video_refs) > max_count:
            raise HTTPException(422, f'当前模型最多使用 {max_count} 个参考视频')
        from agent_media import inspect_file
        total_seconds = 0
        for asset in video_refs:
            path = UPLOADS / Path(asset['url']).name
            if path.suffix.lower() != '.mp4' or path.stat().st_size > 50 * 1024 * 1024:
                raise HTTPException(422, '视频参考仅支持 MP4，单个文件不能超过 50 MB')
            info = inspect_file(path)
            if not info.get('width') or not info.get('height') or not 2 <= info['duration'] <= max_seconds + .15:
                raise HTTPException(422, f'参考视频必须包含画面，单段时长为 2～{max_seconds} 秒')
            total_seconds += info['duration']
        if total_seconds > max_seconds + .15:
            raise HTTPException(422, f'参考视频总时长不能超过 {max_seconds} 秒')
    audio_refs = [get('asset', aid) for aid in asset_ids if get('asset', aid).get('kind') == 'audio']
    if audio_refs:
        model_id = str(config.get('model_id', '')).lower()
        seed25 = 'seedance-2-5' in model_id or 'seedance-2.5' in model_id
        seed20 = 'seedance-2-0' in model_id or 'seedance-2.0' in model_id
        declared = (config.get('capabilities') or {}).get('audio_reference') is True
        if provider_id('video', config) != 'ark' or not (seed20 or seed25 or declared):
            raise HTTPException(422, f'{config["name"]}未接入音频参考，请选择支持全模态参考的 Seedance 模型')
        if not seed25 and not video_refs and not any(is_image_asset_kind(get('asset', aid).get('kind', 'image')) for aid in asset_ids):
            raise HTTPException(422, '该模型使用音频参考时还需要至少一张参考图片或参考视频，请添加后生成')
        max_count, max_seconds = (10, 30) if seed25 else (3, 15)
        if len(audio_refs) > max_count:
            raise HTTPException(422, f'当前模型最多使用 {max_count} 条音频参考')
        durations = [float(asset.get('duration') or 0) for asset in audio_refs]
        if any(value and not 2 <= value <= max_seconds for value in durations) or sum(durations) > max_seconds:
            raise HTTPException(422, f'音频参考单段至少 2 秒，总时长不能超过 {max_seconds} 秒')
        if any(Path(str(asset.get('url', ''))).suffix.lower() not in ('.wav', '.mp3') or asset.get('size_bytes', 0) > 15 * 1024 * 1024 for asset in audio_refs):
            raise HTTPException(422, '音频参考仅支持 WAV、MP3，单个文件不能超过 15 MB')
    capabilities = config.get('capabilities') or {}
    if capabilities.get('resolutions') and p.resolution not in capabilities['resolutions']:
        raise HTTPException(422, f'{config["name"]}仅支持{", ".join(capabilities["resolutions"])}分辨率')
    if capabilities.get('durations') and p.duration not in capabilities['durations']:
        raise HTTPException(422, f'{config["name"]}仅支持{", ".join(str(x) for x in capabilities["durations"])}秒时长')
    if capabilities.get('aspect_ratios') and p.aspect_ratio not in capabilities['aspect_ratios']:
        raise HTTPException(422, f'{config["name"]}仅支持{", ".join(capabilities["aspect_ratios"])}画幅')
    tid = str(uuid.uuid4())
    params = p.model_dump()
    params['client_input_digest'] = request_digest
    params['model'] = config['model_id']
    params['model_config_id'] = config.get('id') or p.model_config_id or active_model_id('video')
    params['model_name'] = config['name']
    try:
        params.update(style_task_fields(params.get('visual_style'), params['prompt'], 'video'))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    if not config.get('api_key'):
        return save('task', {'id': tid, 'task_id': tid, 'project_id': p.project_id, 'params': params, 'status': 'blocked', 'error': f'请先在右侧模型选择中配置 {config["name"]} 的 API Key。当前未调用模型、未扣费。', 'created_at': time.time()})
    task = save('task', {'id': tid, 'task_id': tid, 'project_id': p.project_id, 'params': params, 'status': 'queued', 'provider': config['provider'], 'created_at': time.time()})
    with submission_task_lock(VIDEO_WORKER_LOCK):
        ACTIVE_VIDEO_TASKS.add(tid)
    start_generation_worker('task', task, run_provider_task, (tid, params, config))
    return task

@app.get('/api/tasks/{tid}')
def task(tid: str):
    value = get('task', tid)
    if value.get('deleted_at'): raise HTTPException(404, '生成记录已删除')
    return refresh_video_task(tid)

from agent_runtime import install_agent


class AgentHost:
    """Resolve business functions at call time, including test substitutions."""
    def __getattr__(self, name):
        return globals()[name]


def submission_task_lock(lock):
    # A manual submission already owns its conversation/database lock. Avoid taking
    # a process-wide task lock after that SQL lock: canvas writers take those
    # locks in the opposite order. Its task IDs and ACTIVE registrations are
    # protected by the media submission lock until the queued transaction commits.
    return nullcontext() if transaction_connection(database) is not None else lock


def start_generation_worker(kind, task, target, args):
    lock, active = {'image_task': (IMAGE_TASK_LOCK, ACTIVE_IMAGE_TASKS),
                    'audio_task': (AUDIO_TASK_LOCK, ACTIVE_AUDIO_TASKS),
                    'task': (VIDEO_WORKER_LOCK, ACTIVE_VIDEO_TASKS)}[kind]
    def unregister():
        with lock:
            active.discard(task['id'])
    def start():
        try:
            threading.Thread(target=target, args=args, daemon=True).start()
        except Exception:
            unregister()
            save(kind, {**task, 'status': 'failed', 'error': '生成任务未能启动，请稍后重试', 'updated_at': time.time()})
            raise HTTPException(503, '生成任务未能启动，请稍后重试') from None
    after_commit(start, on_rollback=unregister)


manual_generation = ConversationGeneration(AgentHost())


from media_library import migrate_media_library

migrate_media_library(connect, DATA)
from conversation_service import install_conversations, migrate_agent_conversations
migrate_agent_conversations(AgentHost())
migrate_ownership(connect, DATA)
install_conversations(app, AgentHost())
install_agent(app, AgentHost())

app.mount('/uploads', PrivateUploads(directory=UPLOADS), name='uploads')
app.mount('/', StaticFiles(directory=ROOT / 'public', html=True), name='frontend')
