"""Create or preserve private MQTT settings for a source installation."""

import getpass
import os
from pathlib import Path
import re
import stat
import tempfile


def ensure_config(path, target, read=input, read_password=getpass.getpass):
    path = Path(path)
    if target not in ('linux', 'pve'):
        raise ValueError('unsupported configuration target')
    try:
        info = path.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('untrusted MQTT settings')
        return

    host = read('MQTT broker host: ')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', host):
        raise ValueError('invalid MQTT broker host')
    port = read('MQTT broker port [1883]: ') or '1883'
    if not re.fullmatch(r'[0-9]{1,5}', port) or not 1 <= int(port) <= 65535:
        raise ValueError('invalid MQTT broker port')
    user = read('MQTT username: ')
    password = read_password('MQTT password: ')
    if any('\n' in value or '\r' in value or '\0' in value for value in (user, password)):
        raise ValueError('MQTT credentials must be single-line values')

    def quote(value):
        value = value.replace('\\', '\\\\').replace('"', '\\"')
        value = value.replace('$', '$$') if target == 'linux' else value.replace('$', '\\$').replace('`', '\\`')
        return '"' + value + '"'

    content = ''.join(f'{key}={quote(value)}\n' for key, value in (
        ('BROKER_HOST', host), ('BROKER_PORT', port), ('MQTT_USER', user), ('MQTT_PASS', password)))
    descriptor, temporary = tempfile.mkstemp(prefix='.env-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
            output.write(content)
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
