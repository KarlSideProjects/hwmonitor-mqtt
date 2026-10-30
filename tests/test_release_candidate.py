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
        manifest = json.dumps([{'Config': 'config.json', 'Layers': ['layer.tar']}]).encode()
        for name, data in [('manifest.json', manifest), ('config.json', json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(layer.getvalue()).hexdigest()]}}).encode()), ('layer.tar', layer.getvalue())]:
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
        for name, data in [('manifest.json', json.dumps([{'Config': 'config.json', 'Layers': ['layer.tar']}]).encode()), ('config.json', json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(layer.getvalue()).hexdigest()]}}).encode()), ('layer.tar', layer.getvalue())]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match='sender'):
        checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), 'a' * 64)


def test_image_layer_only_allows_exact_known_debian_python_file(tmp_path):
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode='w') as tar:
        for name, data in [('usr/share/gcc/python/libstdcxx/__init__.py', b'\n'), ('app/sender', b'sender')]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    image = tmp_path / 'image.tar'
    with tarfile.open(image, 'w') as tar:
        for name, data in [('manifest.json', json.dumps([{'Config': 'config.json', 'Layers': ['layer.tar']}]).encode()), ('config.json', json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(layer.getvalue()).hexdigest()]}}).encode()), ('layer.tar', layer.getvalue())]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(b'sender').hexdigest())


def test_image_layer_rejects_unapproved_app_files_even_without_env_extension(tmp_path):
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode='w') as tar:
        entry = tarfile.TarInfo('app/settings.ini')
        entry.size = 1
        tar.addfile(entry, io.BytesIO(b'x'))
    image = tmp_path / 'image.tar'
    with tarfile.open(image, 'w') as tar:
        for name, data in [('manifest.json', json.dumps([{'Config': 'config.json', 'Layers': ['layer.tar']}]).encode()), ('config.json', json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(layer.getvalue()).hexdigest()]}}).encode()), ('layer.tar', layer.getvalue())]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match='app'):
        checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), 'a' * 64)


def test_image_configuration_rejects_embedded_credentials():
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    with pytest.raises(ValueError, match='configuration'):
        checker.check_image_config(b'{"config":{"Env":["MQTT_PASS=private-value"]}}')


def test_unknown_runtime_notice_set_cannot_pass_release_gate():
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    with pytest.raises(ValueError, match='not established'):
        checker.check_release_notice_readiness('linux-x64', {'compiler': 'Nuitka unknown', 'runtime': {'python': '0.0.0'}})


def test_image_scans_non_app_layer_file_for_private_settings(tmp_path):
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode='w') as tar:
        for name, data in [('etc/other.conf', b'MQTT_PASS=private-value\n'), ('app/sender', b'sender')]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    image = tmp_path / 'image.tar'
    with tarfile.open(image, 'w') as tar:
        for name, data in [('manifest.json', json.dumps([{'Config': 'config.json', 'Layers': ['layer.tar']}]).encode()), ('config.json', json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(layer.getvalue()).hexdigest()]}}).encode()), ('layer.tar', layer.getvalue())]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match='secret'):
        checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(b'sender').hexdigest())


def test_image_configuration_requires_linux_x64():
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    with pytest.raises(ValueError, match='configuration'):
        checker.check_image_config(b'{"architecture":"arm64","os":"linux"}')


def test_runtime_inventory_rejects_changed_payload_bytes(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    payload = tmp_path / 'sender.bin'
    payload.write_bytes(b'changed')
    inventory = [{'path': 'sender.bin', 'size': 7, 'sha256': hashlib.sha256(b'correct').hexdigest()}]
    with pytest.raises(ValueError, match='inventory'):
        checker.inspect_runtime_files(tmp_path, inventory)


def test_archive_rejects_record_inside_release_assets(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    approval = assets / 'record.json'
    approval.write_text('{}')
    result = subprocess.run([sys.executable, str(CHECKER), '--archive', '--assets', str(assets), 'linux-x64', str(assets / 'sender-linux-x64.tar.gz'), str(approval)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'independent' in result.stderr


def test_build_report_rejects_unexpected_entrypoint(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    report = tmp_path / 'report.xml'
    report.write_text('<nuitka-compilation-report completion="yes"><module name="__main__" kind="PythonMainModule" source_path="other.py"/><module name="hwmonitor_mqtt" kind="CompiledPythonPackage"/></nuitka-compilation-report>')
    inventory = tmp_path / 'inventory.json'
    inventory.write_text('[]')
    record = {'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(), 'inventory_sha256': hashlib.sha256(inventory.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='entrypoint'):
        checker.inspect_build_evidence('windows-x64', report, inventory, record)


def test_arm_runtime_bundle_preserves_its_versioned_license_inputs():
    root = Path(__file__).resolve().parents[1]
    inputs = root / 'release-notices/linux-arm64-cpython-3.11.15-pbs-20260728'
    bundle = (inputs / 'runtime-bundle.txt').read_bytes()
    assert (REPO_NOTICES / 'python.txt').read_bytes() in bundle
    licenses = list((root / 'release-notices/linux-x64-cpython-3.11.15-pbs-20260728').glob('LICENSE.*.txt'))
    assert licenses
    for license_file in licenses:
        assert license_file.read_bytes() in bundle


def test_image_configuration_checks_selected_arm_architecture():
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.check_image_config(b'{"architecture":"arm64","os":"linux"}', 'linux-arm64')
    with pytest.raises(ValueError, match='configuration'):
        checker.check_image_config(b'{"architecture":"amd64","os":"linux"}', 'linux-arm64')


def test_image_rejects_excessive_expanded_layer_size(tmp_path, monkeypatch):
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    monkeypatch.setattr(checker, 'MAX_IMAGE_EXPANDED', 2)
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode='w') as tar:
        entry = tarfile.TarInfo('app/sender')
        entry.size = 3
        tar.addfile(entry, io.BytesIO(b'bad'))
    image = tmp_path / 'image.tar'
    with tarfile.open(image, 'w') as tar:
        for name, data in [('manifest.json', json.dumps([{'Config': 'config.json', 'Layers': ['layer.tar']}]).encode()), ('config.json', json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(layer.getvalue()).hexdigest()]}}).encode()), ('layer.tar', layer.getvalue())]:
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    with pytest.raises(ValueError, match='expanded'):
        checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(b'bad').hexdigest())


def test_rejects_duplicate_json_keys_in_candidate_metadata():
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    with pytest.raises(ValueError, match='duplicate'):
        checker.strict_json(b'{"platform":"linux-x64","platform":"windows-x64"}')


def test_image_secret_exception_is_bound_to_exact_vendor_path_and_bytes(tmp_path, monkeypatch):
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    name = 'usr/bin/vendor'
    safe = b'PASSWORD=example\n'
    monkeypatch.setitem(checker.DEBIAN_SECRET_PATTERN_FILES, name, hashlib.sha256(safe).hexdigest())

    def image_with(data):
        layer = io.BytesIO()
        with tarfile.open(fileobj=layer, mode='w') as tar:
            for path, content in [(name, data), ('app/sender', b'sender')]:
                entry = tarfile.TarInfo(path)
                entry.size = len(content)
                tar.addfile(entry, io.BytesIO(content))
        image = tmp_path / 'image.tar'
        with tarfile.open(image, 'w') as tar:
            for path, content in [('manifest.json', json.dumps([{'Config': 'config.json', 'Layers': ['layer.tar']}]).encode()), ('config.json', json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(layer.getvalue()).hexdigest()]}}).encode()), ('layer.tar', layer.getvalue())]:
                entry = tarfile.TarInfo(path)
                entry.size = len(content)
                tar.addfile(entry, io.BytesIO(content))
        return image

    image = image_with(safe)
    checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(b'sender').hexdigest())
    image = image_with(safe + b'changed')
    with pytest.raises(ValueError, match='secret'):
        checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(b'sender').hexdigest())


def test_build_report_rejects_xml_doctype(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    report = tmp_path / 'report.xml'
    report.write_text('<!DOCTYPE x [<!ENTITY a "x">]><nuitka-compilation-report completion="yes"/>')
    inventory = tmp_path / 'inventory.json'
    inventory.write_text('[]')
    record = {'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(), 'inventory_sha256': hashlib.sha256(inventory.read_bytes()).hexdigest()}
    with pytest.raises(ValueError, match='DOCTYPE'):
        checker.inspect_build_evidence('windows-x64', report, inventory, record)


def test_archive_rejects_excessive_member_count(tmp_path, monkeypatch):
    import importlib.util
    import io
    import tarfile

    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    monkeypatch.setattr(checker, 'MAX_ARCHIVE_MEMBERS', 1)
    archive = tmp_path / 'sender-linux-x64.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        for name in ('sender', 'manifest.json'):
            item = tarfile.TarInfo(name)
            item.size = 1
            package.addfile(item, io.BytesIO(b'x'))
    with pytest.raises(ValueError, match='too many'):
        checker.inspect_archive('linux-x64', archive, {'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})


def test_inventoried_runtime_rejects_private_secret(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    data = b'\x7fELF\x02\x01' + b'\0' * 12 + (62).to_bytes(2, 'little') + b'MQTT_PASS=actual-secret'
    (tmp_path / 'sender.bin').write_bytes(data)
    inventory = [{'path': 'sender.bin', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]
    with pytest.raises(ValueError, match='secret'):
        checker.inspect_runtime_files(tmp_path, inventory, 'linux-x64')


def test_windows_runtime_rejects_native_x86(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    data = bytearray(256)
    data[:2] = b'MZ'
    data[60:64] = (128).to_bytes(4, 'little')
    data[128:132] = b'PE\0\0'
    data[132:134] = (0x14c).to_bytes(2, 'little')
    (tmp_path / 'bad.dll').write_bytes(data)
    inventory = [{'path': 'bad.dll', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}]
    with pytest.raises(ValueError, match='architecture'):
        checker.inspect_runtime_files(tmp_path, inventory, 'windows-x64')


@pytest.mark.parametrize('diff_ids', [['sha256:' + '0' * 64], [], ['sha256:' + '0' * 64, 'sha256:' + '1' * 64]])
def test_image_rejects_incorrect_layer_diff_ids(tmp_path, diff_ids):
    import importlib.util
    import io
    import tarfile
    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode='w') as tar:
        item = tarfile.TarInfo('app/sender')
        item.size = 6
        tar.addfile(item, io.BytesIO(b'sender'))
    image = tmp_path / 'image.tar'
    config = json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': diff_ids}}).encode()
    with tarfile.open(image, 'w') as tar:
        for name, data in [('manifest.json', json.dumps([{'Config': 'config.json', 'Layers': ['layer.tar']}]).encode()), ('config.json', config), ('layer.tar', layer.getvalue())]:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    with pytest.raises(ValueError, match='diff ID'):
        checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(b'sender').hexdigest())


def test_windows_runtime_accepts_anycpu_and_rejects_required_32bit():
    import importlib.util
    import struct
    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    data = bytearray(1024)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 60, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<HH', data, 132, 0x14c, 1)
    struct.pack_into('<H', data, 148, 224)
    opt = 152
    struct.pack_into('<H', data, opt, 0x10b)
    struct.pack_into('<I', data, opt + 92, 16)
    struct.pack_into('<II', data, opt + 96 + 14 * 8, 0x2000, 72)
    section = opt + 224
    struct.pack_into('<III', data, section + 12, 0x2000, 512, 512)
    struct.pack_into('<I', data, 512, 72)
    struct.pack_into('<III', data, 520, 0x2060, 128, 9)
    data[608:612] = b'BSJB'
    struct.pack_into('<I', data, 620, 4)
    struct.pack_into('<H', data, 630, 2)
    struct.pack_into('<II', data, 632, 100, 4)
    data[640:643] = b'#~\0'
    struct.pack_into('<II', data, 644, 104, 4)
    data[652:661] = b'#Strings\0'
    assert checker.windows_runtime_architecture(data)
    for flags in (0, 3, 0x20001):
        struct.pack_into('<I', data, 528, flags)
        assert not checker.windows_runtime_architecture(data)
    struct.pack_into('<I', data, 528, 9)
    data[608:612] = b'bad!'
    assert not checker.windows_runtime_architecture(data)
    struct.pack_into('<H', data, 132, 0xaa64)
    assert not checker.windows_runtime_architecture(data)


def test_image_diff_ids_follow_uncompressed_layer_order(tmp_path):
    import gzip
    import importlib.util
    import io
    import tarfile
    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    layers = []
    for name, content in [('etc/example', b'value'), ('app/sender', b'sender')]:
        layer = io.BytesIO()
        with tarfile.open(fileobj=layer, mode='w') as tar:
            item = tarfile.TarInfo(name)
            item.size = len(content)
            tar.addfile(item, io.BytesIO(content))
        layers.append(layer.getvalue())
    ids = ['sha256:' + hashlib.sha256(layer).hexdigest() for layer in layers]
    image = tmp_path / 'image.tar'
    def write_image(diff_ids):
        config = json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': diff_ids}}).encode()
        with tarfile.open(image, 'w') as tar:
            for name, data in [('manifest.json', json.dumps([{'Config': 'config.json', 'Layers': ['first.tar.gz', 'second.tar']}]).encode()), ('config.json', config), ('first.tar.gz', gzip.compress(layers[0])), ('second.tar', layers[1])]:
                item = tarfile.TarInfo(name)
                item.size = len(data)
                tar.addfile(item, io.BytesIO(data))
    write_image(ids)
    checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(b'sender').hexdigest())
    write_image(ids[::-1])
    with pytest.raises(ValueError, match='diff ID'):
        checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(b'sender').hexdigest())


def test_runtime_secret_scan_allows_only_proven_literal_context(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    elf = b'\x7fELF\x02\x01' + b'\0' * 12 + (62).to_bytes(2, 'little')
    data = elf + b'PVE_TOKEN_ID=\0' + b':param proxy_password: password for SOCKS5 proxy\n'
    path = tmp_path / 'sender.bin'
    def scan(content):
        path.write_bytes(content)
        checker.inspect_runtime_files(tmp_path, [{'path': 'sender.bin', 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}], 'linux-x64')
    scan(data)
    for secret in (b'MQTT_PASS=actual-secret', b'PVE_TOKEN_SECRET=actual-secret', b'password: private-value', b'-----BEGIN PRIVATE KEY-----'):
        with pytest.raises(ValueError, match='secret'):
            scan(data + secret)


def test_image_limits_total_uncompressed_layer_streams(tmp_path, monkeypatch):
    import importlib.util
    import io
    import tarfile
    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode='w') as tar:
        item = tarfile.TarInfo('app/sender')
        item.size = 6
        tar.addfile(item, io.BytesIO(b'sender'))
    data = layer.getvalue()
    monkeypatch.setattr(checker, 'MAX_IMAGE_EXPANDED', len(data) + 1)
    digest = 'sha256:' + hashlib.sha256(data).hexdigest()
    config = json.dumps({'architecture': 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': [digest, digest]}}).encode()
    image = tmp_path / 'image.tar'
    with tarfile.open(image, 'w') as tar:
        for name, content in [('manifest.json', json.dumps([{'Config': 'config.json', 'Layers': ['a.tar', 'b.tar']}]).encode()), ('config.json', config), ('a.tar', data), ('b.tar', data)]:
            item = tarfile.TarInfo(name)
            item.size = len(content)
            tar.addfile(item, io.BytesIO(content))
    with pytest.raises(ValueError, match='expanded'):
        checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(b'sender').hexdigest())


def test_windows_runtime_scans_around_source_proven_docstrings(tmp_path):
    import importlib.util
    import struct
    spec = importlib.util.spec_from_file_location('candidate_checker', CHECKER)
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    pe = bytearray(512)
    pe[:2] = b'MZ'
    struct.pack_into('<I', pe, 60, 128)
    pe[128:132] = b'PE\0\0'
    struct.pack_into('<HH', pe, 132, 0x8664, 1)
    struct.pack_into('<H', pe, 148, 112)
    struct.pack_into('<H', pe, 152, 0x20b)
    data = bytes(pe) + b':param password: leave blank to use current context\n'
    path = tmp_path / 'agent_sender_windows.dll'
    def scan(content):
        path.write_bytes(content)
        checker.inspect_runtime_files(tmp_path, [{'path': path.name, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}], 'windows-x64')
    scan(data)
    with pytest.raises(ValueError, match='secret'):
        scan(data + b'MQTT_PASS=actual-secret')
    path.unlink()
    other = tmp_path / 'other.dll'
    other.write_bytes(data)
    with pytest.raises(ValueError, match='secret'):
        checker.inspect_runtime_files(tmp_path, [{'path': other.name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}], 'windows-x64')
