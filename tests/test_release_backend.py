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
from release_backend import GitHub, drain_ready, identity, prepare_candidate, reconcile_latest


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
                    # GitHub can retag drafts when a PATCH omits tag_name.
                    if release['draft'] and 'tag_name' not in update:
                        release['tag_name'] = 'untagged-53a2b2d1d4319c06414f'
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


def test_draft_ready_and_publication_preserve_intended_tag(service, tmp_path):
    api, state = service
    receipt = make_candidate(tmp_path / 'candidate', 1)
    release = prepare_candidate(api, receipt, tmp_path / 'candidate')
    assert release['tag_name'] == 'v0.1.1'
    drain_ready(api)
    assert state['releases'][0]['tag_name'] == 'v0.1.1'
    assert state['releases'][0]['draft'] is False
    assert state['latest'] == release['id']


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


def test_ready_retry_reuses_saved_assets_after_rebuild(service, tmp_path):
    api, state = service
    receipt = make_candidate(tmp_path / 'candidate', 1)
    release = prepare_candidate(api, receipt, tmp_path / 'candidate')
    rebuilt = make_candidate(tmp_path / 'rebuilt', 1)
    path = tmp_path / 'rebuilt' / 'sender-linux-x64.tar.gz'
    path.write_bytes(b'new build bytes')
    (path.parent / (path.name + '.sha256')).write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
    rebuilt['assets'] = {p.name: {'size': p.stat().st_size, 'digest': 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest()} for p in path.parent.iterdir()}
    writes = list(state['writes'])
    assert prepare_candidate(api, rebuilt, path.parent)['id'] == release['id']
    assert state['writes'] == writes


def test_zero_size_receipt_rejected(service, tmp_path):
    api, state = service
    receipt = make_candidate(tmp_path / 'candidate', 1)
    receipt['assets']['version.json']['size'] = 0
    with pytest.raises(ValueError): prepare_candidate(api, receipt, tmp_path / 'candidate')
    assert not state['writes']


def test_changed_rebuild_replaces_interrupted_staging_without_mixing(service, tmp_path):
    api, state = service
    old = make_candidate(tmp_path / 'old', 1)
    staged = prepare_candidate(api, old, tmp_path / 'old')
    staged = state['releases'][0]
    staged['body'] = staged['body'].replace('\"READY\"', '\"STAGING\"')
    new = make_candidate(tmp_path / 'new', 1)
    path = tmp_path / 'new' / 'sender-linux-x64.tar.gz'
    path.write_bytes(b'rebuilt')
    (path.parent / (path.name + '.sha256')).write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
    new['assets'] = {p.name: {'size': p.stat().st_size, 'digest': 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest()} for p in path.parent.iterdir()}
    state['fail_upload'] = True
    with pytest.raises(ValueError): prepare_candidate(api, new, path.parent)
    release = prepare_candidate(api, new, path.parent)
    assert len(state['releases']) == 1
    assert {a['name']: a['digest'] for a in state['assets'][release['id']]} == {n: v['digest'] for n, v in new['assets'].items()}


def test_oversized_receipt_rejected(service, tmp_path):
    api, state = service
    receipt = make_candidate(tmp_path / 'candidate', 1)
    receipt['assets']['version.json']['size'] = 1024 ** 3 + 1
    with pytest.raises(ValueError): identity(receipt)
    assert not state['writes']


@pytest.mark.parametrize('mode', ['api', 'rate-limit', 'transport', 'digest', 'latest', 'receipt', 'unknown', 'dynamic-metadata'])
def test_exact_public_verification_is_read_only_and_resumable(service, tmp_path, monkeypatch, mode):
    import io
    import release_backend as backend
    from urllib.error import URLError
    api, state = service
    directory = tmp_path / 'candidate'
    receipt = make_candidate(directory, 1)
    release = prepare_candidate(api, receipt, directory)
    drain_ready(api)
    release = state['releases'][0]
    writes = list(state['writes'])
    base = 'https://github.com/KarlSideProjects/hwmonitor-mqtt-releases/releases'
    calls = []
    class Response(io.BytesIO):
        status = 200
        def __init__(self, data, url):
            super().__init__(data)
            self.url = url
        def geturl(self):
            return self.url
    class Anonymous:
        def open(self, request, timeout):
            assert not request.has_header('Authorization')
            url = request.full_url
            calls.append(url)
            if mode == 'transport':
                raise URLError('injected post-publication failure')
            if url.startswith('https://api.github.com'):
                if mode == 'rate-limit':
                    from urllib.error import HTTPError
                    raise HTTPError(url, 403, 'rate limited', {}, None)
                return Response(json.dumps(release).encode(), url)
            if url.endswith('/latest'):
                return Response(b'page', base + ('/tag/v0.1.2' if mode == 'latest' else '/tag/v0.1.1'))
            if '/download/' in url:
                name = url.rsplit('/', 1)[1]
                data = (directory / name).read_bytes()
                return Response(b'bad' if mode == 'digest' else data, url)
            return Response(b'page', url)
    from urllib.request import build_opener as real_opener
    # Only anonymous requests use the double; authenticated reads retain the local transport.
    monkeypatch.setattr(backend, 'build_opener', lambda *handlers: real_opener(*handlers) if handlers else Anonymous())
    if mode == 'dynamic-metadata':
        original = api.request
        def request(method, path, *args, **kwargs):
            status, row = original(method, path, *args, **kwargs)
            if method == 'GET' and path == '/releases/latest':
                row['download_count'] = 7
            return status, row
        monkeypatch.setattr(api, 'request', request)
    if mode == 'receipt':
        receipt = dict(receipt, source='f' * 40)
    if mode == 'unknown':
        release['body'] = release['body'].replace('READY', 'UNKNOWN')
    if mode in ('digest', 'latest', 'receipt', 'unknown'):
        with pytest.raises(ValueError):
            backend.verify_public(api, release['id'], receipt)
    else:
        report = backend.verify_public(api, release['id'], receipt)
        assert report['state'] == ('published-but-verification-pending' if mode == 'transport' else 'verified')
        assert report['release_id'] == release['id'] and report['receipt'] == receipt
        if mode == 'transport':
            mode = 'api'
            assert backend.verify_public(api, release['id'], receipt)['state'] == 'verified'
        assert len([url for url in calls if '/download/' in url]) == 15
    assert state['writes'] == writes


def test_read_only_verification_rejects_latest_downgrade_and_unpublished(service, tmp_path):
    from release_backend import verify_public
    api, state = service
    first = make_candidate(tmp_path / 'first', 1)
    second = make_candidate(tmp_path / 'second', 2)
    one = prepare_candidate(api, first, tmp_path / 'first')
    drain_ready(api)
    two = prepare_candidate(api, second, tmp_path / 'second')
    writes = list(state['writes'])
    with pytest.raises(ValueError, match='exact published'):
        verify_public(api, two['id'], second)
    assert state['writes'] == writes
    drain_ready(api)
    state['latest'] = one['id']
    writes = list(state['writes'])
    with pytest.raises(ValueError, match='downgrade'):
        verify_public(api, two['id'], second)
    assert state['writes'] == writes
