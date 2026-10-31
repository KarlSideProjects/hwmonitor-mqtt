"""Guard the private container QA against host-side execution and drift."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_private_container_qa_guards():
    workflow = (ROOT / '.github/workflows/windows-container-prerequisite.yml').read_text()
    script = (ROOT / 'tests/windows_container_prerequisite.ps1').read_text()
    assert 'qa/windows-container-prerequisite-20261031' in workflow
    assert 'actions: read' in workflow and 'contents: read' in workflow
    assert 'run-id: 41148709956' in workflow
    assert '31b8ebebc9004896438bed9c531313a1403df15adb648e5db2c7c6fcb173328c' in script
    assert '593cf4a5ef08cfff00a03637e45eba073b8fe9dc90b80af4449bc5f427c7cdcd' in script
    assert '843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c' in script
    inside, host = script.split('if (-not $ArtifactDir', 1)
    assert inside.count('Start-Process') == 1 and 'Start-Process' not in host
    assert '--isolation=process --network=none' in host and 'readonly' in host
    assert 'docker rm -f $name' in host and 'docker system prune' not in host
    assert 'if ($process.ExitCode -ne 0)' in inside
    assert '$_.Exception.Message -cne $expectedError' in inside
    assert inside.count("(Inventory 'C:\\stage') -cne $inventory") == 2


def test_container_result_and_runtime_inventory_guards():
    script = (ROOT / 'tests/windows_container_prerequisite.ps1').read_text()
    assert 'docker inspect --format' in script
    assert '$state.Running' in script and "$state.Status -cne 'exited'" in script and '$state.ExitCode -ne 0' in script
    assert "ConvertFrom-Json" in script and "$result.negative -cne 'passed: exact prerequisite error and unchanged inventory'" in script
    assert "$result.positive -cne 'passed: prerequisite check and unchanged inventory'" in script and '$result.failure' in script
    assert 'WOW6432Node' in script and 'CurrentVersion\\Uninstall' in script
    assert "Microsoft Visual C\\+\\+" in script and 'Redistributable|Runtime' in script


def test_evidence_copy_preserves_state_and_uses_new_directory():
    script = (ROOT / 'tests/windows_container_prerequisite.ps1').read_text()
    assert script.index("Join-Path $EvidenceDir 'state.json'") < script.index('docker cp ')
    assert "startExitCode = $exitCode; inspectExitCode = $inspectExitCode; state = $state" in script
    assert "if (Test-Path -LiteralPath $recovered)" in script
    assert 'docker cp "${name}:C:\\qa-evidence" $recovered' in script
    assert "Join-Path $recovered 'container.json'" in script
    assert 'Get-ChildItem -LiteralPath $EvidenceDir -Recurse -File' in script
