"""用户目录缺失时的在线检索，包括来源开关、精确市场和真实名称。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient as Client

from api.thesis_ledger_reference_data import build_reference_data_router
from data_provider.futu_static_info import find_futu_instrument
from data_provider.hithink_instrument_search import search_instruments as native_search
from src.services import thesis_ledger_instrument_search as service


@pytest.fixture
def endpoint(monkeypatch):
    store = Mock()
    store.effective_policy_v3.return_value = {"enabled": True}
    store.provider_registry.return_value = [
        {"providerId": p, "enabled": True, "configVersion": 1} for p in ("hithink", "futu")
    ]
    store.provider_credential_snapshot.side_effect = lambda p: SimpleNamespace(values=(
        {"apiKey": "synthetic"} if p == "hithink" else {"host": "localhost", "port": "11111"}
    ))
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    app = FastAPI()
    app.include_router(build_reference_data_router(lambda: SimpleNamespace(store=store)), prefix="/api/v1/thesis-ledger")
    return store, Client(app)


URL = "/api/v1/thesis-ledger/instruments/search"
AUTH = {"Authorization": "Bearer test-token"}


def source(monkeypatch, rows):
    raw = {"code": 0, "data": {"timestamp": 1, "item": rows}}
    http = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: raw))
    monkeypatch.setattr(service, "search_instruments", lambda *args, **kwargs: native_search(*args, **kwargs, http_get=http))
    return http


def test_cn_name_query_and_full_code_use_bounded_search_and_return_real_name(endpoint, monkeypatch):
    _, client = endpoint
    http = source(monkeypatch, [{"thscode": "510300.SH", "asset_type": "fund-etf", "exchange": "SH",
                                 "name": "沪深300ETF", "currency": "CNY"}])
    assert client.get(URL, params={"query": "510300"}).status_code == 401
    http.assert_not_called()
    for query in ("沪深300", "510300", "510300.sh"):
        result = client.get(URL, params={"query": query, "instrumentType": "ETF"}, headers=AUTH)
        assert result.status_code == 200
        assert result.json() == {"contractVersion": 1, "providerId": "hithink", "items": [
            {"canonicalCode": "510300", "market": "SH", "instrumentType": "ETF", "displayName": "沪深300ETF"},
        ]}
        params = http.call_args.kwargs["params"]
        assert params["asset_type"] == "fund-etf" and params["q"] == query.upper() and params["limit"] == 20
        assert params.get("exchange") == ("SH" if query.endswith(".sh") else None)


def test_fund_identity_has_no_exchange_and_empty_is_success(endpoint, monkeypatch):
    _, client = endpoint
    http = source(monkeypatch, [{"thscode": "000001.OF", "asset_type": "fund-otc", "exchange": None,
                                 "name": "场外基金", "currency": "CNY"}])
    result = client.get(URL, params={"query": "000001.OF"}, headers=AUTH)
    assert result.status_code == 200
    assert result.json()["items"] == [{"canonicalCode": "000001", "market": "OF", "instrumentType": "MUTUAL_FUND",
                                      "displayName": "场外基金"}]
    assert http.call_args.kwargs["params"]["asset_type"] == "fund-otc"
    source(monkeypatch, [])
    assert client.get(URL, params={"query": "不存在"}, headers=AUTH).json()["items"] == []


def test_failures_return_diagnostics_and_never_an_empty_match(endpoint, monkeypatch):
    store, client = endpoint
    http = source(monkeypatch, [{"thscode": "510300.SZ", "exchange": "SH"}])
    result = client.get(URL, params={"query": "510300"}, headers=AUTH)
    assert result.status_code == 502 and result.json()["detail"]["code"] == "invalid_response"
    store.effective_policy_v3.return_value = {"enabled": False}
    result = client.get(URL, params={"query": "510300"}, headers=AUTH)
    assert result.status_code == 503 and result.json()["detail"]["code"] == "disabled"
    assert http.call_count == 1
    assert client.get(URL, params={"query": " "}, headers=AUTH).status_code == 422


@pytest.mark.parametrize(("query", "market", "kind"), [("SPY.US", "US", "ETF"), ("700.HK", "HK", "STOCK")])
def test_futu_exact_query_uses_returned_type_and_filters_delisted(endpoint, monkeypatch, query, market, kind):
    _, client = endpoint
    code = "SPY" if market == "US" else "00700"
    row = {"code": market + "." + code, "stock_type": kind, "name": "源证券名称", "delisting": False}
    context = Mock()
    sdk = SimpleNamespace(OpenQuoteContext=Mock(return_value=context), RET_OK=0, Market=SimpleNamespace(US="US", HK="HK"))
    def read(_self, symbol):
        context.get_stock_basicinfo.return_value = (0, pd.DataFrame([row]))
        return find_futu_instrument(sdk, "localhost", 11111, symbol)
    monkeypatch.setattr(service.FutuMarketAdapter, "find_instrument", read)
    assert client.get(URL, params={"query": query}, headers=AUTH).json() == {
        "contractVersion": 1, "providerId": "futu", "items": [
            {"canonicalCode": code, "market": market, "instrumentType": kind, "displayName": "源证券名称"}]}
    context.get_stock_basicinfo.assert_called_once_with(market, code_list=[row["code"]])
    context.close.assert_called_once()
    row["delisting"] = True
    assert client.get(URL, params={"query": query}, headers=AUTH).json()["items"] == []
    row["code"] = "HK.00001"
    assert client.get(URL, params={"query": query}, headers=AUTH).status_code == 502


def test_unsupported_explicit_scope_does_not_call_a_different_provider(endpoint, monkeypatch):
    _, client = endpoint
    read = source(monkeypatch, [])
    result = client.get(URL, params={"query": "腾讯", "market": "HK"}, headers=AUTH)
    assert result.status_code == 422 and result.json()["detail"]["code"] == "unsupported_capability"
    read.assert_not_called()
