import json
import subprocess

import pytest

from scripts.source_docker import DockerActivation
from scripts.source_deploy import deploy_source


def inspect(root, *, container_id='container-id', running=True):
    return json.dumps({
        'Id': container_id,
        'Image': 'sha256:oldimage',
        'Config': {'Image': 'old-sender:latest', 'Labels': {
            'com.docker.compose.project.working_dir': str(root),
            'com.docker.compose.service': 'sys_agent',
            'com.docker.compose.project.config_files': str(root / 'docker-compose.linux.yml'),
        }},
        'State': {'Status': 'running' if running else 'exited',
                  'Running': running, 'Pid': 123 if running else 0},
        'RestartCount': 0,
    })


def test_fresh_install_builds_source_and_requires_stable_sender(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ['docker', 'inspect', 'sys-agent']:
            count = sum(c[:3] == args[:3] for c in calls)
            if count <= 2:
                return subprocess.CompletedProcess(args, 1, '', 'No such object: sys-agent')
            return subprocess.CompletedProcess(args, 0, inspect(tmp_path), '')
        return subprocess.CompletedProcess(args, 0, '', '')
    activation = DockerActivation(tmp_path, run=run, sleep=lambda _: None, which=lambda _: None)
    activation.prepare()
    activation.activate()
    up = next(call for call in calls if 'up' in call)
    assert str(tmp_path / 'docker-compose.source.yml') in up
    assert '--build' in up
    assert sum(call[:3] == ['docker', 'inspect', 'sys-agent'] for call in calls) == 5


def test_rejects_unrelated_existing_container_without_replacing_it(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        unrelated = json.loads(inspect(tmp_path))
        unrelated['Config']['Labels'] = {}
        return subprocess.CompletedProcess(args, 0, json.dumps(unrelated), '')
    activation = DockerActivation(tmp_path, run=run, sleep=lambda _: None, which=lambda _: None)
    with pytest.raises(RuntimeError, match='unrecognized'):
        activation.prepare()
    assert not any('up' in call or 'rm' in call for call in calls)


def test_fresh_failed_start_removes_only_recognized_sender(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ['docker', 'inspect', 'sys-agent']:
            count = sum(c[:3] == args[:3] for c in calls)
            if count <= 2:
                return subprocess.CompletedProcess(args, 1, '', 'No such object: sys-agent')
            return subprocess.CompletedProcess(args, 0, inspect(tmp_path, running=False), '')
        if 'up' in args:
            return subprocess.CompletedProcess(args, 1, '', 'failed')
        return subprocess.CompletedProcess(args, 0, '', '')
    activation = DockerActivation(tmp_path, run=run, sleep=lambda _: None, which=lambda _: None)
    activation.prepare()
    with pytest.raises(RuntimeError, match='startup'):
        activation.activate()
    activation.recover()
    assert ['docker', 'rm', '-f', 'sys-agent'] in calls


def test_upgrade_failure_restores_pinned_image_and_previous_compose(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ['docker', 'inspect', 'sys-agent']:
            return subprocess.CompletedProcess(args, 0, inspect(tmp_path), '')
        if args[:3] == ['docker', 'image', 'inspect']:
            if '--format' in args:
                return subprocess.CompletedProcess(args, 0, 'sha256:oldimage\n', '')
            return subprocess.CompletedProcess(args, 1, '', 'not found')
        if 'up' in args and '--build' in args:
            return subprocess.CompletedProcess(args, 1, '', 'failed')
        return subprocess.CompletedProcess(args, 0, '', '')
    activation = DockerActivation(tmp_path, run=run, sleep=lambda _: None, which=lambda _: None)
    activation.prepare()
    with pytest.raises(RuntimeError, match='startup'):
        activation.activate()
    activation.recover()
    assert any(call[:2] == ['docker', 'tag'] and call[-1] == 'old-sender:latest' for call in calls)
    assert any('up' in call and '--no-build' in call for call in calls)
    activation.finish()
    assert any(call[:3] == ['docker', 'image', 'rm'] for call in calls)


def test_upgrade_refuses_a_sender_replaced_after_preparation(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ['docker', 'inspect', 'sys-agent']:
            count = sum(c[:3] == args[:3] for c in calls)
            return subprocess.CompletedProcess(args, 0, inspect(tmp_path, container_id='old' if count == 1 else 'new'), '')
        if args[:3] == ['docker', 'image', 'inspect']:
            return subprocess.CompletedProcess(args, 0 if '--format' in args else 1, 'sha256:oldimage\n', '')
        return subprocess.CompletedProcess(args, 0, '', '')
    activation = DockerActivation(tmp_path, run=run, sleep=lambda _: None, which=lambda _: None)
    activation.prepare()
    with pytest.raises(RuntimeError, match='changed'):
        activation.activate()
    assert not any('up' in call for call in calls)


def test_failed_upgrade_restores_source_before_restarting_old_container(tmp_path):
    root, source = tmp_path / 'root', tmp_path / 'source'
    root.mkdir()
    source.mkdir()
    (root / 'hwmonitor_mqtt').mkdir()
    (root / 'hwmonitor_mqtt' / 'sender.py').write_text('old')
    (source / 'hwmonitor_mqtt').mkdir()
    (source / 'hwmonitor_mqtt' / 'sender.py').write_text('new')
    for name in ('Dockerfile.source', 'Dockerfile.source.dockerignore',
                 'docker-compose.linux.yml', 'docker-compose.source.yml'):
        (source / name).write_text('new')
    (root / 'docker-compose.linux.yml').write_text('old')
    (root / '.env').write_text('private')
    def run(args, **kwargs):
        if args[:3] == ['docker', 'inspect', 'sys-agent']:
            return subprocess.CompletedProcess(args, 0, inspect(root), '')
        if args[:3] == ['docker', 'image', 'inspect']:
            return subprocess.CompletedProcess(args, 0 if '--format' in args else 1, 'sha256:oldimage\n', '')
        if 'up' in args and '--build' in args:
            assert (root / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'new'
            return subprocess.CompletedProcess(args, 1, '', 'failed')
        if 'up' in args and '--no-build' in args:
            assert (root / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'old'
        return subprocess.CompletedProcess(args, 0, '', '')
    activation = DockerActivation(root, run=run, sleep=lambda _: None, which=lambda _: None)
    activation.prepare()
    with pytest.raises(RuntimeError, match='startup'):
        deploy_source(source, root, activation.activate, activation.recover)
    activation.finish()
    assert (root / '.env').read_text() == 'private'
    assert (root / 'docker-compose.linux.yml').read_text() == 'old'


def test_active_native_pve_sender_blocks_docker_before_it_changes_anything(tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['systemctl', 'show']:
            return subprocess.CompletedProcess(args, 0,
                'LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n', '')
        return subprocess.CompletedProcess(args, 0, '', '')
    activation = DockerActivation(tmp_path, run=run, sleep=lambda _: None, which=lambda _: '/usr/bin/systemctl')
    with pytest.raises(RuntimeError, match='native'):
        activation.prepare()
    assert not any(args[0] == 'docker' for args in calls)
