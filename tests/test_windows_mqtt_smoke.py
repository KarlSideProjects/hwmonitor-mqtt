from pathlib import Path

import pytest

from _pwsh import run_pwsh


SCRIPT = Path(__file__).resolve().parent / 'windows_compiled_mqtt_smoke.ps1'


@pytest.mark.parametrize('available,count,smartctl', [('false', 1, 'false'), ('true', 0, 'false'), ('true', 1, 'true')])
def test_required_wmi_fallback_fails_if_unverified(available, count, smartctl):
    text = SCRIPT.read_text()
    start = text.index('function Test-WmiFallbackAvailable')
    end = text.index('# End WMI fallback availability check', start)
    function = text[start:end]
    result = run_pwsh(function + f"\n$baseline = [pscustomobject]@{{available=${available};physical_count={count}}}; try {{ Test-WmiFallbackAvailable $baseline ${smartctl} $true; exit 0 }} catch {{ Write-Output $_.Exception.Message; exit 1 }}")
    assert result.returncode != 0
    assert 'WMI fallback' in result.stdout


def test_available_wmi_fallback_is_selected():
    text = SCRIPT.read_text()
    start = text.index('function Test-WmiFallbackAvailable')
    end = text.index('# End WMI fallback availability check', start)
    result = run_pwsh(text[start:end] + "\n$baseline = [pscustomobject]@{available=$true;physical_count=1}; if (Test-WmiFallbackAvailable $baseline $false $true) { exit 0 }; exit 1")
    assert result.returncode == 0, result.stdout + result.stderr


def test_nonrequired_unavailable_wmi_remains_optional():
    text = SCRIPT.read_text()
    start = text.index('function Test-WmiFallbackAvailable')
    end = text.index('# End WMI fallback availability check', start)
    result = run_pwsh(text[start:end] + "\n$baseline = [pscustomobject]@{available=$false;physical_count=0}; if (Test-WmiFallbackAvailable $baseline $false $false) { exit 1 }; exit 0")
    assert result.returncode == 0, result.stdout + result.stderr
