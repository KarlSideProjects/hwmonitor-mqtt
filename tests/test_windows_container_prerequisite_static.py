"""Guard the disabled, private container QA against host-side execution and drift."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_private_container_qa_guards():
    workflow = (ROOT / '.github/workflows/windows-container-prerequisite.yml').read_text()
    script = (ROOT / 'tests/windows_container_prerequisite.ps1').read_text()
    assert 'disabled-windows-container-prerequisite-placeholder' in workflow
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
