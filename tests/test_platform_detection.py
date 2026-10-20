import subprocess
import json
import pytest
from pathlib import Path

from hwmonitor_mqtt.platforms.detect import Target, detect_target
from hwmonitor_mqtt.platforms.dispatch import collector_for, install_command
from hwmonitor_mqtt.platforms import install
from hwmonitor_mqtt.platforms.installers import linux
from hwmonitor_mqtt.transport import mqtt as mqtt_transport


def test_pve_host_requires_installed_manager_and_successful_version(tmp_path):
    release = tmp_path / "os-release"
    release.write_text('ID=debian\nPRETTY_NAME="Debian GNU/Linux"\n')
    calls = []

    def run(command):
        calls.append(command)
        if command[:2] == ["dpkg-query", "-W"]:
            return subprocess.CompletedProcess(command, 0, "install ok installed\n", "")
        if command[0] == "pveversion":
            return subprocess.CompletedProcess(command, 0, "pve-manager/8.4-1\n", "")
        return subprocess.CompletedProcess(command, 1, "", "")

    target = detect_target(system="Linux", release_path=release, environ={}, run=run)

    assert (target.os, target.system, target.runtime) == ("linux", "pve", "native")
    assert "pve-manager installed" in target.evidence
    assert "pveversion succeeded" in target.evidence


def test_ordinary_linux_guest_does_not_become_pve_host(tmp_path):
    release = tmp_path / "os-release"
    release.write_text("ID=debian\n")

    def run(command):
        if command[0] == "systemd-detect-virt":
            return subprocess.CompletedProcess(command, 0, "kvm\n", "")
        return subprocess.CompletedProcess(command, 1, "", "not installed")

    target = detect_target(system="Linux", release_path=release, environ={}, run=run)

    assert target.system == "linux"
    assert target.runtime == "vm"


def test_nested_pve_guest_is_classified_from_pve_evidence(tmp_path):
    release = tmp_path / "os-release"
    release.write_text("ID=debian\n")

    def run(command):
        if command[0] == "systemd-detect-virt":
            return subprocess.CompletedProcess(command, 0, "kvm\n", "")
        if command[0] == "dpkg-query":
            return subprocess.CompletedProcess(command, 0, "install ok installed", "")
        return subprocess.CompletedProcess(command, 0, "pve-manager/8.4-1", "")

    target = detect_target(system="Linux", release_path=release, environ={}, run=run)

    assert target.system == "pve"
    assert target.runtime == "vm"


def test_docker_dry_run_uses_container_target_and_does_not_claim_host(tmp_path):
    release = tmp_path / "os-release"
    release.write_text("ID=alpine\n")
    target = detect_target(
        system="Linux", release_path=release,
        environ={}, exists=lambda path: path == "/.dockerenv", run=lambda command: None,
    )

    assert target.os == "linux"
    assert target.system == "unknown"
    assert target.runtime == "docker"


def test_containerd_marker_does_not_probe_host_pve_packages(tmp_path):
    release = tmp_path / "os-release"
    release.write_text("ID=debian\n")
    commands = []

    def run(command):
        commands.append(command)
        return None

    target = detect_target(system="Linux", release_path=release, environ={"container": "containerd"}, run=run)

    assert target.system == "unknown"
    assert target.runtime == "containerd"
    assert commands == []


def test_arbitrary_pveversion_output_is_not_pve_evidence(tmp_path):
    release = tmp_path / "os-release"
    release.write_text("ID=debian\n")

    def run(command):
        if command[0] == "dpkg-query":
            return subprocess.CompletedProcess(command, 0, "install ok installed", "")
        return subprocess.CompletedProcess(command, 0, "unexpected output", "")

    target = detect_target(system="Linux", release_path=release, environ={}, run=run)

    assert target.system == "unsupported"
    with pytest.raises(ValueError, match="unsupported or contradictory"):
        collector_for(target)


def test_linux_and_windows_select_existing_installers():
    linux = Target("linux", "linux", "native", "host", "debian", ())
    windows = Target("windows", "windows", "native", "host", None, ())

    assert install_command(linux, "/project") == [
        "docker", "compose", "-f", "/project/docker-compose.linux.yml", "--profile", "agent", "up", "-d",
    ]
    assert Path(install_command(windows, "/project")[-1]) == Path("/project/scripts/windows/install_windows_sender_task.ps1")


def test_linux_dry_run_selects_gpu_optional_service_without_writing(monkeypatch, capsys):
    target = Target("linux", "linux", "native", "fixture-host", "debian", ("os-release ID=debian",))
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr(linux, "preflight", lambda: None)
    monkeypatch.setattr(install.subprocess, "run", lambda *args, **kwargs: pytest.fail("dry-run executed installer"))

    assert install.main(["--dry-run", "--project-root", "/project"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["target"]["machine"] == "fixture-host"
    assert result["collector"] == "hwmonitor_mqtt.agents.agent_sender_async"
    assert result["ready"] is True
    assert "/project/docker-compose.linux.yml" in result["command"]
    compose = (Path(__file__).resolve().parents[1] / "docker-compose.linux.yml").read_text()
    assert "nvidia" not in compose.lower()
    assert "privileged" not in compose
    assert "/dev/dri" not in compose


def test_windows_dry_run_selects_windows_installer_and_collector(monkeypatch, capsys):
    target = Target("windows", "windows", "native", "fixture-windows", None, ("Windows sender platform",))
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr(install.subprocess, "run", lambda *args, **kwargs: pytest.fail("dry-run executed installer"))

    assert install.main(["--dry-run", "--project-root", "/project"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["target"]["machine"] == "fixture-windows"
    assert result["collector"] == "hwmonitor_mqtt.agents.agent_sender_windows"
    assert result["command"][-1].endswith("install_windows_sender_task.ps1")


def test_pve_dry_run_reports_pending_support_without_installing(monkeypatch, capsys):
    target = Target("linux", "pve", "native", "fixture-pve", "debian", ("pve-manager installed", "pveversion succeeded"))
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr(install.subprocess, "run", lambda *args, **kwargs: pytest.fail("PVE installer is pending"))

    assert install.main(["--dry-run"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["target"]["system"] == "pve"
    assert result["collector"] == "hwmonitor_mqtt.agents.agent_sender_async"
    assert result["status"] == "PVE detected; PVE installation support is pending"
    assert "command" not in result


def test_unsupported_linux_distribution_dry_run_stops_before_writes(monkeypatch, capsys):
    target = Target("linux", "linux", "native", "fixture-host", "arch", ())
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr(install.subprocess, "run", lambda *args, **kwargs: pytest.fail("unsupported target executed installer"))

    assert install.main(["--dry-run"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["target"]["distribution"] == "arch"
    assert result["status"] == "Unsupported Linux distribution: arch"
    assert "command" not in result


def test_linux_dry_run_reports_missing_docker_without_writing(monkeypatch, capsys):
    target = Target("linux", "linux", "native", "fixture-host", "ubuntu", ())
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr(linux, "preflight", lambda: "Docker is not installed")
    monkeypatch.setattr(install.subprocess, "run", lambda *args, **kwargs: pytest.fail("dry-run executed installer"))

    assert install.main(["--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ready"] is False
    assert result["status"] == "Docker is not installed"


def test_shared_mqtt_transport_supports_anonymous_publish(monkeypatch):
    class Info:
        rc = 0

    class Client:
        def __init__(self, *args, **kwargs):
            self.published = None

        def publish(self, *args, **kwargs):
            self.published = (args, kwargs)
            return Info()

        def username_pw_set(self, *args, **kwargs):
            pytest.fail("anonymous MQTT configuration must not set credentials")

    monkeypatch.setattr(mqtt_transport.mqtt, "Client", Client)
    monkeypatch.delenv("MQTT_USER", raising=False)
    monkeypatch.delenv("MQTT_PASS", raising=False)
    config = mqtt_transport.configuration("fixture-host")
    client, stats, _, _ = mqtt_transport.create_client(config)

    mqtt_transport.publish(client, stats, config["topic"], {"cpu": {"percent_total": 12}})

    assert client.published[0][0] == config["topic"]
    assert stats["publish_ok"] == 1
    assert stats["publish_err"] == 0
