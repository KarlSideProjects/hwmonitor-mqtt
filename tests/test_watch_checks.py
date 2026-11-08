import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/watch_checks.py'
RUN_URL = 'https://github.com/owner/repo/actions/runs/123'


@pytest.fixture
def fake_gh(tmp_path, monkeypatch):
    binary = tmp_path / 'gh'
    binary.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys, time
root = pathlib.Path(os.environ['FAKE_GH'])
with (root / 'calls').open('a') as out:
    out.write(json.dumps(sys.argv[1:]) + '\\n')
kind = 'logs' if sys.argv[2].endswith('/logs') else ('jobs' if '/jobs?' in sys.argv[2] else 'run')
reply = json.loads((root / (kind + '.json')).read_text())
time.sleep(reply.get('delay', 0))
if reply.get('error'):
    print(reply['error'], file=sys.stderr)
    sys.exit(1)
value = reply['value']
print(value if isinstance(value, str) else json.dumps(value))
''')
    binary.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ['PATH'])
    monkeypatch.setenv('FAKE_GH', str(tmp_path))

    def reply(kind, value=None, **options):
        (tmp_path / (kind + '.json')).write_text(json.dumps({'value': value, **options}))

    reply('run', {'id': 123, 'status': 'in_progress', 'conclusion': None, 'html_url': RUN_URL})
    reply('jobs', [{'total_count': 1, 'jobs': [job('in_progress')]}])
    reply('logs', 'secret failure tail')
    return reply


def job(status, conclusion=None):
    return {'id': 456, 'run_id': 123, 'status': status, 'conclusion': conclusion,
            'html_url': RUN_URL + '/job/456', 'name': 'tests'}


def watch(tmp_path, *arguments):
    return subprocess.run([sys.executable, str(SCRIPT), '--repo', 'owner/repo',
                           '--run-id', '123', '--wait-seconds', '0.3',
                           '--state-file', str(tmp_path / 'state.json'),
                           '--log-dir', str(tmp_path / 'logs'), *arguments],
                          capture_output=True, text=True, timeout=5)


def events(result):
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_pending_resume_deduplicates_and_retains_handle(tmp_path, fake_gh):
    first = watch(tmp_path)
    assert first.returncode == 2
    assert {(e['run'], e['job'], e['status']) for e in events(first)} == {
        (123, None, 'in_progress'), (123, 456, 'in_progress')}
    second = watch(tmp_path)
    assert second.returncode == 2
    assert second.stdout == ''
    state = tmp_path / 'state.json'
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert json.loads(state.read_text())['run'] == 123
    assert sorted(p.name for p in tmp_path.glob('.state*')) == []
    calls = [json.loads(line) for line in (tmp_path / 'calls').read_text().splitlines()]
    assert all(call[0] == 'api' and '--method' in call and 'GET' in call for call in calls)
    assert sum('/jobs?' in call[1] for call in calls) == 2


@pytest.mark.parametrize('conclusion,code', [('success', 0), ('failure', 1),
                                            ('cancelled', 1), ('timed_out', 1)])
def test_run_terminal_verdict_is_authoritative(tmp_path, fake_gh, conclusion, code):
    fake_gh('run', {'id': 123, 'status': 'completed', 'conclusion': conclusion, 'html_url': RUN_URL})
    fake_gh('jobs', [{'total_count': 1, 'jobs': [job('completed', 'success')]}])
    result = watch(tmp_path)
    assert result.returncode == code
    assert events(result)[0]['conclusion'] == conclusion
    assert not any('/logs' in line for line in (tmp_path / 'calls').read_text().splitlines())


@pytest.mark.parametrize('tail,classification', [
    ('urllib.error.HTTPError: HTTP Error 504: Gateway Time-out\nFailed to download package SECRET',
     'transport-download-504'),
    ('FAILED test_sensor.py::test_value - AssertionError: SECRET', 'test-failure'),
    ('Process crashed SECRET', 'unknown'),
])
def test_failure_logs_are_private_and_never_printed(tmp_path, fake_gh, tail, classification):
    fake_gh('run', {'id': 123, 'status': 'completed', 'conclusion': 'failure', 'html_url': RUN_URL})
    fake_gh('jobs', [{'total_count': 1, 'jobs': [job('completed', 'failure')]}])
    fake_gh('logs', 'prefix\n' * 1000 + tail)
    logs = tmp_path / 'private-logs'
    result = watch(tmp_path, '--log-dir', str(logs))
    assert result.returncode == 1
    failure = next(e for e in events(result) if e['job'] == 456)
    assert failure['failure'] == classification
    path = Path(failure['log_path'])
    assert path.parent == logs
    assert stat.S_IMODE(logs.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text().endswith(tail + '\n')
    assert len(path.read_text().splitlines()) <= 200
    assert 'SECRET' not in result.stdout + result.stderr
    assert watch(tmp_path, '--log-dir', str(logs)).stdout == ''
    assert sum('/logs' in line for line in (tmp_path / 'calls').read_text().splitlines()) == 1


@pytest.mark.parametrize('message,error', [('gh: API rate limit exceeded (HTTP 403) SECRET', 'rate-limit'),
                                          ('gh: Gateway Timeout (HTTP 504) SECRET', 'transport-error'),
                                          ('gh: Not Found (HTTP 404) SECRET', 'not-found-or-inaccessible')])
def test_api_errors_keep_pending_handle_without_raw_output(tmp_path, fake_gh, message, error):
    fake_gh('run', error=message)
    result = watch(tmp_path)
    assert result.returncode == 2
    assert events(result) == [{'run': 123, 'job': None, 'status': 'pending', 'conclusion': None,
                               'url': RUN_URL, 'error': error}]
    assert 'SECRET' not in result.stdout + result.stderr
    assert json.loads((tmp_path / 'state.json').read_text())['run'] == 123


def test_hung_gh_is_bounded_and_remains_pending(tmp_path, fake_gh):
    fake_gh('run', delay=3)
    started = time.monotonic()
    result = watch(tmp_path)
    assert time.monotonic() - started < 1.5
    assert result.returncode == 2
    assert events(result)[0]['error'] == 'deadline'
    assert events(result)[0]['run'] == 123


def test_log_download_error_does_not_reclassify_job_as_transport_failure(tmp_path, fake_gh):
    fake_gh('run', {'id': 123, 'status': 'completed', 'conclusion': 'failure', 'html_url': RUN_URL})
    fake_gh('jobs', [{'total_count': 1, 'jobs': [job('completed', 'failure')]}])
    fake_gh('logs', error='gh: log download failed (HTTP 504) SECRET')
    result = watch(tmp_path)
    assert result.returncode == 1
    failure = next(e for e in events(result) if e['job'] == 456)
    assert failure['failure'] == 'unknown'
    assert failure['error'] == 'transport-error'
    assert stat.S_IMODE(Path(failure['log_path']).stat().st_mode) == 0o600
    assert 'SECRET' not in result.stdout + result.stderr


@pytest.mark.parametrize('unsafe', ['symlink', 'public', 'wrong-run'])
def test_unsafe_or_unrelated_state_is_rejected_before_api(tmp_path, fake_gh, unsafe):
    state = tmp_path / 'state.json'
    content = json.dumps({'repo': 'owner/repo', 'run': 999, 'records': {}})
    if unsafe == 'symlink':
        target = tmp_path / 'target'
        target.write_text(content)
        state.symlink_to(target)
    else:
        state.write_text(content)
        state.chmod(0o600 if unsafe == 'wrong-run' else 0o644)
    result = watch(tmp_path)
    assert result.returncode != 0
    assert 'state-error' in result.stdout
    assert not (tmp_path / 'calls').exists()
    assert state.read_text() == content


def test_wait_over_55_seconds_is_rejected(tmp_path, fake_gh):
    result = watch(tmp_path, '--wait-seconds', '56')
    assert result.returncode != 0
    assert not (tmp_path / 'calls').exists()


def test_paginated_jobs_are_observed_without_fetching_running_job_logs(tmp_path, fake_gh):
    second = {**job('in_progress', 'failure'), 'id': 789}
    fake_gh('jobs', [{'total_count': 2, 'jobs': [job('in_progress')]},
                     {'total_count': 2, 'jobs': [second]}])
    result = watch(tmp_path)
    assert result.returncode == 2
    assert {e['job'] for e in events(result)} == {None, 456, 789}
    assert '/logs' not in (tmp_path / 'calls').read_text()


def test_non_test_failure_is_not_reported_as_test_failure(tmp_path, fake_gh):
    fake_gh('run', {'id': 123, 'status': 'completed', 'conclusion': 'failure', 'html_url': RUN_URL})
    fake_gh('jobs', [{'total_count': 1, 'jobs': [job('completed', 'failure')]}])
    fake_gh('logs', 'Failed to build wheel for external dependency')
    result = watch(tmp_path)
    assert next(e for e in events(result) if e['job'] == 456)['failure'] == 'unknown'


def test_existing_public_log_directory_is_rejected_without_writing_logs(tmp_path, fake_gh):
    fake_gh('run', {'id': 123, 'status': 'completed', 'conclusion': 'failure', 'html_url': RUN_URL})
    fake_gh('jobs', [{'total_count': 1, 'jobs': [job('completed', 'failure')]}])
    directory = tmp_path / 'public-logs'
    directory.mkdir(mode=0o755)
    result = watch(tmp_path, '--log-dir', str(directory))
    assert result.returncode == 2
    assert events(result)[-1]['error'] == 'private-file-error'
    assert not list(directory.iterdir())
    assert 'SECRET' not in result.stdout + result.stderr


def test_state_write_error_preserves_handle_without_traceback(tmp_path, fake_gh):
    fake_gh('run', error='gh: HTTP 504 SECRET')
    result = watch(tmp_path, '--state-file', str(tmp_path / 'missing' / 'state.json'))
    assert result.returncode == 2
    assert events(result)[0]['run'] == 123
    assert events(result)[0]['error'] == 'private-file-error'
    assert result.stderr == ''


def test_log_endpoint_explicitly_allows_ansi_only_for_private_download(tmp_path, fake_gh):
    fake_gh('run', {'id': 123, 'status': 'completed', 'conclusion': 'failure', 'html_url': RUN_URL})
    fake_gh('jobs', [{'total_count': 1, 'jobs': [job('completed', 'failure')]}])
    fake_gh('logs', '\x1b[31mAssertionError: SECRET\x1b[0m')
    result = watch(tmp_path)
    assert result.returncode == 1
    calls = [json.loads(line) for line in (tmp_path / 'calls').read_text().splitlines()]
    assert all(('--allow-escape-sequences' in call) == call[1].endswith('/logs') for call in calls)
    failure = next(e for e in events(result) if e['job'] == 456)
    assert b'\x1b[31m' in Path(failure['log_path']).read_bytes()
    assert '\x1b' not in result.stdout + result.stderr
