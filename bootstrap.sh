#!/usr/bin/env bash
set -euo pipefail

repo=https://github.com/KarlSideProjects/hwmonitor-mqtt/archive/refs/heads/master.tar.gz
root=${HWMONITOR_BOOTSTRAP_ROOT:-/opt/hwmonitor-mqtt}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

if [[ $(id -u) -ne 0 ]]; then
    echo "Run this bootstrap as root, for example: sudo bash bootstrap.sh" >&2
    exit 1
fi
if [[ ! -r /etc/os-release ]]; then
    echo "Cannot identify Linux distribution; supported hosts are Debian, Ubuntu, and Raspberry Pi OS." >&2
    exit 1
fi
. /etc/os-release
case "${ID:-}" in debian|ubuntu|raspbian) ;; *) echo "Unsupported distribution: ${ID:-unknown}" >&2; exit 1 ;; esac

if [[ -e "$root" && ( ! -f "$root/.hwmonitor-mqtt-bootstrap" || ! -f "$root/install.sh" || ! -f "$root/hwmonitor_mqtt/platforms/install.py" ) ]]; then
    echo "Refusing to use existing unrecognized directory: $root" >&2
    exit 1
fi

base_packages=(ca-certificates curl python3 python3-venv tar)
missing_base_packages=()
for package in "${base_packages[@]}"; do
    package_status=$(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true)
    [[ "$package_status" == installed ]] || missing_base_packages+=("$package")
done
if ((${#missing_base_packages[@]})); then
    if ! apt-get update; then
        echo "Warning: APT index refresh failed; attempting only the required missing packages with existing signed metadata and cache." >&2
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing_base_packages[@]}"
fi
if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    echo "Python 3.11 or newer is required; upgrade Python and rerun." >&2
    exit 1
fi

if [[ -e "$root" ]]; then
    : # Reuse bootstrap-owned files without replacing them.
else
    archive=${HWMONITOR_BOOTSTRAP_ARCHIVE_URL:-$repo}
    curl --fail --location --silent --show-error "$archive" --output "$tmp/source.tar.gz"
    mkdir "$tmp/unpacked"
    tar -xzf "$tmp/source.tar.gz" -C "$tmp/unpacked"
    source_dir=$(find "$tmp/unpacked" -mindepth 1 -maxdepth 1 -type d -print -quit)
    [[ -n "$source_dir" && -f "$source_dir/install.sh" ]] || { echo "Downloaded archive did not contain the project." >&2; exit 1; }
    install -d -m 0755 "$(dirname "$root")"
    mv "$source_dir" "$root"
    : > "$root/.hwmonitor-mqtt-bootstrap"
fi

target_system=$(PYTHONPATH="$root" python3 -c 'from hwmonitor_mqtt.platforms.detect import detect_target; print(detect_target().system)')
if [[ "$target_system" == pve ]]; then
    pve_config=${PVE_CONFIG_FILE:-/etc/hwmonitor-mqtt/pve.env}
    if [[ ! -e "$pve_config" ]]; then
        install -d -m 0755 "$(dirname "$pve_config")"
        install -m 0600 "$root/docs/configuration/pve.env.example" "$pve_config"
        echo "PVE sender is not installed yet. Edit $pve_config with a read-only API token, install your trusted CA at its PVE_CA_FILE path, then rerun this bootstrap." >&2
        echo "Setup guide: https://github.com/KarlSideProjects/hwmonitor-mqtt/blob/master/README.md#在-linux-或-pve-安裝-sender" >&2
        exit 2
    fi
    pve_ca=$(python3 - "$pve_config" <<'PY'
import sys
from pathlib import Path
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    key, sep, value = line.partition("=")
    if sep and key.strip() == "PVE_CA_FILE":
        print(value.strip().strip("\"'"))
        break
PY
)
    if [[ -z "$pve_ca" || ! -f "$pve_ca" ]]; then
        echo "PVE sender is not installed yet. Install a trusted CA file at the PVE_CA_FILE path in $pve_config, then rerun this bootstrap." >&2
        echo "Setup guide: https://github.com/KarlSideProjects/hwmonitor-mqtt/blob/master/README.md#在-linux-或-pve-安裝-sender" >&2
        exit 2
    fi
fi
if [[ ! -e "$root/.env" ]]; then
    read -r -p 'MQTT broker host: ' broker_host
    [[ "$broker_host" =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Use a DNS name or IPv4 address for MQTT broker host." >&2; exit 1; }
    read -r -p 'MQTT broker port [1883]: ' broker_port
    broker_port=${broker_port:-1883}
    [[ "$broker_port" =~ ^[0-9]{1,5}$ ]] && (( broker_port > 0 && broker_port < 65536 )) || { echo "MQTT port must be between 1 and 65535." >&2; exit 1; }
    read -r -p 'MQTT username: ' mqtt_user
    [[ "$mqtt_user" != *$'\n'* ]] || { echo "Username must be one line." >&2; exit 1; }
    read -r -s -p 'MQTT password: ' mqtt_pass
    echo
    [[ "$mqtt_pass" != *$'\n'* ]] || { echo "Password must be one line." >&2; exit 1; }
    umask 077
    export broker_host broker_port mqtt_user mqtt_pass target_system
    python3 - "$root/.env" <<'PY'
import os, sys
from pathlib import Path

def quote(value):
    if os.environ["target_system"] == "pve":
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$") + '"'

Path(sys.argv[1]).write_text("\n".join((
    f"BROKER_HOST={quote(os.environ['broker_host'])}",
    f"BROKER_PORT={quote(os.environ['broker_port'])}",
    f"MQTT_USER={quote(os.environ['mqtt_user'])}",
    f"MQTT_PASS={quote(os.environ['mqtt_pass'])}",
)) + "\n", encoding="utf-8")
PY
    chmod 0600 "$root/.env"
    unset broker_host broker_port mqtt_user mqtt_pass
fi

if [[ "$target_system" == linux && ! -x "$(command -v docker || true)" ]]; then
    installed_packages=$(dpkg-query -W -f='${db:Status-Status}\n' containerd.io containerd docker-ce docker-ce-cli moby-engine runc 2>/dev/null || true)
    if grep -qx installed <<<"$installed_packages"; then
        echo "A conflicting container runtime package is installed; resolve it before installing Docker." >&2
        exit 1
    fi
    if apt-cache show docker.io 2>/dev/null | grep -q '^Package: docker.io$' && apt-cache show docker-compose-v2 2>/dev/null | grep -q '^Package: docker-compose-v2$'; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2
    else
        case "$ID" in debian|raspbian) docker_repo=debian ;; ubuntu) docker_repo=ubuntu ;; esac
        if [[ ! -n "${VERSION_CODENAME:-}" || ! "$VERSION_CODENAME" =~ ^[a-z0-9]+$ ]]; then
            echo "Cannot select a Docker package repository for this release; install Docker Engine with Compose v2, then rerun." >&2
            exit 1
        fi
        DEBIAN_FRONTEND=noninteractive apt-get install -y gpg
        install -d -m 0755 /etc/apt/keyrings
        curl --fail --location --silent --show-error "https://download.docker.com/linux/$docker_repo/gpg" --output "$tmp/docker.gpg.asc"
        gpg --dearmor --yes --output /etc/apt/keyrings/docker.gpg "$tmp/docker.gpg.asc"
        chmod 0644 /etc/apt/keyrings/docker.gpg
        printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' "$(dpkg --print-architecture)" "$docker_repo" "$VERSION_CODENAME" > /etc/apt/sources.list.d/docker.list
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    fi
fi
if [[ "$target_system" == linux ]] && ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 is unavailable; install the Compose plugin and rerun." >&2
    exit 1
fi
bash "$root/install.sh"
if [[ "$target_system" == pve ]]; then
    echo "PVE sender service installed. VM and container data depends on a valid read-only API token and trusted CA in $pve_config."
fi
