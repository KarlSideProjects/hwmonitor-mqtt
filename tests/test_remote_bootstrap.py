import os
from pathlib import Path
import re
import subprocess

import pytest

from _pwsh import run_pwsh

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("failure", ["", "release", "installer", "checksum", "bad_hash"])
def test_installation_docs_linux_downloads_safely(tmp_path, failure):
    import hashlib

    section = (REPO / "docs/deployment/installation.md").read_text().split("## 在 Linux 或 PVE 安裝 Sender", 1)[1]
    command = re.search(r"```bash\n(.*?)\n```", section, re.DOTALL).group(1)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    installer = b'#!/bin/sh\ntouch "$MARKER"\n'
    digest = hashlib.sha256(installer).hexdigest()
    (tmp_path / "payload").write_bytes(installer)
    for name in ("install.sh", "install.sh.sha256"):
        (tmp_path / name).write_text("preserve")
    curl = bindir / "curl"
    curl.write_text("""#!/bin/bash
case "$*" in
  *api.github*) [ "$FAILURE" != release ] || exit 1; echo '{"tag_name":"v0.1.32"}' ;;
  *.sha256*) [ "$FAILURE" != checksum ] || exit 1; printf '%s  install.sh\\n' "$DIGEST" > install.sh.sha256 ;;
  *) [ "$FAILURE" != installer ] || exit 1; cp "$PAYLOAD" install.sh ;;
esac
""")
    curl.chmod(0o755)
    result = subprocess.run(["bash", "-c", command], cwd=tmp_path, capture_output=True, text=True, env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FAILURE": failure, "PAYLOAD": str(tmp_path / "payload"), "MARKER": str(tmp_path / "ran"), "DIGEST": "0" * 64 if failure == "bad_hash" else digest, "TMPDIR": str(tmp_path)})
    assert (result.returncode == 0) == (failure == ""), result.stderr
    assert (tmp_path / "ran").exists() == (failure == "")
    assert all((tmp_path / name).read_text() == "preserve" for name in ("install.sh", "install.sh.sha256"))
    assert not list(tmp_path.glob("tmp.*"))


@pytest.mark.parametrize("failure", ["", "release", "installer", "checksum", "bad_hash", "bad_utf8"])
def test_installation_docs_windows_verifies_bytes_before_execution(tmp_path, failure):
    import hashlib
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    marker = tmp_path / "ran"
    payload = b"Set-Content -LiteralPath $env:MARKER -Value ran" if failure != "bad_utf8" else b"\xff"
    digest = hashlib.sha256(payload).hexdigest() if failure != "bad_hash" else "0" * 64

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            missing = (failure == "installer" and self.path.endswith("/install.ps1")) or (failure == "checksum" and self.path.endswith(".sha256"))
            self.send_response(404 if missing else 200)
            self.end_headers()
            self.wfile.write((digest + "  install.ps1\n").encode() if self.path.endswith(".sha256") else payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        section = (REPO / "docs/deployment/installation.md").read_text().split("## Windows Sender 安裝與注意事項", 1)[1]
        command = re.search(r"```powershell\n(.*?)\n```", section, re.DOTALL).group(1)
        command = re.sub(r'^\$releaseBase = .*$', f'$releaseBase = "http://127.0.0.1:{server.server_port}"', command, flags=re.M)
        mock = "function Invoke-RestMethod { if ($env:FAILURE -eq 'release') { throw 'release failed' }; @{tag_name='v0.1.32'} }; try {\n" + command + "\n} catch { exit 7 }\nexit 0"
        result = run_pwsh(mock, env={"FAILURE": failure, "MARKER": str(marker)})
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
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
