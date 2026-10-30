#!/usr/bin/env python3
"""Produce private inspection inputs in a trusted fixed-source build job."""

import argparse
import hashlib
import json
import re
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

from capture_sender_build import capture
from sender_build_inputs import windows_compiler_evidence
from inspect_sender_candidate import (LINUX_ARCHIVE_PATHS, MAX_ARCHIVE_MEMBERS, PLATFORMS, WINDOWS_ARCHIVE_PATHS,
                                      inspect_build_evidence, inspect_runtime_files, receipt_inputs, strict_json)


def digest(path):
    with path.open('rb') as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def read_manifest(platform, archive):
    if archive.is_symlink() or not archive.is_file() or archive.stat().st_size > 256 * 1024 * 1024:
        raise ValueError('invalid archive')
    if platform == 'windows-x64':
        with zipfile.ZipFile(archive) as package:
            if len(package.infolist()) > MAX_ARCHIVE_MEMBERS:
                raise ValueError('too many archive entries')
            entries = [item for item in package.infolist() if item.filename == 'manifest.json']
            if len(entries) != 1 or entries[0].is_dir() or entries[0].file_size > 1024 * 1024 or stat.S_IFMT(entries[0].external_attr >> 16) not in (0, stat.S_IFREG):
                raise ValueError('invalid archive manifest')
            return strict_json(package.read(entries[0]))
    with tarfile.open(archive, 'r:gz') as package:
        entries = []
        for index, item in enumerate(package):
            if index >= MAX_ARCHIVE_MEMBERS:
                raise ValueError('too many archive entries')
            if item.name == 'manifest.json':
                entries.append(item)
        if len(entries) != 1 or not entries[0].isfile() or entries[0].size > 1024 * 1024:
            raise ValueError('invalid archive manifest')
        return strict_json(package.extractfile(entries[0]).read())


def windows_assets(git, source, assets):
    names = ('sender-windows-x64.zip', 'validate_sender_package.ps1', 'activate_sender_package.ps1')
    result = {name: digest(assets / name) for name in names}
    for name in names[1:]:
        committed = subprocess.check_output(git + ['show', f'{source}:scripts/windows/{name}'])
        if hashlib.sha256(committed).hexdigest() != result[name]:
            raise ValueError(f'asset differs from committed source: {name}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--platform', choices=sorted(PLATFORMS), required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--provenance', type=Path, required=True)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--input-receipt', type=Path)
    parser.add_argument('--compiler-log', type=Path)
    parser.add_argument('--runtime-dir', type=Path, required=True)
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--image', type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--python-version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.assets.resolve()):
        parser.error('build record must be outside release assets')
    git = ['git', '-C', str(args.source_root)]
    if subprocess.check_output(git + ['status', '--porcelain', '--untracked-files=no']).strip():
        parser.error('dirty tracked source checkout')
    source = subprocess.check_output(git + ['rev-parse', 'HEAD'], text=True).strip()
    if not re.fullmatch(r'[a-f0-9]{40}', source) or not re.fullmatch(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)', args.version):
        parser.error('invalid source or version')
    manifest = read_manifest(args.platform, args.archive)
    paths = WINDOWS_ARCHIVE_PATHS if args.platform == 'windows-x64' else LINUX_ARCHIVE_PATHS
    if (not isinstance(manifest, dict) or manifest.get('source') != source or manifest.get('platform') != args.platform
            or manifest.get('version') != args.version or manifest.get('executable_sha256') != digest(args.executable)
            or not isinstance(manifest.get('runtime'), dict) or manifest['runtime'].get('python') != args.python_version):
        parser.error('manifest does not match fixed-source build inputs')
    source_paths = {name for name in paths if not name.startswith('licenses/') and name not in ('manifest.json', 'sender', 'sender.exe', '.dockerignore')}
    source_files = {}
    for name in sorted(source_paths):
        committed = subprocess.check_output(git + ['show', f'{source}:{name}'])
        if (args.source_root / name).read_bytes() != committed:
            parser.error(f'working source bytes differ from commit: {name}')
        source_files[name] = hashlib.sha256(committed).hexdigest()
    if args.platform != 'windows-x64':
        source_files['.dockerignore'] = hashlib.sha256(b'*\n!Dockerfile\n!sender\n').hexdigest()
    record = {key: manifest[key] for key in ('platform', 'source', 'version', 'executable_sha256', 'compiler', 'runtime')}
    record.update(archive_sha256=digest(args.archive), report_sha256=digest(args.report),
                  inventory_sha256=digest(args.inventory), source_files=source_files)
    if args.platform == 'windows-x64':
        try:
            record['assets'] = windows_assets(git, source, args.assets)
        except ValueError as error:
            parser.error(str(error))
    else:
        if args.image is None:
            parser.error('Linux image archive required')
        record['image_sha256'] = digest(args.image)
    if args.provenance.is_symlink() or not args.provenance.is_file() or args.provenance.stat().st_size > 4096:
        parser.error('invalid compiler invocation provenance')
    provenance = strict_json(args.provenance.read_bytes())
    expected_provenance = {key: record[key] for key in ('executable_sha256', 'report_sha256', 'inventory_sha256')}
    if args.platform == 'windows-x64':
        if args.compiler_log is None or args.compiler_log.is_symlink() or not args.compiler_log.is_file() or args.compiler_log.stat().st_size > 64 * 1024 * 1024:
            parser.error('actual compiler log is required')
        expected_provenance['compiler_log_sha256'] = digest(args.compiler_log)
    if provenance != expected_provenance:
        parser.error('compiler invocation provenance mismatch')
    entries = inspect_build_evidence(args.platform, args.report, args.inventory, record)
    with tempfile.TemporaryDirectory(prefix='sender-record-') as directory:
        captured = capture([str(args.executable.resolve())], args.runtime_dir, Path(directory) / 'runtime')
        if captured != entries:
            parser.error('extracted runtime differs from inventory')
    inspect_runtime_files(args.runtime_dir, entries, args.platform)
    if args.input_receipt is None or args.input_receipt.is_symlink() or not args.input_receipt.is_file() or args.input_receipt.stat().st_size > 1024 * 1024:
        parser.error('selected input receipt is required')
    record['selected_inputs'] = strict_json(args.input_receipt.read_bytes())
    receipt_inputs(args.platform, record)
    if args.platform == 'windows-x64':
        compiler = windows_compiler_evidence(args.compiler_log)
        if any(record['selected_inputs']['toolchain'].get(k) != v for k, v in compiler.items()):
            parser.error('receipt compiler differs from actual build')
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
