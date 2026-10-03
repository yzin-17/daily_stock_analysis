"""证据文件原子发布、寻址和损坏拒绝。"""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pytest

from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore, MAX_BYTES


def test_store_survives_new_instance_and_concurrent_identical_writes(tmp_path):
    store = MappingEvidenceStore(tmp_path)
    raw = b'{"kind":"split-date-mapping"}'
    with ThreadPoolExecutor(max_workers=4) as pool:
        references = list(pool.map(store.put, [raw] * 8))
    assert len(set(references)) == 1
    assert MappingEvidenceStore(tmp_path).read(references[0]) == raw
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("reference", ["../private", "https://example.invalid/a", "sha256:../x", "sha256:" + "A" * 64])
def test_reference_cannot_select_arbitrary_paths(tmp_path, reference):
    with pytest.raises(ValueError):
        MappingEvidenceStore(tmp_path).read(reference)


def test_corruption_is_not_overwritten(tmp_path):
    store = MappingEvidenceStore(tmp_path)
    raw = b'{}'
    reference = store.put(raw)
    path = tmp_path / f"{reference[7:]}.json"
    path.write_bytes(b'[]')
    with pytest.raises(ValueError, match="摘要"):
        store.read(reference)
    with pytest.raises(ValueError, match="摘要"):
        store.put(raw)
    assert path.read_bytes() == b'[]'
    assert not list(tmp_path.glob('.mapping-*'))


def test_missing_symlink_and_oversize_are_rejected(tmp_path):
    store = MappingEvidenceStore(tmp_path)
    raw = b'{}'
    digest = sha256(raw).hexdigest()
    reference = f"sha256:{digest}"
    with pytest.raises(FileNotFoundError):
        store.read(reference)
    outside = tmp_path / 'outside.json'
    outside.write_bytes(raw)
    path = tmp_path / f"{digest}.json"
    path.symlink_to(outside)
    with pytest.raises(OSError):
        store.read(reference)
    with pytest.raises(OSError):
        store.put(raw)
    with pytest.raises(ValueError):
        store.put(b'x' * (MAX_BYTES + 1))
    with pytest.raises(ValueError):
        store.put(b'')
