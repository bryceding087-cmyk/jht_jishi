"""Bounded, validated downloads for generated video results.

Video providers return signed object-storage URLs.  Those URLs are useful for
an immediate preview, but they are intentionally short lived.  The application
copies the result into its own uploads directory before marking a task ready so
projects keep playing after the provider URL expires.
"""

import ipaddress
import socket
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from model_gateway import video_download_url


MAX_VIDEO_BYTES = 100 * 1024 * 1024
VIDEO_DOWNLOAD_TIMEOUT = 120
VIDEO_CHUNK_BYTES = 256 * 1024


def validate_video_url(url, trusted_origin=None):
    """Allow public HTTPS storage or the exact operator-configured gateway."""
    parsed = urlparse(str(url))
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('模型返回了无效的视频下载地址')
    def origin(value):
        return value.scheme, value.hostname, value.port or (443 if value.scheme == 'https' else 80)
    if trusted_origin and origin(parsed) == origin(urlparse(str(trusted_origin))):
        return str(url)
    if parsed.scheme != 'https':
        raise ValueError('模型返回的视频下载地址不允许访问')
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError('无法解析生成视频的下载地址，请稍后重试') from None
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('模型返回的视频下载地址不允许访问')
    return str(url)


class PublicVideoRedirectHandler(HTTPRedirectHandler):
    def __init__(self, trusted_origin=None):
        super().__init__()
        self.trusted_origin = trusted_origin

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        newurl = video_download_url(newurl, self.trusted_origin)
        validate_video_url(newurl, self.trusted_origin)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def _suffix(url, content_type, data):
    content_type = str(content_type or '').split(';', 1)[0].strip().lower()
    pathname = Path(urlparse(str(url)).path).suffix.lower()
    if content_type == 'video/webm' or pathname == '.webm' or data.startswith(b'\x1aE\xdf\xa3'):
        return '.webm'
    # Ark returns video/mp4 for Seedance.  Some object storage responses use
    # application/octet-stream, so the URL/signature and MP4 ftyp marker are
    # also accepted.
    if content_type in ('video/mp4', 'application/mp4') or pathname in ('.mp4', '.m4v') or b'ftyp' in data[:64]:
        return '.mp4'
    if content_type.startswith('video/'):
        return '.mp4'
    raise ValueError('模型返回的文件不是有效视频')


def download_generated_video(url, trusted_origin=None):
    """Download a provider video with size, redirect and time bounds.

    Returns ``(bytes, suffix)``.  The caller owns persistence and can perform
    an atomic write to its configured uploads directory.
    """
    url = video_download_url(url, trusted_origin)
    validate_video_url(url, trusted_origin)
    request = Request(str(url), headers={'User-Agent': 'JianHuiTong/1.0', 'Accept': 'video/*'})
    with build_opener(PublicVideoRedirectHandler(trusted_origin)).open(request, timeout=VIDEO_DOWNLOAD_TIMEOUT) as response:
        final_url = response.geturl() if hasattr(response, 'geturl') else str(url)
        validate_video_url(final_url, trusted_origin)
        length = response.headers.get('Content-Length', '')
        if str(length).isdigit() and int(length) > MAX_VIDEO_BYTES:
            raise ValueError('生成视频超过 100 MB，暂时无法缓存')
        chunks, total, started = [], 0, time.monotonic()
        while True:
            if time.monotonic() - started > VIDEO_DOWNLOAD_TIMEOUT:
                raise TimeoutError('生成视频下载超时，请稍后重试')
            chunk = response.read(VIDEO_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_VIDEO_BYTES:
                raise ValueError('生成视频超过 100 MB，暂时无法缓存')
            chunks.append(chunk)
        data = b''.join(chunks)
        if not data:
            raise ValueError('模型返回了空视频，请重试')
        return data, _suffix(final_url, response.headers.get('Content-Type', ''), data)
