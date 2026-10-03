"""ThesisLedger CN 股票历史可交易性来源事实。"""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping
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
    provider: str = "baostock",
) -> dict[str, Any]:
    return {
        "provider": provider,
        "providerRevision": provider_revision,
        "coverage": _coverage(start, end, complete),
        "tradable": tradable,
        "suspendedDates": suspended_dates or [],
        "ipoDate": ipo_date,
        "outDate": out_date,
        "availableAt": available_at,
        "reason": reason,
    }


def _cn_exchange_sessions(start: date, end: date) -> set[str] | None:
    """Return explicit XSHG sessions; no weekday or bar-gap inference."""
    try:
        from src.core import trading_calendar

        if not trading_calendar._XCALS_AVAILABLE:
            return None
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XSHG")
        first_session = getattr(calendar, "first_session", None)
        last_session = getattr(calendar, "last_session", None)
        if first_session is None or last_session is None:
            return None
        if start < first_session.date() or end > last_session.date():
            return None
        schedule = calendar.schedule.loc[start.isoformat() : end.isoformat()]
        return {index.date().isoformat() for index in schedule.index}
    except Exception as exc:  # optional dependency boundary
        logger.warning("Historical calendar provider unavailable: %s", exc)
        return None


def normalize_complete_bar_tradability(
    *,
    bars: pd.DataFrame,
    expected_sessions: set[str] | None,
    start: date,
    end: date,
    data_as_of: datetime,
    provider: str,
    upstream_source: str,
    provider_revision: str,
    route_index: int,
    policy_revision: int,
) -> dict[str, Any]:
    """Accept only a complete, positive-volume point-in-time bar set.

    A missing bar is never interpreted as a suspension.  Any missing,
    duplicate, malformed, future, or zero-volume session makes the evidence
    unavailable instead.
    """

    def unavailable(reason: str) -> dict[str, Any]:
        return _provider_result(
            start=start,
            end=end,
            provider=f"{provider}/{upstream_source}",
            provider_revision=provider_revision,
            available_at=data_as_of.astimezone(timezone.utc).isoformat(),
            complete=False,
            tradable=False,
            reason=reason,
        )

    if expected_sessions is None or not expected_sessions:
        return unavailable("Provider 交易日历当前不可用或请求区间没有交易会话")
    if (
        not provider.strip()
        or provider.lower() == "unknown"
        or not upstream_source.strip()
        or upstream_source.lower() == "unknown"
        or route_index < 0
        or policy_revision <= 0
    ):
        return unavailable("Provider BarSeries provenance 不完整")
    if not provider_revision.strip() or provider_revision == "unknown":
        return unavailable("Provider BarSeries revision 不可审计")
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        return unavailable("Provider 未返回历史 BarSeries")
    if "date" not in bars.columns or "volume" not in bars.columns:
        return unavailable("Provider BarSeries 缺少日期或成交量字段")

    by_day: dict[str, Any] = {}
    for _, row in bars.iterrows():
        day = _safe_date(row.get("date"))
        if day is None or not start <= day <= end:
            return unavailable("Provider BarSeries 日期无法解析或超出请求范围")
        day_text = day.isoformat()
        if day_text not in expected_sessions:
            return unavailable(f"Provider BarSeries 包含非交易会话: {day_text}")
        if day_text in by_day:
            return unavailable(f"Provider BarSeries 存在重复会话: {day_text}")
        try:
            volume = float(row.get("volume"))
        except (TypeError, ValueError):
            return unavailable(f"Provider BarSeries 成交量非法: {day_text}")
        if not math.isfinite(volume) or volume <= 0:
            return unavailable(f"Provider BarSeries 成交量未证明为正数: {day_text}")
        session_close = datetime.combine(
            day,
            time(15, 0),
            tzinfo=ZoneInfo("Asia/Shanghai"),
        ).astimezone(timezone.utc)
        if session_close > data_as_of.astimezone(timezone.utc):
            return unavailable(f"Provider BarSeries 可用时间晚于 dataAsOf: {day_text}")
        by_day[day_text] = session_close

    missing = sorted(expected_sessions.difference(by_day))
    if missing:
        return unavailable(
            "Provider BarSeries 覆盖不完整，缺失会话: " + ", ".join(missing[:10])
        )
    available_at = max(by_day.values()).isoformat()
    return _provider_result(
        start=start,
        end=end,
        provider=f"{provider}/{upstream_source}",
        provider_revision=(
            f"{provider_revision};upstreamSource={upstream_source};"
            f"routeIndex={route_index};policyRevision={policy_revision}"
        ),
        available_at=available_at,
        complete=True,
        tradable=True,
    )


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

    def unavailable(reason: str) -> dict[str, Any]:
        return _provider_result(
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


def _point_in_time_bar_tradability(
    symbol: str,
    start: date,
    end: date,
    data_as_of: datetime,
    *,
    instrument_type: str,
    route_key: Mapping[str, Any] | None,
    route_target: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Read the exact admitted V3 daily-bar route for tradability evidence."""
    if (
        instrument_type not in {"STOCK", "ETF"}
        or not isinstance(route_key, Mapping)
        or set(route_key) != {"kind", "market", "assetType", "capability", "timeframe", "adjustment"}
        or route_key.get("kind") != "bar"
        or route_key.get("market") != "CN"
        or route_key.get("assetType") != instrument_type
        or route_key.get("capability") != "DAILY_BAR"
        or route_key.get("timeframe") != "1d"
        or route_key.get("adjustment") not in {"none", "qfq", "hfq"}
        or not isinstance(route_target, Mapping)
    ):
        return _provider_result(
            start=start, end=end, provider="market-bars",
            provider_revision="market-bars-route-unavailable",
            available_at=data_as_of.astimezone(timezone.utc).isoformat(),
            complete=False, tradable=False,
            reason="缺少匹配标的的精确日线 RouteKey 或 RouteTarget",
        )
    try:
        from src.services.thesis_ledger_provider_runtime import (
            ThesisLedgerDataRequest, get_thesis_ledger_runtime,
        )
        request = ThesisLedgerDataRequest(
            "DAILY_BAR", symbol, timeframe="1d", start=start.isoformat(),
            end=end.isoformat(), limit=3650, instrument_type=instrument_type,
            adjustment=str(route_key["adjustment"]),
            parameters={"historical_tradability": True},
        )
        result = get_thesis_ledger_runtime().execute_market_bars_v3(
            request, route_key, route_target=route_target,
        )
        frame = result.value
        provenance = getattr(frame, "attrs", {})
        provider = str(getattr(result, "provider", "")).strip()
        upstream_source = str(provenance.get("upstream_source", "")).strip()
        if (
            getattr(result, "fallback_used", False)
            or provider != route_target.get("providerId")
            or upstream_source != route_target.get("upstreamSource")
            or getattr(result, "route_index", None) != route_target.get("routeIndex")
        ):
            raise ValueError("精确来源目标与实际 BarSeries 不一致")
        provider_revision = str(getattr(result, "provider_revision", "")).strip()
        route_index = int(getattr(result, "route_index", -1))
        policy = getattr(result, "effective_policy", {})
        policy_revision = int(policy.get("revision", 0)) if isinstance(policy, dict) else 0
        from src.services.thesis_ledger_daily_tradability import daily_tradability_evidence

        evidence = daily_tradability_evidence(
            bars=frame, symbol=symbol, instrument_type=instrument_type,
            start=start.isoformat(), end=end.isoformat(), data_as_of=data_as_of,
            route_key=route_key, route_target=route_target, provider_revision=provider_revision,
        )
        if policy_revision <= 0 or route_index < 0:
            raise ValueError("来源策略修订不可审计")
        output = _provider_result(
            start=start, end=end, provider=f"{provider}/{upstream_source}",
            provider_revision=provider_revision, available_at=evidence["barSource"]["observedAt"],
            complete=True, tradable=any(day["state"] == "observed-traded" for day in evidence["days"]),
        )
        output["historicalTradability"] = evidence
        return output
    except Exception as exc:  # provider boundary; retain fail-closed behavior.
        logger.warning("Routed BarSeries tradability provider unavailable: %s", exc)
        return _provider_result(
            start=start,
            end=end,
            provider="market-bars",
            provider_revision="market-bars-tradability-unavailable",
            available_at=data_as_of.astimezone(timezone.utc).isoformat(),
            complete=False,
            tradable=False,
            reason="Provider 历史 BarSeries 当前不可用",
        )


def real_cn_tradability(
    symbol: str,
    start: date,
    end: date,
    data_as_of: datetime,
    *,
    instrument_type: str = "STOCK",
    route_key: Mapping[str, Any] | None = None,
    route_target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Load CN historical tradability with an explicit, bounded provider path."""

    available = datetime.combine(
        end + timedelta(days=1),
        time.min,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    ).astimezone(timezone.utc)
    available_at = available.isoformat()
    revision = "market-bars-tradability-v3"
    if data_as_of.astimezone(timezone.utc) < available:
        return _provider_result(
            start=start,
            end=end,
            provider="market-bars",
            provider_revision=revision,
            available_at=available_at,
            complete=False,
            tradable=False,
            reason="dataAsOf 早于请求区间历史交易状态的保守可用时间",
        )

    # The caller must select one exact admitted source and adjustment. Missing
    # or malformed bars remain unavailable until daily-state production lands.
    return _point_in_time_bar_tradability(
        symbol, start, end, data_as_of, instrument_type=instrument_type,
        route_key=route_key, route_target=route_target,
    )
