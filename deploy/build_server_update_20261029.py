"""Package current working files using the established offline patch format."""
import ast
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'jht-admin-logs-update-20261029'
VERSION = 'admin-logs-update-20261029-1'
STAGE = ROOT / 'work' / NAME
OUT = ROOT / 'outputs'
ARCHIVE = OUT / (NAME + '.tar.gz')
README = ROOT / 'docs/server-update-20261029.md'
BASH = 'D:/coding/Git/bin/bash.exe'
NODE = shutil.which('node')
TEXT = {'.py', '.js', '.mjs', '.cjs', '.css', '.html', '.json', '.md', '.txt', '.svg', '.conf'}
if ARCHIVE.exists() or Path(str(ARCHIVE) + '.sha256').exists():
    raise SystemExit('Published archive already exists; use a new release name.')


def sha(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def normalize(name, data):
    return data.replace(b'\r\n', b'\n') if data is not None and Path(name).suffix in TEXT else data


def runtime(name):
    p = Path(name)
    return ((len(p.parts) == 1 and p.suffix == '.py')
            or p.parts[0] in ('public', 'resources', 'storage', 'agents')
            or name in ('deploy/container_entrypoint.py', 'deploy/container_smoke.py', 'deploy/fonts.conf', 'requirements.txt'))


def payload_node(text):
    return next(node for node in ast.parse(text).body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'PAYLOAD' for t in node.targets))


def previous_payload(package):
    archive = OUT / (package + '.tar.gz')
    if archive.exists():
        expected = Path(str(archive) + '.sha256').read_text('ascii').split()[0]
        assert sha(archive.read_bytes()) == expected, 'Prior archive checksum mismatch: ' + package
        with tarfile.open(archive, 'r:gz') as tar:
            text = tar.extractfile(package + '/patch.py').read().decode('utf-8')
    else:
        text = (ROOT / 'work' / package / 'patch.py').read_text('utf-8')
    result = ast.literal_eval(payload_node(text).value)
    for name, item in result.items():
        assert sha(base64.b64decode(item['data'], validate=True)) == item['sha256'], name
    return result


revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT).decode().strip()
names = subprocess.check_output(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=ROOT).decode().split('\0')
files = sorted({name for name in names if name and runtime(name)})
assert files and all((ROOT / name).is_file() and not (ROOT / name).is_symlink() for name in files)
assert all(not any(part in {'.env', 'data', 'uploads', 'node_modules', '__pycache__'} for part in Path(name).parts) for name in files)
# Keep the exact working bytes, including modifications not committed to Git.
source = {name: (ROOT / name).read_bytes() for name in files}
for name, data in source.items():
    if re.search(rb'ark-[a-zA-Z0-9-]{30,}|sk-[a-zA-Z0-9]{32,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----', data):
        raise SystemExit('Possible credential requires review: ' + name)
source_hashes = {name: sha(data) for name, data in source.items()}
snapshot_hash = sha(json.dumps(source_hashes, sort_keys=True, separators=(',', ':')).encode())
git_archive = subprocess.check_output(['git', 'archive', '--format=tar', revision], cwd=ROOT)
with tarfile.open(fileobj=io.BytesIO(git_archive)) as tar:
    committed = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile() and runtime(m.name)}
assert set(committed).issubset(source), 'Runtime deletions need explicit installer support.'
dirty_runtime = [name for name in files if normalize(name, source[name]) != normalize(name, committed.get(name))]

image_root = ROOT / 'work/offline-build/rootfs/app'
baseline = {name: (image_root / name).read_bytes() if (image_root / name).is_file() else None for name in files}
baselines = {}
for package, version in (
    ('jht-market-login-20261018', 'market-login-1'),
    ('jht-market-proxy-fix-20261018', 'proxy-origin-1'),
    ('jht-video-reliability-20261018', 'video-reliability-1'),
    ('jht-market-task-return-20261019', 'market-task-return-1'),
    ('jht-media-library-styles-20261019', 'media-library-styles-1'),
    ('jht-media-library-styles-20261019-r2', 'media-library-styles-2'),
    ('jht-media-update-20261020', 'media-update-20261020-1'),
    ('jht-agent-playback-update-20261023', 'agent-playback-update-20261023-1'),
    ('jht-agent-playback-update-20261023-r2', 'agent-playback-update-20261023-2'),
):
    for name, item in previous_payload(package).items():
        assert name in baseline, 'Previously shipped runtime was deleted: ' + name
        baseline[name] = base64.b64decode(item['data'], validate=True)
    if version not in ('market-login-1', 'proxy-origin-1'):
        baselines[version] = dict(baseline)

assert normalize('requirements.txt', baseline['requirements.txt']) == normalize('requirements.txt', source['requirements.txt']), 'New dependencies require an image update.'
changed = [name for name in files if any(normalize(name, b[name]) != normalize(name, source[name]) for b in baselines.values())]
assert set(dirty_runtime).issubset(changed), 'A local runtime change would be omitted.'
assert all(name in changed for name in ('app.py', 'model_gateway.py', 'video_service.py', 'public/mention-editor.js', 'public/node-player.js', 'resources/visual-styles.json'))
assert all(name in changed for name in ('admin_logs.py', 'auth_service.py', 'market_service.py',
    'generation_queue.py', 'public/admin-logs.js', 'public/admin-logs.css', 'public/index.html'))

payload = {}
for name in changed:
    accepted = []
    for snapshot in baselines.values():
        previous = snapshot[name]
        variants = [previous]
        if previous is not None and Path(name).suffix in TEXT:
            lf = normalize(name, previous)
            variants.extend([lf, lf.replace(b'\n', b'\r\n')])
        accepted.extend(map(sha, variants))
    payload[name] = {'sha256': sha(source[name]), 'accepted': list(dict.fromkeys(accepted)),
                     'data': base64.b64encode(source[name]).decode('ascii')}

STAGE.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)
template = (ROOT / 'work/jht-market-task-return-20261019/patch.py').read_text('utf-8')
node = payload_node(template)
lines = template.splitlines(keepends=True)
lines[node.lineno - 1:node.end_lineno] = ['PAYLOAD = ' + repr(payload) + '\n']
installer = ''.join(lines).replace('market-task-return-1', VERSION)
# This release updates auth_service.py. Accept both supported starting versions
# and the installed release so repeat installs and rollback pass the guard.
auth_versions = [snapshot['auth_service.py'] for snapshot in baselines.values()] + [source['auth_service.py']]
auth_hashes = tuple(dict.fromkeys(sha(variant) for data in auth_versions
    for variant in (data, normalize('auth_service.py', data), normalize('auth_service.py', data).replace(b'\n', b'\r\n'))))
auth_guard = next(line for line in installer.splitlines() if "if sha(read(root / 'auth_service.py'))" in line)
installer = installer.replace(auth_guard, "    if sha(read(root / 'auth_service.py')) not in " + repr(auth_hashes) + ':')
# New Agent resource directories are created only after the full preflight.
installer = installer.replace("    fd, temporary = tempfile.mkstemp", "    path.parent.mkdir(parents=True, exist_ok=True)\n    fd, temporary = tempfile.mkstemp")
installer = installer.replace("    if not paths['public/index.html'].is_file():", "    for path in paths.values():\n        if any(parent.exists() and not parent.is_dir() for parent in path.parents):\n            raise SystemExit('Parent path is not a directory: ' + str(path))\n    if not paths['public/index.html'].is_file():")
(STAGE / 'patch.py').write_text(installer, encoding='utf-8', newline='\n')
(STAGE / 'apply.sh').write_bytes((ROOT / 'work/jht-market-task-return-20261019/apply.sh').read_bytes().replace(b'\r\n', b'\n'))
(STAGE / 'README.zh-CN.md').write_bytes(README.read_bytes())

manifest = {
    'version': VERSION,
    'created_at_utc': datetime.now(timezone.utc).isoformat(),
    'source_revision': revision,
    'source_revision_type': 'working-tree',
    'source_base_commit': revision,
    'source_snapshot_sha256': snapshot_hash,
    'source_runtime_file_count': len(source),
    'source_working_tree_changes': dirty_runtime,
    'supported_baselines': list(baselines),
    'runtime_file_count': len(payload),
    'files': {name: {'sha256': item['sha256'], 'accepted_previous_sha256': item['accepted']} for name, item in payload.items()},
    'source_runtime_sha256': source_hashes,
}
(STAGE / 'UPDATE-MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')

verify = (ROOT / 'work/jht-market-task-return-20261019/verify.py').read_text('utf-8')
entry_url = re.search(rb'src="(app\.js\?v=[^"]+)"', source['public/index.html']).group(1).decode()
verify = verify.replace('market-task-return-1', VERSION).replace('app.js?v=' + VERSION, entry_url)
verify += '\nfrom pathlib import Path\nimport hashlib\n'
verify += 'expected = ' + repr({name: item['sha256'] for name, item in payload.items()}) + '\n'
verify += "for name, expected_sha in expected.items():\n    assert hashlib.sha256((Path('/app') / name).read_bytes()).hexdigest() == expected_sha, 'Installed file differs: ' + name\n"
verify += "for name in ('public/app.js', 'public/asset-mentions.js', 'public/mention-editor.js', 'public/home-composer.js', 'public/asset-library.js', 'public/node-player.js', 'public/admin-logs.js', 'public/admin-logs.css'):\n    with urlopen(base + '/' + name.removeprefix('public/') + '?v=" + VERSION + "', timeout=5) as response:\n        assert hashlib.sha256(response.read()).hexdigest() == expected[name], 'Served file differs: ' + name\n"
verify += "styles = json.loads(Path('/app/resources/visual-styles.json').read_text('utf-8'))['styles']\nassert len(styles) == 152, 'Style catalogue incomplete'\n"
verify += "for style in styles:\n    path = Path('/app/public') / style['preview_url'].split('?')[0].lstrip('/')\n    assert hashlib.sha256(path.read_bytes()).hexdigest() == style['preview_sha256'], 'Style thumbnail mismatch: ' + path.name\n"
verify += "print('PASS: current runtime hashes, served UI modules and all 152 style thumbnails')\n"
verify += "from generation_queue import LIMITS, GLOBAL_LIMITS\nassert LIMITS['task'] == 10 and GLOBAL_LIMITS['task'] == 100, 'Video concurrency policy mismatch'\n"
verify += "assert b'id=\"adminLogsNav\"' in html and b'admin-logs.css?v=1' in html, 'Admin logs entry missing'\n"
verify += "for route in ('users', 'summary', 'operations', 'tasks', 'tasks/task/missing'):\n    try:\n        with urlopen(base + '/api/admin/logs/' + route, timeout=5) as response:\n            status = response.status\n    except HTTPError as error:\n        status = error.code\n        error.close()\n    assert status == 401, 'Admin logs route must require authentication: ' + route\n"
verify += "print('PASS: video limits 10/account and 100/system; admin log entry, assets and authentication')\n"
(STAGE / 'verify.py').write_text(verify, encoding='utf-8', newline='\n')
compile(verify, 'verify.py', 'exec')

for version, snapshot in baselines.items():
    with tempfile.TemporaryDirectory(prefix='jht-release-patch-') as directory:
        fixture = Path(directory)
        (fixture / 'auth_service.py').write_bytes(source['auth_service.py'])
        for name in payload:
            path = fixture / name
            if snapshot[name] is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(snapshot[name])
        (fixture / '.env').write_text('FIXTURE=preserve\n')
        (fixture / 'data/uploads').mkdir(parents=True)
        (fixture / 'data/uploads/keep.txt').write_bytes(b'preserve media fixture')

        def run(action, success=True, script=STAGE / 'patch.py'):
            result = subprocess.run([sys.executable, str(script), action, '--root', str(fixture)], capture_output=True, text=True)
            output = result.stdout + result.stderr
            assert (result.returncode == 0) == success, output
            return output

        def current_files():
            return {name: (fixture / name).read_bytes() if (fixture / name).exists() else None for name in payload}

        # Corrupt payloads and unknown installed files must fail before any write.
        corrupt = fixture / 'corrupt-installer.py'
        corrupt.write_text(installer.replace(payload['app.py']['sha256'], '0' * 64, 1), encoding='utf-8', newline='\n')
        assert 'Corrupt payload' in run('apply', False, corrupt)
        assert current_files() == {name: snapshot[name] for name in payload}
        assert not (fixture / '.deployment-patches').exists()
        original = (fixture / 'app.py').read_bytes()
        (fixture / 'app.py').write_bytes(original + b'\n# unknown version\n')
        before_rejection = current_files()
        assert 'Version mismatch' in run('apply', False)
        assert current_files() == before_rejection
        assert not (fixture / '.deployment-patches').exists()
        (fixture / 'app.py').write_bytes(original)
        assert 'SUCCESS' in run('apply')
        assert current_files() == {name: source[name] for name in payload}
        assert 'ALREADY APPLIED' in run('apply')
        patched = (fixture / 'app.py').read_bytes()
        (fixture / 'app.py').write_bytes(patched + b'\n# newer change\n')
        before_rejection = current_files()
        assert 'refuses to overwrite' in run('rollback', False)
        assert current_files() == before_rejection
        (fixture / 'app.py').write_bytes(patched)
        assert 'SUCCESS' in run('rollback')
        assert 'ALREADY ROLLED BACK' in run('rollback')
        assert current_files() == {name: snapshot[name] for name in payload}
        assert 'SUCCESS' in run('apply')
        assert (fixture / '.env').read_text() == 'FIXTURE=preserve\n'
        assert (fixture / 'data/uploads/keep.txt').read_bytes() == b'preserve media fixture'
    print('PASS:', version, 'apply/reapply/rollback, corrupt payload and version protections, data preserved', flush=True)

subprocess.run([BASH, '-n', str(STAGE / 'apply.sh')], check=True)
for name in changed:
    if name.endswith('.py'):
        compile(source[name], name, 'exec')
    elif name.endswith('.js'):
        subprocess.run([str(NODE), '--check', str(ROOT / name)], check=True, capture_output=True)
styles = json.loads(source['resources/visual-styles.json'])['styles']
assert len(styles) == 152
for style in styles:
    assert sha(source['public/' + style['preview_url'].split('?')[0].lstrip('/')]) == style['preview_sha256']
print('PASS: Python/JavaScript/shell syntax and 152 style thumbnails', flush=True)

package_files = ['apply.sh', 'patch.py', 'verify.py', 'README.zh-CN.md', 'UPDATE-MANIFEST.json']
(STAGE / 'SHA256SUMS').write_text(''.join(sha((STAGE / name).read_bytes()) + '  ' + name + '\n' for name in package_files), encoding='ascii', newline='\n')
package_files.append('SHA256SUMS')
subprocess.run([BASH, '-c', 'sha256sum -c SHA256SUMS'], cwd=STAGE, check=True)
assert all((ROOT / name).read_bytes() == data for name, data in source.items()), 'Runtime source changed during packaging; rebuild required.'
with tarfile.open(ARCHIVE, 'x:gz') as tar:
    for name in package_files:
        info = tar.gettarinfo(STAGE / name, arcname=NAME + '/' + name)
        info.uid = info.gid = 0
        info.uname = info.gname = 'root'
        info.mode = 0o755 if name == 'apply.sh' else 0o644
        with (STAGE / name).open('rb') as stream:
            tar.addfile(info, stream)
Path(str(ARCHIVE) + '.sha256').write_text(sha(ARCHIVE.read_bytes()) + '  ' + ARCHIVE.name + '\n', encoding='ascii', newline='\n')
with tarfile.open(ARCHIVE, 'r:gz') as tar:
    assert {m.name for m in tar.getmembers()} == {NAME + '/' + name for name in package_files}
    for name in package_files:
        assert tar.extractfile(NAME + '/' + name).read() == (STAGE / name).read_bytes(), name
subprocess.run([BASH, '-c', 'sha256sum -c ' + ARCHIVE.name + '.sha256'], cwd=OUT, check=True)
print('PASS: archive contents and SHA-256 verified', flush=True)
print('Base commit:', revision)
print('Working runtime changes:', len(dirty_runtime))
print('Runtime files:', len(payload), 'Archive bytes:', ARCHIVE.stat().st_size)
print(ARCHIVE)
