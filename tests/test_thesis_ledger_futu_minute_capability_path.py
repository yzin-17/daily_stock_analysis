"""富途分钟经标准绑定、控制策略、worker 与真实 HTTP 投影的本地闭环。"""

import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pandas as pd

from api.thesis_ledger_chart_v3 import router
from data_provider.thesis_ledger_sources.futu import SOURCE
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ProviderCallError
from tests.test_configured_market_routes import apply, no_admissions
from tests.test_configured_market_routes import store  # noqa: F401 - pytest 按参数名注入 fixture
from tests.test_futu_minute import sdk, row
from tests.test_thesis_ledger_futu_capability_path import configure, worker_transport

PATH = "/api/v3/thesis-ledger/market/chart-bars"


def request(market="HK", asset="STOCK", symbol="00700.HK"):
    return {"contractVersion": 3, "purpose": "interactive-chart", "requestId": "minute-contract",
            "symbol": symbol, "start": "2026-05-18", "end": "2026-05-18",
            "routeKey": {"kind": "bar", "market": market, "assetType": asset,
                         "capability": "MINUTE_BAR", "timeframe": "1m", "adjustment": "none"},
            "routeTarget": {"providerId": "futu", "upstreamSource": "futu", "routeIndex": 0}}


def client_for(monkeypatch, store):
    import src.services.thesis_ledger_provider_runtime as runtime
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-minute-token")
    instance = ThesisLedgerProviderRuntime(store)
    monkeypatch.setattr(runtime, "get_thesis_ledger_runtime", lambda: instance)
    app = FastAPI()
    app.include_router(router, prefix="/api/v3")
    return TestClient(app), instance


def post(client, payload):
    return client.post(PATH, json=payload, headers={"authorization": "Bearer synthetic-minute-token"})


def test_only_four_exact_unadjusted_minute_bindings_are_advertised():
    from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
    from src.services.thesis_ledger_price_route_access import configured_data_route
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
    entries = [(k, t) for k, t, gated in exact_route_inventory() if k["capability"] == "MINUTE_BAR"]
    assert len(entries) == 4
    assert len([b for b in SOURCE.bindings if b.key.capability == "DAILY_BAR"]) == 12
    assert all(k["kind"] == "bar" and k["timeframe"] == "1m" and k["adjustment"] == "none"
               and configured_data_route(k, t) for k, t in entries)
    for key, target in entries:
        revision = market_v3_current_route_revisions(key, target, PROVIDER_MANIFESTS["futu"], credential_version=1)
        assert revision["adapterRevision"] == "futu-exact-minute-v1"
        assert revision["sourceRevision"] == "futu-complete-minute-sdk-pagination-v1"


@pytest.mark.parametrize("market,asset,symbol,code", [
    ("HK", "STOCK", "00700.HK", "HK.00700"), ("HK", "ETF", "02800.HK", "HK.02800"),
    ("US", "STOCK", "AAPL.US", "US.AAPL"), ("US", "ETF", "SPY.US", "US.SPY"),
])
def test_configured_routes_factory_worker_and_http_keep_minutes(monkeypatch, store, market, asset, symbol, code):
    configure(store)
    value, ctx = sdk([(0, pd.DataFrame([row(code=code), row("2026-05-18 09:31:00", code=code)]), None)])
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{"code": code, "stock_type": asset}])
    calls = worker_transport(monkeypatch, value)
    payload = request(market, asset, symbol)
    assert apply(store, payload["routeKey"], {"providerId": "futu", "upstreamSource": "futu"})["effective"]["routes"][0]["targets"][0]["eligible"]
    client, _ = client_for(monkeypatch, store)
    response = post(client, payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["bars"]) == 2 and data["bars"][0]["timestamp"] != data["bars"][1]["timestamp"]
    assert data["bars"][0]["timestamp"].endswith("+08:00" if market == "HK" else "-04:00")
    assert all(p["volume"] == 0 and p["amount"] is None for p in data["bars"])
    assert data["coverage"]["latestCompleteTradingDate"] is None and data["session"] == "regular"
    assert data["routeKey"] == payload["routeKey"] and data["provenance"]["providerId"] == "futu"
    assert "coverageProof" not in data and len(calls) == 1
    sent = json.loads(calls[0][1]["input"])
    assert sent["operation"] == "minute" and sent["adjustment"] == "none"
    no_admissions(store)


def test_bad_token_and_bad_minute_scope_never_reach_sdk(monkeypatch, store):
    configure(store)
    value, ctx = sdk([])
    worker_transport(monkeypatch, value)
    payload = request()
    apply(store, payload["routeKey"], {"providerId": "futu", "upstreamSource": "futu"})
    client, _ = client_for(monkeypatch, store)
    assert client.post(PATH, json=payload).status_code == 401
    assert client.post(PATH, json=payload, headers={"authorization": "Bearer wrong"}).status_code == 401
    for change in [{"adjustment": "qfq"}, {"assetType": "INDEX"}]:
        assert post(client, {**payload, "routeKey": {**payload["routeKey"], **change}}).status_code == 422
    ctx.request_history_kline.assert_not_called()


def test_minute_cannot_be_used_as_backtest_complete_window(monkeypatch, store):
    configure(store)
    value, ctx = sdk([])
    worker_transport(monkeypatch, value)
    key = request()["routeKey"]
    apply(store, key, {"providerId": "futu", "upstreamSource": "futu"})
    runtime = ThesisLedgerProviderRuntime(store)
    with pytest.raises(ProviderCallError) as error:
        runtime.execute_market_bars_v3(ThesisLedgerDataRequest("MINUTE_BAR", "00700.HK",
            instrument_type="STOCK", timeframe="1m", adjustment="none",
            start="2026-05-18", end="2026-05-18"), key)
    assert error.value.code == "invalid_request"
    ctx.request_history_kline.assert_not_called()


def test_later_page_failure_is_not_http_success(monkeypatch, store):
    configure(store)
    value, ctx = sdk([(0, pd.DataFrame([row()]), b"next"), (-1, "permission denied", None)])
    worker_transport(monkeypatch, value)
    payload = request()
    apply(store, payload["routeKey"], {"providerId": "futu", "upstreamSource": "futu"})
    client, _ = client_for(monkeypatch, store)
    response = post(client, payload)
    assert response.status_code in {502, 503}, response.text
    assert "bars" not in response.json()
    ctx.close.assert_called_once()


@pytest.mark.parametrize("change", ["credential", "policy"])
def test_changes_during_minute_read_reject_late_results(monkeypatch, store, change):
    configure(store)
    value, ctx = sdk([])
    worker_transport(monkeypatch, value)
    payload = request()
    target = {"providerId": "futu", "upstreamSource": "futu"}
    apply(store, payload["routeKey"], target)
    def changed(*args, **kwargs):
        if change == "credential":
            store.save_provider_config("futu", {"requestId": "changed", "enabled": True,
                "credentials": {"method": "opend", "values": {"host": "localhost", "port": "11112"}}})
        else:
            store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "changed",
                "revision": 2, "enabled": True, "routes": [{"key": payload["routeKey"], "targets": [target]}]})
        return 0, pd.DataFrame([row()]), None
    ctx.request_history_kline.side_effect = changed
    client, _ = client_for(monkeypatch, store)
    response = post(client, payload)
    assert response.status_code in {502, 503}, response.text
    assert "bars" not in response.json()
