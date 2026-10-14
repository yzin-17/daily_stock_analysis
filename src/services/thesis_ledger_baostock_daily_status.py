"""保留 BaoStock 逐日原始标记；缺失不补正常，价格不参与状态推导。"""

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from typing import Any

import pandas as pd

from src.services.thesis_ledger_tradability_provider import _result_frame

NORMALIZER_REVISION = "baostock-daily-flags-v1"
FIELDS = "date,code,tradestatus,isST"


def _flag(value: Any) -> str:
    if isinstance(value, bool):
        return "unknown"
    text = str(value).strip()
    return text if text in {"0", "1"} else "unknown"


def _date(value: Any) -> str | None:
    try:
        text = str(value)
        return text if date.fromisoformat(text).isoformat() == text else None
    except (TypeError, ValueError):
        return None


def normalize_baostock_daily_status(
    *, code: str, trading_calendar: pd.DataFrame, history: pd.DataFrame,
    start: date, end: date, observed_at: str,
) -> dict[str, Any]:
    """交易状态和粗 ST 分别保留覆盖，非交易日也不补造交易事实。"""
    if not re.fullmatch(r"(?:sh\.(?:60|68)\d{4}|sz\.(?:00|30)\d{4})", code):
        raise ValueError("BaoStock 日级状态仅支持明确沪深股票身份")
    if start > end or (end - start).days > 3660:
        raise ValueError("日级状态请求区间非法或超过 3661 个自然日")
    observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if observed.tzinfo is None or end > observed.astimezone(timezone.utc).date():
        raise ValueError("实际观察时钟必须带时区且不得早于请求日期")
    dates = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    requested = set(dates)
    calendar: dict[str, str] = {}
    for _, row in trading_calendar.iterrows():
        day = _date(row.get("calendar_date"))
        if day not in requested:
            raise ValueError("交易日历日期非法或超出区间")
        flag = _flag(row.get("is_trading_day"))
        if day in calendar:
            flag = "unknown"
        calendar[day] = flag
    sessions = [day for day in dates if calendar.get(day) == "1"]
    observations: dict[str, dict[str, str]] = {}
    for _, row in history.iterrows():
        day = _date(row.get("date"))
        if day not in requested or str(row.get("code", "")).strip() != code:
            raise ValueError("历史状态行日期或证券身份不符")
        flags = {name: _flag(row.get(name)) for name in ("tradestatus", "isST")}
        if day in observations:
            flags = {name: "unknown" for name in flags}
        observations[day] = flags
    days = []
    for day in dates:
        flags = observations.get(day, {}) if day in sessions else {}
        status = flags.get("tradestatus", "unknown")
        state = "unknown"
        if status == "0":
            state = "suspended"
        elif status == "1":
            state = "trading"
        days.append({"date": day, "state": state, "tradestatus": status,
                     "isST": flags.get("isST", "unknown")})
    calendar_unknown = [day for day in dates if calendar.get(day, "unknown") == "unknown"]
    status_unknown = [item["date"] for item in days if item["date"] in sessions and item["tradestatus"] == "unknown"]
    st_unknown = [item["date"] for item in days if item["date"] in sessions and item["isST"] == "unknown"]
    # 摘要绑定规范化查询响应；不称为供应商原始网络字节摘要。
    payload = {"code": code, "range": {"start": start.isoformat(), "end": end.isoformat()},
               "fields": FIELDS, "calendar": calendar, "observations": observations,
               "normalizerRevision": NORMALIZER_REVISION}
    digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"days": days, "expectedSessions": sessions,
            "coverage": {"complete": not calendar_unknown and not status_unknown,
                         "calendarUnknownDates": calendar_unknown, "statusUnknownDates": status_unknown,
                         "stUnknownDates": st_unknown},
            "responseSha256": digest, "observedAt": observed_at,
            "normalizerRevision": NORMALIZER_REVISION}


def read_baostock_daily_status(adapter: Any, *, symbol: str, start: date, end: date,
                              clock=lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    """复用现有适配器会话；查询失败与成功但无行分别表达。"""
    code = adapter._convert_stock_code(symbol)
    if not re.fullmatch(r"(?:sh\.(?:60|68)\d{4}|sz\.(?:00|30)\d{4})", code):
        raise ValueError("BaoStock 日级状态不支持本证券")
    if start > end or (end - start).days > 3660:
        raise ValueError("日级状态请求区间非法或超过 3661 个自然日")
    with adapter._baostock_session() as sdk:
        calendar_result = sdk.query_trade_dates(start_date=start.isoformat(), end_date=end.isoformat())
        calendar = _result_frame(calendar_result, "交易日历")
        if str(calendar_result.error_code) != "0":
            raise RuntimeError("交易日历分页读取失败")
        result = sdk.query_history_k_data_plus(code, FIELDS, start_date=start.isoformat(),
                                              end_date=end.isoformat(), frequency="d", adjustflag="3")
        history = _result_frame(result, "日级状态")
        if str(result.error_code) != "0":
            raise RuntimeError("日级状态分页读取失败")
    return normalize_baostock_daily_status(code=code, trading_calendar=calendar, history=history,
                                          start=start, end=end, observed_at=clock().isoformat())
