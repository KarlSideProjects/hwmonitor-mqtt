import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from test_release_assets import candidate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from release_backend import GitHub, drain_ready, prepare_candidate, reconcile_latest


@pytest.fixture
def service():
    state = {'releases': [], 'assets': {}, 'latest': None, 'fail_upload': False, 'fail_publish': False, 'next': 1, 'writes': []}
    lock = threading.RLock()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, obj=None):
            payload = json.dumps(obj).encode() if obj is not None else b''
            self.send_response(status)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def handle_request(self):
            path = urlsplit(self.path)
            suffix = path.path.split('/hwmonitor-mqtt-releases', 1)[1]
            data = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            with lock:
                if self.command != 'GET':
                    state['writes'].append((self.command, suffix))
                if suffix == '/releases':
                    if self.command == 'GET':
                        self.reply(200, state['releases'])
                    else:
                        release = json.loads(data)
                        release['id'] = state['next']; state['next'] += 1
                        state['releases'].append(release)
                        state['assets'][release['id']] = []
                        self.reply(201, release)
                    return
                if suffix == '/releases/latest':
                    release = next((r for r in state['releases'] if r['id'] == state['latest']), None)
                    self.reply(200 if release else 404, release)
                    return
                parts = suffix.split('/')
                if len(parts) == 4 and parts[2] == 'assets':
                    asset_id = int(parts[3])
                    for assets in state['assets'].values():
                        assets[:] = [a for a in assets if a['id'] != asset_id]
                    self.reply(204)
                    return
                release = next((r for r in state['releases'] if r['id'] == int(parts[2])), None)
                if release is None:
                    self.reply(404); return
                if len(parts) == 3:
                    if self.command == 'GET':
                        self.reply(200, release); return
                    update = json.loads(data)
                    if update.get('draft') is False and state['fail_publish']:
                        state['fail_publish'] = False; self.reply(502); return
                    if update.get('make_latest') == 'true':
                        state['latest'] = release['id']
                    release.update({k: v for k, v in update.items() if k != 'make_latest'})
                    self.reply(200, release); return
                assets = state['assets'][release['id']]
                if self.command == 'GET':
                    self.reply(200, assets); return
                if state['fail_upload']:
                    state['fail_upload'] = False; self.reply(502); return
                name = parse_qs(path.query)['name'][0]
                if any(a['name'] == name for a in assets):
                    self.reply(422); return
                asset = {'id': state['next'], 'name': name, 'state': 'uploaded', 'size': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest()}
                state['next'] += 1; assets.append(asset)
                self.reply(201, asset)
        do_GET = do_POST = do_PATCH = do_DELETE = handle_request

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield GitHub(test_api=f'http://127.0.0.1:{server.server_port}'), state
    server.shutdown(); thread.join(); server.server_close()


def make_candidate(root, n):
    root.mkdir()
    candidate(root)
    metadata = {'source': f'{n:040x}', 'version': f'0.1.{n}'}
    (root / 'version.json').write_text(json.dumps(metadata))
    metadata['assets'] = {p.name: {'size': p.stat().st_size, 'digest': 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest()} for p in root.iterdir()}
    return metadata


def test_resume_and_monotonic_latest(service, tmp_path):
    api, state = service
    one, two = make_candidate(tmp_path / 'one', 1), make_candidate(tmp_path / 'two', 2)
    state['fail_upload'] = True
    with pytest.raises(ValueError): prepare_candidate(api, one, tmp_path / 'one')
    assert state['releases'][0]['draft'] and state['latest'] is None
    release = prepare_candidate(api, two, tmp_path / 'two')
    state['fail_publish'] = True
    with pytest.raises(ValueError): drain_ready(api)
    drain_ready(api)
    assert state['latest'] == release['id']
    prepare_candidate(api, one, tmp_path / 'one')
    drain_ready(api)
    assert state['latest'] == release['id']
    writes = list(state['writes'])
    prepare_candidate(api, two, tmp_path / 'two')
    drain_ready(api)
    assert state['writes'] == writes
    state['assets'][release['id']][0]['digest'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError): reconcile_latest(api)


def test_receipt_missing_corrupt_and_duplicates_rejected(service, tmp_path):
    api, state = service
    receipt = make_candidate(tmp_path / 'one', 1)
    (tmp_path / 'one' / 'sender-linux-x64.tar.gz').unlink()
    with pytest.raises(ValueError): prepare_candidate(api, receipt, tmp_path / 'one')
    assert not state['writes']
    receipt = make_candidate(tmp_path / 'two', 2)
    (tmp_path / 'two' / 'sender-linux-x64.tar.gz').write_bytes(b'corrupt')
    with pytest.raises(ValueError): prepare_candidate(api, receipt, tmp_path / 'two')
    assert not state['writes']
    receipt = make_candidate(tmp_path / 'three', 3)
    release = prepare_candidate(api, receipt, tmp_path / 'three')
    state['releases'].append(dict(release, id=999))
    with pytest.raises(ValueError): drain_ready(api)


def test_distinct_preparation_and_source_serialized_retry(service, tmp_path):
    api, state = service
    receipts = [make_candidate(tmp_path / str(n), n) for n in (1, 2)]
    source_locks = {1: threading.Lock(), 2: threading.Lock()}
    def prepare(n):
        # Actions enforces this lock for same-source jobs, not the draft API.
        with source_locks[n]:
            return prepare_candidate(api, receipts[n-1], tmp_path / str(n))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(prepare, (1, 1)))
    assert results[0]['id'] == results[1]['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(prepare, (1, 2)))
    assert len(state['releases']) == 2
    drain_ready(api)
    assert state['latest'] == state['releases'][1]['id']


def test_starter_recovery_and_restart_after_publication(service, tmp_path):
    api, state = service
    receipt = make_candidate(tmp_path / 'candidate', 1)
    state['fail_upload'] = True
    with pytest.raises(ValueError): prepare_candidate(api, receipt, tmp_path / 'candidate')
    release = state['releases'][0]
    name = 'sender-linux-x64.tar.gz'
    state['assets'][release['id']].append({'id': 999, 'name': name, 'state': 'starter', 'size': 0})
    prepare_candidate(api, receipt, tmp_path / 'candidate')
    assert all(a['id'] != 999 for a in state['assets'][release['id']])
    # A crash after the publication PATCH but before latest reconciliation.
    release['draft'] = False
    assert state['latest'] is None
    drain_ready(api)
    assert state['latest'] == release['id']


def test_loopback_transport_is_explicit():
    with pytest.raises(ValueError): GitHub(test_api='https://example.com')
    with pytest.raises(ValueError): GitHub()
