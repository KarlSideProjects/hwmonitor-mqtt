from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/sender-runtime-fixture-rehearsal.yml'


def test_artifact_rehearsal_is_disabled_until_the_distinct_branch_is_pushed():
    text = WORKFLOW.read_text()
    assert 'branches: [jhihweijhan/sender20-runtime-fixture-rehearsal]' in text
    assert 'workflow_dispatch:' not in text
    assert 'actions: read' in text and 'contents: read' in text
    assert 'run-id: 41139949009' in text and 'run-id: 41105755482' in text
    for value in ('1b9ea2349623fb77eb8bbb242a111747686a1e75', '46bcc40d232cc2720b188c795d87e33d0894ea73',
                  '927dab2c148f7c2cb0e4290e2297d470facd24f1', '4a6bf626ab71f6120ff408fca821b58a39053f4ac1ec5051c9882fd21d69b317',
                  'a8e84fe15966afc3ec26b58615b9a427cf84e426dd0512e4f2830296cf067a35'):
        assert value in text
    assert '-m nuitka' not in text and '--onefile' not in text
    assert '-RequireWmiFallback' in text


def run_absence_step(tmp_path, mode):
    import json
    from _pwsh import run_pwsh

    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if 'name: Rehearse exact signed installed runtime' in line)
    start = next(i for i in range(start, len(lines)) if lines[i].strip() == 'run: |') + 1
    end = next(i for i in range(start, len(lines)) if lines[i].startswith('      - '))
    body = '\n'.join(line[10:] for line in lines[start:end])
    activator = tmp_path / 'scripts/windows/activate_sender_package.ps1'
    activator.parent.mkdir(parents=True)
    activator.write_text("param($ProjectRoot,$StagedPackage,$Source,[switch]$CheckPrerequisitesOnly)\nif (-not $global:installed) { throw 'Visual C++ runtime missing' }\n")
    (tmp_path / 'candidate-staged').mkdir()
    mocks = r'''
Set-Location $env:RUNNER_TEMP
$global:installed = $true
function Get-ScheduledTask { param($TaskName,$ErrorAction) return $null }
function Invoke-WebRequest { param([switch]$UseBasicParsing,$Uri,$OutFile) [IO.File]::WriteAllText($OutFile,'official') }
function Get-AuthenticodeSignature { param($LiteralPath) [pscustomobject]@{Status=$(if ($env:MODE -eq 'bad-signer') {'NotSigned'} else {'Valid'});SignerCertificate=[pscustomobject]@{Subject='CN=Microsoft Corporation, O=Microsoft Corporation'}} }
function Get-FileHash { param($LiteralPath,$Algorithm) if ($LiteralPath -like '*sender.exe') { return [pscustomobject]@{Hash='1AEFAA9C52AD0E79B8CF1DD4216E775C142F5FF9BE877DF4C6EF337EE6A41593'} }; return [pscustomobject]@{Hash=$(if ($env:MODE -eq 'wrong-hash') {'0' * 64} else {'843068991DAAA1F73AD9F6239BCE4D0F6A07A51F18C37EA2A867E9BECA71295C'})} }
function Get-Item { param($LiteralPath) [pscustomobject]@{VersionInfo=[pscustomobject]@{ProductVersion='14.51.36247.0';ProductName='Microsoft Visual C++ v14 Redistributable (x64) - 14.51.36247'}} }
function Get-ItemProperty { param($LiteralPath,$ErrorAction)
  if ($LiteralPath -match 'Runtimes') { return [pscustomobject]@{Installed=1;Version=$(if ($env:MODE -eq 'wrong-version') {'v14.52.1.0'} else {'v14.51.36247.00'})} }
  if ($LiteralPath -match '0e3bb569') { return [pscustomobject]@{DisplayName='Microsoft Visual C++ v14 Redistributable (x64) - 14.51.36247';DisplayVersion='14.51.36247.0';Publisher='Microsoft Corporation'} }
  if ($LiteralPath -match '931A2CF0') { return [pscustomobject]@{DisplayName='Microsoft Visual C++ 2022 X64 Minimum Runtime - 14.51.36247';DisplayVersion='14.51.36247';Publisher='Microsoft Corporation'} }
  return [pscustomobject]@{DisplayName='Microsoft Visual C++ 2022 X64 Additional Runtime - 14.51.36247';DisplayVersion='14.51.36247';Publisher='Microsoft Corporation'}
}
function Test-Path { param($LiteralPath,$Path,$PathType,$ErrorAction) $p=if ($LiteralPath) {$LiteralPath} else {$Path}; if ($p -like 'Registry::*' -or $p -match 'System32[\\/](mfc140u|vcruntime140|vcruntime140_1)\.dll$') { return ($global:installed -or $env:MODE -eq 'residual') }; Microsoft.PowerShell.Management\Test-Path -LiteralPath $p }
function Start-Process { param($FilePath,$ArgumentList,[switch]$Wait,[switch]$PassThru)
  if ($ArgumentList -contains '/uninstall') { $global:installed=$false; return [pscustomobject]@{ExitCode=$(if ($env:MODE -in @('reboot', 'both-fail')) {3010} else {0})} }
  $global:installed=$true; return [pscustomobject]@{ExitCode=$(if ($env:MODE -eq 'both-fail') {99} else {0})}
}
'''
    result = run_pwsh(mocks + body, env={'GITHUB_ACTIONS':'true', 'RUNNER_ENVIRONMENT':('self-hosted' if mode == 'self-hosted' else 'github-hosted'),
        'RUNNER_OS':'Windows', 'RUNNER_ARCH':'X64', 'RUNNER_TEMP':str(tmp_path), 'WINDIR':str(tmp_path), 'MODE':mode})
    return result, json.loads((tmp_path / 'fixture-runtime-absence.json').read_text())


def test_exact_runtime_rehearsal_checks_absence_and_restores(tmp_path):
    result, evidence = run_absence_step(tmp_path, 'normal')
    assert result.returncode == 0, result.stdout + result.stderr
    assert evidence['status'] == 'verified'
    assert evidence['negative_preflight'] and evidence['positive_preflight']
    assert evidence['uninstall_exit'] == 0 and evidence['restore_exit'] == 0


def test_exact_runtime_rehearsal_refuses_wrong_version_without_uninstall(tmp_path):
    result, evidence = run_absence_step(tmp_path, 'wrong-version')
    assert result.returncode != 0
    assert evidence['status'] == 'inconclusive'
    assert evidence['uninstall_exit'] is None and evidence['restore_exit'] is None


def test_exact_runtime_rehearsal_restores_after_reboot_required(tmp_path):
    result, evidence = run_absence_step(tmp_path, 'reboot')
    assert result.returncode != 0
    assert evidence['status'] == 'inconclusive'
    assert evidence['uninstall_exit'] == 3010 and evidence['restore_exit'] == 0
    assert evidence['positive_preflight']


def test_exact_runtime_rehearsal_rejects_untrusted_host_and_signature(tmp_path):
    for mode in ('self-hosted', 'bad-signer'):
        case = tmp_path / mode
        case.mkdir()
        result, evidence = run_absence_step(case, mode)
        assert result.returncode != 0
        assert evidence['status'] == 'inconclusive'
        assert evidence['uninstall_exit'] is None and evidence['restore_exit'] is None


def test_exact_runtime_rehearsal_restores_if_a_dll_remains(tmp_path):
    result, evidence = run_absence_step(tmp_path, 'residual')
    assert result.returncode != 0
    assert evidence['status'] == 'inconclusive'
    assert 'DLLs remain' in evidence['failure']
    assert evidence['restore_exit'] == 0 and evidence['positive_preflight']


def test_artifact_workflow_powershell_blocks_parse(tmp_path):
    from _pwsh import run_pwsh

    lines = WORKFLOW.read_text().splitlines()
    starts = [i + 1 for i, line in enumerate(lines) if line.strip() == 'run: |']
    assert starts
    for index, start in enumerate(starts):
        end = next((i for i in range(start, len(lines)) if lines[i].startswith('      - ')), len(lines))
        script = tmp_path / f'block-{index}.ps1'
        script.write_text('\n'.join(line[10:] for line in lines[start:end]) + '\n')
        result = run_pwsh('$e=$null; $t=$null; [System.Management.Automation.Language.Parser]::ParseFile($env:FILE, [ref]$t, [ref]$e) | Out-Null; if ($e.Count) { $e; exit 1 }', env={'FILE':str(script)})
        assert result.returncode == 0, result.stdout + result.stderr


def test_exact_runtime_rehearsal_rejects_wrong_installer_hash(tmp_path):
    result, evidence = run_absence_step(tmp_path, 'wrong-hash')
    assert result.returncode != 0
    assert evidence['status'] == 'inconclusive'
    assert evidence['uninstall_exit'] is None and evidence['restore_exit'] is None


def test_exact_runtime_rehearsal_preserves_both_failure_messages(tmp_path):
    result, evidence = run_absence_step(tmp_path, 'both-fail')
    assert result.returncode != 0
    assert '3010' in evidence['failure'] and '99' in evidence['restore_failure']
    assert evidence['status'] == 'inconclusive'
