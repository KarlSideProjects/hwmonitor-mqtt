import os
from pathlib import Path
import subprocess


BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap.sh"


def test_unsupported_architecture_is_rejected_before_download_or_install(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"uname": "#!/bin/sh\necho aarch64\n", "id": "#!/bin/sh\necho 0\n", "curl": f"#!/bin/sh\ntouch '{tmp_path / 'downloaded'}'\nexit 1\n"}.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    result = subprocess.run(["bash", str(BOOTSTRAP)], env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HWMONITOR_BOOTSTRAP_ROOT": str(tmp_path / "root")}, text=True, capture_output=True)
    assert result.returncode != 0
    assert "architecture" in result.stderr.lower()
    assert not (tmp_path / "downloaded").exists()
    assert not (tmp_path / "root").exists()


def test_download_failure_never_uses_private_source_and_preserves_old_install(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "requests"
    for name, body in {"uname": "#!/bin/sh\necho x86_64\n", "id": "#!/bin/sh\necho 0\n", "curl": f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{calls}'\nexit 1\n"}.items():
        path = bin_dir / name; path.write_text(body); path.chmod(0o755)
    root = tmp_path / "root"
    root.mkdir()
    for path, value in {".hwmonitor-mqtt-bootstrap": "", "sender": "old executable", ".env": "private"}.items():
        (root / path).write_text(value)
    result = subprocess.run(["bash", str(BOOTSTRAP)], env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HWMONITOR_BOOTSTRAP_ROOT": str(root)}, text=True, capture_output=True)
    assert result.returncode != 0
    assert calls.exists(), "public release API was not contacted"
    assert "hwmonitor-mqtt-releases" in calls.read_text()
    assert "hwmonitor-mqtt/archive" not in calls.read_text()
    assert (root / "sender").read_text() == "old executable"
    assert (root / ".env").read_text() == "private"
