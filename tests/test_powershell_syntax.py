import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(p for p in REPO.rglob("*.ps1") if ".venv" not in p.parts)


def test_powershell_scripts_exist():
    assert SCRIPTS


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: str(p.relative_to(REPO)))
def test_powershell_script_parses(script):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell runtime is unavailable")
    command = (
        "$errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile($env:PS_SCRIPT_PATH, [ref]$null, [ref]$errors) | Out-Null; "
        "$errors | ForEach-Object { \"$($_.Extent.StartLineNumber): $($_.Message)\" }; "
        "exit $errors.Count"
    )
    # pwsh -Command folds trailing arguments into the command text, so pass the path via the environment.
    env = {**os.environ, "PS_SCRIPT_PATH": str(script)}
    result = subprocess.run([pwsh, "-NoProfile", "-Command", command], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
