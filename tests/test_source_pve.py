from pathlib import Path
import subprocess

import pytest

from scripts.source_pve import PveActivation


@pytest.fixture
def existing_install(tmp_path):
    root, runtime, units = tmp_path / 'root', tmp_path / 'runtime', tmp_path / 'systemd'
    root.mkdir()
    (root / '.env').write_text('private settings')
    (runtime / 'app').mkdir(parents=True)
    (runtime / 'app' / 'sender').write_text('old binary')
    (runtime / 'venv').mkdir()
    (runtime / 'venv' / 'old').write_text('old runtime')
    units.mkdir()
    unit = units / 'hwmonitor-mqtt-pve.service'
    unit.write_text('Description=Hardware Monitor MQTT sender for Proxmox VE\nExecStart=' + str(runtime / 'app' / 'sender') + '\n')
    override = units / 'hwmonitor-mqtt-pve.service.d' / '90-hwmonitor-sender.conf'
    override.parent.mkdir()
    override.write_text('[Service]\nExecStart=\nExecStart=' + str(runtime / 'app' / 'sender') +
                        '\nWorkingDirectory=' + str(runtime / 'app') + '\nEnvironment=PYTHONIOENCODING=utf-8\n')
    return root, runtime, units, unit, override


def fake_systemd(runtime, units, calls, *, install_fails=False):
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['systemctl', 'is-active'] or args[:2] == ['systemctl', 'is-enabled']:
            return subprocess.CompletedProcess(args, 0, '', '')
        if args[:2] == ['systemctl', 'show']:
            text = ('path=' + str(runtime / 'venv' / 'bin' / 'python') if '--property=ExecStart' in args else '42')
            return subprocess.CompletedProcess(args, 0, text + '\n', '')
        if '-m' in args and 'hwmonitor_mqtt.platforms.installers.pve' in args:
            (runtime / 'app' / 'hwmonitor_mqtt').mkdir(parents=True)
            (runtime / 'app' / 'hwmonitor_mqtt' / 'sender.py').write_text('new source')
            (runtime / 'venv').mkdir()
            (units / 'hwmonitor-mqtt-pve.service').write_text('new source unit')
            return subprocess.CompletedProcess(args, 1 if install_fails else 0, '', '')
        return subprocess.CompletedProcess(args, 0, '', '')
    return run


def test_compiled_install_migrates_to_python_and_clears_known_override(existing_install):
    root, runtime, units, unit, override = existing_install
    calls = []
    activation = PveActivation(root, '/tmp/pve.env', runtime=runtime, units=units,
                               run=fake_systemd(runtime, units, calls), sleep=lambda _: None)
    activation.prepare()
    activation.activate()
    activation.finish()
    assert not override.exists()
    assert (runtime / 'app' / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'new source'
    assert (root / '.env').read_text() == 'private settings'
    assert any(args[:2] == ['systemctl', 'stop'] for args in calls)


def test_failed_migration_restores_previous_service_and_files(existing_install):
    root, runtime, units, unit, override = existing_install
    old_unit, old_override = unit.read_text(), override.read_text()
    calls = []
    activation = PveActivation(root, '/tmp/pve.env', runtime=runtime, units=units,
                               run=fake_systemd(runtime, units, calls, install_fails=True), sleep=lambda _: None)
    activation.prepare()
    with pytest.raises(RuntimeError, match='startup'):
        activation.activate()
    activation.recover()
    assert (runtime / 'app' / 'sender').read_text() == 'old binary'
    assert (runtime / 'venv' / 'old').read_text() == 'old runtime'
    assert unit.read_text() == old_unit
    assert override.read_text() == old_override
    assert (root / '.env').read_text() == 'private settings'
    assert any(args[:2] == ['systemctl', 'restart'] for args in calls)


def test_unknown_override_is_never_replaced(existing_install):
    root, runtime, units, unit, override = existing_install
    override.write_text('[Service]\nUser=custom\n')
    calls = []
    activation = PveActivation(root, '/tmp/pve.env', runtime=runtime, units=units,
                               run=fake_systemd(runtime, units, calls), sleep=lambda _: None)
    with pytest.raises(RuntimeError, match='unrecognized'):
        activation.prepare()
    assert override.read_text() == '[Service]\nUser=custom\n'
    assert not any(args[:2] == ['systemctl', 'stop'] for args in calls)


def test_interrupted_backup_keeps_runtime_that_was_not_moved(existing_install, monkeypatch):
    from scripts import source_pve
    root, runtime, units, unit, override = existing_install
    original_replace = source_pve.os.replace
    def replace(old, new):
        if Path(old) == runtime / 'venv':
            raise OSError('simulated backup failure')
        original_replace(old, new)
    monkeypatch.setattr(source_pve.os, 'replace', replace)
    calls = []
    activation = PveActivation(root, '/tmp/pve.env', runtime=runtime, units=units,
                               run=fake_systemd(runtime, units, calls), sleep=lambda _: None)
    activation.prepare()
    with pytest.raises(OSError, match='backup failure'):
        activation.activate()
    activation.recover()
    assert (runtime / 'app' / 'sender').read_text() == 'old binary'
    assert (runtime / 'venv' / 'old').read_text() == 'old runtime'


def test_failed_metadata_backup_does_not_delete_existing_override(existing_install, monkeypatch):
    from scripts import source_pve
    root, runtime, units, unit, override = existing_install
    original_copy = source_pve.shutil.copy2
    old_override = override.read_text()
    def copy(old, new, *args, **kwargs):
        if Path(old) == override:
            raise OSError('simulated metadata failure')
        return original_copy(old, new, *args, **kwargs)
    monkeypatch.setattr(source_pve.shutil, 'copy2', copy)
    activation = PveActivation(root, '/tmp/pve.env', runtime=runtime, units=units,
                               run=fake_systemd(runtime, units, []), sleep=lambda _: None)
    activation.prepare()
    with pytest.raises(OSError, match='metadata failure'):
        activation.activate()
    activation.recover()
    assert override.read_text() == old_override
    assert (runtime / 'app' / 'sender').read_text() == 'old binary'


def test_refuses_existing_runtime_without_recognized_service(existing_install):
    root, runtime, units, unit, override = existing_install
    unit.unlink()
    override.unlink()
    activation = PveActivation(root, '/tmp/pve.env', runtime=runtime, units=units,
                               run=fake_systemd(runtime, units, []), sleep=lambda _: None)
    with pytest.raises(RuntimeError, match='unrecognized'):
        activation.prepare()
    assert (runtime / 'app' / 'sender').read_text() == 'old binary'


def test_unconfirmed_previous_stop_does_not_claim_recovery(existing_install):
    root, runtime, units, unit, override = existing_install
    base = fake_systemd(runtime, units, [])
    def run(args, **kwargs):
        if args[:2] == ['systemctl', 'stop']:
            return subprocess.CompletedProcess(args, 1, '', 'unknown stop result')
        return base(args, **kwargs)
    activation = PveActivation(root, '/tmp/pve.env', runtime=runtime, units=units,
                               run=run, sleep=lambda _: None)
    activation.prepare()
    with pytest.raises(RuntimeError, match='cannot stop'):
        activation.activate()
    with pytest.raises(RuntimeError, match='unconfirmed'):
        activation.recover()
    assert (runtime / 'app' / 'sender').read_text() == 'old binary'
