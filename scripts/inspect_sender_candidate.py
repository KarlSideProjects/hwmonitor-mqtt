#!/usr/bin/env python3
"""Check a proposed unpacked Sender layout; this is not a release approval."""

import argparse
import hashlib
import json
import io
import stat
import tarfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
import re
import sys


PLATFORMS = {'linux-x64', 'linux-arm64', 'windows-x64'}
NOTICE_HASHES = {
    'python': '3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf',
    'paho-mqtt': '3c3ce20f2f04eb331f297297c0cc235bb3d1e4365e2b402377a7481a442d7746',
    'psutil': 'b89c063b3786e28e0c0a38f1931db61fed35e69dd2a2966fbecffee0f46c8d10',
    'python-dotenv': '80619b7049f08c81683ad0e01f08f257a840652dd71ee83146d36658c7d2c2b9',
    'pythonnet': 'e1dc3a6ff017a6800dc27a7220500f512b95fbcd996d38ebbbff85c693245369',
    'wmi': 'df96d90111c405f2393770e6e9d115eed77f836cfc12b7c002132955d09f7b28',
}
COMMON = {'python', 'paho-mqtt', 'psutil', 'python-dotenv'}
SECRET = re.compile(rb'(?i)(?:BROKER_HOST|MQTT_USER|MQTT_PASS|PVE_TOKEN_ID|PVE_TOKEN_SECRET|PASSWORD|API_KEY)["\']?\s*[:=]\s*["\']?[^\s"\']+|-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----')
ARCHIVES = (b'PK\x03\x04', b'\x1f\x8b', b'BZh', b'\xfd7zXZ\x00', b'7z\xbc\xaf\x27\x1c', b'Rar!')


def strict_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique)


def inspect(platform, root, approval):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('candidate must be a real directory')
    if approval.is_symlink() or not approval.is_file() or root.resolve() in approval.resolve().parents:
        raise ValueError('approval must be an independent regular file')
    record = strict_json(approval.read_text(encoding='utf-8'))
    if (not isinstance(record, dict) or record.get('platform') != platform
            or not isinstance(record.get('source'), str) or not re.fullmatch(r'[0-9a-f]{40,64}', record['source'])
            or not isinstance(record.get('version'), str) or not record['version'].strip()
            or not isinstance(record.get('executable_sha256'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', record['executable_sha256'])):
        raise ValueError('invalid approval record')
    licenses = COMMON | ({'pythonnet', 'wmi'} if platform == 'windows-x64' else set())
    required = {'sender.exe' if platform == 'windows-x64' else 'sender'} | {f'licenses/{name}.txt' for name in licenses}
    executable = 'sender.exe' if platform == 'windows-x64' else 'sender'
    magic = b'MZ' if platform == 'windows-x64' else b'\x7fELF'
    found = set()
    for path in root.rglob('*'):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f'link is not allowed: {name}')
        if path.is_dir() and name == 'licenses':
            continue
        if name not in required or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError(f'unapproved entry: {name}')
        data = path.read_bytes()
        if not data or data.startswith(ARCHIVES) or SECRET.search(data) or (name == executable and not data.startswith(magic)):
            raise ValueError(f'unsafe or empty content: {name}')
        if name == executable and hashlib.sha256(data).hexdigest() != record['executable_sha256']:
            raise ValueError('executable differs from approval')
        if name.startswith('licenses/') and hashlib.sha256(data).hexdigest() != NOTICE_HASHES[path.stem]:
            raise ValueError(f'unrecognized notice content: {name}')
        found.add(name)
    if found != required:
        raise ValueError('missing required files: ' + ', '.join(sorted(required - found)))


# Archive path allowlists are intentionally fixed; notice completeness remains a release gate.
LINUX_ARCHIVE_PATHS = {
    '.dockerignore', 'Dockerfile', 'docker-compose.linux.yml', 'docker-compose.pve.yml',
    'docs/configuration/pve.env.example', 'manifest.json', 'sender',
    'licenses/project.md', 'licenses/python.txt', 'licenses/paho-mqtt.txt',
    'licenses/psutil.txt', 'licenses/python-dotenv.txt', 'licenses/typing-extensions.txt',
}
WINDOWS_ARCHIVE_PATHS = {
    'sender.exe', 'manifest.json', 'scripts/windows/install_windows_sender_task.ps1',
    'scripts/windows/start_windows_sender.ps1', 'licenses/project.txt',
    'licenses/cffi-0.txt', 'licenses/cffi-1.txt', 'licenses/clr_loader-0.txt',
    'licenses/edl-v10', 'licenses/epl-v20', 'licenses/paho-mqtt-0.txt',
    'licenses/psutil-0.txt', 'licenses/pycparser-0.txt', 'licenses/python-dotenv-0.txt',
    'licenses/python.txt', 'licenses/pythonnet-0.txt', 'licenses/pywin32-0.txt',
    'licenses/pywin32-1.txt', 'licenses/pywin32-2.txt', 'licenses/pywin32-3.txt',
    'licenses/typing_extensions-0.txt', 'licenses/WMI-0.txt', 'licenses/WMI-1.txt',
    'licenses/openssl-3.5.9-LICENSE.txt', 'licenses/xz-5.2.5-COPYING',
    'licenses/cpython-3.13.17-expat-COPYING',
}


ARCHIVE_NOTICE_HASHES = {'linux-x64': {'licenses/paho-mqtt.txt': '3c3ce20f2f04eb331f297297c0cc235bb3d1e4365e2b402377a7481a442d7746',
               'licenses/project.md': 'ffa814dce11f3be832bacb7dc2511c36c935b77845c4f16a7c9d8cfd3ae8ce36',
               'licenses/psutil.txt': 'b89c063b3786e28e0c0a38f1931db61fed35e69dd2a2966fbecffee0f46c8d10',
               'licenses/python-dotenv.txt': '80619b7049f08c81683ad0e01f08f257a840652dd71ee83146d36658c7d2c2b9',
               'licenses/python.txt': 'e51453a463a8dfed1591cf1a9fe5c704a6c4ec864359f2174f8b7d9bd7e43ad6',
               'licenses/typing-extensions.txt': '3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf'},
 'windows-x64': {'licenses/WMI-0.txt': 'bf90809237a8ce1dac86bed7e34e4e76692d87ffd21aed16541105cd63ea533a',
                 'licenses/WMI-1.txt': 'b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5',
                 'licenses/cffi-0.txt': '2a67a60bbfb33759d67d645ff131b8e6d6bc4cafc232d751fcac3eda4d314cc8',
                 'licenses/cffi-1.txt': '5ba24ddc57067f9249add644c3afc41a5d6dc37e23433ef759d95df370b0af63',
                 'licenses/clr_loader-0.txt': 'b00f9a4b112b44f91276ad8388ed470ad36668ff80f56f15e1e6981c85e6d23d',
                 'licenses/cpython-3.13.17-expat-COPYING': '31b15de82aa19a845156169a17a5488bf597e561b2c318d159ed583139b25e87',
                 'licenses/edl-v10': 'e8cf7d54ea46c19aba793983889b7f7425e1ebfcaaccec764a7db091646e203c',
                 'licenses/epl-v20': '8c349f80764d0648e645f41ef23772a70c995a0924b5235f735f4a3d09df127c',
                 'licenses/openssl-3.5.9-LICENSE.txt': '7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a',
                 'licenses/paho-mqtt-0.txt': '66408b049249c3bdb0ba1ed285f5422ce67e371d40151bebd4d806af454ffe7c',
                 'licenses/project.txt': 'ea9d84a35fc6bbf837ae8b26f8a219fda94fbd75e5752880131c91466702a2d5',
                 'licenses/psutil-0.txt': 'c7adc4d5d1337a548b967421f1fbe258b93033a0417708fd6f4e38f8ecbceb80',
                 'licenses/pycparser-0.txt': '0c846399369ea76ddd7b5c44fe6d16497415fcf015f5cbb508c24bf98b81c5b1',
                 'licenses/python-dotenv-0.txt': '80619b7049f08c81683ad0e01f08f257a840652dd71ee83146d36658c7d2c2b9',
                 'licenses/python.txt': '62bec384df47b0328307db41455ff6ea2559e5546b394ac69148561b21703120',
                 'licenses/pythonnet-0.txt': 'e1dc3a6ff017a6800dc27a7220500f512b95fbcd996d38ebbbff85c693245369',
                 'licenses/pywin32-0.txt': '5ea2f23c7f00c3006bacf267183e816d8d4b6dc95ea57a4ddb0ff81de6d8719e',
                 'licenses/pywin32-1.txt': '11d8462e134e8efe8f4bd52372292a72ff0b670753d2f49f71675eed0dbfd085',
                 'licenses/pywin32-2.txt': '11d8462e134e8efe8f4bd52372292a72ff0b670753d2f49f71675eed0dbfd085',
                 'licenses/pywin32-3.txt': 'c3bdfa1152a9b9d404ddc90b817e1b6945803879ac53a14d2dde30d677f883bd',
                 'licenses/typing_extensions-0.txt': '3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf',
                 'licenses/xz-5.2.5-COPYING': 'bcb02973ef6e87ea73d331b3a80df7748407f17efdb784b61b47e0e610d3bb5c'}}


ARCHIVE_NOTICE_HASHES['linux-arm64'] = {**ARCHIVE_NOTICE_HASHES['linux-x64'], 'licenses/python.txt': '1593433169b6f4abf4f2d9eeb2b6fa3097f92c5e7f530215b6464599bf383c64'}

def check_archive_notices(platform, found):
    expected = ARCHIVE_NOTICE_HASHES.get(platform)
    if expected is None:
        raise ValueError('notice inputs for this platform are not established')
    for name, digest in expected.items():
        if name not in found or hashlib.sha256(found[name]).hexdigest() != digest:
            raise ValueError(f'notice differs from pinned input: {name}')


def check_source_files(found, hashes, paths):
    if not isinstance(hashes, dict) or set(hashes) != paths:
        raise ValueError('invalid trusted source file record')
    for name in paths:
        if not isinstance(hashes[name], str) or hashlib.sha256(found[name]).hexdigest() != hashes[name]:
            raise ValueError(f'file differs from trusted source: {name}')


MAX_ARCHIVE_MEMBERS = 128


def inspect_archive(platform, archive, record):
    """Check archive boundaries before any extraction; not a release approval."""
    if platform not in PLATFORMS or not isinstance(record, dict):
        raise ValueError('unknown platform or invalid build record')
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('invalid archive')
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != record.get('archive_sha256'):
        raise ValueError('archive differs from trusted build record')
    allowed = WINDOWS_ARCHIVE_PATHS if platform == 'windows-x64' else LINUX_ARCHIVE_PATHS
    found = {}
    if platform == 'windows-x64':
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            members = package.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError('too many archive entries')
            entries = [(entry.filename, entry.file_size, entry.is_dir(), stat.S_IFMT(entry.external_attr >> 16), lambda entry=entry: package.read(entry)) for entry in members]
            _inspect_entries(entries, allowed, found)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as package:
            members = []
            for entry in package:
                members.append(entry)
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    raise ValueError('too many archive entries')
            entries = [(entry.name, entry.size, entry.isdir(), 0 if entry.isfile() else -1, lambda entry=entry: package.extractfile(entry).read()) for entry in members]
            _inspect_entries(entries, allowed, found)
    if set(found) != allowed:
        raise ValueError('missing required entries: ' + ', '.join(sorted(allowed - set(found))))
    manifest = strict_json(found['manifest.json'])
    if (not isinstance(manifest, dict) or manifest.get('platform') != platform
            or record.get('platform') != platform or manifest.get('source') != record.get('source') or manifest.get('version') != record.get('version')
            or not isinstance(record.get('source'), str) or not re.fullmatch(r'[0-9a-f]{40}', record['source'])
            or not isinstance(record.get('version'), str)
            or not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', record['version'])):
        raise ValueError('manifest does not match trusted build record')
    source_paths = {name for name in allowed if not name.startswith('licenses/') and name not in ('manifest.json', 'sender', 'sender.exe')}
    check_source_files(found, record.get('source_files'), source_paths)
    executable = found['sender.exe' if platform == 'windows-x64' else 'sender']
    executable_hash = hashlib.sha256(executable).hexdigest()
    if manifest.get('executable_sha256') != executable_hash or record.get('executable_sha256') != executable_hash:
        raise ValueError('executable differs from trusted build record')
    if (not isinstance(manifest.get('compiler'), str) or not manifest['compiler']
            or not isinstance(manifest.get('runtime'), dict) or not manifest['runtime']
            or manifest.get('compiler') != record.get('compiler') or manifest.get('runtime') != record.get('runtime')):
        raise ValueError('compiler or runtime differs from trusted build record')
    if platform == 'windows-x64':
        if len(executable) < 64 or executable[:2] != b'MZ':
            raise ValueError('invalid PE executable')
        offset = int.from_bytes(executable[60:64], 'little')
        if offset + 6 > len(executable) or executable[offset:offset + 6] != b'PE\0\0d\x86':
            raise ValueError('wrong PE architecture')
    elif (len(executable) < 20 or executable[:6] != b'\x7fELF\x02\x01'
          or int.from_bytes(executable[18:20], 'little') != (62 if platform == 'linux-x64' else 183)):
        raise ValueError('wrong ELF architecture')
    if platform == 'windows-x64' and (manifest['compiler'] != 'Nuitka 2.8.9' or manifest['runtime'] != {
        'python': '3.13.17', 'WMI': '1.5.1', 'cffi': '2.0.0', 'clr_loader': '0.2.10',
        'paho-mqtt': '2.1.0', 'psutil': '7.1.1', 'pycparser': '3.0', 'python-dotenv': '1.1.1',
        'pythonnet': '3.0.5', 'pywin32': '311', 'typing_extensions': '4.15.0',
    }):
        raise ValueError('Windows notice pins do not match this runtime')
    if platform in ('linux-x64', 'linux-arm64') and (manifest['compiler'] != 'Nuitka 2.7.16' or manifest['runtime'] != {
        'python': '3.11.15', 'paho-mqtt': '2.1.0', 'psutil': '7.1.1',
        'python-dotenv': '1.1.1', 'typing-extensions': '4.15.0',
    }):
        raise ValueError('Linux notice pins do not match this runtime')
    check_archive_notices(platform, found)
    return found


SAFE_EXAMPLES = {
    'docs/configuration/pve.env.example': '4ea9eaaa155af0dc0dc469fbe86c345311551807e66850842d0228d861d32b23',
}

SAFE_INTERPOLATION = re.compile(rb'(?i)(BROKER_HOST|MQTT_USER|MQTT_PASS|PVE_TOKEN_ID|PVE_TOKEN_SECRET|PASSWORD|API_KEY)\s*[:=]\s*\$\{\1(?::-)?\}')


def _inspect_entries(entries, allowed, found):
    total = 0
    for name, size, directory, kind, read in entries:
        if name not in allowed or name in found or directory or kind not in (0, stat.S_IFREG):
            raise ValueError(f'unapproved archive entry: {name}')
        total += size
        if size <= 0 or size > 128 * 1024 * 1024 or total > 256 * 1024 * 1024:
            raise ValueError(f'invalid archive entry size: {name}')
        content = read()
        known_example = hashlib.sha256(content).hexdigest() == SAFE_EXAMPLES.get(name)
        unsafe = any(not (name.startswith('docker-compose.') and SAFE_INTERPOLATION.fullmatch(match.group())) for match in SECRET.finditer(content))
        if len(content) != size or (unsafe and not known_example):
            raise ValueError(f'unsafe archive entry: {name}')
        found[name] = content


def inspect_linux_assets(platform, root, expected):
    if platform not in ('linux-x64', 'linux-arm64') or root.is_symlink() or not root.is_dir():
        raise ValueError('invalid Linux asset directory')
    name = f'sender-{platform}.tar.gz'
    archive, checksum = root / name, root / (name + '.sha256')
    if any(p.is_symlink() or not p.is_file() or p.stat().st_nlink != 1 for p in (archive, checksum)):
        raise ValueError('invalid Linux assets')
    if archive.stat().st_size > 256 * 1024 * 1024 or checksum.stat().st_size > 256:
        raise ValueError('invalid Linux asset size')
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected or checksum.read_bytes() != f'{digest}  {name}\n'.encode('ascii'):
        raise ValueError('Linux asset checksum mismatch')


def inspect_windows_assets(root, expected):
    names = ('sender-windows-x64.zip', 'validate_sender_package.ps1', 'activate_sender_package.ps1')
    if root.is_symlink() or not root.is_dir() or not isinstance(expected, dict) or set(expected) != set(names):
        raise ValueError('invalid Windows asset record')
    for name in names:
        path = root / name
        checksum = root / (name + '.sha256')
        if any(p.is_symlink() or not p.is_file() or p.stat().st_nlink != 1 for p in (path, checksum)):
            raise ValueError(f'invalid Windows asset: {name}')
        if path.stat().st_size > 256 * 1024 * 1024 or checksum.stat().st_size > 256:
            raise ValueError(f'invalid Windows asset size: {name}')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected[name] or checksum.read_bytes() != f'{digest}  {name}\n'.encode('ascii'):
            raise ValueError(f'Windows asset checksum mismatch: {name}')
        if name.endswith('.ps1') and SECRET.search(path.read_bytes()):
            raise ValueError(f'unsafe Windows asset: {name}')


WINDOWS_DIST_PATHS = {'_asyncio.pyd',
 '_bz2.pyd',
 '_cffi_backend.pyd',
 '_ctypes.pyd',
 '_decimal.pyd',
 '_elementtree.pyd',
 '_hashlib.pyd',
 '_lzma.pyd',
 '_multiprocessing.pyd',
 '_overlapped.pyd',
 '_queue.pyd',
 '_socket.pyd',
 '_ssl.pyd',
 '_uuid.pyd',
 '_win32sysloader.pyd',
 '_wmi.pyd',
 'agent_sender_windows.dll',
 'clr_loader/ffi/dlls/amd64/ClrLoader.dll',
 'libcrypto-3.dll',
 'libffi-8.dll',
 'libssl-3.dll',
 'mfc140u.dll',
 'psutil/_psutil_windows.pyd',
 'pyexpat.pyd',
 'python3.dll',
 'python313.dll',
 'pythoncom313.dll',
 'pythonnet/runtime/Python.Runtime.dll',
 'pywintypes313.dll',
 'select.pyd',
 'unicodedata.pyd',
 'vcruntime140.dll',
 'vcruntime140_1.dll',
 'win32api.pyd',
 'win32event.pyd',
 'win32ui.pyd'}


def inspect_build_evidence(platform, report, inventory, record):
    for path, field in ((report, 'report_sha256'), (inventory, 'inventory_sha256')):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError(f'invalid build evidence: {path.name}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != record.get(field):
            raise ValueError(f'build evidence differs from trusted record: {path.name}')
    report_data = report.read_bytes()
    if b'<!DOCTYPE' in report_data or b'<!ENTITY' in report_data or b'\x00' in report_data:
        raise ValueError('DOCTYPE or unsupported encoding in build report')
    root = ET.fromstring(report_data)
    if root.tag != 'nuitka-compilation-report' or root.get('completion') != 'yes':
        raise ValueError('incomplete build report')
    modules = root.findall('module')
    for item in modules:
        if item.get('name', '').startswith('hwmonitor_mqtt') and item.get('kind') not in ('CompiledPythonModule', 'CompiledPythonPackage'):
            raise ValueError('uncompiled first-party module in report')
    if not any(item.get('name', '').startswith('hwmonitor_mqtt') for item in modules):
        raise ValueError('missing first-party modules in report')
    main_modules = [item for item in modules if item.get('name') == '__main__']
    entrypoint = 'hwmonitor_mqtt/agents/agent_sender_windows.py' if platform == 'windows-x64' else 'sender_entrypoint.py'
    if (len(main_modules) != 1 or main_modules[0].get('kind') != 'PythonMainModule'
            or not main_modules[0].get('source_path', '').replace('\\', '/').endswith('/' + entrypoint)):
        raise ValueError('unexpected compiled entrypoint in report')
    runtime = record.get('runtime')
    distribution_items = root.findall('./distributions/distribution')
    distributions = {item.get('name', '').lower().replace('_', '-'): item.get('version') for item in distribution_items}
    if len(distributions) != len(distribution_items):
        raise ValueError('duplicate distributions in build report')
    if (record.get('compiler') != 'Nuitka ' + root.get('nuitka_version', '')
            or not isinstance(runtime, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in runtime.items())
            or distributions != {k.lower().replace('_', '-'): v for k, v in runtime.items() if k != 'python'}):
        raise ValueError('build report does not match trusted compiler and runtime')
    entries = strict_json(inventory.read_text(encoding='utf-8'))
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise ValueError('invalid runtime inventory')
    names = [item.get('path') for item in entries]
    allowed = WINDOWS_DIST_PATHS if platform == 'windows-x64' else {'sender.bin', 'libpython3.11.so.1.0', 'psutil/_psutil_linux.so', 'psutil/_psutil_posix.so'}
    if any(not isinstance(name, str) for name in names) or len(names) != len(set(names)) or set(names) != allowed:
        raise ValueError('unapproved runtime inventory paths')
    if any(not isinstance(item.get('size'), int) or item['size'] <= 0
           or not isinstance(item.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']) for item in entries):
        raise ValueError('invalid runtime inventory entry')
    return entries


# Exact members of Debian libstdc++6 12.2.0-14+deb12u1 (amd64).
DEBIAN_IMAGE_FILES = {
    'usr/share/gcc/python/libstdcxx/__init__.py': '01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b',
    'usr/share/gcc/python/libstdcxx/v6/__init__.py': 'f904daea3a5c91d7b336377a93a85eaa0060842810230704f4c0b702b4154c6a',
    'usr/share/gcc/python/libstdcxx/v6/printers.py': 'a11707c4086bfff865bb6533fe4a64729cea151955d692f4b7c71d050d09013b',
    'usr/share/gcc/python/libstdcxx/v6/xmethods.py': 'ca0a5c9a7f946784e1bc97407fce251f481bfbbd9813a85d628ad602af4b3847',
    'usr/share/gdb/auto-load/usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.30-gdb.py': '6990e32948295b48e5732bb3eaa49a2833343bdd0b254f5fb4d91ca92aac4168',
}


# Exact Debian Bookworm package members; provenance is recorded in the candidate guide.
DEBIAN_SECRET_PATTERN_FILES = {'usr/bin/gpasswd': 'f5ddf4ff327b07c2e1109645d35b0f75011d20877c9a0c2c1b0678dd1b8c3879',
 'usr/bin/newgrp': 'ced400910c4fe7ab0676ae07ee6489d677baa18e1425750a21b9688ab2e02fb6',
 'usr/lib/x86_64-linux-gnu/libgio-2.0.so.0.7400.6': '5eb29a8a41206d23ea321159267e2799e5426975fb4501a062f90efc4450444d',
 'usr/lib/x86_64-linux-gnu/libglib-2.0.so.0.7400.6': '1708487c60fb32855e9d4aec503f0c32dcb74c6834a839e015ccbd27a606a879',
 'usr/lib/x86_64-linux-gnu/libgnutls.so.30.34.3': '779b25d20249988bea2c1aa6bbeb218f5ae7ea8a9d30ce4f54ea37372965cc4b',
 'usr/lib/x86_64-linux-gnu/libpam.so.0.85.1': '0e43004398e03eb745d07c029ca0dc0361524622c4b384ba34865b0183331e4a',
 'usr/lib/x86_64-linux-gnu/security/pam_exec.so': 'a73022118422ca22932c10c501d3cc31ddb90599be0148749d78d8fbadc993ed',
 'usr/lib/x86_64-linux-gnu/security/pam_stress.so': '2cd9f706a6bb11f5eee1233cd431d5fb9a35558026be6f1f97164f389902e902',
 'usr/lib/x86_64-linux-gnu/security/pam_userdb.so': 'a4bff04056b6efc63df85a17a7f7864c1bacfd25af852fdadadf42bdc023af68',
 'usr/sbin/adduser': 'ad8ec15dc661b2ccb236584721c8395a0dd910151d486b0c1440b715ba1beb70',
 'usr/sbin/pam-auth-update': 'b46dcfcd89682917c2840e0b42ad6cfd0bd18ec4930e58ac840a63356e3cae61',
 'usr/share/pam-configs/unix': '66528ec667294fd2eaa1418ad4372f51e0c9a8fbe628275242276ca070639276',
 'usr/share/perl5/Debconf/DbDriver/LDAP.pm': '633a36005440e5101b822a293751d63e0574787fe2b13157aacc2bc00fb0ef02'}


def check_image_config(data, platform='linux-x64'):
    config = strict_json(data)
    if not isinstance(config, dict) or config.get('os') != 'linux' or config.get('architecture') != {'linux-x64': 'amd64', 'linux-arm64': 'arm64'}.get(platform) or SECRET.search(data):
        raise ValueError('unsafe image configuration')


def inspect_runtime_files(root, entries, platform=None):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('invalid runtime directory')
    expected = {item['path']: item for item in entries}
    seen = set()
    for path in root.rglob('*'):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError(f'link in runtime inventory: {name}')
        if path.is_dir():
            if not any(entry.startswith(name + '/') for entry in expected):
                raise ValueError(f'unapproved runtime directory: {name}')
            continue
        if name not in expected or not path.is_file() or path.stat().st_nlink != 1:
            raise ValueError(f'unapproved runtime inventory entry: {name}')
        item = expected[name]
        if path.stat().st_size != item['size'] or path.stat().st_size > 128 * 1024 * 1024:
            raise ValueError(f'runtime inventory size mismatch: {name}')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != item['sha256']:
            raise ValueError(f'runtime inventory hash mismatch: {name}')
        if platform in ('linux-x64', 'linux-arm64') and (len(content) < 20 or content[:6] != b'\x7fELF\x02\x01'
                or int.from_bytes(content[18:20], 'little') != (62 if platform == 'linux-x64' else 183)):
            raise ValueError(f'wrong runtime architecture: {name}')
        seen.add(name)
    if seen != set(expected):
        raise ValueError('missing runtime inventory files')


MAX_IMAGE_MEMBERS = 200000
MAX_IMAGE_EXPANDED = 2 * 1024 * 1024 * 1024


def inspect_image_layers(image, expected, expected_sender, platform='linux-x64'):
    if image.is_symlink() or not image.is_file() or image.stat().st_size > 2 * 1024 * 1024 * 1024:
        raise ValueError('invalid image archive')
    digest = hashlib.sha256()
    with image.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('image differs from trusted build record')
    with tarfile.open(image, 'r:') as outer:
        members = []
        for member in outer:
            members.append(member)
            if len(members) > MAX_IMAGE_MEMBERS:
                raise ValueError('too many image members')
        names = [member.name for member in members]
        if len(names) != len(set(names)) or 'manifest.json' not in names:
            raise ValueError('invalid image manifest')
        manifest_entry = outer.getmember('manifest.json')
        if not manifest_entry.isfile() or manifest_entry.size > 1024 * 1024:
            raise ValueError('invalid image manifest')
        manifest = strict_json(outer.extractfile(manifest_entry).read())
        if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
            raise ValueError('invalid image manifest')
        config_name = manifest[0].get('Config')
        if not isinstance(config_name, str) or config_name not in names:
            raise ValueError('missing image configuration')
        config_entry = outer.getmember(config_name)
        if not config_entry.isfile() or config_entry.size > 8 * 1024 * 1024:
            raise ValueError('invalid image configuration')
        config_data = outer.extractfile(config_entry).read()
        if config_name.startswith('blobs/sha256/') and config_name != 'blobs/sha256/' + hashlib.sha256(config_data).hexdigest():
            raise ValueError('image configuration digest mismatch')
        check_image_config(config_data, platform)
        layers = manifest[0].get('Layers')
        if not isinstance(layers, list) or not layers or any(not isinstance(name, str) for name in layers) or len(set(layers)) != len(layers):
            raise ValueError('invalid image layers')
        sender_count = 0
        member_count = 0
        expanded_size = 0
        for name in layers:
            entry = outer.getmember(name)
            if not entry.isfile() or entry.size > 1024 * 1024 * 1024:
                raise ValueError('invalid image layer')
            with tarfile.open(fileobj=outer.extractfile(entry), mode='r:*') as layer:
                for item in layer:
                    member_count += 1
                    expanded_size += item.size
                    if member_count > MAX_IMAGE_MEMBERS or expanded_size > MAX_IMAGE_EXPANDED:
                        raise ValueError('image expanded size or member count exceeded')
                    path = PurePosixPath(item.name)
                    if path.is_absolute() or '..' in path.parts:
                        raise ValueError('unsafe image layer path')
                    lowered = item.name.lower()
                    if str(path) in ('.wh.app', '.wh..wh..opq'):
                        raise ValueError('forbidden image app whiteout')
                    if path.parts and path.parts[0] == 'app' and not (str(path) == 'app' and item.isdir()) and str(path) != 'app/sender':
                        raise ValueError(f'forbidden image app path: {item.name}')
                    if str(path) == 'app/sender':
                        sender_count += 1
                        if not item.isfile() or item.size > 128 * 1024 * 1024 or hashlib.sha256(layer.extractfile(item).read()).hexdigest() != expected_sender:
                            raise ValueError('image sender differs from trusted executable')
                    if 'hwmonitor_mqtt' in lowered or lowered.endswith(('.py', '.pyc', '.map', '.o')) or path.name in ('.env', '.git'):
                        expected_file = DEBIAN_IMAGE_FILES.get(str(path)) if platform == 'linux-x64' else None
                        if (not expected_file or not item.isfile() or item.size > 1024 * 1024
                                or hashlib.sha256(layer.extractfile(item).read()).hexdigest() != expected_file):
                            raise ValueError(f'forbidden image layer path: {item.name}')
                    if item.isfile():
                        if item.size > 128 * 1024 * 1024:
                            raise ValueError(f'image layer file too large: {item.name}')
                        content = layer.extractfile(item).read()
                        known_vendor = (platform == 'linux-x64' and hashlib.sha256(content).hexdigest()
                                        == DEBIAN_SECRET_PATTERN_FILES.get(str(path)))
                        if SECRET.search(content) and not known_vendor:
                            raise ValueError(f'potential secret in image layer: {item.name}')
        if sender_count != 1:
            raise ValueError('image must contain exactly one sender')


# Runtime notice/source arrangements must be established in trusted code before release.
APPROVED_NOTICE_SETS = frozenset()


def check_release_notice_readiness(platform, record):
    descriptor = {'platform': platform, 'compiler': record.get('compiler'),
                  'runtime': record.get('runtime'), 'notices': ARCHIVE_NOTICE_HASHES.get(platform)}
    fingerprint = hashlib.sha256(json.dumps(descriptor, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if fingerprint not in APPROVED_NOTICE_SETS:
        raise ValueError('runtime notice and source obligations not established for this exact input set')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-dir', type=Path)
    parser.add_argument('--image-archive', type=Path)
    parser.add_argument('--build-report', type=Path)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--assets', type=Path, help='Windows release asset directory')
    parser.add_argument('--archive', action='store_true', help='inspect the archive boundary (WIP)')
    parser.add_argument('platform', choices=sorted(PLATFORMS))
    parser.add_argument('candidate', type=Path)
    parser.add_argument('approval', type=Path)
    args = parser.parse_args()
    try:
        if args.archive:
            if (args.approval.is_symlink() or not args.approval.is_file()
                    or any(root is not None and args.approval.resolve().is_relative_to(root.resolve())
                           for root in (args.assets, args.runtime_dir))):
                raise ValueError('trusted build record must be an independent regular file')
            record = strict_json(args.approval.read_text(encoding='utf-8'))
            inspect_archive(args.platform, args.candidate, record)
            if args.assets is None or args.build_report is None or args.inventory is None or args.runtime_dir is None:
                raise ValueError('release assets, build report and runtime inventory are required')
            runtime_entries = inspect_build_evidence(args.platform, args.build_report, args.inventory, record)
            inspect_runtime_files(args.runtime_dir, runtime_entries, args.platform)
            if args.platform == 'windows-x64':
                inspect_windows_assets(args.assets, record.get('assets'))
                if record['assets']['sender-windows-x64.zip'] != record.get('archive_sha256'):
                    raise ValueError('Windows archive asset differs from candidate')
            else:
                inspect_linux_assets(args.platform, args.assets, record.get('archive_sha256'))
                if args.image_archive is None:
                    raise ValueError('Linux image archive is required')
                inspect_image_layers(args.image_archive, record.get('image_sha256'), record.get('executable_sha256'), args.platform)
            check_release_notice_readiness(args.platform, record)
        else:
            inspect(args.platform, args.candidate, args.approval)
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile, tarfile.TarError) as error:
        print(f'candidate rejected: {error}', file=sys.stderr)
        return 1
    print('candidate boundary accepted; artifact and license review still required')
    return 0


if __name__ == '__main__':
    sys.exit(main())
