"""Bounded downloads and safe error messages for generated image assets."""
import io
import math
import ipaddress
import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from content_errors import normalize_content_error

MAX_IMAGE_BYTES = 20 * 1024 * 1024
IMAGE_RATIOS = ['adaptive', '1:1', '3:4', '16:9', '4:3', '9:16', '2:3', '3:2', '21:9']


def image_capabilities(config):
    model = str(config.get('model') or config.get('model_id') or '').lower()
    if '5-0-pro' in model or '5.0-pro' in model or '5-0-flash' in model or '5.0-flash' in model:
        sizes = ['1K', '1.5K', '2K']
    elif '5-0-lite' in model or '5.0-lite' in model:
        sizes = ['2K', '3K', '4K']
    elif '4-5' in model or '4.5' in model:
        sizes = ['2K', '4K']
    else:
        sizes = ['1K', '2K', '4K']
    return {'resolutions': sizes, 'aspect_ratios': IMAGE_RATIOS, 'counts': [1, 2, 3, 4]}


def image_output_size(size, ratio):
    if ratio == 'adaptive':
        return size
    width_ratio, height_ratio = map(int, ratio.split(':'))
    edge = {'1K': 1024, '1.5K': 1536, '2K': 2048, '3K': 3072, '4K': 4096}[size]
    width = round(math.sqrt(edge * edge * width_ratio / height_ratio) / 16) * 16
    height = round(width * height_ratio / width_ratio / 16) * 16
    return f'{width}x{height}'


def validate_image_url(url):
    parsed = urlparse(str(url))
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('图片模型返回了无效的图片下载地址')
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError('无法解析生成图片的下载地址，请稍后重试') from None
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('图片模型返回了不允许访问的下载地址')
    return url


class PublicImageRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_image_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download_generated_image(url):
    validate_image_url(url)
    request = Request(url, headers={'User-Agent': 'JianHuiTong/1.0', 'Accept': 'image/*'})
    with build_opener(PublicImageRedirectHandler()).open(request, timeout=60) as response:
        length = response.headers.get('Content-Length', '')
        if str(length).isdigit() and int(length) > MAX_IMAGE_BYTES:
            raise ValueError('生成图片超过 20 MB，暂时无法存入素材库')
        data = response.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise ValueError('图片模型返回了空图片，请重试')
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError('生成图片超过 20 MB，暂时无法存入素材库')
    try:
        with Image.open(io.BytesIO(data)) as image:
            suffix = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}.get(image.format)
            if not suffix or image.width * image.height > 40000000:
                raise ValueError('生成图片格式不受支持或像素过大')
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('图片模型返回的文件不是有效图片，请重试') from None
    return data, suffix


def image_error_message(error, api_key=''):
    if isinstance(error, HTTPError):
        text = f'图片模型服务请求失败（HTTP {error.code}）'
        try:
            raw = error.read(65536).decode('utf-8', errors='replace')
            normalized = normalize_content_error(raw)
            if normalized != raw:
                return normalized
            body = json.loads(raw)
            detail = body.get('error', body)
            detail = detail.get('message', '') if isinstance(detail, dict) else str(detail)
            if detail: text += '：' + str(detail)
        except (ValueError, OSError):
            pass
    elif isinstance(error, HTTPException):
        text = str(error.detail)
    elif isinstance(error, (TimeoutError, socket.timeout)):
        text = '图片模型或图片下载请求超时。请先查看素材库是否已有结果，再决定是否重新生成。'
    elif isinstance(error, URLError):
        text = '无法连接图片模型服务或下载图片，请检查模型地址和网络后重试。'
    else:
        text = str(error).strip() or '图片生成失败，请稍后重试'
    text = normalize_content_error(text)
    if api_key:
        text = text.replace(api_key, '[已隐藏]')
    text = re.sub(r'(?i)Bearer\s+[^\s,;"\']+', 'Bearer [已隐藏]', text)
    text = re.sub(r'\b(?:ark|sk)-[a-zA-Z0-9_-]{8,}', '[已隐藏]', text)
    return text.replace('\n', ' ')[:700]
