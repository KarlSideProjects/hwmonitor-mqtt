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
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedPackage $env:STAGE; exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }
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
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedPackage $env:STAGE; exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }
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
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedPackage $env:STAGE; exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }
'''
    result = run_pwsh(shell, env={"ACTIVATOR": str(script), "ROOT": str(root), "STAGE": str(stage)})
    assert result.returncode != 0
    assert "exit code 23" in result.stdout
    assert (old / "sender.exe").read_text() == "old"


def test_bootstrap_checks_release_asset_against_its_checksum(tmp_path):
    import hashlib

    asset = tmp_path / "sender-windows-x64.zip"
    asset.write_bytes(b"downloaded bytes")
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (tmp_path / "sender-windows-x64.zip.sha256").write_text(f"{digest}  sender-windows-x64.zip\n")
    download = tmp_path / "download"
    download.mkdir()
    source = (Path(__file__).resolve().parents[1] / "bootstrap.ps1").read_text()
    functions = source[source.index("    function Download-ReleaseAsset"):source.index("    $versionPath")]
    shell = r'''
$release = @{assets = @(@{name='sender-windows-x64.zip'}, @{name='sender-windows-x64.zip.sha256'})}
$base = 'https://example.invalid/release'
$temp = $env:DOWNLOAD_DIR
function Invoke-WebRequest { param([switch]$UseBasicParsing, $Uri, $OutFile) Copy-Item -LiteralPath (Join-Path $env:ASSET_DIR (Split-Path $Uri -Leaf)) -Destination $OutFile }
''' + functions + r'''
$result = Download-VerifiedAsset 'sender-windows-x64.zip'
Write-Output $result.Sha256
'''
    result = run_pwsh(shell, env={"ASSET_DIR": str(tmp_path), "DOWNLOAD_DIR": str(download)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == digest
    asset.write_bytes(b"changed bytes")
    result = run_pwsh(shell, env={"ASSET_DIR": str(tmp_path), "DOWNLOAD_DIR": str(download)})
    assert result.returncode != 0
    assert "mismatch" in result.stderr.lower()


def test_failed_rollback_preserves_backup_and_reports_original_error(tmp_path):
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
function Get-ScheduledTask { param($TaskName, $ErrorAction) $global:checks++; [pscustomobject]@{State= $(if ($global:checks -eq 1) {'Ready'} else {'Running'})} }
function Export-ScheduledTask { param($TaskName) '<Task />' }
function Stop-ScheduledTask { param($TaskName) throw 'injected rollback stop failure' }
function Start-ScheduledTask { param($TaskName) throw 'injected start failure' }
function powershell.exe { $global:LASTEXITCODE = 0 }
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedPackage $env:STAGE; exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }
'''
    result = run_pwsh(shell, env={"ACTIVATOR": str(script), "ROOT": str(root), "STAGE": str(stage)})
    assert result.returncode != 0
    assert "injected start failure" in result.stdout
    assert "injected rollback stop failure" in result.stdout
    backups = list(root.glob("package-backup-*/sender.exe"))
    assert len(backups) == 1 and backups[0].read_text() == "old"
    recovery = list(root.glob("task-recovery-*.xml"))
    assert len(recovery) == 1 and recovery[0].read_text() == "<Task />"


def test_privileged_install_rejects_untrusted_write_access(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "bootstrap.ps1").read_text()
    assert "function Assert-TrustedItem" in source
    functions = source[source.index("function Assert-TrustedItem"):source.index("# End trusted path checks")]
    shell = r'''
$trustedSids = @('S-1-5-18', 'S-1-5-32-544')
function Get-Item { param($LiteralPath, [switch]$Force) [pscustomobject]@{Attributes=[IO.FileAttributes]::Directory} }
function Get-Acl { param($LiteralPath)
    $acl = [pscustomobject]@{}
    $acl | Add-Member ScriptMethod GetOwner { param($type) [pscustomobject]@{Value='S-1-5-18'} }
    $acl | Add-Member ScriptMethod GetAccessRules { param($a,$b,$type) @([pscustomobject]@{IdentityReference=[pscustomobject]@{Value='S-1-5-21-123'}; AccessControlType='Allow'; FileSystemRights=[Security.AccessControl.FileSystemRights]::WriteData}) }
    return $acl
}
''' + functions + r'''
Assert-TrustedItem $env:ITEM $false
'''
    result = run_pwsh(shell, env={"ITEM": str(tmp_path)})
    assert result.returncode != 0
    assert "untrusted" in result.stderr.lower()


def test_privileged_install_rejects_reparse_path(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "bootstrap.ps1").read_text()
    functions = source[source.index("function Assert-TrustedItem"):source.index("# End trusted path checks")]
    shell = r'''
$trustedSids = @('S-1-5-18', 'S-1-5-32-544')
function Get-Item { param($LiteralPath, [switch]$Force) [pscustomobject]@{Attributes=[IO.FileAttributes]::ReparsePoint} }
''' + functions + r'''
Assert-TrustedItem $env:ITEM $false
'''
    result = run_pwsh(shell, env={"ITEM": str(tmp_path)})
    assert result.returncode != 0
    assert "reparse" in result.stderr.lower()
