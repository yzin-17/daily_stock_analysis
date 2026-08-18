"""Control Contract V1 的原子性、权限和目录 fixture 回归。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "stock_analysis.db"))
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "control-token")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "0123456789abcdef-secret-key")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def _envelope(**values):
    return {
        "contractVersion": 1,
        "consumer": "thesis-ledger",
        "requestId": "control-test-request",
        **values,
    }


def test_control_token_is_independent_and_handshake_is_versioned(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/v1/thesis-ledger/control/handshake",
        headers={"authorization": "Bearer data-token"},
        json=_envelope(supportedVersions=[1]),
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"

    response = client.post(
        "/api/v1/thesis-ledger/control/handshake",
        headers={"authorization": "Bearer control-token"},
        json=_envelope(supportedVersions=[1]),
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["consumer"] == "thesis-ledger"


def test_data_routes_reject_missing_or_control_token(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    for headers in ({}, {"authorization": "Bearer control-token"}):
        response = client.get(
            "/api/v1/thesis-ledger/market/quote?symbol=600519.SH",
            headers=headers,
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "unauthorized"


def test_fund_nav_history_is_ordered_and_uses_of_identity(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get(
        "/api/v1/thesis-ledger/market/fund-nav/history?symbol=000001&limit=5",
        headers={"authorization": "Bearer data-token"},
    )
    assert response.status_code == 200
    points = response.json()
    assert len(points) == 5
    assert {point["symbol"] for point in points} == {"000001.OF"}
    assert [point["navDate"] for point in points] == sorted(
        point["navDate"] for point in points
    )


def test_policy_apply_is_latest_wins_idempotent_and_atomic(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    config = client.post(
        "/api/v1/thesis-ledger/control/providers/akshare/config",
        headers=headers,
        json=_envelope(credential="fixture-secret", enabled=True, settings={}),
    )
    assert config.status_code == 200
    assert config.json()["credentialConfigured"] is True
    assert "fixture-secret" not in config.text

    policy = _envelope(
        revision=1,
        enabled=True,
        routes={"REALTIME_QUOTE": {"STOCK": ["akshare", "efinance"]}},
    )
    applied = client.post(
        "/api/v1/thesis-ledger/control/policies/apply", headers=headers, json=policy
    )
    assert applied.status_code == 200
    assert applied.json()["effective"]["sourceDesiredRevision"] == 1
    assert applied.json()["effective"]["routeStatus"]["REALTIME_QUOTE"]["STOCK"][
        "eligibleProviderIds"
    ] == ["akshare", "efinance"]

    repeated = client.post(
        "/api/v1/thesis-ledger/control/policies/apply", headers=headers, json=policy
    )
    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True

    stale = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json={**policy, "revision": 0},
    )
    assert stale.status_code == 422
    assert stale.json()["detail"]["code"] == "INVALID_REVISION"

    invalid = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json={
            **policy,
            "revision": 2,
            "routes": {"REALTIME_QUOTE": {"STOCK": ["tushare"]}},
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "UNKNOWN_PROVIDER"
    effective = client.get(
        "/api/v1/thesis-ledger/control/policies/effective", headers=headers
    )
    assert effective.json()["projection"]["desired"]["revision"] == 1


def test_empty_routes_are_valid_and_disable_all_effective_routes(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    response = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json=_envelope(revision=1, enabled=True, routes={}),
    )
    assert response.status_code == 200
    assert response.json()["effective"]["routes"] == {}


def test_catalog_snapshot_delta_and_expired_cursor(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer data-token"}
    snapshot = client.get("/api/v1/thesis-ledger/catalog/snapshot", headers=headers)
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["complete"] is True
    assert payload["generation"] == 1
    assert payload["checksum"]

    delta = client.get(
        f"/api/v1/thesis-ledger/catalog/delta?cursor={payload['cursor']}", headers=headers
    )
    assert delta.status_code == 200
    assert delta.json()["items"] == []

    expired = client.get(
        "/api/v1/thesis-ledger/catalog/delta?cursor=generation%3A999", headers=headers
    )
    assert expired.status_code == 409
    assert expired.json()["detail"]["code"] == "CATALOG_CURSOR_EXPIRED"


def test_capability_smoke_does_not_change_policy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    response = client.post(
        "/api/v1/thesis-ledger/control/providers/akshare/test",
        headers=headers,
        json=_envelope(credential="ephemeral-only", capabilities=["REALTIME_QUOTE"]),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["capabilityResults"]["REALTIME_QUOTE"]["attempted"] is True
    assert response.json()["capabilityResults"]["FUND_NAV_HISTORY"]["status"] == "healthy"
    assert response.json()["capabilityResults"]["FUND_NAV_HISTORY"]["attempted"] is True
    registry = client.get("/api/v1/thesis-ledger/control/providers", headers=headers)
    assert registry.json()["providers"][0]["credentialConfigured"] is False


def test_provider_removal_clears_runtime_config_but_keeps_tombstone(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    client.post(
        "/api/v1/thesis-ledger/control/providers/akshare/config",
        headers=headers,
        json=_envelope(credential="fixture-secret", enabled=True),
    )
    response = client.post(
        "/api/v1/thesis-ledger/control/providers/akshare/remove",
        headers=headers,
        json=_envelope(reason="test-removal"),
    )
    assert response.status_code == 200
    assert response.json()["tombstone"]["providerId"] == "akshare"
    registry = client.get("/api/v1/thesis-ledger/control/providers", headers=headers).json()
    removed = next(item for item in registry["providers"] if item["providerId"] == "akshare")
    assert removed["credentialConfigured"] is False
    assert removed["tombstone"]["reason"] == "test-removal"
