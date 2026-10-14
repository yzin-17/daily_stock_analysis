"""功能启停使用原 SQLite Control 投影，保留旧 JSON 与精确主备。"""

from copy import deepcopy

import pytest

from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore, normalize_policy_v3
from src.services.thesis_ledger_feature_policy import feature_target_reason


def policy():
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "feature-test", "revision": 1,
            "enabled": True, "routes": [{"key": {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR",
                                               "timeframe": "1d", "adjustment": "qfq"},
                                        "targets": [{"providerId": "tencent", "upstreamSource": "tencent"},
                                                    {"providerId": "akshare", "upstreamSource": "eastmoney"}]}]}


def test_old_policy_is_not_injected_with_enable_fields():
    original = policy()
    assert normalize_policy_v3(original) == original
    assert "enabled" not in normalize_policy_v3(original)["routes"][0]


@pytest.mark.parametrize("enabled", [None, 0, 1, "false"])
def test_boolean_enable_only(enabled):
    value = policy()
    value["routes"][0]["enabled"] = enabled
    with pytest.raises(ControlContractError, match="布尔值"):
        normalize_policy_v3(value)


def test_new_capability_requires_explicit_enable_state():
    value = policy()
    value["routes"][0]["key"] = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "SECURITY_BASIC_DATES"}
    with pytest.raises(ControlContractError, match="明确启停"):
        normalize_policy_v3(value)
    value["routes"][0]["enabled"] = False
    assert normalize_policy_v3(value) == value


@pytest.mark.parametrize("global_enabled,route_enabled", [(False, True), (True, False), (False, False)])
def test_disabled_feature_never_reads_target_readiness(global_enabled, route_enabled):
    calls = []
    assert feature_target_reason({"enabled": global_enabled}, {"enabled": route_enabled}, lambda: calls.append(True)) == "disabled"
    assert not calls


def test_real_control_projection_preserves_targets_and_reenable_checks_current_state(monkeypatch, tmp_path):
    store = ThesisLedgerControlStore(tmp_path / "feature-control.db")
    value = policy()
    targets = deepcopy(value["routes"][0]["targets"])
    value["routes"][0]["enabled"] = False
    reasons = []
    monkeypatch.setattr(store, "_v3_target_reason", lambda *args: reasons.append(True))
    store.apply_policy_v3(value)
    effective = store.effective_policy_v3()
    assert effective["routes"][0]["enabled"] is False
    assert [(item["providerId"], item["upstreamSource"]) for item in effective["routes"][0]["targets"]] == [
        (item["providerId"], item["upstreamSource"]) for item in targets]
    assert all(not item["eligible"] and item["reason"] == "disabled" for item in effective["routes"][0]["targets"])
    assert not reasons
    value["revision"] += 1
    value["routes"][0]["enabled"] = True
    store.apply_policy_v3(value)
    assert reasons
    assert store.effective_policy_v3()["routes"][0]["enabled"] is True
