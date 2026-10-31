#!/usr/bin/env python3
"""Record the selected installation archives and observed build identities."""

import argparse
import hashlib
import importlib.metadata
import json
import re
import subprocess
import sys
import tarfile
from email.parser import BytesParser
from pathlib import Path
import zipfile


ARM_STARTUP = {'commit': '0784374d561435f7c787a555aeab8ede699ed298',
               'entry_sha256': '14dcdc316f356e3d51217c1a2d73e7942d852d7f42be3308afce19e927a97999',
               'license_sha256': 'f9bc4423732350eb0b3f7ed7e91d530298476f8fec0c6c427a1c04ade22655af'}


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def normalize(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def archive_metadata(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('invalid package archive')
    if path.name.endswith('.whl'):
        with zipfile.ZipFile(path) as archive:
            entries = [i for i in archive.infolist() if re.fullmatch(r'[^/]+\.dist-info/METADATA', i.filename)]
            if len(entries) != 1 or entries[0].file_size > 1024 * 1024:
                raise ValueError('invalid wheel metadata')
            data = archive.read(entries[0])
    elif path.name.endswith('.tar.gz'):
        with tarfile.open(path, 'r:gz') as archive:
            entries = [i for i in archive if re.fullmatch(r'[^/]+/PKG-INFO', i.name)]
            if len(entries) != 1 or not entries[0].isfile() or entries[0].size > 1024 * 1024:
                raise ValueError('invalid source archive metadata')
            data = archive.extractfile(entries[0]).read()
    else:
        raise ValueError('unsupported selected package archive')
    metadata = BytesParser().parsebytes(data, headersonly=True)
    name, version = metadata.get('Name'), metadata.get('Version')
    if not name or not version or not re.fullmatch(r'[A-Za-z0-9_.-]+', name) or not re.fullmatch(r'[A-Za-z0-9_.+!-]+', version):
        raise ValueError('invalid package identity')
    return normalize(name), version


def arm_startup_from_build(inventory):
    if __package__:
        from .linux_arm64.compiler import verify_link_evidence
    else:
        from linux_arm64.compiler import verify_link_evidence
    try:
        provenance = json.loads((inventory.parent / 'provenance.json').read_text(encoding='utf-8'))
        evidence = provenance['arm_startup']
        if provenance['inventory_sha256'] != digest(inventory) or evidence['startup'] != ARM_STARTUP:
            raise ValueError('ARM startup provenance mismatch')
        verify_link_evidence(inventory.parent / 'arm-startup', evidence)
        return evidence['startup']
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('missing ARM startup provenance') from error


def make_receipt(wheels, runtime, runtime_kind, platform, source, version, inventory, expected, toolchain):
    packages = []
    for path in sorted(wheels.iterdir()):
        name, package_version = archive_metadata(path)
        packages.append({'name': name, 'version': package_version, 'filename': path.name, 'sha256': digest(path)})
    if len(packages) != len(expected) or {p['name']: p['version'] for p in packages} != expected:
        raise ValueError('selected packages differ from installed inputs')
    if runtime.is_symlink() or not runtime.is_file() or runtime.stat().st_size > 1024 * 1024 * 1024:
        raise ValueError('invalid runtime archive')
    result = {'platform': platform, 'source': source, 'version': version, 'inventory_sha256': digest(inventory),
              'runtime_archive': {'kind': runtime_kind, 'filename': runtime.name, 'sha256': digest(runtime)},
              'packages': packages, 'toolchain': toolchain}
    if platform == 'linux-arm64':
        result['startup'] = arm_startup_from_build(inventory)
    return result


def validate_receipt(receipt, platform, source, version, inventory_sha256, expected):
    fields = {'platform', 'source', 'version', 'inventory_sha256', 'runtime_archive', 'packages', 'toolchain'}
    if platform == 'linux-arm64':
        fields.add('startup')
    if not isinstance(receipt, dict) or set(receipt) != fields or (platform == 'linux-arm64' and receipt.get('startup') != ARM_STARTUP):
        raise ValueError('invalid selected input receipt')
    if (not isinstance(source, str) or not re.fullmatch(r'[a-f0-9]{40}', source) or not isinstance(version, str)
            or not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', version)
            or not isinstance(inventory_sha256, str) or not re.fullmatch(r'[a-f0-9]{64}', inventory_sha256)
            or (receipt['platform'], receipt['source'], receipt['version'], receipt['inventory_sha256']) != (platform, source, version, inventory_sha256)):
        raise ValueError('selected input receipt candidate mismatch')
    runtime = receipt['runtime_archive']
    if not isinstance(runtime, dict) or set(runtime) != {'kind', 'filename', 'sha256'} or runtime['kind'] != ('verified-embed-reference' if platform == 'windows-x64' else 'runtime-install'):
        raise ValueError('invalid runtime archive receipt')
    def safe_entry(entry):
        return isinstance(entry.get('filename'), str) and re.fullmatch(r'[A-Za-z0-9_.+%-]+', entry['filename']) and isinstance(entry.get('sha256'), str) and re.fullmatch(r'[a-f0-9]{64}', entry['sha256'])
    if not safe_entry(runtime) or not isinstance(receipt['packages'], list) or not 0 < len(receipt['packages']) <= 100:
        raise ValueError('invalid selected input archives')
    packages = receipt['packages']
    if any(not isinstance(p, dict) or set(p) != {'name', 'version', 'filename', 'sha256'} or not safe_entry(p) or not isinstance(p['name'], str) or not isinstance(p['version'], str) for p in packages):
        raise ValueError('invalid selected package')
    selected = {p['name']: p['version'] for p in packages}
    if len(selected) != len(packages) or len({p['filename'] for p in packages}) != len(packages) or any(selected.get(name) != version for name, version in expected.items()):
        raise ValueError('selected packages differ from expected inputs')
    if not isinstance(receipt['toolchain'], dict) or not receipt['toolchain'] or any(not isinstance(k, str) or not isinstance(v, str) or not v or len(v) > 512 for k, v in receipt['toolchain'].items()):
        raise ValueError('invalid toolchain identity')
    result = {'runtime_archive': runtime, 'packages': packages, 'toolchain': receipt['toolchain']}
    if platform == 'linux-arm64':
        result['startup'] = receipt['startup']
    return result



def windows_compiler_evidence(log):
    if log.is_symlink() or not log.is_file() or log.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('invalid compiler log')
    lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
    found = [match.groups() for line in lines if (match := re.fullmatch(r'Scons: Compiler used (.+?) \(MSVC ([0-9.]+)\)\s*', line))]
    if len(found) != 2 or found[0] != found[1]:
        raise ValueError('backend and onefile compiler identities do not match')
    path, version = found[0]
    compiler = Path(path)
    if not re.search(r'(?i)[\\/]VC[\\/]Tools[\\/]MSVC[\\/]14\.44\.35207[\\/]bin[\\/]Hostx64[\\/]x64[\\/]cl\.exe$', path) or not compiler.is_file():
        raise ValueError('actual compiler does not match the supported toolset')
    return {'compiler': f'{path} (MSVC {version})', 'compiler_sha256': digest(compiler)}


def verify_windows_compiler(toolchain, provenance, log):
    compiler = windows_compiler_evidence(log)
    if provenance.get('compiler_log_sha256') != digest(log) or any(toolchain.get(k) != v for k, v in compiler.items()):
        raise ValueError('receipt compiler differs from actual build')


def observed_toolchain(compiler_log=None, arm=False):
    python = {'python_sha256': digest(Path(sys.executable)), 'python_version': sys.version}
    if sys.platform == 'win32':
        if compiler_log is None:
            raise ValueError('actual compiler log is required')
        return {**windows_compiler_evidence(compiler_log), **python}
    packages = subprocess.check_output(['dpkg-query', '-W', '-f=${Package}=${Version}\n', 'gcc', 'gcc-10', 'libgcc-10-dev', 'libgcc-s1', 'binutils'], text=True).strip()
    gcc = '/usr/bin/gcc' if arm else 'gcc'
    compiler = Path(subprocess.check_output([gcc, '-print-prog-name=cc1'], text=True).strip())
    if not compiler.is_file():
        raise ValueError('compiler identity unavailable')
    return {'packages': packages, 'compiler_sha256': digest(compiler), 'compiler_version': subprocess.check_output([gcc, '-dumpfullversion'], text=True).strip(), **python}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--runtime-kind', choices=('runtime-install', 'verified-embed-reference'), required=True)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--compiler-log', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    expected = {}
    for path in args.packages.iterdir():
        name, version = archive_metadata(path)
        if name in expected or importlib.metadata.version(name) != version:
            raise ValueError('installed distribution differs from selected archive')
        expected[name] = version
    toolchain = observed_toolchain(args.compiler_log, arm=args.platform == 'linux-arm64')
    receipt = make_receipt(args.packages, args.runtime, args.runtime_kind, args.platform, args.source,
                           args.version, args.inventory, expected, toolchain)
    validate_receipt(receipt, args.platform, args.source, args.version, digest(args.inventory), expected)
    args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
