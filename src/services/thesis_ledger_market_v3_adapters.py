"""DSA Data V3 市场日线的精确适配器能力事实。"""

from __future__ import annotations

from typing import Any, Iterator
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import DailyBinding
from data_provider.thesis_ledger_sources.hithink import (
    STOCK_SOURCE as HITHINK_STOCK_HISTORY_SOURCE,
    STOCK_SOURCE_REVISION as HITHINK_STOCK_SOURCE_CONTRACT_REVISION,
    STOCK_PROTOCOL as HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1,
)




# Only source-pinned adapters used by ``execute_market_bars_v3`` belong here.
# Generic provider manifests describe broader native support and are not proof
# that a ThesisLedger V3 route can execute one exact source/type combination.
_ROUTE_ADJUSTMENTS: dict[tuple[str, str, str], tuple[str, ...]] = {
}

# 需凭据的适配器单独登记。HiThink ETF 基础价格只检查配置，扩展能力继续独立准入。
_GATED_ROUTE_ADJUSTMENTS: dict[tuple[str, str, str], tuple[str, ...]] = {
}


def market_v3_bar_adapter_reason(
    key: dict[str, Any], target: dict[str, Any]
) -> str | None:
    """Return a fail-closed reason unless this exact bar route has an adapter."""
    if isinstance(CAPABILITY_SOURCES.resolve(key, target), DailyBinding):
        return None
    if key.get("kind") == "bar" and any(
        candidate[0].get("market") == key.get("market")
        and candidate[0].get("assetType") == key.get("assetType")
        and candidate[0].get("capability") == key.get("capability")
        and candidate[0].get("timeframe") == key.get("timeframe")
        and candidate[1] == {name: target.get(name) for name in ("providerId", "upstreamSource")}
        for candidate in CAPABILITY_SOURCES.routes("DAILY_BAR")
    ):
        return "unsupported_adjustment"
    if (
        key.get("kind") != "bar"
        or key.get("market") != "CN"
        or key.get("capability") != "DAILY_BAR"
        or key.get("timeframe") != "1d"
    ):
        return "not_adapted"

    identity = (
        str(key.get("assetType") or ""),
        str(target.get("providerId") or "").strip().lower(),
        str(target.get("upstreamSource") or "").strip().lower(),
    )
    supported_adjustments = _ROUTE_ADJUSTMENTS.get(identity)
    if supported_adjustments is None:
        supported_adjustments = _GATED_ROUTE_ADJUSTMENTS.get(identity)
    if supported_adjustments is None:
        return "not_adapted"
    if key.get("adjustment") not in supported_adjustments:
        return "unsupported_adjustment"
    return None


def iter_market_v3_bar_adapters() -> Iterator[tuple[dict[str, Any], dict[str, str]]]:
    """Yield the established deterministic exact route inventory."""
    for key, target, binding in CAPABILITY_SOURCES.routes("DAILY_BAR"):
        if not binding.gated:
            yield key, target
    for (asset_type, provider_id, upstream_source), adjustments in sorted(_ROUTE_ADJUSTMENTS.items()):
        for adjustment in adjustments:
            yield (
                {
                    "kind": "bar",
                    "market": "CN",
                    "assetType": asset_type,
                    "capability": "DAILY_BAR",
                    "timeframe": "1d",
                    "adjustment": adjustment,
                },
                {"providerId": provider_id, "upstreamSource": upstream_source},
            )


def basic_market_price_route(key, target) -> bool:
    """普通日线按真实精确适配与配置使用，不依赖人工窗口准入。"""
    return market_v3_bar_adapter_reason(key, target) is None


def iter_market_v3_gated_bar_adapters() -> Iterator[tuple[dict[str, Any], dict[str, str]]]:
    """按注册来源补充精确库存；普通能力是否就绪由配置控制。"""
    for key, target, binding in CAPABILITY_SOURCES.routes("DAILY_BAR"):
        if binding.gated:
            yield key, target
    for (asset_type, provider_id, upstream_source), adjustments in sorted(
        _GATED_ROUTE_ADJUSTMENTS.items()
    ):
        for adjustment in adjustments:
            yield (
                {
                    "kind": "bar",
                    "market": "CN",
                    "assetType": asset_type,
                    "capability": "DAILY_BAR",
                    "timeframe": "1d",
                    "adjustment": adjustment,
                },
                {"providerId": provider_id, "upstreamSource": upstream_source},
            )


def market_v3_bar_provider_ids():
    return frozenset(target["providerId"] for _, target in (
        *iter_market_v3_bar_adapters(), *iter_market_v3_gated_bar_adapters(),
    ))


MARKET_DATA_V3_SUPPORTED_PROVIDER_IDS = market_v3_bar_provider_ids()
