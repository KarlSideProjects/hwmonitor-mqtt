"""Replace source deployment inputs, preserving private settings and recovery files."""

import os
from pathlib import Path
import shutil
import tempfile
import warnings


INPUTS = ('hwmonitor_mqtt', 'Dockerfile.source', 'Dockerfile.source.dockerignore',
          'docker-compose.linux.yml', 'docker-compose.source.yml')


def deploy_source(source, root, activate, recover=None):
    source, root = Path(source), Path(root)
    if root.is_symlink() or not root.is_dir() or source.is_symlink():
        raise ValueError('unsafe source or installation directory')
    for name in INPUTS:
        incoming, current = source / name, root / name
        if incoming.is_symlink() or current.is_symlink():
            raise ValueError('symlink in deployment inputs')
        if name == 'hwmonitor_mqtt':
            if not incoming.is_dir() or (current.exists() and not current.is_dir()):
                raise ValueError('invalid sender source directory')
            for directory, subdirs, files in os.walk(incoming, followlinks=False):
                if any((Path(directory) / entry).is_symlink() for entry in subdirs + files):
                    raise ValueError('symlink in sender source directory')
        elif not incoming.is_file() or (current.exists() and not current.is_file()):
            raise ValueError('invalid deployment input')

    backup = Path(tempfile.mkdtemp(prefix='.source-rollback-', dir=root))
    staged = backup / 'new'
    previous = backup / 'previous'
    staged.mkdir()
    previous.mkdir()
    moved_old, moved_new = [], []
    activation_started = False
    try:
        for name in INPUTS:
            incoming = source / name
            if incoming.is_dir():
                shutil.copytree(incoming, staged / name)
            else:
                shutil.copy2(incoming, staged / name)
        for name in INPUTS:
            current = root / name
            if current.exists():
                os.replace(current, previous / name)
                moved_old.append(name)
            os.replace(staged / name, current)
            moved_new.append(name)
        activation_started = True
        activate()
    except BaseException as error:
        try:
            for name in reversed(moved_new):
                current = root / name
                if current.is_dir() and not current.is_symlink():
                    shutil.rmtree(current)
                else:
                    current.unlink()
            for name in reversed(moved_old):
                os.replace(previous / name, root / name)
            if activation_started and recover is not None:
                recover()
        except BaseException as recovery_error:
            raise RuntimeError(f'deployment recovery failed; backup retained at {backup}') from recovery_error
        shutil.rmtree(backup)
        raise error
    try:
        shutil.rmtree(backup)
    except OSError as error:
        warnings.warn(f'sender running; backup retained at {backup}: {error}')
