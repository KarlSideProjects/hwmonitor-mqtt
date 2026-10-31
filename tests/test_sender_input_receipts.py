import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sender_inputs', ROOT / 'scripts/sender_build_inputs.py')
inputs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inputs)


def test_selected_archives_and_candidate_identity_are_bound(tmp_path):
    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    wheel = wheels / 'example_pkg-1.2-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr('example_pkg-1.2.dist-info/METADATA', 'Metadata-Version: 2.1\nName: example-pkg\nVersion: 1.2\n')
    runtime = tmp_path / 'python.tar.gz'
    runtime.write_bytes(b'exact runtime archive')
    inventory = tmp_path / 'inventory.json'
    inventory.write_text('[]')
    receipt = inputs.make_receipt(wheels, runtime, 'runtime-install', 'linux-x64', 'a' * 40, '1.2.3', inventory, {'example-pkg': '1.2'}, {'gcc': '10.2.1-6'})
    assert receipt['packages'][0]['sha256'] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    inputs.validate_receipt(receipt, 'linux-x64', 'a' * 40, '1.2.3', hashlib.sha256(inventory.read_bytes()).hexdigest(), {'example-pkg': '1.2'})
    for key, bad in [('source', 'b' * 40), ('version', '1.2.4'), ('inventory_sha256', 'f' * 64)]:
        changed = json.loads(json.dumps(receipt))
        changed[key] = bad
        with pytest.raises(ValueError):
            inputs.validate_receipt(changed, 'linux-x64', 'a' * 40, '1.2.3', receipt['inventory_sha256'], {'example-pkg': '1.2'})
    with pytest.raises(ValueError):
        inputs.make_receipt(wheels, runtime, 'runtime-install', 'linux-x64', 'a' * 40, '1.2.3', inventory, {'example-pkg': '1.2', 'missing': '1'}, {'gcc': '10.2.1-6'})
    wheel.unlink()
    with pytest.raises(ValueError):
        inputs.make_receipt(wheels, runtime, 'runtime-install', 'linux-x64', 'a' * 40, '1.2.3', inventory, {'example-pkg': '1.2'}, {'gcc': '10.2.1-6'})


def test_windows_compiler_identity_comes_from_both_actual_scons_builds(tmp_path):
    old = tmp_path / 'VC/Tools/MSVC/14.42.34433/bin/Hostx64/x64/cl.exe'
    selected = tmp_path / 'VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/cl.exe'
    old.parent.mkdir(parents=True)
    selected.parent.mkdir(parents=True)
    old.write_bytes(b'older compiler on PATH')
    selected.write_bytes(b'actual latest compiler')
    log = tmp_path / 'build.log'
    log.write_text(f'Scons: Compiler used {selected} (MSVC 14.3)\nScons: Compiler used {selected} (MSVC 14.3)\n')
    result = inputs.windows_compiler_evidence(log)
    assert result['compiler_sha256'] == hashlib.sha256(selected.read_bytes()).hexdigest()
    assert result['compiler_sha256'] != hashlib.sha256(old.read_bytes()).hexdigest()
    log.write_text(f'Scons: Compiler used {old} (MSVC 14.3)\nScons: Compiler used {selected} (MSVC 14.3)\n')
    with pytest.raises(ValueError, match='compiler'):
        inputs.windows_compiler_evidence(log)
    log.write_text(f'Scons: Compiler used {selected} (MSVC 14.3)\n')
    with pytest.raises(ValueError, match='compiler'):
        inputs.windows_compiler_evidence(log)


def test_receipt_cannot_swap_actual_compiler_log(tmp_path):
    compiler = tmp_path / 'VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/cl.exe'
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b'compiler')
    log = tmp_path / 'compiler.log'
    log.write_text(f'Scons: Compiler used {compiler} (MSVC 14.3)\nScons: Compiler used {compiler} (MSVC 14.3)\n')
    evidence = inputs.windows_compiler_evidence(log)
    provenance = {'compiler_log_sha256': hashlib.sha256(log.read_bytes()).hexdigest()}
    inputs.verify_windows_compiler(evidence, provenance, log)
    wrong = dict(evidence, compiler_sha256='0' * 64)
    with pytest.raises(ValueError, match='compiler'):
        inputs.verify_windows_compiler(wrong, provenance, log)
    log.write_text(log.read_text() + 'changed\n')
    with pytest.raises(ValueError, match='compiler'):
        inputs.verify_windows_compiler(evidence, provenance, log)


def test_arm_receipt_requires_pinned_startup_and_preserves_other_platform_schema():
    startup = {'commit': '0784374d561435f7c787a555aeab8ede699ed298',
               'entry_sha256': '14dcdc316f356e3d51217c1a2d73e7942d852d7f42be3308afce19e927a97999',
               'license_sha256': 'f9bc4423732350eb0b3f7ed7e91d530298476f8fec0c6c427a1c04ade22655af'}
    base = {'platform': 'linux-arm64', 'source': 'a' * 40, 'version': '1.2.3', 'inventory_sha256': 'b' * 64,
            'runtime_archive': {'kind': 'runtime-install', 'filename': 'python.tar.gz', 'sha256': 'c' * 64},
            'packages': [{'name': 'example', 'version': '1', 'filename': 'example.whl', 'sha256': 'd' * 64}],
            'toolchain': {'gcc': '10'}}
    with pytest.raises(ValueError):
        inputs.validate_receipt(base, 'linux-arm64', 'a' * 40, '1.2.3', 'b' * 64, {'example': '1'})
    base['startup'] = startup
    assert inputs.validate_receipt(base, 'linux-arm64', 'a' * 40, '1.2.3', 'b' * 64, {'example': '1'})['startup'] == startup
    for key in startup:
        changed = json.loads(json.dumps(base))
        changed['startup'][key] = '0' * len(startup[key])
        with pytest.raises(ValueError):
            inputs.validate_receipt(changed, 'linux-arm64', 'a' * 40, '1.2.3', 'b' * 64, {'example': '1'})
    base['platform'] = 'linux-x64'
    with pytest.raises(ValueError):
        inputs.validate_receipt(base, 'linux-x64', 'a' * 40, '1.2.3', 'b' * 64, {'example': '1'})


def test_arm_receipt_rejects_missing_and_forged_same_invocation_evidence(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    inventory = tmp_path / 'inventory.json'
    inventory.write_text('[]')
    with pytest.raises(ValueError):
        inputs.arm_startup_from_build(inventory)
    (tmp_path / 'provenance.json').write_text(json.dumps({'inventory_sha256': hashlib.sha256(inventory.read_bytes()).hexdigest(), 'arm_startup': {'startup': inputs.ARM_STARTUP, 'files': {}}}))
    with pytest.raises(ValueError):
        inputs.arm_startup_from_build(inventory)


def test_arm_toolchain_identity_uses_the_driver_selected_for_links(tmp_path, monkeypatch):
    cc1 = tmp_path / 'cc1'
    cc1.write_bytes(b'compiler')
    calls = []
    def output(args, **kwargs):
        calls.append(args)
        if args[0] == 'dpkg-query':
            return 'gcc=10'
        if args[-1] == '-print-prog-name=cc1':
            return str(cc1)
        return '10.2.1'
    monkeypatch.setattr(inputs.subprocess, 'check_output', output)
    monkeypatch.setattr(inputs.sys, 'platform', 'linux')
    inputs.observed_toolchain(arm=True)
    assert [call[0] for call in calls[1:]] == ['/usr/bin/gcc', '/usr/bin/gcc']
