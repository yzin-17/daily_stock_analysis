"""历史日线完整分页与精确复权、身份、窗口接缝。"""

import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import pytest

from data_provider.exact_daily_contract import ExactDailyError
from data_provider.futu_daily import read_futu_daily
from data_provider.futu_market import FutuMarketAdapter, RESULT_PREFIX
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from src.services.thesis_ledger_market_v3_facts import market_pagination_contract_v3
from tests.test_configured_market_routes import store, apply, no_admissions


def request(**changes):
    return {"symbol": "00700.HK", "instrumentType": "STOCK", "adjustment": "none",
            "start": "2026-05-18", "end": "2026-05-20", **changes}


def row(day="2026-05-18", **changes):
    return {"code": "HK.00700", "time_key": day + " 00:00:00", "open": 10., "high": 12.,
            "low": 9., "close": 11., "volume": 0, "turnover": None, **changes}


def sdk_for(pages):
    ctx = Mock()
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{"code": "HK.00700", "stock_type": "STOCK"}])
    ctx.request_history_kline.side_effect = pages
    return SimpleNamespace(OpenQuoteContext=Mock(return_value=ctx), RET_OK=0,
        Market=SimpleNamespace(HK="HK", US="US"), SecurityType=SimpleNamespace(STOCK="STOCK", ETF="ETF"),
        AuType=SimpleNamespace(NONE="NONE", QFQ="QFQ", HFQ="HFQ"), KLType=SimpleNamespace(K_DAY="K_DAY")), ctx


@pytest.mark.parametrize("adjustment", ["none", "qfq", "hfq"])
def test_complete_pages_explicit_adjustment_and_nullable_metrics(adjustment):
    sdk, ctx = sdk_for([(0, pd.DataFrame([row()]), b"next"),
                       (0, pd.DataFrame([row("2026-05-19")]), None)])
    frame = read_futu_daily(sdk, "localhost", 11111, request(adjustment=adjustment))
    assert list(frame["date"]) == ["2026-05-18", "2026-05-19"]
    assert frame["volume"].tolist() == [0, 0] and frame["amount"].tolist() == [None, None]
    assert frame.attrs["upstream_source"] == "futu" and len(frame.attrs["responseSha256"]) == 64
    assert frame.attrs["has_more_before"] is False
    calls = ctx.request_history_kline.call_args_list
    assert len(calls) == 2 and calls[0].kwargs["page_req_key"] is None and calls[1].kwargs["page_req_key"] == b"next"
    assert all(c.args == ("HK.00700",) and c.kwargs["autype"] == ("NONE" if adjustment == "none" else adjustment.upper())
               and c.kwargs["ktype"] == "K_DAY" and c.kwargs["extended_time"] is False for c in calls)
    ctx.close.assert_called_once()


@pytest.mark.parametrize("second,code", [
    ((-1, "no permission", None), "upstream_failure"),
    ((0, pd.DataFrame([row("2026-05-19")]), b"next"), "invalid_response"),
    ((0, pd.DataFrame([row("2026-05-19")]), "invalid"), "invalid_response"),
    ((0, pd.DataFrame([row()]), None), "invalid_response"),
    ((0, pd.DataFrame([row("2026-05-19", code="HK.00147")]), None), "invalid_response"),
    ((0, pd.DataFrame([row("2026-05-21")]), None), "invalid_response"),
    ((0, pd.DataFrame(), None), "invalid_response"),
])
def test_later_failure_does_not_submit_partial_data(second, code):
    sdk, ctx = sdk_for([(0, pd.DataFrame([row()]), b"next"), second])
    with pytest.raises(ExactDailyError) as error:
        read_futu_daily(sdk, "localhost", 11111, request())
    assert error.value.code == code
    ctx.close.assert_called_once()


def test_page_ceiling_fails_instead_of_claiming_complete_data():
    sdk, ctx = sdk_for([(0, pd.DataFrame([row()]), str(n).encode()) for n in range(64)])
    with pytest.raises(ExactDailyError) as error:
        read_futu_daily(sdk, "localhost", 11111, request())
    assert error.value.code == "insufficient_coverage" and ctx.request_history_kline.call_count == 64
    ctx.close.assert_called_once()


@pytest.mark.parametrize("changes", [{"adjustment": "auto"}, {"start": "bad"},
                                      {"start": "2026-05-21"}, {"instrumentType": "INDEX"}])
def test_invalid_query_fails_before_connection(changes):
    sdk, _ = sdk_for([])
    with pytest.raises(ExactDailyError):
        read_futu_daily(sdk, "localhost", 11111, request(**changes))
    sdk.OpenQuoteContext.assert_not_called()


def test_wrong_static_asset_fails_before_history():
    sdk, ctx = sdk_for([])
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{"code": "HK.00700", "stock_type": "ETF"}])
    with pytest.raises(ExactDailyError):
        read_futu_daily(sdk, "localhost", 11111, request())
    ctx.request_history_kline.assert_not_called()
    ctx.close.assert_called_once()


@pytest.mark.parametrize("market,symbol,asset", [("HK", "00700.HK", "STOCK"), ("US", "SPY.US", "ETF")])
def test_real_bar_runtime_uses_futu_complete_response(store, monkeypatch, market, symbol, asset):
    store.save_provider_config("futu", {"requestId": "synthetic", "enabled": True,
        "credentials": {"method": "opend", "values": {"host": "localhost", "port": "11111"}}})
    key = {"kind": "bar", "market": market, "assetType": asset, "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "hfq"}
    target = {"providerId": "futu", "upstreamSource": "futu"}
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    sdk, _ = sdk_for([(0, pd.DataFrame([row()]), None)])
    frame = read_futu_daily(sdk, "localhost", 11111, request())
    calls = []
    def run(*args, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(returncode=0, stdout=RESULT_PREFIX + json.dumps({
            "rows": frame[["date", "open", "high", "low", "close", "volume", "amount"]].to_dict(orient="records"),
            "attrs": {**frame.attrs, "fieldAvailability": {"volume": True, "amount": False}},
        }))
    monkeypatch.setattr(subprocess, "run", run)
    runtime = ThesisLedgerProviderRuntime(store)
    req = ThesisLedgerDataRequest("DAILY_BAR", symbol, instrument_type=asset, adjustment="hfq", timeframe="1d",
                                 start="2026-05-18", end="2026-05-20")
    result = runtime.execute_market_bars_v3(req, key)
    assert result.value["close"].tolist() == [11.] and result.provider == "futu"
    assert len(calls) == 1 and calls[0]["operation"] == "daily" and calls[0]["adjustment"] == "hfq"
    assert calls[0]["symbol"] == symbol and calls[0]["instrumentType"] == asset
    assert market_pagination_contract_v3(asset, "futu", "futu")["protocol"] == "dsa-futu-exact-daily-sdk-pagination-v1"
    no_admissions(store)
