"""Exercise scope reporting against real Git trees, including the staged candidate."""
import json
from pathlib import Path
import subprocess
import sys


CHECKER = Path(__file__).resolve().parents[1] / 'tools/flow-docs/flow_checker.py'


def test_explain_filters_changes_and_dependencies_without_granting_review(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()

    def checker(*args, check=True):
        return subprocess.run([sys.executable, str(CHECKER), *args], cwd=tmp_path,
                              capture_output=True, text=True, check=check)

    git('init', '-q')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    docs = tmp_path / 'docs'
    docs.mkdir()
    for name in ('a', 'b'):
        (tmp_path / (name + '.txt')).write_text('initial\n')
        (docs / (name + '.mmd')).write_text('flowchart TD\n    A["Read input"]\n')
    manifest = {'version': 1, 'scope': {'include': ['*.txt'], 'exclude': {}},
                'sources': [{'path': n + '.txt', 'kind': 'file'} for n in ('a', 'b')],
                'features': [{'id': n, 'flow': 'docs/' + n + '.mmd',
                              'mappings': [{'node': 'A', 'path': n + '.txt'}],
                              'depends_on': []} for n in ('a', 'b')], 'reviews': []}
    manifest['features'].append({'id': 'dependent', 'flow': 'docs/a.mmd',
                                'mappings': [{'node': 'A', 'path': 'a.txt'}],
                                'depends_on': ['a']})
    (docs / 'flow-manifest.json').write_text(json.dumps(manifest))
    checker('snapshot')
    git('add', '.')
    git('commit', '-qm', 'baseline')
    base = git('rev-parse', 'HEAD')
    assert json.loads(checker('explain', '--base', base).stdout)['affected'] == []
    (tmp_path / 'a.txt').write_text('changed\n')
    checker('snapshot')
    work = json.loads(checker('explain', '--base', base).stdout)
    assert work['affected'] == ['a', 'dependent']
    assert set(work['fingerprints']) == {'a', 'dependent'}
    assert work['base'] == base and work['mode'] == 'worktree'
    assert checker('check', '--base', base, check=False).returncode == 1
    assert json.loads(checker('explain', '--base', base, '--staged').stdout)['affected'] == []
    git('add', '.')
    staged = json.loads(checker('explain', '--base', base, '--staged').stdout)
    assert staged['affected'] == work['affected'] and staged['mode'] == 'index'
    (tmp_path / 'b.txt').write_text('unstaged\n')
    assert json.loads(checker('explain', '--base', base, '--staged').stdout) == staged
    assert checker('explain', '--base', base, check=False).returncode == 1


def policy_repo(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args], text=True).strip()

    def checker(*args):
        return subprocess.run([sys.executable, str(CHECKER), *args], cwd=tmp_path,
                              capture_output=True, text=True)

    git('init', '-q')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs/flow.mmd').write_text('flowchart TD\n    A["Read input"]\n')
    for name in ('a.txt', 'b.txt', 'local.cfg', 'global.cfg'):
        (tmp_path / name).write_text('initial\n')
    manifest = {'version': 1, 'scope': {'include': ['*.txt'], 'exclude': {}},
                'sources': [{'path': n + '.txt', 'kind': 'file'} for n in ('a', 'b')],
                'features': [{'id': n, 'flow': 'docs/flow.mmd',
                              'mappings': [{'node': 'A', 'path': ('b' if n == 'b' else 'a') + '.txt'}],
                              'depends_on': ['a'] if n == 'dependent' else []}
                             for n in ('a', 'b', 'dependent')],
                'policy_inputs': ['global.cfg'], 'reviews': []}
    manifest['features'][0]['policy_inputs'] = ['local.cfg']
    path = tmp_path / 'docs/flow-manifest.json'
    path.write_text(json.dumps(manifest))
    assert checker('snapshot').returncode == 0
    git('add', '.')
    git('commit', '-qm', 'baseline')
    return git, checker, path


def test_feature_policy_hashes_are_local_byte_sensitive_and_not_approval(tmp_path):
    git, checker, _ = policy_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    assert json.loads(checker('explain', '--base', base).stdout)['affected'] == []
    (tmp_path / 'local.cfg').write_text('initial\n# comment only\n')
    result = json.loads(checker('explain', '--base', base).stdout)
    assert result['affected'] == ['a', 'dependent']
    assert 'missing current review' in checker('check', '--base', base).stderr
    assert json.loads(checker('explain', '--base', base, '--staged').stdout)['affected'] == []
    git('add', 'local.cfg')
    assert json.loads(checker('explain', '--base', base, '--staged').stdout)['affected'] == ['a', 'dependent']
    (tmp_path / 'local.cfg').write_text('initial\n')
    (tmp_path / 'global.cfg').write_text('changed\n')
    assert json.loads(checker('explain', '--base', base).stdout)['affected'] == ['a', 'b', 'dependent']
    (tmp_path / 'a.txt').write_text('stale source\n')
    assert 'run snapshot' in checker('explain', '--base', base).stderr


def test_policy_inputs_reject_invalid_types_paths_and_nonfiles(tmp_path):
    git, checker, path = policy_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    original = json.loads(path.read_text())
    (tmp_path / 'link.cfg').symlink_to('local.cfg')
    for local in (False, True):
        for value in ('local.cfg', {'local.cfg': True}, [None], [42], ['../outside'],
                      ['/absolute'], ['docs/../local.cfg'], ['missing.cfg'], ['docs'], ['link.cfg']):
            manifest = json.loads(json.dumps(original))
            owner = manifest['features'][0] if local else manifest
            owner['policy_inputs'] = value
            path.write_text(json.dumps(manifest))
            result = checker('explain', '--base', base)
            assert result.returncode == 1, (local, value, result.stdout)
