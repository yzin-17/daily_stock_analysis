"""RQData 场内 ETF 原生日线的离线请求与响应合同，不执行 SDK 调用。"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

import pandas as pd


FIELDS = ("open", "high", "low", "close", "volume", "total_turnover")
_EXCHANGE = {"SZ": "XSHE", "SH": "XSHG"}


def _scope(symbol, order_book_id, start, end, maximum_rows):
    match = re.fullmatch(r"([0-9]{6})\.(SZ|SH)", symbol) if isinstance(symbol, str) else None
    if match is None or order_book_id != f"{match[1]}.{_EXCHANGE[match[2]]}":
        raise ValueError("rqdata_etf_daily_identity_invalid")
    if not isinstance(start, str) or not isinstance(end, str):
        raise ValueError("rqdata_etf_daily_window_invalid")
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise ValueError("rqdata_etf_daily_window_invalid") from None
    if (first.isoformat() != start or last.isoformat() != end or first > last
            or (last - first).days >= 366):
        raise ValueError("rqdata_etf_daily_window_invalid")
    if type(maximum_rows) is not int or not 1 <= maximum_rows <= 366:
        raise ValueError("rqdata_etf_daily_budget_invalid")
    return first, last


def rqdata_etf_daily_request(symbol, order_book_id, start, end, *, maximum_rows=366):
    """调用方仍须以当前独立身份原文验证 order_book_id。"""
    _scope(symbol, order_book_id, start, end, maximum_rows)
    return {
        "order_book_ids": order_book_id, "start_date": start, "end_date": end,
        "frequency": "1d", "fields": list(FIELDS), "adjust_type": "none",
        "skip_suspended": False, "expect_df": True, "market": "cn",
    }


def _day(value):
    if isinstance(value, pd.Timestamp):
        if value.tzinfo is not None or value != value.normalize():
            raise ValueError("rqdata_etf_daily_date_invalid")
        return value.date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    raise ValueError("rqdata_etf_daily_date_invalid")


def _number(value, *, positive):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value))
        if not number.is_finite() or (number <= 0 if positive else number < 0):
            raise ValueError()
    except (InvalidOperation, ValueError):
        raise ValueError("rqdata_etf_daily_number_invalid") from None
    return number


def normalize_rqdata_etf_daily(
    frame, symbol, order_book_id, start, end, *, maximum_rows=366,
):
    """只证明本次响应形状；不证明 SDK 权限、原生单位或历史完整性。"""
    first, last = _scope(symbol, order_book_id, start, end, maximum_rows)
    if (not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any()
            or set(frame.columns) != set(FIELDS) or len(frame) > maximum_rows
            or not isinstance(frame.index, pd.MultiIndex)
            or list(frame.index.names) != ["order_book_id", "date"]):
        raise ValueError("rqdata_etf_daily_response_invalid")
    rows, seen = [], set()
    for index, values in zip(frame.index, frame.to_dict("records"), strict=True):
        source_id, raw_day = index
        day = _day(raw_day)
        if source_id != order_book_id or not first <= day <= last or day in seen:
            raise ValueError("rqdata_etf_daily_scope_invalid")
        seen.add(day)
        numbers = {field: _number(values[field], positive=field in FIELDS[:4]) for field in FIELDS}
        if not (
            numbers["low"] <= min(numbers["open"], numbers["close"])
            <= max(numbers["open"], numbers["close"]) <= numbers["high"]
        ):
            raise ValueError("rqdata_etf_daily_ohlc_invalid")
        rows.append({"date": day.isoformat(), **{field: str(numbers[field]) for field in FIELDS}})
    rows.sort(key=lambda row: row["date"])
    request = rqdata_etf_daily_request(
        symbol, order_book_id, start, end, maximum_rows=maximum_rows,
    )
    payload = {"request": request, "rows": rows}
    fingerprint = sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    return {
        "symbol": symbol, "queryOrderBookId": order_book_id, "rows": rows,
        "request": request,
        "validation": {
            "rowsValidated": len(rows),
            "localMaximumRows": maximum_rows, "contentFingerprint": fingerprint,
            "nativeVolumeUnit": "unknown", "nativeAmountUnit": "unknown",
            "tradingCalendarVerified": False, "historicalCoverageComplete": False,
            "upstreamDataRevision": None,
        },
    }
