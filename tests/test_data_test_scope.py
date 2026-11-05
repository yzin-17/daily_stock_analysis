"""数据收集范围必须覆盖全部测试路径，范围缩减必须有显式证据。"""

import json
import shutil

import pytest

from scripts.data_test_scope import ROOT, SCOPE_PATH, load_scope


@pytest.fixture
def scoped_copy(tmp_path):
    scope = json.loads((ROOT / SCOPE_PATH).read_text())
    files = scope["dataFiles"] + list(scope["partialFiles"])
    files += [name for group in scope["retiredByOwner"].values() for name in group["files"]]
    for name in files:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if name in scope["partialFiles"]:
            shutil.copy2(ROOT / name, target)
        else:
            target.touch()
    target = tmp_path / scope["baselineInventory"]
    target.parent.mkdir(parents=True)
    shutil.copy2(ROOT / scope["baselineInventory"], target)
    (tmp_path / SCOPE_PATH).write_text(json.dumps(scope))
    return tmp_path


def mutate(root, operation):
    path = root / SCOPE_PATH
    scope = json.loads(path.read_text())
    operation(scope)
    path.write_text(json.dumps(scope))


def test_repository_scope_is_complete():
    _, targets = load_scope()
    assert "tests/test_data_config.py" in targets and "tests/test_data_indicator_kernels.py" in targets
    assert "tests/test_data_stock_index_http.py" in targets


def test_new_unregistered_test_cannot_silently_escape_ci(scoped_copy):
    (scoped_copy / "tests/test_unregistered.py").touch()
    with pytest.raises(ValueError, match="未登记"):
        load_scope(scoped_copy)


def test_removed_data_file_does_not_turn_into_a_successful_smaller_suite(scoped_copy):
    (scoped_copy / "tests/test_data_config.py").unlink()
    with pytest.raises(ValueError, match="已丢失"):
        load_scope(scoped_copy)


def test_d0_data_file_retirement_requires_ownership_review(scoped_copy):
    name = "tests/test_tencent_etf_basic_quote.py"

    def change(scope):
        scope["dataFiles"].remove(name)
        scope["retiredByOwner"]["D6"]["files"].append(name)

    mutate(scoped_copy, change)
    with pytest.raises(ValueError, match="所有权复核"):
        load_scope(scoped_copy)


def test_new_assertion_in_partial_file_requires_disposition(scoped_copy):
    path = scoped_copy / "tests/test_searxng_env_docs.py"
    path.write_text(path.read_text() + "\ndef test_unclassified_assertion():\n    pass\n")
    with pytest.raises(ValueError, match="未登记的断言"):
        load_scope(scoped_copy)


@pytest.mark.parametrize("name", ["tests/test_ci_test_shard.py", "tests/test_rebase_legacy_thesis_ledger_policy.py"])
def test_retained_data_maintenance_tests_cannot_silently_retire(scoped_copy, name):
    def change(scope):
        scope["dataFiles"].remove(name)
        scope["retiredByOwner"]["D6"]["files"].append(name)

    mutate(scoped_copy, change)
    with pytest.raises(ValueError, match="所有权复核"):
        load_scope(scoped_copy)
