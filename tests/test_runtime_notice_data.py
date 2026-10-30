import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_bundled_zstd_notice_and_attributions_are_in_every_runtime_bundle():
    notice_root = ROOT / 'release-notices'
    license_bytes = (notice_root / 'embedded-nuitka/zstd-1.4.7-LICENSE.txt').read_bytes()
    assert hashlib.sha256(license_bytes).hexdigest() == '2c1a7fa704df8f3a606f6fc010b8b5aaebf403f3aeec339a12048f1ba7331a0b'
    attributions = (b'Copyright (c) 2012-2020, Yann Collet, Facebook, Inc.',
                    b'Copyright (c) 2013-2020, Yann Collet, Facebook, Inc.',
                    b'Copyright (c) 2016-2020, Yann Collet, Facebook, Inc.',
                    b'Copyright (c) 2018-2020, Facebook, Inc.',
                    b'Copyright (c) 2016-2020, Facebook, Inc.')
    data = json.loads((notice_root / 'runtime-components.json').read_text())
    sources = data['embedded_compiler_inputs']['nuitka_onefile_zstd']['source_header_sha256']
    assert sources['common/zstd_deps.h'] == 'd8bc76f6ad1e685f72987aee5914c39e2046bd4c9791efa235ffd6f3d41af376'
    for platform in ('linux-x64-cpython-3.11.15-pbs-20260728', 'linux-arm64-cpython-3.11.15-pbs-20260728', 'windows-x64-cpython-3.13.17'):
        bundle = (notice_root / platform / 'runtime-bundle.txt').read_bytes()
        assert license_bytes in bundle
        assert all(line in bundle for line in attributions)


def test_gcc_runtime_terms_are_retained_as_conditional_evidence():
    data = json.loads((ROOT / 'release-notices/runtime-components.json').read_text())
    gcc = data['embedded_compiler_inputs']['gcc_runtime_conditional']
    assert 'not established' in gcc['membership']
    assert 'gcc/crtstuff.c' in gcc['file_terms']['paths']
    assert gcc['file_terms']['license'] == 'GPL-3.0-or-later WITH GCC-exception-3.1'
    for entry in gcc['notices']:
        assert hashlib.sha256((ROOT / entry['path']).read_bytes()).hexdigest() == entry['sha256']
