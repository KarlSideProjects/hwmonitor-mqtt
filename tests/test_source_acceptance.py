"""Cheap contracts for the real-VM source acceptance entry point."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'scripts/release-tests'
sys.path.insert(0, str(HERE))
import source_acceptance as acceptance
import source_fixture as fixture
sys.path.pop(0)


def test_output_refuses_existing_or_untrusted_directory(tmp_path):
    existing = tmp_path / 'existing'
    existing.mkdir()
    (existing / 'keep').write_text('untouched')
    with pytest.raises(FileExistsError):
        acceptance.output_directory(existing)
    assert (existing / 'keep').read_text() == 'untouched'
    link = tmp_path / 'link'
    link.symlink_to(existing, target_is_directory=True)
    with pytest.raises(ValueError, match='trusted'):
        acceptance.output_directory(link / 'out')
    existing.chmod(0o777)
    with pytest.raises(ValueError, match='trusted'):
        acceptance.output_directory(existing / 'out')


def test_exact_committed_source_is_archived_without_worktree_changes(tmp_path):
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    acceptance.source_inputs(source, tmp_path)
    expected = subprocess.check_output(['git', 'show', source + ':bootstrap-source.sh'], cwd=ROOT)
    assert (tmp_path / 'bootstrap-source.sh').read_bytes() == expected
    assert (tmp_path / 'source').read_text().strip() == source
    assert (tmp_path / 'source.tar.gz').stat().st_size > 0
    with pytest.raises(ValueError, match='exact committed'):
        acceptance.source_inputs('master', tmp_path)


@pytest.mark.parametrize('flag', ['-o', '--output'])
def test_download_fixture_binds_candidate_requests_and_original_one_line_command(tmp_path, flag):
    source = 'a' * 40
    (tmp_path / 'source').write_text(source)
    (tmp_path / 'bootstrap-source.sh').write_bytes(b'bootstrap')
    dest = tmp_path / 'download'
    url = f'https://raw.githubusercontent.com/{fixture.REPO}/master/bootstrap-source.sh'
    assert fixture.download(['-fsSL', url, flag, str(dest)], tmp_path) == 0
    assert dest.read_bytes() == b'bootstrap'
    api = f'https://api.github.com/repos/{fixture.REPO}/commits/master'
    fixture.download([api, '--output', str(dest)], tmp_path)
    assert json.loads(dest.read_text())['sha'] == source
    with pytest.raises(ValueError, match='unexpected candidate'):
        fixture.download([f'https://github.com/{fixture.REPO}/archive/wrong.tar.gz'], tmp_path)
    (tmp_path / 'README.md').write_text((ROOT / 'README.md').read_text())
    command = fixture.bootstrap_command(tmp_path)
    assert command[:2] == ['bash', '-c']
    assert 'mktemp' in command[2] and 'bash "$f"' in command[2]
    assert '| bash' not in command[2]
    subprocess.run(['bash', '-n', '-c', command[2]], check=True)


@pytest.mark.skipif(os.name == 'nt', reason='guest prompt fixture requires POSIX PTY')
def test_prompt_fixture_handles_real_tty_getpass_and_discards_secrets(capfd):
    command = [sys.executable, '-c', "import getpass; assert input('MQTT broker host: ') == '127.0.0.1'; assert input('MQTT broker port [1883]: ') == '1883'; assert input('MQTT username: ') == 'acceptance'; assert getpass.getpass('MQTT password: ') == 'private-secret'"]
    fixture.prompt(command, 'private-secret', timeout=5)
    assert 'private-secret' not in ''.join(capfd.readouterr())
    with pytest.raises(RuntimeError, match='failed'):
        fixture.prompt([sys.executable, '-c', 'raise SystemExit(2)'], 'secret', first=False, timeout=5)
    with pytest.raises(TimeoutError):
        fixture.prompt([sys.executable, '-c', 'import time; time.sleep(10)'], 'secret', timeout=0.1)


def test_guest_observes_real_sender_topic_and_keeps_configuration_across_both_runs():
    guest = (HERE / 'source_guest.sh').read_text()
    assert "-t 'sys/agents/+/metrics'" in guest
    assert 'hwmonitor/#' not in guest
    assert 'python3 source_fixture.py first' in guest
    assert 'python3 source_fixture.py reinstall' in guest
    assert 'sha256sum -c preserved' in guest
    assert 'observe first.json' in guest and 'observe reinstalled.json' in guest
    assert 'pveversion' in guest and 'uname -r | grep -q pve' in guest
    subprocess.run(['bash', '-n', str(HERE / 'source_guest.sh')], check=True)


def test_failure_keeps_small_receipt_and_removes_private_resources(tmp_path, monkeypatch):
    image = tmp_path / 'image'
    image.write_bytes(b'wrong')
    monkeypatch.setattr(acceptance.signal, 'signal', lambda *args: None)
    out = tmp_path / 'out'
    with pytest.raises(ValueError, match='checksum'):
        acceptance.main(['--source', 'a' * 40, '--platform', 'linux-x64', '--cloud-image', str(image),
                         '--cloud-image-sha512', '0' * 128, '--output', str(out)])
    assert {p.name for p in out.iterdir()} == {'receipt.json', 'run.log'}
    receipt = json.loads((out / 'receipt.json').read_text())
    assert receipt['status'] == 'failed'
    assert receipt['private_resources_retained'] is False
    import hashlib
    for filename, digest in receipt['harness_sha256'].items():
        assert digest == hashlib.sha256((HERE / filename).read_bytes()).hexdigest()


def test_receipt_log_is_bounded_and_sanitized(tmp_path):
    raw, safe = tmp_path / 'raw', tmp_path / 'safe'
    raw.write_text('x' * (2 * 1024 * 1024) + '\nMQTT_PASS=arbitrary-password\n' + 'a' * 48)
    acceptance.retain_log(raw, safe)
    assert safe.stat().st_size <= 1024 * 1024 + 100
    assert 'arbitrary-password' not in safe.read_text()
    assert 'a' * 48 not in safe.read_text()


def test_windows_ci_executes_source_tests_in_both_shell_versions():
    ci = (ROOT / '.github/workflows/ci.yml').read_text()
    for shell in ('powershell.exe', 'pwsh'):
        block = ci.split('PWSH_EXE: ' + shell, 1)[1].split('      - name:', 1)[0]
        assert 'tests/test_windows_source_*.py' in block
        assert 'tests/test_windows_compiled_start.py' in block
        assert 'tests/test_powershell_syntax.py tests/test_remote_bootstrap.py -k powershell' in block
        assert '$LASTEXITCODE' in block
    assert 'name: Source bootstrap integration' in ci
    assert 'pytest -q tests/test_source_bootstrap.py tests/test_source_acceptance.py' in ci


def test_heavy_workflow_uses_hosted_full_vm_and_only_small_always_artifacts():
    workflow = (ROOT / '.github/workflows/source-acceptance.yml').read_text()
    assert 'workflow_dispatch:' in workflow and 'schedule:' in workflow
    assert 'pull_request:' not in workflow and 'self-hosted' not in workflow
    assert 'contents: read' in workflow and 'persist-credentials: false' in workflow
    assert '[linux-x64, linux-arm64, pve]' in workflow
    job_env = workflow.split('    env:\n', 1)[1].split('    steps:', 1)[0]
    assert '${{ runner.' not in job_env
    assert 'ACCEPTANCE_OUTPUT: ${{ github.workspace }}/source-acceptance-output' in job_env
    assert '${{ env.ACCEPTANCE_OUTPUT }}/receipt.json' in workflow
    assert '${{ env.ACCEPTANCE_OUTPUT }}/run.log' in workflow
    assert 'if: always()' in workflow
    assert '/receipt.json' in workflow and '/run.log' in workflow
    import re
    for action in re.findall(r'uses: ([^\s]+)', workflow):
        assert re.search(r'@[a-f0-9]{40}$', action), action
    command = acceptance.qemu_command('linux-x64', 'disk', 'seed', 2222)
    assert command[0] == 'qemu-system-x86_64'
    assert 'hostfwd=tcp:127.0.0.1:2222-:22' in ' '.join(command)


def test_cleanup_error_still_produces_failed_receipt(tmp_path, monkeypatch):
    image = tmp_path / 'image'
    image.write_bytes(b'wrong')
    monkeypatch.setattr(acceptance.signal, 'signal', lambda *args: None)
    real_cleanup = acceptance.cleanup
    def cleanup(actions):
        errors = real_cleanup(actions)
        if actions[0][0] == 'VM':
            errors.append('VM: simulated termination error')
        return errors
    monkeypatch.setattr(acceptance, 'cleanup', cleanup)
    out = tmp_path / 'out'
    with pytest.raises(RuntimeError, match='cleanup failed'):
        acceptance.main(['--source', 'a' * 40, '--platform', 'pve', '--cloud-image', str(image),
                         '--cloud-image-sha512', '0' * 128, '--output', str(out)])
    receipt = json.loads((out / 'receipt.json').read_text())
    assert receipt['status'] == 'failed'
    assert 'VM: simulated termination error' in receipt['cleanup_errors']
    assert {p.name for p in out.iterdir()} == {'receipt.json', 'run.log'}


def test_cloud_image_manifest_pins_each_platform_to_official_dated_images():
    import re
    images = json.loads((HERE / 'cloud-images.json').read_text())
    expected = {'linux-x64': 'genericcloud-amd64', 'linux-arm64': 'genericcloud-arm64',
                'pve': 'generic-amd64'}
    assert set(images) == set(expected)
    for platform, flavor in expected.items():
        image = images[platform]
        assert image['url'] == ('https://cloud.debian.org/images/cloud/trixie/20261103-2651/'
                                f'debian-13-{flavor}-20261103-2651.qcow2')
        assert re.fullmatch('[a-f0-9]{128}', image['sha512'])


@pytest.mark.parametrize('bad_value', [None, 'https://example.invalid/image.qcow2',
                                      'http://cloud.debian.org/image.qcow2',
                                      'https://cloud.debian.org/image.qcow2\nINJECTED=true',
                                      'https://cloud.debian.org/image.qcow2\rINJECTED=true'])
def test_cloud_image_workflow_rejects_untrusted_urls_before_writing_env(tmp_path, bad_value):
    import textwrap
    workflow = (ROOT / '.github/workflows/source-acceptance.yml').read_text()
    assert 'vars.SOURCE_ACCEPTANCE_' not in workflow
    assert 'scripts/release-tests/cloud-images.json' in workflow
    script = textwrap.dedent(workflow.split("          python3 - <<'PY'\n", 1)[1].split('          PY\n', 1)[0])
    manifest = tmp_path / 'scripts/release-tests/cloud-images.json'
    manifest.parent.mkdir(parents=True)
    images = json.loads((HERE / 'cloud-images.json').read_text())
    if bad_value is not None:
        images['linux-x64']['url'] = bad_value
    manifest.write_text(json.dumps(images))
    env_file = tmp_path / 'env'
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path, capture_output=True,
                            env={**os.environ, 'PLATFORM': 'linux-x64', 'GITHUB_ENV': str(env_file)})
    if bad_value is None:
        assert result.returncode == 0, result.stderr
        assert env_file.read_text() == (f"CLOUD_IMAGE_URL={images['linux-x64']['url']}\n"
                                        f"CLOUD_IMAGE_SHA512={images['linux-x64']['sha512']}\n")
    else:
        assert result.returncode != 0
        assert not env_file.exists()
