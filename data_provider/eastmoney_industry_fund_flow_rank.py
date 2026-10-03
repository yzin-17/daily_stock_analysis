"""Normalize AKShare's current EastMoney industry fund-flow ranking rows."""

import math
from numbers import Real
from typing import Any, Dict

import pandas as pd


_AMOUNT_COLUMNS = ("今日主力净流入-净额", "主力净流入-净额")


def select_industry_fund_flow_rankings(frame: pd.DataFrame, top_n: int) -> Dict[str, Any]:
    """Keep exact net amounts from the industry rank, never stock summary rows."""
    if (
        frame is None
        or frame.empty
        or not frame.columns.is_unique
        or "名称" not in frame.columns
        or "代码" in frame.columns
        or isinstance(top_n, bool)
        or not isinstance(top_n, int)
        or top_n < 1
    ):
        raise ValueError("invalid industry rank shape")
    amounts = [column for column in _AMOUNT_COLUMNS if column in frame.columns]
    if len(amounts) != 1:
        raise ValueError("ambiguous industry net amount")

    rows = []
    seen_names = set()
    for name, amount in zip(frame["名称"], frame[amounts[0]]):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("invalid industry name")
        name = name.strip()
        if name in seen_names:
            raise ValueError("duplicate industry name")
        seen_names.add(name)
        if amount is None or amount is pd.NA:
            continue
        if isinstance(amount, bool) or not isinstance(amount, Real):
            raise ValueError("invalid industry net amount")
        try:
            value = float(amount)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("invalid industry net amount") from exc
        if math.isnan(value):
            continue
        if not math.isfinite(value):
            raise ValueError("invalid industry net amount")
        rows.append({"name": name, "net_inflow": value})
    if not rows:
        raise ValueError("empty industry net amounts")
    return {
        "top": sorted(rows, key=lambda row: (-row["net_inflow"], row["name"]))[:top_n],
        "bottom": sorted(rows, key=lambda row: (row["net_inflow"], row["name"]))[:top_n],
    }
