"""Select one current Control policy route for non-bar ThesisLedger data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


# Each entry names the source actually selected by the quote adapter. Provider
# manifest aliases alone do not establish an executable exact source.
_DATA_ADAPTERS = {
    ("CN", "STOCK", "REALTIME_QUOTE", "akshare", "eastmoney"): ("akshare-stock-em-v1", "akshare-stock-spot-em-v1"),
    ("CN", "STOCK", "REALTIME_QUOTE", "akshare", "sina"): ("akshare-stock-sina-v1", "akshare-stock-spot-sina-v1"),
    ("CN", "STOCK", "REALTIME_QUOTE", "akshare", "tencent"): ("akshare-stock-tencent-v1", "akshare-stock-spot-tencent-v1"),
    ("CN", "STOCK", "REALTIME_QUOTE", "efinance", "eastmoney"): ("efinance-stock-em-v1", "efinance-stock-quote-em-v1"),
    ("CN", "ETF", "REALTIME_QUOTE", "efinance", "eastmoney"): ("efinance-etf-single-em-v1", "efinance-snapshot-em-v1"),
    ("CN", "STOCK", "REALTIME_QUOTE", "pytdx", "pytdx"): ("pytdx-stock-quote-v1", "pytdx-direct-v1"),
    ("CN", "STOCK", "REALTIME_QUOTE", "yfinance", "yfinance"): ("yfinance-stock-quote-v1", "yfinance-direct-v1"),
    ("CN", "ETF", "REALTIME_QUOTE", "yfinance", "yfinance"): ("yfinance-etf-quote-v1", "yfinance-direct-v1"),
    ("CN", "MUTUAL_FUND", "FUND_NAV", "akshare", "eastmoney"): ("akshare-fund-nav-em-v1", "akshare-fund-open-info-em-v1"),
    ("CN", "MUTUAL_FUND", "FUND_NAV", "efinance", "eastmoney"): ("efinance-fund-nav-em-v1", "efinance-fund-nav-em-v1"),
    ("CN", "MUTUAL_FUND", "FUND_NAV_HISTORY", "akshare", "eastmoney"): ("akshare-fund-nav-em-v1", "akshare-fund-open-info-em-v1"),
    ("CN", "MUTUAL_FUND", "FUND_NAV_HISTORY", "efinance", "eastmoney"): ("efinance-fund-nav-raw-v1", "eastmoney-fund-nav-raw-v1"),
    ("CN", "MUTUAL_FUND", "FUND_HOLDINGS", "akshare", "eastmoney"): ("akshare-fund-holdings-em-v1", "akshare-fund-portfolio-em-v1"),
    ("CN", "STOCK", "CHIP_SUMMARY", "akshare", "eastmoney"): ("akshare-stock-chip-em-v1", "akshare-stock-cyq-em-v1"),
}


def current_data_adapter_revisions(
    key: Mapping[str, Any], target: Mapping[str, Any],
) -> dict[str, str] | None:
    if key.get("kind") != "data":
        return None
    identity = (
        key.get("market"), key.get("assetType"), key.get("capability"),
        target.get("providerId"), target.get("upstreamSource"),
    )
    revisions = _DATA_ADAPTERS.get(identity)
    if revisions is None:
        return None
    return {
        "adapterRevision": revisions[0],
        "sourceRevision": revisions[1],
        "credentialRevision": "not-required",
    }


def iter_current_data_adapters():
    for market, asset_type, capability, provider_id, source in sorted(_DATA_ADAPTERS):
        yield (
            {"kind": "data", "market": market, "assetType": asset_type,
             "capability": capability},
            {"providerId": provider_id, "upstreamSource": source},
        )


class CurrentDataRouteError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _market(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized.endswith(".HK"):
        return "HK"
    if normalized.endswith(".US") or normalized.isalpha():
        return "US"
    return "CN"


def current_data_route_key(capability: str, instrument_type: str, symbol: str) -> dict[str, str]:
    return {
        "kind": "data", "market": _market(symbol),
        "assetType": instrument_type, "capability": capability,
    }


def select_current_data_targets(
    policy: Mapping[str, Any] | None,
    *,
    capability: str,
    instrument_type: str,
    symbol: str,
) -> list[tuple[str, str, int]]:
    if not isinstance(policy, Mapping) or policy.get("contractVersion") != 3:
        raise CurrentDataRouteError("NO_ELIGIBLE_PROVIDER", "当前 Control 路由策略不可用")
    if policy.get("enabled") is not True:
        raise CurrentDataRouteError("NO_ELIGIBLE_PROVIDER", "当前 Control 路由策略已禁用")
    revision = policy.get("revision")
    source_revision = policy.get("sourceDesiredRevision")
    if (
        not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0
        or not isinstance(source_revision, int) or isinstance(source_revision, bool)
        or source_revision != revision
    ):
        raise CurrentDataRouteError("invalid_response", "当前 Control 策略修订不一致")
    routes = policy.get("routes")
    if not isinstance(routes, list):
        raise CurrentDataRouteError("invalid_response", "当前 Control 路由格式无效")
    key = current_data_route_key(capability, instrument_type, symbol)
    matched = [route for route in routes if isinstance(route, Mapping) and route.get("key") == key]
    if not matched:
        raise CurrentDataRouteError("NO_ELIGIBLE_PROVIDER", "当前 Control 未配置对应能力路由")
    if len(matched) != 1:
        raise CurrentDataRouteError("invalid_response", "当前 Control 存在重复能力路由")
    raw_targets = matched[0].get("targets")
    if not isinstance(raw_targets, list) or len(raw_targets) > 2:
        raise CurrentDataRouteError("invalid_response", "当前 Control 目标格式无效")
    seen: set[tuple[str, str]] = set()
    selected: list[tuple[str, str, int]] = []
    for index, raw in enumerate(raw_targets):
        if not isinstance(raw, Mapping) or set(raw) != {
            "providerId", "upstreamSource", "routeIndex", "eligible", "reason",
        }:
            raise CurrentDataRouteError("invalid_response", "当前 Control 目标字段无效")
        provider = raw.get("providerId")
        source = raw.get("upstreamSource")
        route_index = raw.get("routeIndex")
        eligible = raw.get("eligible")
        reason = raw.get("reason")
        if (
            not isinstance(provider, str) or not provider.strip() or provider.strip() != provider
            or not isinstance(source, str) or not source.strip() or source.strip() != source
            or not isinstance(route_index, int) or isinstance(route_index, bool)
            or route_index != index or not isinstance(eligible, bool)
            or eligible != (reason is None)
            or (reason is not None and (not isinstance(reason, str) or not reason.strip()))
        ):
            raise CurrentDataRouteError("invalid_response", "当前 Control 目标状态无效")
        identity = (provider, source)
        if identity in seen:
            raise CurrentDataRouteError("invalid_response", "当前 Control 目标重复")
        seen.add(identity)
        if eligible:
            selected.append((provider, source, index))
    if not selected:
        raise CurrentDataRouteError("NO_ELIGIBLE_PROVIDER", "当前 Control 无可执行目标")
    return selected
