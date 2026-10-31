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
NAME = 'jht-video-reference-compression-20261031'
VERSION = 'video-reference-compression-20261031-1'
FILES = ('app.py', 'auth_service.py', 'generation_queue.py', 'video_callbacks.py', 'conversation_service.py',
         'video_service.py', 'video_file_recovery.py', 'public/asset-library.js', 'public/asset-history.css',
         'public/app.js', 'public/home-composer.js', 'public/index.html', 'provider_adapters.py', 'market_video_models.py', 'public/node-player.js', 'public/node-player.css', 'video_reference_images.py')
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
    callbacks_v1 = {name: base64.b64decode(first_release[name]['data']) if name in first_release else head[name] for name in FILES}
    v2_path = ROOT / 'work/jht-video-callback-update-20261030-v2/patch.py'
    v2_payload = ast.literal_eval(payload_node(v2_path.read_text('utf-8')).value)
    callbacks_v2 = {name: base64.b64decode(v2_payload[name]['data']) if name in v2_payload else head[name] for name in FILES}
    recovery_payload = ast.literal_eval(payload_node((ROOT/'work/jht-video-file-recovery-20261031/patch.py').read_text('utf-8')).value)
    recovery = {name: base64.b64decode(recovery_payload[name]['data']) if name in recovery_payload else head[name] for name in FILES}
    wan_payload = ast.literal_eval(payload_node((ROOT/'work/jht-wan3-aimarket-20261031/patch.py').read_text('utf-8')).value)
    wan = {name: base64.b64decode(wan_payload[name]['data']) if name in wan_payload else head[name] for name in FILES}
    polling_payload = ast.literal_eval(payload_node((ROOT/'work/jht-polling-stability-20261031/patch.py').read_text('utf-8')).value)
    polling = {name: base64.b64decode(polling_payload[name]['data']) if name in polling_payload else head[name] for name in FILES}
    triangle_payload = ast.literal_eval(payload_node((ROOT/'work/jht-triangle-video-loading-20261031/patch.py').read_text('utf-8')).value)
    triangle = {name: base64.b64decode(triangle_payload[name]['data']) if name in triangle_payload else head[name] for name in FILES}
    canvas_payload = ast.literal_eval(payload_node((ROOT/'work/jht-canvas-timeout-state-20261031/patch.py').read_text('utf-8')).value)
    canvas = {name: base64.b64decode(canvas_payload[name]['data']) if name in canvas_payload else head[name] for name in FILES}
    baselines = (('current-source', head), ('account-video-update', older), ('video-callback-v1', callbacks_v1), ('video-callback-v2', callbacks_v2), ('video-file-recovery', recovery), ('wan3-aimarket', wan), ('polling-stability', polling), ('triangle-video-loading', triangle), ('canvas-timeout-state', canvas))
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
    (STAGE / 'README.zh-CN.md').write_bytes((ROOT / 'docs/video-reference-compression-20261031.md').read_bytes())
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
    verify += """if len(sys.argv)<2 or sys.argv[1]=='apply':
    from server import load_deployment_config
    from model_gateway import DOCKER_MODEL_ORIGIN
    load_deployment_config(model_origin_default=DOCKER_MODEL_ORIGIN)
    import app as application
    from market_video_models import WAN_MODEL_ID
    model = application.internal_registry_model('video', WAN_MODEL_ID)
    assert model['base_url'] == DOCKER_MODEL_ORIGIN, 'Server model gateway must use the internal origin; check JIANHUITONG_MARKET_MODEL_ORIGIN'
    assert model['model_id'] == 'wan3.0-video'
    assert model['query_path'] == '/v1/tasks/{task_id}'
    assert model['api_path'] == '/v1/services/aigc/video-generation/video-synthesis'
    assert not model.get('is_default'), 'Existing default model should be preserved'
    print('PASS: shared Wan 3.0 model installed; internal gateway: ' + model['base_url'])
"""
    compile(verify, 'verify.py', 'exec')
    (STAGE / 'verify.py').write_text(verify, encoding='utf-8', newline='\n')
    for label, baseline in baselines:
        with tempfile.TemporaryDirectory(prefix='jht-video-file-release-') as directory:
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
