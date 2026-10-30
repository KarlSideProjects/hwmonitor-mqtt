#!/usr/bin/env python3
"""Run the compiled bootstrap acceptance in a disposable nested Docker daemon."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--bootstrap', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--platform', choices=('linux-x64', 'linux-arm64'), required=True)
    parser.add_argument('--old-archive', type=Path, required=True)
    parser.add_argument('--old-sha256', required=True)
    parser.add_argument('--old-source', required=True)
    parser.add_argument('--old-version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    machine = {'x86_64': 'linux-x64', 'aarch64': 'linux-arm64'}.get(platform.machine())
    if machine != args.platform:
        parser.error('host architecture must match platform; emulation is not accepted')
    if args.old_sha256 == args.sha256 or args.old_source == args.source or args.old_version == args.version:
        parser.error('upgrade requires distinct archive, source, and version')
    for path, digest in ((args.archive, args.sha256), (args.old_archive, args.old_sha256)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            parser.error('archive checksum mismatch')
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    output = args.output.resolve()
    name = 'sender-acceptance-' + uuid.uuid4().hex[:12]
    image = name + ':local'
    log = (output / 'run.log').open('w')
    created = False
    image_created = False

    def run(command, timeout=300, **kwargs):
        print('+', ' '.join(map(str, command)), file=log, flush=True)
        return subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=timeout, **kwargs)

    work = Path(tempfile.mkdtemp(prefix='sender-acceptance-'))
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        os.chmod(work, 0o755)
        shutil.copyfile(args.bootstrap, work / 'bootstrap.sh')
        shutil.copyfile(args.archive, work / 'archive.tar.gz')
        shutil.copyfile(args.old_archive, work / 'old-archive.tar.gz')
        shutil.copyfile(HERE / 'release_endpoint.py', work / 'release_endpoint.py')
        shutil.copyfile(HERE / 'docker_acceptance_inner.sh', work / 'inner.sh')
        image_created = True
        run(['docker', 'build', '-t', image, '-f', str(HERE / 'Dockerfile.installer-double'), str(HERE)])
        created = True
        run(['docker', 'run', '-d', '--name', name, '--privileged', '-v', f'{work}:/work', 'docker@sha256:173f284a4299164772a90f52b373e73e087583c0963f1334c9995f190ef6f3f5', 'dockerd', '--host=unix:///var/run/docker.sock', '--iptables=false', '--ip6tables=false', '--bridge=none'])
        run(['docker', 'exec', name, 'sh', '-c', 'i=0; until docker info >/dev/null 2>&1; do i=$((i+1)); [ "$i" -lt 60 ] || exit 1; sleep 1; done'])
        run(['docker', 'exec', name, 'docker', 'info', '--format', 'Docker={{.ServerVersion}} storage={{.Driver}}'])
        run(['docker', 'exec', name, 'apk', 'add', '--no-cache', 'bash', 'python3', 'mosquitto', 'mosquitto-clients', 'curl', 'jq', 'coreutils'])
        with (work / 'installer.tar').open('wb') as saved:
            subprocess.run(['docker', 'save', image], stdout=saved, stderr=log, check=True, timeout=120)
        run(['docker', 'exec', name, 'docker', 'load', '-i', '/work/installer.tar'])
        run(['docker', 'exec', name, 'bash', '/work/inner.sh', image, args.sha256, args.source, args.version, args.platform, args.old_sha256, args.old_source, args.old_version], timeout=900)
        for item in ('full-old.json', 'full.json', 'esp.json', 'full-after-rollback.json', 'state.txt'):
            shutil.copyfile(work / item, output / item)
    finally:
        original = sys.exc_info()[1]
        errors = []
        if created:
            try:
                subprocess.run(['docker', 'exec', name, 'chown', '-R', f'{os.getuid()}:{os.getgid()}', '/work'], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
                for item in ('runtime.log', 'broker.log', 'endpoint.log', 'full-old.json', 'full.json', 'esp.json', 'full-after-rollback.json', 'state.txt'):
                    if (work / item).is_file():
                        shutil.copyfile(work / item, output / item)
            except Exception as error:
                errors.append(f'evidence/chown: {error}')
            try:
                subprocess.run(['docker', 'rm', '-f', name], stdout=log, stderr=subprocess.STDOUT, timeout=60)
                remaining = subprocess.run(['docker', 'ps', '-a', '--filter', f'name=^/{name}$', '--format', '{{.Names}}'], capture_output=True, text=True, check=True, timeout=20)
                print('owned container absent:', not remaining.stdout.strip(), file=log, flush=True)
                if remaining.stdout.strip():
                    raise RuntimeError('owned container still exists')
            except Exception as error:
                errors.append(f'container cleanup: {error}')
        if image_created:
            try:
                subprocess.run(['docker', 'image', 'rm', image], stdout=log, stderr=subprocess.STDOUT, timeout=60)
                images = subprocess.run(['docker', 'image', 'ls', '--format', '{{.Repository}}:{{.Tag}}', image], capture_output=True, text=True, check=True, timeout=20)
                print('owned image tag absent:', not images.stdout.strip(), file=log, flush=True)
                if images.stdout.strip():
                    raise RuntimeError('owned image tag still exists')
            except Exception as error:
                errors.append(f'image cleanup: {error}')
        try:
            shutil.rmtree(work)
        except Exception as error:
            errors.append(f'temporary directory {work}: {error}')
        for error in errors:
            print('cleanup unresolved:', error, file=log, flush=True)
        log.close()
        if errors and original is None:
            raise RuntimeError('cleanup failed; see run.log')



if __name__ == '__main__':
    main()
