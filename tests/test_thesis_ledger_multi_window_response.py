"""多窗口 wire 组合保留观测时间和来源事实。"""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.services.thesis_ledger_multi_window_response import build_multi_window_response
from src.services.thesis_ledger_multi_window_fingerprint import multi_window_content_hash


def _fixture():
    value = json.loads((Path(__file__).parent / "fixtures/thesis-ledger-multi-window-hash.json").read_text())["response"]
    observations = value.pop("windowObservations")
    return value, observations


def test_build_preserves_evidence_and_uses_latest_availability_without_mutating_inputs():
    parent, observations = _fixture()
    observations[1]["response"]["bars"][0]["availableAt"] = "2026-05-20T07:01:30Z"
    before = deepcopy((parent, observations))
    result = build_multi_window_response(parent, observations)
    assert (parent, observations) == before
    assert result["bars"][1]["availableAt"] == "2026-05-20T07:01:30Z"
    assert result["windowObservations"] == observations
    assert result["sourcePriceBasis"]["basisScope"] == "request-window"
    assert result["inputFingerprint"] == multi_window_content_hash(result)
    assert result["sourcePriceBasis"]["revision"]["contentHash"] == result["inputFingerprint"]


@pytest.mark.parametrize(
    "change",
    ["price", "identity", "volume_basis", "adjustment", "no_overlap", "time", "future_observation", "missing", "nested"],
)
def test_invalid_observations_do_not_produce_success(change):
    parent, observations = _fixture()
    child = observations[1]["response"]
    if change == "price":
        child["bars"][0]["close"] += 1
    elif change == "identity":
        child["provenance"]["effectivePolicyRevision"] += 1
    elif change == "volume_basis":
        child["sourcePriceBasis"]["volumeBasis"] = "shares"
    elif change == "adjustment":
        child["sourcePriceBasis"]["adjustment"] = "none"
    elif change == "no_overlap":
        child["bars"].pop(0)
    elif change == "time":
        observations[1]["startedAt"] = "2026-05-21T00:00:00Z"
    elif change == "future_observation":
        child["sourcePriceBasis"]["observedAt"] = "2026-05-21T00:00:00Z"
    elif change == "missing":
        child["bars"].pop()
    else:
        child["windowObservations"] = []
    with pytest.raises(ValueError):
        build_multi_window_response(parent, observations)
