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
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedPackage $env:STAGE; exit 0 } catch { Write-Output $_; exit 1 }
'''
    result = run_pwsh(shell, env={"ACTIVATOR": str(script), "ROOT": str(root), "STAGE": str(stage)})
    assert result.returncode != 0
    assert "injected start failure" in result.stdout
    assert (old / "sender.exe").read_text() == "old"
    assert (root / ".env").read_text() == "BROKER_HOST=preserved\n"
