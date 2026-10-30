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
