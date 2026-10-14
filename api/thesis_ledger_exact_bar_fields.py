"""普通价格图的真实字段与市场日期；不生成交易日历证明。"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo
import pandas as pd


def bar_fields(row, number):
    fields = {key: number(row.get(key), key) for key in ("open", "high", "low", "close")}
    for key in ("volume", "amount"):
        value = row.get(key)
        fields[key] = None if value is None or pd.isna(value) else number(value, key)
    if (any(value < 0 for value in fields.values() if value is not None)
            or fields["high"] < max(fields["open"], fields["close"], fields["low"])
            or fields["low"] > min(fields["open"], fields["close"], fields["high"])):
        raise ValueError("invalid OHLC or metrics")
    return fields


def chart_bar_point(row, market, fetched_at, number):
    timezone = ZoneInfo({"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}[market])
    raw = row.get("date")
    if isinstance(raw, datetime) and raw.tzinfo:
        trading_date = raw.astimezone(timezone).date()
    else:
        trading_date = date.fromisoformat(str(raw)[:10])
    timestamp = datetime.combine(trading_date, time.min, timezone).isoformat()
    # 这是来源观测日线的当地收盘时钟，不断言节假日、半日市或完整交易窗口。
    closing = datetime.combine(trading_date, time(15 if market == "CN" else 16), timezone)
    observed = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    complete = observed >= closing
    return {"timestamp": timestamp, **bar_fields(row, number),
            "completionStatus": "complete" if complete else "incomplete", "availableAt": fetched_at}
