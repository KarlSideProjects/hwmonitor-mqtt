from pathlib import Path
import zipfile

import pytest

from _pwsh import run_pwsh


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / 'bootstrap-source.ps1'
COMMIT = 'a' * 40


def download_function():
    text = BOOTSTRAP.read_text()
    return text.split('# Begin source download', 1)[1].split('# End source download', 1)[0]


@pytest.mark.parametrize('api_limited', [False, True])
def test_download_pins_commit_and_validates_archive_before_activation(tmp_path, api_limited):
    archive = tmp_path / 'source.zip'
    prefix = 'hwmonitor-mqtt-' + COMMIT + '/'
    with zipfile.ZipFile(archive, 'w') as content:
        for name in ('hwmonitor_mqtt/agents/agent_sender_windows.py',
                     'scripts/windows/install_windows_sender_task.ps1',
                     'scripts/windows/start_windows_sender.ps1',
                     'scripts/windows/activate_source.ps1', 'pyproject.toml', 'uv.lock', 'install.sh', '.python-version', 'README.md'):
            content.writestr(prefix + name, 'content')
    stage = tmp_path / 'work'
    stage.mkdir()
    shell = r'''
$global:urls = [Collections.Generic.List[string]]::new()
function Invoke-RestMethod { param($Uri, $Headers) if ($env:API_LIMITED -eq '1') { throw 'HTTP 403' }; @{ sha = $env:COMMIT } }
function Invoke-WebRequest { param([switch]$UseBasicParsing, $Uri, $OutFile) if ($Uri.Contains('info/refs')) { [IO.File]::WriteAllText($OutFile, ("001e# service=git-upload-pack`n0000003f" + $env:COMMIT + " refs/heads/master`n0000")); return }; $global:urls.Add($Uri); if ($Uri.EndsWith('.zip')) { Copy-Item $env:ARCHIVE $OutFile } else { Copy-Item $env:VALIDATOR $OutFile } }
''' + download_function() + r'''
$result = Get-VerifiedSource -WorkDirectory $env:WORK
if ($result.Commit -ne $env:COMMIT -or -not (Test-Path (Join-Path $result.Path 'uv.lock'))) { throw 'source missing' }
if ($global:urls.Count -ne 2 -or @($global:urls | Where-Object { -not $_.Contains($env:COMMIT) }).Count -ne 0) { throw 'URLs not pinned' }
'''
    result = run_pwsh(shell, env={'WORK': str(stage), 'COMMIT': COMMIT, 'API_LIMITED': str(int(api_limited)), 'ARCHIVE': str(archive),
                                  'VALIDATOR': str(ROOT / 'scripts/windows/validate_source_archive.ps1')})
    assert result.returncode == 0, result.stderr


def test_invalid_commit_stops_before_downloading(tmp_path):
    shell = r'''
$global:downloads = 0
function Invoke-RestMethod { param($Uri, $Headers) @{ sha = '../bad' } }
function Invoke-WebRequest { param([switch]$UseBasicParsing, $Uri, $OutFile) $global:downloads++ }
''' + download_function() + r'''
try { Get-VerifiedSource -WorkDirectory $env:WORK | Out-Null; throw 'accepted invalid commit' }
catch { if ($global:downloads -ne 0 -or $_.Exception.Message -ne 'Invalid source commit.') { throw }; exit 0 }
'''
    result = run_pwsh(shell, env={'WORK': str(tmp_path)})
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('refs', ['', '0000' + 'a' * 40 + ' refs/heads/master\n',
                                  ('003f' + 'a' * 40 + ' refs/heads/master\n') * 2,
                                  '001e# service=git-upload-pack\n0000' + ('003f' + 'a' * 40 + ' refs/heads/master\n') * 2 + '0000',
                                  'not-a-git-advertisement\n003f' + 'a' * 40 + ' refs/heads/master\n',
                                  '001e# service=git-upload-pack\n0000003f' + 'a' * 40 + ' refs/heads/master\n0008xx'])
def test_fallback_rejects_invalid_or_ambiguous_master_refs(tmp_path, refs):
    shell = r'''
function Invoke-RestMethod { param($Uri, $Headers) throw 'HTTP 403' }
function Invoke-WebRequest { param([switch]$UseBasicParsing, $Uri, $OutFile)
    if (-not $Uri.Contains('info/refs')) { throw 'unexpected download' }
    [IO.File]::WriteAllText($OutFile, $env:REFS)
}
''' + download_function() + r'''
try { Get-VerifiedSource -WorkDirectory $env:WORK | Out-Null; throw 'accepted invalid refs' }
catch { if ($_.Exception.Message -ne 'Invalid source commit.') { throw }; exit 0 }
'''
    result = run_pwsh(shell, env={'WORK': str(tmp_path), 'REFS': refs})
    assert result.returncode == 0, result.stderr
