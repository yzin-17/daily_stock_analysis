"""空准入表下的真实配置、策略和普通数据执行接缝。"""

from types import SimpleNamespace

import pandas as pd
import pytest

from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import (
    ThesisLedgerDataRequest, ThesisLedgerGatewayError, ThesisLedgerProviderRuntime,
)


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-route-key")
    monkeypatch.delenv("HITHINK_API_KEY", raising=False)
    return ThesisLedgerControlStore(str(tmp_path / "configured.db"))


def apply(store, key, target):
    return store.apply_policy_v3({
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "configured-route",
        "revision": 1, "enabled": True, "routes": [{"key": key, "targets": [target]}],
    })


def no_admissions(store):
    with store._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM thesis_ledger_route_admission_v3").fetchone()[0] == 0


@pytest.mark.parametrize("asset_type,source", [
    ("STOCK", "eastmoney"), ("STOCK", "sina"), ("STOCK", "tencent"), ("ETF", "eastmoney"),
])
@pytest.mark.parametrize("adjustment", ["none", "qfq", "hfq"])
def test_existing_akshare_daily_executes_exact_source_without_manual_admission(store, asset_type, source, adjustment):
    key = {"kind": "bar", "market": "CN", "assetType": asset_type,
           "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": adjustment}
    target = {"providerId": "akshare", "upstreamSource": source}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    calls = []

    def read(symbol, actual_source, **options):
        calls.append((symbol, actual_source, options))
        frame = pd.DataFrame([{"date": "2026-05-18", "open": 1., "high": 1.2,
                               "low": .9, "close": 1.1, "volume": 100., "amount": 110., "pct_chg": 1.}])
        frame.attrs["upstream_source"] = actual_source
        return frame

    runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": SimpleNamespace(get_daily_data_for_source=read)})
    symbol = "510300.SH" if asset_type == "ETF" else "600519.SH"
    request = ThesisLedgerDataRequest("DAILY_BAR", symbol, instrument_type=asset_type,
                                      timeframe="1d", adjustment=adjustment, start="2026-05-18", end="2026-05-18")
    result = runtime.execute_market_bars_v3(request, key)
    assert result.provider == "akshare" and result.upstream_source == source
    assert calls == [(symbol.removesuffix(".SH"), source, {
        "start_date": "2026-05-18", "end_date": "2026-05-18", "days": 1,
        "adjustment": adjustment, "timeout_seconds": 4.5,
    })]
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
    binding = CAPABILITY_SOURCES.resolve(key, target)
    assert binding.gated is False and binding.maximum_rows is None
    revisions = market_v3_current_route_revisions(key, target, PROVIDER_MANIFESTS["akshare"])
    assert revisions["adapterRevision"] == f"dsa-v3-{asset_type.lower()}-akshare-{source}-{adjustment}-adapter-v1"
    protocol = f"akshare-stock-{source}-range-response-v1" if asset_type == "STOCK" else "akshare-etf-range-response-v1"
    assert binding.protocol == protocol and revisions["sourceRevision"] == protocol
    no_admissions(store)


def test_nav_history_executes_and_bounds_dates_without_manual_scope_records(store):
    key = {"kind": "data", "market": "CN", "assetType": "MUTUAL_FUND", "capability": "FUND_NAV_HISTORY"}
    target = {"providerId": "efinance", "upstreamSource": "eastmoney"}
    apply(store, key, target)
    frame = pd.DataFrame({"净值日期": ["2026-05-17", "2026-05-18", "2026-05-19"], "单位净值": [1., 1.1, 1.2]})
    runtime = ThesisLedgerProviderRuntime(store, adapters={"efinance": SimpleNamespace(get_fund_nav_history=lambda symbol: frame)})
    request = ThesisLedgerDataRequest("FUND_NAV_HISTORY", "000001.OF", instrument_type="MUTUAL_FUND",
                                      start="2026-05-18", end="2026-05-19")
    result = runtime.execute_request(request)
    assert [point.reported_date for point in result.value.points] == ["2026-05-18", "2026-05-19"]
    assert [point.unit_nav for point in result.value.points] == [1.1, 1.2]
    no_admissions(store)


def test_missing_credential_selection_is_saved_with_an_execution_reason(store):
    key = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "REALTIME_QUOTE"}
    target = {"providerId": "hithink", "upstreamSource": "a-share-prices-snapshot"}
    response = apply(store, key, target)
    assert response["status"] == "applied"
    assert response["effective"]["routes"][0]["targets"][0]["reason"] == "credential_missing"
    runtime = ThesisLedgerProviderRuntime(store)
    with pytest.raises(ThesisLedgerGatewayError):
        runtime.execute_request(ThesisLedgerDataRequest("REALTIME_QUOTE", "600519.SH", instrument_type="STOCK"))
    no_admissions(store)


@pytest.mark.parametrize("asset_type,source,symbol", [
    ("STOCK", "a-share-prices-snapshot", "600519.SH"),
    ("ETF", "fund-market-snapshot", "510300.SH"),
])
def test_hithink_quote_uses_page_credentials_and_exact_source_without_admission(store, asset_type, source, symbol):
    store.save_provider_config("hithink", {"requestId": "page-key", "credentials": {
        "method": "api_key", "values": {"apiKey": "synthetic-page-api-key"},
    }})
    key = {"kind": "data", "market": "CN", "assetType": asset_type, "capability": "REALTIME_QUOTE"}
    target = {"providerId": "hithink", "upstreamSource": source}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]

    def quote(actual_symbol, actual_asset):
        assert (actual_symbol, actual_asset) == (symbol, asset_type)
        return {"price": 10., "open_price": 10., "high": 10., "low": 10., "pre_close": 10.,
                "volume": 1., "amount": 10., "provider_timestamp": None,
                "fetched_at": "2026-10-07T00:00:00Z", "source": "hithink/" + source,
                "units": {"price_currency": "CNY", "volume": "share" if asset_type == "STOCK" else "unknown",
                          "turnover_currency": "CNY" if asset_type == "STOCK" else "unknown"}}

    runtime = ThesisLedgerProviderRuntime(store, adapters={"hithink": SimpleNamespace(fetch_quote=quote)})
    result = runtime.execute_request(ThesisLedgerDataRequest("REALTIME_QUOTE", symbol, instrument_type=asset_type))
    assert result.provider == "hithink" and result.upstream_source == source
    no_admissions(store)
