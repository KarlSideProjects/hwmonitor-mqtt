import hashlib
import json
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_release_assets.py'
NAMES = ('sender-linux-x64.tar.gz', 'sender-linux-arm64.tar.gz',
         'sender-windows-x64.zip', 'validate_sender_package.ps1',
         'activate_sender_package.ps1')


def candidate(directory):
    (directory / 'version.json').write_text(json.dumps({'version': '1.2.3', 'source': 'a' * 40}))
    for name in NAMES:
        data = name.encode()
        (directory / name).write_bytes(data)
        (directory / (name + '.sha256')).write_text(f'{hashlib.sha256(data).hexdigest()}  {name}\n')


def check(directory):
    return subprocess.run([sys.executable, str(SCRIPT), str(directory)], capture_output=True, text=True)


def test_complete_set_and_corruption(tmp_path):
    candidate(tmp_path)
    assert check(tmp_path).returncode == 0
    (tmp_path / 'sender-linux-arm64.tar.gz').write_bytes(b'corrupted')
    assert check(tmp_path).returncode != 0


def test_missing_asset_and_wrong_checksum_filename(tmp_path):
    candidate(tmp_path)
    (tmp_path / 'sender-windows-x64.zip').unlink()
    assert check(tmp_path).returncode != 0
    candidate(tmp_path)
    (tmp_path / 'sender-windows-x64.zip.sha256').write_text('a' * 64 + '  other.zip\n')
    assert check(tmp_path).returncode != 0


def test_rejects_unexpected_assets_and_invalid_metadata(tmp_path):
    candidate(tmp_path)
    (tmp_path / 'private.py').write_text('private')
    assert check(tmp_path).returncode != 0
    (tmp_path / 'private.py').unlink()
    (tmp_path / 'version.json').write_text('{"version":"../1", "source":"abc"}')
    assert check(tmp_path).returncode != 0


def test_duplicate_metadata_fields_are_rejected(tmp_path):
    candidate(tmp_path)
    (tmp_path / 'version.json').write_text('{"version":"0.0.0","version":"1.2.3","source":"' + 'a' * 40 + '"}')
    assert check(tmp_path).returncode != 0
