"""补齐的复权组合经真实工厂／运行时接缝读取，拒绝资产和价格口径冒用。"""

from contextlib import contextmanager
from types import SimpleNamespace
import json

import pytest

from data_provider.exact_daily_contract import ExactDailyError
from data_provider.thesis_ledger_sources.baostock import read_daily_frame
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions

DAY = "2026-09-24"


class Result:
    def __init__(self, fields, rows, error_code="0", terminal_error="0"):
        self.fields, self.rows = fields, iter(rows)
        self.error_code, self.terminal_error = error_code, terminal_error

    def next(self):
        try:
            self.current = next(self.rows)
            return True
        except StopIteration:
            self.error_code = self.terminal_error
            return False

    def get_row_data(self):
        return self.current


class BaoSDK:
    def __init__(self, identity, flag):
        self.identity, self.flag, self.calls = identity, flag, []
        self.basic = Result(["code", "type"], [[identity, "5"]])
        self.history = Result("date,code,open,high,low,close,volume,amount,adjustflag".split(","),
                              [[DAY, identity, "2", "2.2", "1.9", "2.1", "1000", "2100", flag]])

    def query_stock_basic(self, **options):
        self.calls.append(("basic", options))
        return self.basic

    def query_history_k_data_plus(self, **options):
        self.calls.append(("history", options))
        return self.history

    @contextmanager
    def session(self, *args):
        self.calls.append(("login", {}))
        try:
            yield self
        finally:
            self.calls.append(("logout", {}))


def query(symbol="510300.SH", asset="ETF", adjustment="qfq"):
    return ThesisLedgerDataRequest("DAILY_BAR", symbol, instrument_type=asset, timeframe="1d",
                                  adjustment=adjustment, start=DAY, end=DAY)


def execute(store, provider, symbol, asset, adjustment):
    key = {"kind": "bar", "market": "CN", "assetType": asset, "capability": "DAILY_BAR",
           "timeframe": "1d", "adjustment": adjustment}
    assert apply(store, key, {"providerId": provider, "upstreamSource": provider})["effective"]["routes"][0]["targets"][0]["eligible"]
    result = ThesisLedgerProviderRuntime(store).execute_market_bars_v3(query(symbol, asset, adjustment), key)
    no_admissions(store)
    return result.value


@pytest.mark.parametrize("symbol,identity", [("510300.SH", "sh.510300"), ("159919.SZ", "sz.159919")])
@pytest.mark.parametrize("adjustment,flag", [("none", "3"), ("qfq", "2"), ("hfq", "1")])
def test_baostock_etf_all_bases_through_factory(monkeypatch, store, symbol, identity, adjustment, flag):
    sdk = BaoSDK(identity, flag)
    monkeypatch.setattr("data_provider.baostock_fetcher.BaostockFetcher._baostock_session", sdk.session)
    frame = execute(store, "baostock", symbol, "ETF", adjustment)
    assert frame.close.tolist() == [2.1]
    assert frame.volume.tolist() == [1000]
    assert sdk.calls[1] == ("basic", {"code": identity})
    assert sdk.calls[2] == ("history", {"code": identity, "fields": "date,code,open,high,low,close,volume,amount,adjustflag",
                                       "start_date": DAY, "end_date": DAY, "frequency": "d", "adjustflag": flag})
    assert sdk.calls[-1][0] == "logout"


@pytest.mark.parametrize("failure", ["type", "symbol", "empty", "duplicate", "basic-error", "basic-terminal", "basis", "history-symbol", "history-terminal", "missing-basis"])
def test_baostock_etf_rejects_unconfirmed_identity_and_basis(failure):
    sdk = BaoSDK("sh.510300", "2")
    if failure == "type":
        sdk.basic = Result(["code", "type"], [[sdk.identity, "1"]])
    elif failure == "symbol":
        sdk.basic = Result(["code", "type"], [["sz.510300", "5"]])
    elif failure == "empty":
        sdk.basic = Result(["code", "type"], [])
    elif failure == "duplicate":
        sdk.basic = Result(["code", "type"], [[sdk.identity, "5"]] * 2)
    elif failure == "basic-error":
        sdk.basic.error_code = "1"
    elif failure == "basic-terminal":
        sdk.basic.terminal_error = "1"
    elif failure == "basis":
        sdk.history = BaoSDK(sdk.identity, "3").history
    elif failure == "history-symbol":
        sdk.history = BaoSDK("sz.510300", "2").history
    elif failure == "history-terminal":
        sdk.history.terminal_error = "1"
    else:
        sdk.history = Result(sdk.history.fields[:-1], [[DAY, sdk.identity, "2", "2.2", "1.9", "2.1", "1000", "2100"]])
    with pytest.raises(ExactDailyError) as error:
        read_daily_frame(SimpleNamespace(_baostock_session=sdk.session), query())
    assert error.value.code == ("upstream_failure" if failure in {"basic-error", "basic-terminal", "history-terminal"} else "invalid_response")
    assert sdk.calls[-1][0] == "logout"
    if failure in {"type", "symbol", "empty", "duplicate", "basic-error", "basic-terminal"}:
        assert not any(name == "history" for name, _ in sdk.calls)


@pytest.mark.parametrize("symbol", ["600519.SH", "510300.HK", "510300.BJ"])
def test_baostock_etf_invalid_request_does_not_connect(symbol):
    def session():
        pytest.fail("无效 ETF 请求不能连接上游")
    with pytest.raises(ExactDailyError):
        read_daily_frame(SimpleNamespace(_baostock_session=session), query(symbol))


@pytest.mark.parametrize("symbol,identity", [("600519.SH", "sh600519"), ("000001.SZ", "sz000001")])
def test_tencent_stock_hfq_through_factory(monkeypatch, store, symbol, identity):
    calls = []
    def get(url, *, params, **options):
        calls.append(params)
        raw = {"code": 0, "data": {identity: {"hfqday": [[DAY, "20", "21", "22", "19", "10", "0", "0", "2"]]}}}
        return SimpleNamespace(text=params["_var"] + "=" + json.dumps(raw), raise_for_status=lambda: None)
    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", get)
    frame = execute(store, "tencent", symbol, "STOCK", "hfq")
    assert frame.close.tolist() == [21]
    assert frame.amount.tolist() == [20000]
    assert calls == [{"_var": "kline_dayhfq2026", "param": f"{identity},day,{DAY},{DAY},640,hfq",
                      "r": "0.8205512681390605"}]
    assert frame.attrs["tencentDailyRetrieval"]["adjustment"] == "hfq"


@pytest.mark.parametrize("symbol", ["510300.SH", "600519.SZ"])
def test_tencent_binding_rejects_asset_or_exchange_mismatch_before_fetch(symbol):
    from data_provider.thesis_ledger_sources.tencent import daily
    from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
    with pytest.raises(CapabilityContractError) as error:
        daily(query(symbol, "STOCK", "hfq"), ReadContext(None))
    assert error.value.code == "invalid_request"


def test_yahoo_back_adjust_does_not_define_uniform_hfq_ohlc():
    import pandas as pd
    from yfinance.utils import back_adjust
    frame = pd.DataFrame({"Open": [100.], "High": [110.], "Low": [90.], "Close": [105.], "Adj Close": [52.5]})
    adjusted = back_adjust(frame)
    assert adjusted.Open.tolist() == [50.]
    assert adjusted.Close.tolist() == [105.]
    assert adjusted.Close.iloc[0] > adjusted.High.iloc[0]
