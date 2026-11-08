from pathlib import Path

from _pwsh import run_pwsh


ACTIVATOR = Path(__file__).resolve().parents[1] / 'scripts/windows/activate_source.ps1'


def source_tree(base, version):
    (base / 'hwmonitor_mqtt').mkdir(parents=True)
    (base / 'hwmonitor_mqtt' / 'sender.py').write_text(version)
    (base / 'scripts/windows').mkdir(parents=True)
    (base / 'scripts/windows/install_windows_sender_task.ps1').write_text('param($ProjectRoot, [switch]$AtStartup)\n')
    (base / 'scripts/windows/start_windows_sender.ps1').write_text(version)
    for name in ('pyproject.toml', 'uv.lock', 'install.sh', '.python-version', 'README.md'):
        (base / name).write_text(version)


def test_start_failure_restores_previous_source_runtime_and_settings(tmp_path):
    root, stage = tmp_path / 'root', tmp_path / 'stage'
    source_tree(root, 'old')
    source_tree(stage, 'new')
    (root / '.env').write_text('private settings')
    (root / '.venv').mkdir()
    (root / '.venv' / 'python').write_text('old runtime')
    (root / '.hwmonitor-mqtt-bootstrap').write_text('old commit')
    shell = r'''
$global:state = 'Running'
$global:attempts = 0
function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{State=$global:state;Actions=@([pscustomobject]@{Execute='powershell.exe';Arguments=('-File "' + (Join-Path $env:ROOT 'scripts/windows/start_windows_sender.ps1') + '" -ProjectRoot "' + $env:ROOT + '"')})} }
function Export-ScheduledTask { param($TaskName) '<Task />' }
function Stop-ScheduledTask { param($TaskName) $global:state = 'Ready' }
function Register-ScheduledTask { param($TaskName, $Xml, [switch]$Force) }
function Start-ScheduledTask { param($TaskName) $global:attempts++; if ($global:attempts -eq 1) { throw 'injected start failure' } }
function powershell.exe { New-Item -ItemType Directory -Path (Join-Path $env:ROOT '.venv') | Out-Null; Set-Content -LiteralPath (Join-Path $env:ROOT '.venv/new-python') -Value 'new runtime'; $global:LASTEXITCODE = 0 }
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedSource $env:STAGE -Source ('a' * 40); exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }
'''
    result = run_pwsh(shell, env={'ACTIVATOR': str(ACTIVATOR), 'ROOT': str(root), 'STAGE': str(stage)})
    assert result.returncode != 0
    assert 'injected start failure' in result.stdout
    assert (root / 'hwmonitor_mqtt/sender.py').read_text() == 'old'
    assert (root / '.venv/python').read_text() == 'old runtime'
    assert (root / '.env').read_text() == 'private settings'
    assert (root / '.hwmonitor-mqtt-bootstrap').read_text() == 'old commit'


def test_unrecognized_task_is_not_replaced(tmp_path):
    root, stage = tmp_path / 'root', tmp_path / 'stage'
    source_tree(root, 'old')
    source_tree(stage, 'new')
    shell = r'''
function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{State='Ready';Actions=@([pscustomobject]@{Execute='other.exe';Arguments='unrelated'})} }
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedSource $env:STAGE -Source ('a' * 40); exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }
'''
    result = run_pwsh(shell, env={'ACTIVATOR': str(ACTIVATOR), 'ROOT': str(root), 'STAGE': str(stage)})
    assert result.returncode != 0
    assert 'unrecognized' in result.stdout.lower()
    assert (root / 'hwmonitor_mqtt/sender.py').read_text() == 'old'


def test_unconfirmed_previous_stop_keeps_source_and_recovery_backup(tmp_path):
    root, stage = tmp_path / 'root', tmp_path / 'stage'
    source_tree(root, 'old')
    source_tree(stage, 'new')
    shell = r'''
function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{State='Running';Actions=@([pscustomobject]@{Execute='powershell.exe';Arguments=('-File "' + (Join-Path $env:ROOT 'scripts/windows/start_windows_sender.ps1') + '" -ProjectRoot "' + $env:ROOT + '"')})} }
function Export-ScheduledTask { param($TaskName) '<Task />' }
function Stop-ScheduledTask { param($TaskName) throw 'stop transport failure' }
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedSource $env:STAGE -Source ('a' * 40); exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }
'''
    result = run_pwsh(shell, env={'ACTIVATOR': str(ACTIVATOR), 'ROOT': str(root), 'STAGE': str(stage)})
    assert result.returncode != 0
    assert 'unconfirmed' in result.stdout.lower()
    assert (root / 'hwmonitor_mqtt/sender.py').read_text() == 'old'
    assert list(root.glob('source-backup-*'))


def test_success_keeps_new_source_and_commits_version(tmp_path):
    root, stage = tmp_path / 'root', tmp_path / 'stage'
    source_tree(root, 'old')
    source_tree(stage, 'new')
    (root / '.env').write_text('private settings')
    (root / '.hwmonitor-mqtt-bootstrap').write_text('old commit')
    shell = r'''
$global:state = 'Ready'
function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{State=$global:state;Actions=@([pscustomobject]@{Execute='powershell.exe';Arguments=('-File "' + (Join-Path $env:ROOT 'scripts/windows/start_windows_sender.ps1') + '" -ProjectRoot "' + $env:ROOT + '"')})} }
function Export-ScheduledTask { param($TaskName) '<Task />' }
function Start-ScheduledTask { param($TaskName) $global:state = 'Running' }
function powershell.exe { $global:LASTEXITCODE = 0 }
& $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedSource $env:STAGE -Source ('a' * 40)
'''
    result = run_pwsh(shell, env={'ACTIVATOR': str(ACTIVATOR), 'ROOT': str(root), 'STAGE': str(stage)})
    assert result.returncode == 0, result.stderr
    assert (root / 'hwmonitor_mqtt/sender.py').read_text() == 'new'
    assert (root / '.python-version').read_text() == 'new'
    assert (root / 'README.md').read_text() == 'new'
    assert (root / '.env').read_text() == 'private settings'
    assert (root / '.hwmonitor-mqtt-bootstrap').read_text() == 'a' * 40 + '\n'
    assert not list(root.glob('source-backup-*'))


def test_failed_recovery_retains_old_task_definition(tmp_path):
    root, stage = tmp_path / 'root', tmp_path / 'stage'
    source_tree(root, 'old')
    source_tree(stage, 'new')
    shell = r'''
$global:checks = 0
function Get-ScheduledTask { param($TaskName, $ErrorAction) $global:checks++; [pscustomobject]@{State=$(if ($global:checks -eq 1) {'Ready'} else {'Running'});Actions=@([pscustomobject]@{Execute='powershell.exe';Arguments=('-File "' + (Join-Path $env:ROOT 'scripts/windows/start_windows_sender.ps1') + '" -ProjectRoot "' + $env:ROOT + '"')})} }
function Export-ScheduledTask { param($TaskName) '<Task />' }
function Start-ScheduledTask { param($TaskName) throw 'injected start failure' }
function Stop-ScheduledTask { param($TaskName) throw 'injected recovery failure' }
function powershell.exe { $global:LASTEXITCODE = 0 }
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedSource $env:STAGE -Source ('a' * 40); exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }
'''
    result = run_pwsh(shell, env={'ACTIVATOR': str(ACTIVATOR), 'ROOT': str(root), 'STAGE': str(stage)})
    assert result.returncode != 0
    assert 'recovery failed' in result.stdout.lower()
    backups = list(root.glob('source-backup-*'))
    assert len(backups) == 1
    assert (backups[0] / 'task.xml').read_text() == '<Task />'
    assert (backups[0] / 'hwmonitor_mqtt/sender.py').read_text() == 'old'


def test_partial_copy_failure_restores_original_files(tmp_path):
    root, stage = tmp_path / 'root', tmp_path / 'stage'
    source_tree(root, 'old')
    source_tree(stage, 'new')
    (root / '.env').write_text('private settings')
    shell = r'''
function Get-ScheduledTask { param($TaskName, $ErrorAction) $null }
function Copy-Item {
    param($LiteralPath, $Destination, [switch]$Recurse)
    if ($LiteralPath -eq (Join-Path $env:STAGE 'scripts')) {
        New-Item -ItemType Directory -Path $Destination | Out-Null
        Set-Content -LiteralPath (Join-Path $Destination 'partial') -Value 'partial'
        throw 'injected partial copy failure'
    }
    Microsoft.PowerShell.Management\Copy-Item -LiteralPath $LiteralPath -Destination $Destination -Recurse:$Recurse
}
try { & $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedSource $env:STAGE -Source ('a' * 40); exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }
'''
    result = run_pwsh(shell, env={'ACTIVATOR': str(ACTIVATOR), 'ROOT': str(root), 'STAGE': str(stage)})
    assert result.returncode != 0
    assert 'partial copy failure' in result.stdout
    assert (root / 'hwmonitor_mqtt/sender.py').read_text() == 'old'
    assert (root / 'scripts/windows/start_windows_sender.ps1').read_text() == 'old'
    assert (root / '.env').read_text() == 'private settings'


def test_redirected_native_progress_does_not_abort_successful_install(tmp_path):
    root, stage = tmp_path / 'root', tmp_path / 'stage'
    source_tree(root, 'old')
    source_tree(stage, 'new')
    (root / '.env').write_text('private settings')
    shell = r'''
$global:state = 'Ready'
function Get-ScheduledTask { param($TaskName, $ErrorAction) [pscustomobject]@{State=$global:state;Actions=@([pscustomobject]@{Execute='powershell.exe';Arguments=('-File "' + (Join-Path $env:ROOT 'scripts/windows/start_windows_sender.ps1') + '" -ProjectRoot "' + $env:ROOT + '"')})} }
function Export-ScheduledTask { param($TaskName) '<Task />' }
function Start-ScheduledTask { param($TaskName) $global:state = 'Running' }
function powershell.exe { Write-Error 'download progress'; $global:LASTEXITCODE = 0 }
& $env:ACTIVATOR -ProjectRoot $env:ROOT -StagedSource $env:STAGE -Source ('a' * 40) *> $env:LOG
'''
    result = run_pwsh(shell, env={'ACTIVATOR': str(ACTIVATOR), 'ROOT': str(root), 'STAGE': str(stage),
                                   'LOG': str(tmp_path / 'install.log')})
    assert result.returncode == 0, result.stderr
    assert (root / 'hwmonitor_mqtt/sender.py').read_text() == 'new'
    assert (root / '.env').read_text() == 'private settings'
