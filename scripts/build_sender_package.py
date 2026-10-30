"""Build a preliminary Linux sender executable from a clean committed tree."""

import argparse
import hashlib
import io
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = {
    "Nuitka": "2.7.16",
    "pip": "24.0",
    "setuptools": "79.0.1",
    "ordered-set": "4.1.0",
    "zstandard": "0.23.0",
    "paho-mqtt": "2.1.0",
    "psutil": "7.1.1",
    "python-dotenv": "1.1.1",
    "typing-extensions": "4.15.0",
}


def compatible_libc(name, version):
    return name == "glibc" and bool(re.fullmatch(r"[0-9]+\.[0-9]+", version)) and tuple(map(int, version.split("."))) <= (2, 31)


def source_snapshot(repo, destination, source="HEAD"):
    archive = subprocess.check_output(["git", "archive", source], cwd=repo)
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        contents.extractall(destination, filter="data")


def write_entrypoint(path, version, source):
    path.write_text(
        "import sys\n"
        + "if sys.argv[1:] == [\"--version\"]:\n"
        + f"    print({version!r}, {source!r})\n"
        + "elif sys.argv[1:2] == [\"--pve-bootstrap\"]:\n    from hwmonitor_mqtt.platforms.pve_bootstrap import main\n    raise SystemExit(main(sys.argv[2:]))\n"
        + "elif sys.argv[1:2] == [\"--install\"]:\n    from hwmonitor_mqtt.platforms.install import main\n    raise SystemExit(main(sys.argv[2:]))\n"
        + "elif sys.argv[1:2] == [\"--native-install\"]:\n    from hwmonitor_mqtt.platforms.installers.pve import main\n    raise SystemExit(main(sys.argv[2:]))\n"
        + "elif sys.argv[1:2] == [\"--stage\"]:\n    from hwmonitor_mqtt.platforms.sender_archive import main\n    raise SystemExit(main(sys.argv[2:]))\n"
        + "else:\n    from hwmonitor_mqtt.agents.agent_sender_async import run\n    run()\n",
        encoding="utf-8",
    )


def create_archive(candidate, output, target="linux-x64"):
    name = f"sender-{target}.tar.gz"
    archive = output / name
    with tarfile.open(archive, "w:gz") as contents:
        for path in sorted(candidate.rglob("*")):
            if path.is_file():
                contents.add(path, arcname=path.relative_to(candidate), recursive=False)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / (name + ".sha256")).write_text(f"{checksum}  {name}\n", encoding="ascii")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.version):
        parser.error("invalid release version")
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, check=True, capture_output=True, text=True)
    if status.stdout:
        parser.error("uncommitted source changes are not allowed")
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    target_platform = {"x86_64": "linux-x64", "aarch64": "linux-arm64"}.get(platform.machine())
    if sys.version_info[:3] != (3, 11, 15) or platform.system() != "Linux" or target_platform is None:
        parser.error("Linux x64 or ARM64 and CPython 3.11.15 are required")
    if not compatible_libc(*platform.libc_ver()):
        parser.error("glibc 2.31 or older is required for portable Linux builds")
    for package, version in DEPENDENCIES.items():
        try:
            installed = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            installed = None
        if installed != version:
            parser.error(f"{package} {version} is required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sender-source-") as directory:
        source_dir = Path(directory) / "source"
        source_snapshot(ROOT, source_dir, source)
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(source_dir)
        build_dir = Path(directory) / "build"
        entrypoint = source_dir / "sender_entrypoint.py"
        write_entrypoint(entrypoint, args.version, source)
        subprocess.run([sys.executable, "-m", "nuitka", "--onefile", "--include-module=hwmonitor_mqtt.agents.agent_sender_async", f"--report={output / 'build-report.xml'}", "--output-filename=sender", f"--output-dir={build_dir}", str(entrypoint)], cwd=source_dir, env=environment, check=True)
        candidate = Path(directory) / "candidate"
        notices = candidate / "licenses"
        notices.mkdir(parents=True)
        executable = candidate / "sender"
        shutil.copy2(build_dir / "sender", executable)
        for name in ("python", "paho-mqtt", "psutil", "python-dotenv"):
            shutil.copy2(source_dir / "tests/fixtures/release-notices" / (name + ".txt"), notices)
        typing_notice = importlib.metadata.distribution("typing-extensions").read_text("licenses/LICENSE")
        if not typing_notice:
            raise RuntimeError("typing-extensions license is missing")
        (notices / "typing-extensions.txt").write_text(typing_notice, encoding="utf-8")
        shutil.copy2(source_dir / "release-public/LICENSE-NOTICE.md", notices / "project.md")
        for name in ("Dockerfile", "docker-compose.linux.yml", "docker-compose.pve.yml", "docs/configuration/pve.env.example"):
            target = candidate / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_dir / name, target)
        (candidate / ".dockerignore").write_text("*\n!Dockerfile\n!sender\n", encoding="utf-8")
        manifest = {"platform": target_platform, "source": source, "version": args.version,
                    "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                    "compiler": "Nuitka 2.7.16", "runtime": {"python": platform.python_version(),
                    **{name: DEPENDENCIES[name] for name in ("paho-mqtt", "psutil", "python-dotenv", "typing-extensions")}}}
        (candidate / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        create_archive(candidate, output, target_platform)


if __name__ == "__main__":
    main()
