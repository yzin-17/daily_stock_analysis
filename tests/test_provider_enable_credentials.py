"""数据源启用与有效凭证在实际 Control 配置事务中保持一致。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3
from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-encryption-key")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "synthetic-control-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "control.db"))
    monkeypatch.setattr("src.services.thesis_ledger_control._environment_credential_values", lambda _: {})
    monkeypatch.setattr("src.services.thesis_ledger_control._environment_credential_state", lambda _: ({}, False))
    return ThesisLedgerControlStore()


def save(store, **values):
    return store.save_provider_config("hithink", {"requestId": "credential-enable", **values})


def configured(store, provider_id="hithink"):
    return next(item for item in store.provider_registry() if item["providerId"] == provider_id)


@pytest.mark.parametrize("provider_id", ["hithink", "tushare", "tickflow", "longbridge", "finnhub", "alphavantage", "rqdata"])
def test_required_provider_cannot_enable_without_credentials_or_write_config(store, provider_id):
    assert configured(store, provider_id)["enabled"] is False
    with pytest.raises(ControlContractError) as caught:
        store.save_provider_config(provider_id, {"requestId": "missing", "enabled": True})
    assert caught.value.code == "PROVIDER_CREDENTIAL_MISSING"
    with store._connect() as connection:
        assert connection.execute("SELECT count(*) FROM thesis_ledger_provider_config").fetchone()[0] == 0


def test_http_reports_missing_credentials_and_accepts_atomic_credentials_with_enable(store):
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    headers = {"authorization": "Bearer synthetic-control-token"}
    envelope = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "enable-http"}
    url = "/api/v3/thesis-ledger/control/providers/hithink/config"
    with TestClient(app) as client:
        response = client.post(url, headers=headers, json={**envelope, "enabled": True})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "PROVIDER_CREDENTIAL_MISSING"
        response = client.post(url, headers=headers, json={
            **envelope, "enabled": True,
            "credentials": {"method": "api_key", "values": {"apiKey": "synthetic-page-key"}},
        })
        assert response.status_code == 200
        assert response.json()["enabled"] is True
        assert "synthetic-page-key" not in response.text
    assert configured(store)["enabled"] is True


def test_clear_last_credential_disables_without_removing_desired_routes(store):
    save(store, enabled=True, credentials={"method": "api_key", "values": {"apiKey": "synthetic-page-key"}})
    key = {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq"}
    target = {"providerId": "hithink", "upstreamSource": "fund-market-historical"}
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "desired", "revision": 1, "enabled": True, "routes": [{"key": key, "targets": [target]}]})
    result = save(store, clearCredentials=True)
    assert result["enabled"] is False
    assert result["credentialConfigured"] is False
    assert configured(store)["enabled"] is False
    assert store.policy_projection_v3()["desired"]["routes"] == [{"key": key, "targets": [target]}]


def test_explicit_enable_and_clear_without_environment_rolls_back(store):
    save(store, enabled=True, credentials={"method": "api_key", "values": {"apiKey": "synthetic-page-key"}})
    before = store.provider_credential_snapshot("hithink")
    with pytest.raises(ControlContractError):
        save(store, enabled=True, clearCredentials=True)
    assert store.provider_credential_snapshot("hithink") == before
    assert configured(store)["enabled"] is True


def test_environment_credentials_permit_enable_and_page_clear(store, monkeypatch):
    monkeypatch.setattr("src.services.thesis_ledger_control._environment_credential_state", lambda _: ({"apiKey": True}, True))
    assert save(store, enabled=True)["enabled"] is True
    save(store, credentials={"method": "api_key", "values": {"apiKey": "synthetic-page-key"}})
    result = save(store, clearCredentials=True)
    assert result["enabled"] is True
    assert result["credentialSource"] == "environment"


def test_legacy_missing_credentials_project_disabled_and_can_be_disabled(store):
    with store._connect() as connection:
        connection.execute("INSERT INTO thesis_ledger_provider_config (provider_id, enabled, settings_json, config_version, credential_version, updated_at) VALUES ('hithink', 1, '{}', 1, 0, '2026-10-07T00:00:00Z')")
    assert configured(store)["enabled"] is False
    assert save(store, enabled=False)["enabled"] is False


def test_public_sources_enable_without_credentials(store):
    result = store.save_provider_config("tencent", {"requestId": "public", "enabled": True})
    assert result["enabled"] is True
    assert configured(store, "tencent")["enabled"] is True
