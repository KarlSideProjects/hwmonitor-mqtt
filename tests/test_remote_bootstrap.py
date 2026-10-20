import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / "bootstrap.sh"


def make_tools(tmp_path, *, fail_download=False, fail_install=False, docker_present=True, conflicting_packages=False,
               base_installed=False, missing_base_packages=(), fail_base_install=False, fail_update=False,
               existing_sender=False, docker_error=False, fail_pve_validation=False, fail_pve_token_setup=False):
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
        **({"docker": "#!/bin/sh\nif [ \"$1\" = inspect ]; then\n" + ("exit 0\n" if existing_sender else "echo \"Cannot connect to Docker daemon\" >&2; exit 1\n" if docker_error else "echo \"Error: No such object: sys-agent\" >&2; exit 1\n") + "fi\nexit 0\n"} if docker_present else {}),
    }
    scripts["pveum"] = (
        "#!/usr/bin/python3\nimport json, sys\n"
        f"with open({str(log)!r}, 'a') as log: log.write('pveum:' + ' '.join(sys.argv[1:]) + '\\n')\n"
        + ("sys.stderr.write('sensitive-pveum-error-secret'); sys.exit(42)\n" if fail_pve_token_setup else "")
        + "args=sys.argv[1:]\n"
        "if args[:3] == ['user','token','add']:\n"
        "    print(json.dumps({'value':'fixture-provisioned-secret','full-tokenid':args[3]+'!'+args[4], 'info':{'privsep':1}}))\n"
        "elif 'list' in args: print('[]')\n"
    )
    # Run the real PVE setup with a fake local pveum and network client.
    scripts["tar"] += (
        f"mkdir -p \"$out/hwmonitor-mqtt-master/hwmonitor_mqtt/clients\"\n"
        f"cp '{REPO}/hwmonitor_mqtt/platforms/pve_bootstrap.py' '{REPO}/hwmonitor_mqtt/platforms/pve_token.py' \"$out/hwmonitor-mqtt-master/hwmonitor_mqtt/platforms/\"\n"
        f"cp '{REPO}/hwmonitor_mqtt/clients/pve.py' \"$out/hwmonitor-mqtt-master/hwmonitor_mqtt/clients/\"\n"
        "printf 'PVE_URL=https://pve.example.invalid:8006\\nPVE_TOKEN_ID=monitor@pve!readonly\\nPVE_TOKEN_SECRET=replace-with-private-token-secret\\nPVE_CA_FILE=/etc/ssl/certs/ca-certificates.crt\\nPVE_CLUSTER_ID=lab\\nPVE_NODE=pve-node-a\\n' > \"$out/hwmonitor-mqtt-master/docs/configuration/pve.env.example\"\n"
    )
    validation = '(_ for _ in ()).throw(m.PveError("unauthorized"))' if fail_pve_validation else "True"
    helper_driver = (
        "import sys; from hwmonitor_mqtt.platforms import pve_bootstrap as m; "
        "real=m.preflight; Fake=type(\"Fake\",(),{\"__init__\":lambda self,config:None,"
        "\"validate_scope\":lambda self:" + validation + "}); "
        "m.preflight=lambda config:real(config,client_factory=Fake); "
        "sys.exit(m.main(sys.argv[3:]))"
    )
    scripts["python3"] = scripts["python3"].replace(
        'if [ "$1" = -m ];',
        f'if [ "$1" = -m ] && [ "$2" = hwmonitor_mqtt.platforms.pve_bootstrap ]; then echo preflight >> \"{log}\"; exec /usr/bin/python3 -c \'{helper_driver}\' "$@"; fi\nif [ "$1" = -m ];'
    )
    for name, body in scripts.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(0o755)
    if not docker_present:
        for name in ("bash", "find", "install", "mkdir", "rm", "mv", "chmod", "dirname", "mktemp", "grep", "cp"):
            (bindir / name).symlink_to(Path("/usr/bin") / name)
    return bindir, log


def run_bootstrap(tmp_path, *, stdin="broker.example\n1883\nuser\nsecret\n", target_system="linux", pve_config=True, **options):
    bindir, log = make_tools(tmp_path, **options)
    install_root = tmp_path / "opt" / "hwmonitor-mqtt"
    extra_env = {}
    if target_system == "pve":
        config = tmp_path / "pve.env"
        if pve_config:
            config.write_text("PVE_URL=https://pve.example:8006\nPVE_TOKEN_ID=real@pve!token\nPVE_TOKEN_SECRET=private-real-value\nPVE_CA_FILE=/etc/ssl/certs/ca-certificates.crt\nPVE_CLUSTER_ID=production\nPVE_NODE=pve\n")
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
    assert "curl" in repeated_log.read_text()


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



def test_pve_first_run_provisions_private_token_and_installs(tmp_path):
    result, root, log = run_bootstrap(tmp_path, target_system="pve", pve_config=False)
    config = tmp_path / "pve.env"
    assert result.returncode == 0, result.stderr
    assert "PVE_TOKEN_SECRET=fixture-provisioned-secret" in config.read_text()
    assert config.stat().st_mode & 0o777 == 0o600
    assert "--privsep 1" in log.read_text()
    assert "--roles PVEAuditor" in log.read_text()
    assert "python:" in log.read_text()
    assert "fixture-provisioned-secret" not in result.stdout + result.stderr + log.read_text()
    assert (root / ".env").exists()


def test_existing_pve_credentials_are_reused_without_local_account_changes(tmp_path):
    result, _, log = run_bootstrap(tmp_path, target_system="pve")
    assert result.returncode == 0, result.stderr
    assert "pveum:" not in log.read_text()
    assert "private-real-value" in (tmp_path / "pve.env").read_text()


def test_linux_install_does_not_provision_pve_identity(tmp_path):
    result, _, log = run_bootstrap(tmp_path)
    assert result.returncode == 0
    assert "pveum:" not in log.read_text()
    assert not (tmp_path / "pve.env").exists()


def test_pve_provision_failure_stops_before_mqtt_without_leaking_cli_output(tmp_path):
    result, root, log = run_bootstrap(tmp_path, target_system="pve", pve_config=False,
                                     stdin="", fail_pve_token_setup=True)
    assert result.returncode == 2
    assert "sensitive-pveum-error-secret" not in result.stdout + result.stderr
    assert "MQTT broker" not in result.stdout
    assert "python:" not in log.read_text()
    assert not (root / ".env").exists()


def test_reinstall_refreshes_code_but_preserves_private_and_custom_files(tmp_path):
    result, root, _ = run_bootstrap(tmp_path)
    assert result.returncode == 0
    private = root / ".env"
    private_before = (private.read_bytes(), private.stat().st_ino, private.stat().st_mode)
    override = root / "docker-compose.override.yml"
    override.write_text("services: {}\n")
    (root / "install.sh").write_text("#!/bin/sh\nexit 99\n")
    repeated, _, _ = run_bootstrap(tmp_path, stdin="")
    assert repeated.returncode == 0, repeated.stderr
    assert (private.read_bytes(), private.stat().st_ino, private.stat().st_mode) == private_before
    assert override.read_text() == "services: {}\n"


def test_failed_refresh_keeps_existing_source_and_credentials(tmp_path):
    first, root, _ = run_bootstrap(tmp_path)
    assert first.returncode == 0
    source_before = (root / "install.sh").read_bytes()
    private_before = (root / ".env").read_bytes()
    failed, _, _ = run_bootstrap(tmp_path, fail_download=True, stdin="")
    assert failed.returncode != 0
    assert (root / "install.sh").read_bytes() == source_before
    assert (root / ".env").read_bytes() == private_before


@pytest.mark.parametrize("options, message", [
    ({"existing_sender": True}, "sys-agent container exists"),
    ({"docker_error": True}, "could not verify"),
])
def test_pve_old_sender_check_stops_before_credentials_and_install(tmp_path, options, message):
    result, root, log = run_bootstrap(tmp_path, target_system="pve", stdin="", **options)
    assert result.returncode == 2
    assert message in result.stderr
    assert "MQTT broker" not in result.stdout
    assert "preflight" not in log.read_text()
    assert "python:" not in log.read_text()
    assert not (root / ".env").exists()


def test_pve_invalid_token_stops_before_mqtt_credentials_and_install(tmp_path):
    result, root, log = run_bootstrap(tmp_path, target_system="pve", stdin="", fail_pve_validation=True)
    assert result.returncode == 2
    assert "unauthorized" in result.stderr
    assert "private-real-value" not in result.stderr
    assert "MQTT broker" not in result.stdout
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
