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
            elif self.path.endswith('/releases/1'):
                self.send_json(200, state['release'])
            elif self.path.endswith('/releases/1/assets?per_page=100'):
                self.send_json(200, list(state['assets'].values()))
            else:
                self.send_json(404, {})

        def do_DELETE(self):
            asset_id = int(self.path.rsplit('/', 1)[1])
            for name, asset in list(state['assets'].items()):
                if asset['id'] == asset_id:
                    del state['assets'][name]
                    self.send_json(204, {})
                    return
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
        state['assets']['starter'] = {'id': 99, 'name': 'sender-linux-x64.tar.gz', 'state': 'starter', 'size': 0}
        second = subprocess.run(cmd, capture_output=True)
        assert second.returncode == 0, second.stderr
        assert len(state['assets']) == 15
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
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_draft_creation_rejects_source_already_assigned_another_tag(tmp_path):
    candidate(tmp_path)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            payload = json.dumps([{'id': 2, 'tag_name': 'v9.9.9', 'body': 'source: ' + 'a' * 40,
                                   'draft': True}]).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            raise AssertionError('must not write on identity conflict')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), '--test-api',
                                 f'http://127.0.0.1:{server.server_port}'], capture_output=True)
        assert result.returncode != 0
        assert b'identity' in result.stderr
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_duplicate_draft_tags_fail_closed(tmp_path):
    candidate(tmp_path)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            release = {'tag_name': 'v1.2.3', 'body': 'source: ' + 'a' * 40, 'draft': True}
            payload = json.dumps([dict(release, id=1), dict(release, id=2)]).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            raise AssertionError('duplicate drafts must not be modified')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path), '--test-api',
                                 f'http://127.0.0.1:{server.server_port}'], capture_output=True)
        assert result.returncode != 0
        assert b'duplicate release tag' in result.stderr
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
