"""Import formal style recipes and selected previews from a local skill bundle.

Usage: python scripts/import_visual_styles.py /path/to/visual-style-studio.zip
An extracted visual-style-studio directory is also supported. No model calls.
"""
import argparse
from collections import Counter
from contextlib import nullcontext
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
FIELDS = (
    'id', 'source_name', 'category', 'display_name', 'aliases', 'summary',
    'image_style_prompt_zh', 'image_style_prompt_en', 'avoid',
    'video_motion', 'video_consistency', 'sources',
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def import_bundle(source):
    catalogue_path = ROOT / 'resources/visual-styles.json'
    previous = json.loads(catalogue_path.read_text(encoding='utf-8'))
    previous_ids = [style['id'] for style in previous['styles']]
    archive = zipfile.ZipFile(source) if source.is_file() else None
    with archive if archive else nullcontext():
        def read(relative):
            path = PurePosixPath(relative)
            if path.is_absolute() or '..' in path.parts or '\\' in relative:
                raise ValueError(f'Invalid bundle path: {relative}')
            if archive:
                return archive.read('visual-style-studio/' + path.as_posix())
            return (source / path).read_bytes()

        source_bytes = read('references/styles.json')
        manifest_bytes = read('assets/preview-manifest.json')
        catalogue = json.loads(source_bytes)
        manifest = json.loads(manifest_bytes)
        styles = catalogue['styles']
        ids = [style['id'] for style in styles]
        previews = {preview['style_id']: preview for preview in manifest['previews']}
        if len(ids) != len(set(ids)) or len(previews) != len(manifest['previews']):
            raise ValueError('Duplicate style or preview IDs')
        if set(ids) != set(previews):
            raise ValueError('Style and preview IDs do not match')
        if not set(previous_ids).issubset(ids):
            raise ValueError('The update would remove existing style IDs')

        records, images = {}, {}
        for style in styles:
            sid = style['id']
            if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', sid) or len(sid) > 80:
                raise ValueError(f'Invalid style ID: {sid}')
            preview = previews[sid]
            if preview['status'] != 'verified' or not preview.get('visual_review', {}).get('passed'):
                raise ValueError(f'Unverified preview: {sid}')
            original = read('assets/' + preview['file'])
            if digest(original) != preview['sha256']:
                raise ValueError(f'Preview checksum mismatch: {sid}')
            with Image.open(BytesIO(original)) as image:
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((768, 768), Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(output, format='JPEG', quality=88, optimize=True, progressive=True)
            images[sid] = output.getvalue()
            checksum = digest(images[sid])
            # Allowlist formal recipes; never import example subjects/prompts.
            records[sid] = {key: style[key] for key in FIELDS}
            records[sid].update(
                preview_url=f'/style-previews/{sid}.jpg?v={checksum[:12]}',
                source_preview_file=preview['file'],
                source_preview_sha256=preview['sha256'],
                preview_sha256=checksum,
            )

    # Keep familiar cards in place within each category, then append new ones.
    categories = list(dict.fromkeys([s['category'] for s in previous['styles']] +
                                   [s['category'] for s in styles]))
    ordered_ids = previous_ids + [sid for sid in ids if sid not in previous_ids]
    imported = [records[sid] for category in categories for sid in ordered_ids
                if records[sid]['category'] == category]
    result = {
        'version': catalogue['version'],
        'source': 'visual-style-studio',
        'updated_date': catalogue['updated_date'],
        'source_catalogue_sha256': digest(source_bytes),
        'source_manifest_sha256': digest(manifest_bytes),
        'styles': imported,
    }
    # All source checks and conversions finish before changing project files.
    preview_dir = ROOT / 'public/style-previews'
    preview_dir.mkdir(parents=True, exist_ok=True)
    for sid, data in images.items():
        (preview_dir / f'{sid}.jpg').write_bytes(data)
    catalogue_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'version': result['version'], 'styles': len(imported),
                      'added': len(imported) - len(previous_ids),
                      'categories': dict(Counter(s['category'] for s in imported)),
                      'preview_bytes': sum(map(len, images.values()))}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Skill ZIP or extracted skill directory')
    import_bundle(parser.parse_args().source)
