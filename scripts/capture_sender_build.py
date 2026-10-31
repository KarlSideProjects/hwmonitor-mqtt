"""Capture the extracted payload from the executable built by this invocation."""

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time


def inventory(root):
    entries = []
    total = 0
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise ValueError('invalid capture entry')
        if path.is_file():
            size = path.stat().st_size
            total += size
            if path.stat().st_nlink != 1 or size > 128 * 1024 * 1024 or total > 512 * 1024 * 1024 or len(entries) >= 1000:
                raise ValueError('capture exceeds file or size limits')
            entries.append({'path': path.relative_to(root).as_posix(), 'size': path.stat().st_size,
                            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    if not entries:
        raise ValueError('empty capture')
    return entries


@contextmanager
def capture_directory():
    directory = tempfile.TemporaryDirectory(prefix='sender-capture-')
    try:
        yield Path(directory.name)
    finally:
        for attempt in range(100):
            try:
                directory.cleanup()
                break
            except PermissionError:
                if attempt == 99:
                    raise
                time.sleep(0.1)


def capture(command, dist, output, timeout=30):
    expected = inventory(dist)
    with capture_directory() as root, socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT') if key in os.environ}
        env.update(TMPDIR=str(root), TMP=str(root), TEMP=str(root), HOME=str(root), USERPROFILE=str(root),
                   PYTHONIOENCODING='utf-8', BROKER_HOST='127.0.0.1', BROKER_PORT=str(listener.getsockname()[1]), LHM_AUTOSTART='0')
        kwargs = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        process = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and process.poll() is None:
                for candidate in root.glob('onefile_*'):
                    if candidate.is_symlink() or not candidate.is_dir():
                        continue
                    try:
                        if inventory(candidate) == expected:
                            with tempfile.TemporaryDirectory(prefix='sender-copy-', dir=output.parent) as staging:
                                copied = Path(staging) / 'runtime'
                                shutil.copytree(candidate, copied)
                                if inventory(copied) == expected and process.poll() is None:
                                    copied.rename(output)
                                    return expected
                    except (OSError, ValueError):
                        pass
                time.sleep(0.01)
        finally:
            if os.name == 'nt':
                if process.poll() is None:
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait(timeout=5)
        raise ValueError('onefile capture did not match this build distribution')


def compile_and_capture(command, source, build_dir, executable, report, dist, runtime, inventory_path, provenance, smoke_args, compiler_log=None, arm_state=None):
    if build_dir.exists() or runtime.exists() or inventory_path.exists() or provenance.exists():
        raise ValueError('build and evidence paths must be fresh')
    env = None
    if arm_state is not None:
        env = {**os.environ, 'CC': str(source / 'scripts/linux_arm64/compiler.py'), 'HWMONITOR_ARM_STATE': str(arm_state)}
    if compiler_log is None:
        subprocess.run(command, cwd=source, check=True, env=env)
    else:
        with compiler_log.open('xb') as output:
            subprocess.run(command, cwd=source, check=True, stdout=output, stderr=subprocess.STDOUT, env=env)
        if compiler_log.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('compiler log too large')
    if arm_state is not None:
        if __package__:
            from .linux_arm64.compiler import link_evidence, verify_local_crt, verify_scons_log
        else:
            from linux_arm64.compiler import link_evidence, verify_local_crt, verify_scons_log
        arm_evidence = link_evidence(arm_state)
        verify_local_crt(arm_state)
        if compiler_log is None:
            raise ValueError('ARM compiler log required')
        verify_scons_log(compiler_log)
    if not executable.is_file() or not report.is_file() or not dist.is_dir():
        raise ValueError('fresh compiler outputs missing')
    exe_hash = hashlib.sha256(executable.read_bytes()).hexdigest()
    report_hash = hashlib.sha256(report.read_bytes()).hexdigest()
    entries = capture([str(executable.resolve()), *smoke_args], dist, runtime)
    if hashlib.sha256(executable.read_bytes()).hexdigest() != exe_hash or hashlib.sha256(report.read_bytes()).hexdigest() != report_hash:
        raise ValueError('compiler outputs changed during capture')
    inventory_path.write_text(json.dumps(entries, indent=2) + '\n', encoding='utf-8')
    data = {'executable_sha256': exe_hash, 'report_sha256': report_hash,
            'inventory_sha256': hashlib.sha256(inventory_path.read_bytes()).hexdigest()}
    if arm_state is not None:
        data['arm_startup'] = arm_evidence
    if compiler_log is not None:
        data['compiler_log_sha256'] = hashlib.sha256(compiler_log.read_bytes()).hexdigest()
    provenance.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args()
    source, build, evidence = args.source.resolve(), args.build_dir.resolve(), args.evidence_dir.resolve()
    if evidence.exists():
        parser.error('evidence directory must be fresh')
    evidence.mkdir(parents=True)
    report = build / 'nuitka-report.xml'
    command = [sys.executable, '-m', 'nuitka', '--onefile', '--msvc=latest', '--show-scons', '--noinclude-dlls=mfc140u.dll', '--noinclude-dlls=vcruntime140.dll', '--noinclude-dlls=vcruntime140_1.dll', f'--output-dir={build}',
               f'--report={report}', '--output-filename=sender.exe', '--include-package=pythonnet',
               '--include-package=clr_loader', '--include-module=clr', '--nofollow-import-to=win32com.test',
               '--nofollow-import-to=win32com.demos', 'hwmonitor_mqtt/agents/agent_sender_windows.py']
    compile_and_capture(command, source, build, build / 'sender.exe', report, build / 'agent_sender_windows.dist',
                        evidence / 'runtime', evidence / 'inventory.json', evidence / 'provenance.json', [], evidence / 'compiler.log')
    shutil.copy2(report, evidence / 'report.xml')


if __name__ == '__main__':
    main()
