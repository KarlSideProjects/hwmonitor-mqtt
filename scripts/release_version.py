#!/usr/bin/env python3
"""Derive a version from a complete, trusted master first-parent history."""
import argparse
import re
import subprocess
import sys
from pathlib import Path


def version(repo, master, source):
    if not re.fullmatch(r'[0-9a-f]{40}', source) or not re.fullmatch(r'[0-9a-f]{40}', master):
        raise ValueError('expected full lowercase commit IDs')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
    if git('rev-parse', '--is-shallow-repository') != 'false':
        raise ValueError('shallow history cannot establish a version')
    if git('rev-parse', '--verify', master + '^{commit}') != master:
        raise ValueError('invalid master commit')
    history = git('rev-list', '--first-parent', '--reverse', master).splitlines()
    if source not in history:
        raise ValueError('source is not in master first-parent history')
    return f'0.1.{history.index(source) + 1}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    parser.add_argument('--master', required=True)
    parser.add_argument('source')
    args = parser.parse_args()
    try:
        print(version(args.repo, args.master, args.source))
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f'version rejected: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
