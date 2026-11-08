#!/usr/bin/env bash
set -euo pipefail

# Begin prerequisites
ensure_source_prerequisites() {
    local target=$1 distro=$2 codename=$3 scratch=$4 status installed
    configure_docker_repository() {
        local repository architecture key=/etc/apt/keyrings/hwmonitor-docker.asc
        local sources=/etc/apt/sources.list.d/hwmonitor-docker.sources
        case "$distro" in ubuntu) repository=ubuntu ;; debian|raspbian) repository=debian ;; *) echo 'Unsupported Docker distribution.' >&2; return 1 ;; esac
        [[ $codename =~ ^[a-z0-9]+$ ]] || { echo 'Invalid distribution codename.' >&2; return 1; }
        architecture=$(dpkg --print-architecture) || return 1
        [[ $architecture == amd64 || $architecture == arm64 ]] || { echo 'Unsupported Docker architecture.' >&2; return 1; }
        [[ ! -e $key && ! -L $key && ! -e $sources && ! -L $sources ]] || {
            echo 'Existing Docker repository files need review before continuing.' >&2; return 1;
        }
        DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl || return 1
        install -d -m 0755 /etc/apt/keyrings || return 1
        curl --fail --location --silent --show-error "https://download.docker.com/linux/$repository/gpg" --output "$scratch/docker.asc" || return 1
        [[ -s "$scratch/docker.asc" ]] || { echo 'Docker signing key is empty.' >&2; return 1; }
        install -m 0644 "$scratch/docker.asc" "$key" || return 1
        printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: %s\n' \
            "$repository" "$codename" "$architecture" "$key" | tee "$sources" >/dev/null || {
                rm -f -- "$key" "$sources"; return 1;
            }
        apt-get update || { rm -f -- "$key" "$sources"; return 1; }
    }
    if [[ $target == pve ]]; then
        status=$(dpkg-query -W -f='${db:Status-Status}' python3-venv 2>/dev/null || true)
        if [[ $status != installed ]]; then
            apt-get update || echo 'Warning: using existing APT metadata.' >&2
            DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv || return 1
        fi
        return 0
    fi
    [[ $target == linux ]] || { echo 'Unsupported prerequisite target.' >&2; return 1; }
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then return 0; fi
    if ! command -v docker >/dev/null 2>&1; then
        installed=$(dpkg-query -W -f='${db:Status-Status}\n' containerd.io containerd docker-ce docker-ce-cli moby-engine runc 2>/dev/null || true)
        while IFS= read -r status; do
            [[ $status != installed ]] || { echo 'Conflicting container runtime; resolve it before installing Docker.' >&2; return 1; }
        done <<<"$installed"
        apt-get update || echo 'Warning: using existing APT metadata.' >&2
        if apt-cache show docker.io >/dev/null 2>&1 && apt-cache show docker-compose-v2 >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 || return 1
        elif apt-cache show docker-ce >/dev/null 2>&1 && apt-cache show docker-compose-plugin >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin || return 1
        else
            configure_docker_repository || return 1
            DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin || return 1
        fi
    else
        apt-get update || echo 'Warning: using existing APT metadata.' >&2
        if apt-cache show docker-compose-v2 >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2 || return 1
        elif apt-cache show docker-compose-plugin >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin || return 1
        else
            configure_docker_repository || return 1
            DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin || return 1
        fi
    fi
    command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 || {
        echo 'Docker Compose v2 is unavailable after installation.' >&2; return 1;
    }
}
# End prerequisites

root=${HWMONITOR_BOOTSTRAP_ROOT:-/opt/hwmonitor-mqtt}
if [[ $(id -u) -ne 0 ]]; then
    echo 'Run this installer as root.' >&2
    exit 1
fi
machine=$(uname -m)
case "$machine" in
    x86_64|aarch64) ;;
    *) echo 'Unsupported architecture.' >&2; exit 1 ;;
esac
[[ -r /etc/os-release ]] || { echo 'Cannot identify this operating system.' >&2; exit 1; }
. /etc/os-release
case "${ID:-}" in debian|ubuntu|raspbian) ;; *) echo 'Unsupported Linux distribution.' >&2; exit 1 ;; esac
codename=${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}
if ! command -v python3 >/dev/null 2>&1; then
    apt-get update || echo 'Warning: using existing APT metadata.' >&2
    DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
    echo 'Python 3.11 or newer is required.' >&2; exit 1;
}
[[ "$root" == /* ]] || { echo 'Installation path must be absolute.' >&2; exit 1; }
parent=$(dirname -- "$root")
[[ -d "$parent" && ! -L "$parent" && $(realpath -e -- "$parent") == "$parent" ]] || {
    echo 'Untrusted installation parent directory.' >&2; exit 1;
}
[[ $(stat -c %u "$parent") == 0 ]] || { echo 'Installation parent must be root-owned.' >&2; exit 1; }
parent_mode=$(stat -c %a "$parent")
(( (8#$parent_mode & 8#022) == 0 )) || { echo 'Installation parent must not be writable by others.' >&2; exit 1; }
if [[ -L "$root" || ( -e "$root" && ! -d "$root" ) ]]; then
    echo 'Untrusted installation directory.' >&2
    exit 1
fi
if [[ -e "$root" ]]; then
    marker="$root/.hwmonitor-mqtt-bootstrap"
    if [[ -L "$root/.env" || -L "$root/hwmonitor_mqtt" || -L "$root/hwmonitor_mqtt/platforms" ]]; then
        echo 'Untrusted installation files.' >&2
        exit 1
    fi
    pending="$root/.hwmonitor-mqtt-bootstrap-pending"
    if [[ -L "$marker" || -L "$pending" ]] ||
       [[ ! -f "$pending" && ( ! -f "$marker" ||
          ( ( -L "$root/sender" || ! -f "$root/sender" ) &&
            ( -L "$root/hwmonitor_mqtt/platforms/install.py" || ! -f "$root/hwmonitor_mqtt/platforms/install.py" ) ) ) ]]; then
        echo "Refusing to use existing unrecognized directory: $root" >&2
        exit 1
    fi
fi

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
if curl --fail --location --silent --show-error \
    https://api.github.com/repos/KarlSideProjects/hwmonitor-mqtt/commits/master \
    --output "$tmp/commit.json"; then
    commit=$(python3 -c 'import json,re,sys; s=json.load(open(sys.argv[1])).get("sha", ""); sys.exit("Invalid source commit") if not isinstance(s, str) or not re.fullmatch("[0-9a-f]{40}", s) else print(s)' "$tmp/commit.json")
else
    curl --fail --location --silent --show-error --max-filesize 10485760 \
        'https://github.com/KarlSideProjects/hwmonitor-mqtt.git/info/refs?service=git-upload-pack' \
        --output "$tmp/refs"
    commit=$(python3 -c 'import pathlib,re,sys; b=pathlib.Path(sys.argv[1]).read_bytes(); m=[x.group(2).decode() for line in b.splitlines() if (x:=re.fullmatch(rb"([0-9a-f]{4})([0-9a-f]{40}) refs/heads/master",line)) and int(x.group(1),16)==len(line)+1]; sys.exit("Invalid source commit") if len(m)!=1 else print(m[0])' "$tmp/refs")
fi
curl --fail --location --silent --show-error \
    "https://raw.githubusercontent.com/KarlSideProjects/hwmonitor-mqtt/$commit/scripts/source_archive.py" \
    --output "$tmp/source_archive.py"
curl --fail --location --silent --show-error \
    "https://github.com/KarlSideProjects/hwmonitor-mqtt/archive/$commit.tar.gz" \
    --output "$tmp/source.tar.gz"
source_dir=$(python3 "$tmp/source_archive.py" "$tmp/source.tar.gz" "$tmp/unpacked")
[[ "$source_dir" == "$tmp/unpacked/hwmonitor-mqtt-$commit" && -f "$source_dir/scripts/source_linux_install.py" ]] || {
    echo 'Downloaded source identity is invalid.' >&2; exit 1;
}
target=$(cd "$source_dir" && PYTHONPATH="$source_dir" python3 -c 'from hwmonitor_mqtt.platforms.detect import detect_target; t=detect_target(); print(t.system if t.system == "pve" or (t.system == "linux" and t.distribution in ("debian", "ubuntu", "raspbian")) else "unsupported")')
[[ $target == linux || $target == pve ]] || { echo 'Unsupported or unrecognized host.' >&2; exit 1; }
[[ $target != pve || $machine == x86_64 ]] || { echo 'PVE requires x64.' >&2; exit 1; }
if [[ ! -e "$root" ]]; then
    mkdir -m 0755 "$root"
    (set -o noclobber; : > "$root/.hwmonitor-mqtt-bootstrap-pending")
fi
[[ ! -L "$root" && -d "$root" && $(stat -c %u "$root") == 0 ]] || { echo 'Untrusted installation directory.' >&2; exit 1; }
root_mode=$(stat -c %a "$root")
(( (8#$root_mode & 8#022) == 0 )) || { echo 'Installation directory must not be writable by others.' >&2; exit 1; }
lock="$root/.sender-install.lock"
[[ ! -L "$lock" && ( ! -e "$lock" || ( -f "$lock" && $(stat -c %u "$lock") == 0 ) ) ]] || {
    echo 'Untrusted installation lock.' >&2; exit 1;
}
exec 9>>"$lock"
flock -n 9 || { echo 'Another installation is running.' >&2; exit 1; }
ensure_source_prerequisites "$target" "$ID" "$codename" "$tmp"
if [[ $target == pve ]]; then
    if command -v docker >/dev/null 2>&1; then
        if docker inspect sys-agent >/dev/null 2>"$tmp/docker-error"; then
            echo 'Existing Docker sender must be migrated before installing native PVE.' >&2; exit 1
        elif ! grep -qi 'No such object' "$tmp/docker-error"; then
            echo 'Cannot verify existing Docker sender.' >&2; exit 1
        fi
    fi
    pve_config=${PVE_CONFIG_FILE:-/etc/hwmonitor-mqtt/pve.env}
    (cd "$source_dir" && PYTHONPATH="$source_dir" python3 -m hwmonitor_mqtt.platforms.pve_bootstrap \
        --config "$pve_config" --template "$source_dir/docs/configuration/pve.env.example")
fi
(cd "$source_dir" && PYTHONPATH="$source_dir" python3 -m scripts.source_linux_install "$source_dir" "$root" "$commit" "$target")
rm -f -- "$root/.hwmonitor-mqtt-bootstrap-pending"
echo "Sender source $commit installed."
