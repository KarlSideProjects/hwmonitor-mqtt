#!/usr/bin/env python3
"""Run exact committed source bootstrap acceptance in a disposable full QEMU VM."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import uuid

from pve_acceptance import cleanup, guest_config, sanitize, verify_file

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def output_directory(path):
    path = Path(path).absolute()
    parent = path.parent
    if parent.resolve(strict=True) != parent or parent.stat().st_uid != os.geteuid() or parent.stat().st_mode & 0o022:
        raise ValueError('output parent must be owned, trusted and not writable by others')
    path.mkdir(mode=0o700, exist_ok=False)
    return path


def source_inputs(source, dest, run=subprocess.run):
    if not re.fullmatch('[0-9a-f]{40}', source):
        raise ValueError('source must be an exact committed SHA')
    run(['git', 'cat-file', '-e', source + '^{commit}'], cwd=ROOT, check=True, capture_output=True)
    run(['git', 'archive', '--format=tar.gz', '--prefix=hwmonitor-mqtt-' + source + '/',
         '--output=' + str(dest / 'source.tar.gz'), source], cwd=ROOT, check=True, capture_output=True)
    for source_path, name in (('bootstrap-source.sh', 'bootstrap-source.sh'),
                              ('scripts/source_archive.py', 'source_archive.py'), ('README.md', 'README.md')):
        result = run(['git', 'show', source + ':' + source_path], cwd=ROOT, check=True, capture_output=True)
        (dest / name).write_bytes(result.stdout)
    (dest / 'source').write_text(source + '\n')


def qemu_command(platform, disk, seed, port):
    arm = platform == 'linux-arm64'
    acceleration = ['-accel', 'kvm', '-cpu', 'host'] if not arm and os.access('/dev/kvm', os.R_OK | os.W_OK) else ['-accel', 'tcg', '-cpu', 'max']
    command = ['qemu-system-aarch64' if arm else 'qemu-system-x86_64', *acceleration,
               '-smp', '2', '-m', '3072', '-nographic', '-no-reboot']
    if arm:
        firmware = Path('/usr/share/qemu-efi-aarch64/QEMU_EFI.fd')
        if not firmware.is_file():
            raise RuntimeError('ARM64 full VM firmware unavailable; install qemu-efi-aarch64')
        command += ['-machine', 'virt', '-bios', str(firmware)]
    return command + ['-drive', f'file={disk},if=virtio,format=qcow2',
                      '-drive', f'file={seed},if=virtio,format=raw,readonly=on',
                      '-netdev', f'user,id=n1,hostfwd=tcp:127.0.0.1:{port}-:22',
                      '-device', 'virtio-net-pci,netdev=n1']


def retain_log(raw, target):
    with raw.open('rb') as stream:
        stream.seek(max(0, raw.stat().st_size - 1024 * 1024))
        text = stream.read().decode(errors='replace')
    target.write_text(sanitize(text))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--platform', required=True, choices=('linux-x64', 'linux-arm64', 'pve'))
    parser.add_argument('--cloud-image', required=True, type=Path)
    parser.add_argument('--cloud-image-sha512', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--retain-on-failure', action='store_true', help='retain private disks and keys after stopping VM; never upload these')
    args = parser.parse_args(argv)
    if not re.fullmatch('[0-9a-f]{40}', args.source) or not re.fullmatch('[0-9a-f]{128}', args.cloud_image_sha512):
        parser.error('invalid immutable source or cloud image identity')
    out = output_directory(args.output)
    scratch = Path(tempfile.mkdtemp(prefix='private-', dir=out))
    receipt = {'source': args.source, 'platform': args.platform,
               'cloud_image_sha512': args.cloud_image_sha512, 'status': 'failed',
               'evidence_scope': 'committed source; candidate downloads and prompts are fixtures; full QEMU VM',
               'harness_sha256': {filename: hashlib.sha256((HERE / filename).read_bytes()).hexdigest()
                   for filename in ('source_acceptance.py', 'source_guest.sh', 'source_fixture.py',
                                    'pve_provision.sh', 'pve_acceptance.py')}}
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    process = None
    log = (scratch / 'run.log').open('w')
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    def run(command, timeout=300, **kwargs):
        return subprocess.run(command, stdout=log, stderr=log, timeout=timeout, check=True, **kwargs)

    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    options = ['-i', str(scratch / 'id_ed25519'), '-o', 'StrictHostKeyChecking=accept-new',
               '-o', 'UserKnownHostsFile=' + str(scratch / 'known_hosts'), '-o', 'ConnectTimeout=5']

    def ssh(command, timeout=300):
        return run(['ssh', *options, '-p', str(port), 'debian@127.0.0.1', command], timeout)

    def ready(pve=False):
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError('QEMU exited before readiness; verify image architecture and host resources')
            try:
                ssh('uname -r | grep -q pve' if pve else 'true', 10)
                return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                time.sleep(3)
        raise TimeoutError('guest readiness timeout; insufficient hardware/time is a failure')

    def stop_vm():
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            if process.poll() is None:
                raise RuntimeError('owned QEMU process remains')

    try:
        verify_file(args.cloud_image, args.cloud_image_sha512, 'sha512', 'cloud image')
        source_inputs(args.source, scratch)
        receipt['archive_sha256'] = hashlib.sha256((scratch / 'source.tar.gz').read_bytes()).hexdigest()
        name = 'source-acceptance-' + uuid.uuid4().hex[:12]
        run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(scratch / 'id_ed25519')])
        (scratch / 'user-data').write_text(guest_config(name, (scratch / 'id_ed25519.pub').read_text().strip()))
        (scratch / 'meta-data').write_text('instance-id: ' + name + '\nlocal-hostname: pve-sandbox\n')
        run(['cloud-localds', str(scratch / 'seed.img'), str(scratch / 'user-data'), str(scratch / 'meta-data')])
        run(['qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(args.cloud_image.resolve()), str(scratch / 'guest.qcow2'), '24G'])

        def boot():
            return subprocess.Popen(qemu_command(args.platform, scratch / 'guest.qcow2', scratch / 'seed.img', port), stdout=log, stderr=log)

        process = boot()
        ready()
        ssh('sudo cloud-init status --wait', 1200)
        ssh('mkdir -m 700 /home/debian/source-fixture')
        files = [scratch / filename for filename in ('source', 'source.tar.gz', 'bootstrap-source.sh', 'source_archive.py', 'README.md')]
        files += [HERE / filename for filename in ('source_fixture.py', 'source_guest.sh', 'pve_provision.sh')]
        run(['scp', '-q', *options, '-P', str(port), *map(str, files), 'debian@127.0.0.1:/home/debian/source-fixture/'])
        if args.platform == 'pve':
            ssh('sudo bash /home/debian/source-fixture/pve_provision.sh kernel', 5400)
            try:
                ssh('sudo systemctl poweroff', 20)
            except subprocess.CalledProcessError as exc:
                if exc.returncode != 255:
                    raise
            process.wait(timeout=180)
            process = boot()
            ready(pve=True)
            ssh('sudo bash /home/debian/source-fixture/pve_provision.sh packages', 5400)
        ssh('sudo bash /home/debian/source-fixture/source_guest.sh ' + name + ' ' + args.platform, 15000)
        result = subprocess.check_output(['ssh', *options, '-p', str(port), 'debian@127.0.0.1',
                                         'sudo cat /home/debian/source-fixture/receipt.json'], stderr=log, timeout=30)
        guest = json.loads(result)
        required = ('first_install', 'reinstall', 'configuration_preserved', 'mqtt_first', 'mqtt_reinstalled')
        if guest.get('source') != args.source or guest.get('platform') != args.platform or not all(guest.get(key) is True for key in required):
            raise ValueError('incomplete or mismatched guest receipt')
        receipt.update({key: True for key in required}, status='passed')
    except BaseException as exc:
        receipt['error'] = type(exc).__name__
        raise
    finally:
        errors = cleanup([('VM', stop_vm)])
        if errors:
            receipt['status'] = 'failed'
            receipt['cleanup_errors'] = errors
        log.close()
        errors += cleanup([('diagnostics', lambda: retain_log(scratch / 'run.log', out / 'run.log'))])
        retain = args.retain_on_failure and receipt['status'] != 'passed'
        if not retain:
            errors += cleanup([('private resources', lambda: shutil.rmtree(scratch))])
        receipt['private_resources_retained'] = scratch.exists()
        if errors:
            receipt['status'] = 'failed'
            receipt['cleanup_errors'] = [sanitize(error) for error in errors]
        (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        if errors:
            raise RuntimeError('acceptance cleanup failed; see receipt')


if __name__ == '__main__':
    main()
