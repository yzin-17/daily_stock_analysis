"""ThesisLedger V2 CN 股票历史可交易性事实 Provider。"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)


def _coverage(start: date, end: date, complete: bool) -> dict[str, Any]:
    return {"start": start.isoformat(), "end": end.isoformat(), "complete": complete}


def _provider_result(
    *,
    start: date,
    end: date,
    provider_revision: str,
    available_at: str,
    complete: bool,
    tradable: bool,
    suspended_dates: list[str] | None = None,
    ipo_date: str | None = None,
    out_date: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": "baostock",
        "providerRevision": provider_revision,
        "coverage": _coverage(start, end, complete),
        "tradable": tradable,
        "suspendedDates": suspended_dates or [],
        "ipoDate": ipo_date,
        "outDate": out_date,
        "availableAt": available_at,
        "reason": reason,
    }


def _safe_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _result_frame(result: Any, label: str) -> pd.DataFrame:
    if str(getattr(result, "error_code", "")) != "0":
        raise RuntimeError(f"{label} Provider 查询失败")
    rows: list[list[Any]] = []
    while result.next():
        rows.append(result.get_row_data())
    fields = list(getattr(result, "fields", []) or [])
    return pd.DataFrame(rows, columns=fields)


def normalize_baostock_tradability(
    *,
    basic: pd.DataFrame,
    trading_calendar: pd.DataFrame,
    history: pd.DataFrame,
    start: date,
    end: date,
    provider_revision: str,
    available_at: str,
) -> dict[str, Any]:
    """Normalize BaoStock listing/suspension evidence without inferring missing rows."""

    unavailable = lambda reason: _provider_result(
        start=start,
        end=end,
        provider_revision=provider_revision,
        available_at=available_at,
        complete=False,
        tradable=False,
        reason=reason,
    )

    if len(basic.index) != 1:
        return unavailable("Provider 未返回唯一证券基础信息")

    row = basic.iloc[0]
    if str(row.get("type") or "").strip() != "1":
        return unavailable("Provider 返回的证券类型不是股票")

    ipo_date = _safe_date(row.get("ipoDate"))
    if ipo_date is None:
        return unavailable("Provider 缺少可解析的上市日期")

    out_raw = str(row.get("outDate") or "").strip()
    out_date = _safe_date(out_raw)
    if out_raw and out_raw.lower() not in {"nan", "nat", "none"} and out_date is None:
        return unavailable("Provider 退市日期无法解析")

    base_kwargs = {
        "start": start,
        "end": end,
        "provider_revision": provider_revision,
        "available_at": available_at,
        "ipo_date": ipo_date.isoformat(),
        "out_date": out_date.isoformat() if out_date else None,
    }
    if start < ipo_date:
        return _provider_result(
            **base_kwargs,
            complete=True,
            tradable=False,
            reason=f"请求区间包含上市前日期，上市日为 {ipo_date.isoformat()}",
        )
    if out_date is not None and end > out_date:
        return _provider_result(
            **base_kwargs,
            complete=True,
            tradable=False,
            reason=f"请求区间包含退市后日期，退市日为 {out_date.isoformat()}",
        )

    expected_sessions: set[str] = set()
    for _, calendar_row in trading_calendar.iterrows():
        if str(calendar_row.get("is_trading_day") or "").strip() != "1":
            continue
        day = _safe_date(calendar_row.get("calendar_date"))
        if day is None:
            return unavailable("Provider 交易日历包含无法解析的日期")
        if start <= day <= end:
            expected_sessions.add(day.isoformat())

    statuses: dict[str, str] = {}
    invalid_dates: set[str] = set()
    for _, history_row in history.iterrows():
        day = _safe_date(history_row.get("date"))
        if day is None:
            continue
        day_text = day.isoformat()
        if day_text not in expected_sessions:
            continue
        status = str(history_row.get("tradestatus") or "").strip()
        if status not in {"0", "1"}:
            invalid_dates.add(day_text)
            continue
        previous = statuses.get(day_text)
        if previous is not None and previous != status:
            invalid_dates.add(day_text)
            continue
        statuses[day_text] = status

    missing_dates = sorted(expected_sessions.difference(statuses))
    if missing_dates or invalid_dates:
        details: list[str] = []
        if missing_dates:
            details.append(f"缺失会话 {', '.join(missing_dates[:10])}")
        if invalid_dates:
            details.append(f"非法状态 {', '.join(sorted(invalid_dates)[:10])}")
        return _provider_result(
            **base_kwargs,
            complete=False,
            tradable=False,
            reason="Provider 历史交易状态覆盖不完整: " + "; ".join(details),
        )

    suspended_dates = sorted(day for day, status in statuses.items() if status == "0")
    return _provider_result(
        **base_kwargs,
        complete=True,
        tradable=not suspended_dates,
        suspended_dates=suspended_dates,
        reason=(
            None
            if not suspended_dates
            else f"请求区间包含停牌交易日: {', '.join(suspended_dates[:10])}"
        ),
    )


def real_cn_tradability(
    symbol: str,
    start: date,
    end: date,
    data_as_of: datetime,
) -> dict[str, Any]:
    """Load CN listing/suspension history from BaoStock with conservative knowledge time."""

    available = datetime.combine(
        end + timedelta(days=1),
        time.min,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    ).astimezone(timezone.utc)
    available_at = available.isoformat()
    revision = "baostock-tradestatus-v1"
    if data_as_of.astimezone(timezone.utc) < available:
        return _provider_result(
            start=start,
            end=end,
            provider_revision=revision,
            available_at=available_at,
            complete=False,
            tradable=False,
            reason="dataAsOf 早于请求区间历史交易状态的保守可用时间",
        )

    try:
        from data_provider.baostock_fetcher import BaostockFetcher

        fetcher = BaostockFetcher()
        bs_code = fetcher._convert_stock_code(symbol)
        with fetcher._baostock_session() as bs:
            revision = f"baostock-{str(getattr(bs, '__version__', 'unknown')).strip() or 'unknown'}-tradestatus-v1"
            basic = _result_frame(bs.query_stock_basic(code=bs_code), "证券基础信息")
            trading_calendar = _result_frame(
                bs.query_trade_dates(start_date=start.isoformat(), end_date=end.isoformat()),
                "交易日历",
            )
            history = _result_frame(
                bs.query_history_k_data_plus(
                    code=bs_code,
                    fields="date,tradestatus",
                    start_date=start.isoformat(),
                    end_date=end.isoformat(),
                    frequency="d",
                    adjustflag="3",
                ),
                "历史交易状态",
            )
        return normalize_baostock_tradability(
            basic=basic,
            trading_calendar=trading_calendar,
            history=history,
            start=start,
            end=end,
            provider_revision=revision,
            available_at=available_at,
        )
    except Exception as exc:  # provider boundary
        logger.warning("V2 historical tradability provider unavailable: %s", exc)
        return _provider_result(
            start=start,
            end=end,
            provider_revision="baostock-tradestatus-unavailable",
            available_at=available_at,
            complete=False,
            tradable=False,
            reason="Provider 历史上市/停牌状态当前不可用",
        )
