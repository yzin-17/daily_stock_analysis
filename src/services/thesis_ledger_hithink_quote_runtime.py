"""HiThink 单标报价的当前策略、准入和凭据快照。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Any, Mapping

from .provider_credentials_runtime import ProviderCredentialSnapshot
from .thesis_ledger_control import (
    ControlContractError, PROVIDER_MANIFESTS,
    _provider_credential_revision_from_snapshot,
)
from .thesis_ledger_hithink_quote_admission import (
    hithink_quote_admission_matches_current,
    hithink_quote_route_key,
)


@dataclass(frozen=True)
class HiThinkQuoteGuard:
    policy_revision: int
    admission_version: int
    credential_revision: str
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
            snapshot.provider_id != "hithink" or snapshot.source != "environment"
            or snapshot.method != "api_key" or not snapshot.values.get("apiKey")
        ):
            return None
        credential_revision = _provider_credential_revision_from_snapshot(snapshot)
        if credential_revision is None:
            return None
        target = {"providerId": "hithink", "upstreamSource": source}
        admission = store.get_route_admission_v3(
            key=hithink_quote_route_key(asset_type), target=target,
        )
        if not hithink_quote_admission_matches_current(
            admission, asset_type=asset_type, source=source, symbol=symbol,
            credential_revision=credential_revision, now=datetime.now(timezone.utc),
        ):
            return None
        return HiThinkQuoteGuard(
            revision, admission["recordVersion"], credential_revision, snapshot,
        )
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
    """读取结束再次核对，不接受策略、凭据或准入版本晚到。"""
    current = current_hithink_quote_guard(
        store, asset_type=asset_type, source=source, symbol=symbol,
        expected_policy_revision=guard.policy_revision,
    )
    return (
        current is not None
        and current.admission_version == guard.admission_version
        and current.credential_revision == guard.credential_revision
        and current.snapshot.config_version == guard.snapshot.config_version
        and current.snapshot.credential_version == guard.snapshot.credential_version
    )
