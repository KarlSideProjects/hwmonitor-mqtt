"""Side-effect-free detection of the machine that runs the installer."""

from dataclasses import dataclass
import os
import platform
import re
import socket
import subprocess
from pathlib import Path
from typing import Callable, Mapping


@dataclass(frozen=True)
class Target:
    os: str
    system: str
    runtime: str
    machine: str
    distribution: str | None
    evidence: tuple[str, ...]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(command, 1, "", "")


def _os_release(path: Path) -> dict[str, str]:
    values = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep:
                values[key] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def detect_target(
    *,
    system: str | None = None,
    release_path: str | Path = "/etc/os-release",
    environ: Mapping[str, str] | None = None,
    exists: Callable[[str], bool] = lambda path: Path(path).exists(),
    run: Callable[[list[str]], subprocess.CompletedProcess[str] | None] = _run,
) -> Target:
    """Detect this process's target without installing, connecting, or writing."""
    host_os = (system or platform.system()).lower()
    env = os.environ if environ is None else environ
    evidence = [f"platform.system={system or platform.system()}"]
    machine = socket.gethostname()
    if host_os == "windows":
        return Target("windows", "windows", "native", machine, None, tuple(evidence + ["Windows sender platform"]))
    if host_os != "linux":
        return Target(host_os or "unknown", "unsupported", "native", machine, None, tuple(evidence))

    release = _os_release(Path(release_path))
    if release.get("ID"):
        evidence.append(f"os-release ID={release['ID']}")
    runtime = "native"
    if env.get("container"):
        runtime = env["container"].lower()
    elif exists("/.dockerenv"):
        runtime = "docker"
    elif exists("/run/.containerenv"):
        runtime = "podman"
    else:
        virt = run(["systemd-detect-virt", "--vm"])
        if virt and virt.returncode == 0 and virt.stdout.strip():
            runtime = "vm"
            evidence.append(f"virtual machine detected: {virt.stdout.strip()}")

    if runtime not in {"native", "vm"}:
        evidence.append(f"container runtime marker={runtime}")
        return Target("linux", "unknown", runtime, machine, release.get("ID"), tuple(evidence + ["host system not inferred from container"]))

    package = run(["dpkg-query", "-W", "-f=${Status}", "pve-manager"])
    package_installed = bool(package and package.returncode == 0 and package.stdout.strip() == "install ok installed")
    version = run(["pveversion"])
    version_ok = bool(version and version.returncode == 0 and re.match(r"^pve-manager/\d+(?:\.\d+)*(?:[-+][\w.-]+)?(?:/|\s|$)", version.stdout.strip()))
    if package_installed:
        evidence.append("pve-manager installed")
    if version_ok:
        evidence.append("pveversion succeeded")
    if package_installed and version_ok:
        return Target("linux", "pve", runtime, machine, release.get("ID"), tuple(evidence))
    if package_installed != version_ok:
        evidence.append("conflicting/incomplete PVE evidence")
        return Target("linux", "unsupported", runtime, machine, release.get("ID"), tuple(evidence))

    return Target("linux", "linux", runtime, machine, release.get("ID"), tuple(evidence))
