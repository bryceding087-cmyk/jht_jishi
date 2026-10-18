"""Typed asset validation. This module never opens the application's database."""
import io
import struct
from pathlib import Path

from fastapi import HTTPException
from PIL import Image, ImageOps, UnidentifiedImageError

MB = 1024 * 1024
FILE_TYPES = {
    '.jpg': ('image', 20 * MB), '.jpeg': ('image', 20 * MB),
    '.png': ('image', 20 * MB), '.webp': ('image', 20 * MB),
    '.mp4': ('video', 100 * MB), '.webm': ('video', 100 * MB),
    '.mp3': ('audio', 30 * MB), '.wav': ('audio', 30 * MB),
    '.m4a': ('audio', 30 * MB), '.ogg': ('audio', 30 * MB),
}
# ``prop`` is a visual subtype of image.  Keeping it as a separate asset
# kind lets the library filter props without making the video/image model
# adapters treat them as a different media format.
IMAGE_ASSET_KINDS = frozenset({'image', 'prop'})
ASSET_KINDS = frozenset({'image', 'prop', 'video', 'audio'})
MIMES = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
         '.webp': 'image/webp', '.mp4': 'video/mp4', '.webm': 'video/webm',
         '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
         '.ogg': 'audio/ogg', '.txt': 'text/plain; charset=utf-8',
         '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}


def is_image_asset_kind(kind):
    """Return whether an asset can be used as an image reference.

    Props are stored as their own library category, but remain valid visual
    inputs for image/video workflows and character-like reference handling.
    """
    return str(kind or 'image').strip().lower() in IMAGE_ASSET_KINDS


def validate_asset_kind(kind, detected_kind):
    """Validate an optional library category supplied with an upload.

    The file extension still determines the physical media type.  Only an
    image upload may be assigned to the ``prop`` category; callers cannot
    spoof a video/audio file into another category.
    """
    requested = str(kind or '').strip().lower()
    if not requested:
        return detected_kind
    if requested not in ASSET_KINDS:
        raise HTTPException(422, '素材类型必须是图片、视频或音频')
    if requested in IMAGE_ASSET_KINDS and detected_kind == 'image':
        return 'image'
    if requested != detected_kind:
        raise HTTPException(422, '上传文件格式与素材类型不匹配')
    return requested


def file_type(filename):
    suffix = Path(str(filename or '').replace('\\', '/')).suffix.lower()
    if suffix not in FILE_TYPES:
        raise HTTPException(422, '不支持此格式。请上传 JPG/PNG/WEBP、MP4/WEBM 或 MP3/WAV/M4A/OGG')
    return suffix, *FILE_TYPES[suffix]


def validate_bytes(data, suffix):
    kind, limit = FILE_TYPES[suffix]
    if not data:
        raise HTTPException(422, '不能上传空文件')
    if len(data) > limit:
        raise HTTPException(413, f'此类型文件不能超过 {limit // MB} MB')
    if kind == 'image':
        try:
            with Image.open(io.BytesIO(data)) as img:
                # Trust the decoded format, not a filename supplied by the client.
                # Supported images are fully verified and re-encoded below, so a
                # JPEG named .png can safely become a correctly named .jpg.
                # Phone JPEGs with auxiliary images (e.g. HDR gain maps) may be
                # decoded as MPO. Only their primary image is stored as JPEG.
                if img.format not in {'JPEG', 'MPO', 'PNG', 'WEBP'}:
                    raise HTTPException(422, '图片实际格式不受支持，请上传 JPG、PNG 或 WEBP 图片')
                if img.width * img.height > 40000000:
                    raise HTTPException(422, '图片总像素不能超过 4000 万，请缩小图片后重试')
                img.verify()
            with Image.open(io.BytesIO(data)) as img:
                img.seek(0)
                ImageOps.exif_transpose(img, in_place=True)
                img.thumbnail((2560, 2560))
                output = io.BytesIO()
                # Preserve transparency, including palette PNG and transparent WebP.
                transparent = 'A' in img.getbands() or 'transparency' in img.info
                if transparent:
                    img.convert('RGBA').save(output, 'PNG')
                    return output.getvalue(), '.png', {}
                img.convert('RGB').save(output, 'JPEG', quality=90)
                return output.getvalue(), '.jpg', {}
        except Image.DecompressionBombError:
            raise HTTPException(422, '图片总像素不能超过 4000 万，请缩小图片后重试')
        except (UnidentifiedImageError, OSError, ValueError):
            raise HTTPException(422, '图片文件损坏或无法完整解码，请重新导出为 JPG、PNG 或 WEBP 后上传')
    if kind == 'text':
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise HTTPException(422, '文本文件必须使用 UTF-8 编码')
        if not text.strip() or '\x00' in text or any(ord(c) < 32 and c not in '\n\r\t' for c in text):
            raise HTTPException(422, '文本文件为空或包含二进制内容')
        return data, suffix, {'content': text}
    if kind == 'script':
        return data, suffix, {}  # The bounded XLSX parser validates the entire archive.
    valid = False
    if suffix in ('.mp4', '.m4a'):
        boxes, offset = [], 0
        while offset + 8 <= len(data):
            length, name = struct.unpack('>I4s', data[offset:offset + 8])
            header = 8
            if length == 1:
                if offset + 16 > len(data): break
                length = struct.unpack('>Q', data[offset + 8:offset + 16])[0]
                header = 16
            if length == 0: length = len(data) - offset
            if length < header or offset + length > len(data): break
            boxes.append((name, data[offset + header:offset + length]))
            offset += length
        valid = offset == len(data) and any(n == b'ftyp' and len(v) >= 8 for n, v in boxes) and any(n == b'mdat' and v for n, v in boxes) and any(n == b'moov' and v for n, v in boxes)
        if valid:
            moov = next(v for n, v in boxes if n == b'moov')
            valid = b'soun' in moov if suffix == '.m4a' else b'vide' in moov
    elif suffix == '.webm':
        valid = data.startswith(b'\x1aE\xdf\xa3') and b'webm' in data[:4096] and b'\x18S\x80g' in data[:8192] and b'V_' in data
    elif suffix == '.wav':
        valid = data.startswith(b'RIFF') and data[8:12] == b'WAVE' and b'fmt ' in data and b'data' in data and len(data) >= 44
        if valid:
            valid = int.from_bytes(data[4:8], 'little') + 8 <= len(data)
    elif suffix == '.ogg':
        valid = data.startswith(b'OggS\x00') and (b'OpusHead' in data[:512] or b'\x01vorbis' in data[:512]) and len(data) > 40
    elif suffix == '.mp3':
        start = 0
        if data.startswith(b'ID3') and len(data) >= 10 and all(x < 128 for x in data[6:10]):
            size = sum(byte << shift for byte, shift in zip(data[6:10], (21, 14, 7, 0)))
            start = 10 + size + (10 if data[5] & 16 else 0)
        # A tag alone is not audio: require a plausible MPEG audio frame.
        for at in range(start, min(start + 1024, len(data) - 4)):
            head = data[at:at + 4]
            if head[0] == 255 and head[1] & 224 == 224 and head[1] & 6 and head[1] & 24 != 8 and 0 < head[2] >> 4 < 15 and head[2] & 12 != 12 and len(data) - at >= 24:
                valid = True
                break
    if not valid:
        raise HTTPException(422, '媒体文件损坏、缺少有效音视频轨道或实际格式与扩展名不符')
    return data, suffix, {}


def project_asset_references(project, asset_id, characters=()):
    """Include recycled projects, snapshots and legacy inspector references."""
    if asset_id in project.get('selected_asset_ids', []):
        return True
    character_ids = {c['id'] for c in characters if c.get('asset_id') == asset_id}
    for node in project.get('nodes', []):
        if asset_id in (node.get('assetId'), node.get('sourceAssetId'), node.get('asset_id'), node.get('referenceAssetId')):
            return True
        if node.get('characterId') in character_ids:
            return True
        if asset_id in (node.get('settings') or {}).get('asset_ids', []):
            return True
        if asset_id in (node.get('audio_settings') or {}).get('reference_asset_ids', []):
            return True
        if any(r.get('asset_id') == asset_id for r in (node.get('script_data') or {}).get('resources', []) if isinstance(r, dict)):
            return True
    return False
