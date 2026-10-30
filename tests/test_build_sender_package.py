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
    result = subprocess.run([sys.executable, str(repo / "scripts" / BUILDER.name), "--output", str(tmp_path / "out")], capture_output=True, text=True)
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
