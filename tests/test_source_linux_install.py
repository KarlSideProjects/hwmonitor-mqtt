from pathlib import Path

import pytest

from scripts.source_linux_install import install_linux


COMMIT = 'a' * 40


class Activation:
    def __init__(self, root, fail=False):
        self.root = root
        self.fail = fail
        self.started = False
        self.calls = []

    def prepare(self):
        self.calls.append('prepare')

    def activate(self):
        self.calls.append('activate')
        self.started = True
        assert (self.root / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'new'
        if self.fail:
            raise RuntimeError('sender failed')

    def recover(self):
        self.calls.append('recover')
        assert (self.root / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'old'

    def finish(self):
        self.calls.append('finish')


@pytest.fixture
def inputs(tmp_path):
    root, source = tmp_path / 'root', tmp_path / 'source'
    root.mkdir()
    source.mkdir()
    (root / 'hwmonitor_mqtt').mkdir()
    (root / 'hwmonitor_mqtt' / 'sender.py').write_text('old')
    (root / '.env').write_text('private')
    (root / '.env').chmod(0o600)
    (root / '.hwmonitor-mqtt-bootstrap').write_text('old commit')
    (source / 'hwmonitor_mqtt').mkdir()
    (source / 'hwmonitor_mqtt' / 'sender.py').write_text('new')
    for name in ('Dockerfile.source', 'Dockerfile.source.dockerignore',
                 'docker-compose.linux.yml', 'docker-compose.source.yml'):
        (source / name).write_text('source')
    return source, root


def test_success_marks_exact_commit_and_preserves_private_settings(inputs):
    source, root = inputs
    activation = Activation(root)
    install_linux(source, root, COMMIT, activation)
    assert (root / '.hwmonitor-mqtt-bootstrap').read_text() == COMMIT + '\n'
    assert (root / '.env').read_text() == 'private'
    assert activation.calls == ['prepare', 'activate', 'finish']


def test_failed_start_restores_files_and_preserves_old_marker(inputs):
    source, root = inputs
    activation = Activation(root, fail=True)
    with pytest.raises(RuntimeError, match='sender failed'):
        install_linux(source, root, COMMIT, activation)
    assert (root / '.hwmonitor-mqtt-bootstrap').read_text() == 'old commit'
    assert (root / '.env').read_text() == 'private'
    assert activation.calls == ['prepare', 'activate', 'recover', 'finish']


def test_rejects_invalid_commit_before_preparing(inputs):
    source, root = inputs
    activation = Activation(root)
    with pytest.raises(ValueError):
        install_linux(source, root, '../invalid', activation)
    assert activation.calls == []


def test_unprotected_settings_stop_before_activation(inputs):
    source, root = inputs
    (root / '.env').chmod(0o644)
    activation = Activation(root)
    with pytest.raises(ValueError, match='untrusted'):
        install_linux(source, root, COMMIT, activation)
    assert activation.calls == ['prepare', 'finish']
    assert (root / '.env').read_text() == 'private'


def test_pve_uses_systemd_environment_format(inputs, monkeypatch):
    from scripts import source_linux_install
    source, root = inputs
    observed = []
    monkeypatch.setattr(source_linux_install, 'ensure_config', lambda path, target: observed.append((path, target)))
    activation = Activation(root)
    install_linux(source, root, COMMIT, activation, target='pve')
    assert observed == [(root / '.env', 'pve')]
    assert activation.calls == ['prepare', 'activate', 'finish']


def test_cleanup_error_does_not_report_a_running_sender_as_failed(inputs, capsys):
    source, root = inputs
    activation = Activation(root)
    def fail_cleanup():
        raise OSError('cleanup failed')
    activation.finish = fail_cleanup
    install_linux(source, root, COMMIT, activation)
    assert (root / '.hwmonitor-mqtt-bootstrap').read_text() == COMMIT + '\n'
    assert 'recovery data retained' in capsys.readouterr().err
