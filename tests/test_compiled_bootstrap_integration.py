"""Exercise the real compiled package through a controlled release endpoint.

Set HWMONITOR_TEST_ARCHIVE and provide protected21-bootstrap-test:local to run.
The Docker command inside the disposable installer container is a test double.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tarfile
import threading

import pytest

BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap.sh"


@pytest.fixture
def release(tmp_path):
    name = os.getenv("HWMONITOR_TEST_ARCHIVE")
    if not name or subprocess.run(["docker", "image", "inspect", "protected21-bootstrap-test:local"], capture_output=True).returncode:
        pytest.skip("requires compiled archive and isolated installer test image")
    blob = Path(name).read_bytes()
    with tarfile.open(fileobj=__import__("io").BytesIO(blob), mode="r:gz") as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
    checksum = hashlib.sha256(blob).hexdigest()
    state = {"bad_checksum": False, "missing": False}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            base = f"http://127.0.0.1:{self.server.server_port}"
            version = manifest["version"]
            names = ["version.json", "sender-linux-x64.tar.gz", "sender-linux-x64.tar.gz.sha256"]
            if self.path == "/releases/latest":
                data = json.dumps({"tag_name": "v" + version, "assets": [{"name": n, "browser_download_url": f"{base}/releases/download/v{version}/{n}"} for n in names]}).encode()
            elif self.path.endswith("/version.json"):
                data = json.dumps({"version": version, "source": manifest["source"]}).encode()
            elif self.path.endswith(".tar.gz.sha256"):
                data = (("0" * 64 if state["bad_checksum"] else checksum) + "  sender-linux-x64.tar.gz\n").encode()
            elif self.path.endswith(".tar.gz") and not state["missing"]:
                data = blob
            else:
                self.send_error(404); return
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield server.server_port, state, manifest
    server.shutdown(); server.server_close(); thread.join()


def run_installer(tmp_path, port, *, existing=True, fail=False, input_text="", native_active=False, mutate_stage=False):
    (tmp_path / "bin").mkdir(exist_ok=True)
    docker = tmp_path / "bin" / "docker"
    docker.write_text('''#!/bin/sh
printf '%s\\n' "$*" >>/work/docker-calls
case "$1 $2" in
 'compose version') exit 0;;
 'inspect sys-agent') if [ "$FAKE_EXISTING" = 1 ]; then exit 0; else echo 'No such object' >&2; exit 1; fi;;
 'inspect --format') case "$3" in *State.Running*) echo 'true 1234 0';; *Config.Image*) echo old:tag;; *) echo sha256:old;; esac; exit 0;;
 'compose -f') exit "$FAKE_EXIT";;
 *) exit 0;;
esac
''')
    docker.chmod(0o755)
    virt = tmp_path / "bin" / "systemd-detect-virt"; virt.write_text("#!/bin/sh\nexit 1\n"); virt.chmod(0o755)
    if native_active:
        systemctl = tmp_path / "bin" / "systemctl"
        systemctl.write_text("#!/bin/sh\nprintf 'LoadState=loaded\\nActiveState=active\\nUnitFileState=enabled\\n'\n")
        systemctl.chmod(0o755)
    if mutate_stage:
        wrapper = tmp_path / "bin" / "runuser"
        wrapper.write_text('''#!/bin/sh
/sbin/runuser "$@"
result=$?
if [ "$result" = 0 ] && [ "$5" = --stage ]; then
    /sbin/runuser -u nobody -- sh -c 'printf tampered > "$1/Dockerfile"' _ "$7"
    touch /work/stage-mutated
fi
exit "$result"
''')
        wrapper.chmod(0o755)
    root = tmp_path / "root"
    if existing:
        root.mkdir()
        for n, data in {".hwmonitor-mqtt-bootstrap": "", "sender": "old binary", ".env": "private settings", "Dockerfile": "old Dockerfile", "docker-compose.linux.yml": "old compose", "docker-compose.override.yml": "custom override"}.items():
            (root / n).write_text(data)
    command = ["docker", "run", "--rm", "-i", "--network", "host", "-v", f"{BOOTSTRAP}:/bootstrap.sh:ro", "-v", f"{tmp_path}:/work", "-e", "HWMONITOR_BOOTSTRAP_ROOT=/work/root", "-e", f"HWMONITOR_TEST_RELEASE_BASE=http://127.0.0.1:{port}", "-e", f"FAKE_EXISTING={int(existing)}", "-e", f"FAKE_EXIT={23 if fail else 0}", "-e", "PATH=/work/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "protected21-bootstrap-test:local", "bash", "-c", "rm -f /.dockerenv; exec bash /bootstrap.sh"]
    result = subprocess.run(command, input=input_text, text=True, capture_output=True, timeout=90)
    return result, root


def test_checksum_failure_does_not_activate_or_replace_previous_install(release, tmp_path):
    port, state, _ = release; state["bad_checksum"] = True
    result, root = run_installer(tmp_path, port)
    assert result.returncode != 0
    assert (root / "sender").read_text() == "old binary"
    assert (root / ".env").read_text() == "private settings"
    assert not (tmp_path / "docker-calls").exists()


def test_failed_activation_restores_previous_files_and_service_identity(release, tmp_path):
    port, _, _ = release
    result, root = run_installer(tmp_path, port, fail=True)
    assert result.returncode != 0
    for name, value in {"sender": "old binary", ".env": "private settings", "Dockerfile": "old Dockerfile", "docker-compose.linux.yml": "old compose", "docker-compose.override.yml": "custom override"}.items():
        assert (root / name).read_text() == value
    calls = (tmp_path / "docker-calls").read_text()
    assert "tag sha256:old old:tag" in calls
    assert "--no-build" in calls and "docker-compose.override.yml" in calls


def test_successful_upgrade_preserves_existing_settings_and_override(release, tmp_path):
    port, _, manifest = release
    result, root = run_installer(tmp_path, port)
    assert result.returncode == 0, result.stderr
    assert (root / ".env").read_text() == "private settings"
    assert (root / "docker-compose.override.yml").read_text() == "custom override"
    result = subprocess.run([str(root / "sender"), "--version"], text=True, capture_output=True, check=True)
    assert result.stdout.strip() == manifest["version"] + " " + manifest["source"]


def test_new_install_quotes_password_for_compose(release, tmp_path):
    port, _, _ = release
    password = 'slash\\ and $ and " and #'
    result, root = run_installer(tmp_path, port, existing=False, input_text=f"broker.example\n1883\nuser\n{password}\n")
    assert result.returncode == 0, result.stderr
    permissions = subprocess.check_output(["docker", "run", "--rm", "-v", f"{tmp_path}:/work:ro", "protected21-bootstrap-test:local", "stat", "-c", "%a", "/work/root/.env"], text=True)
    assert permissions.strip() == "600"
    content = subprocess.check_output(["docker", "run", "--rm", "-v", f"{tmp_path}:/work:ro", "protected21-bootstrap-test:local", "cat", "/work/root/.env"])
    envfile = tmp_path / "check.env"; envfile.write_bytes(content)
    parsed = subprocess.run(["docker", "compose", "--env-file", str(envfile), "-f", str(root / "docker-compose.linux.yml"), "config", "--environment"], env={**os.environ, "SENDER_HOSTNAME": "test"}, capture_output=True, text=True, check=True)
    assert f"MQTT_PASS={password}" in parsed.stdout.splitlines()


def test_active_native_sender_blocks_docker_without_replacing_existing_files(release, tmp_path):
    port, _, _ = release
    result, root = run_installer(tmp_path, port, native_active=True)
    assert result.returncode != 0
    assert (root / "sender").read_text() == "old binary"
    assert (root / ".env").read_text() == "private settings"
    calls = (tmp_path / "docker-calls").read_text() if (tmp_path / "docker-calls").exists() else ""
    assert " up " not in calls


def test_unprivileged_staging_mutation_cannot_change_activated_files(release, tmp_path):
    port, _, _ = release
    result, root = run_installer(tmp_path, port, mutate_stage=True)
    assert (tmp_path / "stage-mutated").exists()
    assert result.returncode == 0, result.stderr
    assert (root / "Dockerfile").read_text() != "tampered"
    assert (root / "Dockerfile").read_text().startswith("# syntax=docker/dockerfile:")
