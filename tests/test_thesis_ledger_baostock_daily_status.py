"""按日状态与粗标记的独立覆盖；不借价格或整窗汇总补造事实。"""

from contextlib import contextmanager
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.services.thesis_ledger_baostock_daily_status import (
    FIELDS, normalize_baostock_daily_status, read_baostock_daily_status,
)

CODE = "sz.300757"
START, END = date(2023, 8, 31), date(2023, 9, 4)
CALENDAR = pd.DataFrame([
    {"calendar_date": "2023-08-31", "is_trading_day": "1"},
    {"calendar_date": "2023-09-01", "is_trading_day": "1"},
    {"calendar_date": "2023-09-02", "is_trading_day": "0"},
    {"calendar_date": "2023-09-03", "is_trading_day": "0"},
    {"calendar_date": "2023-09-04", "is_trading_day": "1"},
])


def row(day, status="1", st="0", **extra):
    return {"date": day, "code": CODE, "tradestatus": status, "isST": st, **extra}


def normalize(rows, **kwargs):
    return normalize_baostock_daily_status(code=CODE, trading_calendar=kwargs.get("calendar", CALENDAR),
        history=pd.DataFrame(rows), start=START, end=END, observed_at="2026-10-09T00:00:00+00:00")


def test_cross_month_start_already_suspended_and_price_does_not_imply_trade():
    result = normalize([row("2023-08-31", "0", close="10", volume=""),
                        row("2023-09-01", "0", st="1"), row("2023-09-04")])
    assert [d["state"] for d in result["days"]] == ["suspended", "suspended", "unknown", "unknown", "trading"]
    assert result["days"][1]["isST"] == "1"
    assert result["coverage"]["complete"] is True
    assert all("riskWarning" not in d and "dailyPriceLimit" not in d for d in result["days"])


def test_missing_row_does_not_erase_known_suspension_or_prove_normal():
    result = normalize([row("2023-08-31", "0"), row("2023-09-04")])
    assert result["days"][0]["state"] == "suspended"
    assert result["days"][1]["state"] == "unknown"
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["statusUnknownDates"] == ["2023-09-01"]


@pytest.mark.parametrize("invalid", ["", None, float("nan"), "2", True])
def test_invalid_status_keeps_independently_known_st(invalid):
    result = normalize([row("2023-08-31", invalid, "1"), row("2023-09-01"), row("2023-09-04")])
    assert result["days"][0] == {"date": "2023-08-31", "state": "unknown", "tradestatus": "unknown", "isST": "1"}
    assert result["coverage"]["complete"] is False


def test_numeric_zero_and_unknown_st_are_separate_from_trade_status():
    result = normalize([row("2023-08-31", 0, ""), row("2023-09-01", 1), row("2023-09-04", 1)])
    assert result["days"][0]["state"] == "suspended"
    assert result["coverage"]["complete"] is True
    assert result["coverage"]["stUnknownDates"] == ["2023-08-31"]


def test_duplicate_conflict_only_makes_affected_day_unknown():
    result = normalize([row("2023-08-31", "0"), row("2023-08-31", "1"), row("2023-09-01"), row("2023-09-04")])
    assert result["days"][0]["state"] == "unknown"
    assert result["days"][-1]["state"] == "trading"


def test_calendar_gap_does_not_infer_session_from_price_row():
    result = normalize([row("2023-08-31"), row("2023-09-01"), row("2023-09-04")], calendar=CALENDAR.iloc[1:])
    assert result["days"][0]["state"] == "unknown"
    assert result["coverage"]["calendarUnknownDates"] == ["2023-08-31"]


def test_successful_empty_history_has_unknown_coverage():
    result = normalize([])
    assert result["coverage"]["complete"] is False
    assert all(d["state"] == "unknown" for d in result["days"])


@pytest.mark.parametrize("change", [{"code": "sh.600221"}, {"date": "2023-09-05"}, {"date": "bad"}])
def test_wrong_identity_or_date_is_rejected(change):
    with pytest.raises(ValueError):
        normalize([{**row("2023-08-31"), **change}])


def test_digest_is_stable_and_binds_flags_and_symbol_window():
    rows = [row("2023-08-31", "0"), row("2023-09-01"), row("2023-09-04")]
    assert normalize(rows)["responseSha256"] == normalize(list(reversed(rows)))["responseSha256"]
    assert normalize(rows)["responseSha256"] != normalize([row("2023-08-31"), *rows[1:]])["responseSha256"]


class Result:
    error_code = "0"

    def __init__(self, frame):
        self.fields = list(frame.columns)
        self.rows = iter(frame.itertuples(index=False, name=None))

    def next(self):
        self.current = next(self.rows, None)
        return self.current is not None

    def get_row_data(self):
        return list(self.current)


def test_reader_reuses_adapter_session_and_explicit_query_fields():
    calls = []

    class Adapter:
        def _convert_stock_code(self, symbol):
            assert symbol == "300757.SZ"
            return CODE

        @contextmanager
        def _baostock_session(self):
            calls.append("login")
            try:
                yield self
            finally:
                calls.append("logout")

        def query_trade_dates(self, **kwargs):
            assert kwargs == {"start_date": "2023-08-31", "end_date": "2023-09-04"}
            return Result(CALENDAR)

        def query_history_k_data_plus(self, code, fields, **kwargs):
            assert code == CODE and fields == FIELDS
            assert kwargs["frequency"] == "d" and kwargs["adjustflag"] == "3"
            return Result(pd.DataFrame([row("2023-08-31", "0")]))

    result = read_baostock_daily_status(Adapter(), symbol="300757.SZ", start=START, end=END,
                                     clock=lambda: datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert calls == ["login", "logout"]
    assert result["days"][0]["state"] == "suspended"


def test_reader_distinguishes_provider_failure_from_empty_history():
    class Adapter:
        _convert_stock_code = lambda self, _: CODE

        @contextmanager
        def _baostock_session(self):
            yield self

        def query_trade_dates(self, **kwargs):
            result = Result(CALENDAR)
            result.error_code = "1"
            return result

    with pytest.raises(RuntimeError, match="查询失败"):
        read_baostock_daily_status(Adapter(), symbol="300757.SZ", start=START, end=END)
