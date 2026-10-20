"""Native systemd installation for a detected PVE host."""

import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

from hwmonitor_mqtt.clients.pve import PveApiClient, PveConfig, PveError


SERVICE_NAME = "hwmonitor-mqtt-pve.service"
DEFAULT_CONFIG = "/etc/hwmonitor-mqtt/pve.env"
INSTALL_ROOT = "/opt/hwmonitor-mqtt-pve"
APP_ROOT = f"{INSTALL_ROOT}/app"
SERVICE_VENV = f"{INSTALL_ROOT}/venv"
SYSTEMD_DIR = "/etc/systemd/system"


def command(project_root, config_file=DEFAULT_CONFIG):
    return [sys.executable, "-m", "hwmonitor_mqtt.platforms.installers.pve",
            "--project-root", str(Path(project_root).resolve()), "--config-file", str(Path(config_file).resolve())]


def readiness(config_file=DEFAULT_CONFIG):
    try:
        config = PveConfig.from_environment(env={}, env_file=config_file)
        if config is None:
            return "PVE configuration is unconfigured"
        PveApiClient(config).validate_scope()
        return None
    except PveError as error:
        return f"PVE validation failed: {error}"


def _unit_escape(value):
    return str(value).replace("\\", "\\\\").replace(" ", "\\x20").replace("\t", "\\x09").replace('"', "\\x22").replace("%", "%%")


def unit_text(project_root, python=f"{SERVICE_VENV}/bin/python", config_file=DEFAULT_CONFIG):
    root = Path(project_root).resolve()
    config_file = Path(config_file).resolve()
    return ("[Unit]\nDescription=Hardware Monitor MQTT sender for Proxmox VE\nAfter=network-online.target\nWants=network-online.target\n\n"
            "[Service]\nType=simple\nDynamicUser=yes\nWorkingDirectory=" + _unit_escape(APP_ROOT) + "\n"
            "EnvironmentFile=-" + _unit_escape(root / ".env") + "\nEnvironmentFile=-" + _unit_escape(config_file) + "\n"
            "Environment=PVE_ENABLED=1\nEnvironment=PVE_CONFIG_LOADED=1\nEnvironment=PVE_ENV_PRELOADED=1\n"
            "Environment=PVE_CONFIG_FILE=" + _unit_escape(config_file) + "\nEnvironment=SENDER_PROFILE=full\n"
            "ExecStart=" + _unit_escape(python) + " -m hwmonitor_mqtt.agents.agent_sender_async\n"
            "Restart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n")



def _normalize_tree_permissions(root):
    root = Path(root)
    if root.is_symlink():
        raise RuntimeError("installer runtime path must not be a symbolic link")
    root.chmod(0o755)
    for directory, subdirs, files in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        for name in tuple(subdirs):
            path = current / name
            if path.is_symlink():
                subdirs.remove(name)
            else:
                path.chmod(0o755)
        for name in files:
            path = current / name
            if path.is_symlink():
                continue
            mode = stat.S_IMODE(path.stat().st_mode)
            path.chmod(0o755 if mode & 0o111 else 0o644)


def install(project_root, config_file=DEFAULT_CONFIG, run=subprocess.run, which=shutil.which, geteuid=os.geteuid):
    config_file = Path(config_file).resolve()
    if not Path(project_root, "hwmonitor_mqtt", "agents", "agent_sender_async.py").is_file():
        raise RuntimeError("sender source is missing")
    systemctl = which("systemctl")
    if not systemctl:
        raise RuntimeError("systemd is unavailable")
    python3 = which("python3")
    if not python3:
        raise RuntimeError("python3 is unavailable")
    if geteuid() != 0:
        raise RuntimeError("run the native PVE installer with sudo")
    validation = readiness(config_file)
    if validation and validation != "PVE configuration is unconfigured":
        raise RuntimeError(validation)
    docker = which("docker")
    if docker:
        existing = run([docker, "inspect", "sys-agent"], check=False, capture_output=True, text=True)
        if existing.returncode == 0:
            raise RuntimeError("sys-agent container exists; stop/remove it before installing the native PVE sender")
        if "No such object" not in (getattr(existing, "stderr", "") or ""):
            raise RuntimeError("could not verify that no sys-agent container is running")
    source = Path(project_root).resolve() / "hwmonitor_mqtt"
    app = Path(APP_ROOT)
    install_root = app.parent
    if install_root.is_symlink():
        raise RuntimeError("installer runtime path must not be a symbolic link")
    install_root.mkdir(parents=True, exist_ok=True)
    install_root.chmod(0o755)
    package = app / "hwmonitor_mqtt"
    shutil.copytree(source, package, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _normalize_tree_permissions(app)
    venv_python = Path(SERVICE_VENV) / "bin" / "python"
    for args in ([python3, "-m", "venv", SERVICE_VENV],
                 [str(venv_python), "-m", "pip", "install", "psutil>=7", "paho-mqtt>=2.1", "python-dotenv>=1.1"]):
        result = run(args, check=False)
        if result.returncode:
            return result.returncode
    _normalize_tree_permissions(Path(SERVICE_VENV))
    unit = Path(SYSTEMD_DIR) / SERVICE_NAME
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(unit_text(project_root, str(venv_python), config_file))
    for args in ([systemctl, "daemon-reload"], [systemctl, "enable", "--now", SERVICE_NAME],
                 [systemctl, "restart", SERVICE_NAME]):
        result = run(args, check=False)
        if result.returncode:
            return result.returncode
    result = run([systemctl, "is-active", "--quiet", SERVICE_NAME], check=False)
    return result.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--config-file", default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    try:
        return install(args.project_root, args.config_file)
    except RuntimeError as error:
        print(str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
