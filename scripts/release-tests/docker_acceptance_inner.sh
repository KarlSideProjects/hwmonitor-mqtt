#!/bin/bash
set -euo pipefail
image=$1 checksum=$2 source=$3 version=$4 platform=$5
cd /work
mkdir root control
chmod 755 root control
touch root/.hwmonitor-mqtt-bootstrap
printf 'BROKER_HOST="127.0.0.1"\nBROKER_PORT="18883"\nMQTT_USER=""\nMQTT_PASS=""\n' > root/.env
chmod 600 root/.env
mosquitto -p 18883 > /work/broker.log 2>&1 &
broker=$!
python3 /work/release_endpoint.py --archive /work/archive.tar.gz --sha256 "$checksum" --source "$source" --version "$version" --platform "$platform" > /work/endpoint.url 2>/work/endpoint.log &
endpoint=$!
(while :; do docker inspect sys-agent --format "{{.State.Status}} {{.State.ExitCode}} {{json .Config.Cmd}}" 2>/dev/null || :; docker logs sys-agent 2>&1 || :; sleep .1; done) > /work/runtime.log &
monitor=$!
trap 'kill "$monitor" "$broker" "$endpoint" 2>/dev/null || true; wait "$broker" "$endpoint" 2>/dev/null || true' EXIT
for i in $(seq 1 100); do [ -s endpoint.url ] && break; kill -0 "$endpoint"; sleep .1; done
base=$(cat endpoint.url)
[ -n "$base" ]
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
 [ "$(docker exec sys-agent /app/sender --version)" = "$version $source" ]
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
 timeout 50 mosquitto_sub -h 127.0.0.1 -p 18883 -t 'sys/agents/+/metrics' -C 1 > "/work/$1.json"
 python3 - "$1" <<'PY'
import json,sys
p=json.load(open('/work/'+sys.argv[1]+'.json'))
assert isinstance(p,dict) and p.get('host') and 'cpu' in p
if sys.argv[1]=='full': assert 'disk_smart' in p
else: assert 'disk_health' in p and 'disk_smart' not in p
PY
}
override full
install_sender
check_sender
capture full
sha256sum root/.env root/docker-compose.override.yml > before
install_sender
sha256sum -c before
check_sender
override esp
install_sender
check_sender
capture esp
sha256sum root/.env root/docker-compose.override.yml root/sender > rollback-before
old_image=$(docker inspect --format '{{.Image}}' sys-agent)
docker inspect --format 'before rollback: image={{.Image}} tag={{.Config.Image}} state={{.State.Status}}' sys-agent
docker image ls --no-trunc
touch control/fail
if install_sender; then echo 'expected real start failure' >&2; exit 1; fi
[ ! -e control/fail ]
docker image ls --no-trunc
sha256sum -c rollback-before
check_sender
[ "$(docker inspect --format '{{.Image}}' sys-agent)" = "$old_image" ]
capture esp
docker stop sys-agent
old_image=$(docker inspect --format '{{.Image}}' sys-agent)
touch control/fail
if install_sender; then echo 'expected real stopped-state start failure' >&2; exit 1; fi
[ ! -e control/fail ]
sha256sum -c rollback-before
[ "$(docker inspect --format '{{.Image}} {{.State.Running}}' sys-agent)" = "$old_image false" ]
[ "$(docker ps -a --filter name=^/sys-agent$ -q | wc -l)" -eq 1 ]
printf 'source=%s\nversion=%s\nplatform=%s\narchive_sha256=%s\nbootstrap_sha256=%s\nrollback=passed\nstopped_rollback=passed\n' "$source" "$version" "$platform" "$checksum" "$(sha256sum bootstrap.sh | cut -d' ' -f1)" > state.txt
