"""每手股数的真实 Control 存储、主备执行及 HTTP 鉴权接缝。"""

from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_reference_data import build_reference_data_router
from data_provider.security_lot_size import LotSizeError, lot_reference
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ProviderCallError

KEY = {"kind": "data", "market": "HK", "assetType": "STOCK", "capability": "SECURITY_LOT_SIZE"}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-lot-key")
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-lot-token")
    monkeypatch.setattr("src.services.thesis_ledger_route_catalog.provider_dependency_ready", lambda *args: True)
    monkeypatch.setattr("src.services.thesis_ledger_provider_dependencies.provider_dependency_ready", lambda *args: True)
    store = ThesisLedgerControlStore(str(tmp_path / "lots.db"))
    for provider, method, values in [
        ("futu", "opend", {"host": "localhost", "port": "11111"}),
        ("longbridge", "legacy", {"appKey": "test-key", "appSecret": "test-secret", "accessToken": "test-token"}),
    ]:
        store.save_provider_config(provider, {"requestId": provider, "enabled": True,
                                             "credentials": {"method": method, "values": values}})
    return store


def runtime_for(store, targets=("futu", "longbridge"), fail=False):
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "lots",
                           "revision": 1, "enabled": True, "routes": [{"key": KEY, "targets": [
                               {"providerId": p, "upstreamSource": p} for p in targets]}]})
    futu = Mock(return_value=lot_reference("00700.HK", "100", "futu", "2026-10-08T10:00:00Z"))
    if fail:
        futu.side_effect = LotSizeError("timeout", "测试超时")
    lb = Mock(return_value=SimpleNamespace(symbol="700.HK", lot_size=100, board="HKEquity"))
    runtime = ThesisLedgerProviderRuntime(store, adapters={
        "futu": SimpleNamespace(get_security_lot_size=futu),
        "longbridge": SimpleNamespace(_get_static_info=lb),
    })
    return runtime, futu, lb


def test_catalog_and_primary_success(setup):
    runtime, futu, lb = runtime_for(setup)
    entries = [e for e in runtime.market_route_catalog_v3()["entries"] if e["key"] == KEY]
    assert {e["target"]["providerId"] for e in entries} == {"futu", "longbridge"}
    assert all(e["state"] == "ready" for e in entries)
    result = runtime.execute_request(ThesisLedgerDataRequest("SECURITY_LOT_SIZE", "00700.HK", instrument_type="STOCK"))
    assert result.provider == "futu" and result.route_index == 0
    assert result.value["sharesPerLot"] == "100"
    futu.assert_called_once()
    lb.assert_not_called()


def test_fallback_is_only_the_configured_backup(setup):
    runtime, futu, lb = runtime_for(setup, fail=True)
    result = runtime.execute_request(ThesisLedgerDataRequest("SECURITY_LOT_SIZE", "00700.HK", instrument_type="STOCK"))
    assert result.provider == "longbridge" and result.route_index == 1
    lb.assert_called_once()


def test_no_hidden_fallback(setup):
    runtime, futu, lb = runtime_for(setup, targets=("futu",), fail=True)
    with pytest.raises(ProviderCallError):
        runtime.execute_request(ThesisLedgerDataRequest("SECURITY_LOT_SIZE", "00700.HK", instrument_type="STOCK"))
    lb.assert_not_called()


def test_http_auth_and_response(setup):
    runtime, _, _ = runtime_for(setup)
    app = FastAPI()
    app.include_router(build_reference_data_router(lambda: runtime))
    body = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "lot-http", "symbol": "00700.HK"}
    with TestClient(app) as client:
        assert client.post("/market/security-lot-size", json=body).status_code == 401
        response = client.post("/market/security-lot-size", json=body, headers={"authorization": "Bearer synthetic-lot-token"})
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["symbol"] == "00700.HK" and value["sharesPerLot"] == "100"
    assert value["routeTarget"] == {"providerId": "futu", "upstreamSource": "futu", "routeIndex": 0}
    assert value["policyRevision"] == 1


def test_current_only_and_not_price_route(setup):
    runtime, futu, lb = runtime_for(setup)
    with pytest.raises(ProviderCallError):
        runtime.execute_request(ThesisLedgerDataRequest("SECURITY_LOT_SIZE", "00700.HK", instrument_type="STOCK", start="2025-01-01"))
    futu.assert_not_called()
    assert setup.effective_policy_v3()["routes"][0]["key"] == KEY


def test_futu_adapter_uses_page_endpoint(setup):
    runtime = ThesisLedgerProviderRuntime(setup)
    adapter = runtime._adapter("futu")
    assert adapter.host == "localhost" and adapter.port == 11111
    provider = next(p for p in setup.provider_registry() if p["providerId"] == "futu")
    assert provider["credentialMethod"] == "opend"
    assert provider["capabilities"] == {"SECURITY_LOT_SIZE": ["STOCK"], "REALTIME_QUOTE": ["ETF", "STOCK"],
                                        "DAILY_BAR": ["ETF", "STOCK"], "SECURITY_STATIC_INFO": ["ETF", "STOCK"]}
