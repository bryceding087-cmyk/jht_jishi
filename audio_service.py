"""Seed Audio HTTP contract, bounded media handling, and request validation."""
import base64
import binascii
import io
import ipaddress
import json
import math
import re
import socket
import uuid
import wave
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException
from mutagen.mp3 import MP3
from mutagen.oggopus import OggOpus
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from PIL import Image
from content_errors import normalize_content_error
from market_service import audio_api_key, redact_market_result, safe_model_error

MB = 1024 * 1024
MAX_AUDIO_BYTES = 30 * MB
SAMPLE_RATES = {
    'wav': [8000, 16000, 24000, 32000, 40000, 44100, 48000],
    'mp3': [8000, 16000, 24000, 32000, 44100, 48000],
    'ogg_opus': [48000],
}

# These are application modes expressed through Seed Audio's text_prompt,
# not separate upstream enum parameters. Keep intent explicit for short prompts.
AUDIO_MODE_INSTRUCTIONS = {
    'voice': '配音：',
    'sfx': '生成音效或环境氛围声。不要对白、旁白、解说、朗读或背景音乐。\n声音描述：',
    'music': '生成纯器乐背景音乐，用乐器、节奏与音色表现所需氛围。不要任何人声、对白、旁白、解说、朗读、歌词、演唱或哼唱。\n配乐描述：',
}
IMAGE_SOUND_INSTRUCTION = '参考图片只用于理解场景、时代、情绪和氛围，将其转化为声音；不要口头描述图片，也不要为图中人物生成台词。\n'


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    format: str = 'wav'
    sample_rate: int | None = None
    speech_rate: int = Field(default=0, ge=-50, le=100)
    loudness_rate: int = Field(default=0, ge=-50, le=100)
    pitch_rate: int = Field(default=0, ge=-12, le=12)
    enable_subtitle: bool = False

    @model_validator(mode='after')
    def validate_sample_rate(self):
        if self.format not in SAMPLE_RATES:
            raise ValueError('请选择可试听的 WAV、MP3 或 OGG Opus 输出格式')
        if self.sample_rate is None:
            self.sample_rate = {'wav': 40000, 'mp3': 44100, 'ogg_opus': 48000}[self.format]
        if self.sample_rate not in SAMPLE_RATES[self.format]:
            raise ValueError('当前输出格式不支持该采样率')
        return self


class AudioGenerationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(min_length=1, max_length=3000)
    name: str = Field(default='AI 生成音频', min_length=1, max_length=160)
    mode: str = 'sfx'
    model_config_id: str = Field(default='', max_length=160)
    project_id: str = Field(default='', max_length=160)
    folder_id: str = Field(default='', max_length=160)
    request_id: str = Field(default='', max_length=160)
    reference_asset_ids: list[str] = Field(default_factory=list, max_length=3)
    speaker: str = Field(default='', max_length=200)
    audio_config: AudioConfig = Field(default_factory=AudioConfig)
    watermark: bool = False

    @field_validator('prompt', 'name')
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError('请填写音频名称和生成描述')
        return value.strip()

    @field_validator('mode')
    @classmethod
    def known_mode(cls, value):
        if value not in ('sfx', 'voice', 'music'):
            raise ValueError('音频类型必须是音效、配音或背景音乐')
        return value

    @field_validator('reference_asset_ids')
    @classmethod
    def valid_references(cls, values):
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value) for value in values):
            raise ValueError('参考素材编号无效')
        if len(set(values)) != len(values):
            raise ValueError('请勿重复选择同一个参考素材')
        return values

    @field_validator('speaker')
    @classmethod
    def trim_speaker(cls, value):
        return value.strip()


def inspect_audio(data, audio_format):
    """Validate the actual container and duration, including truncated WAVs."""
    if not data or len(data) > MAX_AUDIO_BYTES:
        raise ValueError('音频为空或超过 30 MB')
    try:
        if audio_format == 'wav':
            with wave.open(io.BytesIO(data), 'rb') as stream:
                frames, rate = stream.getnframes(), stream.getframerate()
                frame_size = stream.getnchannels() * stream.getsampwidth()
                if not frames or not rate or frame_size < 1 or frames * frame_size > len(data):
                    raise ValueError('音频帧不完整')
                if len(stream.readframes(frames)) != frames * frame_size:
                    raise ValueError('音频帧不完整')
                duration = frames / rate
        elif audio_format == 'mp3':
            duration = MP3(io.BytesIO(data)).info.length
        elif audio_format == 'ogg_opus':
            duration = OggOpus(io.BytesIO(data)).info.length
        else:
            raise ValueError('不支持的音频格式')
    except Exception as error:
        raise ValueError('音频无法解码，请使用完整的 WAV、MP3 或 OGG Opus 文件') from error
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('音频时长无效')
    return duration


def audio_reference(data, suffix):
    if len(data) > 10 * MB:
        raise ValueError('单条参考音频不能超过 10 MB')
    audio_format = {'.wav': 'wav', '.mp3': 'mp3', '.ogg': 'ogg_opus'}.get(suffix.lower())
    if not audio_format:
        raise ValueError('参考音频请选择 WAV、MP3 或 OGG Opus；M4A 和原始 PCM 请先转换')
    if inspect_audio(data, audio_format) > 30:
        raise ValueError('单条参考音频不能超过 30 秒，请先截取后上传')
    return {'audio_data': base64.b64encode(data).decode('ascii')}


def image_reference(data):
    if not data or len(data) > 10 * MB:
        raise ValueError('参考图片不能为空或超过 10 MB')
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 40_000_000:
                raise ValueError('参考图片格式或像素不受支持')
            image.verify()
    except Exception as error:
        raise ValueError('参考图片请选择有效的 JPEG、PNG 或 WebP 文件') from error
    return {'image_data': base64.b64encode(data).decode('ascii')}


def audio_payload(params, config, references):
    references = list(references)
    if params.get('speaker'):
        references.insert(0, {'speaker': params['speaker']})
    images = [item for item in references if 'image_data' in item]
    if images and (len(images) != 1 or len(references) != 1):
        raise ValueError('最多使用一张参考图片，且不能与参考音频或音色 ID 混用')
    if not images and len(references) > 3:
        raise ValueError('参考音频和音色 ID 合计最多 3 项')
    instruction = AUDIO_MODE_INSTRUCTIONS[params['mode']]
    if images and params['mode'] != 'voice':
        instruction = IMAGE_SOUND_INSTRUCTION + instruction
    # Never silently drop the selected mode when the user fills the text limit.
    limit = 3000 - len(instruction)
    if len(params['prompt']) > limit:
        raise ValueError(f'当前音频类型和参考方式最多支持 {limit} 字的生成描述，请缩短后重试')
    prompt = instruction + params['prompt']
    payload = {'model': config['model_id'],
               'text_prompt': prompt,
               'audio_config': params['audio_config'],
               'watermark': {'aigc_watermark': params.get('watermark', False)}}
    if references:
        payload['references'] = references
    return payload


class NoApiRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError('音频接口地址发生重定向，请在模型管理中填写最终接口地址')


def request_audio(config, payload, trace_id=None):
    url = config['base_url'].rstrip('/') + '/' + config['api_path'].lstrip('/')
    api_key = audio_api_key(url, config['api_key'])
    request = Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), method='POST', headers={
        'X-Api-Key': api_key, 'X-Api-Request-Id': trace_id or str(uuid.uuid4()),
        'Content-Type': 'application/json', 'Accept': 'application/json',
    })
    limit = ((MAX_AUDIO_BYTES + 2) // 3) * 4 + MB
    with build_opener(NoApiRedirect()).open(request, timeout=config.get('timeout_seconds', 300)) as response:
        raw = response.read(limit + 1)
        log_id = response.headers.get('X-Tt-Logid', '')
    if len(raw) > limit:
        raise ValueError('音频接口响应过大，无法保存，请缩短生成时长')
    try:
        body = redact_market_result(json.loads(raw), api_key)
    except (ValueError, UnicodeDecodeError):
        raise ValueError('音频接口没有返回有效 JSON，请核对模型地址') from None
    if not isinstance(body, dict):
        raise ValueError('音频接口响应格式不正确')
    if body.get('code') not in (0, 20000000, None):
        normalized = normalize_content_error(body)
        if normalized != body:
            raise ValueError(normalized)
        raise ValueError(f"音频接口返回错误 {body['code']}：{str(body.get('message') or '生成失败')[:500]}")
    return body, safe_model_error(str(log_id), api_key)[:200]


def validate_download_url(url):
    parsed = urlparse(str(url))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('音频接口返回了无效的下载地址')
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('音频接口返回了不允许访问的下载地址')


class PublicAudioRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_download_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_audio(url):
    validate_download_url(url)
    with build_opener(PublicAudioRedirect()).open(Request(url, headers={'Accept': 'audio/*'}), timeout=60) as response:
        length = response.headers.get('Content-Length', '')
        if str(length).isdigit() and int(length) > MAX_AUDIO_BYTES:
            raise ValueError('生成音频超过 30 MB，请缩短生成时长')
        data = response.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError('生成音频超过 30 MB，请缩短生成时长')
    return data


def decode_audio_result(body, audio_format):
    encoded = body.get('audio')
    if encoded:
        if not isinstance(encoded, str) or len(encoded) > ((MAX_AUDIO_BYTES + 2) // 3) * 4:
            raise ValueError('生成音频数据无效或超过 30 MB')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('音频接口返回了无效的 Base64 数据') from None
    elif body.get('url'):
        data = download_audio(body['url'])
    else:
        raise ValueError('音频接口没有返回音频数据或下载地址')
    duration = inspect_audio(data, audio_format)
    suffix = {'wav': '.wav', 'mp3': '.mp3', 'ogg_opus': '.ogg'}[audio_format]
    return data, suffix, duration


def audio_error_message(error, api_key=''):
    if isinstance(error, HTTPError):
        text = f'音频接口请求失败（HTTP {error.code}）'
        try:
            raw = error.read(65536).decode('utf-8', errors='replace')
            normalized = normalize_content_error(raw)
            if normalized != raw:
                return normalized
            body = json.loads(raw)
            detail = body.get('message') or body.get('error', {}).get('message')
            if detail:
                text += '：' + str(detail)
        except (ValueError, AttributeError, OSError):
            pass
    elif isinstance(error, HTTPException):
        text = str(error.detail)
    elif isinstance(error, (TimeoutError, socket.timeout)):
        text = '音频生成超时，上游可能仍在处理。请先到服务商控制台确认，再决定是否重新生成。'
    elif isinstance(error, URLError):
        text = '无法连接音频服务，请检查模型地址和网络。'
    else:
        text = str(error).strip() or '音频生成失败，请稍后重试'
    text = normalize_content_error(text)
    if api_key:
        text = text.replace(api_key, '[已隐藏]')
    text = re.sub(r'(?i)(?:Bearer|X-Api-Key)\s*[:=]?\s+[^\s,;"\']+', '[已隐藏]', text)
    return text.replace('\n', ' ')[:700]
