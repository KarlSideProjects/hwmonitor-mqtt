import json
from pathlib import Path

from _pwsh import run_pwsh


ROOT = Path(__file__).resolve().parents[1]


def run_rehearsal(tmp_path, mode):
    lines = (ROOT / '.github/workflows/protected-windows-package.yml').read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if 'name: Rehearse absent Microsoft runtime' in line)
    start = next(i for i in range(start, len(lines)) if lines[i].strip() == 'run: |') + 1
    end = next(i for i in range(start, len(lines)) if lines[i].startswith('      - '))
    body = '\n'.join(line[10:] for line in lines[start:end])
    stage = tmp_path / 'candidate-staged'
    (stage / 'scripts/windows').mkdir(parents=True)
    (stage / 'sender.exe').write_bytes(b'sender')
    (stage / 'scripts/windows/install_windows_sender_task.ps1').write_text('')
    activator = tmp_path / 'scripts/windows/activate_sender_package.ps1'
    activator.parent.mkdir(parents=True)
    activator.write_bytes((ROOT / 'scripts/windows/activate_sender_package.ps1').read_bytes())
    cached = tmp_path / 'programdata/Package Cache/test/vc_redist.x64.exe'
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'cached signed installer')
    (tmp_path / 'vc_redist.x64.exe').write_bytes(b'recovery signed installer')
    mocks = r'''
$global:installed = $true
$global:actions = [Collections.Generic.List[string]]::new()
function Get-AuthenticodeSignature { param($LiteralPath) [pscustomobject]@{Status=$(if ($env:MODE -eq 'bad-signer' -and $LiteralPath -eq $env:CACHED) {'NotSigned'} else {'Valid'});SignerCertificate=[pscustomobject]@{Subject='CN=Microsoft Corporation, O=Microsoft Corporation'}} }
function Get-ScheduledTask { param($TaskName,$ErrorAction) return $null }
function Get-ItemProperty { param($Path,$LiteralPath,$ErrorAction)
  if ($Path) { return [pscustomobject]@{DisplayName='Microsoft Visual C++ 2015-2022 Redistributable (x64) - 14.44.35211';QuietUninstallString=('"' + $env:CACHED + '" /uninstall /quiet')} }
  if ($global:installed) { return [pscustomobject]@{Installed=1;Version='v14.44.35211.0'} }
  return $null
}
function Get-Item { param($LiteralPath,$ErrorAction) if ($LiteralPath -match 'System32[\\/](mfc140u|vcruntime140|vcruntime140_1)\.dll$') { if (-not $global:installed) { throw 'missing' }; return [pscustomobject]@{VersionInfo=[pscustomobject]@{FileVersion='14.44.35211.0'}} }; Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath }
function Test-Path { param($LiteralPath,$Path,$PathType,$ErrorAction) $p = if ($LiteralPath) {$LiteralPath} else {$Path}; if ($p -like 'Registry::*VC*Runtimes*x64') { return $global:installed }; if ($p -match 'System32[\\/](mfc140u|vcruntime140|vcruntime140_1)\.dll$') { return ($global:installed -or $env:MODE -eq 'residual') }; Microsoft.PowerShell.Management\Test-Path -LiteralPath $p }
function Start-Process { param($FilePath,$ArgumentList,[switch]$Wait,[switch]$PassThru)
  if ($ArgumentList -contains '/uninstall') { $global:actions.Add('uninstall'); $global:installed=$false; return [pscustomobject]@{ExitCode=$(if ($env:MODE -in @('reboot', 'both-fail')) {3010} else {0})} }
  $global:actions.Add('install'); $global:installed=$true; return [pscustomobject]@{ExitCode=$(if ($env:MODE -eq 'both-fail') {99} else {0})}
}
'''
    result = run_pwsh(mocks + body, env={'GITHUB_WORKSPACE': str(tmp_path), 'RUNNER_TEMP': str(tmp_path),
        'ProgramData': str(tmp_path / 'programdata'), 'WINDIR': str(tmp_path), 'CACHED': str(cached),
        'RUNNER_ENVIRONMENT': ('self-hosted' if mode == 'self-hosted' else 'github-hosted'), 'RUNNER_OS': 'Windows', 'RUNNER_ARCH': 'X64',
        'GITHUB_ACTIONS': 'true', 'BUILD_SOURCE': 'a' * 40, 'MODE': mode})
    evidence = json.loads((tmp_path / 'vc-runtime-absence.json').read_text())
    return result, evidence


def test_absence_rehearsal_checks_and_restores(tmp_path):
    result, evidence = run_rehearsal(tmp_path, 'normal')
    assert result.returncode == 0, result.stdout + result.stderr
    assert evidence['status'] == 'verified'
    assert evidence['uninstall_exit'] == 0 and evidence['restore_exit'] == 0
    assert evidence['negative_preflight'] and evidence['positive_preflight']


def test_reboot_required_is_inconclusive_and_restored(tmp_path):
    result, evidence = run_rehearsal(tmp_path, 'reboot')
    assert result.returncode != 0
    assert evidence['status'] == 'inconclusive'
    assert evidence['uninstall_exit'] == 3010 and evidence['restore_exit'] == 0
    assert evidence['positive_preflight']


def test_rehearsal_refuses_untrusted_host_or_unsigned_uninstaller(tmp_path):
    for mode in ('self-hosted', 'bad-signer'):
        case = tmp_path / mode
        case.mkdir()
        result, evidence = run_rehearsal(case, mode)
        assert result.returncode != 0
        assert evidence['status'] == 'inconclusive'
        assert evidence['uninstall_exit'] is None and evidence['restore_exit'] is None


def test_rehearsal_reports_original_and_restoration_failures(tmp_path):
    result, evidence = run_rehearsal(tmp_path, 'both-fail')
    assert result.returncode != 0
    assert '3010' in evidence['failure']
    assert '99' in evidence['restore_failure']
    assert evidence['status'] == 'inconclusive'


def test_remaining_dlls_do_not_count_as_absence_and_are_restored(tmp_path):
    result, evidence = run_rehearsal(tmp_path, 'residual')
    assert result.returncode != 0
    assert evidence['status'] == 'inconclusive'
    assert 'DLLs remain' in evidence['failure']
    assert not evidence['negative_preflight'] and evidence['positive_preflight']
    assert evidence['restore_exit'] == 0
