#!/usr/bin/env python3
"""Run the native PVE installer against an isolated local MQTT broker."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from threading import Event
from queue import Queue, Empty

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from paho.mqtt import client as mqtt
from hwmonitor_mqtt.platforms.installers import pve


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="required: install a temporary systemd service as root")
    parser.add_argument("--timeout", type=int, default=90, help="seconds to wait for a real MQTT message")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("native installation changes /opt and /etc/systemd/system; pass --run to confirm an isolated smoke run")
    if os.geteuid() != 0:
        parser.error("run this smoke check as root (for example, with sudo)")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    if not shutil.which("systemctl") or not shutil.which("python3") or not shutil.which("mosquitto"):
        parser.error("systemctl, python3, and mosquitto are required")

    old_umask = os.umask(0o077)
    install_root = None
    service_name = f"hwmonitor-mqtt-smoke-{os.getpid()}-{uuid.uuid4().hex[:8]}.service"
    broker = None
    systemd_dir = Path("/etc/systemd/system")
    old_installer_paths = (pve.INSTALL_ROOT, pve.APP_ROOT, pve.SERVICE_VENV, pve.SERVICE_NAME, pve.SYSTEMD_DIR)
    try:
        install_root = Path(tempfile.mkdtemp(prefix="hwmonitor-mqtt-smoke-", dir="/opt"))
        config = install_root / "pve.env"
        config.write_text("")
        config.chmod(0o600)
        project_root = install_root / "project"
        shutil.copytree(REPO / "hwmonitor_mqtt", project_root / "hwmonitor_mqtt",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        install_root.chmod(0o755)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        broker_config = install_root / "mosquitto.conf"
        broker_config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
        broker = subprocess.Popen(["mosquitto", "-c", str(broker_config)], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if broker.poll() is not None:
                raise RuntimeError("local mosquitto broker exited during startup")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("local mosquitto broker did not start")

        runtime_root = install_root / "runtime"
        pve.INSTALL_ROOT = str(runtime_root)
        pve.APP_ROOT = str(runtime_root / "app")
        pve.SERVICE_VENV = str(runtime_root / "venv")
        pve.SERVICE_NAME = service_name

        def run(command, check=False, **kwargs):
            if Path(command[0]).name == "systemctl" and command[1:2] == ["daemon-reload"]:
                unit = systemd_dir / service_name
                unit_text = unit.read_text().replace(
                    "[Install]", f"Environment=BROKER_HOST=127.0.0.1\nEnvironment=BROKER_PORT={port}\n"
                    "Environment=PUBLISH_INTERVAL_SEC=1\n\n[Install]"
                )
                unit.write_text(unit_text)
            return subprocess.run(command, check=check, timeout=120, **kwargs)

        pve.SYSTEMD_DIR = str(systemd_dir)
        subscriber = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"smoke-{os.getpid()}")
        messages = Queue()
        subscribed = Event()
        subscriber.on_connect = lambda client, userdata, flags, reason, properties=None: client.subscribe("sys/agents/+/metrics")
        subscriber.on_subscribe = lambda client, userdata, mid, reason, properties=None: subscribed.set()
        subscriber.on_message = lambda client, userdata, message: messages.put((message.topic, message.payload))
        subscriber.connect("127.0.0.1", port, keepalive=30)
        subscriber.loop_start()
        try:
            if not subscribed.wait(10):
                raise RuntimeError("local MQTT subscriber did not connect")
            which = lambda name: None if name == "docker" else shutil.which(name)
            result = pve.install(project_root, config_file=config, run=run, which=which)
            if result:
                raise RuntimeError(f"native installer exited with status {result}")
            unit_text = (systemd_dir / service_name).read_text()
            if "DynamicUser=yes" not in unit_text or stat_mode(config) != 0o600:
                raise RuntimeError("installed service or private fixture configuration has unsafe permissions")
            subprocess.run([str(runtime_root / "venv" / "bin" / "python"), "-m", "pip", "--version"],
                           check=True, capture_output=True, text=True, timeout=10)
            try:
                topic, payload = messages.get(timeout=args.timeout)
            except Empty as error:
                status = subprocess.run(["systemctl", "status", service_name, "--no-pager"],
                                        capture_output=True, text=True, timeout=10)
                raise RuntimeError(f"no MQTT metric arrived; systemd status: {status.stdout}\n{status.stderr}") from error
            metric = json.loads(payload)
            if not topic.startswith("sys/agents/") or "system" not in metric or "cpu" not in metric:
                raise RuntimeError(f"received an unexpected sender payload on {topic}")
            print(f"native service published a metrics packet on {topic}")
        finally:
            subscriber.loop_stop()
            subscriber.disconnect()
    finally:
        if install_root is not None:
            try:
                subprocess.run(["systemctl", "disable", "--now", service_name], check=False,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                pass
            wants = systemd_dir / "multi-user.target.wants"
            if wants.is_dir():
                (wants / service_name).unlink(missing_ok=True)
            (systemd_dir / service_name).unlink(missing_ok=True)
            try:
                subprocess.run(["systemctl", "daemon-reload"], check=False, timeout=20)
            except (OSError, subprocess.TimeoutExpired):
                pass
            shutil.rmtree(install_root, ignore_errors=True)
        try:
            if broker is not None:
                broker.terminate()
                broker.wait(timeout=5)
        except ProcessLookupError:
            pass
        except subprocess.TimeoutExpired:
            broker.kill()
            broker.wait()
        finally:
            os.umask(old_umask)
            pve.INSTALL_ROOT, pve.APP_ROOT, pve.SERVICE_VENV, pve.SERVICE_NAME, pve.SYSTEMD_DIR = old_installer_paths
    return 0


def stat_mode(path):
    return Path(path).stat().st_mode & 0o777


if __name__ == "__main__":
    raise SystemExit(main())
