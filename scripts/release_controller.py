#!/usr/bin/env python3
"""Validate source-bound platform evidence before staging a release."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from check_release_assets import check
from release_backend import GitHub, drain_ready, parse, prepare_candidate, releases, remote_assets
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


def pending_sources(api, repo, master, start, limit):
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
    return [source for source in history[history.index(start):] if source not in confirmed][:limit]


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
    pending = sub.add_parser('pending')
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
            print(json.dumps(pending_sources(api, args.repo, args.master, args.start, args.limit)))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'release controller rejected: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
