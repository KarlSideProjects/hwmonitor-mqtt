import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('pve_acceptance', Path(__file__).resolve().parents[1] / 'scripts/release-tests/pve_acceptance.py')
pve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pve)


def test_image_checksum_is_verified_before_creating_disk(tmp_path):
    image = tmp_path / 'image'
    image.write_bytes(b'not a cloud image')
    with pytest.raises(ValueError, match='cloud image checksum'):
        pve.verify_file(image, '0' * 128, 'sha512', 'cloud image')


def test_cleanup_attempts_every_owned_resource_after_failure():
    attempted = []

    def action(name):
        attempted.append(name)
        if name == 'container':
            raise RuntimeError('failure')

    errors = pve.cleanup([('container', lambda: action('container')), ('image', lambda: action('image')), ('listener', lambda: action('listener'))])
    assert attempted == ['container', 'image', 'listener']
    assert errors and 'container' in errors[0]
