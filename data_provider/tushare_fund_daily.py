"""Tushare ETF 原始日线的有界日期分段读取与源响应校验。"""

from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Callable

import pandas as pd
from .tushare_history_window import fund_history_windows, parse_tushare_date as _date

FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
PAGINATION_PROTOCOL = "tushare-fund-daily-date-partitions-366-v1"
REQUIRED = {"ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"}


def _validate(frame: pd.DataFrame, symbol: str, start: date, end: date) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or not REQUIRED.issubset(frame.columns):
        raise ValueError("fund_daily missing required columns")
    if frame.columns.duplicated().any() or len(frame) > (end - start).days + 1:
        raise ValueError("fund_daily invalid row count or columns")
    result = frame.copy()
    seen = set()
    for row in result.to_dict("records"):
        day = _date(row["trade_date"])
        if row["ts_code"] != symbol or not start <= day <= end or day in seen:
            raise ValueError("fund_daily symbol/date scope mismatch or duplicate")
        seen.add(day)
        numbers = {}
        for field in ("open", "high", "low", "close", "vol", "amount"):
            try:
                value = row[field]
                if isinstance(value, bool):
                    raise ValueError()
                number = Decimal(str(value))
                if not number.is_finite() or number < 0 or (field in ("open", "high", "low", "close") and number == 0):
                    raise ValueError()
                numbers[field] = number
            except (ValueError, InvalidOperation):
                raise ValueError("fund_daily invalid numeric value") from None
        if not (
            numbers["low"] <= min(numbers["open"], numbers["close"])
            <= max(numbers["open"], numbers["close"]) <= numbers["high"]
        ):
            raise ValueError("fund_daily invalid OHLC")
    for field in ("open", "high", "low", "close", "vol", "amount"):
        result[field] = pd.to_numeric(result[field], errors="raise")
    return result.sort_values("trade_date").reset_index(drop=True)


def fetch_tushare_fund_daily(
    api, symbol: str, start: str, end: str, *, before_call: Callable[[], None], max_calls: int = 32,
) -> pd.DataFrame:
    windows = fund_history_windows(symbol, start, end, max_calls)
    frames, evidence = [], []
    for cursor, stop in windows:
        before_call()
        frame = _validate(api.fund_daily(
            ts_code=symbol, start_date=cursor.strftime("%Y%m%d"), end_date=stop.strftime("%Y%m%d"), fields=FIELDS,
        ), symbol, cursor, stop)
        digest = sha256(frame.to_json(orient="split", index=False, double_precision=15).encode()).hexdigest()
        evidence.append({"start": cursor.isoformat(), "end": stop.isoformat(), "rows": len(frame), "sha256": digest})
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    result.attrs["fundDailyRetrieval"] = {
        "endpoint": "fund_daily", "adjustment": "none", "partitions": evidence,
        "partitionComplete": True, "tradingCalendarVerified": False,
        "nativeVolumeUnit": "lot-100-units", "nativeAmountUnit": "CNY-1000",
        "revision": sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    return result
