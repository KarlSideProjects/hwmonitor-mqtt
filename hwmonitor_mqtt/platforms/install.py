"""Run or preview the fixed installer selected from the local target."""

import argparse
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
    args = parser.parse_args(argv)
    target = detect_target()
    collector = None
    linux_installer = None
    try:
        collector, collector_status = collector_for(target)
        command = install_command(target, args.project_root)
        if target.system == "pve":
            args.pve_config = str(Path(args.pve_config).resolve())
            command[-1] = args.pve_config
            from .installers import pve as pve_installer
        if target.os == "linux" and target.system != "pve":
            from .installers import linux as linux_installer
    except ValueError as error:
        print(json.dumps({
            "role": args.role,
            "target": {**target.__dict__, "evidence": list(target.evidence)},
            "collector": collector,
            "status": str(error),
        }, indent=2))
        return 2
    requirement_error = linux_installer.preflight() if linux_installer else pve_installer.readiness(args.pve_config) if target.system == "pve" else None
    result = {
        "role": args.role,
        "target": {**target.__dict__, "evidence": list(target.evidence)},
        "collector": collector,
        "status": requirement_error or collector_status,
        "command": command,
        "requirements": (["native systemd service"] if target.system == "pve" else ["Docker Compose v2"] if target.os == "linux" else []),
        "ready": requirement_error is None,
    }
    print(json.dumps(result, indent=2))
    if args.dry_run:
        return 0
    if requirement_error and target.system != "pve":
        return 2
    return subprocess.run(command, cwd=args.project_root, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
