import json
import subprocess
import sys
from pathlib import Path


def test_build_record_rejects_dirty_tracked_source(tmp_path):
    root = Path(__file__).resolve().parents[1]
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.invalid'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], check=True)
    tracked = repo / 'tracked'
    tracked.write_text('original')
    subprocess.run(['git', '-C', str(repo), 'add', 'tracked'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'initial'], check=True)
    tracked.write_text('changed')
    result = subprocess.run([sys.executable, str(root / 'scripts/create_sender_build_record.py'), '--source-root', str(repo), '--platform', 'linux-x64', '--archive', str(tmp_path / 'archive'), '--executable', str(tmp_path / 'sender'), '--report', str(tmp_path / 'report'), '--inventory', str(tmp_path / 'inventory'), '--runtime-dir', str(tmp_path / 'runtime'), '--assets', str(tmp_path / 'assets'), '--version', '1.0.0', '--python-version', '3.11.15', '--output', str(tmp_path / 'record')], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'dirty' in result.stderr.lower()


def test_build_record_binds_committed_source_and_private_evidence(tmp_path):
    import hashlib
    import importlib.util
    import io
    import tarfile

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('candidate_checker', root / 'scripts/inspect_sender_candidate.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.invalid'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], check=True)
    paths = {name for name in checker.LINUX_ARCHIVE_PATHS if not name.startswith('licenses/') and name not in ('manifest.json', 'sender')}
    for name in paths:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('committed ' + name)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'initial'], check=True)
    source = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    executable = tmp_path / 'sender'
    executable.write_bytes(b'compiled bytes')
    manifest = {'platform': 'linux-x64', 'source': source, 'version': '1.0.0', 'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(), 'compiler': 'Nuitka test', 'runtime': {'python': '3.11.15'}}
    archive = tmp_path / 'sender-linux-x64.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        data = json.dumps(manifest).encode()
        entry = tarfile.TarInfo('manifest.json')
        entry.size = len(data)
        package.addfile(entry, io.BytesIO(data))
    report = tmp_path / 'report'
    report.write_text('<nuitka-compilation-report completion="yes" nuitka_version="test"><module name="__main__" kind="PythonMainModule" source_path="${cwd}/sender_entrypoint.py"/><module name="hwmonitor_mqtt" kind="CompiledPythonPackage"/><distributions/></nuitka-compilation-report>')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    entries = []
    for name in ('sender.bin', 'libpython3.11.so.1.0', 'psutil/_psutil_linux.so', 'psutil/_psutil_posix.so'):
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = b'\x7fELF\x02\x01' + b'\0' * 12 + (62).to_bytes(2, 'little')
        path.write_bytes(data)
        entries.append({'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    (tmp_path / 'inventory').write_text(json.dumps(entries))
    (tmp_path / 'image').write_bytes(b'image')
    output = tmp_path / 'record.json'
    result = subprocess.run([sys.executable, str(root / 'scripts/create_sender_build_record.py'), '--source-root', str(repo), '--platform', 'linux-x64', '--archive', str(archive), '--executable', str(executable), '--report', str(tmp_path / 'report'), '--inventory', str(tmp_path / 'inventory'), '--runtime-dir', str(tmp_path / 'runtime'), '--assets', str(tmp_path / 'assets'), '--image', str(tmp_path / 'image'), '--version', '1.0.0', '--python-version', '3.11.15', '--output', str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    record = json.loads(output.read_text())
    assert record['source'] == source
    assert record['source_files'] == {name: hashlib.sha256((repo / name).read_bytes()).hexdigest() for name in paths}
    assert record['executable_sha256'] == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert record['report_sha256'] == hashlib.sha256((tmp_path / 'report').read_bytes()).hexdigest()


def test_windows_asset_cannot_be_changed_after_committed_source(tmp_path):
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'scripts'))
    try:
        spec = importlib.util.spec_from_file_location('build_record', root / 'scripts/create_sender_build_record.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    repo = tmp_path / 'repo'
    assets = tmp_path / 'assets'
    repo.mkdir()
    assets.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.invalid'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], check=True)
    for name in ('validate_sender_package.ps1', 'activate_sender_package.ps1'):
        target = repo / 'scripts/windows' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('committed')
        (assets / name).write_text('altered')
    (assets / 'sender-windows-x64.zip').write_bytes(b'archive')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'initial'], check=True)
    source = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    import pytest
    with pytest.raises(ValueError, match='committed'):
        module.windows_assets(['git', '-C', str(repo)], source, assets)


def test_build_record_must_not_be_written_inside_release_assets(tmp_path):
    root = Path(__file__).resolve().parents[1]
    assets = tmp_path / 'assets'
    assets.mkdir()
    result = subprocess.run([sys.executable, str(root / 'scripts/create_sender_build_record.py'), '--source-root', str(tmp_path), '--platform', 'linux-x64', '--archive', str(tmp_path / 'archive'), '--executable', str(tmp_path / 'sender'), '--report', str(tmp_path / 'report'), '--inventory', str(tmp_path / 'inventory'), '--runtime-dir', str(tmp_path / 'runtime'), '--assets', str(assets), '--version', '1.0.0', '--python-version', '3.11.15', '--output', str(assets / 'record.json')], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'outside release assets' in result.stderr


def test_record_generator_rejects_duplicate_manifests(tmp_path):
    import importlib.util
    import io
    import tarfile
    import pytest

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'scripts'))
    try:
        spec = importlib.util.spec_from_file_location('build_record', root / 'scripts/create_sender_build_record.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    archive = tmp_path / 'candidate.tar.gz'
    with tarfile.open(archive, 'w:gz') as package:
        for _ in range(2):
            entry = tarfile.TarInfo('manifest.json')
            entry.size = 2
            package.addfile(entry, io.BytesIO(b'{}'))
    with pytest.raises(ValueError, match='manifest'):
        module.read_manifest('linux-x64', archive)
