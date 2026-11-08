import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which('docker') is None, reason='Docker Compose is unavailable')


def test_linux_compose_selects_python_image_when_requested():
    result = subprocess.run(
        ['docker', 'compose', '-f', str(ROOT / 'docker-compose.linux.yml'),
         '-f', str(ROOT / 'docker-compose.source.yml'),
         '--profile', 'agent', 'config', '--format', 'json'],
        env={**os.environ, 'SENDER_HOSTNAME': 'test-host',
             'BROKER_HOST': 'localhost'},
        capture_output=True, text=True, check=True,
    )
    config = json.loads(result.stdout)
    assert config['services']['sys_agent']['build']['dockerfile'] == 'Dockerfile.source'
    assert (ROOT / config['services']['sys_agent']['build']['dockerfile']).is_file()


def test_linux_compose_keeps_compiled_image_by_default():
    env = {**os.environ, 'SENDER_HOSTNAME': 'test-host', 'BROKER_HOST': 'localhost'}
    result = subprocess.run(
        ['docker', 'compose', '-f', str(ROOT / 'docker-compose.linux.yml'),
         '--profile', 'agent', 'config', '--format', 'json'],
        env=env, capture_output=True, text=True, check=True,
    )
    assert json.loads(result.stdout)['services']['sys_agent']['build']['dockerfile'] == 'Dockerfile'
