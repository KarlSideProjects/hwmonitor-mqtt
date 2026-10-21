import os
import shutil
import subprocess

import pytest


def pwsh_executable():
    """PWSH_EXE selects the shell (CI sets powershell.exe for Windows PowerShell 5.1)."""
    exe = os.environ.get("PWSH_EXE") or shutil.which("pwsh")
    if exe:
        return exe
    if os.environ.get("CI"):
        pytest.fail("PowerShell is required in CI; install pwsh or set PWSH_EXE")
    pytest.skip("PowerShell runtime is unavailable")


def run_pwsh(script, env=None):
    """Run under bootstrap.ps1's ErrorActionPreference, so a failing statement cannot be masked by later ones."""
    return subprocess.run(
        [pwsh_executable(), "-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference = 'Stop'; " + script],
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )
