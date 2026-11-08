#!/bin/bash
set -euo pipefail
trap 'status=$?; echo "source acceptance failed: line=$LINENO status=$status" >&2; exit "$status"' ERR
umask 077
cd /home/debian/source-fixture
# The marker is supplied by our cloud-init seed, never by an existing host.
test "$(cat /etc/hwmonitor-pve-acceptance-owner)" = "$1"
platform=$2
source=$(cat source)
case "$platform" in
    linux-arm64) test "$(uname -m)" = aarch64 ;;
    linux-x64|pve) test "$(uname -m)" = x86_64 ;;
    *) exit 1 ;;
esac
test ! -e /opt/hwmonitor-mqtt
test ! -e /opt/hwmonitor-mqtt-pve
if command -v docker >/dev/null; then ! docker inspect sys-agent >/dev/null 2>&1; fi
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y mosquitto mosquitto-clients python3-venv curl
python3 - <<'PY'
import secrets
from pathlib import Path
Path('password').write_text(secrets.token_hex(24))
PY
mosquitto_passwd -b -c /etc/mosquitto/passwd acceptance "$(cat password)"
chown mosquitto:mosquitto /etc/mosquitto/passwd
chmod 600 /etc/mosquitto/passwd
printf 'listener 1883 127.0.0.1\nallow_anonymous false\npassword_file /etc/mosquitto/passwd\n' > /etc/mosquitto/conf.d/acceptance.conf
systemctl restart mosquitto
mkdir -m 700 bin
printf '#!/bin/sh\nexec python3 /home/debian/source-fixture/source_fixture.py download "$@"\n' > bin/curl
chmod 700 bin/curl
export PATH="$PWD/bin:$PATH"
# Pass broker credentials through a private configuration file, not process arguments.
printf '%s\n' '-h 127.0.0.1' '-u acceptance' "-P $(cat password)" > mosquitto_sub
export XDG_CONFIG_HOME=$PWD
observe() {
    timeout 180 mosquitto_sub -t 'sys/agents/+/metrics' -C 1 > "$1"
    python3 - "$1" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
assert isinstance(payload, dict) and payload.get('host'), 'no sender MQTT payload'
assert 'cpu' in payload or 'cpu_percent' in payload, 'incomplete sender payload'
PY
}
python3 source_fixture.py first
observe first.json
test "$(cat /opt/hwmonitor-mqtt/.hwmonitor-mqtt-bootstrap)" = "$source"
sha256sum /opt/hwmonitor-mqtt/.env > preserved
if [ "$platform" = pve ]; then
    uname -r | grep -q pve
    pveversion
    systemctl is-active --quiet hwmonitor-mqtt-pve
    sha256sum /etc/hwmonitor-mqtt/pve.env >> preserved
else
    docker inspect sys-agent --format '{{.State.Running}}' | grep -qx true
    test "$(docker inspect sys-agent --format '{{.Config.Hostname}}')" = "$(hostname)"
fi
python3 source_fixture.py reinstall
sha256sum -c preserved
observe reinstalled.json
test "$(cat /opt/hwmonitor-mqtt/.hwmonitor-mqtt-bootstrap)" = "$source"
python3 - "$source" "$platform" <<'PY'
import json, sys
from pathlib import Path
Path('receipt.json').write_text(json.dumps({'source': sys.argv[1], 'platform': sys.argv[2],
    'first_install': True, 'reinstall': True, 'configuration_preserved': True,
    'mqtt_first': True, 'mqtt_reinstalled': True,
    'evidence_scope': 'committed source; download and prompt fixtures; real VM, installer and MQTT'}, indent=2) + '\n')
PY
