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


def test_arm_glibc_notice_retains_exact_source_terms():
    inputs = ROOT / 'release-notices/linux-arm64-cpython-3.11.15-pbs-20260728'
    license_bytes = (inputs / 'glibc-2.31-COPYING.LIB').read_bytes()
    header = (inputs / 'glibc-2.31-aarch64-start-notice.txt').read_bytes()
    bundle = (inputs / 'runtime-bundle.txt').read_bytes()
    assert hashlib.sha256(license_bytes).hexdigest() == 'dc626520dcd53a22f727af3ee42c770e56c97a64fe3adb063799d8ab032fe551'
    assert hashlib.sha256(header).hexdigest() == 'b8a6a3a65915eabf1ff46028103092e53d487b0ef81dfadf54e3b355143ff1c1'
    assert b'Copyright (C) 1995-2020 Free Software Foundation, Inc.' in header
    assert b'GNU C Library' in header and b'version 2.1' in header
    assert b'This ARM64 Sender uses the GNU C Library' in bundle
    assert license_bytes in bundle and header in bundle


def test_arm_glibc_notice_map_binds_retained_bytes():
    data = json.loads((ROOT / 'release-notices/runtime-components.json').read_text())
    arm = data['platforms']['linux-arm64']
    for entry in [arm['runtime_bundle'], *arm['glibc_startup']['notices']]:
        assert hashlib.sha256((ROOT / entry['path']).read_bytes()).hexdigest() == entry['sha256']


def test_arm_bundle_contains_exact_musl_notice_and_historical_glibc_terms():
    import hashlib
    root = ROOT / 'release-notices/linux-arm64-cpython-3.11.15-pbs-20260728'
    notice = (ROOT / 'scripts/linux_arm64/startup/LICENSE.musl').read_bytes()
    assert hashlib.sha256(notice).hexdigest() == 'f9bc4423732350eb0b3f7ed7e91d530298476f8fec0c6c427a1c04ade22655af'
    bundle = (root / 'runtime-bundle.txt').read_bytes()
    assert notice in bundle
    assert (root / 'glibc-2.31-COPYING.LIB').read_bytes() in bundle
    assert (root / 'glibc-2.31-aarch64-start-notice.txt').read_bytes() in bundle
