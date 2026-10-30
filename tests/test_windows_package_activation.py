from pathlib import Path

from _pwsh import run_pwsh


def test_start_failure_restores_previous_package_and_settings(tmp_path):
    root = tmp_path / "installed"
    old = root / "package"
    old.mkdir(parents=True)
    (old / "sender.exe").write_text("old")
    (root / ".env").write_text("BROKER_HOST=preserved\n")
    stage = tmp_path / "stage"
    scripts = stage / "scripts/windows"
    scripts.mkdir(parents=True)
    (stage / "sender.exe").write_text("new")
    (scripts / "install_windows_sender_task.ps1").write_text('param($ProjectRoot, $SenderExe, [switch]$AtStartup)\n')
    script = Path(__file__).resolve().parents[1] / "scripts/windows/activate_sender_package.ps1"
    shell = r'''
function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{State='Running'} }
function Export-ScheduledTask { param($TaskName) '<Task />' }
function Stop-ScheduledTask { param($TaskName) }
function Register-ScheduledTask { param($TaskName, $Xml, [switch]$Force) }
$global:attempts = 0
function Start-ScheduledTask { param($TaskName) $global:attempts++; if ($global:attempts -eq 1) { throw 'injected start failure' } }
function powershell.exe { $global:LASTEXITCODE = 0 }
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedPackage $env:STAGE; exit 0 } catch { Write-Output $_; exit 1 }
'''
    result = run_pwsh(shell, env={"ACTIVATOR": str(script), "ROOT": str(root), "STAGE": str(stage)})
    assert result.returncode != 0
    assert "injected start failure" in result.stdout
    assert (old / "sender.exe").read_text() == "old"
    assert (root / ".env").read_text() == "BROKER_HOST=preserved\n"


def test_public_bootstrap_does_not_download_private_source():
    source = (Path(__file__).resolve().parents[1] / "bootstrap.ps1").read_text()
    assert "api.github.com/repos/KarlSideProjects/hwmonitor-mqtt-releases/releases/latest" in source
    assert "github.com/KarlSideProjects/hwmonitor-mqtt/archive/" not in source


def test_sender_that_exits_after_initial_running_rolls_back(tmp_path):
    root = tmp_path / "installed"
    old = root / "package"
    old.mkdir(parents=True)
    (old / "sender.exe").write_text("old")
    stage = tmp_path / "stage"
    scripts = stage / "scripts/windows"
    scripts.mkdir(parents=True)
    (stage / "sender.exe").write_text("new")
    (scripts / "install_windows_sender_task.ps1").write_text('param($ProjectRoot, $SenderExe, [switch]$AtStartup)\n')
    script = Path(__file__).resolve().parents[1] / "scripts/windows/activate_sender_package.ps1"
    shell = r'''
$global:checks = 0
function Get-ScheduledTask { param($TaskName, $ErrorAction) $global:checks++; [pscustomobject]@{State= $(if ($global:checks -lt 3) {'Running'} else {'Ready'})} }
function Export-ScheduledTask { param($TaskName) '<Task />' }
function Stop-ScheduledTask { param($TaskName) }
function Register-ScheduledTask { param($TaskName, $Xml, [switch]$Force) }
function Start-ScheduledTask { param($TaskName) }
function Start-Sleep { param($Milliseconds) }
function powershell.exe { $global:LASTEXITCODE = 0 }
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedPackage $env:STAGE; exit 0 } catch { Write-Output $_; exit 1 }
'''
    result = run_pwsh(shell, env={"ACTIVATOR": str(script), "ROOT": str(root), "STAGE": str(stage)})
    assert result.returncode != 0
    assert (old / "sender.exe").read_text() == "old"


def test_task_installer_nonzero_restores_previous_package(tmp_path):
    root = tmp_path / "installed"
    old = root / "package"
    old.mkdir(parents=True)
    (old / "sender.exe").write_text("old")
    stage = tmp_path / "stage"
    scripts = stage / "scripts/windows"
    scripts.mkdir(parents=True)
    (stage / "sender.exe").write_text("new")
    (scripts / "install_windows_sender_task.ps1").write_text('exit 23\n')
    script = Path(__file__).resolve().parents[1] / "scripts/windows/activate_sender_package.ps1"
    shell = r'''
function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{State='Ready'} }
function Export-ScheduledTask { param($TaskName) '<Task />' }
function Stop-ScheduledTask { param($TaskName) }
function Register-ScheduledTask { param($TaskName, $Xml, [switch]$Force) }
function powershell.exe { $global:LASTEXITCODE = 23 }
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedPackage $env:STAGE; exit 0 } catch { Write-Output $_; exit 1 }
'''
    result = run_pwsh(shell, env={"ACTIVATOR": str(script), "ROOT": str(root), "STAGE": str(stage)})
    assert result.returncode != 0
    assert "exit code 23" in result.stdout
    assert (old / "sender.exe").read_text() == "old"
