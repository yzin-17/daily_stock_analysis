"""ThesisLedger Control Contract V3 严格准入回归。"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3
from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "stock_analysis.db"))
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "control-token")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "0123456789abcdef-secret-key")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    return TestClient(app)


def _fixture(name: str):
    fixture_root = (
        Path(__file__).resolve().parents[2]
        / "thesis-ledger"
        / "packages"
        / "schemas"
        / "fixtures"
    )
    return json.loads((fixture_root / name).read_text(encoding="utf-8"))


def _v3_policy(*, revision=1, enabled=True, request_id="control-v3-test", routes=None):
    return {
        "contractVersion": 3,
        "consumer": "thesis-ledger",
        "requestId": request_id,
        "revision": revision,
        "enabled": enabled,
        "routes": routes
        if routes is not None
        else [
            {
                "key": {
                    "kind": "bar",
                    "market": "CN",
                    "assetType": "ETF",
                    "capability": "DAILY_BAR",
                    "timeframe": "1d",
                    "adjustment": "qfq",
                },
                "targets": [
                    {"providerId": "akshare", "upstreamSource": "eastmoney"}
                ],
            }
        ],
    }


def _assert_route_identity_alignment(desired_routes, effective_routes):
    assert len(effective_routes) == len(desired_routes)
    for desired_route, effective_route in zip(desired_routes, effective_routes, strict=True):
        assert set(effective_route) == {"key", "targets", "reason"}
        assert effective_route["key"] == desired_route["key"]
        assert len(effective_route["targets"]) == len(desired_route["targets"])
        for index, (desired_target, effective_target) in enumerate(
            zip(desired_route["targets"], effective_route["targets"], strict=True)
        ):
            assert set(effective_target) == {
                "providerId", "upstreamSource", "routeIndex", "eligible", "reason"
            }
            assert effective_target["providerId"] == desired_target["providerId"]
            assert effective_target["upstreamSource"] == desired_target["upstreamSource"]
            assert effective_target["routeIndex"] == index
            assert effective_target["eligible"] is (effective_target["reason"] is None)
        if any(target["eligible"] for target in effective_route["targets"]):
            assert effective_route["reason"] is None
        else:
            assert effective_route["reason"] is not None


def test_provider_changes_return_only_current_policy_projection(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "provider-v3.sqlite"))
    store.apply_policy_v3(_v3_policy())
    saved = store.save_provider_config(
        "akshare", {"requestId": "save-current-provider", "enabled": True, "settings": {}},
    )
    assert saved["effective"]["contractVersion"] == 3
    assert saved["effective"]["sourceDesiredRevision"] == 1
    assert saved["effective"]["routes"][0]["key"] == _v3_policy()["routes"][0]["key"]

    removed = store.remove_provider("akshare", {"requestId": "remove-current-provider"})
    assert removed["effective"]["contractVersion"] == 3
    assert removed["effective"]["routes"][0]["targets"][0]["reason"] == "disabled"
    with store._connect() as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='thesis_ledger_policy_state_v3'"
        ).fetchone() is None
        assert connection.execute(
            "SELECT revision FROM thesis_ledger_policy_state WHERE consumer='thesis-ledger'"
        ).fetchone()[0] == 1


def test_current_policy_same_revision_is_atomic_across_connections(tmp_path):
    database_path = str(tmp_path / "policy-race-v3.sqlite")
    barrier = Barrier(2)

    def apply(enabled: bool):
        store = ThesisLedgerControlStore(database_path)
        barrier.wait(timeout=5)
        try:
            return store.apply_policy_v3(_v3_policy(enabled=enabled))
        except ControlContractError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(apply, True)
        second = pool.submit(apply, False)
        results = [first.result(timeout=10), second.result(timeout=10)]
    assert sum(isinstance(result, ControlContractError) for result in results) == 1
    assert next(
        result.code for result in results if isinstance(result, ControlContractError)
    ) == "REVISION_CONFLICT"
    projection = ThesisLedgerControlStore(database_path).policy_projection_v3()
    assert projection is not None
    assert projection["desired"]["enabled"] in {True, False}
    assert projection["effective"]["sourceDesiredRevision"] == 1


def test_old_policy_row_is_rejected_before_current_write(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "old-policy-row.sqlite"))
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO thesis_ledger_policy_state "
            "(consumer, revision, enabled, routes_json, status, effective_json, "
            "last_error_json, request_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("thesis-ledger", 1, 1, "{}", "applied", '{"contractVersion":1}',
             None, "old", "2026-09-29T00:00:00+00:00"),
        )
    for operation in (store.policy_projection_v3, lambda: store.apply_policy_v3(_v3_policy())):
        try:
            operation()
        except ControlContractError as exc:
            assert exc.code == "UNSUPPORTED_STORED_POLICY"
        else:
            raise AssertionError("旧策略行必须前置拒绝")
    with store._connect() as connection:
        assert connection.execute(
            "SELECT routes_json FROM thesis_ledger_policy_state WHERE consumer='thesis-ledger'"
        ).fetchone()[0] == "{}"


def test_v3_handshake_matches_shared_fixture_and_rejects_old_versions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    request = _fixture("market-control-v3.handshake.request.json")
    response = client.post(
        "/api/v3/thesis-ledger/control/handshake", headers=headers, json=request
    )

    assert response.status_code == 200, response.text
    assert response.json() == _fixture("market-control-v3.handshake.response.json")

    for version in (1, 2):
        legacy = client.post(
            "/api/v3/thesis-ledger/control/handshake",
            headers=headers,
            json={
                "contractVersion": version,
                "consumer": "thesis-ledger",
                "requestId": f"legacy-v{version}",
                "supportedVersions": [version],
            },
        )
        assert legacy.status_code == 422, legacy.text
        assert legacy.json()["detail"]["code"] == "CONTROL_CONTRACT_UNSUPPORTED"
        assert legacy.json()["detail"]["supportedVersions"] == [3]


def test_v3_handshake_rejects_unsupported_versions_with_safe_error(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    response = client.post(
        "/api/v3/thesis-ledger/control/handshake",
        headers=headers,
        json={
            "contractVersion": 3,
            "consumer": "thesis-ledger",
            "requestId": "market-control-v3-handshake-fixture",
            "supportedVersions": [2],
        },
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    fixture_error = _fixture("market-control-v3.errors.json")[0]
    for field in ("code", "requestId", "diagnosticId"):
        assert detail[field] == fixture_error[field]
    assert detail["code"] == "CONTROL_CONTRACT_UNSUPPORTED"
    assert detail["message"] == "没有共同的 Control Contract 版本"
    assert detail["contractVersion"] == 3
    assert detail["supportedVersions"] == [3]


def test_v3_policy_apply_matches_wire_and_is_default_read(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    fixture_effective = _fixture("market-control-v3.effective.etf-qfq.json")
    desired = {
        key: fixture_effective[key]
        for key in ("contractVersion", "consumer", "requestId", "revision", "enabled")
    }
    desired["routes"] = [
        {"key": route["key"], "targets": [
            {"providerId": target["providerId"], "upstreamSource": target["upstreamSource"]}
            for target in route["targets"]
        ]}
        for route in fixture_effective["routes"]
    ]

    applied = client.post(
        "/api/v3/thesis-ledger/control/policies/apply", headers=headers, json=desired
    )
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["status"] == "applied"
    assert payload["desired"] == desired
    assert payload["requestId"] == desired["requestId"]
    effective = payload["effective"]
    assert set(effective) == {
        "contractVersion",
        "consumer",
        "requestId",
        "revision",
        "sourceDesiredRevision",
        "enabled",
        "routes",
        "appliedAt",
    }
    assert effective["contractVersion"] == 3
    assert effective["requestId"] == desired["requestId"]
    assert effective["revision"] == desired["revision"]
    assert effective["sourceDesiredRevision"] == desired["revision"]
    assert effective["routes"][0]["key"] == fixture_effective["routes"][0]["key"]
    assert effective["routes"][0]["targets"][0]["routeIndex"] == 0
    assert effective["routes"][0]["targets"][0]["eligible"] is False
    assert effective["routes"][0]["targets"][0]["reason"] == "not_adapted"
    _assert_route_identity_alignment(desired["routes"], effective["routes"])

    explicit_v3 = client.get(
        "/api/v3/thesis-ledger/control/policies/effective?contractVersion=3",
        headers=headers,
    )
    assert explicit_v3.status_code == 200
    assert explicit_v3.json()["contractVersion"] == 3
    projected_effective = explicit_v3.json()["projection"]["effective"]
    assert projected_effective["requestId"] == desired["requestId"]
    _assert_route_identity_alignment(desired["routes"], projected_effective["routes"])

    current_default = client.get(
        "/api/v3/thesis-ledger/control/policies/effective", headers=headers
    )
    assert current_default.status_code == 200
    assert current_default.json()["contractVersion"] == 3
    default_effective = current_default.json()["projection"]["effective"]
    assert default_effective["requestId"] == projected_effective["requestId"]
    _assert_route_identity_alignment(desired["routes"], default_effective["routes"])
    old_version = client.get(
        "/api/v3/thesis-ledger/control/policies/effective?contractVersion=2",
        headers=headers,
    )
    assert old_version.status_code == 422
    assert old_version.json()["detail"]["code"] == "CONTROL_CONTRACT_UNSUPPORTED"


def test_v3_route_catalog_is_exact_complete_and_independent_of_policy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    assert client.get(
        "/api/v1/thesis-ledger/control/routes/capabilities?contractVersion=3",
        headers=headers,
    ).status_code == 404

    missing_version = client.get(
        "/api/v3/thesis-ledger/control/routes/capabilities", headers=headers
    )
    wrong_version = client.get(
        "/api/v3/thesis-ledger/control/routes/capabilities?contractVersion=2",
        headers=headers,
    )
    assert missing_version.status_code == 422
    assert wrong_version.status_code == 422
    assert missing_version.json()["detail"]["contractVersion"] == 3
    assert missing_version.json()["detail"]["code"] == "CONTROL_CONTRACT_UNSUPPORTED"

    url = "/api/v3/thesis-ledger/control/routes/capabilities?contractVersion=3"
    response = client.get(url, headers=headers)
    repeated = client.get(url, headers=headers)

    assert response.status_code == 200, response.text
    catalog = response.json()
    assert set(catalog) == {
        "contractVersion", "consumer", "catalogRevision", "generatedAt", "integrity", "entries"
    }
    assert catalog["contractVersion"] == 3
    assert catalog["consumer"] == "thesis-ledger"
    assert catalog["integrity"] == "complete"
    assert catalog["catalogRevision"] > 0
    assert catalog["catalogRevision"] == repeated.json()["catalogRevision"]
    assert all(set(entry) == {"key", "target", "state"} for entry in catalog["entries"])
    bars = [entry for entry in catalog["entries"] if entry["key"]["kind"] == "bar"]
    events = [
        entry for entry in catalog["entries"]
        if entry["key"]["kind"] == "data"
        and entry["key"]["capability"] in {"CASH_DISTRIBUTION", "SPLIT_EVENT"}
    ]
    quote_targets = [
        entry for entry in catalog["entries"]
        if entry["key"]["kind"] == "data"
        and entry["key"]["capability"] == "REALTIME_QUOTE"
    ]
    assert {
        (entry["key"]["assetType"], entry["target"]["providerId"],
         entry["target"]["upstreamSource"])
        for entry in quote_targets
    } >= {
        ("ETF", "hithink", "fund-market-snapshot"),
        ("STOCK", "hithink", "a-share-prices-snapshot"),
        ("ETF", "efinance", "eastmoney"),
        ("STOCK", "akshare", "eastmoney"),
    }
    assert all(entry["state"] == "not_admitted" for entry in quote_targets)
    assert events == [{
        "key": {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"},
        "target": {"providerId": "akshare", "upstreamSource": "eastmoney"}, "state": "not_admitted",
    }, {
        "key": {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "SPLIT_EVENT"},
        "target": {"providerId": "akshare", "upstreamSource": "eastmoney"}, "state": "not_admitted",
    }, {
        "key": {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"},
        "target": {"providerId": "rqdata", "upstreamSource": "rqdata"}, "state": "not_admitted",
    }, {
        "key": {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "SPLIT_EVENT"},
        "target": {"providerId": "rqdata", "upstreamSource": "rqdata"}, "state": "not_admitted",
    }, {
        "key": {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"},
        "target": {"providerId": "tushare", "upstreamSource": "tushare"}, "state": "not_admitted",
    }, {
        "key": {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"},
        "target": {"providerId": "hithink", "upstreamSource": "fund-corporate-actions-dividends"},
        "state": "not_admitted",
    }]
    assert all(
        set(entry["key"]) == {
            "kind", "market", "assetType", "capability", "timeframe", "adjustment"
        }
        and set(entry["target"]) == {"providerId", "upstreamSource"}
        for entry in bars
    )

    rows = {
        (
            entry["key"]["assetType"],
            entry["key"]["adjustment"],
            entry["target"]["providerId"],
            entry["target"]["upstreamSource"],
        ): entry["state"]
        for entry in bars
    }
    expected = {
        ("STOCK", adjustment, "akshare", source)
        for adjustment in ("none", "qfq", "hfq")
        for source in ("eastmoney", "sina", "tencent")
    }
    expected |= {
        ("ETF", adjustment, "akshare", "eastmoney")
        for adjustment in ("none", "qfq", "hfq")
    }
    expected |= {
        (asset_type, adjustment, "tencent", "tencent")
        for asset_type in ("STOCK", "ETF")
        for adjustment in ("none", "qfq")
    }
    expected.add(("ETF", "hfq", "tencent", "tencent"))
    expected.add(("ETF", "qfq", "hithink", "fund-market-historical"))
    expected.add(("ETF", "none", "tushare", "tushare"))
    expected |= {
        ("STOCK", adjustment, "hithink", "hithink-financial-api")
        for adjustment in ("none", "qfq", "hfq")
    }
    assert set(rows) == expected
    assert len(bars) == len(rows) == 22
    assert len(catalog["entries"]) > len(bars) + len(events)
    basic_ready = {("ETF", adjustment, "tencent", "tencent") for adjustment in ("none", "qfq", "hfq")}
    basic_ready.add(("ETF", "qfq", "hithink", "fund-market-historical"))
    assert {key for key, state in rows.items() if state == "ready"} == basic_ready
    assert all(state == "not_admitted" for key, state in rows.items() if key not in basic_ready)
    assert all(entry["key"]["market"] == "CN" for entry in catalog["entries"])
    assert all(entry["key"]["timeframe"] == "1d" for entry in bars)


def test_v3_effective_readiness_uses_exact_adapter_routes(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "v3-exact-adapters.db"))
    unsupported = _v3_policy(
        routes=[
            {
                "key": {
                    "kind": "bar",
                    "market": "CN",
                    "assetType": "ETF",
                    "capability": "DAILY_BAR",
                    "timeframe": "1d",
                    "adjustment": "qfq",
                },
                "targets": [
                    {"providerId": "akshare", "upstreamSource": "tencent"}
                ],
            }
        ]
    )

    applied = store.apply_policy_v3(unsupported)

    target = applied["effective"]["routes"][0]["targets"][0]
    assert target["eligible"] is False
    assert target["reason"] == "not_adapted"


def test_v3_revisions_are_isolated_atomic_and_idempotent(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "v3-revisions.db"))
    first = _v3_policy()
    applied = store.apply_policy_v3(first)
    assert applied["effective"]["routes"][0]["targets"][0]["eligible"] is False
    assert applied["effective"]["routes"][0]["targets"][0]["reason"] == "not_admitted"
    assert applied["effective"]["routes"][0]["reason"] == "not_admitted"

    repeated = store.apply_policy_v3({**first, "requestId": "retry-request"})
    assert repeated["idempotent"] is True
    assert repeated["requestId"] == "retry-request"
    assert repeated["effective"]["requestId"] == "retry-request"

    try:
        store.apply_policy_v3({**first, "enabled": False})
    except Exception as error:  # assertions inspect the stable contract error fields.
        assert getattr(error, "code", None) == "REVISION_CONFLICT"
        assert getattr(error, "contract_version", None) == 3
    else:
        raise AssertionError("same revision with different content must conflict")

    next_revision = store.apply_policy_v3({**first, "revision": 2})
    assert next_revision["effective"]["sourceDesiredRevision"] == 2
    try:
        store.apply_policy_v3({**first, "requestId": "stale", "revision": 1})
    except Exception as error:
        assert getattr(error, "code", None) == "STALE_REVISION"
    else:
        raise AssertionError("older revisions must be rejected")


def test_v3_unsupported_adjustment_after_old_contract_rejected(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    v2 = {
        "contractVersion": 2,
        "consumer": "thesis-ledger",
        "requestId": "v2-control-state",
        "revision": 9,
        "enabled": True,
        "routes": {
            "DAILY_BAR": {
                "ETF": [{"providerId": "akshare", "upstreamSource": "eastmoney"}]
            }
        },
    }
    v2_result = client.post(
        "/api/v3/thesis-ledger/control/policies/apply", headers=headers, json=v2
    )
    assert v2_result.status_code == 422
    assert v2_result.json()["detail"]["code"] == "CONTROL_CONTRACT_UNSUPPORTED"

    unsupported = _v3_policy(
        routes=[
            {
                "key": {
                    "kind": "bar",
                    "market": "CN",
                    "assetType": "STOCK",
                    "capability": "DAILY_BAR",
                    "timeframe": "1d",
                    "adjustment": "hfq",
                },
                "targets": [
                    {"providerId": "tencent", "upstreamSource": "tencent"}
                ],
            }
        ]
    )
    v3_result = client.post(
        "/api/v3/thesis-ledger/control/policies/apply",
        headers=headers,
        json=unsupported,
    )
    assert v3_result.status_code == 200, v3_result.text
    target = v3_result.json()["effective"]["routes"][0]["targets"][0]
    assert target["eligible"] is False
    assert target["reason"] == "unsupported_adjustment"

    effective_read = client.get(
        "/api/v3/thesis-ledger/control/policies/effective?contractVersion=3", headers=headers
    )
    assert effective_read.status_code == 200
    assert effective_read.json()["contractVersion"] == 3
    assert effective_read.json()["projection"]["effective"]["sourceDesiredRevision"] == unsupported["revision"]


def test_v3_policy_rejects_unknown_version_and_non_strict_shape(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    unsupported = client.post(
        "/api/v3/thesis-ledger/control/policies/apply",
        headers=headers,
        json={**_v3_policy(), "contractVersion": 4},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["detail"]["code"] == "CONTROL_CONTRACT_UNSUPPORTED"
    assert unsupported.json()["detail"]["contractVersion"] == 3

    malformed = client.post(
        "/api/v3/thesis-ledger/control/policies/apply",
        headers=headers,
        json={**_v3_policy(), "unexpected": True},
    )
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["contractVersion"] == 3
    assert malformed.json()["detail"]["code"] == "INVALID_POLICY_SCHEMA"
