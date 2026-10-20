import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "bootstrap.sh"


def make_tools(tmp_path, *, fail_download=False, fail_install=False, docker_present=True):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    log.write_text("")
    scripts = {
        "id": "#!/bin/sh\necho 0\n",
        "curl": f"#!/bin/sh\necho curl >> '{log}'\n" + ("exit 17\n" if fail_download else "while [ $# -gt 0 ]; do if [ \"$1\" = -o ]; then shift; touch \"$1\"; fi; shift; done\n"),
        "tar": "#!/bin/sh\nwhile [ $# -gt 0 ]; do if [ \"$1\" = -C ]; then shift; out=$1; fi; shift; done\nmkdir -p \"$out/hwmonitor-mqtt-master/hwmonitor_mqtt/platforms\"\nprintf '#!/bin/sh\\npython3 -m hwmonitor_mqtt.platforms.install\\n' > \"$out/hwmonitor-mqtt-master/install.sh\"\nprintf '' > \"$out/hwmonitor-mqtt-master/hwmonitor_mqtt/platforms/install.py\"\nmkdir -p \"$out/hwmonitor-mqtt-master/docs/configuration\"\nprintf '# PVE config template\\nPVE_TOKEN_SECRET=replace-me\\n' > \"$out/hwmonitor-mqtt-master/docs/configuration/pve.env.example\"\nprintf 'import os\\ndef detect_target():\\n    class T: system = os.environ.get(\"BOOTSTRAP_TARGET\", \"linux\")\\n    return T()\\n' > \"$out/hwmonitor-mqtt-master/hwmonitor_mqtt/platforms/detect.py\"\n",
        "python3": f"#!/bin/sh\nif [ \"$1\" = -m ]; then echo python:$* >> '{log}'; exit {23 if fail_install else 0}; fi\nexec /usr/bin/python3 \"$@\"\n",
        "apt-get": f"#!/bin/sh\necho apt-get:$* >> '{log}'\ncase \"$*\" in *docker.io*) printf '#!/bin/sh\\nexit 0\\n' > '{bindir}/docker'; chmod +x '{bindir}/docker' ;; esac\nexit 0\n",
        "apt-cache": "#!/bin/sh\necho Package: $2\n",
        "dpkg-query": "#!/bin/sh\nexit 1\n",
        "dpkg": "#!/bin/sh\necho amd64\n",
        **({"docker": "#!/bin/sh\nexit 0\n"} if docker_present else {}),
    }
    for name, body in scripts.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    if not docker_present:
        for name in ("bash", "find", "install", "mkdir", "rm", "mv", "chmod", "dirname", "mktemp", "grep"):
            (bindir / name).symlink_to(Path("/usr/bin") / name)
    return bindir, log


def run_bootstrap(tmp_path, *, stdin="broker.example\n1883\nuser\nsecret\n", target_system="linux", pve_config=True, **options):
    bindir, log = make_tools(tmp_path, **options)
    install_root = tmp_path / "opt" / "hwmonitor-mqtt"
    extra_env = {}
    if target_system == "pve":
        config = tmp_path / "pve.env"
        if pve_config:
            config.write_text("PVE_CA_FILE=/etc/ssl/certs/ca-certificates.crt\n")
        extra_env["PVE_CONFIG_FILE"] = str(config)
    result = subprocess.run(
        ["bash", str(BOOTSTRAP)], cwd=tmp_path, input=stdin, text=True,
        capture_output=True, check=False,
        env={**os.environ, **extra_env, "PATH": str(bindir) if not options.get("docker_present", True) else f"{bindir}:{os.environ['PATH']}", "HWMONITOR_BOOTSTRAP_ROOT": str(install_root), "BOOTSTRAP_TARGET": target_system},
    )
    return result, install_root, log


def test_downloads_prompts_once_and_installs_under_stable_root(tmp_path):
    result, root, log = run_bootstrap(tmp_path, stdin="broker.example\n1883\nuser\nsp ace/#$'\\pass\n")

    assert result.returncode == 0
    from dotenv import dotenv_values

    assert dotenv_values(root / ".env") == {
        "BROKER_HOST": "broker.example", "BROKER_PORT": "1883", "MQTT_USER": "user",
        "MQTT_PASS": "sp ace/#$'\\pass",
    }
    assert (root / ".env").stat().st_mode & 0o777 == 0o600
    assert "hwmonitor_mqtt.platforms.install" in log.read_text()
    assert "sp ace" not in log.read_text()

    env_before = (root / ".env").read_bytes()
    repeated, _, repeated_log = run_bootstrap(tmp_path, stdin="")
    assert repeated.returncode == 0
    assert (root / ".env").read_bytes() == env_before
    assert "\ncurl\n" not in repeated_log.read_text()


def test_download_failure_stops_before_install(tmp_path):
    result, root, log = run_bootstrap(tmp_path, fail_download=True)

    assert result.returncode != 0
    assert not root.exists()
    assert "python:" not in log.read_text()



def test_pve_first_run_creates_private_template_and_stops(tmp_path):
    result, root, log = run_bootstrap(tmp_path, target_system="pve", pve_config=False, stdin="")

    config = tmp_path / "pve.env"
    assert result.returncode == 2
    assert "PVE_TOKEN_SECRET" in config.read_text()
    assert config.stat().st_mode & 0o777 == 0o600
    assert "read-only API token" in result.stderr
    assert "python:" not in log.read_text()
    assert not (root / ".env").exists()


def test_docker_and_compose_are_installed_when_missing(tmp_path):
    result, _, log = run_bootstrap(tmp_path, docker_present=False)

    assert result.returncode == 0
    assert "apt-get:install -y docker.io docker-compose-v2" in log.read_text()


def test_existing_installer_failure_is_forwarded(tmp_path):
    result, _, _ = run_bootstrap(tmp_path, fail_install=True)

    assert result.returncode == 23


def test_pve_writes_systemd_escaped_password_value(tmp_path):
    password = "sp ace/#$'\"\\pass"
    result, root, _ = run_bootstrap(tmp_path, target_system="pve", stdin=f"pve.example\n8006\nuser\n{password}\n")

    assert result.returncode == 0
    expected = '"' + password.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'
    assert f"MQTT_PASS={expected}" in (root / ".env").read_text()
    if not shutil.which("systemd-run"):
        pytest.skip("systemd-run is unavailable")
    parsed = subprocess.run(
        ["systemd-run", "--user", "--pipe", "--wait", "--quiet", f"--property=EnvironmentFile={root / '.env'}",
         "/usr/bin/python3", "-c", "import os; print(os.environ['MQTT_PASS'])"],
        text=True, capture_output=True, check=False,
    )
    if parsed.returncode:
        pytest.skip("user systemd manager is unavailable")
    assert parsed.stdout.strip() == password


def test_refuses_unknown_install_directory_without_modifying_it(tmp_path):
    install_root = tmp_path / "opt" / "hwmonitor-mqtt"
    install_root.mkdir(parents=True)
    sentinel = install_root / "keep.txt"
    sentinel.write_text("user data")
    bindir, _ = make_tools(tmp_path)

    result = subprocess.run(
        ["bash", str(BOOTSTRAP)], text=True, capture_output=True, check=False,
        env={**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "HWMONITOR_BOOTSTRAP_ROOT": str(install_root)},
    )

    assert result.returncode != 0
    assert sentinel.read_text() == "user data"
    assert "apt-get:" not in (tmp_path / "calls.log").read_text()
