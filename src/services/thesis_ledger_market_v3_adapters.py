"""DSA Data V3 市场日线的精确适配器能力事实。"""

from __future__ import annotations

from typing import Any, Iterator


HITHINK_STOCK_HISTORY_SOURCE = "hithink-financial-api"
# This is the frozen local endpoint/request contract, not an upstream data or
# adjustment-algorithm revision.
HITHINK_STOCK_SOURCE_CONTRACT_REVISION = "dsa-hithink-stock-request-contract-v1"
HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1 = "hithink-stock-single-response-local-v1"


# Only source-pinned adapters used by ``execute_market_bars_v3`` belong here.
# Generic provider manifests describe broader native support and are not proof
# that a ThesisLedger V3 route can execute one exact source/type combination.
_ROUTE_ADJUSTMENTS: dict[tuple[str, str, str], tuple[str, ...]] = {
    ("STOCK", "akshare", "eastmoney"): ("none", "qfq", "hfq"),
    ("STOCK", "akshare", "sina"): ("none", "qfq", "hfq"),
    ("STOCK", "akshare", "tencent"): ("none", "qfq", "hfq"),
    ("ETF", "akshare", "eastmoney"): ("none", "qfq", "hfq"),
    ("STOCK", "tencent", "tencent"): ("none", "qfq"),
    ("ETF", "tencent", "tencent"): ("none", "qfq", "hfq"),
}

# 需凭据的适配器单独登记。HiThink ETF 基础价格只检查配置，扩展能力继续独立准入。
_GATED_ROUTE_ADJUSTMENTS: dict[tuple[str, str, str], tuple[str, ...]] = {
    ("ETF", "tushare", "tushare"): ("none",),
    ("ETF", "hithink", "fund-market-historical"): ("qfq",),
    ("STOCK", "hithink", HITHINK_STOCK_HISTORY_SOURCE): ("none", "qfq", "hfq"),
}


def market_v3_bar_adapter_reason(
    key: dict[str, Any], target: dict[str, Any]
) -> str | None:
    """Return a fail-closed reason unless this exact bar route has an adapter."""
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
    """已实现的共同 ETF 价格能力按配置启用，不要求人工逐窗准入。"""
    return (
        key.get("assetType") == "ETF"
        and (target.get("providerId"), target.get("upstreamSource")) in {
            ("hithink", "fund-market-historical"), ("tencent", "tencent"),
        }
        and market_v3_bar_adapter_reason(key, target) is None
    )


def iter_market_v3_gated_bar_adapters() -> Iterator[tuple[dict[str, Any], dict[str, str]]]:
    """Yield exact adapters staged behind source-evidence and admission gates."""
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
