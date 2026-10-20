from pathlib import Path
import shutil
import subprocess


def command(project_root):
    compose = str(Path(project_root) / "docker-compose.linux.yml")
    return ["docker", "compose", "-f", compose, "--profile", "agent", "up", "-d"]


def preflight(which=shutil.which, run=subprocess.run):
    docker = which("docker")
    if not docker:
        return "Docker is not installed"
    if run([docker, "compose", "version"], capture_output=True, check=False).returncode:
        return "Docker Compose v2 is not available"
    return None
