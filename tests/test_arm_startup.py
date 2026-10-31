"""Exercise the private ARM compiler seam without requiring an ARM toolchain."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('arm_compiler', ROOT / 'scripts/linux_arm64/gcc.py')
compiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compiler)

STARTFILE = '%{shared:;      pg|p|profile:%{static-pie:grcrt1.o%s;:gcrt1.o%s};      static:crt1.o%s;      static-pie:rcrt1.o%s;      !no-pie:Scrt1.o%s;      :crt1.o%s} crti.o%s    %{static:crtbeginT.o%s;      shared|static-pie|!no-pie:crtbeginS.o%s;      :crtbegin.o%s}    %{fvtable-verify=none:%s;      fvtable-verify=preinit:vtv_start_preinit.o%s;      fvtable-verify=std:vtv_start.o%s} '


def test_spec_substitutes_only_default_pie_entry():
    source = '*other:\nuntouched\n\n*startfile:\n' + STARTFILE + '\n\n*endfile:\ncrtendS.o%s crtn.o%s\n'
    assert compiler.startfile_spec(source, '/private/entry.o') == '*startfile:\n' + STARTFILE.replace('Scrt1.o%s', '/private/entry.o') + '\n'
    with pytest.raises(ValueError):
        compiler.startfile_spec(source.replace('crti.o%s', 'changed.o%s'), '/private/entry.o')
    with pytest.raises(ValueError):
        compiler.startfile_spec(source.replace('Scrt1.o%s', 'other.o%s'), '/private/entry.o')
    with pytest.raises(ValueError):
        compiler.startfile_spec(source.replace('Scrt1.o%s', 'Scrt1.o%s Scrt1.o%s'), '/private/entry.o')


def test_classifies_expected_links_and_preserves_response_file(tmp_path):
    response = tmp_path / '@link_input.txt'
    response.write_text('one.o "two objects.o"\n')
    backend = ['-o', str(tmp_path / 'sender_entrypoint.dist/sender.bin'), '-s', '-z', 'noexecstack', '-export-dynamic', '-Wl,-rpath=/runtime/lib', '-Wl,-R,$ORIGIN', '-Wl,--disable-new-dtags', '@./@link_input.txt', '-L/runtime/lib', '-lpthread']
    assert compiler.classify(backend, tmp_path) == 'backend'
    assert response.read_text() == 'one.o "two objects.o"\n'
    assert compiler.classify(['-o', str(tmp_path / 'sender'), 'static_src/OnefileBootstrap.o'], tmp_path) == 'onefile'
    assert compiler.classify(['-c', 'one.c', '-o', 'one.o'], tmp_path) is None
    assert compiler.classify(['-shared', '-o', 'extension.so', 'one.o'], tmp_path) is None


@pytest.mark.parametrize('flag', ['-nostartfiles', '-nostdlib', '-static', '-static-pie', '-no-pie', 'Scrt1.o', '-Wl,-Map,other.map', '-Wl,-static', '-Wl,-R,$ORIGIN,-static', '-Wl,-rpath=/safe,-static', '-fno-pie', '-specs', '--specs', '-wrapper', '-fplugin=/tmp/x.so', '-Xlinker', '-B/tmp/other'])
def test_rejects_conflicting_link_options(tmp_path, flag):
    with pytest.raises(ValueError):
        compiler.classify(['-o', str(tmp_path / 'sender'), 'one.o', flag], tmp_path)


def test_rejects_nested_response_and_unrecognized_executable(tmp_path):
    (tmp_path / 'args').write_text('@nested\n')
    with pytest.raises(ValueError):
        compiler.classify(['-o', str(tmp_path / 'sender'), '@args'], tmp_path)
    with pytest.raises(ValueError):
        compiler.classify(['-o', str(tmp_path / 'unexpected'), 'one.o'], tmp_path)


def test_compiler_discovery_and_probe_calls_are_forwarded(tmp_path):
    assert compiler.classify(['--version'], tmp_path) is None
    assert compiler.classify(['-print-file-name=crtbeginS.o'], tmp_path) is None
    assert compiler.classify(['-o', 'conftest', 'conftest.c'], tmp_path) is None


def test_pinned_source_and_license_are_checked_before_compilation(tmp_path):
    startup = ROOT / 'scripts/linux_arm64/startup'
    assert compiler.verified_inputs(startup)['entry_sha256'] == '14dcdc316f356e3d51217c1a2d73e7942d852d7f42be3308afce19e927a97999'
    (tmp_path / 'crt_arch.h').write_bytes((startup / 'crt_arch.h').read_bytes() + b'x')
    (tmp_path / 'LICENSE.musl').write_bytes((startup / 'LICENSE.musl').read_bytes())
    with pytest.raises(ValueError):
        compiler.verified_inputs(tmp_path)
    (tmp_path / 'crt_arch.h').write_bytes((startup / 'crt_arch.h').read_bytes())
    (tmp_path / 'LICENSE.musl').write_bytes((startup / 'LICENSE.musl').read_bytes() + b'x')
    with pytest.raises(ValueError):
        compiler.verified_inputs(tmp_path)


def test_link_driver_preserves_args_and_rejects_duplicate_or_missing_evidence(tmp_path):
    import json
    gcc = tmp_path / 'gcc'
    gcc.write_text('''#!/usr/bin/env python3
import pathlib, sys
args = sys.argv[1:]
if args == ['-print-prog-name=cc1']:
    print(__file__); raise SystemExit
if args == ['-dumpfullversion', '-dumpversion']:
    print('1.0'); raise SystemExit
out = pathlib.Path(args[args.index('-o') + 1])
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(b'fake output')
pathlib.Path('called.json').write_text(__import__('json').dumps(args))
for item in args:
    if item.startswith('-Wl,-Map,'):
        pathlib.Path(item.split(',', 2)[2]).write_text(str(pathlib.Path(item.split(',', 2)[2]).parent / 'entry.o') + '\\n')
print(str(pathlib.Path(next(x.split('=', 1)[1] for x in args if x.startswith('-specs='))).parent / 'entry.o'))
''')
    gcc.chmod(0o755)
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'entry.o').write_bytes(b'entry')
    (state / 'startup.specs').write_text('spec')
    (state / 'inputs.json').write_text(json.dumps({'build_dir': str(tmp_path), 'object_sha256': '0' * 64, 'compiler': compiler.compiler_identity(str(gcc)), 'crt': {}}))
    with pytest.raises(ValueError):
        compiler.run_link(str(gcc), ['-o', str(tmp_path / 'sender'), 'one.o'], state, tmp_path)
    (state / 'inputs.json').write_text(json.dumps({'build_dir': str(tmp_path), 'object_sha256': __import__('hashlib').sha256(b'entry').hexdigest(), 'compiler': compiler.compiler_identity(str(gcc)), 'crt': {}}))
    with pytest.raises(ValueError):
        compiler.run_link(str(gcc), ['-o', '/wrong/sender', 'one.o'], state, tmp_path)
    response = tmp_path / '@link_input.txt'
    response.write_text('one.o\n')
    args = ['-o', str(tmp_path / 'sender_entrypoint.dist/sender.bin'), '@./@link_input.txt', '-pthread']
    compiler.run_link(str(gcc), args, state, tmp_path)
    used = json.loads((tmp_path / 'called.json').read_text())
    assert used[:len(args)] == args
    assert response.read_text() == 'one.o\n'
    assert (state / 'backend.response.0').read_bytes() == response.read_bytes()
    with pytest.raises(ValueError, match='duplicate'):
        compiler.run_link(str(gcc), args, state, tmp_path)


def test_prepare_checks_crt_paths_and_compiles_only_verified_entry(tmp_path):
    import os
    gcc = tmp_path / 'gcc'
    crt = tmp_path / 'crt'
    crt.mkdir()
    for name in ('crti.o', 'crtbeginS.o', 'crtendS.o', 'crtn.o'):
        (crt / name).write_bytes(name.encode())
    gcc.write_text('''#!/usr/bin/env python3
import os, pathlib, sys
a = sys.argv[1:]
if a == ['-print-prog-name=cc1']:
    print(__file__)
elif a == ['-dumpfullversion', '-dumpversion']:
    print('1.0')
elif a == ['-dumpspecs']:
    print('*startfile:\\n' + os.environ['TEST_STARTFILE'] + '\\n')
elif a[0].startswith('-print-file-name='):
    print(os.path.join(os.environ['TEST_CRT'], a[0].split('=', 1)[1]))
elif '-c' in a:
    pathlib.Path(a[a.index('-o') + 1]).write_bytes(b'object')
else:
    raise SystemExit(1)
''')
    gcc.chmod(0o755)
    env = {**os.environ, 'TEST_CRT': str(crt), 'TEST_STARTFILE': STARTFILE}
    state = tmp_path / 'state'
    compiler.prepare(str(gcc), ROOT / 'scripts/linux_arm64/startup', state, env, tmp_path)
    assert (state / 'entry.o').read_bytes() == b'object'
    assert 'mov x2, x0' in (state / 'entry.h').read_text()
    assert (state / 'startup.specs').read_text().count(str(state / 'entry.o')) == 1
    compiler.verify_local_crt(state)
    gcc.write_text(gcc.read_text() + '\n# changed compiler\n')
    with pytest.raises(ValueError, match='compiler'):
        compiler.verify_local_crt(state)
    gcc.write_text(gcc.read_text().replace('\n# changed compiler\n', ''))
    (crt / 'crtn.o').write_bytes(b'changed')
    with pytest.raises(ValueError):
        compiler.verify_local_crt(state)
    (crt / 'crtn.o').unlink()
    with pytest.raises(ValueError, match='CRT'):
        compiler.prepare(str(gcc), ROOT / 'scripts/linux_arm64/startup', tmp_path / 'second', env, tmp_path)


def test_link_evidence_requires_both_phases_and_detects_tampering(tmp_path):
    import json
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'entry.o').write_bytes(b'entry')
    (state / 'startup.specs').write_text(compiler.startfile_spec('*startfile:\n' + STARTFILE + '\n', str(state / 'entry.o')))
    (state / 'inputs.json').write_text(json.dumps({'startup': compiler.verified_inputs(ROOT / 'scripts/linux_arm64/startup'), 'glue_sha256': 'b' * 64, 'build_dir': str(tmp_path), 'object_sha256': __import__('hashlib').sha256(b'entry').hexdigest(), 'crt': {name: {'path': '/x/' + name, 'sha256': 'a' * 64} for name in ('crti.o', 'crtbeginS.o', 'crtendS.o', 'crtn.o')}}))
    inputs = json.loads((state / 'inputs.json').read_text())
    inputs['compiler'] = {'path': '/usr/bin/gcc', 'sha256': 'a' * 64, 'cc1_path': '/usr/lib/cc1', 'cc1_sha256': 'b' * 64, 'version': '1.0'}
    (state / 'inputs.json').write_text(json.dumps(inputs))
    for phase in ('backend', 'onefile'):
        (state / f'{phase}.argv.json').write_text(json.dumps({'cwd': str(tmp_path), 'args': ['-o', str(tmp_path / ('sender_entrypoint.dist/sender.bin' if phase == 'backend' else 'sender')), 'one.o']}))
        selected = [str(state / 'entry.o'), *('/x/' + name for name in ('crti.o', 'crtbeginS.o', 'crtendS.o', 'crtn.o'))]
        (state / f'{phase}.trace').write_text('\n'.join(selected) + '\n')
        (state / f'{phase}.map').write_text('\n'.join(selected) + '\n')
        data = phase.encode()
        (state / f'{phase}.raw').write_bytes(data)
        names = [f'{phase}.argv.json', f'{phase}.map', f'{phase}.trace']
        metadata = {'phase': phase, 'output': str(tmp_path / ('sender_entrypoint.dist/sender.bin' if phase == 'backend' else 'sender')), 'size': len(data), 'sha256': __import__('hashlib').sha256(data).hexdigest(), 'evidence': {name: __import__('hashlib').sha256((state / name).read_bytes()).hexdigest() for name in names}}
        (state / f'{phase}.output.json').write_text(json.dumps(metadata))
    header = state / 'entry.h'
    raw = (ROOT / 'scripts/linux_arm64/startup/crt_arch.h').read_text()
    header.write_text(raw.replace('"\tmov x0, sp\\n"', '"\tmov x2, x0\\n"\n"\tmov x0, sp\\n"'))
    evidence = compiler.link_evidence(state)
    raw_output = state / 'backend.raw'
    original_output = raw_output.read_bytes()
    raw_output.write_bytes((state / 'onefile.raw').read_bytes())
    with pytest.raises(ValueError):
        compiler.link_evidence(state)
    raw_output.write_bytes(original_output)
    metadata_path = state / 'backend.output.json'
    original_metadata = metadata_path.read_text()
    swapped = (state / 'onefile.raw').read_bytes()
    raw_output.write_bytes(swapped)
    metadata = json.loads(original_metadata)
    metadata.update(size=len(swapped), sha256=__import__('hashlib').sha256(swapped).hexdigest())
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='evidence mismatch'):
        compiler.verify_link_evidence(state, evidence)
    raw_output.write_bytes(original_output)
    metadata_path.write_text(original_metadata)
    (state / 'unrelated').write_text('unexpected')
    with pytest.raises(ValueError):
        compiler.link_evidence(state)
    (state / 'unrelated').unlink()
    header.unlink()
    with pytest.raises(ValueError):
        compiler.link_evidence(state)
    header.write_text(raw.replace('"\tmov x0, sp\\n"', '"\tmov x2, x0\\n"\n"\tmov x0, sp\\n"'))
    entry = state / 'entry.o'
    copy = tmp_path / 'copied-entry.o'
    copy.write_bytes(entry.read_bytes())
    entry.unlink()
    entry.symlink_to(copy)
    with pytest.raises(ValueError):
        compiler.link_evidence(state)
    entry.unlink()
    entry.write_bytes(copy.read_bytes())
    bad = json.loads((state / 'inputs.json').read_text())
    bad['startup']['commit'] = '0' * 40
    (state / 'inputs.json').write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        compiler.link_evidence(state)
    bad['startup'] = compiler.verified_inputs(ROOT / 'scripts/linux_arm64/startup')
    (state / 'inputs.json').write_text(json.dumps(bad))
    map_file = state / 'backend.map'
    original = map_file.read_text()
    map_file.write_text(original + '/evil/Scrt1.o\n')
    with pytest.raises(ValueError):
        compiler.link_evidence(state)
    map_file.write_text('tampered')
    with pytest.raises(ValueError):
        compiler.verify_link_evidence(state, evidence)
    (state / 'onefile.trace').unlink()
    with pytest.raises(ValueError):
        compiler.link_evidence(state)


def test_link_evidence_rejects_forged_wrong_phase(tmp_path):
    import json
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'entry.o').write_bytes(b'entry')
    (state / 'startup.specs').write_text(compiler.startfile_spec('*startfile:\n' + STARTFILE + '\n', str(state / 'entry.o')))
    (state / 'inputs.json').write_text(json.dumps({'startup': compiler.verified_inputs(ROOT / 'scripts/linux_arm64/startup'), 'glue_sha256': 'b' * 64, 'build_dir': str(tmp_path), 'object_sha256': __import__('hashlib').sha256(b'entry').hexdigest(), 'crt': {name: {'path': '/x/' + name, 'sha256': 'a' * 64} for name in ('crti.o', 'crtbeginS.o', 'crtendS.o', 'crtn.o')}}))
    for phase in ('backend', 'onefile'):
        (state / f'{phase}.argv.json').write_text(json.dumps({'cwd': str(tmp_path), 'args': ['-o', '/wrong/sender', 'one.o']}))
        (state / f'{phase}.trace').write_text(str(state / 'entry.o') + '\n')
        (state / f'{phase}.map').write_text(str(state / 'entry.o') + '\n')
    with pytest.raises(ValueError):
        compiler.link_evidence(state)


def test_link_evidence_rejects_missing_selected_crt(tmp_path):
    import json
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'entry.o').write_bytes(b'entry')
    (state / 'entry.h').write_bytes(b'header')
    (state / 'startup.specs').write_text(compiler.startfile_spec('*startfile:\n' + STARTFILE + '\n', str(state / 'entry.o')))
    (state / 'inputs.json').write_text(json.dumps({'startup': compiler.verified_inputs(ROOT / 'scripts/linux_arm64/startup'), 'glue_sha256': 'b' * 64, 'build_dir': str(tmp_path), 'object_sha256': __import__('hashlib').sha256(b'entry').hexdigest(), 'crt': {name: {'path': '/x/' + name, 'sha256': 'a' * 64} for name in ('crti.o', 'crtbeginS.o', 'crtendS.o', 'crtn.o')}}))
    for phase in ('backend', 'onefile'):
        args = ['-o', str(tmp_path / ('sender_entrypoint.dist/sender.bin' if phase == 'backend' else 'sender')), 'one.o']
        (state / f'{phase}.argv.json').write_text(json.dumps({'cwd': str(tmp_path), 'args': args}))
        (state / f'{phase}.trace').write_text(str(state / 'entry.o') + '\n')
        (state / f'{phase}.map').write_text(str(state / 'entry.o') + '\n')
    with pytest.raises(ValueError):
        compiler.link_evidence(state)


def test_compiler_log_requires_both_complete_scons_commands(tmp_path):
    log = tmp_path / 'compiler.log'
    log.write_text('Scons: Scons command: python scons.py -f Backend.scons show_scons=true\n')
    with pytest.raises(ValueError):
        compiler.verify_scons_log(log)
    log.write_text(log.read_text() + 'Scons: Scons command: python scons.py -f Onefile.scons show_scons=true\n')
    compiler.verify_scons_log(log)


def test_link_evidence_rejects_symlink_inputs(tmp_path):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'inputs.json').symlink_to(tmp_path / 'missing')
    with pytest.raises(ValueError):
        compiler.link_evidence(state)


def test_response_file_rejects_ambiguous_escaping(tmp_path):
    (tmp_path / 'args').write_text(r'one\ two.o')
    with pytest.raises(ValueError):
        compiler.classify(['-o', str(tmp_path / 'sender'), '@args'], tmp_path)


def test_successful_link_without_output_is_rejected(tmp_path):
    import hashlib
    import json
    gcc = tmp_path / 'gcc'
    gcc.write_text('''#!/usr/bin/env python3
import pathlib, sys
if sys.argv[1:] == ['-print-prog-name=cc1']:
    print(__file__); raise SystemExit
if sys.argv[1:] == ['-dumpfullversion', '-dumpversion']:
    print('1.0'); raise SystemExit
state = pathlib.Path(next(x.split('=', 1)[1] for x in sys.argv if x.startswith('-specs='))).parent
(state / 'backend.map').write_text(str(state / 'entry.o'))
print(state / 'entry.o')
''')
    gcc.chmod(0o755)
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'entry.o').write_bytes(b'entry')
    (state / 'inputs.json').write_text(json.dumps({'build_dir': str(tmp_path), 'object_sha256': hashlib.sha256(b'entry').hexdigest(), 'compiler': compiler.compiler_identity(str(gcc)), 'crt': {}}))
    with pytest.raises(ValueError):
        compiler.run_link(str(gcc), ['-o', str(tmp_path / 'sender_entrypoint.dist/sender.bin'), 'one.o'], state, tmp_path)


@pytest.mark.parametrize('kind', ['symlink', 'empty', 'oversize', 'directory'])
def test_link_rejects_invalid_output(tmp_path, kind):
    import hashlib
    import json
    gcc = tmp_path / 'gcc'
    gcc.write_text('''#!/usr/bin/env python3
import pathlib, sys
args = sys.argv[1:]
if args == ['-print-prog-name=cc1']:
    print(__file__); raise SystemExit
if args == ['-dumpfullversion', '-dumpversion']:
    print('1.0'); raise SystemExit
state = pathlib.Path(next(x.split('=', 1)[1] for x in args if x.startswith('-specs='))).parent
(state / 'onefile.map').write_text(str(state / 'entry.o'))
print(state / 'entry.o')
''')
    gcc.chmod(0o755)
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'entry.o').write_bytes(b'entry')
    (state / 'inputs.json').write_text(json.dumps({'build_dir': str(tmp_path), 'object_sha256': hashlib.sha256(b'entry').hexdigest(), 'compiler': compiler.compiler_identity(str(gcc)), 'crt': {}}))
    output = tmp_path / 'sender'
    if kind == 'symlink':
        output.symlink_to(state / 'entry.o')
    elif kind == 'empty':
        output.touch()
    elif kind == 'oversize':
        with output.open('wb') as file:
            file.truncate(128 * 1024 * 1024 + 1)
    else:
        output.mkdir()
    with pytest.raises(ValueError, match='link output'):
        compiler.run_link(str(gcc), ['-o', str(output), 'one.o'], state, tmp_path)


def test_final_binding_checks_transformation_and_launcher(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'backend.raw').write_bytes(b'backend')
    (state / 'onefile.raw').write_bytes(b'launcher')
    tool = tmp_path / 'patchelf'
    tool.write_text("#!/usr/bin/env python3\nimport pathlib, sys\nassert sys.argv[1:4] == ['--force-rpath', '--set-rpath', '$ORIGIN']\np = pathlib.Path(sys.argv[4]); p.write_bytes(p.read_bytes() + b'-normalized')\n")
    tool.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + __import__('os').environ['PATH'])
    backend = tmp_path / 'sender.bin'
    backend.write_bytes(b'backend-normalized')
    executable = tmp_path / 'sender'
    executable.write_bytes(b'launcher-payload')
    compiler.bind_final_outputs(state, executable, backend)
    compiler.verify_final_binding(state, executable, backend)
    for path, bad in ((backend, b'wrong'), (executable, b'launcher'), (executable, b'wrong-payload'), (state / 'backend.normalized', b'changed'), (state / 'backend.raw', b'swapped')):
        original = path.read_bytes()
        path.write_bytes(bad)
        with pytest.raises(ValueError):
            compiler.verify_final_binding(state, executable, backend)
        path.write_bytes(original)
    tool.unlink()
    compiler.verify_final_binding(state, executable, backend)


def test_binding_rejects_missing_raw_and_changed_tool(tmp_path, monkeypatch):
    import os
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'onefile.raw').write_bytes(b'launcher')
    executable = tmp_path / 'sender'
    executable.write_bytes(b'launcherp')
    backend = tmp_path / 'sender.bin'
    backend.write_bytes(b'backend')
    tool = tmp_path / 'patchelf'
    tool.write_text('#!/usr/bin/env python3\nimport pathlib, sys\np = pathlib.Path(sys.argv[0]); p.write_text(p.read_text() + "# changed\\n")\n')
    tool.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + os.environ['PATH'])
    with pytest.raises((ValueError, OSError)):
        compiler.bind_final_outputs(state, executable, backend)
    (state / 'backend.raw').write_bytes(b'backend')
    with pytest.raises(ValueError, match='patchelf changed'):
        compiler.bind_final_outputs(state, executable, backend)


def test_binding_rejects_wrong_recorded_args(tmp_path, monkeypatch):
    import json
    import os
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'backend.raw').write_bytes(b'backend')
    (state / 'onefile.raw').write_bytes(b'launcher')
    executable = tmp_path / 'sender'
    executable.write_bytes(b'launcherp')
    backend = tmp_path / 'sender.bin'
    backend.write_bytes(b'backend')
    tool = tmp_path / 'patchelf'
    tool.write_text('#!/bin/sh\nexit 0\n')
    tool.chmod(0o755)
    monkeypatch.setenv('PATH', str(tmp_path) + ':' + os.environ['PATH'])
    compiler.bind_final_outputs(state, executable, backend)
    metadata = json.loads((state / 'binding.json').read_text())
    metadata['argv'][3] = 'ORIGIN'
    (state / 'binding.json').write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='transformation'):
        compiler.verify_final_binding(state, executable, backend)
