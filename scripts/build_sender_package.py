"""Build a preliminary Linux sender executable from a clean committed tree."""

import argparse
import hashlib
import io
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = {
    "Nuitka": "2.7.16",
    "ordered-set": "4.1.0",
    "zstandard": "0.23.0",
    "paho-mqtt": "2.1.0",
    "psutil": "7.1.1",
    "python-dotenv": "1.1.1",
}


def source_snapshot(repo, destination, source="HEAD"):
    archive = subprocess.check_output(["git", "archive", source], cwd=repo)
    destination.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        contents.extractall(destination, filter="data")


def create_archive(candidate, output):
    name = "sender-linux-x64.tar.gz"
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
    parser.add_argument("--version", default="development")
    args = parser.parse_args()
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, check=True, capture_output=True, text=True)
    if status.stdout:
        parser.error("uncommitted source changes are not allowed")
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if sys.version_info[:3] != (3, 11, 15) or platform.system() != "Linux" or platform.machine() != "x86_64":
        parser.error("Linux x64 and CPython 3.11.15 are required")
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
        subprocess.run([sys.executable, "-m", "nuitka", "--onefile", "--output-filename=sender", f"--output-dir={build_dir}", str(source_dir / "hwmonitor_mqtt/agents/agent_sender_async.py")], cwd=source_dir, env=environment, check=True)
        candidate = Path(directory) / "candidate"
        notices = candidate / "licenses"
        notices.mkdir(parents=True)
        executable = candidate / "sender"
        shutil.copy2(build_dir / "sender", executable)
        for name in ("python", "paho-mqtt", "psutil", "python-dotenv"):
            shutil.copy2(source_dir / "tests/fixtures/release-notices" / (name + ".txt"), notices)
        shutil.copy2(source_dir / "release-public/LICENSE-NOTICE.md", notices / "project.md")
        manifest = {"platform": "linux-x64", "source": source, "version": args.version,
                    "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                    "compiler": "Nuitka 2.7.16", "runtime": {"python": platform.python_version(),
                    **{name: DEPENDENCIES[name] for name in ("paho-mqtt", "psutil", "python-dotenv")}}}
        (candidate / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        create_archive(candidate, output)


if __name__ == "__main__":
    main()
