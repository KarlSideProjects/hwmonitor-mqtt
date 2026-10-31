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
    result = subprocess.run([sys.executable, str(root / 'scripts/create_sender_build_record.py'), '--source-root', str(repo), '--platform', 'linux-x64', '--archive', str(tmp_path / 'archive'), '--executable', str(tmp_path / 'sender'), '--report', str(tmp_path / 'report'), '--provenance', str(tmp_path / 'provenance'), '--inventory', str(tmp_path / 'inventory'), '--runtime-dir', str(tmp_path / 'runtime'), '--assets', str(tmp_path / 'assets'), '--version', '1.0.0', '--python-version', '3.11.15', '--output', str(tmp_path / 'record')], capture_output=True, text=True)
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
    paths = {name for name in checker.LINUX_ARCHIVE_PATHS if not name.startswith('licenses/') and name not in ('manifest.json', 'sender', '.dockerignore')}
    for name in paths:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('committed ' + name)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'initial'], check=True)
    source = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    executable = tmp_path / 'sender'
    executable.write_bytes(b'#!/bin/sh\nsleep 2\n')
    executable.chmod(0o755)
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
    (tmp_path / 'provenance').write_text(json.dumps({'executable_sha256': hashlib.sha256(executable.read_bytes()).hexdigest(), 'report_sha256': hashlib.sha256(report.read_bytes()).hexdigest(), 'inventory_sha256': hashlib.sha256((tmp_path / 'inventory').read_bytes()).hexdigest()}))
    (tmp_path / 'image').write_bytes(b'image')
    output = tmp_path / 'record.json'
    result = subprocess.run([sys.executable, str(root / 'scripts/create_sender_build_record.py'), '--source-root', str(repo), '--platform', 'linux-x64', '--archive', str(archive), '--executable', str(executable), '--report', str(tmp_path / 'report'), '--provenance', str(tmp_path / 'provenance'), '--inventory', str(tmp_path / 'inventory'), '--runtime-dir', str(tmp_path / 'runtime'), '--assets', str(tmp_path / 'assets'), '--image', str(tmp_path / 'image'), '--version', '1.0.0', '--python-version', '3.11.15', '--output', str(output)], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'capture' in result.stderr
    assert not output.exists()
    report.write_text(report.read_text() + '<!-- different build -->')
    swapped = subprocess.run(result.args, capture_output=True, text=True)
    assert swapped.returncode != 0
    assert 'provenance mismatch' in swapped.stderr
    assert not output.exists()


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
    result = subprocess.run([sys.executable, str(root / 'scripts/create_sender_build_record.py'), '--source-root', str(tmp_path), '--platform', 'linux-x64', '--archive', str(tmp_path / 'archive'), '--executable', str(tmp_path / 'sender'), '--report', str(tmp_path / 'report'), '--provenance', str(tmp_path / 'provenance'), '--inventory', str(tmp_path / 'inventory'), '--runtime-dir', str(tmp_path / 'runtime'), '--assets', str(assets), '--version', '1.0.0', '--python-version', '3.11.15', '--output', str(assets / 'record.json')], capture_output=True, text=True)
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


def test_arm_record_rejects_missing_private_link_evidence(tmp_path, monkeypatch):
    import importlib.util
    import pytest
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / 'scripts'))
    spec = importlib.util.spec_from_file_location('build_record', root / 'scripts/create_sender_build_record.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError):
        module.verify_arm_build(tmp_path, {}, {}, ['git'], 'a' * 40, tmp_path / 'sender', tmp_path / 'sender.bin')


def test_arm_record_binds_receipt_committed_inputs_and_link_files(tmp_path, monkeypatch):
    import hashlib
    import importlib.util
    import shutil
    import pytest
    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / 'scripts'))
    from linux_arm64 import gcc as compiler
    spec = importlib.util.spec_from_file_location('build_record', root / 'scripts/create_sender_build_record.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    startup = repo / 'scripts/linux_arm64/startup'
    shutil.copytree(root / 'scripts/linux_arm64/startup', startup)
    shutil.copy2(root / 'scripts/linux_arm64/gcc.py', startup.parent / 'gcc.py')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Test', '-c', 'user.email=t@example.invalid', 'commit', '-qm', 'source'], check=True)
    source = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    state = tmp_path / 'arm-startup'
    state.mkdir()
    (state / 'entry.o').write_bytes(b'entry')
    raw = (startup / 'crt_arch.h').read_text()
    (state / 'entry.h').write_text(raw.replace('"\tmov x0, sp\\n"', '"\tmov x2, x0\\n"\n"\tmov x0, sp\\n"'))
    (state / 'startup.specs').write_text(compiler.startfile_spec('*startfile:\n' + compiler.PINNED_STARTFILE + '\n', str(state / 'entry.o')))
    crts = {name: {'path': '/x/' + name, 'sha256': 'a' * 64} for name in ('crti.o', 'crtbeginS.o', 'crtendS.o', 'crtn.o')}
    inputs = {'startup': compiler.ARM_STARTUP, 'crt': crts, 'build_dir': str(tmp_path), 'object_sha256': hashlib.sha256(b'entry').hexdigest(), 'glue_sha256': hashlib.sha256((startup / 'entry.c').read_bytes()).hexdigest()}
    inputs['compiler'] = {'path': '/usr/bin/gcc', 'sha256': 'a' * 64, 'cc1_path': '/usr/lib/cc1', 'cc1_sha256': 'b' * 64, 'version': '1.0'}
    (state / 'inputs.json').write_text(json.dumps(inputs))
    from capture_sender_build import arm_launcher
    arm_launcher(repo, state)
    for phase in ('backend', 'onefile'):
        args = ['-o', str(tmp_path / ('sender_entrypoint.dist/sender.bin' if phase == 'backend' else 'sender')), 'one.o']
        (state / f'{phase}.argv.json').write_text(json.dumps({'cwd': str(tmp_path), 'args': args}))
        selected = [str(state / 'entry.o'), *(crt['path'] for crt in crts.values())]
        (state / f'{phase}.trace').write_text('\n'.join(selected) + '\n')
        (state / f'{phase}.map').write_text('\n'.join(selected) + '\n')
        data = phase.encode()
        (state / f'{phase}.raw').write_bytes(data)
        names = [f'{phase}.argv.json', f'{phase}.map', f'{phase}.trace']
        metadata = {'phase': phase, 'output': args[1], 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'evidence': {name: hashlib.sha256((state / name).read_bytes()).hexdigest() for name in names}}
        (state / f'{phase}.output.json').write_text(json.dumps(metadata))
    executable = tmp_path / 'candidate'
    executable.write_bytes(b'onefile-payload')
    backend = tmp_path / 'sender.bin'
    backend.write_bytes(b'normalized')
    (state / 'backend.normalized').write_bytes(backend.read_bytes())
    binding = {'tool_path': '/builder/patchelf', 'tool_sha256': 'c' * 64, 'argv': ['/builder/patchelf', '--force-rpath', '--set-rpath', '$ORIGIN', '/tmp/backend'], 'raw_sha256': hashlib.sha256(b'backend').hexdigest(), 'raw_size': 7, 'normalized_sha256': hashlib.sha256(backend.read_bytes()).hexdigest(), 'normalized_size': len(backend.read_bytes())}
    (state / 'binding.json').write_text(json.dumps(binding))
    log = tmp_path / 'compiler.log'
    log.write_text('Scons: Scons command: python -f Backend.scons\nScons: Scons command: python -f Onefile.scons\n')
    provenance = {'arm_startup': compiler.link_evidence(state), 'compiler_log_sha256': hashlib.sha256(log.read_bytes()).hexdigest()}
    receipt = {'startup': compiler.ARM_STARTUP, 'toolchain': {'compiler_sha256': 'b' * 64, 'compiler_version': '1.0'}}
    git = ['git', '-C', str(repo)]
    module.verify_arm_build(tmp_path, provenance, receipt, git, source, executable, backend)
    inventory = tmp_path / 'inventory.json'
    inventory.write_text('[]')
    provenance['inventory_sha256'] = hashlib.sha256(inventory.read_bytes()).hexdigest()
    (tmp_path / 'provenance.json').write_text(json.dumps(provenance))
    from sender_build_inputs import arm_startup_from_build
    assert arm_startup_from_build(inventory, receipt['toolchain']) == compiler.ARM_STARTUP
    from sender_build_inputs import make_receipt
    import zipfile
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    with zipfile.ZipFile(wheels / 'example-1-py3-none-any.whl', 'w') as archive:
        archive.writestr('example-1.dist-info/METADATA', 'Name: example\nVersion: 1\n')
    runtime = tmp_path / 'python.tar.gz'
    runtime.write_bytes(b'runtime')
    args = (wheels, runtime, 'runtime-install', 'linux-arm64', source, '1.0.0', inventory, {'example': '1'})
    assert make_receipt(*args, receipt['toolchain'])['startup'] == compiler.ARM_STARTUP
    for wrong in ({'compiler_sha256': '0' * 64, 'compiler_version': '1.0'}, {'compiler_sha256': 'b' * 64, 'compiler_version': 'wrong'}):
        with pytest.raises(ValueError, match='compiler'):
            make_receipt(*args, wrong)
        with pytest.raises(ValueError, match='compiler'):
            module.verify_arm_build(tmp_path, provenance, dict(receipt, toolchain=wrong), git, source, executable, backend)
    # A previously valid provenance without final binding must not be accepted.
    binding_bytes = (state / 'binding.json').read_bytes()
    normalized_bytes = (state / 'backend.normalized').read_bytes()
    (state / 'binding.json').unlink()
    (state / 'backend.normalized').unlink()
    unbound = dict(provenance, arm_startup=compiler.link_evidence(state))
    (tmp_path / 'provenance.json').write_text(json.dumps(unbound))
    with pytest.raises(ValueError, match='binding'):
        module.verify_arm_build(tmp_path, unbound, receipt, git, source, executable, backend)
    with pytest.raises(ValueError, match='binding'):
        arm_startup_from_build(inventory, receipt['toolchain'])
    (state / 'binding.json').write_bytes(binding_bytes)
    (state / 'backend.normalized').write_bytes(normalized_bytes)
    (tmp_path / 'provenance.json').write_text(json.dumps(provenance))
    with pytest.raises(ValueError):
        module.verify_arm_build(tmp_path, provenance, receipt, git, source, tmp_path / 'wrong', backend)
    with pytest.raises(ValueError):
        module.verify_arm_build(tmp_path, provenance, receipt, git, source, executable, tmp_path / 'wrong')
    altered = json.loads(json.dumps(provenance))
    altered['arm_startup']['files']['onefile.raw'] = '0' * 64
    with pytest.raises(ValueError):
        module.verify_arm_build(tmp_path, altered, receipt, git, source, executable, backend)
    for path, bad in ((executable, b'wrong'), (backend, b'wrong'), (state / 'backend.raw', b'wrong'), (state / 'binding.json', b'{}')):
        original = path.read_bytes()
        path.write_bytes(bad)
        with pytest.raises(ValueError):
            module.verify_arm_build(tmp_path, provenance, receipt, git, source, executable, backend)
        path.write_bytes(original)
    with pytest.raises(ValueError):
        module.verify_arm_build(tmp_path, provenance, dict(receipt, toolchain={'compiler_sha256': '0' * 64, 'compiler_version': '1.0'}), git, source, executable, backend)
    with pytest.raises(ValueError):
        arm_startup_from_build(inventory, {'compiler_sha256': '0' * 64, 'compiler_version': '1.0'})
    (state / 'backend.map').write_text('forged')
    with pytest.raises(ValueError):
        module.verify_arm_build(tmp_path, provenance, receipt, git, source, executable, backend)
    with pytest.raises(ValueError):
        arm_startup_from_build(inventory, receipt['toolchain'])
