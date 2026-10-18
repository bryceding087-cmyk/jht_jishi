const fixture = String.raw`
import sys, io, json, socket, threading, time
from pathlib import Path
sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / 'tests')]
from test_canvas_groups import CanvasGroupsTests
from auth_service import hash_password
from PIL import Image
import uvicorn
CanvasGroupsTests.setUpClass()
host = CanvasGroupsTests.host
server = None
try:
    with host.connect() as db:
        db.execute('UPDATE auth_users SET password_hash=? WHERE username=?', (hash_password('viewport-test'), 'admin'))
    image = io.BytesIO()
    Image.new('RGB', (320, 200), '#809ca4').save(image, format='PNG')
    assets = [host.store_uploaded_asset(image.getvalue(), name, '') for name in ['viewport-source.png', 'viewport-second.png']]
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    origin = 'http://127.0.0.1:' + str(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(host.app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline: raise RuntimeError('Fixture startup timed out')
        time.sleep(.05)
    print('FIXTURE ' + json.dumps({'origin': origin, 'assets': assets}), flush=True)
    sys.stdin.readline()
finally:
    if server:
        server.should_exit = True
        thread.join(10)
    CanvasGroupsTests.tearDownClass()
`;
module.exports = { fixture };
