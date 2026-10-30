"""Assemble a Windows sender candidate from a compiled binary and real notices."""

import argparse
import hashlib
import importlib.metadata
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NAME = "sender-windows-x64.zip"


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--notices", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--version", required=True)
    parser.add_argument("--compiler", required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{40}", args.source):
        parser.error("source must be a full lowercase commit SHA")
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", args.version):
        parser.error("version must have three numeric components")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", args.python_version):
        parser.error("Python version must have three numeric components")
    notices = sorted(args.notices.iterdir())
    if not notices or any(not p.is_file() or p.is_symlink() or p.name.lower() == "project.txt" or not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9._-]*", p.name) or not p.stat().st_size for p in notices):
        parser.error("notices must contain nonempty, safely named regular files")
    runtime = {"python": args.python_version}
    if args.report:
        report = ET.parse(args.report).getroot()
        if report.tag != "nuitka-compilation-report" or report.get("completion") != "yes":
            parser.error("Nuitka report is incomplete")
        for item in report.findall("./distributions/distribution"):
            name, version = item.get("name"), item.get("version")
            if not name or not version or name in runtime:
                parser.error("invalid distribution in Nuitka report")
            runtime[name] = version
        if len(runtime) == 1:
            parser.error("Nuitka report contains no distributions")
    else:
        for name in ("paho-mqtt", "psutil", "python-dotenv", "pythonnet", "clr-loader", "cffi", "pycparser", "pywin32", "WMI"):
            try:
                runtime[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                pass
    manifest = {
        "platform": "windows-x64", "source": args.source, "version": args.version,
        "executable_sha256": sha256(args.executable), "compiler": args.compiler, "runtime": runtime,
        "runtime_inventory_basis": "nuitka-report" if args.report else "installed-candidates-unverified",
    }
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / NAME
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.write(args.executable, "sender.exe")
        package.writestr("manifest.json", json.dumps(manifest, sort_keys=True, indent=2) + "\n")
        package.write(args.source_root / "release-public/LICENSE-NOTICE.md", "licenses/project.txt")
        for notice in notices:
            package.write(notice, f"licenses/{notice.name}")
        for name in ("install_windows_sender_task.ps1", "start_windows_sender.ps1"):
            package.write(args.source_root / "scripts/windows" / name, f"scripts/windows/{name}")
    (args.output / (NAME + ".sha256")).write_text(f"{sha256(archive)}  {NAME}\n", encoding="ascii")


if __name__ == "__main__":
    main()
