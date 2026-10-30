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


def test_compiled_install_does_not_reuse_unverified_lhm(tmp_path):
    sender = tmp_path / "sender.exe"
    sender.write_bytes(b"placeholder")
    old_lhm = tmp_path / "third_party/librehardwaremonitor/LibreHardwareMonitor.exe"
    old_lhm.parent.mkdir(parents=True)
    old_lhm.write_bytes(b"unverified")
    script = Path(__file__).resolve().parents[1] / "scripts/windows/install_windows_sender_task.ps1"
    shell = r'''
function Invoke-WebRequest { param([switch]$UseBasicParsing, $Uri, $OutFile) throw 'pinned LHM download attempted' }
try { & $env:INSTALL_SCRIPT -ProjectRoot $env:PROJECT_ROOT -SenderExe $env:SENDER_EXE -LhmInstallDir $env:NEW_LHM -EnsurePawnIo:$false -AtStartup; exit 0 } catch { Write-Output $_; exit 1 }
'''
    result = run_pwsh(shell, env={"INSTALL_SCRIPT": str(script), "PROJECT_ROOT": str(tmp_path), "SENDER_EXE": str(sender), "NEW_LHM": str(tmp_path / "new-lhm"), "TEMP": str(tmp_path)})
    assert result.returncode != 0
    assert "pinned LHM download attempted" in result.stdout
    assert old_lhm.read_bytes() == b"unverified"


def test_compiled_sender_reads_settings_from_task_working_directory(tmp_path):
    import subprocess
    import sys

    (tmp_path / ".env").write_text("BROKER_HOST=compiled-test.example\n")
    agent = Path(__file__).resolve().parents[1] / "hwmonitor_mqtt/agents/agent_sender_windows.py"
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('sender_test', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "module.__compiled__ = True\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "print(module.BROKER_HOST)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(agent.parents[2])}
    env.pop("BROKER_HOST", None)
    result = subprocess.run([sys.executable, str(runner), str(agent)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "compiled-test.example"


def test_compiled_sender_uses_installation_directory_for_lhm_fallback(tmp_path):
    import subprocess
    import sys

    agent = Path(__file__).resolve().parents[1] / "hwmonitor_mqtt/agents/agent_sender_windows.py"
    runner = tmp_path / "runner.py"
    runner.write_text(
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('sender_test', sys.argv[1])\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "module.__compiled__ = True\n"
        "sys.modules[spec.name] = module\n"
        "spec.loader.exec_module(module)\n"
        "print(module.LHMProcessManager._candidate_paths()[-1])\n"
    )
    env = {**os.environ, "PYTHONPATH": str(agent.parents[2])}
    env.pop("LHM_PATH", None)
    result = subprocess.run([sys.executable, str(runner), str(agent)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(tmp_path / "third_party/librehardwaremonitor/LibreHardwareMonitor.exe")
