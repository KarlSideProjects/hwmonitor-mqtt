#!/usr/bin/env python3
"""Guest-only download and prompt fixture; all installation work remains real."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

BASE = Path('/home/debian/source-fixture')
REPO = 'KarlSideProjects/hwmonitor-mqtt'


def download(args, base=BASE):
    source = (base / 'source').read_text().strip()
    urls = [arg for arg in args if arg.startswith('https://')]
    if len(urls) != 1:
        return subprocess.call(['/usr/bin/curl', *args])
    url = urls[0]
    files = {
        f'https://raw.githubusercontent.com/{REPO}/master/bootstrap-source.sh': 'bootstrap-source.sh',
        f'https://raw.githubusercontent.com/{REPO}/{source}/scripts/source_archive.py': 'source_archive.py',
        f'https://github.com/{REPO}/archive/{source}.tar.gz': 'source.tar.gz',
    }
    if url == f'https://api.github.com/repos/{REPO}/commits/master':
        data = json.dumps({'sha': source}).encode()
    elif url in files:
        data = (base / files[url]).read_bytes()
    elif any(host in url for host in ('api.github.com/repos/' + REPO, 'raw.githubusercontent.com/' + REPO, 'github.com/' + REPO + '/archive/')):
        raise ValueError('unexpected candidate download')
    else:
        return subprocess.call(['/usr/bin/curl', *args])
    output_flag = next((flag for flag in ('--output', '-o') if flag in args), None)
    if output_flag:
        Path(args[args.index(output_flag) + 1]).write_bytes(data)
    else:
        sys.stdout.buffer.write(data)
    return 0


def bootstrap_command(base=BASE):
    lines = [line for line in (base / 'README.md').read_text().splitlines()
             if line.startswith('(f=') and '/master/bootstrap-source.sh' in line]
    if len(lines) != 1:
        raise ValueError('expected one documented source bootstrap command')
    return ['bash', '-c', lines[0]]


def prompt(command, password, first=True, timeout=7200):
    import pty
    import signal
    pid, fd = pty.fork()
    if pid == 0:
        os.execvp(command[0], command)
    replies = [(b'MQTT broker host: ', b'127.0.0.1'),
               (b'MQTT broker port [1883]: ', b'1883'),
               (b'MQTT username: ', b'acceptance'),
               (b'MQTT password: ', password.encode())] if first else []
    seen = 0
    buffer = b''
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            ready, _, _ = select.select([fd], [], [], 1)
            if ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                # Deliberately discard terminal output; it may echo credentials.
                buffer = (buffer + data)[-4096:]
                if seen < len(replies) and replies[seen][0] in buffer:
                    os.write(fd, replies[seen][1] + b'\n')
                    seen += 1
                    buffer = b''
        else:
            raise TimeoutError('source installation timed out')
        _, status = os.waitpid(pid, 0)
        if status or seen != len(replies):
            raise RuntimeError('source bootstrap failed or did not consume expected prompts')
    finally:
        os.close(fd)
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


if __name__ == '__main__':
    if sys.argv[1] == 'download':
        raise SystemExit(download(sys.argv[2:]))
    password = (BASE / 'password').read_text().strip()
    prompt(bootstrap_command(), password, first=sys.argv[1] == 'first')
