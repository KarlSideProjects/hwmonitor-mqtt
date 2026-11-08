#!/usr/bin/env python3
"""Observe one existing Actions run. Exit 0=success, 1=terminal failure, 2=pending/error."""

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time


class ObservationError(Exception):
    pass


def api_error(message):
    text = message.decode(errors='replace').lower()
    if 'rate limit' in text or 'http 429' in text:
        return 'rate-limit'
    if 'http 404' in text:
        return 'not-found-or-inaccessible'
    if 'http 401' in text or 'http 403' in text:
        return 'authentication-or-permission-error'
    if re.search(r'http 5\d\d|timeout|timed out|connection|tls|network|eof', text):
        return 'transport-error'
    return 'api-error'


def gh_api(endpoint, deadline, *, paginate=False, output=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ObservationError('deadline')
    command = ['gh', 'api', endpoint, '--method', 'GET']
    if paginate:
        command += ['--paginate', '--slurp']
    if output is not None:
        command += ['--allow-escape-sequences']
    try:
        result = subprocess.run(command, stdout=output if output is not None else subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=remaining)
    except subprocess.TimeoutExpired:
        raise ObservationError('deadline') from None
    except OSError:
        raise ObservationError('gh-unavailable') from None
    if result.returncode:
        if output is not None:
            output.write(result.stderr)
        raise ObservationError(api_error(result.stderr))
    if output is not None:
        return None
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError):
        raise ObservationError('invalid-response') from None


def private_path(path, *, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    allowed = 0o700 if directory else 0o600
    if (not expected(info.st_mode) or stat.S_IMODE(info.st_mode) & ~allowed
            or (hasattr(os, 'getuid') and info.st_uid != os.getuid())):
        raise PermissionError('private path required')


def load_state(path, repo, run):
    state = {'repo': repo, 'run': run, 'records': {}}
    if path is None:
        return state
    try:
        private_path(path)
    except FileNotFoundError:
        return state
    with path.open() as source:
        loaded = json.load(source)
    if (loaded.get('repo') != repo or loaded.get('run') != run
            or not isinstance(loaded.get('records'), dict)
            or any(not isinstance(record, dict) for record in loaded['records'].values())):
        raise ValueError('state handle mismatch')
    return loaded


def save_state(path, state):
    if path is None:
        return
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as target:
            json.dump(state, target, separators=(',', ':'))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def emit(event, state, path):
    key = 'run' if event['job'] is None else str(event['job'])
    if state['records'].get(key) != event:
        state['records'][key] = event
        save_state(path, state)
        print(json.dumps(event, separators=(',', ':')), flush=True)


def failure_logs(repo, job, deadline, directory):
    error = None
    with tempfile.TemporaryFile() as download:
        try:
            gh_api(f'repos/{repo}/actions/jobs/{job}/logs', deadline, output=download)
        except ObservationError as exc:
            error = str(exc)
        download.seek(0, os.SEEK_END)
        download.seek(max(0, download.tell() - 65536))
        tail = b''.join(download.read().splitlines(keepends=True)[-200:])
    text = tail.decode(errors='replace').lower()
    failure = 'unknown'
    if error is None:
        if '504' in text and ('download' in text or 'urlopen' in text or 'urlretrieve' in text):
            failure = 'transport-download-504'
        elif ('assertionerror' in text or 'error collecting' in text
              or re.search(r'\bfailed\s+\S*(?:test[^\s]*\.py|\.py::)|\b\d+ failed\b', text)):
            failure = 'test-failure'
    if directory is None:
        directory = Path(tempfile.mkdtemp(prefix='watch-checks-'))
    else:
        directory.mkdir(mode=0o700, exist_ok=True)
        private_path(directory, directory=True)
    fd, filename = tempfile.mkstemp(prefix=f'job-{job}-', suffix='.log', dir=directory)
    with os.fdopen(fd, 'wb') as target:
        target.write(tail)
    details = {'failure': failure, 'log_path': str(Path(filename).absolute())}
    if error:
        details['error'] = error
    return details


def observe(args):
    deadline = time.monotonic() + args.wait_seconds
    run_url = f'https://github.com/{args.repo}/actions/runs/{args.run_id}'
    pending = {'run': args.run_id, 'job': None, 'status': 'pending',
               'conclusion': None, 'url': run_url}
    try:
        state = load_state(args.state_file, args.repo, args.run_id)
    except (OSError, ValueError, AttributeError):
        print(json.dumps({**pending, 'error': 'state-error'}, separators=(',', ':')), flush=True)
        return 2
    while True:
        try:
            run = gh_api(f'repos/{args.repo}/actions/runs/{args.run_id}', deadline)
            pages = gh_api(f'repos/{args.repo}/actions/runs/{args.run_id}/jobs?per_page=100',
                           deadline, paginate=True)
            if run['id'] != args.run_id or not isinstance(run['status'], str):
                raise ObservationError('invalid-response')
            jobs = [job for page in pages for job in page['jobs']]
            if any(job['run_id'] != args.run_id or not isinstance(job['id'], int)
                   or not isinstance(job['status'], str) for job in jobs):
                raise ObservationError('invalid-response')
            emit({'run': args.run_id, 'job': None, 'status': run['status'],
                  'conclusion': run['conclusion'], 'url': run_url}, state, args.state_file)
            for job in jobs:
                event = {'run': args.run_id, 'job': job['id'], 'status': job['status'],
                         'conclusion': job['conclusion'], 'url': job['html_url']}
                if job['status'] == 'completed' and job['conclusion'] == 'failure':
                    previous = state['records'].get(str(job['id']), {})
                    if previous.get('failure') and 'error' not in previous:
                        event.update({key: previous[key] for key in ('failure', 'log_path')})
                    else:
                        event.update(failure_logs(args.repo, job['id'], deadline, args.log_dir))
                emit(event, state, args.state_file)
            if run['status'] == 'completed':
                return 0 if run['conclusion'] == 'success' else 1
        except ObservationError as exc:
            emit({**pending, 'error': str(exc)}, state, args.state_file)
            return 2
        except (KeyError, TypeError, ValueError):
            emit({**pending, 'error': 'invalid-response'}, state, args.state_file)
            return 2
        remaining = deadline - time.monotonic()
        if remaining <= args.interval_seconds:
            if remaining > 0:
                time.sleep(remaining)
            return 2
        time.sleep(args.interval_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        'Resume with the same --repo/--run-id and optional private --state-file. '
        'Only changed metadata is printed; pending/error exits 2. No rerun or merge is performed.'))
    parser.add_argument('--repo', required=True, help='GitHub OWNER/REPO')
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--wait-seconds', type=float, default=55)
    parser.add_argument('--interval-seconds', type=float, default=30)
    parser.add_argument('--state-file', type=Path)
    parser.add_argument('--log-dir', type=Path, help='Private 0700 directory; default: new private temp directory')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.repo) or args.run_id <= 0:
        parser.error('a valid OWNER/REPO and positive run ID are required')
    if not 0 < args.wait_seconds <= 55 or not 0 < args.interval_seconds < float('inf'):
        parser.error('wait must be greater than 0 and at most 55 seconds; interval must be positive')
    try:
        return observe(args)
    except OSError:
        print(json.dumps({'run': args.run_id, 'job': None, 'status': 'pending',
                          'conclusion': None,
                          'url': f'https://github.com/{args.repo}/actions/runs/{args.run_id}',
                          'error': 'private-file-error'}, separators=(',', ':')), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
