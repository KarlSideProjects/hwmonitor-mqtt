from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/protected-windows-package.yml'


def test_release_workflow_keeps_positive_runtime_checks_without_uninstalling_host_runtime():
    text = WORKFLOW.read_text()
    assert "'/uninstall'" not in text
    assert 'vc-runtime-absence.json' not in text
    assert 'vc-runtime-prerequisite.json' in text
    assert 'CheckPrerequisitesOnly' in text
    assert 'Test compiled executable sensor entrypoint' in text
    assert 'Exercise native sender with required Microsoft runtime' in text
    assert '-RequireWmiFallback' in text
    assert 'Observe compiled sender profiles on an isolated broker' in text
    assert 'Rehearse official bootstrap with Windows PowerShell 5.1' in text
    assert 'Rehearse official bootstrap with PowerShell 7' in text
    assert 'sensors-native.json' in text and 'sensors-native.log' in text
