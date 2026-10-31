"""Bounded, reusable image derivatives for video requests; originals stay intact."""
from collections import OrderedDict
from dataclasses import dataclass
import io
from pathlib import Path
import threading

from PIL import Image, ImageOps

MAX_EDGE = 1920
TARGET_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MIN_EDGE = 768
CACHE_BYTES = 24 * 1024 * 1024
_cache = OrderedDict()
_cache_size = 0
_lock = threading.Lock()
_encoders = threading.Semaphore(2)


@dataclass(frozen=True)
class ReferenceImage:
    data: bytes
    mime: str
    original_bytes: int
    width: int
    height: int


def encode_reference_image(data):
    if not data or len(data) > MAX_SOURCE_BYTES:
        raise ValueError('视频参考图为空或超过 20 MB，请更换图片')
    with Image.open(io.BytesIO(data)) as original:
        if original.format not in ('JPEG', 'PNG', 'WEBP'):
            raise ValueError('视频参考图仅支持 JPG、PNG、WebP')
        original.load()
        orientation = original.getexif().get(274, 1)
        if len(data) <= TARGET_BYTES and max(original.size) <= MAX_EDGE and orientation == 1:
            return ReferenceImage(data, Image.MIME[original.format], len(data), *original.size)
        image = ImageOps.exif_transpose(original)
        alpha = 'A' in image.getbands() or 'transparency' in image.info
        image = image.convert('RGBA' if alpha else 'RGB')
    image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
    while True:
        candidates = []
        if alpha or original.format == 'PNG':
            output = io.BytesIO(); image.save(output, 'PNG', compress_level=6)
            candidates.append((output.getvalue(), 'image/png'))
        if not alpha:
            for quality in (88, 82, 76):
                output = io.BytesIO(); image.save(output, 'JPEG', quality=quality, optimize=True)
                candidates.append((output.getvalue(), 'image/jpeg'))
                if len(output.getvalue()) <= TARGET_BYTES:
                    break
        encoded, mime = min(candidates, key=lambda item: len(item[0]))
        if len(encoded) <= TARGET_BYTES or max(image.size) <= MIN_EDGE:
            return ReferenceImage(encoded, mime, len(data), *image.size)
        edge = max(MIN_EDGE, int(max(image.size) * .8))
        image.thumbnail((edge, edge), Image.Resampling.LANCZOS)


def video_reference_image(path):
    global _cache_size
    path = Path(path).resolve()
    stat = path.stat()
    if stat.st_size > MAX_SOURCE_BYTES:
        raise ValueError('视频参考图超过 20 MB，请更换图片')
    key = (str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    with _lock:
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    with _encoders:
        result = encode_reference_image(path.read_bytes())
    with _lock:
        previous = _cache.pop(key, None)
        if previous is not None:
            _cache_size -= len(previous.data)
        _cache[key] = result; _cache_size += len(result.data)
        while _cache and (_cache_size > CACHE_BYTES or len(_cache) > 32):
            _, removed = _cache.popitem(last=False)
            _cache_size -= len(removed.data)
    return result
