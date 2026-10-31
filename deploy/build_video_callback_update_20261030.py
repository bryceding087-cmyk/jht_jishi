"""Build a credential-free, version-checked and reversible video update."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'jht-video-callback-update-20261030-v2'
VERSION = 'video-callback-update-20261030-2'
FILES = ('app.py', 'auth_service.py', 'generation_queue.py', 'video_callbacks.py', 'conversation_service.py',
         'public/app.js', 'public/home-composer.js', 'public/index.html')
STAGE = ROOT / 'work' / NAME
ARCHIVE = ROOT / 'outputs' / (NAME + '.tar.gz')


def sha(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def payload_node(text):
    return next(n for n in ast.parse(text).body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == 'PAYLOAD' for t in n.targets))


def main():
    if ARCHIVE.exists():
        raise SystemExit('Release already exists; choose a new version instead of overwriting.')
    previous_path = ROOT / 'work/jht-liufangya-video-update-20261029/patch.py'
    previous = ast.literal_eval(payload_node(previous_path.read_text('utf-8')).value)
    first_release_path = ROOT / 'work/jht-video-callback-update-20261030/patch.py'
    first_release = ast.literal_eval(payload_node(first_release_path.read_text('utf-8')).value)
    source = {name: (ROOT / name).read_bytes() for name in FILES}
    head = {}
    for name in FILES:
        result = subprocess.run(['git', 'show', 'HEAD:' + name], cwd=ROOT, capture_output=True)
        head[name] = result.stdout if result.returncode == 0 else None
    older = {name: base64.b64decode(previous[name]['data']) if name in previous else head[name] for name in FILES}
    callbacks_v1 = {name: base64.b64decode(first_release[name]['data']) for name in FILES}
    baselines = (('current-source', head), ('account-video-update', older), ('video-callback-v1', callbacks_v1))
    payload = {}
    for name, data in source.items():
        assert not re.search(rb'ark-[a-zA-Z0-9-]{30,}|sk-[a-zA-Z0-9]{32,}', data), name
        accepted = []
        for _, files in baselines:
            baseline = files[name]
            lf = baseline.replace(b'\r\n', b'\n') if baseline is not None else None
            accepted.extend(sha(v) for v in (baseline, lf, lf.replace(b'\n', b'\r\n') if lf is not None else None))
        payload[name] = {'sha256': sha(data), 'accepted': list(dict.fromkeys(accepted)),
                         'data': base64.b64encode(data).decode('ascii')}
    template = (ROOT / 'work/jht-market-task-return-20261019/patch.py').read_text('utf-8')
    node = payload_node(template)
    lines = template.splitlines(keepends=True)
    installer = ''.join(lines[:node.lineno-1]) + 'PAYLOAD = ' + repr(payload) + '\n' + ''.join(lines[node.end_lineno:])
    installer = re.sub(r"    if sha\(read\(root / 'auth_service.py'\)\).*?    paths =", '    paths =', installer, flags=re.S)
    installer = installer.replace('market-task-return-1', VERSION)
    compile(installer, 'patch.py', 'exec')
    STAGE.mkdir(parents=True, exist_ok=True)
    (STAGE / 'patch.py').write_text(installer, encoding='utf-8', newline='\n')
    (STAGE / 'apply.sh').write_bytes((ROOT / 'work/jht-market-task-return-20261019/apply.sh').read_bytes())
    (STAGE / 'README.zh-CN.md').write_bytes((ROOT / 'docs/video-callbacks-20261030.md').read_bytes())
    verify = "import hashlib, json, sys, time\nfrom pathlib import Path\nfrom urllib.request import Request, urlopen\nfrom urllib.error import HTTPError\n"
    verify += 'expected = ' + repr({k: sha(v) for k, v in source.items()}) + '\n'
    verify += "if len(sys.argv)<2 or sys.argv[1]=='apply':\n    for name,digest in expected.items():\n        assert hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()==digest, name\n"
    verify += "for attempt in range(30):\n    try:\n        with urlopen('http://127.0.0.1:8000/api/health', timeout=3) as response:\n            assert json.load(response)['status']=='ok'\n        break\n    except Exception:\n        if attempt==29: raise\n        time.sleep(1)\nprint('PASS: installed source and application health')\n"
    verify += """if len(sys.argv)<2 or sys.argv[1]=='apply':
    for provider in ('ark', 'minimax'):
        request = Request('http://127.0.0.1:8000/api/video-callbacks/' + provider + '/connectivity-probe',
                          data=b'{}', headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urlopen(request, timeout=3) as response:
                raise AssertionError('Callback accepted an invalid task credential: ' + provider)
        except HTTPError as error:
            assert error.code == 404, (provider, error.code)
            assert json.load(error)['detail'] == '回调凭据无效或已过期', provider
    sys.path.insert(0, '/app')
    from dotenv import load_dotenv
    from video_callbacks import callback_base
    load_dotenv('/app/.env', override=False)
    base = callback_base()
    print('PASS: both callback routes validate task credentials without a browser login')
    print('Callback base URL: ' + (base or '(disabled by an explicit empty environment value)'))
    print('Public proxy and supplier connectivity still need verification after deployment.')
"""
    compile(verify, 'verify.py', 'exec')
    (STAGE / 'verify.py').write_text(verify, encoding='utf-8', newline='\n')
    for label, baseline in baselines:
        with tempfile.TemporaryDirectory(prefix='jht-callback-release-') as directory:
            root = Path(directory)
            for name, data in baseline.items():
                if data is not None:
                    path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            (root / '.env').write_text('FIXTURE=preserve\n')
            def run(action, success=True):
                result = subprocess.run([sys.executable, str(STAGE/'patch.py'), action, '--root', str(root)], capture_output=True)
                assert (result.returncode == 0) == success, result.stderr.decode(errors='replace')
            original = (root/'app.py').read_bytes()
            (root/'app.py').write_bytes(original+b'\n# newer file\n')
            run('apply', False)
            assert not (root/'.deployment-patches').exists()
            (root/'app.py').write_bytes(original)
            run('apply'); run('apply')
            assert all((root/n).read_bytes()==data for n,data in source.items())
            run('rollback'); run('rollback')
            assert all(((root/n).read_bytes() if (root/n).exists() else None)==data for n,data in baseline.items())
            assert (root/'.env').read_text()=='FIXTURE=preserve\n'
        print('PASS:', label, 'apply/reapply/rollback and unknown-file rejection')
    names = ['patch.py', 'apply.sh', 'verify.py', 'README.zh-CN.md']
    (STAGE/'SHA256SUMS').write_text(''.join(sha((STAGE/n).read_bytes())+'  '+n+'\n' for n in names), encoding='ascii', newline='\n')
    names.append('SHA256SUMS')
    assert all((ROOT/n).read_bytes()==data for n,data in source.items()), 'Source changed during packaging'
    with tarfile.open(ARCHIVE, 'x:gz') as archive:
        for name in names:
            info = archive.gettarinfo(STAGE/name, arcname=NAME+'/'+name)
            info.uid = info.gid = 0; info.uname = info.gname = 'root'
            info.mode = 0o755 if name=='apply.sh' else 0o644
            with (STAGE/name).open('rb') as stream: archive.addfile(info,stream)
    Path(str(ARCHIVE)+'.sha256').write_text(sha(ARCHIVE.read_bytes())+'  '+ARCHIVE.name+'\n', encoding='ascii', newline='\n')
    print(ARCHIVE)


if __name__ == '__main__':
    main()
