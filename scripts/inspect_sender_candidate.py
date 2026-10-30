#!/usr/bin/env python3
"""Check a proposed unpacked Sender layout; this is not a release approval."""

import argparse
from pathlib import Path
import re
import sys


PLATFORMS = {'linux-x64', 'linux-arm64', 'windows-x64'}
COMMON = {'python', 'paho-mqtt', 'psutil', 'python-dotenv'}
SECRET = re.compile(rb'(?i)(?:MQTT_PASS|PVE_TOKEN_SECRET|PASSWORD|API_KEY)\s*[:=]\s*["\']?[^\s"\']+|-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----')
ARCHIVES = (b'PK\x03\x04', b'\x1f\x8b', b'BZh', b'\xfd7zXZ\x00', b'7z\xbc\xaf\x27\x1c', b'Rar!')


def inspect(platform, root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('candidate must be a real directory')
    licenses = COMMON | ({'pythonnet', 'wmi'} if platform == 'windows-x64' else set())
    required = {'sender.exe' if platform == 'windows-x64' else 'sender'} | {f'licenses/{name}.txt' for name in licenses}
    executable = 'sender.exe' if platform == 'windows-x64' else 'sender'
    magic = b'MZ' if platform == 'windows-x64' else b'\x7fELF'
    found = set()
    for path in root.rglob('*'):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f'link is not allowed: {name}')
        if path.is_dir() and name == 'licenses':
            continue
        if name not in required or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError(f'unapproved entry: {name}')
        data = path.read_bytes()
        if not data or data.startswith(ARCHIVES) or SECRET.search(data) or (name == executable and not data.startswith(magic)):
            raise ValueError(f'unsafe or empty content: {name}')
        found.add(name)
    if found != required:
        raise ValueError('missing required files: ' + ', '.join(sorted(required - found)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('platform', choices=sorted(PLATFORMS))
    parser.add_argument('candidate', type=Path)
    args = parser.parse_args()
    try:
        inspect(args.platform, args.candidate)
    except (OSError, ValueError) as error:
        print(f'candidate rejected: {error}', file=sys.stderr)
        return 1
    print('candidate layout accepted; artifact and license review still required')
    return 0


if __name__ == '__main__':
    sys.exit(main())
