import os
from pathlib import Path
import subprocess


BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap.sh"


def test_unsupported_architecture_is_rejected_before_download_or_install(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {"uname": "#!/bin/sh\necho armv7l\n", "id": "#!/bin/sh\necho 0\n", "curl": f"#!/bin/sh\ntouch '{tmp_path / 'downloaded'}'\nexit 1\n"}.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    result = subprocess.run(["bash", str(BOOTSTRAP)], env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HWMONITOR_BOOTSTRAP_ROOT": str(tmp_path / "root")}, text=True, capture_output=True)
    assert result.returncode != 0
    assert "architecture" in result.stderr.lower()
    assert not (tmp_path / "downloaded").exists()
    assert not (tmp_path / "root").exists()


def test_download_failure_never_uses_private_source_and_preserves_old_install(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "requests"
    for name, body in {"uname": "#!/bin/sh\necho x86_64\n", "id": "#!/bin/sh\necho 0\n", "curl": f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{calls}'\nexit 1\n"}.items():
        path = bin_dir / name; path.write_text(body); path.chmod(0o755)
    root = tmp_path / "root"
    root.mkdir()
    for path, value in {".hwmonitor-mqtt-bootstrap": "", "sender": "old executable", ".env": "private"}.items():
        (root / path).write_text(value)
    result = subprocess.run(["bash", str(BOOTSTRAP)], env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HWMONITOR_BOOTSTRAP_ROOT": str(root)}, text=True, capture_output=True)
    assert result.returncode != 0
    assert calls.exists(), "public release API was not contacted"
    assert "hwmonitor-mqtt-releases" in calls.read_text()
    assert "hwmonitor-mqtt/archive" not in calls.read_text()
    assert (root / "sender").read_text() == "old executable"
    assert (root / ".env").read_text() == "private"


def test_unrecognized_directory_is_not_modified_or_downloaded(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    for name, body in {"uname": "#!/bin/sh\necho x86_64\n", "id": "#!/bin/sh\necho 0\n", "curl": f"#!/bin/sh\ntouch '{tmp_path / 'downloaded'}'\nexit 1\n"}.items():
        tool = bin_dir / name; tool.write_text(body); tool.chmod(0o755)
    root = tmp_path / "root"; root.mkdir()
    (root / "keep").write_text("user data")
    result = subprocess.run(["bash", str(BOOTSTRAP)], env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HWMONITOR_BOOTSTRAP_ROOT": str(root)}, text=True, capture_output=True)
    assert result.returncode != 0
    assert (root / "keep").read_text() == "user data"
    assert not (tmp_path / "downloaded").exists()


def test_rejects_symlinked_marker_without_contacting_release(tmp_path):
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    for name, body in {"uname": "#!/bin/sh\necho x86_64\n", "id": "#!/bin/sh\necho 0\n", "curl": f"#!/bin/sh\ntouch '{tmp_path / 'downloaded'}'\nexit 1\n"}.items():
        tool = bin_dir / name; tool.write_text(body); tool.chmod(0o755)
    root = tmp_path / "root"; root.mkdir()
    target = tmp_path / "target"; target.write_text("marker")
    (root / ".hwmonitor-mqtt-bootstrap").symlink_to(target)
    result = subprocess.run(["bash", str(BOOTSTRAP)], env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HWMONITOR_BOOTSTRAP_ROOT": str(root)}, text=True, capture_output=True)
    assert result.returncode != 0
    assert target.read_text() == "marker"
    assert not (tmp_path / "downloaded").exists()


def test_arm64_selects_its_named_release_asset(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "requests"
    for name, body in {"uname": "#!/bin/sh\necho aarch64\n", "id": "#!/bin/sh\necho 0\n", "curl": f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{calls}'\nexit 1\n"}.items():
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    result = subprocess.run(["bash", str(BOOTSTRAP)], env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "HWMONITOR_BOOTSTRAP_ROOT": str(tmp_path / "root")}, text=True, capture_output=True)
    assert result.returncode != 0
    assert calls.exists()
    assert "architecture" not in result.stderr.lower()


def test_pve_bootstrap_interrupt_waits_for_in_process_recovery(tmp_path):
    import os
    import signal
    import subprocess
    import time
    import pytest

    if os.name != 'posix':
        pytest.skip('POSIX sessions required')
    root = tmp_path / 'root'
    root.mkdir()
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    sender = root / 'sender'
    sender.write_text('''#!/usr/bin/env python3
import os, subprocess, sys, time
from pathlib import Path
from hwmonitor_mqtt.platforms import install
from hwmonitor_mqtt.platforms.detect import Target
from hwmonitor_mqtt.platforms.installers import pve
root = Path(os.environ['TEST_ROOT'])
(root / 'payload-pid').write_text(str(os.getpgrp()))
pve.APP_ROOT = str(root / 'app')
pve.SYSTEMD_DIR = str(root / 'units')
pve.__compiled__ = True
pve.readiness = lambda *args: None
install.detect_target = lambda: Target('linux', 'pve', 'native', 'fixture', 'debian', ())
def run(args, **kwargs):
    if 'restart' in args:
        (root / 'ready').touch()
        time.sleep(30)
    if 'daemon-reload' in args and (root / 'ready').exists():
        (root / 'recovering').touch()
        time.sleep(6)
    return subprocess.CompletedProcess(args, 0)
pve.main = lambda args: pve.install_compiled(root, root / 'config', 'systemctl', run)
with install.cancellation_channel():
    raise SystemExit(install.main(sys.argv[2:]))
''')
    sender.chmod(0o755)
    script = BOOTSTRAP.read_text()
    start = script.index('if [[ $target == pve ]]; then\n    command -v setsid')
    end = script.index('\nif [[ $target == linux ]]; then\n    previous_state=', start)
    body = script[start:end]
    wrapper = tmp_path / 'wrapper.sh'
    wrapper.write_text('set -euo pipefail\nroot="$TEST_ROOT"\nscratch="$TEST_SCRATCH"\ntarget=pve\npve_config="$root/config"\nexec 9>"$root/lock"\nflock 9\n' + body + '\n')
    env = {**os.environ, 'TEST_ROOT': str(root), 'TEST_SCRATCH': str(scratch), 'PYTHONPATH': os.pathsep.join(filter(None, [str(BOOTSTRAP.parent), os.environ.get('PYTHONPATH')]))}
    process = subprocess.Popen(['bash', str(wrapper)], env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 15
        while not (root / 'ready').exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert (root / 'ready').exists()
        os.killpg(process.pid, signal.SIGINT)
        deadline = time.monotonic() + 5
        while not (root / 'recovering').exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert (root / 'recovering').exists()
        os.killpg(process.pid, signal.SIGINT)
        assert process.poll() is None
        assert subprocess.run(['flock', '-n', str(root / 'lock'), 'true']).returncode != 0
        assert process.wait(timeout=15) != 0
        assert not (root / 'app' / 'sender').exists()
        assert not (root / 'units' / pve_service_name()).exists()
    finally:
        if process.poll() is None:
            pid_file = root / 'payload-pid'
            if pid_file.exists():
                try:
                    os.killpg(int(pid_file.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def pve_service_name():
    from hwmonitor_mqtt.platforms.installers.pve import SERVICE_NAME
    return SERVICE_NAME


def test_pve_late_interrupt_keeps_successful_native_and_root_inputs(tmp_path):
    import signal
    import time

    root = tmp_path / 'root'; root.mkdir()
    scratch = tmp_path / 'scratch'; scratch.mkdir()
    sender = root / 'sender'
    sender.write_text('#!/bin/sh\necho new > "$TEST_ROOT/native"\ntouch "$TEST_ROOT/committed"\nsleep 1\nexit 0\n')
    sender.chmod(0o755)
    (root / 'input').write_text('new')
    script = BOOTSTRAP.read_text()
    start = script.index('if [[ $target == pve ]]; then\n    command -v setsid')
    end = script.index('\nif [[ $target == linux ]]; then\n    previous_state=', start)
    wrapper = tmp_path / 'wrapper.sh'
    wrapper.write_text('set -euo pipefail\nroot="$TEST_ROOT"\nscratch="$TEST_SCRATCH"\ntarget=pve\npve_config="$root/config"\ntrap \'s=$?; if [[ $s != 0 ]]; then echo old > "$root/input"; fi\' EXIT\n' + script[start:end] + '\n')
    process = subprocess.Popen(['bash', str(wrapper)], env={**os.environ, 'TEST_ROOT': str(root), 'TEST_SCRATCH': str(scratch)}, start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while not (root / 'committed').exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert (root / 'committed').exists()
        os.killpg(process.pid, signal.SIGINT)
        assert process.wait(timeout=5) == 0
        assert (root / 'input').read_text() == 'new'
        assert (root / 'native').read_text().strip() == 'new'
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def test_pve_marker_failure_keeps_committed_native_and_root_inputs(tmp_path):
    root = tmp_path / 'root'; root.mkdir()
    scratch = tmp_path / 'scratch'; scratch.mkdir()
    backup = tmp_path / 'backup'; backup.mkdir()
    (backup / 'input').write_text('old')
    (root / 'input').write_text('new')
    (root / '.hwmonitor-mqtt-bootstrap').write_text('marker')
    sender = root / 'sender'
    sender.write_text('#!/bin/sh\necho new > "$TEST_ROOT/native"\n')
    sender.chmod(0o755)
    script = BOOTSTRAP.read_text()
    finish_start = script.index('rollback() {')
    finish_end = script.index('\nif [[ $target == linux && -n $old_image ]]', finish_start)
    install_start = script.index('if [[ $target == pve ]]; then\n    command -v setsid')
    install_end = script.index('\necho "Sender $version installed."', install_start)
    wrapper = tmp_path / 'wrapper.sh'
    wrapper.write_text('''set -euo pipefail
root="$TEST_ROOT"
scratch="$TEST_SCRATCH"
backup="$TEST_BACKUP"
target=pve
pve_config="$root/config"
files=(input)
pin_owned=0
old_pin=
touch() { echo 'marker update failed' >&2; return 1; }
''' + script[finish_start:finish_end] + '\n' + script[install_start:install_end] + '\necho "Sender installed."\n')
    result = subprocess.run(['bash', str(wrapper)], env={**os.environ, 'TEST_ROOT': str(root), 'TEST_SCRATCH': str(scratch), 'TEST_BACKUP': str(backup)}, text=True, capture_output=True)
    assert result.returncode != 0
    assert 'marker update failed' in result.stderr
    assert 'Sender installed.' not in result.stdout
    assert (root / 'input').read_text() == 'new'
    assert (root / 'native').read_text().strip() == 'new'
