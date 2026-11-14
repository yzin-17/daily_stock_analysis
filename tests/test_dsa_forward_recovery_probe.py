"""候选镜像隔离恢复的文件路径保护。"""

import importlib.util
import io
from pathlib import Path
import tarfile
import pytest

spec = importlib.util.spec_from_file_location(
    'forward_probe',
    Path(__file__).resolve().parents[1] / 'scripts/local-runtime/dsa-forward-recovery-probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize('name,kind', [
    ('../escape', tarfile.REGTYPE), ('/escape', tarfile.REGTYPE), ('link', tarfile.SYMTYPE)])
def test_archive_rejects_unsafe_objects(tmp_path, name, kind):
    archive_path = tmp_path / 'data.tar'
    with tarfile.open(archive_path, 'w') as archive:
        item = tarfile.TarInfo(name)
        item.type = kind
        archive.addfile(item)
    target = tmp_path / 'isolated'
    target.mkdir()
    with pytest.raises(ValueError):
        probe.restore_files(archive_path, target)


def test_archive_restores_regular_files_without_replacing_existing_files(tmp_path):
    archive_path = tmp_path / 'data.tar'
    with tarfile.open(archive_path, 'w') as archive:
        item = tarfile.TarInfo('artifacts/fact.json')
        item.size = 2
        archive.addfile(item, io.BytesIO(b'{}'))
    target = tmp_path / 'isolated'
    target.mkdir()
    probe.restore_files(archive_path, target)
    assert (target / 'artifacts/fact.json').read_bytes() == b'{}'
    with pytest.raises(FileExistsError):
        probe.restore_files(archive_path, target)
