from pathlib import Path


def test_release_workflow_is_master_only_and_has_no_publisher_credentials():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    assert 'branches: [master]' in workflow
    assert 'workflow_dispatch:' in workflow
    assert "github.ref == 'refs/heads/master'" in workflow
    assert 'fetch-depth: 0' in workflow
    assert 'persist-credentials: false' in workflow
    assert workflow.count('secrets.SENDER_RELEASE_TOKEN') == 4
    assert 'environment: sender-release-publisher' in workflow
    assert 'scripts/release_controller.py drain' in workflow
    assert 'schedule:' in workflow
    assert 'scripts/release_version.py' in workflow


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
    linux = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-linux-build.yml').read_text()
    assert 'ubuntu-24.04-arm' in linux and 'ubuntu-24.04' in linux
    assert 'scripts/build_sender_package.py' in linux
    assert 'scripts/create_sender_build_record.py' in linux
    assert 'needs.version.outputs.source' in workflow and 'inputs.source' in linux
    assert 'secrets.' not in linux


def test_preparation_requires_all_build_jobs_and_uses_controller():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    assert 'needs: [version, checks, linux-build, windows-build]' in workflow
    assert 'scripts/release_controller.py prepare' in workflow
    assert 'SOURCE_WORKFLOW_TOKEN: ${{ github.token }}' in workflow
    assert 'actions: write' in workflow


def test_build_tree_remains_private_but_owned_by_runner():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-linux-build.yml').read_text()
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
    expression = expression.replace('always()', 'True').replace('!', 'not ').replace('&&', ' and ').replace('||', ' or ')
    for key, replacement in {'github.ref': 'ref', 'github.event_name': 'event', 'needs.prepare.result': 'result', 'inputs.source': 'source', 'inputs.build_only': 'build_only'}.items():
        expression = expression.replace(key, replacement)
    def allowed(event, result, source='', ref='refs/heads/master', build_only=False):
        return eval(expression, {'__builtins__': {}}, {'ref': ref, 'event': event, 'result': result, 'source': source, 'build_only': build_only})
    assert allowed('push', 'success')
    assert not allowed('push', 'failure')
    assert allowed('schedule', 'skipped')
    assert allowed('workflow_dispatch', 'skipped')
    assert allowed('workflow_dispatch', 'success', 'a' * 40)
    assert not allowed('workflow_dispatch', 'failure', 'a' * 40)
    assert not allowed('schedule', 'skipped', ref='refs/heads/other')
    assert not allowed('workflow_dispatch', 'success', 'a' * 40, build_only=True)
    assert not allowed('workflow_dispatch', 'skipped', build_only=True)


def test_bullseye_builder_uses_archived_security_repository():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-linux-build.yml').read_text()
    assert 'http://archive.debian.org/debian-security bullseye-security main' in workflow
    assert 'Acquire::Check-Valid-Until=false' in workflow


def test_linux_upgrade_baseline_is_built_from_distinct_pinned_source():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-linux-build.yml').read_text()
    assert 'c40a66d16d23ba7fac7fba171d02e4959a9a91a9' in workflow
    assert 'scripts/build_sender_package.py --version 0.0.1 --output /work/old-result' in workflow
    assert 'test "$SOURCE" != "$OLD_SOURCE"' in workflow


def test_linux_docker_acceptance_uses_both_real_archives():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-linux-build.yml').read_text()
    assert 'scripts/release-tests/docker_acceptance.py' in workflow
    assert '--old-archive "$RUNNER_TEMP/old-result/sender-$TARGET.tar.gz"' in workflow
    assert '--old-source "$OLD_SOURCE" --old-version 0.0.1' in workflow


def test_linux_harness_uses_workflow_checkout_and_exact_source_checkout():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    linux = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-linux-build.yml').read_text()
    assert 'path: source' in linux
    assert 'git -C source rev-parse HEAD' in linux
    assert '--source-root source' in linux
    assert '--bootstrap "$GITHUB_WORKSPACE/source/bootstrap.sh"' in linux


def test_linux_release_does_not_require_pve_vm_acceptance():
    linux = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-linux-build.yml').read_text()
    assert 'pve' not in linux.lower()
    assert 'qcow2' not in linux


def test_failed_acceptance_retains_linux_evidence_and_private_build():
    linux = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-linux-build.yml').read_text()
    uploads = linux.split('      - uses: actions/upload-artifact@')[1:]
    assert len(uploads) == 2
    for upload, name, path in zip(uploads, ('sender-acceptance-', 'sender-'), ('acceptance', 'private')):
        assert 'if: ${{ always() }}' in upload
        assert f'name: {name}${{{{ inputs.platform }}}}-${{{{ inputs.source }}}}' in upload
        assert f'path: ${{{{ runner.temp }}}}/{path}\n' in upload
        assert 'retention-days: 3' in upload
    assert 'if-no-files-found: error' in uploads[-1]


def test_linux_compiled_failure_integration_uses_candidate_and_enforces_skips():
    linux = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-linux-build.yml').read_text()
    assert 'protected21-bootstrap-test:local' in linux
    assert 'HWMONITOR_TEST_ARCHIVE:' in linux
    assert 'tests/test_compiled_bootstrap_integration.py' in linux
    assert 'expected_skips = 1 if' in linux
    assert 'scripts/release-tests/docker_acceptance.py' in linux
    for name in ('Exercise compiled installer failure cases', 'Verify distinct baseline archive checksum', 'Exercise compiled Docker install, upgrade, MQTT and rollback'):
        step = linux.split(f'      - name: {name}\n', 1)[1].split('      - ', 1)[0]
        assert '\n        if:' not in step
        assert 'continue-on-error:' not in step


def test_scheduled_recovery_reads_fresh_master_without_replacing_controller():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    recovery = workflow.split('  recover:', 1)[1].split('  # A scheduled', 1)[0]
    assert 'ref: master' in recovery
    assert 'path: recovery-master' in recovery
    assert 'git -C recovery-master rev-parse HEAD' in recovery
    assert 'scripts/release_controller.py recover --repo recovery-master' in recovery
    assert 'persist-credentials: false' in recovery


def test_source_checks_have_one_pr_and_master_path_and_keep_native_windows():
    root = Path(__file__).resolve().parents[1]
    ci = (root / '.github/workflows/ci.yml').read_text()
    workflow = (root / '.github/workflows/sender-release.yml').read_text()
    triggers = ci.split('permissions:', 1)[0]
    assert '  push:' not in triggers
    assert '  pull_request:' in triggers
    assert '  workflow_call:' in triggers
    assert 'uses: ./.github/workflows/ci.yml' in workflow.split('  checks:', 1)[1].split('  linux-build:', 1)[0]
    assert 'native-install-smoke:' in ci and 'windows-sender:' in ci
    assert 'PWSH_EXE: powershell.exe' in ci and 'PWSH_EXE: pwsh' in ci
    assert ci.count('ref: ${{ inputs.source || github.sha }}') == 4


def test_missing_publisher_credential_gates_builds_but_not_checks():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    preflight = workflow.split('  preflight:', 1)[1].split('  checks:', 1)[0]
    assert 'environment: sender-release-publisher' in preflight
    assert 'scripts/release_controller.py preflight' in preflight
    assert '--require-publish' in preflight
    for name in ('linux-build', 'windows-build'):
        job = workflow.split(f'  {name}:', 1)[1].split('\n\n', 1)[0]
        assert 'needs: [version, checks, preflight]' in job
    checks = workflow.split('  checks:', 1)[1].split('  linux-build:', 1)[0]
    assert 'needs: version' in checks
    assert 'SENDER_RELEASE_TOKEN' not in checks


def test_manual_build_only_skips_publisher_environment_and_public_preparation():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/sender-release.yml').read_text()
    inputs = workflow.split('  workflow_dispatch:', 1)[1].split('  schedule:', 1)[0]
    assert 'build_only:' in inputs and 'type: boolean' in inputs and 'default: false' in inputs
    preflight = workflow.split('  preflight:', 1)[1].split('  checks:', 1)[0]
    prepare = workflow.split('  prepare:', 1)[1].split('  recover:', 1)[0]
    guard = "if: ${{ !(github.event_name == 'workflow_dispatch' && inputs.build_only) }}"
    assert guard in preflight and guard in prepare
    for name in ('linux-build', 'windows-build'):
        job = workflow.split(f'  {name}:', 1)[1].split('    uses:', 1)[0]
        expression = job.split('if: ${{ ', 1)[1].split(' }}', 1)[0]
        expression = expression.replace('!cancelled()', 'True').replace('&&', ' and ').replace('||', ' or ')
        for key, value in {'needs.version.result': 'version', 'needs.preflight.result': 'preflight', 'needs.checks.result': 'checks',
                           'github.event_name': 'event', 'inputs.build_only': 'build_only'}.items():
            expression = expression.replace(key, value)
        def allowed(event, build_only, preflight, version='success', checks='success'):
            return eval(expression, {'__builtins__': {}}, dict(event=event, build_only=build_only,
                                                             preflight=preflight, version=version, checks=checks))
        assert allowed('workflow_dispatch', True, 'skipped')
        for result in ('failure', 'skipped', 'cancelled'):
            assert not allowed('workflow_dispatch', True, 'skipped', checks=result)
            assert not allowed('push', False, 'success', checks=result)
        assert allowed('push', False, 'success')
        assert not allowed('push', False, 'failure')
        assert not allowed('push', True, 'skipped')
        assert not allowed('workflow_dispatch', True, 'failure')
        assert not allowed('workflow_dispatch', True, 'skipped', version='failure')
    version = workflow.split('  version:', 1)[1].split('  preflight:', 1)[0]
    assert "github.ref == 'refs/heads/master'" in version
    assert "inputs.source != ''" in version
    assert 'scripts/release_version.py --master "$MASTER" "$SOURCE"' in version
    assert 'needs: [version, checks, linux-build, windows-build]' in prepare


def test_windows_build_has_manual_and_reusable_triggers_without_obsolete_branch_pushes():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/protected-windows-package.yml').read_text()
    triggers = workflow.split('permissions:', 1)[0]
    assert '  push:' not in triggers
    assert '  workflow_dispatch:' in triggers and '  workflow_call:' in triggers


def test_flow_gate_uses_exact_source_parent_and_stops_on_failure():
    import os
    import subprocess
    import tempfile
    import textwrap
    root = Path(__file__).resolve().parents[1]
    ci = (root / '.github/workflows/ci.yml').read_text()
    block = ci.split('      - name: Validate selected source flows\n', 1)[1].split('      - ', 1)[0]
    script = textwrap.dedent(block.split('        run: |\n', 1)[1].split('\n\n', 1)[0])
    assert 'secrets.' not in ci and 'environment:' not in ci
    assert 'continue-on-error:' not in ci
    assert '${{' not in script
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        def git(*args):
            return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()
        git('init', '-q')
        git('config', 'user.email', 'gate@example.invalid')
        git('config', 'user.name', 'Gate Test')
        commits = []
        for i in range(3):
            (repo / 'input').write_text(str(i))
            git('add', '.')
            git('commit', '-qm', str(i))
            commits.append(git('rev-parse', 'HEAD'))
        git('checkout', '-q', commits[1])
        bin_dir = repo / 'bin'
        bin_dir.mkdir()
        fake = bin_dir / 'python3'
        fake.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\nif [ "$2" = "$FAIL_COMMAND" ]; then exit 1; fi\nexit 0\n')
        fake.chmod(0o755)
        log = repo / 'calls'
        env = dict(os.environ, PATH=str(bin_dir) + ':' + os.environ['PATH'], CALL_LOG=str(log),
                   FAIL_COMMAND='', SOURCE=commits[1], FLOW_BASE=commits[2], FLOW_DEFAULT_BRANCH='main',
                   GITHUB_EVENT_NAME='workflow_dispatch')
        def run(**values):
            log.unlink(missing_ok=True)
            return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', script], cwd=repo,
                                  env=dict(env, **values), capture_output=True, text=True)
        assert run().returncode == 0
        assert '--base ' + commits[0] in log.read_text()
        for event in ('pull_request', 'push'):
            assert run(SOURCE='', GITHUB_EVENT_NAME=event, FLOW_BASE=commits[2]).returncode == 0
            assert '--base ' + commits[2] in log.read_text()
        for command in ('explain', 'check'):
            assert run(FAIL_COMMAND=command).returncode != 0
        for source in ('main', '$(touch injected)', commits[2]):
            assert run(SOURCE=source).returncode != 0
            assert not log.exists()
        for base in ('', 'HEAD', '0' * 40, '$(touch injected)'):
            assert run(SOURCE='', GITHUB_EVENT_NAME='push', FLOW_BASE=base).returncode != 0
            assert not log.exists()
        assert not (repo / 'injected').exists()


def test_reusable_flow_failure_blocks_every_full_check_and_release_preparation():
    root = Path(__file__).resolve().parents[1]
    ci = (root / '.github/workflows/ci.yml').read_text()
    workflow = (root / '.github/workflows/sender-release.yml').read_text()
    for name in ('tests', 'native-install-smoke', 'windows-sender'):
        block = ci.split('  ' + name + ':', 1)[1].split('    steps:', 1)[0]
        assert 'needs: flow-docs' in block
        assert 'always()' not in block and 'continue-on-error:' not in block
    checks = workflow.split('  checks:', 1)[1].split('  linux-build:', 1)[0]
    assert 'source: ${{ needs.version.outputs.source }}' in checks
    assert 'secrets:' not in checks and 'preflight' not in checks
    prepare = workflow.split('  prepare:', 1)[1].split('    runs-on:', 1)[0]
    assert 'needs: [version, checks, linux-build, windows-build]' in prepare
    assert 'always()' not in prepare
    publisher = workflow.split('  publisher:', 1)[1]
    assert 'needs: prepare' in publisher
    # Source-bound publication requires prepare success; drains recover previously validated READY drafts.
    assert "needs.prepare.result == 'success'" in publisher
