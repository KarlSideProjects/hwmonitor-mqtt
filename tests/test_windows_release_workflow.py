import subprocess
from pathlib import Path

from _pwsh import run_pwsh


def test_reusable_windows_source_and_version_are_bound_to_first_parent(tmp_path):
    repo = tmp_path / 'repo'
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Test'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.invalid'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '--allow-empty', '-qm', 'first'], check=True)
    first = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    subprocess.run(['git', '-C', str(repo), 'commit', '--allow-empty', '-qm', 'second'], check=True)
    master = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    subprocess.run(['git', '-C', str(repo), 'update-ref', 'refs/remotes/origin/master', master], check=True)
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/protected-windows-package.yml').read_text()
    block = workflow.split('      - name: Validate source and version for reusable builds\n', 1)[1].split('      - uses:', 1)[0].split('        run: |\n', 1)[1]
    script = "Set-Location $env:TEST_REPO\n" + '\n'.join(line[10:] for line in block.splitlines())
    env = {'TEST_REPO': str(repo), 'BUILD_SOURCE': master, 'BUILD_VERSION': '0.1.2', 'REUSABLE_BUILD': 'true'}
    assert run_pwsh(script, env).returncode == 0
    assert run_pwsh(script, {**env, 'BUILD_VERSION': '0.1.1'}).returncode != 0
    assert run_pwsh(script, {**env, 'BUILD_SOURCE': first, 'BUILD_VERSION': '0.1.1'}).returncode != 0
    subprocess.run(['git', '-C', str(repo), 'checkout', '--quiet', '--detach', first], check=True)
    assert run_pwsh(script, {**env, 'BUILD_SOURCE': first, 'BUILD_VERSION': '0.1.1'}).returncode == 0
    subprocess.run(['git', '-C', str(repo), 'commit', '--allow-empty', '-qm', 'side'], check=True)
    side = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    assert run_pwsh(script, {**env, 'BUILD_SOURCE': side, 'BUILD_VERSION': '0.1.2'}).returncode != 0


def test_upgrade_baseline_is_built_from_pinned_source_and_shared_digest():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/protected-windows-package.yml').read_text()
    assert 'actions/download-artifact@' not in workflow
    assert "git archive --output old-source.tar $oldSource" in workflow
    assert "46bcc40d232cc2720b188c795d87e33d0894ea73" in workflow
    assert "uv sync --locked" in workflow
    assert "OLD_SENDER_SHA256=$oldHash" in workflow
    assert workflow.count('-OldSha256 $env:OLD_SENDER_SHA256') == 2
