#!/usr/bin/env python3
"""Provision an isolated PVE guest and run compiled sender acceptance."""
import argparse
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import uuid

HERE = Path(__file__).resolve().parent
GUEST_EVIDENCE = ('full.ndjson', 'denied.ndjson', 'recovered.ndjson', 'esp.ndjson',
                  'rollback-before', 'preserved', 'endpoint.log', 'direct-smart.txt',
                  'sender-pids', 'restored-old-full.ndjson', 'restored-old-esp.ndjson',
                  'fresh-new-full.ndjson', 'fresh-new-esp.ndjson', 'stopped-before', 'stopped-after')


def sanitize(text):
    text = re.sub(r'-----BEGIN [^\n]*PRIVATE KEY-----.*?(?:-----END [^\n]*PRIVATE KEY-----|\Z)', '[REDACTED KEY]', text, flags=re.S)
    text = re.sub(r'(?im)^.*(?:MQTT_PASS|PVE_TOKEN_SECRET|Authorization).*$', '[REDACTED CREDENTIAL LINE]', text)
    # The sandbox generates a 48-hex MQTT password and Proxmox UUID token secrets.
    text = re.sub(r'(?i)\b[0-9a-f]{48}\b|\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', '[REDACTED]', text)
    return text


def retain_diagnostics(out):
    dest = out / 'diagnostics'
    dest.mkdir(mode=0o700)
    shutil.copyfile(out / 'inputs.json', dest / 'inputs.json')
    (dest / 'run.log').write_text(sanitize((out / 'run.log').read_text(errors='replace')))
    if not (out / 'guest-evidence.tar').exists():
        return
    try:
        with tarfile.open(out / 'guest-evidence.tar') as source, tarfile.open(dest / 'guest-evidence.tar', 'w') as target:
            for member in source:
                if member.name not in GUEST_EVIDENCE or not member.isfile() or member.size > 8 * 1024 * 1024:
                    continue
                data = sanitize(source.extractfile(member).read().decode('utf-8', errors='replace')).encode()
                safe = tarfile.TarInfo(member.name)
                safe.size, safe.mode = len(data), 0o600
                target.addfile(safe, io.BytesIO(data))
    except tarfile.TarError:
        (dest / 'guest-evidence.tar').unlink(missing_ok=True)
        with (dest / 'run.log').open('a') as log:
            log.write('\nGuest evidence archive unavailable or incomplete.\n')


def verify_file(path, expected, algorithm, label):
    h = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    if h.hexdigest() != expected:
        raise ValueError(f'{label} checksum mismatch')


def guest_config(name, public_key):
    return ('#cloud-config\nhostname: pve-sandbox\nfqdn: pve-sandbox.local\nmanage_etc_hosts: false\n'
            'write_files:\n  - path: /etc/hwmonitor-pve-acceptance-owner\n    permissions: \"0600\"\n    content: ' + name + '\n'
            'users:\n  - name: debian\n    groups: sudo\n    shell: /bin/bash\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    ssh_authorized_keys:\n      - ' + public_key + '\nssh_pwauth: false\n')


def cleanup(actions):
    errors = []
    for name, action in actions:
        try:
            action()
        except Exception as exc:
            errors.append(f'{name}: {exc}')
    return errors


def main():
    p = argparse.ArgumentParser()
    for option in ('bootstrap', 'archive', 'old-archive', 'cloud-image', 'output'):
        p.add_argument('--' + option, required=True, type=Path)
    for option in ('bootstrap-sha256', 'sha256', 'source', 'version', 'old-sha256', 'old-source', 'old-version', 'cloud-image-sha512'):
        p.add_argument('--' + option, required=True)
    a = p.parse_args()
    for value, pattern in ((a.sha256, r'[0-9a-f]{64}'), (a.old_sha256, r'[0-9a-f]{64}'), (a.bootstrap_sha256, r'[0-9a-f]{64}'), (a.cloud_image_sha512, r'[0-9a-f]{128}'), (a.source, r'[0-9a-f]{40}'), (a.old_source, r'[0-9a-f]{40}'), (a.version, r'[0-9]+\.[0-9]+\.[0-9]+'), (a.old_version, r'[0-9]+\.[0-9]+\.[0-9]+')):
        if not re.fullmatch(pattern, value):
            p.error('invalid immutable identity')
    if a.sha256 == a.old_sha256 or a.source == a.old_source or a.version == a.old_version:
        p.error('old and new archive, source, and version must be distinct')
    for path, digest, algorithm, label in ((a.bootstrap, a.bootstrap_sha256, 'sha256', 'bootstrap'), (a.archive, a.sha256, 'sha256', 'archive'), (a.old_archive, a.old_sha256, 'sha256', 'old archive'), (a.cloud_image, a.cloud_image_sha512, 'sha512', 'cloud image')):
        verify_file(path, digest, algorithm, label)
    a.output.mkdir(mode=0o700, exist_ok=False)
    out = a.output.resolve()
    (out / 'inputs.json').write_text(json.dumps({
        'bootstrap_sha256': a.bootstrap_sha256, 'cloud_image_sha512': a.cloud_image_sha512,
        'archive_sha256': a.sha256, 'source': a.source, 'version': a.version,
        'old_archive_sha256': a.old_sha256, 'old_source': a.old_source, 'old_version': a.old_version,
        'proxmox_key_sha256': '136673be77aba35dcce385b28737689ad64fd785a797e57897589aed08db6e45',
    }, indent=2) + '\n')
    name = 'sender-pve-' + uuid.uuid4().hex[:12]
    image = name + ':local'
    log = (out / 'run.log').open('w')
    port = None
    pid = None
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    def run(cmd, timeout=300, **kw):
        print('+', ' '.join(map(str, cmd)), file=log, flush=True)
        return subprocess.run(cmd, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, **kw)

    def capture(cmd, timeout=30):
        return subprocess.check_output(cmd, text=True, stderr=log, timeout=timeout).strip()

    def docker(*cmd, **kw):
        return run(['docker', *cmd], **kw)

    def ssh(command, timeout=300):
        return run(['ssh', '-i', str(out / 'id_ed25519'), '-o', 'StrictHostKeyChecking=accept-new', '-o', f'UserKnownHostsFile={out / "known_hosts"}', '-o', 'ConnectTimeout=5', '-p', str(port), 'debian@127.0.0.1', command], timeout=timeout)

    def copy(path):
        return run(['scp', '-q', '-i', str(out / 'id_ed25519'), '-o', 'StrictHostKeyChecking=accept-new', '-o', f'UserKnownHostsFile={out / "known_hosts"}', '-P', str(port), str(path), 'debian@127.0.0.1:/home/debian/'], timeout=300)

    def guest_evidence():
        if port is None:
            return
        with (out / 'guest-evidence.tar').open('wb') as evidence:
            subprocess.run(['ssh', '-i', str(out / 'id_ed25519'), '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={out / "known_hosts"}', '-o', 'ConnectTimeout=5', '-p', str(port), 'debian@127.0.0.1', 'sudo tar -C /home/debian -cf - --ignore-failed-read ' + ' '.join(GUEST_EVIDENCE)], stdout=evidence, stderr=log, timeout=30, check=True)
        subprocess.run(['docker', 'logs', name], stdout=log, stderr=log, timeout=30)

    def absent_container():
        subprocess.run(['docker', 'rm', '-f', name], stdout=log, stderr=log, timeout=60)
        if capture(['docker', 'ps', '-a', '--filter', f'name=^/{name}$', '--format', '{{.Names}}']):
            raise RuntimeError('container remains')
        if pid is not None and Path(f'/proc/{pid}').exists():
            raise RuntimeError('owned QEMU process remains')
        print('owned container and QEMU process absent', file=log, flush=True)

    def absent_preparation():
        subprocess.run(['docker', 'rm', '-f', name + '-prepare'], stdout=log, stderr=log, timeout=60)
        if capture(['docker', 'ps', '-a', '--filter', f'name=^/{name}-prepare$', '--format', '{{.Names}}']):
            raise RuntimeError('preparation container remains')
        print('owned preparation container absent', file=log, flush=True)

    def absent_image():
        subprocess.run(['docker', 'image', 'rm', image], stdout=log, stderr=log, timeout=60)
        if capture(['docker', 'image', 'ls', '--format', '{{.Repository}}:{{.Tag}}', image]):
            raise RuntimeError('image tag remains')
        print('owned image tag absent', file=log, flush=True)

    def absent_port():
        if port is not None:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', port))
            print('owned listener absent', file=log, flush=True)

    try:
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(out / 'id_ed25519')])
        pub = (out / 'id_ed25519.pub').read_text().strip()
        (out / 'user-data').write_text(guest_config(name, pub))
        (out / 'meta-data').write_text('instance-id: ' + name + '\nlocal-hostname: pve-sandbox\n')
        shutil.copyfile(a.cloud_image, out / 'base.qcow2')
        for source, target in ((a.bootstrap, 'bootstrap.sh'), (a.archive, 'archive.tar.gz'), (a.old_archive, 'old-archive.tar.gz'), (HERE / 'release_endpoint.py', 'release_endpoint.py'), (HERE / 'pve_guest.sh', 'pve_guest.sh'), (HERE / 'pve_provision.sh', 'pve_provision.sh')):
            shutil.copyfile(source, out / target)
        docker('build', '-t', image, '-f', str(HERE / 'Dockerfile.pve'), str(HERE), timeout=1200)
        run(['docker', 'image', 'inspect', image, '--format', '{{.Id}}'])
        docker('run', '--rm', '--name', name + '-prepare', image, 'qemu-system-x86_64', '--version')
        docker('run', '--rm', '--name', name + '-prepare', '-v', f'{out}:/work', image, 'sh', '-c', 'cd /work; qemu-img create -f qcow2 -F qcow2 -b base.qcow2 guest.qcow2 20G; qemu-img create -f qcow2 nvme.qcow2 128M; cloud-localds seed.img user-data meta-data')
        acceleration = ['--device', '/dev/kvm'] if os.access('/dev/kvm', os.R_OK | os.W_OK) else []
        cpu = ['-enable-kvm', '-cpu', 'host'] if acceleration else ['-accel', 'tcg', '-cpu', 'max']
        docker('run', '-d', '--name', name, *acceleration, '-p', '127.0.0.1::2222', '-v', f'{out}:/work', '-w', '/work', image, 'qemu-system-x86_64', *cpu, '-smp', '2', '-m', '3072', '-nographic', '-drive', 'file=guest.qcow2,if=virtio,format=qcow2', '-drive', 'file=seed.img,if=virtio,format=raw,readonly=on', '-drive', 'file=nvme.qcow2,if=none,id=nvme0,format=qcow2', '-device', 'nvme,drive=nvme0,serial=HWMONITOR-DISPOSABLE', '-netdev', 'user,id=n1,hostfwd=tcp:0.0.0.0:2222-:22', '-device', 'virtio-net-pci,netdev=n1')
        pid = int(capture(['docker', 'inspect', '--format', '{{.State.Pid}}', name]))
        port = int(capture(['docker', 'port', name, '2222/tcp']).rsplit(':', 1)[1])
        for attempt in range(180):
            try:
                ssh('true', timeout=10)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                time.sleep(2)
        else:
            raise RuntimeError('guest SSH did not become ready')
        ssh('sudo cloud-init status --wait', timeout=600)
        for filename in ('bootstrap.sh', 'archive.tar.gz', 'old-archive.tar.gz', 'release_endpoint.py', 'pve_guest.sh', 'pve_provision.sh'):
            copy(out / filename)
        ssh('sudo bash /home/debian/pve_provision.sh kernel', timeout=3600)
        try:
            ssh('sudo systemctl reboot', timeout=20)
        except subprocess.CalledProcessError as error:
            if error.returncode != 255:
                raise
        time.sleep(15)
        for attempt in range(240):
            try:
                ssh('test "$(uname -r | grep -c pve)" -eq 1', timeout=10)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                time.sleep(3)
        else:
            raise RuntimeError('PVE kernel did not boot')
        ssh('sudo bash /home/debian/pve_provision.sh packages', timeout=3600)
        ssh('sudo bash /home/debian/pve_guest.sh ' + ' '.join((a.old_sha256, a.old_source, a.old_version, a.sha256, a.source, a.version, name)), timeout=1800)
    finally:
        original = sys.exc_info()[1]
        errors = cleanup([('evidence', guest_evidence), ('container', absent_container), ('preparation', absent_preparation), ('image', absent_image), ('listener', absent_port)])
        for error in errors:
            print('cleanup unresolved:', error, file=log, flush=True)
        log.close()
        retain_diagnostics(out)
        if errors and original is None:
            raise RuntimeError('cleanup failed; see private run.log')


if __name__ == '__main__':
    main()
