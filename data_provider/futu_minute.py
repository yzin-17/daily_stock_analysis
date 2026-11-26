"""富途历史分钟投影：保留市场时区与完整分钟身份，不合成缺失值。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import math
from zoneinfo import ZoneInfo

import pandas as pd

from .exact_daily_contract import ExactDailyError
from .futu_history import read_futu_history


def _number(value, *, optional=False):
    if value is None or pd.isna(value):
        if optional:
            return None
        raise ValueError("missing price")
    if isinstance(value, bool):
        raise ValueError("boolean number")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (not optional and number == 0):
        raise ValueError("invalid number")
    return number


def normalize_futu_minute(raw, zone, *, observed_at=None, timeframe="1m"):
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ExactDailyError("invalid_request", "分钟观察时间必须包含时区")
    rows, seen = [], set()
    # A 股原生各分钟周期均按结束时刻标注，午休前最后一根均为 11:30。
    # 一分钟另含 09:30 竞价点；保留原标签，由消费方依据真实日历区分。
    convention = "interval-end" if zone == "Asia/Shanghai" else "interval-start"
    try:
        for item in raw.to_dict(orient="records"):
            stamp = datetime.strptime(str(item["time_key"]), "%Y-%m-%d %H:%M:%S")
            if stamp.second != 0:
                raise ValueError("not a minute boundary")
            stamp = stamp.replace(tzinfo=ZoneInfo(zone))
            if stamp in seen:
                raise ValueError("duplicate minute")
            seen.add(stamp)
            prices = {field: _number(item[field]) for field in ("open", "high", "low", "close")}
            if (prices["low"] > min(prices["open"], prices["close"])
                    or prices["high"] < max(prices["open"], prices["close"])
                    or prices["low"] > prices["high"]):
                raise ValueError("inconsistent OHLC")
            rows.append({"timestamp": stamp.isoformat(), **prices,
                         "volume": _number(item.get("volume"), optional=True),
                         "amount": _number(item.get("turnover"), optional=True),
                         "complete": stamp + timedelta(minutes=int(timeframe[:-1]) if convention == 'interval-start' else 0) <= observed_at})
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ExactDailyError("invalid_response", "富途分钟时间或数值无效") from None
    frame = pd.DataFrame(sorted(rows, key=lambda row: row["timestamp"]))
    for field in ("volume", "amount"):
        frame[field] = frame[field].astype(object)
        frame.loc[frame[field].isna(), field] = None
    frame.attrs.update({"upstream_source": "futu", "timeframe": timeframe, "timezone": zone,
                        "session": "regular", "adjustment": "none", "observedAt": observed_at.isoformat(),
                        "responseSha256": sha256(raw.to_json(date_format="iso", orient="split").encode()).hexdigest(),
                        "fieldAvailability": {field: bool(frame[field].notna().all()) for field in ("volume", "amount")},
                        "has_more_before": False, "timestampConvention": convention})
    return frame


def read_futu_minute(sdk, host, port, request):
    raw, zone = read_futu_history(sdk, host, port, request, timeframe=request.get("timeframe", "1m"))
    frame = normalize_futu_minute(raw, zone, timeframe=request.get("timeframe", "1m"))
    frame.attrs["pagination"] = raw.attrs["pagination"]
    return frame
