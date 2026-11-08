import os
from pathlib import Path
import subprocess


BOOTSTRAP = Path(__file__).resolve().parents[1] / 'bootstrap-source.sh'


def run_prerequisites(tmp_path, target, tools):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    for name, body in tools.items():
        path = bindir / name
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    source = BOOTSTRAP.read_text().split('# Begin prerequisites', 1)[1].split('# End prerequisites', 1)[0]
    return subprocess.run(['/bin/bash', '-c', source + '\nensure_source_prerequisites "$TARGET" debian bookworm "$WORK"'],
                          env={**os.environ, 'PATH': str(bindir), 'TARGET': target, 'WORK': str(tmp_path),
                               'CALLS': str(tmp_path / 'calls'), 'BINDIR': str(bindir)},
                          capture_output=True, text=True)


def test_existing_docker_and_compose_do_not_install_packages(tmp_path):
    result = run_prerequisites(tmp_path, 'linux', {
        'docker': 'exit 0',
        'apt-get': 'echo called >> "$CALLS"; exit 1',
    })
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / 'calls').exists()


def test_pve_installs_missing_python_venv_support(tmp_path):
    result = run_prerequisites(tmp_path, 'pve', {
        'dpkg-query': 'exit 1',
        'apt-get': 'echo "$*" >> "$CALLS"; exit 0',
    })
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'calls').read_text().splitlines() == ['update', 'install -y python3-venv']


def test_linux_installs_docker_and_compose_when_missing(tmp_path):
    result = run_prerequisites(tmp_path, 'linux', {
        'dpkg-query': 'exit 1',
        'apt-cache': 'exit 0',
        'apt-get': '''echo "$*" >> "$CALLS"
case "$*" in *docker.io*) printf '#!/bin/sh\\nexit 0\\n' > "$BINDIR/docker"; /usr/bin/chmod +x "$BINDIR/docker" ;; esac
exit 0''',
    })
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'calls').read_text().splitlines() == ['update', 'install -y docker.io docker-compose-v2']


def test_conflicting_runtime_is_not_replaced(tmp_path):
    result = run_prerequisites(tmp_path, 'linux', {
        'dpkg-query': 'echo installed; exit 0',
        'apt-get': 'echo called >> "$CALLS"; exit 1',
    })
    assert result.returncode != 0
    assert 'conflict' in result.stderr.lower()
    assert not (tmp_path / 'calls').exists()


def test_linux_uses_official_docker_repository_when_distribution_packages_are_missing(tmp_path):
    result = run_prerequisites(tmp_path, 'linux', {
        'dpkg-query': 'exit 1',
        'apt-cache': 'exit 1',
        'dpkg': 'echo amd64',
        'install': 'exit 0',
        'curl': '''while [ "$1" != "--output" ]; do shift; done
printf 'official-key' > "$2"''',
        'tee': 'while IFS= read -r line; do echo "$line" >> "$CALLS"; done',
        'apt-get': '''echo "$*" >> "$CALLS"
case "$*" in *docker-ce*) printf '#!/bin/sh\\nexit 0\\n' > "$BINDIR/docker"; /usr/bin/chmod +x "$BINDIR/docker" ;; esac
exit 0''',
    })
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / 'calls').read_text()
    assert 'URIs: https://download.docker.com/linux/debian' in calls
    assert 'Signed-By: /etc/apt/keyrings/hwmonitor-docker.asc' in calls
    assert 'install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin' in calls


def test_failed_repository_write_removes_only_new_repository_files(tmp_path):
    result = run_prerequisites(tmp_path, 'linux', {
        'dpkg-query': 'exit 1',
        'apt-cache': 'exit 1',
        'dpkg': 'echo amd64',
        'install': 'exit 0',
        'curl': 'while [ "$1" != "--output" ]; do shift; done; printf key > "$2"',
        'tee': 'exit 1',
        'rm': 'echo "rm $*" >> "$CALLS"',
        'apt-get': 'echo "$*" >> "$CALLS"; exit 0',
    })
    assert result.returncode != 0
    calls = (tmp_path / 'calls').read_text()
    assert 'rm -f -- /etc/apt/keyrings/hwmonitor-docker.asc /etc/apt/sources.list.d/hwmonitor-docker.sources' in calls
    assert 'install -y docker-ce' not in calls
