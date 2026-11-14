import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest


BOOTSTRAP = Path(__file__).resolve().parents[1] / 'bootstrap-source.sh'


def run_bootstrap(tmp_path, root):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for name, body in {
        'id': '#!/bin/sh\necho 0\n',
        'uname': '#!/bin/sh\necho x86_64\n',
        'stat': '#!/bin/sh\nif [ "$2" = "%u" ]; then echo 0; else exec /usr/bin/stat "$@"; fi\n',
        'curl': f'#!/bin/sh\ntouch "{tmp_path / "contacted"}"\nexit 1\n',
    }.items():
        tool = bindir / name
        tool.write_text(body)
        tool.chmod(0o755)
    return subprocess.run(
        ['bash', str(BOOTSTRAP)],
        env={**os.environ, 'PATH': f'{bindir}:{os.environ["PATH"]}',
             'HWMONITOR_BOOTSTRAP_ROOT': str(root)},
        text=True, capture_output=True,
    )


def test_unrecognized_directory_is_not_changed_or_downloaded(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'keep').write_text('user data')
    result = run_bootstrap(tmp_path, root)
    assert result.returncode != 0
    assert (root / 'keep').read_text() == 'user data'
    assert not (tmp_path / 'contacted').exists()


def test_symlinked_marker_is_rejected_before_download(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    original = tmp_path / 'marker'
    original.write_text('original')
    (root / '.hwmonitor-mqtt-bootstrap').symlink_to(original)
    (root / 'sender').write_text('old sender')
    result = run_bootstrap(tmp_path, root)
    assert result.returncode != 0
    assert original.read_text() == 'original'
    assert not (tmp_path / 'contacted').exists()


def test_symlinked_settings_are_rejected_before_download(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    (root / '.hwmonitor-mqtt-bootstrap').touch()
    (root / 'sender').write_text('old sender')
    settings = tmp_path / 'settings'
    settings.write_text('private')
    (root / '.env').symlink_to(settings)
    result = run_bootstrap(tmp_path, root)
    assert result.returncode != 0
    assert settings.read_text() == 'private'
    assert not (tmp_path / 'contacted').exists()


def test_download_failure_preserves_existing_settings_and_sender(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    root.chmod(0o755)
    (root / '.hwmonitor-mqtt-bootstrap').touch()
    (root / 'sender').write_text('old sender')
    (root / '.env').write_text('private settings')
    result = run_bootstrap(tmp_path, root)
    assert result.returncode != 0
    assert (tmp_path / 'contacted').exists()
    assert (root / 'sender').read_text() == 'old sender'
    assert (root / '.env').read_text() == 'private settings'


def test_invalid_commit_stops_before_fetching_source(tmp_path):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for name, body in {
        'id': '#!/bin/sh\necho 0\n',
        'uname': '#!/bin/sh\necho x86_64\n',
        'stat': '#!/bin/sh\nif [ "$2" = "%u" ]; then echo 0; else exec /usr/bin/stat "$@"; fi\n',
        'curl': '''#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
while [ "$1" != "--output" ]; do shift; done
printf '{"sha":"../bad"}' > "$2"
''',
    }.items():
        tool = bindir / name
        tool.write_text(body)
        tool.chmod(0o755)
    calls = tmp_path / 'calls'
    result = subprocess.run(
        ['bash', str(BOOTSTRAP)], text=True, capture_output=True,
        env={**os.environ, 'PATH': f'{bindir}:{os.environ["PATH"]}',
             'HWMONITOR_BOOTSTRAP_ROOT': str(tmp_path / 'root'), 'CALLS': str(calls),
             'PYTHONOPTIMIZE': '1'},
    )
    assert result.returncode != 0
    assert len(calls.read_text().splitlines()) == 1
    assert not (tmp_path / 'root').exists()


@pytest.mark.parametrize('refs', ['', '0000' + 'a' * 40 + ' refs/heads/master\n',
                                  ('003f' + 'a' * 40 + ' refs/heads/master\n') * 2,
                                  '001e# service=git-upload-pack\n0000' + ('003f' + 'a' * 40 + ' refs/heads/master\n') * 2 + '0000',
                                  'not-a-git-advertisement\n003f' + 'a' * 40 + ' refs/heads/master\n',
                                  '001e# service=git-upload-pack\n0000003f' + 'a' * 40 + ' refs/heads/master\n0008xx'])
def test_invalid_fallback_refs_stop_before_source_download(tmp_path, refs):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for name, body in {
        'id': '#!/bin/sh\necho 0\n',
        'uname': '#!/bin/sh\necho x86_64\n',
        'stat': '#!/bin/sh\nif [ "$2" = "%u" ]; then echo 0; else exec /usr/bin/stat "$@"; fi\n',
        'curl': '''#!/bin/sh
url=
while [ "$#" -gt 0 ]; do
  case "$1" in https://*) url=$1 ;; --output) shift; out=$1 ;; esac
  shift
done
case "$url" in
  */commits/master) exit 22 ;;
  *info/refs*) printf '%s' "$REFS" > "$out" ;;
  *) touch "$UNEXPECTED"; exit 1 ;;
esac
''',
    }.items():
        tool = bindir / name
        tool.write_text(body)
        tool.chmod(0o755)
    result = subprocess.run(['bash', str(BOOTSTRAP)], text=True, capture_output=True,
        env={**os.environ, 'PATH': f'{bindir}:{os.environ["PATH"]}',
             'HWMONITOR_BOOTSTRAP_ROOT': str(tmp_path / 'root'), 'REFS': refs,
             'UNEXPECTED': str(tmp_path / 'unexpected')})
    assert result.returncode != 0
    assert 'Invalid source commit' in result.stderr
    assert not (tmp_path / 'unexpected').exists()


@pytest.mark.parametrize('repo_root', ['hwmonitor-mqtt-', 'JWJhome-agent-'])
@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('target', ['linux', 'pve'])
@pytest.mark.parametrize('api_limited', [False, True])
def test_linux_bootstrap_passes_verified_source_to_installer(tmp_path, existing, target, api_limited, repo_root):
    commit = 'a' * 40
    root = tmp_path / 'root'
    if existing:
        root.mkdir()
        root.chmod(0o755)
        (root / '.hwmonitor-mqtt-bootstrap').touch()
        (root / 'sender').write_text('old sender')
        (root / '.env').write_text('private settings')
        (root / '.env').chmod(0o600)
    source = tmp_path / (repo_root + commit)
    (source / 'scripts').mkdir(parents=True)
    (source / 'hwmonitor_mqtt' / 'platforms').mkdir(parents=True)
    (source / 'hwmonitor_mqtt' / 'platforms' / 'detect.py').write_text(
        'from types import SimpleNamespace\n'
        f'def detect_target(): return SimpleNamespace(system="{target}", distribution="debian")\n')
    (source / 'hwmonitor_mqtt' / 'platforms' / 'pve_bootstrap.py').write_text(
        'from pathlib import Path\nimport os\nPath(os.environ["PVE_PREPARED"]).write_text("yes")\n')
    (source / 'docs' / 'configuration').mkdir(parents=True)
    (source / 'docs' / 'configuration' / 'pve.env.example').write_text('example')
    (source / 'scripts' / 'source_linux_install.py').write_text(
        'from pathlib import Path\nimport sys\n'
        'import os\n'
        'assert sys.argv[4] != "pve" or Path(os.environ["PVE_PREPARED"]).exists()\n'
        'Path(sys.argv[2], "activation.ok").write_text(sys.argv[3] + " " + sys.argv[4])\n'
        'Path(sys.argv[2], ".hwmonitor-mqtt-bootstrap").write_text(sys.argv[3])\n')
    archive = tmp_path / 'source.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(source, arcname=source.name)
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for name, body in {
        'id': '#!/bin/sh\necho 0\n',
        'uname': '#!/bin/sh\necho x86_64\n',
        'stat': '#!/bin/sh\nif [ "$2" = "%u" ]; then echo 0; else exec /usr/bin/stat "$@"; fi\n',
        'docker': '#!/bin/sh\nif [ "$1 $2" = "compose version" ]; then exit 0; fi\necho "No such object: sys-agent" >&2\nexit 1\n',
        'curl': '''#!/bin/sh
url=
while [ "$#" -gt 0 ]; do
  case "$1" in https://*) url=$1 ;; --output) shift; out=$1 ;; esac
  shift
done
printf '%s\\n' "$url" >> "$CALLS"
case "$url" in
  */commits/master) if [ "$API_LIMITED" = 1 ]; then exit 22; fi; printf '{"sha":"%s"}' "$COMMIT" > "$out" ;;
  *info/refs*) printf '001e# service=git-upload-pack\n0000003f%s refs/heads/master\n0000' "$COMMIT" > "$out" ;;
  */scripts/source_archive.py) cp "$HELPER" "$out" ;;
  *.tar.gz) cp "$ARCHIVE" "$out" ;;
  *) exit 1 ;;
esac
''',
    }.items():
        tool = bindir / name
        tool.write_text(body)
        tool.chmod(0o755)
    calls = tmp_path / 'calls'
    result = subprocess.run(
        ['bash', str(BOOTSTRAP)], text=True, capture_output=True,
        env={**os.environ, 'PATH': f'{bindir}:{os.environ["PATH"]}',
             'HWMONITOR_BOOTSTRAP_ROOT': str(root), 'CALLS': str(calls),
             'COMMIT': commit, 'API_LIMITED': str(int(api_limited)), 'HELPER': str(BOOTSTRAP.parent / 'scripts/source_archive.py'),
             'ARCHIVE': str(archive), 'PVE_PREPARED': str(tmp_path / 'pve-prepared')},
    )
    assert result.returncode == 0, result.stderr
    assert (root / 'activation.ok').read_text() == commit + ' ' + target
    assert (tmp_path / 'pve-prepared').exists() == (target == 'pve')
    if existing:
        assert (root / '.env').read_text() == 'private settings'
    assert not (root / '.hwmonitor-mqtt-bootstrap-pending').exists()
    requests = calls.read_text().splitlines()
    assert len(requests) == (4 if api_limited else 3)
    assert all(commit in url for url in requests[-2:])


def test_linux_bootstrap_activates_source_and_preserves_existing_settings(tmp_path):
    commit = 'b' * 40
    root = tmp_path / 'root'
    root.mkdir(mode=0o755)
    root.chmod(0o755)
    (root / '.hwmonitor-mqtt-bootstrap').write_text('old version')
    (root / 'sender').write_text('old binary')
    (root / '.env').write_text('BROKER_HOST=broker\n')
    (root / '.env').chmod(0o600)
    source = tmp_path / ('hwmonitor-mqtt-' + commit)
    scripts = source / 'scripts'
    scripts.mkdir(parents=True)
    for name in ('source_linux_install.py', 'source_deploy.py', 'source_docker.py', 'source_config.py'):
        shutil.copy2(BOOTSTRAP.parent / 'scripts' / name, scripts / name)
    platforms = source / 'hwmonitor_mqtt' / 'platforms'
    platforms.mkdir(parents=True)
    (platforms / 'detect.py').write_text(
        'from types import SimpleNamespace\n'
        'def detect_target(): return SimpleNamespace(system="linux", distribution="debian")\n')
    for name in ('Dockerfile.source', 'Dockerfile.source.dockerignore',
                 'docker-compose.linux.yml', 'docker-compose.source.yml'):
        shutil.copy2(BOOTSTRAP.parent / name, source / name)
    archive = tmp_path / 'source.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(source, arcname=source.name)
    state = tmp_path / 'docker-state'
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    tools = {
        'id': '#!/bin/sh\necho 0\n',
        'uname': '#!/bin/sh\necho x86_64\n',
        'stat': '#!/bin/sh\nif [ "$2" = "%u" ]; then echo 0; else exec /usr/bin/stat "$@"; fi\n',
        'systemctl': '#!/bin/sh\nprintf "LoadState=not-found\\nActiveState=inactive\\nUnitFileState=\\n"\n',
        'curl': '''#!/bin/sh
while [ "$#" -gt 0 ]; do
  case "$1" in https://*) url=$1 ;; --output) shift; out=$1 ;; esac
  shift
done
case "$url" in
  */commits/master) printf '{"sha":"%s"}' "$COMMIT" > "$out" ;;
  */scripts/source_archive.py) cp "$HELPER" "$out" ;;
  *.tar.gz) cp "$ARCHIVE" "$out" ;;
  *) exit 1 ;;
esac
''',
        'docker': '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
root = Path(os.environ['HWMONITOR_BOOTSTRAP_ROOT'])
state = Path(os.environ['DOCKER_STATE'])
if args[:2] == ['compose', 'version']:
    sys.exit(0)
if args[0] == 'compose' and 'config' in args:
    print(json.dumps({'services': {'sys_agent': {'build': {'context': str(root), 'dockerfile': 'Dockerfile.source'}}}})); sys.exit(0)
if args[:2] == ['inspect', 'sys-agent']:
    if not state.exists():
        print('No such object: sys-agent', file=sys.stderr); sys.exit(1)
    print(state.read_text()); sys.exit(0)
if args[0] == 'compose' and 'up' in args:
    data = {'Id': 'new-id', 'Image': 'sha256:new', 'RestartCount': 0,
            'State': {'Status': 'running', 'Running': True, 'Pid': 123},
            'Config': {'Image': 'test:latest', 'Labels': {
                'com.docker.compose.project.working_dir': str(root),
                'com.docker.compose.service': 'sys_agent',
                'com.docker.compose.project.config_files': str(root / 'docker-compose.linux.yml') + ',' + str(root / 'docker-compose.source.yml')}}}
    state.write_text(json.dumps(data)); sys.exit(0)
sys.exit(1)
''',
    }
    for name, body in tools.items():
        tool = bindir / name
        tool.write_text(body)
        tool.chmod(0o755)
    result = subprocess.run(
        ['bash', str(BOOTSTRAP)], text=True, capture_output=True, timeout=20,
        env={**os.environ, 'PATH': f'{bindir}:{os.environ["PATH"]}',
             'HWMONITOR_BOOTSTRAP_ROOT': str(root), 'DOCKER_STATE': str(state),
             'COMMIT': commit, 'HELPER': str(BOOTSTRAP.parent / 'scripts/source_archive.py'),
             'ARCHIVE': str(archive)},
    )
    assert result.returncode == 0, result.stderr
    assert (root / '.hwmonitor-mqtt-bootstrap').read_text() == commit + '\n'
    assert (root / '.env').read_text() == 'BROKER_HOST=broker\n'
    assert (root / 'hwmonitor_mqtt' / 'platforms' / 'detect.py').is_file()
    assert state.is_file()
