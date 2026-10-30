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
