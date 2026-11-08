"""Start a source sender and recover a recognized previous Docker sender."""

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import subprocess
import time
import uuid


class DockerActivation:
    def __init__(self, root, run=subprocess.run, sleep=time.sleep, which=shutil.which):
        self.root = Path(root).resolve()
        self.run = run
        self.sleep = sleep
        self.which = which
        self.previous = None
        self.pin = None
        self.started = False

    def _run(self, args, *, env=None):
        return self.run(args, check=False, capture_output=True, text=True, env=env)

    def _inspect(self):
        result = self._run(['docker', 'inspect', 'sys-agent', '--format', '{{json .}}'])
        if result.returncode:
            if 'no such object' in result.stderr.lower():
                return None
            raise RuntimeError('cannot inspect existing sender container')
        try:
            return json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise RuntimeError('invalid sender container inspection') from error

    def _files(self, container):
        try:
            labels = container['Config']['Labels']
            if (labels['com.docker.compose.project.working_dir'] != str(self.root)
                    or labels['com.docker.compose.service'] != 'sys_agent'):
                raise ValueError
            files = labels['com.docker.compose.project.config_files'].split(',')
            allowed = {str(self.root / name) for name in (
                'docker-compose.linux.yml', 'docker-compose.source.yml', 'docker-compose.override.yml')}
            if not files or files[0] != str(self.root / 'docker-compose.linux.yml') or not set(files) <= allowed:
                raise ValueError
            return files
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError('unrecognized sender container') from error

    def _compose(self, files, operation):
        env = os.environ.copy()
        env['SENDER_HOSTNAME'] = socket.gethostname()
        args = ['docker', 'compose', '--project-directory', str(self.root)]
        for path in files:
            args += ['-f', path]
        args += ['--profile', 'agent'] + operation
        return self._run(args, env=env)

    def _validate_source_compose(self, files):
        result = self._compose(files, ['config', '--format', 'json'])
        try:
            if result.returncode:
                raise ValueError
            service = json.loads(result.stdout)['services']['sys_agent']
            build = service['build']
            context = Path(build['context']).resolve()
            dockerfile = Path(build['dockerfile'])
            if not dockerfile.is_absolute():
                dockerfile = context / dockerfile
            app = PurePosixPath('/app')
            mounts = [PurePosixPath(volume['target']) for volume in service.get('volumes', [])]
            environment = service.get('environment') or {}
            if (context != self.root or dockerfile.resolve() != self.root / 'Dockerfile.source'
                    or service.get('command') is not None or service.get('entrypoint') is not None
                    or service.get('pull_policy') not in (None, 'build')
                    or service.get('working_dir') not in (None, '/app')
                    or any(target == app or target in app.parents or app in target.parents for target in mounts)
                    or 'PYTHONPATH' in environment or 'PYTHONHOME' in environment):
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError) as error:
            raise RuntimeError('compose configuration does not run the source sender') from error

    def prepare(self):
        if self.which('systemctl'):
            status = self._run(['systemctl', 'show', '--property=LoadState', '--property=ActiveState',
                                '--property=UnitFileState', 'hwmonitor-mqtt-pve.service'])
            fields = dict(line.split('=', 1) for line in status.stdout.splitlines() if '=' in line)
            safe = ((fields.get('LoadState'), fields.get('ActiveState'), fields.get('UnitFileState'))
                    in (('not-found', 'inactive', ''), ('loaded', 'inactive', 'disabled')))
            if status.returncode or not safe:
                raise RuntimeError('native PVE sender is active, enabled, or cannot be checked')
        if self._run(['docker', 'compose', 'version']).returncode:
            raise RuntimeError('Docker Compose v2 is unavailable')
        self.previous = self._inspect()
        if self.previous is None:
            return
        self._files(self.previous)
        try:
            if self.previous['State']['Status'] not in ('running', 'exited', 'created'):
                raise ValueError
            image = self.previous['Image']
            reference = self.previous['Config']['Image']
            if not image or not reference:
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError('cannot identify previous sender image') from error
        pin = 'hwmonitor-mqtt-rollback:' + uuid.uuid4().hex
        if self._run(['docker', 'image', 'inspect', pin]).returncode == 0:
            raise RuntimeError('rollback image tag already exists')
        if self._run(['docker', 'tag', image, pin]).returncode:
            raise RuntimeError('cannot preserve previous sender image')
        self.pin = pin
        self._check_pin()

    def activate(self):
        current = self._inspect()
        if (current is None) != (self.previous is None):
            raise RuntimeError('sender changed after preparation')
        if current is not None and (current.get('Id') != self.previous.get('Id') or current.get('Image') != self.previous.get('Image')):
            raise RuntimeError('sender changed after preparation')
        files = [str(self.root / 'docker-compose.linux.yml'), str(self.root / 'docker-compose.source.yml')]
        override = self.root / 'docker-compose.override.yml'
        if override.is_symlink():
            raise RuntimeError('untrusted compose override')
        if override.is_file():
            files.append(str(override))
        self._validate_source_compose(files)
        self.started = True
        if self._compose(files, ['up', '--build', '-d']).returncode:
            raise RuntimeError('sender startup failed')
        previous_state = None
        for _ in range(3):
            self.sleep(1)
            current = self._inspect()
            if current is None:
                raise RuntimeError('sender did not remain running')
            self._files(current)
            try:
                state = (current['Id'], current['State']['Pid'], current['RestartCount'])
                if not current['State']['Running'] or not isinstance(state[1], int) or state[1] <= 0:
                    raise ValueError
                if previous_state is not None and previous_state != state:
                    raise ValueError
            except (KeyError, TypeError, ValueError) as error:
                raise RuntimeError('sender did not remain stable') from error
            previous_state = state

    def recover(self):
        if not self.started:
            return
        current = self._inspect()
        if current is not None:
            self._files(current)
        if self.previous is None:
            if current is not None and self._run(['docker', 'rm', '-f', 'sys-agent']).returncode:
                raise RuntimeError('cannot remove failed sender')
            return
        self._check_pin()
        if self._run(['docker', 'tag', self.pin, self.previous['Config']['Image']]).returncode:
            raise RuntimeError('cannot restore previous sender image')
        operation = ['up', '--no-build', '-d'] if self.previous['State']['Status'] == 'running' else ['create', '--no-build']
        if self._compose(self._files(self.previous), operation).returncode:
            raise RuntimeError('cannot restore previous sender container')

    def _check_pin(self):
        result = self._run(['docker', 'image', 'inspect', '--format', '{{.Id}}', self.pin])
        if result.returncode or result.stdout.strip() != self.previous['Image']:
            raise RuntimeError('rollback image identity changed')

    def finish(self):
        if self.pin is not None:
            self._check_pin()
            if self._run(['docker', 'image', 'rm', self.pin]).returncode:
                raise RuntimeError('cannot remove rollback image tag')
            self.pin = None
