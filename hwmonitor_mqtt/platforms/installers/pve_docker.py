"""Docker deployment checks and private mounts for a PVE host sender."""

from pathlib import Path
import shutil
import subprocess

from hwmonitor_mqtt.clients.pve import PveConfig


def command(project_root):
    compose = str(Path(project_root) / "docker-compose.pve.yml")
    return ["docker", "compose", "-f", compose, "--profile", "pve", "up", "--build", "-d"]


def deployment_environment(config_file, hostname, host_runtime, environ, run=subprocess.run, which=shutil.which):
    if host_runtime not in {"native", "vm"}:
        raise RuntimeError("Run the PVE Docker installer on the host so it can verify the native service state")
    systemctl = which("systemctl")
    if not systemctl:
        raise RuntimeError("systemctl is unavailable; cannot verify that a native PVE sender is stopped")
    result = run([systemctl, "show", "--property=LoadState", "--property=ActiveState", "--property=UnitFileState", "hwmonitor-mqtt-pve.service"],
                 check=False, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("Could not inspect native PVE sender state; resolve systemd access before selecting Docker")
    states = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    load_state = states.get("LoadState", "")
    active_state = states.get("ActiveState", "")
    unit_file_state = states.get("UnitFileState", "")
    if load_state == "loaded" and active_state == "inactive" and unit_file_state == "disabled":
        pass
    elif load_state == "not-found" and active_state == "inactive" and not unit_file_state:
        pass
    elif load_state in {"loaded", "not-found"} and active_state == "inactive":
        raise RuntimeError("Native PVE sender is enabled or has an unknown boot state; disable it before selecting Docker (--deployment docker)")
    else:
        raise RuntimeError("Native PVE sender is active or its state is unknown; disable it before selecting Docker (--deployment docker)")

    path = Path(config_file).resolve()
    config = PveConfig.from_environment(env={}, env_file=path)
    if config is None:
        raise RuntimeError("PVE configuration is unconfigured")
    if not Path(config.ca_file).is_absolute():
        raise RuntimeError("PVE_CA_FILE must be an absolute path for Docker bind mounts")
    environment = environ.copy()
    environment.update({
        "SENDER_HOSTNAME": hostname,
        "PVE_CONFIG_HOST": str(path),
        "PVE_CA_HOST": str(Path(config.ca_file).resolve()),
        "PVE_CA_TARGET": config.ca_file,
    })
    return environment
