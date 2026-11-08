"""Keep remote protection names aligned with emitted Actions checks."""

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_required_checks_are_emitted_by_sender_ci():
    policy = json.loads((ROOT / 'scripts/github-required-checks.json').read_text())
    workflow = (ROOT / '.github/workflows/ci.yml').read_text()
    names = set(re.findall(r'^    name: (.+)$', workflow, re.M))
    names.add('flow-docs')
    template = 'Tests (Python ${{ matrix.python-version }})'
    assert template in names
    names.remove(template)
    matrix = re.search(r'python-version: \[([^\n]+)\]', workflow).group(1)
    for version in re.findall(r'"([0-9.]+)"', matrix):
        names.add(f'Tests (Python {version})')
    checks = policy['required_status_checks']['checks']
    required = [check['context'] for check in checks]
    assert len(required) == len(set(required))
    assert set(required) <= names
    assert 'Source bootstrap integration' in required
    assert all(check['app_id'] == 15368 for check in checks)


def test_protection_requires_current_passing_candidate_for_everyone():
    policy = json.loads((ROOT / 'scripts/github-required-checks.json').read_text())
    assert policy['required_status_checks']['strict']
    assert policy['enforce_admins']
    assert not policy['allow_force_pushes']
    assert not policy['allow_deletions']
    assert policy['required_conversation_resolution']
