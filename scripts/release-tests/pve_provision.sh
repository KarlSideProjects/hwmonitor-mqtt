#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
if [ "$1" = kernel ]; then
  printf '127.0.0.1 localhost\n10.0.2.15 pve-sandbox.local pve-sandbox\n::1 localhost ip6-localhost ip6-loopback\n' > /etc/hosts
  printf 'grub-pc grub-pc/install_devices multiselect /dev/vda\n' | debconf-set-selections
  cat > /etc/apt/sources.list.d/pve-install-repo.sources <<'CONFIG'
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: trixie
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
CONFIG
  curl -fsSL https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg -o /usr/share/keyrings/proxmox-archive-keyring.gpg
  echo '136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45  /usr/share/keyrings/proxmox-archive-keyring.gpg' | sha256sum -c -
  apt-get update
  sha256sum /var/lib/apt/lists/*InRelease
  apt-get -y -o Dpkg::Options::=--force-confold full-upgrade
  apt-get install -y proxmox-default-kernel
elif [ "$1" = packages ]; then
  uname -r | grep -q pve
  printf 'postfix postfix/mailname string pve-sandbox.local\npostfix postfix/main_mailer_type select Local only\n' | debconf-set-selections
  apt-get install -y proxmox-ve postfix open-iscsi chrony smartmontools mosquitto mosquitto-clients jq
  mkdir -p /etc/systemd/resolved.conf.d
  printf '[Resolve]\nDomains=local\n' > /etc/systemd/resolved.conf.d/pve-test.conf
  systemctl restart systemd-resolved
  iface=$(ip route show default | awk 'NR==1 {print $5}')
  test -n "$iface"
  resolvectl domain "$iface" local
  test "$(hostname --fqdn)" = pve-sandbox.local
  pvecm updatecerts --force
  systemctl restart pveproxy
  openssl x509 -in /etc/pve/local/pveproxy-ssl.pem -noout -checkhost pve-sandbox.local | grep -q 'does match certificate'
  dpkg-query -W proxmox-ve pve-manager proxmox-default-kernel
  for unit in pve-cluster pveproxy mosquitto; do systemctl is-active --quiet "$unit"; done
  pveversion
else
  exit 1
fi
