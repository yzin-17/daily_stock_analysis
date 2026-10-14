"""当前证券资料的精确身份、未知字段、整数精度和运行时合同。"""

from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from data_provider.futu_market import FutuMarketError
from data_provider.futu_static_info import read_futu_static_info
from api.thesis_ledger_static_info import build_static_info_router
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ProviderCallError
from tests.test_security_lot_size_routes import setup
from src.services.thesis_ledger_exact_probe_read import read_exact_probe


def static_sdk(**changes):
    row = {"code": "HK.00700", "stock_type": "STOCK", "name": "腾讯控股", "stock_id": 9007199254740993,
           "lot_size": 100, "listing_date": "2004-06-16", "delisting": False, "exchange_type": "HK_MAIN", **changes}
    context = Mock()
    context.get_stock_basicinfo.return_value = 0, pd.DataFrame([row])
    sdk = SimpleNamespace(OpenQuoteContext=Mock(return_value=context), RET_OK=0,
                          Market=SimpleNamespace(HK="HK", US="US"),
                          SecurityType=SimpleNamespace(STOCK="STOCK", ETF="ETF"))
    return sdk, context


def test_exact_static_identity_and_integer_precision():
    sdk, context = static_sdk()
    value = read_futu_static_info(sdk, "localhost", 11111, "00700.HK", "STOCK")
    assert value["securityId"] == "9007199254740993"
    assert value["sharesPerLot"] == "100" and value["delisted"] is False
    assert value["historicalChanges"] == "unverified"
    context.get_stock_basicinfo.assert_called_once_with("HK", stock_type="STOCK", code_list=["HK.00700"])
    context.close.assert_called_once()


def test_missing_optional_fields_remain_unknown():
    sdk, _ = static_sdk(name="", stock_id=None, lot_size=float("nan"), listing_date="", delisting=None, exchange_type=None)
    value = read_futu_static_info(sdk, "localhost", 11111, "00700.HK", "STOCK")
    assert all(value[key] is None for key in ("name", "securityId", "sharesPerLot", "listingDate", "delisted", "exchange"))


@pytest.mark.parametrize("changes", [
    {"code": "US.AAPL"}, {"stock_type": "ETF"}, {"stock_id": 1.5}, {"stock_id": True},
    {"lot_size": 0}, {"listing_date": "2004-02-30"}, {"listing_date": "20040616"}, {"delisting": "false"},
])
def test_invalid_response_closes_context(changes):
    sdk, context = static_sdk(**changes)
    with pytest.raises(FutuMarketError) as error:
        read_futu_static_info(sdk, "localhost", 11111, "00700.HK", "STOCK")
    assert error.value.code == "invalid_response"
    context.close.assert_called_once()


@pytest.mark.parametrize("symbol,asset", [("AAPL", "STOCK"), ("700.HK", "STOCK"),
    ("600519.SH", "STOCK"), ("00700.HK", "OPTION"), ("00700.HK", [])])
def test_invalid_input_never_connects(symbol, asset):
    sdk, _ = static_sdk()
    with pytest.raises(FutuMarketError):
        read_futu_static_info(sdk, "localhost", 11111, symbol, asset)
    sdk.OpenQuoteContext.assert_not_called()


def runtime_for(store, *, fail=False):
    key = {"kind": "data", "market": "HK", "assetType": "STOCK", "capability": "SECURITY_STATIC_INFO"}
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "static",
                          "revision": 1, "enabled": True, "routes": [{"key": key, "targets": [
                              {"providerId": "futu", "upstreamSource": "futu"}]}]})
    sdk, _ = static_sdk()
    value = read_futu_static_info(sdk, "localhost", 11111, "00700.HK", "STOCK")
    read = Mock(return_value=value)
    if fail:
        read.side_effect = FutuMarketError("timeout", "测试超时")
    runtime = ThesisLedgerProviderRuntime(store, adapters={"futu": SimpleNamespace(get_security_static_info=read)})
    return runtime, read


def test_exact_catalog_and_runtime_read(setup):
    runtime, read = runtime_for(setup)
    entries = [e for e in runtime.market_route_catalog_v3()["entries"] if e["key"]["capability"] == "SECURITY_STATIC_INFO"]
    assert len(entries) == 4 and all(e["target"]["providerId"] == "futu" for e in entries)
    value = runtime.execute_request(ThesisLedgerDataRequest("SECURITY_STATIC_INFO", "00700.HK", instrument_type="STOCK"))
    assert value.provider == "futu" and value.effective_revision == 1
    read.assert_called_once_with("00700.HK", "STOCK")


def test_missing_route_does_not_read_or_fallback(setup):
    runtime, read = runtime_for(setup)
    with pytest.raises(ProviderCallError):
        runtime.execute_request(ThesisLedgerDataRequest("SECURITY_STATIC_INFO", "SPY.US", instrument_type="ETF"))
    read.assert_not_called()


def test_timeout_has_no_hidden_fallback(setup):
    runtime, read = runtime_for(setup, fail=True)
    with pytest.raises(ProviderCallError):
        runtime.execute_request(ThesisLedgerDataRequest("SECURITY_STATIC_INFO", "00700.HK", instrument_type="STOCK"))
    read.assert_called_once()


@pytest.mark.parametrize("params", [{"start": "2024-01-01"}, {"adjustment": "none"}, {"parameters": {"asOf": "2024-01-01"}}])
def test_current_only_rejects_historical_queries(setup, params):
    runtime, read = runtime_for(setup)
    with pytest.raises(ProviderCallError):
        runtime.execute_request(ThesisLedgerDataRequest("SECURITY_STATIC_INFO", "00700.HK", instrument_type="STOCK", **params))
    read.assert_not_called()


def test_http_authorization_validation_and_response(setup):
    runtime, read = runtime_for(setup)
    app = FastAPI()
    app.include_router(build_static_info_router(lambda: runtime))
    body = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "static-http",
            "symbol": "00700.HK", "assetType": "STOCK"}
    headers = {"authorization": "Bearer synthetic-lot-token"}
    with TestClient(app) as client:
        assert client.post("/market/security-static-info", json=body).status_code == 401
        assert client.post("/market/security-static-info", json={**body, "assetType": []}, headers=headers).status_code == 422
        assert client.post("/market/security-static-info", json={**body, "asOf": "2020-01-01"}, headers=headers).status_code == 422
        read.assert_not_called()
        response = client.post("/market/security-static-info", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["requestId"] == "static-http"
    assert response.json()["routeTarget"] == {"providerId": "futu", "upstreamSource": "futu", "routeIndex": 0}


@pytest.mark.parametrize("market,asset,symbol", [("HK", "ETF", "02800.HK"), ("US", "STOCK", "AAPL.US")])
def test_provider_check_uses_current_single_security_reader(market, asset, symbol):
    read = Mock(return_value={"symbol": symbol, "market": market, "assetType": asset,
        "name": None, "securityId": None, "sharesPerLot": None, "listingDate": None,
        "delisted": None, "exchange": None, "provider": "futu", "observedAt": "2026-07-01T00:00:00+00:00",
        "historicalChanges": "unverified"})
    runtime = SimpleNamespace(_adapter=Mock(return_value=SimpleNamespace(get_security_static_info=read)))
    result = read_exact_probe(runtime, {"kind": "data", "market": market, "assetType": asset, "capability": "SECURITY_STATIC_INFO"},
                              {"providerId": "futu", "upstreamSource": "futu"}, None)
    assert result == {"symbol": symbol}
    read.assert_called_once_with(symbol, asset)
