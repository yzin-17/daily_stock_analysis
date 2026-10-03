"""HiThink 报价公开量额单位，仅接受已核对的来源合同。"""

from __future__ import annotations

from typing import Any, Mapping

from src.services.thesis_ledger_hithink_quote import (
    HITHINK_ETF_SNAPSHOT_SOURCE, HITHINK_STOCK_SNAPSHOT_SOURCE,
)


_UNITS = {
    HITHINK_STOCK_SNAPSHOT_SOURCE: {
        "price_currency": "CNY", "volume": "share", "turnover_currency": "CNY",
    },
    HITHINK_ETF_SNAPSHOT_SOURCE: {
        "price_currency": "CNY", "volume": "unknown", "turnover_currency": "unknown",
    },
}


def hithink_quote_public_units(source: str | None, units: Any) -> dict[str, str] | None:
    """ETF 量额不得借用股票单位；缺失或漂移时拒绝公开报价。"""
    expected = _UNITS.get(source)
    if expected is None or not isinstance(units, Mapping) or dict(units) != expected:
        return None
    return {
        "priceCurrency": expected["price_currency"],
        "volume": expected["volume"],
        "turnoverCurrency": expected["turnover_currency"],
    }
