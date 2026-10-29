"""Archive the current source tree without credentials, data or local artifacts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile


def build(root, name, patch_manifest=None):
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', name):
        raise ValueError('Archive name must contain only letters, digits, underscores and hyphens')
    out = root / 'outputs'
    out.mkdir(exist_ok=True)
    archive = out / (name + '.zip')
    checksum = out / (name + '.zip.sha256')
    if archive.exists() or checksum.exists():
        raise SystemExit('Published archive already exists; choose a new name.')
    names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root).decode().split('\0')
    excluded = {'.git', '.venv', 'venv', '.runtime', 'node_modules', '__pycache__',
                'data', 'uploads', 'work', 'outputs', '.pytest_cache', 'test-results', 'playwright-report'}
    source = {}
    for name_in_tree in sorted(set(filter(None, names))):
        relative = Path(name_in_tree)
        if any(part in excluded for part in relative.parts):
            continue
        if relative.name.startswith('.env') and relative.name != '.env.example':
            continue
        if relative.suffix.lower() in {'.key', '.pem', '.pfx', '.p12', '.db', '.sqlite', '.sqlite3', '.log', '.pyc'}:
            continue
        if relative.name in {'credentials.json', 'secrets.json', 'PACKAGING_MANIFEST.json'}:
            continue
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise SystemExit('Source is missing or symlinked: ' + name_in_tree)
        data = path.read_bytes()
        # Fail with the filename only; never print suspected secret contents.
        if re.search(rb'ark-[a-zA-Z0-9-]{30,}|sk-[a-zA-Z0-9]{32,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', data):
            raise SystemExit('Possible credential requires review: ' + name_in_tree)
        source[name_in_tree] = data
    digest = lambda data: hashlib.sha256(data).hexdigest()
    hashes = {path: digest(data) for path, data in source.items()}
    manifest = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'source_base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip(),
        'source_type': 'working-tree-including-uncommitted-files',
        'file_count': len(source),
        'files_sha256': hashes,
        'excluded': 'credentials, databases, generated/user media, local dependencies, temporary outputs, git history',
    }
    # The source and upload package must contain byte-identical runtime files.
    if patch_manifest is not None:
        patch_manifest = root / patch_manifest
        patch = json.loads(patch_manifest.read_text('utf-8'))
        for file, expected in patch['source_runtime_sha256'].items():
            assert hashes.get(file) == expected, 'Runtime differs from upload package: ' + file
        manifest['matching_upload_version'] = patch['version']
        manifest['runtime_snapshot_sha256'] = patch['source_snapshot_sha256']
    packed = {**source, 'SOURCE-MANIFEST.json': (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()}
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as stream:
        for file, data in packed.items():
            stream.writestr(name + '/' + file, data)
    with zipfile.ZipFile(archive) as stream:
        assert stream.testzip() is None
        assert len(stream.namelist()) == len(packed)
        for file, data in packed.items():
            assert stream.read(name + '/' + file) == data, file
    assert all((root / file).read_bytes() == data for file, data in source.items()), 'Source changed during packaging'
    checksum.write_text(digest(archive.read_bytes()) + '  ' + archive.name + '\n', encoding='ascii', newline='\n')
    print('PASS: source ZIP contents, runtime parity, credential exclusions and SHA-256; files:', len(source))
    print(archive)
    print('Bytes:', archive.stat().st_size)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', required=True)
    parser.add_argument('--patch-manifest', type=Path, help='Verify runtime parity with this upload package manifest')
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.name, args.patch_manifest)
