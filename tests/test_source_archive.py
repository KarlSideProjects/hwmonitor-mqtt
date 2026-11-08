import io
import tarfile

import pytest

from scripts.source_archive import extract_source


def make_archive(path, entries):
    with tarfile.open(path, 'w:gz') as archive:
        for name, content, kind in entries:
            item = tarfile.TarInfo(name)
            if kind == 'link':
                item.type = tarfile.SYMTYPE
                item.linkname = content
                archive.addfile(item)
            else:
                data = content.encode()
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))


def test_extracts_source_with_one_root(tmp_path):
    archive = tmp_path / 'source.tar.gz'
    make_archive(archive, [('project/hwmonitor_mqtt/__init__.py', '', 'file'),
                           ('project/install.sh', 'ok', 'file')])
    result = extract_source(archive, tmp_path / 'destination')
    assert (result / 'install.sh').read_text() == 'ok'


@pytest.mark.parametrize('entries', [
    [('project/../escape', 'bad', 'file')],
    [('/absolute', 'bad', 'file')],
    [('project/link', '../../outside', 'link')],
    [('project/file', 'a', 'file'), ('project/file', 'b', 'file')],
    [('project/file', 'a', 'file'), ('other/file', 'b', 'file')],
])
def test_rejects_unsafe_archive_before_extracting(tmp_path, entries):
    archive = tmp_path / 'source.tar.gz'
    make_archive(archive, entries)
    destination = tmp_path / 'destination'
    with pytest.raises(ValueError):
        extract_source(archive, destination)
    assert not destination.exists()
