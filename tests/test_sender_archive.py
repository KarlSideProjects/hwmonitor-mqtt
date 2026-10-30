import hashlib
import io
import json
import tarfile

import pytest

from hwmonitor_mqtt.platforms.sender_archive import stage


SOURCE = "a" * 40
VERSION = "1.2.3"
ELF = b"\x7fELF\x02\x01" + bytes(12) + b"\x3e\x00" + b"test"


def archive(tmp_path, extra=()):
    path = tmp_path / "candidate.tar.gz"
    manifest = json.dumps({"platform": "linux-x64", "version": VERSION, "source": SOURCE, "executable_sha256": hashlib.sha256(ELF).hexdigest()}).encode()
    with tarfile.open(path, "w:gz") as out:
        for name, body in [("sender", ELF), ("manifest.json", manifest), *(("licenses/" + n, b"notice") for n in ("python.txt", "paho-mqtt.txt", "psutil.txt", "python-dotenv.txt", "project.md")), ("Dockerfile", b"FROM image\n"), ("docker-compose.linux.yml", b"services: {}\n"), ("docker-compose.pve.yml", b"services: {}\n"), ("docs/configuration/pve.env.example", b"PVE_ENABLED=1\n"), (".dockerignore", b"*\n!sender\n"), *extra]:
            info = tarfile.TarInfo(name)
            info.size = len(body)
            out.addfile(info, io.BytesIO(body))
    return path


def test_stage_rejects_traversal_and_leaves_destination_untouched(tmp_path):
    package = archive(tmp_path, [("../escape", b"bad")])
    with pytest.raises(ValueError):
        stage(package, tmp_path / "install", VERSION, SOURCE, hashlib.sha256(package.read_bytes()).hexdigest())
    assert not (tmp_path / "install").exists()
    assert not (tmp_path / "escape").exists()


def test_stage_accepts_matching_release_without_running_sender(tmp_path):
    package = archive(tmp_path)
    destination = tmp_path / "install"
    stage(package, destination, VERSION, SOURCE, hashlib.sha256(package.read_bytes()).hexdigest())
    assert (destination / "sender").read_bytes() == ELF
    assert (destination / "sender").stat().st_mode & 0o111


def test_stage_rejects_duplicate_sender(tmp_path):
    package = archive(tmp_path, [("sender", ELF)])
    with pytest.raises(ValueError):
        stage(package, tmp_path / "install", VERSION, SOURCE, hashlib.sha256(package.read_bytes()).hexdigest())
    assert not (tmp_path / "install").exists()


def test_stage_rejects_untrusted_checksum(tmp_path):
    package = archive(tmp_path)
    with pytest.raises(ValueError):
        stage(package, tmp_path / "install", VERSION, SOURCE, "0" * 64)
    assert not (tmp_path / "install").exists()


def test_stage_rejects_symlink_and_wrong_identity(tmp_path):
    package = tmp_path / "candidate.tar.gz"
    with tarfile.open(package, "w:gz") as out:
        link = tarfile.TarInfo("sender"); link.type = tarfile.SYMTYPE; link.linkname = "/etc/passwd"
        out.addfile(link)
    with pytest.raises(ValueError):
        stage(package, tmp_path / "install", VERSION, SOURCE, hashlib.sha256(package.read_bytes()).hexdigest())
    package = archive(tmp_path)
    with pytest.raises(ValueError):
        stage(package, tmp_path / "install", "1.2.4", SOURCE, hashlib.sha256(package.read_bytes()).hexdigest())
    assert not (tmp_path / "install").exists()


def test_stage_accepts_deployment_files_for_compiled_installation(tmp_path):
    package = archive(tmp_path)
    stage(package, tmp_path / "install", VERSION, SOURCE, hashlib.sha256(package.read_bytes()).hexdigest())
    assert (tmp_path / "install" / "Dockerfile").read_text() == "FROM image\n"
