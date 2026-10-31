import sys
from pathlib import Path

import pytest


def test_capture_rejects_payload_from_different_executable(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from capture_sender_build import capture

    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'payload').write_bytes(b'build one')
    executable = tmp_path / 'sender.py'
    executable.write_text("import os,time\nfrom pathlib import Path\np=Path(os.environ['TMPDIR'])/'onefile_1'\np.mkdir()\n(p/'payload').write_bytes(b'build two')\ntime.sleep(2)\n")
    with pytest.raises(ValueError, match='capture'):
        capture([sys.executable, str(executable)], dist, tmp_path / 'runtime', timeout=0.5)


def test_capture_records_actual_extracted_payload(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from capture_sender_build import capture

    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'payload').write_bytes(b'build one')
    executable = tmp_path / 'sender.py'
    executable.write_text("import os,time\nfrom pathlib import Path\np=Path(os.environ['TMPDIR'])/'onefile_1'\np.mkdir()\n(p/'payload').write_bytes(b'build one')\ntime.sleep(2)\n")
    capture([sys.executable, str(executable)], dist, tmp_path / 'runtime', timeout=1)
    assert (tmp_path / 'runtime/payload').read_bytes() == b'build one'


def test_capture_temporary_cleanup_retries_transient_lock(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import capture_sender_build as capture_module
    calls = []
    class Directory:
        name = 'temporary'
        def cleanup(self):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError('locked')
    monkeypatch.setattr(capture_module.tempfile, 'TemporaryDirectory', lambda **kwargs: Directory())
    monkeypatch.setattr(capture_module.time, 'sleep', lambda delay: None)
    with capture_module.capture_directory() as path:
        assert path == Path('temporary')
    assert len(calls) == 3


def test_capture_temporary_cleanup_fails_if_lock_persists(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import capture_sender_build as capture_module
    calls = []
    class Directory:
        name = 'temporary'
        def cleanup(self):
            calls.append(1)
            raise PermissionError('locked')
    monkeypatch.setattr(capture_module.tempfile, 'TemporaryDirectory', lambda **kwargs: Directory())
    monkeypatch.setattr(capture_module.time, 'sleep', lambda delay: None)
    with pytest.raises(PermissionError):
        with capture_module.capture_directory():
            pass
    assert len(calls) == 100


def test_windows_capture_excludes_microsoft_runtime_dlls(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    import capture_sender_build as module
    calls = []
    monkeypatch.setattr(module, 'compile_and_capture', lambda *args: calls.append(args))
    monkeypatch.setattr(module.shutil, 'copy2', lambda *args: None)
    monkeypatch.setattr(sys, 'argv', ['capture', '--source', str(tmp_path), '--build-dir', str(tmp_path / 'build'), '--evidence-dir', str(tmp_path / 'evidence')])
    module.main()
    command = calls[0][0]
    assert {item for item in command if item.startswith('--noinclude-dlls=')} == {
        '--noinclude-dlls=mfc140u.dll', '--noinclude-dlls=vcruntime140.dll', '--noinclude-dlls=vcruntime140_1.dll'}
    assert '--show-scons' in command


def test_arm_capture_requires_link_evidence_before_provenance(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import capture_sender_build as module
    build, runtime = tmp_path / 'build', tmp_path / 'runtime'
    inventory, provenance = tmp_path / 'inventory.json', tmp_path / 'provenance.json'
    state = tmp_path / 'arm-startup'
    state.mkdir()
    (state / 'inputs.json').write_text('{}')
    def fake_run(*args, **kwargs):
        build.mkdir()
        (build / 'sender').write_bytes(b'exe')
        (build / 'report').write_bytes(b'report')
        (build / 'dist').mkdir()
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *args, **kwargs: '12.1')
    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    monkeypatch.setattr(module, 'capture', lambda *args: [])
    with pytest.raises(ValueError):
        module.compile_and_capture(['compiler'], Path(__file__).resolve().parents[1], build, build / 'sender', build / 'report', build / 'dist', runtime, inventory, provenance, [], arm_state=state)
    assert not provenance.exists()


def test_arm_cc_selects_pinned_nuitka_gcc_c11_route(tmp_path, monkeypatch):
    import ast
    import os
    from types import SimpleNamespace

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import capture_sender_build as module

    class Captured(Exception):
        pass

    def capture_command(*args, **kwargs):
        raise Captured(kwargs['env']['CC'])

    state = tmp_path / 'state'
    state.mkdir()
    (state / 'inputs.json').write_text('{}')
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *args, **kwargs: '12.1')
    monkeypatch.setattr(module.subprocess, 'run', capture_command)
    with pytest.raises(Captured) as caught:
        module.compile_and_capture(['nuitka'], Path(__file__).resolve().parents[1],
                                   tmp_path / 'build', tmp_path / 'sender', tmp_path / 'report',
                                   tmp_path / 'dist', tmp_path / 'runtime', tmp_path / 'inventory',
                                   tmp_path / 'provenance', [], arm_state=tmp_path / 'state')
    cc = caught.value.args[0]
    fixture = Path(__file__).parent / 'fixtures/nuitka-2.7.16'
    namespace = {'os': os, 'hasFilenameExtension': lambda path, ext: path.endswith(ext),
                 'cheapCopyFile': lambda *args: None, 'isWin32Windows': lambda: False}
    exec(compile(ast.parse((fixture / 'SconsUtils.py').read_text()), 'SconsUtils.py', 'exec'), namespace)
    assert Path(cc).is_file() and os.access(cc, os.X_OK)
    monkeypatch.setenv('CC', cc)
    for script in ('Backend.scons', 'Onefile.scons'):
        class Environment(dict):
            pass
        env = Environment(CC='default-cc', CXX='default-c++')
        env.experimental_flags = ()
        env.clangcl_mode = env.msvc_mode = False
        env.gcc_version = (12,)
        env.nuitka_src = env.source_dir = str(tmp_path)
        scope = {**namespace, 'env': env, 'clang_mode': False,
                 'scons_details_logger': SimpleNamespace(info=lambda *args: None)}
        exec(compile(ast.parse((fixture / script).read_text()), script, 'exec'), scope)
        exec(compile(ast.parse((fixture / 'SconsCompilerSettings.py').read_text()), 'settings', 'exec'), scope)
        assert env.the_compiler == cc and env['CC'] == cc
        assert env.gcc_mode and scope['c11_mode']
        env.c11_mode = scope['c11_mode']
        (tmp_path / 'module.sample.c').touch()
        assert list(scope['scanSourceDir'](env, str(tmp_path), [])) == [str(tmp_path / 'module.sample.c')]
        assert scope['provideStaticSourceFile'](env, 'OnefileBootstrap.c', env.c11_mode).endswith('.c')


def test_arm_launcher_runs_version_probe_without_path_or_inherited_state(tmp_path):
    import json
    import subprocess
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    import capture_sender_build as module

    state = tmp_path / "private state"
    state.mkdir()
    (state / 'inputs.json').write_text(json.dumps({'build_dir': str(tmp_path)}))
    launcher = module.arm_launcher(Path(__file__).resolve().parents[1], state)
    result = subprocess.run([str(launcher), '-dumpversion', '-dumpfullversion'],
                            env={'PATH': '/nonexistent'}, text=True, capture_output=True, check=True)
    expected = subprocess.check_output(['/usr/bin/gcc', '-dumpversion', '-dumpfullversion'], text=True)
    assert result.stdout == expected
    import ast
    import re
    from types import SimpleNamespace
    fixture = Path(__file__).parent / 'fixtures/nuitka-2.7.16/SconsHacks.py'
    def execute(command):
        completed = subprocess.run(command, env={'PATH': '/nonexistent'}, capture_output=True)
        return completed.stdout, completed.stderr, completed.returncode
    gcc_name = next(node for node in ast.parse((fixture.parent / 'SconsUtils.py').read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'isGccName')
    scope = {}
    exec(compile(ast.Module(body=[gcc_name], type_ignores=[]), 'SconsUtils.py', 'exec'), scope)
    scope.update({'executeProcess': execute,
             'decodeData': lambda value: value.decode(), 're': re,
             'scons_details_logger': SimpleNamespace(info=lambda *args: None)})
    exec(compile(ast.parse(fixture.read_text()), str(fixture), 'exec'), scope)
    assert scope['_myDetectVersion'](str(launcher)) == tuple(map(int, expected.strip().split('.')))


def test_arm_preflight_stops_before_compilation_on_version_mismatch(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))
    import capture_sender_build as module
    state = tmp_path / 'state'
    state.mkdir()
    (state / 'inputs.json').write_text('{}')
    results = iter(['12.1', '13.1'])
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *args, **kwargs: next(results))
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs: pytest.fail('compiler started'))
    with pytest.raises(ValueError, match='version probe mismatch'):
        module.compile_and_capture(['nuitka'], Path(__file__).resolve().parents[1], tmp_path / 'build',
                                   tmp_path / 'sender', tmp_path / 'report', tmp_path / 'dist',
                                   tmp_path / 'runtime', tmp_path / 'inventory', tmp_path / 'provenance',
                                   [], arm_state=state)
