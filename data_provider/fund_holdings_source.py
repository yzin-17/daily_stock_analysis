"""基金持仓来源年份与观测证据。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from src.services.thesis_ledger_holding_rows import (
    holding_period,
    validate_holding_rows,
)

_SOURCE_ENDPOINT = "akshare/eastmoney:fund_portfolio_hold_em"


def read_recent_fund_holdings(
    fund_code: str,
    *,
    current_year: int,
    fetch_year: Callable[[str, str], Any],
    observed_at: str | None = None,
) -> Any:
    """只在空结果时回退上一年；非空错年或坏合同立即拒绝。"""
    if (
        not isinstance(fund_code, str)
        or not fund_code.strip()
        or type(current_year) is not int
        or current_year < 2000
    ):
        raise ValueError("invalid_fund_holdings_request")

    observation = observed_at or datetime.now(timezone.utc).isoformat()
    last_frame = None
    for year in (current_year, current_year - 1):
        frame = fetch_year(fund_code.strip(), str(year))
        last_frame = frame
        if frame is None or getattr(frame, "empty", True):
            continue

        validate_holding_rows(frame)
        periods = {
            holding_period(row.get("季度"))
            for _, row in frame.iterrows()
        }
        if any(period_year != year for period_year, _ in periods):
            raise ValueError("fund_holdings_query_year_mismatch")

        attrs = getattr(frame, "attrs", None)
        if isinstance(attrs, dict):
            attrs["sourceEndpoint"] = _SOURCE_ENDPOINT
            attrs["sourceQueryYear"] = year
            attrs["sourceObservedAt"] = observation
        return frame

    return last_frame
