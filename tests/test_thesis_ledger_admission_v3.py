"""G0 RouteTarget admission persistence and fail-closed V3 Effective gates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from src.services.thesis_ledger_control import (
    CONSUMER_NAMESPACE,
    ThesisLedgerControlStore,
)
from src.services.thesis_ledger_market_v3_adapters import iter_market_v3_bar_adapters
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies


def _target(provider_id: str = "akshare", upstream_source: str = "eastmoney"):
    return {"providerId": provider_id, "upstreamSource": upstream_source}


def _policy(routes):
    return {
        "contractVersion": 3,
        "consumer": CONSUMER_NAMESPACE,
        "requestId": "admission-v3-test",
        "revision": 1,
        "enabled": True,
        "routes": routes,
    }


def _admit(store, key, target, *, valid_from=None, valid_until=None):
    now = datetime.now(timezone.utc)
    starts_at = valid_from or (now - timedelta(minutes=1)).isoformat()
    expires_at = valid_until or (now + timedelta(days=1)).isoformat()
    return store.record_route_admission_v3(
        key=key,
        target=target,
        evidence_ref="fixture://g0/synthetic-bar-probe.json",
        evidence_sha256=sha256(b"synthetic G0 evidence fixture").hexdigest(),
        scope_symbols=["159516.SZ"],
        scope_date_from="2026-05-16",
        scope_date_to="2026-08-09",
        adapter_revision="test-adapter-v1",
        source_revision="test-source-v1",
        credential_revision="test-credential-v1",
        valid_from=starts_at,
        valid_until=expires_at,
        recorded_by="test-fixture",
    )


def _single_route(key=None, target=None):
    default_key, default_target = next(iter_market_v3_bar_adapters())
    return [{"key": key or default_key, "targets": [target or default_target]}]


def _effective_for(store, key, target):
    store.apply_policy_v3(_policy(_single_route(key, target)))
    return store.effective_policy_v3()["routes"][0]["targets"][0]


def test_basic_tencent_routes_are_ready_while_other_adapters_start_pending(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "route-admission-pending.db"))
    policy = {"consumer": CONSUMER_NAMESPACE, "enabled": True}

    with store._connect() as connection:
        configurations = store._configuration(connection)
        reasons = [
            store._v3_target_reason(connection, policy, key, target, configurations)
            for key, target in iter_market_v3_bar_adapters()
        ]

    assert len(reasons) == 17
    assert reasons.count(None) == 3
    assert reasons.count("not_admitted") == 14


def test_tencent_etf_hfq_is_ready_without_manual_adjustment_admission(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "tencent-hfq-independent-admission.db"))
    target = _target("tencent", "tencent")
    qfq_key = {
        "kind": "bar", "market": "CN", "assetType": "ETF",
        "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq",
    }
    none_key = {**qfq_key, "adjustment": "none"}
    hfq_key = {**qfq_key, "adjustment": "hfq"}

    _admit(store, none_key, target)
    _admit(store, qfq_key, target)

    assert store.get_route_admission_v3(key=none_key, target=target) is not None
    assert store.get_route_admission_v3(key=qfq_key, target=target) is not None
    assert store.get_route_admission_v3(key=hfq_key, target=target) is None
    pending = _effective_for(store, hfq_key, target)
    assert pending["eligible"] is True
    assert pending["reason"] is None


def test_explicit_scoped_evidence_admits_only_its_exact_route_target(tmp_path):
    database_path = tmp_path / "route-admission-explicit.db"
    store = ThesisLedgerControlStore(str(database_path))
    key, target = next(iter_market_v3_bar_adapters())
    store.apply_policy_v3(_policy(_single_route(key, target)))

    first = _admit(store, key, target)
    repeated = _admit(
        store,
        key,
        target,
        valid_from=first["validFrom"],
        valid_until=first["validUntil"],
    )
    effective = store.effective_policy_v3()["routes"]

    assert first["status"] == "admitted"
    assert first["evidenceRef"].startswith("fixture://")
    assert len(first["evidenceSha256"]) == 64
    assert first["scopeSymbols"] == ["159516.SZ"]
    assert first["recordVersion"] == repeated["recordVersion"] == 1
    assert len(effective) == 1
    assert effective[0]["targets"][0]["eligible"] is True

    reopened = ThesisLedgerControlStore(str(database_path))
    persisted = reopened.get_route_admission_v3(key=key, target=target)
    assert persisted["admissionState"] == "admitted"
    assert persisted["recordVersion"] == 1


@pytest.mark.parametrize(
    ("operation", "expected_state", "expected_reason"),
    [
        ("invalidate_route_admission_v3", "invalid", "admission_invalid"),
        ("revoke_route_admission_v3", "revoked", "admission_revoked"),
    ],
)
def test_invalid_or_revoked_admission_fails_effective_closed(
    tmp_path, operation, expected_state, expected_reason
):
    store = ThesisLedgerControlStore(str(tmp_path / f"route-admission-{expected_state}.db"))
    key, target = next(iter_market_v3_bar_adapters())
    _effective_for(store, key, target)
    _admit(store, key, target)

    changed = getattr(store, operation)(key=key, target=target, reason="fixture withdrawn")
    current = store.get_route_admission_v3(key=key, target=target)
    effective_target = store.effective_policy_v3()["routes"][0]["targets"][0]

    assert changed["status"] == expected_state
    assert current["admissionState"] == expected_state
    assert effective_target["eligible"] is False
    assert effective_target["reason"] == expected_reason


def test_expired_and_not_yet_valid_admissions_fail_effective_closed(tmp_path):
    now = datetime.now(timezone.utc)
    cases = [
        (
            "expired",
            (now - timedelta(days=2)).isoformat(),
            (now - timedelta(days=1)).isoformat(),
            "expired",
        ),
        (
            "not-yet-valid",
            (now + timedelta(days=1)).isoformat(),
            (now + timedelta(days=2)).isoformat(),
            "not_yet_valid",
        ),
    ]
    for name, starts_at, expires_at, expected_state in cases:
        store = ThesisLedgerControlStore(str(tmp_path / f"route-admission-{name}.db"))
        key, target = next(iter_market_v3_bar_adapters())
        _effective_for(store, key, target)
        _admit(store, key, target, valid_from=starts_at, valid_until=expires_at)

        admission = store.get_route_admission_v3(key=key, target=target)
        effective_target = store.effective_policy_v3()["routes"][0]["targets"][0]

        assert admission["admissionState"] == expected_state
        assert effective_target["eligible"] is False
        assert effective_target["reason"] == f"admission_{expected_state}"


def test_circuit_state_is_execution_only_and_does_not_replace_g0_admission(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "route-admission-circuit.db"))
    key, target = next(iter_market_v3_bar_adapters())
    _effective_for(store, key, target)
    store.record_health(
        target["providerId"],
        "DAILY_BAR",
        key["assetType"],
        state="degraded",
        circuit="open",
        upstream_source=target["upstreamSource"],
    )

    assert store.effective_policy_v3()["routes"][0]["targets"][0]["reason"] == "not_admitted"
    _admit(store, key, target)
    admission = store.get_route_admission_v3(key=key, target=target)
    effective_target = store.effective_policy_v3()["routes"][0]["targets"][0]

    assert admission["admissionState"] == "admitted"
    assert effective_target["eligible"] is False
    assert effective_target["reason"] == "upstream_failure"


def test_wrong_route_identity_does_not_reuse_admission_and_scope_is_bounded(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "route-admission-identity.db"))
    key, target = next(iter_market_v3_bar_adapters())
    _effective_for(store, key, target)
    admission = _admit(store, key, target)
    wrong_key = {**key, "adjustment": "hfq"}

    assert store.get_route_admission_v3(key=wrong_key, target=target) is None
    assert store.get_route_admission_v3(
        key=key, target={"providerId": "akshare", "upstreamSource": "sina"}
    ) is None
    assert route_admission_scope_applies(
        admission,
        symbol="159516.sz",
        date_from="2026-05-20",
        date_to="2026-08-01",
    )
    assert not route_admission_scope_applies(
        admission,
        symbol="159516.SZ",
        date_from="2026-05-15",
        date_to="2026-08-01",
    )
    assert not route_admission_scope_applies(
        admission,
        symbol="510300.SH",
        date_from="2026-05-20",
        date_to="2026-08-01",
    )


def test_admission_metadata_is_required_and_scope_rejects_open_ended_ranges(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "route-admission-validation.db"))
    key, target = next(iter_market_v3_bar_adapters())
    now = datetime.now(timezone.utc)
    base = {
        "key": key,
        "target": target,
        "evidence_ref": "fixture://evidence",
        "evidence_sha256": "a" * 64,
        "scope_symbols": ["159516.SZ"],
        "scope_date_from": "2026-05-16",
        "scope_date_to": "2026-08-09",
        "adapter_revision": "adapter-v1",
        "source_revision": "source-v1",
        "credential_revision": "credential-v1",
        "valid_from": (now - timedelta(minutes=1)).isoformat(),
        "valid_until": (now + timedelta(days=1)).isoformat(),
        "recorded_by": "test-fixture",
    }

    with pytest.raises(ValueError, match="SHA-256"):
        store.record_route_admission_v3(**{**base, "evidence_sha256": "not-a-hash"})
    with pytest.raises(ValueError, match="source, and credential revisions"):
        store.record_route_admission_v3(**{**base, "source_revision": ""})
    with pytest.raises(ValueError, match="scope date boundaries"):
        store.record_route_admission_v3(**{**base, "scope_date_to": "open"})
    with pytest.raises(ValueError, match="must include a timezone"):
        store.record_route_admission_v3(**{**base, "valid_from": "2026-05-16T00:00:00"})
