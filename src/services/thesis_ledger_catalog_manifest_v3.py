"""精确 V3 适配库存与宽 Provider manifest 的兼容边界。"""

from typing import Any, Mapping

from src.services.thesis_ledger_event_v3_adapters import event_adapter_matches
from src.services.thesis_ledger_current_data_route import current_data_adapter_revisions
from src.services.thesis_ledger_capability_contracts import CatalogManifestContext
from src.services.thesis_ledger_capability_registry import DailyBinding
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


def catalog_manifest_matches_v3(provider: Mapping[str, Any], key: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    if current_data_adapter_revisions(key, target) is not None:
        return _source_declares(provider, key, target)
    if key.get("kind") == "data" and key.get("capability") == "REALTIME_QUOTE":
        return False
    source = CAPABILITY_SOURCES.source(str(target.get('providerId') or ''))
    if source is not None and source.catalog_manifest_policy is not None:
        decision = source.catalog_manifest_policy(CatalogManifestContext(provider, key, target,
            isinstance(CAPABILITY_SOURCES.resolve(key, target), DailyBinding)))
        if decision is not None:
            if type(decision) is not bool:
                return False
            return decision
    if event_adapter_matches(key, target) and provider.get("providerId") == target.get("providerId"):
        return True
    markets, sources = provider.get("markets"), provider.get("upstreamSources")
    if not isinstance(markets, list) or key.get("market") not in markets or not isinstance(sources, list):
        return False
    source_id = str(target.get("upstreamSource") or "").strip().lower()
    asset_type = str(key.get("assetType") or "")
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        capabilities = source.get("capabilities")
        source_daily = capabilities.get("DAILY_BAR") if isinstance(capabilities, Mapping) else None
        if (str(source.get("sourceId") or "").strip().lower() == source_id
                and isinstance(source_daily, list) and asset_type in source_daily):
            return True
    return False


def _source_declares(provider: Mapping[str, Any], key: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    if provider.get("providerId") != target.get("providerId"):
        return False
    if key.get("market") not in provider.get("markets", []):
        return False
    return any(
        isinstance(source, Mapping)
        and source.get("sourceId") == target.get("upstreamSource")
        and key.get("assetType") in source.get("capabilities", {}).get(key.get("capability"), [])
        for source in provider.get("upstreamSources", [])
    )
