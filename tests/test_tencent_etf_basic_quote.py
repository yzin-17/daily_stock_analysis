"""腾讯 ETF 基础报价的解析、当前路由与实际鉴权 HTTP。"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3
from data_provider.base import DataFetchError
from data_provider.tencent_etf_quote import fetch_tencent_etf_quote
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS, ThesisLedgerControlStore
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import QuoteBinding
from src.services.thesis_ledger_provider_runtime import (
    ThesisLedgerDataGateway, ThesisLedgerDataRequest, ThesisLedgerGatewayError,
    ThesisLedgerProviderRuntime,
)


KEY = {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "REALTIME_QUOTE"}
TARGET = {"providerId": "tencent", "upstreamSource": "tencent"}
NOW = datetime(2026, 10, 6, 8, tzinfo=timezone.utc)


def content(wire="sh510300", **changes):
    fields = [""] * 88
    for index, value in {1: "ETF", 2: wire[2:], 3: "10", 4: "9.5", 5: "10",
                         30: "20260930161443", 33: "11", 34: "9"}.items():
        fields[index] = value
    for index, value in changes.items():
        fields[int(index)] = value
    return f'v_{wire}="' + "~".join(fields) + '";'


def read(symbol, payload=None):
    calls = []
    def get(url, **options):
        calls.append((url, options))
        return SimpleNamespace(status_code=200, text=payload or content())
    value = fetch_tencent_etf_quote(symbol, http_get=get, clock=lambda: NOW)
    assert len(calls) == 1
    assert calls[0][1] == {"timeout": 4.5, "allow_redirects": False}
    return value


@pytest.mark.parametrize("symbol,wire", [("510300.SH", "sh510300"), ("159516.SZ", "sz159516")])
def test_prices_original_clock_and_unknown_units(symbol, wire):
    value = read(symbol, content(wire))
    assert value["price"] == 10
    assert value["volume"] is None and value["amount"] is None
    assert value["units"] == {"price_currency": "CNY", "volume": "unknown", "turnover_currency": "unknown"}
    assert value["provider_timestamp"] == "2026-09-30T16:14:43+08:00"
    assert value["fetched_at"] == NOW.isoformat()


@pytest.mark.parametrize("symbol", ["510300.SZ", "159516.SH", "600519.SH", "510300", "159516.HK"])
def test_wrong_market_or_asset_is_rejected_before_request(symbol):
    with pytest.raises(DataFetchError):
        fetch_tencent_etf_quote(symbol, http_get=lambda *_a, **_k: pytest.fail("不应请求来源"))


@pytest.mark.parametrize("payload", [content("sz510300"), content(**{"2": "159516"}),
                                    content(**{"3": "nan"}), content(**{"33": "8"}),
                                    content() + content(), content() + "alert(1)"])
def test_invalid_identity_price_and_envelope_rejected(payload):
    with pytest.raises(DataFetchError):
        read("510300.SH", payload)


def test_unknown_time_does_not_become_fetch_time():
    assert read("510300.SH", content(**{"30": "--"}))["provider_timestamp"] is None


def policy(revision=1, enabled=True):
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": f"tencent-quote-{revision}",
            "revision": revision, "enabled": enabled, "routes": [{"key": KEY, "targets": [TARGET]}]}


def setup(tmp_path, on_fetch=None):
    store = ThesisLedgerControlStore(str(tmp_path / "control.sqlite"))
    store.apply_policy_v3(policy())
    class Adapter:
        def get_realtime_quote_single_symbol(self, symbol):
            if on_fetch:
                on_fetch(store)
            return read(symbol)
    runtime = ThesisLedgerProviderRuntime(store, adapters={"tencent": Adapter()})
    return store, runtime


def test_catalog_policy_and_authenticated_http_without_manual_admission(tmp_path, monkeypatch):
    store, runtime = setup(tmp_path)
    entry = next(e for e in runtime.market_route_catalog_v3()["entries"] if e["key"] == KEY and e["target"] == TARGET)
    assert entry["state"] == "ready"
    assert store.get_route_admission_v3(key=KEY, target=TARGET) is None
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-token")
    monkeypatch.delenv("THESIS_LEDGER_FIXTURE_MODE", raising=False)
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway",
                        lambda: ThesisLedgerDataGateway(runtime))
    app = FastAPI()
    app.include_router(router_v3)
    with TestClient(app) as client:
        path = "/thesis-ledger/market/quote"
        assert client.get(path, params={"symbol": "510300.SH"}).status_code == 401
        response = client.get(path, params={"symbol": "510300.SH"}, headers={"Authorization": "Bearer synthetic-token"})
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "tencent"
    assert response.json()["marketTime"] == "2026-09-30T08:14:43+00:00"
    assert response.json()["freshness"] == "stale"
    assert response.json()["volume"] is None and response.json()["amount"] is None


def test_late_policy_change_rejects_result(tmp_path):
    _, runtime = setup(tmp_path, lambda store: store.apply_policy_v3(policy(2, False)))
    with pytest.raises(ThesisLedgerGatewayError) as rejected:
        runtime.execute_request(ThesisLedgerDataRequest("REALTIME_QUOTE", "510300.SH", instrument_type="ETF"))
    assert rejected.value.code == "not_admitted"


def test_late_source_catalog_removal_rejects_result(tmp_path, monkeypatch):
    manifest = PROVIDER_MANIFESTS["tencent"]
    def remove(_store):
        monkeypatch.setitem(PROVIDER_MANIFESTS, "tencent", {**manifest, "upstreamSources": []})
    _, runtime = setup(tmp_path, remove)
    with pytest.raises(ThesisLedgerGatewayError) as rejected:
        runtime.execute_request(ThesisLedgerDataRequest("REALTIME_QUOTE", "510300.SH", instrument_type="ETF"))
    assert rejected.value.code == "not_admitted"


def test_scope_does_not_relax_other_sources_or_capabilities():
    binding = CAPABILITY_SOURCES.resolve(KEY, TARGET)
    assert isinstance(binding, QuoteBinding)
    for key, target in [(KEY, {**TARGET, "upstreamSource": "other"}),
                        ({**KEY, "capability": "CASH_DISTRIBUTION"}, TARGET)]:
        assert CAPABILITY_SOURCES.resolve(key, target) is None
    for key, target in [({**KEY, "assetType": "STOCK"}, TARGET),
                        (KEY, {"providerId": "hithink", "upstreamSource": "fund-market-snapshot"})]:
        assert CAPABILITY_SOURCES.resolve(key, target) is not binding
