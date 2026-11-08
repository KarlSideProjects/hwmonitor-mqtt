"""Switch a recognized PVE sender to Python with service and file recovery."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from hwmonitor_mqtt.platforms.installers.pve import SERVICE_NAME, _unit_escape


class PveActivation:
    def __init__(self, root, config, *, runtime='/opt/hwmonitor-mqtt-pve',
                 units='/etc/systemd/system', run=subprocess.run, sleep=time.sleep):
        self.root = Path(root).resolve()
        self.config = Path(config)
        self.runtime = Path(runtime)
        self.units = Path(units)
        self.app = self.runtime / 'app'
        self.venv = self.runtime / 'venv'
        self.unit = self.units / SERVICE_NAME
        self.dropdir = self.units / (SERVICE_NAME + '.d')
        self.override = self.dropdir / '90-hwmonitor-sender.conf'
        self.run = run
        self.sleep = sleep
        self.started = False
        self.stopped = False
        self.stop_attempted = False
        self.installer_attempted = False
        self.override_removed = False
        self.backup = None
        self.moved = []

    def _run(self, args, **kwargs):
        return self.run(args, check=False, capture_output=True, text=True, **kwargs)

    def prepare(self):
        for path in (self.runtime, self.app, self.venv, self.units, self.unit, self.dropdir, self.override):
            if path.is_symlink():
                raise RuntimeError('untrusted symlink in PVE installation paths')
        self.existed = self.unit.is_file()
        if self.unit.exists() and not self.existed:
            raise RuntimeError('unrecognized sender service')
        if not self.existed and (self.app.exists() or self.venv.exists()):
            raise RuntimeError('unrecognized existing PVE runtime')
        if self.existed:
            text = self.unit.read_text()
            if ('Description=Hardware Monitor MQTT sender for Proxmox VE' not in text
                    or ('hwmonitor_mqtt.agents.agent_sender_async' not in text and str(self.app / 'sender') not in text)):
                raise RuntimeError('unrecognized sender service')
        legacy = ('[Service]\nExecStart=\nExecStart=' + _unit_escape(self.app / 'sender')
                  + '\nWorkingDirectory=' + _unit_escape(self.app) + '\n')
        if self.override.exists() and (not self.override.is_file() or self.override.read_text() not in (
                legacy, legacy + 'Environment=PYTHONIOENCODING=utf-8\n')):
            raise RuntimeError('unrecognized sender override')
        active = self._run(['systemctl', 'is-active', '--quiet', SERVICE_NAME]).returncode
        enabled = self._run(['systemctl', 'is-enabled', '--quiet', SERVICE_NAME]).returncode
        if active not in (0, 3, 4) or enabled not in (0, 1, 4) or self.existed and 4 in (active, enabled):
            raise RuntimeError('cannot determine previous PVE service state')
        self.active, self.enabled = active == 0, enabled == 0

    def activate(self):
        self.started = True
        if self.active:
            self.stop_attempted = True
            if self._run(['systemctl', 'stop', SERVICE_NAME]).returncode:
                raise RuntimeError('cannot stop previous PVE service')
            self.stopped = True
        self.runtime.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.backup = Path(tempfile.mkdtemp(prefix='.source-pve-rollback-', dir=self.runtime))
        for name, path in (('unit', self.unit), ('override', self.override)):
            if path.exists():
                shutil.copy2(path, self.backup / name)
        for name, path in (('app', self.app), ('venv', self.venv)):
            if path.exists():
                os.replace(path, self.backup / name)
                self.moved.append(name)
        self.override.unlink(missing_ok=True)
        self.override_removed = True
        self.installer_attempted = True
        env = os.environ.copy()
        env['PYTHONPATH'] = str(self.root)
        result = self._run([sys.executable, '-m', 'hwmonitor_mqtt.platforms.installers.pve',
                            '--project-root', str(self.root), '--config-file', str(self.config)],
                           cwd=self.root, env=env)
        if result.returncode:
            descriptor, log = tempfile.mkstemp(prefix='.source-pve-failure-', suffix='.log', dir=self.runtime)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                output.write((result.stdout or '')[-524288:])
                output.write((result.stderr or '')[-524288:])
            raise RuntimeError(f'PVE sender startup failed (exit {result.returncode}); private log: {log}')
        effective = self._run(['systemctl', 'show', '--property=ExecStart', '--value', SERVICE_NAME])
        if effective.returncode or 'path=' + str(self.venv / 'bin' / 'python') not in effective.stdout:
            raise RuntimeError('PVE sender is not using the expected Python runtime')
        previous_pid = None
        for _ in range(3):
            self.sleep(1)
            if self._run(['systemctl', 'is-active', '--quiet', SERVICE_NAME]).returncode:
                raise RuntimeError('PVE sender did not remain active')
            status = self._run(['systemctl', 'show', '--property=MainPID', '--value', SERVICE_NAME])
            pid = status.stdout.strip()
            if status.returncode or not pid.isdigit() or int(pid) == 0 or previous_pid is not None and pid != previous_pid:
                raise RuntimeError('PVE sender did not remain stable')
            previous_pid = pid

    def recover(self):
        if not self.started:
            return
        if self.stop_attempted and not self.stopped:
            raise RuntimeError('previous PVE service stop is unconfirmed')
        if self.installer_attempted:
            stopped = self._run(['systemctl', 'stop', SERVICE_NAME]).returncode
            if stopped:
                state = self._run(['systemctl', 'is-active', '--quiet', SERVICE_NAME]).returncode
                if state != 3 and not (state == 4 and not self.existed and not self.unit.exists()):
                    raise RuntimeError('cannot stop new PVE sender for recovery')
            if not self.existed and self.unit.exists():
                disabled = self._run(['systemctl', 'disable', SERVICE_NAME]).returncode
                if disabled and self._run(['systemctl', 'is-enabled', '--quiet', SERVICE_NAME]).returncode not in (1, 4):
                    raise RuntimeError('cannot disable new PVE sender for recovery')
        if self.backup is not None:
            for name, path in (('app', self.app), ('venv', self.venv)):
                if name not in self.moved and not self.installer_attempted:
                    continue
                if path.is_symlink():
                    raise RuntimeError('untrusted PVE recovery path')
                if path.exists():
                    shutil.rmtree(path)
                if name in self.moved:
                    os.replace(self.backup / name, path)
            for name, path in (('unit', self.unit), ('override', self.override)):
                if name == 'unit' and not self.installer_attempted:
                    continue
                if name == 'override' and not self.override_removed:
                    continue
                if path.is_symlink():
                    raise RuntimeError('untrusted PVE recovery file')
                saved = self.backup / name
                if saved.exists():
                    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                    descriptor, temporary = tempfile.mkstemp(prefix='.source-restore-', dir=path.parent)
                    os.close(descriptor)
                    try:
                        shutil.copy2(saved, temporary)
                        os.replace(temporary, path)
                    finally:
                        Path(temporary).unlink(missing_ok=True)
                else:
                    path.unlink(missing_ok=True)
            if self._run(['systemctl', 'daemon-reload']).returncode:
                raise RuntimeError('cannot reload restored PVE service')
        if self.installer_attempted and self.existed:
            command = 'enable' if self.enabled else 'disable'
            if self._run(['systemctl', command, SERVICE_NAME]).returncode:
                raise RuntimeError('cannot restore PVE service enablement')
        if self.existed and (self.stopped or self.installer_attempted):
            command = 'restart' if self.active else 'stop'
            if self._run(['systemctl', command, SERVICE_NAME]).returncode:
                raise RuntimeError('cannot restore PVE service state')
        self.finish()

    def finish(self):
        if self.backup is not None:
            shutil.rmtree(self.backup)
            self.backup = None
