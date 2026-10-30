import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading

from test_release_assets import candidate


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/stage_release_draft.py'


def test_interrupted_upload_resumes_and_never_publishes(tmp_path):
    candidate(tmp_path)
    state = {'release': None, 'assets': {}, 'fail': True, 'writes': []}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_json(self, status, data):
            payload = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path.endswith('/releases?per_page=100&page=1'):
                self.send_json(200, [state['release']] if state['release'] else [])
            elif self.path.endswith('/releases/1/assets?per_page=100'):
                self.send_json(200, list(state['assets'].values()))
            else:
                self.send_json(404, {})

        def do_POST(self):
            data = self.rfile.read(int(self.headers['Content-Length']))
            with lock:
                state['writes'].append(self.path)
                if self.path.endswith('/releases'):
                    if state['release']:
                        self.send_json(422, {})
                        return
                    release = json.loads(data)
                    release['id'] = 1
                    state['release'] = release
                    self.send_json(201, release)
                else:
                    name = self.path.split('name=', 1)[1]
                    if state['fail'] and len(state['assets']) == 2:
                        state['fail'] = False
                        self.send_json(502, {})
                        return
                    if name in state['assets']:
                        self.send_json(422, {})
                        return
                    asset = {'id': len(state['assets']) + 1, 'name': name, 'state': 'uploaded',
                             'size': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
                    state['assets'][name] = asset
                    self.send_json(201, asset)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cmd = [sys.executable, str(SCRIPT), str(tmp_path), '--test-api',
               f'http://127.0.0.1:{server.server_port}']
        first = subprocess.run(cmd, capture_output=True)
        assert first.returncode != 0
        assert state['release']['draft'] is True
        second = subprocess.run(cmd, capture_output=True)
        assert second.returncode == 0, second.stderr
        assert len(state['assets']) == 11
        writes = list(state['writes'])
        assert subprocess.run(cmd, capture_output=True).returncode == 0
        assert state['writes'] == writes
        state['assets']['sender-linux-x64.tar.gz']['digest'] = 'sha256:' + '0' * 64
        assert subprocess.run(cmd, capture_output=True).returncode != 0
        assert state['writes'] == writes
        state['assets']['sender-linux-x64.tar.gz']['digest'] = 'sha256:' + hashlib.sha256((tmp_path / 'sender-linux-x64.tar.gz').read_bytes()).hexdigest()
        state['release']['draft'] = False
        assert subprocess.run(cmd, capture_output=True).returncode != 0
        assert state['writes'] == writes
        state.update(release=None, assets={}, fail=False, writes=[])
        one = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        two = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output_one = one.communicate(timeout=10)
        output_two = two.communicate(timeout=10)
        assert one.returncode == 0, output_one
        assert two.returncode == 0, output_two
        assert len(state['assets']) == 11
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
