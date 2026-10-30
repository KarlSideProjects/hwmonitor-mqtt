import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('candidate_checker', Path(__file__).resolve().parents[1] / 'scripts/inspect_sender_candidate.py')
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def image_scan(tmp_path, vendor, sender=b'sender', platform='linux-arm64', vendor_path='usr/lib/aarch64-linux-gnu/libgnutls.so.30.34.3'):
    layer = io.BytesIO()
    with tarfile.open(fileobj=layer, mode='w') as tar:
        for name, content in [(vendor_path, vendor), ('app/sender', sender)]:
            item = tarfile.TarInfo(name)
            item.size = len(content)
            tar.addfile(item, io.BytesIO(content))
    layer_data = layer.getvalue()
    config = json.dumps({'architecture': 'arm64' if platform == 'linux-arm64' else 'amd64', 'os': 'linux', 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + hashlib.sha256(layer_data).hexdigest()]}}).encode()
    image = tmp_path / 'image.tar'
    with tarfile.open(image, mode='w') as tar:
        for name, content in [('manifest.json', json.dumps([{'Config': 'config.json', 'Layers': ['layer.tar']}]).encode()), ('config.json', config), ('layer.tar', layer_data)]:
            item = tarfile.TarInfo(name)
            item.size = len(content)
            tar.addfile(item, io.BytesIO(content))
    checker.inspect_image_layers(image, hashlib.sha256(image.read_bytes()).hexdigest(), hashlib.sha256(sender).hexdigest(), platform)


def test_arm_vendor_exception_is_exact_and_scans_other_content(tmp_path, monkeypatch):
    path = 'usr/lib/aarch64-linux-gnu/libgnutls.so.30.34.3'
    public = b'-----BEGIN EC PRIVATE KEY-----\npublic self-test fixture'
    monkeypatch.setattr(checker, 'DEBIAN_ARM_SECRET_PATTERN_FILES', {path: hashlib.sha256(public).hexdigest()}, raising=False)
    image_scan(tmp_path, public)
    for secret in (b'MQTT_PASS=private', b'PVE_TOKEN_SECRET=private', b'-----BEGIN PRIVATE KEY-----'):
        with pytest.raises(ValueError, match='potential secret'):
            image_scan(tmp_path, public + secret)
        with pytest.raises(ValueError, match='potential secret'):
            image_scan(tmp_path, public, sender=b'sender' + secret)
    with pytest.raises(ValueError, match='potential secret'):
        image_scan(tmp_path, public, vendor_path=path + '.other')
    with pytest.raises(ValueError, match='potential secret'):
        image_scan(tmp_path, public, platform='linux-x64')


@pytest.mark.parametrize('path', [
    'usr/sbin/adduser',
    'usr/sbin/pam-auth-update',
    'usr/share/pam-configs/unix',
    'usr/share/perl5/Debconf/DbDriver/LDAP.pm',
])
def test_arm_arch_independent_debian_members_are_exact(tmp_path, monkeypatch, path):
    public = b'password => $public_setting'
    monkeypatch.setitem(checker.DEBIAN_SECRET_PATTERN_FILES, path, hashlib.sha256(public).hexdigest())
    image_scan(tmp_path, public, vendor_path=path)
    for secret in (b'MQTT_PASS=private', b'PVE_TOKEN_SECRET=private', b'-----BEGIN PRIVATE KEY-----'):
        with pytest.raises(ValueError, match='potential secret'):
            image_scan(tmp_path, public + secret, vendor_path=path)
        with pytest.raises(ValueError, match='potential secret'):
            image_scan(tmp_path, public, sender=b'sender' + secret, vendor_path=path)
