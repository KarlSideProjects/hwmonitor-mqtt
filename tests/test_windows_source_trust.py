from pathlib import Path

import pytest

from _pwsh import run_pwsh


BOOTSTRAP = Path(__file__).resolve().parents[1] / 'bootstrap-source.ps1'


@pytest.mark.parametrize(('kind', 'target', 'accepted'), [
    ('Junction', 'inside', True),
    ('Junction', 'outside', False),
    ('SymbolicLink', 'inside', False),
])
def test_only_internal_python_runtime_junction_is_allowed(tmp_path, kind, target, accepted):
    root = tmp_path / 'root'
    python_root = root / '.uv-python'
    python_root.mkdir(parents=True)
    inside = python_root / 'cpython-version'
    inside.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    source = BOOTSTRAP.read_text().split('# Begin Python alias check', 1)[1].split('# End Python alias check', 1)[0]
    shell = r'''
$root = $env:ROOT
$global:checked = $false
function Assert-TrustedItem { param($Path, $Ancestor, $Secret) $global:checked = $true }
''' + source + r'''
$item = [pscustomobject]@{ LinkType = $env:KIND; Target = @($env:TARGET) }
try { Assert-TrustedPythonAlias $item; if (-not $global:checked) { throw 'target not checked' }; exit 0 } catch { exit 1 }
'''
    result = run_pwsh(shell, env={'ROOT': str(root), 'KIND': kind,
                                   'TARGET': str(inside if target == 'inside' else outside)})
    assert (result.returncode == 0) == accepted, result.stderr
