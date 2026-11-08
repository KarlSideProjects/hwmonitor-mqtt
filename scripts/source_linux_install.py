"""Activate Linux sender source after preserving the previous installation."""

import os
from pathlib import Path
import re
import sys
import tempfile

from scripts.source_deploy import deploy_source
from scripts.source_docker import DockerActivation
from scripts.source_config import ensure_config


def install_linux(source, root, commit, activation, *, target='linux'):
    root = Path(root)
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('invalid source commit')
    if target not in ('linux', 'pve'):
        raise ValueError('unsupported installation target')
    marker = root / '.hwmonitor-mqtt-bootstrap'
    if marker.is_symlink():
        raise ValueError('untrusted installation marker')

    def activate():
        activation.activate()
        descriptor, temporary = tempfile.mkstemp(prefix='.source-marker-', dir=root)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                output.write(commit + '\n')
            os.replace(temporary, marker)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def recover():
        activation.recover()
        activation.finish()

    activation.prepare()
    try:
        ensure_config(root / '.env', target)
        deploy_source(source, root, activate, recover)
    except BaseException:
        if not activation.started:
            activation.finish()
        raise
    try:
        activation.finish()
    except (OSError, RuntimeError) as error:
        print(f'Sender is running; recovery data retained: {error}', file=sys.stderr)


if __name__ == '__main__':
    if len(sys.argv) != 5:
        raise SystemExit('usage: python -m scripts.source_linux_install SOURCE ROOT COMMIT linux|pve')
    source, root, commit, target = sys.argv[1:]
    if target == 'pve':
        from scripts.source_pve import PveActivation
        activation = PveActivation(root, os.environ.get('PVE_CONFIG_FILE', '/etc/hwmonitor-mqtt/pve.env'))
    else:
        activation = DockerActivation(root)
    install_linux(source, root, commit, activation, target=target)
