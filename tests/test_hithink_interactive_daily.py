import data_provider.hithink_interactive_daily as source_adapter
"""HiThink 普通图表不借用独立上市/日历事实，严格读取仍保留边界。"""

from types import SimpleNamespace
import pytest
from tests.test_configured_market_routes import store, apply, no_admissions
from tests.test_thesis_ledger_hithink_stock import _item, _date_ms
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ProviderCallError


@pytest.mark.parametrize("asset,symbol,source", [("STOCK", "600519.SH", "hithink-financial-api"), ("ETF", "510300.SH", "fund-market-historical")])
def test_observed_chart_can_read_unlisted_local_fact_and_outside_local_calendar(monkeypatch, store, asset, symbol, source):
    import src.services.thesis_ledger_hithink_interactive_daily as observed
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: {"code": 0, "data": {
            "thscode": symbol, "interval": "1d", "adjust": None if asset == "ETF" else kwargs["params"]["adjust"],
            "timestamp": _date_ms("2020-01-02"), "item": [_item("2020-01-02")],
        }})
    monkeypatch.setattr(source_adapter.requests, "get", get)
    store.save_provider_config("hithink", {"requestId": "synthetic", "credentials": {"method": "api_key", "values": {"apiKey": "synthetic"}}})
    key = {"kind": "bar", "market": "CN", "assetType": asset, "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq"}
    target = {"providerId": "hithink", "upstreamSource": source}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    request = ThesisLedgerDataRequest("DAILY_BAR", symbol, instrument_type=asset, timeframe="1d", adjustment="qfq",
                                      start="2020-01-02", end="2020-01-02", parameters={"interactive_chart": True})
    result = ThesisLedgerProviderRuntime(store).execute_market_bars_v3(request, key)
    assert result.value["date"].tolist() == ["2020-01-02"]
    assert len(calls) == 1 and calls[0][1]["params"]["thscode"] == symbol
    if asset == "STOCK":
        assert result.value.attrs["thesis_ledger_source"]["coverage"]["complete"] is None
        assert result.value.attrs["thesis_ledger_source"]["coverage"]["calendar_revision"] is None
    assert "calendarEvidence" not in result.value.attrs
    no_admissions(store)
    calls.clear()
    strict = ThesisLedgerDataRequest("DAILY_BAR", symbol, instrument_type=asset, timeframe="1d", adjustment="qfq",
                                     start="2020-01-02", end="2020-01-02")
    with pytest.raises(ProviderCallError): ThesisLedgerProviderRuntime(store).execute_market_bars_v3(strict, key)
    assert calls == []


@pytest.mark.parametrize("fault", ["identity", "duplicate", "adjustment", "pagination"])
def test_observed_etf_still_rejects_invalid_source_response(monkeypatch, fault):
    import src.services.thesis_ledger_hithink_interactive_daily as observed
    from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
    data = {"thscode": "510300.SH", "interval": "1d", "adjust": None,
            "timestamp": _date_ms("2020-01-02"), "item": [_item("2020-01-02")]}
    if fault == "identity": data["thscode"] = "159516.SZ"
    elif fault == "duplicate": data["item"] *= 2
    elif fault == "adjustment": data["adjust"] = "hfq"
    else: data["has_more"] = True
    monkeypatch.setattr(source_adapter.requests, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: {"code": 0, "data": data}))
    snapshot = ProviderCredentialSnapshot.create("hithink", "control", "api_key", {"apiKey": "synthetic"}, 1, 1)
    request = ThesisLedgerDataRequest("DAILY_BAR", "510300.SH", instrument_type="ETF", timeframe="1d", adjustment="qfq", start="2020-01-02", end="2020-01-02")
    with pytest.raises(ProviderCallError): observed.read_interactive_hithink_daily(request, snapshot)


def test_empty_observed_window_remains_not_covered_instead_of_upstream_failure(monkeypatch, store):
    import src.services.thesis_ledger_hithink_interactive_daily as observed
    monkeypatch.setattr(source_adapter.requests, "get", lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: {
        "code": 0, "data": {"thscode": "510300.SH", "interval": "1d", "adjust": None, "timestamp": None, "item": []},
    }))
    store.save_provider_config("hithink", {"requestId": "synthetic", "credentials": {"method": "api_key", "values": {"apiKey": "synthetic"}}})
    key = {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq"}
    apply(store, key, {"providerId": "hithink", "upstreamSource": "fund-market-historical"})
    request = ThesisLedgerDataRequest("DAILY_BAR", "510300.SH", instrument_type="ETF", timeframe="1d", adjustment="qfq",
                                      start="2020-01-02", end="2020-01-02", parameters={"interactive_chart": True})
    with pytest.raises(ProviderCallError) as error: ThesisLedgerProviderRuntime(store).execute_market_bars_v3(request, key)
    assert error.value.code == "not_covered"
