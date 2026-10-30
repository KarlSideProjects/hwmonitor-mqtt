#!/bin/bash
set -euo pipefail
image=$1 checksum=$2 source=$3 version=$4 platform=$5 old_checksum=$6 old_source=$7 old_version=$8
cd /work
mkdir root control
chmod 755 root control
touch root/.hwmonitor-mqtt-bootstrap
printf 'BROKER_HOST="127.0.0.1"\nBROKER_PORT="18883"\nMQTT_USER=""\nMQTT_PASS=""\n' > root/.env
chmod 600 root/.env
mosquitto -p 18883 > /work/broker.log 2>&1 &
broker=$!
endpoint=
(while :; do docker inspect sys-agent --format "{{.State.Status}} {{.State.ExitCode}} {{json .Config.Cmd}}" 2>/dev/null || :; docker logs sys-agent 2>&1 || :; sleep .1; done) > /work/runtime.log &
monitor=$!
trap 'kill "$monitor" "$broker" ${endpoint:+"$endpoint"} 2>/dev/null || true; wait "$broker" ${endpoint:+"$endpoint"} 2>/dev/null || true' EXIT
start_release() {
 if [ -n "$endpoint" ]; then kill "$endpoint"; wait "$endpoint" || true; fi
 : > /work/endpoint.url
 python3 /work/release_endpoint.py --archive "$1" --sha256 "$2" --source "$3" --version "$4" --platform "$platform" > /work/endpoint.url 2>>/work/endpoint.log &
 endpoint=$!
 for i in $(seq 1 100); do [ -s endpoint.url ] && break; kill -0 "$endpoint"; sleep .1; done
 base=$(cat endpoint.url)
 [ -n "$base" ]
}
override() {
cat > root/docker-compose.override.yml <<CONFIG
services:
  sys_agent:
    build:
      context: .
      network: host
    volumes: !override
      - /work/control:/control
    cap_add: !reset []
    pid: !reset null
    restart: "no"
    entrypoint: ["/bin/sh", "-c"]
    command: ["if [ -f /control/fail ]; then rm /control/fail; exit 17; fi; exec /app/sender"]
    environment:
      SENDER_PROFILE: $1
    labels:
      acceptance: preserved
CONFIG
}
install_sender() {
 docker run --rm -i --network host -v /work:/work -v /var/run/docker.sock:/var/run/docker.sock -v /usr/local/bin/docker:/usr/local/bin/docker:ro -v /usr/local/libexec/docker/cli-plugins:/usr/local/libexec/docker/cli-plugins:ro -e HWMONITOR_BOOTSTRAP_ROOT=/work/root -e HWMONITOR_TEST_RELEASE_BASE="$base" "$image" bash -c 'rm -f /.dockerenv; exec bash /work/bootstrap.sh'
}
check_sender() {
 [ "$(docker exec sys-agent /app/sender --version)" = "$1 $2" ]
 [ "$(docker ps --filter name=^/sys-agent$ -q | wc -l)" -eq 1 ]
 [ -z "$(docker exec sys-agent find /app -type f -name "*.py" -print -quit)" ]
 docker inspect sys-agent > /work/inspect.json
 python3 - <<'PY'
import json
x=json.load(open('/work/inspect.json'))[0]
assert x['State']['Running']
assert all(m['Destination'] not in ('/dev', '/sys') for m in x['Mounts'])
assert not x['HostConfig']['CapAdd']
PY
}
capture() {
 timeout 50 mosquitto_sub -R -h 127.0.0.1 -p 18883 -t 'sys/agents/+/metrics' -C 1 > "/work/$2.json"
 python3 - "$1" "$2" <<'PY'
import json,sys
p=json.load(open('/work/'+sys.argv[2]+'.json'))
assert isinstance(p,dict) and p.get('host') and 'cpu' in p
if sys.argv[1]=='full': assert 'disk_smart' in p
else: assert 'disk_health' in p and 'disk_smart' not in p
PY
}
override full
start_release /work/old-archive.tar.gz "$old_checksum" "$old_source" "$old_version"
install_sender
check_sender "$old_version" "$old_source"
old_sender=$(sha256sum root/sender | cut -d' ' -f1)
capture full full-old
sha256sum root/.env root/docker-compose.override.yml root/sender > rollback-before
old_image=$(docker inspect --format '{{.Image}}' sys-agent)
docker inspect --format 'before rollback: image={{.Image}} tag={{.Config.Image}} state={{.State.Status}}' sys-agent
docker image ls --no-trunc
start_release /work/archive.tar.gz "$checksum" "$source" "$version"
touch control/fail
if install_sender; then echo 'expected real start failure' >&2; exit 1; fi
[ ! -e control/fail ]
docker image ls --no-trunc
sha256sum -c rollback-before
check_sender "$old_version" "$old_source"
[ "$(docker inspect --format '{{.Image}}' sys-agent)" = "$old_image" ]
capture full full-after-rollback
docker stop sys-agent
old_image=$(docker inspect --format '{{.Image}}' sys-agent)
touch control/fail
if install_sender; then echo 'expected real stopped-state start failure' >&2; exit 1; fi
[ ! -e control/fail ]
sha256sum -c rollback-before
[ "$(docker inspect --format '{{.Image}} {{.State.Running}}' sys-agent)" = "$old_image false" ]
[ "$(docker ps -a --filter name=^/sys-agent$ -q | wc -l)" -eq 1 ]
SENDER_HOSTNAME=$(hostname) docker compose --project-directory /work/root -f /work/root/docker-compose.linux.yml -f /work/root/docker-compose.override.yml --profile agent up --no-build -d
check_sender "$old_version" "$old_source"
sha256sum root/.env root/docker-compose.override.yml > before
install_sender
sha256sum -c before
check_sender "$version" "$source"
[ "$(sha256sum root/sender | cut -d' ' -f1)" != "$old_sender" ]
capture full full
override esp
install_sender
check_sender "$version" "$source"
capture esp esp
printf 'old_source=%s\nold_version=%s\nold_archive_sha256=%s\nold_sender_sha256=%s\nsource=%s\nversion=%s\nplatform=%s\narchive_sha256=%s\nbootstrap_sha256=%s\nrollback=passed\nstopped_rollback=passed\n' "$old_source" "$old_version" "$old_checksum" "$old_sender" "$source" "$version" "$platform" "$checksum" "$(sha256sum bootstrap.sh | cut -d' ' -f1)" > state.txt
