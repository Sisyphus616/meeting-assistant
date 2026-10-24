"""Exercise the real server with paths stripped by Jupyter's port proxy."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from websockets.asyncio.client import connect


def test_rewritten_proxy_paths_serve_assets_api_and_websocket(tmp_path):
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    root = Path(__file__).resolve().parent.parent
    prefix = f'/user/test-account/proxy/{port}'
    env = dict(os.environ, PROVIDER='demo', ROOT_PATH=prefix,
               DATA_DIR=str(tmp_path), HOST='127.0.0.1', PORT=str(port))
    with (tmp_path / 'server.log').open('w') as log:
        process = subprocess.Popen([sys.executable, 'scripts/run.py'], cwd=root,
                                   env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False) as client:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        response = client.get('/api/health')
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert process.poll() is None, 'Server exited during startup'
                    assert time.monotonic() < deadline, 'Server startup timed out'
                    time.sleep(.05)
                for path in ['/', '/static/app.js', '/static/style.css', '/static/pcm-worklet.js']:
                    assert client.get(path).status_code == 200, path
                mid = client.post('/api/meetings', json={'title': 'Proxy test'}).json()['id']

                async def check_socket():
                    async with connect(f'ws://127.0.0.1:{port}/ws/{mid}', proxy=None) as ws:
                        assert json.loads(await ws.recv())['type'] == 'snapshot'
                        await ws.send(json.dumps({'type': 'demo_next'}))
                        await ws.send(json.dumps({'type': 'finish'}))
                        while True:
                            event = json.loads(await asyncio.wait_for(ws.recv(), 10))
                            assert event['type'] != 'error', event
                            if event['type'] == 'finished':
                                return

                asyncio.run(check_socket())
                meeting = client.get(f'/api/meetings/{mid}').json()
                assert meeting['status'] == 'completed' and len(meeting['segments']) == 1
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
