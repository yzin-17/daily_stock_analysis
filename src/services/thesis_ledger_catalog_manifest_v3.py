"""精确 V3 适配库存与宽 Provider manifest 的兼容边界。"""

from typing import Any, Mapping

from src.services.thesis_ledger_event_v3_adapters import event_adapter_matches
from src.services.thesis_ledger_hithink_dividend_contract_v3 import hithink_dividend_route_matches
from src.services.thesis_ledger_hithink_quote_admission import hithink_quote_revisions
from src.services.thesis_ledger_current_data_route import current_data_adapter_revisions
from src.services.thesis_ledger_market_v3_adapters import HITHINK_STOCK_HISTORY_SOURCE, market_v3_bar_adapter_reason


def catalog_manifest_matches_v3(provider: Mapping[str, Any], key: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    if current_data_adapter_revisions(key, target) is not None:
        return _source_declares(provider, key, target)
    if key.get("kind") == "data" and key.get("capability") == "REALTIME_QUOTE":
        source_id = target.get("upstreamSource")
        asset_type = key.get("assetType")
        if (
            key.get("market") != "CN" or provider.get("providerId") != "hithink"
            or target.get("providerId") != "hithink"
            or hithink_quote_revisions(asset_type, source_id) is None
        ):
            return False
        return any(
            isinstance(source, Mapping)
            and source.get("sourceId") == source_id
            and source.get("capabilities", {}).get("REALTIME_QUOTE") == [asset_type]
            for source in provider.get("upstreamSources", [])
        )
    if hithink_dividend_route_matches(key, target):
        markets, sources = provider.get("markets"), provider.get("upstreamSources")
        if (provider.get("providerId") != "hithink" or not isinstance(markets, list)
                or "CN" not in markets or not isinstance(sources, list)):
            return False
        return any(
            isinstance(source, Mapping)
            and source.get("sourceId") == target.get("upstreamSource")
            and isinstance(source.get("capabilities"), Mapping)
            and source["capabilities"].get("CASH_DISTRIBUTION") == ["ETF"]
            for source in sources
        )
    if event_adapter_matches(key, target) and provider.get("providerId") == target.get("providerId"):
        return True
    if (provider.get("providerId") == "hithink" and key.get("assetType") == "STOCK"
            and target.get("providerId") == "hithink" and target.get("upstreamSource") == HITHINK_STOCK_HISTORY_SOURCE
            and market_v3_bar_adapter_reason(dict(key), dict(target)) is None):
        return True
    markets, sources = provider.get("markets"), provider.get("upstreamSources")
    if not isinstance(markets, list) or "CN" not in markets or not isinstance(sources, list):
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
