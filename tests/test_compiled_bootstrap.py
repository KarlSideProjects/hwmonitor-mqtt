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
