import os
from pathlib import Path

import pytest

from _pwsh import run_pwsh


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable used to exercise the launcher")
def test_compiled_sender_inherits_install_directory_and_lhm_path(tmp_path):
    sender = tmp_path / "sender"
    sender.write_text('#!/bin/sh\nprintf "%s\\n%s\\n" "$PWD" "$LHM_PATH"\nexit 23\n')
    sender.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "scripts/windows/start_windows_sender.ps1"
    result = run_pwsh(
        '& $env:START_SCRIPT -ProjectRoot $env:PROJECT_ROOT -SenderExe $env:SENDER_EXE -LhmPath $env:LHM_EXE; exit $LASTEXITCODE',
        env={"START_SCRIPT": str(script), "PROJECT_ROOT": str(tmp_path), "SENDER_EXE": str(sender), "LHM_EXE": "example-lhm.exe"},
    )
    assert result.returncode == 23, result.stdout + result.stderr
    assert result.stdout.splitlines() == [str(tmp_path), "example-lhm.exe"]


def test_task_installer_targets_compiled_sender_without_python_setup(tmp_path):
    sender = tmp_path / "sender.exe"
    sender.write_bytes(b"placeholder")
    script = Path(__file__).resolve().parents[1] / "scripts/windows/install_windows_sender_task.ps1"
    result = run_pwsh(
        '& $env:INSTALL_SCRIPT -ProjectRoot $env:PROJECT_ROOT -SenderExe $env:SENDER_EXE -EnsureLhm:$false -EnsurePawnIo:$false -WhatIf',
        env={"INSTALL_SCRIPT": str(script), "PROJECT_ROOT": str(tmp_path), "SENDER_EXE": str(sender)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'-SenderExe "{sender}"' in result.stdout
    assert "-UseUv" not in result.stdout
