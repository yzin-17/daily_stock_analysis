"""Tushare ETF raw 从 SQLite 准入到 HTTP V3 的离线纵向验收。"""

import json as json_module
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3
from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS, ThesisLedgerControlStore, _provider_credential_revision_from_snapshot,
)
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime, market_v3_current_route_revisions

KEY = {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR",
       "timeframe": "1d", "adjustment": "none"}
TARGET = {"providerId": "tushare", "upstreamSource": "tushare"}
REQUEST = {"contractVersion": 3, "requestId": "tushare-v3-fixture", "symbol": "159516.SZ",
           "routeKey": KEY, "start": "2026-06-22", "end": "2026-06-23"}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    config = SimpleNamespace(tushare_token="fixture-token")
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "fixture-master")
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "fixture-data-token")
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://fixture.example/api")
    store = ThesisLedgerControlStore(str(tmp_path / "tushare-v3.sqlite"))
    runtime = ThesisLedgerProviderRuntime(store)
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    store.apply_policy_v3({
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "fixture-policy", "revision": 1,
        "enabled": True, "routes": [{"key": KEY, "targets": [TARGET]}],
    })
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    client = TestClient(app)
    return store, runtime, client, config


def admit(store, **revision_overrides):
    revision = _provider_credential_revision_from_snapshot(store.provider_credential_snapshot("tushare"))
    revisions = market_v3_current_route_revisions(KEY, TARGET, PROVIDER_MANIFESTS["tushare"], credential_revision=revision)
    assert revisions is not None
    revisions.update(revision_overrides)
    store.record_route_admission_v3(
        key=KEY, target=TARGET, evidence_ref="fixture://tushare-raw", evidence_sha256="b" * 64,
        scope_symbols=[REQUEST["symbol"]], scope_date_from=REQUEST["start"], scope_date_to=REQUEST["end"],
        valid_from="2026-01-01T00:00:00+00:00", valid_until="2027-01-01T00:00:00+00:00",
        recorded_by="pytest-fixture", adapter_revision=revisions["adapterRevision"],
        source_revision=revisions["sourceRevision"], credential_revision=revisions["credentialRevision"],
    )


def response(days=("20260622", "20260623")):
    return Mock(status_code=200, text=json_module.dumps({"code": 0, "data": {
        "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
        "items": [["159516.SZ", day, 1, 2, 1, 2, 10, 1] for day in days],
    }}))


def read(client):
    return client.post("/api/v3/thesis-ledger/market/bars", json=REQUEST,
                       headers={"Authorization": "Bearer fixture-data-token"})


def entry(runtime):
    return next(row for row in runtime.market_route_catalog_v3()["entries"] if row["key"] == KEY and row["target"] == TARGET)


def test_default_denied_then_explicit_current_admission_returns_calendar_proven_raw(setup, monkeypatch):
    store, runtime, client, _ = setup
    post = Mock(return_value=response())
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    assert entry(runtime)["state"] == "not_admitted"
    assert read(client).status_code != 200
    post.assert_not_called()
    admit(store)
    assert entry(runtime)["state"] == "ready"
    result = read(client)
    assert result.status_code == 200, result.text
    body = result.json()
    assert len(body["bars"]) == 2
    assert body["bars"][0]["volume"] == 1000
    assert body["bars"][0]["amount"] == 1000
    assert body["routeKey"] == KEY
    assert body["coverageProof"]["calendar"]["expectedSessionDates"] == ["2026-06-22", "2026-06-23"]
    assert body["coverageProof"]["window"]["status"] == "complete"
    assert body["coverageProof"]["pagination"]["pagesFetched"] == 1
    assert "fixture-token" not in result.text and "fixture.example" not in result.text
    post.assert_called_once()


@pytest.mark.parametrize("field", ["adapterRevision", "sourceRevision", "credentialRevision"])
def test_stale_admission_never_calls_provider(setup, monkeypatch, field):
    store, runtime, client, _ = setup
    admit(store, **{field: "stale-fixture-revision"})
    post = Mock(return_value=response())
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    assert entry(runtime)["state"] == "not_admitted"
    assert read(client).status_code != 200
    post.assert_not_called()


@pytest.mark.parametrize("change", ["token", "endpoint", "revoke"])
def test_inflight_rotation_or_revocation_rejects_late_result(setup, monkeypatch, change):
    store, runtime, client, config = setup
    admit(store)

    def changed_response(*args, **kwargs):
        if change == "token":
            config.tushare_token = "rotated-token"
        elif change == "endpoint":
            monkeypatch.setenv("TUSHARE_HTTP_URL", "https://rotated.example/api")
        else:
            store.revoke_route_admission_v3(key=KEY, target=TARGET, reason="fixture revoked")
        return response()

    post = Mock(side_effect=changed_response)
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    result = read(client)
    assert result.status_code != 200
    assert "bars" not in result.json()
    assert entry(runtime)["state"] == "not_admitted"
    post.assert_called_once()


def test_completed_transport_with_missing_trading_day_is_not_complete_coverage(setup, monkeypatch):
    store, _, client, _ = setup
    admit(store)
    post = Mock(return_value=response(("20260622",)))
    monkeypatch.setattr("data_provider.tushare_fetcher.requests.post", post)
    result = read(client)
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "insufficient_coverage"
    post.assert_called_once()
