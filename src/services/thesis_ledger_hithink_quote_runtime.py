"""HiThink 单标报价的精确策略与有效凭据快照。"""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any, Mapping

from .provider_credentials_runtime import ProviderCredentialSnapshot
from .thesis_ledger_control import (
    ControlContractError, PROVIDER_MANIFESTS,
)
from .thesis_ledger_hithink_quote_admission import (
    hithink_quote_route_key,
)


@dataclass(frozen=True)
class HiThinkQuoteGuard:
    policy_revision: int
    snapshot: ProviderCredentialSnapshot


def current_hithink_quote_guard(
    store: Any,
    *,
    asset_type: str,
    source: str,
    symbol: str,
    expected_policy_revision: int,
) -> HiThinkQuoteGuard | None:
    """只为当前精确目标和标的返回可执行的凭据快照。"""
    try:
        manifest = PROVIDER_MANIFESTS["hithink"]
        source_manifest = next(
            (item for item in manifest["upstreamSources"] if item.get("sourceId") == source),
            None,
        )
        if (
            source_manifest is None or asset_type not in
            source_manifest.get("capabilities", {}).get("REALTIME_QUOTE", [])
            or not isinstance(expected_policy_revision, int)
            or isinstance(expected_policy_revision, bool)
        ):
            return None
        effective = store.effective_policy_v3()
        if not isinstance(effective, Mapping) or effective.get("contractVersion") != 3:
            return None
        revision = effective.get("revision")
        if (
            not effective.get("enabled") or not isinstance(revision, int)
            or isinstance(revision, bool) or revision <= 0
            or effective.get("sourceDesiredRevision") != revision
            or revision != expected_policy_revision
        ):
            return None
        routes = effective.get("routes")
        if not isinstance(routes, list):
            return None
        matching_routes = [
            route for route in routes
            if isinstance(route, Mapping) and route.get("key") == hithink_quote_route_key(asset_type)
        ]
        if len(matching_routes) != 1:
            return None
        entries = matching_routes[0].get("targets")
        if not isinstance(entries, list) or not any(
            isinstance(entry, Mapping)
            and entry.get("providerId") == "hithink"
            and entry.get("upstreamSource") == source
            and entry.get("eligible") is True
            for entry in entries
        ):
            return None
        snapshot = store.provider_credential_snapshot("hithink")
        if (
            snapshot.provider_id != "hithink" or snapshot.source not in {"environment", "control"}
            or snapshot.method != "api_key" or not snapshot.values.get("apiKey")
        ):
            return None
        return HiThinkQuoteGuard(revision, snapshot)
    except (AttributeError, ControlContractError, KeyError, sqlite3.Error, TypeError, ValueError):
        return None


def hithink_quote_guard_still_current(
    store: Any,
    guard: HiThinkQuoteGuard,
    *,
    asset_type: str,
    source: str,
    symbol: str,
) -> bool:
    """读取结束再次核对，不接受策略或凭据变更后的晚到结果。"""
    current = current_hithink_quote_guard(
        store, asset_type=asset_type, source=source, symbol=symbol,
        expected_policy_revision=guard.policy_revision,
    )
    return (
        current is not None
        and current.snapshot == guard.snapshot
    )
