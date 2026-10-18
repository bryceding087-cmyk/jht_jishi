"""Disposable local UI fixture: viewport loading, canvas info and attribution.

Run from the repository with the virtualenv Python; open the printed origin.
Uses temporary storage and synthetic media only. Enter a newline to stop.
Request observations and optional slow/error controls live in outputs/runtime.
"""
import asyncio
import io
import json
from pathlib import Path
import socket
import sys
import threading
import time
import wave

sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / 'tests')]
from PIL import Image, ImageDraw
from starlette.responses import JSONResponse
import uvicorn
from agent_media import execute
from auth_service import hash_password
from creator_metadata import creator_scope, user_creator
from test_canvas_groups import CanvasGroupsTests

CanvasGroupsTests.setUpClass()
host = CanvasGroupsTests.host
runtime = Path('outputs/runtime')
runtime.mkdir(parents=True, exist_ok=True)
control = runtime / 'viewport-fixture-control.json'
traffic = runtime / 'viewport-fixture-media.jsonl'
control.write_text(json.dumps({'delay': 1.5, 'fail': ''}), encoding='utf-8')
traffic.write_text('', encoding='utf-8')
server = None
try:
    with host.connect() as db:
        db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password('viewport-test'), 'admin'))
        row = db.execute('SELECT id, username, display_name FROM auth_users WHERE username=?', ('admin',)).fetchone()
    creator = user_creator(dict(zip(('id', 'username', 'display_name'), row)))
    video = host.DATA / 'fixture-video.mp4'
    execute(['-f', 'lavfi', '-i', 'testsrc2=size=320x200:rate=12', '-t', '1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)])
    sound = io.BytesIO()
    with wave.open(sound, 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(bytes(32000))
    assets = []
    names = {}
    with creator_scope(creator):
        for i in range(32):
            number = f'{i + 1:02}'
            if i % 8 == 6:
                data, name = video.read_bytes(), f'验证视频-{number}.mp4'
            elif i % 8 == 5:
                data, name = sound.getvalue(), f'验证音频-{number}.wav'
            else:
                picture = Image.new('RGB', (480, 300), (28 + i * 4, 72 + i * 3, 100 + i * 2))
                painter = ImageDraw.Draw(picture)
                painter.ellipse((210, 30, 390, 210), fill=(110 + i * 3, 180, 190))
                painter.polygon([(0, 270), (160, 100), (350, 300)], fill='#30494c')
                painter.text((25, 25), number, fill='white', font_size=50)
                image = io.BytesIO()
                picture.save(image, 'PNG')
                data, name = image.getvalue(), f'验证图片-{number}.png'
            asset = host.store_uploaded_asset(data, name)
            assets.append(asset)
            names[asset['url']] = name
            nodes = [{'id': 'media', 'type': 'asset', 'assetId': asset['id']}]
            if i == 31:
                nodes += [{'id': kind, 'type': kind} for kind in ('prompt', 'video', 'audio', 'timeline', 'character', 'unknown')]
            host.create_project(host.Project(name=f'验证画布 {number}', nodes=nodes))

    @host.app.middleware('http')
    async def fixture_requests(request, call_next):
        path = request.url.path
        if path.startswith('/uploads/'):
            with traffic.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps({'name': names.get(path, path), 'url': path, 'time': time.time()}, ensure_ascii=False) + '\n')
        if path in ('/api/projects', '/api/project-folders', '/api/assets', '/api/asset-folders'):
            settings = json.loads(control.read_text(encoding='utf-8-sig'))
            await asyncio.sleep(settings.get('delay', 0))
            if settings.get('fail') == path:
                return JSONResponse({'detail': '验证：暂时无法读取，请重试'}, status_code=503)
        return await call_next(request)

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(host.app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError('Fixture startup timed out')
        time.sleep(.05)
    print('FIXTURE ' + json.dumps({'origin': origin, 'assets': len(assets), 'creator': creator}), flush=True)
    sys.stdin.readline()
finally:
    if server:
        server.should_exit = True
        thread.join(10)
    CanvasGroupsTests.tearDownClass()
