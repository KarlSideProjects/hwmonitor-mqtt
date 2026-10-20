import math
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import ssl
import subprocess
import threading
import time
import os
import stat
import shutil
import sys
import urllib.error
import http.client

import pytest

from hwmonitor_mqtt.clients.pve import PveApiClient, PveConfig, PveError
from hwmonitor_mqtt.collectors.pve import PveCollector
from hwmonitor_mqtt.platforms.installers.pve import unit_text


def config(base_url, ca_file="/etc/ssl/certs/ca-certificates.crt"):
    return PveConfig(base_url, "monitor@pve!readonly", "secret-value", ca_file,
                     "cluster-a", "node-a")


def test_preloaded_systemd_config_does_not_open_private_file():
    env = {"PVE_CONFIG_LOADED": "1", "PVE_URL": "https://pve.example:8006", "PVE_TOKEN_ID": "monitor@pve!readonly",
           "PVE_TOKEN_SECRET": "secret-value", "PVE_CA_FILE": "/etc/ssl/certs/ca-certificates.crt",
           "PVE_CLUSTER_ID": "cluster-a", "PVE_NODE": "node-a"}
    assert PveConfig.from_environment(env, "/root/unreadable/pve.env").node == "node-a"


def test_api_client_rejects_redirects_without_following_authorization(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(cert), "-days", "1", "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost"], check=True, capture_output=True)
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/leak")
            self.end_headers()
        def log_message(self, *args): pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with pytest.raises(PveError, match="http_error"):
        PveApiClient(config(f"https://localhost:{server.server_port}", str(cert))).resources()
    server.shutdown()
    server.server_close()
    assert requests == ["/api2/json/nodes"]


def test_snapshot_maps_resources_to_fixture_contract_and_filters_node(monkeypatch):
    client = PveApiClient(config("https://pve.example:8006"))
    monkeypatch.setattr(client, "resources", lambda: [
        {"type": "qemu", "vmid": 100, "node": "node-a", "name": "web", "status": "running",
         "uptime": 120, "maxcpu": 2, "cpu": .25, "maxmem": 2147483648, "mem": 536870912},
        {"type": "lxc", "vmid": 101, "node": "other", "name": "elsewhere", "status": "stopped"},
    ])
    snapshot = PveCollector(client, poll_interval=10).poll_once(now=1792483000)
    assert snapshot == {
        "version": 1, "provider": "pve", "cluster_id": "cluster-a", "node": "node-a",
        "observed_at": 1792483000, "status": "ok", "guests": [{
            "id": "cluster-a:qemu:100", "vmid": 100, "type": "qemu", "node": "node-a",
            "name": "web", "status": "running", "uptime": 120, "vcpus": 2,
            "memory_basis": "pve", "cpu": {"percent_total": 25.0},
            "memory": {"ram": {"used": 536870912, "total": 2147483648, "percent": 25.0}},
            "network_io": {"total": {"rate": {"rx_bytes_per_s": None, "tx_bytes_per_s": None}}},
            "disk_io": {"total": {"rate": {"read_bytes_per_s": None, "write_bytes_per_s": None}}},
        }],
    }


def test_guest_io_rates_use_monotonic_intervals_and_reset_unknown_baselines():
    class Clock:
        value = 100.0
        def __call__(self): return self.value
    clock = Clock()
    class Api:
        config = config("https://pve.example:8006")
        uptime, node, netin, netout, diskread, diskwrite = 100, "node-a", 1000, 2000, 3000, 4000
        def resources(self):
            return [{"type": "qemu", "vmid": 100, "node": self.node, "name": "vm", "status": "running",
                     "uptime": self.uptime, "netin": self.netin, "netout": self.netout,
                     "diskread": self.diskread, "diskwrite": self.diskwrite}]
    api = Api()
    collector = PveCollector(api, monotonic=clock)
    first = collector.poll_once(now=1000)["guests"][0]
    assert first["network_io"]["total"]["rate"] == {"rx_bytes_per_s": None, "tx_bytes_per_s": None}
    clock.value += 10
    api.netin += 5000; api.netout += 2500; api.diskread += 10000; api.diskwrite += 5000
    second = collector.poll_once(now=1010)["guests"][0]
    assert second["network_io"]["total"]["rate"] == {"rx_bytes_per_s": 500.0, "tx_bytes_per_s": 250.0}
    assert second["disk_io"]["total"]["rate"] == {"read_bytes_per_s": 1000.0, "write_bytes_per_s": 500.0}
    api.netin -= 1
    clock.value += 10
    reset = collector.poll_once(now=1020)["guests"][0]
    assert reset["network_io"]["total"]["rate"] == {"rx_bytes_per_s": None, "tx_bytes_per_s": None}
    assert reset["disk_io"]["total"]["rate"] == {"read_bytes_per_s": None, "write_bytes_per_s": None}
    api.netin += 100; api.uptime = 1; clock.value += 10
    uptime_reset = collector.poll_once(now=1030)["guests"][0]
    assert uptime_reset["network_io"]["total"]["rate"]["rx_bytes_per_s"] is None
    api.node = "node-b"
    api.config = PveConfig("https://pve.example:8006", "monitor@pve!readonly", "secret-value",
                           "/etc/ssl/certs/ca-certificates.crt", "cluster-a", "node-b")
    clock.value += 10
    node_reset = collector.poll_once(now=1040)["guests"][0]
    assert node_reset["network_io"]["total"]["rate"]["rx_bytes_per_s"] is None
    api.netin = None; clock.value += 10
    missing = collector.poll_once(now=1050)["guests"][0]
    assert missing["network_io"]["total"]["rate"]["rx_bytes_per_s"] is None
    api.netin = 20000; clock.value += 10
    restored = collector.poll_once(now=1060)["guests"][0]
    assert restored["network_io"]["total"]["rate"]["rx_bytes_per_s"] is None


def test_snapshot_uses_the_shared_virtualization_v1_fixture():
    fixture = json.loads(open("tests/fixtures/virtualization-v1.json").read())
    class Api:
        config = PveConfig("https://pve.example:8006", "monitor@pve!readonly", "secret-value",
                           "/etc/ssl/certs/ca-certificates.crt", "lab", "pve-node-a")
        def resources(self):
            return [
                {"type": "qemu", "vmid": 100, "node": "pve-node-a", "name": "web-vm", "status": "running",
                 "uptime": 120, "maxcpu": 2, "cpu": .25, "mem": 536870912, "maxmem": 2147483648},
                {"type": "lxc", "vmid": 101, "node": "pve-node-a", "name": "db-ct", "status": "stopped",
                 "uptime": 0, "maxcpu": 1, "maxmem": 1073741824},
            ]
    assert PveCollector(Api()).poll_once(now=fixture["ts"]) == fixture["virtualization"]


def test_verified_tls_api_fixture_flows_through_client_and_collector(tmp_path):
    fixture = json.loads(open("tests/fixtures/virtualization-v1.json").read())
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(cert), "-days", "1", "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost"], check=True, capture_output=True)
    raw = [
        {"type": "qemu", "vmid": 100, "node": "pve-node-a", "name": "web-vm", "status": "running",
         "uptime": 120, "maxcpu": 2, "cpu": .25, "mem": 536870912, "maxmem": 2147483648},
        {"type": "lxc", "vmid": 101, "node": "pve-node-a", "name": "db-ct", "status": "stopped",
         "uptime": 0, "maxcpu": 1, "maxmem": 1073741824},
    ]
    routes = {"/api2/json/nodes": [{"node": "pve-node-a"}],
              "/api2/json/access/permissions": {"/": {"VM.Audit": True, "Sys.Audit": True}},
              "/api2/json/access/acl": [],
              "/api2/json/cluster/resources?type=vm": raw}
    auth = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            auth.append(self.headers.get("Authorization"))
            body = json.dumps({"data": routes[self.path]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cfg = PveConfig(f"https://localhost:{server.server_port}", "monitor@pve!readonly", "secret-value",
                    str(cert), "lab", "pve-node-a")
    snapshot = PveCollector(PveApiClient(cfg)).poll_once(now=fixture["ts"])
    server.shutdown()
    server.server_close()
    assert snapshot == fixture["virtualization"]
    assert auth == ["PVEAPIToken=monitor@pve!readonly=secret-value"] * 4


def test_api_client_rejects_hostname_mismatch_even_with_the_issuing_ca(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                    "-out", str(cert), "-days", "1", "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost"], check=True, capture_output=True)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b'{"data": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args): pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = PveApiClient(PveConfig(f"https://127.0.0.1:{server.server_port}", "monitor@pve!readonly",
                                    "secret-value", str(cert), "lab", "node-a"))
    try:
        with pytest.raises(PveError, match="connection_error"):
            client._get("nodes")
    finally:
        server.shutdown()
        server.server_close()


def test_api_client_uses_the_configured_finite_timeout():
    client = PveApiClient(PveConfig("https://pve.example:8006", "monitor@pve!readonly", "secret-value",
                                    "/etc/ssl/certs/ca-certificates.crt", "lab", "node-a", .25))
    class Opener:
        def open(self, request, timeout):
            assert timeout == .25
            raise TimeoutError
    client.opener = Opener()
    with pytest.raises(PveError, match="connection_error"):
        client.resources()


@pytest.mark.parametrize(("status", "code"), [(401, "unauthorized"), (403, "forbidden")])
def test_api_authentication_and_permission_errors_are_sanitized(status, code):
    client = PveApiClient(config("https://pve.example:8006"))
    class Opener:
        def open(self, *args, **kwargs):
            raise urllib.error.HTTPError("https://pve.example/secret", status, "denied", {}, None)
    client.opener = Opener()
    with pytest.raises(PveError, match=code):
        client.resources()


def test_deeply_nested_api_json_is_sanitized_as_invalid_response():
    client = PveApiClient(PveConfig("https://pve.example:8006", "monitor@pve!readonly", "secret-value",
                                    "/etc/ssl/certs/ca-certificates.crt", "lab", "node-a"))
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b"[" * 2000 + b"0" + b"]" * 2000
    class Opener:
        def open(self, *args, **kwargs): return Response()
    client.opener = Opener()
    with pytest.raises(PveError, match="invalid_response"):
        client._get("nodes")


def test_truncated_api_response_is_sanitized_and_client_recovers():
    client = PveApiClient(PveConfig("https://pve.example:8006", "monitor@pve!readonly", "secret-value",
                                    "/etc/ssl/certs/ca-certificates.crt", "lab", "node-a"))
    class Response:
        status = 200
        def __init__(self, truncated): self.truncated = truncated
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size):
            if self.truncated: raise http.client.IncompleteRead(b"{")
            return b'{"data": []}'
    class Opener:
        truncated = True
        def open(self, *args, **kwargs): return Response(self.truncated)
    client.opener = Opener()
    with pytest.raises(PveError, match="connection_error"):
        client._get("nodes")
    client.opener.truncated = False
    assert client._get("nodes") == []


def test_failure_keeps_last_complete_snapshot_and_first_failure_has_no_guests():
    class Api:
        def __init__(self): self.fail = False
        def resources(self):
            if self.fail: raise PveError("timeout")
            return [{"type": "lxc", "vmid": 101, "node": "node-a", "name": "db", "status": "stopped"}]
    api = Api()
    api.config = config("https://pve.example:8006")
    collector = PveCollector(api)
    pending = collector.snapshot()
    assert pending["status"] == "error" and pending["cluster_id"] == "cluster-a" and "guests" not in pending
    first = collector.poll_once(now=100)
    api.fail = True
    failed = collector.poll_once(now=200)
    assert first["guests"][0]["memory"]["ram"]["used"] is None
    assert failed["status"] == "error" and failed["observed_at"] == 100
    assert failed["guests"] == first["guests"]
    cold = PveCollector(api).poll_once(now=200)
    assert cold["status"] == "error" and "guests" not in cold
    api.fail = False
    recovered = collector.poll_once(now=300)
    assert recovered["status"] == "ok" and recovered["observed_at"] == 300
    assert recovered["guests"] == first["guests"]


def test_guest_missing_or_empty_name_and_status_get_safe_fallbacks():
    class Api:
        config = config("https://pve.example:8006")
        def resources(self):
            return [
                {"type": "qemu", "vmid": 202, "node": "node-a", "name": "", "status": ""},
                {"type": "lxc", "vmid": 203, "node": "node-a"},
            ]
    guests = PveCollector(Api()).poll_once(now=100)["guests"]
    assert [(g["name"], g["status"]) for g in guests] == [("qemu-202", "unknown"), ("lxc-203", "unknown")]

    class ZeroCpuApi:
        config = config("https://pve.example:8006")
        def resources(self):
            return [{"type": "qemu", "vmid": 204, "node": "node-a", "maxcpu": 0}]
    assert PveCollector(ZeroCpuApi()).poll_once(now=100)["guests"][0]["vcpus"] is None

    class InvalidApi:
        config = config("https://pve.example:8006")
        def resources(self):
            return [{"type": "qemu", "vmid": 202, "node": "node-a", "name": 12, "status": "running"}]
    assert PveCollector(InvalidApi()).poll_once(now=100)["error"] == "invalid_inventory"


def test_oversize_inventory_reports_capacity_error_without_truncating():
    class Api:
        config = config("https://pve.example:8006")
        def resources(self):
            return [{"type": "lxc", "vmid": 100 + i, "node": "node-a", "name": "n" * 128,
                     "status": "stopped"} for i in range(1200)]
    snapshot = PveCollector(Api()).poll_once(now=100)
    assert snapshot["status"] == "error" and snapshot["error"] == "capacity_error"
    assert "guests" not in snapshot


def test_oversized_api_integer_is_rejected_and_poller_recovers():
    class Api:
        config = config("https://pve.example:8006")
        invalid = True
        def resources(self):
            return [{"type": "qemu", "vmid": 404, "node": "node-a", "maxmem": 10**1000}] if self.invalid else []
    api = Api()
    collector = PveCollector(api)
    rejected = collector.poll_once(now=100)
    assert rejected["error"] == "invalid_inventory" and "guests" not in rejected
    api.invalid = False
    recovered = collector.poll_once(now=200)
    assert recovered["status"] == "ok" and recovered["observed_at"] == 200 and recovered["guests"] == []


def test_background_polling_never_overlaps_requests():
    class Api:
        config = config("https://pve.example:8006")
        def __init__(self): self.active = 0; self.overlaps = 0; self.count = 0
        def resources(self):
            self.active += 1
            self.overlaps = max(self.overlaps, self.active)
            time.sleep(.02)
            self.active -= 1
            self.count += 1
            return []
    api = Api()
    collector = PveCollector(api, poll_interval=.01)
    collector.start()
    limit = time.monotonic() + 1
    while api.count < 3 and time.monotonic() < limit:
        time.sleep(.005)
    collector.stop()
    assert api.count >= 3 and api.overlaps == 1


def test_invalid_duplicate_or_nonfinite_inventory_is_atomic():
    class Api:
        def resources(self):
            return [
                {"type": "qemu", "vmid": 100, "node": "node-a", "name": "one", "status": "running", "cpu": math.inf},
                {"type": "qemu", "vmid": 100, "node": "node-a", "name": "two", "status": "running"},
            ]
    snapshot = PveCollector(Api()).poll_once(now=100)
    assert snapshot["status"] == "error"
    assert "guests" not in snapshot


def test_scope_requires_propagated_vm_audit_and_allows_a_proven_empty_node(monkeypatch):
    client = PveApiClient(config("https://pve.example:8006"))
    replies = {
        "nodes": [{"node": "node-a"}],
        "access/permissions": {"/": {"VM.Audit": True, "Sys.Audit": True}, "/vms/100": {"VM.Audit": 0}},
        "access/acl": [],
        "cluster/resources?type=vm": [],
    }
    monkeypatch.setattr(client, "_get", replies.__getitem__)
    assert client.resources() == []
    replies["access/acl"] = [{"path": "/vms/100", "roleid": "NoAccess"}]
    replies["access/permissions"].pop("/vms/100")
    with pytest.raises(PveError, match="vm_audit_scope_"):
        client.resources()
    replies["access/acl"] = []
    replies["access/permissions"] = {"/": {"VM.Audit": 0, "Sys.Audit": 1}}
    with pytest.raises(PveError, match="vm_audit_scope_missing"):
        client.resources()


def test_ram_stats_keep_source_bytes_and_allow_over_100_percent():
    class Api:
        config = config("https://pve.example:8006")
        def resources(self):
            return [{"type": "qemu", "vmid": 100, "node": "node-a", "name": "vm", "status": "running",
                     "mem": 12, "maxmem": 10}]
    guest = PveCollector(Api()).poll_once(now=100)["guests"][0]
    assert guest["memory"]["ram"] == {"used": 12, "total": 10, "percent": 120.0}


def test_installer_dry_run_reports_missing_private_configuration(monkeypatch, capsys):
    from hwmonitor_mqtt.platforms import install
    from hwmonitor_mqtt.platforms.detect import Target
    monkeypatch.setattr(install, "detect_target", lambda: Target(
        "linux", "pve", "native", "pve-node", "debian", ("pve-manager installed", "pveversion succeeded")))
    assert install.main(["--dry-run", "--pve-config", "/missing/pve.env"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["collector"].endswith("agent_sender_async")
    assert result["status"] == "PVE configuration is unconfigured"
    assert result["ready"] is False


def test_native_service_uses_existing_private_env_without_replacing_it(tmp_path):
    project = tmp_path / "project with space"
    project.mkdir()
    private_env = project / ".env"
    private_env.write_text("BROKER_HOST=broker.internal\nMQTT_PASS=private-value\n")
    before = private_env.read_bytes()
    unit = unit_text(project, config_file="/etc/hwmonitor-mqtt/pve.env")
    assert "WorkingDirectory=/opt/hwmonitor-mqtt-pve/app" in unit
    escaped_env = str(private_env).replace(" ", "\\x20")
    assert f"EnvironmentFile=-{escaped_env}" in unit
    assert 'EnvironmentFile=-/etc/hwmonitor-mqtt/pve.env' in unit
    assert "DynamicUser=yes" in unit and "SENDER_PROFILE=full" in unit and "PVE_ENABLED=1" in unit
    assert "ExecStart=/opt/hwmonitor-mqtt-pve/venv/bin/python" in unit
    assert private_env.read_bytes() == before


def test_installer_deploys_unprivileged_idempotent_service_and_restarts(tmp_path, monkeypatch):
    from hwmonitor_mqtt.platforms.installers import pve
    project = tmp_path / "source with space"
    sender = project / "hwmonitor_mqtt/agents/agent_sender_async.py"
    sender.parent.mkdir(parents=True)
    sender.write_text("# sender source\n")
    (project / ".env").write_text("BROKER_HOST=broker.internal\n")
    original_env = (project / ".env").read_bytes()
    install_root = tmp_path / "installed runtime"
    systemd_dir = tmp_path / "systemd"
    monkeypatch.setattr(pve, "APP_ROOT", str(install_root / "app"))
    monkeypatch.setattr(pve, "SERVICE_VENV", str(install_root / "venv"))
    monkeypatch.setattr(pve, "SYSTEMD_DIR", str(systemd_dir))
    calls = []
    def run(args, check=False):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)
    first_unit = None
    for iteration in range(2):
        assert pve.install(project, str(tmp_path / "missing-pve.env"), run=run,
                           which=lambda name: {"systemctl": "/usr/bin/systemctl", "python3": "/usr/bin/python3"}.get(name),
                           geteuid=lambda: 0) == 0
        if iteration == 0:
            first_unit = (systemd_dir / pve.SERVICE_NAME).read_text()
    unit = systemd_dir / pve.SERVICE_NAME
    text = unit.read_text()
    assert text == first_unit
    escaped_app = str(install_root / "app").replace(" ", "\\x20")
    assert "DynamicUser=yes" in text and f"WorkingDirectory={escaped_app}" in text
    assert 'EnvironmentFile=-' + str(project / ".env").replace(" ", "\\x20") in text
    assert "EnvironmentFile=-" + str(tmp_path / "missing-pve.env") in text
    deployed_sender = install_root / "app/hwmonitor_mqtt/agents/agent_sender_async.py"
    assert deployed_sender.is_file()
    assert stat.S_IMODE(deployed_sender.stat().st_mode) == 0o644
    import_env = os.environ.copy()
    import_env["PYTHONPATH"] = str(install_root / "app")
    imported = subprocess.run(
        [sys.executable, "-c", "from hwmonitor_mqtt.agents import agent_sender_async; print(agent_sender_async.__name__)"],
        cwd=install_root / "app", env=import_env, capture_output=True, text=True,
    )
    assert imported.returncode == 0, imported.stderr
    assert imported.stdout.strip() == "hwmonitor_mqtt.agents.agent_sender_async"
    assert (project / ".env").read_bytes() == original_env
    assert sum(call[-2:] == ["restart", pve.SERVICE_NAME] for call in calls) == 2
    assert calls.count(["/usr/bin/systemctl", "enable", "--now", pve.SERVICE_NAME]) == 2


def test_installer_refuses_nonroot_before_deploying(tmp_path, monkeypatch):
    from hwmonitor_mqtt.platforms.installers import pve
    sender = tmp_path / "source/hwmonitor_mqtt/agents/agent_sender_async.py"
    sender.parent.mkdir(parents=True)
    sender.write_text("# sender source\n")
    install_root = tmp_path / "installed"
    monkeypatch.setattr(pve, "APP_ROOT", str(install_root / "app"))
    with pytest.raises(RuntimeError, match="sudo"):
        pve.install(sender.parents[2], str(tmp_path / "missing.env"), run=lambda *a, **k: None,
                    which=lambda name: {"systemctl": "/usr/bin/systemctl", "python3": "/usr/bin/python3"}.get(name),
                    geteuid=lambda: 1000)
    assert not install_root.exists()


def test_generated_service_unit_parses_with_systemd_when_available(tmp_path, monkeypatch):
    systemd_analyze = shutil.which("systemd-analyze")
    if not systemd_analyze:
        pytest.skip("systemd-analyze is unavailable")
    from hwmonitor_mqtt.platforms.installers import pve
    app = tmp_path / "installed app"
    app.mkdir()
    monkeypatch.setattr(pve, "APP_ROOT", str(app))
    unit = tmp_path / "hwmonitor-mqtt-pve.service"
    unit.write_text(pve.unit_text(tmp_path / "source tree", "/usr/bin/true", tmp_path / "pve config.env"))
    result = subprocess.run([systemd_analyze, "verify", str(unit)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_native_pve_sender_keeps_host_payload_when_api_config_is_missing(monkeypatch, tmp_path):
    from hwmonitor_mqtt.agents import agent_sender_async as sender
    monkeypatch.setenv("PVE_ENABLED", "1")
    monkeypatch.setenv("PVE_CONFIG_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr(sender, "pve_collector", None)
    sender.metrics.update(cpu={"percent_total": 12}, memory={"ram": {"used": 1, "total": 2}},
                          system={"hostname": sender.HOSTNAME}, disk_io={}, temperatures={}, network_io={}, gpu=[])
    collector = sender.initialize_pve_collector()
    payload = sender.build_full_payload(now_ts=100)
    assert collector.snapshot()["status"] == "unconfigured"
    assert "guests" not in payload["virtualization"]


def test_malformed_pve_url_does_not_block_native_host_payload(monkeypatch):
    from hwmonitor_mqtt.agents import agent_sender_async as sender
    monkeypatch.setenv("PVE_ENABLED", "1")
    monkeypatch.setenv("PVE_CONFIG_LOADED", "1")
    monkeypatch.setenv("PVE_URL", "https://[")
    for key, value in {
        "PVE_TOKEN_ID": "monitor@pve!readonly", "PVE_TOKEN_SECRET": "secret-value",
        "PVE_CA_FILE": "/etc/ssl/certs/ca-certificates.crt", "PVE_CLUSTER_ID": "lab",
        "PVE_NODE": "node-a",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sender, "pve_collector", None)
    collector = sender.initialize_pve_collector()
    sender.metrics.update(cpu={"percent_total": 12}, memory={"ram": {"used": 1, "total": 2}},
                          system={"hostname": sender.HOSTNAME}, disk_io={}, temperatures={}, network_io={}, gpu=[])
    payload = sender.build_full_payload(now_ts=100)
    assert collector.snapshot()["error"] == "invalid_config"
    assert payload["cpu"]["percent_total"] == 12
    assert payload["cpu"]["percent_total"] == 12


def test_full_host_payload_keeps_base_metrics_when_guest_snapshot_exceeds_limit(monkeypatch):
    from hwmonitor_mqtt.agents import agent_sender_async as sender
    class Api:
        config = config("https://pve.example:8006")
        def resources(self):
            return [{"type": "lxc", "vmid": 100 + i, "node": "node-a", "name": "guest",
                     "status": "stopped"} for i in range(400)]
    collector = PveCollector(Api())
    assert collector.poll_once(now=100)["status"] == "ok"
    monkeypatch.setattr(sender, "pve_collector", collector)
    sender.metrics.update(cpu={"percent_total": 12}, memory={"ram": {"used": 1, "total": 2}},
                          system={"hostname": sender.HOSTNAME}, disk_io={}, temperatures={"sample": "x" * 150000},
                          network_io={}, gpu=[])
    payload = sender.build_full_payload(now_ts=100)
    assert payload["cpu"]["percent_total"] == 12
    assert payload["temperatures"]["sample"] == "x" * 150000
    assert payload["virtualization"]["status"] == "error"
    assert payload["virtualization"]["error"] == "capacity_error"
    assert "guests" not in payload["virtualization"]
    assert len(json.dumps(payload, separators=(",", ":")).encode()) <= 262144


def test_slow_api_poll_does_not_block_host_payload(monkeypatch):
    from hwmonitor_mqtt.agents import agent_sender_async as sender
    entered, release = threading.Event(), threading.Event()
    class Api:
        config = config("https://pve.example:8006")
        def resources(self):
            entered.set()
            release.wait(2)
            return []
    collector = PveCollector(Api())
    monkeypatch.setattr(sender, "pve_collector", collector)
    sender.metrics.update(cpu={"percent_total": 12}, memory={"ram": {"used": 1, "total": 2}},
                          system={"hostname": sender.HOSTNAME}, disk_io={}, temperatures={}, network_io={}, gpu=[])
    collector.start()
    assert entered.wait(1)
    started = time.monotonic()
    payload = sender.build_full_payload(now_ts=100)
    elapsed = time.monotonic() - started
    release.set()
    collector.stop()
    assert elapsed < .25
    assert payload["cpu"]["percent_total"] == 12
