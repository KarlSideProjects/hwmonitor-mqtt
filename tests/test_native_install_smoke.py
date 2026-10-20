import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace


REPO = Path(__file__).resolve().parents[1]
SMOKE = REPO / "scripts" / "native_install_smoke.py"
sys.path.insert(0, str(REPO))

from scripts.native_install_smoke import cleanup_smoke_install, cleanup_systemd_service


def test_native_smoke_requires_explicit_run_before_touching_systemd(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SMOKE)],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--run" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_failed_disable_preserves_installed_files_and_reports_cleanup_failure(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    unit = tmp_path / "fixture.service"
    unit.write_text("[Service]\n")
    wants = tmp_path / "multi-user.target.wants"
    wants.mkdir()
    link = wants / unit.name
    link.symlink_to(unit)

    def run(command, **kwargs):
        if command[1] == "disable":
            return SimpleNamespace(returncode=1, stdout="", stderr="fixture failure")
        if command[1] == "stop":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[1] == "show":
            state = "inactive\n" if command[2] == "--property=ActiveState" else "0\n"
            return SimpleNamespace(returncode=0, stdout=state, stderr="")
        raise AssertionError(f"unexpected systemctl operation: {command}")

    exit_code, error = cleanup_smoke_install("fixture.service", unit, link, tmp_path, run=run)

    assert exit_code == 1
    assert error is not None
    assert "disable" in error
    assert runtime.is_dir()
    assert unit.is_file()
    assert link.is_symlink()


def test_failed_stop_kills_then_verifies_before_removing_service_files(tmp_path):
    unit = tmp_path / "fixture.service"
    unit.write_text("[Service]\n")
    link = tmp_path / "multi-user.target.wants" / unit.name
    link.parent.mkdir()
    link.symlink_to(unit)
    calls = []
    stop_calls = 0

    def run(command, **kwargs):
        nonlocal stop_calls
        calls.append(command[1])
        if command[1] == "stop":
            stop_calls += 1
            return SimpleNamespace(returncode=1 if stop_calls == 1 else 0, stdout="", stderr="")
        if command[1] == "show":
            state = "inactive\n" if command[2] == "--property=ActiveState" else "0\n"
            return SimpleNamespace(returncode=0, stdout=state, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    error = cleanup_systemd_service("fixture.service", unit, link, run=run)

    assert error is None
    assert calls == ["disable", "stop", "kill", "stop", "show", "show", "daemon-reload"]
    assert not unit.exists()
    assert not link.exists()


def test_service_still_active_after_kill_preserves_runtime_and_unit(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    unit = tmp_path / "fixture.service"
    unit.write_text("[Service]\n")
    link = tmp_path / "multi-user.target.wants" / unit.name
    link.parent.mkdir()
    link.symlink_to(unit)

    def run(command, **kwargs):
        if command[1] == "show":
            state = "active\n" if command[2] == "--property=ActiveState" else "1234\n"
            return SimpleNamespace(returncode=0, stdout=state, stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="stop failed")

    exit_code, error = cleanup_smoke_install("fixture.service", unit, link, tmp_path, run=run)

    assert exit_code == 1
    assert error is not None
    assert "MainPID=1234" in error
    assert runtime.is_dir()
    assert unit.is_file()
    assert link.is_symlink()
