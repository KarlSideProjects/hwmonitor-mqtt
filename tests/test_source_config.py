import stat
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.source_config import ensure_config


def test_new_config_prompts_and_is_private(tmp_path):
    answers = iter(['192.168.5.33', '', 'monitor'])
    path = tmp_path / '.env'
    ensure_config(path, 'linux', read=lambda _: next(answers), read_password=lambda _: 'pass$word')
    assert path.read_text() == ('BROKER_HOST="192.168.5.33"\nBROKER_PORT="1883"\n'
                                'MQTT_USER="monitor"\nMQTT_PASS="pass$$word"\n')
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_private_settings_are_preserved_without_prompt(tmp_path):
    path = tmp_path / '.env'
    path.write_text('private settings')
    path.chmod(0o600)
    ensure_config(path, 'linux', read=lambda _: pytest.fail('unexpected prompt'))
    assert path.read_text() == 'private settings'


def test_rejects_symlinked_settings(tmp_path):
    target = tmp_path / 'target'
    target.write_text('private')
    path = tmp_path / '.env'
    path.symlink_to(target)
    with pytest.raises(ValueError, match='untrusted'):
        ensure_config(path, 'linux')
    assert target.read_text() == 'private'


@pytest.mark.parametrize('host,port', [('bad host', '1883'), ('broker', '65536')])
def test_invalid_broker_settings_do_not_create_a_file(tmp_path, host, port):
    answers = iter([host, port])
    path = tmp_path / '.env'
    with pytest.raises(ValueError):
        ensure_config(path, 'linux', read=lambda _: next(answers))
    assert not path.exists()


@pytest.mark.skipif(shutil.which('docker') is None, reason='Docker Compose is unavailable')
def test_compose_receives_exact_password_with_special_characters(tmp_path):
    password = 'pa$$word"with\\slash'
    answers = iter(['broker', '', 'monitor'])
    path = tmp_path / '.env'
    ensure_config(path, 'linux', read=lambda _: next(answers), read_password=lambda _: password)
    compose = Path(__file__).resolve().parents[1] / 'docker-compose.linux.yml'
    env = {key: value for key, value in os.environ.items()
           if key not in ('BROKER_HOST', 'BROKER_PORT', 'MQTT_USER', 'MQTT_PASS')}
    env['SENDER_HOSTNAME'] = 'test-host'
    result = subprocess.run(['docker', 'compose', '--env-file', str(path), '-f', str(compose),
                             '--profile', 'agent', 'config', '--environment'],
                            env=env, capture_output=True, text=True, check=True)
    actual = next(line.partition('=')[2] for line in result.stdout.splitlines()
                  if line.startswith('MQTT_PASS='))
    assert actual == password
