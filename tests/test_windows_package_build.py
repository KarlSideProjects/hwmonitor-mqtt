import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def test_windows_package_contains_exact_binary_and_installer_scripts(tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / "sender.exe"
    binary.write_bytes(b"test binary bytes")
    notices = tmp_path / "notices"
    notices.mkdir()
    (notices / "python.txt").write_text("test notice")
    output = tmp_path / "out"
    result = subprocess.run([sys.executable, str(root / "scripts/windows/package_sender.py"), "--executable", str(binary), "--notices", str(notices), "--output", str(output), "--source", "a" * 40, "--version", "1.2.3", "--compiler", "Nuitka test"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    archive = output / "sender-windows-x64.zip"
    with zipfile.ZipFile(archive) as package:
        assert package.read("sender.exe") == b"test binary bytes"
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["source"] == "a" * 40 and manifest["version"] == "1.2.3"
        assert manifest["executable_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
        assert package.read("scripts/windows/start_windows_sender.ps1") == (root / "scripts/windows/start_windows_sender.ps1").read_bytes()
        assert package.read("scripts/windows/install_windows_sender_task.ps1") == (root / "scripts/windows/install_windows_sender_task.ps1").read_bytes()
        assert package.read("licenses/python.txt") == b"test notice"
    assert (output / "sender-windows-x64.zip.sha256").read_text().strip() == hashlib.sha256(archive.read_bytes()).hexdigest() + "  sender-windows-x64.zip"


def test_producer_archive_passes_windows_staging_validation(tmp_path):
    import struct

    from _pwsh import run_pwsh

    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / "sender.exe"
    data = bytearray(256)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, 0x8664)
    binary.write_bytes(data)
    notices = tmp_path / "notices"
    notices.mkdir()
    (notices / "python.txt").write_text("test notice")
    output = tmp_path / "out"
    result = subprocess.run([sys.executable, str(root / "scripts/windows/package_sender.py"), "--executable", str(binary), "--notices", str(notices), "--output", str(output), "--source", "a" * 40, "--version", "1.2.3", "--compiler", "Nuitka test"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    archive = output / "sender-windows-x64.zip"
    result = run_pwsh('& $env:VALIDATOR -Archive $env:ARCHIVE -Sha256 $env:SHA -Destination $env:DEST', env={"VALIDATOR": str(root / "scripts/windows/validate_sender_package.ps1"), "ARCHIVE": str(archive), "SHA": hashlib.sha256(archive.read_bytes()).hexdigest(), "DEST": str(tmp_path / "stage")})
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "stage/sender.exe").read_bytes() == data
