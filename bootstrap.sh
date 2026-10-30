#!/usr/bin/env bash
set -euo pipefail

[[ $(uname -m) == x86_64 ]] || { echo 'Unsupported architecture; Linux x64 is required.' >&2; exit 1; }
[[ $(id -u) == 0 ]] || { echo 'Run this installer as root.' >&2; exit 1; }
for tool in curl jq tar sha256sum runuser od flock; do
    command -v "$tool" >/dev/null || { echo "Install the required tool: $tool" >&2; exit 1; }
done
root=${HWMONITOR_BOOTSTRAP_ROOT:-/opt/hwmonitor-mqtt}
if [[ -L $root || ( -e $root && ( ! -d $root || ! -f $root/.hwmonitor-mqtt-bootstrap ) ) ]]; then
    echo "Refusing unrecognized installation directory: $root" >&2
    exit 1
fi

# Resolve one release and use only its named official assets.
repo=KarlSideProjects/hwmonitor-mqtt-releases
api="https://api.github.com/repos/$repo/releases/latest"
download_base="https://github.com/$repo/releases/download"
if [[ -n ${HWMONITOR_TEST_RELEASE_BASE:-} ]]; then
    [[ $HWMONITOR_TEST_RELEASE_BASE =~ ^http://127\.0\.0\.1:[0-9]+$ ]] || { echo "Invalid test release endpoint" >&2; exit 1; }
    api="$HWMONITOR_TEST_RELEASE_BASE/releases/latest"
    download_base="$HWMONITOR_TEST_RELEASE_BASE/releases/download"
fi
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
curl --fail --location --silent --show-error "$api" --output "$scratch/release.json"
tag=$(jq -er '.tag_name | select(type == "string")' "$scratch/release.json")
[[ $tag =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo 'Invalid release tag' >&2; exit 1; }
version=${tag#v}
for asset in version.json sender-linux-x64.tar.gz sender-linux-x64.tar.gz.sha256; do
    [[ $(jq -r --arg n "$asset" '[.assets[] | select(.name == $n)] | length' "$scratch/release.json") == 1 ]] || { echo "Missing release asset: $asset" >&2; exit 1; }
    url=$(jq -er --arg n "$asset" '.assets[] | select(.name == $n) | .browser_download_url' "$scratch/release.json")
    [[ $url == "$download_base/$tag/$asset" ]] || { echo 'Unexpected release asset URL' >&2; exit 1; }
    curl --fail --location --silent --show-error "$url" --output "$scratch/$asset"
done
jq -e --arg version "$version" 'type == "object" and (keys == ["source", "version"]) and .version == $version and (.source | type == "string" and test("^[0-9a-f]{40}$"))' "$scratch/version.json" >/dev/null || { echo 'Invalid release metadata' >&2; exit 1; }
source=$(jq -r '.source' "$scratch/version.json")
checksum_line=$(cat "$scratch/sender-linux-x64.tar.gz.sha256")
[[ $checksum_line =~ ^([0-9a-f]{64})\ \ sender-linux-x64\.tar\.gz$ ]] || { echo 'Invalid release checksum' >&2; exit 1; }
checksum=${BASH_REMATCH[1]}
archive=$scratch/sender-linux-x64.tar.gz
[[ $(stat -c %s "$archive") -le 134217728 && $(sha256sum "$archive" | cut -d' ' -f1) == "$checksum" ]] || { echo 'Archive checksum or size mismatch' >&2; exit 1; }

# Stream only the verified release's regular sender member, then let it validate the entire archive.
chmod 0711 "$scratch"
chmod 0444 "$archive"
mkdir "$scratch/worker"
chown nobody:nogroup "$scratch/worker"
chmod 0700 "$scratch/worker"
runuser -u nobody -- tar -tzf "$archive" --quoting-style=escape > "$scratch/names"
[[ $(grep -Fxc sender "$scratch/names") == 1 ]] || { echo 'Invalid sender entry count' >&2; exit 1; }
entry=$(runuser -u nobody -- tar -tvzf "$archive" sender --quoting-style=escape)
[[ $entry == -* && $entry != *$'\n'* ]] || { echo 'Invalid sender entry type' >&2; exit 1; }
runuser -u nobody -- bash -c 'ulimit -f 262144; exec tar -xOzf "$1" sender' _ "$archive" > "$scratch/sender"
[[ $(od -An -tx1 -N6 "$scratch/sender" | tr -d ' \n') == 7f454c460201 && $(od -An -tu2 -j18 -N2 "$scratch/sender" | tr -d ' ') == 62 ]] || { echo 'Sender architecture mismatch' >&2; exit 1; }
chmod 0555 "$scratch/sender"
runuser -u nobody -- "$scratch/sender" --stage "$archive" "$scratch/worker/staged" "$version" "$source" "$checksum"
[[ $(runuser -u nobody -- "$scratch/worker/staged/sender" --version) == "$version $source" ]] || { echo 'Sender version mismatch' >&2; exit 1; }

if command -v pveversion >/dev/null 2>&1 && dpkg-query -W -f='${Status}' pve-manager 2>/dev/null | grep -qx 'install ok installed' && pveversion | grep -q '^pve-manager/'; then
    target=pve
else
    target=linux
fi
pve_config=${PVE_CONFIG_FILE:-/etc/hwmonitor-mqtt/pve.env}
mkdir -p "$root"
exec 9>"$root/.sender-install.lock"
flock -n 9 || { echo "Another sender installation is running." >&2; exit 1; }
if [[ $target == pve ]]; then
    if command -v docker >/dev/null 2>&1; then
        if docker inspect sys-agent >/dev/null 2>"$scratch/docker-error"; then
            echo 'sys-agent container exists; stop and remove or rename it before installing the native sender.' >&2
            exit 2
        elif ! grep -q 'No such object' "$scratch/docker-error"; then
            echo 'Could not verify the existing Docker sender.' >&2
            exit 2
        fi
    fi
    "$scratch/worker/staged/sender" --pve-bootstrap --config "$pve_config" --template "$scratch/worker/staged/docs/configuration/pve.env.example"
else
    if ! command -v docker >/dev/null 2>&1; then
        installed=$(dpkg-query -W -f='${db:Status-Status}\n' containerd.io containerd docker-ce docker-ce-cli moby-engine runc 2>/dev/null || true)
        if grep -qx installed <<<"$installed"; then
            echo 'A conflicting container runtime is installed; resolve it before installing Docker.' >&2
            exit 1
        fi
        if apt-cache show docker.io 2>/dev/null | grep -q '^Package: docker.io$' && apt-cache show docker-compose-v2 2>/dev/null | grep -q '^Package: docker-compose-v2$'; then
            DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2
        else
            echo 'Install Docker Engine and Compose v2, then rerun.' >&2
            exit 1
        fi
    fi
    docker compose version >/dev/null 2>&1 || { echo 'Docker Compose v2 is required.' >&2; exit 1; }
fi
if [[ ! -e $root/.env ]]; then
    read -r -p 'MQTT broker host: ' broker_host
    [[ $broker_host =~ ^[A-Za-z0-9.-]+$ ]] || { echo 'Invalid MQTT broker host' >&2; exit 1; }
    read -r -p 'MQTT broker port [1883]: ' broker_port
    broker_port=${broker_port:-1883}
    [[ $broker_port =~ ^[0-9]{1,5}$ ]] && ((broker_port > 0 && broker_port < 65536)) || { echo 'Invalid MQTT port' >&2; exit 1; }
    read -r -p 'MQTT username: ' mqtt_user
    read -r -s -p 'MQTT password: ' mqtt_pass; echo
    quote() {
        local value=$1
        value=${value//\\/\\\\}; value=${value//\"/\\\"}
        if [[ $target == linux ]]; then value=${value//\$/\$\$}; else value=${value//\$/\\\$}; value=${value//\`/\\\`}; fi
        printf '"%s"' "$value"
    }
    umask 077
    { printf 'BROKER_HOST=%s\n' "$(quote "$broker_host")"; printf 'BROKER_PORT=%s\n' "$(quote "$broker_port")"; printf 'MQTT_USER=%s\n' "$(quote "$mqtt_user")"; printf 'MQTT_PASS=%s\n' "$(quote "$mqtt_pass")"; } > "$root/.env"
    unset broker_host broker_port mqtt_user mqtt_pass
fi

# Keep prior deployment inputs until the new service has started.
backup=$(mktemp -d "$root/.sender-rollback.XXXXXX")
files=(sender Dockerfile docker-compose.linux.yml docker-compose.pve.yml docs/configuration/pve.env.example)
[[ -e $root/.dockerignore ]] || files+=(.dockerignore)
for file in "${files[@]}"; do
    mkdir -p "$root/$(dirname "$file")" "$backup/$(dirname "$file")"
    if [[ -e $root/$file ]]; then cp -p "$root/$file" "$backup/$file"; else touch "$backup/$file.absent"; fi
done
old_image= old_ref= docker_attempted=0
if [[ $target == linux ]]; then
    if docker inspect sys-agent >/dev/null 2>"$scratch/docker-error"; then
        old_image=$(docker inspect --format '{{.Image}}' sys-agent)
        old_ref=$(docker inspect --format '{{.Config.Image}}' sys-agent)
    elif ! grep -q 'No such object' "$scratch/docker-error"; then
        echo 'Cannot verify the existing Docker sender.' >&2
        exit 1
    fi
fi
rollback() {
    local file
    for file in "${files[@]}"; do
        if [[ -e $backup/$file.absent ]]; then rm -f "$root/$file"; else cp -p "$backup/$file" "$root/$file"; fi
    done
    if [[ $target == linux && $docker_attempted == 1 ]]; then
        if [[ -n $old_image && -n $old_ref ]]; then
            docker tag "$old_image" "$old_ref" || return 1
            local compose=(-f "$root/docker-compose.linux.yml")
            [[ ! -f $root/docker-compose.override.yml ]] || compose+=(-f "$root/docker-compose.override.yml")
            SENDER_HOSTNAME=$(hostname) docker compose --project-directory "$root" "${compose[@]}" --profile agent up --no-build -d || return 1
        elif docker inspect sys-agent >/dev/null 2>&1; then
            docker rm -f sys-agent >/dev/null || return 1
        fi
    fi
}
transaction=1
finish() {
    local status=$?
    trap - EXIT
    if [[ $transaction == 1 && $status != 0 ]]; then
        if rollback; then rm -rf "$backup"; else echo "Rollback failed; backup retained at $backup" >&2; fi
    fi
    rm -rf "$scratch"
    exit "$status"
}
trap finish EXIT
for file in "${files[@]}"; do cp -p "$scratch/worker/staged/$file" "$root/$file"; done
docker_attempted=1
"$root/sender" --install --project-root "$root" --pve-config "$pve_config"
if [[ $target == linux ]]; then
    previous_state=
    for _ in 1 2 3; do
        sleep 1
        state=$(docker inspect --format '{{.State.Running}} {{.State.Pid}} {{.RestartCount}}' sys-agent)
        [[ $state =~ ^true\ ([1-9][0-9]*)\ ([0-9]+)$ ]] || { echo "Sender container did not remain running." >&2; exit 1; }
        [[ -z $previous_state || $state == "$previous_state" ]] || { echo "Sender container restarted during startup." >&2; exit 1; }
        previous_state=$state
    done
fi
touch "$root/.hwmonitor-mqtt-bootstrap"
transaction=0
rm -rf "$backup"
echo "Sender $version installed."
