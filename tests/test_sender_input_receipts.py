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
    old = tmp_path / 'old-cl.exe'
    selected = tmp_path / 'selected-cl.exe'
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
