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
    result = subprocess.run([sys.executable, str(root / "scripts/windows/package_sender.py"), "--executable", str(binary), "--notices", str(notices), "--output", str(output), "--source", "a" * 40, "--version", "1.2.3", "--compiler", "Nuitka test", "--python-version", "3.13.17"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    archive = output / "sender-windows-x64.zip"
    with zipfile.ZipFile(archive) as package:
        assert package.read("sender.exe") == b"test binary bytes"
        manifest = json.loads(package.read("manifest.json"))
        assert manifest["source"] == "a" * 40 and manifest["version"] == "1.2.3"
        assert manifest["runtime"]["python"] == "3.13.17"
        assert manifest["executable_sha256"] == hashlib.sha256(binary.read_bytes()).hexdigest()
        assert package.read("scripts/windows/start_windows_sender.ps1") == (root / "scripts/windows/start_windows_sender.ps1").read_bytes()
        assert package.read("scripts/windows/install_windows_sender_task.ps1") == (root / "scripts/windows/install_windows_sender_task.ps1").read_bytes()
        assert package.read("licenses/python.txt") == b"test notice"
        assert package.read("licenses/project.txt") == (root / "release-public/LICENSE-NOTICE.md").read_bytes()
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
    result = subprocess.run([sys.executable, str(root / "scripts/windows/package_sender.py"), "--executable", str(binary), "--notices", str(notices), "--output", str(output), "--source", "a" * 40, "--version", "1.2.3", "--compiler", "Nuitka test", "--python-version", "3.13.17"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    archive = output / "sender-windows-x64.zip"
    result = run_pwsh('& $env:VALIDATOR -Archive $env:ARCHIVE -Sha256 $env:SHA -Destination $env:DEST', env={"VALIDATOR": str(root / "scripts/windows/validate_sender_package.ps1"), "ARCHIVE": str(archive), "SHA": hashlib.sha256(archive.read_bytes()).hexdigest(), "DEST": str(tmp_path / "stage")})
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "stage/sender.exe").read_bytes() == data


def test_package_uses_scripts_from_selected_committed_source_tree(tmp_path):
    root = Path(__file__).resolve().parents[1]
    source = tmp_path / "prior-source"
    scripts = source / "scripts/windows"
    scripts.mkdir(parents=True)
    (scripts / "start_windows_sender.ps1").write_text("old launcher")
    (scripts / "install_windows_sender_task.ps1").write_text("old installer")
    (source / "release-public").mkdir()
    (source / "release-public/LICENSE-NOTICE.md").write_text("old project notice")
    executable = tmp_path / "sender.exe"
    executable.write_bytes(b"old binary")
    notices = tmp_path / "notices"
    notices.mkdir()
    (notices / "python.txt").write_text("notice")
    output = tmp_path / "output"
    result = subprocess.run([sys.executable, str(root / "scripts/windows/package_sender.py"), "--executable", str(executable), "--notices", str(notices), "--output", str(output), "--source", "a" * 40, "--source-root", str(source), "--version", "0.0.0", "--compiler", "test", "--python-version", "3.13.17"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(output / "sender-windows-x64.zip") as package:
        assert package.read("scripts/windows/start_windows_sender.ps1") == b"old launcher"
        assert package.read("licenses/project.txt") == b"old project notice"


def test_manifest_runtime_uses_actual_nuitka_report(tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = tmp_path / "sender.exe"
    binary.write_bytes(b"test binary bytes")
    notices = tmp_path / "notices"
    notices.mkdir()
    (notices / "python.txt").write_text("notice")
    report = tmp_path / "report.xml"
    report.write_text('<nuitka-compilation-report completion="yes"><distributions><distribution name="psutil" version="7.1.1"/><distribution name="setuptools" version="84.2.1"/></distributions></nuitka-compilation-report>')
    output = tmp_path / "out"
    result = subprocess.run([sys.executable, str(root / "scripts/windows/package_sender.py"), "--executable", str(binary), "--notices", str(notices), "--output", str(output), "--source", "a" * 40, "--version", "1.2.3", "--compiler", "test", "--python-version", "3.13.17", "--report", str(report)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(output / "sender-windows-x64.zip") as package:
        runtime = json.loads(package.read("manifest.json"))["runtime"]
    assert runtime == {"python": "3.13.17", "psutil": "7.1.1", "setuptools": "84.2.1"}
