import importlib.util
import io
from pathlib import Path
import tarfile
import subprocess

import pytest

spec = importlib.util.spec_from_file_location('pve_acceptance', Path(__file__).resolve().parents[1] / 'scripts/release-tests/pve_acceptance.py')
pve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pve)


def test_image_checksum_is_verified_before_creating_disk(tmp_path):
    image = tmp_path / 'image'
    image.write_bytes(b'not a cloud image')
    with pytest.raises(ValueError, match='cloud image checksum'):
        pve.verify_file(image, '0' * 128, 'sha512', 'cloud image')


def test_cleanup_attempts_every_owned_resource_after_failure():
    attempted = []

    def action(name):
        attempted.append(name)
        if name == 'container':
            raise RuntimeError('failure')

    errors = pve.cleanup([('container', lambda: action('container')), ('image', lambda: action('image')), ('listener', lambda: action('listener'))])
    assert attempted == ['container', 'image', 'listener']
    assert errors and 'container' in errors[0]


def test_guest_seed_has_an_owned_marker_bound_to_the_run():
    config = pve.guest_config('sender-pve-0123456789ab', 'ssh-ed25519 example')
    assert '/etc/hwmonitor-pve-acceptance-owner' in config
    assert 'sender-pve-0123456789ab' in config
    assert 'ssh-ed25519 example' in config


def test_diagnostics_keep_only_sanitized_allowlisted_evidence(tmp_path):
    (tmp_path / 'inputs.json').write_text('{"source":"fixed"}\n')
    secret = 'a' * 48
    token = '12345678-1234-1234-1234-123456789abc'
    private = '-----BEGIN OPENSSH PRIVATE KEY-----\nprivate material\n-----END OPENSSH PRIVATE KEY-----'
    (tmp_path / 'run.log').write_text(f'guest acceptance failed line=37 status=2\n{secret}\n{token}\n{private}\nMQTT_PASS="arbitrary password"\n')
    (tmp_path / 'id_ed25519').write_text(private)
    with tarfile.open(tmp_path / 'guest-evidence.tar', 'w') as archive:
        for name, value in [('full.ndjson', '{"host":"pve-sandbox","value":42}'), ('endpoint.log', secret), ('.env', secret), ('../pve.env', token)]:
            data = value.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    pve.retain_diagnostics(tmp_path)
    dest = tmp_path / 'diagnostics'
    assert {p.name for p in dest.iterdir()} == {'inputs.json', 'run.log', 'guest-evidence.tar'}
    log = (dest / 'run.log').read_text()
    assert 'line=37 status=2' in log
    for value in (secret, token, 'private material', 'arbitrary password'):
        assert value not in log
    with tarfile.open(dest / 'guest-evidence.tar') as archive:
        assert archive.getnames() == ['full.ndjson', 'endpoint.log']
        assert b'"value":42' in archive.extractfile('full.ndjson').read()
        assert secret.encode() not in archive.extractfile('endpoint.log').read()


def test_diagnostics_survive_missing_guest_evidence(tmp_path):
    (tmp_path / 'inputs.json').write_text('{}')
    (tmp_path / 'run.log').write_text('provision failed')
    pve.retain_diagnostics(tmp_path)
    assert (tmp_path / 'diagnostics/run.log').read_text() == 'provision failed'


def test_guest_failure_reports_line_and_preserves_exit_status():
    header = (Path(__file__).resolve().parents[1] / 'scripts/release-tests/pve_guest.sh').read_text().split('umask', 1)[0]
    result = subprocess.run(['bash', '-c', header + "bash -c 'exit 2'\n"], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'PVE acceptance failed: line=4 status=2' in result.stderr
