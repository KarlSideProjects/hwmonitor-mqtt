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


def test_full_workflow_history_window_keeps_recent_attempts():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    from release_controller import workflow_runs
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            data = json.dumps({'workflow_runs': [{}] * 100}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert len(workflow_runs('test', test_api=f'http://127.0.0.1:{server.server_port}')) == 10000
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_recovery_uses_newer_master_when_scheduled_snapshot_is_stale(service, tmp_path):
    from release_controller import pending_sources
    api, _ = service
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    first = setup(repo, assets, evidence)
    (repo / 'next').write_text('next')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=test@example.com', 'commit', '-qm', 'next'], check=True)
    second = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    (assets / 'version.json').write_text(json.dumps({'source': second, 'version': '0.1.2'}))
    for record in evidence.glob('*/record.json'):
        data = json.loads(record.read_text())
        data.update(source=second, version='0.1.2')
        record.write_text(json.dumps(data))
    prepare(api, repo, second, second, assets, evidence, lambda args: None)
    with pytest.raises(ValueError):
        pending_sources(api, repo, first, first, 10)
    assert pending_sources(api, repo, second, first, 10) == [first]


def test_preflight_without_credential_preserves_source_checks(tmp_path):
    from release_controller import preflight
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    report = preflight(repo, source, source, None)
    assert report['checks'] == 'ready'
    assert report['build'] == 'ready'
    assert report['publish'] == 'blocked'
    assert report['blockers'] == ['missing SENDER_RELEASE_TOKEN in sender-release-publisher']


def test_preflight_checks_authenticated_repository_and_releases_without_writes(service, tmp_path):
    from release_controller import preflight
    api, state = service
    api.token = 'secret-value'
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    report = preflight(repo, source, source, 'secret-value', api=api)
    assert report['publish'] == 'read-access-verified-write-unverified'
    assert report['blockers'] == []
    assert state['reads'] == [('', 'Bearer secret-value'), ('/releases', 'Bearer secret-value')]
    assert not state['writes']
    assert 'secret-value' not in json.dumps(report)


@pytest.mark.parametrize('path', ['', '/releases'])
@pytest.mark.parametrize('status', [401, 403, 404, 500])
def test_preflight_blocks_http_failures_before_builds(service, tmp_path, monkeypatch, capsys, path, status):
    import sys
    import release_controller as controller
    api, state = service
    state['read_failures'][path] = status
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    monkeypatch.setattr(controller, 'GitHub', lambda **kwargs: api)
    monkeypatch.setenv('SENDER_RELEASE_TOKEN', 'secret-value')
    monkeypatch.setattr(sys, 'argv', ['release_controller.py', 'preflight', '--repo', str(repo),
                                   '--master', source, '--source', source, '--require-publish'])
    assert controller.main() == 1
    output = capsys.readouterr()
    report = json.loads(output.out)
    assert report['publish'] == 'blocked'
    assert f'HTTP {status}' in report['blockers'][0]
    assert 'secret-value' not in output.out + output.err
    assert not state['writes']


@pytest.mark.parametrize('metadata', [{}, {'full_name': 'other/repo', 'private': False, 'permissions': {'push': True}},
                                      {'full_name': 'KarlSideProjects/hwmonitor-mqtt-releases', 'private': True},
                                      {'full_name': 'KarlSideProjects/hwmonitor-mqtt-releases', 'private': False,
                                       'permissions': {'push': False}}])
def test_preflight_blocks_wrong_private_or_nonpush_repository(service, tmp_path, metadata):
    from release_controller import preflight
    api, state = service
    state['repository'] = metadata
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    report = preflight(repo, source, source, 'secret-value', api=api)
    assert report['publish'] == 'blocked' and report['blockers']
    assert len(state['reads']) == 1 and not state['writes']


@pytest.mark.parametrize('failure', [TimeoutError('secret-value'), OSError('secret-value'), ValueError('secret-value')])
def test_preflight_sanitizes_transport_and_invalid_json_failures(service, tmp_path, monkeypatch, failure):
    from release_controller import preflight
    import release_backend as backend
    class BrokenOpener:
        def open(self, *args, **kwargs):
            raise failure
    monkeypatch.setattr(backend, 'build_opener', lambda *args: BrokenOpener())
    api, state = service
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    report = preflight(repo, source, source, 'secret-value', api=api)
    assert report['publish'] == 'blocked' and report['blockers']
    assert 'secret-value' not in json.dumps(report)
    assert not state['writes']


def test_preflight_cli_missing_secret_is_read_only_and_only_blocks_publish(tmp_path):
    import os
    import sys
    from pathlib import Path
    repo, assets, evidence = (tmp_path / name for name in ('repo', 'assets', 'evidence'))
    source = setup(repo, assets, evidence)
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/release_controller.py'),
               'preflight', '--repo', str(repo), '--master', source, '--source', source]
    env = {k: v for k, v in os.environ.items() if k != 'SENDER_RELEASE_TOKEN'}
    before = subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'])
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['publish'] == 'blocked'
    result = subprocess.run([*command, '--require-publish'], env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)['checks'] == 'ready'
    assert subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain']) == before


@pytest.mark.parametrize('status, expected_exit', [('verified', 0), ('published-but-verification-pending', 1)])
def test_verify_command_resumes_without_draining_or_preparing(tmp_path, monkeypatch, capsys, status, expected_exit):
    import sys
    import release_controller as controller
    receipt = {'source': 'a' * 40, 'version': '0.1.1', 'assets': {}}
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(receipt))
    api = object()
    monkeypatch.setattr(controller, 'GitHub', lambda **kwargs: api)
    def verify(client, release_id, saved):
        assert client is api and release_id == 123 and saved == receipt
        return {'state': status, 'release_id': release_id, 'receipt': saved}
    monkeypatch.setattr(controller, 'verify_public', verify)
    def forbidden(*args):
        pytest.fail('read-only resume must never prepare or drain')
    monkeypatch.setattr(controller, 'drain_ready', forbidden)
    monkeypatch.setattr(controller, 'prepare', forbidden)
    monkeypatch.setattr(sys, 'argv', ['release_controller.py', 'verify', '--release-id', '123', '--receipt', str(path)])
    assert controller.main() == expected_exit
    assert json.loads(capsys.readouterr().out)['state'] == status


def test_drain_reports_post_publication_transport_failure_without_retrying_writes(service, tmp_path, monkeypatch, capsys):
    import sys
    import release_controller as controller
    import release_backend as backend
    from urllib.error import URLError
    from urllib.request import build_opener as real_opener
    api, state = service
    receipt = make_candidate(tmp_path / 'candidate', 1)
    release = backend.prepare_candidate(api, receipt, tmp_path / 'candidate')
    monkeypatch.setattr(controller, 'GitHub', lambda **kwargs: api)
    class Unavailable:
        def open(self, *args, **kwargs):
            raise URLError('injected anonymous transport outage')
    # Only anonymous requests fail; authenticated publication and reconciliation succeed.
    monkeypatch.setattr(backend, 'build_opener', lambda *handlers: real_opener(*handlers) if handlers else Unavailable())
    monkeypatch.setattr(sys, 'argv', ['release_controller.py', 'drain'])
    assert controller.main() == 1
    report = json.loads(capsys.readouterr().out)[0]
    assert report == {'state': 'published-but-verification-pending', 'release_id': release['id'], 'receipt': receipt}
    assert state['releases'][0]['draft'] is False and state['latest'] == release['id']
    writes = list(state['writes'])
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps(report['receipt']))
    monkeypatch.setattr(sys, 'argv', ['release_controller.py', 'verify', '--release-id', str(release['id']), '--receipt', str(path)])
    assert controller.main() == 1
    assert json.loads(capsys.readouterr().out)['state'] == 'published-but-verification-pending'
    assert state['writes'] == writes
