import os
from pathlib import Path
import re
import subprocess

import pytest

from _pwsh import run_pwsh

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("failure", ["", "download", "installer"])
def test_installation_docs_linux_one_line_runs_only_after_download(tmp_path, failure):
    section = (REPO / "docs/deployment/installation.md").read_text().split("## 在 Linux 或 PVE 安裝 Sender", 1)[1]
    command = re.search(r"```bash\n(.*?)\n```", section, re.DOTALL).group(1)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    assert len(command.splitlines()) == 1
    installer = b'#!/bin/sh\ntouch "$MARKER"\n[ "$FAILURE" != installer ]\n'
    (tmp_path / "payload").write_bytes(installer)
    curl = bindir / "curl"
    curl.write_text("""#!/bin/bash
[ "$FAILURE" != download ] || exit 1
while [ "$1" != -o ]; do shift; done
cp "$PAYLOAD" "$2"
""")
    curl.chmod(0o755)
    identity = bindir / 'id'
    identity.write_text('#!/bin/sh\necho 0\n')
    identity.chmod(0o755)
    result = subprocess.run(["bash", "-c", command], cwd=tmp_path, capture_output=True, text=True, env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAILURE": failure, "PAYLOAD": str(tmp_path / "payload"), "MARKER": str(tmp_path / "ran"), "TMPDIR": str(tmp_path)})
    assert (result.returncode == 0) == (failure == ""), result.stderr
    assert (tmp_path / "ran").exists() == (failure != "download")
    assert not list(tmp_path.glob("tmp.*"))


@pytest.mark.parametrize("failure", ["", "download"])
def test_installation_docs_windows_one_line_runs_only_after_download(tmp_path, failure):
    marker = tmp_path / "ran"
    section = (REPO / "docs/deployment/installation.md").read_text().split("## Windows Sender 安裝與注意事項", 1)[1]
    command = re.search(r"```powershell\n(.*?)\n```", section, re.DOTALL).group(1)
    assert len(command.splitlines()) == 1
    mock = r'''function Invoke-WebRequest { param([switch]$UseBasicParsing, $Uri, $ErrorAction) if ($env:FAILURE -eq 'download') { throw 'download failed' }; [pscustomobject]@{Content='Set-Content -LiteralPath $env:MARKER -Value ran'} }
try {
''' + command + "\n} catch { exit 7 }\nexit 0"
    result = run_pwsh(mock, env={"FAILURE": failure, "MARKER": str(marker)})
    assert result.returncode == (0 if failure == "" else 7), result.stderr
    assert marker.exists() == (failure == "")


def test_powershell_dotenv_guard_catches_empty_interpolation(tmp_path):
    source = (REPO / "bootstrap.ps1").read_text()
    match = re.search(r"\$user -match '([^']+)'", source)
    assert match and match.group(1) == r"\$\{[^}]*\}"
    result = run_pwsh(f"$value='abc${{}}def'; if ($value -match '{match.group(1)}') {{ exit 0 }} else {{ exit 1 }}")
    assert result.returncode == 0, result.stderr



@pytest.mark.parametrize(("content", "expected"), [("", "unknown"), ("abc123\n", "abc123")])
def test_powershell_kept_install_marker_reads_empty_and_commit(tmp_path, content, expected):
    source = (REPO / "bootstrap.ps1").read_text()
    match = re.search(r"^\s*(\$installed = .*\n\s*if \(-not \$installed\).*)$", source, re.M)
    assert match
    marker = tmp_path / ".hwmonitor-mqtt-bootstrap"
    marker.write_text(content)
    snippet = match.group(1).replace("(Join-Path $root '.hwmonitor-mqtt-bootstrap')", "$env:MARKER_PATH")
    result = run_pwsh(snippet + "; Write-Output $installed", env={"MARKER_PATH": str(marker)})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
