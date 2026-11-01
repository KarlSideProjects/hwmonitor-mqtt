#!/bin/bash
set -Eeuo pipefail
trap 'status=$?; printf "PVE acceptance failed: line=%s status=%s\n" "$LINENO" "$status" >&2' ERR
umask 077
cd /home/debian
old_sha=$1 old_source=$2 old_version=$3 new_sha=$4 new_source=$5 new_version=$6
test "$(hostname)" = pve-sandbox
systemd-detect-virt -q
test "$(cat /etc/hwmonitor-pve-acceptance-owner)" = "$7"
broker_pass=$(openssl rand -hex 24)
mosquitto_passwd -b -c /etc/mosquitto/acceptance.passwd acceptance "$broker_pass"
chown mosquitto:mosquitto /etc/mosquitto/acceptance.passwd
printf 'listener 1883 127.0.0.1\nallow_anonymous false\npassword_file /etc/mosquitto/acceptance.passwd\n' > /etc/mosquitto/conf.d/acceptance.conf
systemctl restart mosquitto
mkdir -p /opt/hwmonitor-mqtt
: > /opt/hwmonitor-mqtt/.hwmonitor-mqtt-bootstrap
printf 'BROKER_HOST="127.0.0.1"\nBROKER_PORT="1883"\nMQTT_USER="acceptance"\nMQTT_PASS="%s"\n' "$broker_pass" > /opt/hwmonitor-mqtt/.env
endpoint=''
trap 'if [ -n "$endpoint" ]; then kill "$endpoint" 2>/dev/null || :; wait "$endpoint" 2>/dev/null || :; fi' EXIT
start_release() {
  if [ -n "$endpoint" ]; then kill "$endpoint"; wait "$endpoint" || :; fi
  : > endpoint.url
  python3 release_endpoint.py --archive "$1" --sha256 "$2" --source "$3" --version "$4" --platform linux-x64 > endpoint.url 2> endpoint.log &
  endpoint=$!
  for i in $(seq 1 100); do [ -s endpoint.url ] && break; kill -0 "$endpoint"; sleep .1; done
  base=$(cat endpoint.url)
  test -n "$base"
}
install_sender() { HWMONITOR_TEST_RELEASE_BASE="$base" bash bootstrap.sh; }
check_sender() {
  [ "$(/opt/hwmonitor-mqtt-pve/app/sender --version)" = "$1 $2" ]
  systemctl is-active --quiet hwmonitor-mqtt-pve.service
  [ "$(systemctl show hwmonitor-mqtt-pve.service -p DynamicUser --value)" = yes ]
  [ -z "$(find /opt/hwmonitor-mqtt /opt/hwmonitor-mqtt-pve/app -type f -name '*.py' -print -quit)" ]
  [ "$(systemctl list-units --type=service --all --no-legend 'hwmonitor-mqtt-pve.service' | wc -l)" -eq 1 ]
  main_pid=$(systemctl show hwmonitor-mqtt-pve.service -p MainPID --value)
  [ "$main_pid" -gt 0 ]
  pgrep -f '^/opt/hwmonitor-mqtt-pve/app/sender$' > sender-pids
  [ "$(wc -l < sender-pids)" -eq 2 ]
  grep -qx "$main_pid" sender-pids
  for i in $(seq 1 30); do
    [ "$(ss -tn state established '( sport = :1883 )' | tail -n +2 | wc -l)" -eq 1 ] && break
    sleep 1
  done
  [ "$(ss -tn state established '( sport = :1883 )' | tail -n +2 | wc -l)" -eq 1 ]
}
capture() {
  timeout 90 mosquitto_sub -R -u acceptance -P "$broker_pass" -h 127.0.0.1 -t 'sys/agents/+/metrics' -C 3 > "$2.ndjson"
  python3 - "$1" "$2.ndjson" <<'PY'
import json,sys
rows=[json.loads(line) for line in open(sys.argv[2])]
assert len(rows)==3
assert all(d.get('host') and 'cpu' in d for d in rows)
if sys.argv[1]=='esp':
    assert any('disk_health' in d and 'disk_smart' not in d for d in rows)
elif sys.argv[1]=='unavailable':
    assert all(d['disk_smart']['status']=='unavailable' and not d['disk_smart']['devices'] for d in rows)
else:
    assert any(d.get('disk_smart',{}).get('devices') for d in rows)
PY
}
start_release old-archive.tar.gz "$old_sha" "$old_source" "$old_version"
install_sender
check_sender "$old_version" "$old_source"
old_executable=$(sha256sum /opt/hwmonitor-mqtt-pve/app/sender | cut -d' ' -f1)
mkdir -p /etc/systemd/system/hwmonitor-mqtt-pve.service.d
printf '[Service]\nRestartSec=7\n' > /etc/systemd/system/hwmonitor-mqtt-pve.service.d/70-acceptance.conf
systemctl daemon-reload
paths='/etc/hwmonitor-mqtt/pve.env /opt/hwmonitor-mqtt/.env /etc/systemd/system/hwmonitor-mqtt-pve.service /etc/systemd/system/hwmonitor-mqtt-pve.service.d/70-acceptance.conf /opt/hwmonitor-mqtt-pve/app/sender /opt/hwmonitor-mqtt/sender'
sha256sum $paths > rollback-before
start_release archive.tar.gz "$new_sha" "$new_source" "$new_version"
printf '[Service]\nExecStartPre=+/bin/sh -c '\''if test -e /run/hwmonitor-inject-failure; then rm /run/hwmonitor-inject-failure; exit 1; fi'\''\n' > /etc/systemd/system/hwmonitor-mqtt-pve.service.d/80-acceptance-failure.conf
systemctl daemon-reload
for state in active stopped; do
  if [ "$state" = stopped ]; then
    systemctl stop hwmonitor-mqtt-pve
    systemctl show hwmonitor-mqtt-pve -p ActiveState -p SubState -p MainPID > stopped-before
    [ "$(systemctl show hwmonitor-mqtt-pve -p ActiveState --value)" = inactive ]
    [ "$(systemctl show hwmonitor-mqtt-pve -p SubState --value)" = dead ]
    [ "$(systemctl show hwmonitor-mqtt-pve -p MainPID --value)" = 0 ]
  fi
  touch /run/hwmonitor-inject-failure
  if install_sender; then echo 'expected start failure' >&2; exit 1; fi
  [ ! -e /run/hwmonitor-inject-failure ]
  sha256sum -c rollback-before
  if [ "$state" = active ]; then
    check_sender "$old_version" "$old_source"
    capture full restored-old-full
    printf '[Service]\nEnvironment=SENDER_PROFILE=esp\n' > /etc/systemd/system/hwmonitor-mqtt-pve.service.d/99-acceptance-profile.conf
    systemctl daemon-reload
    systemctl restart hwmonitor-mqtt-pve
    capture esp restored-old-esp
    rm /etc/systemd/system/hwmonitor-mqtt-pve.service.d/99-acceptance-profile.conf
    systemctl daemon-reload
    systemctl restart hwmonitor-mqtt-pve
  else
    systemctl show hwmonitor-mqtt-pve -p ActiveState -p SubState -p MainPID > stopped-after
    cmp stopped-before stopped-after
    [ "$(systemctl show hwmonitor-mqtt-pve -p MainPID --value)" = 0 ]
  fi
done
rm /etc/systemd/system/hwmonitor-mqtt-pve.service.d/80-acceptance-failure.conf
systemctl daemon-reload
systemctl start hwmonitor-mqtt-pve
sha256sum /etc/hwmonitor-mqtt/pve.env /opt/hwmonitor-mqtt/.env /etc/systemd/system/hwmonitor-mqtt-pve.service /etc/systemd/system/hwmonitor-mqtt-pve.service.d/70-acceptance.conf > preserved
install_sender
sha256sum -c preserved
check_sender "$new_version" "$new_source"
[ "$(sha256sum /opt/hwmonitor-mqtt-pve/app/sender | cut -d' ' -f1)" != "$old_executable" ]
capture full full
service_uid=$(systemctl show hwmonitor-mqtt-pve.service -p MainPID --value)
service_uid=$(stat -c %u "/proc/$service_uid")
if setpriv --reuid "$service_uid" --regid "$service_uid" --clear-groups smartctl -a /dev/nvme0n1 > direct-smart.txt 2>&1; then echo "unexpected direct SMART access" >&2; exit 1; fi
grep -qi "permission denied" direct-smart.txt
python3 - <<'PY'
import ssl, urllib.request, json
cfg={}
for line in open('/etc/hwmonitor-mqtt/pve.env'):
    if '=' in line and not line.startswith('#'):
        k,v=line.strip().split('=',1); cfg[k]=v.strip('"\'')
ctx=ssl.create_default_context(cafile=cfg['PVE_CA_FILE'])
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
for path in ('/nodes/pve-sandbox/status','/nodes/pve-sandbox/disks/list','/nodes/pve-sandbox/disks/smart?disk=%2Fdev%2Fnvme0n1'):
    req=urllib.request.Request(cfg['PVE_URL']+'/api2/json'+path,headers={'Authorization':'PVEAPIToken='+cfg['PVE_TOKEN_ID']+'='+cfg['PVE_TOKEN_SECRET']})
    with opener.open(req,timeout=10) as response:
        assert response.status==200 and json.load(response)['data'] is not None
PY
token=$(sed -n 's/^PVE_TOKEN_ID=//p' /etc/hwmonitor-mqtt/pve.env | tr -d "'\"")
restore() { pveum acl modify / --tokens "$token" --roles PVEAuditor --propagate 1 >/dev/null; }
trap 'restore; kill "$endpoint" 2>/dev/null || :' EXIT
pveum acl delete / --tokens "$token" --roles PVEAuditor >/dev/null
systemctl restart hwmonitor-mqtt-pve
capture unavailable denied
restore
systemctl restart hwmonitor-mqtt-pve
capture full recovered
printf '[Service]\nEnvironment=SENDER_PROFILE=esp\n' > /etc/systemd/system/hwmonitor-mqtt-pve.service.d/99-acceptance-profile.conf
systemctl daemon-reload
systemctl restart hwmonitor-mqtt-pve
capture esp esp
# Reset only the guest created by this run; its owner marker was checked above.
systemctl disable --now hwmonitor-mqtt-pve.service
pveum user delete "${token%%!*}"
trap 'kill "$endpoint" 2>/dev/null || :' EXIT
rm -rf /opt/hwmonitor-mqtt /opt/hwmonitor-mqtt-pve /etc/hwmonitor-mqtt /etc/systemd/system/hwmonitor-mqtt-pve.service /etc/systemd/system/hwmonitor-mqtt-pve.service.d
systemctl daemon-reload
[ ! -e /opt/hwmonitor-mqtt/.env ]
[ ! -e /opt/hwmonitor-mqtt-pve/app ]
[ ! -e /etc/hwmonitor-mqtt/pve.env ]
[ ! -e /etc/systemd/system/hwmonitor-mqtt-pve.service ]
[ "$(systemctl show hwmonitor-mqtt-pve -p LoadState --value)" = not-found ]
printf '127.0.0.1\n1883\nacceptance\n%s\n' "$broker_pass" | install_sender
check_sender "$new_version" "$new_source"
capture full fresh-new-full
mkdir -p /etc/systemd/system/hwmonitor-mqtt-pve.service.d
printf '[Service]\nEnvironment=SENDER_PROFILE=esp\n' > /etc/systemd/system/hwmonitor-mqtt-pve.service.d/99-acceptance-profile.conf
systemctl daemon-reload
systemctl restart hwmonitor-mqtt-pve
capture esp fresh-new-esp
printf 'fresh PVE acceptance passed\n'
