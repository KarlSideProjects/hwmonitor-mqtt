"""Extract a bounded GitHub source archive into a new private staging directory."""

from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile


def extract_source(archive, destination):
    destination = Path(destination)
    with tarfile.open(archive, 'r:gz') as source:
        members = []
        paths = set()
        roots = set()
        total = 0
        for member in source:
            members.append(member)
            if len(members) > 50000:
                raise ValueError('invalid archive member count')
            name = member.name.rstrip('/')
            path = PurePosixPath(name)
            if (not name or name.startswith('/') or '\\' in name
                    or any(part in ('', '.', '..') for part in name.split('/'))
                    or not (member.isfile() or member.isdir())):
                raise ValueError('unsafe archive member')
            if path in paths:
                raise ValueError('duplicate archive member')
            paths.add(path)
            roots.add(path.parts[0])
            total += member.size
            if member.size > 128 * 1024 * 1024 or total > 512 * 1024 * 1024:
                raise ValueError('archive exceeds size limit')
        if not members or len(roots) != 1:
            raise ValueError('archive must have one root directory')
        root = next(iter(roots))
        if PurePosixPath(root) in paths and not next(
            member for member in members if member.name.rstrip('/') == root
        ).isdir():
            raise ValueError('archive root must be a directory')
        destination.mkdir(mode=0o700)
        try:
            for member in members:
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(mode=0o755, parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                with source.extractfile(member) as reader, target.open('xb') as writer:
                    shutil.copyfileobj(reader, writer)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
        except BaseException:
            shutil.rmtree(destination)
            raise
    return destination / root


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit('usage: source_archive.py ARCHIVE NEW_DESTINATION')
    print(extract_source(sys.argv[1], sys.argv[2]))
