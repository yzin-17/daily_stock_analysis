"""来源精确日线的响应规范化；保留原始字段缺失，不合成量额。"""

from datetime import date
from hashlib import sha256

import pandas as pd


class ExactDailyError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def normalize_exact_daily(raw, *, start, end, source, columns=None, date_unit=None, timezone="Asia/Shanghai"):
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        raise ExactDailyError("not_covered", "来源未返回请求的日线数据")
    fingerprint = sha256(raw.to_json(date_format="iso", orient="split").encode()).hexdigest()
    frame = raw.rename(columns=columns or {}).copy()
    if "date" not in frame.columns:
        raise ExactDailyError("invalid_response", "来源日线缺少日期")
    try:
        if date_unit is not None:
            frame["date"] = pd.to_datetime(frame["date"], unit=date_unit, utc=True).dt.tz_convert(timezone).dt.strftime("%Y-%m-%d")
        else:
            frame["date"] = frame["date"].map(lambda value: date.fromisoformat(str(value).strip()[:10]).isoformat())
        frame = frame[(frame["date"] >= start) & (frame["date"] <= end)].copy()
        if frame.empty:
            raise ExactDailyError("not_covered", "请求窗口没有来源日线")
        if frame["date"].duplicated().any():
            raise ValueError("duplicate date")
        for field in ("open", "high", "low", "close"):
            frame[field] = pd.to_numeric(frame[field], errors="raise")
            if frame[field].isna().any():
                raise ValueError("missing price")
        for field in ("volume", "amount"):
            if field not in frame.columns:
                frame[field] = None
            frame[field] = pd.to_numeric(frame[field], errors="raise").astype(object)
            frame.loc[frame[field].isna(), field] = None
    except (KeyError, TypeError, ValueError):
        raise ExactDailyError("invalid_response", "来源日线字段或日期无效") from None
    frame = frame.sort_values("date").reset_index(drop=True)
    frame.attrs.update({"upstream_source": source, "responseSha256": fingerprint,
                        "fieldAvailability": {field: frame[field].notna().all() for field in ("volume", "amount")},
                        "has_more_before": False})
    return frame
