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
