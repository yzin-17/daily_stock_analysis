"""有凭证报价的精确接口、准入为空与配置前置检查。"""

from types import SimpleNamespace
import pandas as pd
import pytest
from tests.test_configured_market_routes import store, apply, no_admissions
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime


@pytest.mark.parametrize("provider,method,values", [
    ("tushare", "token", {"token": "synthetic"}),
    ("tickflow", "api_key", {"apiKey": "synthetic"}),
])
def test_exact_quote_executes_without_record_or_hidden_source(store, provider, method, values):
    store.save_provider_config(provider, {"requestId": "synthetic", "credentials": {"method": method, "values": values}})
    key = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "REALTIME_QUOTE"}
    target = {"providerId": provider, "upstreamSource": provider}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    calls = []
    quote = SimpleNamespace(price=12., open_price=11., high=13., low=10., pre_close=10.)
    if provider == "tushare":
        def query(endpoint, ts_code, fields):
            assert endpoint == "rt_k"
            assert fields == "ts_code,close,open,high,low,pre_close,vol,amount,trade_time"
            calls.append(ts_code)
            return pd.DataFrame({"ts_code": [ts_code], "close": [12.], "open": [11.], "high": [13.], "low": [10.], "pre_close": [10.]})
        adapter = SimpleNamespace(_token="synthetic", _api=object(), _minute_start=None,
                                  _check_rate_limit=lambda: None, _build_api_client=lambda _: SimpleNamespace(query=query))
    else:
        def get(symbols): calls.append(symbols[0]); return [{"symbol": symbols[0]}]
        adapter = SimpleNamespace(_get_client=lambda: SimpleNamespace(quotes=SimpleNamespace(get=get)),
                                  _quote_to_unified_quote=lambda symbol, raw: quote)
    runtime = ThesisLedgerProviderRuntime(store, adapters={provider: adapter})
    result = runtime.execute_request(ThesisLedgerDataRequest("REALTIME_QUOTE", "600519.SH", instrument_type="STOCK"))
    assert result.value.price == 12.
    assert result.upstream_source == provider and calls == ["600519.SH"]
    no_admissions(store)


def test_missing_sdk_is_a_configuration_reason_and_does_not_reject_choice(monkeypatch, store):
    import src.services.thesis_ledger_provider_dependencies as dependencies
    monkeypatch.setattr(dependencies, "find_spec", lambda _: None)
    key = {"kind": "bar", "market": "US", "assetType": "STOCK", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq"}
    target = {"providerId": "yfinance", "upstreamSource": "yfinance"}
    response = apply(store, key, target)
    assert response["status"] == "applied"
    assert response["effective"]["routes"][0]["targets"][0]["reason"] == "dependency_missing"
    catalog = ThesisLedgerProviderRuntime(store).market_route_catalog_v3()
    assert next(entry for entry in catalog["entries"] if entry["key"] == key and entry["target"] == target)["state"] == "dependency_missing"
    no_admissions(store)


def test_longbridge_credential_fields_are_checked_as_one_complete_snapshot():
    from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
    from src.services.thesis_ledger_price_route_access import basic_price_credential_ready
    complete = ProviderCredentialSnapshot.create("longbridge", "control", "legacy", {"appKey": "synthetic", "appSecret": "synthetic", "accessToken": "synthetic"}, 1, 1)
    assert basic_price_credential_ready(complete)
    incomplete = ProviderCredentialSnapshot.create("longbridge", "control", "legacy", {"appKey": "synthetic"}, 1, 1)
    assert not basic_price_credential_ready(incomplete)


def test_environment_key_rotation_with_same_versions_rebuilds_cached_adapter():
    from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
    snapshots = [ProviderCredentialSnapshot.create("finnhub", "environment", "api_key", {"apiKey": key}, 0, 0)
                 for key in ("synthetic-initial", "synthetic-next")]
    state = SimpleNamespace(snapshot=snapshots[0])
    runtime = ThesisLedgerProviderRuntime(SimpleNamespace(provider_credential_snapshot=lambda _: state.snapshot))
    first = runtime._adapter("finnhub")
    state.snapshot = snapshots[1]
    second = runtime._adapter("finnhub")
    assert first is not second
    assert first._api_key == "synthetic-initial" and second._api_key == "synthetic-next"
    assert "synthetic-next" not in repr(runtime._adapter_versions)


@pytest.mark.parametrize("symbol,secid,market", [("510300.SH", "1.510300", 1), ("159516.SZ", "0.159516", 0)])
def test_akshare_etf_quote_is_single_symbol_and_exchange_pinned(monkeypatch, store, symbol, secid, market):
    import data_provider.akshare_etf_quote as native
    calls = []
    def get(url, params, timeout):
        calls.append((url, params, timeout))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": {
            "f57": symbol.split(".")[0], "f13": market, "f43": 12., "f46": 11., "f44": 13., "f45": 10., "f60": 10.,
        }})
    monkeypatch.setattr(native.requests, "get", get)
    key = {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "REALTIME_QUOTE"}
    target = {"providerId": "akshare", "upstreamSource": "eastmoney"}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": object()})
    result = runtime.execute_request(ThesisLedgerDataRequest("REALTIME_QUOTE", symbol, instrument_type="ETF"))
    assert result.value.price == 12.
    assert len(calls) == 1 and calls[0][1]["secid"] == secid
    assert result.value.volume is None and result.value.amount is None
    no_admissions(store)


def test_akshare_etf_quote_rejects_response_exchange_mismatch(monkeypatch):
    import data_provider.akshare_etf_quote as native
    from src.services.thesis_ledger_provider_runtime import ProviderCallError
    monkeypatch.setattr(native.requests, "get", lambda *args, **kwargs: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": {"f57": "510300", "f13": 0}}))
    with pytest.raises(ProviderCallError) as error:
        native.exact_etf_quote("510300.SH", ProviderCallError)
    assert error.value.code == "invalid_response"


def test_exact_etf_failure_falls_back_wholly_without_universe_request(monkeypatch, store):
    import data_provider.akshare_etf_quote as native
    from src.services.thesis_ledger_provider_runtime import ProviderCallError
    calls = []
    def get(*args, **kwargs):
        calls.append("akshare-single")
        raise native.requests.Timeout("synthetic timeout")
    monkeypatch.setattr(native.requests, "get", get)
    key = {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "REALTIME_QUOTE"}
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "fallback", "revision": 1, "enabled": True,
                           "routes": [{"key": key, "targets": [{"providerId": provider, "upstreamSource": "eastmoney"} for provider in ("akshare", "efinance")]}]})
    def single(symbol):
        calls.append("efinance-single")
        return SimpleNamespace(price=12., open_price=11., high=13., low=10., pre_close=10.)
    runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": object(), "efinance": SimpleNamespace(get_realtime_quote_single_symbol=single)})
    result = runtime.execute_request(ThesisLedgerDataRequest("REALTIME_QUOTE", "510300.SH", instrument_type="ETF"))
    assert result.provider == "efinance" and result.fallback_used and result.route_index == 1
    assert calls == ["akshare-single", "efinance-single"]
