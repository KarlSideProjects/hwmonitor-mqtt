#!/usr/bin/env python3
"""Validate source-bound platform evidence before staging a release."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from check_release_assets import check
from release_backend import GitHub, NoRedirect, drain_ready, parse, prepare_candidate, releases, remote_assets
from release_version import version

PLATFORMS = ('linux-x64', 'linux-arm64', 'windows-x64')


def inspect(args):
    subprocess.run([sys.executable, str(Path(__file__).with_name('inspect_sender_candidate.py')), *map(str, args)], check=True)


def prepare(api, repo, master, source, assets, evidence, inspector=inspect):
    repo, assets, evidence = Path(repo), Path(assets), Path(evidence)
    product_version = version(repo, master, source)
    check(assets)
    metadata = json.loads((assets / 'version.json').read_text(encoding='utf-8'))
    if metadata != {'source': source, 'version': product_version}:
        raise ValueError('release metadata does not match source history')
    for ext in ('sh', 'ps1'):
        committed = subprocess.check_output(['git', '-C', str(repo), 'show', f'{source}:bootstrap.{ext}'])
        if (assets / f'install.{ext}').read_bytes() != committed:
            raise ValueError('installer differs from source')
    if evidence.resolve().is_relative_to(assets.resolve()):
        raise ValueError('build evidence must be private')
    commands = []
    for platform in PLATFORMS:
        folder = evidence / platform
        record = json.loads((folder / 'record.json').read_text(encoding='utf-8'))
        if any(record.get(k) != v for k, v in {'source': source, 'version': product_version, 'platform': platform}.items()):
            raise ValueError('build record identity mismatch')
        archive = assets / ('sender-' + platform + ('.zip' if platform == 'windows-x64' else '.tar.gz'))
        args = ['--archive', platform, archive, folder / 'record.json', '--assets', assets,
                '--build-report', folder / 'report.xml', '--inventory', folder / 'inventory.json', '--runtime-dir', folder / 'runtime']
        if platform != 'windows-x64':
            args += ['--image-archive', folder / 'image.tar']
        commands.append(args)
    for args in commands:
        inspector(args)
    receipt = {'source': source, 'version': product_version, 'assets': {
        p.name: {'size': p.stat().st_size, 'digest': 'sha256:' + hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in assets.iterdir()}}
    return prepare_candidate(api, receipt, assets)


def pending_sources(api, repo, master, start, limit, runs=()):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('invalid recovery limit')
    version(repo, master, start)
    history = subprocess.check_output(['git', '-C', str(repo), 'rev-list', '--first-parent', '--reverse', master], text=True).splitlines()
    confirmed = set()
    for release in releases(api):
        state, receipt, ordinal = parse(release)
        source = receipt['source']
        if source not in history or history.index(source) + 1 != ordinal:
            raise ValueError('release identity conflicts with master history')
        if state == 'READY':
            if remote_assets(api, release, receipt) != set(receipt['assets']):
                raise ValueError('ready release incomplete')
            confirmed.add(source)
    pending = [source for source in history[history.index(start):] if source not in confirmed]
    return recovery_order(pending, runs)[:limit]


def recovery_order(sources, runs):
    attempts = {}
    for run in runs:
        title = run.get('display_title', '')
        stamp = run.get('updated_at', '')
        if (run.get('event') not in ('push', 'workflow_dispatch') or run.get('head_branch') != 'master'
                or not isinstance(title, str) or not re.fullmatch(r'sender-recovery-[0-9a-f]{40}', title)
                or not isinstance(stamp, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', stamp)):
            continue
        source = title[len('sender-recovery-'):]
        if run['event'] == 'push' and run.get('head_sha') != source:
            continue
        attempts[source] = max(stamp, attempts.get(source, ''))
    return sorted(sources, key=lambda source: attempts.get(source, ''))


def workflow_runs(token, test_api=None):
    if not token:
        raise ValueError('source workflow token required')
    result = []
    base = 'https://api.github.com'
    if test_api is not None:
        url = urlsplit(test_api)
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.path or url.query or url.fragment:
            raise ValueError('test API must be loopback')
        base = test_api
    base += '/repos/KarlSideProjects/hwmonitor-mqtt/actions/workflows/sender-release.yml/runs'
    for page in range(1, 101):
        request = Request(f'{base}?per_page=100&page={page}', headers={'Authorization': 'Bearer ' + token,
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            data = json.load(response)
        if not isinstance(data, dict) or not isinstance(data.get('workflow_runs'), list):
            raise ValueError('invalid workflow run history')
        rows = data['workflow_runs']
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError('invalid workflow run history')
        result.extend(rows)
        if len(rows) < 100:
            return result
    return result


def dispatch_sources(sources, token, test_api=None):
    if not token or len(sources) > 100 or any(not re.fullmatch(r'[0-9a-f]{40}', source) for source in sources):
        raise ValueError('invalid recovery dispatch')
    base = 'https://api.github.com'
    if test_api is not None:
        url = urlsplit(test_api)
        if url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.path or url.query or url.fragment:
            raise ValueError('test API must be loopback')
        base = test_api
    path = '/repos/KarlSideProjects/hwmonitor-mqtt/actions/workflows/sender-release.yml/dispatches'
    for source in sources:
        data = json.dumps({'ref': 'master', 'inputs': {'source': source}}).encode()
        request = Request(base + path, data=data, method='POST', headers={
            'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json'})
        with build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 204:
                raise ValueError('recovery dispatch failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    stage = sub.add_parser('prepare')
    stage.add_argument('--repo', type=Path, default=Path('.'))
    stage.add_argument('--master', required=True)
    stage.add_argument('--source', required=True)
    stage.add_argument('--assets', type=Path, required=True)
    stage.add_argument('--evidence', type=Path, required=True)
    sub.add_parser('drain')
    for command in ('pending', 'recover'):
        pending = sub.add_parser(command)
        pending.add_argument('--repo', type=Path, default=Path('.'))
        pending.add_argument('--master', required=True)
        pending.add_argument('--start', required=True)
        pending.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    try:
        api = GitHub(token=os.environ.get('SENDER_RELEASE_TOKEN'))
        if args.command == 'prepare':
            prepare(api, args.repo, args.master, args.source, args.assets, args.evidence)
        elif args.command == 'drain':
            drain_ready(api)
        else:
            token = os.environ.get('SOURCE_WORKFLOW_TOKEN')
            runs = workflow_runs(token) if args.command == 'recover' else ()
            sources = pending_sources(api, args.repo, args.master, args.start, args.limit, runs)
            if args.command == 'recover':
                dispatch_sources(sources, token)
            print(json.dumps(sources))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'release controller rejected: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
