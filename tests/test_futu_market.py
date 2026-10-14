"""富途精确报价的身份、类型、时区与故障边界。"""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from data_provider.futu_market import FutuMarketAdapter, FutuMarketError, RESULT_PREFIX, futu_security, read_futu_quote
from tests.test_configured_market_routes import store, apply, no_admissions
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime


def quote_row(code="HK.00700", **changes):
    return {"code": code, "last_price": 12., "prev_close_price": 11., "open_price": 11.,
            "high_price": 13., "low_price": 10., "volume": 0, "turnover": None,
            "update_time": "2026-07-01 10:00:00", **changes}


def sdk_for(code="HK.00700", asset="STOCK", **changes):
    context = Mock()
    context.get_stock_basicinfo.return_value = 0, pd.DataFrame([{"code": code, "stock_type": asset}])
    context.get_market_snapshot.return_value = 0, pd.DataFrame([quote_row(code, **changes)])
    sdk = SimpleNamespace(OpenQuoteContext=Mock(return_value=context), RET_OK=0,
                          Market=SimpleNamespace(HK="HK", US="US"),
                          SecurityType=SimpleNamespace(STOCK="STOCK", ETF="ETF"))
    return sdk, context


@pytest.mark.parametrize("symbol,code,zone", [("00700.HK", "HK.00700", "Asia/Hong_Kong"),
    ("HK00700", "HK.00700", "Asia/Hong_Kong"), ("HK.00700", "HK.00700", "Asia/Hong_Kong"),
    ("AAPL.US", "US.AAPL", "America/New_York"), ("US.AAPL", "US.AAPL", "America/New_York"),
    ("BRK.B.US", "US.BRK.B", "America/New_York")])
def test_symbol_binding(symbol, code, zone):
    assert futu_security(symbol) == (code, code[:2], zone)


@pytest.mark.parametrize("symbol", ["600519.SH", "SH.600519", "00700.HK.US", "00000.HK", "HK000700", "00700", ""])
def test_invalid_symbol_rejected_before_sdk(symbol):
    with pytest.raises(FutuMarketError):
        futu_security(symbol)


@pytest.mark.parametrize("symbol,code,asset,expected", [
    ("00700.HK", "HK.00700", "STOCK", "2026-07-01T02:00:00+00:00"),
    ("SPY.US", "US.SPY", "ETF", "2026-07-01T14:00:00+00:00"),
])
def test_query_shape_identity_units_and_clock(symbol, code, asset, expected):
    sdk, context = sdk_for(code, asset)
    result = read_futu_quote(sdk, "localhost", 11111, symbol, asset)
    assert result["provider_timestamp"] == expected
    assert result["fetched_at"] != expected
    assert result["volume"] == 0 and result["amount"] is None
    context.get_stock_basicinfo.assert_called_once_with(code[:2], stock_type=asset, code_list=[code])
    context.get_market_snapshot.assert_called_once_with([code])
    context.close.assert_called_once()


def test_us_winter_clock_is_not_fixed_utc_offset():
    sdk, _ = sdk_for("US.AAPL", update_time="2026-01-06 10:00:00")
    assert read_futu_quote(sdk, "localhost", 11111, "AAPL.US", "STOCK")["provider_timestamp"] == "2026-01-06T15:00:00+00:00"


@pytest.mark.parametrize("changes", [{"update_time": "bad"}, {"last_price": float("nan")}, {"last_price": True}])
def test_bad_fields_close_context(changes):
    sdk, context = sdk_for(**changes)
    with pytest.raises(FutuMarketError) as error:
        read_futu_quote(sdk, "localhost", 11111, "00700.HK", "STOCK")
    assert error.value.code == "invalid_response"
    context.close.assert_called_once()


@pytest.mark.parametrize("change", ["wrong-asset", "wrong-code", "empty", "failure", "exception"])
def test_failed_identity_or_sdk_never_returns_partial_or_calls_another_source(change):
    sdk, context = sdk_for()
    if change == "wrong-asset":
        context.get_stock_basicinfo.return_value = 0, pd.DataFrame([{"code": "HK.00700", "stock_type": "ETF"}])
    elif change == "wrong-code":
        context.get_market_snapshot.return_value = 0, pd.DataFrame([quote_row("HK.00147")])
    elif change == "empty":
        context.get_market_snapshot.return_value = 0, pd.DataFrame()
    elif change == "failure":
        context.get_market_snapshot.return_value = -1, "permission denied"
    else:
        context.get_market_snapshot.side_effect = OSError("disconnected")
    with pytest.raises((FutuMarketError, OSError)):
        read_futu_quote(sdk, "localhost", 11111, "00700.HK", "STOCK")
    context.close.assert_called_once()
    assert not context.request_history_kline.called


def test_missing_source_time_is_not_replaced_with_fetch_time():
    sdk, _ = sdk_for(update_time=None, volume=None)
    value = read_futu_quote(sdk, "localhost", 11111, "00700.HK", "STOCK")
    assert value["provider_timestamp"] is None and value["fetched_at"]
    assert value["volume"] is None


def test_subprocess_deadline_is_terminable(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 4.5
        assert json.loads(kwargs["input"])["symbol"] == "00700.HK"
        raise subprocess.TimeoutExpired("futu", 4.5)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(FutuMarketError) as error:
        FutuMarketAdapter(host="localhost", port="11111").get_realtime_quote("00700.HK")
    assert error.value.code == "timeout"


@pytest.mark.parametrize("market,symbol,asset", [("HK", "00700.HK", "STOCK"), ("US", "SPY.US", "ETF")])
def test_real_runtime_route_calls_exact_adapter_without_admission_or_hidden_fallback(store, monkeypatch, market, symbol, asset):
    store.save_provider_config("futu", {"requestId": "synthetic", "enabled": True,
        "credentials": {"method": "opend", "values": {"host": "localhost", "port": "11111"}}})
    key = {"kind": "data", "market": market, "assetType": asset, "capability": "REALTIME_QUOTE"}
    target = {"providerId": "futu", "upstreamSource": "futu"}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    value = {"price": 12., "pre_close": 11., "open_price": 11., "high": 13., "low": 10.,
             "volume": 0, "amount": None, "provider_timestamp": None, "fetched_at": "2026-07-01T14:00:00+00:00"}
    calls = []
    def run(*args, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout=RESULT_PREFIX + json.dumps(value))
    monkeypatch.setattr(subprocess, "run", run)
    runtime = ThesisLedgerProviderRuntime(store)
    result = runtime.execute_request(ThesisLedgerDataRequest("REALTIME_QUOTE", symbol, instrument_type=asset))
    assert result.value.price == 12. and result.value.volume == 0
    assert result.provider == "futu" and result.upstream_source == "futu"
    assert len(calls) == 1 and calls[0]["instrumentType"] == asset
    assert futu_security(calls[0]["symbol"])[0] == futu_security(symbol)[0]
    no_admissions(store)
