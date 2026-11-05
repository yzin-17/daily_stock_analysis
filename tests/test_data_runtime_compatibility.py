"""兼容性指纹包含 SDK/DDL 变化，业务数据与普通 Adapter 代码不影响基线。"""

import json

import pytest

from scripts.data_runtime_compatibility import compatibility_inventory


def make_root(root):
    contract = {"contractVersion": "dsa-data-runtime-v1", "compatibilityInputs": [
        "requirements.txt", "patches/sdk", "src/data/schema.py",
    ]}
    for folder in ("scripts", "patches/sdk", "src/data"):
        (root / folder).mkdir(parents=True)
    (root / "scripts/data-runtime-contract.json").write_text(json.dumps(contract))
    for name in ("requirements.txt", "patches/sdk/native.patch", "src/data/schema.py"):
        (root / name).write_text("controlled-input")


@pytest.mark.parametrize("name", ["requirements.txt", "patches/sdk/native.patch", "src/data/schema.py"])
def test_dependency_native_and_schema_changes_bind_compatibility(tmp_path, name):
    make_root(tmp_path)
    before = compatibility_inventory(tmp_path)
    (tmp_path / name).write_text("changed-input")
    assert compatibility_inventory(tmp_path) != before


def test_data_and_regular_source_do_not_become_build_dependencies(tmp_path):
    make_root(tmp_path)
    before = compatibility_inventory(tmp_path)
    (tmp_path / "src/data/adapter.py").write_text("changed adapter")
    (tmp_path / "data.db").write_text("controlled business state")
    assert compatibility_inventory(tmp_path) == before


def test_missing_input_is_rejected(tmp_path):
    make_root(tmp_path)
    (tmp_path / "requirements.txt").unlink()
    with pytest.raises(ValueError, match="缺失"):
        compatibility_inventory(tmp_path)
