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
    def fake_run(*args, **kwargs):
        build.mkdir()
        (build / 'sender').write_bytes(b'exe')
        (build / 'report').write_bytes(b'report')
        (build / 'dist').mkdir()
    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    monkeypatch.setattr(module, 'capture', lambda *args: [])
    with pytest.raises(ValueError):
        module.compile_and_capture(['compiler'], tmp_path, build, build / 'sender', build / 'report', build / 'dist', runtime, inventory, provenance, [], arm_state=state)
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
