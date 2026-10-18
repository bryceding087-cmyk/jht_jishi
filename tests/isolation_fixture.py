"""Disposable UI fixture for member/admin login and account isolation."""
import json
from pathlib import Path
import socket
import sys
import threading
import time

sys.path[:0] = [str(Path.cwd()), str(Path.cwd() / 'tests')]
import uvicorn
from test_user_isolation import UserIsolationTests

UserIsolationTests.setUpClass()
case = UserIsolationTests()
server = None
try:
    case.setUp()
    host = case.h
    host.save('project', {'id': 'unknown-history', 'name': '归属待确认的历史画布', 'nodes': [], 'created_at': time.time()})
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
    print('FIXTURE ' + json.dumps({'origin': origin}), flush=True)
    sys.stdin.readline()
finally:
    if server:
        server.should_exit = True
        thread.join(10)
    UserIsolationTests.tearDownClass()
