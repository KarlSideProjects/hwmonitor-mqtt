import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/release_version.py'


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], text=True).strip()


def test_first_parent_versions_and_rejects_unrelated_source(tmp_path):
    git(tmp_path, 'init', '-b', 'master')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'commit', '--allow-empty', '-m', 'first')
    first = git(tmp_path, 'rev-parse', 'HEAD')
    git(tmp_path, 'commit', '--allow-empty', '-m', 'docs')
    second = git(tmp_path, 'rev-parse', 'HEAD')
    def version(source):
        return subprocess.run(['python3', str(SCRIPT), '--repo', str(tmp_path), '--master', second, source], capture_output=True, text=True)
    assert version(first).stdout.strip() == '0.1.1'
    assert version(second).stdout.strip() == '0.1.2'
    assert version('a' * 40).returncode != 0
    assert version(first[:8]).returncode != 0


def test_shallow_history_is_rejected(tmp_path):
    origin = tmp_path / 'origin'
    origin.mkdir()
    git(origin, 'init', '-b', 'master')
    git(origin, 'config', 'user.email', 'test@example.invalid')
    git(origin, 'config', 'user.name', 'Test')
    git(origin, 'commit', '--allow-empty', '-m', 'first')
    git(origin, 'commit', '--allow-empty', '-m', 'second')
    clone = tmp_path / 'clone'
    subprocess.check_call(['git', 'clone', '--depth=1', origin.as_uri(), str(clone)], stdout=subprocess.DEVNULL)
    head = git(clone, 'rev-parse', 'HEAD')
    result = subprocess.run(['python3', str(SCRIPT), '--repo', str(clone), '--master', head, head], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'shallow' in result.stderr
