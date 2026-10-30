import hashlib
import json
import struct
import zipfile
from pathlib import Path

from _pwsh import run_pwsh


def test_wrong_architecture_is_rejected_before_extraction(tmp_path):
    exe = bytearray(256)
    exe[:2] = b"MZ"
    struct.pack_into("<I", exe, 0x3C, 0x80)
    exe[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", exe, 0x84, 0x14C)  # x86
    archive = tmp_path / "package.zip"
    manifest = {"platform": "windows-x64", "source": "a" * 40, "version": "1", "executable_sha256": hashlib.sha256(exe).hexdigest(), "compiler": "test", "runtime": {}}
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("sender.exe", exe)
        package.writestr("manifest.json", json.dumps(manifest))
    destination = tmp_path / "out"
    script = Path(__file__).resolve().parents[1] / "scripts/windows/validate_sender_package.ps1"
    result = run_pwsh('& $env:VALIDATOR -Archive $env:ARCHIVE -Sha256 $env:SHA -Destination $env:DEST', env={"VALIDATOR": str(script), "ARCHIVE": str(archive), "SHA": hashlib.sha256(archive.read_bytes()).hexdigest(), "DEST": str(destination)})
    assert result.returncode != 0
    assert not destination.exists()
    assert "architecture" in (result.stdout + result.stderr).lower()


def test_matching_x64_package_extracts_after_validation(tmp_path):
    exe = bytearray(256)
    exe[:2] = b"MZ"
    struct.pack_into("<I", exe, 0x3C, 0x80)
    exe[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", exe, 0x84, 0x8664)
    archive = tmp_path / "package.zip"
    manifest = {"platform": "windows-x64", "source": "a" * 40, "version": "1", "executable_sha256": hashlib.sha256(exe).hexdigest(), "compiler": "test", "runtime": {}}
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("sender.exe", exe)
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr("licenses/example.txt", "license")
    destination = tmp_path / "out"
    script = Path(__file__).resolve().parents[1] / "scripts/windows/validate_sender_package.ps1"
    result = run_pwsh('& $env:VALIDATOR -Archive $env:ARCHIVE -Sha256 $env:SHA -Destination $env:DEST', env={"VALIDATOR": str(script), "ARCHIVE": str(archive), "SHA": hashlib.sha256(archive.read_bytes()).hexdigest(), "DEST": str(destination)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert (destination / "sender.exe").read_bytes() == exe


def test_traversal_entry_is_rejected_before_extraction(tmp_path):
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escape", "unsafe")
    destination = tmp_path / "out"
    script = Path(__file__).resolve().parents[1] / "scripts/windows/validate_sender_package.ps1"
    result = run_pwsh('& $env:VALIDATOR -Archive $env:ARCHIVE -Sha256 $env:SHA -Destination $env:DEST', env={"VALIDATOR": str(script), "ARCHIVE": str(archive), "SHA": hashlib.sha256(archive.read_bytes()).hexdigest(), "DEST": str(destination)})
    assert result.returncode != 0
    assert "unsafe" in (result.stdout + result.stderr).lower()
    assert not destination.exists()
    assert not (tmp_path / "escape").exists()


def test_parent_segment_in_license_entry_is_rejected(tmp_path):
    archive = tmp_path / "package.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("licenses/..", "unsafe")
    destination = tmp_path / "out"
    script = Path(__file__).resolve().parents[1] / "scripts/windows/validate_sender_package.ps1"
    result = run_pwsh('& $env:VALIDATOR -Archive $env:ARCHIVE -Sha256 $env:SHA -Destination $env:DEST', env={"VALIDATOR": str(script), "ARCHIVE": str(archive), "SHA": hashlib.sha256(archive.read_bytes()).hexdigest(), "DEST": str(destination)})
    assert result.returncode != 0
    assert "unsafe" in (result.stdout + result.stderr).lower()
    assert not destination.exists()
