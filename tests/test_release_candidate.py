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


def test_archive_rejects_unapproved_entry_even_with_matching_digest(tmp_path):
    import importlib.util
    import tarfile
    import io

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    archive = tmp_path / 'sender-linux-x64.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        entry = tarfile.TarInfo('licenses/thirdparty.py')
        entry.size = 1
        package.addfile(entry, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='unapproved'):
        checker.inspect_archive('linux-x64', archive, {'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})


def test_archive_requires_manifest_to_match_trusted_source(tmp_path):
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    archive = tmp_path / 'sender-linux-x64.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        for name in checker.LINUX_ARCHIVE_PATHS:
            content = json.dumps({'source': 'b' * 40, 'version': '1.2.3', 'platform': 'linux-x64'}).encode() if name == 'manifest.json' else b'nonempty'
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            package.addfile(entry, io.BytesIO(content))
    record = {'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'source': 'a' * 40, 'version': '1.2.3'}
    with pytest.raises(ValueError, match='manifest'):
        checker.inspect_archive('linux-x64', archive, record)


def test_windows_release_assets_reject_corrupt_checksum(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    assets = {}
    for name in ('sender-windows-x64.zip', 'validate_sender_package.ps1', 'activate_sender_package.ps1'):
        content = name.encode()
        (tmp_path / name).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        assets[name] = digest
        (tmp_path / (name + '.sha256')).write_text(f'{digest}  {name}\n')
    (tmp_path / 'activate_sender_package.ps1.sha256').write_text('0' * 64 + '  activate_sender_package.ps1\n')
    with pytest.raises(ValueError, match='checksum'):
        checker.inspect_windows_assets(tmp_path, assets)


@pytest.mark.parametrize('kind', ['duplicate', 'symlink', 'traversal'])
def test_archive_rejects_unsafe_members(tmp_path, kind):
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    archive = tmp_path / 'sender-linux-x64.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        entry = tarfile.TarInfo('../sender' if kind == 'traversal' else 'sender')
        if kind == 'symlink':
            entry.type = tarfile.SYMTYPE
            entry.linkname = 'manifest.json'
            package.addfile(entry)
        else:
            entry.size = 1
            package.addfile(entry, io.BytesIO(b'x'))
            if kind == 'duplicate':
                package.addfile(entry, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='unapproved'):
        checker.inspect_archive('linux-x64', archive, {'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})


def test_archive_rejects_changed_versioned_notice(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    with pytest.raises(ValueError, match='notice'):
        checker.check_archive_notices('windows-x64', {'licenses/python.txt': b'forged notice'})


def test_build_evidence_rejects_first_party_uncompiled_module(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    report = tmp_path / 'report.xml'
    report.write_text('<nuitka-compilation-report completion="yes"><module name="hwmonitor_mqtt.secret" kind="UncompiledPythonModule" /></nuitka-compilation-report>')
    inventory = tmp_path / 'inventory.json'
    inventory.write_text('[]')
    record = {'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(), 'inventory_sha256': hashlib.sha256(inventory.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='uncompiled'):
        checker.inspect_build_evidence('windows-x64', report, inventory, record)


def test_linux_runtime_bundle_preserves_each_retained_license():
    root = Path(__file__).resolve().parents[1]
    inputs = root / 'release-notices/linux-x64-cpython-3.11.15-pbs-20260728'
    bundle = (inputs / 'runtime-bundle.txt').read_bytes()
    assert (REPO_NOTICES / 'python.txt').read_bytes() in bundle
    for license_file in inputs.glob('LICENSE.*.txt'):
        assert license_file.read_bytes() in bundle


def test_archive_allows_only_pinned_example_configuration_with_placeholder_values():
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    data = (Path(__file__).resolve().parents[1] / 'docs/configuration/pve.env.example').read_bytes()
    found = {}
    checker._inspect_entries([('docs/configuration/pve.env.example', len(data), False, 0, lambda: data)], {'docs/configuration/pve.env.example'}, found)
    changed = data + b'\nMQTT_PASS=actual-secret\n'
    with pytest.raises(ValueError, match='unsafe'):
        checker._inspect_entries([('docs/configuration/pve.env.example', len(changed), False, 0, lambda: changed)], {'docs/configuration/pve.env.example'}, {})


def test_linux_release_checksum_binds_exact_archive_name(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    archive = tmp_path / 'sender-linux-x64.tar.gz'
    archive.write_bytes(b'archive')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (tmp_path / (archive.name + '.sha256')).write_text(digest + '  another.tar.gz\n')
    with pytest.raises(ValueError, match='checksum'):
        checker.inspect_linux_assets('linux-x64', tmp_path, digest)


def test_archive_interpolation_permits_only_empty_default():
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    name = 'docker-compose.linux.yml'
    good = b'MQTT_PASS: ${MQTT_PASS:-}\n'
    checker._inspect_entries([(name, len(good), False, 0, lambda: good)], {name}, {})
    bad = b'MQTT_PASS: ${MQTT_PASS:-actual-secret}\n'
    with pytest.raises(ValueError, match='unsafe'):
        checker._inspect_entries([(name, len(bad), False, 0, lambda: bad)], {name}, {})


def test_archive_requires_deployment_file_to_match_trusted_source_record():
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    content = b'original deployment script'
    found = {'Dockerfile': b'changed deployment script'}
    with pytest.raises(ValueError, match='source'):
        checker.check_source_files(found, {'Dockerfile': hashlib.sha256(content).hexdigest()}, {'Dockerfile'})


def test_image_layer_rejects_deleted_first_party_source(tmp_path):
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode='w') as tar:
        entry = tarfile.TarInfo('app/hwmonitor_mqtt/secret.py')
        entry.size = 1
        tar.addfile(entry, io.BytesIO(b'x'))
    image = tmp_path / 'image.tar'
    with tarfile.open(image, 'w') as tar:
        manifest = json.dumps([{'Layers': ['layer.tar']}]).encode()
        for name, data in [('manifest.json', manifest), ('layer.tar', layer.getvalue())]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match='forbidden'):
        checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), 'a' * 64)


def test_image_layer_requires_trusted_sender_bytes(tmp_path):
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode='w') as tar:
        entry = tarfile.TarInfo('app/sender')
        entry.size = 3
        tar.addfile(entry, io.BytesIO(b'bad'))
    image = tmp_path / 'image.tar'
    with tarfile.open(image, 'w') as tar:
        for name, data in [('manifest.json', json.dumps([{'Layers': ['layer.tar']}]).encode()), ('layer.tar', layer.getvalue())]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match='sender'):
        checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), 'a' * 64)
