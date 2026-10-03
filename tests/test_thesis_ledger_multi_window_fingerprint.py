"""用跨仓 golden 验证多窗口编码一致性。"""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.services.thesis_ledger_multi_window_fingerprint import multi_window_content_hash


def _golden():
    path = Path(__file__).parent / "fixtures/thesis-ledger-multi-window-hash.json"
    return json.loads(path.read_text())


def test_shared_golden_and_key_order_numeric_representation():
    golden = _golden()
    response = golden["response"]
    before = deepcopy(response)
    assert multi_window_content_hash(response) == golden["expectedContentHash"]
    assert response == before
    response = dict(reversed(list(response.items())))
    response["bars"][0]["volume"] = float(response["bars"][0]["volume"])
    assert multi_window_content_hash(response) == golden["expectedContentHash"]


def test_child_fingerprint_and_observation_time_affect_hash():
    golden = _golden()
    for field in ["fingerprint", "time"]:
        response = deepcopy(golden["response"])
        item = response["windowObservations"][0]
        if field == "fingerprint":
            item["response"]["inputFingerprint"] = "changed"
        else:
            item["completedAt"] = "2026-05-20T07:03:00Z"
        assert multi_window_content_hash(response) != golden["expectedContentHash"]


def test_nonfinite_number_is_rejected():
    response = _golden()["response"]
    response["bars"][0]["close"] = float("nan")
    with pytest.raises(ValueError):
        multi_window_content_hash(response)
