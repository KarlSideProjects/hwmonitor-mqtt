import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "bootstrap.sh"


def make_tools(tmp_path, *, fail_download=False, fail_install=False, docker_present=True, conflicting_packages=False,
               base_installed=False, missing_base_packages=(), fail_base_install=False, fail_update=False):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    log.write_text("")
    scripts = {
        "id": "#!/bin/sh\necho 0\n",
        "curl": f"#!/bin/sh\necho curl >> '{log}'\n" + ("exit 17\n" if fail_download else "while [ $# -gt 0 ]; do if [ \"$1\" = -o ]; then shift; touch \"$1\"; fi; shift; done\n"),
        "tar": "#!/bin/sh\nwhile [ $# -gt 0 ]; do if [ \"$1\" = -C ]; then shift; out=$1; fi; shift; done\nmkdir -p \"$out/hwmonitor-mqtt-master/hwmonitor_mqtt/platforms\"\nprintf '#!/bin/sh\\npython3 -m hwmonitor_mqtt.platforms.install\\n' > \"$out/hwmonitor-mqtt-master/install.sh\"\nprintf '' > \"$out/hwmonitor-mqtt-master/hwmonitor_mqtt/platforms/install.py\"\nmkdir -p \"$out/hwmonitor-mqtt-master/docs/configuration\"\nprintf '# PVE config template\\nPVE_TOKEN_SECRET=replace-me\\n' > \"$out/hwmonitor-mqtt-master/docs/configuration/pve.env.example\"\nprintf 'import os\\ndef detect_target():\\n    class T: system = os.environ.get(\"BOOTSTRAP_TARGET\", \"linux\")\\n    return T()\\n' > \"$out/hwmonitor-mqtt-master/hwmonitor_mqtt/platforms/detect.py\"\n",
        "python3": f"#!/bin/sh\nif [ \"$1\" = -m ]; then echo python:$* >> '{log}'; exit {23 if fail_install else 0}; fi\nexec /usr/bin/python3 \"$@\"\n",
        "apt-get": f"#!/bin/sh\necho apt-get:$* >> '{log}'\ncase \"$1\" in update) exit {100 if fail_update else 0} ;; install) case \"$*\" in *ca-certificates*|*python3-venv*|*tar*) exit {100 if fail_base_install else 0} ;; esac ;; esac\ncase \"$*\" in *docker.io*) printf '#!/bin/sh\\nexit 0\\n' > '{bindir}/docker'; chmod +x '{bindir}/docker' ;; esac\nexit 0\n",
        "apt-cache": "#!/bin/sh\necho Package: $2\n",
        "dpkg-query": (
            "#!/bin/sh\ncase \"$3\" in containerd.io) echo installed; exit 1 ;; esac\nexit 1\n" if conflicting_packages else
            "#!/bin/sh\necho installed\nexit 0\n" if base_installed else
            "#!/bin/sh\ncase \"$3\" in " + "|".join(missing_base_packages) + ") exit 1 ;; *) echo installed; exit 0 ;; esac\n"
            if missing_base_packages else "#!/bin/sh\nexit 1\n"
        ),
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
    assert (root / ".env").stat().st_mode & 0o777 == 0o600
    assert "MQTT_PASS=\"sp ace/#$$'\\\\pass\"" in (root / ".env").read_text()
    assert "hwmonitor_mqtt.platforms.install" in log.read_text()
    assert "sp ace" not in log.read_text()

    env_before = (root / ".env").read_bytes()
    repeated, _, repeated_log = run_bootstrap(tmp_path, stdin="")
    assert repeated.returncode == 0
    assert (root / ".env").read_bytes() == env_before
    assert "\ncurl\n" not in repeated_log.read_text()


@pytest.mark.parametrize("password", [
    "slash\\ and apostrophe ' with $ and #",
    "abc\\",
    "abc\\'tail",
    "literal ${} interpolation",
])
def test_linux_password_round_trips_through_real_compose(tmp_path, password):
    if not shutil.which("docker") or subprocess.run(["docker", "compose", "version"], capture_output=True).returncode:
        pytest.skip("Docker Compose v2 is unavailable")
    result, root, _ = run_bootstrap(tmp_path, stdin=f"broker.example\n1883\nuser\n{password}\n")
    assert result.returncode == 0

    parsed = subprocess.run(
        ["docker", "compose", "--env-file", str(root / ".env"), "-f", str(REPO / "docker-compose.linux.yml"), "config", "--environment"],
        cwd=tmp_path, env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "DOCKER_CONFIG": str(tmp_path), "SENDER_HOSTNAME": "bootstrap-test"}, text=True, capture_output=True, check=False,
    )
    assert parsed.returncode == 0
    assert f"MQTT_PASS={password}" in parsed.stdout


def test_download_failure_stops_before_install(tmp_path):
    result, root, log = run_bootstrap(tmp_path, fail_download=True)

    assert result.returncode != 0
    assert not root.exists()
    assert "python:" not in log.read_text()


def test_unrelated_refresh_failure_warns_but_cached_prerequisites_continue(tmp_path):
    result, _, log = run_bootstrap(tmp_path, fail_update=True)

    assert result.returncode == 0
    assert "apt-get:install -y ca-certificates curl python3 python3-venv tar" in log.read_text()
    assert "apt-get:update" in log.read_text()
    assert "warning" in result.stderr.lower()
    assert "python:" in log.read_text()


def test_prerequisite_install_failure_stops_before_download(tmp_path):
    result, root, log = run_bootstrap(tmp_path, fail_update=True, fail_base_install=True)

    assert result.returncode != 0
    assert not root.exists()
    assert "apt-get:install -y ca-certificates curl python3 python3-venv tar" in log.read_text()
    assert "python:" not in log.read_text()
    assert "curl\n" not in log.read_text()


def test_installed_base_prerequisites_skip_apt_entirely(tmp_path):
    result, _, log = run_bootstrap(tmp_path, base_installed=True)

    assert result.returncode == 0
    assert "apt-get:" not in log.read_text()


def test_installs_only_missing_base_prerequisites(tmp_path):
    result, _, log = run_bootstrap(tmp_path, missing_base_packages=("python3-venv",))

    assert result.returncode == 0
    calls = log.read_text()
    assert "apt-get:install -y python3-venv" in calls
    assert "apt-get:install -y ca-certificates curl python3 python3-venv tar" not in calls


@pytest.mark.parametrize("wget_fails,curl_fails,installer_exit", [
    (False, False, 0),
    (True, False, 0),
    (True, True, 0),
    (False, False, 37),
])
def test_readme_bootstrap_uses_downloader_fallback_safely(tmp_path, wget_fails, curl_fails, installer_exit):
    readme = (REPO / "README.md").read_text()
    section = readme.split("## 在 Linux 或 PVE 安裝 Sender", 1)[1]
    command = re.search(r"```bash\n(.*?)\n```", section, re.DOTALL).group(1)
    assert command == 'u=https://raw.githubusercontent.com/KarlSideProjects/hwmonitor-mqtt/refs/heads/master/bootstrap.sh; f=$(mktemp) && (wget -qO "$f" "$u" || curl -fsSL "$u" -o "$f") && bash "$f"'
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


def test_readme_windows_command_is_the_short_admin_installer():
    readme = (REPO / "README.md").read_text()
    section = readme.split("## Windows Sender 安裝與注意事項", 1)[1]
    command = re.search(r"```powershell\n(.*?)\n```", section, re.DOTALL).group(1)
    assert "系統管理員" in section
    assert command == "irm https://raw.githubusercontent.com/KarlSideProjects/hwmonitor-mqtt/refs/heads/master/bootstrap.ps1 -ErrorAction Stop | iex"



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


def test_partial_dpkg_query_result_still_detects_conflicting_runtime(tmp_path):
    result, _, log = run_bootstrap(tmp_path, docker_present=False, conflicting_packages=True)

    assert result.returncode != 0
    assert "conflicting container runtime" in result.stderr
    assert "apt-get:install -y docker.io" not in log.read_text()


def test_powershell_dotenv_guard_catches_empty_interpolation(tmp_path):
    source = (REPO / "bootstrap.ps1").read_text()
    match = re.search(r"\$user -match '([^']+)'", source)
    assert match and match.group(1) == r"\$\{[^}]*\}"
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell runtime is unavailable")
    script = f"$value='abc${{}}def'; if ($value -match '{match.group(1)}') {{ exit 0 }} else {{ exit 1 }}"
    result = subprocess.run([pwsh, "-NoProfile", "-Command", script], capture_output=True, check=False)
    assert result.returncode == 0


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
