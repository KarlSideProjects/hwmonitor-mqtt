from pathlib import Path


def test_release_workflow_is_master_only_and_has_no_publisher_credentials():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    assert 'branches: [master]' in workflow
    assert 'workflow_dispatch:' in workflow
    assert "github.ref == 'refs/heads/master'" in workflow
    assert 'fetch-depth: 0' in workflow
    assert 'persist-credentials: false' in workflow
    assert workflow.count('secrets.SENDER_RELEASE_TOKEN') == 1
    assert 'environment: sender-release-publisher' in workflow
    assert 'scripts/release_controller.py drain' in workflow
    assert 'schedule:' in workflow
    assert 'scripts/release_version.py' in workflow
    assert 'publication is disabled' in workflow


def test_recovery_dispatch_accepts_source_only_as_data():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    assert 'source:' in workflow
    assert 'SOURCE: ${{ inputs.source || github.sha }}' in workflow
    assert 'scripts/release_version.py --master "$MASTER" "$SOURCE"' in workflow
