import stat
from pathlib import Path
import zipfile

import pytest

from _pwsh import run_pwsh


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / 'scripts/windows/validate_source_archive.ps1'
COMMIT = 'a' * 40
PREFIX = 'hwmonitor-mqtt-' + COMMIT + '/'
REQUIRED = ('hwmonitor_mqtt/agents/agent_sender_windows.py',
            'scripts/windows/install_windows_sender_task.ps1',
            'scripts/windows/start_windows_sender.ps1', 'scripts/windows/activate_source.ps1',
            'pyproject.toml', 'uv.lock', 'install.sh', '.python-version', 'README.md')


def make_zip(path, extra=()):
    with zipfile.ZipFile(path, 'w') as archive:
        for name in REQUIRED:
            archive.writestr(PREFIX + name, 'content')
        for entry, content in extra:
            archive.writestr(entry, content)


def validate(path, destination):
    return run_pwsh('& $env:VALIDATOR -Archive $env:ARCHIVE -Destination $env:DEST -Source $env:COMMIT',
                    env={'VALIDATOR': str(VALIDATOR), 'ARCHIVE': str(path),
                         'DEST': str(destination), 'COMMIT': COMMIT})


def test_extracts_expected_source_archive(tmp_path):
    archive, destination = tmp_path / 'source.zip', tmp_path / 'stage'
    make_zip(archive)
    result = validate(archive, destination)
    assert result.returncode == 0, result.stderr
    assert (destination / PREFIX / REQUIRED[0]).read_text() == 'content'


@pytest.mark.parametrize('name', [
    PREFIX + '../escape', PREFIX + 'a/../../escape', '/absolute',
    PREFIX + 'folder\\..\\escape',
    PREFIX + 'file:stream', PREFIX + 'CON.txt', PREFIX + 'CONIN$', PREFIX + 'COM¹.txt', PREFIX + 'file. ',
    PREFIX + 'hwmonitor_mqtt/agents/AGENT_SENDER_WINDOWS.PY',
])
def test_rejects_unsafe_or_duplicate_windows_paths_before_extracting(tmp_path, name):
    archive, destination = tmp_path / 'source.zip', tmp_path / 'stage'
    make_zip(archive, [(name, 'bad')])
    result = validate(archive, destination)
    assert result.returncode != 0
    assert not destination.exists()


def test_rejects_archive_symlinks_before_extracting(tmp_path):
    archive, destination = tmp_path / 'source.zip', tmp_path / 'stage'
    link = zipfile.ZipInfo(PREFIX + 'link')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    make_zip(archive, [(link, '../outside')])
    result = validate(archive, destination)
    assert result.returncode != 0
    assert not destination.exists()


def test_rejects_wrong_source_identity(tmp_path):
    archive, destination = tmp_path / 'source.zip', tmp_path / 'stage'
    with zipfile.ZipFile(archive, 'w') as content:
        content.writestr('hwmonitor-mqtt-' + 'b' * 40 + '/uv.lock', 'content')
    result = validate(archive, destination)
    assert result.returncode != 0
    assert not destination.exists()


def test_rejects_missing_activation_script(tmp_path):
    archive, destination = tmp_path / 'source.zip', tmp_path / 'stage'
    with zipfile.ZipFile(archive, 'w') as content:
        for name in REQUIRED:
            if not name.endswith('activate_source.ps1'):
                content.writestr(PREFIX + name, 'content')
    result = validate(archive, destination)
    assert result.returncode != 0
    assert not destination.exists()


def test_extracts_renamed_source_and_rejects_mixed_roots(tmp_path):
    archive, destination = tmp_path / 'new.zip', tmp_path / 'new'
    new_prefix = 'JWJhome-agent-' + COMMIT + '/'
    with zipfile.ZipFile(archive, 'w') as content:
        for name in REQUIRED:
            content.writestr(new_prefix + name, 'content')
    result = validate(archive, destination)
    assert result.returncode == 0, result.stderr
    assert (destination / new_prefix / REQUIRED[0]).is_file()
    mixed = tmp_path / 'mixed.zip'
    make_zip(mixed, [(new_prefix + 'extra', 'bad')])
    assert validate(mixed, tmp_path / 'mixed').returncode != 0
