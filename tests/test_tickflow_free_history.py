"""免费历史端点隔离、单请求预算及精确规范化；真实成功另由运行验收记录。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from data_provider.thesis_ledger_sources.tickflow_free_history import (
    BASE_URL, SOURCE, UPSTREAM, FreeHistoryConnection, daily,
)
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyQuery, ReadContext
from tests.test_configured_market_routes import store, apply, no_admissions


def query(adjustment="none", **changes):
    return DailyQuery.model_validate({
        "symbol": "600519.SH", "instrument_type": "STOCK", "adjustment": adjustment,
        "start": "2025-06-23", "end": "2025-07-04", "request_id": "offline",
        **changes,
    })


def raw():
    return {"timestamp": [1750608000000, 1750694400000],
            "open": [1400, 1401], "high": [1402, 1403], "low": [1399, 1400],
            "close": [1401, 1402], "volume": [1234, 0], "amount": [123456.78, 0]}


def adapter(payload):
    calls, closed = [], []
    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return deepcopy(payload)
    value = SimpleNamespace(
        _get_client=lambda: SimpleNamespace(klines=SimpleNamespace(get=get),
            instruments=SimpleNamespace(get=lambda symbol: {"symbol": symbol, "exchange": "SH", "region": "CN", "type": "stock"})),
        _daily_kline_count=FreeHistoryConnection._daily_kline_count,
        _date_to_ms=FreeHistoryConnection._date_to_ms,
        close=lambda: closed.append(True),
    )
    return value, calls, closed


def test_free_target_has_only_three_stock_daily_bindings():
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from data_provider.thesis_ledger_sources.tickflow import SOURCE as PAID
    assert SOURCE.credentials == () and SOURCE.manifest()["requiresCredential"] is False
    assert SOURCE.manifest()["displayName"] == "TickFlow 免费历史"
    assert len(SOURCE.bindings) == 3 and len(PAID.bindings) == 4
    assert PAID.manifest()["requiresCredential"] is True
    for binding in SOURCE.bindings:
        assert CAPABILITY_SOURCES.resolve(binding.key.route_key(), {
            "providerId": "tickflow-free", "upstreamSource": UPSTREAM,
        }) is binding
        assert binding.maximum_rows == 10000
    assert not any(b.key.capability != "DAILY_BAR" or b.key.asset_type != "STOCK" for b in SOURCE.bindings)


def test_sdk_free_connection_never_inherits_environment_key(monkeypatch):
    monkeypatch.setenv("TICKFLOW_API_KEY", "synthetic-not-a-secret")
    monkeypatch.setenv("TICKFLOW_BASE_URL", "https://invalid.example")
    connection = FreeHistoryConnection(4.5)
    try:
        assert connection.client._client.api_key == ""
        assert str(connection.client._client.base_url).rstrip("/") == BASE_URL
        assert connection.client._client.max_retries == 0
    finally:
        connection.close()


@pytest.mark.parametrize("adjustment,upstream", [("none", "none"), ("qfq", "forward"), ("hfq", "backward")])
def test_one_request_exact_window_and_units_not_guessed(adjustment, upstream):
    value, calls, closed = adapter(raw())
    result = daily(query(adjustment), ReadContext(value))
    assert result.pages_fetched == 1 and len(calls) == 1 and closed == [True]
    args, options = calls[0]
    assert args == ("600519.SH",)
    assert options == {"period": "1d", "count": 41,
                       "start_time": 1750608000000, "end_time": 1751644799999,
                       "adjust": upstream, "as_dataframe": False}
    assert result.frame.date.tolist() == ["2025-06-23", "2025-06-24"]
    assert result.frame.volume.tolist() == [1234, 0]  # 不沿原Fetcher的手转股猜倍率。
    assert result.frame.amount.tolist() == [123456.78, 0]
    assert result.frame.attrs["upstream_source"] == UPSTREAM


@pytest.mark.parametrize("field", ["volume", "amount"])
def test_missing_volume_or_amount_remains_unknown(field):
    payload = raw()
    payload.pop(field)
    value, _, _ = adapter(payload)
    frame = daily(query(), ReadContext(value)).frame
    assert frame[field].tolist() == [None, None]


@pytest.mark.parametrize("failure,code", [
    ("shape", "invalid_response"), ("price", "invalid_response"),
    ("length", "invalid_response"), ("identity", "invalid_response"),
    ("duplicate", "invalid_response"), ("numeric", "invalid_response"),
    ("limit", "insufficient_coverage"), ("empty", "not_covered"),
])
def test_bad_response_does_not_claim_complete(failure, code):
    payload = raw()
    if failure == "shape": payload = []
    elif failure == "price": payload.pop("close")
    elif failure == "length": payload["amount"] = [1]
    elif failure == "identity": payload["symbol"] = "000001.SZ"
    elif failure == "duplicate": payload["timestamp"][1] = payload["timestamp"][0]
    elif failure == "numeric": payload["close"][0] = "broken"
    elif failure == "limit": payload = {key: values[:1] * 41 for key, values in payload.items()}
    elif failure == "empty": payload = {key: [] for key in payload}
    value, calls, closed = adapter(payload)
    with pytest.raises(CapabilityContractError) as caught:
        daily(query(), ReadContext(value))
    assert caught.value.code == code and len(calls) == 1 and closed == [True]


def test_out_of_budget_window_and_noncanonical_symbol_never_call_upstream():
    for changes in ({"start": "2000-01-01"}, {"symbol": "600519"},
                    {"symbol": "900901.SH"}, {"symbol": "200002.SZ"}, {"symbol": "510300.SH"}):
        value, calls, closed = adapter(raw())
        with pytest.raises(CapabilityContractError) as caught:
            daily(query(**changes), ReadContext(value))
        assert caught.value.code == "invalid_request" and calls == [] and closed == [True]


def test_upstream_failure_propagates_and_client_closes():
    value, _, closed = adapter(raw())
    def fail(*args, **kwargs):
        raise RuntimeError("offline-upstream-error")
    value._get_client = lambda: SimpleNamespace(instruments=SimpleNamespace(get=fail))
    with pytest.raises(RuntimeError, match="offline-upstream-error"):
        daily(query(), ReadContext(value))
    assert closed == [True]


def test_metadata_does_not_turn_request_binding_into_vendor_kline_echo():
    value, calls, closed = adapter(raw())
    frame = daily(query(), ReadContext(value)).frame
    identity = frame.attrs["tickflow_free_history_identity"]
    assert identity["instrument"]["symbol"] == "600519.SH"
    assert identity["klineIdentity"] == "request-bound" and identity["historicalIdentity"] == "notVerified"
    bad, calls, closed = adapter(raw())
    bad._get_client = lambda: SimpleNamespace(instruments=SimpleNamespace(get=lambda symbol: {"symbol": symbol, "type": "etf"}))
    with pytest.raises(CapabilityContractError) as caught:
        daily(query(), ReadContext(bad))
    assert caught.value.code == "invalid_response" and calls == [] and closed == [True]


def test_connection_probe_uses_same_daily_handler_instead_of_legacy_fetcher(monkeypatch):
    from data_provider.thesis_ledger_sources import tickflow_free_history as source
    calls = []
    monkeypatch.setattr(source, "daily", lambda query, context: calls.append((query, context)))
    context = SimpleNamespace(sample_symbol="600519", adapter=object(), snapshot=None)
    SOURCE.probe_policy.read(context)
    query, read_context = calls[0]
    assert query.symbol == "600519.SH" and query.adjustment == "none" and query.instrument_type == "STOCK"
    assert read_context.adapter is context.adapter and read_context.probe
    assert SOURCE.probe_policy.capabilities == ("DAILY_BAR",)


def test_runtime_consumes_selected_free_route_without_credentials_or_window_admission(store):
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
    key = SOURCE.bindings[0].key.route_key()
    target = {"providerId": "tickflow-free", "upstreamSource": UPSTREAM}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    value, calls, closed = adapter(raw())
    request = ThesisLedgerDataRequest("DAILY_BAR", "600519.SH", instrument_type="STOCK",
                                     timeframe="1d", adjustment="none", start="2025-06-23", end="2025-07-04")
    result = ThesisLedgerProviderRuntime(store, adapters={"tickflow-free": value}).execute_market_bars_v3(request, key)
    assert result.provider == "tickflow-free" and result.upstream_source == UPSTREAM
    assert result.value["close"].tolist() == [1401, 1402]
    assert result.value.attrs["thesis_ledger_v3_pagination"]["pagesFetched"] == 1
    assert len(calls) == 1 and closed == [True]
    no_admissions(store)
