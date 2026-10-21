from pathlib import Path

import pytest

from _pwsh import run_pwsh

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = sorted(p for p in REPO.rglob("*.ps1") if ".venv" not in p.parts)


def test_powershell_scripts_exist():
    assert SCRIPTS


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.relative_to(REPO).as_posix())
def test_powershell_script_parses(script):
    # pwsh -Command folds trailing arguments into the command text, so pass the path via the environment.
    result = run_pwsh(
        "$errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile($env:PS_SCRIPT_PATH, [ref]$null, [ref]$errors) | Out-Null; "
        "$errors | ForEach-Object { \"$($_.Extent.StartLineNumber): $($_.Message)\" }; "
        "exit $errors.Count",
        env={"PS_SCRIPT_PATH": str(script)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
