import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


CHECKER = Path(__file__).resolve().parents[1] / 'scripts' / 'inspect_sender_candidate.py'
REPO_NOTICES = Path(__file__).resolve().parent / 'fixtures' / 'release-notices'


def candidate(tmp_path, platform='linux-x64'):
    root = tmp_path / 'candidate'
    root.mkdir()
    (root / ('sender.exe' if platform == 'windows-x64' else 'sender')).write_bytes((b'MZ' if platform == 'windows-x64' else b'\x7fELF') + b'compiled placeholder')
    licenses = root / 'licenses'
    licenses.mkdir()
    for name in ('python', 'paho-mqtt', 'psutil', 'python-dotenv') + (('pythonnet', 'wmi') if platform == 'windows-x64' else ()):
        (licenses / f'{name}.txt').write_bytes((REPO_NOTICES / f'{name}.txt').read_bytes())
    approve(root, platform)
    return root


def approve(root, platform='linux-x64'):
    executable = root / ('sender.exe' if platform == 'windows-x64' else 'sender')
    record = {'platform': platform, 'source': 'a' * 40, 'version': '1.0.0',
              'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest()}
    (root.parent / 'trusted.json').write_text(json.dumps(record))


def inspect(root, platform='linux-x64'):
    return subprocess.run([sys.executable, str(CHECKER), platform, str(root), str(root.parent / 'trusted.json')], capture_output=True, text=True)


@pytest.mark.parametrize('platform', ['linux-x64', 'linux-arm64', 'windows-x64'])
def test_accepts_minimal_candidate_structure(tmp_path, platform):
    assert inspect(candidate(tmp_path, platform), platform).returncode == 0


@pytest.mark.parametrize('name', ['agent.py', 'licenses/third-party.py', '.env', 'build/output.o', 'sender.map', 'nested.zip'])
def test_rejects_unapproved_contents(tmp_path, name):
    root = candidate(tmp_path)
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('not allowed')
    assert inspect(root).returncode != 0


def test_rejects_missing_notice(tmp_path):
    root = candidate(tmp_path)
    (root / 'licenses' / 'paho-mqtt.txt').unlink()
    assert inspect(root).returncode != 0


def test_rejects_symlink_and_secret_and_archive(tmp_path):
    root = candidate(tmp_path)
    executable = root / 'sender'
    executable.unlink()
    executable.symlink_to('/etc/passwd')
    assert inspect(root).returncode != 0
    executable.unlink()
    executable.write_bytes(b'\x7fELF binary\nMQTT_PASS=actual-secret\n')
    approve(root)
    assert inspect(root).returncode != 0
    executable.write_bytes(b'\x7fELF compiled placeholder')
    (root / 'licenses' / 'python.txt').write_bytes(b'PK\x03\x04arbitrary archive')
    assert inspect(root).returncode != 0


def test_rejects_unknown_platform(tmp_path):
    assert inspect(candidate(tmp_path), 'other').returncode != 0


def test_rejects_python_source_disguised_as_executable(tmp_path):
    root = candidate(tmp_path)
    (root / 'sender').write_text('def send():\n    pass\n')
    assert inspect(root).returncode != 0


def test_rejects_altered_notice_at_approved_path(tmp_path):
    root = candidate(tmp_path)
    (root / 'licenses' / 'python.txt').write_text('def private_source(): pass')
    assert inspect(root).returncode != 0


def test_rejects_secret_in_notice_at_approved_path(tmp_path):
    root = candidate(tmp_path)
    (root / 'licenses' / 'python.txt').write_text('MQTT_PASS=hidden-secret')
    assert inspect(root).returncode != 0


@pytest.mark.parametrize('key', ['BROKER_HOST', 'MQTT_USER', 'PVE_TOKEN_ID'])
def test_rejects_known_private_settings_in_trusted_executable(tmp_path, key):
    root = candidate(tmp_path)
    (root / 'sender').write_bytes(b'\x7fELF\n' + key.encode() + b'=private-value\n')
    approve(root)
    assert inspect(root).returncode != 0


def test_rejects_changed_executable_against_trusted_digest(tmp_path):
    root = candidate(tmp_path)
    with (root / 'sender').open('ab') as executable:
        executable.write(b'\ndef private_source(): pass\n')
    assert inspect(root).returncode != 0


def test_rejects_candidate_supplied_approval(tmp_path):
    root = candidate(tmp_path)
    (root / 'trusted.json').write_bytes((tmp_path / 'trusted.json').read_bytes())
    result = subprocess.run([sys.executable, str(CHECKER), 'linux-x64', str(root), str(root / 'trusted.json')], capture_output=True)
    assert result.returncode != 0


def test_allows_setting_names_without_values(tmp_path):
    root = candidate(tmp_path)
    (root / 'sender').write_bytes(b'\x7fELF BROKER_HOST MQTT_USER PVE_TOKEN_ID')
    approve(root)
    assert inspect(root).returncode == 0


def test_rejects_invalid_approval_context(tmp_path):
    root = candidate(tmp_path)
    record = json.loads((tmp_path / 'trusted.json').read_text())
    record['source'] = 'branch-name'
    (tmp_path / 'trusted.json').write_text(json.dumps(record))
    assert inspect(root).returncode != 0


def test_rejects_json_quoted_private_settings_in_trusted_executable(tmp_path):
    root = candidate(tmp_path)
    (root / 'sender').write_bytes(b'\x7fELF {"BROKER_HOST":"internal.example","MQTT_USER":"alice"}')
    approve(root)
    assert inspect(root).returncode != 0
