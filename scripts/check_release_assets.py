#!/usr/bin/env python3
"""Check the release asset set and checksums; this is not release approval."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


ASSETS = (
    'sender-linux-x64.tar.gz', 'sender-linux-arm64.tar.gz', 'sender-windows-x64.zip',
    'validate_sender_package.ps1', 'activate_sender_package.ps1', 'install.sh', 'install.ps1',
)


def check(root):
    expected = {'version.json'} | set(ASSETS) | {name + '.sha256' for name in ASSETS}
    if root.is_symlink() or not root.is_dir() or {p.name for p in root.iterdir()} != expected:
        raise ValueError('release asset set does not match required files')
    for path in root.iterdir():
        if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError(f'asset is not an independent regular file: {path.name}')
    def unique_fields(pairs):
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError('duplicate metadata field')
        return fields

    metadata = json.loads((root / 'version.json').read_text(encoding='utf-8'), object_pairs_hook=unique_fields)
    if (not isinstance(metadata, dict) or set(metadata) != {'version', 'source'}
            or not isinstance(metadata['version'], str)
            or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', metadata['version'])
            or not isinstance(metadata['source'], str)
            or not re.fullmatch(r'[0-9a-f]{40}', metadata['source'])):
        raise ValueError('invalid version metadata')
    for name in ASSETS:
        with (root / name).open('rb') as asset:
            digest = hashlib.file_digest(asset, 'sha256').hexdigest()
        if (root / (name + '.sha256')).read_text(encoding='ascii') != f'{digest}  {name}\n':
            raise ValueError(f'checksum mismatch: {name}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    try:
        check(args.directory)
    except (OSError, ValueError, UnicodeError) as error:
        print(f'release assets rejected: {error}', file=sys.stderr)
        return 1
    print('Release asset hashes match; content and legal approval remain required.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
