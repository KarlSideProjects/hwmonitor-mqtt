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


def test_offline_selected_wheels_use_locked_export_without_remote_resolution():
    workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/protected-windows-package.yml').read_text()
    start = workflow.index('      - name: Install locked application and pinned compiler dependencies')
    end = workflow.index('      - name: Pin the build-only Dependency Walker archive', start)
    install = workflow[start:end]
    assert 'uv export --locked --no-dev --no-emit-project' in install
    assert 'uv venv --python (Get-Command python).Source' in install
    assert '--offline --no-index --find-links selected-packages --no-cache --no-deps --require-hashes -r selected-requirements.txt' in install
    assert 'uv sync' not in install


def test_offline_install_requires_the_selected_hashlocked_wheel(tmp_path):
    import hashlib
    import subprocess
    import sys
    import zipfile

    wheels = tmp_path / 'wheels'
    wheels.mkdir()
    wheel = wheels / 'selected_demo-1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as package:
        package.writestr('selected_demo.py', 'value = 1\n')
        package.writestr('selected_demo-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\nName: selected-demo\nVersion: 1.0\n')
        package.writestr('selected_demo-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        package.writestr('selected_demo-1.0.dist-info/RECORD', 'selected_demo.py,,\nselected_demo-1.0.dist-info/METADATA,,\nselected_demo-1.0.dist-info/WHEEL,,\nselected_demo-1.0.dist-info/RECORD,,\n')
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text('selected-demo==1.0 --hash=sha256:' + hashlib.sha256(wheel.read_bytes()).hexdigest() + '\n')
    for label in ('present', 'missing'):
        envdir = tmp_path / label
        if label == 'missing':
            wheel.unlink()
        create = subprocess.run(['uv', 'venv', '--offline', '--python', sys.executable, str(envdir)], capture_output=True, text=True)
        assert create.returncode == 0, create.stderr
        python = envdir / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        install = subprocess.run(['uv', 'pip', 'install', '--python', str(python), '--offline', '--no-index', '--find-links', str(wheels), '--no-cache', '--no-deps', '--require-hashes', '-r', str(requirements)], capture_output=True, text=True)
        if label == 'present':
            assert install.returncode == 0, install.stderr
            assert subprocess.run([str(python), '-c', 'import selected_demo; assert selected_demo.value == 1']).returncode == 0
        else:
            assert install.returncode != 0
