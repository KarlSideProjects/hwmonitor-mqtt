"""Run or preview the fixed installer selected from the local target."""

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess

from .detect import detect_target
from .dispatch import collector_for, install_command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("sender",), default="sender")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--project-root", default=str(Path.cwd()))
    parser.add_argument("--pve-config", default=os.getenv("PVE_CONFIG_FILE", "/etc/hwmonitor-mqtt/pve.env"))
    parser.add_argument("--target-system", choices=("linux", "pve"), help="Explicit host target for an existing container")
    parser.add_argument("--deployment", choices=("docker",), help="Select Docker deployment on a detected host")
    args = parser.parse_args(argv)
    target = detect_target()
    if args.target_system:
        if target.runtime in {"native", "vm"} or target.system not in {"unknown", args.target_system}:
            print(json.dumps({"target": {**target.__dict__, "evidence": list(target.evidence)}, "status": "Explicit target contradicts detected host"}, indent=2))
            return 2
        target = replace(target, system=args.target_system, evidence=target.evidence + (f"explicit host target={args.target_system}",))
    deployment = args.deployment or (
        "docker" if target.system == "linux" or target.system == "pve" and target.runtime not in {"native", "vm"}
        else "native"
    )
    if args.deployment:
        if target.system in {"unknown", "unsupported"}:
            print(json.dumps({"target": {**target.__dict__, "evidence": list(target.evidence)}, "status": "Host system must be explicit before selecting a deployment runtime"}, indent=2))
            return 2
    selected_target = replace(target, runtime=deployment)
    collector = None
    pve_installer = None
    linux_installer = None
    try:
        collector, collector_status = collector_for(selected_target)
        command = install_command(selected_target, args.project_root)
        if target.system == "pve":
            args.pve_config = str(Path(args.pve_config).resolve())
            from .installers import pve as pve_installer
            if deployment == "native":
                command[-1] = args.pve_config
        if target.os == "linux" and (target.system == "linux" or deployment == "docker"):
            from .installers import linux as linux_installer
    except ValueError as error:
        print(json.dumps({
            "role": args.role,
            "target": {**target.__dict__, "evidence": list(target.evidence)},
            "collector": collector,
            "status": str(error),
        }, indent=2))
        return 2
    requirement_error = None
    if deployment == "docker" and target.os == "linux":
        requirement_error = linux_installer.preflight()
    if target.system == "pve":
        requirement_error = requirement_error or pve_installer.readiness(args.pve_config)
    result = {
        "role": args.role,
        "target": {**target.__dict__, "evidence": list(target.evidence)},
        "deployment": deployment,
        "collector": collector,
        "status": requirement_error or collector_status,
        "command": command,
        "requirements": (["Docker Compose v2", "validated PVE API config", "private CA/config mounts"] if deployment == "docker" and target.system == "pve" else ["native systemd service"] if target.system == "pve" else ["Docker Compose v2"] if deployment == "docker" and target.os == "linux" else []),
        "ready": requirement_error is None,
    }
    print(json.dumps(result, indent=2))
    if args.dry_run:
        return 0
    if requirement_error and (target.system != "pve" or deployment == "docker"):
        return 2
    if deployment == "docker":
        environment = os.environ.copy()
        environment["SENDER_HOSTNAME"] = target.machine
        if target.system == "pve":
            try:
                from .installers.pve_docker import deployment_environment
                environment = deployment_environment(args.pve_config, target.machine, target.runtime, environment, run=subprocess.run)
            except (OSError, RuntimeError, ValueError) as error:
                print(str(error))
                return 2
        return subprocess.run(command, cwd=args.project_root, env=environment, check=False).returncode
    return subprocess.run(command, cwd=args.project_root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
