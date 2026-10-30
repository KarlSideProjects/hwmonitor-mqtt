import os
from pathlib import Path
import re
import subprocess

import pytest

from _pwsh import run_pwsh

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("wget_fails,curl_fails,installer_exit", [
    (False, False, 0),
    (True, False, 0),
    (True, True, 0),
    (False, False, 37),
])
def test_installation_docs_bootstrap_uses_downloader_fallback_safely(tmp_path, wget_fails, curl_fails, installer_exit):
    installation = (REPO / "docs/deployment/installation.md").read_text()
    section = installation.split("## 在 Linux 或 PVE 安裝 Sender", 1)[1]
    command = re.search(r"```bash\n(.*?)\n```", section, re.DOTALL).group(1)
    assert command == 'u=https://github.com/KarlSideProjects/hwmonitor-mqtt-releases/releases/latest/download/bootstrap.sh; f=$(mktemp) && (wget -qO "$f" "$u" || curl -fsSL "$u" -o "$f") && bash "$f"'
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "downloaders.log"
    for name, fails, args in (("wget", wget_fails, ('-qO',)), ("curl", curl_fails, ('-o', '--output'))):
        script = bindir / name
        script.write_text(
            "#!/bin/sh\n"
            f"echo {name} >> '{log}'\n"
            + ("exit 9\n" if fails else "")
            + "while [ $# -gt 0 ]; do\n"
            + f"  case \"$1\" in {args[0]}) out=$2; shift 2 ;; "
            + (f"{args[1]}) out=$2; shift 2 ;; " if len(args) > 1 else "")
            + "*) shift ;; esac\ndone\n"
            + f"printf '#!/bin/sh\\n[ {installer_exit} -eq 0 ] || exit {installer_exit}\\ntouch {tmp_path}/bootstrap-ran\\n' > \"$out\"\n"
        )
        script.chmod(0o755)
    for name, target in (("bash", "/bin/bash"), ("mktemp", "/usr/bin/mktemp"), ("touch", "/usr/bin/touch")):
        (bindir / name).symlink_to(target)
    result = subprocess.run(
        ["bash", "-c", command], cwd=tmp_path, text=True, capture_output=True, check=False,
        env={**os.environ, "PATH": str(bindir)},
    )

    if wget_fails and curl_fails:
        assert result.returncode != 0
        assert not (tmp_path / "bootstrap-ran").exists()
        assert log.read_text().splitlines() == ["wget", "curl"]
    else:
        assert result.returncode == installer_exit, result.stderr
        assert (tmp_path / "bootstrap-ran").exists() == (installer_exit == 0)
        assert log.read_text().splitlines() == (["wget", "curl"] if wget_fails else ["wget"])



def test_installation_docs_windows_command_is_the_short_admin_installer():
    installation = (REPO / "docs/deployment/installation.md").read_text()
    section = installation.split("## Windows Sender 安裝與注意事項", 1)[1]
    command = re.search(r"```powershell\n(.*?)\n```", section, re.DOTALL).group(1)
    assert "系統管理員" in section
    assert command == "irm https://raw.githubusercontent.com/KarlSideProjects/hwmonitor-mqtt/refs/heads/master/bootstrap.ps1 -ErrorAction Stop | iex"



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
