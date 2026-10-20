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


def test_docker_target_system_is_explicit_and_selects_pve_compose(monkeypatch, capsys):
    target = Target("linux", "unknown", "docker", "fixture-container", "debian", ("container runtime marker=docker",))
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr(install.subprocess, "run", lambda *args, **kwargs: pytest.fail("dry-run executed installer"))
    monkeypatch.setattr("hwmonitor_mqtt.platforms.installers.pve.readiness", lambda config: None)
    monkeypatch.setattr(linux, "preflight", lambda: None)

    assert install.main(["--dry-run", "--target-system", "pve", "--pve-config", "/etc/hwmonitor-mqtt/pve.env"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["target"]["system"] == "pve"
    assert result["target"]["runtime"] == "docker"
    assert result["deployment"] == "docker"
    assert result["ready"] is True
    assert result["command"] == ["docker", "compose", "-f", str(Path(__file__).resolve().parents[1] / "docker-compose.pve.yml"), "--profile", "pve", "up", "--build", "-d"]


def test_docker_pve_target_requires_valid_api_configuration(monkeypatch, capsys):
    target = Target("linux", "unknown", "docker", "fixture-container", "debian", ())
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr("hwmonitor_mqtt.platforms.installers.pve.readiness", lambda config: "PVE validation failed: invalid_config")
    monkeypatch.setattr(linux, "preflight", lambda: None)

    assert install.main(["--dry-run", "--target-system", "pve", "--pve-config", "/etc/hwmonitor-mqtt/pve.env"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ready"] is False
    assert result["status"] == "PVE validation failed: invalid_config"


def test_native_pve_target_cannot_be_overridden_by_container_setting(monkeypatch, capsys):
    target = Target("linux", "pve", "native", "fixture-pve", "debian", ())
    monkeypatch.setattr(install, "detect_target", lambda: target)

    assert install.main(["--dry-run", "--target-system", "linux"]) == 2
    assert "contradicts detected host" in capsys.readouterr().out


def test_detected_pve_host_can_select_docker_deployment(monkeypatch, capsys):
    target = Target("linux", "pve", "native", "fixture-pve-host", "debian", ("pve-manager installed", "pveversion succeeded"))
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr("hwmonitor_mqtt.platforms.installers.pve.readiness", lambda config: None)
    monkeypatch.setattr(linux, "preflight", lambda: None)

    assert install.main(["--dry-run", "--deployment", "docker", "--pve-config", "/etc/hwmonitor-mqtt/pve.env"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["target"]["system"] == "pve"
    assert result["target"]["runtime"] == "native"
    assert result["deployment"] == "docker"
    assert result["command"][-7:] == ["-f", str(Path(__file__).resolve().parents[1] / "docker-compose.pve.yml"), "--profile", "pve", "up", "--build", "-d"]


def test_docker_compose_profiles_keep_stable_host_identity_and_private_mounts():
    base = Path(__file__).resolve().parents[1]
    linux = (base / "docker-compose.linux.yml").read_text()
    pve = (base / "docker-compose.pve.yml").read_text()
    assert "hostname: ${SENDER_HOSTNAME:?" in linux and "hostname: ${SENDER_HOSTNAME:?" in pve
    assert "source: ${PVE_CONFIG_HOST:?set by host-aware installer}" in pve
    assert "source: ${PVE_CA_HOST:?set by host-aware installer}" in pve
    assert "target: ${PVE_CA_TARGET:?set by host-aware installer}" in pve
    assert "PVE_ENABLED: \"1\"" in pve and "PVE_CONFIG_FILE: /run/secrets/pve.env" in pve
    assert "privileged" not in pve and "MQTT_PASS" in pve


def test_live_pve_docker_install_passes_private_paths_without_printing_them(monkeypatch, capsys, tmp_path):
    target = Target("linux", "pve", "native", "fixture-pve-host", "debian", ())
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr("hwmonitor_mqtt.platforms.installers.pve.readiness", lambda config: None)
    monkeypatch.setattr(linux, "preflight", lambda: None)
    monkeypatch.setattr("hwmonitor_mqtt.clients.pve.PveConfig.from_environment", lambda **kwargs: type("Config", (), {"ca_file": "/private/ca.pem"})())
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "UnitFileState=\nActiveState=inactive\nLoadState=not-found\n" if command[0].endswith("systemctl") else "", "")

    monkeypatch.setattr(install.subprocess, "run", run)
    config = tmp_path / "pve.env"
    config.write_text("private placeholder\n")
    assert install.main(["--deployment", "docker", "--pve-config", str(config)]) == 0
    output = capsys.readouterr().out
    assert "private placeholder" not in output
    compose_call, compose_kwargs = calls[-1]
    assert compose_call[-7:] == ["-f", str(Path(__file__).resolve().parents[1] / "docker-compose.pve.yml"), "--profile", "pve", "up", "--build", "-d"]
    assert compose_kwargs["env"]["SENDER_HOSTNAME"] == "fixture-pve-host"
    assert compose_kwargs["env"]["PVE_CONFIG_HOST"] == str(config.resolve())
    assert compose_kwargs["env"]["PVE_CA_HOST"] == "/private/ca.pem"
    assert compose_kwargs["env"]["PVE_CA_TARGET"] == "/private/ca.pem"


def test_ordinary_linux_compose_install_receives_host_identity(monkeypatch):
    target = Target("linux", "linux", "native", "fixture-linux-host", "debian", ())
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr(linux, "preflight", lambda: None)
    calls = []
    monkeypatch.setattr(install.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)) or subprocess.CompletedProcess(command, 0))

    assert install.main(["--project-root", "/project"]) == 0
    assert calls[0][1]["env"]["SENDER_HOSTNAME"] == "fixture-linux-host"


def test_pve_docker_install_refuses_an_active_native_sender(monkeypatch, capsys, tmp_path):
    target = Target("linux", "pve", "native", "fixture-pve-host", "debian", ())
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr("hwmonitor_mqtt.platforms.installers.pve.readiness", lambda config: None)
    monkeypatch.setattr(linux, "preflight", lambda: None)
    monkeypatch.setattr("hwmonitor_mqtt.clients.pve.PveConfig.from_environment", lambda **kwargs: type("Config", (), {"ca_file": "/private/ca.pem"})())
    config = tmp_path / "pve.env"
    config.write_text("fixture\n")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "UnitFileState=enabled\nActiveState=active\nLoadState=loaded\n" if command[0].endswith("systemctl") else "", "")

    monkeypatch.setattr(install.subprocess, "run", run)
    assert install.main(["--pve-config", str(config), "--deployment", "docker"]) == 2
    assert "disable it before selecting Docker" in capsys.readouterr().out
    assert not any(command[0] == "docker" for command in calls)


def test_pve_docker_install_fails_closed_when_systemd_state_cannot_be_inspected(monkeypatch, capsys, tmp_path):
    target = Target("linux", "pve", "native", "fixture-pve-host", "debian", ())
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr("hwmonitor_mqtt.platforms.installers.pve.readiness", lambda config: None)
    monkeypatch.setattr(linux, "preflight", lambda: None)
    monkeypatch.setattr("hwmonitor_mqtt.clients.pve.PveConfig.from_environment", lambda **kwargs: type("Config", (), {"ca_file": "/private/ca.pem"})())
    config = tmp_path / "pve.env"
    config.write_text("fixture\n")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "Failed to connect to bus")

    monkeypatch.setattr(install.subprocess, "run", run)
    assert install.main(["--pve-config", str(config), "--deployment", "docker"]) == 2
    assert "Could not inspect native PVE sender state" in capsys.readouterr().out
    assert len(calls) == 1 and calls[0][0].endswith("systemctl")


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


def test_pve_docker_accepts_named_systemd_properties_in_any_order(tmp_path, monkeypatch):
    from hwmonitor_mqtt.platforms.installers.pve_docker import deployment_environment

    monkeypatch.setattr("hwmonitor_mqtt.clients.pve.PveConfig.from_environment", lambda **kwargs: type("Config", (), {"ca_file": "/private/ca.pem"})())
    result = deployment_environment(
        tmp_path / "pve.env", "fixture-host", "native", {},
        run=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "UnitFileState=disabled\nLoadState=loaded\nActiveState=inactive\n", ""),
        which=lambda name: "/usr/bin/systemctl",
    )
    assert result["SENDER_HOSTNAME"] == "fixture-host"


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
        "docker", "compose", "-f", "/project/docker-compose.linux.yml", "--profile", "agent", "up", "--build", "-d",
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


def test_pve_dry_run_reports_unconfigured_native_sender_without_installing(monkeypatch, capsys):
    target = Target("linux", "pve", "native", "fixture-pve", "debian", ("pve-manager installed", "pveversion succeeded"))
    monkeypatch.setattr(install, "detect_target", lambda: target)
    monkeypatch.setattr(install.subprocess, "run", lambda *args, **kwargs: pytest.fail("dry run installed PVE sender"))

    assert install.main(["--dry-run", "--pve-config", "/missing/pve.env"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["target"]["system"] == "pve"
    assert result["collector"] == "hwmonitor_mqtt.agents.agent_sender_async"
    assert result["status"] == "PVE configuration is unconfigured"
    assert result["requirements"] == ["native systemd service"]
    assert result["command"][-1] == "/missing/pve.env"


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
