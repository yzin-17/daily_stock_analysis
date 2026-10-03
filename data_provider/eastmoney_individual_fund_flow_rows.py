"""Select a daily EastMoney individual fund-flow observation from AKShare rows."""

import math
import re
from datetime import date, datetime
from numbers import Real
from typing import Any, Dict

import pandas as pd


def _daily_date(value: object) -> date:
    if value is pd.NaT:
        raise ValueError("invalid daily date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("invalid daily date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid daily date") from exc


def select_latest_individual_fund_flow(frame: pd.DataFrame) -> Dict[str, Any]:
    """Reject ambiguous tables and retain only the latest day's exact net amount."""
    required = {"日期", "主力净流入-净额"}
    if frame is None or frame.empty or not frame.columns.is_unique or not required.issubset(frame.columns):
        raise ValueError("invalid daily columns")

    seen = set()
    latest_date = None
    latest_position = None
    for position, value in enumerate(frame["日期"]):
        trade_date = _daily_date(value)
        if trade_date in seen:
            raise ValueError("duplicate daily date")
        seen.add(trade_date)
        if latest_date is None or trade_date > latest_date:
            latest_date, latest_position = trade_date, position

    amount = frame.iloc[latest_position]["主力净流入-净额"]
    if isinstance(amount, bool) or not isinstance(amount, Real):
        raise ValueError("invalid latest net amount")
    try:
        net_amount = float(amount)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("invalid latest net amount") from exc
    if not math.isfinite(net_amount):
        raise ValueError("invalid latest net amount")
    return {
        "main_net_inflow": net_amount,
        "inflow_5d": None,
        "inflow_10d": None,
        "trade_date": latest_date.isoformat(),
        "source_available_at": None,
        "amount_unit": "unknown",
    }
