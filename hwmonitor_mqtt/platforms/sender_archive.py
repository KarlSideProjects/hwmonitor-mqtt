"""Validate and stage a checksum-bound Linux Sender release archive."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile


REQUIRED = {"sender", "manifest.json", "licenses/python.txt", "licenses/paho-mqtt.txt",
            "licenses/psutil.txt", "licenses/python-dotenv.txt", "licenses/project.md",
            "Dockerfile", "docker-compose.linux.yml", "docker-compose.pve.yml",
            "docs/configuration/pve.env.example", ".dockerignore"}
MAX_MEMBER = 128 * 1024 * 1024


def stage(archive, destination, version, source, checksum):
    archive, destination = Path(archive), Path(destination)
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or not re.fullmatch(r"[0-9a-f]{40}", source) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("invalid release metadata")
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination already exists")
    with archive.open("rb") as archive_file:
        if os.fstat(archive_file.fileno()).st_size > MAX_MEMBER:
            raise ValueError("archive is too large")
        archive_bytes = archive_file.read(MAX_MEMBER + 1)
        if len(archive_bytes) > MAX_MEMBER or hashlib.sha256(archive_bytes).hexdigest() != checksum:
            raise ValueError("archive checksum mismatch")
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as package:
            members = []
            for member in package:
                members.append(member)
                if len(members) > len(REQUIRED):
                    raise ValueError("too many archive entries")
            names = [member.name for member in members]
            if len(names) != len(set(names)) or set(names) != REQUIRED:
                raise ValueError("archive paths are invalid")
            if sum(m.size for m in members) > MAX_MEMBER or any(not m.isfile() or m.size <= 0 or m.size > MAX_MEMBER for m in members):
                raise ValueError("archive entry type or size is invalid")
            manifest_file = package.extractfile("manifest.json")
            manifest = json.load(manifest_file)
            if not isinstance(manifest, dict) or manifest.get("platform") != "linux-x64" or manifest.get("version") != version or manifest.get("source") != source or not isinstance(manifest.get("executable_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["executable_sha256"]):
                raise ValueError("manifest mismatch")
            with tempfile.TemporaryDirectory(prefix="sender-stage-", dir=destination.parent) as temporary:
                root = Path(temporary) / "content"
                (root / "licenses").mkdir(parents=True)
                for member in members:
                    (root / member.name).parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(member) as src, (root / member.name).open("wb") as dest:
                        shutil.copyfileobj(src, dest)
                sender = root / "sender"
                with sender.open("rb") as file:
                    header = file.read(20)
                    file.seek(0)
                    digest = hashlib.file_digest(file, "sha256").hexdigest()
                if header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00" or digest != manifest["executable_sha256"]:
                    raise ValueError("executable integrity or architecture mismatch")
                sender.chmod(0o755)
                os.replace(root, destination)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("archive", "destination", "version", "source", "checksum"):
        parser.add_argument(name)
    args = parser.parse_args(argv)
    try:
        stage(args.archive, args.destination, args.version, args.source, args.checksum)
    except (OSError, ValueError, tarfile.TarError) as error:
        parser.exit(1, f"Invalid sender package: {error}\n")
    return 0
