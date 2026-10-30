import hashlib
import json
import subprocess

import pytest

from test_release_backend import make_candidate, service
from release_controller import prepare


def setup(repo, assets, evidence):
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    for name in ('bootstrap.sh', 'bootstrap.ps1'):
        (repo / name).write_text(name)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'test'], check=True)
    source = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    make_candidate(assets, 1)
    (assets / 'version.json').write_text(json.dumps({'source': source, 'version': '0.1.1'}))
    for ext in ('sh', 'ps1'):
        name = 'install.' + ext
        data = (repo / ('bootstrap.' + ext)).read_bytes()
        (assets / name).write_bytes(data)
        (assets / (name + '.sha256')).write_text(hashlib.sha256(data).hexdigest() + '  ' + name + '\n')
    for platform in ('linux-x64', 'linux-arm64', 'windows-x64'):
        folder = evidence / platform
        folder.mkdir(parents=True)
        (folder / 'record.json').write_text(json.dumps({'source': source, 'version': '0.1.1', 'platform': platform}))
    return source


def test_controller_inspects_all_platforms_before_staging(service, tmp_path):
    api, state = service
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    seen = []
    prepare(api, repo, source, source, assets, evidence, lambda args: seen.append(args))
    assert len(seen) == 3
    assert state['releases'][0]['draft'] is True


def test_controller_rejects_mismatched_build_record_before_writes(service, tmp_path):
    api, state = service
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    (evidence / 'linux-arm64' / 'record.json').write_text('{}')
    with pytest.raises(ValueError):
        prepare(api, repo, source, source, assets, evidence, lambda args: None)
    assert not state['writes']


def test_controller_inspection_failure_never_stages(service, tmp_path):
    api, state = service
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    def reject(args):
        raise subprocess.CalledProcessError(1, args)
    with pytest.raises(subprocess.CalledProcessError):
        prepare(api, repo, source, source, assets, evidence, reject)
    assert not state['writes']


def test_controller_rejects_uncommitted_installer_even_with_valid_hash(service, tmp_path):
    api, state = service
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    (assets / 'install.sh').write_bytes(b'other')
    (assets / 'install.sh.sha256').write_text(hashlib.sha256(b'other').hexdigest() + '  install.sh\n')
    with pytest.raises(ValueError):
        prepare(api, repo, source, source, assets, evidence, lambda args: None)
    assert not state['writes']


def test_controller_requires_all_platform_records(service, tmp_path):
    api, state = service
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    (evidence / 'windows-x64' / 'record.json').unlink()
    with pytest.raises(OSError):
        prepare(api, repo, source, source, assets, evidence, lambda args: None)
    assert not state['writes']


def test_pending_history_is_bounded_and_includes_staging(service, tmp_path):
    from release_controller import pending_sources
    api, state = service
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    first = setup(repo, assets, evidence)
    (repo / 'next').write_text('next')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'next'], check=True)
    second = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    assert pending_sources(api, repo, second, first, 1) == [first]
    prepare(api, repo, second, first, assets, evidence, lambda args: None)
    assert pending_sources(api, repo, second, first, 10) == [second]
    release = state['releases'][0]
    release['body'] = release['body'].replace('"READY"', '"STAGING"')
    assert pending_sources(api, repo, second, first, 10) == [first, second]


def test_pending_history_rejects_rewritten_release_identity(service, tmp_path):
    from release_controller import pending_sources
    api, state = service
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    prepare(api, repo, source, source, assets, evidence, lambda args: None)
    release = state['releases'][0]
    release['body'] = release['body'].replace(source, 'f' * 40)
    with pytest.raises(ValueError):
        pending_sources(api, repo, source, source, 10)


def test_pending_history_rejects_corrupt_ready_assets(service, tmp_path):
    from release_controller import pending_sources
    api, state = service
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    release = prepare(api, repo, source, source, assets, evidence, lambda args: None)
    state['assets'][release['id']].pop()
    with pytest.raises(ValueError):
        pending_sources(api, repo, source, source, 10)


def test_recovery_dispatches_each_bounded_source_to_master_only():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    from release_controller import dispatch_sources
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, body))
            self.send_response(204)
            self.end_headers()
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        dispatch_sources(['a' * 40, 'b' * 40], 'test', test_api=f'http://127.0.0.1:{server.server_port}')
        assert [r[1] for r in requests] == [{'ref': 'master', 'inputs': {'source': 'a' * 40}}, {'ref': 'master', 'inputs': {'source': 'b' * 40}}]
        assert all(r[0] == '/repos/KarlSideProjects/hwmonitor-mqtt/actions/workflows/sender-release.yml/dispatches' for r in requests)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_recovery_dispatch_rejects_untrusted_endpoints_and_invalid_sources():
    from release_controller import dispatch_sources
    with pytest.raises(ValueError):
        dispatch_sources(['a' * 40], 'token', test_api='https://example.com')
    with pytest.raises(ValueError):
        dispatch_sources(['not-a-sha'], 'token', test_api='http://127.0.0.1:1')


def test_failed_attempts_and_deleted_history_keep_recovery_fair():
    from release_controller import recovery_order
    sources = [f'{n:040x}' for n in range(1, 7)]
    runs = []
    for hour in range(3):
        batch = recovery_order(sources, runs)[:2]
        assert batch == sources[2 * hour:2 * hour + 2]
        runs += [{'display_title': 'sender-recovery-' + source, 'event': 'workflow_dispatch', 'head_branch': 'master', 'updated_at': f'2026-01-01T0{hour}:00:00Z'} for source in batch]
    assert recovery_order(sources, runs)[:2] == sources[:2]
    assert recovery_order(sources, [])[:2] == sources[:2]
    runs += [{'display_title': 'sender-recovery-' + sources[0], 'event': 'pull_request', 'head_branch': 'master', 'updated_at': '2026-01-02T00:00:00Z'}, {'display_title': 'sender-recovery-invalid', 'event': 'push', 'head_branch': 'master', 'updated_at': '2026-01-02T00:00:00Z'}]
    assert recovery_order(sources, runs)[:2] == sources[:2]


def test_workflow_run_history_paginates_and_rejects_bad_response():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    from release_controller import workflow_runs
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            page = int(self.path.split('page=')[-1])
            rows = [{}] * (100 if page == 1 else 1)
            data = json.dumps({'workflow_runs': rows}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert len(workflow_runs('test', test_api=f'http://127.0.0.1:{server.server_port}')) == 101
        with pytest.raises(ValueError):
            workflow_runs('test', test_api='https://example.com')
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
