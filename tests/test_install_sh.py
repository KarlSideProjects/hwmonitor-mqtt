import json
import os
from pathlib import Path
import subprocess
import sys


REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "install.sh"


def test_shell_entrypoint_forwards_arguments_from_any_working_directory(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "python-calls.jsonl"
    fake_python = bin_dir / "python3"
    fake_python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"with open({str(record)!r}, 'a') as calls:\n"
        "    calls.write(json.dumps({'cwd': os.getcwd(), 'args': sys.argv[1:]}) + '\\n')\n"
        "sys.exit(0 if sys.argv[1:2] == ['-c'] else 23)\n"
    )
    fake_python.chmod(0o755)

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--dry-run", "--deployment", "docker", "--", "value with spaces"],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
    )

    calls = [json.loads(line) for line in record.read_text().splitlines()]
    assert result.returncode == 23
    assert calls[0]["args"][0] == "-c"
    assert calls[1] == {
        "cwd": str(REPO),
        "args": [
            "-m",
            "hwmonitor_mqtt.platforms.install",
            "--project-root",
            str(REPO),
            "--dry-run",
            "--deployment",
            "docker",
            "--",
            "value with spaces",
        ],
    }


def test_shell_entrypoint_rejects_python_below_311(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_python = bin_dir / "python3"
    fake_python.write_text("#!/bin/sh\nexit 1\n")
    fake_python.chmod(0o755)

    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--dry-run"],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Python 3.11 or newer is required" in result.stderr
