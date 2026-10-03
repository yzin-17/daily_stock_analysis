"""按请求身份和日期窗口选择指数日线原始行。"""

from datetime import datetime
import re
from typing import Optional

import pandas as pd


def _trade_date(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
        raise ValueError("index_daily 日期必须为 YYYYMMDD")
    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("index_daily 日期无效") from exc
    return value


def select_latest_index_daily_row(
    frame: Optional[pd.DataFrame], ts_code: str, start_date: str, end_date: str,
) -> Optional[pd.Series]:
    """完整校验响应后返回唯一最大日期的原始行；坏行拒绝整个指数。"""
    start, end = _trade_date(start_date), _trade_date(end_date)
    if start > end:
        raise ValueError("index_daily 请求窗口无效")
    if frame is None or frame.empty:
        return None
    if not frame.columns.is_unique or not {"ts_code", "trade_date"}.issubset(frame.columns):
        raise ValueError("index_daily 缺少唯一身份或日期列")

    dates = set()
    latest_date = ""
    latest_position = 0
    for position, (identity, raw_date) in enumerate(zip(frame["ts_code"], frame["trade_date"])):
        if not isinstance(identity, str) or identity != ts_code:
            raise ValueError("index_daily 返回身份与请求不符")
        trade_date = _trade_date(raw_date)
        if not start <= trade_date <= end:
            raise ValueError("index_daily 返回日期越过请求窗口")
        if trade_date in dates:
            raise ValueError("index_daily 返回重复日期")
        dates.add(trade_date)
        if trade_date > latest_date:
            latest_date, latest_position = trade_date, position
    return frame.iloc[latest_position]
