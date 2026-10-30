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
