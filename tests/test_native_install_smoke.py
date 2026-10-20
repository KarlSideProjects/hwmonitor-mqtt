import os
from pathlib import Path
import subprocess
import sys


REPO = Path(__file__).resolve().parents[1]
SMOKE = REPO / "scripts" / "native_install_smoke.py"


def test_native_smoke_requires_explicit_run_before_touching_systemd(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SMOKE)],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--run" in result.stderr
    assert list(tmp_path.iterdir()) == []
