from pathlib import Path

import pytest

from scripts.source_deploy import deploy_source


FILES = ('Dockerfile.source', 'Dockerfile.source.dockerignore',
         'docker-compose.linux.yml', 'docker-compose.source.yml')


@pytest.fixture
def deployment(tmp_path):
    source = tmp_path / 'source'
    root = tmp_path / 'root'
    source.mkdir()
    root.mkdir()
    (source / 'hwmonitor_mqtt').mkdir()
    (source / 'hwmonitor_mqtt' / 'sender.py').write_text('new sender')
    for name in FILES:
        (source / name).write_text('new ' + name)
    (root / 'hwmonitor_mqtt').mkdir()
    (root / 'hwmonitor_mqtt' / 'sender.py').write_text('old sender')
    (root / 'Dockerfile.source').write_text('old dockerfile')
    (root / '.env').write_text('private settings')
    (root / 'docker-compose.override.yml').write_text('private override')
    return source, root


def test_activation_uses_new_sources_and_preserves_settings(deployment):
    source, root = deployment
    def activate():
        assert (root / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'new sender'
        assert (root / '.env').read_text() == 'private settings'
    deploy_source(source, root, activate)
    assert (root / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'new sender'
    assert (root / 'docker-compose.override.yml').read_text() == 'private override'
    assert not list(root.glob('.source-rollback-*'))


def test_activation_failure_restores_existing_sources(deployment):
    source, root = deployment
    def activate():
        raise RuntimeError('activation failed')
    with pytest.raises(RuntimeError, match='activation failed'):
        deploy_source(source, root, activate)
    assert (root / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'old sender'
    assert (root / 'Dockerfile.source').read_text() == 'old dockerfile'
    assert not (root / 'docker-compose.source.yml').exists()
    assert (root / '.env').read_text() == 'private settings'


def test_refuses_symlinked_application_files(deployment, tmp_path):
    source, root = deployment
    target = tmp_path / 'outside'
    target.write_text('do not change')
    (root / 'Dockerfile.source').unlink()
    (root / 'Dockerfile.source').symlink_to(target)
    with pytest.raises(ValueError, match='symlink'):
        deploy_source(source, root, lambda: pytest.fail('activation should not run'))
    assert target.read_text() == 'do not change'
    assert (root / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'old sender'


def test_service_recovery_runs_after_old_files_are_restored(deployment):
    source, root = deployment
    calls = []
    def activate():
        raise RuntimeError('activation failed')
    def recover():
        calls.append((root / 'hwmonitor_mqtt' / 'sender.py').read_text())
    with pytest.raises(RuntimeError, match='activation failed'):
        deploy_source(source, root, activate, recover)
    assert calls == ['old sender']


def test_failed_service_recovery_keeps_the_backup(deployment):
    source, root = deployment
    def activate():
        raise RuntimeError('activation failed')
    def recover():
        raise RuntimeError('recovery failed')
    with pytest.raises(RuntimeError, match='backup retained'):
        deploy_source(source, root, activate, recover)
    assert (root / '.env').read_text() == 'private settings'
    assert list(root.glob('.source-rollback-*'))


def test_interrupted_file_swap_restores_already_moved_files(deployment, monkeypatch):
    from scripts import source_deploy
    source, root = deployment
    original_replace = source_deploy.os.replace
    calls = 0
    def replace(old, new):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError('simulated filesystem failure')
        original_replace(old, new)
    monkeypatch.setattr(source_deploy.os, 'replace', replace)
    with pytest.raises(OSError, match='simulated filesystem failure'):
        deploy_source(source, root, lambda: pytest.fail('activation should not run'))
    assert (root / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'old sender'
    assert (root / 'Dockerfile.source').read_text() == 'old dockerfile'
    assert (root / '.env').read_text() == 'private settings'


def test_success_retains_backup_if_cleanup_fails(deployment, monkeypatch):
    from scripts import source_deploy
    source, root = deployment
    def fail_cleanup(path):
        raise OSError('simulated cleanup failure')
    monkeypatch.setattr(source_deploy.shutil, 'rmtree', fail_cleanup)
    with pytest.warns(UserWarning, match='backup retained'):
        deploy_source(source, root, lambda: None)
    assert (root / 'hwmonitor_mqtt' / 'sender.py').read_text() == 'new sender'
    assert list(root.glob('.source-rollback-*'))
