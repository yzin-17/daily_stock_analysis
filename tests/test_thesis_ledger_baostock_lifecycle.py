"""单证券基础日期与有界状态响应；摘牌日不会被改成最后交易日。"""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import pandas as pd
import pytest
from src.services.thesis_ledger_baostock_lifecycle import normalize_baostock_lifecycle, read_baostock_lifecycle
from src.services.thesis_ledger_baostock_status_source import read_baostock_status_response
from src.services.thesis_ledger_status_cache import HistoricalStatusCache
from tests.test_thesis_ledger_baostock_status_routes import request, value

CLOCK = "2026-10-09T00:00:00+00:00"

def frame(ipo="2019-01-23", out="", code="sz.300757", kind="1"):
    return pd.DataFrame([{"code": code, "ipoDate": ipo, "outDate": out, "type": kind}])

def normalize(data):
    return normalize_baostock_lifecycle(data, code="sz.300757", observed_at=CLOCK)

@pytest.mark.parametrize("ipo,out,ipo_state,out_state", [
    ("2019-01-23", "", "known", "notProvided"),
    ("2019-01-23", "2024-10-10", "known", "known"),
    ("", "", "notProvided", "notProvided"),
    ("2019/01/23", "0000-00-00", "invalid", "invalid"),
    ("2027-01-01", "2027-02-01", "invalid", "invalid"),
    ("2019-01-23", "2018-12-01", "known", "invalid"),
])
def test_dates_are_independent_and_out_is_not_last_trade(ipo, out, ipo_state, out_state):
    result = normalize(frame(ipo, out))
    assert result["ipoDateStatus"] == ipo_state and result["outDateStatus"] == out_state
    assert result["ipoDate"] == (ipo if ipo_state == "known" else None)
    assert result["outDate"] == (out if out_state == "known" else None)
    assert len(result["responseSha256"]) == 64
    assert "lastTradingDate" not in result

@pytest.mark.parametrize("data", [frame(code="sh.600221"), frame(kind="2"), pd.DataFrame(), pd.concat([frame(), frame()])])
def test_identity_and_unique_row_are_required(data):
    with pytest.raises(ValueError):
        normalize(data)

def test_digest_preserves_raw_date_change_not_clock():
    first = normalize(frame())
    assert first["responseSha256"] != normalize(frame(out="2024-10-10"))["responseSha256"]
    assert first["responseSha256"] == normalize_baostock_lifecycle(frame(), code="sz.300757", observed_at="2026-10-10T00:00:00Z")["responseSha256"]

def test_failed_basic_read_is_unavailable_not_zero_or_delisted():
    @contextmanager
    def session():
        yield SimpleNamespace(query_stock_basic=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("offline")))
    adapter = SimpleNamespace(_convert_stock_code=lambda _: "sz.300757", _baostock_session=session)
    result = read_baostock_lifecycle(adapter, "300757.SZ")
    assert result["outDateStatus"] == "unavailable" and result["outDate"] is None
    assert "responseSha256" not in result

def test_lifecycle_is_carried_through_selected_cached_status_response(tmp_path):
    clock = lambda: datetime(2026, 10, 9, tzinfo=timezone.utc)
    adapter = SimpleNamespace(_historical_status_cache=HistoricalStatusCache(tmp_path, clock=clock))
    calls = []
    def reader(*args):
        calls.append(args)
        return {**value(), "basicLifecycle": normalize(frame())}
    first = read_baostock_status_response(adapter, request(), reader=reader, clock=clock)
    second = read_baostock_status_response(adapter, {**request(), "requestId": "cached"}, reader=reader, clock=clock)
    assert len(calls) == 1 and first["basicLifecycle"] == second["basicLifecycle"]
    life = first["basicLifecycle"]
    assert life["ipoDate"] == "2019-01-23" and life["outDate"] is None
    source = next(r for r in first["sourceRecords"] if r["id"] == life["sourceRecordId"])
    assert source["queryMethod"] == "query_stock_basic" and source["symbol"] == request()["symbol"]
    assert source["observedAt"] == CLOCK
    assert first["days"] == second["days"] and len(first["sourceRecords"]) == 2


def test_sdk_reads_exact_symbol_and_keeps_delisting_date():
    calls = []
    class Result:
        error_code = "0"
        error_msg = ""
        fields = ["code", "ipoDate", "outDate", "type"]
        index = 0
        def next(self):
            self.index += 1
            return self.index == 1
        def get_row_data(self):
            return ["sz.300757", "2019-01-23", "2024-10-10", "1"]
    @contextmanager
    def session():
        def query(**kwargs):
            calls.append(kwargs)
            return Result()
        yield SimpleNamespace(query_stock_basic=query)
    adapter = SimpleNamespace(_convert_stock_code=lambda _: "sz.300757", _baostock_session=session)
    result = read_baostock_lifecycle(adapter, "300757.SZ", clock=lambda: datetime.fromisoformat(CLOCK))
    assert calls == [{"code": "sz.300757"}]
    assert result["outDate"] == "2024-10-10" and result["outDateStatus"] == "known"


def test_existing_fetcher_login_error_only_marks_basic_dates_unavailable():
    from data_provider.base import DataFetchError
    @contextmanager
    def session():
        raise DataFetchError("login unavailable")
        yield
    result = read_baostock_lifecycle(SimpleNamespace(_convert_stock_code=lambda _: "sz.300757", _baostock_session=session), "300757.SZ")
    assert result["ipoDateStatus"] == result["outDateStatus"] == "unavailable"


def test_basic_failure_is_not_cached_as_prolonged_unknown(tmp_path):
    clock = lambda: datetime(2026, 10, 9, tzinfo=timezone.utc)
    adapter = SimpleNamespace(_historical_status_cache=HistoricalStatusCache(tmp_path, clock=clock))
    calls = []
    def reader(*args):
        calls.append(args)
        life = {"ipoDate": None, "outDate": None, "ipoDateStatus": "unavailable", "outDateStatus": "unavailable", "reason": "基础日期来源不可用"} if len(calls) == 1 else normalize(frame())
        return {**value(), "basicLifecycle": life}
    failed = read_baostock_status_response(adapter, request(), reader=reader, clock=clock)
    assert failed["basicLifecycle"]["ipoDateStatus"] == "unavailable"
    assert not list(tmp_path.glob("*.json"))
    recovered = read_baostock_status_response(adapter, request(), reader=reader, clock=clock)
    cached = read_baostock_status_response(adapter, request(), reader=reader, clock=clock)
    assert len(calls) == 2
    assert recovered["basicLifecycle"] == cached["basicLifecycle"]
    assert failed["days"] == recovered["days"]


def test_daily_and_basic_reads_borrow_one_existing_session(monkeypatch):
    from datetime import date
    from src.services.thesis_ledger_baostock_lifecycle import read_status_with_lifecycle
    sessions = []
    sdk = object()
    @contextmanager
    def session():
        sessions.append("open")
        yield sdk
        sessions.append("close")
    adapter = SimpleNamespace(_convert_stock_code=lambda _: "sz.300757", _baostock_session=session)
    def daily(borrowed, **kwargs):
        with borrowed._baostock_session() as connection:
            assert connection is sdk
        return value()
    def basic(borrowed, symbol):
        with borrowed._baostock_session() as connection:
            assert connection is sdk
        return normalize(frame())
    monkeypatch.setattr("src.services.thesis_ledger_baostock_daily_status.read_baostock_daily_status", daily)
    monkeypatch.setattr("src.services.thesis_ledger_baostock_lifecycle.read_baostock_lifecycle", basic)
    result = read_status_with_lifecycle(adapter, symbol="300757.SZ", start=date(2023, 8, 31), end=date(2023, 9, 1))
    assert result["basicLifecycle"]["ipoDateStatus"] == "known"
    assert sessions == ["open", "close"]
