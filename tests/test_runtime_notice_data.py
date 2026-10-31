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


def test_windows_cffi_libffi_file_notices_are_bundled():
    notice = (ROOT / 'release-notices/windows-x64-cpython-3.13.17/cffi-2.0.0-libffi-notices.txt').read_bytes()
    bundle = (ROOT / 'release-notices/windows-x64-cpython-3.13.17/runtime-bundle.txt').read_bytes()
    assert hashlib.sha256(notice).hexdigest() == '6145f9941c8c3991d0987726127e3079f810576c5398fa691ab2de0094ccb4b2'
    for attribution in (b'Copyright (c) 1996, 1998, 1999, 2001  Red Hat, Inc.',
                        b'Copyright (c) 2002  Ranjit Mathew',
                        b'Copyright (c) 2002  Bo Thorsen',
                        b'Copyright (c) 2002  Roger Sayle',
                        b'Copyright (c) 1996-2003  Red Hat, Inc.',
                        b'Copyright (c) 1996, 1998  Red Hat, Inc.',
                        b'Copyright (c) 1996  Red Hat, Inc.'):
        assert attribution in notice
    assert b'The above copyright notice and this permission notice shall be included' in notice
    assert b'IN NO EVENT SHALL CYGNUS SOLUTIONS BE LIABLE' in notice
    assert notice in bundle
