"""两种结构的隔离来源；此模块不能被生产注册入口导入。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import CapabilityContract, ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_order_book_contract import (
    OrderBook, OrderBookRequest, validate_book, validate_book_binding, correlate_bound_book,
)


@dataclass(frozen=True)
class Input:
    raw: dict
    permitted: bool = True
    depth_policy_allowed: bool = False


def envelope(query, context, provider, source, limit):
    if not context.adapter.permitted:
        raise CapabilityContractError("permission_denied", "隔离来源未授予盘口权限")
    degraded = limit is not None and query.depth > limit
    if degraded and not (query.allowDepthDegradation and context.adapter.depth_policy_allowed):
        raise CapabilityContractError("unsupported_depth", "来源深度不足且未获双方降级许可")
    return {"contractVersion": 1, "capability": "ORDER_BOOK", "symbol": query.symbol, "market": query.market,
            "venue": "XHKG", "coverage": "venue", "priceCurrency": "HKD", "requestedDepth": query.depth,
            "sourceDepthLimit": limit, "depthDegraded": degraded,
            "receivedAt": "2026-10-09T08:00:01+00:00", "target": {"providerId": provider, "upstreamSource": source},
            "adapterRevision": "fixture-book-v1", "dataNature": "realtime", "state": "valid", "continuity": "unknown"}


def read_a(query, context):
    raw = context.adapter.raw
    if raw["code"] != "HK." + query.symbol.removesuffix(".HK") or query.market != "HK":
        raise CapabilityContractError("invalid_response", "来源 A 标的身份不匹配")
    value = envelope(query, context, "fixture-a", "venue-a", raw["maxLevels"])
    lot_size = raw.get("lotSize")
    known = type(lot_size) is int and lot_size > 0
    def levels(name, descending):
        rows = [{"price": price, "quantity": str(Decimal(size) * lot_size) if known else size, "orderCount": None}
                for price, size in raw[name]]
        return sorted(rows, key=lambda row: Decimal(row["price"]), reverse=descending)
    value.update(bids=levels("buy", True), asks=levels("sell", False), quantityUnit="share" if known else "unknown",
                 sequence=raw.get("seq"), sourceEventTime=datetime.fromtimestamp(raw["timestamp_ms"] / 1000, timezone.utc).isoformat()
                 if raw.get("timestamp_ms") is not None else None)
    return OrderBook.model_validate(value)


def read_b(query, context):
    raw = context.adapter.raw
    if raw["instrument"] != {"ticker": query.symbol.removesuffix(".HK"), "exchange": "XHKG"} or query.market != "HK":
        raise CapabilityContractError("invalid_response", "来源 B 标的身份不匹配")
    value = envelope(query, context, "fixture-b", "venue-b", raw["depth_limit"])
    rows = raw["levels"]
    if any(row["direction"] not in {"B", "S"} for row in rows):
        raise CapabilityContractError("invalid_response", "来源 B 方向非法")
    def levels(direction, descending):
        return sorted(({"price": row["px"], "quantity": row["size"], "orderCount": row.get("orders")}
                       for row in rows if row["direction"] == direction),
                      key=lambda row: Decimal(row["price"]), reverse=descending)
    value.update(bids=levels("B", True), asks=levels("S", False), quantityUnit="share" if raw["unit"] == "shares" else "unknown",
                 sequence=raw.get("change_id"), sourceEventTime=raw.get("observed_at"))
    return OrderBook.model_validate(value)


CONTRACT = CapabilityContract("ORDER_BOOK", OrderBookRequest, validate_book,
                              validate_binding=validate_book_binding, correlate_result=correlate_bound_book)


def source(provider, upstream, read):
    return SourceDefinition(provider, lambda: {"providerId": provider}, lambda context: context.adapter, (
        QueryBinding(CONTRACT, ExactKey("HK", "STOCK", "ORDER_BOOK"), upstream, "fixture-book-v1", "fixture-source-v1", read),
    ))


SOURCES = (source("fixture-a", "venue-a", read_a), source("fixture-b", "venue-b", read_b))
RAW_A = {"code": "HK.00700", "maxLevels": 5, "buy": [["302.10", "20"], ["302.20", "10"]],
         "sell": [["302.30", "30"]], "lotSize": 100, "timestamp_ms": 1791532800000, "seq": 1}
RAW_B = {"instrument": {"ticker": "00700", "exchange": "XHKG"}, "depth_limit": 5, "unit": "shares",
         "levels": [{"direction": "S", "px": "302.30", "size": "3000"},
                    {"direction": "B", "px": "302.10", "size": "2000"},
                    {"direction": "B", "px": "302.20", "size": "1000"}],
         "observed_at": "2026-10-09T08:00:00+00:00", "change_id": 1}
