"""Builds must not silently identify uncommitted code as a Git revision."""

import shutil
import subprocess
import sys
from pathlib import Path


BUILDER = Path(__file__).resolve().parents[1] / "scripts" / "build_sender_package.py"


def test_builder_rejects_uncommitted_source(tmp_path):
    assert BUILDER.is_file(), "build entrypoint is missing"
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copyfile(BUILDER, repo / "scripts" / BUILDER.name)
    shutil.copyfile(BUILDER.parent / "capture_sender_build.py", repo / "scripts" / "capture_sender_build.py")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"], check=True)
    (repo / "scripts" / BUILDER.name).write_text((repo / "scripts" / BUILDER.name).read_text() + "\n# modified\n")
    result = subprocess.run([sys.executable, str(repo / "scripts" / BUILDER.name), "--output", str(tmp_path / "out"), "--version", "1.2.3"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "uncommitted" in result.stderr.lower()
    assert not (tmp_path / "out").exists()


def test_source_snapshot_excludes_ignored_local_files(tmp_path):
    from scripts.build_sender_package import source_snapshot

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("local.py\n")
    (repo / "tracked.py").write_text("print('committed')\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"], check=True)
    (repo / "local.py").write_text("print('private')\n")
    snapshot = tmp_path / "snapshot"
    source_snapshot(repo, snapshot)
    assert (snapshot / "tracked.py").read_text() == "print('committed')\n"
    assert not (snapshot / "local.py").exists()


def test_archive_contains_only_candidate_files_and_checksum(tmp_path):
    import hashlib
    import tarfile
    from scripts.build_sender_package import create_archive

    candidate = tmp_path / "candidate"
    (candidate / "licenses").mkdir(parents=True)
    (candidate / "sender").write_bytes(b"test executable")
    (candidate / "manifest.json").write_text('{}\n')
    (candidate / "licenses" / "python.txt").write_text("notice")
    output = tmp_path / "output"
    output.mkdir()
    create_archive(candidate, output)
    archive = output / "sender-linux-x64.tar.gz"
    with tarfile.open(archive) as contents:
        assert set(contents.getnames()) == {"sender", "manifest.json", "licenses/python.txt"}
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (output / "sender-linux-x64.tar.gz.sha256").read_text() == f"{expected}  sender-linux-x64.tar.gz\n"


def test_entrypoint_reports_embedded_release_and_source(tmp_path):
    from scripts.build_sender_package import write_entrypoint

    script = tmp_path / "entrypoint.py"
    write_entrypoint(script, "1.2.3", "a" * 40)
    result = subprocess.run([sys.executable, str(script), "--version"], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "1.2.3 " + "a" * 40


def test_entrypoint_starts_sender_without_module_reexecution(tmp_path):
    import os
    from scripts.build_sender_package import write_entrypoint

    package = tmp_path / "hwmonitor_mqtt" / "agents"
    package.mkdir(parents=True)
    (package / "agent_sender_async.py").write_text("def run():\n    print('sender started')\n")
    script = tmp_path / "entrypoint.py"
    write_entrypoint(script, "1.2.3", "a" * 40)
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "sender started"


def test_entrypoint_passes_arguments_to_pve_bootstrap(tmp_path):
    import os
    from scripts.build_sender_package import write_entrypoint

    package = tmp_path / "hwmonitor_mqtt" / "platforms"
    package.mkdir(parents=True)
    (package / "pve_bootstrap.py").write_text("def main(argv):\n    print(','.join(argv))\n    return 3\n")
    script = tmp_path / "entrypoint.py"
    write_entrypoint(script, "1.2.3", "a" * 40)
    result = subprocess.run([sys.executable, str(script), "--pve-bootstrap", "--config", "settings"], env={**os.environ, "PYTHONPATH": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 3
    assert result.stdout.strip() == "--config,settings"


def test_entrypoint_dispatches_staging_command(tmp_path):
    import os
    from scripts.build_sender_package import write_entrypoint

    package = tmp_path / "hwmonitor_mqtt" / "platforms"
    package.mkdir(parents=True)
    (package / "sender_archive.py").write_text("def main(argv):\n    print(','.join(argv))\n    return 4\n")
    script = tmp_path / "entrypoint.py"
    write_entrypoint(script, "1.2.3", "a" * 40)
    result = subprocess.run([sys.executable, str(script), "--stage", "archive", "target"], env={**os.environ, "PYTHONPATH": str(tmp_path)}, capture_output=True, text=True)
    assert result.returncode == 4
    assert result.stdout.strip() == "archive,target"


def test_builder_rejects_missing_or_invalid_release_version_before_build(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copyfile(BUILDER, repo / "scripts" / BUILDER.name)
    shutil.copyfile(BUILDER.parent / "capture_sender_build.py", repo / "scripts" / "capture_sender_build.py")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "base"], check=True)
    for extra, message in (([], "required"), (["--version", "development"], "invalid release version")):
        result = subprocess.run([sys.executable, str(repo / "scripts" / BUILDER.name), "--output", str(tmp_path / "out"), *extra], capture_output=True, text=True)
        assert result.returncode != 0
        assert message in result.stderr.lower()
        assert not (tmp_path / "out").exists()


def test_build_host_libc_is_compatible_with_supported_linux_baseline():
    from scripts.build_sender_package import compatible_libc
    assert compatible_libc("glibc", "2.31")
    assert not compatible_libc("glibc", "2.38")
    assert not compatible_libc("unknown", "")


def test_arm64_archive_uses_official_asset_name(tmp_path):
    from scripts.build_sender_package import create_archive

    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "sender").write_bytes(b"binary")
    create_archive(candidate, tmp_path, "linux-arm64")
    assert (tmp_path / "sender-linux-arm64.tar.gz").is_file()
    assert (tmp_path / "sender-linux-arm64.tar.gz.sha256").read_text().endswith("  sender-linux-arm64.tar.gz\n")


def test_builder_main_packages_both_platforms(tmp_path, monkeypatch):
    import json
    import tarfile
    from types import SimpleNamespace
    from scripts import build_sender_package as builder
    from scripts.linux_arm64 import compiler
    calls = []
    monkeypatch.setattr(compiler, "prepare", lambda *args: calls.append(args))

    monkeypatch.setattr(builder.sys, "version_info", (3, 11, 15))
    monkeypatch.setattr(builder.platform, "system", lambda: "Linux")
    monkeypatch.setattr(builder.platform, "libc_ver", lambda: ("glibc", "2.31"))
    monkeypatch.setattr(builder.importlib.metadata, "version", lambda name: builder.DEPENDENCIES[name])
    monkeypatch.setattr(builder.importlib.metadata, "distribution", lambda name: SimpleNamespace(read_text=lambda _: "license"))
    monkeypatch.setattr(builder.subprocess, "check_output", lambda *args, **kwargs: "a" * 40)
    def snapshot(repo, destination, source):
        for name in ("tests/fixtures/release-notices", "release-public", "docs/configuration", "release-notices"):
            shutil.copytree(repo / name, destination / name)
        for name in ("Dockerfile", "docker-compose.linux.yml", "docker-compose.pve.yml"):
            shutil.copyfile(repo / name, destination / name)
    monkeypatch.setattr(builder, "source_snapshot", snapshot)
    def run(command, **kwargs):
        if command[0] == "git":
            return SimpleNamespace(stdout="")
        build_dir = Path(next(item.split("=", 1)[1] for item in command if item.startswith("--output-dir=")))
        build_dir.mkdir(parents=True)
        (build_dir / "sender").write_bytes(b"compiled executable")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(builder.subprocess, "run", run)
    def capture(command, source, build_dir, executable, report, dist, runtime, inventory, provenance, smoke_args, **kwargs):
        if kwargs:
            assert kwargs["arm_state"].name == "arm-startup"
            assert "--show-scons" in command
        build_dir.mkdir(parents=True)
        executable.write_bytes(b"compiled executable")
        report.write_text("report")
    monkeypatch.setattr(builder, "compile_and_capture", capture)
    for machine, target in (("x86_64", "linux-x64"), ("aarch64", "linux-arm64")):
        monkeypatch.setattr(builder.platform, "machine", lambda: machine)
        output = tmp_path / target
        monkeypatch.setattr(builder.sys, "argv", ["builder", "--output", str(output), "--version", "1.2.3"])
        builder.main()
        with tarfile.open(output / f"sender-{target}.tar.gz") as package:
            manifest = json.load(package.extractfile("manifest.json"))
            bundle = builder.ROOT / "release-notices" / f"{target}-cpython-3.11.15-pbs-20260728" / "runtime-bundle.txt"
            assert package.extractfile("licenses/python.txt").read() == bundle.read_bytes()
        assert manifest["platform"] == target
        assert manifest["source"] == "a" * 40
    assert len(calls) == 1


def test_dockerfile_installs_intel_tools_only_on_amd64(tmp_path):
    import os

    dockerfile = (BUILDER.parents[1] / "Dockerfile").read_text()
    instruction = dockerfile.split("RUN ", 1)[1].split("\n\n", 1)[0].replace("\\\n", " ")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "dpkg": 'printf "%s\\n" "$TEST_ARCH"',
        "sed": ":",
        "apt-get": 'printf "%s\\n" "$*" >> "$TEST_CALLS"',
        "rm": ":",
    }.items():
        script = bin_dir / name
        script.write_text("#!/bin/sh\n" + body + "\n")
        script.chmod(0o755)
    for arch in ("amd64", "arm64"):
        calls = tmp_path / arch
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "TEST_ARCH": arch, "TEST_CALLS": str(calls)}
        subprocess.run(["sh", "-c", instruction], env=env, check=True)
        assert ("intel-gpu-tools" in calls.read_text()) == (arch == "amd64")
        assert "smartmontools" in calls.read_text()


def test_runtime_image_uses_authenticated_dated_snapshot():
    dockerfile = (BUILDER.parents[1] / 'Dockerfile').read_text()
    assert '# syntax=' not in dockerfile
    assert 'snapshot.debian.org/archive/debian/20261027T000000Z' in dockerfile
    assert 'snapshot.debian.org/archive/debian-security/20261027T000000Z' in dockerfile
    assert 'Check-Valid-Until: no' in dockerfile
    assert 'trusted=yes' not in dockerfile and '--allow-unauthenticated' not in dockerfile
