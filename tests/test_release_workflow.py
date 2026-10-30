from pathlib import Path


def test_release_workflow_is_master_only_and_has_no_publisher_credentials():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    assert 'branches: [master]' in workflow
    assert 'workflow_dispatch:' in workflow
    assert "github.ref == 'refs/heads/master'" in workflow
    assert 'fetch-depth: 0' in workflow
    assert 'persist-credentials: false' in workflow
    assert workflow.count('secrets.SENDER_RELEASE_TOKEN') == 3
    assert 'environment: sender-release-publisher' in workflow
    assert 'scripts/release_controller.py drain' in workflow
    assert 'schedule:' in workflow
    assert 'scripts/release_version.py' in workflow
    assert 'Block until native release acceptance is wired' in workflow


def test_recovery_dispatch_accepts_source_only_as_data():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    assert 'source:' in workflow
    assert 'SOURCE: ${{ inputs.source || github.sha }}' in workflow
    assert 'scripts/release_version.py --master "$MASTER" "$SOURCE"' in workflow


def test_source_identity_job_exports_verified_source_and_version():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    assert 'source: ${{ steps.identity.outputs.source }}' in workflow
    assert 'version: ${{ steps.identity.outputs.version }}' in workflow
    assert 'id: identity' in workflow
    assert '"source=$SOURCE" >> "$GITHUB_OUTPUT"' in workflow


def test_linux_builds_are_source_bound_and_have_no_publisher_token():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    linux = workflow.split('  linux-build:', 1)[1].split('  windows-build:', 1)[0]
    assert 'ubuntu-24.04-arm' in linux and 'ubuntu-24.04' in linux
    assert 'scripts/build_sender_package.py' in linux
    assert 'scripts/create_sender_build_record.py' in linux
    assert 'needs.version.outputs.source' in linux
    assert 'secrets.' not in linux


def test_preparation_requires_all_build_jobs_and_uses_controller():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    assert 'needs: [version, checks, linux-build, windows-build]' in workflow
    assert 'scripts/release_controller.py prepare' in workflow
    assert 'SOURCE_WORKFLOW_TOKEN: ${{ github.token }}' in workflow
    assert 'actions: write' in workflow


def test_build_tree_remains_private_but_owned_by_runner():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    assert 'libc6-dev' in workflow
    assert 'chown -R "$RUNNER_UID:$RUNNER_GID" /work/result' in workflow
    assert 'chmod -R' not in workflow


def test_publisher_requires_successful_prepare_or_explicit_recovery():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    publisher = workflow.split('  publisher:', 1)[1]
    assert 'needs: prepare' in publisher
    assert "needs.prepare.result == 'success'" in publisher
    assert "github.event_name == 'schedule'" in publisher
    assert "github.event_name == 'workflow_dispatch' && inputs.source == ''" in publisher
    assert 'always()' in publisher


def test_publisher_trigger_cases():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    expression = workflow.split('  publisher:', 1)[1].split('if: ${{ ', 1)[1].split(' }}', 1)[0]
    expression = expression.replace('always()', 'True').replace('&&', ' and ').replace('||', ' or ')
    for key, replacement in {'github.ref': 'ref', 'github.event_name': 'event', 'needs.prepare.result': 'result', 'inputs.source': 'source'}.items():
        expression = expression.replace(key, replacement)
    def allowed(event, result, source=''):
        return eval(expression, {'__builtins__': {}}, {'ref': 'refs/heads/master', 'event': event, 'result': result, 'source': source})
    assert allowed('push', 'success')
    assert not allowed('push', 'failure')
    assert allowed('schedule', 'skipped')
    assert allowed('workflow_dispatch', 'skipped')
    assert allowed('workflow_dispatch', 'success', 'a' * 40)
    assert not allowed('workflow_dispatch', 'failure', 'a' * 40)
