"""Keep the real sender manifest's review invalidation within feature boundaries."""

import importlib.util
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('flow_policy_checker', REPO / 'tools/flow-docs/flow_checker.py')
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


@pytest.fixture
def candidate():
    tree = CHECKER.Tree(REPO)
    # Other WIP edits may still await snapshot; this test concerns input ownership.
    loaded = CHECKER.load(tree, 'docs/flow-manifest.json', verify=False)
    base = 'a' * 40
    return tree, loaded, base, CHECKER.fingerprints(tree, loaded, base)


INSTALLER_FEATURES = {
    'sender.distribution', 'sender.build', 'sender.compiled-lifecycle',
    'sender.source-lifecycle', 'sender.release-orchestration',
}


@pytest.mark.parametrize(('path', 'expected'), [
    ('scripts/release-tests/source_acceptance.py', {
        'sender.build', 'sender.source-lifecycle', 'sender.compiled-lifecycle',
        'sender.release-orchestration',
    }),
    ('scripts/watch_checks.py', {'development.delivery'}),
    ('tests/test_watch_checks.py', {'development.delivery'}),
    ('scripts/github-required-checks.json', {'development.delivery'}),
    ('tests/test_required_checks.py', {'development.delivery'}),
    ('docs/agents/delivery.md', {'development.delivery'}),
    ('scripts/windows/install_windows_sender_task.ps1', INSTALLER_FEATURES),
    ('scripts/windows/start_windows_sender.ps1', INSTALLER_FEATURES),
    ('hwmonitor_mqtt/platforms/install.py', INSTALLER_FEATURES),
    ('hwmonitor_mqtt/platforms/detect.py', INSTALLER_FEATURES),
    ('hwmonitor_mqtt/platforms/pve_bootstrap.py', INSTALLER_FEATURES),
    ('hwmonitor_mqtt/platforms/pve_token.py', INSTALLER_FEATURES),
    ('hwmonitor_mqtt/clients/pve.py', INSTALLER_FEATURES | {'sender.smart-discovery'}),
    ('tests/_pwsh.py', INSTALLER_FEATURES),
    ('tests/test_powershell_syntax.py', INSTALLER_FEATURES),
    ('docs/deployment/installation.md', {
        'sender.distribution', 'sender.compiled-lifecycle', 'sender.source-lifecycle',
    }),
    ('docs/configuration/pve.env.example', {
        'sender.build', 'sender.release-inspection', 'sender.compiled-lifecycle',
        'sender.release-orchestration', 'sender.source-lifecycle',
    }),
    ('uv.lock', {
        'sender.build', 'sender.compiled-lifecycle', 'sender.source-lifecycle',
        'sender.release-orchestration',
    }),
    ('tests/fixtures/release-notices/wmi.txt', {
        'sender.build', 'sender.release-inspection', 'sender.compiled-lifecycle',
        'sender.release-orchestration',
    }),
])
def test_feature_inputs_only_invalidate_their_consumers(candidate, monkeypatch, path, expected):
    tree, loaded, base, before = candidate
    read = tree.read
    monkeypatch.setattr(tree, 'read', lambda name: read(name) + b'\nchanged input\n' if name == path else read(name))
    after = CHECKER.fingerprints(tree, loaded, base)
    assert {feature for feature in before if before[feature] != after[feature]} == expected


@pytest.mark.parametrize('path', [
    'AGENTS.md', 'tools/flow-docs/flow_checker.py', 'docs/flows/contracts.md',
])
def test_common_policy_still_invalidates_every_feature(candidate, monkeypatch, path):
    tree, loaded, base, before = candidate
    read = tree.read
    monkeypatch.setattr(tree, 'read', lambda name: read(name) + b'\nchanged policy\n' if name == path else read(name))
    after = CHECKER.fingerprints(tree, loaded, base)
    assert all(before[feature] != after[feature] for feature in before)


def test_changing_comparison_base_still_invalidates_every_fingerprint(candidate):
    tree, loaded, _, before = candidate
    after = CHECKER.fingerprints(tree, loaded, 'b' * 40)
    assert all(before[feature] != after[feature] for feature in before)
