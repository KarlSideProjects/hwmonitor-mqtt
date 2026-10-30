from pathlib import Path

from _pwsh import run_pwsh


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/sender-runtime-fixture-diagnostic.yml'


def test_diagnostic_is_read_only_and_limited_to_its_branch():
    text = WORKFLOW.read_text()
    assert 'branches: [jhihweijhan/sender20-runtime-fixture-validation]' in text
    assert 'contents: read' in text
    assert 'pull_request_target' not in text and 'workflow_run:' not in text
    assert 'Start-Process' not in text and '/uninstall' not in text and '/install' not in text
    assert 'Get-AuthenticodeSignature' in text
    assert 'Get-FileHash' in text
    assert 'uninstall_command=' not in text
    assert 'installer_execution' not in text


def test_diagnostic_powershell_parses(tmp_path):
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == 'run: |') + 1
    end = next(i for i in range(start, len(lines)) if lines[i].startswith('      - '))
    script = tmp_path / 'diagnostic.ps1'
    script.write_text('\n'.join(line[10:] for line in lines[start:end]) + '\n')
    result = run_pwsh('$e=$null; $t=$null; [System.Management.Automation.Language.Parser]::ParseFile($env:FILE, [ref]$t, [ref]$e) | Out-Null; if ($e.Count) { $e; exit 1 }', env={'FILE': str(script)})
    assert result.returncode == 0, result.stdout + result.stderr


def test_diagnostic_records_only_whitelisted_metadata(tmp_path):
    import json
    import hashlib

    cache = tmp_path / 'programdata/Package Cache/fixture/vc_redist.x64.exe'
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'cached')
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == 'run: |') + 1
    end = next(i for i in range(start, len(lines)) if lines[i].startswith('      - '))
    body = '\n'.join(line[10:] for line in lines[start:end])
    mocks = r'''
function Get-ItemProperty { param($Path,$LiteralPath,$ErrorAction)
  if ($LiteralPath) { return [pscustomobject]@{Installed=1;Version='v14.51.36247.00';Major=14;Minor=51;Bld=36247;Rbld=0} }
  return @([pscustomobject]@{PSChildName='{fixture}';DisplayName='Microsoft Visual C++ v14 Redistributable (x64) - 14.51.36247';DisplayVersion='14.51.36247';Publisher='Microsoft Corporation';QuietUninstallString=('"' + $env:CACHED + '" /uninstall /quiet')}, [pscustomobject]@{DisplayName='unrelated private program';QuietUninstallString='DO NOT EXPORT'})
}
function Invoke-WebRequest { param([switch]$UseBasicParsing,$Uri,$OutFile) [IO.File]::WriteAllText($OutFile,'official') }
function Get-AuthenticodeSignature { param($LiteralPath) [pscustomobject]@{Status='Valid';SignerCertificate=[pscustomobject]@{Subject='CN=Microsoft Corporation, O=Microsoft Corporation'}} }
function Get-Item { param($LiteralPath) [pscustomobject]@{VersionInfo=[pscustomobject]@{FileVersion='14.51.36247.0';ProductVersion='14.51.36247.0';ProductName='Microsoft Visual C++'}} }
function Get-Command { param($Name,$ErrorAction) [pscustomobject]@{Source='7z.exe'} }
function 7z.exe {
  $folder = Join-Path $env:RUNNER_TEMP 'vc-installer-metadata'
  [IO.File]::WriteAllText((Join-Path $folder '0'), '<BurnManifest><Registration Id="{bundle}" Version="14.51.36247.0" /></BurnManifest>')
  [IO.File]::WriteAllText((Join-Path $folder 'u17'), '<Application><WixBundleProperties DisplayName="Microsoft Visual C++ v14 Redistributable (x64) - 14.51.36247" /></Application>')
  $global:LASTEXITCODE = 0
}
function Start-Process { throw 'process execution is forbidden' }
'''
    result = run_pwsh(mocks + body, env={'GITHUB_ACTIONS':'true', 'RUNNER_ENVIRONMENT':'github-hosted',
        'RUNNER_OS':'Windows', 'RUNNER_ARCH':'X64', 'RUNNER_TEMP':str(tmp_path),
        'ProgramData':str(tmp_path / 'programdata'), 'CACHED':str(cache)})
    assert result.returncode == 0, result.stdout + result.stderr
    raw = (tmp_path / 'sender-runtime-diagnostic.json').read_text()
    data = json.loads(raw)
    assert data['status'] == 'complete' and data['entry_count'] == 1 and len(data['entries']) == 1
    assert data['entries'][0]['cache_status'] == 'present'
    assert data['entries'][0]['sha256'] == hashlib.sha256(b'cached').hexdigest()
    assert data['official']['bundle_id'] == '{bundle}'
    assert data['official']['bundle_version'] == '14.51.36247.0'
    assert 'DO NOT EXPORT' not in raw and 'unrelated private program' not in raw
